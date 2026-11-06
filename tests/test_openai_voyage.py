import json
import os
import shutil
import subprocess
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from test_rca_agent import _valid_rca_payload
from test_retrieval import InMemoryKnowledgeStore

from dataops_control_plane.api.schemas import RCAOutput
from dataops_control_plane.config import Settings
from dataops_control_plane.services.openai_rca import OpenAIRCAClient
from dataops_control_plane.services.provider_factory import (
    create_embedding_provider,
    create_rca_client,
)
from dataops_control_plane.services.rca_agent import LLMResponseInvalid
from dataops_control_plane.services.retrieval import (
    EmbeddingUnavailable,
    HybridRetriever,
    KnowledgeFilter,
)


def test_luna_uses_strict_output_without_losing_recovery_parameters() -> None:
    """Catches a model-only switch using unsupported or unbounded generation settings."""
    wire_payload = _valid_rca_payload()
    wire_payload["recommended_action"]["parameters"] = '{"scope":"invalid_rows"}'

    def handler(request):
        body = json.loads(request.content)
        assert request.url == "https://api.openai.test/v1/chat/completions"
        assert body["model"] == "gpt-6-luna"
        assert body.get("reasoning_effort") == "none"
        assert body.get("max_completion_tokens") == 1200
        assert "temperature" not in body
        assert body["response_format"]["type"] == "json_schema"
        wire = body["response_format"]["json_schema"]
        assert wire["strict"] is True
        assert wire["schema"]["additionalProperties"] is False
        assert set(wire["schema"]["required"]) == set(RCAOutput.model_fields)
        action = wire["schema"]["$defs"]["RCARecommendedAction"]
        assert action["properties"]["parameters"]["type"] == "string"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(wire_payload)}}
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 60},
            },
        )

    client = OpenAIRCAClient(
        httpx.Client(base_url="https://api.openai.test/v1", transport=httpx.MockTransport(handler)),
        model_name="gpt-6-luna",
        api_key="test-only-key",
    )
    try:
        result = client.generate(
            system_prompt="Use evidence.",
            user_prompt="Synthetic case.",
            schema=RCAOutput.model_json_schema(),
        )
    finally:
        client.close()
    output = RCAOutput.model_validate(result.payload)
    assert output.recommended_action.parameters == {"scope": "invalid_rows"}
    assert (result.prompt_tokens, result.completion_tokens) == (100, 60)


@pytest.mark.parametrize("finish,refusal", [("length", None), ("stop", "refused")])
def test_luna_rejects_truncated_or_refused_reports(finish, refusal) -> None:
    """Catches partial/refused responses being persisted as valid model reports."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {
                            "content": json.dumps(_valid_rca_payload()),
                            "refusal": refusal,
                        },
                    }
                ],
            },
        )
    )
    client = OpenAIRCAClient(
        httpx.Client(base_url="https://openai.test/v1", transport=transport),
        model_name="gpt-6-luna",
        api_key="test-only-key",
    )
    try:
        with pytest.raises(LLMResponseInvalid):
            client.generate(
                system_prompt="Use evidence.",
                user_prompt="Synthetic case.",
                schema=RCAOutput.model_json_schema(),
            )
    finally:
        client.close()


def voyage_settings(**changes):
    return Settings(
        _env_file=None,
        **{
            "embedding_provider": "voyage",
            "embedding_url": "https://api.voyage.test/v1",
            "embedding_model": "voyage-4-lite",
            "embedding_dimensions": 2,
            "embedding_api_key": "test-only-voyage-key",
            **changes,
        },
    )


def install_transport(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)
    )


def test_voyage_preserves_batch_order_and_uses_provider_wire_contract(monkeypatch) -> None:
    """Catches reversed vectors and sending OpenAI dimensions to the Voyage API."""
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url == "https://api.voyage.test/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer test-only-voyage-key"
        body = json.loads(request.content)
        assert body == {
            "model": "voyage-4-lite",
            "input": ["first", "second"],
            "input_type": "document",
            "output_dimension": 2,
            "output_dtype": "float",
            "truncation": False,
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.3, 0.4]},
                    {"index": 0, "embedding": [0.1, 0.2]},
                ]
            },
        )

    install_transport(monkeypatch, handler)
    provider = create_embedding_provider(voyage_settings())
    try:
        assert provider.embed([]) == []
        assert provider.embed(["first", "second"]) == [[0.1, 0.2], [0.3, 0.4]]
    finally:
        provider.close()
    assert len(requests) == 1


def test_retrieval_marks_voyage_documents_and_queries_differently(monkeypatch) -> None:
    """Catches retrieval embedding search questions with the document prompt."""
    input_types = []

    def handler(request):
        body = json.loads(request.content)
        input_types.append(body["input_type"])
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    install_transport(monkeypatch, handler)
    retriever = HybridRetriever(
        InMemoryKnowledgeStore(), create_embedding_provider(voyage_settings())
    )
    try:
        retriever.index_document(
            document_type="RUNBOOK",
            title="Schema checks",
            content="Inspect schema drift before publishing.",
            source_uri="manual://schema",
            project_ref="example/demo",
            provider="github",
            incident_type=None,
            environment="test",
            version="1",
            metadata={},
        )
        result = retriever.search(
            "schema", top_k=1, filters=KnowledgeFilter(project_ref="example/demo")
        )
    finally:
        retriever.close()
    assert len(result.items) == 1
    assert input_types == ["document", "query"]


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [0.1]}],
        [{"index": 0, "embedding": [True, 0.2]}],
        [{"index": 0, "embedding": [float("nan"), 0.2]}],
        [{"index": 1, "embedding": [0.1, 0.2]}],
    ],
)
def test_voyage_rejects_invalid_vectors(monkeypatch, data) -> None:
    """Catches corrupt provider output reaching an Elasticsearch vector index."""
    install_transport(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=json.dumps({"data": data}), headers={"Content-Type": "application/json"}
        ),
    )
    provider = create_embedding_provider(voyage_settings())
    try:
        with pytest.raises(EmbeddingUnavailable):
            provider.embed(["synthetic text"])
    finally:
        provider.close()


def test_cloud_providers_require_keys_before_creating_clients(monkeypatch) -> None:
    """Catches silently starting the selected cloud profile with empty credentials."""
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: pytest.fail("Created a client without a key")
    )
    with pytest.raises(ValueError, match="DATAOPS_EMBEDDING_API_KEY"):
        create_embedding_provider(voyage_settings(embedding_api_key=None))
    with pytest.raises(ValueError, match="DATAOPS_LLM_API_KEY"):
        create_rca_client(
            Settings(
                _env_file=None,
                llm_provider="openai",
                llm_model="gpt-6-luna",
                llm_url="https://api.openai.com/v1",
            )
        )


@pytest.mark.parametrize("base_file", ["compose.yaml", "deploy/demo-vps/compose.yaml"])
def test_cloud_overlay_overrides_existing_ollama_runtime_settings(tmp_path, base_file) -> None:
    """Catches old qwen/BGE URL/model values surviving the requested cloud migration."""
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("Docker Compose CLI required")
    root = Path(__file__).resolve().parents[1]
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    env = {
        k: v for k, v in os.environ.items() if not k.startswith(("DATAOPS_", "OPENAI_", "VOYAGE_"))
    }
    env.update(
        DATAOPS_POSTGRES_PASSWORD="test-password",
        DATAOPS_AGENT_TOKEN="test-instance",
        DATAOPS_KIBANA_ENCRYPTION_KEY="test-key-not-production-32-characters",
        DATAOPS_LLM_PROVIDER="ollama",
        DATAOPS_LLM_URL="http://old-ollama:11434",
        DATAOPS_LLM_MODEL="qwen3.5:4b",
        DATAOPS_EMBEDDING_MODEL="bge-m3:567m",
        DATAOPS_EMBEDDING_URL="http://old-ollama:11434",
        OPENAI_API_KEY="fake-openai-key",
        VOYAGE_API_KEY="fake-voyage-key",
    )
    result = subprocess.run(
        [
            docker,
            "compose",
            "--project-directory",
            str(root),
            "--env-file",
            str(env_file),
            "-f",
            str(root / base_file),
            "-f",
            str(root / "compose.cloud.yaml"),
            "config",
            "--format",
            "json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(result.stdout)["services"]["api"]["environment"]
    assert {
        k: actual[k]
        for k in (
            "DATAOPS_LLM_PROVIDER",
            "DATAOPS_LLM_MODEL",
            "DATAOPS_LLM_URL",
            "DATAOPS_EMBEDDING_PROVIDER",
            "DATAOPS_EMBEDDING_MODEL",
            "DATAOPS_EMBEDDING_URL",
            "DATAOPS_LLM_API_KEY",
            "DATAOPS_EMBEDDING_API_KEY",
        )
    } == {
        "DATAOPS_LLM_PROVIDER": "openai",
        "DATAOPS_LLM_MODEL": "gpt-6-luna",
        "DATAOPS_LLM_URL": "https://api.openai.com/v1",
        "DATAOPS_EMBEDDING_PROVIDER": "voyage",
        "DATAOPS_EMBEDDING_MODEL": "voyage-4-lite",
        "DATAOPS_EMBEDDING_URL": "https://api.voyageai.com/v1",
        "DATAOPS_LLM_API_KEY": "fake-openai-key",
        "DATAOPS_EMBEDDING_API_KEY": "fake-voyage-key",
    }


def test_cloud_check_dry_run_does_not_call_providers_or_print_keys(monkeypatch, capsys) -> None:
    """Catches accidental paid requests or secret disclosure from the default preflight."""
    monkeypatch.setenv("DATAOPS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LLM_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("DATAOPS_LLM_MODEL", "gpt-6-luna")
    monkeypatch.setenv("DATAOPS_LLM_API_KEY", "private-openai-test-value")
    monkeypatch.setenv("DATAOPS_EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("DATAOPS_EMBEDDING_URL", "https://api.voyageai.com/v1")
    monkeypatch.setenv("DATAOPS_EMBEDDING_MODEL", "voyage-4-lite")
    monkeypatch.setenv("DATAOPS_EMBEDDING_API_KEY", "private-voyage-test-value")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: pytest.fail("Dry run called a provider"))
    module = import_module("dataops_control_plane.cloud_check")
    assert module.main([]) == 0
    output = capsys.readouterr().out
    assert "private-openai-test-value" not in output
    assert "private-voyage-test-value" not in output
    assert json.loads(output)["live_requests"] == 0


def test_cloud_check_refuses_live_requests_without_both_keys(monkeypatch, capsys) -> None:
    """Catches partial credentials reaching one paid provider before migration can succeed."""
    for key in ("DATAOPS_LLM_API_KEY", "DATAOPS_EMBEDDING_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATAOPS_LLM_PROVIDER", "openai")
    monkeypatch.setenv("DATAOPS_LLM_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("DATAOPS_LLM_MODEL", "gpt-6-luna")
    monkeypatch.setenv("DATAOPS_EMBEDDING_PROVIDER", "voyage")
    monkeypatch.setenv("DATAOPS_EMBEDDING_URL", "https://api.voyageai.com/v1")
    monkeypatch.setenv("DATAOPS_EMBEDDING_MODEL", "voyage-4-lite")
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: pytest.fail("Live call without both keys")
    )
    module = import_module("dataops_control_plane.cloud_check")
    assert module.main(["--live"]) == 2
    assert json.loads(capsys.readouterr().out)["live_requests"] == 0

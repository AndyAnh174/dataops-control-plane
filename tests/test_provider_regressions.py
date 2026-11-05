import json
import os
import shutil
import subprocess
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from dataops_control_plane.config import Settings
from dataops_control_plane.services import elasticsearch_knowledge
from dataops_control_plane.services.elasticsearch_knowledge import ElasticsearchKnowledgeStore
from dataops_control_plane.services.provider_factory import create_rca_client


@pytest.mark.parametrize("provider", ["ollama", "openai", "gemini", "deepseek"])
def test_app_starts_with_each_supported_llm_provider(monkeypatch, provider) -> None:
    """Catches a provider switch that crashes app import or bypasses the factory."""
    monkeypatch.setenv("DATAOPS_LLM_PROVIDER", provider)
    monkeypatch.setenv("DATAOPS_EMBEDDING_PROVIDER", "ollama")
    monkeypatch.setenv("DATAOPS_LLM_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("DATAOPS_LLM_MODEL", "test-model")
    module = import_module("dataops_control_plane.main")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with TestClient(module.create_app(engine=engine)) as client:
        response = client.get("/health")
    engine.dispose()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "dataops-control-plane"}


def test_openai_factory_sends_completion_to_configured_endpoint(monkeypatch) -> None:
    """Catches an installed OpenAI adapter that cannot be selected by the factory."""

    def respond(request):
        assert request.url == "https://llm.example.test/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-openai-key"
        body = json.loads(request.content)
        assert body["model"] == "configured-openai-model"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": '{"root_cause":"column renamed"}',
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
        )

    original_client = httpx.Client
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original_client(transport=transport, **kwargs)
    )
    client = create_rca_client(
        Settings(
            llm_provider="openai",
            llm_url="https://llm.example.test/v1",
            llm_model="configured-openai-model",
            llm_api_key="test-openai-key",
        )
    )
    try:
        result = client.generate(
            system_prompt="Analyze the supplied incident.",
            user_prompt="The amount column was renamed.",
            schema={"type": "object"},
        )
    finally:
        client.close()
    assert result.payload == {"root_cause": "column renamed"}
    assert (result.prompt_tokens, result.completion_tokens) == (12, 8)


@pytest.fixture
def storage_requests(monkeypatch):
    """Record the Elasticsearch wire contract without starting a real cluster."""
    requests = {"templates": [], "indices": [], "aliases": []}

    def client(*args, **kwargs):
        return SimpleNamespace(
            indices=SimpleNamespace(
                put_index_template=lambda **body: requests["templates"].append(body),
                create=lambda **body: requests["indices"].append(body),
                update_aliases=lambda **body: requests["aliases"].append(body),
            ),
            close=lambda: None,
        )

    monkeypatch.setattr(elasticsearch_knowledge, "Elasticsearch", client)
    return requests


def test_gemini_store_mapping_matches_selected_embedding_dimensions(storage_requests) -> None:
    """Catches 768-dimensional Gemini vectors being indexed into a 1024-dimension mapping."""
    store = ElasticsearchKnowledgeStore.from_settings(
        Settings(embedding_provider="gemini", gemini_embedding_dimensions=768)
    )
    store.ensure_resources()
    mapping = storage_requests["templates"][0]["template"]["mappings"]
    assert mapping["properties"]["embedding"]["dims"] == 768


@pytest.mark.parametrize(
    "change",
    [
        {"embedding_provider": "ollama"},
        {"gemini_embedding_model": "another-embedding-model"},
        {"gemini_base_url": "https://another.example.test"},
        {"gemini_embedding_dimensions": 768},
    ],
)
def test_embedding_profile_changes_use_separate_storage_resources(storage_requests, change) -> None:
    """Catches mixing vectors from different model spaces, even with equal dimensions."""
    first = Settings(embedding_provider="gemini", gemini_embedding_dimensions=1024)
    second = Settings(
        **{"embedding_provider": "gemini", "gemini_embedding_dimensions": 1024, **change}
    )
    ElasticsearchKnowledgeStore.from_settings(first).ensure_resources()
    ElasticsearchKnowledgeStore.from_settings(second).ensure_resources()
    indexes = [item["index"] for item in storage_requests["indices"]]
    aliases = [item["actions"][0]["add"]["alias"] for item in storage_requests["aliases"]]
    templates = [item["name"] for item in storage_requests["templates"]]
    assert len(set(indexes)) == 2
    assert len(set(aliases)) == 2
    assert len(set(templates)) == 2


def test_rotating_embedding_key_keeps_existing_knowledge_accessible(storage_requests) -> None:
    """Catches API-key rotation unnecessarily sending retrieval to an empty index."""
    for key in ("old-key", "new-key"):
        store = ElasticsearchKnowledgeStore.from_settings(
            Settings(embedding_provider="gemini", gemini_api_key=key)
        )
        store.ensure_resources()
    assert len({item["index"] for item in storage_requests["indices"]}) == 1


def test_existing_ollama_bge_installation_keeps_legacy_index(storage_requests) -> None:
    """Catches upgrades hiding knowledge already stored under the original BGE alias."""
    ElasticsearchKnowledgeStore.from_settings(Settings()).ensure_resources()
    assert storage_requests["indices"] == [{"index": "knowledge-dataops-v1"}]
    assert storage_requests["aliases"][0]["actions"][0]["add"]["alias"] == "knowledge-dataops"


@pytest.mark.parametrize("compose_file", ["compose.yaml", "deploy/demo-vps/compose.yaml"])
def test_compose_forwards_provider_settings_to_the_api_container(tmp_path, compose_file) -> None:
    """Catches cloud selectors/keys existing in host env but missing inside the API container."""
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI is needed for the configuration contract test")
    version = subprocess.run(
        [docker, "compose", "version"], capture_output=True, text=True, check=False
    )
    if version.returncode:
        pytest.skip("Docker Compose plugin is not installed")

    root = Path(__file__).resolve().parents[1]
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    expected = {
        "DATAOPS_LLM_PROVIDER": "openai",
        "DATAOPS_LLM_API_KEY": "fake-openai-key",
        "DATAOPS_EMBEDDING_PROVIDER": "gemini",
        "DATAOPS_GEMINI_API_KEY": "fake-gemini-key",
        "DATAOPS_GEMINI_BASE_URL": "https://gemini.example.test",
        "DATAOPS_GEMINI_LLM_MODEL": "test-gemini-llm",
        "DATAOPS_GEMINI_EMBEDDING_MODEL": "test-gemini-embedding",
        "DATAOPS_GEMINI_EMBEDDING_DIMENSIONS": "768",
        "DATAOPS_DEEPSEEK_API_KEY": "fake-deepseek-key",
        "DATAOPS_DEEPSEEK_BASE_URL": "https://deepseek.example.test",
        "DATAOPS_DEEPSEEK_LLM_MODEL": "test-deepseek-llm",
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("DATAOPS_")}
    env.update(
        expected,
        DATAOPS_POSTGRES_PASSWORD="fake-review-password",
        DATAOPS_AGENT_TOKEN="fake-review-token",
        DATAOPS_KIBANA_ENCRYPTION_KEY="review-key-not-for-production-32-characters",
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
            str(root / compose_file),
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
    for key, value in expected.items():
        assert actual.get(key) == value, f"{key} is not forwarded by {compose_file}"

import json

import httpx
import pytest

from dataops_control_plane.services.ollama_rca import (
    LLMResponseInvalid,
    LLMUnavailable,
    OllamaRCAClient,
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"root_cause": {"type": "string"}},
    "required": ["root_cause"],
}


def test_ollama_rca_uses_one_non_streaming_structured_chat_request() -> None:
    """Catches free-form output or accidental streaming/tool loops in the RCA boundary."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "model": "gemma4:e2b",
                "message": {
                    "role": "assistant",
                    "content": '{"root_cause":"amount range violation"}',
                },
                "done": True,
                "prompt_eval_count": 120,
                "eval_count": 40,
                "total_duration": 1_500_000_000,
            },
        )

    client = OllamaRCAClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama.test"),
        model_name="gemma4:e2b",
    )

    result = client.generate(
        system_prompt="system policy",
        user_prompt="incident evidence",
        schema=OUTPUT_SCHEMA,
    )

    assert result.payload == {"root_cause": "amount range violation"}
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 40
    assert result.duration_ms == 1_500
    assert captured["path"] == "/api/chat"
    assert captured["payload"] == {
        "model": "gemma4:e2b",
        "messages": [
            {"role": "system", "content": "system policy"},
            {"role": "user", "content": "incident evidence"},
        ],
        "stream": False,
        "think": False,
        "format": OUTPUT_SCHEMA,
        "options": {"temperature": 0, "num_ctx": 8_192, "num_predict": 1_200},
    }


def test_ollama_rca_distinguishes_invalid_model_output_from_an_outage() -> None:
    """Catches malformed JSON being treated as a retryable infrastructure failure."""
    invalid = OllamaRCAClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"message": {"role": "assistant", "content": "not-json"}},
                )
            ),
            base_url="http://ollama.test",
        ),
        model_name="gemma4:e2b",
    )
    unavailable = OllamaRCAClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"error": "model path /secret/model"})
            ),
            base_url="http://ollama.test",
        ),
        model_name="gemma4:e2b",
    )

    with pytest.raises(LLMResponseInvalid, match="valid structured JSON"):
        invalid.generate(system_prompt="system", user_prompt="incident", schema=OUTPUT_SCHEMA)
    with pytest.raises(LLMUnavailable) as error:
        unavailable.generate(system_prompt="system", user_prompt="incident", schema=OUTPUT_SCHEMA)

    assert str(error.value) == "LLM service is temporarily unavailable"
    assert "/secret/model" not in str(error.value)


def test_ollama_rca_accepts_one_json_fence_but_not_surrounding_prose() -> None:
    """Catches a small local model wrapping an otherwise valid structured response."""

    def client_for(content: str) -> OllamaRCAClient:
        return OllamaRCAClient(
            httpx.Client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200,
                        json={"message": {"role": "assistant", "content": content}},
                    )
                ),
                base_url="http://ollama.test",
            ),
            model_name="gemma4:e2b",
        )

    fenced = client_for('```json\n{"root_cause":"amount range violation"}\n```')
    prose = client_for('Result:\n```json\n{"root_cause":"amount range violation"}\n```')

    result = fenced.generate(
        system_prompt="system",
        user_prompt="incident",
        schema=OUTPUT_SCHEMA,
    )

    assert result.payload == {"root_cause": "amount range violation"}
    with pytest.raises(LLMResponseInvalid, match="valid structured JSON"):
        prose.generate(system_prompt="system", user_prompt="incident", schema=OUTPUT_SCHEMA)

import json
from unittest.mock import MagicMock

import httpx
import pytest

from dataops_control_plane.config import Settings
from dataops_control_plane.services.deepseek_rca import DeepSeekRCAClient
from dataops_control_plane.services.gemini_embeddings import GeminiEmbeddingProvider
from dataops_control_plane.services.gemini_rca import GeminiRCAClient
from dataops_control_plane.services.ollama_embeddings import OllamaEmbeddingProvider
from dataops_control_plane.services.ollama_rca import OllamaRCAClient
from dataops_control_plane.services.provider_factory import (
    create_embedding_provider,
    create_rca_client,
)
from dataops_control_plane.services.rca_agent import (
    LLMResponseInvalid,
    LLMUnavailable,
)
from dataops_control_plane.services.retrieval import EmbeddingUnavailable


def test_factory_defaults() -> None:
    settings = Settings()
    rca_client = create_rca_client(settings)
    embedding_provider = create_embedding_provider(settings)

    assert isinstance(rca_client, OllamaRCAClient)
    assert isinstance(embedding_provider, OllamaEmbeddingProvider)


def test_factory_gemini_and_deepseek() -> None:
    gemini_settings = Settings(
        llm_provider="gemini",
        embedding_provider="gemini",
        gemini_api_key="test-gemini-key",
    )
    assert isinstance(create_rca_client(gemini_settings), GeminiRCAClient)
    assert isinstance(create_embedding_provider(gemini_settings), GeminiEmbeddingProvider)

    deepseek_settings = Settings(
        llm_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
    )
    assert isinstance(create_rca_client(deepseek_settings), DeepSeekRCAClient)


def test_factory_unsupported() -> None:
    bad_llm = Settings(llm_provider="unsupported")
    with pytest.raises(ValueError, match="Unsupported LLM provider"):
        create_rca_client(bad_llm)

    bad_embed = Settings(embedding_provider="unsupported")
    with pytest.raises(ValueError, match="Unsupported embedding provider"):
        create_embedding_provider(bad_embed)


def test_gemini_rca_generate_success() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    expected_payload = {
        "incident_type": "DATA_DRIFT",
        "root_cause": "Schema mismatch in amount column",
        "confidence": 0.95,
        "recommended_action": "QUARANTINE",
    }
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(expected_payload)}],
                }
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 150,
            "candidatesTokenCount": 50,
        },
    }
    mock_client.post.return_value = mock_response

    client = GeminiRCAClient(
        mock_client,
        model_name="gemini-2.0-flash",
        api_key="test-key",
    )
    result = client.generate(
        system_prompt="system",
        user_prompt="user",
        schema={"type": "object"},
    )

    assert result.payload == expected_payload
    assert result.prompt_tokens == 150
    assert result.completion_tokens == 50
    assert result.duration_ms >= 0

    mock_client.post.assert_called_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["x-goog-api-key"] == "test-key"


def test_gemini_rca_markdown_json() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    expected_payload = {"incident_type": "SCHEMA_DRIFT"}
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": f"```json\n{json.dumps(expected_payload)}\n```"}],
                }
            }
        ]
    }
    mock_client.post.return_value = mock_response

    client = GeminiRCAClient(mock_client, model_name="gemini-2.0-flash")
    result = client.generate(system_prompt="", user_prompt="", schema={})
    assert result.payload == expected_payload


def test_gemini_rca_error_handling() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.ConnectError("Connection failed")

    client = GeminiRCAClient(mock_client, model_name="gemini-2.0-flash")
    with pytest.raises(LLMUnavailable):
        client.generate(system_prompt="", user_prompt="", schema={})


def test_gemini_rca_invalid_json() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": "not a json string"}],
                }
            }
        ]
    }
    mock_client.post.return_value = mock_response

    client = GeminiRCAClient(mock_client, model_name="gemini-2.0-flash")
    with pytest.raises(LLMResponseInvalid):
        client.generate(system_prompt="", user_prompt="", schema={})


def test_gemini_embeddings_success() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    vector_a = [0.1] * 768
    vector_b = [0.2] * 768
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "embeddings": [
            {"values": vector_a},
            {"values": vector_b},
        ]
    }
    mock_client.post.return_value = mock_response

    provider = GeminiEmbeddingProvider(
        mock_client,
        model_name="text-embedding-004",
        dimensions=768,
        api_key="test-key",
    )
    result = provider.embed(["text1", "text2"])

    assert len(result) == 2
    assert result[0] == vector_a
    assert result[1] == vector_b

    assert provider.embed([]) == []


def test_gemini_embeddings_dimension_mismatch() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "embeddings": [{"values": [0.1, 0.2]}]
    }
    mock_client.post.return_value = mock_response

    provider = GeminiEmbeddingProvider(
        mock_client,
        model_name="text-embedding-004",
        dimensions=768,
    )
    with pytest.raises(EmbeddingUnavailable, match="expected 768 dimensions"):
        provider.embed(["text"])


def test_deepseek_rca_generate_success() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    expected_payload = {
        "incident_type": "NULL_RATE",
        "root_cause": "Excessive nulls in customer_id",
        "confidence": 0.88,
    }
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {"content": json.dumps(expected_payload)},
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 40,
        },
    }
    mock_client.post.return_value = mock_response

    client = DeepSeekRCAClient(
        mock_client,
        model_name="deepseek-chat",
        api_key="test-deepseek-key",
    )
    result = client.generate(
        system_prompt="system",
        user_prompt="user",
        schema={"type": "object"},
    )

    assert result.payload == expected_payload
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 40
    assert result.duration_ms >= 0

    mock_client.post.assert_called_once()
    _, kwargs = mock_client.post.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer test-deepseek-key"
    assert kwargs["json"]["response_format"] == {"type": "json_object"}


def test_deepseek_rca_error_handling() -> None:
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.TimeoutException("Timeout")

    client = DeepSeekRCAClient(mock_client, model_name="deepseek-chat")
    with pytest.raises(LLMUnavailable):
        client.generate(system_prompt="", user_prompt="", schema={})

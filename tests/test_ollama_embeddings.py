import httpx
import pytest

from dataops_control_plane.services.ollama_embeddings import (
    EmbeddingUnavailable,
    OllamaEmbeddingProvider,
)


def test_ollama_embedder_uses_batch_api_and_rejects_dimension_drift() -> None:
    """Catches a model change silently writing vectors incompatible with the ES mapping."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "model": "bge-m3:567m",
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            },
        )

    provider = OllamaEmbeddingProvider(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://ollama.test"),
        model_name="bge-m3:567m",
        dimensions=3,
    )

    embeddings = provider.embed(["first", "second"])

    assert embeddings == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert captured["path"] == "/api/embed"
    assert '"model":"bge-m3:567m"' in str(captured["payload"])
    assert '"input":["first","second"]' in str(captured["payload"])
    assert '"truncate":false' in str(captured["payload"])

    provider = OllamaEmbeddingProvider(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})
            ),
            base_url="http://ollama.test",
        ),
        model_name="bge-m3:567m",
        dimensions=3,
    )
    with pytest.raises(EmbeddingUnavailable, match="expected 3 dimensions, received 2"):
        provider.embed(["dimension drift"])


def test_ollama_embedder_hides_upstream_response_details() -> None:
    """Catches an upstream error body or URL being exposed through the public API."""
    provider = OllamaEmbeddingProvider(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(500, json={"error": "internal model path /secret/model"})
            ),
            base_url="http://ollama.test",
        ),
        model_name="bge-m3:567m",
        dimensions=3,
    )

    with pytest.raises(EmbeddingUnavailable) as error:
        provider.embed(["schema drift"])

    assert str(error.value) == "Embedding service is temporarily unavailable"
    assert "/secret/model" not in str(error.value)

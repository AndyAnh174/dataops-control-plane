from collections.abc import Sequence

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.ai_payloads import indexed_embeddings, validate_embeddings
from dataops_control_plane.services.retrieval import EmbeddingUnavailable


class VoyageEmbeddingProvider:
    def __init__(self, client: httpx.Client, *, model_name: str, dimensions: int) -> None:
        self._client = client
        self._model = model_name
        self.model_name = f"voyage/{model_name}"
        self.dimensions = dimensions

    @classmethod
    def from_settings(cls, settings: Settings) -> "VoyageEmbeddingProvider":
        key = settings.embedding_api_key
        if key is None or not key.get_secret_value().strip():
            raise ValueError("DATAOPS_EMBEDDING_API_KEY is required for Voyage")
        return cls(
            httpx.Client(
                base_url=settings.embedding_url.rstrip("/") + "/",
                headers={"Authorization": f"Bearer {key.get_secret_value()}"},
                timeout=settings.embedding_timeout_seconds,
            ),
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

    def embed(self, texts: Sequence[str], *, input_type: str = "document") -> list[list[float]]:
        if input_type not in {"document", "query"}:
            raise ValueError("input_type must be document or query")
        if not texts:
            return []
        try:
            response = self._client.post(
                "embeddings",
                json={
                    "model": self._model,
                    "input": list(texts),
                    "input_type": input_type,
                    "output_dimension": self.dimensions,
                    "output_dtype": "float",
                    "truncation": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingUnavailable(
                "Voyage embedding service is temporarily unavailable"
            ) from exc
        if not isinstance(payload, dict):
            raise EmbeddingUnavailable("Voyage returned an invalid embedding response")
        vectors = indexed_embeddings(payload.get("data"), count=len(texts))
        return validate_embeddings(vectors, count=len(texts), dimensions=self.dimensions)

    def close(self) -> None:
        self._client.close()

import math
from collections.abc import Sequence

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.retrieval import EmbeddingUnavailable


class OllamaEmbeddingProvider:
    def __init__(
        self,
        client: httpx.Client,
        *,
        model_name: str,
        dimensions: int,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self.dimensions = dimensions

    @classmethod
    def from_settings(cls, settings: Settings) -> "OllamaEmbeddingProvider":
        return cls(
            httpx.Client(
                base_url=settings.embedding_url,
                timeout=settings.embedding_timeout_seconds,
            ),
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.post(
                "/api/embed",
                json={
                    "model": self.model_name,
                    "input": list(texts),
                    "truncate": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingUnavailable("Embedding service is temporarily unavailable") from exc

        raw_embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise EmbeddingUnavailable("Embedding service returned an invalid response")

        embeddings: list[list[float]] = []
        for raw_embedding in raw_embeddings:
            if not isinstance(raw_embedding, list):
                raise EmbeddingUnavailable("Embedding service returned an invalid response")
            if len(raw_embedding) != self.dimensions:
                raise EmbeddingUnavailable(
                    f"Embedding service expected {self.dimensions} dimensions, "
                    f"received {len(raw_embedding)}"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in raw_embedding
            ):
                raise EmbeddingUnavailable("Embedding service returned an invalid response")
            embeddings.append([float(value) for value in raw_embedding])
        return embeddings

    def close(self) -> None:
        self._client.close()

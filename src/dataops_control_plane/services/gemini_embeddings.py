import math
from collections.abc import Sequence

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.retrieval import EmbeddingUnavailable


class GeminiEmbeddingProvider:
    def __init__(
        self,
        client: httpx.Client,
        *,
        model_name: str,
        dimensions: int = 768,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self.dimensions = dimensions
        self._api_key = api_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "GeminiEmbeddingProvider":
        api_key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key
            else None
        )
        return cls(
            httpx.Client(
                base_url=settings.gemini_base_url.rstrip("/"),
                timeout=settings.embedding_timeout_seconds,
            ),
            model_name=settings.gemini_embedding_model,
            dimensions=settings.gemini_embedding_dimensions,
            api_key=api_key,
        )

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        headers: dict[str, str] = {"Content-Type": "application/json"}
        params: dict[str, str] = {}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
            params["key"] = self._api_key

        requests = [
            {
                "model": f"models/{self.model_name}",
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.dimensions,
            }
            for text in texts
        ]

        response: httpx.Response | None = None
        try:
            response = self._client.post(
                f"/v1beta/models/{self.model_name}:batchEmbedContents",
                headers=headers,
                params=params,
                json={"requests": requests},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            error_detail = response.text if response is not None else str(exc)
            raise EmbeddingUnavailable(
                f"Gemini embedding service returned error: {error_detail}"
            ) from exc

        raw_embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise EmbeddingUnavailable("Gemini embedding service returned an invalid response")

        embeddings: list[list[float]] = []
        for raw_embedding in raw_embeddings:
            values = raw_embedding.get("values") if isinstance(raw_embedding, dict) else None
            if not isinstance(values, list):
                raise EmbeddingUnavailable("Gemini embedding service returned an invalid response")
            if len(values) != self.dimensions:
                raise EmbeddingUnavailable(
                    f"Gemini embedding service expected {self.dimensions} dimensions, "
                    f"received {len(values)}"
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                for value in values
            ):
                raise EmbeddingUnavailable("Gemini embedding service returned invalid vector values")
            embeddings.append([float(value) for value in values])

        return embeddings

    def close(self) -> None:
        self._client.close()

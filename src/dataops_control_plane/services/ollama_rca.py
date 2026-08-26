import json
from collections.abc import Mapping

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.rca_agent import (
    LLMResponseInvalid,
    LLMUnavailable,
    RCACompletion,
)


class OllamaRCAClient:
    def __init__(self, client: httpx.Client, *, model_name: str) -> None:
        self._client = client
        self.model_name = model_name

    @classmethod
    def from_settings(cls, settings: Settings) -> "OllamaRCAClient":
        return cls(
            httpx.Client(
                base_url=settings.llm_url,
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.llm_model,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, object],
    ) -> RCACompletion:
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self.model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                    "format": dict(schema),
                    "options": {"temperature": 0, "num_predict": 1_200},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable("LLM service is temporarily unavailable") from exc

        try:
            response_payload = response.json()
            message = response_payload["message"]
            content = message["content"]
            output_payload = json.loads(content)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMResponseInvalid("LLM did not return valid structured JSON") from exc
        if not isinstance(output_payload, dict):
            raise LLMResponseInvalid("LLM did not return valid structured JSON")

        return RCACompletion(
            payload=output_payload,
            prompt_tokens=_non_negative_int(response_payload.get("prompt_eval_count")),
            completion_tokens=_non_negative_int(response_payload.get("eval_count")),
            duration_ms=_duration_ms(response_payload.get("total_duration")),
        )

    def close(self) -> None:
        self._client.close()


def _non_negative_int(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(value: object) -> int:
    nanoseconds = _non_negative_int(value)
    return nanoseconds // 1_000_000

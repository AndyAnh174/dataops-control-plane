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
    def __init__(
        self,
        client: httpx.Client,
        *,
        model_name: str,
        context_tokens: int = 8_192,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._context_tokens = context_tokens

    @classmethod
    def from_settings(cls, settings: Settings) -> "OllamaRCAClient":
        return cls(
            httpx.Client(
                base_url=settings.llm_url,
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.llm_model,
            context_tokens=settings.llm_context_tokens,
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
                    "think": False,
                    "format": dict(schema),
                    "options": {
                        "temperature": 0,
                        "num_ctx": self._context_tokens,
                        "num_predict": 1_200,
                    },
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable("LLM service is temporarily unavailable") from exc

        try:
            response_payload = response.json()
            message = response_payload["message"]
            content = message["content"]
            output_payload = _structured_json_object(content)
        except (KeyError, TypeError, ValueError) as exc:
            raise LLMResponseInvalid("LLM did not return valid structured JSON") from exc

        return RCACompletion(
            payload=output_payload,
            prompt_tokens=_non_negative_int(response_payload.get("prompt_eval_count")),
            completion_tokens=_non_negative_int(response_payload.get("eval_count")),
            duration_ms=_duration_ms(response_payload.get("total_duration")),
        )

    def close(self) -> None:
        self._client.close()


def _structured_json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise TypeError("Structured output must be a string")
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        lines = value.strip().splitlines()
        if len(lines) < 3 or lines[0].strip().lower() != "```json" or lines[-1].strip() != "```":
            raise
        payload = json.loads("\n".join(lines[1:-1]))
    if not isinstance(payload, dict):
        raise TypeError("Structured output must be a JSON object")
    return dict(payload)


def _non_negative_int(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _duration_ms(value: object) -> int:
    nanoseconds = _non_negative_int(value)
    return nanoseconds // 1_000_000

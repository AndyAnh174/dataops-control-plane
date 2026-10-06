import json
import time
from collections.abc import Mapping

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.rca_agent import (
    LLMResponseInvalid,
    LLMUnavailable,
    RCACompletion,
)


class OpenAIRCAClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        model_name: str,
        api_key: str,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._api_key = api_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIRCAClient":
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
        return cls(
            httpx.Client(
                base_url=settings.llm_url or "https://api.openai.com/v1",
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.llm_model,
            api_key=api_key,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, object],
    ) -> RCACompletion:
        try:
            start_time = time.monotonic()
            response = self._client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self.model_name,
                    "messages": [
                        {
                            "role": "system",
                            "content": f"{system_prompt}\n\nYou MUST return a JSON object that satisfies this schema:\n{json.dumps(schema)}"
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0,
                },
            )
            response.raise_for_status()
            duration_ms = int((time.monotonic() - start_time) * 1000)
        except httpx.HTTPError as exc:
            raise LLMUnavailable("OpenAI/Deepseek LLM service is temporarily unavailable") from exc

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
            usage = response_payload.get("usage", {})
            output_payload = self._structured_json_object(content)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMResponseInvalid("LLM did not return valid structured JSON") from exc

        return RCACompletion(
            payload=output_payload,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
        )

    def _structured_json_object(self, value: object) -> dict[str, object]:
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

    def close(self) -> None:
        self._client.close()

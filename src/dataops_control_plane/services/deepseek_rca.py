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


class DeepSeekRCAClient:
    def __init__(
        self,
        client: httpx.Client,
        *,
        model_name: str,
        api_key: str | None = None,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._api_key = api_key

    @classmethod
    def from_settings(cls, settings: Settings) -> "DeepSeekRCAClient":
        api_key = (
            settings.deepseek_api_key.get_secret_value()
            if settings.deepseek_api_key
            else None
        )
        return cls(
            httpx.Client(
                base_url=settings.deepseek_base_url.rstrip("/"),
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.deepseek_llm_model,
            api_key=api_key,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, object],
    ) -> RCACompletion:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        system_instruction = (
            f"{system_prompt}\n\n"
            f"You must strictly output a valid JSON object complying with this JSON Schema:\n"
            f"{json.dumps(dict(schema))}"
        )

        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        start_time = time.perf_counter()
        try:
            response = self._client.post(
                "/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMUnavailable("DeepSeek service is temporarily unavailable") from exc

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        try:
            response_payload = response.json()
            choices = response_payload.get("choices", [])
            if not choices:
                raise LLMResponseInvalid("DeepSeek returned no choices")
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            output_payload = _structured_json_object(content)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMResponseInvalid("DeepSeek did not return valid structured JSON") from exc

        usage = response_payload.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))

        return RCACompletion(
            payload=output_payload,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_ms=duration_ms,
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
        if len(lines) >= 3 and lines[0].strip().lower() == "```json" and lines[-1].strip() == "```":
            payload = json.loads("\n".join(lines[1:-1]))
        else:
            raise
    if not isinstance(payload, dict):
        raise TypeError("Structured output must be a JSON object")
    return dict(payload)

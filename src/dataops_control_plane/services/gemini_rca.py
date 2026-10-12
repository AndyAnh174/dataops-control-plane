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


class GeminiRCAClient:
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
    def from_settings(cls, settings: Settings) -> "GeminiRCAClient":
        api_key = (
            settings.gemini_api_key.get_secret_value()
            if settings.gemini_api_key
            else None
        )
        return cls(
            httpx.Client(
                base_url=settings.gemini_base_url.rstrip("/"),
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.gemini_llm_model,
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
        params: dict[str, str] = {}
        if self._api_key:
            headers["x-goog-api-key"] = self._api_key
            params["key"] = self._api_key

        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}],
                }
            ],
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": dict(schema),
            },
        }

        start_time = time.perf_counter()
        response: httpx.Response | None = None
        try:
            response = self._client.post(
                f"/v1beta/models/{self.model_name}:generateContent",
                headers=headers,
                params=params,
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            error_detail = response.text if response is not None else str(exc)
            raise LLMUnavailable(f"Gemini service returned error: {error_detail}") from exc

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        try:
            response_payload = response.json()
            candidates = response_payload.get("candidates", [])
            if not candidates:
                raise LLMResponseInvalid("Gemini returned no candidates")
            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            if not parts:
                raise LLMResponseInvalid("Gemini candidate has no content parts")
            text = parts[0].get("text", "")
            output_payload = _structured_json_object(text)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMResponseInvalid("Gemini did not return valid structured JSON") from exc

        usage = response_payload.get("usageMetadata", {})
        prompt_tokens = int(usage.get("promptTokenCount", 0))
        completion_tokens = int(usage.get("candidatesTokenCount", 0))

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

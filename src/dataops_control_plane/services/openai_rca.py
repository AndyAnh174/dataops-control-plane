import json
import time
from collections.abc import Mapping

import httpx

from dataops_control_plane.config import Settings
from dataops_control_plane.services.ai_payloads import (
    non_negative_int,
    rca_transport_schema,
    structured_json_object,
)
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
        max_output_tokens: int = 1200,
    ) -> None:
        self._client = client
        self.model_name = model_name
        self._api_key = api_key
        self._max_output_tokens = max_output_tokens

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAIRCAClient":
        api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else ""
        if settings.llm_model == "gpt-6-luna" and not api_key.strip():
            raise ValueError("DATAOPS_LLM_API_KEY is required for gpt-6-luna")
        return cls(
            httpx.Client(
                base_url=settings.llm_url or "https://api.openai.com/v1",
                timeout=settings.llm_timeout_seconds,
            ),
            model_name=settings.llm_model,
            api_key=api_key,
            max_output_tokens=settings.llm_max_output_tokens,
        )

    def generate(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        schema: Mapping[str, object],
    ) -> RCACompletion:
        luna = self.model_name == "gpt-6-luna"
        wire_schema = rca_transport_schema(schema) if luna else dict(schema)
        output_format = {"type": "json_object"}
        if luna:
            output_format = {
                "type": "json_schema",
                "json_schema": {"name": "dataops_rca", "strict": True, "schema": wire_schema},
            }
        system = (
            f"{system_prompt}\n\nYou MUST return one JSON object satisfying this schema:\n"
            + json.dumps(wire_schema)
        )
        if luna:
            system += (
                "\nThis transport schema overrides representation only. Encode recovery "
                "parameters as a JSON object string; preserve evidence and safety rules."
            )
        body = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": output_format,
        }
        if luna:
            body.update(reasoning_effort="none", max_completion_tokens=self._max_output_tokens)
        else:
            body["temperature"] = 0
        try:
            start_time = time.monotonic()
            response = self._client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=body,
            )
            response.raise_for_status()
            duration_ms = int((time.monotonic() - start_time) * 1000)
        except httpx.HTTPError as exc:
            raise LLMUnavailable("OpenAI/Deepseek LLM service is temporarily unavailable") from exc

        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            message = choice["message"]
            if luna and (choice.get("finish_reason") != "stop" or message.get("refusal")):
                raise ValueError("Incomplete or refused model response")
            content = message["content"]
            usage = response_payload.get("usage") or {}
            output_payload = (
                structured_json_object(content) if luna else self._structured_json_object(content)
            )
        except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:
            raise LLMResponseInvalid("LLM did not return valid structured JSON") from exc

        return RCACompletion(
            payload=output_payload,
            prompt_tokens=non_negative_int(usage.get("prompt_tokens", 0)),
            completion_tokens=non_negative_int(usage.get("completion_tokens", 0)),
            duration_ms=duration_ms,
        )

    def _structured_json_object(self, value: object) -> dict[str, object]:
        if not isinstance(value, str):
            raise TypeError("Structured output must be a string")
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            lines = value.strip().splitlines()
            if (
                len(lines) < 3
                or lines[0].strip().lower() != "```json"
                or lines[-1].strip() != "```"
            ):
                raise
            payload = json.loads("\n".join(lines[1:-1]))
        if not isinstance(payload, dict):
            raise TypeError("Structured output must be a JSON object")
        return dict(payload)

    def close(self) -> None:
        self._client.close()

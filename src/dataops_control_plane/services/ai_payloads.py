import json
import math
from collections.abc import Mapping
from copy import deepcopy

from dataops_control_plane.services.retrieval import EmbeddingUnavailable


def validate_embeddings(value: object, *, count: int, dimensions: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise EmbeddingUnavailable("Embedding service returned an invalid response")
    embeddings = []
    for vector in value:
        if not isinstance(vector, list):
            raise EmbeddingUnavailable("Embedding service returned an invalid response")
        if len(vector) != dimensions:
            raise EmbeddingUnavailable(
                f"Embedding service expected {dimensions} dimensions, received {len(vector)}"
            )
        if any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            for item in vector
        ):
            raise EmbeddingUnavailable("Embedding service returned an invalid response")
        embeddings.append([float(item) for item in vector])
    return embeddings


def indexed_embeddings(value: object, *, count: int) -> list[object]:
    if not isinstance(value, list) or len(value) != count:
        raise EmbeddingUnavailable("Embedding service returned an invalid response")
    by_index: dict[int, object] = {}
    for item in value:
        if not isinstance(item, dict):
            raise EmbeddingUnavailable("Embedding service returned an invalid response")
        index = item.get("index")
        if type(index) is not int or not 0 <= index < count or index in by_index:
            raise EmbeddingUnavailable("Embedding service returned an invalid response")
        by_index[index] = item.get("embedding")
    return [by_index[index] for index in range(count)]


def structured_json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        raise TypeError("Structured output must be a string")
    try:
        payload = json.loads(value, parse_constant=_reject_non_finite_json)
    except json.JSONDecodeError:
        lines = value.strip().splitlines()
        if len(lines) < 3 or lines[0].strip().lower() != "```json" or lines[-1].strip() != "```":
            raise
        payload = json.loads("\n".join(lines[1:-1]), parse_constant=_reject_non_finite_json)
    if not isinstance(payload, dict):
        raise TypeError("Structured output must be a JSON object")
    action = payload.get("recommended_action")
    if isinstance(action, dict) and isinstance(action.get("parameters"), str):
        parameters = json.loads(action["parameters"], parse_constant=_reject_non_finite_json)
        if not isinstance(parameters, dict):
            raise TypeError("Recovery parameters must be a JSON object")
        action["parameters"] = parameters
    return payload


def _reject_non_finite_json(value: str) -> None:
    raise ValueError("Non-finite JSON numbers are invalid")


def non_negative_int(value: object) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def rca_transport_schema(schema: Mapping[str, object]) -> dict[str, object]:
    wire_schema = deepcopy(dict(schema))
    definitions = wire_schema.get("$defs", {})
    action = definitions.get("RCARecommendedAction") if isinstance(definitions, dict) else None
    if isinstance(action, dict) and "parameters" in action.get("properties", {}):
        # Strict cloud schemas cannot express an arbitrary dict[str, JsonValue].
        # Preserve every recovery parameter by carrying its JSON object as a string.
        action["properties"]["parameters"] = {
            "type": "string",
            "description": (
                "Recovery parameters encoded as a JSON object string; use '{}' if empty."
            ),
        }
        definitions.pop("JsonValue", None)
    return _strict_schema(wire_schema)


def _strict_schema(schema: dict) -> dict:
    constraints = {}
    for key in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
        "pattern",
        "format",
        "multipleOf",
    ):
        if key in schema:
            constraints[key] = schema.pop(key)
    schema.pop("default", None)
    if constraints:
        schema["description"] = (
            schema.get("description", "") + " Application constraints: " + json.dumps(constraints)
        ).strip()
    for key in ("properties", "$defs"):
        if isinstance(schema.get(key), dict):
            schema[key] = {name: _strict_schema(child) for name, child in schema[key].items()}
    if schema.get("type") == "object" or "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    if isinstance(schema.get("items"), dict):
        schema["items"] = _strict_schema(schema["items"])
    for key in ("anyOf", "oneOf"):
        if isinstance(schema.get(key), list):
            schema[key] = [_strict_schema(child) for child in schema[key]]
    return schema

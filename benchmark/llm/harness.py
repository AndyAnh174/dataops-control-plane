"""Small, sequential benchmark primitives. Never execute model output."""

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

MODELS = [
    {"provider": "ollama", "model": "qwen3.5:9b"},
    {"provider": "ollama", "model": "gemma4:12b"},
    {"provider": "ollama", "model": "ornith-1.5:9b"},
    {"provider": "openai", "model": "gpt-6-luna", "key": "OPENAI_API_KEY"},
    {"provider": "deepseek", "model": "deepseek-flash", "key": "DEEPSEEK_API_KEY"},
    {"provider": "gemini", "model": "gemini-3.5-flash-lite", "key": "GEMINI_API_KEY"},
]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def classification(text, labels):
    data = json.loads(text)
    if not isinstance(data, dict) or set(data) != {"label", "probabilities"}:
        raise ValueError("Unexpected classification fields")
    probabilities = data["probabilities"]
    if data["label"] not in labels or not isinstance(probabilities, dict):
        raise ValueError("Invalid label or probabilities")
    if set(probabilities) != set(labels):
        raise ValueError("Probability labels differ")
    if any(
        type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
        for v in probabilities.values()
    ):
        raise ValueError("Invalid probability value")
    if not math.isclose(sum(probabilities.values()), 1, abs_tol=0.001):
        raise ValueError("Probabilities do not sum to one")
    if probabilities[data["label"]] < max(probabilities.values()):
        raise ValueError("Chosen label disagrees with probabilities")
    return {"prediction": data["label"], "probabilities": probabilities}


def classification_schema(labels):
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": list(labels)},
            "probabilities": {
                "type": "object",
                "properties": {
                    label: {"type": "number", "minimum": 0, "maximum": 1} for label in labels
                },
                "required": list(labels),
                "additionalProperties": False,
            },
        },
        "required": ["label", "probabilities"],
        "additionalProperties": False,
    }


def completion_request(spec, prompt, schema, keys, max_tokens=256):
    provider, model = spec["provider"], spec["model"]
    headers = {"Content-Type": "application/json"}
    messages = [{"role": "user", "content": prompt}]
    if provider == "ollama":
        return (
            "http://127.0.0.1:11434/api/chat",
            headers,
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "format": schema,
                "keep_alive": "10m",
                "options": {
                    "temperature": 0,
                    "seed": 2026,
                    "num_ctx": 4096,
                    "num_predict": max_tokens,
                },
            },
        )
    key = keys.get(spec["key"]) or (keys.get("GOOGLE_API_KEY") if provider == "gemini" else None)
    if not key:
        raise ValueError("Missing provider key")
    if provider == "gemini":
        headers["x-goog-api-key"] = key
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers,
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                    "maxOutputTokens": max_tokens,
                    "temperature": 0,
                    "thinkingConfig": {"thinkingLevel": "MINIMAL"},
                },
            },
        )
    headers["Authorization"] = "Bearer " + key
    body = {"model": model, "messages": messages, "stream": False}
    if provider == "openai":
        body.update(
            max_completion_tokens=max_tokens,
            reasoning_effort="none",
            store=False,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "benchmark", "strict": True, "schema": schema},
            },
        )
        return "https://api.openai.com/v1/chat/completions", headers, body
    if provider == "deepseek":
        body.update(
            max_tokens=max_tokens,
            temperature=0,
            thinking={"type": "disabled"},
            response_format={"type": "json_object"},
        )
        return "https://api.deepseek.com/chat/completions", headers, body
    raise ValueError("Unsupported provider")


class ProviderError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(f"http_{status}")


def post_json(url, headers, body, timeout=180):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderError(exc.code) from None
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Oversized response")
    return json.loads(data), (time.perf_counter() - start) * 1000


def response_text(provider, response):
    if provider == "ollama":
        if response.get("done_reason") != "stop" or not response.get("done"):
            raise ValueError("Truncated Ollama response")
        return response["message"]["content"]
    if provider == "gemini":
        candidate = response["candidates"][0]
        if candidate.get("finishReason") != "STOP":
            raise ValueError("Incomplete Gemini response")
        return "".join(
            p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought")
        )
    choice = response["choices"][0]
    if choice.get("finish_reason") != "stop" or choice["message"].get("refusal"):
        raise ValueError("Truncated or refused completion")
    return choice["message"]["content"]


def complete(spec, prompt, schema, keys, max_tokens=256):
    url, headers, body = completion_request(spec, prompt, schema, keys, max_tokens)
    response, ms = post_json(url, headers, body)
    usage = response.get("usage", response.get("usageMetadata"))
    if spec["provider"] == "ollama":
        usage = {
            k: response.get(k)
            for k in (
                "prompt_eval_count",
                "eval_count",
                "load_duration",
                "prompt_eval_duration",
                "eval_duration",
            )
        }
    return response_text(spec["provider"], response), {
        "latency_ms": ms,
        "usage": usage,
        "returned_model": response.get("model", response.get("modelVersion")),
    }


class Journal:
    """Write pending before a call; on resume never silently rebill an ambiguous call."""

    def __init__(self, directory, identity, cap=3000):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        manifest = self.directory / "identity.json"
        if manifest.exists() and json.loads(manifest.read_text()) != identity:
            raise ValueError("Dataset/protocol identity differs; use a new output folder")
        manifest.write_text(json.dumps(identity, indent=2), encoding="utf-8")
        self.path = self.directory / "attempts.jsonl"
        self.cap, self.rows = cap, {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.rows[row["key"]] = row

    def has(self, key):
        return key in self.rows

    def check_budget(self):
        if len(self.rows) >= self.cap:
            raise RuntimeError("Request cap reached")

    def append(self, row):
        if row["key"] not in self.rows:
            self.check_budget()
        row = {**row, "timestamp_utc": datetime.now(UTC).isoformat()}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
        self.rows[row["key"]] = row


def quantile(values, q):
    if not values:
        return None
    values = sorted(values)
    at = (len(values) - 1) * q
    lower, upper = math.floor(at), math.ceil(at)
    return values[lower] + (values[upper] - values[lower]) * (at - lower)


def classification_metrics(rows, labels):
    if not rows:
        return {}
    valid = [r for r in rows if r.get("status") == "ok"]
    scores = []
    support = dict(Counter(r["label"] for r in rows))
    per_class = {}
    for label in labels:
        tp = sum(
            r["label"] == label and r.get("prediction") == label and r.get("status") == "ok"
            for r in rows
        )
        fp = sum(
            r["label"] != label and r.get("prediction") == label and r.get("status") == "ok"
            for r in rows
        )
        fn = support.get(label, 0) - tp
        score = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0
        scores.append(score)
        per_class[label] = {
            "f1": score,
            "support": support.get(label, 0),
            "recall": tp / support[label] if support.get(label) else None,
        }
    times = [r["latency_ms"] for r in valid if "latency_ms" in r]
    present = [v["f1"] for v in per_class.values() if v["support"]]
    return {
        "n": len(rows),
        "coverage": len(valid) / len(rows),
        "support": support,
        "accuracy": sum(r.get("status") == "ok" and r.get("prediction") == r["label"] for r in rows)
        / len(rows),
        "macro_f1": sum(scores) / len(scores),
        "supported_macro_f1": sum(present) / len(present),
        "per_class": per_class,
        "latency_p50_ms": quantile(times, 0.5),
        "latency_p95_ms": quantile(times, 0.95),
    }


def normalize_vectors(vectors, dimensions):
    result = []
    for vector in vectors:
        if len(vector) != dimensions or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in vector
        ):
            raise ValueError("Invalid embedding dimension/value")
        norm = math.sqrt(sum(v * v for v in vector))
        if not norm:
            raise ValueError("Zero embedding")
        result.append([v / norm for v in vector])
    return result


def retrieval_metrics(ranking, relevant, k=5):
    if not relevant or len(set(ranking)) != len(ranking):
        raise ValueError("Empty qrels or duplicate ranked IDs")
    hits = [i for i, key in enumerate(ranking[:k], 1) if key in relevant]
    dcg = sum(1 / math.log2(i + 1) for i in hits)
    ideal = sum(1 / math.log2(i + 1) for i in range(1, min(k, len(relevant)) + 1))
    return {
        "recall": len(hits) / len(relevant),
        "mrr": 1 / hits[0] if hits else 0,
        "ndcg": dcg / ideal,
    }

"""Run the frozen proxy evaluation on the user's authorized Colab runtime only."""

import hashlib
import json
import os
import random
import subprocess
import threading
import time
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import run_benchmark as bench
from benchmark_core import normalize_answer, request_payload

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
RESULTS.mkdir(exist_ok=True)
PREDICTIONS = RESULTS / "predictions.jsonl"
MODELS = [
    ("ollama", "d3:9b"),
    ("ollama", "nimble-v3:9b"),
    ("ollama", "clef-flash:9b"),
    ("jev", "jev-1.14.0"),
    ("openai", "gpt-6-luna"),
]


def post_local(route, payload):
    request = urllib.request.Request(
        "http://127.0.0.1:11434" + route,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def get_key(names):
    for name in names:
        if os.environ.get(name):
            return os.environ[name], "environment"
    try:
        from google.colab import userdata
    except ImportError:
        return None, "colab_secrets_unavailable"
    reasons = []
    for name in names:
        try:
            value = userdata.get(name)
            if value:
                return value, "colab_secret"
        except Exception as exc:
            # Only exception class, never secret values or exception bodies.
            reasons.append(type(exc).__name__)
    return None, "/".join(sorted(set(reasons)))


class GPUMonitor:
    def __init__(self):
        self.stop_event = threading.Event()
        self.peak = None

    def sample(self):
        while not self.stop_event.is_set():
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
            )
            try:
                used = float(result.stdout.strip().splitlines()[0])
                self.peak = max(self.peak or 0, used)
            except (ValueError, IndexError):
                pass
            self.stop_event.wait(0.5)

    def __enter__(self):
        self.thread = threading.Thread(target=self.sample, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=2)


def main():
    started = time.perf_counter()
    previous_elapsed = 0
    dataset = ROOT / "data/cases.jsonl"
    report = json.loads((ROOT / "data/dataset_report.json").read_text())
    assert hashlib.sha256(dataset.read_bytes()).hexdigest() == report["cases_sha256"], (
        "Dataset changed"
    )
    cases = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines()]
    test = [row for row in cases if row["split"] == "test"]
    assert test and all(len(row["state"]) <= 2000 for row in test)
    keys, availability = {}, {}
    for backend, names in [
        ("jev", ["TYPESAFE_API_KEY", "JEV_API_KEY"]),
        ("openai", ["OPENAI_API_KEY"]),
    ]:
        keys[backend], reason = get_key(names)
        availability[backend] = {"key_available": bool(keys[backend]), "source_or_reason": reason}
    print(
        json.dumps(
            {
                "provider_availability": availability,
                "test_cases": len(test),
                "test_support": dict(Counter(r["environment"] + "/" + r["label"] for r in test)),
            },
            indent=2,
        ),
        flush=True,
    )
    metadata = {
        "started_utc": datetime.now(UTC).isoformat(),
        "seed": 2026,
        "dataset_sha256": report["cases_sha256"],
        "provider_availability": availability,
        "concurrency": 1,
        "repeats": 3,
        "state_limit_chars": 2000,
        "max_external_requests": 1000,
        "external_request_count": 0,
        "label_kind": "source_derived_proxy",
        "blocks": [],
        "model_metadata": {},
    }
    if (RESULTS / "run_metadata.json").exists():
        previous = json.loads((RESULTS / "run_metadata.json").read_text())
        if previous.get("dataset_sha256") != report["cases_sha256"]:
            raise ValueError("Cannot resume results from a different dataset")
        metadata["external_request_count"] = previous.get("external_request_count", 0)
        metadata["blocks"] = previous.get("blocks", [])
        previous_elapsed = previous.get("elapsed_seconds", 0)
        metadata["sessions"] = previous.get("sessions", []) + [
            {
                "started_utc": previous["started_utc"],
                "completed_utc": previous.get("completed_utc"),
                "elapsed_seconds": previous_elapsed,
            }
        ]
    allowed = set(filter(None, os.environ.get("BENCH_BACKENDS", "").split(",")))
    active_models = [
        (backend, model) for backend, model in MODELS if not allowed or backend in allowed
    ]
    metadata["backends_this_session"] = sorted({backend for backend, _ in active_models})
    robust_meta = RESULTS / "robustness_metadata.json"
    if robust_meta.exists():
        metadata["external_request_count"] = max(
            metadata["external_request_count"],
            json.loads(robust_meta.read_text()).get("external_requests_total_including_main", 0),
        )
    metadata["code_sha256"] = {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        for name in ("benchmark_core.py", "run_benchmark.py", "colab_run.py")
    }
    metadata["hardware"] = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    with urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=10) as response:
        metadata["ollama"] = json.load(response)
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=10) as response:
        metadata["model_tags"] = json.load(response)
    for backend, model in MODELS:
        if backend == "ollama":
            try:
                metadata["model_metadata"][model] = post_local("/api/show", {"model": model})
            except Exception as exc:
                metadata["model_metadata"][model] = {"error_type": type(exc).__name__}
    disabled = set()

    def bounded_transport(backend, model, payload, key):
        if backend != "ollama":
            if metadata["external_request_count"] >= 1000:
                raise bench.ProviderError(429)
            metadata["external_request_count"] += 1
        return bench.transport(backend, model, payload, key)

    def save():
        metadata["elapsed_seconds_this_session"] = time.perf_counter() - started
        metadata["elapsed_seconds"] = previous_elapsed + metadata["elapsed_seconds_this_session"]
        (RESULTS / "run_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        if PREDICTIONS.exists():
            rows = [json.loads(line) for line in PREDICTIONS.read_text().splitlines()]
            (RESULTS / "metrics.json").write_text(
                json.dumps(bench.summarize(rows), indent=2), encoding="utf-8"
            )

    save()
    for repeat in range(3):
        order = list(active_models)
        random.Random(2026 + repeat).shuffle(order)
        for backend, model in order:
            if (
                model in disabled
                or backend in disabled
                or (backend != "ollama" and not keys.get(backend))
            ):
                continue
            block = {"repeat": repeat, "backend": backend, "model": model}
            print(json.dumps({"starting_block": block}), flush=True)
            with GPUMonitor() as monitor:
                # Synthetic warmup excludes load/compile time from measured real-case latencies.
                for task in sorted({row["task"] for row in test}):
                    spec = bench.TASKS[task]
                    payload = request_payload(
                        backend,
                        model,
                        "A required database connection was refused and the operation could not complete.",
                        spec["criteria"],
                        spec["instructions"],
                    )
                    try:
                        response, latency = bounded_transport(
                            backend, model, payload, keys.get(backend)
                        )
                        normalize_answer(backend, response, spec["criteria"])
                        block.setdefault("warmup_ms", []).append(latency)
                    except Exception as exc:
                        block["warmup_error"] = (
                            str(exc) if isinstance(exc, bench.ProviderError) else type(exc).__name__
                        )
                        disabled.add(backend if backend != "ollama" else model)
                        break
                if model not in disabled and backend not in disabled:
                    block["result"] = bench.run(
                        test,
                        backend,
                        model,
                        PREDICTIONS,
                        api_key=keys.get(backend),
                        transport=bounded_transport,
                        repeats=repeat + 1,
                        max_calls=500,
                    )
                    if block["result"]["stopped_reason"]:
                        disabled.add(backend if backend != "ollama" else model)
                block["peak_gpu_memory_mib_observed"] = monitor.peak
            if backend == "ollama":
                try:
                    post_local("/api/generate", {"model": model, "keep_alive": 0})
                except Exception:
                    pass
            metadata["blocks"].append(block)
            save()
            print(json.dumps({"completed_block": block}), flush=True)
    metadata["completed_utc"] = datetime.now(UTC).isoformat()
    metadata["disabled"] = sorted(disabled)
    save()
    print(
        "BENCHMARK_FINISHED",
        json.dumps({"results": str(RESULTS), "elapsed_seconds": metadata["elapsed_seconds"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()

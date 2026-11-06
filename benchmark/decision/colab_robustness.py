"""Run a separate, tiny paired injection probe after the main worker completes."""

import hashlib
import json
import os
import time
from datetime import UTC, datetime

import run_benchmark as bench
from colab_run import MODELS, RESULTS, ROOT, get_key, post_local


def main():
    deadline = time.monotonic() + 1200
    while True:
        path = RESULTS / "run_metadata.json"
        meta = json.loads(path.read_text()) if path.exists() else {}
        if meta.get("completed_utc"):
            break
        if time.monotonic() > deadline:
            raise RuntimeError("Main benchmark did not finish within the bounded wait")
        time.sleep(2)
    cohort = ROOT / "data/robustness_cases.jsonl"
    assert (
        hashlib.sha256(cohort.read_bytes()).hexdigest()
        == "504f97cd4f58ad90bc60973b23a34f2accce6f094498d7c1ef2299774a9d6a09"
    )
    cases = [json.loads(line) for line in cohort.read_text(encoding="utf-8").splitlines()]
    metadata = {
        "started_utc": datetime.now(UTC).isoformat(),
        "cases": len(cases),
        "cohort_sha256": hashlib.sha256(cohort.read_bytes()).hexdigest(),
        "models": [],
    }
    key_map = {
        "jev": get_key(["TYPESAFE_API_KEY", "JEV_API_KEY"])[0],
        "openai": get_key(["OPENAI_API_KEY"])[0],
    }
    external = meta.get("external_request_count", 0)
    previous_probe = RESULTS / "robustness_metadata.json"
    if previous_probe.exists():
        external = max(
            external,
            json.loads(previous_probe.read_text()).get("external_requests_total_including_main", 0),
        )
    allowed = set(filter(None, os.environ.get("BENCH_BACKENDS", "").split(",")))

    def transport(backend, model, payload, key):
        nonlocal external
        if backend != "ollama":
            if external >= 1000:
                raise bench.ProviderError(429)
            external += 1
        return bench.transport(backend, model, payload, key)

    for backend, model in MODELS:
        if allowed and backend not in allowed:
            continue
        if backend != "ollama" and not key_map.get(backend):
            metadata["models"].append({"backend": backend, "model": model, "status": "missing_key"})
            continue
        print("Starting paired robustness probe", backend, model, flush=True)
        result = bench.run(
            cases,
            backend,
            model,
            RESULTS / "robustness_predictions.jsonl",
            api_key=key_map.get(backend),
            transport=transport,
            repeats=1,
            max_calls=12,
        )
        metadata["models"].append({"backend": backend, "model": model, **result})
        if backend == "ollama":
            try:
                post_local("/api/generate", {"model": model, "keep_alive": 0})
            except Exception:
                pass
        metadata["external_requests_total_including_main"] = external
        (RESULTS / "robustness_metadata.json").write_text(json.dumps(metadata, indent=2))
    metadata["completed_utc"] = datetime.now(UTC).isoformat()
    (RESULTS / "robustness_metadata.json").write_text(json.dumps(metadata, indent=2))
    print("ROBUSTNESS_FINISHED", flush=True)


if __name__ == "__main__":
    main()

"""Read-only resource observations; optional and separate from model requests."""

import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def monitor(root="/content/hcmute_llm"):
    root = Path(root)
    output = root / "results/resource_samples.jsonl"
    output.parent.mkdir(exist_ok=True, parents=True)
    for step in range(3600):
        if (root / "results/finished.flag").exists():
            break
        row = {
            "observed_utc": datetime.now(UTC).isoformat(),
            "note": "Observation begins after first Gemma block; not full-job peak measurement",
        }
        row["gpu_csv"] = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,utilization.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        for route in ["ps", "tags", "version"] if step % 15 == 0 else ["ps"]:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:11434/api/" + route, timeout=3
                ) as response:
                    row[route] = json.load(response)
            except Exception as exc:
                row[route + "_error"] = type(exc).__name__
        with output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
        time.sleep(2)


if __name__ == "__main__":
    monitor()

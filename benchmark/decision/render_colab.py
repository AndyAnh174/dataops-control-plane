"""Install isolated plotting dependencies and export the measured charts on Colab."""

import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    dependencies = ROOT / ".figure-deps"
    dependencies.mkdir(exist_ok=True)
    print("Installing isolated figure dependencies; model APIs are not called.", flush=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--upgrade",
            "--target",
            str(dependencies),
            "-r",
            str(ROOT / "requirements-figures.txt"),
        ],
        check=True,
    )
    bootstrap = (
        "import runpy,sys; sys.path.insert(0,sys.argv[1]); "
        "sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    subprocess.run(
        [
            sys.executable,
            "-u",
            "-c",
            bootstrap,
            str(dependencies),
            str(ROOT / "make_figures.py"),
            "--force",
        ],
        cwd=ROOT,
        check=True,
    )
    environment = {
        "completed_utc": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "renderer": "render_colab.py",
        "model_inference_requests_during_chart_generation": 0,
    }
    if shutil.which("nvidia-smi"):
        environment["gpu_visible_to_chart_runtime"] = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    (ROOT / "figures/render_environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    archive_path = ROOT / "HCMUTE-Decision-Charts-20261106.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT / "figures").iterdir()):
            if path.is_file() and path.suffix in {".svg", ".pdf", ".png", ".json"}:
                archive.write(path, str(path.relative_to(ROOT)))
        for name in (
            "make_figures.py",
            "render_colab.py",
            "requirements-figures.txt",
            "CHARTS.md",
            "results/hcmute-20261106.json",
            "diagrams/benchmark-flow.mmd",
            "diagrams/prompt-injection-probe.mmd",
        ):
            archive.write(ROOT / name, name)
    print(
        json.dumps(
            {
                "charts_ready": 8,
                "pdf_book_pages": 8,
                "archive": str(archive_path),
                "bytes": archive_path.stat().st_size,
                "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
                "render_environment": environment,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

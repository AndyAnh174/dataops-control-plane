"""Execute a separate embedding notebook inside the already allocated Colab VM."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

KEY_NAMES = ("OPENAI_API_KEY", "VOYAGE_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY")


def child_environment(keys, *, base=None):
    base = os.environ if base is None else base
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "TMPDIR",
        "TEMP",
        "TMP",
        "PYTHONPATH",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    }
    result = {
        name: value
        for name, value in base.items()
        if name in allowed or name.startswith(("CUDA_", "NVIDIA_"))
    }
    result.update({name: keys[name] for name in KEY_NAMES if keys.get(name)})
    result["BENCH_SHARED_NOTEBOOK"] = "1"
    return result


def build_notebook(root):
    root = str(Path(root))

    def code(source):
        return {
            "cell_type": "code",
            "metadata": {},
            "execution_count": None,
            "source": source,
            "outputs": [],
        }

    cells = [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": "# HCMUTE code embedding benchmark\n\nExecuted as a separate Jupyter notebook inside the retained Colab A100 VM. "
            "The cloud LLM study may run concurrently; each study uses one request at a time. "
            "Only the embedding/Decision process loads GPU models. No API key values are in this file.\n\n"
            "49 public source files, 166 fixed windows; 24 EN/VI intent pairs. Source-verified provisional navigation labels, "
            "not human-adjudicated full code-QA ground truth. See PROTOCOL.md and the dataset manifest.\n",
        }
    ]
    cells.append(
        code(
            "import os,sys,json,subprocess\nfrom pathlib import Path\n"
            f"ROOT=Path({root!r})\nsys.path.insert(0,str(ROOT))\n"
            "get_ipython().run_line_magic('xmode','Minimal')\n"
            "hardware=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total,driver_version','--format=csv,noheader'],capture_output=True,text=True).stdout.strip()\n"
            "assert 'A100' in hardware, 'This study requires the allocated A100'\nprint(hardware)\n"
            "print(next(line.strip() for line in open('/proc/meminfo') if line.startswith('MemTotal:')))\n"
            "from setup_runtime import setup\nsetup(str(ROOT),context=4096)\n"
            "keys={}\nfor name in ['OPENAI_API_KEY','VOYAGE_API_KEY','GEMINI_API_KEY','GOOGLE_API_KEY']:\n"
            "    value=os.environ.get(name)\n    if not value and not os.environ.get('BENCH_SHARED_NOTEBOOK'):\n        try:\n            from google.colab import userdata\n            value=userdata.get(name)\n        except Exception:\n            value=None\n"
            "    if value: keys[name]=value\nprint(json.dumps({'key_presence_only':{name:bool(keys.get(name)) for name in ['OPENAI_API_KEY','VOYAGE_API_KEY','GEMINI_API_KEY']}}))\n"
            "print((ROOT/'data/manifest.json').read_text())\n"
        )
    )
    cells.append(
        code(
            "from run_retrieval import run,run_readers\nretrieval_results=run(keys,str(ROOT),repeats=3)\n"
        )
    )
    cells.append(code("reader_results=run_readers(keys,str(ROOT))\n"))
    cells.append(
        code(
            "from make_comparison import make\nfigure_manifest=make(ROOT,'embedding')\n"
            "print(json.dumps({'figure_files':len(figure_manifest['outputs']),'results_directory':str(ROOT/'results')},indent=2))\n"
            "from IPython.display import display,Image\n"
            "display(Image(filename=str(ROOT/'figures/01_embedding_retrieval.png')))\n"
            "display(Image(filename=str(ROOT/'figures/03_fixed_readers.png')))\n"
        )
    )
    for index, cell in enumerate(cells):
        cell["id"] = f"benchmark-cell-{index}"
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            "shared_colab_execution": True,
        },
        "cells": cells,
    }


def launch(keys, root="/content/hcmute_embedding"):
    root = Path(root)
    status_path = root / "shared-job-status.json"
    if status_path.exists():
        old = json.loads(status_path.read_text())
        if old.get("state") == "COMPLETED":
            return old
        if old.get("pid"):
            command = Path(f"/proc/{old['pid']}/cmdline")
            if command.exists() and b"shared_runtime.py" in command.read_bytes():
                return old
    source = root / "HCMUTE_Code_Embedding_Benchmark_20261106.source.ipynb"
    source.write_text(
        json.dumps(build_notebook(root), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log = (root / "shared-job.log").open("ab")
    process = subprocess.Popen(
        [sys.executable, str(root / "shared_runtime.py"), "--worker", "--root", str(root)],
        env=child_environment(keys),
        stdout=log,
        stderr=subprocess.STDOUT,
        cwd=root,
    )
    status = {
        "state": "LAUNCHED",
        "pid": process.pid,
        "started_utc": datetime.now(UTC).isoformat(),
        "source_notebook": str(source),
        "note": "Separate notebook/kernel in the retained A100 VM; no new GPU runtime",
        "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def worker(root):
    import nbformat
    from jupyter_client.kernelspec import KernelSpecManager
    from nbclient import NotebookClient

    root = Path(root)
    status_path = root / "shared-job-status.json"
    status = {
        "state": "RUNNING",
        "pid": os.getpid(),
        "started_utc": datetime.now(UTC).isoformat(),
        "execution": "nbclient kernel inside existing Colab A100 VM; cloud LLM client may overlap",
    }

    def save():
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")

    save()
    source = root / "HCMUTE_Code_Embedding_Benchmark_20261106.source.ipynb"
    executed = root / "HCMUTE_Code_Embedding_Benchmark_20261106.ipynb"
    notebook = nbformat.read(source, as_version=4)
    kernel_directory = root / "plain-kernel-spec"
    kernel_directory.mkdir(exist_ok=True)
    (kernel_directory / "kernel.json").write_text(
        json.dumps(
            {
                "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
                "display_name": "DataOps embedding - shared A100",
                "language": "python",
            }
        )
    )
    kernel_name = "dataops-embedding-20261106"
    KernelSpecManager().install_kernel_spec(
        str(kernel_directory), kernel_name=kernel_name, user=True
    )
    status["kernel_name"] = kernel_name

    def started(cell, cell_index, **kwargs):
        status.update(cell_index=cell_index)
        save()

    client = NotebookClient(
        notebook,
        timeout=7200,
        kernel_name=kernel_name,
        resources={"metadata": {"path": str(root)}},
        store_widget_state=False,
        on_cell_start=started,
    )
    try:
        client.execute()
        status["state"] = "COMPLETED"
    except Exception as exc:
        status.update(state="FAILED", error_type=type(exc).__name__)
    finally:
        nbformat.write(notebook, executed)
        (root / "results").mkdir(exist_ok=True)
        (root / "results/notebook_execution.json").write_text(
            json.dumps(
                {
                    **status,
                    "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "execution_backend": "separate plain IPython kernel on existing Colab A100 VM",
                    "keys": "process environment only; not serialized in notebook or artifacts",
                },
                indent=2,
            )
        )
        archive = Path("/content/HCMUTE-Code-Embedding-Benchmark-20261106.zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                relative = path.relative_to(root)
                if relative.parts[0] in {"data", "results", "figures"} or (
                    len(relative.parts) == 1 and path.suffix in {".py", ".ipynb", ".md"}
                ):
                    output.write(path, str(relative))
        status.update(
            completed_utc=datetime.now(UTC).isoformat(),
            archive=str(archive),
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            executed_notebook=str(executed),
        )
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", default="/content/hcmute_embedding")
    args = parser.parse_args()
    if args.worker:
        worker(args.root)

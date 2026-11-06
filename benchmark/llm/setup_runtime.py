"""Install the official Ollama build on a disposable Colab runtime, loopback only."""

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

VERSION = "0.40.14"


def server_environment(base, context):
    if type(context) is not int or not 1024 <= context <= 65536:
        raise ValueError("Invalid runtime context")
    return {
        **{name: value for name, value in base.items() if not name.endswith("_API_KEY")},
        "OLLAMA_HOST": "127.0.0.1:11434",
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_MAX_LOADED_MODELS": "1",
        "OLLAMA_CONTEXT_LENGTH": str(context),
    }


def setup(root="/content/hcmute_llm", context=4096):
    root = Path(root)
    root.mkdir(exist_ok=True, parents=True)
    with (root / "install.log").open("ab") as log:
        if not shutil.which("zstd"):
            subprocess.run(
                ["apt-get", "update", "-qq"], stdout=log, stderr=subprocess.STDOUT, check=True
            )
            subprocess.run(
                ["apt-get", "install", "-y", "-qq", "zstd"],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        if not shutil.which("ollama"):
            installer = urllib.request.urlopen("https://ollama.com/install.sh", timeout=60).read()
            path = root / "ollama-install.sh"
            path.write_bytes(installer)
            subprocess.run(
                ["sh", str(path)],
                env={**os.environ, "OLLAMA_VERSION": VERSION},
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=1200,
            )
    try:
        response = json.load(
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
        )
    except OSError:
        output = (root / "ollama-server.log").open("ab")
        subprocess.Popen(
            ["ollama", "serve"],
            env=server_environment(os.environ, context),
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        for _ in range(90):
            try:
                response = json.load(
                    urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
                )
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("Ollama did not start")
    if response["version"] != VERSION:
        raise ValueError("Unexpected Ollama version; freeze new protocol before changing")
    print(
        json.dumps(
            {
                "ollama": response,
                "parallelism": 1,
                "loaded_models": 1,
                "context_requested_at_launch": context,
            }
        ),
        flush=True,
    )

import json
import os
import pathlib
import shutil
import subprocess
import time
import urllib.request

PINNED_OLLAMA_VERSION = "0.40.14"
BENCH_DIR = pathlib.Path("/content/hcmute_decision_benchmark")
BENCH_DIR.mkdir(exist_ok=True)
if shutil.which("zstd") is None:
    with (BENCH_DIR / "zstd-install.log").open("wb") as output:
        subprocess.run(
            ["apt-get", "update", "-qq"],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
        subprocess.run(
            ["apt-get", "install", "-y", "-qq", "zstd"],
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=180,
        )
if shutil.which("ollama") is None:
    installer = urllib.request.urlopen("https://ollama.com/install.sh", timeout=60).read()
    assert b"OLLAMA_VERSION" in installer
    install_file = BENCH_DIR / "ollama-install.sh"
    install_file.write_bytes(installer)
    with (BENCH_DIR / "install.log").open("wb") as output:
        subprocess.run(
            ["sh", str(install_file)],
            env=dict(os.environ, OLLAMA_VERSION=PINNED_OLLAMA_VERSION),
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=1200,
        )
try:
    version = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2))
except Exception:
    serve_env = dict(
        os.environ,
        OLLAMA_HOST="127.0.0.1:11434",
        OLLAMA_MAX_LOADED_MODELS="1",
        OLLAMA_NUM_PARALLEL="1",
        OLLAMA_CONTEXT_LENGTH="4096",
    )
    ollama_server_log = (BENCH_DIR / "server.log").open("ab")
    ollama_server = subprocess.Popen(
        ["ollama", "serve"], env=serve_env, stdout=ollama_server_log, stderr=subprocess.STDOUT
    )
    for _attempt in range(90):
        try:
            version = json.load(
                urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            )
            break
        except Exception:
            time.sleep(1)
assert version["version"] == PINNED_OLLAMA_VERSION
print(json.dumps({"ollama": version, "parallel_requests": 1, "context_length": 4096}, indent=2))
with (BENCH_DIR / "pull-d3.log").open("wb") as output:
    subprocess.run(
        ["ollama", "pull", "d3:9b"],
        stdout=output,
        stderr=subprocess.STDOUT,
        check=True,
        timeout=1200,
    )
print("d3:9b download complete")

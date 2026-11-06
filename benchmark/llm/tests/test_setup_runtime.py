import importlib.util
from pathlib import Path


def test_explicit_context_preserves_gpu_environment_and_loopback_binding():
    path = Path(__file__).resolve().parents[1] / "setup_runtime.py"
    spec = importlib.util.spec_from_file_location("runtime_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "server_environment"), "Explicit runtime context is not implemented"
    environment = module.server_environment(
        {
            "OLLAMA_HOST": "0.0.0.0:11434",
            "CUDA_VISIBLE_DEVICES": "0",
            "OPENAI_API_KEY": "not-for-local-server",
            "VOYAGE_API_KEY": "also-private",
        },
        16384,
    )
    assert environment["OLLAMA_HOST"] == "127.0.0.1:11434"
    assert environment["OLLAMA_CONTEXT_LENGTH"] == "16384"
    assert environment["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert environment["CUDA_VISIBLE_DEVICES"] == "0"
    assert "OPENAI_API_KEY" not in environment and "VOYAGE_API_KEY" not in environment

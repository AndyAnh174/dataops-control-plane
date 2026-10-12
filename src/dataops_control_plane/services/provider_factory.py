from dataops_control_plane.config import Settings
from dataops_control_plane.services.deepseek_rca import DeepSeekRCAClient
from dataops_control_plane.services.gemini_embeddings import GeminiEmbeddingProvider
from dataops_control_plane.services.gemini_rca import GeminiRCAClient
from dataops_control_plane.services.ollama_embeddings import OllamaEmbeddingProvider
from dataops_control_plane.services.ollama_rca import OllamaRCAClient
from dataops_control_plane.services.rca_agent import RCAClient
from dataops_control_plane.services.retrieval import EmbeddingProvider


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    provider = settings.embedding_provider.lower().strip()
    if provider == "gemini":
        return GeminiEmbeddingProvider.from_settings(settings)
    if provider == "ollama":
        return OllamaEmbeddingProvider.from_settings(settings)
    raise ValueError(f"Unsupported embedding provider: '{settings.embedding_provider}' (choose 'ollama' or 'gemini')")


def create_rca_client(settings: Settings) -> RCAClient:
    provider = settings.llm_provider.lower().strip()
    if provider == "gemini":
        return GeminiRCAClient.from_settings(settings)
    if provider == "deepseek":
        return DeepSeekRCAClient.from_settings(settings)
    if provider == "ollama":
        return OllamaRCAClient.from_settings(settings)
    raise ValueError(f"Unsupported LLM provider: '{settings.llm_provider}' (choose 'ollama', 'gemini', or 'deepseek')")

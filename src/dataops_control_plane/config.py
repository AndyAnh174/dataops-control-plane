import hashlib
import json

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./dataops.db"
    public_url: str | None = None
    web_session_cookie_name: str = "dataops_session"
    web_session_ttl_hours: int = 12
    web_session_cookie_secure: bool = True
    agent_token: SecretStr | None = None
    elasticsearch_url: str = "http://localhost:9201"
    elasticsearch_api_key: SecretStr | None = None
    elasticsearch_username: str | None = None
    elasticsearch_password: SecretStr | None = None
    elasticsearch_ca_certs: str | None = None
    elasticsearch_verify_certs: bool = True
    elasticsearch_log_retention: str = "30d"
    embedding_url: str = "http://localhost:11434"
    embedding_model: str = "bge-m3:567m"
    embedding_dimensions: int = 1024
    embedding_timeout_seconds: float = 60.0
    embedding_api_key: SecretStr | None = None
    llm_api_key: SecretStr | None = None
    llm_url: str = "http://localhost:11434"
    llm_model: str = "gemma4:e2b"
    llm_timeout_seconds: float = 300.0
    llm_context_tokens: int = 8_192
    llm_max_output_tokens: int = 1200
    rca_prompt_version: str = "rca-v1"
    rca_context_max_chars: int = 16_000
    github_api_url: str = "https://api.github.com"
    github_token: SecretStr | None = None
    github_recovery_token: SecretStr | None = None
    github_recovery_workflow: str = "dataops-recovery.yml"
    github_recovery_timeout_seconds: float = 15.0
    # Provider selection
    llm_provider: str = "ollama"
    embedding_provider: str = "ollama"

    # Google Gemini settings
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_llm_model: str = "gemini-3.5-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    gemini_embedding_dimensions: int = 1024

    # DeepSeek settings
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_llm_model: str = "deepseek-chat"

    @property
    def embedding_profile(self) -> tuple[str, str, str, int]:
        provider = self.embedding_provider.lower().strip()
        if provider == "gemini":
            return (
                provider,
                self.gemini_base_url.rstrip("/"),
                self.gemini_embedding_model,
                self.gemini_embedding_dimensions,
            )
        return (
            provider,
            self.embedding_url.rstrip("/"),
            self.embedding_model,
            self.embedding_dimensions,
        )

    @property
    def embedding_index_suffix(self) -> str:
        provider, _, model, dimensions = self.embedding_profile
        # Preserve knowledge already indexed by the original BGE installation.
        if provider == "ollama" and model == "bge-m3:567m" and dimensions == 1024:
            return ""
        identity = json.dumps(self.embedding_profile, separators=(",", ":"))
        return "-" + hashlib.sha256(identity.encode()).hexdigest()[:16]

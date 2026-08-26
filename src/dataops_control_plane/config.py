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
    llm_url: str = "http://localhost:11434"
    llm_model: str = "gemma4:e2b"
    llm_timeout_seconds: float = 300.0
    rca_prompt_version: str = "rca-v1"
    rca_context_max_chars: int = 16_000
    github_api_url: str = "https://api.github.com"
    github_token: SecretStr | None = None

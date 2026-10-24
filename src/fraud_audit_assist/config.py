"""Settings from environment variables (prefix `FAA_`, nested with `__`), validated once at startup."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["fake", "openai", "azure_openai", "anthropic"]


class LlmSettings(BaseModel):
    """One chat model behind every agent, chosen by `provider` (ADR-0009)."""

    provider: Provider = "fake"
    model: str = "gpt-4.1-mini"
    api_key: SecretStr | None = None
    # Azure OpenAI only: the resource endpoint and API version; `model` is the deployment name.
    endpoint: str | None = None
    api_version: str = "2024-10-21"
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=6)
    requests_per_second: float | None = Field(default=None, gt=0)
    # Prices per 1,000 tokens in the billing currency, so logged cost matches the provider's invoice.
    input_price_per_1k: float = Field(default=0.0, ge=0)
    output_price_per_1k: float = Field(default=0.0, ge=0)
    max_tool_rounds: int = Field(default=6, ge=1, le=20)
    fallback_provider: Provider | None = None
    fallback_model: str | None = None
    fallback_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def _complete(self) -> LlmSettings:
        missing = _missing_for(self.provider, self.api_key, self.endpoint)
        if missing:
            raise ValueError(f"LLM provider '{self.provider}' needs {', '.join(missing)}")
        if self.fallback_provider is not None:
            if not self.fallback_model:
                raise ValueError("fallback_provider needs fallback_model")
            fallback_missing = _missing_for(self.fallback_provider, self.fallback_api_key, self.endpoint)
            if fallback_missing:
                raise ValueError(
                    f"fallback provider '{self.fallback_provider}' needs {', '.join(fallback_missing)}"
                )
        return self


def _missing_for(provider: Provider, key: SecretStr | None, endpoint: str | None) -> list[str]:
    missing = []
    if provider != "fake" and key is None:
        missing.append("an API key (FAA_LLM__API_KEY)")
    if provider == "azure_openai" and not endpoint:
        missing.append("an endpoint (FAA_LLM__ENDPOINT)")
    return missing


class InvestigationSettings(BaseModel):
    """The budget guard: Investigations are the only thing that spends money (ADR-0004)."""

    enabled: bool = True
    daily_limit: int = Field(default=20, ge=0)
    queue_capacity: int = Field(default=1000, ge=1)
    # The Reviewer and the Investigator's retry each cost a model call; this bounds one Investigation's spend.
    max_attempts: int = Field(default=2, ge=1, le=3)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FAA_", env_nested_delimiter="__", env_file=".env", extra="ignore", frozen=True
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "sqlite+aiosqlite:///./fraud_audit_assist.db"
    pack_dir: Path = Path("packs/investor_portal")
    rules_version: str | None = None

    ingest_api_key: SecretStr | None = None
    analyst_api_key: SecretStr | None = None

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True
    otel_exporter_otlp_endpoint: str | None = None
    service_name: str = "fraud-audit-assist"

    llm: LlmSettings = LlmSettings()
    investigation: InvestigationSettings = InvestigationSettings()

    @model_validator(mode="after")
    def _production_is_locked_down(self) -> Settings:
        if self.environment != "production":
            return self
        problems = []
        if self.ingest_api_key is None or self.analyst_api_key is None:
            problems.append("FAA_INGEST_API_KEY and FAA_ANALYST_API_KEY must be set")
        if self.llm.provider == "fake":
            problems.append("the fake LLM provider is for development and tests only")
        if self.database_url.startswith("sqlite"):
            problems.append("SQLite is single-writer and for development only; use PostgreSQL")
        if problems:
            raise ValueError("Unsafe production settings: " + "; ".join(problems))
        return self

    @property
    def auth_enabled(self) -> bool:
        return self.ingest_api_key is not None or self.analyst_api_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

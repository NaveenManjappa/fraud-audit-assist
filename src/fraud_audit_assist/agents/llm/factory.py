"""Builds the configured chat model. Agents only ever see `BaseChatModel`, never the provider (ADR-0009)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.rate_limiters import InMemoryRateLimiter
from pydantic import SecretStr

from fraud_audit_assist.agents.llm.fake import ScriptedChatModel
from fraud_audit_assist.config import LlmSettings, Provider


def build_chat_model(
    settings: LlmSettings, provider: Provider, model: str, api_key: SecretStr | None
) -> BaseChatModel:
    # The gateway owns retries and timeouts so every provider behaves the same; the SDKs' own retries are off so
    # attempts never multiply.
    rate_limiter = (
        InMemoryRateLimiter(requests_per_second=settings.requests_per_second)
        if settings.requests_per_second
        else None
    )
    common: dict[str, Any] = {"max_retries": 0, "rate_limiter": rate_limiter, "temperature": 0}
    match provider:
        case "fake":
            return ScriptedChatModel()
        case "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=model, api_key=api_key, timeout=settings.timeout_seconds, **common)
        case "azure_openai":
            from langchain_openai import AzureChatOpenAI

            return AzureChatOpenAI(
                azure_endpoint=settings.endpoint,
                azure_deployment=model,
                api_version=settings.api_version,
                api_key=api_key,
                timeout=settings.timeout_seconds,
                **common,
            )
        case "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model_name=model,
                api_key=api_key or SecretStr(""),
                default_request_timeout=settings.timeout_seconds,
                **common,
            )
    raise ValueError(f"Unknown LLM provider '{provider}'")  # pragma: no cover - Provider is a closed Literal

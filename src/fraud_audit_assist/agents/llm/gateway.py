"""Every model call goes through the gateway: timeout, retries with backoff, fallback, logging, metrics, cost."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from fraud_audit_assist.config import LlmSettings
from fraud_audit_assist.domain.cases import AgentUsage
from fraud_audit_assist.errors import LlmError
from fraud_audit_assist.observability.logging import get_logger
from fraud_audit_assist.observability.metrics import Metrics
from fraud_audit_assist.observability.tracing import tracer

log = get_logger(__name__)

_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_RETRYABLE_NAMES = (
    "Timeout",
    "Connection",
    "RateLimit",
    "Overloaded",
    "ServiceUnavailable",
    "InternalServer",
)


def is_retryable(error: BaseException) -> bool:
    """Transient failures worth another attempt: timeouts, dropped connections, rate limits and server errors.

    Provider SDKs raise their own exception types, so this reads the HTTP status where there is one and otherwise
    the exception's name; a bad request or a rejected key is never retried.
    """
    if isinstance(error, TimeoutError | ConnectionError):
        return True
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS
    return any(name in type(error).__name__ for name in _RETRYABLE_NAMES)


@dataclass(frozen=True)
class ModelEndpoint:
    name: str
    model: BaseChatModel


@dataclass(frozen=True)
class LlmReply:
    message: AIMessage
    usage: AgentUsage


class LlmGateway:
    def __init__(
        self,
        primary: ModelEndpoint,
        settings: LlmSettings,
        metrics: Metrics,
        fallback: ModelEndpoint | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._settings = settings
        self._metrics = metrics

    @property
    def model_name(self) -> str:
        return self._primary.name

    async def chat(
        self, agent: str, prompt_version: str, messages: Sequence[BaseMessage], tools: Sequence[BaseTool] = ()
    ) -> LlmReply:
        """One model round trip. Raises `LlmError` once the primary and any fallback have both failed."""
        endpoints = [self._primary] + ([self._fallback] if self._fallback else [])
        last_error: BaseException | None = None
        for endpoint in endpoints:
            try:
                return await self._call(endpoint, agent, prompt_version, messages, tools)
            except Exception as error:
                last_error = error
                self._metrics.llm_calls.labels(agent, "error").inc()
                log.warning(
                    "llm call failed",
                    agent=agent,
                    model=endpoint.name,
                    prompt_version=prompt_version,
                    error_type=type(error).__name__,
                    error=str(error)[:300],
                    fallback_next=endpoint is not endpoints[-1],
                )
        raise LlmError(
            f"{agent}: model call failed: {type(last_error).__name__}: {last_error}"
        ) from last_error

    async def _call(
        self,
        endpoint: ModelEndpoint,
        agent: str,
        prompt_version: str,
        messages: Sequence[BaseMessage],
        tools: Sequence[BaseTool],
    ) -> LlmReply:
        runnable = endpoint.model.bind_tools(list(tools)) if tools else endpoint.model
        started = time.perf_counter()
        attempts = 0
        with tracer().start_as_current_span(f"chat {agent}") as span:
            span.set_attribute("gen_ai.request.model", endpoint.name)
            span.set_attribute("faa.prompt_version", prompt_version)
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(is_retryable),
                stop=stop_after_attempt(self._settings.max_retries + 1),
                wait=wait_exponential_jitter(multiplier=0.5, max=8.0, jitter=0.5),
                reraise=True,
            ):
                with attempt:
                    attempts += 1
                    reply = await asyncio.wait_for(
                        runnable.ainvoke(list(messages)), self._settings.timeout_seconds
                    )
            latency_ms = int((time.perf_counter() - started) * 1000)
            if not isinstance(reply, AIMessage):
                raise TypeError(f"expected an AIMessage, got {type(reply).__name__}")
            usage = reply.usage_metadata or {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            input_tokens, output_tokens = int(usage["input_tokens"]), int(usage["output_tokens"])
            cost = (
                input_tokens / 1000 * self._settings.input_price_per_1k
                + output_tokens / 1000 * self._settings.output_price_per_1k
            )
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)

        self._metrics.llm_calls.labels(agent, "ok").inc()
        self._metrics.llm_tokens.labels(agent, "input").inc(input_tokens)
        self._metrics.llm_tokens.labels(agent, "output").inc(output_tokens)
        self._metrics.llm_cost.labels(agent).inc(cost)
        self._metrics.llm_seconds.labels(agent).observe(latency_ms / 1000)
        log.info(
            "llm call",
            agent=agent,
            model=endpoint.name,
            prompt_version=prompt_version,
            latency_ms=latency_ms,
            attempts=attempts,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=round(cost, 6),
            tool_calls=len(reply.tool_calls),
        )
        return LlmReply(
            message=reply,
            usage=AgentUsage(
                agent=agent,
                prompt_version=prompt_version,
                model=endpoint.name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                latency_ms=latency_ms,
            ),
        )

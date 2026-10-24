"""OpenTelemetry tracing. Spans are always created; they are exported only when an OTLP endpoint is configured."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

TRACER_NAME = "fraud_audit_assist"


def configure_tracing(service_name: str, otlp_endpoint: str | None) -> None:
    """Installs the global tracer provider once; later calls (another app in the same process) keep the first."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint.rstrip('/')}/v1/traces"))
        )
    trace.set_tracer_provider(provider)


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None

"""Prometheus metrics for the key paths: ingestion, detection, Cases and LLM spend.

Fraud Alerts are a product feature stored in the database; these are operational signals for engineers.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram


class Metrics:
    """One set of instruments per registry, so each test app gets its own counters."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.events_ingested = Counter(
            "faa_events_ingested_total", "Events accepted, by outcome", ["outcome"], registry=self.registry
        )
        self.rule_hits = Counter(
            "faa_rule_hits_total", "Rule Hits stored, by rule", ["rule_id"], registry=self.registry
        )
        self.alert_bands = Counter(
            "faa_alert_band_changes_total", "Alerts reaching a band", ["band"], registry=self.registry
        )
        self.cases_opened = Counter("faa_cases_opened_total", "Cases opened", registry=self.registry)
        self.ingest_seconds = Histogram(
            "faa_ingest_seconds", "Ingest request latency", registry=self.registry
        )
        self.llm_tokens = Counter(
            "faa_llm_tokens_total",
            "LLM tokens, by agent and direction",
            ["agent", "direction"],
            registry=self.registry,
        )
        self.llm_cost = Counter(
            "faa_llm_cost_total", "Estimated LLM cost, by agent", ["agent"], registry=self.registry
        )
        self.llm_calls = Counter(
            "faa_llm_calls_total",
            "LLM calls, by agent and outcome",
            ["agent", "outcome"],
            registry=self.registry,
        )
        self.llm_seconds = Histogram("faa_llm_seconds", "LLM call latency", ["agent"], registry=self.registry)
        self.investigations = Counter(
            "faa_investigations_total",
            "Investigations finished, by outcome",
            ["outcome"],
            registry=self.registry,
        )
        self.investigation_seconds = Histogram(
            "faa_investigation_seconds",
            "Seconds from start to Await Approval",
            registry=self.registry,
            buckets=(1, 5, 10, 30, 60, 120, 300),
        )

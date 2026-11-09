"""Prometheus metrics (spec Section 14.2), one registry per app instance."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter("lorde_requests", "Requests by route and status", ["route", "status"],
                                registry=self.registry)
        self.latency = Histogram("lorde_request_seconds", "Request latency by route", ["route"],
                                 buckets=(0.1, 0.25, 0.5, 1, 1.5, 2, 3, 5, 10, 30), registry=self.registry)
        self.judgments = Counter("lorde_judgments", "Judgments by gate and decision", ["kind", "decision"],
                                 registry=self.registry)
        self.unparseable = Counter("lorde_judge_unparseable", "Replies that failed the judge contract", ["kind"],
                                   registry=self.registry)
        self.template = Counter("lorde_template", "Chat-completion calls by template fingerprint match",
                                ["match"], registry=self.registry)
        self.rate_limited = Counter("lorde_rate_limited", "Requests refused by the rate limiter",
                                    registry=self.registry)

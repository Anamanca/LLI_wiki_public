"""Prometheus-backed metrics adapter.

Concrete implementation of MetricsPort using the prometheus_client library.
Thread-safe (prometheus_client handles its own internal locking).
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest


class PrometheusMetricsAdapter:
    """Prometheus metrics adapter implementing the MetricsPort contract.

    Lazily creates metric objects on first use to avoid metric name conflicts
    and keep init fast. All I/O is in-memory — no network calls.
    """

    def __init__(self) -> None:
        self._registry: CollectorRegistry = CollectorRegistry(auto_describe=True)
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._gauges: dict[str, Gauge] = {}

    def counter(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        if name not in self._counters:
            label_keys = sorted(labels.keys()) if labels else []
            self._counters[name] = Counter(name, name, label_keys, registry=self._registry)
        metric = self._counters[name]
        if labels:
            metric.labels(**labels).inc(value)
        else:
            metric.inc(value)

    def histogram(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        if name not in self._histograms:
            label_keys = sorted(labels.keys()) if labels else []
            self._histograms[name] = Histogram(name, name, label_keys, registry=self._registry)
        metric = self._histograms[name]
        if labels:
            metric.labels(**labels).observe(value)
        else:
            metric.observe(value)

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        if name not in self._gauges:
            label_keys = sorted(labels.keys()) if labels else []
            self._gauges[name] = Gauge(name, name, label_keys, registry=self._registry)
        metric = self._gauges[name]
        if labels:
            metric.labels(**labels).set(value)
        else:
            metric.set(value)

    def get_registry(self) -> CollectorRegistry:
        return self._registry

    def get_metrics_response(self) -> bytes:
        return generate_latest(self._registry)

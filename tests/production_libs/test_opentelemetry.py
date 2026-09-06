"""`libs/opentelemetry.py` — the registry's readings as OTLP metrics (§5.11.3).

The pack answers the same seam `libs/prometheus.py` does and makes the
opposite trade: OTLP has no way to carry a pre-aggregated histogram
through an asynchronous instrument, so this exporter says so rather than
inventing a shape for it. What it exports is asserted here, and so is what
it deliberately does not:

* **A counter stays a counter and a gauge stays a gauge**, under the
  registry's own name, with the registry's label names as attributes.
* **A histogram exports its `count` and `sum` and nothing else** — named
  by the two suffixes this module owns. Its buckets would need an `le`
  attribute per bound, which is a Prometheus convention rather than an
  OTLP one, and a shape nobody could read back is worse than an absence
  the docstring names.
* **`protocol` is a closed vocabulary and its wire spelling lives in the
  method.** `OTEL_PROTOCOLS` is snake_case like every other vocabulary;
  the library's own `http/protobuf` is produced where the library is
  named and nowhere else.
* **A failing exporter fails nothing.** The endpoint is read from the
  environment by NAME, and an absent variable is a swallowed, counted
  failure — never a raise into a tick.

The library is named only inside a method, so the translation is asserted
through the one hook a subclass overrides, exactly as the prometheus and
calendar packs are. The tests that need the SDK ask for it through
`pytest.importorskip` and drive it through an in-memory reader, so nothing
here opens a socket.
"""

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.libs.opentelemetry import (
    COUNT_SUFFIX,
    DEFAULT_INTERVAL_S,
    SUM_SUFFIX,
    OtelSink,
)
from dskit.production.metrics import METRIC_SINK_KINDS, Metrics, MetricSink

# ---------------------------------------------------------------------------
# The delivery seam, overridden — the pack without its library
# ---------------------------------------------------------------------------


class Recording(OtelSink):
    """A sink whose delivery records what it was handed instead of exporting it."""

    def __init__(self, params=None):
        super().__init__(params)
        self.delivered = []

    def _deliver(self, samples, at_ms):
        self.delivered.append((samples, at_ms))


def rows(sink):
    """The last delivery as `(family, name, labels, value)` tuples, sorted."""
    samples, _at_ms = sink.delivered[-1]
    return sorted(
        (sample.family, sample.name, tuple(sorted(sample.labels.items())), sample.value)
        for sample in samples
    )


def sink(**params):
    """A recording sink over the grpc protocol."""
    return Recording({"endpoint_env": "OTLP_ENDPOINT", "protocol": "grpc", **params})


# ---------------------------------------------------------------------------
# The registration §4.3 calls import
# ---------------------------------------------------------------------------


def test_the_pack_registers_opentelemetry_into_the_metric_sink_family():
    assert METRIC_SINK_KINDS.resolve("opentelemetry") is OtelSink
    assert "opentelemetry" in METRIC_SINK_KINDS
    assert issubclass(OtelSink, MetricSink)


def test_the_kind_is_the_label_value_the_failure_counter_declares():
    assert sink().kind == "opentelemetry"
    assert "opentelemetry" in vocab.METRIC_LABEL_VALUES["metric_sink_failures_total"]["sink"]


def test_the_protocols_are_the_closed_vocabulary_and_are_snake_case():
    # §5.0: a closed set lives in `vocab.py` and its members are tokens.
    # The library's own `http/protobuf` spelling is a library fact and
    # belongs where the library is named.
    assert vocab.OTEL_PROTOCOLS == ("grpc", "http_protobuf")


# ---------------------------------------------------------------------------
# Default-deny
# ---------------------------------------------------------------------------


class TestParams:
    def test_an_unknown_knob_refuses(self):
        with pytest.raises(ProductionError):
            OtelSink({"endpoint_env": "OTLP_ENDPOINT", "protocol": "grpc", "insecure": True})

    def test_notes_is_allowed_like_everywhere(self):
        assert sink(notes="shipped to the ops collector")

    def test_the_endpoint_is_an_env_var_name_never_a_url(self):
        with pytest.raises(ProductionError, match="endpoint_env"):
            OtelSink({"endpoint_env": "http://collector:4317", "protocol": "grpc"})

    def test_a_missing_endpoint_env_refuses(self):
        with pytest.raises(ProductionError, match="endpoint_env"):
            OtelSink({"protocol": "grpc"})

    def test_a_protocol_outside_the_vocabulary_refuses_naming_it(self):
        with pytest.raises(ProductionError) as exc:
            OtelSink({"endpoint_env": "OTLP_ENDPOINT", "protocol": "http/protobuf"})
        assert "grpc" in str(exc.value) and "http_protobuf" in str(exc.value)

    def test_a_missing_protocol_refuses_rather_than_guessing_a_wire_format(self):
        with pytest.raises(ProductionError, match="protocol"):
            OtelSink({"endpoint_env": "OTLP_ENDPOINT"})

    def test_the_export_interval_has_one_named_default_and_must_be_positive(self):
        assert sink().settings["interval_s"] == DEFAULT_INTERVAL_S
        assert sink(interval_s=5).settings["interval_s"] == 5
        for bad in (0, -1, "60"):
            with pytest.raises(ProductionError, match="interval_s"):
                OtelSink({"endpoint_env": "OTLP_ENDPOINT", "protocol": "grpc", "interval_s": bad})

    def test_the_resource_map_is_flat_and_scalar(self):
        assert sink(resource={"service.name": "serve", "replica": 2}).settings["resource"] == {
            "service.name": "serve",
            "replica": 2,
        }
        with pytest.raises(ProductionError, match="resource"):
            OtelSink(
                {
                    "endpoint_env": "OTLP_ENDPOINT",
                    "protocol": "grpc",
                    "resource": {"nested": {"deep": 1}},
                }
            )

    def test_a_resource_value_that_would_be_masked_refuses(self):
        # §5.11.3: a NON-SECRET attribute map. Every exported point
        # carries it, so a credential in there is a credential on the
        # wire; `redact` is the one owner of what a credential looks like.
        with pytest.raises(ProductionError, match="resource"):
            OtelSink(
                {
                    "endpoint_env": "OTLP_ENDPOINT",
                    "protocol": "grpc",
                    "resource": {"gateway": "https://ops.example/hooks/abc"},
                }
            )


# ---------------------------------------------------------------------------
# The translation
# ---------------------------------------------------------------------------


class TestTheTranslation:
    def test_a_counter_keeps_its_name_and_carries_its_labels_as_attributes(self):
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(2, status="decided")
        exporter = sink()
        registry.subscribe(exporter)
        registry.flush(1_767_225_600_000, "tick-1")
        assert ("counter", "ticks_total", (("status", "decided"),), 2) in rows(exporter)
        assert exporter.delivered[-1][1] == 1_767_225_600_000

    def test_a_gauge_is_a_gauge(self):
        registry = Metrics()
        registry.gauge("ledger_append_seconds").set(0.25)
        exporter = sink()
        registry.subscribe(exporter)
        registry.flush(1, "tick-1")
        assert ("gauge", "ledger_append_seconds", (), 0.25) in rows(exporter)

    def test_a_histogram_exports_its_count_and_sum_and_drops_its_buckets(self):
        registry = Metrics()
        registry.histogram("tick_seconds", labels=("phase",), buckets=(0.1, 1.0)).observe(
            0.05, phase="evaluate"
        )
        exporter = sink()
        registry.subscribe(exporter)
        registry.flush(1, "tick-1")
        exported = [row for row in rows(exporter) if row[1].startswith("tick_seconds")]
        assert exported == [
            ("counter", "tick_seconds" + COUNT_SUFFIX, (("phase", "evaluate"),), 1),
            ("counter", "tick_seconds" + SUM_SUFFIX, (("phase", "evaluate"),), 0.05),
        ]

    def test_a_declared_metric_with_no_recording_exports_no_point(self):
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",))
        exporter = sink()
        registry.subscribe(exporter)
        registry.flush(1, "tick-1")
        assert not [row for row in rows(exporter) if row[1] == "ticks_total"]


# ---------------------------------------------------------------------------
# The library half — driven through an in-memory reader, no socket
# ---------------------------------------------------------------------------


def in_memory(params=None):
    """An `OtelSink` whose reader collects in process rather than over OTLP."""
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()

    class InProcess(OtelSink):
        def _reader(self):
            return reader

    return InProcess(params or {"endpoint_env": "OTLP_ENDPOINT", "protocol": "grpc"}), reader


def points(reader):
    """`{metric name: {attributes: value}}` from one collection."""
    data = reader.get_metrics_data()
    out = {}
    for resource in data.resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                out[metric.name] = {
                    tuple(sorted(point.attributes.items())): point.value
                    for point in metric.data.data_points
                }
    return out


class TestWithTheLibrary:
    def test_the_readings_reach_the_sdk_under_the_registrys_own_names(self):
        pytest.importorskip("opentelemetry.sdk")
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(3, status="decided")
        registry.gauge("ledger_append_seconds").set(0.25)
        exporter, reader = in_memory()
        registry.subscribe(exporter)
        registry.flush(1_767_225_600_000, "tick-1")
        collected = points(reader)
        assert collected["ticks_total"] == {(("status", "decided"),): 3}
        assert collected["ledger_append_seconds"] == {(): 0.25}
        exporter.close()

    def test_a_later_flush_moves_the_reading_rather_than_adding_a_series(self):
        pytest.importorskip("opentelemetry.sdk")
        registry = Metrics()
        counter = registry.counter("ticks_total", labels=("status",))
        exporter, reader = in_memory()
        registry.subscribe(exporter)
        counter.inc(status="decided")
        registry.flush(1, "tick-1")
        counter.inc(status="decided")
        registry.flush(2, "tick-2")
        assert points(reader)["ticks_total"] == {(("status", "decided"),): 2}
        exporter.close()

    def test_the_resource_attributes_ride_with_the_export(self):
        pytest.importorskip("opentelemetry.sdk")
        registry = Metrics()
        registry.gauge("ledger_append_seconds").set(1.0)
        exporter, reader = in_memory(
            {
                "endpoint_env": "OTLP_ENDPOINT",
                "protocol": "grpc",
                "resource": {"service.name": "serve"},
            }
        )
        registry.subscribe(exporter)
        registry.flush(1, "tick-1")
        attributes = reader.get_metrics_data().resource_metrics[0].resource.attributes
        assert attributes["service.name"] == "serve"
        exporter.close()

    def test_an_unset_endpoint_variable_is_swallowed_and_counted(self):
        pytest.importorskip("opentelemetry.sdk")
        registry = Metrics()
        registry.subscribe(
            OtelSink({"endpoint_env": "DSKIT_UNSET_OTLP_ENDPOINT", "protocol": "grpc"})
        )
        assert registry.flush(1, "tick-1") is False
        assert registry.snapshot()["metric_sink_failures_total"] == {"sink=opentelemetry": 1}

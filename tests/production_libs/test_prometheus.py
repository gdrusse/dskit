"""`libs/prometheus.py` — the registry's readings as a Prometheus exposition (§5.11.3).

The pack's whole promise is that it **translates nothing**: §5.11.1 already
pins the registry's names and label sets to Prometheus's own shape, so an
exporter that renamed anything would be a second naming rule nobody
reviewed. That is what most of this file asserts — the samples the sink
builds carry the registry's names, the registry's label NAMES, and the
values it recorded, with the histogram's cumulative buckets intact.

Three refusals carry the rest:

* **`mode` is required.** `pull` binds a port and `push` writes to a
  gateway; a default would silently bind a socket in a serve process whose
  operator asked for neither, so the document says which.
* **A knob the mode cannot use refuses.** `port` under `push` and
  `gateway_url_env` under `pull` are knobs someone believes in and nothing
  reads — the same argument default-deny makes about a typo.
* **A failing exporter fails nothing.** §5.11.3 gives a metric sink the
  swallow-and-count rule a failing JSONL flush gets, so the test that
  matters drives a REAL push to a closed port through `Metrics.flush` and
  asserts the tick-side answer is unchanged and the failure is counted
  under this pack's own kind.

The library is named only inside a method, so every test but the last two
runs on a host where it is not installed: the translation is asserted
through a subclass that overrides the one delivery hook — the same seam
`tests/production_libs/test_exchange_calendars.py` uses for the read it
cannot make. The two that need the library ask for it through
`pytest.importorskip` and skip when it is absent, as every pack test does.
"""

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.libs.prometheus import DEFAULT_ADDR, MAX_PORT, PrometheusSink
from dskit.production.metrics import METRIC_SINK_KINDS, Metrics, MetricSink

# ---------------------------------------------------------------------------
# The delivery seam, overridden — the pack without its library
# ---------------------------------------------------------------------------


class Recording(PrometheusSink):
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


def pull_sink(**params):
    """A recording sink in `pull` mode."""
    return Recording({"mode": "pull", "port": 9_400, **params})


# ---------------------------------------------------------------------------
# The registration §4.3 calls import
# ---------------------------------------------------------------------------


def test_the_pack_registers_prometheus_into_the_metric_sink_family():
    assert METRIC_SINK_KINDS.resolve("prometheus") is PrometheusSink
    assert "prometheus" in METRIC_SINK_KINDS
    assert issubclass(PrometheusSink, MetricSink)


def test_the_kind_is_the_label_value_the_failure_counter_declares():
    # §5.11.3: a swallowed publish is counted under
    # `metric_sink_failures_total{sink}`, whose value set is the pack kind
    # names. The sink's own answer and the vocabulary are one fact.
    assert pull_sink().kind == "prometheus"
    assert "prometheus" in vocab.METRIC_LABEL_VALUES["metric_sink_failures_total"]["sink"]


# ---------------------------------------------------------------------------
# Default-deny, and the two modes
# ---------------------------------------------------------------------------


class TestParams:
    def test_an_unknown_knob_refuses(self):
        with pytest.raises(ProductionError):
            PrometheusSink({"mode": "pull", "port": 9_400, "prot": "http"})

    def test_notes_is_allowed_like_everywhere(self):
        assert pull_sink(notes="scraped by the ops prometheus")

    def test_a_missing_mode_refuses_rather_than_binding_a_socket_by_default(self):
        with pytest.raises(ProductionError, match="mode"):
            PrometheusSink({"port": 9_400})

    def test_an_unknown_mode_refuses_naming_the_two(self):
        with pytest.raises(ProductionError) as exc:
            PrometheusSink({"mode": "scrape", "port": 9_400})
        assert "pull" in str(exc.value) and "push" in str(exc.value)

    def test_pull_needs_a_port(self):
        with pytest.raises(ProductionError, match="port"):
            PrometheusSink({"mode": "pull"})

    def test_a_port_outside_the_range_refuses(self):
        for port in (0, MAX_PORT + 1):
            with pytest.raises(ProductionError, match="port"):
                PrometheusSink({"mode": "pull", "port": port})

    def test_the_pull_address_defaults_to_the_loopback_not_every_interface(self):
        # An exporter that binds every interface by default publishes a
        # serve process's internals to the network nobody asked it to.
        assert DEFAULT_ADDR == "127.0.0.1"
        assert pull_sink().settings["addr"] == DEFAULT_ADDR
        assert pull_sink(addr="10.0.0.4").settings["addr"] == "10.0.0.4"

    def test_push_needs_the_env_var_name_and_a_job(self):
        with pytest.raises(ProductionError) as exc:
            PrometheusSink({"mode": "push"})
        assert "gateway_url_env" in str(exc.value) and "job" in str(exc.value)

    def test_push_takes_the_env_var_name_never_the_url(self):
        # §5.11.3: "the env-var NAME, never the URL". A URL in the
        # document is a credential in the document.
        with pytest.raises(ProductionError, match="gateway_url_env"):
            PrometheusSink(
                {"mode": "push", "gateway_url_env": "http://gw:9091", "job": "serve"}
            )

    def test_a_knob_the_mode_cannot_use_refuses(self):
        with pytest.raises(ProductionError, match="port"):
            PrometheusSink(
                {"mode": "push", "gateway_url_env": "GW", "job": "serve", "port": 9_400}
            )
        with pytest.raises(ProductionError, match="gateway_url_env"):
            PrometheusSink({"mode": "pull", "port": 9_400, "gateway_url_env": "GW"})


# ---------------------------------------------------------------------------
# The translation — the registry's names, unchanged
# ---------------------------------------------------------------------------


class TestTheTranslation:
    def test_a_counter_keeps_its_name_its_label_names_and_its_value(self):
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(2, status="decided")
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1_767_225_600_000, "tick-1")
        assert (
            "counter",
            "ticks_total",
            (("status", "decided"),),
            2,
        ) in rows(sink)

    def test_the_instant_the_loop_flushed_at_is_handed_on(self):
        registry = Metrics()
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1_767_225_600_000, "tick-1")
        assert sink.delivered[-1][1] == 1_767_225_600_000

    def test_a_gauge_is_a_gauge_and_an_unlabelled_series_carries_no_labels(self):
        registry = Metrics()
        registry.gauge("ledger_append_seconds").set(0.25)
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        assert ("gauge", "ledger_append_seconds", (), 0.25) in rows(sink)

    def test_a_histogram_keeps_its_cumulative_buckets_its_count_and_its_sum(self):
        registry = Metrics()
        registry.histogram("tick_seconds", labels=("phase",), buckets=(0.1, 1.0)).observe(
            0.05, phase="evaluate"
        )
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        family, name, labels, value = [row for row in rows(sink) if row[1] == "tick_seconds"][0]
        assert (family, name, labels) == ("histogram", "tick_seconds", (("phase", "evaluate"),))
        assert value["count"] == 1 and value["sum"] == 0.05
        assert value["buckets"] == {"0.1": 1, "1.0": 1, "+Inf": 1}

    def test_the_counter_suffix_decides_the_family_through_the_rule_that_owns_it(self):
        # `metrics.COUNTER_SUFFIX` is what makes a `_total` name a counter
        # at DECLARATION; the exporter reads the same rule rather than a
        # second copy of the string.
        registry = Metrics()
        registry.counter("refusals_total", labels=("reason",)).inc(reason="refused")
        registry.gauge("ledger_append_seconds").set(1.0)
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        families = {
            name: family
            for family, name, _labels, _value in rows(sink)
            if name in ("refusals_total", "ledger_append_seconds")
        }
        assert families == {"refusals_total": "counter", "ledger_append_seconds": "gauge"}

    def test_a_dropped_label_value_exports_as_the_reserved_value(self):
        # The registry never raises on the hot path: an undeclared value
        # becomes `other`. The exporter must show exactly that, or an
        # operator would read a cardinality drop as a missing series.
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(status="nobody_declared_this")
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        assert ("counter", "ticks_total", (("status", "other"),), 1) in rows(sink)

    def test_a_declared_metric_with_no_recording_exports_no_series(self):
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",))
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        assert not [row for row in rows(sink) if row[1] == "ticks_total"]

    def test_the_registrys_own_counters_reach_the_exposition(self):
        # The registry declares two counters for itself; an exporter that
        # dropped them would hide exactly the swallowed failures §5.11.1
        # says the counts are the only evidence of. The labelled one has
        # no series until something fails, so a fresh registry exports the
        # unlabelled one alone.
        registry = Metrics()
        sink = pull_sink()
        registry.subscribe(sink)
        registry.flush(1, "tick-1")
        assert rows(sink) == [
            ("counter", "metrics_label_cardinality_dropped_total", (), 0)
        ]


# ---------------------------------------------------------------------------
# The library halves
# ---------------------------------------------------------------------------


class TestWithTheLibrary:
    def test_the_exposition_carries_the_registrys_own_names(self):
        pytest.importorskip("prometheus_client")
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(3, status="decided")
        registry.gauge("ledger_append_seconds").set(0.25)
        registry.histogram("tick_seconds", labels=("phase",), buckets=(0.1,)).observe(
            0.05, phase="evaluate"
        )
        sink = PrometheusSink({"mode": "pull", "port": 9_400})
        registry.subscribe(sink)
        sink.publish(registry.snapshot(), 1_767_225_600_000)
        text = sink.expose().decode()
        assert 'ticks_total{status="decided"} 3.0' in text
        assert "ledger_append_seconds 0.25" in text
        assert 'tick_seconds_bucket{le="0.1",phase="evaluate"} 1.0' in text
        assert 'tick_seconds_count{phase="evaluate"} 1.0' in text
        assert "# TYPE ticks_total counter" in text
        sink.close()

    def test_a_push_to_a_closed_port_is_swallowed_and_counted_by_the_flush(self, monkeypatch):
        # §5.11.3's headline: a broken exporter can slow nothing and fail
        # no tick. The gateway is a closed loopback port, so the failure is
        # immediate and no network is touched.
        pytest.importorskip("prometheus_client")
        monkeypatch.setenv("DSKIT_TEST_GATEWAY", "http://127.0.0.1:1")
        registry = Metrics()
        registry.counter("ticks_total", labels=("status",)).inc(status="decided")
        registry.subscribe(
            PrometheusSink(
                {"mode": "push", "gateway_url_env": "DSKIT_TEST_GATEWAY", "job": "serve"}
            )
        )
        assert registry.flush(1, "tick-1") is False
        assert registry.snapshot()["metric_sink_failures_total"] == {"sink=prometheus": 1}

    def test_a_gateway_env_var_that_is_not_set_is_a_swallowed_failure_not_a_raise(self):
        pytest.importorskip("prometheus_client")
        registry = Metrics()
        registry.subscribe(
            PrometheusSink(
                {"mode": "push", "gateway_url_env": "DSKIT_UNSET_GATEWAY", "job": "serve"}
            )
        )
        registry.flush(1, "tick-1")
        assert registry.snapshot()["metric_sink_failures_total"] == {"sink=prometheus": 1}

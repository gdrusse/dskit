"""``libs/prometheus.py`` — the registry's readings, exported unchanged (§5.11.3).

``metrics.py`` counts what the serve loop swallows, and §5.11.1 already
pins those counts to Prometheus's own shape: ``snake_case`` names, base
units in the suffix, ``_total`` on counters and only on counters, and a
CLOSED label set per metric. This pack is what carries them out of the
process, and its whole design follows from that pinning — **it translates
nothing**. The exposition's names are :data:`~dskit.production.vocab.METRIC_NAMES`
verbatim, its label names and values are the registry's, and which family
each one exports as is :func:`~dskit.production.metrics.readings`'
answer — the rule that DECLARED the metric, read from the one module that
owns it, rather than a second list kept here.

Three rules carry the module:

* **The mode is declared, never defaulted.** ``pull`` binds a port for
  Prometheus to scrape; ``push`` writes to a gateway. They are different
  operational facts, and a serve process that bound a socket because a
  knob was absent would be exporting its internals to a network nobody
  asked about. A knob the chosen mode cannot use refuses for the same
  reason a typo does — it is a setting someone believes in and nothing
  reads.
* **The push gateway is an env-var NAME.** The document never holds a
  URL: a pushgateway address is a credential-shaped fact and §5.0 keeps
  those out of documents, stores and hashes.
* **Nothing here may fail a tick.** ``Metrics.flush`` swallows and counts
  whatever :meth:`PrometheusSink.publish` raises under
  ``metric_sink_failures_total{sink="prometheus"}``, so this module
  refuses loudly (a missing env var, a closed gateway) rather than
  defending itself — the count is the evidence and the tick is untouched.

The delivery is a strategy object per mode rather than a branch, and
:meth:`PrometheusSink._deliver` is the one hook a subclass overrides to
send the readings somewhere else — which is also how the suite asserts the
translation on a host with no ``prometheus_client`` installed.

``prometheus_client`` is named only inside a method, per §8's tier-2 rule:
importing this pack must not import the library, and a serve document that
declares no ``prometheus`` sink never loads it.
"""

import os
from abc import ABC, abstractmethod

from dskit.pipeline.node import check_int_param
from dskit.production import vocab
from dskit.production.base import ProductionError, _check_str, pin_members
from dskit.production.metrics import METRIC_SINK_KINDS, MetricSink, readings

__all__ = [
    "DEFAULT_ADDR",
    "MAX_PORT",
    "PrometheusSink",
]

#: What a ``pull`` endpoint binds when the document names no ``addr``: the
#: loopback, never every interface. An exporter reachable from the network
#: by DEFAULT publishes a serve process's internals to whoever asks.
DEFAULT_ADDR = "127.0.0.1"

#: The highest TCP port there is; a ``port`` above it is a typo.
MAX_PORT = 65_535

#: The HELP line every family carries. The registry holds no per-metric
#: documentation — §5.11.1's table is names and label values — so one
#: honest line beats a sentence per metric invented here.
_HELP = "dskit.production serve metric"

def _label_values(sample, names):
    """Return one sample's label values in ``names`` order."""
    return [sample.labels[name] for name in names]


def _scalar_family(builder, name, samples):
    """Build one counter or gauge family from every series of one metric."""
    names = sorted(samples[0].labels)
    family = builder(name, _HELP, labels=names)
    for sample in samples:
        family.add_metric(_label_values(sample, names), sample.value)
    return family


def _counter_family(core, name, samples):
    """Build the counter family ``name``'s series make."""
    return _scalar_family(core.CounterMetricFamily, name, samples)


def _gauge_family(core, name, samples):
    """Build the gauge family ``name``'s series make."""
    return _scalar_family(core.GaugeMetricFamily, name, samples)


def _histogram_family(core, name, samples):
    """Build the histogram family ``name``'s series make, buckets cumulative."""
    names = sorted(samples[0].labels)
    family = core.HistogramMetricFamily(name, _HELP, labels=names)
    for sample in samples:
        family.add_metric(
            _label_values(sample, names),
            [(bound, count) for bound, count in sample.value["buckets"].items()],
            sum_value=sample.value["sum"],
        )
    return family


#: Family -> the builder that turns one metric's series into the library's
#: own family object. A table rather than a branch, and every builder is
#: HANDED the library module, so none of them names it. Keyed by the whole
#: vocabulary and pinned EXACTLY: a family with no builder here is a
#: reading the exposition would silently drop.
_FAMILIES = pin_members(
    "libs/prometheus.py's family builders",
    {
        "counter": _counter_family,
        "gauge": _gauge_family,
        "histogram": _histogram_family,
    },
    vocab.METRIC_FAMILIES,
    exact=True,
)


class _Endpoint(ABC):
    """One delivery mode: the knobs it reads, and what it does with a collector.

    Parameters
    ----------
    params : dict
        The sink's validated params; each mode takes only its own knobs.
    """

    #: The knobs this mode reads. Every other mode's knobs refuse on it.
    KNOBS = ()

    @classmethod
    def check(cls, problems, params):
        """Append every problem with this mode's own knobs.

        Parameters
        ----------
        problems : list of str
            The accumulator.
        params : dict
            The params block as written in the document.

        Returns
        -------
        None
            Problems are appended.
        """

    @property
    def settings(self):
        """Return the mode's resolved knobs, defaults applied."""
        return {}

    @abstractmethod
    def deliver(self, registry):
        """Hand the library's collector registry to wherever this mode delivers.

        Parameters
        ----------
        registry : object
            The library's ``CollectorRegistry``.

        Returns
        -------
        None
            The answer is the delivery; a failure raises and is swallowed
            and counted by ``Metrics.flush``.
        """

    def close(self):
        """Release whatever the mode holds open; most modes hold nothing.

        Returns
        -------
        None
            Nothing is held by default.
        """


class _PullEndpoint(_Endpoint):
    """``mode: pull`` — a local HTTP endpoint Prometheus scrapes."""

    KNOBS = ("addr", "port")

    @classmethod
    def check(cls, problems, params):
        """Require a port in range; the address is a name when it is given."""
        port = params.get("port")
        check_int_param(problems, "port", port, ge=1)
        if isinstance(port, int) and not isinstance(port, bool) and port > MAX_PORT:
            problems.append(f"port must be at most {MAX_PORT}, got {port!r}")
        if "addr" in params:
            _check_str(problems, "addr", params["addr"])

    def __init__(self, params):
        self._addr = params.get("addr", DEFAULT_ADDR)
        self._port = int(params["port"])
        self._server = None

    @property
    def settings(self):
        """Return the address and port the endpoint binds."""
        return {"addr": self._addr, "port": self._port}

    def deliver(self, registry):
        """Start the scrape endpoint on the first delivery; serve the same registry after.

        Parameters
        ----------
        registry : object
            The library's ``CollectorRegistry``, which reads the sink's
            latest readings at scrape time.

        Returns
        -------
        None
            The endpoint is the delivery; later publishes only refresh
            what a scrape will find.
        """
        from prometheus_client import start_http_server

        if self._server is None:
            self._server, _thread = start_http_server(self._port, self._addr, registry=registry)

    def close(self):
        """Shut the scrape endpoint down.

        Returns
        -------
        None
            Idempotent: a sink closed twice, or never started, is fine.
        """
        if self._server is not None:
            self._server.shutdown()
            self._server = None


class _PushGateway(_Endpoint):
    """``mode: push`` — one write per flush to a Prometheus pushgateway."""

    KNOBS = ("gateway_url_env", "job")

    @classmethod
    def check(cls, problems, params):
        """Require the env-var NAME and the job; a URL in the document refuses."""
        gateway = params.get("gateway_url_env")
        _check_str(problems, "gateway_url_env", gateway)
        if isinstance(gateway, str) and "://" in gateway:
            problems.append(
                "gateway_url_env is the NAME of an environment variable holding the "
                f"gateway URL, not the URL itself, got {gateway!r}"
            )
        _check_str(problems, "job", params.get("job"))

    def __init__(self, params):
        self._gateway_url_env = params["gateway_url_env"]
        self._job = params["job"]

    @property
    def settings(self):
        """Return the env-var name and job label; never the resolved URL."""
        return {"gateway_url_env": self._gateway_url_env, "job": self._job}

    def deliver(self, registry):
        """Push the current readings to the gateway the environment names.

        Parameters
        ----------
        registry : object
            The library's ``CollectorRegistry``.

        Returns
        -------
        None
            The push is the delivery.

        Raises
        ------
        ProductionError
            If the environment does not hold the named variable. Raising
            is correct: ``Metrics.flush`` counts it under
            ``metric_sink_failures_total`` and the tick never sees it.
        """
        from prometheus_client import push_to_gateway

        gateway = os.environ.get(self._gateway_url_env)
        if not gateway:
            raise ProductionError(
                [f"the environment holds no {self._gateway_url_env}, so there is no gateway to push to"]
            )
        push_to_gateway(gateway, job=self._job, registry=registry)


#: Mode -> its delivery strategy. The keys ARE the modes a document may
#: name, so there is no second list of them to drift.
_MODES = {"pull": _PullEndpoint, "push": _PushGateway}


class PrometheusSink(MetricSink):
    """The metric registry, exported to Prometheus by scrape or by push.

    Registered as ``prometheus`` in
    :data:`~dskit.production.metrics.METRIC_SINK_KINDS` (§4.3: import is
    registration), and declared in ``placement.metric_sinks`` — an
    exporter is placement, never policy, because a metric is never an
    input to a decision, a guard or a record (§5.11.1).

    Nothing is renamed on the way out: the exposition carries
    ``vocab.METRIC_NAMES`` and the registry's own label sets, and the
    family of each metric is decided by the rule that declared it — a
    ``_total`` name is a counter, a series holding buckets is a histogram,
    anything else is a gauge.

    Parameters
    ----------
    params : dict
        ``mode`` (``"pull"`` or ``"push"``, required — a default would
        bind a socket nobody asked for); under ``pull``: ``port`` (int in
        ``[1, MAX_PORT]``, required) and ``addr`` (str, default
        :data:`DEFAULT_ADDR`); under ``push``: ``gateway_url_env`` (the
        NAME of the environment variable holding the gateway URL,
        required) and ``job`` (str, required). ``notes`` is allowed, as
        everywhere.

    Attributes
    ----------
    KIND : str
        ``"prometheus"`` — the value ``metric_sink_failures_total{sink}``
        counts a swallowed publish under.

    Examples
    --------
    An endpoint on the loopback for a local Prometheus to scrape::

        sink = PrometheusSink({"mode": "pull", "port": 9400})
        sink.settings           # {'mode': 'pull', 'addr': '127.0.0.1', 'port': 9400}
        registry = Metrics()
        registry.subscribe(sink)
        registry.flush(1_767_225_600_000, "tick-1")   # publishes, then writes the JSONL
        sink.close()
    """

    KIND = "prometheus"
    _PARAMS = ("mode",) + tuple(
        sorted({knob for endpoint in _MODES.values() for knob in endpoint.KNOBS})
    )

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when it is acceptable.

        The library is never asked for anything here: a document is
        validated on hosts that do not have it installed, and a malformed
        block must refuse identically wherever it is read.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Accumulated problems: unknown keys, an unknown ``mode``, the
            mode's own knobs, and any knob belonging to the OTHER mode.
        """
        problems = super().validate_params(params)
        endpoint = _MODES.get(params.get("mode"))
        if endpoint is None:
            problems.append(
                f"mode must be one of {sorted(_MODES)}, got {params.get('mode')!r}"
            )
            return problems
        endpoint.check(problems, params)
        foreign = {
            knob
            for other in _MODES.values()
            if other is not endpoint
            for knob in other.KNOBS
        } - set(endpoint.KNOBS)
        for knob in sorted(foreign & set(params)):
            problems.append(
                f"{knob} is not a knob mode {params['mode']!r} reads — it belongs to the "
                f"other mode, and a setting nothing reads is a setting someone believes in"
            )
        return problems

    def _configure(self, params):
        """Bind the delivery strategy the validated mode names; touch no library."""
        self._name = params["mode"]
        self._endpoint = _MODES[self._name](params)
        self._samples = ()
        self._registry = None

    @property
    def settings(self):
        """Return the resolved knobs — the mode and its own, defaults applied.

        Returns
        -------
        dict
            What an operator's readout would show; never a resolved URL.
        """
        return {"mode": self._name, **self._endpoint.settings}

    def publish(self, snapshot, at_ms):
        """Translate one flush's readings and deliver them.

        Parameters
        ----------
        snapshot : dict
            ``{name: {series key: value}}`` — the registry's own copy.
        at_ms : int
            The instant the loop flushed at.

        Returns
        -------
        None
            The answer is the export; a failure raises and
            ``Metrics.flush`` counts it.
        """
        self._deliver(readings(snapshot), at_ms)

    def _deliver(self, samples, at_ms):
        """Hold the readings and hand the collector registry to the mode."""
        self._samples = samples
        self._endpoint.deliver(self._collector())

    def _collector(self):
        """Return the library registry this sink collects into, built once."""
        from prometheus_client import CollectorRegistry

        if self._registry is None:
            self._registry = CollectorRegistry()
            self._registry.register(self)
        return self._registry

    def collect(self):
        """Yield one library metric family per metric — the collector contract.

        The sink IS the collector: a scrape and a push both ask this, so
        the readings a scrape finds are the ones the last flush left and
        nothing has to be copied into a second registry.

        Returns
        -------
        iterator
            The library's metric family objects, one per declared metric
            that holds at least one series.
        """
        from prometheus_client import core

        grouped = {}
        for sample in self._samples:
            grouped.setdefault(sample.name, []).append(sample)
        for name, samples in grouped.items():
            yield _FAMILIES[samples[0].family](core, name, samples)

    def expose(self):
        """Return the current exposition, as a scrape would read it.

        Returns
        -------
        bytes
            The library's text rendering of the last published readings.
        """
        from prometheus_client import generate_latest

        return generate_latest(self._collector())

    def close(self):
        """Stop the scrape endpoint, if this sink started one.

        Returns
        -------
        None
            Idempotent, and swallowed by ``Metrics.close`` like every
            other exporter's.
        """
        self._endpoint.close()


METRIC_SINK_KINDS.register("prometheus", PrometheusSink)

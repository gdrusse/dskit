"""``libs/opentelemetry.py`` — the registry's readings as OTLP metrics (§5.11.3).

The second exporter over ``metrics.py``'s :class:`~dskit.production.metrics.MetricSink`
seam, and the one that has to say what it cannot carry. OTLP's model is
asynchronous: an exporter registers instruments whose CALLBACKS are read on
the collector's own schedule, which fits a registry whose values are
absolute readings rather than deltas. So a counter becomes an observable
counter, a gauge an observable gauge, and each series' labels become that
point's attributes — names unchanged, exactly as the Prometheus pack
leaves them.

**A histogram exports its ``count`` and its ``sum``, and its buckets are
dropped.** A pre-aggregated bucket set has no asynchronous instrument in
OTLP; carrying it would mean inventing an ``le`` attribute per bound,
which is a Prometheus convention wearing OTLP's clothes and a shape
nobody could read back. Naming the omission here is worth more than a
translation no consumer would understand — and the Prometheus pack
carries the buckets faithfully for anyone who needs them.

Three rules carry the rest:

* **The endpoint is an env-var NAME** and the resource attributes are
  refused when :func:`~dskit.production.redact.redact` would mask one:
  every exported point carries the resource, so a credential in there is
  a credential on the wire.
* **The protocol is a closed vocabulary.**
  :data:`~dskit.production.vocab.OTEL_PROTOCOLS` is snake_case like every
  other vocabulary; the library's own ``http/protobuf`` spelling is a
  library fact and appears only inside the method that imports the
  library, keyed by the same table that chooses the exporter class.
* **Nothing here may fail a tick.** ``Metrics.flush`` swallows and counts
  whatever :meth:`OtelSink.publish` raises under
  ``metric_sink_failures_total{sink="opentelemetry"}``, so an unset
  endpoint variable refuses loudly rather than exporting to nowhere.

``opentelemetry`` is named only inside :meth:`OtelSink._reader` and
:meth:`OtelSink._meter`, per §8's tier-2 rule: importing this pack must not
import the library, and a serve document that declares no
``opentelemetry`` sink never loads it.
"""

import os

from dskit.production import vocab
from dskit.production.base import ProductionError, _check_dict, _check_str, pin_members
from dskit.production.metrics import METRIC_SINK_KINDS, MetricSink, Reading, readings
from dskit.production.redact import redact

__all__ = [
    "COUNT_SUFFIX",
    "DEFAULT_INTERVAL_S",
    "INSTRUMENTATION_SCOPE",
    "SUM_SUFFIX",
    "OtelSink",
]

#: How often the reader exports when the document names no ``interval_s``.
#: One minute is the OTLP SDK's own default and a serve loop's ticks are
#: seconds apart, so a shorter one exports the same readings repeatedly.
DEFAULT_INTERVAL_S = 60.0

#: What a histogram's two carried numbers are named. The suffixes are this
#: module's, because the split is this module's: the registry declares one
#: metric and OTLP receives two.
COUNT_SUFFIX = "_count"
SUM_SUFFIX = "_sum"

#: The scope every instrument is created under — the package that
#: produced the reading, which is what a scope names.
INSTRUMENTATION_SCOPE = "dskit.production"

#: The scalar types a resource attribute may hold — OTLP's own set. A
#: nested object or a list is refused rather than flattened: a document
#: whose meaning depended on how it was flattened is a document nobody
#: could review.
_ATTRIBUTE_TYPES = (str, int, float, bool)


def _observable_counter(meter, name, callback):
    """Create the monotonic instrument a counter's readings are published through."""
    return meter.create_observable_counter(name, callbacks=[callback])


def _observable_gauge(meter, name, callback):
    """Create the instrument a gauge's readings are published through."""
    return meter.create_observable_gauge(name, callbacks=[callback])


#: Family -> the instrument its readings become. A table rather than a
#: branch, and each builder is HANDED the meter, so none of them names the
#: library. A histogram never reaches it: the readings are split into
#: the two counters above before an instrument is ever asked for.
_INSTRUMENTS = pin_members(
    "libs/opentelemetry.py's instrument builders",
    {"counter": _observable_counter, "gauge": _observable_gauge},
    vocab.METRIC_FAMILIES,
)


def _split_histogram(reading):
    """Return the two counters a histogram is carried as; its buckets are dropped."""
    return (
        Reading("counter", reading.name + COUNT_SUFFIX, reading.labels, reading.value["count"]),
        Reading("counter", reading.name + SUM_SUFFIX, reading.labels, reading.value["sum"]),
    )


class OtelSink(MetricSink):
    """The metric registry, exported over OTLP as observable instruments.

    Registered as ``opentelemetry`` in
    :data:`~dskit.production.metrics.METRIC_SINK_KINDS` (§4.3: import is
    registration), and declared in ``placement.metric_sinks`` — an
    exporter is placement, never policy, because a metric is never an
    input to a decision, a guard or a record (§5.11.1).

    Each :meth:`publish` replaces what the instruments' callbacks report;
    the reader exports on its own schedule, so a slow collector delays
    nothing on the tick path. A histogram is carried as its ``count`` and
    ``sum`` alone — see the module docstring for why its buckets are not
    translated.

    Parameters
    ----------
    params : dict
        ``endpoint_env`` (the NAME of the environment variable holding the
        OTLP endpoint, required — never the URL); ``protocol`` (an
        :data:`~dskit.production.vocab.OTEL_PROTOCOLS` member, required —
        the two wire formats are not interchangeable and guessing one
        exports into silence); ``interval_s`` (positive seconds, default
        :data:`DEFAULT_INTERVAL_S`); ``resource`` (a flat map of scalar,
        non-secret attributes attached to every point). ``notes`` is
        allowed, as everywhere.

    Attributes
    ----------
    KIND : str
        ``"opentelemetry"`` — the value ``metric_sink_failures_total{sink}``
        counts a swallowed publish under.

    Examples
    --------
    An exporter to the collector the environment names::

        sink = OtelSink({
            "endpoint_env": "OTLP_ENDPOINT",
            "protocol": "grpc",
            "resource": {"service.name": "serve"},
        })
        sink.settings["interval_s"]   # 60.0
        registry = Metrics()
        registry.subscribe(sink)
        registry.flush(1_767_225_600_000, "tick-1")
        sink.close()
    """

    KIND = "opentelemetry"
    _PARAMS = ("endpoint_env", "protocol", "interval_s", "resource")

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
            Accumulated problems, each naming the offending key.
        """
        problems = super().validate_params(params)
        endpoint = params.get("endpoint_env")
        _check_str(problems, "endpoint_env", endpoint)
        if isinstance(endpoint, str) and "://" in endpoint:
            problems.append(
                "endpoint_env is the NAME of an environment variable holding the OTLP "
                f"endpoint, not the endpoint itself, got {endpoint!r}"
            )
        if params.get("protocol") not in vocab.OTEL_PROTOCOLS:
            problems.append(
                f"protocol must be one of {list(vocab.OTEL_PROTOCOLS)}, "
                f"got {params.get('protocol')!r}"
            )
        cls._check_interval(problems, params)
        cls._check_resource(problems, params.get("resource", {}))
        return problems

    @staticmethod
    def _check_interval(problems, params):
        """Require a positive export interval; a zero one would export in a spin."""
        interval_s = params.get("interval_s", DEFAULT_INTERVAL_S)
        if isinstance(interval_s, bool) or not isinstance(interval_s, (int, float)):
            problems.append(f"interval_s must be a positive number, got {interval_s!r}")
        elif interval_s <= 0:
            problems.append(f"interval_s must be greater than zero, got {interval_s!r}")

    @staticmethod
    def _check_resource(problems, resource):
        """Require a flat map of scalar attributes, and refuse one that would be masked."""
        _check_dict(problems, "resource", resource)
        if not isinstance(resource, dict):
            return
        for key, value in sorted(resource.items()):
            if not isinstance(value, _ATTRIBUTE_TYPES):
                problems.append(
                    f"resource.{key} must be a string, a number or a boolean, got "
                    f"{value!r} — an attribute map is flat"
                )
            elif isinstance(value, str) and redact(value) != value:
                problems.append(
                    f"resource.{key} looks like a credential, and every exported point "
                    "carries the resource — name a non-secret fact instead"
                )

    def _configure(self, params):
        """Take the knobs; build no library object and read no environment."""
        self._endpoint_env = params["endpoint_env"]
        self._protocol = params["protocol"]
        self._interval_s = params.get("interval_s", DEFAULT_INTERVAL_S)
        self._resource = dict(params.get("resource", {}))
        self._carried = {}
        self._provider = None
        self._instruments = {}

    @property
    def settings(self):
        """Return the resolved knobs, defaults applied.

        Returns
        -------
        dict
            What an operator's readout would show; the endpoint appears as
            the variable NAME, never as a resolved URL.
        """
        return {
            "endpoint_env": self._endpoint_env,
            "protocol": self._protocol,
            "interval_s": self._interval_s,
            "resource": dict(self._resource),
        }

    def publish(self, snapshot, at_ms):
        """Replace what the instruments report with this flush's readings.

        Parameters
        ----------
        snapshot : dict
            ``{name: {series key: value}}`` — the registry's own copy.
        at_ms : int
            The instant the loop flushed at.

        Returns
        -------
        None
            The reader exports on its own schedule; a failure to build the
            exporter raises and ``Metrics.flush`` counts it.
        """
        self._deliver(self._points(readings(snapshot)), at_ms)

    @staticmethod
    def _points(published):
        """Return the readings OTLP can carry, histograms split into their two counters."""
        carried = []
        for reading in published:
            carried.extend(
                _split_histogram(reading)
                if reading.family not in _INSTRUMENTS
                else (reading,)
            )
        return tuple(carried)

    def _deliver(self, samples, at_ms):
        """Hold the readings and make sure each has an instrument reporting it."""
        held = {}
        for reading in samples:
            held.setdefault(reading.name, []).append(reading)
        self._carried = held
        meter = self._meter()
        for name, readings_for in held.items():
            if name not in self._instruments:
                self._instruments[name] = _INSTRUMENTS[readings_for[0].family](
                    meter, name, self._callback(name)
                )

    def _callback(self, name):
        """Return the callback the SDK polls for one metric's current readings."""

        def observe(options):
            """Report every series this metric holds at collection time."""
            from opentelemetry.metrics import Observation

            return [
                Observation(reading.value, attributes=dict(reading.labels))
                for reading in self._carried.get(name, ())
            ]

        return observe

    def _endpoint(self):
        """Return the OTLP endpoint the environment holds, or refuse naming the variable."""
        endpoint = os.environ.get(self._endpoint_env)
        if not endpoint:
            raise ProductionError(
                [
                    f"the environment holds no {self._endpoint_env}, so there is no "
                    "collector to export to"
                ]
            )
        return endpoint

    def _reader(self):
        """Build the periodic reader that exports over the declared protocol.

        The one method that names the exporters. Both live behind the same
        class name in two library modules, so the protocol chooses the
        MODULE and nothing else has to know the difference.

        Returns
        -------
        object
            The library's ``PeriodicExportingMetricReader``.

        Raises
        ------
        ProductionError
            If the environment does not hold the named endpoint variable.
        """
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter as GrpcExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HttpExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporters = pin_members(
            "libs/opentelemetry.py's exporters",
            {"grpc": GrpcExporter, "http_protobuf": HttpExporter},
            vocab.OTEL_PROTOCOLS,
            exact=True,
        )
        return PeriodicExportingMetricReader(
            exporters[self._protocol](endpoint=self._endpoint()),
            export_interval_millis=self._interval_s * 1_000,
        )

    def _meter(self):
        """Return this sink's meter, building the provider and its reader once.

        Returns
        -------
        object
            The library's ``Meter``, created under
            :data:`INSTRUMENTATION_SCOPE`.
        """
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource

        if self._provider is None:
            self._provider = MeterProvider(
                metric_readers=[self._reader()],
                resource=Resource.create(dict(self._resource)),
            )
        return self._provider.get_meter(INSTRUMENTATION_SCOPE)

    def close(self):
        """Shut the provider and its reader down.

        Returns
        -------
        None
            Idempotent, and swallowed by ``Metrics.close`` like every
            other exporter's; a sink that never published holds nothing.
        """
        if self._provider is not None:
            self._provider.shutdown()
            self._provider = None


METRIC_SINK_KINDS.register("opentelemetry", OtelSink)

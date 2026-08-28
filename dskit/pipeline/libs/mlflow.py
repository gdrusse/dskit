"""mlflow library pack — the tracking sink the Tracker seam was built for.

The engine has always had the whole seam and none of the destination:
:class:`~dskit.pipeline.protocols.Tracker` says what a sink must do,
``SINK_KINDS``/``register_sink_kind`` (``base.py``) says how one is
claimed, a document's ``tracking.sinks`` section says which ones a run
opens — and the only sink that ever registered was the in-memory one
``dskit.pipeline.testing`` puts up for tests. This pack is the real one,
tier 2: it names mlflow only inside methods, so a machine without mlflow
still plans every document (``tests/pipeline/test_purity.py`` enforces
that, class bodies included).

**Registration — the seam, verbatim from the memory sink.** The toolkit
registers no real sink at import; the application does it, exactly the
way ``testing.register_synthetic`` claims ``"memory"``::

    from dskit.pipeline.libs.mlflow import register

    register()   # idempotent; claims the "mlflow" sink kind

after which a document may say ``{"kind": "mlflow", "params": {...}}``.
A project that never wants the registration can spell the class instead
— ``{"kind": "dskit.pipeline.libs.mlflow:MlflowTracker"}`` — and the
class-ref path validates through the SAME
:meth:`MlflowTracker.validate_params`, because that method is what
``register()`` hands to ``register_sink_kind``. One rule, one name.

**Why this sink is so loud about its config.** ``driver._Trackers``
SWALLOWS every per-sink exception, deliberately: telemetry must never
kill a run the driver promised to record. The cost is that a
misconfigured sink logs nothing and says nothing — the run reports
success and the experiment is simply absent. So everything checkable is
checked where the swallow cannot reach:

* **At plan/validate time** — :meth:`MlflowTracker.validate_params` runs
  inside ``SinkConfig.__post_init__``, i.e. when the document is parsed.
  Params are default-deny, and the destination is PROVED reachable
  there: a local store's parent directory must exist and be writable, a
  server must accept a TCP connection within ``connect_timeout``. An
  unreachable URI fails the plan, not the run.
* **At construction** — the constructor re-runs the same validator and
  opens the mlflow run immediately, before any node executes
  (``_open_sinks`` runs ahead of the driver's node loop). A missing
  mlflow install, an unwritable store, a broken experiment: all raise
  here, where they are visible.

Only the three seam calls afterwards (``log_params``/``log_metrics``/
``close``) sit inside the swallow — by then the configuration has been
proven, which is the point.

**What lands.** ``log_params`` receives the driver's one-per-run payload:
the five identity fields plus every node's declared params flattened to
``"<node>.<param.path>"`` keys, so runs are filterable by hyperparameter.
Values are stringified and truncated to
:data:`MAX_PARAM_VALUE_CHARS` (the store's own limit would otherwise
raise INTO the swallow). ``log_metrics`` namespaces each stage's metrics
as ``"<stage>.<name>"``, so two nodes both reporting ``metrics.loss``
never overwrite each other.

**What this pack is not.** No node kind (``NODE_KINDS`` is empty): a
tracking destination is not a step of the pipeline, and keeping it out of
``pipeline`` keeps it out of everything an identity hash is computed
over. No server, no UI, no model registry — the default is a LOCAL store
so tests and laptops need nothing running.

**Which local store.** The default is ``sqlite:///mlruns.db``, not the
older ``./mlruns`` directory: mlflow put the plain-directory file store
into maintenance mode and now REFUSES it unless ``MLFLOW_ALLOW_FILE_STORE``
is set in the environment. Directory URIs (bare paths and ``file:``) stay
in the vocabulary — they are correct on mlflow 2.x and for anyone who has
opted out — but this pack sets no environment variable on your behalf, so
on a modern mlflow a directory URI raises at CONSTRUCTION, carrying
mlflow's own message. Loud and before the run, like every other refusal
here; just not a refusal the plan-time probe can make, because whether
the store is allowed is the installed mlflow's business, not the
document's.

Import cost: stdlib + ``dskit.pipeline`` only.
"""

from __future__ import annotations

import os
import socket
import time
import urllib.parse
import urllib.request

from dskit.pipeline.base import SINK_KINDS, ConfigError, register_sink_kind
from dskit.pipeline.node import reject_unknown_params

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_EXPERIMENT",
    "DEFAULT_TRACKING_URI",
    "MAX_PARAM_VALUE_CHARS",
    "METRIC_BATCH",
    "MlflowTracker",
    "NODE_KINDS",
    "PARAM_BATCH",
    "SINK_KIND",
    "TRACKING_URI_SCHEMES",
    "register",
]

#: The name this pack claims in ``SINK_KINDS``.
SINK_KIND = "mlflow"

#: A LOCAL sqlite store, relative to the working directory. The default
#: is deliberately serverless — a document that declares nothing but the
#: kind still tracks, on a laptop or in CI — and deliberately sqlite
#: rather than the older ``./mlruns`` directory, which current mlflow
#: refuses (see the module docstring).
DEFAULT_TRACKING_URI = "sqlite:///mlruns.db"

#: Where runs group when the document does not say. Created on first use.
DEFAULT_EXPERIMENT = "dskit-pipeline"

#: Seconds the reachability probe waits on a tracking SERVER before
#: calling the URI unreachable. Only http/https URIs are probed this way.
DEFAULT_CONNECT_TIMEOUT = 5.0

#: Longest param value the sink sends. mlflow's own limit has moved
#: between releases (500, then 6000); the smallest is the safe one, and a
#: value this long is an artifact, not a hyperparameter.
MAX_PARAM_VALUE_CHARS = 500

#: Batch sizes the tracking store accepts in one ``log_batch`` call.
PARAM_BATCH = 100
METRIC_BATCH = 1000

#: Deliberately EMPTY — a tracking destination is not a pipeline step.
#: The pack registers into ``SINK_KINDS`` (see :func:`register`), never
#: into the node registry, which is what keeps mlflow config out of the
#: hash-graded ``pipeline`` section.
NODE_KINDS = ()

#: Every knob that HAS a default. Resolved in ONE place (:func:`_settings`)
#: so ``validate_params`` and the run can never read different values —
#: the ``params.get(k, <literal>)`` in both halves defect, where validation
#: approves a value the run never uses.
_DEFAULTS = {
    "connect_timeout": DEFAULT_CONNECT_TIMEOUT,
    "experiment": DEFAULT_EXPERIMENT,
    "tracking_uri": DEFAULT_TRACKING_URI,
}


def _settings(params):
    """Lay declared params over the defaults — the one resolution."""
    return {**_DEFAULTS, **params}


def _local_path(uri):
    """Filesystem path a local (bare or ``file:``) tracking URI names."""
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme == "file":
        return os.path.abspath(urllib.request.url2pathname(parsed.path))
    return os.path.abspath(uri)


def _unreachable(uri, why):
    """Word an unreachable destination the one way every probe words it."""
    return (
        f"tracking_uri {uri!r} is unreachable: {why}. A sink that cannot be "
        "reached logs NOTHING and says nothing at run time (the driver "
        "swallows sink exceptions so telemetry can never kill a run), so it "
        "is refused here, at plan time."
    )


def _probe_local(uri, timeout):
    """Problems with a local file store: parent must exist and be writable."""
    del timeout  # a directory is reached without waiting
    path = _local_path(uri)
    if urllib.parse.urlparse(uri).netloc:
        return [_unreachable(uri, "a file: URI may not name a host")]
    if os.path.exists(path):
        if not os.path.isdir(path):
            return [_unreachable(uri, f"{path!r} exists and is not a directory")]
        if not os.access(path, os.W_OK):
            return [_unreachable(uri, f"{path!r} is not writable")]
        return []
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        return [_unreachable(uri, f"its parent directory {parent!r} does not exist")]
    if not os.access(parent, os.W_OK):
        return [_unreachable(uri, f"its parent directory {parent!r} is not writable")]
    return []


def _sqlite_path(uri):
    """Database file a ``sqlite:`` URI names (SQLAlchemy's slash rule)."""
    # sqlite:///rel.db -> "rel.db"; sqlite:////abs.db -> "/abs.db". urlparse
    # keeps one more leading slash than the path has, in both spellings.
    return os.path.abspath(urllib.parse.urlparse(uri).path[1:])


def _probe_sqlite(uri, timeout):
    """Problems with a local sqlite store: writable file or writable parent."""
    del timeout  # a local file is reached without waiting
    path = _sqlite_path(uri)
    if not path or path == os.path.abspath(""):
        return [_unreachable(uri, "it names no database file")]
    if os.path.exists(path):
        if not os.path.isfile(path):
            return [_unreachable(uri, f"{path!r} exists and is not a file")]
        if not os.access(path, os.W_OK):
            return [_unreachable(uri, f"{path!r} is not writable")]
        return []
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        return [_unreachable(uri, f"its parent directory {parent!r} does not exist")]
    if not os.access(parent, os.W_OK):
        return [_unreachable(uri, f"its parent directory {parent!r} is not writable")]
    return []


def _probe_host(uri, timeout):
    """Problems with a tracking server: it must accept a TCP connection."""
    parsed = urllib.parse.urlparse(uri)
    host = parsed.hostname
    if not host:
        return [_unreachable(uri, "it names no host")]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return []
    except OSError as exc:
        return [_unreachable(uri, f"{host}:{port} refused the connection ({exc})")]


#: URI scheme -> the probe that PROVES that family reachable. A closed
#: vocabulary on purpose: a scheme this pack cannot prove reachable is a
#: scheme whose misconfiguration would be silent, which is the one
#: failure mode the pack exists to remove. Other stores are reached by
#: subclassing :class:`MlflowTracker` and overriding ``probe_destination``.
_URI_PROBES = {
    "": _probe_local,
    "file": _probe_local,
    "http": _probe_host,
    "https": _probe_host,
    "sqlite": _probe_sqlite,
}

#: The scheme vocabulary, derived from the probe table so the two can
#: never disagree.
TRACKING_URI_SCHEMES = tuple(sorted(_URI_PROBES))


def _check_str(problems, name, value):
    """Append a problem unless ``value`` is a non-empty string."""
    if not isinstance(value, str) or not value:
        problems.append(f"{name} must be a non-empty string, got {value!r}")


def _check_timeout(problems, value):
    """Append a problem unless ``value`` is a positive, non-bool number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        problems.append(
            f"connect_timeout must be a number of seconds > 0, got {value!r}"
        )


def _check_tags(problems, value):
    """Append a problem unless ``value`` is a flat str -> str mapping."""
    if not isinstance(value, dict):
        problems.append(f"tags must be a dict of string -> string, got {value!r}")
        return
    for key, tag in value.items():
        if not isinstance(key, str) or not isinstance(tag, str):
            problems.append(
                f"tags entry {key!r}: both name and value must be strings, "
                f"got {tag!r}"
            )


def _param_value(value):
    """One param value as the store holds it: text, length-capped."""
    text = value if isinstance(value, str) else str(value)
    if len(text) <= MAX_PARAM_VALUE_CHARS:
        return text
    return text[: MAX_PARAM_VALUE_CHARS - 3] + "..."


def _chunks(items, size):
    """Yield ``items`` in lists of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


class MlflowTracker:
    """The mlflow tracking sink (kind ``"mlflow"``).

    Implements the :class:`~dskit.pipeline.protocols.Tracker` seam
    against an mlflow tracking store, and refuses a configuration it
    cannot honour BEFORE the run starts — see the module docstring for
    why that loudness is the whole point.

    Parameters
    ----------
    params : dict
        ``tracking_uri`` (str, default ``"./mlruns"`` — a local file
        store; schemes ``""``/``file``/``http``/``https``),
        ``experiment`` (str, default ``"dskit-pipeline"``, created on
        first use), ``run_name`` (str, optional — mlflow names the run
        itself when omitted), ``tags`` (dict of str -> str, optional),
        ``connect_timeout`` (number of seconds > 0, default 5.0 —
        the server reachability probe's budget), ``notes`` (str, the
        config standard's documentation field).

    Raises
    ------
    ConfigError
        When ``params`` is invalid, the destination is unreachable, or
        mlflow is not installed. Raised at construction, which the
        driver performs before any node runs.

    Examples
    --------
    Track into a local file store, no server anywhere::

        sink = MlflowTracker({"tracking_uri": "/tmp/mlruns"})
        sink.log_params({"name": "demo", "train.lr": 0.001})
        sink.log_metrics("validate", {"metrics.loss": 0.31})
        sink.close()
    """

    #: Default-deny: every knob this sink allows, and nothing else.
    _PARAMS = (
        "connect_timeout",
        "experiment",
        "notes",
        "run_name",
        "tags",
        "tracking_uri",
    )

    def __init__(self, params):
        problems = self.validate_params(params)
        if problems:
            raise ConfigError([f"tracking.{SINK_KIND}: {m}" for m in problems])
        self.params = dict(params)
        self._client = None
        self._run_id = ""
        self._open()

    # -- configuration ------------------------------------------------------

    @classmethod
    def validate_params(cls, params):
        """Problems with a declared mlflow sink config, as strings.

        Both entry points use this one method: ``register()`` hands it to
        ``register_sink_kind`` as the kind's validator, and the class-ref
        spelling reaches it through ``base._ref_param_errors``.

        Parameters
        ----------
        params : dict
            The sink's declared params, straight from the document.

        Returns
        -------
        list of str
            Empty when the config is usable. Type/name problems are
            reported alone — the destination probe runs only once the
            values are known to be well-formed, so a typo never triggers
            a network wait.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        settings = _settings(params)
        _check_str(problems, "tracking_uri", settings["tracking_uri"])
        _check_str(problems, "experiment", settings["experiment"])
        if "run_name" in params:
            _check_str(problems, "run_name", params["run_name"])
        if "notes" in params:
            _check_str(problems, "notes", params["notes"])
        if "tags" in params:
            _check_tags(problems, params["tags"])
        _check_timeout(problems, settings["connect_timeout"])
        if problems:
            return problems
        return cls.probe_destination(
            settings["tracking_uri"], settings["connect_timeout"]
        )

    @classmethod
    def probe_destination(cls, uri, timeout):
        """Problems proving ``uri`` reachable, without importing mlflow.

        The subclass hook for a store family this pack does not know:
        override, handle your scheme, and delegate the rest to
        ``super()``.

        Parameters
        ----------
        uri : str
            The declared tracking URI.
        timeout : int or float
            Seconds a server probe may wait.

        Returns
        -------
        list of str
            Empty when the destination is reachable.
        """
        scheme = urllib.parse.urlparse(uri).scheme
        probe = _URI_PROBES.get(scheme)
        if probe is None:
            return [
                f"tracking_uri {uri!r} uses scheme {scheme!r}, which this sink "
                f"cannot prove reachable — allowed: {list(TRACKING_URI_SCHEMES)} "
                "(subclass MlflowTracker and override probe_destination for "
                "another store)"
            ]
        return probe(uri, timeout)

    # -- the run ------------------------------------------------------------

    @property
    def run_id(self):
        """The mlflow run this sink writes to (str)."""
        return self._run_id

    def _open(self):
        """Create the local store if needed, then start the mlflow run."""
        try:
            from mlflow.tracking import MlflowClient
        except ImportError as exc:
            raise ConfigError(
                [
                    f"tracking sink {SINK_KIND!r} needs mlflow — install the "
                    "extra: pip install 'dskit[mlflow]'"
                ]
            ) from exc
        settings = _settings(self.params)
        uri = settings["tracking_uri"]
        if _URI_PROBES.get(urllib.parse.urlparse(uri).scheme) is _probe_local:
            os.makedirs(_local_path(uri), exist_ok=True)
        name = settings["experiment"]
        try:
            client = MlflowClient(tracking_uri=uri)
            experiment = client.get_experiment_by_name(name)
            experiment_id = (
                experiment.experiment_id
                if experiment is not None
                else client.create_experiment(name)
            )
            run = client.create_run(
                experiment_id,
                tags=dict(self.params.get("tags", {})),
                run_name=self.params.get("run_name"),
            )
        except Exception as exc:  # noqa: BLE001 — surfaced, never swallowed
            raise ConfigError(
                [
                    f"tracking sink {SINK_KIND!r} could not open experiment "
                    f"{name!r} at {uri!r}: {type(exc).__name__}: {exc}"
                ]
            ) from exc
        self._client = client
        self._run_id = run.info.run_id

    def log_params(self, mapping):
        """Record the run's identity and hyperparameters, once at run start.

        Parameters
        ----------
        mapping : dict
            ``name`` -> value. The driver sends the five identity fields
            plus every node's declared params under ``"<node>.<path>"``
            keys; values are stringified and length-capped.

        Returns
        -------
        None
            The payload is written to the tracking store.
        """
        from mlflow.entities import Param

        entries = [Param(str(k), _param_value(v)) for k, v in mapping.items()]
        for chunk in _chunks(entries, PARAM_BATCH):
            self._client.log_batch(self._run_id, params=chunk)

    def log_metrics(self, stage, mapping):
        """Record one stage's numeric metrics, namespaced by that stage.

        Parameters
        ----------
        stage : str
            The node key that produced the metrics.
        mapping : dict
            ``name`` -> number. Keys land as ``"<stage>.<name>"``, so two
            nodes reporting the same metric never overwrite each other.

        Returns
        -------
        None
            The metrics are written to the tracking store.
        """
        from mlflow.entities import Metric

        now = int(time.time() * 1000)
        entries = [
            Metric(f"{stage}.{k}", float(v), now, 0) for k, v in mapping.items()
        ]
        for chunk in _chunks(entries, METRIC_BATCH):
            self._client.log_batch(self._run_id, metrics=chunk)

    def close(self):
        """End the mlflow run. Idempotent — the driver always calls it.

        Returns
        -------
        None
            The run is marked ``FINISHED`` in the tracking store.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        client.set_terminated(self._run_id, "FINISHED")


def register() -> None:
    """Claim ``"mlflow"`` in the tracking-sink registry.

    Explicit and idempotent — the seam ``testing.register_synthetic``
    established for the ``"memory"`` sink, and the reason ``base.py``
    says real sinks register application-side. There is exactly one sink
    registry (``base.SINK_KINDS``), so this takes no registry argument.

    Returns
    -------
    None
        ``SINK_KINDS["mlflow"]`` binds this pack's validator and factory.
    """
    if SINK_KIND not in SINK_KINDS:
        register_sink_kind(SINK_KIND, MlflowTracker.validate_params, MlflowTracker)

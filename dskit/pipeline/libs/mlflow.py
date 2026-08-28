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
  then opens the mlflow CLIENT and EXPERIMENT immediately, before any
  node executes (``_open_sinks`` runs ahead of the driver's node loop).
  What only the installed mlflow can decide is decided here: a missing
  mlflow install, a store family it refuses, an experiment that exists
  but was DELETED (mlflow keeps returning it, and every write to it
  raises). Those raise, where they are visible.

Only the three seam calls afterwards (``log_params``/``log_metrics``/
``close``) sit inside the swallow — by then the configuration has been
proven, which is the point.

**The one thing loudness must not do is kill the run.**
``_open_sinks`` runs OUTSIDE ``run_document``'s ``try``, so a sink that
raises at construction aborts the run before a single node executes —
which is precisely what the swallow exists to prevent. So the two
failures are separated by whose FAULT they are, not by symptom:

* a **misconfiguration** raises ``ConfigError`` — mlflow not installed,
  a non-active experiment, a store family the installed mlflow refuses.
  Each names something a human can go and change;
* **the weather** does not. A degraded SERVER (an ``http``/``https``
  destination proved to accept a TCP connection at plan time, now
  5xx-ing or resetting) and a BUSY local store (another process holding
  the sqlite write lock — contention is the ordinary cost of a shared
  local store, and :func:`_is_transient` reads it off the DBAPI's own
  error classes rather than a message match) are both conditions of the
  destination, which no document can fix and no run should die of. The
  sink DISABLES itself, logs a warning naming the URI and the
  experiment, and every later seam call is a no-op — degraded, never
  fatal, never silent. The cost, recorded rather than fixed: a disabled
  sink is a line in the log, not a run-visible result.

For the same reason ``connect_timeout`` bounds more than the stdlib
probe: it is also pushed into mlflow's own HTTP knobs
(:data:`_HTTP_TIMEOUT_ENV`/:data:`_HTTP_RETRIES_ENV`, and only when the
operator has not set them) for the duration of this sink's calls.
Without that, construction against a reachable-but-degraded server runs
under mlflow's default retry/backoff policy and stalls ``run_document``
for minutes, before any node, with no output at all.

**Which stores, and how another one arrives.** The scheme vocabulary is
CLOSED — ``""``, ``file``, ``http``, ``https``, ``sqlite``. mlflow also
speaks postgres and mysql; this pack refuses what it cannot PROVE
reachable, because an unprovable destination is one whose
misconfiguration would be silent, the failure mode the pack exists to
remove. Another family arrives by subclassing: lay your scheme over
:data:`MlflowTracker._DESTINATIONS`, which carries BOTH facts a family
decides — the probe that proves it reachable
(:meth:`MlflowTracker.probe_destination`) and whether it is a SERVER
(:meth:`MlflowTracker.destination_is_remote`, which settles the
degrade-or-refuse split above and whether the HTTP budget applies).
They travel together on purpose: a seam that handed out only the probe
left a subclass running on failure semantics it never chose.

**Why the RUN is not opened there too.** The driver builds sinks before
resolve finishes and closes them on every pre-execution refusal (an
occupied run dir is the documented normal case: reruns need a new asof
or name). A run created in ``__init__`` therefore landed in the store
for refusals that never executed a node — empty and ``FINISHED``, which
browses exactly like a successful run and corrupts the cross-run
comparison this pack exists to provide. So the mlflow run is created
lazily, on the first ``log_params``/``log_metrics``; ``run_id`` is
``""`` until then. Construction stays loud — it just proves the store
instead of writing to it.

**What lands.** ``log_params`` receives the driver's one-per-run payload:
the five identity fields plus every node's declared params flattened to
``"<node>.<param.path>"`` keys, so runs are filterable by hyperparameter.
Values are stringified and truncated to :data:`MAX_PARAM_VALUE_CHARS`,
and NAMES — param and metric alike — to :data:`MAX_ENTITY_KEY_CHARS`,
because the store caps both and ``log_batch`` is all-or-nothing: one
over-long flattened path would otherwise cost the whole call, identity
fields included, and raise INTO the swallow. A capped name keeps its
head (the ``"<node>."`` prefix runs are filtered by) and carries the
full name's digest, so two deep paths sharing a prefix stay two params.
``log_metrics`` namespaces each stage's metrics as ``"<stage>.<name>"``,
so two nodes both reporting ``metrics.loss`` never overwrite each other,
and gives each key its own increasing STEP: mlflow breaks a latest-value
tie by max(step), then max(timestamp), then max(VALUE), so restatement
at a fixed step reports the larger value rather than the last one — and
restatement is a shipped path (the driver re-logs a re-executed node's
metrics so records and sinks reflect the final pass, spec §8), besides
being what a node logging a per-epoch series through
``ctx.tracker.log_metrics`` does.

**What this pack is not.** No node kind (``NODE_KINDS`` is empty): a
tracking destination is not a step of the pipeline. That alone would not
keep it out of the identity hash, though — the hash covers the WHOLE
document minus a named exclusion list, so a ``tracking`` section left
off that list is graded as surely as ``pipeline`` is. What keeps it out
is the name: ``tracking`` sits in
:data:`~dskit.pipeline.document.DOC_NON_IDENTITY_SECTIONS` beside
``env``/``outputs``/``schedule``, because WHERE a run's metrics are
logged is placement and the identity hash grades what the run COMPUTES.
So a document repointed at another store keeps its identity, its run
directory and its ``$prev`` series. No server, no UI, no model registry
— the default is a LOCAL store so tests and laptops need nothing
running.

**Which local store.** The default is ``sqlite:///mlruns.db``, not the
older ``./mlruns`` directory: mlflow put the plain-directory file store
into maintenance mode and now REFUSES it unless ``MLFLOW_ALLOW_FILE_STORE``
is set in the environment. Directory URIs (bare paths and ``file:``) stay
in the vocabulary — they are correct on mlflow 2.x and for anyone who has
opted out — but this pack never opts you in, so on a modern mlflow a
directory URI raises at CONSTRUCTION, carrying mlflow's own message and
leaving nothing behind on disk. Loud and before the run, like every
other refusal here; just not a refusal the plan-time probe can make,
because whether the store is allowed is the installed mlflow's business,
not the document's. (The only environment this pack ever writes is the
HTTP budget above — scoped to its own calls, and never over a value you
set yourself.)

Import cost: stdlib + ``dskit.pipeline`` only.
"""

from __future__ import annotations

import collections
import contextlib
import errno
import hashlib
import logging
import os
import socket
import sqlite3
import time
import urllib.parse
import urllib.request

from dskit.pipeline.base import SINK_KINDS, ConfigError, register_sink_kind
from dskit.pipeline.node import reject_unknown_params

_log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_EXPERIMENT",
    "DEFAULT_TRACKING_URI",
    "MAX_ENTITY_KEY_CHARS",
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

#: Longest param or metric KEY the sink sends. The store enforces the
#: same limit on names as on values, and ``log_batch`` is all-or-nothing:
#: one over-long key would cost the whole call, inside the swallow.
MAX_ENTITY_KEY_CHARS = 250

#: Hex digits of the full key's digest that a capped key carries, so two
#: long keys sharing a prefix stay two keys.
_KEY_DIGEST_CHARS = 12

#: mlflow's own HTTP budget knobs, read from the environment per request.
#: ``connect_timeout`` bounds the stdlib TCP probe directly; these are the
#: only way to bound the calls mlflow itself makes, which otherwise run
#: under its default retry/backoff policy (minutes against a degraded
#: server, ahead of every node). The pack sets them only when the operator
#: has not, and only for the duration of its own calls.
_HTTP_TIMEOUT_ENV = "MLFLOW_HTTP_REQUEST_TIMEOUT"
_HTTP_RETRIES_ENV = "MLFLOW_HTTP_REQUEST_MAX_RETRIES"

#: Batch sizes the tracking store accepts in one ``log_batch`` call.
PARAM_BATCH = 100
METRIC_BATCH = 1000

#: Deliberately EMPTY — a tracking destination is not a pipeline step.
#: The pack registers into ``SINK_KINDS`` (see :func:`register`), never
#: into the node registry, so a store URI is never spellable as a node
#: param. What keeps the ``tracking`` SECTION out of identity is the
#: exclusion list, not this table (see the module docstring).
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


def _parent_problems(uri, path):
    """Problems creating ``path``: its parent must exist and be writable."""
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        return [_unreachable(uri, f"its parent directory {parent!r} does not exist")]
    if not os.access(parent, os.W_OK):
        return [_unreachable(uri, f"its parent directory {parent!r} is not writable")]
    return []


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
    return _parent_problems(uri, path)


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
    return _parent_problems(uri, path)


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


#: One destination FAMILY: the probe that proves that scheme reachable,
#: and whether the scheme names a SERVER. Both facts belong to the
#: family, so both travel together — a subclass that adds a store cannot
#: declare one and silently inherit the other.
_Family = collections.namedtuple("_Family", "probe remote")


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


def _entity_key(name):
    """One param/metric name as the store holds it: capped, still unique."""
    if len(name) <= MAX_ENTITY_KEY_CHARS:
        return name
    # Keep the HEAD — the '<node>.' prefix runs are filtered by — and
    # carry the whole name's digest so two paths sharing a long prefix
    # cannot collapse into one param, which the store would refuse.
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:_KEY_DIGEST_CHARS]
    kept = MAX_ENTITY_KEY_CHARS - _KEY_DIGEST_CHARS - 1
    return f"{name[:kept]}.{digest}"


def _chunks(items, size):
    """Yield ``items`` in lists of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


@contextlib.contextmanager
def _request_budget(remote, timeout):
    """Bound mlflow's own HTTP calls to ``timeout``, for a server only."""
    if not remote:
        yield
        return
    ours = {_HTTP_TIMEOUT_ENV: str(max(1, int(timeout))), _HTTP_RETRIES_ENV: "1"}
    # Never override a budget the operator declared — only supply one
    # where mlflow would otherwise use its own generous default.
    set_here = [name for name in ours if name not in os.environ]
    for name in set_here:
        os.environ[name] = ours[name]
    try:
        yield
    finally:
        for name in set_here:
            os.environ.pop(name, None)


#: ``OSError`` numbers that mean BUSY or momentarily out of resources —
#: the store is fine, this instant is not. Anything else (a permission,
#: a missing path) is a config problem the plan-time probe already had
#: its chance to catch, so it stays fatal.
_TRANSIENT_ERRNOS = frozenset(
    {
        errno.EAGAIN,
        errno.EBUSY,
        errno.EINTR,
        errno.EMFILE,
        errno.ENFILE,
        errno.ENOLCK,
        errno.ETIMEDOUT,
    }
)


def _is_transient(exc):
    """Say whether a failed open is the WEATHER rather than the config."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        # sqlite reports lock contention and momentary I/O trouble as
        # OperationalError ("database is locked"), and a store that is
        # genuinely wrong as some other DatabaseError ("file is not a
        # database") — so the split is the DBAPI's own, not a message
        # match. The driver wraps it, so walk the whole chain.
        if isinstance(exc, sqlite3.OperationalError):
            return True
        if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNOS:
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class _StoreRefused(Exception):
    """The store answered and said no — a config problem, not a blip."""


def _usable_experiment(client, name):
    """Id of an EXISTING, writable experiment; None when there is none."""
    experiment = client.get_experiment_by_name(name)
    if experiment is None:
        return None
    stage = getattr(experiment, "lifecycle_stage", "active")
    if stage != "active":
        # A non-None answer proves the NAME is taken, not that it is
        # usable: mlflow keeps returning a DELETED experiment, and every
        # write to it raises — inside the swallow, where the run would
        # report success having tracked nothing. Deleting an experiment
        # in the UI is a normal operation, so say so out here instead.
        raise _StoreRefused(
            f"experiment {name!r} exists but its lifecycle_stage is {stage!r}, "
            "not 'active' — restore it in mlflow or declare another experiment"
        )
    return experiment.experiment_id


def _open_experiment(client, name):
    """Experiment id for ``name``, creating it once — race-tolerantly."""
    existing = _usable_experiment(client, name)
    if existing is not None:
        return existing
    try:
        return client.create_experiment(name)
    except Exception:  # noqa: BLE001 — re-raised below unless it was the race
        # Look-then-create is not atomic: a concurrent run_document
        # against the same store can create the experiment between the
        # two calls, and the loser's create fails on a UNIQUE
        # constraint. Losing that race is not a misconfiguration, and
        # this runs inside __init__ — raising would let TRACKING kill a
        # correctly configured run, the one thing this seam forbids.
        existing = _usable_experiment(client, name)
        if existing is None:
            raise
        return existing


class MlflowTracker:
    """The mlflow tracking sink (kind ``"mlflow"``).

    Implements the :class:`~dskit.pipeline.protocols.Tracker` seam
    against an mlflow tracking store, and refuses a configuration it
    cannot honour BEFORE the run starts — see the module docstring for
    why that loudness is the whole point.

    Parameters
    ----------
    params : dict
        ``tracking_uri`` (str, default ``"sqlite:///mlruns.db"`` — a
        LOCAL, serverless sqlite store; schemes ``""``, ``file``,
        ``http``, ``https``, ``sqlite``, and note that the two directory
        spellings — a bare path and ``file:`` — reach a store current
        mlflow refuses, see the module docstring), ``experiment`` (str,
        default ``"dskit-pipeline"``, created on first use),
        ``run_name`` (str, optional — mlflow names the run itself when
        omitted), ``tags`` (dict of str -> str, optional),
        ``connect_timeout`` (number of seconds > 0, default 5.0 — the
        budget for BOTH the plan-time server probe and the sink's own
        mlflow HTTP calls). Documentation goes in the sink's OWN
        ``notes`` field beside ``params``, never inside them. The
        defaults live in
        :data:`DEFAULT_TRACKING_URI`/:data:`DEFAULT_EXPERIMENT`/
        :data:`DEFAULT_CONNECT_TIMEOUT` and the scheme list in
        :data:`TRACKING_URI_SCHEMES`; the copies above are pinned to them
        by ``test_the_class_docstring_states_the_default_and_the_vocabulary``.

    Raises
    ------
    ConfigError
        When ``params`` is invalid, the destination is unreachable,
        mlflow is not installed, or the declared experiment exists but
        is not active. Raised at construction, which the driver performs
        before any node runs. A destination merely having a BAD DAY is
        not a misconfiguration and does NOT raise — a reachable server
        that then fails, or a local store another process holds the
        write lock on: the sink disables itself and warns, because
        construction runs outside the driver's try and raising there
        would abort a correctly configured run.

    Examples
    --------
    Track into the default local sqlite store, no server anywhere::

        sink = MlflowTracker({"tracking_uri": "sqlite:///mlruns.db"})
        sink.log_params({"name": "demo", "train.lr": 0.001})
        sink.log_metrics("validate", {"metrics.loss": 0.31})
        sink.close()
    """

    #: URI scheme -> :data:`_Family`. THE extension point for a store
    #: family this pack does not ship: a subclass lays its scheme over
    #: this table and gets the probe AND the failure semantics in one
    #: declaration. A closed vocabulary on purpose — a scheme the sink
    #: cannot prove reachable is a scheme whose misconfiguration would be
    #: silent, the one failure mode this pack exists to remove. Read by
    #: unpacking, so a subclass may spell an entry as a plain
    #: ``(probe, remote)`` pair.
    _DESTINATIONS = {
        "": _Family(_probe_local, False),
        "file": _Family(_probe_local, False),
        "http": _Family(_probe_host, True),
        "https": _Family(_probe_host, True),
        "sqlite": _Family(_probe_sqlite, False),
    }

    #: Default-deny: every knob this sink allows, and nothing else.
    #: (No ``notes``: ``SinkConfig`` carries the config standard's
    #: documentation field itself, and a tier-2 pack never restates
    #: tier-1 truth — two ``notes`` on one object would be two bounds.)
    _PARAMS = (
        "connect_timeout",
        "experiment",
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
        self._experiment = ""
        self._run_id = ""
        self._steps = {}
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

        Half of the store-family hook: add your scheme to
        :data:`_DESTINATIONS` (which carries the other half,
        :meth:`destination_is_remote`), or override this and delegate the
        rest to ``super()``.

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
        family = cls._DESTINATIONS.get(scheme)
        if family is None:
            return [
                f"tracking_uri {uri!r} uses scheme {scheme!r}, which this sink "
                f"cannot prove reachable — allowed: {sorted(cls._DESTINATIONS)} "
                "(subclass MlflowTracker and declare another store family in "
                "_DESTINATIONS)"
            ]
        probe, _remote = family
        return probe(uri, timeout)

    @classmethod
    def destination_is_remote(cls, uri):
        """Whether ``uri`` names a tracking SERVER rather than a local store.

        The other half of the store-family hook, and the reason the two
        travel together: this one answer decides whether a failure at
        construction DEGRADES or refuses the run, and whether mlflow's
        own HTTP calls are bounded. A subclass that adds a family to
        :data:`_DESTINATIONS` declares both at once; one that only
        widens :meth:`probe_destination` gets the safe reading, because
        an undeclared scheme is one this class proved nothing LOCAL
        about — so a failure there is never the document's fault.

        Parameters
        ----------
        uri : str
            The declared tracking URI.

        Returns
        -------
        bool
            True for a server family, and for any scheme this class does
            not declare.
        """
        family = cls._DESTINATIONS.get(urllib.parse.urlparse(uri).scheme)
        if family is None:
            return True
        _probe, remote = family
        return remote

    # -- the run ------------------------------------------------------------

    @property
    def run_id(self):
        """The mlflow run this sink writes to (str, ``""`` until first log)."""
        return self._run_id

    def _budget(self):
        """Bound this sink's own mlflow calls to the declared timeout."""
        settings = _settings(self.params)
        return _request_budget(
            self.destination_is_remote(settings["tracking_uri"]),
            settings["connect_timeout"],
        )

    def _open(self):
        """Prove the store usable now — open its client and experiment, write nothing."""
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
        uri, name = settings["tracking_uri"], settings["experiment"]
        try:
            with self._budget():
                client = MlflowClient(tracking_uri=uri)
                experiment = _open_experiment(client, name)
        except _StoreRefused as exc:
            raise ConfigError([self._open_failed(name, uri, exc)]) from exc
        except Exception as exc:  # noqa: BLE001 — routed, never swallowed
            if self.destination_is_remote(uri) or _is_transient(exc):
                # Neither failure is the DOCUMENT's. A SERVER was proved
                # to accept a TCP connection at plan time and is now
                # having a bad day; a local store was proved writable and
                # is BUSY this instant (another run_document holds the
                # sqlite write lock — contention is the normal cost of a
                # shared local store, not a misconfiguration). Either
                # way `_open_sinks` runs OUTSIDE `run_document`'s try, so
                # raising here would let a telemetry destination abort a
                # correctly configured run before a single node ran — the
                # one thing this seam forbids. Degrade, loudly.
                _log.warning(
                    "%s — tracking disabled for this run",
                    self._open_failed(name, uri, exc),
                )
                return
            # Everything left is a reason a human can act on: mlflow
            # missing, a store family this mlflow refuses, a permission.
            # Those stay fatal, which is the whole point of the pack.
            raise ConfigError([self._open_failed(name, uri, exc)]) from exc
        self._client = client
        self._experiment = experiment

    @staticmethod
    def _open_failed(name, uri, exc):
        """Word a store that would not open, the one way every path words it."""
        return (
            f"tracking sink {SINK_KIND!r} could not open experiment {name!r} "
            f"at {uri!r}: {type(exc).__name__}: {exc}"
        )

    def _ensure_run(self):
        """Return the run id, creating it on FIRST log, never at construction."""
        if not self._run_id:
            run = self._client.create_run(
                self._experiment,
                tags=dict(self.params.get("tags", {})),
                run_name=self.params.get("run_name"),
            )
            self._run_id = run.info.run_id
        return self._run_id

    def _next_step(self, key):
        """Count this key's own writes, so the LAST one wins in the store."""
        step = self._steps.get(key, -1) + 1
        self._steps[key] = step
        return step

    def log_params(self, mapping):
        """Record the run's identity and hyperparameters, once at run start.

        Parameters
        ----------
        mapping : dict
            ``name`` -> value. The driver sends the five identity fields
            plus every node's declared params under ``"<node>.<path>"``
            keys; names and values are both length-capped, values
            stringified.

        Returns
        -------
        None
            The payload is written to the tracking store, or dropped
            when the sink disabled itself at construction.
        """
        if self._client is None:
            return
        from mlflow.entities import Param

        with self._budget():
            run_id = self._ensure_run()
            entries = [
                Param(_entity_key(str(k)), _param_value(v))
                for k, v in mapping.items()
            ]
            for chunk in _chunks(entries, PARAM_BATCH):
                self._client.log_batch(run_id, params=chunk)

    def log_metrics(self, stage, mapping):
        """Record one stage's numeric metrics, namespaced by that stage.

        Parameters
        ----------
        stage : str
            The node key that produced the metrics.
        mapping : dict
            ``name`` -> number. Keys land as ``"<stage>.<name>"``, so two
            nodes reporting the same metric never overwrite each other,
            each at its own next STEP so a restated or per-epoch value
            is ordered by write rather than by size.

        Returns
        -------
        None
            The metrics are written to the tracking store, or dropped
            when the sink disabled itself at construction.
        """
        if self._client is None:
            return
        from mlflow.entities import Metric

        with self._budget():
            run_id = self._ensure_run()
            now = int(time.time() * 1000)
            entries = []
            for name, value in mapping.items():
                key = _entity_key(f"{stage}.{name}")
                entries.append(
                    Metric(key, float(value), now, self._next_step(key))
                )
            for chunk in _chunks(entries, METRIC_BATCH):
                self._client.log_batch(run_id, metrics=chunk)

    def close(self):
        """End the mlflow run, if one was ever started. Idempotent.

        A sink that logged NOTHING never created a run, so nothing is
        terminated — see :meth:`_ensure_run`. A run that did start is
        marked ``FINISHED`` whatever happened, because the ``Tracker``
        seam carries no status and the driver's ``close()`` is the same
        call on the success and the crash path; distinguishing them
        needs a status on the protocol, which is core's to add.

        Returns
        -------
        None
            Any started run is marked ``FINISHED`` in the tracking store.
        """
        if self._client is None:
            return
        client, self._client = self._client, None
        if self._run_id:
            with self._budget():
                client.set_terminated(self._run_id, "FINISHED")


#: The scheme vocabulary this pack ships, derived from the family table
#: so the two can never disagree. A subclass widens the table, not this.
TRACKING_URI_SCHEMES = tuple(sorted(MlflowTracker._DESTINATIONS))


def register():
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

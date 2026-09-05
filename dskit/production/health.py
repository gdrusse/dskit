"""Health as a state machine, the heartbeat, the instance lock and the signals (§5.11, D18).

The dead-man's switch is external: this process cannot page anyone about
its own death, so everything here makes its death VISIBLE and its
behaviour while dying SAFE.

* **Scope decides the answer, not the probe.** Every :class:`HealthProbe`
  declares a scope. A failing ``local`` probe (the ledger is unwritable)
  means this process is broken: ``unhealthy``, which stops acting AND
  stops heartbeating so the external supervisor pages. A failing
  ``dependency`` probe (the venue, the feed) means the world is broken:
  ``degraded``, which keeps observing and refuses acts. Probes run on
  one :class:`~dskit.production.alerts.SupervisedWorker` each, bounded by
  their ``timeout_s``; a raise or a missed deadline is a result with
  ``ok=False`` and a masked reason, and a stuck worker is answered
  ``ok=False`` — never replaced — until its call returns.
* **Hysteresis, then transitions.** A probe counts as failing after
  ``failure_threshold`` consecutive failures and as recovered after
  ``success_threshold`` consecutive successes. The state is
  ``starting → {ready | degraded | unhealthy} → stopping``; ``ready`` and
  ``degraded`` move freely between each other, ``unhealthy`` and
  ``stopping`` latch (only a restart clears them). Every transition —
  never a level — appends one §6 ``health`` record and raises one alert
  whose fingerprint is derived from the transition, so two processes
  hitting the same one dedup at the router.
* **The heartbeat is not the tick.** :class:`Heartbeat` emits
  ``{process_id, sequence, at_ms, status}`` at its own ``every_s``,
  through ``file`` (an atomic rewrite of ``heartbeat.json``) and
  deadline-bound ``url`` emitters. It watches the monotonic stamp of the
  last completed tick; once ``dead_after_ms`` has elapsed it makes health
  ``unhealthy`` and stops — deliberately withdrawing the signal the
  supervisor watches for.
* **No auxiliary thread appends.** ``mark_unhealthy`` is the one verb the
  heartbeat's own worker may call: it latches the state and raises the
  alert at once, and leaves its record for the next loop-thread verb
  (``evaluate``, ``probe_once``, ``stop``) to append (D23, ruling R19).
* **One process per series.** :class:`InstanceLock` is
  ``flock(LOCK_EX | LOCK_NB)`` on ``serve.lock``; the loop takes it before
  the ledger opens and hands it to the ledger (ruling R18: one lock).
* **Signals set a flag.** :class:`SignalHandler` sets a
  ``threading.Event`` and returns; the loop notices within one sleep
  slice.
"""

import fcntl
import json
import os
import signal
import tempfile
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from dskit.onboarding.base import durable_write_json
from dskit.pipeline.node import check_int_param
from dskit.production import vocab
from dskit.production.alerts import (
    SupervisedWorker,
    check_timeout_s,
    deadline,
    redact_strings,
)
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_str,
    reject_unknown_params,
)
from dskit.production.document import MIN_HEARTBEAT_EVERY_S
from dskit.production.records import Alert
from dskit.production.redact import get_logger, redact

__all__ = [
    "DEAD_CAUSE",
    "DEFAULT_EMIT_TIMEOUT_S",
    "DEFAULT_IN_DEGRADED",
    "DEFAULT_PROBE_TIMEOUT_S",
    "ExecutorCheckProbe",
    "FeedAgeProbe",
    "FileEmitter",
    "HEARTBEAT_KINDS",
    "Health",
    "HealthProbe",
    "Heartbeat",
    "HeartbeatEmitter",
    "HeartbeatPayload",
    "InstanceLock",
    "LedgerWritableProbe",
    "PROBE_KINDS",
    "ProbeResult",
    "SignalHandler",
    "UrlEmitter",
]

#: ``heartbeat.in_degraded`` when the document is silent: a degraded
#: process stays quiet, keeping the heartbeat honest rather than flowing.
DEFAULT_IN_DEGRADED = False
#: A probe's ``timeout_s`` when neither the probe site nor the caller names one.
DEFAULT_PROBE_TIMEOUT_S = 1.0
#: A ``url`` emitter's ``timeout_s`` when its params name none.
DEFAULT_EMIT_TIMEOUT_S = 5.0
#: The cause the heartbeat's dead-man check latches health with.
DEAD_CAUSE = "tick_dead"

_NOTES = ("notes",)
_MS_PER_S = 1000
_JSON_HEADERS = {"Content-Type": "application/json"}
_STARTING, _READY, _DEGRADED, _UNHEALTHY, _STOPPING = vocab.HEALTH_STATES
#: The states nothing but a restart leaves.
_LATCHED = (_UNHEALTHY, _STOPPING)
#: What a failing probe of each scope makes the process (D18).
_STATE_BY_SCOPE = {"local": _UNHEALTHY, "dependency": _DEGRADED}
#: The alert severity of entering each state; ``unhealthy`` is the loudest.
_SEVERITY_BY_STATE = {
    _STARTING: "info",
    _READY: "info",
    _DEGRADED: "warning",
    _UNHEALTHY: "critical",
    _STOPPING: "info",
}
_SIGNALS = (signal.SIGTERM, signal.SIGINT)

_log = get_logger("health")


class _Configured(ABC):
    """``cls(params)`` construction: default-deny over ``_PARAMS`` plus ``notes``."""

    _PARAMS = ()

    def __init__(self, params):
        params = {} if params is None else params
        problems = self.validate_params(params)
        if problems:
            raise ProductionError(problems)
        self._params = dict(params)

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Default-deny over ``_PARAMS`` plus ``notes``, then the
            subclass ``_check`` hook.
        """
        if not isinstance(params, dict):
            return [f"params must be an object (dict), got {params!r}"]
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        cls._check(problems, params)
        return problems

    @classmethod
    def _check(cls, problems, params):
        """Append problems with the declared knobs; the base has none."""


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """What one probe check came to.

    Parameters
    ----------
    ok : bool
        Whether the check passed.
    at_ms : int or None
        The evaluation instant, stamped by :class:`Health` on every result
        it publishes; a probe's own ``check`` leaves it ``None``.
    detail : str
        A masked reason when ``ok`` is false; empty otherwise.

    Examples
    --------
    ::

        result = ProbeResult(ok=False, at_ms=1_767_225_600_000, detail="disk full")
        result.to_obj()   # {'ok': False, 'at_ms': 1767225600000, 'detail': 'disk full'}
    """

    ok: bool
    at_ms: int
    detail: str

    def to_obj(self):
        """Return the result as a JSON-ready dict, fields in declared order.

        Returns
        -------
        dict
            ``{"ok", "at_ms", "detail"}``.
        """
        return {"ok": bool(self.ok), "at_ms": self.at_ms, "detail": self.detail}


class HealthProbe(_Configured):
    """The probe seam: ``check() -> ProbeResult`` under a name, a scope and a deadline.

    Parameters
    ----------
    params : dict or None
        The ``health.probes.<name>.params`` block, default-deny over the
        subclass's ``_PARAMS`` plus ``notes``.
    name : str, optional
        The document's name for the probe; ``None`` takes the key it is
        registered under.
    scope : str, optional
        A ``vocab.PROBE_SCOPES`` member; ``None`` takes the class ``SCOPE``.
    timeout_s : int or float, optional
        The check's deadline; ``None`` takes ``DEFAULT_PROBE_TIMEOUT_S``
        (``compose.py`` passes ``health.timeout_s`` here).

    Attributes
    ----------
    SCOPE : str
        The scope a subclass declares (``"local"`` on the ABC: an
        undeclared probe that fails stops the process, the safe reading).

    Examples
    --------
    A child probe that asks its own service::

        class QueueDepthProbe(HealthProbe):
            SCOPE = "dependency"

            def check(self):
                return ProbeResult(ok=True, at_ms=None, detail="")

        probe = QueueDepthProbe(None, name="queue", timeout_s=0.5)
        (probe.scope, probe.timeout_s)   # ('dependency', 0.5)
    """

    SCOPE = "local"

    def __init__(self, params, *, name=None, scope=None, timeout_s=None):
        super().__init__(params)
        problems = []
        scope = self.SCOPE if scope is None else scope
        if scope not in vocab.PROBE_SCOPES:
            problems.append(f"scope {scope!r} is not one of {list(vocab.PROBE_SCOPES)}")
        if name is not None:
            _check_str(problems, "name", name)
        timeout_s = DEFAULT_PROBE_TIMEOUT_S if timeout_s is None else timeout_s
        check_timeout_s(problems, "timeout_s", timeout_s)
        if problems:
            raise ProductionError(problems)
        self._name = name
        self._scope = scope
        self._timeout_s = timeout_s

    @property
    def name(self):
        """Return the probe's name, or ``None`` when the registry key names it."""
        return self._name

    @property
    def scope(self):
        """Return the probe's scope."""
        return self._scope

    @property
    def timeout_s(self):
        """Return the check's deadline in seconds."""
        return self._timeout_s

    @abstractmethod
    def check(self):
        """Run one check and report it.

        Returns
        -------
        ProbeResult
            ``at_ms`` may be ``None``; :class:`Health` stamps it. A raise
            is recorded by the supervisor as ``ok=False``.
        """


class LedgerWritableProbe(HealthProbe):
    """``ledger-writable`` (local): the series root accepts a durable file.

    Creates, writes, fsyncs and unlinks one temporary file in the series
    directory — the same operations an append needs — leaving nothing
    behind.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.
    serve_root : ledger.ServeRoot
        Required; ``series_path`` is where the probe writes.

    Examples
    --------
    ::

        probe = LedgerWritableProbe(None, serve_root=ServeRoot("./serve", series_id))
        probe.check().ok   # True while the series root is writable
    """

    SCOPE = "local"

    def __init__(self, params, *, serve_root=None, **kw):
        super().__init__(params, **kw)
        if not isinstance(getattr(serve_root, "series_path", None), str):
            raise ProductionError(["ledger-writable requires the serve_root collaborator"])
        self._series_path = serve_root.series_path

    def check(self):
        """Write and remove one temporary file; see :meth:`HealthProbe.check`."""
        try:
            fd, path = tempfile.mkstemp(dir=self._series_path, prefix=".probe-")
            try:
                os.write(fd, b"ok\n")
                os.fsync(fd)
            finally:
                os.close(fd)
                os.unlink(path)
        except OSError as exc:
            return ProbeResult(ok=False, at_ms=None, detail=redact(str(exc)))
        return ProbeResult(ok=True, at_ms=None, detail="")


class ExecutorCheckProbe(HealthProbe):
    """``executor-check`` (dependency): the venue still accepts our configuration.

    Asks ``executor.check(config)``; a non-empty answer (the problems it
    found) or a raise is a failure carrying the masked reason.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.
    executor : executor.Executor
        Required; anything with ``check(config)``.
    config : object, optional
        What ``check`` is handed — the document's ``execution`` view;
        ``None`` when the executor needs nothing.

    Examples
    --------
    ::

        probe = ExecutorCheckProbe(None, executor=executor, config=document.execution)
        probe.check().ok   # True when the executor reports no problem
    """

    SCOPE = "dependency"

    def __init__(self, params, *, executor=None, config=None, **kw):
        super().__init__(params, **kw)
        if not callable(getattr(executor, "check", None)):
            raise ProductionError(["executor-check requires the executor collaborator (check)"])
        self._executor = executor
        self._config = config

    def check(self):
        """Ask the executor; see :meth:`HealthProbe.check`."""
        try:
            problems = self._executor.check(self._config)
        except Exception as exc:  # the venue's fault is a result, never a raise
            return ProbeResult(ok=False, at_ms=None, detail=redact(f"{type(exc).__name__}: {exc}"))
        problems = [str(problem) for problem in (problems or ())]
        return ProbeResult(ok=not problems, at_ms=None, detail=redact("; ".join(problems)))


class FeedAgeProbe(HealthProbe):
    """``feed-age`` (dependency): every required key's data is inside the age bound.

    Reads an atomic snapshot — ``feed_ages()`` answers the last tick's
    ``tuple[records.FeedAge]``, never the feed itself (D23) — and fails on
    any key older than ``max_age_ms`` (inclusive bound). No ages yet is
    not a failure.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.
    feed_ages : callable
        Required; zero-argument, returning an iterable of ``FeedAge``.
    max_age_ms : int
        Required; the one owner is ``document.schedule.max_staleness_ms``,
        injected so the probe cannot drift from the tick's gate.

    Examples
    --------
    ::

        probe = FeedAgeProbe(None, feed_ages=lambda: tick.feed_ages, max_age_ms=120_000)
        probe.check().ok   # True while every key is fresh enough
    """

    SCOPE = "dependency"

    def __init__(self, params, *, feed_ages, max_age_ms, **kw):
        super().__init__(params, **kw)
        problems = []
        if not callable(feed_ages):
            problems.append(f"feed-age requires a callable feed_ages, got {feed_ages!r}")
        check_int_param(problems, "max_age_ms", max_age_ms, ge=0)
        if problems:
            raise ProductionError(problems)
        self._feed_ages = feed_ages
        self._max_age_ms = int(max_age_ms)

    def check(self):
        """Compare every key's age with the bound; see :meth:`HealthProbe.check`."""
        stale = sorted(
            (age for age in self._feed_ages() if age.age_ms > self._max_age_ms),
            key=lambda age: -age.age_ms,
        )
        detail = "; ".join(
            f"{age.key} is {age.age_ms} ms old (max {self._max_age_ms})" for age in stale
        )
        return ProbeResult(ok=not stale, at_ms=None, detail=redact(detail))


# ---------------------------------------------------------------------------
# The state machine
# ---------------------------------------------------------------------------


class _Tally:
    """One probe's hysteresis: consecutive counts and whether it counts as failing."""

    __slots__ = ("failures", "successes", "failing")

    def __init__(self):
        self.failures = 0
        self.successes = 0
        self.failing = False

    def observe(self, ok, failure_threshold, success_threshold):
        """Fold one result; flip ``failing`` only at a threshold."""
        if ok:
            self.successes += 1
            self.failures = 0
            if self.failing and self.successes >= success_threshold:
                self.failing = False
        else:
            self.failures += 1
            self.successes = 0
            if not self.failing and self.failures >= failure_threshold:
                self.failing = True


class Health:
    """``starting → {ready | degraded | unhealthy} → stopping``, driven by supervised probes.

    Parameters
    ----------
    document_health : object
        The ``document.health`` view: ``failure_threshold``,
        ``success_threshold`` (consecutive counts, each ``>= 1``).
    probes : dict of str to HealthProbe
        The built probes keyed by the document's names; a probe that
        carries a name must sit under it.
    clock : clock.Clock
        Injected; ``stop()`` stamps with it.
    alerts : alerts.AlertRouter
        Where each transition's alert is raised (``raise_alert``).
    ledger : ledger.Ledger
        Where each transition's ``health`` record is appended.
    metrics : metrics.Metrics
        Held for the phase-2 health gauges; the phase-1 table has none.
    in_degraded : bool, optional
        Whether ``can_heartbeat()`` holds in ``degraded`` —
        ``document.heartbeat.in_degraded``; default ``DEFAULT_IN_DEGRADED``.

    Examples
    --------
    Evaluate once per tick and gate acting on the answer::

        health = Health(
            document.health, {"disk": LedgerWritableProbe(None, serve_root=serve)},
            clock=clock, alerts=router, ledger=ledger, metrics=Metrics(),
        )
        health.state                  # 'starting'
        health.evaluate(clock.now_ms())   # 'ready'
        health.can_act()              # True
    """

    def __init__(
        self,
        document_health,
        probes,
        *,
        clock,
        alerts,
        ledger,
        metrics,
        in_degraded=DEFAULT_IN_DEGRADED,
    ):
        problems = []
        failure_threshold = getattr(document_health, "failure_threshold", None)
        success_threshold = getattr(document_health, "success_threshold", None)
        check_int_param(problems, "health.failure_threshold", failure_threshold, ge=1)
        check_int_param(problems, "health.success_threshold", success_threshold, ge=1)
        if not isinstance(probes, dict):
            problems.append(f"probes must be a dict of name -> HealthProbe, got {probes!r}")
            probes = {}
        for name, probe in probes.items():
            if not isinstance(probe, HealthProbe):
                problems.append(f"probe {name!r} must be a HealthProbe, got {probe!r}")
            elif probe.name is not None and probe.name != name:
                problems.append(f"probe {name!r} is named {probe.name!r}; the two must agree")
        if not isinstance(in_degraded, bool):
            problems.append(f"in_degraded must be a bool, got {in_degraded!r}")
        for owner, method, label in (
            (clock, "now_ms", "clock"),
            (alerts, "raise_alert", "alerts"),
            (ledger, "append", "ledger"),
        ):
            if not callable(getattr(owner, method, None)):
                problems.append(f"{label} must provide {method}(), got {owner!r}")
        if problems:
            raise ProductionError(problems)
        self._failure_threshold = int(failure_threshold)
        self._success_threshold = int(success_threshold)
        self._probes = dict(probes)
        self._clock = clock
        self._alerts = alerts
        self._ledger = ledger
        self._metrics = metrics
        self._can_heartbeat_in = {_READY: True, _DEGRADED: in_degraded}
        self._state = _STARTING
        self._workers = {name: SupervisedWorker(name) for name in self._probes}
        self._tallies = {name: _Tally() for name in self._probes}
        self._evidence = {}
        self._unrecorded = []
        self._ordinal = 0
        self._lock = threading.Lock()

    @property
    def state(self):
        """Return the current ``vocab.HEALTH_STATES`` member."""
        return self._state

    def can_act(self):
        """Return whether a leg may submit: ``ready`` only.

        Returns
        -------
        bool
        """
        return self._state == _READY

    def can_heartbeat(self):
        """Return whether the heartbeat is sent: ``ready``, or ``degraded`` when declared.

        Returns
        -------
        bool
        """
        return self._can_heartbeat_in.get(self._state, False)

    def probe_once(self, name, now_ms):
        """Run one probe under its deadline and publish a stamped, masked result.

        Parameters
        ----------
        name : str
            A probe the machine was built with.
        now_ms : int
            The evaluation instant, stamped on the result.

        Returns
        -------
        ProbeResult
            ``ok=False`` with the reason when the probe raised, missed its
            deadline, or is still running an earlier check.

        Raises
        ------
        ProductionError
            If ``name`` is not one of the probes.
        """
        probe = self._probes.get(name)
        if probe is None:
            raise ProductionError([f"no probe named {name!r}; probes: {sorted(self._probes)}"])
        self._record_deferred()
        call = self._workers[name].call(probe.check, probe.timeout_s)
        if call.timed_out:
            result = ProbeResult(
                ok=False, at_ms=now_ms, detail=f"no answer within {probe.timeout_s}s"
            )
        elif call.error is not None:
            result = ProbeResult(
                ok=False, at_ms=now_ms, detail=redact(f"{type(call.error).__name__}: {call.error}")
            )
        elif not isinstance(call.value, ProbeResult):
            result = ProbeResult(ok=False, at_ms=now_ms, detail="the probe returned no ProbeResult")
        else:
            result = replace(call.value, at_ms=now_ms, detail=redact(call.value.detail))
        self._evidence[name] = result
        return result

    def evaluate(self, now_ms):
        """Run every probe, fold the hysteresis and transition if the target moved.

        Parameters
        ----------
        now_ms : int
            The evaluation instant.

        Returns
        -------
        str
            The state afterwards; a latched state (``unhealthy``,
            ``stopping``) answers itself without probing.
        """
        self._record_deferred()
        if self._state in _LATCHED:
            return self._state
        for name in self._probes:
            result = self.probe_once(name, now_ms)
            self._tallies[name].observe(
                result.ok, self._failure_threshold, self._success_threshold
            )
        failing = sorted(name for name, tally in self._tallies.items() if tally.failing)
        target = max(
            (_STATE_BY_SCOPE[self._probes[name].scope] for name in failing),
            key=vocab.HEALTH_STATES.index,
            default=_READY,
        )
        if target != self._state:
            cause = "probe:" + ",".join(failing) if failing else "probes_ok"
            self._transition(target, cause, now_ms, deferred=False)
        return self._state

    def mark_unhealthy(self, cause, now_ms):
        """Latch ``unhealthy`` from outside the probes — the heartbeat's dead-man.

        Callable from an auxiliary thread: the state and the alert change
        at once; the ``health`` record is appended by the next loop-thread
        verb (D23).

        Parameters
        ----------
        cause : str
            Why (``DEAD_CAUSE`` from the heartbeat); masked into the record.
        now_ms : int
            The instant.

        Returns
        -------
        str
            The state afterwards; a latched state is left alone.
        """
        if self._state not in _LATCHED:
            self._transition(_UNHEALTHY, cause, now_ms, deferred=True)
        return self._state

    def stop(self):
        """Move to ``stopping``; idempotent.

        Returns
        -------
        str
            ``"stopping"``.
        """
        self._record_deferred()
        if self._state != _STOPPING:
            self._transition(_STOPPING, "stop", self._clock.now_ms(), deferred=False)
        return self._state

    def _transition(self, to, cause, now_ms, deferred):
        """Change state, build the record (append or defer it) and raise the alert."""
        with self._lock:
            came_from, self._state = self._state, to
            self._ordinal += 1
            body = {
                "from": came_from,
                "to": to,
                "cause": redact(cause),
                "probe_evidence": {name: result.to_obj() for name, result in self._evidence.items()},
            }
            record = {
                "kind": "health",
                "id": f"health:{came_from}->{to}:{now_ms}:{self._ordinal}",
                "body": body,
            }
            if deferred:
                self._unrecorded.append(record)
        if not deferred:
            self._record_deferred()
            self._ledger.append(record)
        _log.log(
            vocab.SEVERITY_LEVELS[_SEVERITY_BY_STATE[to]]["logging"],
            "health %s -> %s (%s)", came_from, to, body["cause"],
        )
        self._alerts.raise_alert(
            Alert(
                fingerprint=f"health:{came_from}->{to}",
                severity=_SEVERITY_BY_STATE[to],
                status="firing",
                summary=f"health {came_from} -> {to}: {body['cause']}",
                source="health",
                tick_id=None,
                at_ms=now_ms,
                labels={"from": came_from, "to": to},
            )
        )

    def _record_deferred(self):
        """Append the records an auxiliary thread left behind, on this (loop) thread."""
        with self._lock:
            pending, self._unrecorded = self._unrecorded, []
        for record in pending:
            self._ledger.append(record)


# ---------------------------------------------------------------------------
# The heartbeat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HeartbeatPayload:
    """One heartbeat emission — the four fields §5.11 names, nothing of a tick.

    Parameters
    ----------
    process_id : str
        The emitting process.
    sequence : int
        The heartbeat's own counter, from 1.
    at_ms : int
        The emission instant.
    status : str
        The health state at emission.

    Examples
    --------
    ::

        payload = HeartbeatPayload(process_id="proc-a", sequence=1, at_ms=0, status="ready")
        payload.to_obj()   # {'process_id': 'proc-a', 'sequence': 1, 'at_ms': 0, 'status': 'ready'}
    """

    process_id: str
    sequence: int
    at_ms: int
    status: str

    def to_obj(self):
        """Return the payload as a JSON-ready dict, fields in declared order.

        Returns
        -------
        dict
            ``{"process_id", "sequence", "at_ms", "status"}``.
        """
        return {
            "process_id": self.process_id,
            "sequence": self.sequence,
            "at_ms": self.at_ms,
            "status": self.status,
        }


class HeartbeatEmitter(_Configured):
    """The emitter seam: ``emit(payload)``; a raise is a failure the heartbeat counts.

    Parameters
    ----------
    params : dict or None
        The ``heartbeat.emitters.<name>.params`` block, default-deny over
        the subclass's ``_PARAMS`` plus ``notes``.

    Examples
    --------
    A child emitter that pokes its own supervisor::

        class SocketEmitter(HeartbeatEmitter):
            def emit(self, payload):
                pass   # write payload.to_obj() somewhere; raise on failure

        SocketEmitter(None).emit(HeartbeatPayload("proc-a", 1, 0, "ready"))
    """

    @staticmethod
    def body(payload):
        """Return the payload as the masked JSON-ready dict an emitter writes.

        Parameters
        ----------
        payload : HeartbeatPayload
            What to send.

        Returns
        -------
        dict
            ``payload.to_obj()`` with every string through ``redact``.
        """
        return redact_strings(payload.to_obj())

    @abstractmethod
    def emit(self, payload):
        """Send one heartbeat.

        Parameters
        ----------
        payload : HeartbeatPayload
            What to send; serialize it through :meth:`body`.

        Returns
        -------
        None
            Raise to report a failure; the heartbeat swallows and counts it.
        """


class FileEmitter(HeartbeatEmitter):
    """``file``: rewrite ``heartbeat.json`` atomically and durably on every beat.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.
    path : str
        Required; ``serve_root.heartbeat_path``.

    Examples
    --------
    ::

        emitter = FileEmitter(None, path=serve.heartbeat_path)
        emitter.emit(HeartbeatPayload("proc-a", 1, 0, "ready"))   # one JSON object on disk
    """

    def __init__(self, params, *, path=None):
        super().__init__(params)
        problems = []
        _check_str(problems, "path", path)
        if problems:
            raise ProductionError(problems)
        self._path = path

    def emit(self, payload):
        """Stage, fsync and rename the payload into place; see :meth:`HeartbeatEmitter.emit`."""
        durable_write_json(self._path, self.body(payload))


class UrlEmitter(HeartbeatEmitter):
    """``url``: POST the payload to ``secrets[url_env]`` within ``timeout_s``.

    The URL is a credential exactly as a webhook sink's is: named by an
    env var, read at emission, never stored. A 2xx is success; any other
    status raises, which the heartbeat counts as a failure.

    Parameters
    ----------
    params : dict
        ``url_env`` (str, required) and ``timeout_s`` (positive seconds,
        default ``DEFAULT_EMIT_TIMEOUT_S``).
    transport : resilience.Transport
        Required.
    secrets : dskit.pipeline.env.Secrets
        Required; ``url_env`` must be resolvable in it.

    Examples
    --------
    ::

        emitter = UrlEmitter(
            {"url_env": "DEADMAN_URL", "timeout_s": 2},
            transport=UrllibTransport({}), secrets=resolve_secrets(document.env),
        )
        emitter.emit(HeartbeatPayload("proc-a", 1, 0, "ready"))   # one POST
    """

    _PARAMS = ("url_env", "timeout_s")

    def __init__(self, params, *, transport=None, secrets=None):
        super().__init__(params)
        problems = []
        url_env = self._params["url_env"]
        if not callable(getattr(transport, "send", None)):
            problems.append("a url emitter requires the transport collaborator (send)")
        if secrets is None:
            problems.append("a url emitter requires the secrets collaborator")
        elif url_env not in secrets:
            problems.append(f"url_env {url_env!r} cannot be resolved — list it in env.require")
        if problems:
            raise ProductionError(problems)
        self._url_env = url_env
        self._timeout_s = self._params.get("timeout_s", DEFAULT_EMIT_TIMEOUT_S)
        self._transport = transport
        self._secrets = secrets

    @classmethod
    def _check(cls, problems, params):
        """Require ``url_env``; bound ``timeout_s``."""
        _check_str(problems, "url_env", params.get("url_env"))
        if "timeout_s" in params:
            check_timeout_s(problems, "timeout_s", params["timeout_s"])

    def emit(self, payload):
        """POST the payload; see :meth:`HeartbeatEmitter.emit`.

        Raises
        ------
        ProductionError
            On a non-2xx answer; the transport's own raise passes through.
        """
        body = json.dumps(self.body(payload), sort_keys=True).encode("utf-8")
        status, _headers, _payload = self._transport.send(
            "POST",
            self._secrets[self._url_env],
            dict(_JSON_HEADERS),
            body,
            deadline(self._timeout_s),
        )
        if not 200 <= status < 300:
            raise ProductionError([f"heartbeat url answered HTTP {status}"])


class Heartbeat:
    """Emit ``{process_id, sequence, at_ms, status}`` at ``every_s``; stop when the loop is dead.

    ``beat(now_ms)`` is the driver: the dead-man check first (has more
    than ``dead_after_ms`` passed since the last completed tick's
    monotonic stamp? then ``health.mark_unhealthy(DEAD_CAUSE)`` and no
    beat, ever again), the ``every_s`` gate second, ``health.can_heartbeat()``
    third; then one payload to every emitter, each failure swallowed and
    counted. ``start()`` runs the driver on one daemon worker so the
    cadence is independent of tick duration.

    Parameters
    ----------
    document_heartbeat : object
        The ``document.heartbeat`` view: ``every_s`` (int, at least
        ``MIN_HEARTBEAT_EVERY_S``). ``in_degraded`` is health's knob.
    emitters : dict of str to HeartbeatEmitter
        The built emitters keyed by the document's names.
    clock : clock.Clock
        Injected; ``monotonic()`` for the dead-man, ``now_ms()`` for the worker.
    health : Health
        Whose ``can_heartbeat``, ``state`` and ``mark_unhealthy`` this reads.
    process_id : str
        Stamped on every payload.
    dead_after_ms : int
        ``document.schedule.dead_after_ms``.

    Examples
    --------
    ::

        beat = Heartbeat(
            document.heartbeat, {"file": FileEmitter(None, path=serve.heartbeat_path)},
            clock=clock, health=health, process_id="proc-a", dead_after_ms=600_000,
        )
        beat.note_tick_completed(clock.monotonic())
        beat.beat(clock.now_ms()).sequence   # 1
    """

    def __init__(self, document_heartbeat, emitters, *, clock, health, process_id, dead_after_ms):
        problems = []
        every_s = getattr(document_heartbeat, "every_s", None)
        check_int_param(problems, "heartbeat.every_s", every_s, ge=MIN_HEARTBEAT_EVERY_S)
        if not isinstance(emitters, dict):
            problems.append(f"emitters must be a dict of name -> HeartbeatEmitter, got {emitters!r}")
            emitters = {}
        for name, emitter in emitters.items():
            if not isinstance(emitter, HeartbeatEmitter):
                problems.append(f"emitter {name!r} must be a HeartbeatEmitter, got {emitter!r}")
        _check_str(problems, "process_id", process_id)
        check_int_param(problems, "dead_after_ms", dead_after_ms, ge=1)
        for method in ("now_ms", "monotonic"):
            if not callable(getattr(clock, method, None)):
                problems.append(f"clock must provide {method}(), got {clock!r}")
        for method in ("can_heartbeat", "mark_unhealthy"):
            if not callable(getattr(health, method, None)):
                problems.append(f"health must provide {method}(), got {health!r}")
        if problems:
            raise ProductionError(problems)
        self._every_s = int(every_s)
        self._every_ms = self._every_s * _MS_PER_S
        self._emitters = dict(emitters)
        self._clock = clock
        self._health = health
        self._process_id = process_id
        self._dead_after_ms = int(dead_after_ms)
        self._last_tick_monotonic = clock.monotonic()
        self._last_beat_ms = None
        self._sequence = 0
        self._failures = 0
        self._stop = threading.Event()
        self._thread = None

    @property
    def sequence(self):
        """Return the number of beats emitted so far."""
        return self._sequence

    @property
    def failures(self):
        """Return how many emissions failed — counted and swallowed, never raised."""
        return self._failures

    def note_tick_completed(self, monotonic_s):
        """Restart the dead-man countdown from a completed tick's monotonic stamp.

        Parameters
        ----------
        monotonic_s : float
            ``clock.monotonic()`` taken when the tick completed.

        Raises
        ------
        ProductionError
            If the stamp is not a number.
        """
        if isinstance(monotonic_s, bool) or not isinstance(monotonic_s, (int, float)):
            raise ProductionError([f"a monotonic stamp is a number of seconds, got {monotonic_s!r}"])
        self._last_tick_monotonic = float(monotonic_s)

    def beat(self, now_ms):
        """Emit one heartbeat if the loop is alive, the cadence allows and health permits.

        Parameters
        ----------
        now_ms : int
            The emission instant.

        Returns
        -------
        HeartbeatPayload or None
            What was emitted, or ``None`` when withheld — including the
            beat that finds the loop dead and latches health ``unhealthy``.
        """
        elapsed_ms = (self._clock.monotonic() - self._last_tick_monotonic) * _MS_PER_S
        if elapsed_ms > self._dead_after_ms:
            self._health.mark_unhealthy(DEAD_CAUSE, now_ms)
            return None
        if self._last_beat_ms is not None and now_ms - self._last_beat_ms < self._every_ms:
            return None
        if not self._health.can_heartbeat():
            return None
        self._sequence += 1
        payload = HeartbeatPayload(
            process_id=self._process_id,
            sequence=self._sequence,
            at_ms=now_ms,
            status=self._health.state,
        )
        for name, emitter in self._emitters.items():
            try:
                emitter.emit(payload)
            except Exception as exc:  # an emitter's fault is counted, never raised
                self._failures += 1
                _log.warning(
                    "heartbeat %d not emitted by %s: %s", payload.sequence, name,
                    redact(f"{type(exc).__name__}: {exc}"),
                )
        self._last_beat_ms = now_ms
        return payload

    def start(self):
        """Run ``beat`` on one daemon worker every ``every_s`` seconds.

        Raises
        ------
        ProductionError
            If already started or closed.
        """
        if self._thread is not None or self._stop.is_set():
            raise ProductionError(["the heartbeat is already started or closed"])
        self._thread = threading.Thread(
            target=self._work, name="dskit-production-heartbeat", daemon=True
        )
        self._thread.start()

    def close(self):
        """Stop the worker; idempotent, bounded by one cadence.

        Returns
        -------
        None
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self._every_s)

    def _work(self):
        """Beat now, then wait one cadence, until stopped."""
        while not self._stop.is_set():
            self.beat(self._clock.now_ms())
            self._stop.wait(self._every_s)


# ---------------------------------------------------------------------------
# One process per series; signals set a flag
# ---------------------------------------------------------------------------


class InstanceLock:
    """``flock(LOCK_EX | LOCK_NB)`` on ``serve.lock``: the second instance refuses.

    Taken by the loop (or a CLI verb) BEFORE the ledger opens and handed
    to ``JsonlLedger(..., lock=)`` so the series has exactly one lock
    (ruling R18). Same filesystem only; the §5.7.2 fenced lease covers
    other hosts.

    Parameters
    ----------
    lock_path : str
        ``serve_root.lock_path``; its directory must exist.

    Attributes
    ----------
    path : str
        As given.
    fd : int or None
        The open descriptor while held.
    held : bool
        Whether this object holds the lock.

    Examples
    --------
    ::

        with InstanceLock(serve.lock_path) as lock:
            lock.held   # True — a second process's acquire() now refuses
    """

    def __init__(self, lock_path):
        problems = []
        _check_str(problems, "lock_path", lock_path)
        if problems:
            raise ProductionError(problems)
        self._path = lock_path
        self._fd = None

    @property
    def path(self):
        """Return the lock file's path."""
        return self._path

    @property
    def fd(self):
        """Return the held descriptor, or ``None``."""
        return self._fd

    @property
    def held(self):
        """Return whether this object holds the lock."""
        return self._fd is not None

    def acquire(self):
        """Take the lock without blocking; idempotent while held.

        Raises
        ------
        ProductionError
            If the file cannot be created, or another process holds it.
        """
        if self._fd is not None:
            return
        try:
            fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            raise ProductionError([f"cannot create the instance lock {self._path}: {exc}"]) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise ProductionError(
                [f"{self._path} is held by another process ({exc}) — already running"]
            ) from exc
        self._fd = fd

    def release(self):
        """Unlock and close; idempotent.

        Returns
        -------
        None
        """
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self):
        """Acquire on entry."""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        """Release on exit."""
        self.release()


class SignalHandler:
    """Turn SIGTERM and SIGINT into a set ``threading.Event``, nothing more.

    The handler runs on whatever stack the signal interrupted, so it does
    no shutdown work: it sets the flag and returns, and the loop notices
    within one sleep slice (``clock.MAX_SLEEP_SLICE_S``). ``restore()``
    puts the previous dispositions back.

    Parameters
    ----------
    stop_flag : threading.Event
        Set on either signal.

    Examples
    --------
    ::

        stop = threading.Event()
        with SignalHandler(stop):
            pass   # a SIGTERM in here sets `stop`
    """

    def __init__(self, stop_flag):
        if not callable(getattr(stop_flag, "set", None)):
            raise ProductionError([f"stop_flag must be a threading.Event, got {stop_flag!r}"])
        self._stop_flag = stop_flag
        self._previous = {}

    def install(self):
        """Install the handler for every signal in the set; idempotent.

        Raises
        ------
        ProductionError
            When not called from the main thread, where Python forbids it.
        """
        if self._previous:
            return
        try:
            for signum in _SIGNALS:
                self._previous[signum] = signal.signal(signum, self._handle)
        except ValueError as exc:
            self.restore()
            raise ProductionError([f"signal handlers install on the main thread only: {exc}"]) from exc

    def restore(self):
        """Put the previous handlers back; idempotent."""
        previous, self._previous = self._previous, {}
        for signum, handler in previous.items():
            signal.signal(signum, handler)

    def _handle(self, signum, frame):
        """Set the flag; never act here."""
        self._stop_flag.set()

    def __enter__(self):
        """Install on entry."""
        self.install()
        return self

    def __exit__(self, exc_type, exc, tb):
        """Restore on exit."""
        self.restore()


# ---------------------------------------------------------------------------
# The registries — open doorways (§4.3)
# ---------------------------------------------------------------------------

PROBE_KINDS = Registry("probe", HealthProbe)
PROBE_KINDS.register("executor-check", ExecutorCheckProbe)
PROBE_KINDS.register("feed-age", FeedAgeProbe)
PROBE_KINDS.register("ledger-writable", LedgerWritableProbe)

HEARTBEAT_KINDS = Registry("heartbeat_emitter", HeartbeatEmitter)
HEARTBEAT_KINDS.register("file", FileEmitter)
HEARTBEAT_KINDS.register("url", UrlEmitter)

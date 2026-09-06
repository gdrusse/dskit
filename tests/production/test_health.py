"""`health.py` — the state machine, the heartbeat, the lock and the signals.

D18 is the ruling this file tests: **the dead-man's switch is external**. This
process cannot page anyone about its own death, so everything here exists to
make its death VISIBLE and its behaviour while dying SAFE:

- **Scope decides the answer, not the probe.** A `local` failure (the ledger is
  unwritable) means this process is broken: `unhealthy`, which stops acting AND
  stops heartbeating so the external supervisor pages. A `dependency` failure or
  staleness (the venue, the feed) means the world is broken: `degraded`, which
  keeps observing and refuses acts. That split is the whole of D18, and a probe
  that raises or hangs is a FAILURE — recorded as `ok=False` with the reason,
  never an exception that escapes into a tick.
- **Hysteresis, then transitions.** `failure_threshold` consecutive failures
  before a probe counts as failing, `success_threshold` before it counts as
  recovered — so one blip is not a page. Alerts fire on TRANSITIONS, not on
  levels: a hundred evaluations in `degraded` are one alert, which is the
  difference between an on-call rotation that reads alerts and one that mutes
  them.
- **The heartbeat is not the tick.** Its cadence, sequence and instant are its
  own (`{process_id, sequence, at_ms, status}`), so a heartbeat cannot be
  forged by a loop that is spinning without doing work. It watches the atomic
  monotonic stamp of the last successful tick, and when `dead_after_ms` elapses
  it makes health `unhealthy` and STOPS — deliberately withdrawing the signal
  the supervisor is watching for, because a process that keeps saying "alive"
  while ticking nothing is worse than one that goes quiet.
- **One process per series.** `flock(LOCK_EX | LOCK_NB)` refuses the second
  instance on the same filesystem; the subprocess test is the only honest way
  to prove that, since two locks in one process are a different question.
- **Signals set a flag, they do not act.** A handler that ran shutdown logic
  would run it on whatever stack the signal interrupted; it sets a
  `threading.Event` the loop notices within one sleep slice.

Determinism: every instant comes from a `TestClock`; probes and emitters are
injected doubles; the only real waits are the ones whose subject IS a thread or
a process, each bounded well under a second.
"""

import json
import os
from types import SimpleNamespace
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.clock import MAX_SLEEP_SLICE_S, TestClock
from dskit.production.document import ServeDocument
from dskit.production.health import (
    DEFAULT_IN_DEGRADED,
    DEFAULT_PROBE_TIMEOUT_S,
    HEARTBEAT_KINDS,
    PROBE_KINDS,
    ExecutorCheckProbe,
    FeedAgeProbe,
    FileEmitter,
    Health,
    Heartbeat,
    HeartbeatEmitter,
    HeartbeatPayload,
    HealthProbe,
    InstanceLock,
    LedgerWritableProbe,
    ProbeResult,
    SignalHandler,
    SystemdEmitter,
    UrlEmitter,
)
from dskit.pipeline.env import Secrets
from dskit.production.ledger import ServeRoot
from dskit.production.metrics import Metrics
from dskit.production.records import Alert, FeedAge
from dskit.production.redact import REDACTED, register_secret
from tests.production.test_document import example_document

#: The instant every test starts at.
NOW_MS = 1_767_225_600_000

#: The series and process the records are written under.
SERIES = "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1"
PROCESS = "proc-a"

#: `schedule.dead_after_ms` in the §4.1 illustration.
DEAD_AFTER_MS = 600_000

#: The env-var NAME a `url` emitter points at, and a value shaped like a
#: real endpoint — the URL is a credential exactly as a webhook sink's is.
URL_ENV = "OPS_WEBHOOK_URL"
BEAT_URL = "https://deadman.example.test/ping/QqSecretPath"

#: `HealthProbe.check()` answers with this shape (§5.11).
PROBE_RESULT_FIELDS = ("ok", "at_ms", "detail")

#: What one heartbeat emission carries — §5.11 names exactly these four.
PAYLOAD_FIELDS = ("process_id", "sequence", "at_ms", "status")


# --------------------------------------------------------------------------
# doubles
# --------------------------------------------------------------------------


class Env:
    """A `Secrets`-shaped resolver recording every value read."""

    def __init__(self, values=None):
        # `{}` means "nothing resolvable" — only an omitted argument gets the default.
        self._values = dict(values if values is not None else {URL_ENV: BEAT_URL})
        self.reads = []

    def __contains__(self, name):
        return name in self._values

    def __getitem__(self, name):
        self.reads.append(name)
        return self._values[name]


class FakeTransport:
    """A `Transport`-shaped recorder: no socket, one `send`, a value back."""

    def __init__(self, answer=(204, {}, b""), error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def send(self, method, url, headers, body, timeout):
        """Record the call and answer (or raise) as configured."""
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "body": body,
             "timeout": dict(timeout or {})}
        )
        if self.error is not None:
            raise self.error
        return self.answer


class FakeRouter:
    """An `AlertRouter`-shaped recorder: `raise_alert` and nothing else."""

    def __init__(self):
        self.alerts = []

    def raise_alert(self, alert):
        """Record the alert and accept it, as the real router does."""
        assert isinstance(alert, Alert)
        self.alerts.append(alert)
        return True

    def resolve(self, fingerprint):
        """Record a resolution; health never calls this, but a double may be handed one."""
        return False


class FakeLedger:
    """Records what health appended, refusing anything but `{kind, id, body}`."""

    def __init__(self):
        self.records = []
        self.barriers = 0

    def append(self, record):
        """Append one record and return its dense 1-based seq."""
        assert set(record) == {"kind", "id", "body"}, record
        assert record["kind"] in vocab.RECORD_KINDS
        json.dumps(record)
        self.records.append(record)
        return len(self.records)

    def barrier(self):
        """Count a barrier."""
        self.barriers += 1

    def of_kind(self, kind):
        """Every appended record of one kind, in order."""
        return [r for r in self.records if r["kind"] == kind]


class ScriptedProbe(HealthProbe):
    """A probe whose every `check()` answer the test wrote down in advance."""

    _PARAMS = ()

    def __init__(self, params=None, *, answers=(), **kw):
        super().__init__(params, **kw)
        self.answers = list(answers)
        self.calls = 0

    def check(self):
        """Return (or raise) the next scripted answer; the last one repeats."""
        self.calls += 1
        answer = self.answers[min(self.calls - 1, len(self.answers) - 1)]
        if isinstance(answer, BaseException):
            raise answer
        return ProbeResult(ok=bool(answer), at_ms=NOW_MS, detail="" if answer else "scripted")


class HangingProbe(HealthProbe):
    """A probe whose `check()` never returns until the test releases it."""

    _PARAMS = ()

    def __init__(self, params=None, **kw):
        super().__init__(params, **kw)
        self.released = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def check(self):
        """Block; the supervisor must not wait for this."""
        self.calls += 1
        self.entered.set()
        self.released.wait(10)
        return ProbeResult(ok=True, at_ms=NOW_MS, detail="late")


class LifecycleEmitter(HeartbeatEmitter):
    """An emitter that records which lifecycle verbs it was told about."""

    def __init__(self, params=None, *, error=None, **kw):
        super().__init__(params, **kw)
        self.error = error
        self.lifecycle = []

    def emit(self, payload):
        """Nothing; this emitter's subject is the two hooks."""

    def ready(self):
        """Record the transition, raising if the test asked for a failure."""
        self._note("ready")

    def stopping(self):
        """Record the transition, raising if the test asked for a failure."""
        self._note("stopping")

    def _note(self, verb):
        self.lifecycle.append(verb)
        if self.error is not None:
            raise self.error


class RecordingEmitter(HeartbeatEmitter):
    """An emitter that keeps every payload, or fails on demand."""

    _PARAMS = ()

    def __init__(self, params=None, *, error=None, **kw):
        super().__init__(params, **kw)
        self.error = error
        self.payloads = []

    def emit(self, payload):
        """Record the payload, raising if the test asked for a failure."""
        self.payloads.append(payload)
        if self.error is not None:
            raise self.error


class FakeExecutor:
    """An `Executor`-shaped double for the `executor-check` probe."""

    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.calls = 0

    def check(self, config=None):
        """Answer the conformance question, or raise."""
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.answer


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def health_doc(**health):
    """The `document.health` view, with the illustration's values overridden."""
    obj = example_document()
    for key, value in health.items():
        if value is None:
            obj["health"].pop(key, None)
        else:
            obj["health"][key] = value
    return ServeDocument.from_obj(obj).health


def heartbeat_doc(**heartbeat):
    """The `document.heartbeat` view, with the illustration's values overridden."""
    obj = example_document()
    for key, value in heartbeat.items():
        if value is None:
            obj["heartbeat"].pop(key, None)
        else:
            obj["heartbeat"][key] = value
    return ServeDocument.from_obj(obj).heartbeat


def a_health(probes, clock, *, alerts=None, ledger=None, metrics=None, **health):
    """A `Health` over the given probes with every collaborator injected."""
    return Health(
        health_doc(**health),
        probes,
        clock=clock,
        alerts=alerts if alerts is not None else FakeRouter(),
        ledger=ledger if ledger is not None else FakeLedger(),
        metrics=metrics if metrics is not None else Metrics(),
    )


def a_heartbeat(emitters, clock, health, *, every_s=60, in_degraded=None, dead_after_ms=DEAD_AFTER_MS):
    """A `Heartbeat` over the given emitters, wired to a health machine."""
    return Heartbeat(
        heartbeat_doc(every_s=every_s, in_degraded=in_degraded),
        emitters,
        clock=clock,
        health=health,
        process_id=PROCESS,
        dead_after_ms=dead_after_ms,
    )


def ready_health(clock, **kw):
    """A `Health` whose one probe always passes, already evaluated to `ready`."""
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock, **kw)
    health.evaluate(clock.now_ms())
    return health


@pytest.fixture
def clock():
    """The one instant every test moves by hand."""
    return TestClock(start_ms=NOW_MS)


@pytest.fixture
def serve(tmp_path):
    """A real serve-series root — the ledger-writable probe's subject."""
    return ServeRoot(str(tmp_path / "serve"), SERIES)


@pytest.fixture
def hanging_probe():
    """A `HangingProbe` whose blocked worker is released at teardown."""
    probe = HangingProbe(None, name="stuck", scope="local", timeout_s=0.05)
    yield probe
    probe.released.set()


# --------------------------------------------------------------------------
# the probe seam
# --------------------------------------------------------------------------


def test_health_probe_is_abstract_and_check_is_the_hook():
    with pytest.raises(TypeError):
        HealthProbe(None)

    class NoCheck(HealthProbe):
        pass

    with pytest.raises(TypeError):
        NoCheck(None)
    assert isinstance(ScriptedProbe(answers=[True]), HealthProbe)


def test_probe_result_is_a_frozen_value_of_exactly_three_fields():
    result = ProbeResult(ok=False, at_ms=NOW_MS, detail="disk full")
    assert tuple(result.to_obj()) == PROBE_RESULT_FIELDS
    assert result.to_obj() == {"ok": False, "at_ms": NOW_MS, "detail": "disk full"}
    with pytest.raises(Exception):
        result.ok = True


def test_the_probe_registry_carries_exactly_the_three_kinds():
    assert PROBE_KINDS.family == "probe"
    assert PROBE_KINDS.kinds() == ("executor-check", "feed-age", "ledger-writable")
    for name in PROBE_KINDS.kinds():
        assert issubclass(PROBE_KINDS.resolve(name), HealthProbe)
    with pytest.raises(ProductionError):
        PROBE_KINDS.resolve("ping")


def test_each_core_probe_declares_the_scope_the_plan_gives_it(serve):
    # §5.11: `ledger-writable` (local), `executor-check` (dependency),
    # `feed-age` (dependency). D18 turns that word into unhealthy vs
    # degraded, so it is a safety fact, not documentation.
    assert LedgerWritableProbe(None, serve_root=serve).scope == "local"
    assert ExecutorCheckProbe(None, executor=FakeExecutor()).scope == "dependency"
    assert FeedAgeProbe(None, feed_ages=lambda: (), max_age_ms=1_000).scope == "dependency"
    for probe in (
        LedgerWritableProbe(None, serve_root=serve),
        ExecutorCheckProbe(None, executor=FakeExecutor()),
        FeedAgeProbe(None, feed_ages=lambda: (), max_age_ms=1_000),
    ):
        assert probe.scope in vocab.PROBE_SCOPES


def test_a_probe_takes_its_name_scope_and_timeout_from_the_document(serve):
    # `health.probes.<name>` carries `scope` and `timeout_s` beside
    # `uses`/`params`, so a document may override the class default.
    probe = LedgerWritableProbe(None, serve_root=serve, name="disk", scope="dependency", timeout_s=2.5)
    assert (probe.name, probe.scope, probe.timeout_s) == ("disk", "dependency", 2.5)


def test_an_absent_probe_timeout_uses_the_one_named_default(serve):
    assert LedgerWritableProbe(None, serve_root=serve).timeout_s == DEFAULT_PROBE_TIMEOUT_S
    assert DEFAULT_PROBE_TIMEOUT_S > 0


def test_a_probe_refuses_an_unknown_param(serve):
    with pytest.raises(ProductionError) as exc:
        LedgerWritableProbe({"invented_knob": 1}, serve_root=serve)
    assert "invented_knob" in str(exc.value)


def test_a_probe_refuses_a_scope_outside_the_vocabulary(serve):
    with pytest.raises(ProductionError) as exc:
        LedgerWritableProbe(None, serve_root=serve, scope="somewhere")
    assert "somewhere" in str(exc.value)


def test_the_ledger_writable_probe_passes_on_a_writable_series(serve, clock):
    result = LedgerWritableProbe(None, serve_root=serve).check()
    assert isinstance(result, ProbeResult)
    assert result.ok is True


def test_the_ledger_writable_probe_fails_when_the_series_cannot_be_written(tmp_path):
    # A regular file where the series directory should be fails the probe's
    # write for every uid — mode bits alone would not stop root.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    probe = LedgerWritableProbe(None, serve_root=SimpleNamespace(series_path=str(blocker)))
    result = probe.check()
    assert result.ok is False
    assert result.detail


def test_the_ledger_writable_probe_leaves_nothing_behind(serve):
    before = sorted(os.listdir(serve.series_path))
    LedgerWritableProbe(None, serve_root=serve).check()
    assert sorted(os.listdir(serve.series_path)) == before


def test_the_executor_check_probe_asks_the_executor():
    executor = FakeExecutor()
    assert ExecutorCheckProbe(None, executor=executor).check().ok is True
    assert executor.calls == 1


def test_the_executor_check_probe_fails_when_the_executor_raises():
    probe = ExecutorCheckProbe(None, executor=FakeExecutor(error=OSError("venue down")))
    result = probe.check()
    assert result.ok is False
    assert "venue down" in result.detail


def test_the_executor_check_probe_reports_a_refusal_as_a_failure():
    # `Executor.check(config)` answers with the problems it found; a
    # non-empty answer is a venue that will not accept our orders.
    probe = ExecutorCheckProbe(None, executor=FakeExecutor(answer=["scope mismatch"]))
    result = probe.check()
    assert result.ok is False
    assert "scope mismatch" in result.detail


def test_the_feed_age_probe_passes_while_every_key_is_inside_the_bound(clock):
    # `feed_ages` is a zero-argument callable answering the tick's
    # `tuple[FeedAge]` — an atomic snapshot, never the feed itself (D23).
    ages = (FeedAge(key="INS1", age_ms=500, watermark_ms=NOW_MS - 500),
            FeedAge(key="INS2", age_ms=900, watermark_ms=NOW_MS - 900))
    probe = FeedAgeProbe(None, feed_ages=lambda: ages, max_age_ms=1_000)
    assert probe.check().ok is True


def test_the_feed_age_probe_fails_on_the_oldest_key_past_the_bound(clock):
    ages = (FeedAge(key="INS1", age_ms=500, watermark_ms=NOW_MS - 500),
            FeedAge(key="INS2", age_ms=5_000, watermark_ms=NOW_MS - 5_000))
    probe = FeedAgeProbe(None, feed_ages=lambda: ages, max_age_ms=1_000)
    result = probe.check()
    assert result.ok is False
    assert "INS2" in result.detail


def test_the_feed_age_bound_is_inclusive(clock):
    ages = (FeedAge(key="INS1", age_ms=1_000, watermark_ms=NOW_MS - 1_000),)
    assert FeedAgeProbe(None, feed_ages=lambda: ages, max_age_ms=1_000).check().ok is True


def test_the_feed_age_probe_has_no_ages_before_the_first_tick(clock):
    # Nothing has been fetched yet; that is not a dependency failure.
    assert FeedAgeProbe(None, feed_ages=lambda: (), max_age_ms=1_000).check().ok is True


def test_the_max_age_bound_is_injected_not_a_literal():
    # `document.schedule.max_staleness_ms` is the one owner of "too old";
    # a probe that carried its own copy would drift from the tick's gate.
    with pytest.raises(TypeError):
        FeedAgeProbe(None, feed_ages=lambda: ())


# --------------------------------------------------------------------------
# supervised probe execution — a raise or a hang is a failure, not a crash
# --------------------------------------------------------------------------


def test_probe_once_returns_a_result_and_stamps_the_instant(clock):
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    result = health.probe_once("disk", NOW_MS)
    assert isinstance(result, ProbeResult)
    assert result.ok is True


def test_a_probe_that_raises_is_recorded_as_a_failure(clock):
    health = a_health({"disk": ScriptedProbe(answers=[RuntimeError("disk exploded")])}, clock)
    result = health.probe_once("disk", NOW_MS)
    assert result.ok is False
    assert "disk exploded" in result.detail


def test_a_probe_that_raises_never_reaches_the_caller(clock):
    # A probe is auxiliary work (D23); an exception escaping it would kill
    # the tick it was supposed to be reporting on. It still COUNTS as a
    # failure — swallowing it into `ready` would be worse than crashing.
    health = a_health(
        {"disk": ScriptedProbe(answers=[RuntimeError("boom")], name="disk", scope="local")},
        clock,
        failure_threshold=1,
    )
    assert health.evaluate(NOW_MS) == "unhealthy"


def test_a_probe_failure_detail_is_redacted(clock):
    register_secret("probe-secret-value")
    health = a_health({"disk": ScriptedProbe(answers=[RuntimeError("auth probe-secret-value")])}, clock)
    result = health.probe_once("disk", NOW_MS)
    assert "probe-secret-value" not in result.detail
    assert REDACTED in result.detail


def test_a_hanging_probe_is_bounded_by_its_timeout(clock, hanging_probe):
    health = a_health({"stuck": hanging_probe}, clock)
    started = time.monotonic()
    result = health.probe_once("stuck", NOW_MS)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert result.ok is False
    assert result.detail


def test_a_hanging_probe_is_not_replaced_while_it_is_still_stuck(clock, hanging_probe):
    # D18: "a missed deadline or dead/stuck worker is a failure and no
    # replacement is spawned until it returns."
    health = a_health({"stuck": hanging_probe}, clock)
    health.probe_once("stuck", NOW_MS)
    assert hanging_probe.entered.is_set()
    threads_after_first = threading.active_count()
    for offset in (1_000, 2_000, 3_000):
        assert health.probe_once("stuck", NOW_MS + offset).ok is False
    assert threading.active_count() <= threads_after_first
    assert hanging_probe.calls == 1


def test_probe_once_refuses_a_name_the_document_never_declared(clock):
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    with pytest.raises(ProductionError):
        health.probe_once("nonesuch", NOW_MS)


# --------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------


def test_health_starts_in_starting(clock):
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    assert health.state == "starting"
    assert health.state in vocab.HEALTH_STATES


def test_a_passing_evaluation_reaches_ready(clock):
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    assert health.evaluate(NOW_MS) == "ready"
    assert health.state == "ready"


def test_a_local_failure_makes_the_process_unhealthy(clock):
    # D18: local failure -> unhealthy (stop acting AND heartbeating).
    probe = ScriptedProbe(answers=[False], name="disk", scope="local")
    health = a_health({"disk": probe}, clock, failure_threshold=1)
    assert health.evaluate(NOW_MS) == "unhealthy"


def test_a_dependency_failure_makes_the_process_degraded(clock):
    # D18: dependency failure or staleness -> degraded (observe, refuse acts).
    probe = ScriptedProbe(answers=[False], name="venue", scope="dependency")
    health = a_health({"venue": probe}, clock, failure_threshold=1)
    assert health.evaluate(NOW_MS) == "degraded"


def test_a_local_failure_outranks_a_dependency_failure(clock):
    health = a_health(
        {
            "disk": ScriptedProbe(answers=[False], name="disk", scope="local"),
            "venue": ScriptedProbe(answers=[False], name="venue", scope="dependency"),
        },
        clock,
        failure_threshold=1,
    )
    assert health.evaluate(NOW_MS) == "unhealthy"


def test_failure_threshold_hysteresis_holds_the_state_until_it_is_reached(clock):
    probe = ScriptedProbe(answers=[False], name="venue", scope="dependency")
    health = a_health({"venue": probe}, clock, failure_threshold=3, success_threshold=1)
    health.evaluate(NOW_MS)  # the first evaluation still has no history to break
    assert health.evaluate(NOW_MS + 1_000) != "degraded"
    assert health.evaluate(NOW_MS + 2_000) == "degraded"


def test_a_single_blip_never_changes_the_state(clock):
    probe = ScriptedProbe(answers=[True, False, True, True], name="venue", scope="dependency")
    health = a_health({"venue": probe}, clock, failure_threshold=3, success_threshold=1)
    states = [health.evaluate(NOW_MS + i * 1_000) for i in range(4)]
    assert states == ["ready", "ready", "ready", "ready"]


def test_success_threshold_hysteresis_holds_the_recovery(clock):
    probe = ScriptedProbe(
        answers=[False, False, True, True, True], name="venue", scope="dependency"
    )
    health = a_health({"venue": probe}, clock, failure_threshold=2, success_threshold=3)
    assert health.evaluate(NOW_MS) != "degraded"
    assert health.evaluate(NOW_MS + 1_000) == "degraded"
    assert health.evaluate(NOW_MS + 2_000) == "degraded"
    assert health.evaluate(NOW_MS + 3_000) == "degraded"
    assert health.evaluate(NOW_MS + 4_000) == "ready"


def test_can_act_is_ready_only(clock):
    ready = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    ready.evaluate(NOW_MS)
    assert ready.can_act() is True

    degraded = a_health(
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock,
        failure_threshold=1,
    )
    degraded.evaluate(NOW_MS)
    assert degraded.state == "degraded"
    assert degraded.can_act() is False

    unhealthy = a_health(
        {"disk": ScriptedProbe(answers=[False], name="disk", scope="local")},
        clock,
        failure_threshold=1,
    )
    unhealthy.evaluate(NOW_MS)
    assert unhealthy.can_act() is False


def test_starting_cannot_act(clock):
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock)
    assert health.state == "starting"
    assert health.can_act() is False


def test_can_heartbeat_is_ready_and_degraded_only_when_declared(clock):
    ready = ready_health(clock)
    assert ready.can_heartbeat() is True

    quiet = a_health(
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock,
        failure_threshold=1,
    )
    quiet.evaluate(NOW_MS)
    assert quiet.state == "degraded"
    assert quiet.can_heartbeat() is DEFAULT_IN_DEGRADED

    loud = Health(
        health_doc(failure_threshold=1),
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock=clock,
        alerts=FakeRouter(),
        ledger=FakeLedger(),
        metrics=Metrics(),
        in_degraded=True,
    )
    loud.evaluate(NOW_MS)
    assert loud.state == "degraded"
    assert loud.can_heartbeat() is True


def test_the_in_degraded_default_is_the_quiet_one():
    # §4.1 writes `in_degraded: false`; the safe default is to keep the
    # heartbeat honest rather than to keep it flowing.
    assert DEFAULT_IN_DEGRADED is False


def test_unhealthy_stops_both_acting_and_heartbeating(clock):
    health = a_health(
        {"disk": ScriptedProbe(answers=[False], name="disk", scope="local")},
        clock,
        failure_threshold=1,
    )
    health.evaluate(NOW_MS)
    assert health.state == "unhealthy"
    assert health.can_act() is False
    assert health.can_heartbeat() is False


def test_stop_moves_to_stopping_and_stays_there(clock):
    health = ready_health(clock)
    assert health.stop() == "stopping"
    assert health.state == "stopping"
    assert health.evaluate(NOW_MS + 1_000) == "stopping"
    assert health.can_act() is False
    assert health.can_heartbeat() is False


def test_the_dead_man_latch_cannot_be_lifted_by_a_passing_probe(clock):
    # A stalled tick loop is not made well by a writable disk: once
    # `dead_after_ms` has spoken, only a restart clears it.
    health = ready_health(clock)
    health.mark_unhealthy("tick_dead", NOW_MS)
    assert health.state == "unhealthy"
    assert health.evaluate(NOW_MS + 1_000) == "unhealthy"


# --------------------------------------------------------------------------
# transitions: one record, one alert, and never a level
# --------------------------------------------------------------------------


def test_a_transition_appends_a_health_record_with_the_four_fields(clock):
    ledger = FakeLedger()
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock, ledger=ledger)
    health.evaluate(NOW_MS)
    (record,) = ledger.of_kind("health")
    assert record["id"].startswith("health:")
    assert set(record["body"]) == {"from", "to", "cause", "probe_evidence"}
    assert record["body"]["from"] == "starting"
    assert record["body"]["to"] == "ready"


def test_the_records_probe_evidence_names_every_probe(clock):
    ledger = FakeLedger()
    health = a_health(
        {
            "disk": ScriptedProbe(answers=[True], name="disk", scope="local"),
            "venue": ScriptedProbe(answers=[False], name="venue", scope="dependency"),
        },
        clock,
        ledger=ledger,
        failure_threshold=1,
    )
    health.evaluate(NOW_MS)
    (record,) = ledger.of_kind("health")
    evidence = record["body"]["probe_evidence"]
    assert set(evidence) == {"disk", "venue"}
    assert set(evidence["disk"]) == set(PROBE_RESULT_FIELDS)
    assert evidence["venue"]["ok"] is False


def test_a_transition_raises_exactly_one_alert(clock):
    alerts = FakeRouter()
    health = a_health({"disk": ScriptedProbe(answers=[True])}, clock, alerts=alerts)
    health.evaluate(NOW_MS)
    assert len(alerts.alerts) == 1
    assert alerts.alerts[0].severity in vocab.SEVERITIES
    assert alerts.alerts[0].status == "firing"


def test_a_level_raises_nothing(clock):
    # §5.11: "transitions (not levels) raise alerts". Fifty evaluations in
    # one state must be one alert, or the pager becomes noise.
    alerts = FakeRouter()
    ledger = FakeLedger()
    health = a_health(
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock,
        alerts=alerts,
        ledger=ledger,
        failure_threshold=1,
    )
    for index in range(50):
        health.evaluate(NOW_MS + index * 1_000)
    assert health.state == "degraded"
    assert len(alerts.alerts) == 1  # starting -> degraded is ONE transition
    assert len(ledger.of_kind("health")) == 1


def test_a_recovery_transition_is_its_own_record_and_alert(clock):
    alerts = FakeRouter()
    ledger = FakeLedger()
    probe = ScriptedProbe(answers=[False, False, True], name="venue", scope="dependency")
    health = a_health(
        {"venue": probe}, clock, alerts=alerts, ledger=ledger,
        failure_threshold=1, success_threshold=1,
    )
    health.evaluate(NOW_MS)
    health.evaluate(NOW_MS + 1_000)
    health.evaluate(NOW_MS + 2_000)
    transitions = [(r["body"]["from"], r["body"]["to"]) for r in ledger.of_kind("health")]
    assert transitions == [("starting", "degraded"), ("degraded", "ready")]
    assert len(alerts.alerts) == 2


def test_the_alert_fingerprint_is_stable_across_the_same_transition(clock):
    # Two processes hitting the same transition must dedup at the router,
    # which is only true if the fingerprint is derived, not invented.
    first, second = FakeRouter(), FakeRouter()
    for alerts in (first, second):
        a_health({"disk": ScriptedProbe(answers=[True])}, clock, alerts=alerts).evaluate(NOW_MS)
    assert first.alerts[0].fingerprint == second.alerts[0].fingerprint


def test_the_unhealthy_transition_is_the_loudest(clock):
    # An operator triaging by severity must be able to tell "we are broken"
    # from "the venue is slow" without reading the body.
    degraded, unhealthy = FakeRouter(), FakeRouter()
    a_health(
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock, alerts=degraded, failure_threshold=1,
    ).evaluate(NOW_MS)
    a_health(
        {"disk": ScriptedProbe(answers=[False], name="disk", scope="local")},
        clock, alerts=unhealthy, failure_threshold=1,
    ).evaluate(NOW_MS)
    order = vocab.SEVERITIES
    assert order.index(unhealthy.alerts[-1].severity) > order.index(degraded.alerts[-1].severity)


def test_no_probes_at_all_is_ready(clock):
    # A shadow document may declare nothing to probe; that is not a fault.
    health = Health(
        health_doc(probes={}),
        {},
        clock=clock,
        alerts=FakeRouter(),
        ledger=FakeLedger(),
        metrics=Metrics(),
    )
    assert health.evaluate(NOW_MS) == "ready"


# --------------------------------------------------------------------------
# the heartbeat
# --------------------------------------------------------------------------


def test_the_heartbeat_payload_is_exactly_the_four_declared_fields(clock):
    emitter = RecordingEmitter(None)
    beat = a_heartbeat({"file": emitter}, clock, ready_health(clock))
    payload = beat.beat(NOW_MS)
    assert isinstance(payload, HeartbeatPayload)
    assert tuple(payload.to_obj()) == PAYLOAD_FIELDS
    assert payload.to_obj() == {
        "process_id": PROCESS, "sequence": 1, "at_ms": NOW_MS, "status": "ready",
    }


def test_the_sequence_is_the_heartbeats_own_and_starts_at_one(clock):
    emitter = RecordingEmitter(None)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1)
    sequences = []
    for index in range(4):
        at = NOW_MS + index * 1_000
        clock.set(at)
        sequences.append(beat.beat(at).sequence)
    assert sequences == [1, 2, 3, 4]
    assert [p.sequence for p in emitter.payloads] == [1, 2, 3, 4]


def test_the_heartbeat_is_keyed_by_the_process_id_not_a_tick_id(clock):
    emitter = RecordingEmitter(None)
    payload = a_heartbeat({"file": emitter}, clock, ready_health(clock)).beat(NOW_MS)
    assert payload.process_id == PROCESS
    assert "tick_id" not in payload.to_obj()


def test_every_s_paces_the_beat_independently_of_ticks(clock):
    # §5.11: "its own supervised worker and cadence independent of tick
    # duration". A beat asked for too early is not a beat.
    emitter = RecordingEmitter(None)
    beat = a_heartbeat({"file": emitter}, clock, ready_health(clock), every_s=60)
    assert beat.beat(NOW_MS) is not None
    clock.set(NOW_MS + 59_999)
    assert beat.beat(NOW_MS + 59_999) is None
    clock.set(NOW_MS + 60_000)
    assert beat.beat(NOW_MS + 60_000) is not None
    assert len(emitter.payloads) == 2


def test_every_s_must_be_at_least_one_second(clock):
    # `document.py` pins the bound; this pins that the heartbeat reads it
    # rather than carrying a second copy.
    obj = example_document()
    obj["heartbeat"]["every_s"] = 0
    with pytest.raises(ProductionError):
        ServeDocument.from_obj(obj)


def test_a_degraded_process_is_silent_unless_the_document_says_otherwise(clock):
    emitter = RecordingEmitter(None)
    health = a_health(
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock, failure_threshold=1,
    )
    health.evaluate(NOW_MS)
    assert health.state == "degraded"
    assert a_heartbeat({"file": emitter}, clock, health, in_degraded=False).beat(NOW_MS) is None
    assert emitter.payloads == []


def test_a_degraded_process_beats_when_in_degraded_is_declared(clock):
    emitter = RecordingEmitter(None)
    health = Health(
        health_doc(failure_threshold=1),
        {"venue": ScriptedProbe(answers=[False], name="venue", scope="dependency")},
        clock=clock, alerts=FakeRouter(), ledger=FakeLedger(), metrics=Metrics(),
        in_degraded=True,
    )
    health.evaluate(NOW_MS)
    payload = a_heartbeat({"file": emitter}, clock, health, in_degraded=True).beat(NOW_MS)
    assert payload is not None
    assert payload.status == "degraded"


def test_an_unhealthy_process_never_beats(clock):
    emitter = RecordingEmitter(None)
    health = a_health(
        {"disk": ScriptedProbe(answers=[False], name="disk", scope="local")},
        clock, failure_threshold=1,
    )
    health.evaluate(NOW_MS)
    assert a_heartbeat({"file": emitter}, clock, health).beat(NOW_MS) is None
    assert emitter.payloads == []


def test_dead_after_makes_health_unhealthy_and_stops_the_beat(clock):
    # D18: the heartbeat watches the last successful tick's MONOTONIC
    # stamp; exceeding `dead_after_ms` withdraws the signal so the
    # external dead-man pages.
    emitter = RecordingEmitter(None)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1, dead_after_ms=10_000)
    beat.note_tick_completed(clock.monotonic())
    clock.advance(1_000)
    assert beat.beat(clock.now_ms()) is not None

    clock.advance(10_001)
    assert beat.beat(clock.now_ms()) is None
    assert health.state == "unhealthy"
    assert len(emitter.payloads) == 1


def test_the_dead_after_bound_is_inclusive_of_the_last_good_tick(clock):
    emitter = RecordingEmitter(None)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1, dead_after_ms=10_000)
    beat.note_tick_completed(clock.monotonic())
    clock.advance(10_000)
    assert beat.beat(clock.now_ms()) is not None
    assert health.state == "ready"


def test_a_completed_tick_restarts_the_dead_man_countdown(clock):
    emitter = RecordingEmitter(None)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1, dead_after_ms=10_000)
    for _ in range(5):
        clock.advance(9_000)
        beat.note_tick_completed(clock.monotonic())
        assert beat.beat(clock.now_ms()) is not None
    assert health.state == "ready"
    assert len(emitter.payloads) == 5


def test_the_countdown_starts_at_construction_not_at_the_first_tick(clock):
    # Before the first tick there is no stamp; treating that as "dead"
    # would kill every process during startup reconciliation.
    emitter = RecordingEmitter(None)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1, dead_after_ms=10_000)
    clock.advance(5_000)
    assert beat.beat(clock.now_ms()) is not None


def test_an_emitter_failure_is_swallowed_and_never_blocks(clock):
    good = RecordingEmitter(None)
    bad = RecordingEmitter(None, error=OSError("no route to host"))
    beat = a_heartbeat({"bad": bad, "good": good}, clock, ready_health(clock))
    payload = beat.beat(NOW_MS)
    assert payload is not None
    assert len(good.payloads) == 1


def test_the_sequence_advances_even_when_an_emitter_fails(clock):
    bad = RecordingEmitter(None, error=OSError("down"))
    health = ready_health(clock)
    beat = a_heartbeat({"bad": bad}, clock, health, every_s=1)
    for index in range(3):
        at = NOW_MS + index * 1_000
        clock.set(at)
        beat.beat(at)
    assert beat.sequence == 3


def test_construction_starts_no_worker(clock):
    before = threading.active_count()
    a_heartbeat({"file": RecordingEmitter(None)}, clock, ready_health(clock))
    assert threading.active_count() == before


# --------------------------------------------------------------------------
# emitters
# --------------------------------------------------------------------------


def test_the_emitter_registry_carries_exactly_the_three_kinds():
    assert HEARTBEAT_KINDS.family == "heartbeat_emitter"
    assert HEARTBEAT_KINDS.kinds() == ("file", "systemd", "url")
    for name in HEARTBEAT_KINDS.kinds():
        assert issubclass(HEARTBEAT_KINDS.resolve(name), HeartbeatEmitter)


def test_heartbeat_emitter_is_abstract_and_emit_is_the_hook():
    with pytest.raises(TypeError):
        HeartbeatEmitter(None)

    class NoEmit(HeartbeatEmitter):
        pass

    with pytest.raises(TypeError):
        NoEmit(None)


def test_the_file_emitter_rewrites_heartbeat_json_atomically(serve, clock):
    emitter = FileEmitter(None, path=serve.heartbeat_path)
    health = ready_health(clock)
    beat = a_heartbeat({"file": emitter}, clock, health, every_s=1)
    beat.beat(NOW_MS)
    with open(serve.heartbeat_path, encoding="utf-8") as fh:
        first = json.load(fh)
    assert tuple(sorted(first)) == tuple(sorted(PAYLOAD_FIELDS))
    assert first["sequence"] == 1

    clock.advance(1_000)
    beat.beat(clock.now_ms())
    with open(serve.heartbeat_path, encoding="utf-8") as fh:
        second = json.load(fh)
    assert second["sequence"] == 2
    # A rewrite, not an append: one JSON object, and no temp file left over.
    leftovers = [n for n in os.listdir(serve.series_path) if "tmp" in n or n.startswith(".")]
    assert leftovers == []


def test_the_file_emitter_writes_where_serve_root_says(serve):
    FileEmitter(None, path=serve.heartbeat_path).emit(
        HeartbeatPayload(process_id=PROCESS, sequence=7, at_ms=NOW_MS, status="ready")
    )
    assert os.path.basename(serve.heartbeat_path) == "heartbeat.json"
    with open(serve.heartbeat_path, encoding="utf-8") as fh:
        assert json.load(fh)["sequence"] == 7


def test_the_url_emitter_posts_the_payload_within_its_deadline():
    transport = FakeTransport()
    emitter = UrlEmitter(
        {"url_env": URL_ENV, "timeout_s": 2}, transport=transport, secrets=Env()
    )
    emitter.emit(HeartbeatPayload(process_id=PROCESS, sequence=3, at_ms=NOW_MS, status="ready"))
    (call,) = transport.calls
    assert call["method"] == "POST"
    assert call["url"] == BEAT_URL
    assert call["timeout"] == {"connect_s": 2, "read_s": 2}
    assert json.loads(call["body"].decode("utf-8"))["sequence"] == 3


def test_the_url_emitter_refuses_a_url_env_it_cannot_resolve():
    with pytest.raises(ProductionError) as exc:
        UrlEmitter({"url_env": URL_ENV}, transport=FakeTransport(), secrets=Env({}))
    assert URL_ENV in str(exc.value)


def test_the_url_emitter_never_reads_the_url_at_construction():
    secrets = Env()
    emitter = UrlEmitter({"url_env": URL_ENV}, transport=FakeTransport(), secrets=secrets)
    assert secrets.reads == []
    emitter.emit(HeartbeatPayload(process_id=PROCESS, sequence=1, at_ms=NOW_MS, status="ready"))
    assert secrets.reads == [URL_ENV]


def test_the_url_emitter_refuses_an_unknown_param():
    with pytest.raises(ProductionError) as exc:
        UrlEmitter({"url_env": URL_ENV, "invented_knob": 1}, transport=FakeTransport(), secrets=Env())
    assert "invented_knob" in str(exc.value)


@pytest.mark.parametrize("status", [200, 202, 204])
def test_a_2xx_answer_is_a_successful_emission(status):
    transport = FakeTransport(answer=(status, {}, b""))
    emitter = UrlEmitter({"url_env": URL_ENV}, transport=transport, secrets=Env())
    emitter.emit(HeartbeatPayload(process_id=PROCESS, sequence=1, at_ms=NOW_MS, status="ready"))
    assert len(transport.calls) == 1


def test_a_non_2xx_answer_is_counted_and_never_blocks(clock):
    # §5.11: "2xx is success, any other result counts a failure and never
    # blocks". The count is what an operator sees; the beat goes on.
    transport = FakeTransport(answer=(500, {}, b""))
    emitter = UrlEmitter({"url_env": URL_ENV}, transport=transport, secrets=Env())
    beat = a_heartbeat({"url": emitter}, clock, ready_health(clock), every_s=1)
    for index in range(3):
        at = NOW_MS + index * 1_000
        clock.set(at)
        assert beat.beat(at) is not None
    assert len(transport.calls) == 3


def test_a_transport_raise_never_reaches_the_beat(clock):
    transport = FakeTransport(error=OSError("unreachable"))
    emitter = UrlEmitter({"url_env": URL_ENV}, transport=transport, secrets=Env())
    beat = a_heartbeat({"url": emitter}, clock, ready_health(clock))
    assert beat.beat(NOW_MS) is not None


def test_the_url_emitter_body_carries_no_credential():
    register_secret("beat-secret")
    transport = FakeTransport()
    emitter = UrlEmitter({"url_env": URL_ENV}, transport=transport, secrets=Env())
    emitter.emit(
        HeartbeatPayload(process_id="proc-beat-secret", sequence=1, at_ms=NOW_MS, status="ready")
    )
    text = transport.calls[0]["body"].decode("utf-8")
    assert "beat-secret" not in text
    assert BEAT_URL not in text


# --------------------------------------------------------------------------
# the single-instance lock
# --------------------------------------------------------------------------

#: What a second process must do when the lock is already held: refuse,
#: with `ProductionError`, and print the marker below so the parent can
#: tell a refusal apart from a crash.
SECOND_INSTANCE = """
import sys
from dskit.production.base import ProductionError
from dskit.production.health import InstanceLock

lock = InstanceLock({path!r})
try:
    lock.acquire()
except ProductionError as exc:
    print("REFUSED")
    sys.exit(0)
print("ACQUIRED")
sys.exit(1)
"""


def _child(code):
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )


def test_the_instance_lock_is_taken_and_released(tmp_path):
    lock = InstanceLock(str(tmp_path / "serve.lock"))
    assert lock.held is False
    lock.acquire()
    assert lock.held is True
    lock.release()
    assert lock.held is False


def test_the_instance_lock_release_is_idempotent(tmp_path):
    lock = InstanceLock(str(tmp_path / "serve.lock"))
    lock.acquire()
    lock.release()
    lock.release()


def test_the_instance_lock_is_a_context_manager(tmp_path):
    path = str(tmp_path / "serve.lock")
    with InstanceLock(path) as lock:
        assert lock.held is True
    assert lock.held is False
    again = InstanceLock(path)
    again.acquire()
    again.release()


def test_a_second_process_cannot_take_the_same_lock(tmp_path):
    # §5.11's whole claim: `flock(LOCK_EX | LOCK_NB)` prevents a SECOND
    # PROCESS, which only a second process can prove.
    path = str(tmp_path / "serve.lock")
    lock = InstanceLock(path)
    lock.acquire()
    try:
        result = _child(SECOND_INSTANCE.format(path=path))
    finally:
        lock.release()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REFUSED" in result.stdout


def test_the_lock_is_free_again_once_the_holder_releases_it(tmp_path):
    path = str(tmp_path / "serve.lock")
    first = InstanceLock(path)
    first.acquire()
    first.release()
    result = _child(SECOND_INSTANCE.format(path=path))
    assert "ACQUIRED" in result.stdout


def test_the_lock_refuses_a_path_it_cannot_create(tmp_path):
    lock = InstanceLock(str(tmp_path / "missing-dir" / "serve.lock"))
    with pytest.raises(ProductionError):
        lock.acquire()


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------


@pytest.fixture
def guarded_signals():
    """Absorb SIGTERM/SIGINT for the test, then put the real handlers back.

    Without this, a `SignalHandler` that fails to install would not FAIL the
    test — it would deliver the default disposition and kill the test
    session, which is the one outcome a red phase must never produce.
    """
    absorbed = []

    def absorb(signum, frame):
        absorbed.append(signum)

    previous = {sig: signal.signal(sig, absorb) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield absorbed
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_sets_the_stop_flag(guarded_signals, signum):
    # The handler sets a flag and returns: it runs on whatever stack the
    # signal interrupted, so it must not do shutdown work there (D23).
    stop = threading.Event()
    handler = SignalHandler(stop)
    handler.install()
    try:
        assert stop.is_set() is False
        signal.raise_signal(signum)
        assert stop.is_set() is True
        assert guarded_signals == []
    finally:
        handler.restore()


def test_restoring_puts_the_previous_handlers_back(guarded_signals):
    handler = SignalHandler(threading.Event())
    handler.install()
    handler.restore()
    signal.raise_signal(signal.SIGTERM)
    assert guarded_signals == [signal.SIGTERM]


def test_the_signal_handler_is_a_context_manager(guarded_signals):
    stop = threading.Event()
    original = signal.getsignal(signal.SIGTERM)
    with SignalHandler(stop):
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set() is True
    assert signal.getsignal(signal.SIGTERM) is original


def test_a_second_signal_is_harmless(guarded_signals):
    stop = threading.Event()
    with SignalHandler(stop):
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)
    assert stop.is_set() is True


def test_a_waiting_process_notices_a_signal_within_one_second():
    # §5.11: "signals wake within 1 s". The mechanism is `WallClock`'s
    # sleep slicing, whose bound is a named constant — pinned here so a
    # change to it is a change to this promise.
    assert MAX_SLEEP_SLICE_S <= 1.0


# --------------------------------------------------------------------------
# §5.11.2 — the lifecycle hooks and the systemd emitter
# --------------------------------------------------------------------------


class Listener:
    """A real `AF_UNIX` `SOCK_DGRAM` socket, bound where the test says.

    No network: an abstract or filesystem unix socket is a local kernel
    object, which is what makes the `@`-to-NUL translation testable at all
    rather than by reading the implementation back to itself.
    """

    def __init__(self, address):
        self.address = address
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.bind(address)
        self.socket.settimeout(2.0)

    def datagrams(self, expected=1):
        """Read `expected` datagrams as text, failing the test on a timeout."""
        return [self.socket.recv(4096).decode("utf-8") for _ in range(expected)]

    def close(self):
        self.socket.close()


@pytest.fixture
def abstract_socket():
    """A listener on a Linux abstract socket, named the way systemd does."""
    address = "\0dskit-production-test-" + uuid.uuid4().hex
    listener = Listener(address)
    yield listener, "@" + address[1:]
    listener.close()


@pytest.fixture
def path_socket(tmp_path):
    """A listener on a filesystem socket path."""
    address = str(tmp_path / "notify.sock")
    listener = Listener(address)
    yield listener, address
    listener.close()


def systemd(notify_socket, params=None):
    """A `SystemdEmitter` whose `NOTIFY_SOCKET` the test dictates."""
    values = {} if notify_socket is None else {"NOTIFY_SOCKET": notify_socket}
    return SystemdEmitter(params, secrets=Secrets(values))


def test_the_two_lifecycle_hooks_are_concrete_and_do_nothing_by_default():
    """§5.11.2: "two CONCRETE lifecycle hooks that do nothing by default",
    so no existing subclass changes — an ABC hook would have broken every
    child emitter written against phase 1."""

    class Bare(HeartbeatEmitter):
        def emit(self, payload):
            pass

    emitter = Bare(None)
    assert emitter.ready() is None
    assert emitter.stopping() is None
    assert "ready" not in HeartbeatEmitter.__abstractmethods__
    assert "stopping" not in HeartbeatEmitter.__abstractmethods__


def test_the_file_and_url_emitters_ignore_both_hooks(serve, clock):
    """The two phase-1 emitters have no lifecycle to report, and calling a
    hook on one must not write, POST or raise."""
    emitter = FileEmitter(None, path=serve.heartbeat_path)
    assert emitter.ready() is None and emitter.stopping() is None
    assert not os.path.exists(serve.heartbeat_path)
    transport = FakeTransport()
    url = UrlEmitter({"url_env": URL_ENV}, transport=transport, secrets=Env())
    assert url.ready() is None and url.stopping() is None
    assert transport.calls == []


def test_the_systemd_emitter_refuses_an_unset_notify_socket():
    """§5.11.2: "a heartbeat that silently emits nothing is precisely the
    failure D18 exists to prevent, and systemd's own watchdog would then be
    the thing that never fires"."""
    with pytest.raises(ProductionError) as excinfo:
        systemd(None)
    assert "NOTIFY_SOCKET" in str(excinfo.value)
    with pytest.raises(ProductionError):
        systemd("")


def test_the_systemd_emitter_refuses_an_address_systemd_could_never_use():
    """sd_notify's own rule: an absolute path or an abstract `@` name. A
    relative path can never be connected to, so refusing it at
    construction is the same refusal as refusing an unset variable."""
    with pytest.raises(ProductionError) as excinfo:
        systemd("notify.sock")
    assert "notify.sock" in str(excinfo.value)


def test_the_systemd_emitter_needs_the_secrets_collaborator():
    with pytest.raises(ProductionError):
        SystemdEmitter(None)


def test_the_systemd_emitter_is_default_deny_over_its_params():
    with pytest.raises(ProductionError):
        systemd("/run/systemd/notify", params={"nonsuch": 1})


def test_construction_opens_no_socket(path_socket):
    """Construction validates configuration only (§5.11): a socket opened
    at construction would make an emitter's health a constructor side
    effect."""
    listener, address = path_socket
    systemd(address)
    listener.socket.settimeout(0.05)
    with pytest.raises(OSError):
        listener.socket.recv(4096)


def test_emit_sends_watchdog_and_a_one_line_status(path_socket):
    listener, address = path_socket
    emitter = systemd(address)
    emitter.emit(HeartbeatPayload(process_id="proc-a", sequence=3, at_ms=NOW_MS,
                                  status="ready"))
    (datagram,) = listener.datagrams()
    lines = datagram.split("\n")
    assert lines[0] == "WATCHDOG=1"
    assert len(lines) == 2 and lines[1].startswith("STATUS=")
    for field in ("proc-a", "3", "ready", str(NOW_MS)):
        assert field in lines[1]


def test_a_leading_at_names_an_abstract_socket_and_becomes_a_nul(abstract_socket):
    """§5.11.2's one piece of protocol detail, and the reason it is stated:
    an emitter that sent to the literal `@name` would reach nothing, and
    nothing would say so."""
    listener, notify_socket = abstract_socket
    assert notify_socket.startswith("@")
    systemd(notify_socket).emit(
        HeartbeatPayload(process_id="proc-a", sequence=1, at_ms=NOW_MS, status="ready")
    )
    (datagram,) = listener.datagrams()
    assert datagram.startswith("WATCHDOG=1")


def test_the_status_line_never_carries_a_newline(path_socket):
    """A datagram is KEY=value lines, so a status carrying a newline would
    forge a second assignment systemd would then act on."""
    listener, address = path_socket
    systemd(address).emit(
        HeartbeatPayload(process_id="proc\na", sequence=1, at_ms=NOW_MS, status="ready")
    )
    (datagram,) = listener.datagrams()
    assert len(datagram.split("\n")) == 2


def test_ready_and_stopping_send_the_two_systemd_notifications(path_socket):
    listener, address = path_socket
    emitter = systemd(address)
    emitter.ready()
    emitter.stopping()
    assert listener.datagrams(2) == ["READY=1", "STOPPING=1"]


def test_a_send_failure_never_raises_an_os_error_and_is_counted(tmp_path, clock):
    """§5.11.2: "`emit` never raises; a send failure is counted like any
    other emitter's". `Heartbeat.failures` is the only counter there is,
    and it counts an emitter that RAISES — so the failure arrives as the
    `ProductionError` every emitter reports a failure with, never as the
    bare `OSError` the socket produced."""
    emitter = systemd(str(tmp_path / "nothing-listens.sock"))
    payload = HeartbeatPayload(process_id="proc-a", sequence=1, at_ms=NOW_MS, status="ready")
    with pytest.raises(ProductionError):
        emitter.emit(payload)
    beat = a_heartbeat({"sd": emitter}, clock, ready_health(clock))
    assert beat.beat(NOW_MS) is not None
    assert beat.failures == 1


def test_neither_lifecycle_hook_raises_when_the_socket_is_gone(tmp_path):
    """The loop calls these two OUTSIDE the heartbeat's own swallow, so a
    raise here would fault a serve that is merely shutting down."""
    emitter = systemd(str(tmp_path / "nothing-listens.sock"))
    assert emitter.ready() is None
    assert emitter.stopping() is None


# -- the heartbeat's own fan-out -------------------------------------------


def test_the_heartbeat_reports_ready_once_however_often_it_is_told(clock):
    """The loop asks at every tick whose health is ready; the heartbeat
    owns "first", so the loop keeps no state of its own."""
    emitter = LifecycleEmitter(None)
    beat = a_heartbeat({"sd": emitter}, clock, ready_health(clock))
    beat.ready()
    beat.ready()
    assert emitter.lifecycle == ["ready"]


def test_the_heartbeat_reports_stopping_once(clock):
    emitter = LifecycleEmitter(None)
    beat = a_heartbeat({"sd": emitter}, clock, ready_health(clock))
    beat.stopping()
    beat.stopping()
    assert emitter.lifecycle == ["stopping"]


def test_the_heartbeat_fans_a_lifecycle_verb_out_to_every_emitter(clock):
    emitters = {"a": LifecycleEmitter(None), "b": LifecycleEmitter(None)}
    beat = a_heartbeat(emitters, clock, ready_health(clock))
    beat.ready()
    assert [e.lifecycle for e in emitters.values()] == [["ready"], ["ready"]]


def test_a_failing_lifecycle_hook_is_counted_and_never_raised(clock):
    """Same rule as `emit`: the loop calls these, and a stop that raised
    would leave the process without its `process` stop record."""
    failing = LifecycleEmitter(None, error=RuntimeError("no socket"))
    healthy = LifecycleEmitter(None)
    beat = a_heartbeat({"bad": failing, "good": healthy}, clock, ready_health(clock))
    beat.ready()
    assert beat.failures == 1
    assert healthy.lifecycle == ["ready"]

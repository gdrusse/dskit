"""Coordinator (children/index_options/tools/mio_parallel_queue.py) with fake and real hosts (ADR-0297).

Scheduling uses fake hosts, a fake probe and a fake document launcher. The real
LocalHost runs a stub worker as a subprocess, the real SshHost talks to a local
shell through a fake ``ssh`` script, and the real DocumentRunner launches a stub
pipeline. Every path is a tmp_path; nothing here touches a real quarter.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from index_options.ledger_studies import MIOPacketStudy  # noqa: E402
from index_options.mio_worker import stamp  # noqa: E402
from mio_parallel_queue import (Coordinator, DocumentRunner, Host, LocalHost, PidLock, ResourceProbe,  # noqa: E402
                                Similarity, SshHost, UnitQueue, main, recorded_from_report, _load_plan)
from test_mio_packets import fixed_world, study  # noqa: E402,F401  (the synthetic packet fixture)

BUNDLE, PLAN = "b" * 64, "p" * 64
import mio_parallel_queue as _queue_module  # noqa: E402

#: The compute skill's real directory: the coordinator pins it (a config may not name another), tests patch the pin.
REAL_SKILL = _queue_module.SKILL_SCRIPTS


@pytest.fixture(autouse=True)
def _no_real_skill(monkeypatch, tmp_path):
    """The skill directory is pinned in the coordinator; point the pin at an empty directory so no test reads the real one."""
    monkeypatch.setattr(_queue_module, "SKILL_SCRIPTS", str(tmp_path / "no-skill"))


@pytest.fixture(autouse=True)
def _keep_umask():
    """``main`` sets umask 077 for its children; a test must not leave it set for the next one."""
    old = os.umask(0o022)
    os.umask(old)
    yield
    os.umask(old)


def _plan(**arm_over):
    return {"quarters": [
        {"id": "2022q1", "asof": "2026-01-01", "arms": [
            {"id": "rules", "methods": ["m1", "m2"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {"run": "r"}, **arm_over},
            {"id": "control", "methods": ["c1"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {"run": "c"}}]},
        {"id": "2022q2", "asof": "2026-01-01", "arms": [
            {"id": "rules", "methods": ["m1"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {"run": "r2"}}]}]}


def _cert(objective=10., termination="optimal"):
    return {"solver": "s", "status": "ok", "termination": termination, "objective": objective, "bound": objective,
            "gap": 0., "seconds": 1., "variables": 1, "constraints": 1}


def _body(method, attempt, objectives=None, terminations=None, **over):
    weeks = ["2022-01-03", "2022-01-10"]
    decisions = {w: {"actions": None, "certificates": [_cert((objectives or {}).get(w, 10.), (terminations or {}).get(w, "optimal"))]}
                 for w in weeks}
    body = {"format": MIOPacketStudy.RECORDED_FORMAT, "method": method, "attempt": attempt,
            "host": {"label": "fake"}, "bundle_manifest_sha256": BUNDLE, "plan_sha256": PLAN,
            "study_sha256": "s" * 64, "portable_inputs_sha256": "i" * 64, "dependencies": {},
            "actions": {"method": method, "weeks": weeks, "decisions": decisions},
            "result_sha256": "r" * 64, "seconds": 1.0, "timeouts": 0}
    body.update(over)
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return raw, hashlib.sha256(raw).hexdigest() + f"  {method}.json\n"


class FakeHost(Host):
    """A scripted host: spawn records, results and liveness are set by the test."""

    def __init__(self, name, slots, online=True, ram=100.):
        super().__init__(name, slots)
        self.probe_limited = name == "wsl"
        self.is_online, self.ram = online, ram
        self.spawned, self.killed, self.staged = [], [], []
        self.results, self.dead, self.progress_stamp, self.hold = {}, set(), {}, {}
        self.next_pid = 100

    def online(self):
        return self.is_online

    def available_gb(self):
        return self.ram

    def stage(self, unit):
        self.staged.append(unit["unit_id"])

    def spawn(self, unit, attempt):
        self.next_pid += 1
        self.spawned.append((unit["unit_id"], attempt, self.next_pid))
        return {"pid": self.next_pid, "pid_start": "t0"}

    def alive(self, unit, attempt, handle):
        return (unit["unit_id"], attempt) not in self.dead

    def progress(self, unit, attempt):
        return self.progress_stamp.get((unit["unit_id"], attempt))

    def fetch(self, unit, attempt):
        return self.results.get((unit["unit_id"], attempt))

    def kill(self, unit, attempt, handle):
        self.killed.append((unit["unit_id"], attempt))
        self.dead.add((unit["unit_id"], attempt))

    def finish(self, unit_id, attempt, **over):
        method = unit_id.rsplit("/", 1)[1].split("@")[0]
        self.results[(unit_id, attempt)] = _body(method, attempt, **over)


class Clock:
    def __init__(self, start=1_700_000_000.):
        self.now = start

    def __call__(self):
        return self.now


class Documents:
    def __init__(self, recorded_from=None, gate=None):
        self.calls, self.recorded_from, self.gate = [], recorded_from, gate

    def __call__(self, spec, quarter, arm, recorded_dir, asof):
        if self.gate is not None:
            self.gate.wait(10)
        files = sorted(p.stem for p in Path(recorded_dir).glob("*.json")) if recorded_dir and Path(recorded_dir).exists() else []
        self.calls.append((quarter, arm, recorded_dir is not None, files))
        return {"ok": True, "complete": True, "run_dir": f"/runs/{quarter}-{arm}",
                "recorded_from": files if self.recorded_from is None else self.recorded_from}


def _probe(wsl=30., windows=40., c=20.):
    return lambda: {"wsl_available_gb": wsl, "windows_available_gb": windows, "c_free_gb": c}


def _passed_parity(root, plan, unit):
    """A parity verdict that passed for this unit, thresholds and bundle (what a finished first-parity run leaves)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "parity.json").write_text(json.dumps({
        "passed": True, "reason": "ok", "unit": unit, "thresholds_sha256": Similarity.thresholds_sha256(),
        "bundle_sha256": BUNDLE, "hosts": {"first": "mac", "second": "wsl"}, "sha256": {"first": None, "second": None}}))


def _coordinator(tmp_path, hosts, documents=None, probe=None, limits=None, clock=None, plan=None, **kw):
    """A Mac host needs a parity unit; unless the test is about parity, one has already passed."""
    plan = plan or _plan()
    if any(h.name == "mac" for h in hosts) and "parity" not in kw:
        arm, method = plan["quarters"][0]["arms"][0], plan["quarters"][0]["arms"][0]["methods"][0]
        kw["parity"] = {"unit": f"{plan['quarters'][0]['id']}/{arm['id']}/{method}"}
        _passed_parity(tmp_path / "q", plan, kw["parity"]["unit"])
    c = Coordinator(tmp_path / "q", plan, hosts, documents or Documents(), probe or _probe(),
                    {"unit_ram_gb": 8, "document_ram_gb": 8, "stale_seconds": 1800, **(limits or {})},
                    clock or Clock(), **kw)
    if c.parity_unit and c.parity_state() == "passed" and c.queue.unit(c.twin_unit)["status"] == "pending":
        c.queue.lease(c.twin_unit, "wsl")
        c.queue.transition(c.twin_unit, "verified", 1)
    return c


def _tick(c):
    """One pass, then wait for any document thread and settle it."""
    c.tick()
    c.join_documents()
    c.write_status()
    return not all(a["status"] in ("closed", "failed") for a in c.queue.arms())


def test_dependency_ordering_documents_wait_for_every_unit(tmp_path):
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 2)
    docs = Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs)
    assert _tick(c)
    leased = {u["unit_id"]: u["host"] for u in c.queue.units(status="leased")}
    assert len(leased) == 3 and list(leased.values()).count("mac") == 1 and mac.staged == [next(iter(leased))]
    assert sorted(leased)[0].startswith("2022q1/"), "quarter order first"
    assert docs.calls == [], "no document before its units settle"
    for unit_id, host in leased.items():
        (mac if host == "mac" else wsl).finish(unit_id, 1)
    assert _tick(c)
    done_arms = [(q, a) for q, a, _, _ in docs.calls]
    assert ("2022q1", "control") in done_arms and ("2022q1", "rules") in done_arms
    assert ("2022q2", "rules") not in done_arms
    for call in docs.calls:
        if call[:2] == ("2022q1", "rules"):
            assert call[3] == ["m1", "m2"], "the document sees every verified method"
    assert all(u["status"] == "replayed" for u in c.queue.units(quarter="2022q1") if u["role"] == "primary")
    recorded = sorted(p.name for p in (tmp_path / "q" / "recorded" / "2022q1-rules").iterdir())
    assert recorded == ["m1.json", "m1.json.sha256", "m2.json", "m2.json.sha256"], "no temp file left behind"
    q2 = c.queue.units(quarter="2022q2")
    assert len(q2) == 1 and q2[0]["status"] == "leased"
    (mac if q2[0]["host"] == "mac" else wsl).finish(q2[0]["unit_id"], 1)
    assert not _tick(c), "everything closed"
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert set(status) == {"updated_at", "units", "arms", "hosts", "moving", "waiting", "parity", "parity_reason",
                           "skipped", "orphans", "no_worker_units", "rejections", "fallbacks", "sealed", "sealed_counts"}
    assert all(set(u) == {"unit_id", "status", "attempt", "host", "result_sha256", "started", "last_progress"}
               for u in status["units"]), "states and hashes only, never method economics"


def test_double_claim_is_refused_and_a_restart_adopts_live_leases(tmp_path):
    queue = UnitQueue(tmp_path / "queue.sqlite")
    queue.add("q/a/m", "q", "a", "m")
    assert queue.lease("q/a/m", "wsl", pid=1) == 1
    with pytest.raises(ValueError, match="double claim"):
        queue.lease("q/a/m", "mac")
    with pytest.raises(ValueError, match="stale"):
        queue.transition("q/a/m", "returned", 2)
    queue.transition("q/a/m", "verified", 1, result_sha256="x")
    with pytest.raises(ValueError, match="already verified"):
        queue.transition("q/a/m", "pending", 1)
    with pytest.raises(ValueError, match="unknown unit status"):
        queue.transition("q/a/m", "bogus", 1)
    wsl = FakeHost("wsl", 4)
    c = _coordinator(tmp_path, [wsl], probe=_probe(wsl=60.))
    c.tick()
    first = {u["unit_id"]: (u["attempt"], u["pid"]) for u in c.queue.units(status="leased")}
    assert len(first) >= 1
    again = _coordinator(tmp_path, [wsl], probe=_probe(wsl=60.))  # a coordinator restart on the same queue root
    again.tick()
    after = {u["unit_id"]: (u["attempt"], u["pid"]) for u in again.queue.units(status="leased") if u["unit_id"] in first}
    assert after == first, "live leases are adopted, never re-leased"
    assert len(wsl.spawned) == len({s[0] for s in wsl.spawned})


def test_killed_worker_is_requeued_and_resumes_with_a_new_attempt(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl])
    c.tick()
    unit = c.queue.units(status="leased")[0]
    wsl.dead.add((unit["unit_id"], 1))
    c.tick()
    rows = {u["unit_id"]: u for u in c.queue.units()}
    assert rows[unit["unit_id"]]["attempt"] == 2 and rows[unit["unit_id"]]["status"] == "leased"
    assert [h["event"] for h in rows[unit["unit_id"]]["history"]] == ["leased", "pending", "leased"]
    assert "exited without a result" in rows[unit["unit_id"]]["history"][1]["note"]
    wsl.finish(unit["unit_id"], 2)
    c.tick()
    assert c.queue.unit(unit["unit_id"])["status"] in ("verified", "replayed")


def test_a_worker_that_finishes_and_exits_between_fetch_and_alive_is_not_requeued(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl])
    c.tick()
    unit = c.queue.units(status="leased")[0]
    calls = []

    def racing_fetch(u, attempt):
        calls.append(1)
        return None if len(calls) == 1 else _body(u["method"], attempt)  # the file appears after the first look

    wsl.fetch = racing_fetch
    wsl.dead.add((unit["unit_id"], 1))
    c.tick()
    row = c.queue.unit(unit["unit_id"])
    assert row["status"] in ("verified", "replayed") and row["attempt"] == 1, row["history"]


def test_offline_host_falls_back_to_wsl_and_stale_leases_requeue(tmp_path):
    mac, wsl = FakeHost("mac", 2, online=False), FakeHost("wsl", 1)
    clock = Clock()
    c = _coordinator(tmp_path, [mac, wsl], clock=clock)
    c.tick()
    assert all(u["host"] == "wsl" for u in c.queue.units(status="leased")) and not mac.spawned
    mac.is_online = True
    c.tick()
    mac_units = [u for u in c.queue.units(status="leased") if u["host"] == "mac"]
    assert len(mac_units) == 2
    mac.is_online = False  # the Mac drops mid-unit: nothing happens until the stale limit
    clock.now += 100
    c.tick()
    assert all(c.queue.unit(u["unit_id"])["status"] == "leased" for u in mac_units)
    clock.now += 1801
    c.tick()
    for u in mac_units:
        row = c.queue.unit(u["unit_id"])
        assert row["status"] in ("pending", "leased") and "offline past stale" in row["history"][1]["note"]
        assert row["host"] != "mac" or row["status"] == "pending"


def test_progress_is_judged_by_change_so_real_stamps_odd_stamps_and_skew_never_kill_a_live_worker(tmp_path):
    """The Critical defect: a stamp the coordinator could not parse read as epoch 0 and killed every unit."""
    mac = FakeHost("mac", 1)
    clock = Clock()
    c = _coordinator(tmp_path, [mac], clock=clock, plan={"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]})
    c.tick()
    key = ("q/a/m", 1)
    for token in (stamp(clock.now),                       # the worker's real format
                  "2022-01-03T10:00:00",                  # the old local-time format, no Z
                  "garbage",                              # unparseable
                  stamp(clock.now - 86400 * 30)):         # a Mac clock a month behind
        clock.now += 500
        mac.progress_stamp[key] = token
        c.tick()
        assert c.queue.unit("q/a/m")["status"] == "leased" and not mac.killed, token
    clock.now += 1801  # the same token for longer than the stale limit: that is a stalled worker
    c.tick()
    assert mac.killed == [key] and c.queue.unit("q/a/m")["attempt"] == 2


def test_unparseable_stored_stamp_never_kills(tmp_path):
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)])
    assert c._epoch("not a stamp") is None and c._epoch(None) is None
    assert c._age("not a stamp") == 0
    assert c._epoch(stamp(1_700_000_000.)) == 1_700_000_000


@pytest.mark.parametrize("kind", ["truncated", "sidecar", "empty_sidecar", "whitespace_sidecar", "attempt", "bundle",
                                  "plan", "method", "format", "weeks", "host_list", "host_missing", "seconds", "timeouts"])
def test_partial_or_corrupt_return_is_refused_and_requeued(tmp_path, kind):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], limits={"max_attempts": {"wsl": 2}})
    c.tick()
    unit = c.queue.units(status="leased")[0]
    method = unit["method"]
    raw, side = _body(method, 1)
    if kind == "truncated":
        raw = raw[:-20]
        side = hashlib.sha256(raw).hexdigest() + "  x\n"
    elif kind == "sidecar":
        side = "0" * 64 + "  x\n"
    elif kind == "empty_sidecar":
        side = ""
    elif kind == "whitespace_sidecar":
        side = "  \n"
    elif kind == "attempt":
        raw, side = _body(method, 2)
    elif kind == "bundle":
        raw, side = _body(method, 1, bundle_manifest_sha256="z" * 64)
    elif kind == "plan":
        raw, side = _body(method, 1, plan_sha256="z" * 64)
    elif kind == "method":
        raw, side = _body("other", 1)
    elif kind == "format":
        raw, side = _body(method, 1, format="mio-method-actions-v0")
    elif kind == "weeks":
        raw, side = _body(method, 1, actions={"method": method, "weeks": [], "decisions": {}})
    elif kind == "host_list":
        raw, side = _body(method, 1, host=["not", "a", "dict"])
    elif kind == "host_missing":
        raw, side = _body(method, 1, host=None)
    elif kind == "seconds":
        raw, side = _body(method, 1, seconds="fast")
    elif kind == "timeouts":
        raw, side = _body(method, 1, timeouts=1.5)
    wsl.results[(unit["unit_id"], 1)] = (raw, side)
    c.tick()
    row = c.queue.unit(unit["unit_id"])
    assert row["attempt"] == 2 and row["status"] == "leased", row["history"]
    assert any(h["event"] == "pending" and h["note"].startswith("rejected:") for h in row["history"])
    assert not (tmp_path / "q" / "recorded").exists() or not list((tmp_path / "q" / "recorded").rglob("*.json")), \
        "nothing reaches recorded/ before every gate passes"
    aside = list((tmp_path / "q" / "rejected").glob("*.json"))
    assert len(aside) == 1 and aside[0].read_bytes() == raw
    wsl.results[(unit["unit_id"], 2)] = (raw, "0" * 64 + "  x\n")  # a second bad return
    _tick(c)
    row = c.queue.unit(unit["unit_id"])
    events = [h["event"] for h in row["history"]]
    assert events[:4] == ["leased", "pending", "leased", "rejected"], events
    # attempts exhausted: terminal; the arm's document then solved the method itself
    assert row["status"] == "solved_local" and c.queue.arm(row["quarter"], row["arm"])["status"] == "closed"


def test_attempts_exhausted_on_every_host_rejects_a_pending_unit_instead_of_leaving_it(tmp_path):
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 1)
    docs = Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs, limits={"max_attempts": {"mac": 1, "wsl": 1}}, plan={"quarters": [
        {"id": "q", "asof": "2026-01-01", "arms": [
            {"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]})
    c.tick()
    assert c.queue.unit("q/a/m")["host"] == "mac"
    mac.dead.add(("q/a/m", 1))
    c.tick()  # requeued, leased to wsl
    assert c.queue.unit("q/a/m")["host"] == "wsl"
    wsl.dead.add(("q/a/m", 2))
    _tick(c)  # requeued, no host left: rejected, then the document solves it locally
    row = c.queue.unit("q/a/m")
    assert row["status"] == "solved_local", row["history"]
    assert any(h["event"] == "rejected" and "no usable host" in h["note"] for h in row["history"])
    assert docs.calls and docs.calls[0][3] == []


def test_late_duplicate_result_and_an_existing_final_file_are_superseded_never_counted(tmp_path):
    wsl = FakeHost("wsl", 1)
    clock = Clock()
    c = _coordinator(tmp_path, [wsl], clock=clock)
    c.tick()
    unit = c.queue.units(status="leased")[0]
    clock.now += 1801
    c.tick()  # attempt 1 stale -> killed -> attempt 2 leased
    assert c.queue.unit(unit["unit_id"])["attempt"] == 2
    wsl.finish(unit["unit_id"], 2)
    c.tick()
    row = c.queue.unit(unit["unit_id"])
    assert row["status"] in ("verified", "replayed") and row["result_sha256"]
    recorded = c.recorded_dir(unit["quarter"], unit["arm"]) / f"{unit['method']}.json"
    before = recorded.read_bytes()
    late_raw, late_side = _body(unit["method"], 1, seconds=99.)
    assert c._accept(c.queue.unit(unit["unit_id"]), 1, late_raw, late_side) is False
    assert recorded.read_bytes() == before
    assert c.queue.unit(unit["unit_id"])["result_sha256"] == row["result_sha256"]
    aside = list((tmp_path / "q" / "superseded").glob("*.1.json"))
    assert len(aside) == 1 and aside[0].read_bytes() == late_raw


def test_publish_is_exclusive_atomic_and_a_present_final_file_wins(tmp_path):
    """The exclusive create of recorded/<method>.json: a file already there is never replaced."""
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl])
    c.tick()
    unit = c.queue.units(status="leased")[0]
    target = c.recorded_dir(unit["quarter"], unit["arm"])
    target.mkdir(parents=True)
    (target / f"{unit['method']}.json").write_bytes(b"first writer")
    raw, side = _body(unit["method"], 1)
    assert c._accept(unit, 1, raw, side) is False
    assert (target / f"{unit['method']}.json").read_bytes() == b"first writer"
    row = c.queue.unit(unit["unit_id"])
    assert row["status"] == "superseded"
    assert not list(target.glob("*.part")) and not list(target.glob(".*"))


def test_limits_are_floored_never_lowered_and_unknown_limits_refuse(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], limits={"c_floor_gb": 0.1, "c_job_gb": 0, "unit_ram_gb": 0.5, "document_ram_gb": 1,
                                              "ramp_seconds": 0, "wsl_reserve_gb": 1, "windows_reserve_gb": 0,
                                              "mac_reserve_gb": 0, "stale_seconds": 5})
    assert c.limits["c_floor_gb"] == 3 and c.limits["c_job_gb"] >= 3.4 and c.limits["unit_ram_gb"] >= 4
    assert c.limits["document_ram_gb"] >= 4 and c.limits["ramp_seconds"] >= 120
    assert c.limits["wsl_reserve_gb"] == 6 and c.limits["windows_reserve_gb"] == 2 and c.limits["stale_seconds"] >= 1800
    raised = _coordinator(tmp_path / "r", [wsl], limits={"c_floor_gb": 10, "unit_ram_gb": 12})
    assert raised.limits["c_floor_gb"] == 10 and raised.limits["unit_ram_gb"] == 12, "a config can tighten"
    for bad in ({"deadline_seconds": 100}, {"heartbeat_seconds": 1}):
        with pytest.raises(ValueError, match="unknown limits"):
            _coordinator(tmp_path / "u", [wsl], limits=bad)
    with pytest.raises(ValueError, match="must be a number"):
        _coordinator(tmp_path / "n", [wsl], limits={"c_floor_gb": None})


@pytest.mark.parametrize("name", ["windows_reserve_gb", "wsl_reserve_gb", "mac_reserve_gb", "c_floor_gb", "c_job_gb",
                                  "unit_ram_gb", "document_ram_gb", "stale_seconds", "ramp_seconds", "max_refunds",
                                  "parity_grace_seconds"])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_a_non_finite_limit_is_refused_because_nan_slips_past_a_floor(tmp_path, name, literal):
    """``max(nan, floor)`` is nan: a hand-edited NaN literal used to drop the 2 GB Windows reserve and the stale kill."""
    limits = json.loads('{"%s": %s}' % (name, literal))  # Python's json accepts the bare literals
    with pytest.raises(ValueError, match="finite"):
        _coordinator(tmp_path, [FakeHost("wsl", 1)], limits=limits)


def test_a_nan_reserve_does_not_admit_a_unit_beside_a_starved_windows(tmp_path, monkeypatch):
    """The consequence, through the config path: the reserves the compute skill sets are floors NaN cannot lower."""
    from mio_parallel_queue import _build
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "compute_state.py").write_text("WSL_RESERVE = 6 * 2**30\nWIN_RESERVE = 2 * 2**30\n"
                                            "def windows_available_cached(max_age=900, refresh=True):\n    return None\n")
    base = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(_stub_runtime(tmp_path)),
            "quarters": []}
    monkeypatch.setattr(_queue_module, "SKILL_SCRIPTS", str(skill))
    for name in ("wsl_reserve_gb", "windows_reserve_gb"):
        with pytest.raises(ValueError, match="finite"):
            _build({**base, "limits": {name: float("nan")}})
    ok = Coordinator(tmp_path / "ok", _plan(), [FakeHost("wsl", 4)], Documents(), _probe(wsl=40., windows=.5), {}, Clock())
    assert ok._wsl_admit(8., False)[0] == 0, "Windows at 0.5 GB is below its 2 GB reserve: nothing is admitted"


def test_resource_gates_fail_closed_and_project_the_jobs_own_growth(tmp_path):
    def leased(sub, hosts, **probe):
        c = _coordinator(tmp_path / sub, hosts, probe=_probe(**probe))
        c.tick()
        return [u["host"] for u in c.queue.units(status="leased")], c

    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 4)
    got, c = leased("one", [mac, wsl], c=2.5)
    assert got == ["mac"], "C: below the floor: no WSL unit, the Mac still runs"
    got, _ = leased("two", [FakeHost("wsl", 4)], wsl=23.9)
    assert len(got) == 2, "(23.9 - 6 reserve) // 8 = 2 slots: no document is due, so no document room is held back"
    got, c = leased("three", [FakeHost("wsl", 4)], windows=9.9)
    assert not got and "RAM" in c.reasons["wsl"], "Windows under its reserve plus one unit"
    got, c = leased("four", [FakeHost("wsl", 4)], windows=None)
    assert got == [] and c.reasons["wsl"] == "Windows available RAM unknown", "an unknown Windows figure is a stop"
    got, c = leased("five", [FakeHost("wsl", 4)], c=None)
    assert got == [] and "unreadable" in c.reasons["disk"], "an unreadable C: stops all placement and says so"
    got, c = leased("six", [FakeHost("wsl", 4)], wsl=None)
    assert got == [] and c.reasons["wsl"] == "WSL memory unknown"
    # C: projection: 5.0 GiB free is above the 3 GiB floor but the job's ~3.4 GiB would cross it
    got, c = leased("seven", [FakeHost("wsl", 4)], wsl=60., c=5.0)
    assert got == [] and "projected growth" in c.reasons["wsl"]
    got, c = leased("eight", [FakeHost("wsl", 4)], wsl=60., c=6.5)
    assert len(got) == 1, "6.5 - 3.4 = 3.1 stays above the floor for one job, not for two"
    # the next pass counts that job as not yet grown: no second WSL job within the ramp window
    c.place()
    assert len(c.queue.units(status="leased")) == 1


def test_wsl_ramp_window_keeps_a_just_started_job_counted(tmp_path):
    wsl, clock = FakeHost("wsl", 4), Clock()
    c = _coordinator(tmp_path, [wsl], probe=_probe(wsl=30.), clock=clock)
    c.tick()
    first = len(c.queue.units(status="leased"))
    assert first == 3, "(30 - 6) // 8"
    clock.now += 10
    c.tick()
    assert len(c.queue.units(status="leased")) == first, "the RAM of what just started is not yet in MemAvailable"


def test_mac_slots_follow_the_mac_own_free_ram_and_unknown_ram_places_nothing(tmp_path):
    mac = FakeHost("mac", 4, ram=6. + 16.)
    c = _coordinator(tmp_path / "a", [mac])
    c.tick()
    assert len(c.queue.units(status="leased")) == 2, "(22 - 6 reserve) // 8"
    mac2 = FakeHost("mac", 4)
    mac2.ram = None
    c = _coordinator(tmp_path / "b", [mac2])
    c.tick()
    assert not c.queue.units(status="leased")


def test_c_drive_stop_kills_wsl_workers_and_refunds_the_attempt_but_leaves_the_mac(tmp_path):
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 2)
    state = {"c": 20.}
    c = _coordinator(tmp_path, [mac, wsl], probe=lambda: {"wsl_available_gb": 60., "windows_available_gb": 40.,
                                                          "c_free_gb": state["c"]}, limits={"max_attempts": {"wsl": 1}})
    c.tick()
    on = {h: [u for u in c.queue.units(status="leased") if u["host"] == h] for h in ("mac", "wsl")}
    assert on["wsl"] and on["mac"]
    state["c"] = 2.9
    c.tick()
    for u in on["wsl"]:
        assert (u["unit_id"], 1) in wsl.killed
        row = c.queue.unit(u["unit_id"])
        assert row["status"] == "pending" and row["history"][-1]["note"] == "C: stop" and row["history"][-1].get("refund")
        assert c._attempts_on(row, "wsl") == 0, "a resource stop is not the host's fault"
    assert all(c.queue.unit(u["unit_id"])["status"] == "leased" for u in on["mac"]) and not mac.killed
    assert "C:" in c.reasons["wsl"]


def test_documents_run_on_a_thread_so_placement_and_reaping_continue(tmp_path):
    wsl = FakeHost("wsl", 3)
    gate = threading.Event()
    docs = Documents(gate=gate)
    plan = {"quarters": [
        {"id": "q1", "asof": "2026-01-01", "arms": [
            {"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]},
        {"id": "q2", "asof": "2026-01-01", "arms": [
            {"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]}
    c = _coordinator(tmp_path, [wsl], docs, plan=plan, probe=_probe(wsl=60.))
    c.tick()
    wsl.finish("q1/a/m", 1)
    started = time.time()
    c.tick()  # q1's document starts and blocks on the gate: tick must still return
    assert time.time() - started < 5 and c.queue.arm("q1", "a")["status"] == "running"
    assert c.queue.unit("q1/a/m")["status"] == "verified"
    wsl.finish("q2/a/m", 1)
    c.tick()  # reaping a second unit works while the first document runs
    assert c.queue.unit("q2/a/m")["status"] == "verified"
    gate.set()
    c.join_documents()
    assert c.queue.arm("q1", "a")["status"] == "closed" and c.queue.arm("q2", "a")["status"] == "waiting", \
        "one registry (arm a): q2's document waits for q1's"
    c.tick()
    c.join_documents()
    assert c.queue.arm("q2", "a")["status"] == "closed"


def test_a_restart_mid_document_adopts_the_running_arm(tmp_path):
    wsl = FakeHost("wsl", 3)
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]}
    gate, docs1 = threading.Event(), None
    docs1 = Documents(gate=gate)
    c1 = _coordinator(tmp_path, [wsl], docs1, plan=plan)
    c1.tick()
    wsl.finish("q/a/m", 1)
    c1.tick()
    assert c1.queue.arm("q", "a")["status"] == "running"
    # the first coordinator dies here; its thread is abandoned (gate never opens before the second one starts)
    docs2 = Documents()
    c2 = _coordinator(tmp_path, [wsl], docs2, plan=plan)
    c2.tick()
    c2.join_documents()
    assert docs2.calls, "the new coordinator re-attached to the running arm"
    assert c2.queue.arm("q", "a")["status"] == "closed"
    assert c2.queue.unit("q/a/m")["status"] == "replayed"
    gate.set()
    c1.join_documents()


def test_a_blinded_arm_has_no_units_and_runs_as_a_plain_wsl_document(tmp_path):
    wsl = FakeHost("wsl", 2)
    docs = Documents()
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}}]}]}
    c = _coordinator(tmp_path, [wsl], docs, plan=plan)
    assert not _tick(c)
    assert docs.calls == [("q", "a", False, [])], "launched with no recorded directory"
    assert not wsl.spawned and c.queue.arm("q", "a")["status"] == "closed"


def test_a_document_waits_when_the_ram_or_c_limits_do_not_admit_it(tmp_path):
    wsl, docs = FakeHost("wsl", 2), Documents()
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}}]}]}
    for sub, probe in (("ram", _probe(wsl=10.)), ("c", _probe(c=5.0)), ("win", _probe(windows=None))):
        c = _coordinator(tmp_path / sub, [wsl], docs, plan=plan, probe=probe)
        c.tick()
        assert c.queue.arm("q", "a")["status"] == "waiting" and "q/a" in c.reasons and not docs.calls, sub


# ---- parity ---------------------------------------------------------------------------------------------
def test_similarity_is_pass_fail_with_a_reason_code_and_never_a_value():
    base, _ = _body("m", 1)
    a, b = json.loads(base), json.loads(base)
    assert Similarity.compare(a, b) == (True, "ok")
    b["actions"]["decisions"]["2022-01-03"]["certificates"][0]["objective"] = 10.05
    assert Similarity.compare(a, b) == (True, "ok"), "0.5% apart: similar, not equal (owner: trust the Mac's answer)"
    b["actions"]["decisions"]["2022-01-03"]["certificates"][0]["objective"] = 10.2
    assert Similarity.compare(a, b) == (False, "objective"), "2% apart in one week fails"
    for key, value, reason in (("bundle_manifest_sha256", "z", "identity"), ("method", "x", "identity"),
                               ("dependencies", {"numpy": "9"}, "identity")):
        c = json.loads(base)
        c[key] = value
        assert Similarity.compare(a, c) == (False, reason)
    c = json.loads(base)
    c["actions"]["weeks"] = c["actions"]["weeks"][:1]
    assert Similarity.compare(a, c) == (False, "weeks")
    c = json.loads(base)
    del c["actions"]["decisions"]["2022-01-10"]
    assert Similarity.compare(a, c) == (False, "weeks")
    # no routine thresholds beyond the one: a time-limited week is held to the same 1%, with no share allowance
    lim_a = json.loads(_body("m", 1, terminations={"2022-01-03": "maxTimeLimit"})[0])
    lim_b = json.loads(_body("m", 1, terminations={"2022-01-03": "maxTimeLimit"}, objectives={"2022-01-03": 12.})[0])
    assert Similarity.compare(lim_a, lim_b) == (False, "objective")
    assert Similarity.compare({}, {}) == (False, "malformed")
    assert len(Similarity.thresholds_sha256()) == 64
    assert not hasattr(Similarity, "OPTIMAL_REL") and not hasattr(Similarity, "LIMIT_SHARE"), "the 1e-6 and 90% rules are gone"


def test_the_similarity_floor_is_a_cent_and_cannot_hide_a_held_books_difference():
    """M5: near zero the relative test is taken against one dollar (1% of it is a cent); above that it is 1% relative."""
    from dskit.pipeline.base import value_hash

    def pair(first, second):
        a = json.loads(_body("m", 1, objectives={"2022-01-03": first})[0])
        b = json.loads(_body("m", 1, objectives={"2022-01-03": second})[0])
        return Similarity.compare(a, b)
    assert Similarity.FLOOR == 1.0 and Similarity.REL == 1e-2
    assert pair(0., 0.) == (True, "ok") and pair(0., .009) == (True, "ok"), "two near-zero weeks agree within a cent"
    assert pair(0., .02) == (False, "objective"), "two cents apart at zero is a real difference (a floor of 100 would pass it)"
    assert pair(.3, .32) == (False, "objective")
    assert pair(1000., 1009.) == (True, "ok") and pair(1000., 1011.) == (False, "objective"), "above the floor: 1% relative"
    assert Similarity.thresholds_sha256() != value_hash([Similarity.REL]), "the verdict hash covers the floor as well"


def test_matching_actions_are_recorded_as_information_and_never_decide_the_verdict():
    """M5: the owner wants similar, not equal: different integer actions with close objectives still pass."""
    base = json.loads(_body("m", 1)[0])
    other = json.loads(_body("m", 1)[0])
    assert Similarity.actions_match(base, other) is True
    other["actions"]["decisions"]["2022-01-03"]["actions"] = {"primary_quantities": [[1]], "pairs": []}
    assert Similarity.actions_match(base, other) is False
    assert Similarity.compare(base, other) == (True, "ok"), "different actions, same objectives: similar"
    del other["actions"]["decisions"]["2022-01-10"]
    assert Similarity.actions_match(base, other) is False
    assert Similarity.actions_match({}, {}) is None


def _parity_coordinator(tmp_path, mac, wsl, **kw):
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": ["m1", "m2", "m3"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]}
    return _coordinator(tmp_path, [mac, wsl], plan=plan, parity={"unit": "q/a/m1"},
                        probe=_probe(wsl=80.), **kw)


def test_first_parity_gate_runs_only_the_parity_unit_on_the_mac_and_its_twin_on_wsl(tmp_path):
    mac, wsl = FakeHost("mac", 4), FakeHost("wsl", 0)  # wsl_slots 0, mac slots 4: the gate caps both
    c = _parity_coordinator(tmp_path, mac, wsl)
    assert c.parity_state() == "pending"
    c.tick()
    assert [s[0] for s in mac.spawned] == ["q/a/m1"], "one Mac unit, the parity unit"
    assert [s[0] for s in wsl.spawned] == ["q/a/m1@parity"], "the twin runs even at wsl_slots 0, and nothing else does"
    mac.finish("q/a/m1", 1, objectives={"2022-01-03": 11.})
    wsl.finish("q/a/m1@parity", 1, objectives={"2022-01-03": 11.})
    c.tick()
    held = c.queue.unit("q/a/m1")
    assert held["status"] == "verified" and (c.recorded_dir("q", "a") / "m1.json").exists()
    verdict = json.loads((tmp_path / "q" / "parity.json").read_text())
    assert verdict["passed"] is True and verdict["reason"] == "ok"
    assert set(verdict) == {"passed", "reason", "unit", "thresholds_sha256", "bundle_sha256", "hosts", "sha256",
                            "actions_match"}, "pass/fail, hashes and whether the integer actions matched"
    assert verdict["actions_match"] is True
    assert all(len(v) == 64 for v in verdict["sha256"].values())
    assert sorted(p.name for p in c.recorded_dir("q", "a").iterdir()) == ["m1.json", "m1.json.sha256"], \
        "only the Mac file is recorded, never the WSL twin"
    assert c.parity_state() == "passed"
    c.tick()
    assert {s[0] for s in mac.spawned} == {"q/a/m1", "q/a/m2", "q/a/m3"}, "the gate is open"


def test_failed_parity_holds_the_mac_file_out_of_recorded_and_disables_the_mac(tmp_path):
    mac, wsl = FakeHost("mac", 4), FakeHost("wsl", 2)
    c = _parity_coordinator(tmp_path, mac, wsl)
    c.tick()
    mac.finish("q/a/m1", 1, objectives={"2022-01-03": 11.})
    wsl.finish("q/a/m1@parity", 1, objectives={"2022-01-03": 99.})
    c.tick()
    verdict = json.loads((tmp_path / "q" / "parity.json").read_text())
    assert verdict["passed"] is False and verdict["reason"] == "objective"
    assert c.queue.unit("q/a/m1")["status"] == "rejected"
    assert not (c.recorded_dir("q", "a") / "m1.json").exists(), "a dissimilar Mac file never reaches the document"
    before = len(mac.spawned)
    c.tick()
    assert len(mac.spawned) == before, "the Mac takes no more units"
    assert c.parity_state() == "failed"
    assert {u["host"] for u in c.queue.units(status="leased")} <= {"wsl"}


def test_a_coordinator_without_a_mac_needs_no_parity(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], parity={"unit": "2022q1/rules/m1"})
    assert c.parity_state() is None and not [u for u in c.queue.units() if u["role"] == "parity"]


def test_parity_unit_must_be_in_the_plan(tmp_path):
    with pytest.raises(ValueError, match="not in the plan"):
        _coordinator(tmp_path, [FakeHost("mac", 1), FakeHost("wsl", 1)], parity={"unit": "nope/a/m"})


# ---- real LocalHost, SshHost, DocumentRunner, helpers ---------------------------------------------------
STUB_WORKER = '''
import json, sys, time, hashlib, os
spec = json.load(open(sys.argv[1]))
out = spec["out"]
open(out + ".progress", "a").write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " week 1/1 ok\\n")
env = {k: os.environ.get(k) for k in ("PYOMO_CONFIG_DIR", "XDG_CACHE_HOME", "TMPDIR", "HOME", "PYTHONPYCACHEPREFIX")}
body = {"method": spec["method"], "attempt": spec["attempt"], "cwd": os.getcwd(), "env": env}
raw = json.dumps(body, sort_keys=True).encode()
open(out, "wb").write(raw)
open(out + ".sha256", "w").write(hashlib.sha256(raw).hexdigest() + "  x\\n")
open(out + ".done", "w").write("done\\n")
if spec["method"] == "slow":
    time.sleep(60)
'''


def _stub_bundle(tmp_path):
    bundle = tmp_path / "bundle"
    pkg = bundle / "code" / "index_options"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "mio_worker.py").write_text(STUB_WORKER)
    (bundle / "manifest.json").write_text(json.dumps({"files": {}, "sha256": "m" * 64}))
    return bundle


def _unit(method="m", unit_id="q/a/m"):
    return {"unit_id": unit_id, "quarter": "q", "arm": "a", "method": method}


def test_local_host_creates_its_roots_and_runs_the_worker_from_the_bundle_code_tree(tmp_path):
    """The Major: cwd=work_root was never created, so every WSL unit failed on a fresh queue root."""
    bundle = _stub_bundle(tmp_path)
    host = LocalHost("wsl", 1, sys.executable, lambda unit: bundle, tmp_path / "fresh" / "work", tmp_path / "fresh" / "out")
    unit = _unit()
    assert host.online() and host.progress(unit, 1) is None and host.fetch(unit, 1) is None
    handle = host.spawn(unit, 1)
    assert handle["pid"] and handle["pid_start"]
    deadline = time.time() + 30
    while host.fetch(unit, 1) is None and time.time() < deadline:
        time.sleep(0.1)
    raw, side = host.fetch(unit, 1)
    assert json.loads(raw)["method"] == "m" and side.split()[0] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["cwd"] == str((tmp_path / "fresh" / "work").resolve())
    assert host.progress(unit, 1).endswith("Z")
    deadline = time.time() + 10
    while host.alive(unit, 1, handle) and time.time() < deadline:
        time.sleep(0.1)
    assert not host.alive(unit, 1, handle)
    slow = _unit("slow", "q/a/slow")
    handle = host.spawn(slow, 1)
    assert host.alive(slow, 1, handle)
    host.kill(slow, 1, handle)
    deadline = time.time() + 10
    while host.alive(slow, 1, handle) and time.time() < deadline:
        time.sleep(0.1)
    assert not host.alive(slow, 1, handle)
    assert not host.alive(slow, 1, {"pid": handle["pid"], "pid_start": "other"}) and not host.alive(slow, 1, {})


def _fake_ssh(tmp_path, vm_stat=True, mounted=True):
    """An ``ssh`` that ignores its options and runs the remote command in a local /bin/sh, with df/vm_stat shims."""
    shim = tmp_path / "shim"
    shim.mkdir(exist_ok=True)
    if mounted:
        (shim / "df").write_text('#!/bin/sh\nfor last; do :; done\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nramdisk 1 1 1 1%% %s\\n" "$last"\n')
    if vm_stat:
        (shim / "vm_stat").write_text('#!/bin/sh\necho "Mach Virtual Memory Statistics: (page size of 16384 bytes)"\n'
                                      'echo "Pages free:                               65536."\n'
                                      'echo "Pages inactive:                           65536."\n'
                                      'echo "Pages speculative:                        65536."\n')
    for path in shim.iterdir():
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "ssh"
    script.write_text('#!/bin/sh\nwhile [ "$#" -gt 1 ]; do\n  case "$1" in -o) shift 2;; *) shift;; esac\ndone\n'
                      'for last; do :; done\n'
                      'PATH="' + str(shim) + ':$PATH" exec /bin/sh -c "$last"\n')
    script.chmod(0o755)
    return str(script)


def _remote_root(tmp_path, token="tok"):
    root = tmp_path / "ram"
    root.mkdir(exist_ok=True)
    (root / ".mio-sentinel").write_text(token)
    return root


def _ssh_host(tmp_path, root, **kw):
    bundle = _stub_bundle(tmp_path)
    script = Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "mac_worker.sh"  # the real one
    return SshHost("mac", 2, "mac", str(root), sys.executable, lambda unit: str(root / "bundles" / "q-a"),
                   str(script), kw.pop("sentinel", "tok"), local_bundle_for=lambda unit: bundle,
                   ssh=kw.pop("ssh", _fake_ssh(tmp_path)), **kw)


def test_ssh_host_identity_gate_refuses_an_unmounted_or_unmarked_root_and_a_missing_certificate(tmp_path):
    root = _remote_root(tmp_path)
    assert _ssh_host(tmp_path, root).online()
    assert not _ssh_host(tmp_path, root, sentinel="other").online(), "wrong sentinel: not our RAM disk"
    assert not _ssh_host(tmp_path, root, certificate=lambda: False).online(), "no valid Mac certificate"
    assert _ssh_host(tmp_path, root, certificate=lambda: True).online()
    ssd = tmp_path / "plain-dir"
    ssd.mkdir()
    (ssd / ".mio-sentinel").write_text("tok")
    (tmp_path / "noshim").mkdir()
    real_df = _fake_ssh(tmp_path / "noshim", mounted=False)  # no df shim: the real df says this is not a mount point
    assert not _ssh_host(tmp_path, ssd, ssh=real_df).online(), "a sentinel in a plain SSD directory is not a RAM disk"
    assert not _ssh_host(tmp_path, tmp_path / "absent").online()


def test_ssh_host_spawn_is_portable_posix_and_writes_the_whole_spec(tmp_path):
    """`A && cat > spec &` backgrounded the spec write in plain sh (stdin /dev/null), leaving an empty spec."""
    root = _remote_root(tmp_path)
    host = _ssh_host(tmp_path, root)
    assert host.stage(_unit()) and (root / "bundles" / "q-a").is_dir()
    assert host.stage(_unit()), "idempotent by manifest digest"
    unit = _unit()
    handle = host.spawn(unit, 1)
    assert handle["pid"] > 0
    deadline = time.time() + 30
    while host.fetch(unit, 1) is None and time.time() < deadline:
        time.sleep(0.1)
    raw, side = host.fetch(unit, 1)
    spec = json.loads((root / "out" / "q__a__m.1.spec.json").read_text())
    assert spec["method"] == "m" and spec["work_dir"] == str(root / "work" / "q__a__m.1") and spec["out"].endswith("q__a__m.1.json")
    body = json.loads(raw)
    assert body["method"] == "m" and side.split()[0] == hashlib.sha256(raw).hexdigest()
    home = Path(root).resolve()
    assert body["cwd"] == str(home / "work"), "mac_worker.sh runs from the RAM root's work directory"
    assert body["env"] == {"PYOMO_CONFIG_DIR": str(home / "pyomo"), "XDG_CACHE_HOME": str(home / "cache"),
                           "TMPDIR": str(home / "tmp"), "HOME": str(home / "home"),
                           "PYTHONPYCACHEPREFIX": str(home / "pycache")}, "every cache and config goes to the RAM root"
    assert host.progress(unit, 1).endswith("Z")
    with pytest.raises(RuntimeError, match="remote spawn failed"):
        _ssh_host(tmp_path, root, sentinel="wrong").spawn(unit, 2)
    assert not (root / "out" / "q__a__m.2.spec.json").exists(), "an unmarked root is never written"
    slow = _unit("slow", "q/a/slow")
    handle = host.spawn(slow, 1)
    time.sleep(0.5)
    host.kill(slow, 1, handle)


def test_a_new_queue_root_never_receives_an_earlier_runs_finished_file_under_the_same_attempt_name(tmp_path):
    """Parity fails, the operator starts a new queue root, attempt 1 is leased again: the old ``.done`` must not answer."""
    root = _remote_root(tmp_path)
    host = _ssh_host(tmp_path, root)
    unit = _unit("slow", "q/a/slow")
    assert host.stage(unit), "the worker script needs its bundle on the RAM disk"
    out = root / "out"
    out.mkdir()
    for suffix, text in ((".json", "STALE"), (".json.sha256", "stale  x\n"), (".json.done", "done\n"),
                         (".json.progress", "old\n")):
        (out / f"q__a__slow.1{suffix}").write_text(text)
    assert host.fetch(unit, 1) == (b"STALE", "stale  x\n"), "the premise: the stale files would be fetched"
    handle = host.spawn(unit, 1)
    try:
        deadline = time.time() + 30
        fetched = host.fetch(unit, 1)
        while (fetched is None or fetched[0] == b"STALE") and time.time() < deadline:
            time.sleep(0.1)
            fetched = host.fetch(unit, 1)
        assert fetched is not None and fetched[0] != b"STALE" and json.loads(fetched[0])["attempt"] == 1
        assert b"old" not in (out / "q__a__slow.1.json.progress").read_bytes()
    finally:
        host.kill(unit, 1, handle)


def test_a_new_queue_root_local_host_also_clears_an_earlier_finished_file(tmp_path):
    bundle = _stub_bundle(tmp_path)
    host = LocalHost("wsl", 1, sys.executable, lambda unit: bundle, tmp_path / "w", tmp_path / "o")
    unit = _unit("slow", "q/a/slow")
    earlier = host.spawn(unit, 1)
    deadline = time.time() + 30
    while host.fetch(unit, 1) is None and time.time() < deadline:
        time.sleep(0.1)
    first = host.fetch(unit, 1)
    host.kill(unit, 1, earlier)
    assert first is not None
    stale = host._out(unit, 1)
    stale.write_bytes(b"STALE")
    handle = host.spawn(unit, 1)
    try:
        deadline = time.time() + 30
        fetched = host.fetch(unit, 1)
        while (fetched is None or fetched[0] == b"STALE") and time.time() < deadline:
            time.sleep(0.1)
            fetched = host.fetch(unit, 1)
        assert fetched is not None and fetched[0] != b"STALE"
    finally:
        host.kill(unit, 1, handle)


def test_a_fresh_bring_up_can_stage_the_bundle_before_the_mac_has_a_certificate(tmp_path):
    """Circular before: no certificate meant offline, offline meant never staged, certify needs the staged bundle."""
    from mio_parallel_queue import stage_bundle
    root = _remote_root(tmp_path)
    mac = _ssh_host(tmp_path, root, certificate=lambda: False)
    assert not mac.online(), "no certificate yet: the coordinator would place nothing here"
    plan = _one_arm_plan(("m1",))
    c = Coordinator(tmp_path / "q", plan, [mac, FakeHost("wsl", 0)], Documents(), _probe(), {}, Clock(),
                    parity={"unit": "q/a/m1"})
    digest = stage_bundle(c, "q/a")
    assert (root / "bundles" / "q-a").is_dir() and digest == json.loads(
        (root / "bundles" / "q-a" / "manifest.json").read_text())["sha256"]
    assert stage_bundle(c, "q/a") == digest, "idempotent by manifest digest"
    with pytest.raises(ValueError, match="not in the plan"):
        stage_bundle(c, "q/missing")
    wrong = _ssh_host(tmp_path, root, sentinel="other", certificate=lambda: False)  # not our RAM disk: still refused
    elsewhere = Coordinator(tmp_path / "q2", plan, [wrong, FakeHost("wsl", 0)], Documents(), _probe(), {}, Clock(),
                            parity={"unit": "q/a/m1"})
    (root / "bundles" / "q-a" / "manifest.json").write_text('{"sha256": "stale"}')
    with pytest.raises(RuntimeError, match="staging failed"):
        stage_bundle(elsewhere, "q/a")
    with pytest.raises(ValueError, match="no Mac host"):
        stage_bundle(_coordinator(tmp_path / "w", [FakeHost("wsl", 1)]), "q/a")


def test_main_stage_refuses_an_arm_that_is_not_planned(tmp_path, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(tmp_path / "queue"), "python": sys.executable,
                                  "runtime": str(_stub_runtime(tmp_path)), "quarters": []}))
    assert main(["stage", str(config), "q/a"]) == 1 and "no Mac host" in capsys.readouterr().err


def test_the_runbooks_launch_lines_match_its_own_jobs_json_under_the_compute_gate(monkeypatch):
    """The gate substring-matches a job's literal command: a launch with nohup used to match nothing (a forced waiver)."""
    import re
    import mio_parallel_queue
    gate_file = Path(REAL_SKILL) / "compute_gate.py"
    if not gate_file.exists():
        pytest.skip("the compute skill is not on this machine")
    for name in ("compute_state", "compute_gate"):  # a test's stub of the skill's module must not shadow the real one
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(gate_file.parent))
    gate = importlib.import_module("compute_gate")
    monkeypatch.setitem(sys.modules, "compute_gate", gate)  # restored (removed) at teardown, like compute_state
    monkeypatch.setitem(sys.modules, "compute_state", sys.modules["compute_state"])
    text = (Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "RUNBOOK.md").read_text()
    plan = json.loads(next(b for b in re.findall(r"```json\n(.*?)\n```", text, re.S) if '"run_id"' in b))
    commands = []
    for line in text.splitlines():
        line = line.strip().strip("`").split("   #")[0]
        if line.startswith("cd /home/russell/wt/mio-parallel-20261105 &&") and "mio_parallel_queue.py" in line:
            commands.append(line.replace("<run_dir>", "X").replace("QUARTER/ARM", "q/a"))
    verbs = {re.search(r"mio_parallel_queue\.py (\w[\w-]*)", c).group(1) for c in commands}
    assert {"stage", "run", "reopen", "recorded-from"} <= verbs, verbs
    for command in commands:
        assert gate.matched_jobs(plan, command), "no job of the runbook's jobs.json matches: "+command[-80:]
    assert "$R" not in " ".join(commands) and "$PY" not in " ".join(commands)


def test_ssh_host_reads_the_macs_free_ram_and_fails_closed_when_it_cannot(tmp_path):
    root = _remote_root(tmp_path)
    assert _ssh_host(tmp_path, root).available_gb() == pytest.approx(3 * 65536 * 16384 / 2**30)
    broken = tmp_path / "broken"
    broken.mkdir()
    ssh = _fake_ssh(broken, vm_stat=False)  # no vm_stat on this machine: nothing to count
    assert _ssh_host(tmp_path, root, ssh=ssh).available_gb() in (None, 0.0)


def _stub_runtime(tmp_path, lock_ok=True):
    """A runtime with a stub `index_options.ticker_lock` and a stub `dskit.pipeline run` that writes a result."""
    runtime = tmp_path / "runtime"
    child = runtime / "children" / "index_options" / "index_options"
    child.mkdir(parents=True)
    (child / "__init__.py").write_text("")
    (child / "ticker_lock.py").write_text(
        "import json, sys\nraise SystemExit(1 if 'bad' in json.load(open(sys.argv[1])).get('name', '') else 0)\n")
    pipe = runtime / "dskit" / "pipeline"
    pipe.mkdir(parents=True)
    (runtime / "dskit" / "__init__.py").write_text("")
    (pipe / "__init__.py").write_text("")
    (pipe / "__main__.py").write_text(
        "import json, sys, os, pathlib\n"
        "cfg = json.load(open(sys.argv[2]))\n"
        "run = pathlib.Path(cfg['outputs']['run_root']) / 'r1'\n"
        "run.mkdir(parents=True, exist_ok=True)\n"
        "print('MIO_EVENT {\"type\":\"weekly_decision\",\"objective\":123456.78}')\n"
        "(run / 'result.json').write_text(json.dumps({'state': 'ran', 'exit_code': 0, 'document_hash': 'h' * 64}))\n"
        "(run / 'carry.json').write_text(json.dumps({'close_packet': {'receipt': {'path': 'receipt.json'}}}))\n"
        "(run / 'receipt.json').write_text(json.dumps({'complete': True, 'results': [{'id': 'm1', 'status': 'completed', 'secret': 9.99}],\n"
        "    'audit': {'recorded_from': {'m1': {'host': {'label': 'mac'}, 'attempt': 1, 'sha256': 'a' * 64, 'bundle_manifest_sha256': 'b' * 64}}}}))\n"
        "(run / 'run.log').write_text('MIO_EVENT {\"type\":\"recorded_rejected\",\"x\":1}\\nMIO_EVENT {\"type\":\"recorded_absent\"}\\nMIO_EVENT {\"type\":\"recorded_rejected\"}\\n')\n")
    return runtime


def _doc(tmp_path, name, run_root):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"name": name, "outputs": {"run_root": str(run_root)},
                                "pipeline": {"study": {"uses": "x:MIOPacketStudy", "params": {"study": {"s": 1}}}}}))
    return path


def test_document_runner_applies_the_ticker_lock_seals_its_logs_and_runs_the_arm_stages(tmp_path):
    runtime = _stub_runtime(tmp_path)
    runner = DocumentRunner(sys.executable, str(runtime), tmp_path / "gen", tmp_path / "sealed", poll=0.01)
    assert oct((tmp_path / "sealed").stat().st_mode & 0o777) == "0o700"
    refused = runner.run("init-x", _doc(tmp_path, "bad", tmp_path / "runs-bad"), "2026-01-01")
    assert not refused["ok"] and refused["refused"] == "ticker_lock"
    assert not (tmp_path / "runs-bad").exists(), "the document never ran"
    cfg = _doc(tmp_path, "good-run", tmp_path / "runs" / "run")
    init = _doc(tmp_path, "good-init", tmp_path / "runs" / "init")
    spec = {"initialize": str(init), "run": str(cfg)}
    outcome = runner.run_arm(spec, "q", "a", tmp_path / "recorded", "2026-01-01")
    assert outcome["ok"] and outcome["complete"] and outcome["recorded_from"] == ["m1"]
    assert (tmp_path / "gen" / "run-q-a.json").exists(), "recorded config written"
    assert json.loads((tmp_path / "gen" / "run-q-a.json").read_text())["pipeline"]["study"]["params"]["recorded"] == str(tmp_path / "recorded")
    logs = sorted((tmp_path / "sealed").glob("*.log"))
    assert logs and all(oct(p.stat().st_mode & 0o777) == "0o600" for p in logs), "document stdout goes to a sealed path"
    assert not list((tmp_path / "gen").glob("*.log")) and not (tmp_path / "logs").exists()
    plain = runner.run_arm({"initialize": str(init), "run": str(cfg)}, "q", "b", None, "2026-01-01")
    assert plain["ok"], "recorded_dir None runs the document as is"


def test_document_runner_ticker_lock_refusal_stops_the_arm_before_any_run(tmp_path):
    runtime = _stub_runtime(tmp_path)
    runner = DocumentRunner(sys.executable, str(runtime), tmp_path / "gen", tmp_path / "sealed", poll=0.01)
    outcome = runner.run_arm({"initialize": str(_doc(tmp_path, "bad-init", tmp_path / "r1")),
                              "run": str(_doc(tmp_path, "good-run", tmp_path / "r2"))}, "q", "a", None, "2026-01-01")
    assert not outcome["ok"] and outcome["stage"] == "initialize" and outcome["entry"]["refused"] == "ticker_lock"
    assert not (tmp_path / "r2").exists()


def test_recorded_from_helper_prints_hashes_and_counts_never_values(tmp_path, capsys):
    runtime = _stub_runtime(tmp_path)
    runner = DocumentRunner(sys.executable, str(runtime), tmp_path / "gen", tmp_path / "sealed", poll=0.01)
    cfg = _doc(tmp_path, "good-run", tmp_path / "runs" / "run")
    entry = runner.run("run-x", cfg, "2026-01-01")
    report = recorded_from_report(entry["run_dir"])
    assert report["complete"] and report["recorded_rejected"] == 2 and report["recorded_absent"] == 1
    assert report["recorded_from"] == {"m1": {"host": "mac", "attempt": 1, "sha256": "a" * 64, "bundle_manifest_sha256": "b" * 64}}
    assert main(["recorded-from", entry["run_dir"]]) == 0
    printed = capsys.readouterr().out
    assert "9.99" not in printed and "123456" not in printed and "weekly_decision" not in printed
    assert json.loads(printed)["recorded_rejected"] == 2


def _ready(root, quarter, run_cfg, init_cfg, name="rules"):
    (root / f"READY-{quarter}.json").write_text(json.dumps(
        {"asof": "2026-01-01", "runs": [{"name": name, "initialize": str(init_cfg), "run": str(run_cfg)}]}))


def test_load_plan_builds_verifies_skips_and_never_trusts_an_existing_bundle(study, tmp_path):  # noqa: F811
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir()

    def run_doc(name, edit=lambda s: s):
        spec = edit(json.loads(json.dumps(study)))
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "outputs": {"run_root": str(tmp_path / name)},
                                    "pipeline": {"study": {"uses": "index_options.ledger_studies:MIOPacketStudy",
                                                           "params": {"study": spec}}}}))
        return path

    good_run, good_init = run_doc("good-run"), _doc(tmp_path, "good-init", tmp_path / "i")

    def blind(spec):
        spec["domain"]["predictive"] = {"policy": "owner_selected_blinded_cache"}
        return spec

    _ready(root, "q1", good_run, good_init)
    _ready(root, "q2", run_doc("good-blind", blind), good_init, name="blind")
    _ready(root, "q3", run_doc("bad-lock"), _doc(tmp_path, "bad-init", tmp_path / "j"))
    config = {"queue_root": str(root), "python": sys.executable, "runtime": str(runtime),
              "quarters": ["q1", "q2", "q3"]}
    plan = _load_plan(config)
    assert [q["id"] for q in plan["quarters"]] == ["q1", "q2"]
    arm = plan["quarters"][0]["arms"][0]
    assert arm["methods"] and len(arm["bundle_sha256"]) == 64 and len(arm["plan_sha256"]) == 64
    assert plan["quarters"][1]["arms"][0]["methods"] == [], "a blinded arm has no worker units (ADR-0293)"
    assert "locked ticker list" in plan["skipped"]["q3"], "a quarter that cannot be planned is skipped, not a crash"
    again = _load_plan(config)
    assert again["quarters"][0]["arms"][0]["bundle_sha256"] == arm["bundle_sha256"], "idempotent"
    victim = next(Path(arm["bundle_root"]).glob("code/**/mio_contract.py"))
    original = victim.read_bytes()
    victim.unlink()  # the bundle file may be a hard link to the real source: never write through it
    victim.write_bytes(original + b"\n# tampered after the bundle was staged\n")
    refused = _load_plan(config)
    assert [q["id"] for q in refused["quarters"]] == [] and "q1" in refused["skipped"], \
        "an existing bundle is re-verified, so a changed file skips the quarter instead of being trusted"
    assert "earlier skipped quarter" in refused["skipped"]["q2"], "q2 shares q1's registry: it cannot open before q1"
    _ready(root, "q4", run_doc("other-registry", lambda sp: sp["registry"].update(path=str(tmp_path / "other.sqlite")) or sp), good_init)
    own = _load_plan({**config, "quarters": ["q1", "q4"]})
    assert [q["id"] for q in own["quarters"]] == ["q4"] and "q1" in own["skipped"], \
        "a quarter with its own registry does not wait for a skipped one (owner: one registry per quarter-arm)"
    assert own["quarters"][0]["arms"][0]["registry"] == str(tmp_path / "other.sqlite")


def test_main_runs_one_pass_writes_status_and_a_second_coordinator_is_refused_cleanly(tmp_path, capsys):
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root), "scratch_root": str(tmp_path / "scratch"),
                                  "python": sys.executable, "runtime": str(runtime), "quarters": []}))
    assert main(["run", str(config), "--once"]) == 0
    status = json.loads((root / "status.json").read_text())
    assert status["units"] == [] and status["hosts"]["wsl"]["slots"] == 0, "wsl_slots defaults to 0"
    assert (root / "sealed").is_dir() and oct((root / "sealed").stat().st_mode & 0o777) == "0o700"
    lock = PidLock(root / "coordinator.lock")
    assert lock.acquire()
    try:
        assert main(["run", str(config), "--once"]) == 4
        assert "already holds" in capsys.readouterr().err
        other = PidLock(root / "coordinator.lock")
        assert not other.acquire()
    finally:
        lock.release()
    assert main(["run", str(config), "--once"]) == 0, "the lock is released"


def test_resource_probe_reads_what_it_can_and_reports_the_rest_as_unknown(tmp_path):
    probe = ResourceProbe(str(tmp_path), None)()
    assert probe["windows_available_gb"] is None and probe["c_free_gb"] > 0
    assert ResourceProbe(str(tmp_path / "absent"), lambda: 12.5)()["c_free_gb"] is None
    assert ResourceProbe(str(tmp_path), lambda: 12.5)()["windows_available_gb"] == 12.5

    def boom():
        raise OSError("powershell missing")

    assert ResourceProbe(str(tmp_path), boom)()["windows_available_gb"] is None


def test_skill_helpers_read_the_compute_skill_and_fail_closed_without_it(tmp_path):
    from mio_parallel_queue import skill_certificate, windows_reader

    scripts = tmp_path / "skill"
    scripts.mkdir()
    (scripts / "compute_state.py").write_text("def windows_available_cached(max_age=900, refresh=False):\n    return 12 * 2**30\n")
    (scripts / "compute_run.py").write_text(
        "def valid_cert(job_type, mac_host='mac'):\n"
        "    return {'job_type': job_type} if job_type == 'ok-type' and mac_host == 'mac' else None\n")
    saved_path = list(sys.path)
    names = ("compute_state", "compute_run")
    try:
        for name in names:
            sys.modules.pop(name, None)
        assert windows_reader(str(scripts))() == 12.0
        assert skill_certificate("ok-type", str(scripts))() is True
        assert skill_certificate("other-type", str(scripts))() is False
        assert skill_certificate("ok-type", str(scripts), "elsewhere")() is False
        for name in names:
            sys.modules.pop(name, None)
        sys.path[:] = [p for p in sys.path if p != str(scripts)]
        missing = str(tmp_path / "no-skill")
        assert windows_reader(missing)() is None, "an unreadable Windows figure is None, which the coordinator treats as a stop"
        assert skill_certificate("ok-type", missing)() is False, "no skill, no certificate, the Mac stays offline"
    finally:
        sys.path[:] = saved_path
        for name in names:
            sys.modules.pop(name, None)


def test_build_wires_the_mac_certificate_gate_and_requires_the_ram_disk_sentinel(tmp_path, monkeypatch):
    import mio_parallel_queue
    from mio_parallel_queue import _build

    runtime = _stub_runtime(tmp_path)
    monkeypatch.setattr(mio_parallel_queue, "_load_plan", lambda config: {"skipped": {}, "quarters": [
        {"id": "q", "asof": "2026-01-01", "arms": [{"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE,
                                                    "plan_sha256": PLAN, "spec": {}, "bundle_root": str(tmp_path / "b")}]}]})
    config = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(runtime), "quarters": [], "parity": {"unit": "q/a/m"},
              "mac": {"alias": "mac", "root": "/Volumes/mio-ram", "python": "/p", "worker_script": "/w.sh",
                      "sentinel": "tok", "slots": 1}}
    for broken in ({k: v for k, v in config.items() if k != "parity"}, {**config, "parity": None}, {**config, "parity": {}}):
        with pytest.raises(ValueError, match="parity.unit"):
            _build(broken)
    coordinator = _build(config)
    mac = coordinator.hosts[0]
    assert mac.name == "mac" and mac.certificate is not None and mac.sentinel == "tok"
    assert mac.online() is False, "no valid certificate: no ssh is even attempted"
    assert coordinator.hosts[1].name == "wsl" and coordinator.hosts[1].slots == 0
    with pytest.raises(KeyError):
        _build({**config, "mac": {k: v for k, v in config["mac"].items() if k != "sentinel"}})


def test_end_to_end_real_coordinator_real_local_worker_and_the_recorded_document(study, tmp_path, monkeypatch):  # noqa: F811
    """Units complete through the real worker, nothing is falsely killed as stale, and the document replays them."""
    from dskit.pipeline.driver import run_document
    from index_options.mio_worker import MIOMethodBundle
    from test_mio_worker import _close_document, _init, _receipt, _two_methods

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    spec = _two_methods(study)
    spec["registry"] = {**spec["registry"], "path": str(tmp_path / "exposure-b.sqlite")}
    methods = [m["id"] for m in spec["domain"]["params"]["methods"]]
    scratch = tmp_path / "scratch"
    bundle_root = scratch / "bundles" / "q-a"
    manifest = MIOMethodBundle(spec, bundle_root).build()
    plan_sha = json.loads((bundle_root / "plan.json").read_text())["sha256"]
    root = tmp_path / "queue"
    receipts = {}

    def documents(_spec, quarter, arm, recorded_dir, asof):
        registry, plan, request = _init(spec)
        ran = run_document(_close_document(tmp_path, "recorded", spec, plan, request, recorded=recorded_dir),
                           asof="2019-02-09", journal=False)
        art = _receipt(ran, None)
        receipts["art"] = art
        return {"ok": ran.state == "ran" and art["complete"], "run_dir": str(ran.run_dir),
                "recorded_from": sorted(art["audit"]["recorded_from"])}

    host = LocalHost("wsl", 2, sys.executable, lambda unit: bundle_root, scratch / "work", scratch / "out")
    plan = {"quarters": [{"id": "q", "asof": "2019-02-09", "arms": [
        {"id": "a", "methods": methods, "bundle_sha256": manifest["sha256"], "plan_sha256": plan_sha, "spec": {}}]}]}
    c = Coordinator(root, plan, [host], documents, _probe(wsl=60.), {"unit_ram_gb": 4, "document_ram_gb": 4,
                                                                      "stale_seconds": 1800})
    deadline = time.time() + 400
    while c.tick():
        assert time.time() < deadline, "the queue did not finish"
        time.sleep(0.5)
    c.join_documents()
    assert c.queue.arm("q", "a")["status"] == "closed", c.queue.arm("q", "a")
    rows = c.queue.units()
    assert [u["status"] for u in rows] == ["replayed"] * len(methods)
    assert all(u["attempt"] == 1 and not any("stale" in h.get("note", "") for h in u["history"]) for u in rows), \
        "no false stale kill: each unit ran once, to completion"
    assert sorted(receipts["art"]["audit"]["recorded_from"]) == sorted(methods)
    out = sorted((scratch / "out").glob("*.progress"))
    assert len(out) == len(methods)
    import re
    assert all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", line) for p in out for line in p.read_text().splitlines())
    assert not list((scratch / "out").glob("*.result.json")), "no economics beside what operators tail"
    status = json.loads((root / "status.json").read_text())
    assert all(set(u) == {"unit_id", "status", "attempt", "host", "result_sha256", "started", "last_progress"}
               for u in status["units"])


def test_the_example_config_starts_at_first_parity_settings_and_the_worker_script_refuses_an_unmarked_root(tmp_path):
    here = Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel"
    config = json.loads((here / "queue.example.json").read_text())
    assert config["mac"]["slots"] == 1 and config["wsl_slots"] == 0 and config["parity"]["unit"]
    assert "certificate_job_type" not in config["mac"] and config["mac"]["sentinel"], "the certificate type is not configurable"
    assert config["scratch_root"].startswith("/mnt/d/") and config["reuse_root"].startswith(config["scratch_root"])
    assert config["queue_root"].startswith("/home/"), "sealed files live on WSL ext4, not on a Windows drive"
    assert config["limits"]["c_floor_gb"] == 3 and config["limits"]["c_job_gb"] >= 3.4
    assert not {"deadline_seconds"} & set(config["limits"]) and Coordinator.__init__ is not None
    assert all(config["limits"][k] >= v for k, v in __import__("mio_parallel_queue").LIMIT_FLOORS.items() if k in config["limits"])
    assert all(isinstance(n, int) and n >= 1 for n in config["limits"]["max_attempts"].values()), "never unlimited"
    root = tmp_path / "plain"
    (root / "out").mkdir(parents=True)
    spec = root / "out" / "u.1.spec.json"
    spec.write_text("{}")
    done = subprocess.run(["sh", str(here / "mac_worker.sh"), sys.executable, str(spec)], capture_output=True, text=True)
    assert done.returncode == 3 and "not a mount point" in done.stderr, "a plain directory is never a worker root"


def test_the_worker_script_exports_the_ram_disk_environment_before_it_runs_any_python_and_forwards_options(tmp_path):
    """mac_worker.sh ran `python -c` before HOME and the caches pointed at the RAM disk; options (--print) are forwarded."""
    script = (Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "mac_worker.sh").read_text()
    lines = script.splitlines()
    first_python = next(i for i, line in enumerate(lines) if '"$PY"' in line and not line.lstrip().startswith("#"))
    for name in ("HOME=", "XDG_CACHE_HOME=", "TMPDIR=", "PYTHONPYCACHEPREFIX=", "PYOMO_CONFIG_DIR="):
        assert next(i for i, line in enumerate(lines) if f"export {name}" in line) < first_python, name
    assert lines[-1].endswith('"$SPEC" "$@"') and "umask 077" in script


MAC_PYTHON = "/Users/gibsonrussell/Compute/software/mio-worker-20261102/bin/python"


@pytest.mark.skipif(not __import__("os").environ.get("MIO_MAC_E2E"), reason="live Mac test: set MIO_MAC_E2E=1")
def test_end_to_end_with_a_real_mac_worker_on_a_tiny_ram_disk_and_a_real_parity_unit(study, tmp_path, monkeypatch):  # noqa: F811
    """Synthetic only. A 256 MB RAM disk is created on the Mac, used as the worker root and detached afterwards."""
    import importlib.metadata
    import re
    import subprocess as sp
    from dskit.pipeline.driver import run_document
    from index_options.mio_worker import MIOMethodBundle
    from test_mio_worker import _close_document, _init, _receipt, _two_methods

    def mac(command, **kw):
        return sp.run(["ssh", "-o", "BatchMode=yes", "mac", command], capture_output=True, text=True, timeout=120, **kw)

    attach = mac("hdiutil attach -nomount ram://524288")
    assert attach.returncode == 0, attach.stderr
    device = attach.stdout.split()[0]
    try:
        assert mac(f"diskutil erasevolume HFS+ miotest {device}").returncode == 0
        root = "/Volumes/miotest"
        assert mac(f"echo e2e-token > {root}/.mio-sentinel && mkdir -p {root}/out").returncode == 0
        script = Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "mac_worker.sh"
        assert mac(f"cat > {root}/mac_worker.sh", input=script.read_text()).returncode == 0
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
        spec = _two_methods(study)
        spec["dependencies"] = {"numpy": importlib.metadata.version("numpy")}  # a pin both hosts satisfy
        spec["registry"] = {**spec["registry"], "path": str(tmp_path / "exposure-b.sqlite")}
        methods = [m["id"] for m in spec["domain"]["params"]["methods"]]
        scratch = tmp_path / "scratch"
        bundle_root = scratch / "bundles" / "q-a"
        manifest = MIOMethodBundle(spec, bundle_root).build()
        plan_sha = json.loads((bundle_root / "plan.json").read_text())["sha256"]
        receipts = {}

        def documents(_spec, quarter, arm, recorded_dir, asof):
            registry, plan, request = _init(spec)
            ran = run_document(_close_document(tmp_path, "recorded", spec, plan, request, recorded=recorded_dir),
                               asof="2019-02-09", journal=False)
            art = _receipt(ran, None)
            receipts["art"] = art
            return {"ok": ran.state == "ran" and art["complete"], "run_dir": str(ran.run_dir),
                    "recorded_from": sorted(art["audit"]["recorded_from"])}

        macs = SshHost("mac", 2, "mac", root, MAC_PYTHON, lambda unit: f"{root}/bundles/{unit['quarter']}-{unit['arm']}",
                       f"{root}/mac_worker.sh", "e2e-token", local_bundle_for=lambda unit: bundle_root)
        assert macs.online() and macs.available_gb() > 8
        wsl = LocalHost("wsl", 0, sys.executable, lambda unit: bundle_root, scratch / "work", scratch / "out")
        plan = {"quarters": [{"id": "q", "asof": "2019-02-09", "arms": [
            {"id": "a", "methods": methods, "bundle_sha256": manifest["sha256"], "plan_sha256": plan_sha, "spec": {}}]}]}
        c = Coordinator(tmp_path / "queue", plan, [macs, wsl], documents, _probe(wsl=60.),
                        {"unit_ram_gb": 4, "document_ram_gb": 4, "stale_seconds": 1800, "mac_reserve_gb": 6},
                        parity={"unit": f"q/a/{methods[0]}"})
        deadline = time.time() + 900
        while c.tick():
            assert time.time() < deadline, "the queue did not finish"
            time.sleep(1)
        c.join_documents()
        assert c.queue.arm("q", "a")["status"] == "closed"
        verdict = json.loads((tmp_path / "queue" / "parity.json").read_text())
        assert verdict["passed"] is True, verdict["reason"]
        rows = [u for u in c.queue.units() if u["role"] == "primary"]
        assert [u["status"] for u in rows] == ["replayed"] * len(methods)
        assert all(not any("stale" in h.get("note", "") for h in u["history"]) for u in rows), "no false stale kill"
        assert {u["host"] for u in rows} == {"mac"}
        assert sorted(receipts["art"]["audit"]["recorded_from"]) == sorted(methods)
        assert all(v["host"]["label"] == "mac" for v in
                   (json.loads((c.recorded_dir("q", "a") / f"{m}.json").read_text()) for m in methods))
        stamps = mac(f"cat {root}/out/*.progress").stdout.split("\n")
        assert stamps and all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", line) for line in stamps if line)
        assert mac("ls /Users/gibsonrussell/.cache 2>/dev/null | grep -c -i -E 'sqlite-parquet|mio'").stdout.strip() in ("0", "")
    finally:
        mac(f"hdiutil detach {device} -force")


# ---- skeptic round 2 (ADR-0297): parity gate, exhaustion, fallbacks, orphans, registry order, C: stop ----------------
def _one_arm_plan(methods=("m",), quarters=("q",), registry=None, arm="a"):
    arms = lambda: [{"id": arm, "methods": list(methods), "bundle_sha256": BUNDLE, "plan_sha256": PLAN,
                     "spec": {}, **({"registry": registry} if registry else {})}]
    return {"quarters": [{"id": q, "asof": "2026-01-01", "arms": arms()} for q in quarters]}


def test_a_mac_host_without_a_parity_unit_is_refused_at_construction(tmp_path):
    for sub, parity in enumerate((None, {}, {"unit": ""}, {"unit": None}, "q/a/m")):
        with pytest.raises(ValueError, match="parity.unit"):
            Coordinator(tmp_path / f"q{sub}", _one_arm_plan(), [FakeHost("mac", 1), FakeHost("wsl", 1)], Documents(),
                        _probe(), {}, Clock(), parity=parity)
    Coordinator(tmp_path / "ok", _one_arm_plan(), [FakeHost("wsl", 1)], Documents(), _probe(), {}, Clock())  # no Mac: fine


def test_the_default_attempt_cap_is_finite_and_a_partial_cap_never_means_unlimited(tmp_path):
    c = _coordinator(tmp_path / "a", [FakeHost("wsl", 1)])
    assert c.caps == {"mac": 2, "wsl": 2}
    c = _coordinator(tmp_path / "b", [FakeHost("wsl", 1)], limits={"max_attempts": {"mac": 5}})
    assert c.caps == {"mac": 5, "wsl": 2}
    with pytest.raises(ValueError, match="max_attempts"):
        _coordinator(tmp_path / "c", [FakeHost("wsl", 1)], limits={"max_attempts": {"wsl": 0}})


def test_a_unit_failing_twice_on_the_only_usable_host_is_rejected_and_the_arm_settles(tmp_path):
    """wsl_slots 0, caps mac 2 / wsl 2: the unit used to stay pending forever because wsl had no cap use to exhaust."""
    mac, wsl, docs = FakeHost("mac", 1), FakeHost("wsl", 0), Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs, plan=_one_arm_plan(), limits={"max_attempts": {"mac": 2, "wsl": 2}})
    for attempt in (1, 2):
        c.tick()
        assert c.queue.unit("q/a/m")["attempt"] == attempt and c.queue.unit("q/a/m")["host"] == "mac"
        mac.dead.add(("q/a/m", attempt))
    _tick(c)
    row = c.queue.unit("q/a/m")
    assert row["status"] == "solved_local" and docs.calls and c.queue.arm("q", "a")["status"] == "closed", row["history"]


def test_a_host_with_no_slots_is_not_usable_so_the_document_solves_locally(tmp_path):
    mac, wsl, docs = FakeHost("mac", 0), FakeHost("wsl", 0), Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs, plan=_one_arm_plan(("m1", "m2")))
    assert not _tick(c) or True
    c.join_documents()
    c.tick()
    assert not mac.spawned and not wsl.spawned
    assert {u["status"] for u in c.queue.units() if u["role"] == "primary"} == {"solved_local"}
    assert c.queue.arm("q", "a")["status"] == "closed"


def test_repeated_spawn_and_stage_failures_end_in_a_local_solve_not_a_hang(tmp_path):
    wsl, docs = FakeHost("wsl", 1), Documents()

    def broken(unit, attempt):
        raise OSError("disk full")

    wsl.spawn = broken
    c = _coordinator(tmp_path, [wsl], docs, plan=_one_arm_plan())
    for _ in range(8):
        c.tick()
        c.join_documents()
    row = c.queue.unit("q/a/m")
    # a failed spawn is refunded (up to max_refunds = 3), so 2 + 3 leases are spent before the host is exhausted
    assert row["status"] == "solved_local" and sum(h["event"] == "leased" for h in row["history"]) == 5, row["history"]
    assert any("spawn failed: OSError" in h.get("note", "") for h in row["history"])


def test_refunds_are_capped_so_a_resource_stop_loop_cannot_hide_a_dead_unit(tmp_path):
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], limits={"max_refunds": 2, "max_attempts": {"wsl": 3}})
    c.queue.lease("2022q1/rules/m1", "wsl")
    row = c.queue.unit("2022q1/rules/m1")
    for n in range(1, 6):
        if n > 1:
            c.queue.lease("2022q1/rules/m1", "wsl")
        c.queue.transition("2022q1/rules/m1", "pending", n, refund=True)
    row = c.queue.unit("2022q1/rules/m1")
    assert sum(1 for h in row["history"] if h.get("refund")) == 5
    assert c._attempts_on(row, "wsl") == 5 - 2, "only max_refunds refunds are forgiven"


def _parity_plan():
    return {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": ["m1", "m2", "m3"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": {}}]}]}


def _failed_parity_first_pass(tmp_path, mac, wsl, docs=None, **kw):
    c = _coordinator(tmp_path, [mac, wsl], docs, plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.), **kw)
    c.tick()
    mac.finish("q/a/m1", 1, objectives={"2022-01-03": 11.})
    wsl.finish("q/a/m1@parity", 1, objectives={"2022-01-03": 99.})
    c.tick()
    assert c.parity_state() == "failed"
    return c


def test_failed_parity_with_no_wsl_slots_rejects_the_units_the_document_solves_and_status_says_why(tmp_path):
    """It used to stall the whole queue silently: the Mac was disabled and WSL had no slot."""
    mac, wsl, docs = FakeHost("mac", 4), FakeHost("wsl", 0), Documents()
    c = _failed_parity_first_pass(tmp_path, mac, wsl, docs)
    _tick(c)
    c.join_documents()
    c.tick()
    assert c.queue.arm("q", "a")["status"] == "closed" and docs.calls
    assert {u["status"] for u in c.queue.units() if u["role"] == "primary"} <= {"solved_local", "replayed"}
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert status["parity"] == "failed" and status["parity_reason"] == "objective"
    assert "wsl_slots is 0" in status["waiting"]["parity"] and "documents solve locally" in status["waiting"]["parity"]


def test_failed_parity_with_wsl_slots_moves_the_units_to_wsl(tmp_path):
    mac, wsl = FakeHost("mac", 4), FakeHost("wsl", 2)
    c = _failed_parity_first_pass(tmp_path, mac, wsl)
    c.tick()
    assert {u["host"] for u in c.queue.units(status="leased")} == {"wsl"} and not mac.spawned[1:]
    assert "units run on WSL slots" in c.reasons["parity"]


def test_parity_pending_past_the_grace_period_writes_no_verdict_kills_nothing_and_releases_only_the_others(tmp_path):
    """Infrastructure faults are not a comparison: no final verdict, no kill; the real comparison can still happen."""
    mac, wsl, clock, docs = FakeHost("mac", 1, online=False), FakeHost("wsl", 0), Clock(), Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs, plan=_parity_plan(), parity={"unit": "q/a/m1"}, clock=clock,
                     limits={"parity_grace_seconds": 1000, "stale_seconds": 10 ** 6})
    c.tick()
    assert c.parity_state() == "pending" and "not placed" in c.reasons["parity"] and "offline" in c.reasons["place"]
    assert [s[0] for s in wsl.spawned] == ["q/a/m1@parity"], "the twin starts even while the Mac is away"
    clock.now += 999
    c.tick()
    assert not c._released
    clock.now += 2
    c.tick()
    c.join_documents()
    c.tick()
    assert c._released and "no verdict" in c.reasons["parity"]
    assert not (tmp_path / "q" / "parity.json").exists(), "only a comparison writes a verdict"
    assert c.parity_state() == "pending" and not wsl.killed and not mac.killed, "nothing healthy is killed for waiting"
    others = {u["method"]: u["status"] for u in c.queue.units() if u["role"] == "primary"}
    assert others["m2"] == others["m3"] == "rejected", "released to WSL: no slots, so the document will solve them"
    assert c.queue.arm("q", "a")["status"] == "waiting", "the parity unit's arm waits for the comparison"
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert status["parity"] == "pending" and status["parity_reason"] is None
    mac.is_online = True  # the Mac comes back: the pair still completes and compares for real
    c.tick()
    assert c.queue.unit("q/a/m1")["host"] == "mac" and not [u for u in mac.spawned if u[0] != "q/a/m1"]
    mac.finish("q/a/m1", 1)
    wsl.finish("q/a/m1@parity", 1)
    c.tick()
    c.tick()
    assert c.parity_state() == "passed" and json.loads((tmp_path / "q" / "parity.json").read_text())["reason"] == "ok"


def test_parity_units_that_end_without_a_result_write_no_verdict(tmp_path):
    """Two failed spawns used to produce a final 'unverified' with nothing compared."""
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)

    def broken(unit, attempt):
        raise OSError("ssh down")

    mac.spawn = broken
    c = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    for _ in range(10):
        c.tick()
        c.join_spawns()
    assert c.queue.unit("q/a/m1")["status"] == "solved_local", "attempts spent, then its document solved it"
    assert not (tmp_path / "q" / "parity.json").exists() and c.parity_state() == "pending"
    assert "no comparison" in c.reasons["parity"] and "no verdict" in c.reasons["parity"]
    assert c._released and not [s for s in wsl.spawned if s[0] == "q/a/m1@parity" and s[1] > 1]


def test_a_failed_disk_write_of_a_passing_verdict_is_repaired_by_the_next_pass(tmp_path, monkeypatch):
    """The verdict is written AFTER the Mac file is published: a failure there leaves no stuck, verdict-less arm."""
    import mio_parallel_queue
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    c.tick()
    mac.finish("q/a/m1", 1)
    wsl.finish("q/a/m1@parity", 1)
    real = mio_parallel_queue.atomic_write

    def full_disk(path, raw):
        if Path(path).name == "parity.json":
            raise OSError(28, "No space left on device")
        return real(path, raw)

    monkeypatch.setattr(mio_parallel_queue, "atomic_write", full_disk)
    with pytest.raises(OSError):
        c.tick()
    assert not (tmp_path / "q" / "parity.json").exists()
    monkeypatch.undo()
    restarted = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    restarted.tick()
    assert restarted.parity_state() == "passed"
    states = {u["unit_id"]: u["status"] for u in restarted.queue.units() if u["unit_id"].startswith("q/a/m1")}
    assert states == {"q/a/m1": "verified", "q/a/m1@parity": "verified"}
    assert (restarted.recorded_dir("q", "a") / "m1.json").exists()


def test_a_failed_comparison_is_final_and_a_crash_before_rejecting_the_units_is_repaired(tmp_path, monkeypatch):
    # The repair tick also starts the arm's document on a thread; the gate holds that thread so it cannot move
    # the units on to solved_local before the assertions read them (this test used to fail one run in six).
    gate = threading.Event()
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path, [mac, wsl], Documents(gate=gate), plan=_parity_plan(), parity={"unit": "q/a/m1"},
                     probe=_probe(wsl=80.))
    c.tick()
    mac.finish("q/a/m1", 1, objectives={"2022-01-03": 11.})
    wsl.finish("q/a/m1@parity", 1, objectives={"2022-01-03": 99.})
    monkeypatch.setattr(Coordinator, "_settle_failed_parity", lambda self: None)  # the crash after the verdict
    c.tick()
    c.tick()
    assert json.loads((tmp_path / "q" / "parity.json").read_text())["reason"] == "objective"
    assert c.queue.unit("q/a/m1")["status"] == "returned"
    monkeypatch.undo()
    c.tick()
    assert c.queue.unit("q/a/m1")["status"] == "rejected" and c.queue.unit("q/a/m1@parity")["status"] == "rejected"
    assert not (c.recorded_dir("q", "a") / "m1.json").exists(), "a failed pair never publishes the Mac file"
    gate.set()
    c.join_documents(10)  # no document thread outlives the test


def test_a_stale_parity_verdict_is_not_trusted(tmp_path):
    base = {"passed": True, "reason": "ok", "unit": "q/a/m1", "thresholds_sha256": Similarity.thresholds_sha256(),
            "bundle_sha256": BUNDLE, "hosts": {}, "sha256": {}}
    for field, value in (("unit", "q/a/m2"), ("thresholds_sha256", "0" * 64), ("bundle_sha256", "z" * 64)):
        root = tmp_path / field
        root.mkdir()
        (root / "parity.json").write_text(json.dumps({**base, field: value}))
        c = Coordinator(root, _parity_plan(), [FakeHost("mac", 1), FakeHost("wsl", 1)], Documents(), _probe(), {},
                        Clock(), parity={"unit": "q/a/m1"})
        assert c.parity_state() == "failed" and c.parity_reason() == "stale_verdict", field
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "parity.json").write_text(json.dumps(base))
    c = Coordinator(ok, _parity_plan(), [FakeHost("mac", 1), FakeHost("wsl", 1)], Documents(), _probe(), {}, Clock(),
                    parity={"unit": "q/a/m1"})
    assert c.parity_state() == "passed"


def test_the_parity_gate_truth_table_and_the_twin_never_runs_on_the_mac(tmp_path):
    """Mutations fits_parity_open / twin_not_wsl: every (role, host, state) cell is pinned."""
    mac, wsl = FakeHost("mac", 4), FakeHost("wsl", 4)
    c = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"})
    primary, twin, other = (c.queue.unit("q/a/m1"), c.queue.unit("q/a/m1@parity"), c.queue.unit("q/a/m2"))
    table = {  # (unit, host, state) -> fits
        ("primary", "mac", "pending"): True, ("primary", "wsl", "pending"): False,
        ("twin", "mac", "pending"): False, ("twin", "wsl", "pending"): True,
        ("other", "mac", "pending"): False, ("other", "wsl", "pending"): False,
        ("primary", "mac", "passed"): True, ("primary", "wsl", "passed"): True,
        ("twin", "mac", "passed"): False, ("twin", "wsl", "passed"): True,
        ("other", "mac", "passed"): True, ("other", "wsl", "passed"): True,
        ("other", "mac", "failed"): True, ("other", "wsl", "failed"): True}
    units, hosts = {"primary": primary, "twin": twin, "other": other}, {"mac": mac, "wsl": wsl}
    for (u, h, state), want in table.items():
        assert c._fits(units[u], hosts[h], state) is want, (u, h, state)
    # end to end, with Mac listed first and every slot open, the Mac never receives the twin
    c.tick()
    assert [s[0] for s in mac.spawned] == ["q/a/m1"] and [s[0] for s in wsl.spawned] == ["q/a/m1@parity"]


def test_a_failed_parity_leaves_the_mac_ineligible_for_every_unit(tmp_path):
    c = _failed_parity_first_pass(tmp_path, FakeHost("mac", 4), FakeHost("wsl", 2))
    assert all(h.name == "wsl" for h in c._eligible(c.queue.unit("q/a/m2")))


def test_retired_quarters_in_the_queue_database_never_crash_a_restart(tmp_path):
    """queue.sqlite held a quarter no longer in the plan: _priority raised ValueError, arm_specs[...] KeyError."""
    queue = UnitQueue(tmp_path / "q" / "queue.sqlite")
    queue.add("old/a/m", "old", "a", "m")
    queue.add("old/b/m", "old", "b", "m")
    queue.lease("old/b/m", "wsl", pid=7, pid_start="x")
    queue.add("q/a/gone", "q", "a", "gone")  # a method the plan no longer lists, on a planned arm
    queue.set_arm("old", "a", "running")
    wsl, docs = FakeHost("wsl", 2), Documents()
    c = _coordinator(tmp_path, [wsl], docs, plan=_one_arm_plan())
    c.tick()  # must not raise
    wsl.finish("q/a/m", 1)
    c.tick()
    c.join_documents()
    c.tick()
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert set(status["orphans"]) == {"old/a", "old/b"} and "not in the current plan" in status["waiting"]["orphans"]
    assert {u["unit_id"] for u in status["units"]} == {"q/a/m"}, "orphan units never appear as work"
    assert ("old/b/m", 1) in wsl.killed and c.queue.unit("old/b/m")["status"] == "pending", "its worker is stopped"
    assert [call[:2] for call in docs.calls] == [("q", "a")], "only the planned arm ran its document"
    assert c.queue.arm("q", "a")["status"] == "closed" and not c.tick(), "orphans never keep the coordinator running"
    assert c.queue.arm("old", "a")["status"] == "running", "the orphan arm is left as it is"
    assert c.queue.unit("q/a/gone")["status"] == "pending", "an unplanned method of a planned arm is not work"


# registry order -----------------------------------------------------------------------------------------------
def _ordered_docs():
    order, gate = [], {}

    def run(spec, quarter, arm, recorded, asof):
        order.append((quarter, arm, "start"))
        if (quarter, arm) in gate:
            gate[(quarter, arm)].wait(10)
        order.append((quarter, arm, "end"))
        return {"ok": True, "complete": True, "run_dir": f"/runs/{quarter}-{arm}", "recorded_from": []}
    return order, gate, run


def test_documents_of_one_registry_run_one_at_a_time_in_plan_order_even_if_later_units_finish_first(tmp_path):
    order, gate, run = _ordered_docs()
    gate[("q1", "a")] = threading.Event()
    wsl = FakeHost("wsl", 4)
    c = _coordinator(tmp_path, [wsl], run, plan=_one_arm_plan(quarters=("q1", "q2", "q3"), registry="reg"),
                     probe=_probe(wsl=80.))
    c.tick()
    for q in ("q3", "q2"):  # the LATER quarters settle first
        wsl.finish(f"{q}/a/m", 1)
    c.tick()
    assert order == [] and "waits for q1/a" in c.reasons["q2/a"] and "waits for q1/a" in c.reasons["q3/a"]
    wsl.finish("q1/a/m", 1)
    c.tick()
    time.sleep(0.2)
    c.tick()
    assert order == [("q1", "a", "start")], "q2 and q3 must not start while q1 holds the registry"
    assert "waits for q1/a" in c.reasons["q2/a"]
    gate[("q1", "a")].set()
    for _ in range(4):
        c.join_documents()
        c.tick()
    c.join_documents()
    assert [o for o in order if o[2] == "start"] == [("q1", "a", "start"), ("q2", "a", "start"), ("q3", "a", "start")]
    assert order.index(("q1", "a", "end")) < order.index(("q2", "a", "start")) < order.index(("q2", "a", "end")) \
        < order.index(("q3", "a", "start")), "never two active documents on one registry"


def test_documents_of_different_registries_overlap(tmp_path):
    order, gate, run = _ordered_docs()
    gate[("q", "a")], gate[("q", "b")] = threading.Event(), threading.Event()
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}, "registry": "ra"},
        {"id": "b", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}, "registry": "rb"}]}]}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], run, plan=plan, probe=_probe(wsl=80.))
    c.tick()
    time.sleep(0.3)
    assert {o[:2] for o in order} == {("q", "a"), ("q", "b")} and not any(o[2] == "end" for o in order), \
        "both registries run at once"
    for g in gate.values():
        g.set()
    c.join_documents()


def test_a_failed_document_stops_its_registry_chain_with_a_status_line_and_keeps_the_exposure(tmp_path):
    calls = []

    def run(spec, quarter, arm, recorded, asof):
        calls.append((quarter, arm))
        if quarter == "q1":
            return {"ok": False, "stage": "run", "entry": {"rc": 1, "run_dir": "/runs/q1-failed", "ok": False}}
        return {"ok": True, "complete": True, "run_dir": "/runs/x", "recorded_from": []}
    plan = {"quarters": [{"id": q, "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}, "registry": "ra"},
        {"id": "b", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}, "registry": "rb"}]}
        for q in ("q1", "q2")]}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], run, plan=plan, probe=_probe(wsl=80.))
    for _ in range(6):
        c.tick()
        c.join_documents()
    arm = c.queue.arm("q1", "a")
    assert arm["status"] == "failed" and arm["run_dir"] == "/runs/q1-failed", "the failed exposure is retained"
    assert ("q2", "a") not in calls, "the chain stopped: no document on a registry with an unresolved claim"
    assert c.queue.arm("q1", "b")["status"] == "failed" and c.queue.arm("q2", "b")["status"] == "waiting"
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    why = {a["arm_key"]: a["why"] for a in status["arms"]}
    assert "registry chain stopped: q1/a failed (run; exit 1)" in why["q2/a"] and why["q1/a"] == "run; exit 1"
    assert not c.tick(), "nothing can move: the coordinator ends instead of idling forever"


def test_a_document_killed_by_the_disk_guard_is_reported_as_such(tmp_path):
    def run(spec, quarter, arm, recorded, asof):
        return {"ok": False, "stage": "run", "entry": {"rc": -15, "run_dir": None, "ok": False}}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], run, plan=_one_arm_plan(methods=(), quarters=("q1", "q2"), registry="r"),
                     probe=_probe(wsl=80.))
    c.tick()
    c.join_documents()
    c.tick()
    why = {a["arm_key"]: a["why"] for a in json.loads((tmp_path / "q" / "status.json").read_text())["arms"]}
    assert "killed by signal 15" in why["q1/a"] and "disk_guard.sh" in why["q1/a"] and "claim may still be active" in why["q1/a"]
    assert "registry chain stopped" in why["q2/a"]


def test_reopen_lets_a_failed_arm_run_again_after_the_owner_disposed_of_its_claim(tmp_path):
    root = tmp_path / "queue"
    runtime = _stub_runtime(tmp_path)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root), "python": sys.executable, "runtime": str(runtime),
                                  "quarters": []}))
    queue = UnitQueue(root / "queue.sqlite")
    queue.add_arm("q", "a")
    assert main(["reopen", str(config), "q/a"]) == 1, "only a failed arm is reopened"
    queue.set_arm("q", "a", "failed", None, {"stage": "run"})
    assert main(["reopen", str(config), "q/a"]) == 0 and queue.arm("q", "a")["status"] == "waiting"


def test_a_restart_after_a_killed_document_relaunches_through_the_runner_and_a_refusal_fails_the_arm(tmp_path):
    """The arm was `running` when the machine died; the runner finds no process and no run, relaunches, and a still-active
    claim makes the registry refuse: the arm fails with its stage in status instead of hanging."""
    plan = _one_arm_plan(methods=())
    UnitQueue(tmp_path / "q" / "queue.sqlite").add_arm("q", "a")
    UnitQueue(tmp_path / "q" / "queue.sqlite").set_arm("q", "a", "running")

    def run(spec, quarter, arm, recorded, asof):
        return {"ok": False, "stage": "run", "entry": {"rc": 1, "run_dir": None, "ok": False}}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], run, plan=plan, probe=_probe(wsl=80.))
    c.tick()
    c.join_documents()
    c.tick()
    assert c.queue.arm("q", "a")["status"] == "failed"
    assert json.loads((tmp_path / "q" / "status.json").read_text())["arms"][0]["why"] == "run; exit 1"


# placement and C: ---------------------------------------------------------------------------------------------------
def test_place_never_leases_units_of_an_arm_whose_document_is_running(tmp_path):
    """A restart can find an arm `running` with pending units; they are the document's, not the queue's."""
    wsl, gate = FakeHost("wsl", 3), threading.Event()
    docs = Documents(gate=gate)
    UnitQueue(tmp_path / "q" / "queue.sqlite").add_arm("q", "a")
    UnitQueue(tmp_path / "q" / "queue.sqlite").set_arm("q", "a", "running")
    c = _coordinator(tmp_path, [wsl], docs, plan=_one_arm_plan(("m1", "m2")), probe=_probe(wsl=80.))
    c.tick()
    c.place()
    assert not wsl.spawned and c.queue.unit("q/a/m2")["status"] == "pending", \
        "a running arm's pending unit is the document's, not the queue's"
    gate.set()
    c.join_documents()


def test_the_c_stop_fetches_a_finished_unit_instead_of_killing_it_and_leaves_documents_alone(tmp_path):
    wsl, state, docs = FakeHost("wsl", 2), {"c": 20.}, Documents()
    c = _coordinator(tmp_path, [wsl], docs, probe=lambda: {"wsl_available_gb": 60., "windows_available_gb": 40.,
                                                          "c_free_gb": state["c"]}, plan=_one_arm_plan(("m1", "m2")))
    c.tick()
    wsl.finish("q/a/m1", 1)
    state["c"] = 2.0
    c.stop_for_disk()
    assert c.queue.unit("q/a/m1")["status"] == "verified" and ("q/a/m1", 1) not in wsl.killed, "finished: fetched"
    assert ("q/a/m2", 1) in wsl.killed and c.queue.unit("q/a/m2")["status"] == "pending"
    assert not docs.calls


def test_an_unreadable_c_drive_is_reported_and_stops_all_new_placement_on_every_host(tmp_path):
    mac, wsl = FakeHost("mac", 2), FakeHost("wsl", 2)
    c = _coordinator(tmp_path, [mac, wsl], probe=lambda: {"wsl_available_gb": 60., "windows_available_gb": 40., "c_free_gb": None})
    c.tick()
    assert not mac.spawned and not wsl.spawned and not c.queue.units(status="leased")
    assert "unreadable" in c.reasons["disk"]
    assert "unreadable" in json.loads((tmp_path / "q" / "status.json").read_text())["waiting"]["disk"]


class SlowStageHost(FakeHost):
    slow_stage = True

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.release, self.entered = threading.Event(), threading.Event()

    def stage(self, unit):
        self.entered.set()
        self.release.wait(20)
        super().stage(unit)


def test_a_slow_bundle_transfer_runs_on_a_thread_and_never_freezes_the_tick_or_the_c_stop(tmp_path):
    mac, wsl, state = SlowStageHost("mac", 1), FakeHost("wsl", 2), {"c": 20.}
    c = _coordinator(tmp_path, [mac, wsl], probe=lambda: {"wsl_available_gb": 60., "windows_available_gb": 40.,
                                                          "c_free_gb": state["c"]})
    started = time.time()
    c.tick()
    assert mac.entered.wait(5) and time.time() - started < 5, "tick returned while the transfer is still running"
    on_mac = c.queue.units(status="leased")
    assert any(u["host"] == "mac" for u in on_mac) and not mac.spawned, "leased, not yet started"
    state["c"] = 2.0  # while the transfer blocks, the disk stop still acts on WSL workers
    c.tick()
    assert wsl.killed and not mac.spawned
    assert c.queue.unit(next(u["unit_id"] for u in on_mac if u["host"] == "mac"))["status"] == "leased", \
        "a unit being staged is not mistaken for a dead worker"
    mac.release.set()
    c.join_spawns()
    unit = next(u for u in on_mac if u["host"] == "mac")
    assert mac.spawned and c.queue.unit(unit["unit_id"])["pid"] == mac.spawned[0][2]


def test_a_failed_threaded_stage_requeues_the_unit(tmp_path):
    mac = SlowStageHost("mac", 1)
    mac.release.set()
    mac.spawn = lambda unit, attempt: (_ for _ in ()).throw(RuntimeError("no space"))
    c = _coordinator(tmp_path, [mac], plan=_one_arm_plan(("m1", "m2")))
    c.tick()
    c.join_spawns()
    row = c.queue.unit("q/a/m1")
    assert row["status"] == "pending" and "spawn failed: RuntimeError" in row["history"][-1]["note"]


def test_a_mac_worker_requeued_while_offline_is_killed_once_the_mac_answers_again_even_after_a_restart(tmp_path):
    mac, clock = FakeHost("mac", 1), Clock()
    plan = _one_arm_plan(("m1", "m2"))
    c = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], clock=clock, plan=plan)
    c.tick()
    unit = c.queue.units(status="leased")[0]
    mac.is_online = False
    clock.now += 1801
    c.tick()
    assert c.queue.unit(unit["unit_id"])["status"] == "pending" and not mac.killed
    again = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], clock=clock, plan=plan)  # the coordinator restarts meanwhile
    mac.is_online = True
    again.tick()
    assert (unit["unit_id"], 1) in mac.killed, "the stale worker that kept running is stopped"
    assert json.loads((tmp_path / "q" / "pending_kills.json").read_text()) == []


class PidlessIsDeadHost(FakeHost):
    """SshHost.alive answers False for a lease with no pid (nothing to ask ps about): so does this."""

    def alive(self, unit, attempt, handle):
        return bool(handle.get("pid")) and super().alive(unit, attempt, handle)


def test_a_lease_with_no_pid_after_a_crash_between_spawn_and_handle_record_is_killed_by_spec_path(tmp_path):
    """The orphan of attempt 1 may still run on the Mac RAM disk: it must be killed, not just requeued."""
    mac, plan = PidlessIsDeadHost("mac", 1), _one_arm_plan(("m1", "m2"))
    first = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], plan=plan)
    first.queue.lease("q/a/m1", "mac")  # the coordinator died after the spawn, before the pid was recorded
    assert first.queue.unit("q/a/m1")["pid"] is None
    again = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], plan=plan)
    again.tick()
    assert ("q/a/m1", 1) in mac.killed, "the kill by spec path is issued for the pid-less attempt"
    assert again.queue.unit("q/a/m1")["attempt"] == 2 and again.queue.unit("q/a/m1")["status"] == "leased"
    assert ("q/a/m1", 2, 101) in mac.spawned


def test_a_pidless_lease_whose_worker_already_finished_is_accepted_not_killed(tmp_path):
    mac, plan = PidlessIsDeadHost("mac", 1), _one_arm_plan(("m1",))
    first = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], plan=plan)
    first.queue.lease("q/a/m1", "mac")
    mac.finish("q/a/m1", 1)
    again = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], plan=plan)
    again.tick()
    assert not mac.killed and again.queue.unit("q/a/m1")["status"] not in {"pending", "leased"}


def test_every_pending_unit_has_a_reason_when_nothing_moves(tmp_path):
    mac, wsl = FakeHost("mac", 2, online=False), FakeHost("wsl", 2)
    c = _coordinator(tmp_path, [mac, wsl], probe=_probe(wsl=10.))
    c.tick()
    assert not c.queue.units(status="leased") and "mac: offline" in c.reasons["place"] and "wsl: RAM" in c.reasons["place"]
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert status["moving"] is False and status["waiting"]["place"]


def test_progress_changes_are_recorded_in_the_queue_not_only_in_memory(tmp_path):
    """Mutation progress_never_recorded: the stored last_progress, which a restart reads, must move with the token."""
    mac, clock = FakeHost("mac", 1), Clock()
    c = _coordinator(tmp_path, [mac], clock=clock, plan=_one_arm_plan())
    c.tick()
    before = c.queue.unit("q/a/m")["last_progress"]
    clock.now += 500
    mac.progress_stamp[("q/a/m", 1)] = "t1"
    c.tick()
    after = c.queue.unit("q/a/m")["last_progress"]
    assert after != before and after == stamp(clock.now)
    again = _coordinator(tmp_path, [mac], clock=clock, plan=_one_arm_plan())  # a restart judges staleness from the stored stamp
    clock.now += 400
    again.tick()
    assert not mac.killed, "400 s since the recorded progress is inside the 600 s limit"


def test_publish_never_replaces_an_action_file_or_its_sidecar_even_when_the_file_appears_mid_publish(tmp_path, monkeypatch):
    """Mutation publish_overwrites: the exclusive link, not only the exists() check, must refuse a racing writer."""
    import mio_parallel_queue
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], plan=_one_arm_plan())
    c.tick()
    unit = c.queue.unit("q/a/m")
    target = c.recorded_dir("q", "a")
    real = mio_parallel_queue.atomic_write

    def racing(path, raw):
        real(path, raw)
        if Path(path).name.endswith(".sha256.part"):  # the winner lands after our existence check, before our link
            (target / "m.json").write_bytes(b"winner")
            (target / "m.json.sha256").write_bytes(b"winner side")

    monkeypatch.setattr(mio_parallel_queue, "atomic_write", racing)
    raw, side = _body("m", 1)
    assert c._accept(unit, 1, raw, side) is False
    assert (target / "m.json").read_bytes() == b"winner" and (target / "m.json.sha256").read_bytes() == b"winner side"
    assert c.queue.unit("q/a/m")["status"] == "superseded"
    assert not [p for p in target.iterdir() if p.name.endswith(".part")]
    assert oct(target.stat().st_mode & 0o777) == "0o700" and oct((tmp_path / "q" / "superseded").stat().st_mode & 0o777) == "0o700"


def test_a_sidecar_left_by_a_crash_is_cleared_and_a_republish_of_the_same_file_is_idempotent(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], plan=_one_arm_plan())
    c.tick()
    target = c.recorded_dir("q", "a")
    target.mkdir(parents=True)
    (target / "m.json.sha256").write_text("left by a crash")
    raw, side = _body("m", 1)
    assert c._accept(c.queue.unit("q/a/m"), 1, raw, side) is True, "this coordinator is the only writer: the stale claim goes"
    assert (target / "m.json").read_bytes() == raw and (target / "m.json.sha256").read_text().split()[0] == hashlib.sha256(raw).hexdigest()
    assert c.queue.unit("q/a/m")["status"] == "verified"


def test_publishing_the_same_bytes_again_after_a_crash_before_the_state_change_is_idempotent(tmp_path):
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], plan=_one_arm_plan())
    c.tick()
    raw, side = _body("m", 1)
    target = c.recorded_dir("q", "a")
    target.mkdir(parents=True)
    (target / "m.json").write_bytes(raw)  # linked, then the process died before the transition
    (target / "m.json.sha256").write_text(side)
    unit = c.queue.unit("q/a/m")
    assert c._publish(unit, 1, raw, hashlib.sha256(raw).hexdigest(), {"seconds": 1, "timeouts": 0}) is True
    assert c.queue.unit("q/a/m")["status"] == "verified"
    other, _ = _body("m", 2, seconds=5.)
    assert (target / "m.json").read_bytes() == raw and other != raw


def test_worker_logs_and_output_directories_are_owner_only(tmp_path):
    bundle = _stub_bundle(tmp_path)
    host = LocalHost("wsl", 1, sys.executable, lambda unit: bundle, tmp_path / "w", tmp_path / "o")
    unit = _unit()
    handle = host.spawn(unit, 1)
    deadline = time.time() + 30
    while host.alive(unit, 1, handle) and time.time() < deadline:
        time.sleep(0.1)
    assert oct((tmp_path / "o").stat().st_mode & 0o777) == "0o700"
    log = tmp_path / "o" / "q__a__m.1.json.log"
    assert log.exists() and oct(log.stat().st_mode & 0o777) == "0o600"


def test_ticker_lock_distinguishes_a_refusal_from_a_check_that_could_not_run(tmp_path):
    from mio_parallel_queue import ticker_lock_ok
    runtime = _stub_runtime(tmp_path)
    good, bad = _doc(tmp_path, "good", tmp_path / "r1"), _doc(tmp_path, "bad", tmp_path / "r2")
    assert ticker_lock_ok(sys.executable, str(runtime), good) is True
    assert ticker_lock_ok(sys.executable, str(runtime), bad) is False
    with pytest.raises(RuntimeError, match="could not run"):
        ticker_lock_ok(str(tmp_path / "no-python"), str(runtime), good)
    crash = tmp_path / "crash"
    crash.mkdir()
    (crash / "children" / "index_options" / "index_options").mkdir(parents=True)
    (crash / "children" / "index_options" / "index_options" / "ticker_lock.py").write_text("raise ImportError('x')\n")
    with pytest.raises(RuntimeError, match="exit 1"):
        ticker_lock_ok(sys.executable, str(crash), good)
    runner = DocumentRunner(str(tmp_path / "no-python"), str(runtime), tmp_path / "gen", tmp_path / "sealed", poll=0.01)
    out = runner.run("init-x", good, "2026-01-01")
    assert not out["ok"] and out["refused"] == "ticker_lock_unavailable", "a crash is not blamed on the ticker list"


def test_a_malformed_ready_marker_skips_the_quarter_instead_of_crashing(tmp_path):
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir()
    for name, text in (("q1", '{"asof": "2026-01-01", "runs": null}'), ("q2", "[1, 2]"), ("q3", '{"asof": 1, "runs": [3]}')):
        (root / f"READY-{name}.json").write_text(text)
    plan = _load_plan({"queue_root": str(root), "python": sys.executable, "runtime": str(runtime), "quarters": ["q1", "q2", "q3"]})
    assert plan["quarters"] == [] and set(plan["skipped"]) == {"q1", "q2", "q3"}
    assert "TypeError" in plan["skipped"]["q1"]


def test_run_arm_never_abandons_a_packet_because_a_worker_file_was_in_play(tmp_path, monkeypatch):
    import mio_parallel_queue
    runner = DocumentRunner(sys.executable, str(tmp_path), tmp_path / "gen", tmp_path / "sealed")
    steps = []
    monkeypatch.setattr(runner, "run", lambda step, cfg, asof: steps.append(step) or {
        "ok": True, "run_dir": "/r", "document_hash": "h", "minutes": 0., "step": step})
    monkeypatch.setattr(runner, "recorded_config", lambda *a: tmp_path / "cfg.json")
    for report, abandons in (({"complete": False, "recorded_from": {"m": {}}, "recorded_rejected": 0, "recorded_absent": 0,
                               "methods": {}}, False),
                             ({"complete": False, "recorded_from": {}, "recorded_rejected": 2, "recorded_absent": 0,
                               "methods": {}}, False),
                             ({"complete": False, "recorded_from": {}, "recorded_rejected": 0, "recorded_absent": 0,
                               "methods": {}}, True)):
        steps.clear()
        monkeypatch.setattr(mio_parallel_queue, "recorded_from_report", lambda run_dir, r=report: r)
        closing = []
        monkeypatch.setattr(runner, "closing", lambda *a: closing.append(a[1]) or tmp_path / "x.json")
        out = runner.run_arm({"initialize": "i", "run": "r"}, "q", "a", tmp_path / "rec", "2026-01-01")
        assert ("abandon" in closing) is abandons and (out["ok"] is True) is abandons, (report, out)
        if not abandons:
            assert out["stage"] == "incomplete_with_worker_files"


def test_reserves_in_the_config_can_raise_the_skills_floor_never_lower_it(tmp_path, monkeypatch):
    from mio_parallel_queue import _build
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "compute_state.py").write_text("WSL_RESERVE = 6 * 2**30\nWIN_RESERVE = 2 * 2**30\n"
                                            "def windows_available_cached(max_age=900, refresh=True):\n    return None\n")
    runtime = _stub_runtime(tmp_path)
    base = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(runtime), "quarters": []}
    monkeypatch.setattr(_queue_module, "SKILL_SCRIPTS", str(skill))
    low = _build({**base, "limits": {"wsl_reserve_gb": 1, "windows_reserve_gb": 0.5}})
    assert low.limits["wsl_reserve_gb"] == 6 and low.limits["windows_reserve_gb"] == 2
    high = _build({**base, "queue_root": str(tmp_path / "q2"), "limits": {"wsl_reserve_gb": 9}})
    assert high.limits["wsl_reserve_gb"] == 9


def test_a_stranger_file_that_appears_between_the_sidecar_claim_and_the_file_link_is_never_replaced(tmp_path, monkeypatch):
    """Mutation publish_overwrites (the final link): the claim succeeded, the file then appeared, nothing is replaced."""
    import mio_parallel_queue
    wsl = FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [wsl], plan=_one_arm_plan())
    c.tick()
    target = c.recorded_dir("q", "a")
    real_link = mio_parallel_queue.os.link

    def link(src, dst, *a, **kw):
        real_link(src, dst, *a, **kw)
        if str(dst).endswith(".json.sha256"):
            (target / "m.json").write_bytes(b"stranger")

    monkeypatch.setattr(mio_parallel_queue.os, "link", link)
    raw, side = _body("m", 1)
    assert c._accept(c.queue.unit("q/a/m"), 1, raw, side) is False
    assert (target / "m.json").read_bytes() == b"stranger"
    assert not (target / "m.json.sha256").exists(), "no sidecar vouches for a file this publisher did not write"
    assert c.queue.unit("q/a/m")["status"] == "superseded"


def test_the_worker_script_checks_the_sentinel_content_and_keeps_every_path_under_the_root(tmp_path):
    import os
    script = Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "mac_worker.sh"
    _fake_ssh(tmp_path)  # only for its df shim: the root looks like a mount point
    root = _remote_root(tmp_path, "tok")
    (root / "out").mkdir()
    env = {**os.environ, "PATH": f"{tmp_path / 'shim'}:{os.environ['PATH']}"}

    def run(token, **over):
        spec = {"bundle_root": str(root / "bundles" / "b"), "method": "m", "attempt": 1, "work_dir": str(root / "work" / "u"),
                "out": str(root / "out" / "u.json"), **over}
        path = root / "out" / "u.1.spec.json"
        path.write_text(json.dumps(spec))
        extra = {} if token is None else {"MIO_SENTINEL": token}
        return subprocess.run(["sh", str(script), sys.executable, str(path)], env={**env, **extra}, capture_output=True, text=True)

    for token in ("other", None, ""):
        done = run(token)
        assert done.returncode == 3 and "sentinel" in done.stderr, (token, done.stderr)
    for key, value in (("work_dir", "/tmp/elsewhere"), ("out", "/tmp/elsewhere.json"), ("reuse_root", "/tmp/cache"),
                       ("bundle_root", str(root) + "/../escape")):
        done = run("tok", **{key: value})
        assert done.returncode == 3 and "outside" in done.stderr, (key, done.stderr)
    assert run("tok", reuse_root=str(root / "cache" / "x")).returncode != 3, "paths under the root pass the checks"


def test_sealed_dir_reads_the_mode_back_and_refuses_a_filesystem_where_chmod_does_nothing(tmp_path, monkeypatch, capsys):
    import os
    import mio_parallel_queue
    from mio_parallel_queue import _sealed_dir

    path = _sealed_dir(tmp_path / "a" / "b")
    assert oct(path.stat().st_mode & 0o777) == "0o700"
    monkeypatch.setattr(mio_parallel_queue.os, "chmod", lambda *a, **k: None)  # what a Windows drive in WSL does
    (tmp_path / "drvfs").mkdir()
    os.chmod(tmp_path / "drvfs", 0o755)
    with pytest.raises(RuntimeError, match="WSL ext4"):
        _sealed_dir(tmp_path / "drvfs")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(tmp_path / "drvfs"), "python": sys.executable, "runtime": str(tmp_path),
                                  "quarters": []}))
    assert main(["run", str(config), "--once"]) == 2 and "refused to start" in capsys.readouterr().err
    assert oct(os.umask(0o022)) == "0o77", "main sets umask 077 for every file the coordinator and its children create"


def test_the_runner_keeps_run_roots_sealed_and_outside_the_configured_place(tmp_path):
    runtime = _stub_runtime(tmp_path)
    runner = DocumentRunner(sys.executable, str(runtime), tmp_path / "gen", tmp_path / "q" / "sealed", poll=0.01)
    open_root = tmp_path / "open-place" / "runs"
    outcome = runner.run("run-x", _doc(tmp_path, "good-run", open_root), "2026-01-01")
    assert outcome["ok"] and Path(outcome["run_dir"]).is_relative_to(tmp_path / "q" / "sealed" / "runs")
    assert not open_root.exists(), "the configured run_root is never written: run.log holds MIO_EVENT economics"
    assert oct((tmp_path / "q" / "sealed" / "runs").stat().st_mode & 0o777) == "0o700"
    assert oct(Path(outcome["run_dir"]).parent.stat().st_mode & 0o777) == "0o700"
    base = json.loads((tmp_path / "gen" / "launch" / "run-x.json").read_text())
    assert base["outputs"]["run_root"] == str(Path(outcome["run_dir"]).parent) and base["pipeline"]["study"]["params"] == {"study": {"s": 1}}


def _strict_runtime(tmp_path):
    """The stub pipeline, but like the real driver it refuses a non-empty run directory, and STUB_MODE picks the outcome."""
    runtime = _stub_runtime(tmp_path)
    (runtime / "dskit" / "pipeline" / "__main__.py").write_text(
        "import json, sys, os, pathlib\n"
        "cfg = json.load(open(sys.argv[2]))\n"
        "run = pathlib.Path(cfg['outputs']['run_root']) / 'r1'\n"
        "if run.exists() and any(run.iterdir()):\n"
        "    print('run dir already exists'); sys.exit(2)\n"
        "run.mkdir(parents=True, exist_ok=True)\n"
        "mode = pathlib.Path(os.environ['STUB_MODE_FILE']).read_text().strip() if cfg['name'] == 'good-run' else 'ok'\n"
        "(run / 'marker.txt').write_text('mode ' + mode)\n"
        "if mode == 'fail':\n"
        "    (run / 'result.json').write_text(json.dumps({'state': 'error', 'exit_code': 1}))\n"
        "    sys.exit(1)\n"
        "(run / 'result.json').write_text(json.dumps({'state': 'ran', 'exit_code': 0, 'document_hash': 'h' * 64}))\n"
        "(run / 'carry.json').write_text(json.dumps({'close_packet': {'receipt': {'path': 'receipt.json'}}}))\n"
        "audit = {'recorded_from': {'m1': {'host': {'label': 'mac'}, 'attempt': 1, 'sha256': 'a' * 64, 'bundle_manifest_sha256': 'b' * 64}}}\n"
        "(run / 'receipt.json').write_text(json.dumps({'complete': mode != 'incomplete', 'results': [], 'audit': audit}))\n")
    return runtime


def test_a_failed_run_is_never_adopted_or_deleted_and_the_next_launch_gets_a_fresh_run_root(tmp_path, monkeypatch):
    runtime = _strict_runtime(tmp_path)
    mode = tmp_path / "mode"
    monkeypatch.setenv("STUB_MODE_FILE", str(mode))
    runner = DocumentRunner(sys.executable, str(runtime), tmp_path / "gen", tmp_path / "q" / "sealed", poll=0.01)
    spec = {"initialize": str(_doc(tmp_path, "good-init", tmp_path / "i")), "run": str(_doc(tmp_path, "good-run", tmp_path / "r"))}
    mode.write_text("fail")
    failed = runner.run_arm(spec, "q", "a", tmp_path / "recorded", "2026-01-01")
    assert not failed["ok"] and failed["stage"] == "run" and failed["entry"]["rc"] == 1
    kept = Path(failed["entry"]["run_dir"])
    assert (kept / "marker.txt").read_text() == "mode fail" and runner.latest_run("run-q-a") is None, "a failed run is not a run"
    again = runner.run_arm(spec, "q", "a", tmp_path / "recorded", "2026-01-01")  # what reopen leads to
    assert not again["ok"] and again["entry"]["run_dir"] != str(kept), "it launched into a fresh run root, not the occupied one"
    assert again["entry"]["rc"] == 1 and kept.is_dir() and (kept / "marker.txt").read_text() == "mode fail"
    mode.write_text("ok")
    done = runner.run_arm(spec, "q", "a", tmp_path / "recorded", "2026-01-01")
    assert done["ok"] and done["complete"] and Path(done["run_dir"]).parent.name == "run-q-a.retry2"
    assert (kept / "marker.txt").read_text() == "mode fail", "the failed run directories are untouched"
    assert runner.run_arm(spec, "q", "a", tmp_path / "recorded", "2026-01-01")["run_dir"] == done["run_dir"], \
        "a successful run is adopted, never launched twice"


def test_reopen_after_an_incomplete_run_with_worker_files_launches_a_fresh_run_end_to_end(tmp_path, monkeypatch):
    """stage incomplete_with_worker_files -> reopen retires that run -> a fresh run root launches; nothing is deleted."""
    runtime = _strict_runtime(tmp_path)
    mode = tmp_path / "mode"
    monkeypatch.setenv("STUB_MODE_FILE", str(mode))
    root = tmp_path / "q"
    spec = {"initialize": str(_doc(tmp_path, "good-init", tmp_path / "i")), "run": str(_doc(tmp_path, "good-run", tmp_path / "r"))}
    plan = {"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
        {"id": "a", "methods": [], "bundle_sha256": BUNDLE, "plan_sha256": PLAN, "spec": spec}]}]}

    def coordinator():
        runner = DocumentRunner(sys.executable, str(runtime), root / "generated", root / "sealed", poll=0.01,
                                retired=root / "retired_runs.json")
        return Coordinator(root, plan, [FakeHost("wsl", 1)], runner.run_arm, _probe(wsl=80.), {}, parity=None)

    mode.write_text("incomplete")
    c = coordinator()
    c.tick()
    c.join_documents()
    arm = c.queue.arm("q", "a")
    assert arm["status"] == "failed" and arm["detail"]["stage"] == "incomplete_with_worker_files"
    first_run = Path(arm["run_dir"])
    assert (first_run / "marker.txt").exists()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root), "python": sys.executable, "runtime": str(runtime), "quarters": []}))
    mode.write_text("ok")
    assert main(["reopen", str(config), "q/a"]) == 0
    assert json.loads((root / "retired_runs.json").read_text()) == [str(first_run)]
    c = coordinator()  # the restarted coordinator
    c.tick()
    c.join_documents()
    arm = c.queue.arm("q", "a")
    assert arm["status"] == "closed" and Path(arm["run_dir"]) != first_run and Path(arm["run_dir"]).parent.name == "run-q-a.retry1"
    assert (first_run / "marker.txt").read_text() == "mode incomplete", "the earlier run is kept as it was"


def test_cap_refusals_a_quarter_after_2023q4_a_packet_after_the_cap_and_payloads_that_cannot_be_cut(study, tmp_path, monkeypatch):  # noqa: F811
    import mio_parallel_queue
    from index_options import mio_contract
    from index_options.mio_worker import MIOMethodBundle, PayloadCapError

    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir()
    good_init = _doc(tmp_path, "good-init", tmp_path / "i")

    def run_doc(name, edit=lambda sp: sp):
        spec = edit(json.loads(json.dumps(study)))
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "outputs": {"run_root": str(tmp_path / name)},
                                    "pipeline": {"study": {"uses": "index_options.ledger_studies:MIOPacketStudy",
                                                           "params": {"study": spec}}}}))
        return path

    for quarter in ("2024q1", "2023q4", "2025q2"):
        _ready(root, quarter, run_doc("run-" + quarter, lambda sp, q=quarter: sp["registry"].update(path=str(tmp_path / f"{q}.sqlite")) or sp), good_init)
    config = {"queue_root": str(root), "python": sys.executable, "runtime": str(runtime), "quarters": ["2023q4", "2024q1", "2025q2"]}
    plan = _load_plan(config)
    assert [q["id"] for q in plan["quarters"]] == ["2023q4"]
    assert "only through 2023Q4" in plan["skipped"]["2024q1"] and "only through 2023Q4" in plan["skipped"]["2025q2"]
    # the selected packet itself ends after the cap: refused even under an allowed id
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", "2019-01-02")
    late = _load_plan({**config, "quarters": ["2023q4"]})
    assert not late["quarters"] and "MIO only through 2023Q4" in late["skipped"]["2023q4"]
    monkeypatch.undo()
    # an arm whose payloads cannot be cut still runs, as a plain WSL document, with the reason in the plan
    def refuse(self):
        raise PayloadCapError("payload events.json names a date on or after 2024-01-01")

    monkeypatch.setattr(MIOMethodBundle, "build", refuse)
    uncut = _load_plan({**config, "quarters": ["2023q4"]})
    arm = uncut["quarters"][0]["arms"][0]
    assert arm["methods"] == [] and "pin cut source files" in arm["no_units"] and arm["bundle_root"] is None


def test_registries_are_keyed_by_real_path_so_a_symlink_cannot_open_two_chains(tmp_path):
    from mio_parallel_queue import _registry_of

    real = tmp_path / "exposure.sqlite"
    real.write_text("")
    link = tmp_path / "alias.sqlite"
    link.symlink_to(real)
    paths = []
    for name, target in (("a", real), ("b", link)):
        cfg = tmp_path / f"{name}.json"
        cfg.write_text(json.dumps({"pipeline": {"s": {"uses": "x:MIOPacketStudy",
                                                      "params": {"study": {"registry": {"path": str(target)}}}}}}))
        paths.append(_registry_of(cfg))
    assert paths[0] == paths[1] == str(real.resolve())
    plan = {"quarters": [{"id": "q1", "asof": "x", "arms": [{"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN,
                                                             "spec": {}, "registry": str(real)}]},
                         {"id": "q2", "asof": "x", "arms": [{"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN,
                                                             "spec": {}, "registry": str(link)}]}]}
    c = Coordinator(tmp_path / "q", plan, [FakeHost("wsl", 1)], Documents(), _probe(), {})
    assert c.registry_of["q1/a"] == c.registry_of["q2/a"] and len(c.chains) == 1, "one registry, one chain"


def test_build_refuses_a_certificate_type_other_than_the_mio_one_and_a_queue_root_that_cannot_be_sealed(tmp_path, monkeypatch):
    import mio_parallel_queue
    from mio_parallel_queue import _build

    runtime = _stub_runtime(tmp_path)
    monkeypatch.setattr(mio_parallel_queue, "_load_plan", lambda config: {"skipped": {}, "quarters": [
        {"id": "q", "asof": "2026-01-01", "arms": [{"id": "a", "methods": ["m"], "bundle_sha256": BUNDLE,
                                                    "plan_sha256": PLAN, "spec": {}, "bundle_root": str(tmp_path / "b")}]}]})
    config = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(runtime), "quarters": [], "parity": {"unit": "q/a/m"},
              "mac": {"alias": "mac", "root": "/Volumes/mio-ram", "python": "/p", "worker_script": "/w.sh",
                      "sentinel": "tok", "slots": 1}}
    for bad in (None, "", "gpu-job", "mio-method-unit "):
        with pytest.raises(ValueError, match="certificate"):
            _build({**config, "mac": {**config["mac"], "certificate_job_type": bad}})
    coordinator = _build({**config, "mac": {**config["mac"], "certificate_job_type": "mio-method-unit"}})
    assert coordinator.hosts[0].certificate is not None
    assert coordinator.hosts[1].out_root == tmp_path / "q" / "out", "action files go under the sealed queue root"
    monkeypatch.setattr(mio_parallel_queue.os, "chmod", lambda *a, **k: None)
    (tmp_path / "open").mkdir()
    os.chmod(tmp_path / "open", 0o755)
    with pytest.raises(RuntimeError, match="WSL ext4"):
        _build({**config, "queue_root": str(tmp_path / "open")})


# ssh host: staging, unknown liveness, kills, pruning -------------------------------------------------------------------
def _fake_tar(tmp_path, keep_bytes):
    """A ``tar`` that streams only the first ``keep_bytes`` of the archive (an interrupted transfer) and exits 0."""
    shim = tmp_path / "tarshim"
    shim.mkdir(exist_ok=True)
    real = subprocess.run(["which", "tar"], capture_output=True, text=True).stdout.strip()
    (shim / "tar").write_text(f'#!/bin/sh\n{real} "$@" | head -c {keep_bytes}\n')
    (shim / "tar").chmod(0o755)
    return str(shim)


def test_ssh_stage_never_trusts_a_manifest_beside_a_partial_payload_and_a_retry_copies(tmp_path, monkeypatch):
    import os
    root = _remote_root(tmp_path)
    host = _ssh_host(tmp_path, root)
    bundle = _stub_bundle(tmp_path)
    (bundle / "payload.bin").write_bytes(os.urandom(64 * 1024))
    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file())
    for keep in (4096, 40_000, 3 * 512):  # cut inside the payload, and early
        monkeypatch.setenv("PATH", _fake_tar(tmp_path, keep) + ":" + os.environ["PATH"].split(":", 1)[-1] if False else
                           _fake_tar(tmp_path, keep) + ":" + os.environ["PATH"])
        with pytest.raises(RuntimeError, match="incomplete"):
            host.stage(_unit())
        assert not (root / "bundles" / "q-a").exists(), "the bundle never appears under its name unless whole"
        monkeypatch.undo()
    assert not (root / "bundles" / "q-a" / "manifest.json").exists()
    # an older, partial copy: a manifest with missing payload is NOT accepted by the presence probe any more,
    # because the final path is only ever created by a rename of a verified tree
    assert host.stage(_unit()) and (root / "bundles" / "q-a" / "payload.bin").stat().st_size == 64 * 1024
    assert sum(p.stat().st_size for p in (root / "bundles" / "q-a").rglob("*") if p.is_file()) == size
    assert not list((root / "bundles").glob(".stage*")), "no temporary tree left behind"
    assert host.stage(_unit()), "a whole bundle is found again without a second transfer"


def test_ssh_stage_checks_the_file_count_as_well_as_the_bytes(tmp_path, monkeypatch):
    """A transfer that drops an empty file keeps the byte total: only the file count notices it."""
    import os
    root = _remote_root(tmp_path)
    host = _ssh_host(tmp_path, root)
    bundle = _stub_bundle(tmp_path)
    (bundle / "payload.bin").write_bytes(os.urandom(4096))
    (bundle / "empty.marker").write_bytes(b"")
    shim = tmp_path / "tarshim2"
    shim.mkdir()
    real = subprocess.run(["which", "tar"], capture_output=True, text=True).stdout.strip()
    (shim / "tar").write_text(f'#!/bin/sh\ncase " $* " in *" -cf "*) exec {real} --exclude=empty.marker "$@";; '
                              f'*) exec {real} "$@";; esac\n')
    (shim / "tar").chmod(0o755)
    monkeypatch.setenv("PATH", str(shim) + ":" + os.environ["PATH"])
    with pytest.raises(RuntimeError, match="incomplete"):
        host.stage(_unit())
    assert not (root / "bundles" / "q-a").exists(), "the bundle never appears under its name unless whole"
    monkeypatch.undo()
    assert host.stage(_unit()) and (root / "bundles" / "q-a" / "empty.marker").exists()


def test_ssh_alive_is_unknown_not_dead_when_ssh_itself_fails_and_reap_neither_requeues_nor_kills_on_it(tmp_path):
    root = _remote_root(tmp_path)
    broken = tmp_path / "broken-ssh"
    broken.write_text("#!/bin/sh\nexit 255\n")
    broken.chmod(0o755)
    host = _ssh_host(tmp_path, root, ssh=str(broken))
    assert host.alive(_unit(), 1, {"pid": 4242, "pid_start": "t"}) is None, "ssh failed: nobody knows"
    mac, clock = FakeHost("mac", 1), Clock()
    c = _coordinator(tmp_path / "c", [mac], clock=clock, plan=_one_arm_plan(), limits={"stale_seconds": 1800})
    c.tick()
    mac.alive = lambda unit, attempt, handle: None
    clock.now += 100
    c.tick()
    row = c.queue.unit("q/a/m")
    assert row["status"] == "leased" and row["attempt"] == 1 and not mac.killed, "unknown is not a reason to requeue"
    clock.now += 1801  # unknown for longer than the stale limit: requeue, but remember to kill it when it answers
    c.tick()
    assert c.queue.unit("q/a/m")["status"] in ("pending", "leased") and c.queue.unit("q/a/m")["attempt"] >= 1
    kills = json.loads((tmp_path / "c" / "q" / "pending_kills.json").read_text())
    assert kills and kills[0]["unit_id"] == "q/a/m" and not mac.killed


def test_ssh_kill_checks_the_start_time_before_it_signals_a_pid(tmp_path):
    import os
    import signal
    root = _remote_root(tmp_path)
    host = _ssh_host(tmp_path, root)
    child = subprocess.Popen(["sleep", "60"])
    try:
        start = subprocess.run(["ps", "-o", "lstart=", "-p", str(child.pid)], capture_output=True, text=True).stdout.strip()
        host.kill(_unit(), 1, {"pid": child.pid, "pid_start": "Mon Jan  1 00:00:00 1990"})  # a reused pid: another process
        time.sleep(0.3)
        assert child.poll() is None, "a pid whose start time differs is never signalled"
        host.kill(_unit(), 1, {"pid": child.pid})  # no recorded start: the pid alone is not trusted either
        time.sleep(0.3)
        assert child.poll() is None
        host.kill(_unit(), 1, {"pid": child.pid, "pid_start": start})
        assert child.wait(10) == -signal.SIGTERM
    finally:
        child.kill()
        child.wait()


def test_flush_kills_keeps_every_entry_that_may_still_run_and_drops_only_the_gone(tmp_path):
    mac = FakeHost("mac", 1)
    c = _coordinator(tmp_path, [mac], plan=_one_arm_plan())
    path = tmp_path / "q" / "pending_kills.json"
    items = [{"host": "mac", "unit_id": f"q/a/m{i}", "attempt": 1, "pid": i, "pid_start": "t"} for i in range(3)]
    path.write_text(json.dumps(items + [{"host": "retired", "unit_id": "q/a/x", "attempt": 1, "pid": 9, "pid_start": "t"}]))
    mac.kill = lambda unit, attempt, handle: mac.killed.append((unit["unit_id"], attempt))
    state = {"q/a/m0": True, "q/a/m1": None, "q/a/m2": False}  # alive / unknown / gone after the kill
    mac.alive = lambda unit, attempt, handle: state[unit["unit_id"]]
    c._flush_kills()
    assert [i["unit_id"] for i in json.loads(path.read_text())] == ["q/a/m0", "q/a/m1"], "unkilled and unknown entries stay"
    assert [k[0] for k in mac.killed] == ["q/a/m0", "q/a/m1", "q/a/m2"]
    mac.is_online = False
    mac.killed.clear()
    c._online = {}
    c._flush_kills()
    assert len(json.loads(path.read_text())) == 2 and not mac.killed, "an offline host's entries wait"


def test_a_late_spawn_of_a_lease_that_already_ended_is_killed_not_leaked(tmp_path):
    mac = SlowStageHost("mac", 1)
    c = _coordinator(tmp_path, [mac], plan=_one_arm_plan())
    c.tick()
    assert mac.entered.wait(5)
    unit = c.queue.unit("q/a/m")
    c._transition("q/a/m", "pending", unit["attempt"], note="lease ended while staging", refund=True)
    mac.release.set()
    c.join_spawns()
    assert mac.spawned and ("q/a/m", unit["attempt"]) in mac.killed, "the worker that started afterwards is stopped"
    assert c.queue.unit("q/a/m")["status"] == "pending"
    assert not c._spawning


def test_accepting_a_result_prunes_its_working_files_and_an_arm_start_frees_the_staged_bundle(tmp_path):
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    pruned, dropped = [], []
    mac.prune = lambda unit, attempt: pruned.append((unit["unit_id"], attempt))
    mac.drop_bundle = lambda unit: dropped.append(unit["unit_id"])
    docs = Documents()
    c = _coordinator(tmp_path, [mac, wsl], docs, plan=_one_arm_plan(("m",)))
    c.tick()
    mac.finish("q/a/m", 1)
    c.tick()
    assert pruned == [("q/a/m", 1)], "pruned after the result was dealt with"
    c.join_documents()
    assert dropped == ["q/a/m"], "the bundle goes when the arm's document starts"
    # a rejected file is pruned as well, a failure while publishing is not
    mac2 = FakeHost("mac", 1)
    left = []
    mac2.prune = lambda unit, attempt: left.append(unit["unit_id"])
    c2 = _coordinator(tmp_path / "two", [mac2], plan=_one_arm_plan(("m",)))
    c2.tick()
    mac2.results[("q/a/m", 1)] = (b"not json", "x")
    c2.tick()
    assert left == ["q/a/m"]


def test_local_host_prune_removes_only_the_attempts_work_dir(tmp_path):
    bundle = _stub_bundle(tmp_path)
    host = LocalHost("wsl", 1, sys.executable, lambda unit: bundle, tmp_path / "w", tmp_path / "o")
    keep = tmp_path / "w" / "q__a__m.2"
    gone = tmp_path / "w" / "q__a__m.1"
    for d in (keep, gone):
        d.mkdir(parents=True)
        (d / "index.sqlite").write_text("x")
    host.prune(_unit(), 1)
    assert not gone.exists() and keep.exists()


def test_a_unit_does_not_hold_back_document_room_when_no_document_is_due_but_does_when_one_is(tmp_path):
    """The parity twin needed 22 GB (6 reserve + 8 document + 8 unit) and could never place during other work."""
    wsl = FakeHost("wsl", 1)
    probe = _probe(wsl=14.5, windows=40.)  # 14.5 - 6 = 8.5: exactly one real unit, no room for an idle document
    c = _coordinator(tmp_path, [wsl], plan=_one_arm_plan(("m1", "m2")), probe=probe, limits={"c_job_gb": 3.4})
    c.tick()
    assert [u["host"] for u in c.queue.units(status="leased")] == ["wsl"], "the unit places: 14.5 - 6 reserve >= 8"
    # now an arm whose units are all settled and whose document is due (RAM low): units must leave room for it
    c2 = _coordinator(tmp_path / "due", [FakeHost("wsl", 2)], plan=_one_arm_plan(("m1",), quarters=("q1", "q2")), probe=probe)
    c2.queue.lease("q1/a/m1", "wsl")
    c2.queue.transition("q1/a/m1", "verified", 1)
    arm_rows = {a["arm_key"]: a for a in c2.queue.arms()}
    assert c2._due_arms(arm_rows) == ["q1/a"]
    c2.place()
    assert not c2.queue.units(status="leased"), "a due document takes the room before a new unit may"


def test_windows_reader_accepts_only_a_fresh_figure(tmp_path):
    from mio_parallel_queue import windows_reader

    scripts = tmp_path / "skill2"
    scripts.mkdir()
    (scripts / "compute_state.py").write_text(
        "SEEN = []\ndef windows_available_cached(max_age=900, refresh=False):\n    SEEN.append(max_age)\n    return 12 * 2**30\n")
    saved = list(sys.path)
    sys.modules.pop("compute_state", None)
    try:
        assert windows_reader(str(scripts))() == 12.0
        assert sys.modules["compute_state"].SEEN == [120], "a reading older than the ramp window counts as unknown"
    finally:
        sys.path[:] = saved
        sys.modules.pop("compute_state", None)


def test_main_joins_staging_threads_and_survives_a_full_disk_pass_but_not_ten(tmp_path, monkeypatch, capsys):
    import mio_parallel_queue

    class Fake:
        def __init__(self, ticks):
            self.ticks, self.joined = list(ticks), 0
            self.queue = type("Q", (), {"arms": staticmethod(lambda: [])})()

        def tick(self):
            step = self.ticks.pop(0)
            if isinstance(step, Exception):
                raise step
            return step

        def join_spawns(self):
            self.joined += 1

        def rejection_line(self):
            return None

        def fallback_line(self):
            return None

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(tmp_path), "quarters": []}))
    fake = Fake([OSError(28, "No space left on device"), OSError(28, "No space left on device"), True, False])
    monkeypatch.setattr(mio_parallel_queue, "_build", lambda config: fake)
    assert main(["run", str(config), "--poll", "0"]) == 0
    err = capsys.readouterr().err
    assert "pass failed (OSError" in err and "2 in a row" in err, "a failed pass is announced, never silent"
    assert fake.joined == 1, "main waits for the threads that are starting workers"
    fake = Fake([OSError(28, "No space left on device")] * 10)
    monkeypatch.setattr(mio_parallel_queue, "_build", lambda config: fake)
    assert main(["run", str(config), "--poll", "0"]) == 5 and "giving up" in capsys.readouterr().err
    fake = Fake([OSError(28, "No space left on device")])
    monkeypatch.setattr(mio_parallel_queue, "_build", lambda config: fake)
    with pytest.raises(OSError):
        main(["run", str(config), "--once"])


def test_a_failed_publish_of_the_mac_file_leaves_no_passing_verdict_that_nothing_would_act_on(tmp_path, monkeypatch):
    """The verdict used to be written before it was acted on: an ENOSPC in the publish left a 'passed' verdict
    and a Mac file that no restart would ever publish, so the arm stalled for good."""
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 1)
    c = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    c.tick()
    mac.finish("q/a/m1", 1)
    wsl.finish("q/a/m1@parity", 1)
    real = Coordinator._publish

    def full_disk(self, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Coordinator, "_publish", full_disk)
    with pytest.raises(OSError):
        c.tick()
    assert not (tmp_path / "q" / "parity.json").exists(), "no verdict before the Mac file is safely published"
    monkeypatch.setattr(Coordinator, "_publish", real)
    restarted = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    restarted.tick()
    assert restarted.parity_state() == "passed" and (restarted.recorded_dir("q", "a") / "m1.json").exists()
    assert restarted.queue.unit("q/a/m1")["status"] == "verified"


def _fixed_run_doc(tmp_path, name, spec):
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps({"name": name, "outputs": {"run_root": str(tmp_path / name)},
                                "pipeline": {"study": {"uses": "index_options.ledger_studies:MIOPacketStudy",
                                                       "params": {"study": spec}}}}))
    return path


def test_fixed_forecast_arms_get_worker_units_and_a_bad_forecast_skips_the_quarter(tmp_path, monkeypatch):
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir()
    good_init = _doc(tmp_path, "good-init", tmp_path / "i")
    world = fixed_world(tmp_path / "w1", monkeypatch, "2023Q4", seeds=(11, 29, 47))
    _ready(root, "2023q4", _fixed_run_doc(tmp_path, "q4", world["study"]), good_init)
    bad = fixed_world(tmp_path / "w2", monkeypatch, "2023Q4", receipt=lambda b: b.update(evaluation_computed=True))
    _ready(root, "2023q3", _fixed_run_doc(tmp_path, "refused-forecast", bad["study"]), good_init)
    late = fixed_world(tmp_path / "w3", monkeypatch, "2020Q1")
    _ready(root, "2024q1", _fixed_run_doc(tmp_path, "late", late["study"]), good_init)
    config = {"queue_root": str(root), "python": sys.executable, "runtime": str(runtime),
              "scratch_root": str(tmp_path / "scratch"), "quarters": ["2023q3", "2023q4", "2024q1"]}
    plan = _load_plan(config)
    assert [q["id"] for q in plan["quarters"]] == ["2023q4"]
    arm = plan["quarters"][0]["arms"][0]
    assert arm["methods"] == [m["id"] for m in world["study"]["domain"]["params"]["methods"]], "this policy gets worker units"
    assert "no_units" not in arm and len(arm["bundle_sha256"]) == 64
    assert "evaluation" in plan["skipped"]["2023q3"], "a refused forecast skips its quarter, no crash"
    assert "only through 2023Q4" in plan["skipped"]["2024q1"]
    assert not list((tmp_path / "scratch" / "bundles").glob("2023q3*")), "a refused forecast is never staged"


# -- rejections: a WSL rejection is never silent (owner ruling 2026-11-05 ~18:35) -------------------------------------
def _rejection(method="m1", week="2022-01-10", check="certificate_objective", category="tolerance", ratio=2.5):
    return {"method": method, "week": week, "check": check, "category": category, "ratio": ratio}


class RejectingDocuments(Documents):
    """The document of a chosen arm solved a method itself because the worker's file was rejected."""

    def __init__(self, rejections_by_arm):
        super().__init__()
        self.rejections_by_arm = rejections_by_arm

    def __call__(self, spec, quarter, arm, recorded_dir, asof):
        outcome = super().__call__(spec, quarter, arm, recorded_dir, asof)
        outcome["rejections"] = self.rejections_by_arm.get(f"{quarter}/{arm}", [])
        return outcome


def test_rejection_ledger_keeps_only_sanitized_fields_dedupes_and_clears_without_losing_history(tmp_path):
    from mio_parallel_queue import RejectionLedger
    ledger = RejectionLedger(tmp_path / "rejections.jsonl")
    assert ledger.state() == {"open": 0, "cleared": 0, "reasons": {}, "records": []}
    hostile = {"method": "m1", "week": "$1,234,567", "check": "Objective=$99.5", "category": "profit $5", "ratio": float("nan"),
               "error": "gap 123456.78", "objective": 123456.78}
    assert ledger.record("2022q1/rules/m1", hostile, run="run-2022q1-rules", at="2026-01-01T00:00:00Z") is True
    assert ledger.record("2022q1/rules/m1", hostile, run="run-2022q1-rules", at="2026-01-01T00:00:05Z") is False, "same fact twice"
    text = (tmp_path / "rejections.jsonl").read_text()
    assert "123456" not in text and "$" not in text and "error" not in text and "objective" not in text
    [row] = ledger.state()["records"]
    assert (row["week"], row["check"], row["category"], row["ratio"]) == (None, "unclassified", "other", None)
    assert ledger.record("2022q1/rules/m2", _rejection("m2"), run="run-2022q1-rules", at="2026-01-01T00:01:00Z")
    state = ledger.state()
    assert state["open"] == 2 and state["reasons"] == {"other:unclassified": 1, "tolerance:certificate_objective": 1}
    assert {k: state["records"][1][k] for k in ("unit", "week", "check", "category", "ratio")} == {
        "unit": "2022q1/rules/m2", "week": "2022-01-10", "check": "certificate_objective", "category": "tolerance", "ratio": 2.5}
    assert ledger.clear("2026-01-01T01:00:00Z") == 2
    assert ledger.state() == {"open": 0, "cleared": 2, "reasons": {}, "records": []}
    assert ledger.clear("2026-01-01T01:00:01Z") == 0
    assert ledger.record("2022q1/rules/m1", hostile, run="run-2022q1-rules", at="2026-01-01T02:00:00Z") is False, \
        "C7: the same fact of the same run, repeated after a clear (a restart), does not re-open what the owner cleared"
    assert ledger.record("2022q1/rules/m1", hostile, run="run-2022q1-rules-again", at="2026-01-01T02:00:00Z"), \
        "a rejection of another run is new"
    assert ledger.state()["open"] == 1 and ledger.state()["cleared"] == 2
    with open(tmp_path / "rejections.jsonl", "a") as handle:
        handle.write("{not json\n")
    assert ledger.state()["open"] == 2, "an unreadable line fails closed as an open rejection"


def test_clear_closes_up_to_the_row_the_owner_saw_and_a_later_rejection_stays_open(tmp_path):
    """C: a clear names the highest ``seq`` the owner read, so repeating it never closes a row he has not seen."""
    from mio_parallel_queue import RejectionLedger
    ledger = RejectionLedger(tmp_path / "rejections.jsonl")
    for unit in ("q/a/m1", "q/a/m2"):
        ledger.record(unit, _rejection(), run="r", at="2026-01-01T00:00:00Z")
    assert [r["seq"] for r in ledger.state()["records"]] == [1, 2]
    assert ledger.clear("2026-01-01T01:00:00Z", through=1) == 1, "the owner read up to seq 1"
    state = ledger.state()
    assert (state["open"], state["cleared"], [r["unit"] for r in state["records"]]) == (1, 1, ["q/a/m2"])
    assert ledger.clear("2026-01-01T01:30:00Z", through=1) == 0, "the same command again closes nothing"
    ledger.record("q/a/m3", _rejection(), run="r", at="2026-01-01T02:00:00Z")
    assert ledger.clear("2026-01-01T03:00:00Z", through=2) == 1, "m3 (seq 3) arrived after the owner looked"
    assert [r["unit"] for r in ledger.state()["records"]] == ["q/a/m3"]
    ledger.record("q/a/m4", _rejection(), run="r", at="2026-01-01T04:00:00Z")
    ledger._append({"kind": "clear", "through": 3, "at": "2026-01-01T05:00:00Z"})  # what clear() wrote after reading seq 3 as the highest
    assert [r["unit"] for r in ledger.state()["records"]] == ["q/a/m4"], "a row recorded between the read and the line stays open"
    ledger._append({"kind": "clear", "at": "2026-01-01T05:30:00Z"})
    ledger._append({"kind": "clear", "through": 0, "at": "2026-01-01T05:31:00Z"})
    assert ledger.state()["open"] == 1, "a clear line that names no positive row closes nothing"
    assert ledger.clear("2026-01-01T06:00:00Z") == 1 and ledger.state()["open"] == 0, "no bound: everything open now"


@pytest.mark.parametrize("bad", [0, -1, -5, True, 1.5])
def test_clear_refuses_a_non_positive_or_non_integer_bound(tmp_path, bad, capsys):
    """A negative ``--seen`` used to close every open row."""
    from mio_parallel_queue import RejectionLedger
    ledger = RejectionLedger(tmp_path / "rejections.jsonl")
    ledger.record("q/a/m1", _rejection(), run="r", at="2026-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="positive"):
        ledger.clear("2026-01-01T01:00:00Z", through=bad)
    assert ledger.state()["open"] == 1
    root = tmp_path / "root"
    root.mkdir()
    RejectionLedger(root / "rejections.jsonl").record("q/a/m1", _rejection(), run="r", at="2026-01-01T00:00:00Z")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root)}))
    assert main(["clear-rejections", str(config), "--through", "-1"]) == 1 and "refused" in capsys.readouterr().err
    assert main(["clear-rejections", str(config), "--through", "0"]) == 1
    assert RejectionLedger(root / "rejections.jsonl").state()["open"] == 1
    assert main(["clear-rejections", str(config), "--through", "1"]) == 0


def test_an_unreadable_ledger_refuses_and_only_a_missing_file_is_empty(tmp_path):
    """C5: any OSError but FileNotFoundError used to read as an empty ledger (the pause fails open)."""
    from mio_parallel_queue import RejectionLedger
    assert RejectionLedger(tmp_path / "absent.jsonl").state()["open"] == 0
    blocked = tmp_path / "rejections.jsonl"
    blocked.mkdir()  # reading a directory raises IsADirectoryError, an OSError that is not FileNotFoundError
    for call in (lambda x: x.state(), lambda x: x.clear("2026-01-01T00:00:00Z"),
                 lambda x: x.record("q/a/m", _rejection(), run="r", at="2026-01-01T00:00:00Z")):
        with pytest.raises(OSError):
            call(RejectionLedger(blocked))
    unreadable = tmp_path / "locked.jsonl"
    unreadable.write_text("{}\n")
    unreadable.chmod(0)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("running as a user that ignores file modes")
        with pytest.raises(PermissionError):
            RejectionLedger(unreadable).state()
    finally:
        unreadable.chmod(0o600)


def test_a_rejected_method_stops_new_mac_units_until_the_owner_clears_it(tmp_path):
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    docs = RejectingDocuments({"2022q1/control": [_rejection("c1", ratio=3.2)]})
    c = _coordinator(tmp_path, [mac, wsl], docs)
    assert _tick(c)
    [first] = [u for u in c.queue.units(status="leased")]
    assert first["unit_id"] == "2022q1/control/c1"
    mac.finish(first["unit_id"], 1)
    _tick(c)  # accepted; the control document runs and reports its fallback solve
    _tick(c)
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert status["rejections"]["open"] == 1 and status["rejections"]["reasons"] == {"tolerance:certificate_objective": 1}
    [record] = status["rejections"]["records"]
    assert (record["unit"], record["week"], record["check"], record["category"], record["ratio"]) == (
        "2022q1/control/c1", "2022-01-10", "certificate_objective", "tolerance", 3.2)
    assert "rejection" in status["waiting"]["rejections"] and "clear-rejections" in status["waiting"]["rejections"]
    [running] = c.queue.units(status="leased")  # placed in the pass before the document reported: it finishes
    assert len(mac.spawned) == 2
    mac.finish(running["unit_id"], 1)
    _tick(c)
    _tick(c)
    assert not c.queue.units(status="leased") and len(mac.spawned) == 2, "no new Mac unit while a rejection is open"
    assert next(u for u in c.queue.units() if u["unit_id"] == running["unit_id"])["status"] == "verified"
    pending = [u for u in c.queue.units(status="pending") if u["role"] == "primary"]
    assert pending, "the rest wait as pending: nothing is quietly rejected over to WSL"
    line = c.rejection_line()
    assert "open 1" in line and "tolerance:certificate_objective" in line and "$" not in line
    assert c.rejection_line() is None, "a line is printed when the count changes, not every pass"
    from mio_parallel_queue import RejectionLedger
    assert RejectionLedger(tmp_path / "q" / "rejections.jsonl").clear(c._stamp()) == 1
    _tick(c)
    assert c.queue.units(status="leased"), "cleared: the Mac takes the next unit"
    assert json.loads((tmp_path / "q" / "status.json").read_text())["rejections"] == {
        "open": 0, "cleared": 1, "reasons": {}, "records": []}


def test_running_work_finishes_while_new_mac_placement_is_stopped(tmp_path):
    mac = FakeHost("mac", 2)
    c = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)], RejectingDocuments({}))
    _tick(c)
    leased = [u["unit_id"] for u in c.queue.units(status="leased")]
    assert len(leased) == 2
    c.rejections.record("2022q1/other/x", _rejection("x"), run="r", at=c._stamp())
    mac.finish(leased[0], 1)
    _tick(c)
    assert next(u for u in c.queue.units() if u["unit_id"] == leased[0])["status"] in ("verified", "replayed", "solved_local")
    assert len(mac.spawned) == 2, "the leased pair ran; the rest wait"


def test_a_returned_file_that_fails_the_cheap_gates_is_a_recorded_rejection(tmp_path):
    mac = FakeHost("mac", 1)
    c = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)])
    _tick(c)
    [unit] = c.queue.units(status="leased")
    raw, sidecar = _body(unit["method"], 1)
    mac.results[(unit["unit_id"], 1)] = (raw, "0" * 64 + "  x.json\n")
    _tick(c)
    [record] = json.loads((tmp_path / "q" / "status.json").read_text())["rejections"]["records"]
    assert (record["unit"], record["check"], record["category"], record["week"], record["ratio"]) == (
        unit["unit_id"], "sidecar_digest", "file", None, None)


def test_clear_rejections_command_clears_without_the_coordinator_lock(tmp_path, capsys):
    from mio_parallel_queue import RejectionLedger
    root = tmp_path / "q"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root)}))
    assert main(["clear-rejections", str(config)]) == 1 and "nothing to clear" in capsys.readouterr().err
    RejectionLedger(root / "rejections.jsonl").record("q/a/m", _rejection(), run="r", at="2026-01-01T00:00:00Z")
    lock = PidLock(root / "coordinator.lock")
    assert lock.acquire()
    try:
        assert main(["clear-rejections", str(config)]) == 0, "the owner clears while the coordinator runs"
    finally:
        lock.release()
    assert "cleared 1" in capsys.readouterr().out
    assert RejectionLedger(root / "rejections.jsonl").state()["open"] == 0


def test_recorded_from_report_reads_rejections_from_the_audit_else_the_log_by_sanitized_fields(tmp_path):
    from mio_parallel_queue import rejections_from_log
    run = tmp_path / "run"
    run.mkdir()
    (run / "carry.json").write_text(json.dumps({"close_packet": {"receipt": {"path": "receipt.json"}}}))
    hostile = {"method": "m1", "week": "2022-01-10", "check": "certificate_objective", "category": "tolerance",
               "ratio": 2.5, "error": "objective 123456.78", "funding": 99999.9}
    (run / "receipt.json").write_text(json.dumps({"complete": True, "results": [], "audit": {"recorded_rejections": [hostile]}}))
    (run / "run.log").write_text(
        'INFO MIO_EVENT {"type":"recorded_rejected","method":"m2","week":"2022-01-17","check":"capacity",'
        '"category":"invalid_trade","ratio":null,"error":"x $4,000"}\n'
        'INFO MIO_EVENT {"type":"weekly_decision","audited_selection":{"value":123456.78}}\n')
    report = recorded_from_report(run)
    assert report["rejections"] == [{"method": "m1", "week": "2022-01-10", "check": "certificate_objective",
                                     "category": "tolerance", "ratio": 2.5}]
    assert "123456" not in json.dumps(report) and "99999" not in json.dumps(report)
    assert rejections_from_log(run) == [{"method": "m2", "week": "2022-01-17", "check": "capacity",
                                         "category": "invalid_trade", "ratio": None}]
    (run / "receipt.json").write_text(json.dumps({"complete": True, "results": [], "audit": {}}))
    assert recorded_from_report(run)["rejections"] == rejections_from_log(run), "no audit key: the log's structured fields"
    assert rejections_from_log(tmp_path / "missing") == []


def _pins(*ids):
    """Placeholder 64-hex spec pins for configs that never plan a quarter."""
    return {i: hashlib.sha256(i.encode()).hexdigest() for i in ids}


# -- A0010: nine formulations on four selection quarters, the owner picks 3, those run sealed on 2022-2023 ---------------
def _phased(study, tmp_path, selection_methods, holdout_methods, formulations=("nominal", "second", "third"), **over):
    """READY markers for a selection quarter q1 and a sealed quarter q2 whose documents carry the given method ids."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir(exist_ok=True)

    def run_doc(name, ids):
        spec = json.loads(json.dumps(study))
        base = spec["domain"]["params"]["methods"][0]
        spec["domain"]["params"]["methods"] = [{**base, "id": i} for i in ids]
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "outputs": {"run_root": str(tmp_path / name)},
                                    "pipeline": {"study": {"uses": "index_options.ledger_studies:MIOPacketStudy",
                                                           "params": {"study": spec}}}}))
        return path

    from mio_parallel_queue import Phases
    init = _doc(tmp_path, "init", tmp_path / "i")
    _ready(root, "q1", run_doc("sel-run", selection_methods), init)
    _ready(root, "q2", run_doc("hold-run", holdout_methods), init)
    return {"queue_root": str(root), "scratch_root": str(tmp_path / "scratch"), "python": sys.executable,
            "runtime": str(runtime), "quarters": ["q1", "q2"], "selection_quarters": ["q1"],
            "holdout_quarters": ["q2"], "selection_formulations": list(formulations), "selection_picks": 1,
            "selection_formulation_hashes": {i: Phases.spec_hash({**study["domain"]["params"]["methods"][0], "id": i})
                                             for i in formulations}, **over}


def test_a_sealed_quarter_is_not_planned_until_the_selection_is_recorded_and_it_runs_only_the_picks(study, tmp_path):  # noqa: F811
    from mio_parallel_queue import Phases
    config = _phased(study, tmp_path, ["nominal", "second", "third"], ["second"])
    with pytest.raises(ValueError, match="selection is not recorded"):
        _load_plan(config)
    phases = Phases.from_config(config)
    phases.record(["second"], "2026-01-01T00:00:00Z", closed=True)
    plan = _load_plan(config)
    assert [q["id"] for q in plan["quarters"]] == ["q1", "q2"] and not plan["skipped"]
    config2 = _phased(study, tmp_path / "again", ["nominal", "second", "third"], ["nominal"])
    Phases.from_config(config2).record(["second"], "2026-01-01T00:00:00Z", closed=True)
    other = _load_plan(config2)
    assert [q["id"] for q in other["quarters"]] == ["q1"] and "picked" in other["skipped"]["q2"], \
        "a sealed quarter that runs anything but the owner's picks is skipped"
    config3 = _phased(study, tmp_path / "short", ["nominal", "second"], ["second"])
    Phases.from_config(config3).record(["second"], "2026-01-01T00:00:00Z", closed=True)
    short = _load_plan(config3)
    assert "q1" in short["skipped"] and "formulation" in short["skipped"]["q1"], "a selection quarter must carry all nine"
    assert "q2" in short["skipped"], "and the sealed quarter after it is skipped with it (shared registry)"


def test_a_sealed_quarters_skip_reason_is_scrubbed_of_numbers_a_selection_quarters_is_not(study, tmp_path, monkeypatch):  # noqa: F811
    from mio_parallel_queue import Phases
    config = _phased(study, tmp_path, ["nominal", "second", "third"], ["second"])
    Phases.from_config(config).record(["second"], "2026-01-01T00:00:00Z", closed=True)
    real = Phases.check_methods

    def noisy(self, quarter, found):
        real(self, quarter, found)
        raise ValueError(f"{quarter} gap 1234.5 at week 2022-05-02 limit $99")
    monkeypatch.setattr(Phases, "check_methods", noisy)
    skipped = _load_plan(config)["skipped"]
    assert "1234" not in skipped["q2"] and "2022" not in skipped["q2"] and "99" not in skipped["q2"], skipped["q2"]
    assert skipped["q2"].startswith("ValueError: ") and "gap" in skipped["q2"]
    assert "1234.5" in skipped["q1"], "a selection quarter keeps its message"


def test_phases_config_is_validated_and_absent_means_the_old_behaviour():
    from mio_parallel_queue import Phases
    assert Phases.from_config({"queue_root": "/x", "quarters": ["a"]}) is None
    ok = {"queue_root": "/x", "quarters": ["a", "b", "c"], "selection_quarters": ["a"], "holdout_quarters": ["b"],
          "selection_formulations": ["f", "g", "h"], "selection_picks": 3, "selection_formulation_hashes": _pins("f", "g", "h")}
    assert Phases.from_config(ok)
    for edit in ({"holdout_quarters": ["a"]}, {"selection_quarters": ["z"]}, {"selection_formulations": ["f", "f", "g"]},
                 {"selection_picks": 4}, {"selection_picks": 0}, {"holdout_quarters": []}, {"selection_quarters": []},
                 {"selection_formulations": ["f", 1, "g"]},
                 {"selection_formulation_hashes": _pins("f", "g")}, {"selection_formulation_hashes": _pins("f", "g", "z")},
                 {"selection_formulation_hashes": {**_pins("f", "g", "h"), "h": "short"}},
                 {"selection_formulation_hashes": None}):
        with pytest.raises(ValueError):
            Phases.from_config({**ok, **edit})
    del ok["selection_formulation_hashes"]
    with pytest.raises(ValueError, match="selection_formulation_hashes"):
        Phases.from_config(ok)  # M3: a phase config must pin every formulation


def test_a_formulation_id_whose_spec_differs_from_its_pin_is_refused(study, tmp_path):  # noqa: F811
    """M3: the holdout ran 'the owner's picks' by id alone, so a changed spec under a picked id went through."""
    from mio_parallel_queue import Phases
    config = _phased(study, tmp_path, ["nominal", "second", "third"], ["second"])
    Phases.from_config(config).record(["second"], "2026-01-01T00:00:00Z", closed=True)
    assert [q["id"] for q in _load_plan(config)["quarters"]] == ["q1", "q2"], "pins that match plan"
    hashes = dict(config["selection_formulation_hashes"])
    for victim, quarter in (("second", "q2"), ("nominal", "q1")):
        other = _phased(study, tmp_path / ("bad-" + victim), ["nominal", "second", "third"], ["second"])
        Phases.from_config(other).record(["second"], "2026-01-01T00:00:00Z", closed=True)
        other["selection_formulation_hashes"] = {**hashes, victim: Phases.spec_hash({"id": victim, "other": 1})}
        skipped = _load_plan(other)["skipped"]
        assert "differs from its pinned spec" in skipped["q1"] and victim in skipped["q1"], skipped
    phases = Phases(tmp_path, ["a"], ["b"], ["f"], 1, {"f": Phases.spec_hash({"id": "f", "rho": .1})})
    phases.check_methods("a", [{"id": "f", "rho": .1}])
    with pytest.raises(ValueError, match="pinned spec"):
        phases.check_methods("a", [{"id": "f", "rho": .2}])
    with pytest.raises(ValueError, match="pinned spec"):
        phases.check_methods("a", ["f"])  # a bare id carries no spec, so it cannot satisfy a pin


def test_the_owner_records_three_picks_once_after_the_selection_quarters_closed(tmp_path):
    from mio_parallel_queue import Phases
    root = tmp_path / "q"
    root.mkdir()
    config = {"queue_root": str(root), "quarters": ["a", "b"], "selection_quarters": ["a"], "holdout_quarters": ["b"],
              "selection_formulations": ["f", "g", "h", "i"], "selection_picks": 3,
              "selection_formulation_hashes": _pins("f", "g", "h", "i")}
    phases = Phases.from_config(config)
    assert phases.picked() is None
    for ids in (["f", "g"], ["f", "g", "g"], ["f", "g", "z"], ["f", "g", "h", "i"]):
        with pytest.raises(ValueError):
            phases.record(ids, "2026-01-01T00:00:00Z", closed=True)
    with pytest.raises(ValueError, match="not closed"):
        phases.record(["f", "g", "h"], "2026-01-01T00:00:00Z", closed=False)
    phases.record(["h", "f", "g"], "2026-01-01T00:00:00Z", closed=True)
    assert phases.picked() == ["h", "f", "g"]
    with pytest.raises(ValueError, match="already recorded"):
        phases.record(["f", "g", "h"], "2026-01-02T00:00:00Z", closed=True)
    assert not phases.unsealed()
    phases.unseal("2026-01-03T00:00:00Z")
    assert phases.unsealed()


def test_unseal_needs_the_recorded_selection(tmp_path):
    from mio_parallel_queue import Phases
    phases = Phases(tmp_path, ["a"], ["b"], ["f", "g"], 1)
    with pytest.raises(ValueError, match="selection is not recorded"):
        phases.unseal("2026-01-01T00:00:00Z")


def _closed_arm(queue, quarter, arm, run_dir):
    queue.set_arm(quarter, arm, "closed", str(run_dir), {"ok": True})


def _receipt_run(path, results):
    path.mkdir(parents=True)
    (path / "carry.json").write_text(json.dumps({"close_packet": {"receipt": {"path": "receipt.json"}}}))
    (path / "receipt.json").write_text(json.dumps({"complete": True, "audit": {}, "results": results}))
    return path


def test_the_sealed_quarters_hide_their_results_in_status_until_unsealed_and_live_under_holdout(tmp_path):
    from mio_parallel_queue import Phases
    plan = _plan()
    for q in plan["quarters"]:
        q["id"] = {"2022q1": "q1", "2022q2": "q2"}[q["id"]]
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    phases = Phases(tmp_path / "q", ["q1"], ["q2"], ["m1", "m2"], 1)
    c = _coordinator(tmp_path, [mac, wsl], Documents(), plan=plan, phases=phases)
    assert c.recorded_dir("q1", "rules") == tmp_path / "q" / "recorded" / "q1-rules"
    assert c.recorded_dir("q2", "rules") == tmp_path / "q" / "holdout" / "recorded" / "q2-rules"
    _tick(c)
    c.queue.set_arm("q2", "rules", "closed", "/runs/secret-q2", {"ok": True})
    c.write_status()
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    by = {a["arm_key"]: a for a in status["arms"]}
    assert by["q2/rules"]["run_dir"] is None and by["q2/rules"]["status"] == "sealed", "no state word, no location"
    assert by["q1/rules"]["status"] in ("waiting", "running", "closed"), "a selection arm keeps its state"
    assert not [u for u in status["units"] if u["unit_id"].startswith("q2/")], "no per-unit state word for a sealed quarter"
    assert status["sealed_counts"] == {"q2/rules": {"units": 1, "settled": 0}}, "counts per arm only"
    assert "closed" not in json.dumps([a for a in status["arms"] if a["arm_key"].startswith("q2/")])
    assert status["sealed"] == {"quarters": ["q2"], "selection": None, "unsealed": False}
    phases.record(["m1"], "2026-01-01T00:00:00Z", closed=True)
    phases.unseal("2026-01-02T00:00:00Z")
    c.write_status()
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert {a["arm_key"]: a for a in status["arms"]}["q2/rules"]["run_dir"] == "/runs/secret-q2"
    assert {a["arm_key"]: a for a in status["arms"]}["q2/rules"]["status"] == "closed" and status["sealed_counts"] == {}
    assert [u for u in status["units"] if u["unit_id"].startswith("q2/")], "unsealed: the units show again"
    assert status["sealed"] == {"quarters": ["q2"], "selection": ["m1"], "unsealed": True}


def test_the_selection_report_covers_the_selection_quarters_only_and_ranks_nothing(tmp_path, capsys):
    from mio_parallel_queue import Phases
    root = tmp_path / "q"
    root.mkdir()
    result = lambda pnl: {"pnl": pnl, "twr": .01, "max_drawdown": .02, "final_equity": 10000.+pnl, "contracts": 4,
                          "unfilled": 1, "curve": [{"date": "x"}], "decisions": [{"secret": 1}]}
    q1 = _receipt_run(tmp_path / "r1", [{"id": "f", "status": "completed", "result": result(50.)},
                                        {"id": "g", "status": "completed", "result": result(-20.)},
                                        {"id": "h", "status": "failed", "error": "boom"}])
    q2 = _receipt_run(tmp_path / "r2", [{"id": "f", "status": "completed", "result": result(777.)}])
    queue = UnitQueue(root / "queue.sqlite")
    for quarter in ("a", "b"):
        queue.add_arm(quarter, "rules")
    _closed_arm(queue, "a", "rules", q1)
    _closed_arm(queue, "b", "rules", q2)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root), "quarters": ["a", "b"], "selection_quarters": ["a"],
                                  "holdout_quarters": ["b"], "selection_formulations": ["f", "g", "h"], "selection_picks": 1,
                                  "selection_formulation_hashes": _pins("f", "g", "h")}))
    assert main(["report-selection", str(config)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [(r["quarter"], r["arm"], r["method"]) for r in out["rows"]] == [("a", "rules", "f"), ("a", "rules", "g"), ("a", "rules", "h")]
    assert out["rows"][0]["pnl"] == 50. and out["rows"][1]["pnl"] == -20. and out["rows"][2]["status"] == "failed"
    text = json.dumps(out)
    assert "777" not in text and "secret" not in text and "curve" not in text, "sealed quarters and per-week detail stay out"
    assert not any(k in text for k in ("rank", "best", "winner", "score")), "the owner chooses; no rule ranks"
    with pytest.raises(SystemExit):
        main(["report-holdout", str(config)])


def test_the_selection_report_refuses_an_unfinished_selection_quarter(tmp_path, capsys):
    root = tmp_path / "q"
    root.mkdir()
    queue = UnitQueue(root / "queue.sqlite")
    queue.add_arm("a", "rules")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root), "quarters": ["a", "b"], "selection_quarters": ["a"],
                                  "holdout_quarters": ["b"], "selection_formulations": ["f", "g"], "selection_picks": 1,
                                  "selection_formulation_hashes": _pins("f", "g")}))
    assert main(["report-selection", str(config)]) == 1 and "not closed" in capsys.readouterr().err


def test_record_selection_and_unseal_commands_and_the_second_start_guard(study, tmp_path, capsys):  # noqa: F811
    config = _phased(study, tmp_path, ["nominal", "second", "third"], ["second"])
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    root = Path(config["queue_root"])
    queue = UnitQueue(root / "queue.sqlite")
    queue.add_arm("q1", "rules")
    assert main(["record-selection", str(path), "second"]) == 1 and "not closed" in capsys.readouterr().err
    _closed_arm(queue, "q1", "rules", tmp_path / "r")
    assert main(["record-selection", str(path), "second", "third"]) == 1, "this config picks exactly one"
    assert main(["unseal", str(path)]) == 1
    assert main(["run", str(path), "--once"]) == 2 and "selection is not recorded" in capsys.readouterr().err, \
        "a sealed quarter has a READY marker but no selection: the start is refused"
    capsys.readouterr()
    assert main(["record-selection", str(path), "second"]) == 0
    assert json.loads((root / "selection.json").read_text())["picked"] == ["second"]
    assert main(["record-selection", str(path), "third"]) == 1 and "already recorded" in capsys.readouterr().err
    assert main(["unseal", str(path)]) == 0 and (root / "unsealed.json").exists()


def test_the_example_config_is_the_locked_a0010_shape_and_the_nine_are_documented():
    """Path row A0010: nine formulations, four selection quarters, 3 picked for 8 sealed quarters: 9x4 + 3x8 = 60 accounts."""
    from mio_parallel_queue import Phases
    from test_ledger_studies import FORMULATIONS
    here = Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel"
    config = json.loads((here / "queue.example.json").read_text())
    phases = Phases.from_config(config)
    assert phases.selection_quarters == ["2019q4", "2020q1", "2021q1", "2021q2"]
    assert phases.holdout_quarters == [f"{y}q{q}" for y in (2022, 2023) for q in (1, 2, 3, 4)] and phases.picks == 3
    assert sorted(phases.formulations) == sorted(FORMULATIONS) and len(FORMULATIONS) == 9
    assert len(phases.formulations)*len(phases.selection_quarters) + phases.picks*len(phases.holdout_quarters) == 60
    assert phases.spec_hashes == {n: Phases.spec_hash({"id": n, **FORMULATIONS[n]}) for n in FORMULATIONS}, \
        "M3: the example pins the RUNBOOK's nine specs"
    assert config["quarters"] == phases.selection_quarters + phases.holdout_quarters or \
        sorted(config["quarters"]) == sorted(phases.selection_quarters + phases.holdout_quarters)
    runbook = (here / "RUNBOOK.md").read_text()
    adr = (Path(__file__).resolve().parents[3] / "docs" / "architecture" / "decision-log.md").read_text()
    for name in FORMULATIONS:
        assert name in runbook, name+" is missing from the RUNBOOK"
        assert name in adr, name+" is missing from ADR-0297"


def _runbook_commands(text):
    """Every command line of the runbook's shell blocks (continuations joined; comments and heredoc bodies dropped)."""
    import re
    out = []
    for lang, body in re.findall(r"```(\w*)\n(.*?)\n```", text, re.S):
        if lang not in ("bash", "sh"):
            continue
        heredoc = None
        for line in re.sub(r"\\\n\s*", " ", body).splitlines():
            line = line.strip()
            if heredoc:
                heredoc = None if line == heredoc else heredoc
                continue
            marker = re.search(r"<<'(\w+)'\s*$", line)
            heredoc = marker.group(1) if marker else None
            if line and not line.startswith("#"):
                out.append(line.split("   #")[0])
    return out


def _skill_modules(monkeypatch):
    import mio_parallel_queue
    scripts = Path(REAL_SKILL)
    if not (scripts / "compute_gate.py").exists():
        pytest.skip("the compute skill is not on this machine")
    for name in ("compute_state", "compute_gate", "compute_run"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(scripts))
    gate, run = importlib.import_module("compute_gate"), importlib.import_module("compute_run")
    for name in ("compute_state", "compute_gate", "compute_run"):
        monkeypatch.setitem(sys.modules, name, sys.modules[name])
    return gate, run


def test_the_runbook_is_a_valid_plan_in_launch_order_and_every_heavy_line_has_a_job(monkeypatch, tmp_path):
    """Walked against the compute skill's own parsers: jobs.json loads, and no line of the runbook needs a gate waiver."""
    import re
    gate, run = _skill_modules(monkeypatch)
    text = (Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "RUNBOOK.md").read_text()
    jobs_json = json.loads(next(b for b in re.findall(r"```json\n(.*?)\n```", text, re.S) if '"run_id"' in b))
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs_json))
    _, _, jobs = run.load_spec(str(path))          # the skill's own loader and validator
    normal = [{**{k: v for k, v in j.items() if k != "allowed_hosts"}, "host": j["allowed_hosts"][0]} for j in jobs]
    assert not gate.plan_problems({"jobs": normal})
    plan = {"jobs": normal}
    commands = _runbook_commands(text)
    assert len(commands) > 12
    for command in commands:
        if gate.classify(command).heavy:
            assert gate.matched_jobs(plan, command), "this runbook line would need a waiver: "+command[:120]
    index = lambda needle: next(i for i, c in enumerate(commands) if needle in c)
    order = [index("compute_run.py plan new"), index("mio_parallel_queue.py stage"), index("-m index_options.mio_worker wsl.spec.json"),
             index("compute_run.py certify"), index("nohup env")]
    assert order == sorted(order) and len(set(order)) == 5, "plan, stage, time, certify, launch"
    assert {j["id"] for j in jobs_json["jobs"]} >= {"coordinator", "coordinator-tools", "worker-timing", "certify"}
    verbs = {m.group(1) for c in commands if (m := re.search(r"mio_parallel_queue\.py (\w[\w-]*)", c))}
    assert {"run", "stage", "clear-rejections"} <= verbs


@pytest.mark.skipif(not os.path.ismount(f"/run/user/{os.getuid()}") or not os.access(f"/run/user/{os.getuid()}", os.W_OK),
                    reason="needs a writable mount point of the user's own to stand in for the RAM disk")
def test_every_hand_run_mac_line_checks_the_mount_and_the_sentinel_before_it_writes(tmp_path):
    """The remote command of each `ssh mac '...'` line, run here on a stand-in root: refused unless mounted AND marked."""
    import shlex
    text = (Path(__file__).resolve().parents[1] / "docs" / "plans" / "mio-parallel" / "RUNBOOK.md").read_text()
    remote = []
    for command in _runbook_commands(text):
        words = shlex.split(command.replace("<<'EOF'", ""))
        if words[:2] == ["ssh", "mac"] and "/Volumes/mio-ram" in words[2] and "mac_worker.sh" not in words[2]:
            remote.append(words[2])
    ssh_writes = [r for r in remote if any(w in r for w in ("rm ", "mkdir", "cat >"))]
    assert len(ssh_writes) == 3, "the prune lines and the cert spec line are the hand-run writes"
    token, mount = "tok-1", Path(f"/run/user/{os.getuid()}")
    sentinel = mount / ".mio-sentinel"
    if sentinel.exists() or (mount / "out").exists() or (mount / "cache").exists():
        pytest.skip("the stand-in root is in use")
    for body in ssh_writes:
        assert "df -P" in body and ".mio-sentinel" in body, body
        script = body.replace("/Volumes/mio-ram", str(mount)).replace("<token>", token).replace("<quarter>", "q1")
        plain = tmp_path / "plain"      # a plain directory (not a mount point) carrying the right sentinel
        (plain / "out").mkdir(parents=True, exist_ok=True)
        (plain / ".mio-sentinel").write_text(token)
        (plain / "cache").mkdir(exist_ok=True)
        (plain / "out" / "q1.json").write_text("x")
        refused = subprocess.run(["sh", "-c", script.replace(str(mount), str(plain))], input="x", text=True, capture_output=True)
        assert refused.returncode != 0 and (plain / "cache").exists() and (plain / "out" / "q1.json").exists() \
            and not (plain / "out" / "cert.1.spec.json").exists(), "a directory that is not the mount is never touched"
        try:
            (mount / "cache").mkdir()
            (mount / "out").mkdir()
            (mount / "out" / "q1.json").write_text("x")
            sentinel.write_text("another-token")
            wrong = subprocess.run(["sh", "-c", script], input="x", text=True, capture_output=True)
            assert wrong.returncode != 0 and (mount / "cache").exists() and (mount / "out" / "q1.json").exists() \
                and not (mount / "out" / "cert.1.spec.json").exists(), "the wrong sentinel refuses"
            sentinel.write_text(token)
            done = subprocess.run(["sh", "-c", script], input="x", text=True, capture_output=True)
            assert done.returncode == 0, done.stderr
            if "rm -rf" in body:
                assert not (mount / "cache").exists()
            elif "rm -f" in body:
                assert not (mount / "out" / "q1.json").exists()
            else:
                assert (mount / "out" / "cert.1.spec.json").read_text() == "x"
        finally:
            sentinel.unlink(missing_ok=True)
            shutil.rmtree(mount / "out", ignore_errors=True)
            shutil.rmtree(mount / "cache", ignore_errors=True)


# -- fixes from the rules lens: sealed rejection fields, holdout out dir, pins, spec hashes, visible fallbacks -----------
def _sealed_coordinator(tmp_path, **kw):
    plan = _plan()
    for q in plan["quarters"]:
        q["id"] = {"2022q1": "q1", "2022q2": "q2"}[q["id"]]
    from mio_parallel_queue import Phases
    phases = Phases(tmp_path / "q", ["q1"], ["q2"], ["m1", "m2"], 1)
    return _coordinator(tmp_path, [FakeHost("mac", 1), FakeHost("wsl", 0)], Documents(), plan=plan, phases=phases, **kw), phases


def test_a_sealed_quarters_rejections_show_unit_check_and_count_only(tmp_path):
    """Owner ruling: a sealed 2022-23 rejection shows no ratio, week, time, run or category in status, console or reasons."""
    c, phases = _sealed_coordinator(tmp_path)
    c.rejections.record("q1/rules/m1", _rejection(ratio=7.5), run="r1", at=c._stamp())
    c.rejections.record("q2/rules/m1", _rejection(week="2022-05-02", check="model_over_audit", ratio=123.0), run="run-hidden", at=c._stamp())
    c.rejections.record("q2/rules/m2", _rejection(week="2022-05-09", check="incumbent_negative", category="numeric", ratio=3.0),
                        run="r3", at=c._stamp())
    c.rejections.record("unknown", _rejection(ratio=9.0), run="r4", at=c._stamp())
    status = c.write_status()
    by_unit = {r["unit"]: r for r in status["rejections"]["records"]}
    assert by_unit["q1/rules/m1"]["ratio"] == 7.5 and by_unit["q1/rules/m1"]["week"] == "2022-01-10", "a selection quarter is open"
    for unit in ("q2/rules/m1", "q2/rules/m2", "unknown"):
        assert set(by_unit[unit]) == {"unit", "check", "seq"}, unit
    assert status["rejections"]["open"] == 4
    assert status["rejections"]["reasons"] == {"tolerance:certificate_objective": 1, "sealed:model_over_audit": 1,
                                              "sealed:incumbent_negative": 1, "sealed:certificate_objective": 1}
    text = (tmp_path / "q" / "status.json").read_text()
    for hidden in ("123.0", "9.0", "2022-05-02", "2022-05-09", "run-hidden", "numeric"):
        assert hidden not in text, hidden
    line = c.rejection_line()
    assert "123" not in line and "numeric" not in line and "sealed:model_over_audit x1" in line and "7.5" in line, line
    assert "2022-05" not in line and "run-hidden" not in line
    ledger_text = (tmp_path / "q" / "rejections.jsonl").read_text()
    assert "123.0" in ledger_text and "2022-05-02" in ledger_text, "the owner-only ledger keeps the full row"
    phases.record(["m1"], "2026-01-01T00:00:00Z", closed=True)
    phases.unseal("2026-01-02T00:00:00Z")
    unsealed = {r["unit"]: r for r in c.write_status()["rejections"]["records"]}
    assert unsealed["q2/rules/m1"]["ratio"] == 123.0 and unsealed["q2/rules/m1"]["week"] == "2022-05-02", "after unseal the owner sees them"


def test_sealed_fallbacks_and_a_failed_sealed_arm_show_no_attempt_notes(tmp_path):
    """The fallback list, its console line and a failed arm's ``why`` of a sealed quarter carry no note, host or stage."""
    c, phases = _sealed_coordinator(tmp_path)
    for unit in ("q1/rules/m1", "q2/rules/m1"):
        attempt = c.queue.lease(unit, "mac", now=c._stamp())
        c.queue.transition(unit, "pending", attempt, note="stale lease killed")
        attempt = c.queue.lease(unit, "mac", now=c._stamp())
        c.queue.transition(unit, "rejected", attempt, note="rejected: action file identity differs from the lease")
    found = c.fallbacks()
    assert "stale lease killed" in found["q1/rules/m1"] and found["q2/rules/m1"] == "sealed"
    line = c.fallback_line()
    assert "q2/rules/m1: sealed" in line and "q1/rules/m1: attempt 1 on mac" in line
    assert "identity" not in line.split("q2/rules/m1")[1]
    c.queue.set_arm("q2", "rules", "failed", "/runs/x", {"ok": False, "stage": "incomplete_with_worker_files", "entry": {"rc": 3}})
    c.queue.set_arm("q1", "rules", "failed", "/runs/y", {"ok": False, "stage": "incomplete_with_worker_files", "entry": {"rc": 3}})
    by = {a["arm_key"]: a for a in c.write_status()["arms"]}
    assert "incomplete" not in json.dumps(by["q2/rules"]) and "exit" not in json.dumps(by["q2/rules"]) and by["q2/rules"]["status"] == "failed"
    assert "incomplete_with_worker_files" in by["q1/rules"]["why"], "a selection arm keeps its reason"


def test_a_sealed_arms_waiting_reason_is_a_bare_word_in_status_and_leaks_neither_a_failure_nor_a_count(tmp_path):
    """Item 5: ``waiting`` carried a sealed failure text (stage, refusal, exit, error) and 'N of M units unsettled'."""
    plan = _plan()
    for q in plan["quarters"]:
        q["id"] = {"2022q1": "q1", "2022q2": "q2"}[q["id"]]
    plan["quarters"][1]["arms"].append({"id": "fine", "methods": ["m1"], "bundle_sha256": BUNDLE, "plan_sha256": PLAN,
                                        "spec": {"run": "f"}})
    for arm in plan["quarters"][1]["arms"]:
        arm["registry"] = "/reg/shared.sqlite"
    from mio_parallel_queue import Phases
    phases = Phases(tmp_path / "q", ["q1"], ["q2"], ["m1", "m2"], 1)
    c = _coordinator(tmp_path, [FakeHost("mac", 1), FakeHost("wsl", 0)], Documents(), plan=plan, phases=phases)
    c.queue.set_arm("q2", "rules", "failed", "/runs/x",
                    {"ok": False, "stage": "run", "entry": {"rc": 3, "refused": "ticker_lock"}, "error": "ValueError: boom"})
    _tick(c)
    status = c.write_status()
    waiting_sealed = {k: v for k, v in status["waiting"].items() if k.startswith("q2/")}
    text = json.dumps(waiting_sealed) + json.dumps([a for a in status["arms"] if a["arm_key"].startswith("q2/")])
    for hidden in ("ticker_lock", "boom", "exit 3", "refused", "unsettled", "units", "failed ("):
        assert hidden not in text, hidden
    assert waiting_sealed and set(waiting_sealed.values()) == {"sealed"}
    assert status["waiting"]["q1/rules"] == "2 of 2 units unsettled", "a selection arm keeps its reason"
    phases.record(["m1"], "2026-01-01T00:00:00Z", closed=True)
    phases.unseal("2026-01-02T00:00:00Z")
    c.reasons = {}
    _tick(c)
    assert "ticker_lock" in c.write_status()["waiting"]["q2/fine"], "after unseal the owner sees the reason"


def test_the_console_line_shows_the_largest_ratio_so_a_garbage_file_does_not_read_as_a_tolerance_issue(tmp_path):
    """C4."""
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], Documents())
    c.rejections.record("2022q1/rules/m1", _rejection(ratio=2.5), run="r1", at=c._stamp())
    c.rejections.record("2022q1/rules/m2", _rejection(category="numeric", ratio=4.2e7), run="r2", at=c._stamp())
    line = c.rejection_line()
    assert "numeric:certificate_objective" in line and "largest ratio 4.2e+07" in line, line
    c.rejections.record("2022q1/rules/m3", _rejection(category="invalid_trade", check="capacity", ratio=None), run="r3", at=c._stamp())
    assert "largest ratio 4.2e+07" in c.rejection_line()


def test_the_sealed_quarter_progress_files_hold_no_status_word_and_live_under_holdout(tmp_path):
    """M2: the worker's progress line carried each week's status word; a holdout unit also wrote under ``out/``."""
    bundle = _stub_bundle(tmp_path)
    sealed = lambda quarter: quarter == "q2"  # noqa: E731
    host = LocalHost("wsl", 1, sys.executable, lambda unit: bundle, tmp_path / "work", tmp_path / "out",
                     sealed_out_root=tmp_path / "q" / "holdout" / "out", sealed_quarter=sealed)
    open_unit, held = _unit(), {**_unit(unit_id="q2/a/m"), "quarter": "q2"}
    assert host._out(open_unit, 1).parent == tmp_path / "out"
    assert host._out(held, 1).parent == tmp_path / "q" / "holdout" / "out"
    (tmp_path / "q").mkdir()
    os.chmod(tmp_path / "q", 0o700)
    host.spawn(held, 1)
    deadline = time.time() + 30
    while host.fetch(held, 1) is None and time.time() < deadline:
        time.sleep(0.1)
    assert host.fetch(held, 1) is not None and not list((tmp_path / "out").glob("*")), "nothing of a holdout unit in out/"
    assert oct((tmp_path / "q" / "holdout" / "out").stat().st_mode & 0o777) == "0o700"


def test_build_refuses_a_c_mount_or_skill_directory_other_than_the_pinned_ones(tmp_path, monkeypatch):
    """M4: a config could point the 3 GB stop at a roomy disk, or the certificate at its own script."""
    from mio_parallel_queue import C_MOUNT, _build
    monkeypatch.setattr(_queue_module, "SKILL_SCRIPTS", REAL_SKILL)
    base = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(_stub_runtime(tmp_path)), "quarters": []}
    for key, other in (("c_mount", str(tmp_path)), ("compute_scripts", str(tmp_path / "mine"))):
        with pytest.raises(ValueError, match=f"{key} is pinned"):
            _build({**base, key: other})
    assert C_MOUNT == "/mnt/c"
    _build({**base, "c_mount": C_MOUNT, "compute_scripts": REAL_SKILL})  # exactly the pin is accepted


def test_a_unit_that_spent_its_mac_attempts_falls_back_to_wsl_with_a_visible_reason_and_no_pause(tmp_path):
    """C8: stale twice used to turn into a quiet 'rejected' row; status and the console now say why."""
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path, [mac, wsl], Documents())
    unit = "2022q1/control/c1"
    for note in ("stale lease killed", "worker exited without a result"):
        attempt = c.queue.lease(unit, "mac", now=c._stamp())
        c.queue.transition(unit, "pending", attempt, note=note)
    c.tick()
    row = c.queue.unit(unit)
    ended = [h for h in row["history"] if h["event"] == "rejected"]
    assert ended and "no usable host" in ended[0]["note"], "the row moved on to solved_local, the history keeps why"
    status = c.write_status()
    why = status["fallbacks"][unit]
    assert "attempt 1 on mac: stale lease killed" in why and "attempt 2 on mac: worker exited without a result" in why, why
    line = c.fallback_line()
    assert unit in line and "stale lease killed" in line and "no pause" in line, line
    assert c.fallback_line() is None, "shown once per change"
    assert status["rejections"]["open"] == 0 and "rejections" not in status["waiting"], "a fallback pauses nothing"
    assert [u for u in c.queue.units(status="leased")], "the Mac keeps taking the other units"


def test_a_failed_documents_rejections_reach_the_ledger(tmp_path):
    """Test gap: an outcome with ok False still carries the rejections it saw (a run that died after rejecting a file)."""
    class Failing(Documents):
        def __call__(self, spec, quarter, arm, recorded_dir, asof):
            return {"ok": False, "stage": "run", "entry": {"run_dir": f"/runs/{quarter}-{arm}"},
                    "rejections": [_rejection("c1", ratio=3.5)]}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], Failing(),
                     plan={"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
                         {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}}]}]})
    _tick(c)
    assert c.queue.arm("q", "a")["status"] == "failed"
    [row] = c.rejections.state()["records"]
    assert (row["unit"], row["week"], row["check"], row["ratio"], row["run"]) == (
        "q/a/c1", "2022-01-10", "certificate_objective", 3.5, "runs/q-a")


def test_selection_closed_needs_every_arm_of_every_selection_quarter(tmp_path):
    """Test gap: ``all`` of the quarters and ``all`` of their arms; one open arm or one empty quarter is not closed."""
    from mio_parallel_queue import Phases
    phases = Phases(tmp_path, ["a", "b"], ["c"], ["f"], 1)
    row = lambda q, status, arm="x": {"quarter": q, "arm": arm, "status": status}  # noqa: E731
    assert phases.selection_closed([row("a", "closed"), row("b", "closed"), row("c", "waiting")]) is True
    assert phases.selection_closed([row("a", "closed"), row("b", "closed", "x"), row("b", "waiting", "y")]) is False, "one open arm"
    assert phases.selection_closed([row("a", "closed")]) is False, "a selection quarter with no arms"
    assert phases.selection_closed([row("a", "closed"), row("b", "failed")]) is False
    assert phases.selection_closed([]) is False


def test_a_holdout_units_aside_copies_go_under_the_holdout_path_and_a_selection_unit_beside_the_queue(tmp_path):
    """Test gap: ``_keep_aside`` chooses the sealed base for a holdout quarter."""
    c, phases = _sealed_coordinator(tmp_path)
    for quarter, base in (("q1", tmp_path / "q"), ("q2", tmp_path / "q" / "holdout")):
        unit = {"unit_id": f"{quarter}/rules/m1", "quarter": quarter}
        c._keep_aside("rejected", unit, 2, b"{}")
        assert (base / "rejected" / f"{quarter}__rules__m1.2.json").read_bytes() == b"{}"
    assert not (tmp_path / "q" / "rejected" / "q2__rules__m1.2.json").exists()
    assert oct((tmp_path / "q" / "holdout" / "rejected").stat().st_mode & 0o777) == "0o700"


def test_recorded_from_prints_unit_check_and_count_only_for_a_holdout_directory(tmp_path):
    """The helper keeps the whole list (the ledger needs it); the command's view of a holdout directory cuts it."""
    from mio_parallel_queue import recorded_from_view
    for parent, sealed in ((tmp_path / "queue" / "sealed" / "runs", False), (tmp_path / "queue" / "holdout" / "sealed" / "runs", True)):
        run = parent / "run-x"
        run.mkdir(parents=True)
        (run / "carry.json").write_text(json.dumps({"close_packet": {"receipt": {"path": "receipt.json"}}}))
        (run / "receipt.json").write_text(json.dumps({"complete": True, "results": [{"id": "m1", "status": "feasible_hold"}], "audit": {
            "recorded_from": {"m1": {"host": {"label": "macbook-77"}, "attempt": 3, "sha256": "ab" * 32,
                                     "bundle_manifest_sha256": "cd" * 32}},
            "recorded_rejections": [_rejection(check="model_over_audit", ratio=123.0), _rejection(check="model_over_audit", ratio=5.0, week="2022-03-07")]}}))
        report = recorded_from_report(run)
        assert report["sealed"] is sealed and len(report["rejections"]) == 2 and report["rejections"][0]["ratio"] == 123.0
        shown = recorded_from_view(report)
        if sealed:
            assert shown["rejections"] == [] and shown["rejections_sealed"] == [{"method": "m1", "check": "model_over_audit", "count": 2}]
            assert "123" not in json.dumps(shown) and "2022-01" not in json.dumps(shown) and "tolerance" not in json.dumps(shown)
            text = json.dumps(shown)
            for hidden in ("macbook", "feasible_hold", "abab", "cdcd", '"attempt"', '"host"'):
                assert hidden not in text, hidden
            assert shown["recorded_from_count"] == 1 and shown["methods_count"] == 1 and "recorded_from" not in shown
        else:
            assert shown["rejections"][0]["ratio"] == 123.0 and "rejections_sealed" not in shown
            assert shown["recorded_from"]["m1"]["host"] == "macbook-77", "a selection run keeps the per-method account"


def test_the_recorded_from_command_cuts_a_holdout_directory(tmp_path, capsys):
    run = tmp_path / "queue" / "holdout" / "sealed" / "runs" / "run-x"
    run.mkdir(parents=True)
    (run / "carry.json").write_text(json.dumps({"close_packet": {"receipt": {"path": "receipt.json"}}}))
    (run / "receipt.json").write_text(json.dumps({"complete": True, "results": [], "audit": {
        "recorded_rejections": [_rejection(check="model_over_audit", ratio=123.0)]}}))
    assert main(["recorded-from", str(run)]) == 0
    out = capsys.readouterr().out
    assert "123" not in out and "model_over_audit" in out and '"count": 1' in out


# -- skeptic round 3: live pause, sealed ledger, hashes, crashes, reopen, WSL-first ------------------------------------------
def _log_event(path, **fields):
    """Append one ``recorded_rejected`` event line the way the pipeline's logger writes it (whole lines, flushed)."""
    event = {"type": "recorded_rejected", "method": "m1", "week": "2022-01-10", "check": "certificate_objective",
             "category": "tolerance", "ratio": 2.5, **fields}
    with open(path, "a") as handle:
        handle.write("INFO dskit.pipeline.study:ledger_studies.py:2712 MIO_EVENT " + json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


class LiveDocuments(Documents):
    """A gated document that returns the rejections its (test-written) log already showed, from the run directory it logged in."""

    def __init__(self, run_dir, rejections, gated_arm, **kw):
        super().__init__(**kw)
        self.run_dir, self.rejections, self.gated_arm = run_dir, rejections, gated_arm

    def __call__(self, spec, quarter, arm, recorded_dir, asof):
        if f"{quarter}/{arm}" != self.gated_arm:
            return super().__call__(spec, quarter, arm, recorded_dir, asof)
        outcome = super().__call__(spec, quarter, arm, recorded_dir, asof)
        return {**outcome, "run_dir": str(self.run_dir), "rejections": self.rejections}


def test_a_rejection_in_a_running_documents_log_pauses_the_mac_before_the_document_returns(tmp_path):
    """Pause: the ledger used to learn of a rejection only when the whole document returned; the Mac kept leasing meanwhile."""
    gate = threading.Event()
    run_dir = tmp_path / "sealed" / "runs" / "run-2022q1-control" / "20261105T000000"
    run_dir.mkdir(parents=True)
    log = run_dir / "run.log"
    docs = LiveDocuments(run_dir, [_rejection("c1", ratio=3.2)], "2022q1/control", gate=gate)
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path, [mac, wsl], docs, live_runs=lambda q, a: [run_dir] if (q, a) == ("2022q1", "control") else [])
    try:
        c.tick()
        [first] = c.queue.units(status="leased")
        assert first["unit_id"] == "2022q1/control/c1"
        mac.finish(first["unit_id"], 1)
        c.tick()  # c1 accepted; the control document starts and blocks on the gate; the Mac leases the next unit
        assert [t.is_alive() for t in c._threads.values()] == [True]
        [second] = c.queue.units(status="leased")
        assert c.rejections.state()["open"] == 0 and len(mac.spawned) == 2
        log.write_text("INFO noise\n")
        _log_event(log, method="c1", ratio=3.2)
        with open(log, "a") as handle:
            handle.write('INFO MIO_EVENT {"type":"recorded_rejected","method":"half')  # a line still being written: not read yet
        mac.finish(second["unit_id"], 1)
        c.tick()
        assert c._threads and all(t.is_alive() for t in c._threads.values()), "the document has NOT returned"
        assert c.rejections.state()["open"] == 1, "the rejection is in the ledger while the document still runs"
        c.tick()
        c.tick()
        assert len(mac.spawned) == 2 and not c.queue.units(status="leased"), "no Mac lease beyond the one in flight when it was logged"
        assert "clear-rejections" in json.loads((tmp_path / "q" / "status.json").read_text())["waiting"]["rejections"]
        log.write_text(log.read_text().rsplit("\n", 1)[0] + "\n")  # truncated + rewritten log: nothing breaks
        gate.set()
        c.join_documents(10)
        state = c.rejections.state()
        assert state["open"] == 1 and len(state["records"]) == 1, "the document's own list is the same fact: no second row"
        assert state["records"][0]["run"] == "run-2022q1-control/20261105T000000"
    finally:
        gate.set()
        c.join_documents(10)


def test_a_replaced_log_of_the_same_or_larger_size_is_read_from_its_start(tmp_path):
    """Item 7: replacement was detected by a smaller size only; a longer new file was read from the old offset."""
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], Documents())
    log = tmp_path / "run.log"
    _log_event(log, method="a", ratio=1.5)
    assert [r["method"] for r in c._new_rejections(log)] == ["a"]
    assert c._new_rejections(log) == [], "nothing new"
    fresh = tmp_path / "fresh.log"
    _log_event(fresh, method="b", ratio=2.5, week="2022-01-17")
    _log_event(fresh, method="c", ratio=3.5, week="2022-01-24")
    assert fresh.stat().st_size > log.stat().st_size
    os.replace(fresh, log)  # a new inode, longer than the old position
    assert [r["method"] for r in c._new_rejections(log)] == ["b", "c"]


def test_a_log_is_read_in_bounded_chunks_and_a_half_line_waits(tmp_path, monkeypatch):
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], Documents())
    monkeypatch.setattr(Coordinator, "LOG_CHUNK_BYTES", 64)
    log = tmp_path / "run.log"
    for i in range(20):
        _log_event(log, method=f"m{i}", ratio=1.5 + i)
    with open(log, "a") as handle:
        handle.write('INFO MIO_EVENT {"type":"recorded_rejected","method":"half')
    sizes = []
    real_open = open

    def spy(path, *args, **kw):
        handle = real_open(path, *args, **kw)
        if str(path) == str(log) and "b" in (args[0] if args else "r"):
            read = handle.read
            class Spy:
                def __getattr__(self, name):
                    return getattr(handle, name)
                def __enter__(self):
                    handle.__enter__()
                    return self
                def __exit__(self, *exc):
                    return handle.__exit__(*exc)
                def read(self, n=-1):
                    sizes.append(n)
                    return read(n)
            return Spy()
        return handle
    monkeypatch.setattr("builtins.open", spy)
    rows = c._new_rejections(log)
    monkeypatch.undo()
    assert [r["method"] for r in rows] == [f"m{i}" for i in range(20)]
    assert sizes and all(n == 64 for n in sizes), sizes
    assert c._new_rejections(log) == [], "the half line is not consumed"
    with open(log, "a") as handle:
        handle.write('","week":"2022-02-07","check":"capacity","category":"invalid_trade","ratio":null}\n')
    assert [r["method"] for r in c._new_rejections(log)] == ["half"]


def test_a_short_os_write_still_appends_the_whole_row_and_a_torn_tail_never_glues(tmp_path, monkeypatch):
    """Item 8: ``os.write`` may write fewer bytes than asked; and a crash mid-row leaves a tail the next row must not join."""
    from mio_parallel_queue import RejectionLedger
    ledger = RejectionLedger(tmp_path / "rejections.jsonl")
    real = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real(fd, bytes(data)[:7]))
    assert ledger.record("q/a/m1", _rejection(ratio=2.5), run="r1", at="2026-01-01T00:00:00Z")
    monkeypatch.undo()
    text = (tmp_path / "rejections.jsonl").read_text()
    assert text.endswith("\n") and len(text.splitlines()) == 1 and json.loads(text)["unit"] == "q/a/m1"
    with open(tmp_path / "rejections.jsonl", "a") as handle:
        handle.write('{"kind":"reject","unit":"q/a/m2","che')  # the torn tail of a crashed writer
    assert ledger.record("q/a/m3", _rejection(ratio=4.5), run="r3", at="2026-01-01T00:00:00Z")
    lines = (tmp_path / "rejections.jsonl").read_text().splitlines()
    assert len(lines) == 3 and json.loads(lines[2])["unit"] == "q/a/m3" and lines[1].startswith('{"kind":"reject","unit":"q/a/m2"')
    state = ledger.state()
    assert state["open"] == 3 and any("unreadable_ledger_line" in name for name in state["reasons"]), state["reasons"]


def test_a_sealed_arms_live_rejection_pauses_the_mac_and_shows_the_reader_only_unit_and_check(tmp_path):
    run_dir = tmp_path / "holdout" / "sealed" / "runs" / "run-q2-rules" / "d"
    run_dir.mkdir(parents=True)
    gate = threading.Event()
    c, phases = _sealed_coordinator(tmp_path, live_runs=lambda q, a: [run_dir] if (q, a) == ("q2", "rules") else [])
    c.documents = LiveDocuments(run_dir, [], "q2/rules", gate=gate)
    try:
        c._threads["q2/rules"] = threading.Thread(target=gate.wait, args=(10,), daemon=True)
        c._threads["q2/rules"].start()
        _log_event(run_dir / "run.log", week="2022-05-02", ratio=123.0)
        c.tick()
        assert c.rejections.state()["open"] == 1
        status = json.loads((tmp_path / "q" / "status.json").read_text())
        assert status["rejections"]["records"] == [{"unit": "q2/rules/m1", "check": "certificate_objective", "seq": 1}]
        assert "123" not in json.dumps(status) and "2022-05-02" not in json.dumps(status)
        assert "123" not in c.rejection_line()
        assert "123.0" in (tmp_path / "q" / "rejections.jsonl").read_text(), "the full row stays in the owner-only ledger"
    finally:
        gate.set()
        c._threads.clear()


def test_a_completed_holdout_arms_rejection_reaches_the_ledger_and_pauses_the_mac(tmp_path):
    """Major 1: ``recorded_from_report`` emptied the list for a holdout run dir, so the ledger got no row and the Mac went on."""
    run = tmp_path / "queue" / "holdout" / "sealed" / "runs" / "run-2022q1-rules"
    run.mkdir(parents=True)
    (run / "carry.json").write_text(json.dumps({"close_packet": {"receipt": {"path": "r.json"}}}))
    (run / "r.json").write_text(json.dumps({"complete": True, "results": [{"id": "m1", "status": "ok"}], "audit": {
        "recorded_from": {}, "recorded_rejections": [_rejection("m1", week="2022-02-07", check="model_over_audit", ratio=3.0)]}}))
    d = DocumentRunner.__new__(DocumentRunner)
    d.run = lambda step, cfg, asof: {"ok": True, "run_dir": str(run), "minutes": 0, "document_hash": "h"}
    d.recorded_config = lambda *a: "cfg"
    d.closing = lambda *a, **k: "cfg2"
    outcome = DocumentRunner.run_arm(d, {"initialize": "i", "run": "r"}, "2022q1", "rules", tmp_path / "rec", "2022-01-03")
    assert [r["check"] for r in outcome["rejections"]] == ["model_over_audit"], "the ledger gets the whole list"

    class Replaying(Documents):
        def __call__(self, spec, quarter, arm, recorded_dir, asof):
            return {**super().__call__(spec, quarter, arm, recorded_dir, asof), "run_dir": str(run), "rejections": outcome["rejections"]}
    from mio_parallel_queue import Phases
    plan = _plan()
    for q in plan["quarters"]:
        q["id"] = {"2022q1": "q1", "2022q2": "q2"}[q["id"]]
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path, [mac, wsl], Replaying(), plan=plan, phases=Phases(tmp_path / "q", ["q1"], ["q2"], ["m1", "m2"], 1))
    c.queue.set_arm("q1", "rules", "closed")  # one registry: q2's document follows q1's
    for unit in c.queue.units(quarter="q2"):
        attempt = c.queue.lease(unit["unit_id"], "wsl", now=c._stamp())
        c.queue.transition(unit["unit_id"], "verified", attempt)
    c.tick()
    c.join_documents()
    placed = len(mac.spawned)
    c.tick()
    assert c.queue.arm("q2", "rules")["status"] == "closed"
    [row] = c.rejections.state()["records"]
    assert (row["unit"], row["check"], row["week"], row["ratio"]) == ("q2/rules/m1", "model_over_audit", "2022-02-07", 3.0)
    assert len(mac.spawned) == placed and c.queue.units(status="pending"), "the Mac is paused: nothing new is leased"
    shown = json.loads((tmp_path / "q" / "status.json").read_text())["rejections"]
    assert shown["records"] == [{"unit": "q2/rules/m1", "check": "model_over_audit", "seq": 1}]


def test_a_rejection_repeated_by_a_reopened_arms_rerun_is_new(tmp_path):
    """D: the run directory name repeats on a retry; its step root (``.retryN``) is what tells the reruns apart."""
    from mio_parallel_queue import _run_key
    assert _run_key("/s/runs/run-q-a/d1") != _run_key("/s/runs/run-q-a.retry1/d1")
    seen = []

    class Rerun(Documents):
        def __call__(self, spec, quarter, arm, recorded_dir, asof):
            seen.append(1)
            return {"ok": True, "complete": True, "run_dir": f"/s/runs/run-{quarter}-{arm}{'.retry1' if len(seen) > 1 else ''}/d1",
                    "recorded_from": [], "rejections": [_rejection("a1", ratio=2.0)]}
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)], Rerun(),
                     plan={"quarters": [{"id": "q", "asof": "2026-01-01", "arms": [
                         {"id": "a", "methods": [], "bundle_sha256": None, "plan_sha256": None, "spec": {}}]}]})
    _tick(c)
    assert c.rejections.state()["open"] == 1 and c.rejections.clear(c._stamp()) == 1
    c.queue.set_arm("q", "a", "waiting")
    _tick(c)
    assert c.rejections.state()["open"] == 1, "the rerun's rejection is not swallowed by the earlier, cleared one"


def test_numbers_hash_alike_whether_a_config_writes_1_or_1_point_0_and_the_nine_pins_hold_for_both():
    from mio_parallel_queue import Phases
    from test_ledger_studies import FORMULATIONS
    floaty = {"id": "f", "portfolio_fraction": 1., "rho": 0., "shock": {"moves": [-.1, 1.], "beta_lookback": 60.}, "component_fraction": None}
    inty = {"id": "f", "portfolio_fraction": 1, "rho": 0, "shock": {"moves": [-.1, 1], "beta_lookback": 60}, "component_fraction": None}
    assert Phases.spec_hash(floaty) == Phases.spec_hash(inty)
    assert Phases.spec_hash({"id": "f", "flag": True}) != Phases.spec_hash({"id": "f", "flag": 1}), "a boolean is not a number"
    assert Phases.spec_hash({"id": "f", "rho": .1}) != Phases.spec_hash({"id": "f", "rho": .2})
    config = json.loads((Path(__file__).resolve().parents[1] / "docs/plans/mio-parallel/queue.example.json").read_text())
    pins = config["selection_formulation_hashes"]
    assert sorted(pins) == sorted(FORMULATIONS)
    as_int = lambda v: int(v) if type(v) is float and v == int(v) else (  # noqa: E731
        {k: as_int(x) for k, x in v.items()} if isinstance(v, dict) else [as_int(x) for x in v] if isinstance(v, list) else v)
    for name, spec in FORMULATIONS.items():
        assert Phases.spec_hash({"id": name, **spec}) == pins[name], name
        assert Phases.spec_hash({"id": name, **as_int(spec)}) == pins[name], f"{name} written with ints"
    phases = Phases("/tmp", ["a"], ["b"], list(FORMULATIONS), 3, pins)
    phases.check_methods("a", [{"id": n, **as_int(spec)} for n, spec in FORMULATIONS.items()])  # plan time accepts the int forms
    with pytest.raises(ValueError, match="differs from its pinned spec"):
        phases.check_methods("a", [{"id": n, **({**spec, "rho": .5} if n == "nominal_lp" else spec)} for n, spec in FORMULATIONS.items()])


def test_similarity_survives_an_integer_objective_past_float_range():
    big = json.loads(_body("m1", 1, objectives={"2022-01-03": 10**400, "2022-01-10": 10**400})[0])
    assert Similarity.compare(big, json.loads(json.dumps(big))) == (False, "malformed")


def test_a_returned_file_nested_past_the_parsers_depth_is_a_recorded_rejection_not_a_crash(tmp_path):
    mac = FakeHost("mac", 1)
    c = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)])
    c.tick()
    [unit] = c.queue.units(status="leased")
    raw = b"[" * 200000 + b"]" * 200000
    mac.results[(unit["unit_id"], 1)] = (raw, hashlib.sha256(raw).hexdigest() + "  x\n")
    c.tick()
    [record] = c.rejections.state()["records"]
    assert (record["unit"], record["check"], record["category"]) == (unit["unit_id"], "action_file_schema", "file")
    assert c.queue.unit(unit["unit_id"])["status"] == "pending"


def test_a_malformed_history_row_cannot_stop_the_status_pass(tmp_path):
    import sqlite3
    c = _coordinator(tmp_path, [FakeHost("mac", 1), FakeHost("wsl", 0)])
    unit = "2022q1/control/c1"
    history = c.queue.unit(unit)["history"] + [{"event": "rejected"}, {"note": "x"}, {"event": "pending", "note": "n"}]
    con = sqlite3.connect(tmp_path / "q" / "queue.sqlite")
    con.execute("update units set history_json=? where unit_id=?", (json.dumps(history), unit))
    con.commit()
    con.close()
    assert c.write_status()["fallbacks"][unit] == "attempt None on None: n"
    assert c.fallback_line()


def test_an_unreadable_ledger_says_why_in_status_and_holds_the_mac(tmp_path):
    mac = FakeHost("mac", 1)
    c = _coordinator(tmp_path, [mac, FakeHost("wsl", 0)])
    (tmp_path / "q" / "rejections.jsonl").mkdir()  # reading a directory: IsADirectoryError
    c.tick()
    status = json.loads((tmp_path / "q" / "status.json").read_text())
    assert status["rejections"]["unreadable"] == "IsADirectoryError" and status["rejections"]["open"] is None
    assert "cannot be read" in status["waiting"]["rejections"] and not mac.spawned
    assert "cannot be read (IsADirectoryError)" in c.rejection_line()
    # a pass that dies on the ledger still leaves its cause in status.json
    c2 = _coordinator(tmp_path / "again", [mac, FakeHost("wsl", 0)])
    c2.tick()
    [unit] = c2.queue.units(status="leased")
    (tmp_path / "again" / "q" / "rejections.jsonl").mkdir()
    mac.results[(unit["unit_id"], 1)] = (b"{}", "0" * 64 + "  x\n")
    with pytest.raises(OSError):
        c2.tick()
    assert "IsADirectoryError" in json.loads((tmp_path / "again" / "q" / "status.json").read_text())["waiting"]["pass_failed"]


def test_clear_cli_through_and_the_rejection_line_reprints_when_only_the_cleared_count_changes(tmp_path):
    c = _coordinator(tmp_path, [FakeHost("wsl", 1)])
    c.rejections.record("2022q1/rules/m1", _rejection(), run="r1", at=c._stamp())
    c.rejections.record("2022q1/rules/m2", _rejection(week="2022-01-17"), run="r1", at=c._stamp())
    assert c.rejections.record("2022q1/rules/m2", _rejection(week="2022-01-24"), run="r1", at=c._stamp()), "another week is another fact"
    assert c.rejection_line() and c.rejection_line() is None
    c.rejections.clear(c._stamp(), through=3)
    assert "cleared 3" in c.rejection_line(), "open 0 either way: only the cleared count moved"


def test_a_non_primary_unit_and_a_plain_retry_are_not_fallbacks(tmp_path):
    c = _coordinator(tmp_path, [FakeHost("mac", 1), FakeHost("wsl", 0)])
    unit = "2022q1/control/c1"
    attempt = c.queue.lease(unit, "mac", now=c._stamp())
    c.queue.transition(unit, "pending", attempt, note="stale lease killed")
    assert c.fallbacks() == {}, "a retry that is still pending is not a fallback"
    c2 = _coordinator(tmp_path / "p", [FakeHost("mac", 1), FakeHost("wsl", 0)], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    twin = c2.twin_unit
    attempt = c2.queue.lease(twin, "wsl", now=c2._stamp())
    c2.queue.transition(twin, "rejected", attempt, note="parity failed")
    assert c2.fallbacks() == {}, "the parity twin is not a unit the document solves"


def test_similarity_edges_exactly_one_percent_none_objectives_and_the_week_set():
    near = Similarity._near
    assert near(0., 0.01, Similarity.REL), "exactly 1% of the one-dollar floor is within"
    assert not near(0., 0.0100001, Similarity.REL)
    assert near(100., 101., Similarity.REL) and not near(100., 102., Similarity.REL)
    assert near(None, None, Similarity.REL) and not near(None, 5., Similarity.REL) and not near(5., None, Similarity.REL)
    one = json.loads(_body("m1", 1)[0])
    two = json.loads(_body("m1", 1)[0])
    assert Similarity.actions_match(one, two) is True
    del two["actions"]["decisions"]["2022-01-10"]
    assert Similarity.actions_match(one, two) is False and Similarity.actions_match(two, one) is False, "different week sets"


def test_wsl_only_config_builds_no_mac_no_certificate_and_no_parity(tmp_path, monkeypatch):
    """The WSL-first route: no ``mac`` key, ``wsl_slots`` above zero, no ``parity`` (a stray ``parity`` is ignored without a Mac)."""
    from mio_parallel_queue import _build
    monkeypatch.setattr(_queue_module, "SKILL_SCRIPTS", REAL_SKILL)
    base = {"queue_root": str(tmp_path / "q"), "python": sys.executable, "runtime": str(_stub_runtime(tmp_path)), "quarters": [],
            "wsl_slots": 2}
    c = _build(base)
    assert [h.name for h in c.hosts] == ["wsl"] and c.hosts[0].slots == 2
    assert c.parity_unit is None and c.parity_state() is None and c.twin_unit is None
    assert _build({**base, "parity": {"unit": "2019q4/rules/x"}}).parity_state() is None


def test_wsl_first_then_adding_the_mac_later_keeps_what_wsl_finished(tmp_path):
    """No Mac at first; later the same queue root gains a Mac with a parity unit that is still unsolved. Finished units survive."""
    plan = _parity_plan()
    docs = Documents()
    wsl = FakeHost("wsl", 1)
    first = _coordinator(tmp_path, [wsl], docs, plan=plan, probe=_probe(wsl=80.))
    assert first.parity_state() is None
    first.tick()
    [leased] = first.queue.units(status="leased")
    wsl.finish(leased["unit_id"], 1)
    first.tick()
    solved = {u["unit_id"]: u["status"] for u in first.queue.units() if u["status"] == "verified"}
    assert len(solved) == 1 and not (tmp_path / "q" / "parity.json").exists()
    # the second coordinator: same root, a Mac now, parity.unit one WSL has not solved
    pending = [u["unit_id"] for u in first.queue.units(status="pending") if u["role"] == "primary"]
    assert pending
    mac, wsl2 = FakeHost("mac", 1), FakeHost("wsl", 1)
    second = Coordinator(tmp_path / "q", plan, [mac, wsl2], docs, _probe(wsl=80.), {"unit_ram_gb": 8, "document_ram_gb": 8},
                         Clock(), parity={"unit": pending[0]})
    assert {u["unit_id"]: u["status"] for u in second.queue.units() if u["unit_id"] in solved} == solved, "finished units survive"
    second.tick()
    placed = {sp[0] for sp in mac.spawned + wsl2.spawned}
    assert not placed & set(solved), "nothing WSL finished is solved again"
    assert [sp[0] for sp in mac.spawned] == [pending[0]], "the Mac is gated to the parity unit until the comparison"
    # and a parity unit WSL already solved (so leased) is refused rather than left to wait forever
    with pytest.raises(ValueError, match="leased on a host other than the Mac"):
        Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 1)], docs, _probe(wsl=80.),
                    {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": next(iter(solved))})


def test_a_parity_unit_wsl_already_leased_is_refused_so_wsl_cannot_be_compared_with_wsl(tmp_path):
    """A WSL-only session left a unit leased on WSL; naming it parity.unit later must not let WSL pass against WSL."""
    plan = _parity_plan()
    first = Coordinator(tmp_path / "q", plan, [FakeHost("wsl", 3)], Documents(), _probe(wsl=80.),
                        {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock())
    first.tick()
    [leased, *_] = first.queue.units(status="leased")
    assert leased["host"] == "wsl"
    with pytest.raises(ValueError, match="leased on a host other than the Mac"):
        Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 3)], Documents(), _probe(wsl=80.),
                    {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": leased["unit_id"]})


def test_a_restart_after_the_mac_parity_unit_was_rejected_and_solved_locally_starts_and_stays_gated(tmp_path):
    """The Mac held the unit, so this is no WSL-first queue: restart without a verdict, other units released."""
    plan = _parity_plan()
    first = Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 1)], Documents(), _probe(wsl=80.),
                        {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": "q/a/m1"})
    first.tick()
    unit = first.queue.unit("q/a/m1")
    assert unit["host"] == "mac"
    first._transition("q/a/m1", "rejected", unit["attempt"], note="worker result refused")
    first._transition("q/a/m1", "solved_local", unit["attempt"])
    again = Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 1)], Documents(), _probe(wsl=80.),
                        {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": "q/a/m1"})
    again._parity_watch()
    assert again.parity_state() == "pending", "no verdict without a real comparison"
    assert again._released and "no comparison" in again.reasons["parity"]


def test_a_rejected_parity_unit_does_not_hold_its_arm_and_a_new_parity_unit_can_bring_the_mac_back(tmp_path):
    """No comparison can happen once the primary ended: the document runs, no verdict, the Mac stays gated."""
    plan = {"quarters": [{"id": q, "asof": "2026-01-01", "arms": [
        {"id": "rules", "methods": ["m1", "m2"], "bundle_sha256": "b"*64, "plan_sha256": "p"*64, "spec": {},
         "registry": "/r/reg"}]} for q in ("q1", "q2")]}
    limits = {"unit_ram_gb": 8, "document_ram_gb": 8}
    mac, wsl, docs = FakeHost("mac", 1), FakeHost("wsl", 2), Documents()
    coordinator = Coordinator(tmp_path / "q", plan, [mac, wsl], docs, _probe(wsl=80.), limits, Clock(),
                              parity={"unit": "q1/rules/m1"})
    coordinator.tick()
    unit = coordinator.queue.unit("q1/rules/m1")
    coordinator._transition("q1/rules/m1", "rejected", unit["attempt"], note="attempts spent")
    restarted = Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 2)], docs, _probe(wsl=80.), limits,
                            Clock(), parity={"unit": "q2/rules/m2"})
    assert restarted._comparison_possible(), "a unit still pending with no leases can bring the Mac back"
    assert "q1/rules/m1@parity" not in {u["unit_id"] for u in restarted._placeable(
        {a["arm_key"]: a for a in restarted.queue.arms()})}, "the old twin is not placed for a comparison that cannot happen"
    for _ in range(4):
        coordinator.tick()
        coordinator.join_spawns()
        for leased in coordinator.queue.units(status="leased"):
            if leased["host"] == "wsl" and leased["quarter"] == "q1":
                wsl.finish(leased["unit_id"], leased["attempt"])
    coordinator.join_documents()
    coordinator.tick()
    assert docs.calls, "the arm's document runs without the comparison"
    assert coordinator.parity_state() == "pending", "no verdict without a real comparison"
    assert "parity.unit" in coordinator.reasons["parity"], "the way out is named"
    assert not [s for s in mac.spawned if s[0] != "q1/rules/m1"], "the Mac stays gated"


def test_a_restart_with_the_parity_unit_still_leased_on_the_mac_is_accepted(tmp_path):
    plan = _parity_plan()
    first = Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 0)], Documents(), _probe(wsl=80.),
                        {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": "q/a/m1"})
    first.tick()
    assert first.queue.unit("q/a/m1")["host"] == "mac"
    again = Coordinator(tmp_path / "q", plan, [FakeHost("mac", 1), FakeHost("wsl", 0)], Documents(), _probe(wsl=80.),
                        {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock(), parity={"unit": "q/a/m1"})
    assert again.parity_state() == "pending"


def test_a_parity_pass_needs_the_first_result_to_come_from_the_mac(tmp_path):
    """Defence in depth: even if the parity unit somehow ran on WSL, no pass is written."""
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path, [mac, wsl], plan=_parity_plan(), parity={"unit": "q/a/m1"}, probe=_probe(wsl=80.))
    (tmp_path / "q" / "parity.json").unlink(missing_ok=True)  # the helper pre-passes it; this test wants it pending
    c.tick()
    mac.finish("q/a/m1", 1)
    wsl.finish("q/a/m1@parity", 1)
    wsl.finish("q/a/m1", 1)
    with c.queue._tx() as conn:  # test-only: pretend the primary ran on WSL
        conn.execute("UPDATE units SET host='wsl' WHERE unit_id='q/a/m1'")
    for _ in range(3):
        c.tick()
    assert not (tmp_path / "q" / "parity.json").exists(), "WSL against WSL never writes a pass"
    assert c.queue.unit("q/a/m1")["status"] == "returned", "nothing was published or verified"


def test_wsl_only_with_no_wsl_slots_is_refused_at_start_not_rejected_on_the_first_tick(tmp_path):
    with pytest.raises(ValueError, match="wsl_slots"):
        Coordinator(tmp_path / "q", _parity_plan(), [FakeHost("wsl", 0)], Documents(), _probe(wsl=80.),
                    {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock())
    ok = Coordinator(tmp_path / "q2", _parity_plan(), [FakeHost("wsl", 1)], Documents(), _probe(wsl=80.),
                     {"unit_ram_gb": 8, "document_ram_gb": 8}, Clock())
    ok.tick()
    assert not [u for u in ok.queue.units() if u["status"] == "rejected"]


def test_clear_rejections_with_an_unreadable_ledger_refuses_instead_of_crashing(tmp_path, capsys):
    root = tmp_path / "q"
    (root / "rejections.jsonl").mkdir(parents=True)
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"queue_root": str(root)}))
    assert main(["clear-rejections", str(config)]) == 1
    assert "cannot be read" in capsys.readouterr().err


# -- the Mac certificate's short check bundle (owner ruling: a 1-2 week packet, compared within a tolerance) -------------------
def test_check_study_narrows_the_selected_packet_to_at_most_the_asked_weeks():
    from mio_parallel_queue import check_study
    study = {"selected_packet": "p", "packets": [{"id": "p", "start": "2019-10-07", "end_before": "2020-01-06"},
                                                 {"id": "other", "start": "2019-10-07", "end_before": "2020-01-06"}]}
    narrowed = check_study(study, 2)
    assert [p["end_before"] for p in narrowed["packets"]] == ["2019-10-21", "2020-01-06"], "only the selected packet, two weeks"
    assert study["packets"][0]["end_before"] == "2020-01-06", "the original is untouched"
    assert check_study({**study, "packets": [{"id": "p", "start": "2019-10-07", "end_before": "2019-10-10"}]}, 2)["packets"][0][
        "end_before"] == "2019-10-10", "a packet already shorter stays"
    for bad in (0, 3, True, 1.5):
        with pytest.raises(ValueError, match="1 or 2"):
            check_study(study, bad)


def test_check_study_window_is_calendar_days_not_rows_so_a_week_with_no_rows_still_counts(tmp_path):
    """The window ends start + 7 * weeks days whatever rows exist: a holiday week with no rows is not skipped over."""
    from mio_parallel_queue import check_study
    study = {"selected_packet": "p", "packets": [{"id": "p", "start": "2019-12-23", "end_before": "2020-03-30"}]}
    assert check_study(study, 1)["packets"][0]["end_before"] == "2019-12-30"
    assert check_study(study, 2)["packets"][0]["end_before"] == "2020-01-06"


def test_check_bundle_builds_a_short_bundle_beside_the_real_ones_and_the_stage_command_streams_it(study, tmp_path, capsys):  # noqa: F811
    from mio_parallel_queue import check_bundle, stage_bundle
    runtime = _stub_runtime(tmp_path)
    root = tmp_path / "queue"
    root.mkdir()
    run = _fixed_run_doc(tmp_path, "run", study)
    _ready(root, "q1", run, _doc(tmp_path, "init", tmp_path / "i"))
    config = {"queue_root": str(root), "python": sys.executable, "runtime": str(runtime), "quarters": ["q1", "q2"],
              "scratch_root": str(tmp_path / "scratch")}
    digest, path = check_bundle(config, "q1/rules", 1)
    assert path == tmp_path / "scratch" / "bundles" / "check-q1-rules" and len(digest) == 64
    assert any(path.iterdir())
    with pytest.raises(ValueError, match="not in the READY"):
        check_bundle(config, "q1/nothing", 1)
    with pytest.raises(ValueError, match="2023Q4"):
        check_bundle({**config, "quarters": ["2024q1"]}, "2024q1/rules", 1)
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config))
    assert main(["check-bundle", str(config_file), "q1/rules", "--weeks", "1"]) == 0
    assert "check bundle" in capsys.readouterr().out
    mac, wsl = FakeHost("mac", 1), FakeHost("wsl", 0)
    c = _coordinator(tmp_path / "c", [mac, wsl])
    stage_bundle(c, "check:q1/rules")
    assert mac.staged == ["check-q1/rules"], "the Mac host is asked to stage the check bundle by its own name"


def test_a_holdout_quarter_has_no_check_bundle(tmp_path):
    from mio_parallel_queue import check_bundle
    config = {"queue_root": str(tmp_path), "python": sys.executable, "runtime": str(tmp_path), "quarters": ["a", "b"],
              "selection_quarters": ["a"], "holdout_quarters": ["b"], "selection_formulations": ["f"], "selection_picks": 1,
              "selection_formulation_hashes": _pins("f")}
    with pytest.raises(ValueError, match="sealed holdout"):
        check_bundle(config, "b/rules", 1)

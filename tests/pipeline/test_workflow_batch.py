"""workflow-batch: one subprocess per lane, own work_dir, resume, retry, guards."""

import json
import os
import sys
import textwrap
import time

import pytest

from dskit.pipeline.workflow_batch import BatchError, ProcessTree, WorkflowBatch, main

WORKER = textwrap.dedent('''
    import json, os, subprocess, sys, time
    argv = sys.argv[1:]
    manifest = json.load(open(argv[0]))
    steps = list(manifest["steps"])
    args_file = argv[argv.index("--args") + 1]
    lane = argv[argv.index("--only") + 1].lstrip("@")
    work = json.load(open(args_file))["work_dir"]
    mode = json.loads(os.environ.get("FAKE_MODES", "{}")).get(lane, "ok")
    os.makedirs(work, exist_ok=True)
    marker = os.path.join(work, "attempts")
    n = int(open(marker).read()) + 1 if os.path.exists(marker) else 1
    open(marker, "w").write(str(n))
    open(os.path.join(work, "env_seen"), "w").write(os.environ.get("FAKE_PASS", ""))
    if mode in ("orphan", "hang"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        open(os.path.join(work, "grandchild"), "w").write(str(child.pid))
    if mode == "hang":
        time.sleep(60)
    if mode == "big":
        blob = bytearray(120 * 1024 * 1024)
        time.sleep(30)
    done = {s: {"exit_code": 0} for s in steps}
    code = 0
    if mode == "partial":
        done = {steps[0]: {"exit_code": 0}}
    elif mode in ("bad", "halt", "refuse") or (mode == "flaky" and n == 1):
        code = {"halt": 3, "refuse": 5}.get(mode, 1)
        done = {s: {"exit_code": code} for s in steps}
    json.dump({"steps": {"%s@%s" % (k, lane): v for k, v in done.items()}},
              open(os.path.join(work, "workflow.json"), "w"))
    sys.exit(code)
''')


@pytest.fixture
def setup(tmp_path, monkeypatch):
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER)
    manifest = tmp_path / "manifest.json"

    def write_manifest(steps=("s1", "s2"), args=None):
        doc = {
            "args": args or {"tickers": ["A", "B", "C"], "work_dir": "unused"},
            "lanes": {"key": "tickers"}, "steps": {s: {} for s in steps},
        }
        manifest.write_text(json.dumps(doc))

    write_manifest()
    root = tmp_path / "root"

    def make(**over):
        spec = {
            "manifest": str(manifest), "root": str(root),
            "parallel": 2, "poll_seconds": 0.02,
            "argv_prefix": [sys.executable, str(worker)],
        }
        spec.update(over)
        return WorkflowBatch(spec)

    make.write_manifest = write_manifest
    return make, root, tmp_path, monkeypatch


def _batch(root):
    return json.loads((root / "batch.json").read_text())


def _lane(root, name):
    return next(x for x in _batch(root)["lanes"] if x["lane"] == name)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return "Z" not in open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False


def _gone(pid, wait=3.0):
    end = time.time() + wait
    while time.time() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


def test_runs_every_lane_in_its_own_work_dir_and_passes_env(setup):
    make, root, _, monkeypatch = setup
    monkeypatch.setenv("FAKE_PASS", "yes")
    assert make().run() == 0
    for lane in "ABC":
        entry = _lane(root, lane)
        assert entry["status"] == "ok" and entry["exit_code"] == 0
        assert entry["work_dir"] == str(root / lane)
        assert (root / lane / "env_seen").read_text() == "yes"


def test_failure_continues_and_halting_step_follows_manifest_order(setup):
    make, root, _, monkeypatch = setup
    make.write_manifest(steps=("zeta", "alpha"))
    monkeypatch.setenv("FAKE_MODES", json.dumps({"B": "bad"}))
    assert make().run() == 1
    assert _lane(root, "A")["status"] == "ok" and _lane(root, "C")["status"] == "ok"
    bad = _lane(root, "B")
    assert bad["status"] == "failed" and bad["exit_code"] == 1
    assert bad["halting_step"] == "zeta@B"


def test_retry_recovers_a_flaky_lane_but_never_retries_exit_3_or_5(setup):
    make, root, _, monkeypatch = setup
    monkeypatch.setenv("FAKE_MODES", json.dumps({"A": "flaky", "B": "halt", "C": "refuse"}))
    assert make(retry=2).run() == 1
    assert _lane(root, "A")["attempts"] == 2 and _lane(root, "A")["status"] == "ok"
    assert _lane(root, "B")["attempts"] == 1 and _lane(root, "C")["attempts"] == 1


def test_exit_zero_with_partial_ledger_is_not_ok(setup):
    make, root, _, monkeypatch = setup
    monkeypatch.setenv("FAKE_MODES", json.dumps({"A": "partial"}))
    assert make().run() == 1
    entry = _lane(root, "A")
    assert entry["status"] == "failed" and entry["halting_step"] == "s2@A"


def test_rerun_relaunches_every_lane_so_the_runner_decides(setup):
    make, root, _, _ = setup
    assert make().run() == 0
    assert make().run() == 0
    assert (root / "A" / "attempts").read_text() == "2"
    assert _lane(root, "A")["status"] == "ok" and _lane(root, "A")["attempts"] == 1


def test_only_lanes_and_lanes_per_batch_keep_earlier_records(setup):
    make, root, _, monkeypatch = setup
    assert make(only_lanes=["C"]).run() == 0
    statuses = {x["lane"]: x["status"] for x in _batch(root)["lanes"]}
    assert statuses == {"A": "pending", "B": "pending", "C": "ok"}
    assert make(lanes_per_batch=1).run() == 0
    statuses = {x["lane"]: x["status"] for x in _batch(root)["lanes"]}
    assert statuses == {"A": "ok", "B": "pending", "C": "ok"}


def test_unknown_only_lane_is_refused(setup):
    make, _, _, _ = setup
    with pytest.raises(BatchError, match="Z"):
        make(only_lanes=["Z"])


def test_args_overlay_is_deep_merged_and_keyed_args_pass_whole(setup):
    make, root, tmp_path, _ = setup
    keyed = {"tickers": ["A", "B"], "price": {"path": {"A": "a.parquet", "B": "b.parquet"}},
             "work_dir": "x"}
    make.write_manifest(args=keyed)
    overlay = tmp_path / "over.json"
    overlay.write_text(json.dumps({"tickers": ["A"], "price": {"fmt": "pq"}}))
    batch = make(args=str(overlay))
    assert [x.name for x in batch.lanes] == ["A"]
    argv = batch._argv(batch.lanes[0])
    assert argv[-2:] == ["--only", "@A"]
    merged = json.loads((root / "lane_args" / "A.json").read_text())
    assert merged["price"] == {"fmt": "pq"} and merged["work_dir"] == str(root / "A")
    batch = make()
    batch._argv(batch.lanes[1])
    full = json.loads((root / "lane_args" / "B.json").read_text())
    assert set(full) == {"work_dir"}


@pytest.mark.parametrize("pattern, text", [
    ("{root}/fixed", "lane"),
    ("{root}/{lane}/{other}", "unknown field"),
    ("{root}/{lane", "work_dir_pattern"),
    ("{root}/{lane!r}", "conversions"),
])
def test_bad_work_dir_patterns_refused(setup, pattern, text):
    make, _, _, _ = setup
    with pytest.raises(BatchError, match=text):
        make(work_dir_pattern=pattern)


@pytest.mark.parametrize("tickers", [
    ["A", "../B"], ["A", "a"], ["A", "B/C"], ["A", "B@C"], ["A", ".."],
])
def test_unsafe_or_colliding_lane_names_refused(setup, tickers):
    make, _, _, _ = setup
    make.write_manifest(args={"tickers": tickers, "work_dir": "x"})
    with pytest.raises(BatchError):
        make()


def test_duplicate_work_dirs_refused(setup):
    make, _, _, _ = setup
    with pytest.raises(BatchError, match="share work dir"):
        make(work_dir_pattern="{root}/{lane}/..")


@pytest.mark.parametrize("bad", [
    {"parallel": "2"}, {"parallel": True}, {"parallel": 0}, {"retry": -1},
    {"retry": 1.5}, {"lanes_per_batch": "1"}, {"only_lanes": "A"}, {"root": 5},
    {"mem_limit_mb": float("nan")}, {"poll_seconds": 0}, {"bogus": 1},
])
def test_params_are_type_checked(setup, bad):
    make, _, _, _ = setup
    with pytest.raises(BatchError):
        make(**bad)


def test_non_object_work_dir_arg_parent_refused(setup):
    make, _, tmp_path, _ = setup
    overlay = tmp_path / "o.json"
    overlay.write_text(json.dumps({"a": 5}))
    batch = make(args=str(overlay), work_dir_arg="a.b")
    with pytest.raises(BatchError, match="not an object"):
        batch._lane_args(batch.lanes[0])


def test_memory_guard_kills_a_lane_over_the_limit(setup):
    make, root, _, monkeypatch = setup
    if not ProcessTree().available():
        pytest.skip("needs /proc")
    monkeypatch.setenv("FAKE_MODES", json.dumps({"A": "big"}))
    assert make(mem_limit_mb=60, retry=2).run() == 1
    entry = _lane(root, "A")
    assert entry["status"] == "killed" and entry["attempts"] == 1
    assert entry["peak_rss_mb"] > 60 and entry["halting_step"] == "s1@A"
    assert _lane(root, "B")["status"] == "ok"


def test_grandchild_is_killed_when_the_lane_ends_normally(setup):
    make, root, _, monkeypatch = setup
    monkeypatch.setenv("FAKE_MODES", json.dumps({"A": "orphan"}))
    assert make().run() == 0
    assert _gone(int((root / "A" / "grandchild").read_text()))


def test_interrupt_kills_running_lanes_and_their_grandchildren(setup, monkeypatch):
    make, root, _, mp = setup
    mp.setenv("FAKE_MODES", json.dumps({"A": "hang", "B": "hang"}))
    batch = make()
    calls = {"n": 0}
    real = time.sleep

    def sleeper(seconds):
        calls["n"] += 1
        if calls["n"] >= 40 and all((root / x / "grandchild").exists() for x in "AB"):
            raise KeyboardInterrupt
        real(seconds)

    monkeypatch.setattr("dskit.pipeline.workflow_batch.time.sleep", sleeper)
    with pytest.raises(KeyboardInterrupt):
        batch.run()
    for lane in "AB":
        assert _gone(int((root / lane / "grandchild").read_text()))
        assert _lane(root, lane)["status"] == "failed"
    assert all(x.process is None or x.process.poll() is not None for x in batch.lanes)


def test_spawn_failure_closes_the_log_and_marks_the_lane_failed(setup):
    make, root, _, _ = setup
    assert make(argv_prefix=["/nonexistent/interpreter"]).run() == 1
    assert _lane(root, "A")["status"] == "failed"


def test_process_tree_measures_many_roots_in_one_scan(setup):
    if not ProcessTree().available():
        pytest.skip("needs /proc")
    sizes = ProcessTree().measure([os.getpid(), 999999999])
    assert sizes[os.getpid()] > 0 and sizes[999999999] == 0


def test_cli_main(setup):
    make, root, tmp_path, _ = setup
    code = main([
        str(tmp_path / "manifest.json"), "--root", str(root), "--parallel", "1",
        "--argv-prefix", sys.executable, str(tmp_path / "worker.py"),
        "--only-lanes", "A",
    ])
    assert code == 0 and _lane(root, "A")["status"] == "ok"
    assert main([str(tmp_path / "manifest.json"), "--root", str(root), "--parallel", "0"]) == 1


def test_sigterm_to_the_batch_kills_every_lane_group_and_marks_them_failed(setup):
    import signal
    import subprocess

    _, root, tmp_path, _ = setup
    cmd = [sys.executable, "-c", "import sys; from dskit.pipeline.workflow_batch import main; sys.exit(main())",
           str(tmp_path / "manifest.json"), "--root", str(root), "--parallel", "2",
           "--poll-seconds", "0.02", "--argv-prefix", sys.executable, str(tmp_path / "worker.py")]
    env = dict(os.environ, FAKE_MODES=json.dumps({"A": "hang", "B": "hang"}))
    batch = subprocess.Popen(cmd, env=env)
    end = time.time() + 15
    while time.time() < end and not all((root / x / "grandchild").exists() for x in "AB"):
        time.sleep(0.05)
    pids = [int((root / x / "grandchild").read_text()) for x in "AB"]
    batch.send_signal(signal.SIGTERM)
    batch.wait(timeout=15)
    assert batch.returncode != 0
    assert all(_gone(p) for p in pids)
    assert {_lane(root, x)["status"] for x in "AB"} == {"failed"}


def test_non_string_lane_values_are_refused_first(setup):
    make, _, _, _ = setup
    make.write_manifest(args={"tickers": [1, "B"], "work_dir": "x"})
    with pytest.raises(BatchError, match="text"):
        make()


@pytest.mark.parametrize("name", ["batch.json", "logs", "lane_args"])
def test_lane_names_may_not_shadow_the_batch_entries(setup, name):
    make, _, _, _ = setup
    make.write_manifest(args={"tickers": ["A", name], "work_dir": "x"})
    with pytest.raises(BatchError, match="batch's own"):
        make()

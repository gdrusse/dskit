"""The runbook's commands are real: every source it registers and every pull it runs exists, and its snippets run.

A runbook line nobody executes rots. These tests read ``docs/plans/2026-10-06-wsl-data-pull-runbook.md`` and check, offline,
that each ``register-source`` names a config and a connector that agree, that each ``pull`` names a source, the stream its
suite gates and that suite, and that the inline python snippets (the censuses, the gap count, the B6 size count) run over the
synthetic stores. The B6 sizing block is pasted into a bash with a bare HOME, as written, and must run to completion and be
repeatable; the section 1 gate and the B4 ``publish`` helper run under a stubbed ``python``. The network (``curl``) and
``tmux`` are the parts a test cannot run.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

from dskit.onboarding import check_config, load_suite, resolve_connector
from synthetic import shipped
from test_hourly_pipeline import CUTOFF, DOC as HOURLY_DOC, World, runbook_root_command
from test_restwindow_sources import START, FakeCoinbase, acquire

CHILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIGS = os.path.join(CHILD_ROOT, "configs")
RUNBOOK = os.path.join(CHILD_ROOT, "docs", "plans", "2026-10-06-wsl-data-pull-runbook.md")
TEXT = open(RUNBOOK, encoding="utf-8").read()
JOINED = re.sub(r"\\\n\s*", " ", TEXT)  # a shell line continued with a backslash is one command

LOOP = re.compile(r"for s in ([^;]+?); do\s+python -m dskit\.onboarding register-source \$s .*?--connector\s+(\S+)\s+"
                  r"--config\s+@configs/source-\$s\.json")
SINGLE = re.compile(r'register-source\s+([a-z][\w-]*)\s+--root\s+"\$OB"\s+--catalog-source\s+\1\s+--connector\s+(\S+)\s+'
                    r'--config\s+@configs/source-\1\.json')
PULL = re.compile(r"^pull[ \t]+(\S+)[ \t]+(\S+)[ \t]+(suite-\S+\.json)(?:[ \t]+(\w+))?", re.M)


def registrations():
    """``{source: connector reference}`` for every ``register-source`` the runbook gives."""
    found = {}
    for names, connector in LOOP.findall(JOINED):
        for name in names.split():
            found[name] = connector
    for name, connector in SINGLE.findall(JOINED):
        found[name] = connector
    return found


def test_every_shipped_source_is_registered_by_the_runbook_with_a_connector_that_accepts_its_config(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRADING_ROOT", CHILD_ROOT)
    registered = registrations()
    shipped_sources = {os.path.basename(p)[len("source-"):-len(".json")] for p in glob.glob(os.path.join(CONFIGS, "source-*.json"))}
    assert shipped_sources - {"sample"} == set(registered), "a source config the runbook never registers, or the reverse"
    for name, ref in registered.items():
        config = shipped(f"source-{name}.json")
        check_config(resolve_connector(ref)(), config)


def test_every_pull_names_the_stream_its_suite_gates_and_a_stream_the_source_declares(monkeypatch):
    monkeypatch.setenv("CRYPTO_TRADING_ROOT", CHILD_ROOT)
    registered, pulls = registrations(), PULL.findall(TEXT)
    assert len(pulls) >= 19, "the nineteen pulls of sections 3, 4, 7 and 8"
    for source, stream, suite, mode in pulls:
        assert source in registered, f"pull of an unregistered source {source}"
        targets = {rule.target for rule in load_suite(os.path.join(CONFIGS, suite)).rules}
        assert targets == {stream}, f"pull {source} {stream}: {suite} gates {targets}"
        config = {k: v for k, v in shipped(f"source-{source}.json").items() if k != "storage"}
        connector = resolve_connector(registered[source])()
        if registered[source] != "httpblobs":
            assert stream in {d["stream"] for d in connector.discover(config)}, f"{source} has no stream {stream}"
        else:
            assert config["stream"] == stream
        assert mode in ("", "live"), "the helper's optional fourth word is the acquire mode"


def test_the_runbook_pulls_every_new_source_in_the_order_it_says():
    pulled = [source for source, *_ in PULL.findall(TEXT)]
    for name in ("kalshi-history-crypto", "kalshi-history-trades-15m", "kalshi-history-candles-hourly-btc",
                 "kalshi-history-candles-hourly-eth", "kalshi-history-trades-hourly", "coinbase-btcusd-1m",
                 "coinbase-ethusd-1m", "deribit-btc-dvol", "deribit-eth-dvol"):
        assert pulled.count(name) == 1, name
    assert pulled.index("kalshi-history-crypto") < pulled.index("kalshi-history-candles-hourly-btc"), (
        "the markets pull is the measurement the costly candle pull waits on")
    assert pulled.index("kalshi-history-trades-hourly") < pulled.index("kalshi-history-trades-15m"), (
        "measured: the hourly trades cost about 0.9 s a market and the 15-minute ones about 10 s, so the 15-minute pull is last")
    assert "COUNT" in TEXT and "7c" in TEXT


def section(start, stop):
    """The runbook text from the heading line starting ``start`` up to the heading line starting ``stop``."""
    begin = TEXT.index("\n" + start) + 1
    return TEXT[begin:TEXT.index("\n" + stop, begin)]


def test_the_15_minute_trades_pull_is_gated_on_its_measured_cost_not_called_cheap():
    """B-1: a 15-minute market's trade chain is about 15 requests, so the pull is days: behind the count gate, in its own step."""
    gate = section("**7e.", "## 8.")
    assert "kalshi-history-trades-15m" in gate and len(PULL.findall(gate)) == 1, "its own step, the only pull in it"
    for needed in ("days", "all or nothing", "52 GB", "14,955", "9.5 to 12.3 s", "28,037", "7c"):
        assert needed in gate, f"7e must say {needed!r}"
    counted = section("**7c.", "**7d.")
    assert "pages" in counted and "0.9 s" in counted, "7c prices a chain in pages, with the measured hourly figure beside the 15-minute one"
    assert "kalshi-history-trades-15m" not in section("**7d.", "**7e."), "7d holds the pulls that fit the count gate"
    assert "cheaper family" not in TEXT and "cheaper family" not in shipped("source-kalshi-history-trades-15m.json")["notes"]
    assert "days" in shipped("source-kalshi-history-trades-15m.json")["notes"] and "runbook section 7" in shipped(
        "source-kalshi-history-trades-15m.json")["notes"]


def test_the_free_space_line_names_the_15_minute_trades_pull_apart_from_the_rest():
    head = TEXT[:TEXT.index("## 0.")]
    assert re.search(r"about 10 GB free[^.]*except[^.]*trades-15m", head), "the 10 GB figure excludes trades-15m"
    assert re.search(r"trades-15m[^.]*about 50 GB", head)
    assert "trades-15m, then" not in head, "the order line no longer starts the heavy pull first"


def snippet(marker):
    """The python source of the runbook's ``<<'PY'`` block that follows ``marker``."""
    start = TEXT.index(marker)
    body = TEXT[TEXT.index("<<'PY'", start):]
    return body[body.index("\n") + 1:body.index("\nPY\n")]


def run_snippet(code, *argv):
    done = subprocess.run([sys.executable, "-c", code, *argv], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout


def test_the_archive_census_snippet_counts_archived_and_live_markets_per_series(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch)
    out = run_snippet(snippet("python - \"$OB\" \"$CUT\""), world.store.path, CUTOFF)
    counts = {tuple(line.split()[:2]): int(line.split()[2]) for line in out.splitlines()}
    # per asset and event: 2 KXxxxD + 2 KXxxx hourly markets, and six 15-minute markets; event one is archived
    assert counts[("KXBTCD", "archived")] == counts[("KXBTCD", "live")] == 2
    assert counts[("KXBTC", "archived")] == counts[("KXETH", "live")] == 2
    assert counts[("KXBTC15M", "archived")] == counts[("KXETH15M", "live")] == 6
    assert sum(counts.values()) == 40, "every market of the six series is counted once"


def test_the_open_length_census_separates_hourly_events_from_daily_and_weekly_ones(tmp_path, monkeypatch):
    """A1-01: the four 'hourly' series list 25 h and weekly ladders too; the census shows the mixture before a pull is paid for."""
    world = World(tmp_path, monkeypatch)
    out = run_snippet(snippet("python - \"$OB\" <<'PY'    # census: how long"), world.store.path)
    counts = {tuple(line.split()[:2]): int(line.split()[2]) for line in out.splitlines()}
    assert counts[("KXBTCD", "<=70min")] == counts[("KXETHD", "<=70min")] == 4, "every synthetic hourly market was open 60 minutes"
    assert counts[("KXBTC15M", "<=20min")] == counts[("KXETH15M", "<=20min")] == 12
    assert sum(counts.values()) == 40 and {key[1] for key in counts} == {"<=20min", "<=70min"}


def test_the_open_length_classes_split_daily_and_weekly_ladders_and_name_a_missing_open():
    code = snippet("python - \"$OB\" <<'PY'    # census: how long")
    classes = {}
    exec(code.split("rows = ")[0].replace("from dskit.onboarding import scan_stream", ""), classes)  # the two helper functions only
    row = lambda a, b: {"open_time": a, "close_time": b}  # noqa: E731
    got = {name: classes["label"](classes["minutes"](r)) for name, r in {
        "hour": row("2026-10-07T20:00:00Z", "2026-10-07T21:00:00Z"),
        "day": row("2026-10-06T20:00:00Z", "2026-10-07T21:00:00Z"),
        "week": row("2026-10-02T20:00:00Z", "2026-10-09T21:00:00Z"),
        "quarter": row("2026-10-07T20:45:00Z", "2026-10-07T21:00:00Z"),
        "unopened": row("", "2026-10-07T21:00:00Z")}.items()}
    assert got == {"hour": "<=70min", "day": "<=2days", "week": ">2days", "quarter": "<=20min", "unopened": "no_open_time"}


def test_the_gap_count_snippet_reports_the_days_that_are_not_complete(tmp_path, monkeypatch):
    root, _, _ = acquire(tmp_path, monkeypatch, "source-coinbase-btcusd-1m.json", "candles", FakeCoinbase())
    out = run_snippet(snippet("python - \"$OB\" coinbase-btcusd-1m"), root.root, "src", "candles", "time_iso")
    assert out.startswith("2 days;") and "2 not 1440 rows" in out, "the fixture spans the end of one day and the start of the next"
    assert START[:10] in out


def test_the_hourly_size_snippet_counts_settled_hourly_markets_times_leads_and_agrees_with_the_document(tmp_path, monkeypatch):
    """LB2-01: B6 holds every row in memory, so the runbook gives the count to take first; its two constants restate the document's."""
    world = World(tmp_path, monkeypatch)
    code = snippet("python - \"$OB\" <<'PY'    # B6 size")
    out = run_snippet(code, world.store.path)
    assert out.startswith("16 settled hourly markets x 3 leads = 48 rows: about 0.0 GB peak"), out
    doc = json.load(open(HOURLY_DOC, encoding="utf-8"))["pipeline"]
    leads = int(re.search(r"LEADS, RUN_KB, ACQUIRE_KB = .*?, (\d+), ", code).group(1))
    assert leads == len(doc["decisions"]["params"]["leads_minutes"]), "the snippet's lead count is the document's"
    hourly = set(re.findall(r'"(KX[A-Z]+)"', code.split("HOURLY")[1].split("}")[0]))
    assert hourly == set(doc["markets"]["params"]["series"]), "the snippet's series are the document's"
    assert "9.7 KB a row" in TEXT and "9.7" in code, "the figure the prose gives is the one the snippet multiplies by"


def pasted(marker):
    """The runbook's fenced ``bash`` block that holds ``marker``, exactly as the owner would paste it."""
    start = TEXT.rindex("```bash\n", 0, TEXT.index(marker)) + len("```bash\n")
    return TEXT[start:TEXT.index("```", start)]


class Owner:
    """The owner's shell: a HOME and a store of its own, ``python`` is this interpreter, the child is importable."""

    def __init__(self, tmp_path, world):
        self.home, self.work, self.store = tmp_path / "home", tmp_path / "work", world.store.path
        (self.work / "configs").mkdir(parents=True)
        self.home.mkdir()
        (tmp_path / "bin").mkdir()
        (tmp_path / "bin" / "python").symlink_to(sys.executable)
        shutil.copy(HOURLY_DOC, self.work / "configs" / "run-features-hourly.json")
        self.env = {**os.environ, "HOME": str(self.home), "OB": self.store, "PYTHONPATH": CHILD_ROOT,
                    "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}"}

    def paste(self, text):
        return subprocess.run(["bash", "-c", text], cwd=self.work, env=self.env, capture_output=True, text=True, timeout=300)

    def sizing_dir(self):
        return self.home / "data" / "crypto_trading" / "features-hourly-sizing"


def test_the_sizing_copy_runs_as_pasted_and_is_repeatable_after_a_failed_attempt(tmp_path, monkeypatch):
    """B3-01: the bounded path the runbook gives runs from a bare HOME with no mkdir, and a failed try leaves nothing behind."""
    world = World(tmp_path, monkeypatch)
    owner = Owner(tmp_path, world)
    block = pasted("# B6 sizing copy")
    assert owner.paste(runbook_root_command()).returncode == 0
    assert not (owner.home / "data").exists(), "a bare HOME: the block must make every directory it writes into"
    done = owner.paste(block)
    assert done.returncode == 0, done.stderr[-1500:]
    stale = owner.sizing_dir() / "runs" / "an-earlier-failed-run"  # what a run that died on its last node leaves
    stale.mkdir(parents=True)
    (stale / "result.json").write_text("{}")
    (owner.sizing_dir() / "decision_features-an-earlier-failed-run.jsonl").write_text("{}\n")
    for attempt in ("after a failed attempt", "again after a success (the same run name and hash)"):
        done = owner.paste(block)
        assert done.returncode == 0, (attempt, done.stderr[-1500:])
        assert not stale.exists() and not list(owner.sizing_dir().glob("decision_features-an-earlier*")), "rebuilt from scratch"
    rows = [json.loads(line) for path in owner.sizing_dir().glob("decision_features-*.jsonl") for line in path.read_text().splitlines()]
    assert {r["series"] for r in rows} == {"KXBTCD"} and {r["lead_minutes"] for r in rows} == {15}
    assert len(rows) == 4, "two BTC strikes of each of the two events, one lead"
    doc = json.loads((owner.sizing_dir() / "run.json").read_text(encoding="utf-8"))
    shipped_doc = json.load(open(HOURLY_DOC, encoding="utf-8"))
    assert doc["name"] == shipped_doc["name"] + "-sizing"
    assert not (owner.home / "data" / "crypto_trading" / "features-hourly").exists(), "never the directory the acquire loop reads"
    assert not (owner.work / "pipeline_runs").exists(), "the run directory is the copy's own"


def test_the_setup_gate_fails_when_pytest_cannot_run_or_a_test_was_skipped():
    """B3-03: a gate that passes when pytest is missing (its error text holds no 'skip') proves nothing."""
    (line,) = re.findall(r"^(out=\$\(python -m pytest tests/test_binance_vision\.py[^\n]*)$", TEXT, re.M)
    def gate(output, status):
        script = f"python() {{ echo '{output}'; return {status}; }}\n{line}"
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60).returncode
    assert gate("12 passed in 1s", 0) == 0
    assert gate("11 passed, 1 skipped in 1s", 0) != 0, "a skipped transform test"
    assert gate("No module named pytest", 1) != 0, "pytest itself could not run"
    assert gate("1 failed, 11 passed", 1) != 0


def test_the_publish_helper_acquires_every_run_table_then_verifies(tmp_path):
    """B4 and B6 share one loop: each ``decision_features-<run>.jsonl`` is its own stream, and a verify follows."""
    start = TEXT.index("publish() {")
    helper = TEXT[start:TEXT.index("\n}\n", start) + 3]
    table = tmp_path / "data" / "crypto_trading" / "features-hourly"
    table.mkdir(parents=True)
    for run in ("a", "b"):
        (table / f"decision_features-{run}.jsonl").write_text("{}\n")
    calls = tmp_path / "calls.log"
    script = f"python() {{ echo \"$*\" >> {calls}; }}\nOB=/ob\n{helper}\npublish features-hourly"
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env={**os.environ, "HOME": str(tmp_path)})
    assert done.returncode == 0, done.stderr
    assert calls.read_text().splitlines() == [
        "-m dskit.onboarding acquire --root /ob --source features-hourly --stream decision_features-a --mode backfill",
        "-m dskit.onboarding acquire --root /ob --source features-hourly --stream decision_features-b --mode backfill",
        "-m dskit.onboarding verify --root /ob"]

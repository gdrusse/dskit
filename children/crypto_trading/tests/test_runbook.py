"""The runbook's commands are real: every source it registers and every pull it runs exists, and its snippets run.

A runbook line nobody executes rots. These tests read ``docs/plans/2026-10-06-wsl-data-pull-runbook.md`` and check, offline,
that each ``register-source`` names a config and a connector that agree, that each ``pull`` names a source, the stream its
suite gates and that suite, and that the two inline python snippets (the archive census, the gap count) run over the
synthetic stores. The shell around them (``curl``, paths, ``tmux``) is the one part a test cannot run.
"""

import glob
import os
import re
import subprocess
import sys

from dskit.onboarding import check_config, load_suite, resolve_connector
from synthetic import shipped
from test_hourly_pipeline import CUTOFF, World
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
    assert pulled.index("kalshi-history-trades-15m") < pulled.index("kalshi-history-trades-hourly"), "the cheaper family first"
    assert "COUNT" in TEXT and "7c" in TEXT


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


def test_the_gap_count_snippet_reports_the_days_that_are_not_complete(tmp_path, monkeypatch):
    root, _, _ = acquire(tmp_path, monkeypatch, "source-coinbase-btcusd-1m.json", "candles", FakeCoinbase())
    out = run_snippet(snippet("python - \"$OB\" coinbase-btcusd-1m"), root.root, "src", "candles", "time_iso")
    assert out.startswith("2 days;") and "2 not 1440 rows" in out, "the fixture spans the end of one day and the start of the next"
    assert START[:10] in out

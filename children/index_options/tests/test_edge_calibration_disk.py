"""ADR-0311 child fix round: the REAL readers against a synthetic on-disk layout mirroring the owner's.

No seam is overridden: ``IndexOptionsEdgeWalk`` reads one onboarding root (a forecast pin with ``<q>/forecasts.parquet``
and ``<q>/contexts.parquet`` per quarter, a market pin with chain, bars and sessions) through
``MIOPacketInputs.read_bounded``, one quarter per pass. Three quarters, decisions on both sides of each boundary.
"""

import copy
import json
from pathlib import Path

import pytest

from dskit.pipeline.node import NodeContext
from edge_calibration_world import DiskWorld
from index_options.edge_calibration import IndexOptionsEdgeWalk
from test_edge_calibration import ADJUST, DIRECTION, EDGES, POOL, REGIME

DAYS = ["2019-10-07", "2019-10-14", "2019-10-21", "2020-01-06", "2020-01-13", "2020-03-23", "2020-03-30",
        "2020-04-06", "2020-04-13", "2020-05-04"]
WINDOW = {"start": "2019-10-01", "end": "2020-06-14"}


def world(**kw):
    return DiskWorld(decision_days=DAYS, last="2020-07-03", bars_from="2019-06-03", window=WINDOW, **kw)


def params(root, files, out="out", **over):
    p = {**copy.deepcopy(files), "window": dict(WINDOW), "report_from": "2019-10-14", "sealed_from": "2021-07-01",
         "out_dir": str(Path(root) / out), "buckets": dict(EDGES), "regime": dict(REGIME), "pooling": dict(POOL),
         "name_adjust": dict(ADJUST), "checkpoint_every": 1, "ram_gb_limit": 64, "side_rule": "best",
         "study_scope": "window", "history_start": "2019-07-01", "protected_end_before": "2024-01-01",
         "direction_regressor": True, "direction": dict(DIRECTION)}
    p.update(over)
    return p


def run(p, root):
    node = IndexOptionsEdgeWalk("edge", p)
    return node, node.run(NodeContext(name="edge", asof="2020-06-01", run_dir=str(root)), {})["summary"]


def refused(p, root, code):
    with pytest.raises(ValueError) as err:
        run(p, root)
    assert getattr(err.value, "code", None) == code, err.value


def tree(root):
    root = Path(root)
    return {x.relative_to(root).as_posix(): x.read_bytes() for x in sorted(root.rglob("*"))
            if x.is_file() and "inputs" not in x.relative_to(root).parts}


@pytest.fixture(scope="module")
def disk(tmp_path_factory):
    root = tmp_path_factory.mktemp("disk")
    w = world()
    return root, w, w.write(root)


def test_every_quarter_of_the_window_yields_rows_through_the_real_readers(disk, tmp_path):
    root, w, files = disk
    node, summary = run(params(tmp_path, files), tmp_path)
    passes = node._passes()
    assert set(passes) == {"2019Q4", "2020Q1", "2020Q2"}
    for quarter, bounded in passes.items():
        rows = bounded["inputs"]["AAA"]["forecasts"]
        assert rows and {r["quote_date"][:7] for r in rows} <= {"2019-10", "2019-11", "2019-12", "2020-01", "2020-02",
                                                                "2020-03", "2020-04", "2020-05", "2020-06"}
        assert len({r["fold"] for r in rows}) == 1                       # one fold per pass: Quarter.parse(q).fold
    assert {r["fold"] for r in passes["2019Q4"]["inputs"]["AAA"]["forecasts"]} == {29}
    decided = set()
    for f in (Path(node.params["out_dir"]) / "rows").glob("rows-*.jsonl"):
        decided |= {json.loads(x)["t"] for x in f.read_text().splitlines()}
    quarters = {f"{t[:4]}Q{(int(t[5:7]) - 1) // 3 + 1}" for t in decided}
    assert quarters == {"2019Q4", "2020Q1", "2020Q2"}
    assert summary["states"] and not summary["counts"].get("no_eligible_forecast_expiry")
    assert "2020-03-30" in decided and any(t >= "2020-04-06" for t in decided)   # Q1 decisions settle in Q2


def test_a_quarter_without_forecasts_refuses_loudly(tmp_path):
    w = world()
    files = w.write(tmp_path / "data", empty_quarters=("2020Q2",))
    refused(params(tmp_path, files), tmp_path, "forecast_quarter_empty")


def test_swapped_forecast_and_context_pins_are_refused(disk, tmp_path):
    _, _, files = disk
    p = params(tmp_path, files)
    pin = p["forecast_pins"]["2020Q1"]
    pin["forecasts"], pin["contexts"] = pin["contexts"], pin["forecasts"]
    refused(p, tmp_path, "forecast_not_pinned")


def test_a_pin_taken_from_another_quarters_file_is_refused(disk, tmp_path):
    _, _, files = disk
    p = params(tmp_path, files)
    p["forecast_pins"]["2020Q1"] = dict(p["forecast_pins"]["2020Q2"])
    refused(p, tmp_path, "forecast_not_pinned")


def test_a_forecast_file_changed_on_disk_after_pinning_is_refused(tmp_path):
    w = world()
    files = w.write(tmp_path / "data")
    doc = json.loads(Path(files["mio_document"]["path"]).read_text())
    pin = doc["pipeline"]["plan"]["params"]["study"]["domain"]["sources"]["forecasts"]["pin"]
    victim = next(Path(pin["root"]).glob("raw/fc/*/payload/files/2020Q1/forecasts.parquet"))
    victim.write_bytes(victim.read_bytes() + b"x")
    with pytest.raises(ValueError, match="drift"):
        run(params(tmp_path, files), tmp_path)


def test_the_documents_own_clocks_are_not_used(disk, tmp_path):
    """The document says history_start 2020-02-03 (after window.start) and protected_end_before 2020-07-01
    (below sealed_from): read as the study's clocks they refuse; the walk's own clocks run."""
    _, _, files = disk
    doc = json.loads(Path(files["mio_document"]["path"]).read_text())
    domain = doc["pipeline"]["plan"]["params"]["study"]["domain"]
    assert domain["history_start"] > WINDOW["start"] and domain["params"]["bridge"]["protected_end_before"] < "2021-07-01"
    run(params(tmp_path, files), tmp_path)


@pytest.mark.parametrize("over,code", [
    ({"history_start": "2019-10-01"}, "history_start_invalid"),
    ({"protected_end_before": "2021-06-30"}, "protected_end_invalid"),
    ({"protected_end_before": "2024-01-02"}, "protected_end_invalid"),
    ({"history_start": "2019-03-01"}, "history_not_covered"),
])
def test_clock_overrides_are_checked_with_stable_codes(disk, tmp_path, over, code):
    _, _, files = disk
    refused(params(tmp_path, files, **over), tmp_path, code)


def test_a_symbol_missing_a_bar_is_dropped_loudly_and_the_run_goes_on(tmp_path):
    w = DiskWorld(symbols=("AAA", "BBB"), decision_days=DAYS[:4], last="2020-07-03", bars_from="2019-06-03",
                  window=WINDOW, missing_bars={"BBB": "2020-02-12"})
    files = w.write(tmp_path / "data", extra_bar="2020-02-15")                    # AAA also carries a bar on a Saturday
    p = params(tmp_path, files, window={"start": "2019-10-01", "end": "2020-02-29"})
    p["window"]["end"] = "2020-02-29"
    _, summary = run(p, tmp_path)
    assert summary["symbols_dropped"] == {"BBB": 1}
    assert summary["walk_decision_dates"] and summary["states"]


def test_a_resumed_run_reopens_the_existing_quote_indexes(tmp_path):
    w = world()
    files = w.write(tmp_path / "data")
    first, one = run(params(tmp_path, files, out="a"), tmp_path)
    indexes = sorted((Path(first.params["out_dir"]) / "inputs").rglob("*.sqlite"))
    assert indexes
    stamps = {x: (x.stat().st_mtime_ns, x.stat().st_size) for x in indexes}
    before = tree(tmp_path / "a")
    _, two = run(params(tmp_path, files, out="a"), tmp_path)               # same out_dir, same params: a resume
    assert {x: (x.stat().st_mtime_ns, x.stat().st_size) for x in indexes} == stamps    # reopened, not rebuilt
    assert two["output_sha256"] == one["output_sha256"] and tree(tmp_path / "a") == before


def test_study_scope_quarter_equals_window_across_three_quarters(disk, tmp_path):
    _, _, files = disk
    run(params(tmp_path, files, out="w"), tmp_path)
    run(params(tmp_path, files, out="q", study_scope="quarter"), tmp_path)
    a, b = tree(tmp_path / "w"), tree(tmp_path / "q")
    keep = lambda t: {k: v for k, v in t.items() if k.startswith(("map-", "rows/")) or k.endswith(".csv")}
    assert keep(a) and keep(a) == keep(b)


def test_the_output_directory_inside_the_onboarding_root_is_refused(disk, tmp_path):
    root, _, files = disk
    refused(params(tmp_path, files, out="x") | {"out_dir": str(root / "ob" / "results")}, tmp_path, "out_dir_inside_inputs")

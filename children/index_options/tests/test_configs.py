"""Shipped declarations resolve through the public framework interfaces."""

import json
import re

import pytest

from dskit.onboarding import load_suite, run_suite
from dskit.onboarding.connector import check_config
from dskit.onboarding.libs.localfiles import LocalFilesConnector
from dskit.pipeline.document import load_document
from dskit.pipeline.planner import plan


def test_configs_resolve_without_child_registration(child_root, monkeypatch):
    monkeypatch.chdir(child_root)
    check_config(LocalFilesConnector(), json.loads(
        (child_root / "configs/source-fixture.json").read_text()
    ))
    document = load_document(str(child_root / "configs/run-fixture.json"))
    planned = plan(document)
    assert set(planned.order) == {"contracts", "quotes", "settlements", "diagnostic"}
    assert all(item.cls.__module__.startswith("index_options.")
               for item in planned.resolved.values())


@pytest.mark.parametrize("stream, count", [("contracts", 4), ("quotes", 4), ("settlements", 3)])
def test_shipped_suite_passes_each_nonempty_snapshot(child_root, rows, store_factory, stream, count):
    store = store_factory(rows)
    acquired = store.acquire(stream)
    assert acquired["records"] == count  # independently prove coverage, not a vacuous pass
    result = run_suite(store.root, store.registry, load_suite(
        str(child_root / "configs/suite-fixture.json")
    ), acquired["snapshot"])
    assert result["statistics"]["rows"][stream] == count
    assert result["gating"] == "pass"

def test_exact_manifest_and_agent_parity(child_root):
    expected = {
        "pyproject.toml", "AGENTS.md", "CLAUDE.md", "README.md", ".gitignore", "journal.json",
        "index_options/__init__.py", "index_options/contracts.py",
        "index_options/observations.py", "index_options/nodes.py",
        "index_options/distribution.py",
        # ADR-0196: the read-only studies over walk ledgers, and their tests
        "index_options/ledger_studies.py", "tests/test_ledger_studies.py",
        "configs/source-fixture.json", "configs/suite-fixture.json", "configs/run-fixture.json",
        "configs/run-synthetic-distribution.json", "configs/run-synthetic-har.json",
        "configs/run-synthetic-lightgbm.json", "configs/run-distribution-zoo.json",
        "configs/source-cboe-index.json", "configs/source-cboe-chain.json",
        "configs/run-real-distribution.json", "configs/run-real-har.json",
        "configs/run-real-lightgbm.json", "configs/run-real-zoo.json",
        "configs/run-real-vix.json", "configs/run-real-har-vix.json",
        "configs/run-real-lightgbm-vix.json",
        "configs/source-cboe-chain-wide.json", "configs/source-cboe-index-wide.json",
        "configs/source-optionshist-chain.json",
        "fixtures/contracts.jsonl", "fixtures/quotes.jsonl", "fixtures/settlements.jsonl",
        "docs/decisioning/actions.csv", "docs/decisioning/path.csv", "docs/decisioning/README.md",
        "docs/explanations/README.md", "docs/plans/README.md", "docs/memos/README.md",
        "docs/research/README.md", "docs/research/.gitkeep",
        "docs/research/distribution-modeling/2026-09-23-approach.md",
        "docs/research/distribution-modeling/2026-09-23-evaluation.md",
        "docs/research/distribution-modeling/2026-09-23-model-ladder.md",
        "docs/research/distribution-modeling/2026-09-23-ml-dl-transformers.md",
        "docs/research/real-data-backtest/2026-09-24-zoo-vs-vix.md",
        "docs/memos/2026-09-24-real-data-backtest-and-recorder.md",
        "tests/conftest.py", "tests/test_contracts.py", "tests/test_observations.py",
        "tests/test_nodes.py", "tests/test_configs.py", "tests/test_integration.py",
        "tests/test_distribution.py", "tests/test_synthetic_distribution_run.py",
        "tests/test_distribution_zoo.py", "tests/test_real_data.py",
        # ADR-0187: the grid generator, its 21 cell documents, EVERY cell's three other
        # rungs + zoo + two HPO documents (owner question 4, expanded 2026-09-27), and
        # the quote backtest's tests
        "index_options/grid.py", "tests/test_quote_backtest.py",
        *(f"configs/grid/{stem}.json" for stem in (
            f"{u}-{b}" for u in ("spy", "qqq", "iwm")
            for b in ("1", "2-3", "5", "7-10", "14", "21", "30-45"))),
        *(f"configs/grid/{name}" for stem in (
            f"{u}-{b}" for u in ("spy", "qqq", "iwm")
            for b in ("1", "2-3", "5", "7-10", "14", "21", "30-45"))
          for name in _cell_extra_files(stem)),
        # ADR-0193: one put-credit-spread document per dividend-carrying (SPY, QQQ) cell,
        # ADR-0194: the same cells' condor gate-study documents, and ADR-0195: the same cells'
        # payoff-selection documents on the har-vix rung and on the empirical (control) rung
        *(f"configs/grid/{u}-{b}-{kind}.json" for u in ("spy", "qqq")
          for b in ("1", "2-3", "5", "7-10", "14", "21", "30-45")
          for kind in ("put-spread", "gate", "select", "empirical-select")),
        # ADR-0197: the long straddle on the three shortest buckets and the two long spreads on
        # 30-45, for SPY and QQQ (10 documents), and the debit backtests' tests
        *(f"configs/grid/{u}-{b}-long-straddle.json" for u in ("spy", "qqq")
          for b in ("1", "2-3", "5")),
        *(f"configs/grid/{u}-30-45-long-{kind}-spread.json" for u in ("spy", "qqq")
          for kind in ("call", "put")),
        "tests/test_debit_backtest.py",
    }
    ignored = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
               "build", "dist", "ob", "pipeline_runs", ".journal.lock"}
    actual = {p.relative_to(child_root).as_posix() for p in child_root.rglob("*")
              if p.is_file() and not any(part in ignored or part.endswith(".egg-info")
                                         for part in p.relative_to(child_root).parts)}
    assert actual == expected
    # ADR-0167's 30 + ADR-0168's 4 + ADR-0181's 4 + 4 research notes + ADR-0182's 8,
    # less pricing.py (moved to dskit.pipeline.option_pricing, ADR-0182 tier placement),
    # plus the options-dataset-hist source config (ADR-0182 amendment, 2026-09-25) = 57,
    # plus ADR-0187's original 29 (grid.py, 21 cells, 6 worked-cell documents,
    # test_quote_backtest.py) = 86, less those 6 plus the other 20 cells' 6 each = 120,
    # for owner question 4's expansion to every cell (2026-09-27) = 206,
    # plus ADR-0193's 14 put-spread documents = 220, plus ADR-0194's 14 gate documents = 234,
    # plus ADR-0195's 28 select documents (14 cells x two rungs) = 262,
    # plus ADR-0196's ledger_studies.py and its test = 264, plus ADR-0197's 10 documents and
    # test_debit_backtest.py = 275
    assert len(actual) == 275
    assert (child_root / "AGENTS.md").read_bytes() == (child_root / "CLAUDE.md").read_bytes()


# -- ADR-0187: the cell grid, its worked zoo and HPO documents --------------------------------

import copy  # noqa: E402
import hashlib  # noqa: E402
import math  # noqa: E402

from dskit.pipeline.document import PipelineDocument  # noqa: E402

from index_options import grid  # noqa: E402

GRID = "configs/grid"


def _cell_extra_files(cell_name):
    """The six document names one cell ships beyond its har-vix base (owner question 4)."""
    return (f"{cell_name}-empirical.json", f"{cell_name}-vix.json",
            f"{cell_name}-lightgbm-vix.json", f"{cell_name}-zoo.json",
            f"{cell_name}-hpo-har-vix.json", f"{cell_name}-hpo-lightgbm-vix.json")


#: The original hand-checked cell (ADR-0187); every cell now ships the same set (owner
#: question 4, expanded 2026-09-27), but this one stays the primary worked example below.
WORKED_CELL = "spy-30-45"
WORKED = _cell_extra_files(WORKED_CELL)
#: The base rungs the grid is generated from, pinned by digest: a cell document must
#: change through its generator, never by a quiet edit of a base.
BASE_DIGESTS = {
    "run-real-distribution.json": "4b1aa7d1d6e3311ae3cb6533cc5d2359173f96c0318928dfbcc4e5eb56943fe1",
    "run-real-har.json": "1d9f61a16386a5b20f91c648280f44bb4423e3ed828558bc06ad6e8f4ac576ca",
    "run-real-lightgbm.json": "b60c2b82a4e70581e009837c063dfd24a20a7960c57e510064b0882df8812455",
    "run-real-vix.json": "eed7070867848b7805d305121319b8c730d6505f36f98343fd7d062383c2d6ee",
    "run-real-har-vix.json": "5bc0893e340f41433fe9cb22d94b0a6ece765b897440856510f50c3244accc27",
    "run-real-lightgbm-vix.json": "c141abb62774b7a0d7283e45b1e4bc898b771fe033cafe1f7eeb5d55ef7d03e5",
    "run-real-zoo.json": "55d1d6aaae22142c00dc8f76a32c9d06f1de66fd4ff9168eae3803114eb0dd6f",
}


#: The MEASURED maximum calendar reach of h sessions over 1999-11-01..2025-12-15: weekday
#: sessions minus NYSE full-day closures (the fixed-rule holidays, 2001-09-11..14, 2004-06-11,
#: 2007-01-02, 2012-10-29/30, 2018-12-05, 2025-01-09), the worst window for each h. Restated
#: here, never read from grid.py: a formula such as ceil(1.4 h) + 4 UNDERSTATES it (6 vs 7 for
#: h = 1, 35 vs 39 for h = 22), and the embargo must cover what a train label can actually span.
LABEL_REACH_DAYS = {1: 7, 2: 10, 3: 11, 5: 13, 10: 21, 15: 28, 22: 39}


def _grid(child_root, name):
    return json.loads((child_root / GRID / name).read_text())


def test_the_run_real_documents_are_unchanged(child_root):
    for name, digest in BASE_DIGESTS.items():
        assert hashlib.sha256((child_root / "configs" / name).read_bytes()).hexdigest() == digest, name


def test_the_grid_is_twenty_one_cells_with_the_adr_buckets_and_starts():
    assert [b.label for b in grid.BUCKETS] == ["1", "2-3", "5", "7-10", "14", "21", "30-45"]
    assert [(b.dte_min, b.dte_max, b.label_horizon, b.band) for b in grid.BUCKETS] == [
        (1, 1, 1, 0.05), (2, 3, 2, 0.07), (5, 5, 3, 0.08), (7, 10, 5, 0.10),
        (14, 14, 10, 0.12), (21, 21, 15, 0.15), (30, 45, 22, 0.20)]
    assert [b.embargo_days for b in grid.BUCKETS] == [8, 10, 12, 17, 21, 28, 52]
    assert [(u.symbol, u.since, u.first, u.folds, u.backtest) for u in grid.UNDERLYINGS] == [
        ("SPY", "1999-11-01", "2008-01-01", 18, True),
        ("QQQ", "2000-03-21", "2011-01-01", 15, True),
        ("IWM", "2005-07-01", "2008-01-01", 18, False)]
    assert len(grid.CELLS) == 21 and len({c.name for c in grid.CELLS}) == 21
    assert grid.CELLS[0].name == "spy-1" and grid.CELLS[-1].name == "iwm-30-45"
    for cell in grid.CELLS:
        h, hi = cell.bucket.label_horizon, cell.bucket.dte_max
        # the embargo covers the label's reach and the settlement's reach
        assert cell.bucket.embargo_days >= max(LABEL_REACH_DAYS[h], hi)
        assert cell.bucket.embargo_days == hi + 7
        assert cell.bucket.dte_min >= 1  # no 0DTE
    # the generator's own copy (it writes the notes) must agree with this restatement
    assert grid.LABEL_REACH_DAYS == LABEL_REACH_DAYS
    spy = next(c for c in grid.CELLS if c.name == "spy-30-45")
    assert spy.since_ms == 941414400000  # 1999-11-01T00:00:00Z


def test_every_shipped_grid_file_equals_its_generator(child_root, tmp_path):
    files = grid.grid_files(child_root / "configs")
    expected = {f"grid/{c.name}.json" for c in grid.CELLS} | {
        f"grid/{n}" for c in grid.CELLS for n in _cell_extra_files(c.name)} | {
        f"grid/{c.name}-{kind}.json" for c in grid.CELLS if c.underlying.backtest
        for kind in ("put-spread", "gate", "select", "empirical-select")} | {
        f"grid/{u}-{b}-long-straddle.json" for u in ("spy", "qqq") for b in ("1", "2-3", "5")} | {
        f"grid/{u}-30-45-long-{kind}-spread.json" for u in ("spy", "qqq")
        for kind in ("call", "put")}
    assert set(files) == expected
    for relpath, document in files.items():
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    assert sorted(written) == sorted(files)
    for relpath in files:
        assert (tmp_path / relpath).read_bytes() == (child_root / "configs" / relpath).read_bytes()
    assert grid.write_grid(child_root / "configs", tmp_path) == written  # regenerating in place
    nested = tmp_path / "a" / "b"  # missing parents are created
    assert sorted(grid.write_grid(child_root / "configs", nested)) == sorted(files)
    assert (nested / "grid" / "spy-30-45.json").exists()


def _knob_numbers(obj, out):
    """Every number under a params/walkforward object, notes excluded."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key != "notes":
                _knob_numbers(value, out)
    elif isinstance(obj, list):
        for value in obj:
            _knob_numbers(value, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(obj)
    return out


@pytest.mark.parametrize("cell", grid.CELLS, ids=lambda c: c.name)
def test_each_cell_document_carries_its_horizon_bucket_and_folds(child_root, monkeypatch, cell):
    monkeypatch.chdir(child_root)
    doc = _grid(child_root, f"{cell.name}.json")
    pipe, h = doc["pipeline"], cell.bucket.label_horizon
    assert doc["name"] == f"index-options-grid-{cell.name}-har-vix"
    assert list(pipe)[:10] == ["underlying", "vix", "vix_by_date", "market", "rv", "labels",
                               "fwd", "model", "score", "condor"]
    assert pipe["underlying"]["uses"] == "index_options.observations:IndexCloseRows"
    assert pipe["underlying"]["params"] == {"root": "./ob", "source": "optionshist-chain",
                                            "symbol": cell.underlying.symbol,
                                            "since_ms": cell.since_ms}
    assert pipe["market"]["inputs"] == {"records": "$underlying.records",
                                        "iv_index": "$vix_by_date.table"}
    assert pipe["vix"]["params"] == {"root": "./ob", "source": "cboe-index", "symbol": "VIX"}
    assert pipe["labels"]["params"]["horizon"] == pipe["fwd"]["params"]["horizon"] == h
    assert pipe["model"]["uses"] == "dskit.pipeline.distribution_models:LinearScaleLocationScale"
    assert pipe["model"]["params"]["scale_multiplier"] == math.sqrt(h)
    assert pipe["model"]["params"]["scale_features"] == ["rv_1", "rv_5", "rv_22", "iv_index"]
    walk = doc["walkforward"]
    assert (walk["first"], walk["count"], walk["embargo_days"]) == (
        cell.underlying.first, cell.underlying.folds, cell.bucket.embargo_days)
    assert walk["step_days"] == walk["val_days"] == 365
    assert walk["objective"] == "$score.metrics.twcrps" and walk["select"] == "min"
    horizon_knobs = {pipe["labels"]["params"]["horizon"], pipe["fwd"]["params"]["horizon"],
                     pipe["model"]["params"]["scale_multiplier"]}
    if cell.underlying.backtest:
        assert list(pipe)[10:] == ["chain", "backtest"]
        chain, backtest = pipe["chain"]["params"], pipe["backtest"]["params"]
        assert pipe["chain"]["uses"] == "index_options.observations:ChainQuoteRows"
        assert pipe["backtest"]["uses"] == "index_options.nodes:CondorQuoteBacktest"
        assert pipe["backtest"]["inputs"] == {"forecasts": "$model.rows",
                                              "chain": "$chain.records",
                                              "underlying": "$underlying.records"}
        assert chain == {"root": "./ob", "source": "optionshist-chain",
                         "symbol": cell.underlying.symbol, "dte_min": cell.bucket.dte_min,
                         "dte_max": cell.bucket.dte_max,
                         "max_abs_log_moneyness": cell.bucket.band}
        for knob in ("dte_min", "dte_max", "max_abs_log_moneyness"):
            assert chain[knob] == backtest[knob]
        assert backtest["label_horizon"] == h and backtest["carry_rate"] == 0.055
        assert backtest["split"] == pipe["score"]["params"]["split"] == "val"
        assert (backtest["short_q"], backtest["wing_z"], backtest["multiplier"],
                backtest["fee_per_leg"]) == (0.1, 0.5, 100, 0.65)
        assert "hold_steps" not in backtest and "strike_increment" not in backtest
        horizon_knobs.add(backtest["label_horizon"])
    else:
        assert list(pipe)[10:] == [] and "chain" not in pipe and "backtest" not in pipe
    # the 21-day constant of the index track never survives: every horizon knob is the cell's
    assert horizon_knobs == {h, math.sqrt(h)}
    assert 4.58257569495584 not in _knob_numbers(pipe, [])
    planned = plan(load_document(str(child_root / GRID / f"{cell.name}.json")))
    assert set(planned.order) == set(pipe)


@pytest.mark.parametrize("cell", grid.CELLS, ids=lambda c: c.name)
@pytest.mark.parametrize("suffix", ["-empirical", "-vix", "-lightgbm-vix"])
def test_every_cells_rungs_differ_from_its_har_vix_document_only_in_name_notes_and_model(
    child_root, monkeypatch, cell, suffix
):
    monkeypatch.chdir(child_root)
    base = _grid(child_root, f"{cell.name}.json")
    rung = _grid(child_root, f"{cell.name}{suffix}.json")
    stripped = [copy.deepcopy(d) for d in (base, rung)]
    for d in stripped:
        d.pop("name")
        d.pop("notes")
        d["pipeline"].pop("model")
    assert stripped[0] == stripped[1]
    shared = ("fit_split", "label", "scale_field", "scale_multiplier", "n_samples")
    assert {k: rung["pipeline"]["model"]["params"][k] for k in shared} == \
        {k: base["pipeline"]["model"]["params"][k] for k in shared}
    name = f"{cell.name}{suffix}.json"
    assert set(plan(load_document(str(child_root / GRID / name))).order) == set(rung["pipeline"])


@pytest.mark.parametrize("cell", grid.CELLS, ids=lambda c: c.name)
def test_every_cells_zoo_compares_four_rungs(child_root, monkeypatch, cell):
    monkeypatch.chdir(child_root)
    zoo = _grid(child_root, f"{cell.name}-zoo.json")
    params = zoo["stages"]["plan"]["params"]
    candidates = params["candidates"]
    assert [(c["id"], c["path"]) for c in candidates] == [
        ("empirical", f"{cell.name}-empirical.json"), ("vix", f"{cell.name}-vix.json"),
        ("har-vix", f"{cell.name}.json"), ("lightgbm-vix", f"{cell.name}-lightgbm-vix.json")]
    real = {c["id"]: c for c in json.loads(
        (child_root / "configs" / "run-real-zoo.json").read_text())["stages"]["plan"]["params"]["candidates"]}
    for candidate in candidates:  # the rung's own metadata is the real zoo's, re-pointed
        assert {k: v for k, v in candidate.items() if k != "path"} == \
            {k: v for k, v in real[candidate["id"]].items() if k != "path"}
    always = {"pipeline.underlying", "pipeline.vix", "pipeline.vix_by_date", "pipeline.market",
              "pipeline.rv", "pipeline.labels", "pipeline.fwd", "pipeline.score",
              "pipeline.condor", "pipeline.model.inputs",
              "pipeline.model.params.scale_multiplier", "walkforward"}
    assert always <= set(params["contract_paths"])
    # IWM has no backtest, so its zoo names no chain/backtest contract path (ADR-0187)
    if cell.underlying.backtest:
        assert {"pipeline.chain", "pipeline.backtest"} <= set(params["contract_paths"])
    else:
        assert "pipeline.chain" not in params["contract_paths"]
        assert "pipeline.backtest" not in params["contract_paths"]
    assert "pipeline.model.params.ridge_alpha" not in params["contract_paths"]
    assert params["protocol"]["attempt_family"] == f"index-options-grid-{cell.name}"
    assert zoo["stages"]["approval"]["params"]["approved_by"] == "PENDING-PLAN-REVIEW"
    assert zoo["pipeline"]["market"]["params"]["symbol"] == cell.underlying.symbol
    PipelineDocument.from_obj(zoo)
    for c in candidates:
        assert (child_root / GRID / c["path"]).is_file()


@pytest.mark.parametrize("cell", grid.CELLS, ids=lambda c: c.name)
@pytest.mark.parametrize("rung, keys", [
    ("har-vix", ["model.ridge_alpha"]),
    ("lightgbm-vix", ["model.lgbm_params.num_leaves", "model.lgbm_params.learning_rate",
                      "model.lgbm_params.min_child_samples"]),
])
def test_every_cells_hpo_documents_search_the_rungs_own_knobs(
    child_root, monkeypatch, cell, rung, keys
):
    monkeypatch.chdir(child_root)
    doc = _grid(child_root, f"{cell.name}-hpo-{rung}.json")
    search = doc["pipeline"]["search"]
    assert search["uses"] == "hpo-grid"
    assert search["params"]["objective"] == "$score.metrics.twcrps"
    assert search["params"]["select"] == "min"
    assert list(search["params"]["space"]) == keys
    assert all(isinstance(v, list) and len(v) >= 2 for v in search["params"]["space"].values())
    for key in ("condor", "chain", "backtest"):
        assert key not in doc["pipeline"]
    base = _grid(child_root, f"{cell.name}.json" if rung == "har-vix"
                 else f"{cell.name}-{rung}.json")
    for node in ("underlying", "vix", "vix_by_date", "market", "rv", "labels", "fwd", "model",
                 "score"):
        assert doc["pipeline"][node] == base["pipeline"][node], node
    assert doc["walkforward"] == base["walkforward"]
    assert doc["name"] == base["name"] + "-hpo"
    # the planner's search rules hold: a searchable member knob, a val score objective
    planned = plan(load_document(str(child_root / GRID / f"{cell.name}-hpo-{rung}.json")))
    assert "search" in planned.order


def test_synthetic_distribution_config_pins_its_agreements(child_root):
    """sqrt(horizon) scales daily vol to the label; the embargo covers the label's reach."""
    import math

    doc = json.loads((child_root / "configs/run-synthetic-distribution.json").read_text())
    horizon = doc["pipeline"]["labels"]["params"]["horizon"]
    model = doc["pipeline"]["model"]["params"]
    assert model["scale_multiplier"] == math.sqrt(horizon)
    assert model["scale_field"] in doc["pipeline"]["labels"]["params"]["carry_fields"]
    # the synthetic path steps one calendar day per row, so steps == days
    assert doc["walkforward"]["embargo_days"] > horizon
    assert model["fit_split"] == "train"
    assert all(doc["pipeline"][k]["params"]["split"] != model["fit_split"]
               for k in ("score", "condor"))


# -- ADR-0193: the put-credit-spread documents, one per dividend-carrying cell ----------------

#: The 14 SPY and QQQ cell stems (restated here, never read from the generator's table).
BACKTEST_STEMS = [f"{u}-{b}" for u in ("spy", "qqq")
                  for b in ("1", "2-3", "5", "7-10", "14", "21", "30-45")]
ALL_STEMS = [f"{u}-{b}" for u in ("spy", "qqq", "iwm")
             for b in ("1", "2-3", "5", "7-10", "14", "21", "30-45")]
BACKTEST_CELLS = [c for c in grid.CELLS if c.name in BACKTEST_STEMS]


def test_the_put_spread_strikes_are_the_owners_answers_and_exported():
    # 0.16 quantile ~ a 16-delta short put, wing 0.65 further ~ a 5-delta long put (ADR-0193)
    assert grid.PUT_SPREAD_SHORT_Q == 0.16 and grid.PUT_SPREAD_WING_Z == 0.65
    assert {"PUT_SPREAD_SHORT_Q", "PUT_SPREAD_WING_Z", "put_spread_document"} <= set(grid.__all__)
    assert 0 < grid.PUT_SPREAD_SHORT_Q < 0.5 and grid.PUT_SPREAD_WING_Z > 0


def test_the_grid_adds_exactly_one_put_spread_document_per_backtest_cell(child_root):
    files = grid.grid_files(child_root / "configs")
    # exact names, not a suffix: ADR-0197's "<cell>-long-put-spread.json" also ends in it
    spreads = {f"grid/{stem}-put-spread.json" for stem in BACKTEST_STEMS} & set(files)
    assert spreads == {f"grid/{stem}-put-spread.json" for stem in BACKTEST_STEMS}
    assert len(spreads) == 14 and not any("iwm" in f for f in spreads)
    # everything else is what shipped before ADR-0193 — a har-vix document per cell and its six
    # extras — plus ADR-0194's gate documents (pinned in their own test below)
    gates = {f for f in files if f.endswith("-gate.json")}
    selects = {f for f in files if f.endswith("-select.json")}     # ADR-0195, pinned below
    debits = {f for f in files if "-long-" in f}                   # ADR-0197, pinned below
    assert set(files) - spreads - gates - selects - debits == {
        f"grid/{stem}.json" for stem in ALL_STEMS} | {
        f"grid/{name}" for stem in ALL_STEMS for name in _cell_extra_files(stem)}
    for cell in grid.CELLS:
        assert (f"grid/{cell.name}-put-spread.json" in files) is cell.underlying.backtest
        assert set(grid.cell_files(child_root / "configs", cell)) & spreads == (
            {f"grid/{cell.name}-put-spread.json"} if cell.underlying.backtest else set())


def test_every_shipped_put_spread_file_equals_its_generator(child_root, tmp_path):
    files = {f: d for f, d in grid.grid_files(child_root / "configs").items()
             if f.endswith("-put-spread.json") and "-long-" not in f}
    assert len(files) == 14
    for relpath, document in files.items():
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    for relpath in files:
        assert relpath in written
        assert (tmp_path / relpath).read_bytes() == (child_root / "configs" / relpath).read_bytes()


def test_every_generated_document_that_shipped_before_is_unchanged(child_root):
    # the zoo documents are excluded: their approval hashes were pinned by hand and already
    # differ from the generator on the base (a pre-existing, separate failure)
    files = grid.grid_files(child_root / "configs")
    checked = 0
    for relpath, document in files.items():
        if relpath.endswith(("-put-spread.json", "-gate.json", "-select.json", "-zoo.json")) \
                or "-long-" in relpath:
            continue
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
        checked += 1
    assert checked == 21 + 21 * 5   # har-vix, then five of the six extras per cell (no zoo)


def _without_the_sizing_study(doc):
    """A sized document with the ADR-0196 study taken back out (restated here, not the generator's)."""
    plain = copy.deepcopy(doc)
    del plain["pipeline"]["sizing"]
    backtest = plain["pipeline"]["backtest"]
    backtest["inputs"]["forecasts"] = "$signals.rows"
    del backtest["params"]["size_fields"]
    return plain


def _without_the_gate_study(doc):
    """A study document with both studies taken back out (restated here, not the generator's)."""
    plain = _without_the_sizing_study(doc)
    for key in ("vix3m", "signals"):
        del plain["pipeline"][key]
    backtest = plain["pipeline"]["backtest"]
    backtest["inputs"]["forecasts"] = "$model.rows"
    del backtest["params"]["gate_fields"]
    return plain


def _described(notes):
    """A document's notes without its one run sentence, restated here (never the generator's)."""
    stripped = re.sub(r" ?Run: python -m dskit\.pipeline walkforward \S+ --asof <today>\.", "",
                      notes)
    assert stripped != notes, "no run sentence to strip"
    return stripped


@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_put_spread_document_swaps_only_the_backtest_node_and_carries_the_gate_study(
    child_root, cell
):
    # ADR-0194 re-pointed this ADR-0193 pin: a put-spread document is the condor cell's with the
    # backtest node swapped AND the gate study on top; with the study taken out, only the swap
    # remains, exactly as ADR-0193 shipped it.
    base = _grid(child_root, f"{cell.name}.json")
    shipped = _grid(child_root, f"{cell.name}-put-spread.json")
    # ADR-0195 (ADR-0194 review B-M5): the name is the file's (the ungated document no longer
    # ships) and the study APPENDS to the put-spread description instead of replacing it
    assert shipped["name"] == f"index-options-grid-{cell.name}-put-spread"
    assert shipped["notes"] != base["notes"] and "ADR-0194" in shipped["notes"]
    assert "put credit spread" in shipped["notes"] and "ADR-0193" in shipped["notes"]
    assert f"configs/grid/{cell.name}-put-spread.json" in shipped["notes"]   # its own path
    assert shipped["notes"].index("ADR-0193") < shipped["notes"].index("ADR-0194")
    assert "Never decision-eligible" in shipped["notes"]
    doc = _without_the_gate_study(shipped)
    assert list(doc) == list(base) and list(doc["pipeline"]) == list(base["pipeline"])
    for key in base:
        if key not in ("name", "notes", "pipeline"):
            assert doc[key] == base[key], key
    for node in base["pipeline"]:
        if node != "backtest":
            assert doc["pipeline"][node] == base["pipeline"][node], node
    swapped, original = doc["pipeline"]["backtest"], base["pipeline"]["backtest"]
    assert swapped["uses"] == "index_options.nodes:PutSpreadQuoteBacktest"
    assert original["uses"] == "index_options.nodes:CondorQuoteBacktest"
    assert swapped["inputs"] == original["inputs"]
    assert swapped["notes"] != original["notes"] and "put credit spread" in swapped["notes"]
    assert list(swapped) == list(original) and list(swapped["params"]) == list(original["params"])
    changed = {k for k in original["params"] if swapped["params"][k] != original["params"][k]}
    assert changed == {"short_q", "wing_z"}
    assert (swapped["params"]["short_q"], swapped["params"]["wing_z"]) == (0.16, 0.65)
    assert (original["params"]["short_q"], original["params"]["wing_z"]) == (0.1, 0.5)


@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_put_spread_document_resolves_through_the_planner(child_root, monkeypatch, cell):
    from index_options.nodes import PutSpreadQuoteBacktest

    monkeypatch.chdir(child_root)
    path = child_root / GRID / f"{cell.name}-put-spread.json"
    planned = plan(load_document(str(path)))
    assert planned.resolved["backtest"].cls is PutSpreadQuoteBacktest
    assert set(planned.order) == set(_grid(child_root, f"{cell.name}-put-spread.json")["pipeline"])
    params = json.loads(path.read_text())["pipeline"]["backtest"]["params"]
    assert PutSpreadQuoteBacktest.validate_params(params) == []


def test_a_cell_without_a_backtest_has_no_put_spread_document_and_the_base_is_untouched(child_root):
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    before = copy.deepcopy(base)
    iwm = next(c for c in grid.CELLS if c.underlying.symbol == "IWM")
    with pytest.raises(ValueError, match="IWM"):
        grid.put_spread_document(base, iwm)
    spy = next(c for c in grid.CELLS if c.name == "spy-30-45")
    document = grid.put_spread_document(base, spy)
    assert base == before   # derived on a copy
    assert "walkforward" not in document["notes"]     # A-N4: the shipper ends it with the one run sentence
    # the ADR-0193 generator is unchanged; the shipped file is that document with the gate study
    # (ADR-0194) and the sizing study (ADR-0196) on top, and the one run sentence of ADR-0196
    gated = grid.gate_study_document(document, name_suffix="")
    assert _differences(gated, _grid(child_root, "spy-30-45-put-spread.json")) == SIZING_PATHS
    assert "vix3m" not in document["pipeline"] and "gate_fields" not in \
        document["pipeline"]["backtest"]["params"]


# -- ADR-0194: the gate study, on the put-spread documents and on new condor gate documents ----

#: The four gates' names in their order, restated here (never read from the node or the grid).
GATE_NAMES = ["gate_term_inverted", "gate_term_high_pct", "gate_vrp_nonpositive", "gate_any"]
#: The pre-registered ADR-0194 knobs, typed out here — never read from the node or the grid — so
#: the documents are pinned to the owner's numbers, not to whatever the code's defaults become.
SIGNALS_KNOBS = {"implied_field": "iv_index", "realized_field": "rv_22", "periods_per_year": 252,
                 "inverted_at": 1.0, "high_ratio_pct": 0.8, "min_history": 252,
                 "lag_sessions": 1}


#: ADR-0196's pre-registered sizing knobs, typed out here — never read from the node or the grid.
SIZING_KNOBS = {"implied_field": "iv_index", "realized_field": "rv_22", "lag_sessions": 1,
                "min_history": 252, "min_weight": 0.25, "max_weight": 4.0}
#: The three weights' names in their order, restated here (never read from the node or the grid).
SIZE_NAMES = ["size_inv_implied_var", "size_inv_realized_var", "size_implied"]
#: Every path at which a sized document differs from the same document without the study (the
#: notes also gain the run sentence of the file the shipper names).
SIZING_PATHS = {"/notes", "/pipeline/sizing", "/pipeline/backtest/inputs/forecasts",
                "/pipeline/backtest/params/size_fields", "/pipeline/backtest/notes"}


def _differences(a, b, path=""):
    """Every path at which two JSON values differ; a key on one side only is a path too."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = set()
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                out.add(f"{path}/{key}")
            else:
                out |= _differences(a[key], b[key], f"{path}/{key}")
        return out
    return set() if a == b else {path or "/"}


def test_the_gate_study_is_exported_and_its_source_is_the_wide_cboe_index_stream():
    assert "gate_study_document" in grid.__all__
    assert grid.TERM_SOURCE == "cboe-index-wide" and grid.TERM_SYMBOL == "VIX3M"


def test_the_grid_adds_exactly_one_gate_document_per_backtest_cell(child_root):
    files = grid.grid_files(child_root / "configs")
    gates = {f for f in files if f.endswith("-gate.json")}
    assert gates == {f"grid/{stem}-gate.json" for stem in BACKTEST_STEMS}
    assert len(gates) == 14 and not any("iwm" in f for f in gates)
    for cell in grid.CELLS:
        assert set(grid.cell_files(child_root / "configs", cell)) & gates == (
            {f"grid/{cell.name}-gate.json"} if cell.underlying.backtest else set())


def test_every_shipped_gate_and_put_spread_file_equals_its_generator(child_root, tmp_path):
    files = {f: d for f, d in grid.grid_files(child_root / "configs").items()
             if f.endswith(("-gate.json", "-put-spread.json")) and "-long-" not in f}
    assert len(files) == 28
    for relpath, document in files.items():
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    for relpath in files:
        assert relpath in written
        assert (tmp_path / relpath).read_bytes() == (child_root / "configs" / relpath).read_bytes()


@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_gate_document_is_its_condor_document_plus_the_gate_study_only(child_root, cell):
    condor = _grid(child_root, f"{cell.name}.json")
    gate = _grid(child_root, f"{cell.name}-gate.json")
    assert _differences(condor, gate) == {
        "/name", "/notes", "/pipeline/vix3m", "/pipeline/signals", "/pipeline/sizing",
        "/pipeline/backtest/inputs/forecasts", "/pipeline/backtest/params/gate_fields",
        "/pipeline/backtest/params/size_fields", "/pipeline/backtest/notes"}
    assert gate["name"] == f"index-options-grid-{cell.name}-har-vix-gate"
    assert "ADR-0194" in gate["notes"] and "Never decision-eligible" in gate["notes"]
    # A-N4: the source's description is kept, its run sentence (for the SOURCE file) is not
    assert gate["notes"].startswith(_described(condor["notes"])) and gate["notes"] != condor["notes"]
    assert gate["pipeline"]["backtest"]["notes"].startswith(condor["pipeline"]["backtest"]["notes"])
    assert gate["pipeline"]["backtest"]["notes"] != condor["pipeline"]["backtest"]["notes"]
    assert _without_the_gate_study(gate)["pipeline"] == {
        **condor["pipeline"], "backtest": {
            **condor["pipeline"]["backtest"],
            "notes": gate["pipeline"]["backtest"]["notes"]}}
    assert gate["walkforward"] == condor["walkforward"]


GATE_STUDY_NAMES = ["{}-gate.json", "{}-put-spread.json", "{}-select.json",
                    "{}-empirical-select.json"]


@pytest.mark.parametrize("name", GATE_STUDY_NAMES)
@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_the_gate_study_is_wired_the_same_way_into_every_document_that_carries_it(
    child_root, cell, name
):
    from index_options.nodes import (
        CondorQuoteBacktest,
        PayoffSelectQuoteBacktest,
        PutSpreadQuoteBacktest,
        VolRegimeSignals,
        VolSizingWeights,
    )

    doc = _grid(child_root, name.format(cell.name))
    pipe = doc["pipeline"]
    assert list(pipe)[:8] == ["underlying", "vix", "vix_by_date", "market", "rv", "labels",
                              "fwd", "model"]
    assert list(pipe)[8:11] == ["vix3m", "signals", "sizing"]     # right after the model
    assert pipe["vix3m"]["uses"] == "index_options.observations:IndexCloseRows"
    assert pipe["vix3m"]["params"] == {"root": "./ob", "source": "cboe-index-wide",
                                       "symbol": "VIX3M"}
    assert "since_ms" not in pipe["vix3m"]["params"]
    assert set(pipe["vix3m"]) == {"uses", "params", "notes"} and pipe["vix3m"]["notes"]
    # every knob is written out (ADR-0195, B-M2), so the identity hash covers the thresholds
    assert pipe["signals"]["uses"] == "index_options.nodes:VolRegimeSignals"
    assert pipe["signals"]["inputs"] == {"rows": "$model.rows", "term": "$vix3m.records"}
    assert set(pipe["signals"]) == {"uses", "inputs", "params", "notes"}
    assert pipe["signals"]["params"] == SIGNALS_KNOBS and pipe["signals"]["notes"]
    assert VolRegimeSignals.validate_params(pipe["signals"]["params"]) == []
    # ADR-0196: the sizing node reads the annotated rows and the backtest reads ITS rows, with
    # every knob written out too
    assert pipe["sizing"]["uses"] == "index_options.nodes:VolSizingWeights"
    assert pipe["sizing"]["inputs"] == {"rows": "$signals.rows"}
    assert set(pipe["sizing"]) == {"uses", "inputs", "params", "notes"}
    assert pipe["sizing"]["params"] == SIZING_KNOBS and pipe["sizing"]["notes"]
    assert VolSizingWeights.validate_params(pipe["sizing"]["params"]) == []
    backtest = pipe["backtest"]
    assert backtest["inputs"] == {"forecasts": "$sizing.rows", "chain": "$chain.records",
                                  "underlying": "$underlying.records"}
    assert backtest["params"]["gate_fields"] == GATE_NAMES
    assert backtest["params"]["size_fields"] == SIZE_NAMES
    assert list(backtest["params"])[-2:] == ["gate_fields", "size_fields"]
    assert list(VolRegimeSignals.GATE_FIELDS) == GATE_NAMES
    assert list(VolSizingWeights.SIZE_FIELDS) == SIZE_NAMES
    cls = (PutSpreadQuoteBacktest if "put-spread" in name
           else PayoffSelectQuoteBacktest if "select" in name else CondorQuoteBacktest)
    assert cls.validate_params(backtest["params"]) == []
    # only the backtest reads the annotated rows; score and the condor report keep the model's
    assert pipe["score"]["inputs"] == {"forecasts": "$model.rows"}
    assert pipe["condor"]["inputs"] == {"forecasts": "$model.rows"}


@pytest.mark.parametrize("name", GATE_STUDY_NAMES)
@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_gate_study_document_resolves_through_the_planner(child_root, monkeypatch, cell, name):
    from index_options.nodes import VolRegimeSignals, VolSizingWeights

    monkeypatch.chdir(child_root)
    path = child_root / GRID / name.format(cell.name)
    planned = plan(load_document(str(path)))
    assert planned.resolved["signals"].cls is VolRegimeSignals
    assert planned.role_of("signals") == "transform"
    assert set(planned.order) == set(json.loads(path.read_text())["pipeline"])
    assert planned.resolved["sizing"].cls is VolSizingWeights
    assert planned.role_of("sizing") == "transform"
    assert {("model", "signals"), ("vix3m", "signals"), ("signals", "sizing"),
            ("sizing", "backtest")} <= set(planned.edges)
    assert ("model", "backtest") not in planned.edges     # rewired, not wired twice
    assert ("signals", "backtest") not in planned.edges
    order = list(planned.order)
    assert order.index("signals") < order.index("sizing") < order.index("backtest")
    assert order.index("vix3m") < order.index("signals") and order.index("model") < order.index(
        "signals")


def test_the_cli_validates_and_plans_a_gate_document_and_a_put_spread_document(child_root):
    import subprocess
    import sys

    for name in ("spy-30-45-gate.json", "qqq-1-put-spread.json"):
        for verb in ("validate", "plan"):
            done = subprocess.run(
                [sys.executable, "-m", "dskit.pipeline", verb, f"configs/grid/{name}"],
                cwd=child_root, capture_output=True, text=True, timeout=120)
            assert done.returncode == 0, (name, verb, done.stderr[-500:])
        planned = json.loads(done.stdout)       # the last one is the plan
        assert planned["nodes"]["signals"]["class"] == "index_options.nodes:VolRegimeSignals"
        assert planned["nodes"]["signals"]["inputs"] == {"rows": "$model.rows",
                                                         "term": "$vix3m.records"}
        assert planned["nodes"]["sizing"]["class"] == "index_options.nodes:VolSizingWeights"
        assert planned["nodes"]["sizing"]["inputs"] == {"rows": "$signals.rows"}
        assert planned["nodes"]["backtest"]["inputs"]["forecasts"] == "$sizing.rows"
        assert planned["order"].index("signals") < planned["order"].index("sizing") < \
            planned["order"].index("backtest")


def test_gate_study_document_refuses_a_document_without_a_backtest_and_leaves_its_input_alone(
    child_root
):
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    iwm = next(c for c in grid.CELLS if c.underlying.symbol == "IWM")
    no_backtest = grid.grid_document(base, iwm)
    assert "backtest" not in no_backtest["pipeline"]
    with pytest.raises(ValueError, match="backtest"):
        grid.gate_study_document(no_backtest)
    spy = next(c for c in grid.CELLS if c.name == "spy-30-45")
    condor = grid.grid_document(base, spy)
    before = copy.deepcopy(condor)
    gated = grid.gate_study_document(condor)
    assert condor == before and gated != before
    # the shipped file is this document with the sizing study (ADR-0196) and its run sentence
    assert _differences(gated, _grid(child_root, "spy-30-45-gate.json")) == SIZING_PATHS
    # a document the study was already applied to is not studied twice
    with pytest.raises(ValueError, match="already"):
        grid.gate_study_document(gated)


# -- ADR-0195: the ADR-0194 review backlog on the gate study ------------------------------------


@pytest.mark.parametrize("name", GATE_STUDY_NAMES)
@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_every_signals_knob_is_written_out_so_the_identity_hash_covers_the_thresholds(
    child_root, cell, name
):
    # B-M2: with no params on the node, editing the code's defaults changed what 28 documents
    # compute while their hash and run directory stayed the same
    from dskit.pipeline.document import PipelineDocument

    shipped = _grid(child_root, name.format(cell.name))
    assert shipped["pipeline"]["signals"]["params"] == SIGNALS_KNOBS
    baseline = PipelineDocument.from_obj(shipped).hash
    for knob, other in (("inverted_at", 1.05), ("high_ratio_pct", 0.9), ("min_history", 250),
                        ("lag_sessions", 2), ("periods_per_year", 250),
                        ("implied_field", "vix"), ("realized_field", "rv_5")):
        changed = copy.deepcopy(shipped)
        changed["pipeline"]["signals"]["params"][knob] = other
        assert PipelineDocument.from_obj(changed).hash != baseline, knob
    bare = copy.deepcopy(shipped)
    del bare["pipeline"]["signals"]["params"]
    assert PipelineDocument.from_obj(bare).hash != baseline


def _spy_condor(child_root):
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    return grid.grid_document(base, next(c for c in grid.CELLS if c.name == "spy-30-45"))


def test_the_signals_knobs_are_the_nodes_own_defaults_written_out_in_full(child_root):
    from index_options.nodes import VolRegimeSignals

    # the generator writes the node's defaults; both equal the owner's numbers restated above
    doc = grid.gate_study_document(_spy_condor(child_root))
    assert doc["pipeline"]["signals"]["params"] == dict(VolRegimeSignals.DEFAULTS) == SIGNALS_KNOBS


def test_gate_study_document_keeps_the_sources_description_and_names_by_suffix(child_root):
    # B-M5: the study appended to the source's notes, never replaced them, and the name is the
    # source's plus the caller's suffix ("-gate" unless the caller says the document's own file
    # already names it)
    source = _spy_condor(child_root)
    default, plain = grid.gate_study_document(source), grid.gate_study_document(
        source, name_suffix="")
    assert default["name"] == f"{source['name']}-gate" and plain["name"] == source["name"]
    assert grid.gate_study_document(source, name_suffix="-x")["name"] == f"{source['name']}-x"
    for gated in (default, plain):
        assert gated["notes"].startswith(_described(source["notes"]))      # A-N4: minus its run
        assert gated["notes"] != source["notes"]
        assert "ADR-0194" in gated["notes"] and "Never decision-eligible" in gated["notes"]
        assert "walkforward" not in gated["notes"]       # the run sentence is the shipper's
    assert default["pipeline"] == plain["pipeline"]


def test_gate_study_document_refuses_a_backtest_that_reads_other_rows_or_has_gates_already(
    child_root
):
    # A-N1: the study re-points the backtest at the annotated model rows, so it must START
    # reading them, or "the same trades" would silently be someone else's
    source = _spy_condor(child_root)
    elsewhere = copy.deepcopy(source)
    elsewhere["pipeline"]["other"] = copy.deepcopy(elsewhere["pipeline"]["model"])
    elsewhere["pipeline"]["backtest"]["inputs"]["forecasts"] = "$other.rows"
    before = copy.deepcopy(elsewhere)
    with pytest.raises(ValueError, match=r"forecasts.*\$model\.rows"):
        grid.gate_study_document(elsewhere)
    assert elsewhere == before
    gated = copy.deepcopy(source)
    gated["pipeline"]["backtest"]["params"]["gate_fields"] = ["mine"]
    with pytest.raises(ValueError, match="gate_fields"):
        grid.gate_study_document(gated)
    assert gated["pipeline"]["backtest"]["params"]["gate_fields"] == ["mine"]


# -- ADR-0195: the payoff-selection documents ---------------------------------------------------

#: The owner's pre-registered candidates, typed out here (never read from the generator).
SELECT_KNOBS = {"candidate_structures": ["put_spread", "call_spread", "condor"],
                "candidate_short_q": [0.10, 0.16, 0.20, 0.25, 0.30],
                "candidate_wing_z": [0.35, 0.65, 1.0]}
#: Every path at which a select document may differ from its source document (the gate study's
#: seven plus the backtest's class and the three candidate knobs).
SELECT_PATHS = {
    "/name", "/notes", "/pipeline/vix3m", "/pipeline/signals", "/pipeline/sizing",
    "/pipeline/backtest/uses", "/pipeline/backtest/notes",
    "/pipeline/backtest/inputs/forecasts", "/pipeline/backtest/params/gate_fields",
    "/pipeline/backtest/params/size_fields",
    "/pipeline/backtest/params/candidate_structures",
    "/pipeline/backtest/params/candidate_short_q",
    "/pipeline/backtest/params/candidate_wing_z"}
#: (file template, the source document it is derived from) on the two rungs.
SELECT_SOURCES = [("{}-select.json", "{}.json"), ("{}-empirical-select.json", "{}-empirical.json")]


def test_the_select_candidates_are_the_owners_pre_registered_answers_and_exported():
    assert grid.SELECT_STRUCTURES == tuple(SELECT_KNOBS["candidate_structures"])
    assert grid.SELECT_SHORT_Q == tuple(SELECT_KNOBS["candidate_short_q"])
    assert grid.SELECT_WING_Z == tuple(SELECT_KNOBS["candidate_wing_z"])
    assert len(grid.SELECT_STRUCTURES) * len(grid.SELECT_SHORT_Q) * len(grid.SELECT_WING_Z) == 45
    assert {"SELECT_STRUCTURES", "SELECT_SHORT_Q", "SELECT_WING_Z", "select_document",
            "TERM_SOURCE", "TERM_SYMBOL"} <= set(grid.__all__)
    from index_options.nodes import PayoffSelectQuoteBacktest

    # every pre-registered value is one the node accepts, and only those are in the tuples
    knobs = {**SELECT_KNOBS, "split": "val", "short_q": 0.1, "wing_z": 0.5, "multiplier": 100,
             "fee_per_leg": 0.65, "dte_min": 1, "dte_max": 1, "max_abs_log_moneyness": 0.05,
             "label_horizon": 1, "carry_rate": 0.055}
    assert PayoffSelectQuoteBacktest.validate_params(knobs) == []


def test_the_grid_adds_exactly_two_select_documents_per_backtest_cell(child_root):
    files = grid.grid_files(child_root / "configs")
    selects = {f for f in files if f.endswith("-select.json")}
    assert selects == {f"grid/{stem}-{kind}.json" for stem in BACKTEST_STEMS
                       for kind in ("select", "empirical-select")}
    assert len(selects) == 28 and not any("iwm" in f for f in selects)
    for cell in grid.CELLS:
        assert set(grid.cell_files(child_root / "configs", cell)) & selects == (
            {f"grid/{cell.name}-select.json", f"grid/{cell.name}-empirical-select.json"}
            if cell.underlying.backtest else set())


def test_every_shipped_select_file_equals_its_generator(child_root, tmp_path):
    # the zoo files already differ from their generator on the base, so the broad pin cannot be
    # the gate for these: this one is, file by file and through write_grid
    files = {f: d for f, d in grid.grid_files(child_root / "configs").items()
             if f.endswith("-select.json")}
    assert len(files) == 28
    for relpath, document in files.items():
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    for relpath in files:
        assert relpath in written
        assert (tmp_path / relpath).read_bytes() == (child_root / "configs" / relpath).read_bytes()


@pytest.mark.parametrize("template, source", SELECT_SOURCES, ids=["har-vix", "empirical"])
@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_select_document_is_its_source_except_the_declared_paths(
    child_root, cell, template, source
):
    base = _grid(child_root, source.format(cell.name))
    shipped = _grid(child_root, template.format(cell.name))
    assert _differences(base, shipped) == SELECT_PATHS
    assert shipped["name"] == f"{base['name']}-select"
    assert shipped["notes"].startswith(_described(base["notes"]))        # A-N4
    assert "ADR-0195" in shipped["notes"]
    assert "ADR-0194" in shipped["notes"] and "Never decision-eligible" in shipped["notes"]
    assert shipped["notes"].index("ADR-0195") < shipped["notes"].index("ADR-0194")
    backtest, original = shipped["pipeline"]["backtest"], base["pipeline"]["backtest"]
    assert backtest["uses"] == "index_options.nodes:PayoffSelectQuoteBacktest"
    assert original["uses"] == "index_options.nodes:CondorQuoteBacktest"
    assert backtest["notes"].startswith("ADR-0195") and backtest["notes"] != original["notes"]
    # the candidate knobs are the owner's and sit before gate_fields; everything else is the
    # condor's, so the implied book is the source's condor at its short_q / wing_z
    params = backtest["params"]
    assert {k: params[k] for k in SELECT_KNOBS} == SELECT_KNOBS
    assert list(params)[-5:] == [*SELECT_KNOBS, "gate_fields", "size_fields"]
    assert params["gate_fields"] == GATE_NAMES and params["size_fields"] == SIZE_NAMES
    rest = {k: v for k, v in params.items()
            if k not in (*SELECT_KNOBS, "gate_fields", "size_fields")}
    assert rest == original["params"]
    assert (params["short_q"], params["wing_z"]) == (0.1, 0.5)
    # the study itself is the ADR-0194 one, knobs written out
    assert shipped["pipeline"]["signals"]["params"] == SIGNALS_KNOBS
    assert shipped["pipeline"]["sizing"]["params"] == SIZING_KNOBS
    assert shipped["pipeline"]["backtest"]["inputs"]["forecasts"] == "$sizing.rows"
    assert {k: v for k, v in shipped.items() if k not in ("name", "notes", "pipeline")} == {
        k: v for k, v in base.items() if k not in ("name", "notes", "pipeline")}


@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_the_two_select_documents_of_a_cell_differ_only_as_their_sources_do(child_root, cell):
    har = _grid(child_root, f"{cell.name}-select.json")
    empirical = _grid(child_root, f"{cell.name}-empirical-select.json")
    # the control swaps the forecast model and nothing else (the gate study and the selector are
    # identical), which is what makes the premium-vs-forecast difference readable
    assert _differences(har, empirical) == _differences(
        _grid(child_root, f"{cell.name}.json"), _grid(child_root, f"{cell.name}-empirical.json"))
    assert all(p in ("/name", "/notes") or p.startswith("/pipeline/model")
               for p in _differences(har, empirical))
    assert har["name"] == f"index-options-grid-{cell.name}-har-vix-select"
    assert empirical["name"] == f"index-options-grid-{cell.name}-empirical-select"
    assert har["pipeline"]["backtest"] == empirical["pipeline"]["backtest"]
    assert har["pipeline"]["signals"] == empirical["pipeline"]["signals"]
    assert har["pipeline"]["model"]["uses"] != empirical["pipeline"]["model"]["uses"]


@pytest.mark.parametrize("cell", BACKTEST_CELLS[:2], ids=lambda c: c.name)
def test_the_select_candidates_are_in_the_identity_hash(child_root, cell):
    from dskit.pipeline.document import PipelineDocument

    shipped = _grid(child_root, f"{cell.name}-select.json")
    baseline = PipelineDocument.from_obj(shipped).hash
    for knob, other in (("candidate_structures", ["condor"]), ("candidate_short_q", [0.1]),
                        ("candidate_wing_z", [0.5])):
        changed = copy.deepcopy(shipped)
        changed["pipeline"]["backtest"]["params"][knob] = other
        assert PipelineDocument.from_obj(changed).hash != baseline, knob


def test_select_document_refuses_a_document_it_cannot_derive_from_and_leaves_its_input_alone(
    child_root
):
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    iwm = next(c for c in grid.CELLS if c.underlying.symbol == "IWM")
    spy = next(c for c in grid.CELLS if c.name == "spy-30-45")
    with pytest.raises(ValueError, match="backtest"):
        grid.select_document(grid.grid_document(base, iwm))        # no dividend source
    with pytest.raises(ValueError, match="PutSpreadQuoteBacktest"):
        grid.select_document(grid.put_spread_document(base, spy))  # not the condor's backtest
    condor = grid.grid_document(base, spy)
    before = copy.deepcopy(condor)
    selected = grid.select_document(condor)
    assert condor == before and selected != before
    assert _differences(selected, _grid(child_root, "spy-30-45-select.json")) == SIZING_PATHS
    with pytest.raises(ValueError, match="PayoffSelectQuoteBacktest"):
        grid.select_document(selected)                            # never selected twice
    # the gate study's own preconditions still apply: a condor document already studied
    with pytest.raises(ValueError, match="already"):
        grid.select_document({**condor, "pipeline": {
            **condor["pipeline"], "signals": {}, "vix3m": {}}})
    empirical = grid.grid_document(json.loads(
        (child_root / "configs" / "run-real-distribution.json").read_text()), spy, "empirical")
    assert _differences(grid.select_document(empirical), _grid(
        child_root, "spy-30-45-empirical-select.json")) == SIZING_PATHS


@pytest.mark.parametrize("name", ["spy-30-45-select.json", "spy-30-45-empirical-select.json"])
def test_a_select_document_resolves_through_the_planner_to_the_selector(
    child_root, monkeypatch, name
):
    from index_options.nodes import PayoffSelectQuoteBacktest

    monkeypatch.chdir(child_root)
    path = child_root / GRID / name
    planned = plan(load_document(str(path)))
    assert planned.resolved["backtest"].cls is PayoffSelectQuoteBacktest
    assert planned.role_of("backtest") == "score"
    assert set(planned.order) == set(json.loads(path.read_text())["pipeline"])
    assert {("sizing", "backtest"), ("chain", "backtest"), ("underlying", "backtest")} <= set(
        planned.edges)
    assert ("model", "backtest") not in planned.edges and ("signals", "backtest") not in \
        planned.edges
    params = json.loads(path.read_text())["pipeline"]["backtest"]["params"]
    assert PayoffSelectQuoteBacktest.validate_params(params) == []


def test_the_cli_validates_and_plans_a_select_document_on_each_rung(child_root):
    import subprocess
    import sys

    for name in ("spy-30-45-select.json", "spy-30-45-empirical-select.json"):
        for verb in ("validate", "plan"):
            done = subprocess.run(
                [sys.executable, "-m", "dskit.pipeline", verb, f"configs/grid/{name}"],
                cwd=child_root, capture_output=True, text=True, timeout=120)
            assert done.returncode == 0, (name, verb, done.stderr[-500:])
        planned = json.loads(done.stdout)       # the last one is the plan
        assert planned["nodes"]["backtest"]["class"] == \
            "index_options.nodes:PayoffSelectQuoteBacktest"
        assert planned["nodes"]["backtest"]["inputs"]["forecasts"] == "$sizing.rows"
        assert planned["order"].index("sizing") < planned["order"].index("backtest")


# -- ADR-0196: the sizing study, composed on the gate study ------------------------------------


def test_the_sizing_study_is_exported_and_its_node_is_the_single_owner_of_the_weight_names():
    from index_options.nodes import VolSizingWeights

    assert "sizing_study_document" in grid.__all__
    assert list(VolSizingWeights.SIZE_FIELDS) == SIZE_NAMES        # grid reads it, never restates


def test_sizing_study_document_is_the_gate_study_plus_one_node_and_three_paths(child_root):
    gated = grid.gate_study_document(_spy_condor(child_root))
    before = copy.deepcopy(gated)
    sized = grid.sizing_study_document(gated)
    assert gated == before                                           # derived on a copy
    assert _differences(gated, sized) == SIZING_PATHS
    assert sized["name"] == gated["name"]                            # the file names the study
    assert list(sized["pipeline"]) == [
        *list(gated["pipeline"])[:list(gated["pipeline"]).index("signals") + 1], "sizing",
        *list(gated["pipeline"])[list(gated["pipeline"]).index("signals") + 1:]]
    assert sized["pipeline"]["sizing"]["params"] == SIZING_KNOBS
    assert sized["pipeline"]["sizing"]["inputs"] == {"rows": "$signals.rows"}
    assert _without_the_sizing_study(sized)["pipeline"] == {
        **gated["pipeline"],
        "backtest": {**gated["pipeline"]["backtest"],
                     "notes": sized["pipeline"]["backtest"]["notes"]}}
    assert sized["notes"].startswith(gated["notes"]) and "ADR-0196" in sized["notes"]
    assert sized["pipeline"]["backtest"]["notes"].startswith(gated["pipeline"]["backtest"]["notes"])
    assert "Never decision-eligible" in sized["notes"]
    # the notes' numbers are the knobs', and the node is told to stay annotate-only
    assert "0.25" in sized["notes"] and "4.0" in sized["notes"] and "252" in sized["notes"]


def test_sizing_study_document_refuses_a_document_it_cannot_size_and_leaves_its_input_alone(
    child_root
):
    plain = _spy_condor(child_root)
    with pytest.raises(ValueError, match="signals"):                 # no gate study to size
        grid.sizing_study_document(plain)
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    iwm = next(c for c in grid.CELLS if c.underlying.symbol == "IWM")
    with pytest.raises(ValueError, match="backtest"):
        grid.sizing_study_document(grid.grid_document(base, iwm))
    gated = grid.gate_study_document(plain)
    sized = grid.sizing_study_document(gated)
    with pytest.raises(ValueError, match="already carries the sizing study"):
        grid.sizing_study_document(sized)
    declared = copy.deepcopy(gated)
    declared["pipeline"]["backtest"]["params"]["size_fields"] = ["mine"]
    before = copy.deepcopy(declared)
    with pytest.raises(ValueError, match="size_fields"):
        grid.sizing_study_document(declared)
    elsewhere = copy.deepcopy(gated)
    elsewhere["pipeline"]["backtest"]["inputs"]["forecasts"] = "$model.rows"
    with pytest.raises(ValueError, match=r"forecasts.*\$signals\.rows"):
        grid.sizing_study_document(elsewhere)
    assert declared == before


def test_the_grid_composes_the_sizing_study_on_exactly_the_documents_that_carry_the_gate_study(
    child_root
):
    # the 56 of ADR-0196 (gate, put-spread, select, empirical-select on the 14 cells) and, by the
    # ADR-0196 clarification that every document carrying the gate study gets sizing, ADR-0197's 6
    # long-straddle documents: 62
    files = grid.grid_files(child_root / "configs")
    sized = {f for f, doc in files.items() if "sizing" in doc["pipeline"]}
    assert sized == {f"grid/{stem}-{kind}.json" for stem in BACKTEST_STEMS
                     for kind in ("gate", "put-spread", "select", "empirical-select")} | {
        f"grid/{u}-{b}-long-straddle.json" for u in ("spy", "qqq") for b in ("1", "2-3", "5")}
    assert len(sized) == 56 + 6
    assert {f for f, doc in files.items() if "signals" in doc["pipeline"]} == sized   # no others
    for relpath, doc in files.items():
        assert ("size_fields" in doc["pipeline"].get("backtest", {}).get("params", {})) == (
            relpath in sized), relpath


@pytest.mark.parametrize("name", GATE_STUDY_NAMES)
@pytest.mark.parametrize("cell", BACKTEST_CELLS[:3], ids=lambda c: c.name)
def test_every_sizing_knob_and_the_size_fields_are_in_the_identity_hash(child_root, cell, name):
    # as with the signals (ADR-0195 B-M2): written out, so editing the node's defaults can never
    # change what a shipped document computes while its hash and run directory stay put
    from dskit.pipeline.document import PipelineDocument

    shipped = _grid(child_root, name.format(cell.name))
    assert shipped["pipeline"]["sizing"]["params"] == SIZING_KNOBS
    baseline = PipelineDocument.from_obj(shipped).hash
    for knob, other in (("implied_field", "vix"), ("realized_field", "rv_5"),
                        ("lag_sessions", 2), ("min_history", 250), ("min_weight", 0.5),
                        ("max_weight", 3.0)):
        changed = copy.deepcopy(shipped)
        changed["pipeline"]["sizing"]["params"][knob] = other
        assert PipelineDocument.from_obj(changed).hash != baseline, knob
    bare = copy.deepcopy(shipped)
    del bare["pipeline"]["sizing"]["params"]
    assert PipelineDocument.from_obj(bare).hash != baseline
    narrower = copy.deepcopy(shipped)
    narrower["pipeline"]["backtest"]["params"]["size_fields"] = SIZE_NAMES[:2]
    assert PipelineDocument.from_obj(narrower).hash != baseline


def test_the_sizing_knobs_are_the_nodes_own_defaults_written_out_in_full(child_root):
    from index_options.nodes import VolSizingWeights

    doc = grid.sizing_study_document(grid.gate_study_document(_spy_condor(child_root)))
    assert doc["pipeline"]["sizing"]["params"] == dict(VolSizingWeights.DEFAULTS) == SIZING_KNOBS


# -- ADR-0196 (ADR-0195 review A-N4): one run instruction, for the file the reader holds --------

RUN_SENTENCE = "Run: python -m dskit.pipeline walkforward configs/grid/{file} --asof <today>."


@pytest.mark.parametrize("name", GATE_STUDY_NAMES)
@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_derived_documents_notes_carry_exactly_one_run_instruction_and_it_names_its_own_file(
    child_root, cell, name
):
    file = name.format(cell.name)
    notes = _grid(child_root, file)["notes"]
    assert notes.count("python -m dskit.pipeline walkforward") == 1
    assert notes.endswith(RUN_SENTENCE.format(file=file))
    assert re.findall(r"configs/grid/[\w.-]+\.json", notes) == [f"configs/grid/{file}"]
    assert "Run THIS document" not in notes and "<this file>" not in notes
    assert "Never decision-eligible" in notes


@pytest.mark.parametrize("cell", BACKTEST_CELLS, ids=lambda c: c.name)
def test_a_derived_document_keeps_its_sources_description_but_not_its_run_instruction(
    child_root, cell
):
    condor = _grid(child_root, f"{cell.name}.json")
    empirical = _grid(child_root, f"{cell.name}-empirical.json")
    # the condor cell document is itself generated and keeps its own single run sentence
    assert condor["notes"].count("walkforward") == 1
    assert RUN_SENTENCE.format(file=f"{cell.name}.json") in condor["notes"]
    for derived, source in ((f"{cell.name}-gate.json", condor), (f"{cell.name}-select.json", condor),
                            (f"{cell.name}-empirical-select.json", empirical)):
        notes = _grid(child_root, derived)["notes"]
        assert notes.startswith(_described(source["notes"])), derived
        assert RUN_SENTENCE.format(file=f"{cell.name}.json") not in notes, derived
        assert RUN_SENTENCE.format(file=f"{cell.name}-empirical.json") not in notes, derived
    put_spread = _grid(child_root, f"{cell.name}-put-spread.json")["notes"]
    assert put_spread.startswith(f"ADR-0193 cell {cell.name}:")


# -- ADR-0196 (ADR-0195 review A-N1): the empirical control is not "premium only" -------------


def test_the_empirical_control_is_described_as_an_unconditional_shape_trailing_vol_forecast(
    child_root
):
    from index_options.nodes import PayoffSelectQuoteBacktest

    readme = (child_root / "README.md").read_text(encoding="utf-8")
    where = {"the README": readme, "the class docstring": PayoffSelectQuoteBacktest.__doc__,
             "the grid module docstring": grid.__doc__,
             "AGENTS.md": (child_root / "AGENTS.md").read_text(encoding="utf-8")}
    for cell in BACKTEST_CELLS:
        for name in (f"{cell.name}-select.json", f"{cell.name}-empirical-select.json"):
            where[name] = _grid(child_root, name)["notes"]
    for label, text in where.items():
        assert not re.search(r"premium[- ]only|premium alone|only the quotes'? premium",
                             text, re.IGNORECASE), label
    for label in ("the README", "the class docstring", "spy-30-45-select.json",
                  "qqq-1-empirical-select.json"):
        text = re.sub(r"\s+", " ", where[label]).lower()
        assert "unconditional" in text and "trailing" in text and "conditional-forecast" in text, label


# -- ADR-0197: the debit documents ---------------------------------------------------------------

#: The pre-registered ADR-0197 choices, typed out here — never read from the grid or the nodes — so
#: the documents are pinned to the owner's answers, not to whatever the code's constants become.
STRADDLE_BUCKETS = ("1", "2-3", "5")
SPREAD_BUCKETS = ("30-45",)
LONG_CALL_Q, LONG_PUT_Q, LONG_WING = 0.5, 0.35, 0.65
DEBIT_FILES = {
    **{f"{u}-{b}-long-straddle.json": "straddle" for u in ("spy", "qqq") for b in STRADDLE_BUCKETS},
    **{f"{u}-30-45-long-call-spread.json": "call_spread" for u in ("spy", "qqq")},
    **{f"{u}-30-45-long-put-spread.json": "put_spread" for u in ("spy", "qqq")},
}
#: kind -> (the dotted class path, the knobs the document carries beyond the condor's bucket knobs)
DEBIT_NODES = {
    "straddle": ("index_options.nodes:LongStraddleQuoteBacktest", {}),
    "call_spread": ("index_options.nodes:LongCallSpreadQuoteBacktest",
                    {"long_q": LONG_CALL_Q, "wing_z": LONG_WING}),
    "put_spread": ("index_options.nodes:LongPutSpreadQuoteBacktest",
                   {"long_q": LONG_PUT_Q, "wing_z": LONG_WING}),
}
#: Every path at which a debit document differs from its condor cell document once the gate and
#: sizing studies are taken back out (restated here, never read from the generator).
DEBIT_PATHS = {"/name", "/notes", "/pipeline/backtest/uses", "/pipeline/backtest/notes",
               "/pipeline/backtest/params/short_q", "/pipeline/backtest/params/wing_z"}


def _source_stem(file):
    """``spy-30-45-long-call-spread.json`` -> ``spy-30-45``."""
    return file.split("-long-")[0]


def test_the_debit_choices_are_the_owners_pre_registered_answers_and_exported():
    assert grid.LONG_STRADDLE_BUCKETS == STRADDLE_BUCKETS and grid.LONG_SPREAD_BUCKETS == SPREAD_BUCKETS
    assert grid.LONG_CALL_SPREAD_LONG_Q == LONG_CALL_Q and grid.LONG_PUT_SPREAD_LONG_Q == LONG_PUT_Q
    assert grid.LONG_SPREAD_WING_Z == LONG_WING
    assert {"LONG_STRADDLE_BUCKETS", "LONG_SPREAD_BUCKETS", "LONG_CALL_SPREAD_LONG_Q",
            "LONG_PUT_SPREAD_LONG_Q", "LONG_SPREAD_WING_Z", "long_straddle_document",
            "long_call_spread_document", "long_put_spread_document"} <= set(grid.__all__)
    assert 0 < grid.LONG_CALL_SPREAD_LONG_Q < 1 and 0 < grid.LONG_PUT_SPREAD_LONG_Q < 1
    assert grid.LONG_SPREAD_WING_Z > 0
    # every listed bucket is a real one, the straddle's the three shortest
    labels = [b.label for b in grid.BUCKETS]
    assert set(grid.LONG_STRADDLE_BUCKETS) | set(grid.LONG_SPREAD_BUCKETS) <= set(labels)
    assert list(grid.LONG_STRADDLE_BUCKETS) == labels[:3]


def test_the_grid_choices_agree_with_the_nodes_defaults_today_and_a_change_to_either_is_seen():
    # the grid writes each knob out (the identity hash covers it), so the node default is not what
    # the documents run; but two numbers that mean the same thing are pinned to each other
    from index_options.nodes import LongCallSpreadQuoteBacktest, LongPutSpreadQuoteBacktest

    assert LongCallSpreadQuoteBacktest.DEFAULT_LONG_Q == grid.LONG_CALL_SPREAD_LONG_Q
    assert LongPutSpreadQuoteBacktest.DEFAULT_LONG_Q == grid.LONG_PUT_SPREAD_LONG_Q


def test_the_grid_adds_exactly_ten_debit_documents_on_the_named_cells(child_root):
    files = grid.grid_files(child_root / "configs")
    debits = {f for f in files if "-long-" in f}
    assert debits == {f"grid/{name}" for name in DEBIT_FILES} and len(debits) == 10
    assert not any("iwm" in f for f in debits)
    for cell in grid.CELLS:
        shipped = {f for f in grid.cell_files(child_root / "configs", cell) if "-long-" in f}
        want = set()
        if cell.underlying.backtest and cell.bucket.label in STRADDLE_BUCKETS:
            want.add(f"grid/{cell.name}-long-straddle.json")
        if cell.underlying.backtest and cell.bucket.label in SPREAD_BUCKETS:
            want |= {f"grid/{cell.name}-long-call-spread.json",
                     f"grid/{cell.name}-long-put-spread.json"}
        assert shipped == want, cell.name


def test_every_shipped_debit_file_equals_its_generator(child_root, tmp_path):
    # the zoo files already differ from their generator on the base, so the broad pin cannot be the
    # gate for these: this one is, file by file and through write_grid
    files = {f: d for f, d in grid.grid_files(child_root / "configs").items() if "-long-" in f}
    assert len(files) == 10
    for relpath, document in files.items():
        assert (child_root / "configs" / relpath).read_text() == \
            json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    for relpath in files:
        assert relpath in written
        assert (tmp_path / relpath).read_bytes() == (child_root / "configs" / relpath).read_bytes()


@pytest.mark.parametrize("file, kind", list(DEBIT_FILES.items()), ids=list(DEBIT_FILES))
def test_a_debit_document_is_its_condor_cell_document_with_only_the_backtest_node_swapped(
    child_root, file, kind
):
    condor = _grid(child_root, f"{_source_stem(file)}.json")
    shipped = _grid(child_root, file)
    plain = _without_the_gate_study(shipped) if kind == "straddle" else shipped
    uses, knobs = DEBIT_NODES[kind]
    # short_q and wing_z are the condor's and go (wing_z is 0.5 there, 0.65 in a spread); a spread
    # gains long_q; a straddle has no placement knob at all
    paths = DEBIT_PATHS | ({"/pipeline/backtest/params/long_q"} if knobs else set())
    assert _differences(plain, condor) == paths
    node = shipped["pipeline"]["backtest"]
    assert node["uses"] == uses
    # the condor's bucket knobs are the document's, and only short_q / wing_z were replaced: every
    # other knob (and its place in the object) is the cell's
    source_params = condor["pipeline"]["backtest"]["params"]
    kept = {k: v for k, v in source_params.items() if k not in ("short_q", "wing_z")}
    got = {k: v for k, v in node["params"].items()
           if k not in ("long_q", "wing_z", "gate_fields", "size_fields")}
    assert got == kept and list(got) == list(kept)
    assert {k: node["params"][k] for k in knobs} == knobs
    assert "short_q" not in node["params"]
    assert ("wing_z" in node["params"]) == bool(knobs) and ("long_q" in node["params"]) == bool(knobs)
    assert shipped["walkforward"] == condor["walkforward"]
    assert shipped["name"] == f"{condor['name']}-long-{file.split('-long-')[1][:-5]}"
    assert _described(shipped["notes"]).startswith(_described(condor["notes"]))
    assert "ADR-0197" in shipped["notes"] and "Never decision-eligible" in shipped["notes"]
    assert shipped["pipeline"]["backtest"]["notes"] != condor["pipeline"]["backtest"]["notes"]
    assert "DEBIT" in node["notes"] and "ADR-0197" in node["notes"]
    # the nodes the structure does not touch stay the condor's, byte for byte
    for key, value in condor["pipeline"].items():
        if key != "backtest":
            assert shipped["pipeline"][key] == value, key


@pytest.mark.parametrize("file, kind", list(DEBIT_FILES.items()), ids=list(DEBIT_FILES))
def test_a_debit_document_carries_one_run_instruction_and_it_names_its_own_file(
    child_root, file, kind
):
    notes = _grid(child_root, file)["notes"]
    assert notes.count("python -m dskit.pipeline walkforward") == 1
    assert notes.endswith(RUN_SENTENCE.format(file=file))
    assert re.findall(r"configs/grid/[\w.-]+\.json", notes) == [f"configs/grid/{file}"]
    assert "Run THIS document" not in notes and "<this file>" not in notes


def test_the_straddle_documents_carry_the_gate_and_sizing_studies_and_the_spreads_neither(
    child_root
):
    for file, kind in DEBIT_FILES.items():
        pipe = _grid(child_root, file)["pipeline"]
        backtest = pipe["backtest"]
        if kind == "straddle":
            assert {"vix3m", "signals", "sizing"} <= set(pipe), file
            assert backtest["inputs"]["forecasts"] == "$sizing.rows"
            assert backtest["params"]["gate_fields"] == GATE_NAMES
            assert backtest["params"]["size_fields"] == SIZE_NAMES
            assert list(backtest["params"])[-2:] == ["gate_fields", "size_fields"]
            assert pipe["signals"]["params"] == SIGNALS_KNOBS
            assert pipe["sizing"]["params"] == SIZING_KNOBS
        else:
            assert not {"vix3m", "signals", "sizing"} & set(pipe), file
            assert backtest["inputs"]["forecasts"] == "$model.rows"
            assert "gate_fields" not in backtest["params"] and "size_fields" not in backtest["params"]


@pytest.mark.parametrize("file, kind", list(DEBIT_FILES.items()), ids=list(DEBIT_FILES))
def test_a_debit_document_resolves_through_the_planner_to_its_backtest(
    child_root, monkeypatch, file, kind
):
    import index_options.nodes as nodes

    monkeypatch.chdir(child_root)
    path = child_root / GRID / file
    planned = plan(load_document(str(path)))
    cls = getattr(nodes, DEBIT_NODES[kind][0].split(":")[1])
    assert planned.resolved["backtest"].cls is cls
    assert planned.role_of("backtest") == "score"
    assert set(planned.order) == set(json.loads(path.read_text())["pipeline"])
    params = json.loads(path.read_text())["pipeline"]["backtest"]["params"]
    assert cls.validate_params(params) == []
    feeds = "sizing" if kind == "straddle" else "model"
    assert (feeds, "backtest") in planned.edges and ("chain", "backtest") in planned.edges


@pytest.mark.parametrize("file", ["spy-30-45-long-call-spread.json", "spy-30-45-long-put-spread.json",
                                  "qqq-2-3-long-straddle.json"])
def test_every_debit_knob_is_in_the_identity_hash(child_root, file):
    # the knobs are written out, so editing one moves the hash (and the run directory) and a node
    # default changing can never change what a shipped document computes behind its hash
    doc = load_document(str(child_root / GRID / file))
    base = doc.hash
    raw = _grid(child_root, file)
    for knob, value in (("multiplier", 10), ("long_q", 0.4), ("wing_z", 0.7)):
        params = raw["pipeline"]["backtest"]["params"]
        if knob not in params:
            continue
        changed = copy.deepcopy(raw)
        changed["pipeline"]["backtest"]["params"][knob] = value
        assert PipelineDocument.from_obj(changed).hash != base, (file, knob)
    notes_only = copy.deepcopy(raw)
    notes_only["notes"] += " more"
    notes_only["pipeline"]["backtest"]["notes"] += " more"
    assert PipelineDocument.from_obj(notes_only).hash == base       # notes are not identity


def test_the_debit_generators_refuse_a_document_they_cannot_derive_from_and_leave_it_alone(
    child_root
):
    base = json.loads((child_root / "configs" / "run-real-har-vix.json").read_text())
    spy = next(c for c in grid.CELLS if c.name == "spy-30-45")
    cell = grid.grid_document(base, spy)
    before = copy.deepcopy(cell)
    for generate, suffix in ((grid.long_straddle_document, "-long-straddle"),
                             (grid.long_call_spread_document, "-long-call-spread"),
                             (grid.long_put_spread_document, "-long-put-spread")):
        derived = generate(cell)
        assert cell == before                                        # derived on a copy
        assert derived["name"] == f"{cell['name']}{suffix}"
        assert derived["pipeline"]["backtest"]["uses"] != cell["pipeline"]["backtest"]["uses"]
        assert "walkforward configs/grid" not in derived["notes"]    # the shipper adds the one
        with pytest.raises(ValueError, match="condor archived-quote backtest"):
            generate(derived)                                        # already a debit structure
        no_backtest = {**cell, "pipeline": {k: v for k, v in cell["pipeline"].items()
                                            if k != "backtest"}}
        with pytest.raises(ValueError, match="backtest"):
            generate(no_backtest)
    iwm = next(c for c in grid.CELLS if c.name == "iwm-30-45")
    with pytest.raises(ValueError, match="backtest"):
        grid.long_straddle_document(grid.grid_document(base, iwm))


#: The two clauses of the gate study's notes that say what a TRUE gate means for the structure traded,
#: typed out here for the long straddle (ADR-0197 A-M4) and for the credit structures.
STRADDLE_READS = {
    "document": ("the gate was closed (gate true: for this long-volatility structure that is where "
                 "the hypothesis TRADES, the reverse of the condor's reading), open (gate false: "
                 "where it would have stood aside), or unknown"),
    "closed": "_closed_t (gate true: where the long-volatility hypothesis TRADES)",
    "open": "the same three _open_ (gate false: where it would have stood aside)",
}
CONDOR_READS = {
    "document": "the gate was closed (it would have stood aside), open, or unknown",
    "closed": "_closed_t (gate true: it would have stood aside)",
    "open": "the same three _open_ (false)",
}


def test_a_straddle_documents_gate_notes_say_its_hypothesis_trades_where_the_gate_is_true(
    child_root
):
    # ADR-0197 A-M4: the condor's notes say a closed gate (TRUE) "would have stood aside". For a long
    # straddle the hypothesis (Johnson 2017) TAKES the trade where the curve is inverted, the ratio is
    # in its top tail or the premium is non-positive: a reader following the condor's wording would
    # draw the opposite inference from the same split
    straddles = [f for f, kind in DEBIT_FILES.items() if kind == "straddle"]
    assert len(straddles) == 6
    for file in straddles:
        doc = _grid(child_root, file)
        notes, node_notes = doc["notes"], doc["pipeline"]["backtest"]["notes"]
        assert STRADDLE_READS["document"] in notes, file
        assert STRADDLE_READS["closed"] in node_notes and STRADDLE_READS["open"] in node_notes, file
        for text in (notes, node_notes):                     # never the condor's clause about it
            assert "closed (it would have stood aside)" not in text, file
            assert "(gate true: it would have stood aside)" not in text, file
            assert "what standing aside would have removed" not in text, file
        assert "what trading only where the gate is true would have kept" in notes, file


@pytest.mark.parametrize("name", ["{}-gate.json", "{}-put-spread.json", "{}-select.json",
                                  "{}-empirical-select.json"])
def test_the_credit_structures_keep_the_condors_reading_of_a_true_gate_word_for_word(child_root,
                                                                                     name):
    # the six straddle documents are the only ones whose wording changes: the files of the
    # structures that stand aside where the gate is true are what they were
    for cell in BACKTEST_CELLS:
        doc = _grid(child_root, name.format(cell.name))
        notes, node_notes = doc["notes"], doc["pipeline"]["backtest"]["notes"]
        assert CONDOR_READS["document"] in notes, cell.name
        assert CONDOR_READS["closed"] in node_notes and CONDOR_READS["open"] in node_notes
        assert "TRADES" not in notes and "TRADES" not in node_notes, cell.name


def test_the_gate_study_reading_is_a_parameter_the_straddle_generator_passes(child_root):
    # the default reading is the condor's; the straddle's differs from it in those two notes and
    # nowhere else (not a pipeline node, not a param, not the name)
    source = _spy_condor(child_root)
    stood_aside = grid.gate_study_document(source)
    trades = grid.gate_study_document(source, reading=grid.TRADES_WHERE_TRUE)
    assert stood_aside == grid.gate_study_document(source, reading=grid.STAND_ASIDE)
    assert trades != stood_aside
    assert trades["name"] == stood_aside["name"] and trades["pipeline"].keys() == \
        stood_aside["pipeline"].keys()
    for key, node in trades["pipeline"].items():
        other = stood_aside["pipeline"][key]
        assert {k: v for k, v in node.items() if k != "notes"} == \
            {k: v for k, v in other.items() if k != "notes"}, key
    assert CONDOR_READS["document"] in stood_aside["notes"]
    assert STRADDLE_READS["document"] in trades["notes"]
    assert STRADDLE_READS["closed"] in trades["pipeline"]["backtest"]["notes"]
    assert {"STAND_ASIDE", "TRADES_WHERE_TRUE", "GateReading"} <= set(grid.__all__)


#: The sizing-study clauses about what a weight does, typed out here (never read from the generator):
#: the credit structures SELL volatility, the long straddle BUYS it.
SELLER_SIZING = {
    "inv_implied": "median VIX^2 over today's, sell less after high implied variance",
    "implied": "VIX over its median, the opposite bet: sell more after high VIX",
    "literature": ("The literature conflicts (cutting exposure after high volatility helps equity "
                   "factors, and mostly not in real time; higher VIX has paid put writers), hence "
                   "two directions."),
}
BUYER_SIZING = {
    "inv_implied": ("median VIX^2 over today's, buy more contracts after low implied variance, "
                    "when volatility is cheap, and fewer after high"),
    "implied": "VIX over its median, the opposite bet: buy more after high VIX",
    "literature": ("The literature on volatility-managed sizing concerns equity factors and sellers "
                   "of volatility, not buyers, so neither direction is presumed: hence two "
                   "directions, read for a buyer."),
}


def test_a_straddle_documents_sizing_notes_say_what_a_weight_means_for_a_buyer(child_root):
    # ADR-0197: the sizing sentence was written for a seller ("sell less after high implied variance",
    # "higher VIX has paid put writers"). A long straddle BUYS: the same weights shift how many
    # contracts it buys, and the notes say so in the buyer's terms
    straddles = [f for f, kind in DEBIT_FILES.items() if kind == "straddle"]
    assert len(straddles) == 6
    for file in straddles:
        notes = _grid(child_root, file)["notes"]
        for clause in BUYER_SIZING.values():
            assert clause in notes, (file, clause)
        for seller in SELLER_SIZING.values():
            assert seller not in notes, (file, seller)
        for phrase in ("sell less", "sell more", "put writers"):
            assert phrase not in notes, (file, phrase)


@pytest.mark.parametrize("name", ["{}-gate.json", "{}-put-spread.json", "{}-select.json",
                                  "{}-empirical-select.json"])
def test_the_credit_structures_keep_the_sellers_sizing_wording_word_for_word(child_root, name):
    for cell in BACKTEST_CELLS:
        notes = _grid(child_root, name.format(cell.name))["notes"]
        for clause in SELLER_SIZING.values():
            assert clause in notes, (cell.name, clause)
        assert "buy more" not in notes, cell.name


def test_the_sizing_study_reading_is_a_parameter_the_straddle_generator_passes(child_root):
    source = grid.gate_study_document(_spy_condor(child_root))
    sold = grid.sizing_study_document(source)
    bought = grid.sizing_study_document(source, reading=grid.BUYS_VOLATILITY)
    assert sold == grid.sizing_study_document(source, reading=grid.SELLS_VOLATILITY)
    assert bought != sold and bought["name"] == sold["name"]
    assert bought["pipeline"].keys() == sold["pipeline"].keys()
    for key, node in bought["pipeline"].items():
        other = sold["pipeline"][key]
        assert {k: v for k, v in node.items() if k != "notes"} == \
            {k: v for k, v in other.items() if k != "notes"}, key
    for clause in SELLER_SIZING.values():
        assert clause in sold["notes"] and clause not in bought["notes"]
    for clause in BUYER_SIZING.values():
        assert clause in bought["notes"] and clause not in sold["notes"]
    assert {"SizingReading", "SELLS_VOLATILITY", "BUYS_VOLATILITY"} <= set(grid.__all__)


@pytest.mark.parametrize("file, kind", list(DEBIT_FILES.items()), ids=list(DEBIT_FILES))
def test_a_debit_documents_width_rule_counts_the_fees(child_root, file, kind):
    # ADR-0197 A-M3: debit_not_below_width is the debit in USD, fees included, against multiplier x the
    # width; "the debit per share reaches the width" (the pre-fee rule) is gone from every document
    notes = _grid(child_root, file)["pipeline"]["backtest"]["notes"]
    assert ("as debit_not_below_width when the debit, fees included, reaches multiplier x the width "
            "(it cannot finish positive)") in notes, file
    assert "debit per share reaches the width" not in notes, file


def test_the_cli_validates_and_plans_one_document_of_each_debit_kind(child_root):
    import subprocess
    import sys

    for name, cls in (("spy-2-3-long-straddle.json", "index_options.nodes:LongStraddleQuoteBacktest"),
                      ("qqq-30-45-long-call-spread.json",
                       "index_options.nodes:LongCallSpreadQuoteBacktest"),
                      ("spy-30-45-long-put-spread.json",
                       "index_options.nodes:LongPutSpreadQuoteBacktest")):
        for verb in ("validate", "plan"):
            done = subprocess.run(
                [sys.executable, "-m", "dskit.pipeline", verb, f"configs/grid/{name}"],
                cwd=child_root, capture_output=True, text=True, timeout=120)
            assert done.returncode == 0, (name, verb, done.stderr[-500:])
        planned = json.loads(done.stdout)       # the last one is the plan
        assert planned["nodes"]["backtest"]["class"] == cls


def test_feature_availability_json_preserves_cohort_and_reports_missing_groups(child_root):
    from dskit.pipeline.kinds_flow import Derive, Filter, GroupBy
    document = json.loads((child_root/"configs/run-qqq-feature-availability.json").read_text())
    graph = document["pipeline"]
    families = graph["family_contracts"]["params"]["tables"]["families"]
    required = {field for family in families.values() for field in family["fields"]}
    row = {field: .1 for field in required}
    row.update(symbol="QQQ", quote_date="2020-01-02", expiry="2020-01-09",
               actual_calendar_dte=7, terminal_return=.1, rn_proxy_eligible=1, asof_ms=1577923200000)
    for name in required:
        if name.startswith("market_") or name in families["macro_context"]["fields"]:
            row[name+"_missing"] = 0
            row[name+"_age_days"] = 1
    for i in range(9):
        row[f"chain_node_{i:02d}_mask"] = 1
    rows = [dict(row), dict(row, quote_date="2020-01-03", expiry="2020-01-10", asof_ms=1578009600000)]
    rows[1].pop("ret_lag_0")
    rows[1]["market_gvz_missing"] = 1
    rows[1]["chain_node_00_mask"] = 0
    excluded = dict(row, actual_calendar_dte=8)
    selected = Filter("cohort", graph["cohort"]["params"]).run(None, {"records": rows+[excluded]})["records"]
    assert len(selected) == 2
    for key, spec in graph.items():
        if key.startswith("check_"):
            selected = Derive(key, spec["params"]).run(None, {"records": selected})["records"]
    assert selected[0]["available_all_families"] == 1
    assert selected[1]["available_return_history"] == 0
    assert selected[1]["available_volatility_context"] == 0
    assert selected[1]["available_chain_nodes"] == 0
    assert selected[1]["available_implied_cdf"] == 1
    summary = GroupBy("summary", graph["summary_return_history"]["params"]).run(
        None, {"records": selected})["records"]
    assert sum(r["observations"] for r in summary) == 2
    assert {r["available_return_history"]: r["observations"] for r in summary} == {0: 1, 1: 1}
    assert graph["row_evidence"]["params"]["expect"] == 1497
    assert set(document["foreach"]["keys"]) == required
    assert load_document(child_root/"configs/run-qqq-feature-availability.json").name


def test_feature_availability_json_rejects_nonfinite_and_does_not_call_it_known_at(child_root):
    from dskit.pipeline.kinds_flow import Derive
    doc = json.loads((child_root/"configs/run-qqq-feature-availability.json").read_text())
    params = doc["foreach"]["pipeline"]["finite_flag"]["params"]
    params = json.loads(json.dumps(params).replace("$each", "test_feature"))
    rows = [{}, {"test_feature": None}, {"test_feature": float("nan")},
            {"test_feature": float("inf")}, {"test_feature": "3"}, {"test_feature": 0.}]
    out = Derive("audit", params).run(None, {"records": rows})["records"]
    assert [r["audit_finite"] for r in out] == [0, 0, 0, 0, 0, 1]
    assert "historical known-at certification" in doc["notes"]
    clock = doc["pipeline"]["family_contracts"]["params"]["tables"]["families"]["macro_context"]["clock"]
    assert "not certified" in clock


def test_feature_gap_output_names_reasons_and_exact_identities(child_root):
    from dskit.pipeline.kinds_flow import Derive, Filter, GroupBy
    d = json.loads((child_root/"configs/run-qqq-feature-availability.json").read_text())
    graph = d["foreach"]["pipeline"]
    cases = [{}, {"x": None}, {"x": float("nan")}, {"x": "bad"}, {"x": 1.}]
    rows = [dict(r, symbol="QQQ", quote_date=f"2020-01-{i+2:02d}",
                 expiry=f"2020-01-{i+9:02d}") for i, r in enumerate(cases)]
    for name in ("present_flag", "finite_flag", "gap_reason"):
        params = json.loads(json.dumps(graph[name]["params"]).replace("$each", "x"))
        rows = Derive(name, params).run(None, {"records": rows})["records"]
    assert [r["audit_gap"] for r in rows] == [
        "field_absent", "null_value", "nonnumeric_or_nonfinite",
        "nonnumeric_or_nonfinite", "none"]
    gaps = Filter("gaps", graph["gap_rows"]["params"]).run(None, {"records": rows})["records"]
    compact = GroupBy("compact", graph["gap_identities"]["params"]).run(
        None, {"records": gaps})["records"]
    assert len(compact) == 4
    assert {r["quote_date"] for r in compact} == {r["quote_date"] for r in rows[:4]}
    assert all(set(r) == {"symbol", "quote_date", "expiry", "audit_gap", "observations"} for r in compact)
    assert d["pipeline"]["feature_gap_evidence"]["params"]["path"].endswith("feature-gaps.jsonl")
    assert d["pipeline"]["family_gap_evidence"]["params"]["path"].endswith("family-gaps.jsonl")


def test_committed_qqq_cohort_weekday_census(child_root):
    """ADR-0214: weekday over the committed cohort rows, against an independent tally.

    The cohort is Friday-heavy until 2021 and carries no weekend rows, which is
    the confound the weekday feature is meant to expose (not to remove).
    """
    from collections import Counter
    from datetime import date
    from dskit.pipeline.kinds_flow import WeekdayOneHot

    path = child_root/"pipeline_runs/qqq-feature-availability/rows.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    out = WeekdayOneHot("weekday", {"date_field": "quote_date", "prefix": "dow_"}).run(
        None, {"records": rows})
    assert len(out["records"]) == len(rows) == 1497

    # The oracle restates the vocabulary and uses isoweekday() (Monday = 1), so
    # it shares neither the tag tuple nor date.weekday() with the node.
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    tally = Counter(names[date.fromisoformat(r["quote_date"]).isoweekday() - 1] for r in rows)
    assert out["counts"] == {name: tally.get(name, 0) for name in names}
    assert sum(out["counts"].values()) == 1497
    assert out["counts"]["sat"] == out["counts"]["sun"] == 0

    early = [r for r in out["records"] if r["quote_date"] < "2021-01-01"]
    assert len(early) == 508
    assert sum(1 for r in early if r["dow_fri"] == 0) == 35
    for row in out["records"]:
        assert sum(row[f"dow_{name}"] for name in names) == 1

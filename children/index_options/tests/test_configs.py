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
        # ADR-0238: fixed-protocol advanced study and published interim evidence.
        "configs/advanced-cdf-zoo.json",
        "docs/reports/advanced-cdf-zoo-20261006/pooled-base.html",
        "docs/reports/advanced-cdf-zoo-20261006/unpooled-base.html",
        "docs/reports/advanced-cdf-zoo-20261006/pooled-base-options.html",
        "docs/reports/advanced-cdf-zoo-20261006/unpooled-base-options.html",
        "docs/reports/advanced-cdf-zoo-20261006/final-verification.json",
        "docs/memos/2026-10-07-advanced-architecture-results.md",
        "docs/research/advanced-cdf-zoo/2026-10-06-capacity-and-architecture-plan.md",
        "docs/research/advanced-cdf-zoo/2026-10-07-production-proposal.md",
        # Owner-authorized Stage-0 audit and recovery evidence; no new model fits.
        "docs/memos/2026-10-07-production-development-stage0.md",
        "docs/reports/production-development-20261007.json",
        "docs/reports/advanced-cdf-zoo-20261006/advanced-interim-performance.html",
        "docs/reports/advanced-cdf-zoo-20261006/advanced-interim-performance.manifest.json",
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
        # Files ADR-0198 onward added to origin/main without extending this manifest (the
        # CDF study configs, memos, research notes and tests), pinned here 2026-10-02.
        "configs/run-cdf-actual-wing-paired-audit-r2.json",
        "configs/run-cdf-actual-wing-paired-audit-r3.json",
        "configs/run-cdf-actual-wing-paired-audit.json",
        "configs/run-cdf-causal-robust-condor-calibration.json",
        "configs/run-cdf-decision-regions-w1-smoke.json",
        "configs/run-cdf-decision-regions.json",
        "configs/run-cdf-gpd-decision-regions-calibration.json",
        "configs/run-cdf-gpd-decision-regions-screen-r4.json",
        "configs/run-cdf-gpd-decision-regions-screen.json",
        "configs/run-decision-strike-qqq-diagnosis.json",
        "configs/run-predictive-cdf-center-transport.json",
        "configs/run-predictive-cdf-comparison.json",
        "configs/run-predictive-cdf-conditioned-transport.json",
        "configs/run-predictive-cdf-decision-loss-spy-persistence.json",
        "configs/run-predictive-cdf-decision-loss-zoo-r2.json",
        "configs/run-predictive-cdf-decision-loss-zoo-r3.json",
        "configs/run-predictive-cdf-decision-loss-zoo-r4.json",
        "configs/run-predictive-cdf-decision-loss-zoo-r5.json",
        "configs/run-predictive-cdf-decision-loss-zoo.json",
        "configs/run-predictive-cdf-downside.json",
        "configs/run-predictive-cdf-guard-aware.json", "configs/run-predictive-cdf-hpo.json",
        "configs/run-pooled-heads-top5.json", "configs/run-pooled-heads-top5-folds.json",
        "configs/run-predictive-cdf-methods.json",
        "configs/run-predictive-cdf-option-surface.json",
        "configs/run-predictive-cdf-qqq-torch-smoke.json",
        "configs/run-predictive-cdf-qqq-torch-zoo.json",
        "configs/run-predictive-cdf-refinement.json",
        "configs/run-predictive-cdf-risk-neutral.json",
        "configs/run-predictive-cdf-tail-blend.json",
        "configs/run-predictive-cdf-tail-calibration-center-gpd-bounded.json",
        "configs/run-predictive-cdf-tail-calibration-center-gpd-final.json",
        "configs/run-predictive-cdf-tail-calibration-center-gpd.json",
        "configs/run-predictive-cdf-tail-calibration.json",
        "configs/run-predictive-cdf-tail-data.json",
        "configs/run-predictive-cdf-transport-ablation.json",
        "configs/source-cdf-horizon-panel.json", "docs/explanations/robust-condor-selection.md",
        "docs/explanations/robust-condor-selection.svg",
        "docs/memos/2026-09-27-index-options-modelability-ranking.md",
        "docs/memos/2026-09-28-predictive-cdf-comparison.md",
        "docs/memos/2026-09-28-predictive-cdf-downside.md",
        "docs/memos/2026-09-28-predictive-cdf-hpo.md",
        "docs/memos/2026-09-28-predictive-cdf-methods.md",
        "docs/memos/2026-09-28-predictive-cdf-refinement.md",
        "docs/memos/2026-09-29-predictive-cdf-option-surface.md",
        "docs/memos/2026-09-29-risk-neutral-cdf-architecture-comparison.md",
        "docs/memos/2026-09-30-actual-listed-wing-paired-audit.md",
        "docs/memos/2026-10-03-stock-universe-feature-and-data-counts.md",
        "docs/memos/2026-09-30-causal-decision-region-calibration-and-robust-condor.md",
        "docs/memos/2026-09-30-center-only-conditioned-transport.md",
        "docs/memos/2026-09-30-decision-region-loss-zoo-design.md",
        "docs/memos/2026-09-30-decision-region-loss-zoo-results.md",
        "docs/memos/2026-09-30-decision-region-robust-condor-pilot.md",
        "docs/memos/2026-09-30-decision-region-signal-synthesis.md",
        "docs/memos/2026-09-30-dynamic-tail-calibration-and-gpd.md",
        "docs/memos/2026-09-30-gpd-decision-region-screen.md",
        "docs/memos/2026-09-30-guard-aware-cdf-selection.md",
        "docs/memos/2026-09-30-tail-constrained-quantile-blend.md",
        "docs/memos/2026-09-30-tail-data-feature-ablation.md",
        "docs/memos/2026-10-01-qqq-30-day-call-region.png",
        "docs/memos/2026-10-01-qqq-decision-strike-diagnosis.md",
        "docs/memos/2026-10-01-qqq-torch-decision-cdf-results.png",
        "docs/memos/2026-10-01-qqq-torch-decision-cdf-zoo.md",
        "docs/memos/2026-10-01-spy-decision-region-persistence.md",
        "docs/research/distribution-modeling/2026-09-27-feature-acquisition.md",
        "docs/research/distribution-modeling/2026-09-28-cdf-distribution-assumptions-hpo.md",
        "docs/research/distribution-modeling/2026-09-28-exact-expiry-feature-selection-model-zoo.md",
        "docs/research/distribution-modeling/2026-09-28-exact-expiry-tcn-output-and-day-skill.md",
        "docs/research/distribution-modeling/2026-09-28-final-elasticnet-output-and-cell-skill.md",
        "docs/research/distribution-modeling/2026-09-28-fixed-feature-model-zoo-selection.md",
        "docs/research/distribution-modeling/2026-09-28-predictive-cdf-condor-research.md",
        "docs/research/distribution-modeling/2026-09-28-temporal-mlp-gru-screen.md",
        "docs/research/distribution-modeling/basic-iron-condor-optimization.md",
        "docs/research/distribution-modeling/simple-formulation-with-robustification.md",
        "docs/research/stock-options/2026-10-01-source-and-starting-symbol.md",
        "docs/research/strategy-alternatives/2026-09-30-survey.md",
        "docs/research/strategy-alternatives/2026-09-30-synthesis.md",
        "index_options/cdf_study.py", "tests/test_cdf_study.py",
        # The steps 1-6 data-selection documents and source configs (ADR-0217..0224), their
        # IWM copies and the onboarding-store references (ADR-0225: datafiles.py, the four
        # source-store-* registrations, and their tests).
        "configs/run-step1-expiry-coverage.json", "configs/run-step1b-feature-engineering.json",
        "configs/run-step2-feature-availability.json",
        "configs/run-step3-holdout-folds-iwm.json", "configs/run-step3-holdout-folds.json",
        "configs/run-step4-feature-selection-iwm.json",
        "configs/run-step4-feature-selection.json", "configs/run-step5-model-zoo-iwm.json",
        "configs/run-step5-model-zoo.json", "configs/run-step6-hpo-iwm.json",
        "configs/run-step6-hpo.json", "configs/source-feature-panel.json",
        "configs/source-step1-selection.json", "configs/source-step2-dates.json",
        "configs/source-store-exact-expiry-tables.json",
        "configs/source-store-option-archive-pins.json",
        "configs/source-store-option-archive.json",
        "configs/source-stock-daily.json", "configs/stock_universe.json",
        # ADR-0235: the 300-stock option universe (activity pull, ranking run, ranking output,
        # and the option-history pull whose symbols are read from that output).
        "configs/source-option-activity.json", "configs/run-option-universe-300-rank.json",
        "configs/option_universe_300.json", "configs/source-option-universe-300.json",
        # The 300 stocks' daily bars source and its daily-features args overlay.
        "configs/source-stock-daily-300.json", "configs/features-300.json",
        "tests/test_source_pins.py",
        "configs/stocks-long.json", "configs/stocks-long-features.json",
        "configs/stocks-opt.json", "configs/stocks-opt-features.json",
        "index_options/stock_bars.py", "tests/test_stock_bars.py",
        "configs/source-store-raw-chain-features.json", "index_options/datafiles.py",
        "tests/test_config_data_sources.py", "tests/test_datafiles.py",
        # ADR-0226, ADR-0229: the 7-step workflow for arbitrary tickers/families:
        # step1 (target dates), step1b (feature engineering), step2 (availability),
        # step3 (holdout/folds), step4 (forward selection), step5 (zoo), step6 (HPO),
        # step7 (evaluate + report). Template configs, workflow.json, tests, fixtures.
        "configs/workflow.json",
        "configs/workflow-features.json", "configs/benchmark_universe.json",
        "configs/source-benchmark-daily.json", "tests/test_stock_features.py",
        "tests/test_panel_options.py", "tests/test_panel_refactor_pin.py",
        "tests/test_price_calendar_panel.py",
        "configs/templates/features-stock-daily.json",
        "configs/templates/features-stock-option-trades.json",
        "configs/templates/source-features-daily.json",
        "configs/templates/source-features-trades.json",
        "configs/templates/step1-target-dates.json",
        "configs/templates/step1b-feature-engineering.json",
        "configs/templates/step2-feature-availability.json",
        "configs/templates/step3-holdout-folds.json",
        "configs/templates/step4-feature-selection.json",
        "configs/templates/step5-model-zoo.json",
        "configs/templates/step6-hpo.json",
        "configs/templates/report-spec.json",
        "configs/templates/source-handoff-step1.json",
        "configs/templates/source-handoff-step1b.json",
        "configs/templates/source-handoff-step2.json",
        "tests/test_workflow_pins.py", "tests/test_study_literals.py", "tests/test_feature_templates.py",
        "tests/test_step1_template.py", "tests/test_study_templates.py",
        "tests/test_new_family.py", "tests/test_step7_report.py",
        "tests/fixtures/args-step1.json", "tests/fixtures/args-features.json",
        "tests/fixtures/args-report.json", "tests/fixtures/args-study.json",
        "docs/plans/archive-2026-10-02.md",
        # ADR-0236/0237: the pooled zoo over both stock universes, generated by pooled.py
        # and pinned below.
        "index_options/pooled.py", "configs/run-pooled-zoo-417.json",
        "configs/run-pooled-zoo-417-folds.json",
        # Owner request 2026-10-04: its workflow manifest and verified-report spec.
        "configs/workflow-pooled-zoo-417.json",
        "configs/templates/report-spec-pooled-zoo-417.json",
        # The pooled run's results memo (302bf694), which this inventory missed.
        "docs/memos/2026-10-05-pooled-zoo-417.md",
        # ADR-0236 amendment 3 (owner request 2026-10-05): the same zoo fitted per ticker.
        "configs/run-perticker-zoo-417.json", "configs/workflow-perticker-zoo-417.json",
        "configs/templates/report-spec-perticker-zoo-417.json",
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
    # test_debit_backtest.py = 275, plus the 78 files origin/main gained after ADR-0197 = 353,
    # plus the 21 steps 1-6 and onboarding-store files = 374, plus ADR-0226/0229's workflow
    # (workflow.json, 11 templates, 7 tests, 4 fixtures, 1 archive doc) = 24 new = 398,
    # plus the stock-lane work (ADR-0232 amendment): 4 overlays, 3 universe/source configs,
    # workflow-features.json, 4 templates, stock_bars.py and 5 tests = 19 new = 417, plus 1 more = 418,
    # plus ADR-0235's four option-universe-300 configs + the 300-stock bars source and daily-features overlay = 425
    # (427 as shipped), plus ADR-0236/0237's pooled.py and its two generated documents = 430,
    # plus the pooled workflow manifest and report spec = 432, plus its results memo = 433,
    # plus the per-ticker study, its workflow manifest and report spec = 436,
    # plus ADR-0238 config, research and two interim report artifacts = 440; four final reports, evidence and memo = 446; production proposal = 447; Stage-0 evidence = 449
    assert len(actual) == 449
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


def _approved_like(relpath, document, shipped):
    """The generator writes both approval values of a zoo as PENDING-PLAN-REVIEW and the owner
    replaces them by hand after reviewing the inventory (2026-09-27): compare a zoo with the
    shipped pair copied in, and require the shipped pair to be filled. Other files pass through.
    """
    if not relpath.endswith("-zoo.json"):
        return document
    given = shipped["stages"]["approval"]["params"]
    assert "PENDING-PLAN-REVIEW" not in (given["approved_by"], given["approved_inventory_sha256"])
    approval = document["stages"]["approval"]
    return {**document, "stages": {**document["stages"], "approval": {
        **approval, "params": {**approval["params"],
                               "approved_by": given["approved_by"],
                               "approved_inventory_sha256": given["approved_inventory_sha256"]}}}}


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
        shipped = (child_root / "configs" / relpath).read_text()
        document = _approved_like(relpath, document, json.loads(shipped))
        assert shipped == json.dumps(document, indent=2) + "\n", relpath
    written = grid.write_grid(child_root / "configs", tmp_path)
    assert sorted(written) == sorted(files)
    for relpath in files:
        shipped = (child_root / "configs" / relpath).read_bytes()
        if relpath.endswith("-zoo.json"):
            regenerated = _approved_like(relpath, json.loads((tmp_path / relpath).read_text()),
                                         json.loads(shipped))
            assert json.dumps(regenerated, indent=2) + "\n" == shipped.decode(), relpath
        else:
            assert (tmp_path / relpath).read_bytes() == shipped, relpath
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
    approval = zoo["stages"]["approval"]["params"]
    # the owner replaced both PENDING values after reviewing the frozen inventory (2026-09-27)
    assert approval["approved_by"] == "owner directive 2026-09-27"
    assert re.fullmatch(r"[0-9a-f]{64}", approval["approved_inventory_sha256"])
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


# -- Steps 1-2 of the three-step data selection: two ticker/source-neutral documents ----------
#
# Step 1 counts, per exact calendar DTE, the entry dates with a listed expiry and a settled
# close; step 2 reports which feature families exist on step 1's argmax-DTE dates, and the
# distinct-date count of every combination of families. Both are JSON over existing kinds.

import itertools  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402

STEP1 = "configs/run-step1-expiry-coverage.json"
STEP2 = "configs/run-step2-feature-availability.json"
SELECTION_SOURCE = "configs/source-step1-selection.json"
#: The 13 families in the order step 2 checks and combines them, restated here (never read from
#: the document) and pinned to its family_contracts by test_step2_family_vocabulary_agrees_everywhere.
FAMILIES = [
    "implied_cdf", "return_history", "realized_volatility", "ohlc_shape", "volatility_context",
    "expanded_volatility_context", "surface_level", "surface_changes", "variance_gap",
    "liquidity", "positioning_changes", "macro_context", "chain_nodes"]
FLOAT_MAX = sys.float_info.max
SOURCE_ROOT = "./pipeline_runs/cdf-horizon-source"
#: Where step 2 reads the panel: step 1b's output onboarded per run (steps 1, 1b and 3 read the
#: onboarding store instead, and these tests onboard their fixture panel under SOURCE_ROOT).
STEP2_PANEL_ROOT = "./pipeline_runs/feature-panel-source"


def _step(child_root, name):
    return json.loads((child_root / name).read_text())


def _cli(argv, cwd, env=None):
    """One CLI call from ``cwd`` with the journal root unset; the completed process."""
    environ = {k: v for k, v in os.environ.items() if k != "DSKIT_JOURNAL_ROOT"}
    environ.update(env or {})
    return subprocess.run([sys.executable, "-m", *argv], cwd=cwd, env=environ,
                          capture_output=True, text=True, timeout=300)


def _must(done):
    assert done.returncode == 0, (done.args, done.stdout[-800:], done.stderr[-1500:])
    return done


def _kind_classes():
    from dskit.pipeline.kinds_flow import Concat, Derive, Filter, GroupBy, Join, KeyBy
    return {"filter": Filter, "groupby": GroupBy, "keyby": KeyBy, "derive": Derive,
            "join": Join, "concat": Concat}


def _materialize(value, outputs, each):
    """A node's params/inputs with ``$each`` and ``$node.port.path`` references resolved.

    The mini-driver the unit tests run documents with: it honors the document's own wiring, so a
    mis-wired port fails here exactly as it would in the runner.
    """
    if isinstance(value, str):
        if value == "$each":
            return each
        if value.startswith("$"):
            head, *path = value[1:].split(".")
            found = outputs[head]
            for part in path:
                found = found[part]
            return found
        return value
    if isinstance(value, list):
        return [_materialize(v, outputs, each) for v in value]
    if isinstance(value, dict):
        return {k: _materialize(v, outputs, each) for k, v in value.items()}
    return value


def _run_nodes(nodes, outputs, each=None, only=None):
    """Run the flow-kind nodes of ``nodes`` (a document's ordered node map) over ``outputs``."""
    classes = _kind_classes()
    for key, spec in nodes.items():
        if spec["uses"] not in classes or (only is not None and key not in only):
            continue
        inputs = _materialize(spec.get("inputs", {}), outputs, each)
        params = _materialize(spec.get("params", {}), outputs, each)
        outputs[key] = classes[spec["uses"]](key, params).run(None, inputs)
    return outputs


# -- the synthetic fixture panels (never committed; built in pytest's tmp_path) ----------------

#: How each family fails on a date that lacks it, restated here. A family not named breaks by
#: nulling its first field. These exercise the three ways a family goes missing: a null, a
#: source mask, and an observation older than the age limit.
BREAKS = {"return_history": {"ret_lag_0": None},
          "volatility_context": {"market_gvz_missing": 1},
          "macro_context": {"rate_dff_age_days": 15},
          "surface_level": {"chain_log_atm_iv": None}}
#: Families the index panel carries as all-null columns on every date (a uniform column set).
ENTIRELY_NULL = {"ohlc_shape", "expanded_volatility_context", "variance_gap",
                 "positioning_changes", "surface_changes", "chain_nodes"}
QUANTILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _satisfying(quality):
    """Field values that pass every quality clause: an == sets, a >=/<= pair takes the lower."""
    out = {}
    for clause in quality:
        if clause["op"] in ("==", ">="):
            out.setdefault(clause["field"], clause["value"])
    return out


def _day(offset):
    return (date(2020, 1, 2) + timedelta(days=offset)).isoformat()


def _index_row(families, symbol, quote_date, expiry, dte, settled, avail, cdf="ok"):
    """One index-shaped prepared-panel row with a uniform column set (nulls where missing)."""
    row = {"symbol": symbol, "quote_date": quote_date, "expiry": expiry,
           "actual_calendar_dte": dte, "terminal_return": 0.01 if settled else None}
    for name, spec in families.items():
        row.update(_satisfying(spec["quality_checks"]))
        for field in spec["fields"]:
            row[field] = None if name in ENTIRELY_NULL else 0.5
        if name not in avail and name not in ENTIRELY_NULL and name != "implied_cdf":
            broken = BREAKS.get(name, {spec["fields"][0]: None})
            row.update(broken)
    cdf_fields = families["implied_cdf"]["fields"]
    row["rn_proxy_eligible"] = 1 if cdf == "ok" else 0
    for field, q in zip(cdf_fields, QUANTILES):
        row[field] = None if cdf == "null" else q
    return row


def _stock_row(cdf_fields, symbol, quote_date, dte, terminal, eligible, basis="trade_close",
               reasons=()):
    """One OptionCDFPanel.records-shaped row: CDF fields only, lists, asof_ms, price_basis."""
    stamp = datetime.fromisoformat(quote_date).replace(tzinfo=timezone.utc)
    row = {"symbol": symbol, "quote_date": quote_date,
           "expiry": (stamp + timedelta(days=(dte or 40))).date().isoformat(),
           "price_basis": basis, "terminal_return": terminal, "spot": 100.0,
           "actual_calendar_dte": dte, "label_kind": "underlying_expiry_close",
           "label_reasons": list(reasons), "rn_proxy_eligible": int(eligible),
           "asof_ms": int(stamp.timestamp() * 1000), "option_rows": 9,
           "cdf_reasons": [] if eligible else ["insufficient_wing_support"],
           "cdf_kind": "american_option_price_proxy", "spot_basis": "completed_close"}
    for field, q in zip(cdf_fields, QUANTILES):
        row[field] = (q / 10) if eligible else None
    return row


def _index_plan():
    """``{symbol: [(quote_date, expiry, dte, settled, avail, cdf_mode, copies)]}`` and the
    designed answer: winner DTE per symbol, and each winner-cohort date's available families."""
    plan, designed = {}, {}
    # QQQ: DTE 7 has 5 settled dates (3 CDF-eligible) + a pending one; DTE 14 has 4 eligible
    # dates (the old CDF argmax would pick 14); DTE 0 and 46 are out of range.
    q7 = {0: {"implied_cdf", "return_history", "realized_volatility", "volatility_context",
              "surface_level", "liquidity", "macro_context"},
          1: {"implied_cdf", "return_history", "realized_volatility", "macro_context",
              "liquidity"},
          2: {"implied_cdf", "realized_volatility", "volatility_context", "liquidity",
              "macro_context"},
          3: {"return_history", "realized_volatility", "volatility_context", "surface_level",
              "liquidity", "macro_context"},
          4: {"return_history", "realized_volatility", "volatility_context", "liquidity"}}
    modes = {3: "null", 4: "ineligible"}
    rows = []
    for i, avail in q7.items():
        copies = 2 if i == 1 else 1      # one date lists two expiries at the same DTE
        rows.append((_day(i), _day(i + 7), 7, True, avail, modes.get(i, "ok"), copies))
    rows.append((_day(5), _day(12), 7, False, {"implied_cdf"}, "ok", 1))       # pending
    rows += [(_day(30 + i), _day(37 + i), 14, True, {"implied_cdf", "realized_volatility"},
              "ok", 1) for i in range(4)]
    for dte in (0, 46):
        rows += [(_day(60 + i), _day(60 + i + dte), dte, True, {"implied_cdf"}, "ok", 1)
                 for i in range(8)]
    plan["QQQ"] = rows
    designed["QQQ"] = (7, {_day(i): a for i, a in q7.items()})
    # SPY: DTE 7 and DTE 14 tie at 4 dates; the shorter wins. Families vary by date.
    def varied(j):
        return {f for i, f in enumerate(FAMILIES)
                if (i + j) % 3 != 0 and f not in ENTIRELY_NULL}
    rows, cohort = [], {}
    for dte, start in ((7, 0), (14, 40), (21, 80)):
        for j in range(4 if dte != 21 else 2):
            avail = varied(j)
            rows.append((_day(start + j), _day(start + j + dte), dte, True, avail,
                         "ok" if "implied_cdf" in avail else "null", 1))
            if dte == 7:
                cohort[_day(start + j)] = avail
    plan["SPY"], designed["SPY"] = rows, (7, cohort)
    # IWM: DTE 21 has 4 dates; DTE 30 has 5 dates of which 2 are pending: 21 wins.
    rows, cohort = [], {}
    for dte, count, pending in ((7, 2, 0), (21, 4, 0), (30, 5, 2)):
        for j in range(count):
            avail = varied(j + 1)
            settled = j >= pending
            rows.append((_day(100 + dte + j), _day(100 + dte + j + dte), dte, settled, avail,
                         "ok" if "implied_cdf" in avail else "null", 1))
            if dte == 21:
                cohort[_day(100 + dte + j)] = avail
    plan["IWM"], designed["IWM"] = rows, (21, cohort)
    return plan, designed


def _index_panel(families):
    plan, designed = _index_plan()
    rows = []
    for symbol, entries in plan.items():
        for quote_date, expiry, dte, settled, avail, cdf, copies in entries:
            for k in range(copies):
                other = expiry if k == 0 else _day((date.fromisoformat(expiry)
                                                    - date(2020, 1, 2)).days + 1)
                rows.append(_index_row(families, symbol, quote_date, other, dte, settled,
                                       avail, cdf))
    return rows, designed


def _stock_panel(cdf_fields):
    """The AMZN-shaped panel and its designed answer: the winner is DTE 30 (4 settled dates),
    where the old CDF argmax would be 42 (3 eligible dates)."""
    rows = [_stock_row(cdf_fields, "AMZN", f"2024-04-0{i + 1}", 30, 0.01, eligible=i < 2)
            for i in range(4)]
    rows += [_stock_row(cdf_fields, "AMZN", f"2024-05-0{i + 1}", 42, 0.02, eligible=True)
             for i in range(3)]
    rows += [_stock_row(cdf_fields, "AMZN", f"2024-06-0{i + 1}", 42, None, eligible=True,
                        reasons=["pending_or_missing_terminal_close"]) for i in range(2)]
    rows.append(_stock_row(cdf_fields, "AMZN", "2026-09-30", 44, None, eligible=False,
                           basis="indicative_quote"))
    rows.append(_stock_row(cdf_fields, "AMZN", "2024-04-06", None, None, eligible=False,
                           reasons=["non_session_entry"]))
    return rows


def _brute_coverage(rows, symbol):
    """Per exact DTE 1..45: distinct settled entry dates, rows and expiries (restated here)."""
    out = {}
    for r in rows:
        dte = r["actual_calendar_dte"]
        if r["symbol"] != symbol or dte is None or not 1 <= dte <= 45 \
                or r["terminal_return"] is None:
            continue
        cell = out.setdefault(dte, {"dates": set(), "rows": 0, "expiries": set()})
        cell["dates"].add(r["quote_date"])
        cell["rows"] += 1
        cell["expiries"].add(r["expiry"])
    return out


def _winner(counts):
    best = max(len(c["dates"]) for c in counts.values())
    return min(d for d, c in counts.items() if len(c["dates"]) == best)


def _ms(quote_date):
    return int(datetime.fromisoformat(quote_date).replace(tzinfo=timezone.utc).timestamp()
               * 1000)


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# -- (a) the two documents validate and plan -----------------------------------------------------


@pytest.mark.parametrize("name", [STEP1, STEP2])
def test_step_documents_validate_and_plan(child_root, name):
    for verb in ("validate", "plan"):
        done = _must(_cli(["dskit.pipeline", verb, str(child_root / name)], child_root))
    planned = json.loads(done.stdout)
    assert "source" in planned["order"][:2]
    assert all(planned["nodes"][key]["class"] for key in planned["order"])
    expected = {STEP1: {"winner__qqq", "winner__iwm", "selection_rows",
                        "selection_evidence", "coverage_figure__qqq"},
                STEP2: {"combinations__qqq", "combinations__iwm", "date_patterns__iwm",
                        "combination_evidence", "date_evidence", "step1_selection"}}[name]
    assert expected <= set(planned["order"])
    # SPY is parked (owner ruling C, 2026-10-02): foreach.keys are QQQ and IWM, no SPY node
    assert not [key for key in planned["order"] if key.endswith("__spy")]


# -- (b) step 1: no CDF anywhere; settled, listed dates per exact DTE; argmax ---------------


def test_step1_has_no_cdf_clause_and_counts_settled_listed_dates(child_root):
    text = (child_root / STEP1).read_text()
    assert "rn_" not in text            # no eligibility flag, no quantile - notes included
    doc = json.loads(text)
    template = doc["foreach"]["pipeline"]
    assert template["cohort"]["params"]["where"] == [
        {"field": "symbol", "op": "==", "value": "$each"},
        {"field": "actual_calendar_dte", "op": ">=", "value": 1},
        {"field": "actual_calendar_dte", "op": "<=", "value": 45},
        {"field": "terminal_return", "op": "!=", "value": None}]
    assert list(template)[:10] == ["cohort", "coverage", "selection_group", "maximum",
                                   "maximum_value", "maximizers", "tie_break", "selected_dte",
                                   "winner", "coverage_figure"]
    assert template["coverage"]["params"]["keys"] == ["symbol", "actual_calendar_dte"]
    assert {k: v["op"] for k, v in template["coverage"]["params"]["aggregates"].items()} == {
        "listed_dates": "nunique", "listed_forecasts": "count", "expiry_series": "nunique",
        "first_entry_ms": "min", "last_entry_ms": "max"}
    assert template["maximizers"]["params"]["where"][0]["field"] == "listed_dates"
    assert template["tie_break"]["params"]["aggregates"] == {
        "days_to_expiry": {"op": "min", "field": "actual_calendar_dte"}}
    # the document's own chain over the designed panel, against an independent census
    families = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]
    panel, designed = _index_panel(families)
    for symbol, (answer, _) in designed.items():
        rows = [dict(r, quote_date_ms=_ms(r["quote_date"])) for r in panel]
        out = _run_nodes(template, {"source": {"records": rows}}, each=symbol)
        counts = _brute_coverage(panel, symbol)
        assert {r["actual_calendar_dte"]: (r["listed_dates"], r["listed_forecasts"],
                                           r["expiry_series"])
                for r in out["coverage"]["records"]} == {
            d: (len(c["dates"]), c["rows"], len(c["expiries"])) for d, c in counts.items()}
        assert [r["actual_calendar_dte"] for r in out["winner"]["records"]] == [answer]
        assert _winner(counts) == answer
        assert 0 not in counts and 46 not in counts
    # the designed traps: QQQ's old CDF argmax (14) loses to 7 now, and SPY's tie goes short
    assert len(_brute_coverage(panel, "QQQ")[7]["dates"]) == 5
    assert len(_brute_coverage(panel, "QQQ")[14]["dates"]) == 4
    spy = _brute_coverage(panel, "SPY")
    assert len(spy[7]["dates"]) == len(spy[14]["dates"]) == 4
    assert len(_brute_coverage(panel, "IWM")[30]["dates"]) == 3   # pending rows do not count


# -- (c) the two documents share one source and one cohort predicate -----------------------


def test_step_files_share_source_and_cohort_predicate(child_root):
    one, two = _step(child_root, STEP1), _step(child_root, STEP2)
    assert one["pipeline"]["source"]["uses"] == two["pipeline"]["source"]["uses"]
    # one source and one read shape; only the root differs: step 1 reads the onboarding store
    # (an absolute root, pinned in test_config_data_sources), step 2 reads step 1b's per-run
    # hand-off, onboarded under the working directory
    other_than_root = lambda doc: {k: v for k, v in doc["pipeline"]["source"]["params"].items()  # noqa: E731
                                   if k != "root"}
    assert other_than_root(one) == other_than_root(two)
    assert one["pipeline"]["source"]["params"]["root"].startswith("/")
    assert two["pipeline"]["source"]["params"]["root"] == STEP2_PANEL_ROOT
    assert two["pipeline"]["source"]["params"]["ts_out"] != "asof_ms"   # AMZN rows carry it
    assert one["foreach"]["keys"] == two["foreach"]["keys"]
    ranged = ("actual_calendar_dte",)
    first = [c for c in one["foreach"]["pipeline"]["cohort"]["params"]["where"]
             if c["field"] not in ranged]
    second = [c for c in two["foreach"]["pipeline"]["cohort"]["params"]["where"]
              if c["field"] not in ranged]
    assert first == second == [{"field": "symbol", "op": "==", "value": "$each"},
                               {"field": "terminal_return", "op": "!=", "value": None}]
    dte_clauses = [c for c in two["foreach"]["pipeline"]["cohort"]["params"]["where"]
                   if c["field"] == "actual_calendar_dte"]
    assert [c["value"] for c in dte_clauses] == ["$selected_dte.table.all"]
    stripped = json.loads(json.dumps(two))      # no integer DTE typed anywhere in step 2

    def walk(node):
        if isinstance(node, dict):
            if node.get("field") == "actual_calendar_dte" and "value" in node:
                assert not isinstance(node["value"], int), node
            for key, value in node.items():
                if key != "notes":
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(stripped)


# -- (d) step 2: one family vocabulary, everywhere ------------------------------------------


def test_step2_family_vocabulary_agrees_everywhere(child_root):
    doc = _step(child_root, STEP2)
    families = doc["pipeline"]["family_contracts"]["params"]["tables"]["families"]
    assert list(families) == FAMILIES and FAMILIES[0] == "implied_cdf"
    assert {"field": "rn_proxy_eligible", "op": "==", "value": 1} in \
        families["implied_cdf"]["quality_checks"]
    template = doc["foreach"]["pipeline"]
    keys = list(template)
    for prefix in ("check_", "summary_", "expand_"):
        assert [k[len(prefix):] for k in keys if k.startswith(prefix)] == FAMILIES, prefix
    for family, spec in families.items():
        required = [clause for field in spec["fields"]
                    for clause in ({"field": field, "op": ">=", "value": -FLOAT_MAX},
                                   {"field": field, "op": "<=", "value": FLOAT_MAX})]
        node = template[f"check_{family}"]
        assert node["params"] == {"field": f"available_{family}", "cases": [
            {"when": required + spec["quality_checks"], "value": "yes"},
            {"when": [], "value": "no"}]}, family
        assert template[f"summary_{family}"]["params"]["keys"] == [
            "symbol", f"available_{family}"]
        assert template[f"expand_{family}"]["params"] == {
            "key": f"available_{family}", "how": "strict", "allow_fanout": True,
            "tables": {family: {"yes": [{f"with_{family}": 1}, {f"with_{family}": 0}],
                                "no": [{f"with_{family}": 0}]}}}
    flags = [f"available_{f}" for f in FAMILIES]
    assert template["date_patterns"]["params"]["keys"] == [
        "symbol", "selection_group", "actual_calendar_dte", "quote_date", *flags]
    assert template["patterns"]["params"]["keys"] == [
        "symbol", "selection_group", "actual_calendar_dte", *flags]
    assert template["universe_flags"]["params"]["tables"] == {
        "all_available": {"all": {flag: "yes" for flag in flags}}}
    assert template["combinations"]["params"]["keys"] == [
        "symbol", "actual_calendar_dte", *[f"with_{f}" for f in FAMILIES]]
    assert template["combinations"]["params"]["aggregates"] == {
        "dates": {"op": "sum", "field": "pattern_dates"}}
    # the chains are wired in the vocabulary's order
    chain = ["checked_cohort", *[f"check_{f}" for f in FAMILIES]]
    for before, after in zip(chain, chain[1:]):
        assert template[after]["inputs"] == {"records": f"${before}.records"}
    expand = ["pattern_space", *[f"expand_{f}" for f in FAMILIES]]
    assert template["expand_implied_cdf"]["inputs"] == {"records": "$pattern_space.merged"}
    for before, after in zip(expand[1:], expand[2:]):
        assert template[after]["inputs"] == {"records": f"${before}.records"}
    assert template["combinations"]["inputs"] == {"records": "$expand_chain_nodes.records"}
    assert "historical known-at certification" in doc["notes"]


# -- (e) the hand-off from step 1 to step 2 ----------------------------------------------------


def test_selection_handoff_paths_agree(child_root):
    import posixpath

    from dskit.onboarding.connector import check_config
    from dskit.onboarding.libs.localtables import LocalTablesConnector

    one, two = _step(child_root, STEP1), _step(child_root, STEP2)
    source = _step(child_root, SELECTION_SOURCE)
    written = one["pipeline"]["selection_evidence"]["params"]["path"]
    assert posixpath.normpath(posixpath.dirname(written)) == posixpath.normpath(source["path"])
    stem = posixpath.basename(written).rsplit(".", 1)[0]
    assert source["streams"] == [stem] and two["pipeline"]["step1_selection"]["params"][
        "stream"] == stem
    assert source["formats"] == [written.rsplit(".", 1)[1]]
    assert source["effective_field"] in one["foreach"]["pipeline"]["coverage"]["params"][
        "aggregates"]
    assert source["effective_unit"] == "ms"
    assert two["pipeline"]["step1_selection"]["params"]["key_fields"] == ["symbol"]
    check_config(LocalTablesConnector(), source)


# -- (f) step 2's combination table against a brute-force count --------------------------------


def _checked_rows():
    """About twelve entry dates of one ticker with varied, designed yes/no per family."""
    import random

    rng = random.Random(20261001)
    rows = []
    for i in range(12):
        flags = {f"available_{f}": rng.choice(("yes", "no")) for f in FAMILIES}
        for extra in range(2 if i in (3, 8) else 1):   # two expiries, one pattern
            rows.append({"symbol": "QQQ", "selection_group": "all", "actual_calendar_dte": 7,
                         "quote_date": _day(i), "expiry": _day(i + 7 + extra),
                         "quote_date_ms": _ms(_day(i)), **flags})
    rows[1].update({f"available_{f}": "no" for f in FAMILIES})      # an all-no date
    return rows


def test_step2_subset_counts_match_brute_force(child_root):
    template = _step(child_root, STEP2)["foreach"]["pipeline"]
    rows = _checked_rows()
    tail = list(template)[list(template).index("date_patterns"):]
    out = _run_nodes(template, {"check_chain_nodes": {"records": rows}}, each="QQQ", only=tail)
    combos = out["combinations"]["records"]
    assert len(combos) == 2 ** len(FAMILIES) == 8192
    flags = {}
    for r in rows:
        flags[r["quote_date"]] = {f for f in FAMILIES if r[f"available_{f}"] == "yes"}
    seen = set()
    for combo in combos:
        chosen = {f for f in FAMILIES if combo[f"with_{f}"] == 1}
        key = tuple(combo[f"with_{f}"] for f in FAMILIES)
        assert key not in seen
        seen.add(key)
        assert combo["dates"] == sum(chosen <= have for have in flags.values()), chosen
        assert combo["symbol"] == "QQQ" and combo["actual_calendar_dte"] == 7
    assert len(seen) == 8192
    totals = {tuple(c[f"with_{f}"] for f in FAMILIES): c["dates"] for c in combos}
    assert totals[(0,) * 13] == len(flags) == 12            # the cohort total
    # no date has every family, yet the all-families subset is present, with zero, rather than
    # missing: the zero-weight universe row is what makes every subset appear
    assert not any(have == set(FAMILIES) for have in flags.values())
    assert totals[(1,) * 13] == 0
    assert len(out["patterns"]["records"]) <= 12


def test_duplicate_pattern_date_refuses(child_root):
    from dskit.pipeline.kinds_flow import KeyBy

    params = _step(child_root, STEP2)["foreach"]["pipeline"]["one_pattern_per_date"]["params"]
    rows = [{"quote_date": "2020-01-02", "cohort_rows": 1, "available_implied_cdf": "yes"},
            {"quote_date": "2020-01-02", "cohort_rows": 1, "available_implied_cdf": "no"}]
    with pytest.raises(ValueError):
        KeyBy("one_pattern_per_date", params).run(None, {"records": rows})
    # the same pattern on two expiries is ONE date-pattern row and does not refuse
    one = [{"quote_date": "2020-01-02", "cohort_rows": 2}]
    assert KeyBy("one_pattern_per_date", params).run(None, {"records": one})["table"] == {
        "2020-01-02": 2}


# -- (h) both documents, end to end, through the real CLIs on fixture panels --------------


def _onboard(cwd, source, root, config, stream):
    _must(_cli(["dskit.onboarding", "init", "--root", root], cwd))
    _must(_cli(["dskit.onboarding", "register-source", source, "--catalog-source", source,
                "--connector", "localtables", "--config", config, "--activate",
                "--root", root], cwd))
    _must(_cli(["dskit.onboarding", "acquire", "--source", source, "--stream", stream,
                "--mode", "backfill", "--root", root], cwd))


def _only_run(cwd, series):
    runs = sorted((cwd / "pipeline_runs" / series / "runs").iterdir())
    assert len(runs) == 1
    return runs[0]


def _write_panel(shape, child_root, cwd, families):
    """The fixture panel in the layout the shape's runbook uses; the inline source config."""
    if shape == "index":
        rows, designed = _index_panel(families)
        _write_jsonl(cwd / "pipeline_runs" / "panel-data" / "input_panel.jsonl", rows)
        config = json.dumps({"path": "pipeline_runs/panel-data", "layout": "file",
                             "effective_field": "quote_date", "effective_unit": "iso",
                             "streams": ["input_panel"], "formats": ["jsonl"]})
        return rows, designed, config
    from dskit.pipeline.kinds_table import RecordsWrite
    from dskit.pipeline.node import NodeContext

    prep = json.loads((child_root.parent / "stock_options/configs/"
                       "run-prepare-option-panel.json").read_text())
    rows = _stock_panel(families["implied_cdf"]["fields"])
    (cwd / "pipeline_runs" / "option-panel").mkdir(parents=True)
    ctx = NodeContext(name="t", asof="2026-10-01", run_dir=str(cwd / "r"))
    here = os.getcwd()
    os.chdir(cwd)
    try:
        RecordsWrite("panel_rows", prep["pipeline"]["panel_rows"]["params"]).run(
            ctx, {"records": rows})
    finally:
        os.chdir(here)
    config = "@" + str(child_root.parent / "stock_options/configs/source-option-panel.json")
    designed = {"AMZN": (30, {f"2024-04-0{i + 1}": ({"implied_cdf"} if i < 2 else set())
                              for i in range(4)})}
    return rows, designed, config


def _step_copy(child_root, name, cwd, keys, source_root=None):
    """A temporary copy of a step document whose ONLY differences are foreach.keys (when given)
    and the panel source root (when given: steps 1 and 1b ship it at the onboarding store, and
    these tests onboard their fixture panel under the working directory instead)."""
    original = _step(child_root, name)
    copy_ = json.loads(json.dumps(original))
    expected = set()
    if keys is not None:
        copy_["foreach"]["keys"] = keys
        expected.add("/foreach/keys")
    if source_root is not None:
        copy_["pipeline"]["source"]["params"]["root"] = source_root
        expected.add("/pipeline/source/params/root")
    delta = _differences(original, copy_)
    assert delta == expected
    path = cwd / (("amzn-" if keys is not None else "local-") + name.rsplit("/", 1)[1])
    path.write_text(json.dumps(copy_))
    return path


@pytest.mark.parametrize("shape", ["index", "stock"])
def test_two_step_files_end_to_end(child_root, tmp_path, shape):
    pytest.importorskip("matplotlib")
    families = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]
    rows, designed, config = _write_panel(shape, child_root, tmp_path, families)
    _onboard(tmp_path, "cdf-horizon-panel", SOURCE_ROOT, config, "input_panel")
    _onboard(tmp_path, "cdf-horizon-panel", STEP2_PANEL_ROOT, config, "input_panel")   # step 2's root
    for sub in ("step1-expiry-coverage/selection", "step2-feature-availability"):
        (tmp_path / "pipeline_runs" / sub).mkdir(parents=True)
    if shape == "index":
        step1 = _step_copy(child_root, STEP1, tmp_path, None, source_root=SOURCE_ROOT)
        step2 = child_root / STEP2
    else:
        step1 = _step_copy(child_root, STEP1, tmp_path, ["AMZN"], source_root=SOURCE_ROOT)
        step2 = _step_copy(child_root, STEP2, tmp_path, ["AMZN"])
    tickers = [t for t in designed if t != "SPY"]      # SPY is parked: not in foreach.keys
    # -- step 1 --
    _must(_cli(["dskit.pipeline", "run", str(step1), "--asof", "2026-10-01"], tmp_path))
    run1 = _only_run(tmp_path, "step1-expiry-coverage")
    carry = json.loads((run1 / "carry.json").read_text())
    winners = {}
    for symbol in tickers:
        slug = symbol.lower()
        counts = _brute_coverage(rows, symbol)
        table = carry[f"coverage__{slug}"]["records"]
        assert [r["actual_calendar_dte"] for r in table] == sorted(counts)   # full table kept
        for r in table:
            cell = counts[r["actual_calendar_dte"]]
            assert (r["listed_dates"], r["listed_forecasts"], r["expiry_series"]) == (
                len(cell["dates"]), cell["rows"], len(cell["expiries"]))
            assert r["first_entry_ms"] == min(_ms(d) for d in cell["dates"])
            assert r["last_entry_ms"] == max(_ms(d) for d in cell["dates"])
        winner = carry[f"winner__{slug}"]["records"]
        assert [w["actual_calendar_dte"] for w in winner] == [_winner(counts)] \
            == [designed[symbol][0]]
        winners[symbol] = winner[0]
        assert (run1 / "artifacts" / f"coverage_figure__{slug}" / "coverage.png").is_file()
    selected = _read_jsonl(tmp_path / "pipeline_runs/step1-expiry-coverage/selection/"
                           "selected.jsonl")
    assert sorted(r["symbol"] for r in selected) == sorted(tickers)
    assert {r["symbol"]: r for r in selected} == winners
    # -- the disagreeing hand-off refuses (index only; its own working directory) --
    if shape == "index":
        neg = tmp_path / "neg"
        neg.mkdir()
        _write_jsonl(neg / "pipeline_runs/panel-data/input_panel.jsonl", rows)
        _onboard(neg, "cdf-horizon-panel", SOURCE_ROOT, config, "input_panel")
        _onboard(neg, "cdf-horizon-panel", STEP2_PANEL_ROOT, config, "input_panel")
        tampered = [dict(r, listed_dates=r["listed_dates"] + (r["symbol"] == "QQQ"))
                    for r in selected]
        _write_jsonl(neg / "pipeline_runs/step1-expiry-coverage/selection/selected.jsonl",
                     tampered)
        (neg / "pipeline_runs/step2-feature-availability").mkdir(parents=True)
        _onboard(neg, "step1-selection", "./pipeline_runs/step1-selection-source",
                 "@" + str(child_root / SELECTION_SOURCE), "selected")
        done = _cli(["dskit.pipeline", "run", str(step2), "--asof", "2026-10-01"], neg)
        assert done.returncode != 0
        assert "agreed_dte" in done.stdout + done.stderr
        assert not (neg / "pipeline_runs/step2-feature-availability/dates.jsonl").exists()
    # -- step 2 --
    _onboard(tmp_path, "step1-selection", "./pipeline_runs/step1-selection-source",
             "@" + str(child_root / SELECTION_SOURCE), "selected")
    _must(_cli(["dskit.pipeline", "run", str(step2), "--asof", "2026-10-01"], tmp_path))
    carry = json.loads((_only_run(tmp_path, "step2-feature-availability")
                        / "carry.json").read_text())
    out_dir = tmp_path / "pipeline_runs/step2-feature-availability"
    dates = _read_jsonl(out_dir / "dates.jsonl")
    combos = _read_jsonl(out_dir / "combinations.jsonl")
    assert len(combos) == 8192 * len(tickers)
    for symbol in tickers:
        slug = symbol.lower()
        dte, cohort = designed[symbol]
        summary = carry[f"cohort_summary__{slug}"]["records"]
        assert [(r["cohort_dates"], r["actual_calendar_dte"]) for r in summary] == [
            (winners[symbol]["listed_dates"], dte)]
        have = {d["quote_date"]: {f for f in FAMILIES if d[f"available_{f}"] == "yes"}
                for d in dates if d["symbol"] == symbol}
        assert set(have) == set(cohort) and len(have) == winners[symbol]["listed_dates"]
        assert have == {d: set(a) for d, a in cohort.items()}      # the designed flags hold
        for family in FAMILIES:
            yes = sum(family in a for a in have.values())
            got = {r[f"available_{family}"]: r["dates"]
                   for r in carry[f"summary_{family}__{slug}"]["records"]}
            assert got.get("yes", 0) == yes and got.get("no", 0) == len(have) - yes, family
        mine = {}
        for c in combos:
            if c["symbol"] == symbol:
                key = tuple(c[f"with_{f}"] for f in FAMILIES)
                assert key not in mine
                mine[key] = c["dates"]
        assert len(mine) == 8192
        for subset in itertools.product((0, 1), repeat=len(FAMILIES)):
            chosen = {f for f, bit in zip(FAMILIES, subset) if bit}
            assert mine[subset] == sum(chosen <= a for a in have.values()), chosen
    if shape == "index":
        cdf_missing = _day(3)          # a date whose CDF is absent stays in the cohort as a gap
        flags = {d["quote_date"]: d for d in dates if d["symbol"] == "QQQ"}
        assert flags[cdf_missing]["available_implied_cdf"] == "no"
        assert flags[_day(4)]["available_implied_cdf"] == "no"      # ineligible: finite, flag 0
        assert flags[_day(4)]["available_macro_context"] == "no"    # age 15 > 7
        assert flags[_day(1)]["available_volatility_context"] == "no"   # market_gvz_missing 1
        assert flags[_day(2)]["available_return_history"] == "no"
        assert {f for f in FAMILIES if flags[_day(0)][f"available_{f}"] == "yes"} == \
            designed["QQQ"][1][_day(0)]
    else:
        flags = {d["quote_date"]: d for d in dates}
        assert [f for f in FAMILIES if any(d[f"available_{f}"] == "yes"
                                           for d in flags.values())] == ["implied_cdf"]
        totals = {tuple(c[f"with_{f}"] for f in FAMILIES): c["dates"] for c in combos}
        assert totals[(0,) * 13] == 4 and totals[(1,) + (0,) * 12] == 2
        assert totals[(1,) * 13] == 0


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


# -- step 1b: the engineered feature panel between steps 1 and 2 (ADR-0217) ----------------------

FE = "configs/run-step1b-feature-engineering.json"
FE_SOURCE = "configs/source-feature-panel.json"
TAIL_DATA = "configs/run-predictive-cdf-tail-data.json"
FE_IDENTITY = ["symbol", "quote_date", "expiry"]
FE_WITHHELD = "oi_publication_clock_unverified"
#: The 18 withheld open-interest fields, restated here independently of the shipped document:
#: three OI x greek fields of positioning_changes, the two liquidity OI fields, the nine
#: chain-node OI fields and the four panel columns that still hold the same OI.
FE_OI_POSITIONING = ("chain_log_delta_oi", "chain_log_gamma_oi", "chain_log_vega_oi")
FE_OI_LIQUIDITY = ("chain_log_put_call_oi", "chain_log_open_interest")
FE_OI_NODES = tuple(f"chain_node_{i:02d}_log_oi" for i in range(9))
FE_OI_PANEL = ("chain_open_interest", "chain_put_call_oi", "chain_has_put_call_oi",
               "chain_liquidity_asymmetry")
FE_OI_ALL = FE_OI_POSITIONING + FE_OI_LIQUIDITY + FE_OI_NODES + FE_OI_PANEL
#: Open interest read from an EARLIER snapshot of the same expiry series: known at entry.
FE_OI_PRIOR = ("chain_log_lag_open_interest", "chain_log_lag_oi_change")
FE_FAMILIES = ["variance_gap", "ohlc_shape", "positioning_changes", "expanded_volatility_context"]


def _fe(child_root):
    return _step(child_root, FE)


def _fe_attach(child_root):
    return _fe(child_root)["foreach"]["pipeline"]["features"]["params"]


def _is_oi_named(name):
    return "open_interest" in name or "oi" in name.split("_")


def test_step1b_document_validates_and_plans(child_root):
    for verb in ("validate", "plan"):
        done = _must(_cli(["dskit.pipeline", verb, str(child_root / FE)], child_root))
    planned = json.loads(done.stdout)
    assert {"source", "step1_selection", "panel", "panel_evidence", "features__qqq",
            "features__iwm", "dense__qqq", "horizon_rows__iwm"} <= set(planned["order"])
    assert not [key for key in planned["order"] if key.endswith("__spy")]     # SPY is parked
    assert planned["nodes"]["features__qqq"]["class"].endswith(":AttachByIdentity")
    assert planned["nodes"]["panel"]["class"] == "index_options.nodes:ExactExpiryPanelRead"


def test_step1b_reader_params_equal_the_tail_data_block_and_its_columns_the_attach_contract(
        child_root):
    from dskit.pipeline.kinds_flow import AttachByIdentity
    from index_options.nodes import ExactExpiryPanelRead

    reader = _fe(child_root)["pipeline"]["panel"]["params"]
    tail = json.loads((child_root / TAIL_DATA).read_text())["data"]
    assert ExactExpiryPanelRead.validate_params(reader) == []
    study_conventions = {"reference_window", "change_lags", "directional_windows",
                         "periods_per_year", "calendar", "calendar_pad_days", "dividend_field"}
    assert set(reader) - {"columns"} - study_conventions == (
        set(tail) - {"archive_root", "fred_market_symbols"})      # the frozen tail config omits them
    for key, value in reader.items():
        if key in ("surface", "lifecycle", "chain_features"):  # store references now (ADR-0225)
            assert value["relpath"] == os.path.basename(tail[key]), key   # the same file
        elif key not in {"columns", "market_symbols"} | study_conventions:
            assert value == tail[key], key                    # key by key, values unchanged
    markets = reader["market_symbols"]
    assert markets == {k: tail["market_symbols"][k] for k in markets} and set(markets) == {
        "market_vix1y", "market_rvx", "market_vxd", "market_ovx", "market_vxeem",
        "market_vxslv", "market_vxtlt"}                       # exactly the seven the family reads
    attach = _fe_attach(child_root)
    assert AttachByIdentity.validate_params(attach) == []
    owned = [f for spec in attach["families"].values() for f in spec["fields"]]
    assert reader["columns"] == attach["identity"] + attach["agree_fields"] + owned
    assert attach["identity"] == FE_IDENTITY
    assert {"terminal_return", "actual_calendar_dte", "chain_atm_iv", "rv_22"} <= set(
        attach["agree_fields"]) and {f for f in attach["agree_fields"]
                                     if f.startswith("rn_q_")} == {
        "rn_q_0100", "rn_q_0500", "rn_q_1000", "rn_q_2500", "rn_q_5000", "rn_q_7500",
        "rn_q_9000", "rn_q_9500", "rn_q_9900"}


def test_step1b_families_equal_step2s_contracts_with_their_quality_columns(child_root):
    contracts = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]
    families = _fe_attach(child_root)["families"]
    assert list(families) == FE_FAMILIES
    for name in FE_FAMILIES:
        quality = [c["field"] for c in contracts[name]["quality_checks"]]
        want = contracts[name]["fields"] + [f for f in dict.fromkeys(quality)
                                            if f not in contracts[name]["fields"]]
        assert families[name]["fields"] == want, name
    assert len(families["expanded_volatility_context"]["fields"]) == 21


def test_step1b_withholds_exactly_the_open_interest_fields_and_computes_the_prior_ones(
        child_root):
    attach = _fe_attach(child_root)
    families, carried = attach["families"], attach["carried"]
    assert set(families["positioning_changes"]["withheld_fields"]) == set(FE_OI_POSITIONING)
    assert set(carried) == {"expiry_density", "liquidity_oi", "chain_nodes_oi",
                            "oi_panel_columns"}
    assert carried["liquidity_oi"]["fields"] == list(FE_OI_LIQUIDITY)
    assert carried["chain_nodes_oi"]["fields"] == list(FE_OI_NODES)
    assert carried["oi_panel_columns"]["fields"] == list(FE_OI_PANEL)
    assert carried["expiry_density"] == {
        "fields": ["expiry_density"], "withheld_fields": {},
        "clock_note": carried["expiry_density"]["clock_note"]}
    step2 = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]
    assert set(carried["liquidity_oi"]["fields"]) <= set(step2["liquidity"]["fields"])
    assert set(carried["chain_nodes_oi"]["fields"]) <= set(step2["chain_nodes"]["fields"])
    withheld = {}
    for section in (families, carried):
        for name, spec in section.items():
            if name == "expiry_density":
                continue
            assert all(r == FE_WITHHELD for r in spec["withheld_fields"].values()), name
            withheld.update(spec["withheld_fields"])
    for name in ("liquidity_oi", "chain_nodes_oi", "oi_panel_columns"):
        assert set(carried[name]["withheld_fields"]) == set(carried[name]["fields"]), name
    assert set(withheld) == set(FE_OI_ALL) and len(FE_OI_ALL) == 18 == len(withheld)
    positioning = families["positioning_changes"]
    assert set(FE_OI_PRIOR) <= set(positioning["fields"])
    assert not set(FE_OI_PRIOR) & set(withheld)
    for spec in (*families.values(), *carried.values()):
        assert spec["clock_note"] and isinstance(spec["clock_note"], str)
    for name in ("positioning_changes",):
        assert "audit" in families[name]["clock_note"]
    for name in ("liquidity_oi", "chain_nodes_oi", "oi_panel_columns"):
        assert "publication clock" in carried[name]["clock_note"] and "audit" in carried[name][
            "clock_note"]


def test_dropping_liquidity_and_chain_nodes_keeps_carried_fields_inside_the_schema(child_root):
    from dskit.pipeline.workflow_hooks import FamiliesSpec

    manifest = json.loads((child_root / "configs" / "workflow.json").read_text())
    spec = {**manifest["args"]["families"], "drop": ["liquidity", "chain_nodes"]}
    whole = FamiliesSpec().apply({"spec": manifest["args"]["families"]})
    got = FamiliesSpec().apply({"spec": spec})
    assert set(whole["carried"]) - set(got["carried"]) == {"liquidity_oi", "chain_nodes_oi"}
    for name, entry in got["carried"].items():
        inside = {f in whole["schema_fields"] for f in entry["fields"]}
        assert {f in got["schema_fields"] for f in entry["fields"]} == inside, name
    assert not {"liquidity", "chain_nodes"} & set(got["availability"])


def test_step1b_every_open_interest_named_column_is_withheld_or_a_prior_observation(child_root):
    step2 = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]
    reader = _fe(child_root)["pipeline"]["panel"]["params"]["columns"]
    names = {f for spec in step2.values() for f in spec["fields"]} | set(reader)
    unaccounted = sorted(n for n in names if _is_oi_named(n)
                         and n not in FE_OI_ALL and n not in FE_OI_PRIOR)
    assert unaccounted == []


def test_step1b_all_eighteen_withheld_fields_stay_null_whatever_the_sources_hold(child_root):
    from dskit.pipeline.kinds_flow import AttachByIdentity

    attach = _fe_attach(child_root)
    table, stream = [], []
    for d in ("2020-01-02", "2020-01-03"):
        agree = {f: 1.0 for f in attach["agree_fields"]}
        key = {"symbol": "QQQ", "quote_date": d, "expiry": "2020-02-21"}
        table.append({**key, **agree, **{f: 2.0 for s in attach["families"].values()
                                         for f in s["fields"]}})
        stream.append({**key, **agree, **{f: 3.0 for s in attach["carried"].values()
                                          for f in s["fields"]}})
    out = AttachByIdentity("features", attach).run(None, {"records": stream, "table": table})
    assert len(FE_OI_ALL) == 18
    for row in out["records"]:
        assert [row[f] for f in FE_OI_ALL] == [None] * 18
        assert row["chain_log_lag_open_interest"] == 2.0 and row["expiry_density"] == 3.0
    summary = out["summary"]
    for section in ("families", "carried"):
        for entry in summary[section].values():
            for field, stats in entry["fields"].items():
                if field in FE_OI_ALL:
                    assert (stats["non_null"], stats["discarded"]) == (0, 2), field
                    assert stats["min"] is None and stats["max"] is None
    assert FE_WITHHELD in json.dumps(out["records"]) and FE_WITHHELD in json.dumps(
        out["provenance"])


def test_step1b_clock_note_age_limit_is_the_one_number_in_the_data_and_in_step2(child_root):
    import re

    note = _fe_attach(child_root)["families"]["expanded_volatility_context"]["clock_note"]
    limits = {int(n) for n in re.findall(r"(\d+) calendar days", note)}
    ages = {s["max_age_days"] for s in _fe(child_root)["pipeline"]["panel"]["params"][
        "market_symbols"].values()}
    step2 = _step(child_root, STEP2)["pipeline"]["family_contracts"]["params"]["tables"][
        "families"]["expanded_volatility_context"]["quality_checks"]
    bounds = {c["value"] for c in step2 if c["op"] == "<=" and c["field"].endswith("_age_days")}
    assert limits == ages == bounds and len(limits) == 1


def _fe_template(child_root):
    return _fe(child_root)["foreach"]["pipeline"]


def _ancestors(nodes, key):
    seen, todo = set(), [key]
    while todo:
        for ref in nodes[todo.pop()].get("inputs", {}).values():
            parent = ref[1:].split(".")[0]
            if parent in nodes and parent not in seen:
                seen.add(parent)
                todo.append(parent)
    return seen


def _fe_panel_rows(rows):
    """Prepared-panel rows (symbol, quote_date, expiry, actual dte, planned dte, settled)."""
    return [{"symbol": s, "quote_date": d, "expiry": e, "actual_calendar_dte": a,
             "calendar_dte": p, "terminal_return": 0.01 if settled else None}
            for s, d, e, a, p, settled in rows]


def _fe_selection(symbol, dte):
    return [{"symbol": symbol, "actual_calendar_dte": dte, "listed_forecasts": 99}]


def test_step1b_cohort_is_step1s_settled_rows_at_the_selected_dte_never_typed(child_root):
    template = _fe_template(child_root)
    rows = _fe_panel_rows([
        ("QQQ", "2020-01-02", "2020-01-09", 7, 7, True),
        ("QQQ", "2020-01-03", "2020-01-10", 7, 7, False),        # unsettled
        ("QQQ", "2020-01-06", "2020-01-14", 8, 8, True),         # another DTE
        ("SPY", "2020-01-02", "2020-01-09", 7, 7, True)])        # another ticker
    outputs = {"source": {"records": rows}, "step1_selection": {"records": _fe_selection("QQQ", 7)}}
    out = _run_nodes(template, outputs, each="QQQ")
    assert [r["quote_date"] for r in out["cohort"]["records"]] == ["2020-01-02"]
    for name in ("cohort", "horizon_rows"):                     # the DTE is a reference, never a literal
        dte = [c for c in template[name]["params"]["where"]
               if c["field"] in ("actual_calendar_dte", "calendar_dte") and c["op"] in ("==", "<=")]
        assert [c["value"] for c in dte] == ["$selected_dte.table.all"], name
    assert template["selected_dte"]["params"] == {"key": "selection_group",
                                                  "value": "actual_calendar_dte"}


def test_step1b_cohort_predicate_is_step1s_and_the_horizon_differs_only_in_its_bound(child_root):
    one = _step(child_root, STEP1)["foreach"]["pipeline"]["cohort"]["params"]["where"]
    template = _fe_template(child_root)
    bound = lambda c: c["op"] == "<=" and c["field"] in ("actual_calendar_dte", "calendar_dte")  # noqa: E731
    horizon = template["horizon_rows"]["params"]["where"]
    assert [c for c in horizon if not bound(c)] == [c for c in one if not bound(c)]
    assert [c["field"] for c in horizon if bound(c)] == ["calendar_dte"]    # planned, not actual
    assert [c["field"] for c in one if bound(c)] == ["actual_calendar_dte"]
    assert [c["value"] for c in one if bound(c)][0] <= _fe(child_root)["pipeline"]["panel"][
        "params"]["max_dte"]
    cohort = template["cohort"]["params"]["where"]
    assert [c for c in cohort if c["field"] != "actual_calendar_dte"] == [
        c for c in one if c["field"] not in ("actual_calendar_dte",)]


def test_step1b_expiry_density_counts_listed_planned_expiries_before_the_cohort_filter(
        child_root):
    template = _fe_template(child_root)
    assert "cohort" not in _ancestors(template, "density")      # density is counted BEFORE the cohort
    assert {"density_by_date", "cohort"} <= _ancestors(template, "dense")
    rows = _fe_panel_rows([
        # 2020-01-02: three expiries inside the 7-day horizon, one outside, a pending one
        ("QQQ", "2020-01-02", "2020-01-04", 2, 2, True), ("QQQ", "2020-01-02", "2020-01-07", 5, 5, True),
        ("QQQ", "2020-01-02", "2020-01-09", 7, 7, True), ("QQQ", "2020-01-02", "2020-01-16", 14, 14, True),
        ("QQQ", "2020-01-02", "2020-01-08", 6, 6, False),
        # another symbol's rows must not count
        ("SPY", "2020-01-02", "2020-01-09", 7, 7, True), ("SPY", "2020-01-02", "2020-01-08", 6, 6, True),
        # an ad hoc closure: planned 14 and 13, both settling at actual 13 (selected 7 here)
        ("QQQ", "2020-01-03", "2020-01-10", 7, 7, True), ("QQQ", "2020-01-06", "2020-01-20", 7, 8, True)])
    outputs = {"source": {"records": rows}, "step1_selection": {"records": _fe_selection("QQQ", 7)}}
    out = _run_nodes(template, outputs, each="QQQ")
    dense = {r["quote_date"]: r["expiry_density"] for r in out["dense"]["records"]}
    assert dense == {"2020-01-02": 3, "2020-01-03": 1, "2020-01-06": None}    # a cohort-first 1 would fail
    cohort_first = [r for r in out["cohort"]["records"] if r["quote_date"] == "2020-01-02"]
    assert len(cohort_first) == 1                               # the trap the horizon avoids
    # planned 13 and 14, both settling at actual 13 (selected 13): each date's lone expiry counts 1,
    # and a date whose only cohort expiry is planned 14 gets a declared null
    rows = _fe_panel_rows([
        ("SPY", "2020-02-03", "2020-02-14", 13, 13, True), ("SPY", "2020-02-04", "2020-02-14", 13, 14, True),
        ("SPY", "2020-02-05", "2020-02-18", 13, 13, True), ("SPY", "2020-02-05", "2020-02-19", 13, 14, True)])
    outputs = {"source": {"records": rows}, "step1_selection": {"records": _fe_selection("SPY", 13)}}
    out = _run_nodes(template, outputs, each="SPY")
    assert [(r["quote_date"], r["expiry_density"]) for r in out["dense"]["records"]] == [
        ("2020-02-03", 1), ("2020-02-04", None), ("2020-02-05", 1), ("2020-02-05", 1)]


def test_step1b_row_count_is_pinned_to_step1_and_the_sources_agree(child_root):
    one, two, fe = _step(child_root, STEP1), _step(child_root, STEP2), _fe(child_root)
    pipe = fe["pipeline"]
    assert pipe["panel_evidence"]["params"]["expect"] == "$expected_value.table.all"
    assert pipe["expected_sum"]["params"]["aggregates"] == {
        "rows": {"op": "sum", "field": "listed_forecasts"}}
    assert "listed_forecasts" in one["foreach"]["pipeline"]["coverage"]["params"]["aggregates"]
    assert pipe["expected_value"]["params"] == {"key": "selection_group", "value": "rows"}
    ours, theirs = pipe["source"]["params"], one["pipeline"]["source"]["params"]
    assert ours == {k: theirs[k] for k in ("root", "source", "stream", "key_fields")}
    assert not {"ts_field", "ts_unit", "ts_out"} & set(ours)   # no stamp: step 2 stamps its own
    assert two["pipeline"]["source"]["params"]["ts_out"] == "quote_date_ms"
    assert pipe["step1_selection"]["params"] == two["pipeline"]["step1_selection"]["params"]
    assert fe["foreach"]["keys"] == one["foreach"]["keys"] == two["foreach"]["keys"]
    assert pipe["step1_selection"]["params"]["key_fields"] == ["symbol"]


def test_step1b_writer_agrees_with_its_source_config(child_root):
    import posixpath

    from dskit.onboarding.connector import check_config
    from dskit.onboarding.libs.localtables import LocalTablesConnector

    fe, source = _fe(child_root), _step(child_root, FE_SOURCE)
    written = fe["pipeline"]["panel_evidence"]["params"]["path"]
    assert posixpath.normpath(posixpath.dirname(written)) == posixpath.normpath(source["path"])
    stem, suffix = posixpath.basename(written).rsplit(".", 1)
    assert source["streams"] == [stem] == [fe["pipeline"]["source"]["params"]["stream"]]
    assert source["formats"] == [suffix] == ["jsonl"]
    assert (source["effective_field"], source["effective_unit"]) == ("quote_date", "iso")
    check_config(LocalTablesConnector(), source)


def test_step1b_declares_weekday_pending_and_copies_no_code(child_root):
    fe = _fe(child_root)
    pending = fe["pipeline"]["pending_inputs"]["params"]["tables"]["pending"]
    assert pending["weekday_onehot"]["status"] == "pending_merge"
    assert "ADR-0214" in pending["weekday_onehot"]["reason"]
    kinds = {n["uses"] for n in (*fe["pipeline"].values(), *fe["foreach"]["pipeline"].values())}
    assert "weekday-onehot" not in kinds
    assert "AMZN" in fe["foreach"]["notes"] and "AMZN" in fe["notes"]


# -- step 1b end to end: fixture sources -> prepared panel -> step 1 -> step 1b -> step 2 ----------

FE_INDEXES = {"QQQ": "VXN", "SPY": "VIX", "IWM": "RVX"}
FE_RUN_SYMBOLS = ("QQQ", "IWM")        # SPY is parked (owner ruling C, 2026-10-02): sources only
FE_MARKET_SYMBOLS = ("VIX1Y", "RVX", "VXD", "OVX", "VXEEM", "VXSLV", "VXTLT")
FE_PROBS = [.01, .05, .1, .25, .5, .75, .9, .95, .99]
FE_POSITIONING = ["chain_log_volume", "chain_put_call_volume_imbalance",
                  "chain_volume_weighted_rel_spread", "chain_log_lag_open_interest",
                  "chain_log_lag_oi_change", "chain_log_delta_oi", "chain_log_gamma_oi",
                  "chain_log_vega_oi"]


def _fe_env(child_root):
    """The subprocess environment: the child importable from any working directory."""
    return {"PYTHONPATH": os.pathsep.join(
        p for p in (str(child_root), os.environ.get("PYTHONPATH", "")) if p)}


def _onboard_more(cwd, source, root, config, stream, env):
    _must(_cli(["dskit.onboarding", "register-source", source, "--catalog-source", source,
                "--connector", "localtables", "--config", config, "--activate", "--root", root],
               cwd, env))
    _must(_cli(["dskit.onboarding", "acquire", "--source", source, "--stream", stream,
                "--mode", "backfill", "--root", root], cwd, env))


def _fe_world(cwd, child_root):
    """Write synthetic sources for QQQ/SPY/IWM, onboard the index series and build the panels.

    Returns ``(days, reader_paths, panel_frame)``: the session dates, the file paths the
    shipped reader params must point at, and the prepared panel (the read under a config that
    holds no engineered family, plus two unsettled rows).
    """
    import exchange_calendars as xc
    import numpy as np
    import pandas as pd
    from index_options.cdf_study import ExactExpiryCDFPanel

    env = _fe_env(child_root)
    days = [d.strftime("%Y-%m-%d")
            for d in xc.get_calendar("XNYS").sessions_in_range("2023-01-03", "2023-04-28")]
    prices, values = [], []
    for k, symbol in enumerate(FE_INDEXES):
        for i, d in enumerate(days):
            c = 100+i*.3+np.sin(i+k)
            prices.append({"symbol": symbol, "date": d, "open": c*.99, "high": c*1.01,
                           "low": c*.98, "close": c, "dividend_amount": 0.})
    for name in (*FE_INDEXES.values(), *FE_MARKET_SYMBOLS):
        for i, d in enumerate(days):
            if name == "VXSLV" and "2023-03-01" <= d <= "2023-03-20":
                continue                            # a gap longer than the age limit
            values.append({"symbol": name, "date": d, "close": 20.+i*.01})
    _write_jsonl(cwd/"pipeline_runs/index-prices/index_daily.jsonl", prices)
    _write_jsonl(cwd/"pipeline_runs/index-values/index_daily.jsonl", values)
    root = str(cwd/"pipeline_runs/index-data-source")
    _must(_cli(["dskit.onboarding", "init", "--root", root], cwd, env))
    for source, path in (("fixture-prices", "index-prices"), ("fixture-indexes", "index-values")):
        _onboard_more(cwd, source, root, json.dumps({
            "path": f"pipeline_runs/{path}", "layout": "file", "effective_field": "date",
            "effective_unit": "iso", "streams": ["index_daily"], "formats": ["jsonl"]}),
            "index_daily", env)
    surface = []
    for symbol in FE_INDEXES:
        for i, d in enumerate(days[30:60]):
            for k in (3, 7, 14):
                expiry = (pd.Timestamp(d)+pd.Timedelta(days=k)).strftime("%Y-%m-%d")
                px = next(r["close"] for r in prices if r["symbol"] == symbol and r["date"] == d)
                surface.append({
                    "symbol": symbol, "quote_date": d, "expiry": expiry,
                    "chain_underlying_price": px, "chain_atm_iv": .2+i*.001,
                    "chain_put25_iv": .22, "chain_call25_iv": .19, "chain_rel_spread": .01,
                    "chain_put_call_oi": 1.2, "chain_contracts": 100., "chain_open_interest": 1000.,
                    "chain_quote_depth": 50.})
    surface = pd.DataFrame(surface)
    data = cwd/"pipeline_runs/fixture-data"
    data.mkdir(parents=True)
    surface.to_parquet(data/"surface.parquet")
    surface[FE_IDENTITY].assign(first_seen_date="2023-01-03").to_parquet(data/"lifecycle.parquet")
    panel_chain = surface[FE_IDENTITY].copy()
    for p, q in zip(FE_PROBS, np.linspace(-.05, .05, 9)):
        panel_chain[f"rn_q_{int(round(p*1e4)):04d}"] = q
    panel_chain["rn_proxy_eligible"] = 1
    for n in range(9):
        for field in ("log_moneyness", "iv", "log_rel_spread", "log_oi", "log_depth"):
            panel_chain[f"chain_node_{n:02d}_{field}"] = .1+n*.01
        panel_chain[f"chain_node_{n:02d}_mask"] = 1
    tail_chain = panel_chain.copy()
    for j, field in enumerate(FE_POSITIONING):
        tail_chain[field] = .5+.01*j+np.arange(len(tail_chain))*1e-4
    panel_chain.to_parquet(data/"panel_chain.parquet")
    tail_chain.to_parquet(data/"tail_chain.parquet")
    for name in ("panel_chain", "tail_chain"):
        (data/f"{name}.parquet.sources.json").write_text("{}")
    reader = {"root": root, "surface": str(data/"surface.parquet"),
              "lifecycle": str(data/"lifecycle.parquet"),
              "chain_features": str(data/"tail_chain.parquet"),
              "price_source": "fixture-prices", "iv_source": "fixture-indexes"}
    block = json.loads((child_root/TAIL_DATA).read_text())["data"]
    panel_cfg = {k: block[k] for k in ("symbols", "since", "max_dte", "lags", "windows",
                                       "feature_gap_days", "reference_floor", "spot_tolerance",
                                       "raw_chain", "surface_features")}
    panel_cfg.update(reader, chain_features=str(data/"panel_chain.parquet"))
    frame = ExactExpiryCDFPanel(panel_cfg).read()
    pending = frame.iloc[:2].copy()                          # unsettled rows beyond the data
    pending["expiry"] = ["2023-12-22", "2023-12-29"]
    pending["terminal_return"] = np.nan
    frame = pd.concat([frame, pending], ignore_index=True)
    (cwd/"pipeline_runs/panel-data").mkdir(parents=True)
    frame.to_parquet(cwd/"pipeline_runs/panel-data/input_panel.parquet", index=False)
    _onboard(cwd, "cdf-horizon-panel", SOURCE_ROOT, json.dumps({
        "path": "pipeline_runs/panel-data", "layout": "file", "effective_field": "quote_date",
        "effective_unit": "iso", "streams": ["input_panel"], "formats": ["parquet"]}),
        "input_panel")
    return days, reader, frame


def _fe_copy(child_root, name, cwd, edit):
    """A copy of a shipped document with ``edit`` applied; returns its path and the changed paths."""
    original = _step(child_root, name)
    copy_ = json.loads(json.dumps(original))
    edit(copy_)
    path = cwd/("copy-"+name.rsplit("/", 1)[1])
    path.write_text(json.dumps(copy_))
    return path, _differences(original, copy_)


def _fe_none(frame_rows):
    import math
    return [{k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}
            for r in frame_rows]


def test_step1b_end_to_end_from_step1_to_step2(child_root, tmp_path):
    pytest.importorskip("matplotlib")
    import pandas as pd
    from dskit.pipeline.libs.observations import ObservationRows
    from index_options.cdf_study import ExactExpiryCDFPanel

    env = _fe_env(child_root)
    days, reader, frame = _fe_world(tmp_path, child_root)
    panel_rows = _fe_none(frame.to_dict("records"))
    for sub in ("step1-expiry-coverage/selection", "feature-engineering/panel",
                "step2-feature-availability"):
        (tmp_path/"pipeline_runs"/sub).mkdir(parents=True)
    # -- step 1 (the document, reading the fixture panel onboarded here) picks the DTE --
    step1 = _step_copy(child_root, STEP1, tmp_path, None, source_root=SOURCE_ROOT)
    _must(_cli(["dskit.pipeline", "run", str(step1), "--asof", "2026-10-01"], tmp_path, env))
    selected = _read_jsonl(tmp_path/"pipeline_runs/step1-expiry-coverage/selection/selected.jsonl")
    wins = {r["symbol"]: r for r in selected}
    assert sorted(wins) == sorted(FE_RUN_SYMBOLS) and {w["actual_calendar_dte"] for w in
                                                       wins.values()} == {7}
    _onboard(tmp_path, "step1-selection", "./pipeline_runs/step1-selection-source",
             "@"+str(child_root/SELECTION_SOURCE), "selected")
    # -- the disagreeing hand-off refuses before writing anything --
    tampered = [dict(r, listed_forecasts=r["listed_forecasts"]+(r["symbol"] == "QQQ"))
                for r in selected]
    _write_jsonl(tmp_path/"pipeline_runs/neg-selection/selected.jsonl", tampered)
    _onboard(tmp_path, "step1-selection", "./pipeline_runs/neg-selection-source", json.dumps({
        "path": "pipeline_runs/neg-selection", "layout": "file", "effective_field": "last_entry_ms",
        "effective_unit": "ms", "streams": ["selected"], "formats": ["jsonl"]}), "selected")

    def paths(doc):
        doc["pipeline"]["source"]["params"]["root"] = SOURCE_ROOT   # the fixture panel, not the store
        doc["pipeline"]["panel"]["params"].update(reader)

    def neg(doc):
        paths(doc)
        doc["pipeline"]["step1_selection"]["params"]["root"] = "./pipeline_runs/neg-selection-source"

    bad, changed = _fe_copy(child_root, FE, tmp_path, neg)
    assert changed == {f"/pipeline/panel/params/{k}" for k in
                       ("root", "surface", "lifecycle", "chain_features", "price_source",
                        "iv_source")} | {"/pipeline/step1_selection/params/root",
                                         "/pipeline/source/params/root"}
    done = _cli(["dskit.pipeline", "run", str(bad), "--asof", "2026-10-01"], tmp_path, env)
    assert done.returncode != 0 and "panel_evidence" in done.stdout + done.stderr
    assert not (tmp_path/"pipeline_runs/feature-engineering/panel/input_panel.jsonl").exists()
    # -- step 1b --
    good, changed = _fe_copy(child_root, FE, tmp_path, paths)
    assert "/pipeline/step1_selection/params/root" not in changed and len(changed) == 7
    _must(_cli(["dskit.pipeline", "run", str(good), "--asof", "2026-10-01"], tmp_path, env))
    out = _read_jsonl(tmp_path/"pipeline_runs/feature-engineering/panel/input_panel.jsonl")
    cohort = [r for r in panel_rows if r["terminal_return"] is not None
              and r["actual_calendar_dte"] == 7 and r["symbol"] in wins]
    assert len(out) == sum(w["listed_forecasts"] for w in wins.values()) == len(cohort) > 0
    assert all("quote_date_ms" not in r for r in out)
    # every prepared field unchanged; the engineered ones equal the reader's own values
    tail = json.loads((child_root/TAIL_DATA).read_text())["data"]
    tail_cfg = {**{k: v for k, v in tail.items() if k not in ("archive_root", "fred_market_symbols")},
                **reader}
    tail_cfg["market_symbols"] = _fe(child_root)["pipeline"]["panel"]["params"]["market_symbols"]
    read = {tuple(r[k] for k in FE_IDENTITY): r
            for r in _fe_none(ExactExpiryCDFPanel(tail_cfg).read().to_dict("records"))}
    by_key = {tuple(r[k] for k in FE_IDENTITY): r for r in panel_rows}
    attach = _fe_attach(child_root)
    horizon = {}                       # an independent census: settled expiries listed within 7 days
    for r in panel_rows:
        if r["terminal_return"] is not None and r["actual_calendar_dte"] >= 1 \
                and r["calendar_dte"] <= 7:
            horizon.setdefault((r["symbol"], r["quote_date"]), set()).add(r["expiry"])
    density = {k: len(v) for k, v in horizon.items()}
    assert {1, 2} <= set(density.values())                  # a holiday expiry leaves one date at 1
    for row in out:
        key = tuple(row[k] for k in FE_IDENTITY)
        assert all(row[k] == v for k, v in by_key[key].items() if k not in FE_OI_ALL)
        for name, spec in attach["families"].items():
            assert row[f"fe_{name}_status"] == "computed"
            for field in spec["fields"]:
                want = None if field in spec["withheld_fields"] else read[key][field]
                assert row[field] == want, (key, field)
        assert [row[f] for f in FE_OI_ALL] == [None] * 18             # every withheld field, every row
        assert row["expiry_density"] == density[(row["symbol"], row["quote_date"])]
    gap = [r for r in out if r["market_vxslv"] is None]
    assert gap and all(r["market_vxslv_missing"] == 1 for r in gap)
    assert all(json.loads(r["fe_expanded_volatility_context_reasons"])["market_vxslv"]
               == "not_computable" for r in gap)
    assert json.loads(out[0]["fe_positioning_changes_reasons"]) == {
        f: FE_WITHHELD for f in FE_OI_POSITIONING}
    # carry.json: the reader's provenance and each ticker's summary and provenance persist
    carried = [json.loads((r/"carry.json").read_text())
               for r in (tmp_path/"pipeline_runs/feature-engineering/runs").iterdir()]
    carry, = [c for c in carried if "metrics" in c.get("panel_evidence", {})]   # not the refused run
    assert {"surface", "lifecycle", "chain_features", "chain_feature_sources"} == set(
        carry["panel"]["provenance"]["sha256"])
    assert carry["panel"]["provenance"]["adapter_sha256"]
    assert carry["panel_evidence"]["metrics"]["rows"] == len(out)
    for symbol in FE_RUN_SYMBOLS:
        node = carry[f"features__{symbol.lower()}"]
        assert set(node) == {"summary", "provenance"}
        assert set(node["summary"]["families"]) == set(attach["families"])
        assert set(node["summary"]["carried"]) == set(attach["carried"])
        for section in ("families", "carried"):
            for name, spec in attach[section].items():
                fields = node["summary"][section][name]["fields"]
                assert set(fields) == set(spec["fields"]), name
        mine = [density[(r["symbol"], r["quote_date"])] for r in out if r["symbol"] == symbol]
        stats = node["summary"]["carried"]["expiry_density"]["fields"]["expiry_density"]
        assert (stats["min"], stats["max"], stats["null"], stats["non_null"]) == (
            min(mine), max(mine), 0, len(mine))
        stats = node["summary"]["families"]["positioning_changes"]["fields"]["chain_log_gamma_oi"]
        assert stats["non_null"] == 0 and stats["min"] is None and stats["discarded"] == len(mine)
        assert node["provenance"]["input_sha256"]
    # -- the hand-off: onboard the output at the root step 2 already reads --
    _onboard(tmp_path, "cdf-horizon-panel", "./pipeline_runs/feature-panel-source",
             "@"+str(child_root/FE_SOURCE), "input_panel")
    step2 = _step(child_root, STEP2)
    assert step2["pipeline"]["source"]["params"]["root"] == STEP2_PANEL_ROOT   # shipped as is
    params = dict(step2["pipeline"]["source"]["params"])
    here = os.getcwd()
    os.chdir(tmp_path)
    try:
        read_back = ObservationRows("source", params).run(None, {})["records"]
    finally:
        os.chdir(here)
    assert {tuple(r[k] for k in FE_IDENTITY) for r in read_back} == {
        tuple(r[k] for k in FE_IDENTITY) for r in out} and len(read_back) == len(out)
    assert all(r["quote_date_ms"] == _ms(r["quote_date"]) for r in read_back)
    assert {k: v for k, v in sorted(read_back[0].items()) if k != "quote_date_ms"} == dict(
        sorted(next(o for o in out if tuple(o[k] for k in FE_IDENTITY) == tuple(
            read_back[0][k] for k in FE_IDENTITY)).items()))      # nulls stay null
    _must(_cli(["dskit.pipeline", "run", str(child_root/STEP2), "--asof", "2026-10-01"],
               tmp_path, env))
    flags = _read_jsonl(tmp_path/"pipeline_runs/step2-feature-availability/dates.jsonl")
    assert len(flags) == len(out)
    for row in flags:
        for family in ("positioning_changes", "liquidity", "chain_nodes"):   # each holds a withheld field
            assert row[f"available_{family}"] == "no", family
        assert row["available_variance_gap"] == row["available_ohlc_shape"] == "yes"
        assert row["available_implied_cdf"] == "yes"
    assert {r["available_expanded_volatility_context"] for r in flags} == {"yes", "no"}
    assert pd.Series([r["quote_date"] for r in flags]).isin(days).all()


# -- steps 3-6: the owner-ruling values, pinned (ADR-0222..0224) ---------------------------------
#
# The documents are JSON over existing kinds, so the only defence against a silent edit of a ruling
# (tau, the holdout share, the window counts, the baselines, the losses, the exit rule) is a test
# that restates each value here, independently of the document. The tests below also build a filled
# copy of steps 4-6 (what the owner does after the real runs) and run step 3 on fixture sources.

STEP3 = "configs/run-step3-holdout-folds.json"
STEP2_DATES_SOURCE = "configs/source-step2-dates.json"
STEPS_4_TO_6 = ["configs/run-step4-feature-selection.json", "configs/run-step5-model-zoo.json",
                "configs/run-step6-hpo.json"]
WING_LOSSES = [{"kind": "nll", "weight": 0.1}, {"kind": "wing_twcrps", "weight": 1.0}]
RULING_DTE = 7
#: Step 3's window counts (owner rulings), restated independently of the document.
RULING_PLAN = {"train_n": 450, "val_n": 40, "step_n": 40, "warmup_folds": 4}


def _no_notes(value):
    """A JSON value with every ``notes`` key removed (documentation, outside the identity hash)."""
    if isinstance(value, dict):
        return {k: _no_notes(v) for k, v in value.items() if k != "notes"}
    if isinstance(value, list):
        return [_no_notes(v) for v in value]
    return value


def test_step3_document_validates_and_plans(child_root):
    for verb in ("validate", "plan"):
        done = _must(_cli(["dskit.pipeline", verb, str(child_root/STEP3)], child_root))
    planned = json.loads(done.stdout)
    assert {"step2_dates", "source", "plan__qqq", "fold_table__qqq", "fold_evidence",
            "admission_evidence"} <= set(planned["order"])
    assert all(planned["nodes"][key]["class"] for key in planned["order"])


def test_step3_pins_the_owner_rulings(child_root):
    doc = _step(child_root, STEP3)
    each = doc["foreach"]["pipeline"]
    assert doc["foreach"]["keys"] == ["QQQ"]                         # one ticker per run
    assert doc["pipeline"]["tau"]["params"]["tables"] == {"admission": {"tau": 0.9}}
    cut = each["cut"]
    assert cut["uses"] == "holdout-cut" and cut["params"]["fraction"] == 0.2
    assert set(cut["params"]) == {"date_field", "end_field", "fraction"}      # no typed cut date
    plan = each["plan"]["params"]
    assert {k: plan[k] for k in RULING_PLAN} == RULING_PLAN
    # wired, never typed: the embargo is the selected DTE, the holdout is the cut's own metric
    assert plan["embargo_days"] == "$dte.table.all"
    assert plan["holdout_start"] == "$cut.metrics.holdout_start"
    assert set(plan) == {"date_field", "end_field", "holdout_start", "embargo_days", *RULING_PLAN}
    assert each["dte"]["params"]["value"] == "actual_calendar_dte"
    # tau is read by every admission, in BOTH outcomes, and every family has a training clause
    for family in FAMILIES:
        admit = each[f"admit_{family}"]["params"]
        assert admit["field"] == f"required_{family}"
        assert [(c["when"][0]["op"], c["when"][0]["value"], c["value"]) for c in admit["cases"]] == [
            (">=", "$tau.merged.tau", 1), ("<", "$tau.merged.tau", 0)]
    clauses = {c["field"]: c for c in each["training"]["params"]["where"]}
    assert set(clauses) == {f"has_{f}" for f in FAMILIES}
    assert all(c["op"] == ">=" and c["value"] == f"$required_{f}.table.all"
               for f in FAMILIES for c in [clauses[f"has_{f}"]])
    assert each["training"]["inputs"] == {"records": "$has_chain_nodes.records"}
    assert each["plan"]["inputs"] == {"records": "$training.records"}


def test_step2_dates_source_agrees_with_step2s_writer_and_step3s_reader(child_root):
    """Step 3 imports exactly the file step 2 writes: directory, stream, format, key, clock."""
    import posixpath

    from dskit.onboarding.connector import check_config
    from dskit.onboarding.libs.localtables import LocalTablesConnector

    two, three = _step(child_root, STEP2), _step(child_root, STEP3)
    source = _step(child_root, STEP2_DATES_SOURCE)
    written = two["pipeline"]["date_evidence"]["params"]["path"]
    assert posixpath.normpath(posixpath.dirname(written)) == posixpath.normpath(source["path"])
    stem, suffix = posixpath.basename(written).rsplit(".", 1)
    assert source["streams"] == [stem] == [three["pipeline"]["step2_dates"]["params"]["stream"]]
    assert source["formats"] == [suffix]
    patterns = two["foreach"]["pipeline"]["date_patterns"]["params"]["keys"]
    assert source["effective_field"] == "quote_date" and "quote_date" in patterns
    assert source["effective_unit"] == "iso"
    reader = three["pipeline"]["step2_dates"]["params"]
    assert reader["ts_field"] == "quote_date" and reader["ts_unit"] == "iso"
    assert reader["key_fields"] == ["symbol", "quote_date"] and set(reader["key_fields"]) <= set(
        patterns)
    assert reader["source"] == "step2-dates" and reader["root"] == "./pipeline_runs/step2-dates-source"
    # every flag step 3 reads is one step 2 writes, and the two documents list the same families
    each = three["foreach"]["pipeline"]
    flags = {f"available_{f}" for f in FAMILIES}
    assert flags <= set(patterns)
    assert {each[f"has_{f}"]["params"]["cases"][0]["when"][0]["field"] for f in FAMILIES} == flags
    assert {c["when"][0]["value"] for f in FAMILIES for c in each[f"has_{f}"]["params"]["cases"]
            } == {"yes", "no"}
    check_config(LocalTablesConnector(), source)


def _step3_fixture(child_root, cwd):
    """Step 2's dates and the panel for 1,400 weekdays of one ticker; designed availability.

    ``liquidity`` is present on half the dates (rejected at tau 0.9), ``macro_context`` is missing
    on every twentieth date (95%, admitted) and every other family is always present.
    """
    days, day = [], date(2016, 1, 4)
    while len(days) < 1400:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    missing = {days[i].isoformat() for i in range(len(days)) if i % 20 == 3}
    dates = []
    for i, day in enumerate(days):
        row = {"symbol": "QQQ", "selection_group": "all", "actual_calendar_dte": RULING_DTE,
               "quote_date": day.isoformat(), "cohort_rows": 1}
        row.update({f"available_{f}": "yes" for f in FAMILIES})
        row["available_liquidity"] = "yes" if i % 2 == 0 else "no"
        row["available_macro_context"] = "no" if day.isoformat() in missing else "yes"
        dates.append(row)
    panel = [{"symbol": "QQQ", "quote_date": d.isoformat(), "actual_calendar_dte": RULING_DTE,
              "expiry": (d + timedelta(days=RULING_DTE)).isoformat(),
              "settlement_date": (d + timedelta(days=RULING_DTE)).isoformat(),
              "terminal_return": ((i * 7) % 11 - 5) / 500} for i, d in enumerate(days)]
    _write_jsonl(cwd/"pipeline_runs/step2-feature-availability/dates.jsonl", dates)
    # step 2's other stream in the same directory must stay out of the import
    _write_jsonl(cwd/"pipeline_runs/step2-feature-availability/combinations.jsonl",
                 [{"symbol": "QQQ", "combination": "decoy"}])
    _write_jsonl(cwd/"pipeline_runs/panel-data/input_panel.jsonl", panel)
    return [d.isoformat() for d in days], missing


def test_step3_end_to_end_on_fixture_sources(child_root, tmp_path):
    env = _fe_env(child_root)
    days, missing = _step3_fixture(child_root, tmp_path)
    _onboard(tmp_path, "step2-dates", "./pipeline_runs/step2-dates-source",
             "@"+str(child_root/STEP2_DATES_SOURCE), "dates")
    _onboard(tmp_path, "cdf-horizon-panel", SOURCE_ROOT, json.dumps({
        "path": "pipeline_runs/panel-data", "layout": "file", "effective_field": "quote_date",
        "effective_unit": "iso", "streams": ["input_panel"], "formats": ["jsonl"]}), "input_panel")
    (tmp_path/"pipeline_runs/step3-holdout-folds").mkdir(parents=True)
    # step 3 ships reading the onboarding store; the fixture panel is onboarded under tmp_path
    step3, changed = _fe_copy(child_root, STEP3, tmp_path, lambda d: d["pipeline"]["source"][
        "params"].update(root=SOURCE_ROOT))
    assert changed == {"/pipeline/source/params/root"}
    _must(_cli(["dskit.pipeline", "run", str(step3), "--asof", "2026-10-01"], tmp_path, env))
    out = tmp_path/"pipeline_runs/step3-holdout-folds"
    folds = _read_jsonl(out/"fold-table.jsonl")
    admission, = _read_jsonl(out/"admission-and-metrics.jsonl")
    # the holdout is the last 20% of the dates, locked first and never inside a fold
    holdout_start = days[len(days) - len(days)//5]
    assert admission["holdout"]["holdout_start"] == holdout_start
    assert admission["holdout"]["holdout_dates"] == len(days)//5 == 280
    assert all(f["val_end"] < holdout_start for f in folds)
    # tau 0.9 admits macro_context (95%) and refuses liquidity (50%); the rest are always present
    assert admission["required_liquidity"] == 0 and 0.45 < admission["rate_liquidity"] < 0.55
    assert admission["required_macro_context"] == 1
    assert [admission[f"required_{f}"] for f in FAMILIES if f != "liquidity"] == [1] * 12
    # sized by counts: 450 train and 40 validation complete-case dates, four warm-up folds first
    assert len(folds) == admission["plan"]["folds"] >= 5
    assert [f["role"] for f in folds[:4]] == ["warmup"] * 4
    assert {f["role"] for f in folds[4:]} == {"scored"} and admission["plan"]["scored"] == len(
        folds) - 4
    assert {(f["train_dates"], f["val_dates"]) for f in folds} == {(450, 40)}
    assert {f["symbol"] for f in folds} == {"QQQ"}
    assert admission["plan"]["scored_start"] == folds[4]["val_start"]
    # OWNER DECISION 2026-10-02 (option A): imputation is accepted. The counts are complete-case,
    # but a fold only carries boundary dates, so its window spans the dates missing macro_context
    # as well (the study median-imputes them): no training-dates file, no complete-case filter.
    assert not (out/"training-dates.jsonl").exists()
    for fold in folds:
        for edge, count in (("train", 450), ("val", 40)):
            span = [d for d in days if fold[f"{edge}_start"] <= d <= fold[f"{edge}_end"]]
            assert len([d for d in span if d not in missing]) == count
            assert len(span) > count
    # the unrelated stream never reached step 3
    assert not any("decoy" in line for line in (out/"admission-and-metrics.jsonl").read_text()
                   .splitlines())


def _studies(child_root):
    return {name: _step(child_root, name) for name in STEPS_4_TO_6}


def test_steps_4_to_6_share_one_data_and_study_block(child_root):
    docs = [_no_notes(d) for d in _studies(child_root).values()]
    for block in ("data", "diagnostic"):
        assert docs[0][block] == docs[1][block] == docs[2][block], block
    outputs = [d["study"].pop("output") for d in docs]
    assert len(set(outputs)) == 3                    # each step writes its own tree
    assert docs[0]["study"] == docs[1]["study"] == docs[2]["study"]


@pytest.mark.parametrize("name", STEPS_4_TO_6)
def test_steps_4_to_6_pin_the_owner_rulings(child_root, name):
    doc = _step(child_root, name)
    study, experiment, data = doc["study"], doc["experiment"], doc["data"]
    assert data["symbols"] == {"QQQ": "VXN"} and data["exact_dte"] == RULING_DTE
    assert experiment["expected_cells"] == {part: {"QQQ": [RULING_DTE]}
                                            for part in ("development", "evaluation")}
    assert experiment["selection_metric"] == "decision_wing_twcrps" and study["wing_metrics"] is True
    assert study["fold_table"]["roles"] == ["warmup", "scored"]     # the holdout is never a role
    assert study["fold_table"]["cal_n"] == RULING_PLAN["val_n"]
    # both baselines, in every step: signal = beat the empirical CDF, market edge = beat the proxy
    assert set(study["models"]) == {"horizon_empirical", "option_proxy_transport"}
    assert study["reference_model"] == "horizon_empirical"
    assert study["comparison_references"] == ["horizon_empirical", "option_proxy_transport"]
    assert study["models"]["horizon_empirical"]["class"].endswith(":HorizonEmpiricalCDF")
    assert study["models"]["option_proxy_transport"]["class"].endswith(
        ":OptionImpliedTransportCDF")
    # the baselines' column positions name the columns the notes say they do
    features = study["features"]
    empirical = study["models"]["horizon_empirical"]["params"]
    proxy = study["models"]["option_proxy_transport"]["params"]
    assert features[empirical["horizon_index"]] == "calendar_dte"
    assert features[empirical["reference_index"]] == features[proxy["reference_index"]] == study[
        "reference"]
    assert [features[i] for i in proxy["proxy_indices"]] == [
        f"rn_q_{round(p * 10000):04d}" for p in proxy["probabilities"]]
    assert features[proxy["eligible_index"]] == "rn_proxy_eligible"
    # the training recipe of every Torch candidate: patience is the only exit, epochs a guard
    # (patience 6, lowered from 20 on 2026-10-02 to bound a round's run time)
    assert len(experiment["candidates"]) == experiment["max_candidates"]
    for label, candidate in experiment["candidates"].items():
        params = candidate["params"]
        assert candidate["class"] == "dskit.pipeline.libs.predictive_cdf:TorchCDF", label
        assert params["losses"] == WING_LOSSES, label
        assert params["patience"] == 6 and params["epochs"] == 2000, label
        assert candidate.get("calibrate") is False, label


def test_step4_is_forward_step_one_over_admitted_families_without_open_interest(child_root):
    doc = _step(child_root, STEPS_4_TO_6[0])
    features, candidates = doc["study"]["features"], doc["experiment"]["candidates"]
    names = lambda label: [features[i] for i in candidates[label]["params"]["feature_indices"]]  # noqa: E731
    # The file is edited in place round by round: a round is the incumbent "core" (earlier
    # rounds' winners folded in) and core + ONE of the admitted families not yet in it. It now
    # ships round 4, so the candidates are a subset of the admitted families, not all of them.
    admitted = [f for f in FAMILIES if f not in ("return_history", "realized_volatility")]
    labels = list(candidates)
    assert labels[0] == "core" and len(labels) > 1 and len(set(labels)) == len(labels)
    assert all(label.startswith("core+") and label[len("core+"):] in admitted
               for label in labels[1:])
    group, = doc["experiment"]["candidate_groups"]
    assert re.fullmatch(r"forward_[1-9]\d*", group)
    assert doc["experiment"]["candidate_groups"] == {group: labels}
    assert doc["experiment"]["search_partitions"] == doc["experiment"]["candidate_groups"]
    core = names("core")
    for label in candidates:
        assert set(core) <= set(names(label)) and not set(names(label)) & set(FE_OI_ALL), label
        assert (set(names(label)) == set(core)) == (label == "core"), label   # adds a family


def _filled_fold_table(child_root, tmp_path):
    """A real fold table (the step-3 plan on 1,000 fixture dates), its sha256 and holdout_start."""
    import hashlib

    from dskit.pipeline.kinds_split import RollingOriginPlan

    wired = _step(child_root, STEP3)["foreach"]["pipeline"]["plan"]["params"]
    days, day = [], date(2015, 1, 5)
    while len(days) < 1000:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    rows = [{"quote_date": d.isoformat(), "settlement_date": (d + timedelta(days=RULING_DTE)
                                                              ).isoformat()} for d in days]
    holdout = (days[-1] + timedelta(days=30)).isoformat()
    plan = RollingOriginPlan("plan", {
        "date_field": "quote_date", "end_field": "settlement_date", "holdout_start": holdout,
        "embargo_days": RULING_DTE, **{k: wired[k] for k in RULING_PLAN}}).run(
            None, {"records": rows})
    table = tmp_path/"fold-table.jsonl"
    table.write_text("".join(json.dumps(dict(r, symbol="QQQ"), sort_keys=True,
                                        separators=(",", ":")) + "\n" for r in plan["records"]))
    return str(table), hashlib.sha256(table.read_bytes()).hexdigest(), holdout


def _fill(doc, table, sha, holdout, step4_core):
    """What the owner does after the real runs: pin the fold table, then the step-4 winner."""
    doc = json.loads(json.dumps(doc))
    doc["study"]["fold_table"].update(path=table, sha256=sha, holdout_start=holdout)
    features = doc["study"]["features"]
    names = [features[i] for i in step4_core]
    steps = [names.index(f"ret_lag_{k}") for k in range(21, -1, -1)]       # oldest step first
    context = [i for i in range(len(names)) if i not in steps]
    for candidate in doc["experiment"]["candidates"].values():
        params = candidate["params"]
        if params["feature_indices"] == []:                  # steps 5-6 ship it unfilled
            params["feature_indices"] = list(step4_core)
            if params["encoder"]["kind"] == "gru":
                params["encoder"].update(sequence_indices=[[i] for i in steps],
                                         context_indices=context)
    return doc


@pytest.mark.parametrize("name", STEPS_4_TO_6)
def test_steps_4_to_6_refuse_the_shipped_copy_and_accept_a_filled_one(child_root, tmp_path, name):
    pytest.importorskip("pandas")
    pytest.importorskip("torch")
    from dskit.pipeline.libs.predictive_cdf import CDFHyperparameterStudy

    docs = _studies(child_root)
    shipped = docs[name]
    table, sha, holdout = _filled_fold_table(child_root, tmp_path)
    core = docs[STEPS_4_TO_6[0]]["experiment"]["candidates"]["core"]["params"]["feature_indices"]
    # The owner has filled the shipped copies (step 3's fold-table sha256 and holdout_start, and
    # in steps 5-6 the step-4 winner's feature_indices): nothing is left to replace ...
    assert "REPLACE_WITH" not in json.dumps(shipped)
    assert all(c["params"]["feature_indices"] for c in shipped["experiment"]["candidates"].values())
    # ... so the placeholders the shipped copy used to carry are restored on a copy, and they
    # still refuse by design
    placeholder = json.loads(json.dumps(shipped))
    placeholder["study"]["fold_table"].update(sha256="REPLACE_WITH_STEP3_FOLD_TABLE_SHA256",
                                              holdout_start="REPLACE_WITH_STEP3_HOLDOUT_START")
    with pytest.raises(ValueError, match="invalid fold_table"):
        CDFHyperparameterStudy(placeholder)
    filled = _fill(shipped, table, sha, holdout, core)
    study = CDFHyperparameterStudy(filled)
    assert study.plan.warmup_ids and study.plan.scored_ids
    assert study.plan.holdout == holdout
    if name != STEPS_4_TO_6[0]:
        # the fold table alone is not enough: an unfilled feature subset is refused, never run
        unfilled = json.loads(json.dumps(filled))
        for candidate in unfilled["experiment"]["candidates"].values():
            candidate["params"]["feature_indices"] = []
        with pytest.raises(ValueError, match="invalid feature indices"):
            CDFHyperparameterStudy(unfilled)
    tampered = json.loads(json.dumps(filled))
    tampered["study"]["fold_table"]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="sha256 mismatch"):
        CDFHyperparameterStudy(tampered)


# -- ADR-0236/0237: the pooled zoo over both stock universes, generated by pooled.py ------------

from index_options import pooled  # noqa: E402

ZOO_LOSSES = [{"kind": "crps", "weight": 1.0}, {"kind": "tail_crps", "weight": 1.0}]


def _pooled(child_root):
    return json.loads((child_root/"configs"/pooled.STUDY_FILE).read_text())


def test_the_pooled_zoo_documents_equal_their_generator(child_root):
    configs = child_root/"configs"
    shipped = _pooled(child_root)
    assert pooled.zoo_document(configs, pooled.measured(configs, shipped)) == shipped
    folds = json.loads((configs/pooled.FOLDS_FILE).read_text())
    assert pooled.folds_document(configs) == folds
    # The folds graph is the template's, moved to this study's run directory only.
    template = json.loads((configs/pooled.TEMPLATE_FOLDS_FILE).read_text())
    assert folds["pipeline"].keys() == template["pipeline"].keys()
    assert shipped["study"]["fold_table"]["path"] == (
        f"pipeline_runs/{pooled.NAME}/fold-table-pooled-h31.jsonl")


def test_the_pooled_zoo_reads_each_ticker_from_its_own_universe_sources(child_root):
    from index_options.cdf_study import panel_reader_problems

    configs = child_root/"configs"
    doc = _pooled(child_root)
    first = json.loads((configs/"stock_universe.json").read_text())["tickers"]
    second = json.loads((configs/"option_universe_300.json").read_text())["tickers"]
    # Measured 2026-10-04: HES has no bars; eight recent listings have no panel row before
    # the holdout (smoke panel, symbols_without_rows).
    refused = pooled.measured(configs, doc)["refused"]
    assert refused == ["HES", "SPCX", "PURR", "CBRS", "FPS", "INFQ", "GLND", "FRVO", "XE"]
    assert list(doc["data"]["symbols"]) == [t for t in first + second if t not in refused]
    source = doc["data"]["price_source"]
    assert source["source"] == "stock-daily-bars"
    assert all(isinstance(source["relpath"][t], str) for t in first if t not in refused)
    assert all(source["relpath"][t] == {"source": "stock-daily-bars-300", "stream": "files",
                                        "relpath": f"{t.lower()}/underlying_prices.parquet"}
               for t in second if t not in refused)
    for table in doc["data"]["keyed_tables"]["tables"].values():
        assert "source" not in table
        assert [p["source"].rsplit("-", 1)[-1] for p in table["parts"]] == ["r2", "300"]
    windows = doc["data"]["corporate_actions"]["windows"]
    assert windows.keys() == doc["data"]["symbols"].keys() and windows["DHR"]
    assert panel_reader_problems(doc["data"]) == []
    cells = doc["experiment"]["expected_cells"]
    assert set(cells["evaluation"]) <= set(doc["data"]["symbols"])
    assert set(cells["development"]) <= set(cells["evaluation"]) | set(doc["data"]["symbols"])
    # Owner option B (2026-10-04): a ticker enters a fold once its fit band holds 40 rows.
    # Measured on the smoke panel under that rule: 375 development and 393 evaluation cells
    # (15 recent listings never reach 40 fit rows before the last scored fold).
    assert doc["study"]["min_task_fit_rows"] == 40
    assert (len(cells["development"]), len(cells["evaluation"])) == (375, 393)


def test_every_pooled_candidate_trains_on_the_selection_metric_with_ticker_heads(child_root):
    import collections
    import importlib

    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

    doc = _pooled(child_root)
    e, c = doc["experiment"], doc["study"]
    assert e["selection_metric"] == "weighted_crps" and c["tail_weight"] == 1.0
    heads = [c["features"].index(f"is_{t}") for t in doc["data"]["symbols"]]
    assert e["task_features"] == {t: f"is_{t}" for t in doc["data"]["symbols"]}
    assert collections.Counter(n.split("_")[0] for n in e["candidates"]) == {
        "mlp": 18, "gru": 6, "lstm": 6, "cnn": 6, "lgbm": 8}
    assert e["max_candidates"] == 44 and e["search_partitions"] == e["candidate_groups"]
    lags = [f"ret_lag_{i}" for i in range(21, -1, -1)]
    for name, spec in e["candidates"].items():
        p = spec["params"]
        assert p["losses"] == ZOO_LOSSES and p["head_features"] == heads, name
        assert not set(heads) & set(p["feature_indices"]), name
        assert spec["pooled"] is True and spec["calibrate"] is False
        picked = [c["features"][i] for i in p["feature_indices"]]
        encoder = p.get("encoder", {})
        if encoder.get("kind") in ("gru", "lstm", "cnn"):
            assert [picked[row[0]] for row in encoder["sequence_indices"]] == lags, name
        if name.startswith("lgbm"):
            assert (p["hessian"], p["device"], p["patience"]) == ("constant", "cuda", 20)
        module, cls = spec["class"].split(":")
        getattr(importlib.import_module(module), cls)(**p)   # every knob validates
    ChronologicalCDFStudy._pin_tail_weight({**c, "models": e["candidates"]})


def test_the_shipped_pooled_document_constructs_its_study(child_root, monkeypatch):
    # The fold table is a run output (not in git): stand in a valid two-fold table for the
    # pinned file, so the rest of the document is validated as the CLI would.
    from dskit.pipeline.libs import predictive_cdf

    folds = [{"fold": 1, "role": "warmup", "train_start": "2016-02-05",
              "train_end": "2023-07-28", "val_start": "2023-08-29", "val_end": "2023-11-10"},
             {"fold": 2, "role": "scored", "train_start": "2016-02-05",
              "train_end": "2024-01-05", "val_start": "2024-02-06", "val_end": "2024-04-22"}]
    monkeypatch.setattr(predictive_cdf._FoldPlan, "_read", staticmethod(lambda spec: folds))
    study = predictive_cdf.CDFHyperparameterStudy(_pooled(child_root))
    assert study.plan.min_task_fit_rows == 40 and study.plan.admits
    assert list(study.partitions) == ["development", "later"]


def test_the_pooled_workflow_and_report_spec_equal_their_generator_and_validate(child_root):
    import os

    from dskit.pipeline import workflow as wf

    configs = child_root/"configs"
    study = _pooled(child_root)
    manifest = json.loads((configs/pooled.WORKFLOW_FILE).read_text())
    spec = json.loads((configs/pooled.REPORT_SPEC_FILE).read_text())
    assert manifest == pooled.workflow_document(configs)
    assert spec == pooled.report_spec_document(study)
    flow = wf.validate_manifest(json.loads(json.dumps(manifest)), str(configs))
    # Restated on purpose (owner, 2026-10-04): the zoo builds the panel, searches every
    # partition and selects; step 7 is the per-ticker workflow's evaluate stages.
    assert manifest["args"]["zoo"]["stages"]["sequence"] == [
        {"stage": "panel"}, {"stage": "search", "partition": "*"}, {"stage": "select"}]
    assert manifest["args"]["evaluate"]["stages"]["sequence"] == [
        {"stage": "evaluate", "partition": "development"},
        {"stage": "evaluate", "partition": "later"}, {"stage": "report"}]
    source = json.loads((configs/"workflow.json").read_text())["args"]
    assert manifest["args"]["evaluate"]["stages"] == source["evaluate"]["stages"]
    assert manifest["args"]["run_env"] == source["run_env"]
    output = os.path.normpath(study["experiment"]["output"])
    for step, out, tail in (("zoo", "search", "search"), ("zoo", "selection", "selection"),
                            ("step7", "development", "evaluate/development"),
                            ("step7", "scored", "evaluate/later"), ("step7", "report", "report")):
        assert os.path.normpath(flow.path(step, out)) == os.path.join(output, tail)


def _tiny_pooled_run(work, metric):
    """Write the files the study names, for one ticker, under a fake run directory."""
    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

    def put(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    study, admission = work/"study", ChronologicalCDFStudy.ADMISSION_FILE
    put(study/"selection/candidates.jsonl", json.dumps(
        {"partition": "mlp_base", "candidate": "m", "variant": "raw", "n_features": 2,
         "feature_names": ["a", "b"], "warmup_mean_skill": 1.0, "guard_pass": None,
         "winner": True})+"\n")
    put(study/"selection/selected.json", json.dumps({"variants": {"m": "raw"}}))
    for part in ("development", "later"):
        put(study/"evaluate"/part/admission, json.dumps({
            "dropped_rows": {"fit": 3, "calibration": 1, "evaluation": 2}, "not_evaluated": [
                {"group": "LATE", "fold": 1, "reason": "waiting", "fit_rows": 3,
                 "calibration_rows": 1, "evaluation_rows": 2, "dropped_fit_rows": 3,
                 "dropped_calibration_rows": 1, "dropped_evaluation_rows": 2}]}))
        put(study/"evaluate"/part/"complete.json", json.dumps({"identity": {"config": "c"}}))
    intervals = [{"metric": m, "model": "m", "reference": "horizon_empirical",
                  "block_dates": 30, "lo": 0.1, "hi": 0.9} for m in ("crps", metric)]
    put(study/"report/comparison.json", json.dumps({
        "development_relative_primary": {"m:raw": 0.98}, "metrics": [{"model": "m"}],
        "paired_block_intervals": intervals}))
    put(study/"report/skill_by_exact_day.csv",
        "symbol,actual_calendar_dte,skill,metric,model,n\n"
        f"AAA,31,1.5,crps,m,9\nAAA,31,2.5,{metric},m,9\n")
    put(work/"folds.jsonl", json.dumps({"fold": 1, "role": "warmup"})+"\n")


def test_the_pooled_report_builds_from_a_tiny_run(child_root, tmp_path):
    from dskit.pipeline import workflow as wf
    from dskit.pipeline import workflow_report as wr

    study = _pooled(child_root)
    metric = study["experiment"]["selection_metric"]
    work = tmp_path/"work"
    _tiny_pooled_run(work, metric)
    template = pooled.report_spec_document(study)
    for section in template["sections"]:
        if section["name"] == "folds":   # the pinned fold table, read from its own path
            section["source"] = str(work/"folds.jsonl")
    outs = {"selection": work/"study/selection", "development": work/"study/evaluate/development",
            "scored": work/"study/evaluate/later", "report": work/"study/report"}
    spec = wf.expand(template, {
        "W": str(work), "selection": str(outs["selection"]),
        "development": str(outs["development"]), "scored": str(outs["scored"]),
        "evaluation": str(outs["report"]), "L": {"report": {"output": str(work/"out")}}})
    (work/wf.LEDGER_NAME).write_text(json.dumps({"steps": {
        "zoo": {"exit_code": 0, "outputs": {"selection": wf.path_hash(str(outs["selection"]))}},
        "step7": {"exit_code": 0, "outputs": {k: wf.path_hash(str(outs[k]))
                                              for k in ("development", "scored", "report")}}}}))
    path = tmp_path/"spec.json"
    path.write_text(json.dumps(spec))
    lines = []
    assert wr.main([str(path)], out=lines.append) == 0, lines
    sections = work/"out"/"sections"
    assert {p.stem for p in sections.glob("*.csv")} == {s["name"] for s in template["sections"]}
    ticker = (sections/"per_ticker_skill.csv").read_text().splitlines()
    assert ticker == ["symbol,actual_calendar_dte,skill,metric,model,n", f"AAA,31,2.5,{metric},m,9"]
    assert "crps," not in (sections/"scored_intervals.csv").read_text().replace(metric, "")


def test_the_pooled_generator_refuses_a_refused_ticker_in_no_universe(child_root):
    shipped = _pooled(child_root)
    values = pooled.measured(child_root/"configs", shipped)
    with pytest.raises(ValueError, match="in no universe"):
        pooled.zoo_document(child_root/"configs", {**values, "refused": ["HES", "ZZZZ"]})


# -- ADR-0236 amendment 3 (owner request 2026-10-05): the same zoo, fitted once per ticker ------

#: Measured 2026-10-05: after every acquisition the panel's observation reads see (the newest,
#: cboe-index-wide, 2026-10-02T14:31Z), before the recorder's next (2026-10-05T14:30Z).
PER_TICKER_VINTAGE = 1791198000000   # 2026-10-05T11:00:00Z


def _perticker(child_root):
    return json.loads((child_root/"configs"/pooled.PER_TICKER.study_file).read_text())


def test_the_perticker_documents_equal_their_generator(child_root):
    configs, variant = child_root/"configs", pooled.PER_TICKER
    shipped = _perticker(child_root)
    assert pooled.zoo_document(configs, pooled.measured(configs, shipped), variant) == shipped
    manifest = json.loads((configs/variant.workflow_file).read_text())
    assert manifest == pooled.workflow_document(configs, variant)
    spec = json.loads((configs/variant.report_spec_file).read_text())
    assert spec == pooled.report_spec_document(shipped, variant)
    assert variant.folds_file is None and variant.fold_document == pooled.FOLDS_FILE
    assert shipped["data"]["as_of_acquisition_ms"] == PER_TICKER_VINTAGE
    # Review round 1 (M1): the manifest's notes name its own file, never the pooled one.
    assert f"configs/{variant.workflow_file} " in manifest["notes"]
    assert pooled.WORKFLOW_FILE not in manifest["notes"]
    # Review round 2: the vintage is explained where it is declared, and never omitted.
    assert f"as_of_acquisition_ms {PER_TICKER_VINTAGE} (epoch ms" in shipped["data"]["notes"]
    values = pooled.measured(configs, shipped)
    del values["as_of_acquisition_ms"]
    with pytest.raises(ValueError, match="as_of_acquisition_ms"):
        pooled.zoo_document(configs, values, variant)


def test_the_perticker_zoo_is_the_pooled_zoo_fitted_once_per_ticker(child_root):
    import importlib

    pooled_doc, doc = _pooled(child_root), _perticker(child_root)
    # Same universe, data, folds (the pooled pin), cells, loss, selection and reference.
    data = {k: v for k, v in doc["data"].items() if k not in ("notes", "as_of_acquisition_ms")}
    assert data == {k: v for k, v in pooled_doc["data"].items() if k != "notes"}
    study = {k: v for k, v in doc["study"].items() if k not in ("output", "min_task_cal_rows")}
    assert study == {k: v for k, v in pooled_doc["study"].items() if k != "output"}
    assert doc["study"]["output"] == "pipeline_runs/perticker-zoo-417/study/unused"
    # Review round 1 (C1): every candidate early-stops on its ticker's own calibration rows,
    # so a ticker waits while that band is empty. Measured on the pooled panel: PBR's warm-up
    # band is empty, so development loses PBR (375 -> 374); evaluation keeps 393 (QXO and UMC
    # wait in one scored fold each).
    assert doc["study"]["min_task_cal_rows"] == 1
    e, pe = doc["experiment"], pooled_doc["experiment"]
    skip = ("candidates", "output", "notes", "expected_cells")
    assert {k: v for k, v in e.items() if k not in skip} == {
        k: v for k, v in pe.items() if k not in skip}
    cells, pooled_cells = e["expected_cells"], pe["expected_cells"]
    assert cells["evaluation"] == pooled_cells["evaluation"] and len(cells["evaluation"]) == 393
    assert cells["development"] == {t: h for t, h in pooled_cells["development"].items()
                                    if t != "PBR"}
    assert e["output"] == "pipeline_runs/perticker-zoo-417/study"
    heads = {doc["study"]["features"].index(f) for f in e["task_features"].values()}
    assert list(e["candidates"]) == list(pe["candidates"])
    for name, spec in e["candidates"].items():
        want = copy.deepcopy(pe["candidates"][name])
        del want["pooled"], want["params"]["head_features"]
        # Restated on purpose (per-ticker benchmark, 2026-10-05): batch 512 and CPU for the
        # networks; CPU, one thread and 20 rows a leaf for LightGBM.
        want["params"].update({"device": "cpu", "num_threads": 1, "min_data_in_leaf": 20,
                               "n_estimators": 10000}    # the ceiling only (smoke max 643)
                              if name.startswith("lgbm") else {"device": "cpu", "batch_size": 512})
        assert spec == want, name
        assert not heads & set(spec["params"]["feature_indices"]), name   # no is_<T> input
        module, cls = spec["class"].split(":")
        getattr(importlib.import_module(module), cls)(**spec["params"])   # every knob validates


def test_the_perticker_document_constructs_its_study_with_the_pooled_fold_table(
        child_root, monkeypatch):
    from dskit.pipeline.libs import predictive_cdf

    folds = [{"fold": 1, "role": "warmup", "train_start": "2016-02-05",
              "train_end": "2023-07-28", "val_start": "2023-08-29", "val_end": "2023-11-10"},
             {"fold": 2, "role": "scored", "train_start": "2016-02-05",
              "train_end": "2024-01-05", "val_start": "2024-02-06", "val_end": "2024-04-22"}]
    monkeypatch.setattr(predictive_cdf._FoldPlan, "_read", staticmethod(lambda spec: folds))
    doc = _perticker(child_root)
    assert doc["study"]["fold_table"] == _pooled(child_root)["study"]["fold_table"]
    study = predictive_cdf.CDFHyperparameterStudy(doc)
    assert study.plan.min_task_fit_rows == 40
    assert not any(s.get("pooled") for s in doc["experiment"]["candidates"].values())


def test_the_perticker_workflow_leaves_the_worker_width_to_the_machine(child_root):
    import os

    from dskit.pipeline import workflow as wf
    from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy

    configs, doc = child_root/"configs", _perticker(child_root)
    manifest = json.loads((configs/pooled.PER_TICKER.workflow_file).read_text())
    pooled_manifest = json.loads((configs/pooled.WORKFLOW_FILE).read_text())
    flow = wf.validate_manifest(json.loads(json.dumps(manifest)), str(configs))
    # Review round 1 (M4): the runner's ledger records run_env, so a width there would rerun
    # the steps when retuned; the width is set where the run is launched (ADR-0093).
    assert manifest["args"]["run_env"] == pooled_manifest["args"]["run_env"]
    assert ChronologicalCDFStudy.GROUP_WORKERS_ENV not in json.dumps(manifest)
    assert manifest["args"]["zoo"] == pooled_manifest["args"]["zoo"]
    assert manifest["args"]["evaluate"] == pooled_manifest["args"]["evaluate"]
    output = os.path.normpath(doc["experiment"]["output"])
    for step, out, tail in (("zoo", "search", "search"), ("zoo", "selection", "selection"),
                            ("step7", "development", "evaluate/development"),
                            ("step7", "scored", "evaluate/later"), ("step7", "report", "report")):
        assert os.path.normpath(flow.path(step, out)) == os.path.join(output, tail)

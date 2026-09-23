"""Shipped declarations resolve through the public framework interfaces."""

import json

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
        "configs/source-fixture.json", "configs/suite-fixture.json", "configs/run-fixture.json",
        "configs/run-synthetic-distribution.json",
        "fixtures/contracts.jsonl", "fixtures/quotes.jsonl", "fixtures/settlements.jsonl",
        "docs/decisioning/actions.csv", "docs/decisioning/path.csv", "docs/decisioning/README.md",
        "docs/explanations/README.md", "docs/plans/README.md", "docs/memos/README.md",
        "docs/research/README.md", "docs/research/.gitkeep",
        "docs/research/distribution-modeling/2026-09-23-approach.md",
        "docs/research/distribution-modeling/2026-09-23-evaluation.md",
        "docs/research/distribution-modeling/2026-09-23-model-ladder.md",
        "tests/conftest.py", "tests/test_contracts.py", "tests/test_observations.py",
        "tests/test_nodes.py", "tests/test_configs.py", "tests/test_integration.py",
        "tests/test_distribution.py", "tests/test_synthetic_distribution_run.py",
    }
    ignored = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".git",
               "build", "dist", "ob", "pipeline_runs", ".journal.lock"}
    actual = {p.relative_to(child_root).as_posix() for p in child_root.rglob("*")
              if p.is_file() and not any(part in ignored or part.endswith(".egg-info")
                                         for part in p.relative_to(child_root).parts)}
    assert actual == expected
    assert len(actual) == 37  # ADR-0167's 30 + ADR-0168's 4 + 3 journal research notes
    assert (child_root / "AGENTS.md").read_bytes() == (child_root / "CLAUDE.md").read_bytes()


def test_synthetic_distribution_config_pins_its_agreements(child_root):
    """sqrt(horizon) scales daily vol to the label; the embargo covers the label's reach."""
    import math

    from dskit.pipeline.synthetic_paths import _DAY_MS

    doc = json.loads((child_root / "configs/run-synthetic-distribution.json").read_text())
    horizon = doc["pipeline"]["labels"]["params"]["horizon"]
    model = doc["pipeline"]["model"]["params"]
    assert model["scale_multiplier"] == math.sqrt(horizon)
    assert model["scale_field"] in doc["pipeline"]["labels"]["params"]["carry_fields"]
    # the synthetic path steps one calendar day per row, so steps == days
    assert doc["walkforward"]["embargo_days"] * 86_400_000 > horizon * _DAY_MS

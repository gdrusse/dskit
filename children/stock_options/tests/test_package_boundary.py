"""Pin the approved ADR-0212 bootstrap and child boundary."""

from pathlib import Path
import ast
import tomllib

CHILD_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "journal.json",
    "docs/decisioning/README.md",
    "docs/decisioning/actions.csv",
    "docs/decisioning/path.csv",
    "docs/memos/.gitkeep",
    "docs/memos/README.md",
    "docs/plans/.gitkeep",
    "docs/plans/README.md",
    "docs/research/.gitkeep",
    "docs/research/README.md",
    "pyproject.toml",
    "stock_options/__init__.py",
    "tests/conftest.py",
    "tests/test_package_boundary.py",
    "configs/source-option-archives.json",
    "configs/source-underlying-history.json",
    "configs/run-prepare-option-panel.json",
    "configs/run-cdf-horizon-coverage.json",
    "configs/run-amzn-feature-availability.json",
}


def _files() -> set[str]:
    return {
        path.relative_to(CHILD_ROOT).as_posix()
        for path in CHILD_ROOT.rglob("*")
        if path.is_file()
        and "pipeline_runs" not in path.parts
        and "__pycache__" not in path.parts
        and ".pytest_cache" not in path.parts
        and path.name != ".journal.lock"
        and path.suffix != ".pyc"
    }


def test_exact_approved_bootstrap_and_conversion_manifest():
    assert _files() == EXPECTED_FILES


def test_package_is_empty_and_importable():
    import stock_options

    assert stock_options.__all__ == ()
    source = (CHILD_ROOT / "stock_options/__init__.py").read_text()
    body = ast.parse(source).body
    assert len(body) == 2
    assert isinstance(body[0], ast.Expr)
    assert isinstance(body[0].value, ast.Constant)
    assert isinstance(body[0].value.value, str)
    assert isinstance(body[1], ast.Assign)
    assert [target.id for target in body[1].targets if isinstance(target, ast.Name)] == [
        "__all__"
    ]
    assert isinstance(body[1].value, ast.Tuple) and body[1].value.elts == []


def test_packaging_is_standalone_and_only_depends_on_dskit():
    with (CHILD_ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    assert project["project"]["name"] == "stock-options"
    assert project["project"]["dependencies"] == ["dskit"]
    assert project["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "stock_options*"
    ]


def test_child_does_not_import_sibling_children():
    forbidden = {"children", "index_options", "intraday_equities", "intraday_poc", "pmquant"}
    offenders = []
    for path in (CHILD_ROOT / "stock_options").rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                if module.split(".", 1)[0] in forbidden:
                    offenders.append(f"{path.name}:{node.lineno}:{module}")
    assert offenders == []


def test_scope_documents_name_the_prediction_action_split():
    text = "\n".join(
        (CHILD_ROOT / name).read_text()
        for name in ("README.md", "AGENTS.md", "docs/plans/README.md")
    ).lower()
    assert "stock-specific" in text
    assert "option action set" in text
    assert "split-adjusted" in text


def test_stock_conversion_configs_use_shared_nodes_and_same_preparation():
    import json
    from dskit.pipeline.document import load_document

    prepared = json.loads((CHILD_ROOT/"configs/run-prepare-option-panel.json").read_text())
    selected = json.loads((CHILD_ROOT/"configs/run-cdf-horizon-coverage.json").read_text())
    assert prepared["pipeline"] == selected["pipeline"]
    assert selected["foreach"]["keys"] == ["AMZN"]
    assert selected["foreach"]["pipeline"]["cohort"]["inputs"]["records"] == "$panel.records"
    for filename in ("run-prepare-option-panel.json", "run-cdf-horizon-coverage.json"):
        document = load_document(CHILD_ROOT/"configs"/filename)
        assert document.name
    from dskit.pipeline.libs.predictive_cdf import ExpiryCloseLabels, OptionCDFPanel
    for name, cls in (("labels", ExpiryCloseLabels), ("panel", OptionCDFPanel)):
        assert cls.validate_params(prepared["pipeline"][name]["params"]) == []
        assert cls.validate_params(dict(prepared["pipeline"][name]["params"], typo=True))


def test_amzn_feature_gaps_share_interface_and_keep_selected_cohort():
    import json
    from dskit.pipeline.document import load_document
    from dskit.pipeline.kinds_flow import Derive, Filter

    path = CHILD_ROOT / "configs/run-amzn-feature-availability.json"
    assert path.exists(), "AMZN needs the same JSON feature-gap interface"
    amzn = json.loads(path.read_text())
    qqq = json.loads((CHILD_ROOT.parent / "index_options/configs/run-qqq-feature-availability.json").read_text())
    prepared = json.loads((CHILD_ROOT / "configs/run-prepare-option-panel.json").read_text())
    graph = amzn["pipeline"]
    assert all(graph[k] == v for k, v in prepared["pipeline"].items())
    assert amzn["foreach"] == qqq["foreach"]
    assert graph["family_contracts"] == qqq["pipeline"]["family_contracts"]
    for key, value in qqq["pipeline"].items():
        if key.startswith(("check_", "summary_", "family_gap", "all_")):
            if key == "family_gap_evidence":
                continue
            assert graph[key] == value
    probabilities = prepared["pipeline"]["panel"]["params"]["probabilities"]
    row = {f"rn_q_{round(p * 10000):04d}": p / 100 for p in probabilities}
    row.update(symbol="AMZN", quote_date="2024-04-04", expiry="2024-05-16",
               actual_calendar_dte=42, rn_proxy_eligible=1, terminal_return=.01)
    rows = [row, dict(row, actual_calendar_dte=30), dict(row, symbol="QQQ")]
    selected = Filter("cohort", graph["cohort"]["params"]).run(None, {"records": rows})["records"]
    assert selected == [row]
    for key, spec in graph.items():
        if key.startswith("check_"):
            selected = Derive(key, spec["params"]).run(None, {"records": selected})["records"]
    assert len(selected) == 1
    assert selected[0]["available_implied_cdf"] == 1
    assert selected[0]["available_return_history"] == 0
    assert selected[0]["available_all_families"] == 0
    assert graph["row_evidence"]["params"]["expect"] == 90
    assert graph["cohort"]["inputs"]["records"] == "$panel.records"
    for node, filename in (("row_evidence", "rows.jsonl"),
                           ("feature_gap_evidence", "feature-gaps.jsonl"),
                           ("family_gap_evidence", "family-gaps.jsonl")):
        assert graph[node]["params"]["path"] == "./pipeline_runs/amzn-feature-availability/" + filename
    assert load_document(path).name == "amzn-feature-availability"
    from dskit.pipeline.kinds_flow import Concat
    source = Concat("source_contract", graph["source_contract"]["params"]).run(None, {})
    assert source["merged"]["archive"]["expected_dates"] == 90

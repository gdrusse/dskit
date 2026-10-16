"""No-fit contracts for ADR-0269 pipeline preparation; all inputs are declarations."""
import json
from pathlib import Path

from dskit.pipeline.document import PipelineDocument
from dskit.pipeline.planner import plan
from dskit.pipeline.workflow import Workflow

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _read(name):
    return json.loads((CONFIGS / name).read_text())


def test_feature_projection_plans_without_opening_stores():
    doc = _read("run-theta-ml-features.json")
    assert set(plan(PipelineDocument.from_obj(doc)).order) == {
        "theta30", "theta90", "write30", "write90"}
    for name, dte in (("theta30", 30), ("theta90", 90)):
        source = doc["pipeline"][name]["params"]["sources"][0]
        assert source["snapshot"] == "0" * 64
        assert source["read_bounds"]["date"] == {
            "start": "2016-01-04", "end_before": "2026-01-01"}
        assert {"field": "target_dte", "op": "==", "value": dte} in source["where"]
        assert not set(source["columns"]) & {"implied_vol", "implied_volatility", "close"}


def test_panel_preserves_price_target_and_has_no_raw_price_reader():
    doc = _read("run-theta-ml-panel.json")
    node = doc["pipeline"]["panel"]
    assert node["uses"] == "dskit.pipeline.libs.parquet_panel:ParquetPanelAugment"
    params = node["params"]
    assert params["identity"] == ["symbol", "quote_date", "expiry"]
    assert params["base"]["source"] == "universe-model-inputs-20261008"
    assert params["base"]["read_bounds"] == {
        "quote_date": {"start": "2016-01-04", "end_before": "2026-01-01"},
        "settlement_date": {"end_before": "2026-01-01"}}
    assert params["key"] == ["symbol", "quote_date"]
    for table in params["tables"].values():
        assert table["strict_prior"] is True and table["max_age_days"] == 7
        assert table["sources"][0]["snapshot"] == "0" * 64
    clauses = doc["pipeline"]["development"]["params"]["where"]
    assert {"field": "settlement_date", "op": "<", "value": "2021-01-01"} in clauses
    assert {"field": "quote_date", "op": "<", "value": "2021-01-01"} in clauses


def test_candidate_packet_reuses_model_and_has_all_family_combinations():
    old = _read("universe-feature-selection-20261008.json")
    new = _read("theta-ml-initial-study.json")
    assert new["candidates"] == old["candidates"]
    assert new["capacity"] == old["capacity"]
    assert new["training"] == old["training"]
    assert new["arms"]["price_only"] == old["arms"]["base"]
    assert new["study"]["models"] == old["study"]["models"]
    assert len(new["arms"]) == 8
    assert new["panel_sha256"] == "0" * 64
    assert new["study"]["fold_table"]["sha256"] == "0" * 64
    for arm in new["arms"].values():
        assert arm["observed_columns"] == []  # same cohort; training-only imputation
        assert arm["features"][:42] == old["arms"]["base"]["features"]
        indices = [i for step in arm["sequence_indices"] for i in step] + arm["context_indices"]
        assert sorted(indices) == list(range(len(arm["features"])))
    assert new["date_upper_exclusive"] == new["end_upper_exclusive"] == "2021-01-01"


def test_calendar_is_retrospective_bounded_and_no_fit_authorized():
    from datetime import date
    doc = _read("theta-ml-calendar.json")
    assert doc["authorized_fit_attempts"] == 0
    assert doc["end_before"] == "2026-01-01"
    assert doc["seeds"] == [11, 29, 47] and doc["nominal_seed"] == 11
    assert len(doc["blocks"]) == 10
    assert doc["independent_final_test"] is None
    for block in doc["blocks"]:
        start, end, decision = [date.fromisoformat(block[k]) for k in
                                ("origin_start", "origin_end_before", "next_decision")]
        assert (end-start).days == 70 and (decision-end).days == 35
        assert block["status"] == "unopened_development"
    assert doc["blocks"][0]["origin_start"] == "2021-01-04"
    assert doc["blocks"][-1]["origin_end_before"] == "2023-10-16"


def test_workflow_has_no_automatic_adaptive_or_fit_command():
    doc = _read("workflow-theta-ml.json")
    flow = Workflow(doc, str(CONFIGS))
    assert not flow.check()
    assert list(doc["steps"]) == ["features", "panel"]
    commands = [entry["command"] for entry in doc["registry"].values()]
    assert all("-m dskit.pipeline run" in command for command in commands)
    assert all("cdf_experiment" not in command for command in commands)


def test_only_prior_quote_descriptors_are_proposed_not_expiry_substitution():
    doc = _read("theta-ml-feature-families.json")
    assert doc["end_before"] == "2026-01-01"
    assert doc["join"] == "strict_prior_symbol_date"
    assert doc["target"] == "terminal_return"
    assert doc["withheld"] == ["open_interest", "oi_change", "oi_put_call_ratio"]
    assert set(doc["families"]) == {"theta_volatility", "theta_skew", "theta_liquidity"}


def test_feature_document_runs_on_synthetic_parquet_without_model_fit(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from dskit.onboarding.artifacts import payload_files
    from dskit.pipeline.driver import run_document

    doc = _read("run-theta-ml-features.json")
    fields = doc["pipeline"]["theta30"]["params"]["sources"][0]["columns"]
    rows = []
    for dte, day, status, first in [(30, "2019-01-02", "ok", True),
                                  (90, "2019-01-02", "ok", True),
                                  (30, "2019-01-03", "no_rate", True),
                                  (30, "2019-01-04", "ok", False),
                                  (30, "2026-01-01", "ok", True)]:
        row = {field: 0.25 for field in fields}
        row.update(symbol="SYNTHETIC", date=day, expiration="2019-04-02",
                   target_dte=dte, status=status, after_first_quote=first)
        rows.append(row)
    archive = tmp_path / "archive"
    archive.mkdir()
    pq.write_table(pa.Table.from_pylist(rows), archive / "rows.parquet", row_group_size=1)
    ob = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = ob.registry()
    vid = registry.register("source_config", {
        "name": "theta-chain-panel", "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2025-12-31T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(ob, registry, "theta-chain-panel", "files", "backfill")
    pin = payload_files(ob.root, "theta-chain-panel", "files")["manifest_sha256"]
    for dte in (30, 90):
        source = doc["pipeline"][f"theta{dte}"]["params"]["sources"][0]
        source.update(root=ob.root, snapshot=pin)
        doc["pipeline"][f"write{dte}"]["params"]["path"] = str(tmp_path / f"theta{dte}.jsonl")
    doc["outputs"]["run_root"] = str(tmp_path / "runs")
    monkeypatch.chdir(tmp_path)
    result = run_document(PipelineDocument.from_obj(doc), asof="2025-12-31", journal=False)
    assert result.state == "ran", result
    for dte in (30, 90):
        found = [json.loads(line) for line in (tmp_path / f"theta{dte}.jsonl").read_text().splitlines()]
        assert len(found) == 1
        assert (found[0]["symbol"], found[0]["date"], found[0]["target_dte"]) == (
            "SYNTHETIC", "2019-01-02", dte)
        assert found[0]["atm_iv"] == 0.25


def test_full_preparation_workflow_materializes_and_reuses_synthetic_panel(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from dskit.onboarding.artifacts import payload_files
    from dskit.pipeline.workflow import run_workflow

    ob = OnboardingRoot.create(str(tmp_path / "ob"))
    registry = ob.registry()

    def onboard(name, rows, relpath):
        archive = tmp_path / (name + "-input")
        dest = archive / relpath
        dest.parent.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows), dest, row_group_size=1)
        vid = registry.register("source_config", {
            "name": name, "catalog_source": "synthetic", "connector": "localblobs",
            "config": {"path": str(archive), "as_of": "2025-12-31T00:00:00+00:00"}}, origin="test")
        registry.transition(vid, "active", origin="test")
        run_acquisition(ob, registry, name, "files", "backfill")
        return payload_files(ob.root, name, "files")["manifest_sha256"]

    panel = _read("run-theta-ml-panel.json")
    params = panel["pipeline"]["panel"]["params"]
    base_rows = []
    for day, end in [("2019-01-03", "2019-02-04"), ("2019-01-04", "2019-02-05"),
                     ("2020-12-15", "2021-01-15")]:
        row = {key: .2 for key in params["base"]["columns"]}
        row.update(symbol="SYNTHETIC", quote_date=day, expiry=end, settlement_date=end)
        base_rows.append(row)
    base_pin = onboard("universe-model-inputs-20261008", base_rows, "panel/frame.parquet")
    feature = _read("run-theta-ml-features.json")
    fields = feature["pipeline"]["theta30"]["params"]["sources"][0]["columns"]
    chain = []
    for dte in (30, 90):
        row = {key: .25 for key in fields}
        row.update(symbol="SYNTHETIC", date="2019-01-02", expiration="2019-04-02",
                   target_dte=dte, status="ok", after_first_quote=True)
        chain.append(row)
    chain_pin = onboard("theta-chain-panel", chain, "day.parquet")
    params["base"].update(root=ob.root, snapshot=base_pin)
    for table in params["tables"].values():
        table["sources"][0].update(root=ob.root, snapshot=chain_pin)
    for key in ("theta30", "theta90"):
        feature["pipeline"][key]["params"]["sources"][0].update(root=ob.root, snapshot=chain_pin)
    flow = _read("workflow-theta-ml.json")
    old_root = flow["args"]["work_dir"]
    new_root = str(tmp_path / "result")
    configs = tmp_path / "configs"
    configs.mkdir()
    for name, doc in [("run-theta-ml-features.json", feature),
                      ("run-theta-ml-panel.json", panel), ("workflow-theta-ml.json", flow)]:
        (configs / name).write_text(json.dumps(doc).replace(old_root, new_root))
    monkeypatch.chdir(tmp_path)
    messages = []
    assert run_workflow(str(configs / "workflow-theta-ml.json"), out=messages.append) == 0, messages
    output = Path(new_root) / "panel"
    actual = pq.read_table(output / "panel.parquet").to_pylist()
    assert len(actual) == 3
    for expected, found in zip(base_rows, actual):
        assert {key: found[key] for key in expected} == expected
    assert [row["theta_atm_iv_30"] for row in actual] == [.25, .25, None]
    availability = [json.loads(line) for line in
                    (Path(new_root) / "availability-dates.jsonl").read_text().splitlines()]
    assert {row["quote_date"] for row in availability} == {"2019-01-03", "2019-01-04"}
    manifest_before = (output / "manifest.json").read_bytes()
    messages.clear()
    assert run_workflow(str(configs / "workflow-theta-ml.json"), out=messages.append) == 0
    assert sum(message.startswith("skip ") for message in messages) == 2
    assert (output / "manifest.json").read_bytes() == manifest_before


def test_all_preparation_graphs_plan_and_share_exact_source_declarations():
    feature = _read("run-theta-ml-features.json")
    panel = _read("run-theta-ml-panel.json")
    for doc in (feature, panel):
        assert plan(PipelineDocument.from_obj(doc)).order
    params = panel["pipeline"]["panel"]["params"]
    assert params["temporal_fields"] == ["quote_date", "settlement_date"]
    for dte in (30, 90):
        assert params["tables"][f"theta{dte}"]["sources"] == feature["pipeline"][f"theta{dte}"]["params"]["sources"]
    flow = Workflow(_read("workflow-theta-ml.json"), str(CONFIGS))
    assert flow.path("panel", "artifact") == params["output"]
    assert "artifact" in flow.steps["panel"]["out"]
    assert "panel" not in flow.steps["panel"]["out"]  # parent must not be pre-created
    assert flow.path("panel", "admission") == panel["pipeline"]["admission"]["params"]["path"]
    for dte in (30, 90):
        assert flow.path("features", f"theta{dte}") == feature["pipeline"][f"write{dte}"]["params"]["path"]

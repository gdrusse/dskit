"""ADR-0313 stage I8: every document template under ``docs/plans/mio-json/*.template.json`` of the integration (SYNTHETIC fixtures only).

The four integration templates (calibrated arm, hedged-program arm, combined arm, one-week timing check) are checked
against the code that will read them: each method's blocks through ``validate_configuration`` on a fixture study,
the nine ``locked_method_ids`` against the queue config, the development seal, the new method ids and the run command.
"""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.artifacts import payload_files
from dskit.pipeline.__main__ import cmd_validate
from dskit.pipeline.hedge_overlay import DEFAULT_IV_DISAGREE_TOL, DEFAULT_MIN_YEARS, DEFAULT_VOL_FLOOR
from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL
from dskit.pipeline.study_protocol import StudyContract
from index_options import ledger_studies
from test_ledger_studies import FORMULATIONS, _program_world
from test_mio_packets import study  # noqa: F401  (the synthetic packet fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from mio_instantiate import instantiate  # noqa: E402

CHILD = Path(__file__).resolve().parents[1]
PLANS = CHILD / "docs/plans/mio-json"
TEMPLATES = sorted(p for p in PLANS.glob("*.template.json") if json.loads(p.read_text()).get("kind") == "mio-integration-template/v1")
BASE = "w1_010_shock10_lp"


def load(path):
    return json.loads(path.read_text())


def test_the_five_templates_exist():
    assert [p.name for p in TEMPLATES] == ["calibrated-arm.template.json", "combined-arm.template.json",
                                           "hedged-program-arm.template.json", "timing-check-arm.template.json",
                                           "timing-check-control.template.json"]


def fixture_params(template):
    """The program-mode fixture study with the template's overlay merged in (the freeze's merge, for the keys it names)."""
    params, _ = _program_world()
    overlay = template["study_overlay"]["domain"]["params"]
    params["methods"] = copy.deepcopy(overlay["methods"])
    for key in ("locked_method_ids", "hedge_parity", "decision_weeks"):
        if key in overlay:
            params[key] = copy.deepcopy(overlay[key])
    params["clocks"] = {**params["clocks"], **overlay["clocks"]}
    if any("hedge" in m for m in params["methods"]):
        params["hedge_parity"] = overlay["hedge_parity"]
    else:
        params.pop("hedge_parity", None)
    return params


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_validates_against_the_study_and_pins_the_seal(path):
    template = load(path)
    params = fixture_params(template)
    ledger_studies.WeeklyOptionPortfolioStudy.validate_configuration(params)
    assert template["development_seal"] == DEVELOPMENT_SEAL == "2021-07-01"
    assert template["study_overlay"]["domain"]["params"]["clocks"] == {"tail_end_before": DEVELOPMENT_SEAL}
    assert params["clocks"]["tail_end_before"] == DEVELOPMENT_SEAL


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_keeps_the_nine_locked_new_ids_and_the_base_spec(path):
    template = load(path)
    overlay = template["study_overlay"]["domain"]["params"]
    queue = json.loads((CHILD / "docs/plans/mio-parallel/queue.example.json").read_text())
    assert overlay["locked_method_ids"] == queue["selection_formulations"] == list(FORMULATIONS) == list(ledger_studies.LOCKED_FORMULATION_HASHES)
    ids = [m["id"] for m in overlay["methods"]]
    assert queue["selection_formulation_hashes"] == ledger_studies.LOCKED_FORMULATION_HASHES
    assert len(ids) == len(set(ids)) and not set(ids) & set(FORMULATIONS)
    blocks = {"calibration", "hedge", "exposure_limits"}
    for method in overlay["methods"]:
        assert {k: v for k, v in method.items() if k not in blocks | {"id"}} == FORMULATIONS[BASE], method["id"]


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_states_its_command_prerequisites_and_placeholders(path):
    template = load(path)
    notes = template["notes"]
    assert "python -m dskit.pipeline run <doc>.json --asof <quarter end>" in notes
    for phrase in ("diagnostics first", "attempts.jsonl", "`closed` or `insufficient`", "placeholders", "2021-07-01",
                   "dskit.pipeline validate", "PYTHONPATH=.:children/index_options", "tools/mio_instantiate.py",
                   path.name):
        assert phrase in notes, phrase
    assert template["placeholders"] and all({"path", "meaning"} <= set(p) for p in template["placeholders"])


def test_the_blocks_each_template_carries():
    by = {p.name: load(p)["study_overlay"]["domain"]["params"] for p in TEMPLATES}
    cal, hedge, both = (by["calibrated-arm.template.json"]["methods"], by["hedged-program-arm.template.json"]["methods"],
                        by["combined-arm.template.json"]["methods"])
    assert cal[0]["calibration"]["control"] is True and cal[0]["calibration"]["form"] == "identity"
    assert cal[1]["calibration"]["form"] == "slope" and "hedge" not in cal[1]
    assert "calibration" not in hedge[0] and "hedge" not in hedge[0] and hedge[1]["hedge"]["mode"] == "program"
    assert both[1]["calibration"]["regressors"] == 2 and both[1]["hedge"]["mode"] == "program"
    twin_keys = {k: v for k, v in both[0]["calibration"].items() if k not in ("form", "control")}
    arm_keys = {k: v for k, v in both[1]["calibration"].items() if k not in ("form", "control")}
    assert twin_keys == {k: v for k, v in arm_keys.items() if k != "control"}, "the control differs from the arm in form and control only"
    # ADR-0313 section E: the timing check is two documents, the arm and its control, one listed week each
    arm, control = by["timing-check-arm.template.json"], by["timing-check-control.template.json"]
    assert arm["methods"] == [both[1]] and control["methods"] == [both[0]]
    assert len(arm["decision_weeks"]) == len(control["decision_weeks"]) == 1
    assert "hedge_parity" in arm and "hedge_parity" not in control, "the control is unhedged, so it takes no hedge_parity"


# --- from template to a runnable document ---------------------------------------------------------------------------

def walk_pin(tmp_path):
    """An onboarded snapshot holding the two files an edge_maps source names (contents are not read by validation)."""
    archive = tmp_path / "walk"
    archive.mkdir()
    for name in ("edge-maps.json", "summary.json"):
        (archive / name).write_text("{}")
    root = OnboardingRoot.create(str(tmp_path / "ob-walk"))
    registry = root.registry()
    vid = registry.register("source_config", {"name": "walk", "catalog_source": "synthetic", "connector": "localblobs",
                                              "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "walk", "files", "backfill")
    got = payload_files(root, "walk", "files")
    return {"root": root.root, "source": "walk", "stream": "files", "snapshot": got["snapshot"],
            "manifest_sha256": got["manifest_sha256"], "files": got["sha256"]}


def base_document(study):  # noqa: F811
    """The owner's quarter run document in miniature: a plan node whose study is the base."""
    return {"name": "quarter", "pipeline": {"plan": {"uses": "dskit.pipeline.study_protocol:StudyProtocolPlan",
                                                     "params": {"study": study}}}}


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.name)
def test_a_template_instantiates_into_a_document_that_validates_and_resolves(path, study, tmp_path, capsys):  # noqa: F811
    template = load(path)
    document = instantiate(template, base_document(study), name="arm-doc", output_root=str(tmp_path / "runs"),
                           edge_maps_pin=walk_pin(tmp_path), decision_week="2019-01-02")
    assert not set(document) & {"kind", "placeholders", "study_overlay", "development_seal"}
    assert list(document["pipeline"]) == ["plan", "freeze_development", "open_packet", "intake", "study", "close_packet"]
    assert document["outputs"] == {"run_root": str(tmp_path / "runs")} and document["name"] == "arm-doc"
    studies = {json.dumps(node["params"]["study"], sort_keys=True) for node in document["pipeline"].values()}
    assert len(studies) == 1, "every node carries the one merged study"
    merged = document["pipeline"]["plan"]["params"]["study"]
    overlay = template["study_overlay"]["domain"]["params"]
    assert merged["domain"]["params"]["methods"] == overlay["methods"] and "clocks" not in merged["domain"]["params"]
    assert StudyContract.build(merged)["runnable"] is not None, "the contract resolves the merged study"
    out = tmp_path / "doc.json"
    out.write_text(json.dumps(document))
    assert cmd_validate(str(out), []) == 0, capsys.readouterr().out


def test_the_instantiator_refuses_what_it_cannot_resolve(study, tmp_path):  # noqa: F811
    base = base_document(study)
    calibrated, timing = load(PLANS / "calibrated-arm.template.json"), load(PLANS / "timing-check-arm.template.json")
    with pytest.raises(ValueError, match="edge_maps_pin_missing"):
        instantiate(calibrated, base, name="n", output_root="r")
    with pytest.raises(ValueError, match="decision_week_missing"):
        instantiate(timing, base, name="n", output_root="r", edge_maps_pin={})
    late = copy.deepcopy(base)
    late["pipeline"]["plan"]["params"]["study"]["packets"][0]["end_before"] = "2021-07-02"
    with pytest.raises(ValueError, match="development_seal_violation"):
        instantiate(calibrated, late, name="n", output_root="r", edge_maps_pin={})
    twin = copy.deepcopy(base)
    twin["pipeline"]["other"] = {"params": {"study": {"different": True}}}
    with pytest.raises(ValueError, match="base_document_studies_differ"):
        instantiate(calibrated, twin, name="n", output_root="r", edge_maps_pin={})


def _refusal(template, base, code, **kw):
    kw.setdefault("edge_maps_pin", {})
    kw.setdefault("decision_week", "2019-01-02")
    with pytest.raises(ValueError, match=code):
        instantiate(template, base, name="n", output_root="r", **kw)


@pytest.mark.parametrize("name", ["hedged-program-arm", "combined-arm", "timing-check-arm"])
def test_the_instantiator_refuses_an_overlay_that_overrides_locked_content(name, study):  # noqa: F811
    base, template = base_document(study), load(PLANS / f"{name}.template.json")
    params = lambda t: t["study_overlay"]["domain"]["params"]  # noqa: E731
    # (a) a different locked list
    other = copy.deepcopy(template)
    params(other)["locked_method_ids"] = ["something_else"]
    _refusal(other, base, "locked_method_ids_changed")
    dropped = copy.deepcopy(template)
    del params(dropped)["locked_method_ids"]
    _refusal(dropped, base, "locked_method_ids_changed")
    # (b) an overlay method id that is locked, or already in the base
    locked = copy.deepcopy(template)
    params(locked)["methods"][0]["id"] = "w1_010_shock10_lp"
    _refusal(locked, base, "locked_method_id_collision")
    clash = copy.deepcopy(template)
    taken = base["pipeline"]["plan"]["params"]["study"]["domain"]["params"]["methods"][0]["id"]
    params(clash)["methods"][0]["id"] = taken
    _refusal(clash, base, "locked_method_id_collision")
    # (c) the seal is DEVELOPMENT_SEAL, not what the template says
    for key in ("template", "clocks"):
        late = copy.deepcopy(template)
        if key == "template":
            late["development_seal"] = "2030-01-01"
        else:
            params(late)["clocks"] = {"tail_end_before": "2030-01-01"}
        _refusal(late, base, "development_seal_violation")
    sealed = copy.deepcopy(template)
    sealed["development_seal"] = "2020-01-01"      # an earlier seal is not the owner's either
    _refusal(sealed, base, "development_seal_violation")


def test_the_base_declaration_must_match_the_pin(study):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    base["pipeline"]["plan"]["params"]["study"]["domain"]["params"]["locked_method_ids"] = ["only_this"]
    _refusal(template, base, "locked_method_ids_changed")


@pytest.mark.parametrize("declaration", [[], list(reversed(FORMULATIONS)), list(FORMULATIONS)[1:],
                                         [*FORMULATIONS, "extra"]])
def test_base_and_overlay_cannot_agree_to_change_locked_ids(study, declaration):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    study["domain"]["params"]["locked_method_ids"] = declaration
    template["study_overlay"]["domain"]["params"]["locked_method_ids"] = declaration
    _refusal(template, base, "locked_method_ids_changed")


def test_the_instantiator_refuses_an_empty_queue_lock_declaration(study, tmp_path):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    template["study_overlay"]["domain"]["params"]["locked_method_ids"] = []
    queue = tmp_path / "queue.json"
    queue.write_text(json.dumps({"selection_formulations": []}))
    _refusal(template, base, "locked_method_ids_changed", queue_config=queue)


@pytest.mark.parametrize("alias", ["NOMINAL_LP", " nominal_lp ", " NOMINAL "])
def test_an_overlay_method_cannot_alias_a_locked_or_base_id(study, alias):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    template["study_overlay"]["domain"]["params"]["methods"][0]["id"] = alias
    _refusal(template, base, "locked_method_id_collision")


def test_an_overlay_cannot_inherit_the_base_methods(study):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    study["domain"]["params"]["methods"] = [{"id": "nominal_lp", **FORMULATIONS["nominal_lp"]}]
    del template["study_overlay"]["domain"]["params"]["methods"]
    _refusal(template, base, "locked_method_id_collision")


def test_an_instantiator_base_must_keep_the_pinned_spec_for_a_locked_id(study):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    method = {"id": "nominal_lp", **FORMULATIONS["nominal_lp"]}
    study["domain"]["params"]["methods"] = [method]
    instantiate(template, base, name="n", output_root="r")
    method["rho"] = .5
    _refusal(template, base, "locked_formulation_spec_changed")


def test_a_leftover_placeholder_of_any_shape_refuses(study):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    for leftover in ("<onboarding pin>", "TODO: pin", "PLACEHOLDER: x", "root/${nowhere}"):
        left = copy.deepcopy(template)
        left["study_overlay"]["domain"].setdefault("sources", {})["corporate_actions"] = {"pin": leftover}
        _refusal(left, base, "unreplaced_placeholder")


@pytest.mark.parametrize("key", ["<pin>", "TODO: pin", "PLACEHOLDER: x", "root/${nowhere}"])
def test_a_leftover_placeholder_mapping_key_refuses(study, key):  # noqa: F811
    base, template = base_document(study), load(PLANS / "hedged-program-arm.template.json")
    template["study_overlay"]["domain"].setdefault("sources", {})[key] = {}
    _refusal(template, base, "unreplaced_placeholder")


def test_every_template_says_the_overlay_methods_replace_the_base_formulations():
    for path in TEMPLATES:
        assert "REPLACES the base's formulations" in load(path)["notes"], path.name
    assert "REPLACES the base's" in (CHILD / "tools/mio_instantiate.py").read_text().replace("\n", " ")


def test_the_command_line_writes_the_same_document(study, tmp_path):  # noqa: F811
    base, pin, out = tmp_path / "base.json", tmp_path / "pin.json", tmp_path / "out.json"
    base.write_text(json.dumps(base_document(study)))
    pin.write_text(json.dumps(walk_pin(tmp_path)))
    template = PLANS / "timing-check-arm.template.json"
    command = [sys.executable, str(CHILD / "tools/mio_instantiate.py"), str(template), "--base", str(base), "--name", "n",
               "--output-root", str(tmp_path / "runs"), "--edge-maps-pin", str(pin), "--decision-week", "2019-01-02",
               "--out", str(out)]
    root = CHILD.parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join((str(root), str(CHILD)))}
    run = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr or run.stdout
    expected = instantiate(load(template), base_document(study), name="n", output_root=str(tmp_path / "runs"),
                           edge_maps_pin=json.loads(pin.read_text()), decision_week="2019-01-02")
    assert json.loads(out.read_text()) == expected


# --- the walk template feeds a combined arm -------------------------------------------------------------------------

def test_the_walk_template_carries_the_direction_params_the_hedge_prices_with():
    walk = json.loads((CHILD / "configs/edge-calibration-template.json").read_text())["pipeline"]["edge"]["params"]
    direction = walk["direction"]
    combined = load(PLANS / "combined-arm.template.json")["study_overlay"]["domain"]["params"]
    hedge = combined["methods"][1]["hedge"]
    assert direction["rate"] == hedge["rate"] and direction["iv_source"] == hedge["iv_source"] == "mid"
    assert direction["iv_disagree_tol"] == DEFAULT_IV_DISAGREE_TOL
    assert direction["min_years"] == DEFAULT_MIN_YEARS and direction["vol_floor"] == DEFAULT_VOL_FLOOR
    assert {k: direction[k] for k in combined["hedge_parity"]} == combined["hedge_parity"]


def test_a_combined_arm_from_the_template_agrees_with_a_walk_run_from_the_walk_template():
    """The params-agree check itself, fed the walk template's direction block."""
    walk = json.loads((CHILD / "configs/edge-calibration-template.json").read_text())["pipeline"]["edge"]["params"]
    combined = load(PLANS / "combined-arm.template.json")["study_overlay"]["domain"]["params"]
    method = combined["methods"][1]
    direction = dict(walk["direction"])
    checker = ledger_studies.CalibratedEdge.__new__(ledger_studies.CalibratedEdge)
    checker.method = method
    checker.study = type("S", (), {"params": {"hedge_parity": combined["hedge_parity"]}})()
    needs = []
    checker._need = lambda code, why: needs.append(code)
    checker._check_agreement(direction)
    assert needs == []
    checker._check_agreement({**direction, "min_years": 0.002})
    assert needs == ["block_params_disagree"]


# --- a template runs end to end through the owner's entry point ------------------------------------------------------

@pytest.mark.slow
@pytest.mark.parametrize("study", ["hedged_program"], indirect=True)
def test_an_instantiated_hedged_program_arm_runs_to_completion(study, tmp_path, monkeypatch, capsys):  # noqa: F811
    """The registry the freeze needs is the quarter's own historical import, built here on the synthetic world."""
    from dskit.pipeline.__main__ import cmd_run
    from dskit.pipeline.study_protocol import ExposureRegistry

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    ExposureRegistry(study["registry"]).initialize(
        study["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01", "evidence": "synthetic prior use"}], "2020-01-01")
    document = instantiate(load(PLANS / "hedged-program-arm.template.json"), base_document(study), name="e2e-arm",
                           output_root=str(tmp_path / "runs"))
    path = tmp_path / "doc.json"
    path.write_text(json.dumps(document))
    assert cmd_run(str(path), "2019-02-09") == 0, capsys.readouterr().out
    from dskit.pipeline.document import load_document
    from dskit.pipeline.driver import RunAttestation, resolve_json_artifact

    run_dir = max((d for d in (tmp_path / "runs").rglob("*") if (d / "carry.json").exists() or (d / "report.md").exists()),
                  key=lambda d: len(d.parts))
    receipt = resolve_json_artifact(run_dir, RunAttestation(run_dir).attested_output("close_packet", "receipt", load_document(str(path)).hash))
    results = {r["method"] if "method" in r else r["id"]: r for r in receipt["results"]}
    assert set(results) == {"w1_010_shock10_hedge_twin_lp", "w1_010_shock10_hedge_lp"}, results.keys()
    assert all(r["status"] == "completed" and r["replay_parity"] for r in results.values()), results
    assert "hedge" in results["w1_010_shock10_hedge_lp"]["result"] and "hedge" not in results["w1_010_shock10_hedge_twin_lp"]["result"]

    from dskit.pipeline.option_structures import structure_payoff

    arm = results["w1_010_shock10_hedge_lp"]["result"]
    twin = results["w1_010_shock10_hedge_twin_lp"]["result"]
    assert len(arm["trades"]) == len(twin["trades"]) == 1
    book, = arm["decisions"][0]["books"]
    trade, = arm["trades"]
    assert book["id"] == trade["id"] and book["program_hedge"]["units_raw"]
    assert trade["fill_date"] == "2019-01-03" and trade["close_date"] == "2019-02-01"
    assert all(leg["fill_price"] > 0 for leg in trade["legs"])
    hedge = trade["hedge"]
    assert hedge["status"] == "ok" and hedge["units"] != 0
    settlement = 111.0  # the synthetic spot rises by 0.5 per weekday
    positions = [(leg["right"], leg["sign"] * leg["quantity"]) for leg in trade["legs"]]
    strikes = [leg["strike"] for leg in trade["legs"]]
    option_pnl = trade["credit"] + 100 * structure_payoff(positions, settlement, strikes)
    fees = hedge["rates"]["hedge_fee_per_share"]
    spread = hedge["rates"]["hedge_half_spread_bps"] / 1e4
    hedge_pnl = (hedge["units"] * (settlement - hedge["reference"])
                 - abs(hedge["units"]) * (2 * fees + spread * (settlement + hedge["reference"])))
    assert hedge_pnl != 0
    assert trade["pnl"] == pytest.approx(option_pnl + hedge_pnl, abs=.005)
    assert arm["pnl"] == pytest.approx(trade["pnl"], abs=.005)
    totals = arm["ledger_summary"]["totals"]
    assert totals["closed_trades"] == 1
    assert totals["pnl_usd"] == pytest.approx(trade["pnl"], abs=.005)

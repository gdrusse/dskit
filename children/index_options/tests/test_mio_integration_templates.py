"""ADR-0313 stage I8: every document template under ``docs/plans/mio-json/*.template.json`` of the integration (SYNTHETIC fixtures only).

The four integration templates (calibrated arm, hedged-program arm, combined arm, one-week timing check) are checked
against the code that will read them: each method's blocks through ``validate_configuration`` on a fixture study,
the nine ``locked_method_ids`` against the queue config, the development seal, the new method ids and the run command.
"""

import copy
import json
from pathlib import Path

import pytest

from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL
from index_options import ledger_studies
from test_ledger_studies import FORMULATIONS, _program_world

CHILD = Path(__file__).resolve().parents[1]
PLANS = CHILD / "docs/plans/mio-json"
TEMPLATES = sorted(p for p in PLANS.glob("*.template.json") if json.loads(p.read_text()).get("kind") == "mio-integration-template/v1")
BASE = "w1_010_shock10_lp"


def load(path):
    return json.loads(path.read_text())


def test_the_four_templates_exist():
    assert [p.name for p in TEMPLATES] == ["calibrated-arm.template.json", "combined-arm.template.json",
                                           "hedged-program-arm.template.json", "timing-check.template.json"]


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
    assert overlay["locked_method_ids"] == queue["selection_formulations"] == list(FORMULATIONS)
    ids = [m["id"] for m in overlay["methods"]]
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
                   "dskit.pipeline validate", "PYTHONPATH=.:children/index_options"):
        assert phrase in notes, phrase
    assert template["placeholders"] and all({"path", "meaning"} <= set(p) for p in template["placeholders"])
    assert "PLACEHOLDER" in json.dumps(template["study_overlay"]) or "decision_weeks" in notes or "ratio" in json.dumps(template["placeholders"])


def test_the_blocks_each_template_carries():
    by = {p.name: load(p)["study_overlay"]["domain"]["params"]["methods"] for p in TEMPLATES}
    cal, hedge, both, timing = (by["calibrated-arm.template.json"], by["hedged-program-arm.template.json"],
                                by["combined-arm.template.json"], by["timing-check.template.json"])
    assert cal[0]["calibration"]["control"] is True and cal[0]["calibration"]["form"] == "identity"
    assert cal[1]["calibration"]["form"] == "slope" and "hedge" not in cal[1]
    assert "calibration" not in hedge[0] and "hedge" not in hedge[0] and hedge[1]["hedge"]["mode"] == "program"
    assert both[1]["calibration"]["regressors"] == 2 and both[1]["hedge"]["mode"] == "program"
    twin_keys = {k: v for k, v in both[0]["calibration"].items() if k not in ("form", "control")}
    arm_keys = {k: v for k, v in both[1]["calibration"].items() if k not in ("form", "control")}
    assert twin_keys == {k: v for k, v in arm_keys.items() if k != "control"}, "the control differs from the arm in form and control only"
    assert timing == both
    one = load(PLANS / "timing-check.template.json")["study_overlay"]["domain"]["params"]["decision_weeks"]
    assert len(one) == 1

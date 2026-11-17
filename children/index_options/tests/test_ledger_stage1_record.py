"""The opt-in record of a local-search week's full stage-one selection (``record_stage1_selection``) and its reuse."""

import copy
import json

import pytest

from dskit.pipeline.libs.pyomo import RecordedCheckRefusal, TransportPortfolioSolve
from index_options import ledger_studies
from index_options.mio_worker import MIOMethodWorker
from test_ledger_studies import SHOCK10, _local_world

#: A nominal start (no radius, a wide-open shock gate): stage one trades, so a reused selection is not just cash.
LOOSE = {"start": {"rho": 0., "shock": {**SHOCK10, "loss_fraction": 1.}}}
STUDY = ledger_studies.WeeklyOptionPortfolioStudy


def _run(search=None, record=True, **kwargs):
    extra = {**(search or {}), **({"record_stage1_selection": True} if record else {})}
    params, data, method = _local_world(search={**LOOSE, **extra})
    study = STUDY(params, data)
    return params, data, method, study, study._method(method, None, **kwargs)


def _entries(result, method, study):
    """A worker's action file for the method, as it is written and read back."""
    body = MIOMethodWorker.actions(result, study.weeks)
    return json.loads(json.dumps(body))


def _climb(result):
    return [(w["selection"]["quantities"], w["kelly"]["local"]["end_value"], w["kelly"]["local"]["start_value"])
            for w in result["decisions"] if "selection" in w]


def test_the_key_is_validated_as_a_bool_and_changes_no_other_check():
    for bad in (1, "yes", None):
        params, _data, _method = _local_world(search={"record_stage1_selection": bad})
        with pytest.raises(ValueError, match="invalid fixed method"):
            STUDY.validate_configuration(params)
    for good in (True, False):
        params, _data, _method = _local_world(search={"record_stage1_selection": good})
        STUDY.validate_configuration(params)


def test_the_key_absent_records_exactly_the_old_evidence():
    _params, _data, method, study, result = _run(record=False)
    assert any("stage1" in week for week in result["decisions"])
    for week in result["decisions"]:
        if "stage1" in week:
            assert set(week["stage1"]) == {"selection", "certificates"}
    body = _entries(result, method, study)
    assert body["decisions"]
    for entry in body["decisions"].values():
        assert set(entry["stage1"]) == {"actions", "certificates"}
    # ``false`` is the same as absent for the records.
    _p, _d, _m, _s, off = _run(search={"record_stage1_selection": False}, record=False)
    for a, b in zip(result["decisions"], off["decisions"]):
        assert a.get("stage1", {}).keys() == b.get("stage1", {}).keys()


def test_the_key_adds_only_the_stage_one_selection_and_its_digests_and_decides_nothing_else():
    _p, _d, _m, _s, plain = _run(record=False)
    _p, _d, method, study, recorded = _run()
    assert _climb(plain) == _climb(recorded)
    for a, b in zip(plain["decisions"], recorded["decisions"]):
        assert a.get("books") == b.get("books") and a.get("status") == b.get("status")
        if "stage1" in b:
            assert {k: v for k, v in b["stage1"].items() if k not in ("program_sha256", "selection_sha256")}.keys() \
                == a["stage1"].keys()
            assert b["stage1"]["selection"] == a["stage1"]["selection"]
            assert len(b["stage1"]["program_sha256"]) == 64 and len(b["stage1"]["selection_sha256"]) == 64
    body = _entries(recorded, method, study)
    for day, entry in body["decisions"].items():
        stage1 = entry["stage1"]
        assert set(stage1) == {"actions", "certificates", "selection", "program_sha256", "selection_sha256"}
        assert stage1["actions"] == TransportPortfolioSolve.actions(stage1["selection"])


def test_a_recorded_selection_reused_gives_the_live_climb_with_no_stage_one_solve(monkeypatch):
    params, data, method, study, live = _run()
    body = _entries(live, method, study)
    traded = [w for w in live["decisions"] if "stage1" in w]
    assert traded and any(any(q for row in w["stage1"]["selection"]["quantities"] for q in row) for w in traded)

    def refuse(*_a, **_k):
        raise AssertionError("stage one was solved or re-derived")

    monkeypatch.setattr(TransportPortfolioSolve, "run", refuse)
    monkeypatch.setattr(TransportPortfolioSolve, "derived", refuse)
    again = STUDY(params, data)._method(method, None, derived=body)
    assert _climb(again) == _climb(live)
    for a, b in zip(live["decisions"], again["decisions"]):
        assert a.get("books") == b.get("books")
        if "stage1" in a:
            assert a["stage1"]["selection"] == b["stage1"]["selection"]
            assert a["kelly"]["local"] == b["kelly"]["local"]


def test_an_action_file_without_the_selection_still_derives_under_the_key():
    params, data, method, study, live = _run()
    body = _entries(live, method, study)
    for entry in body["decisions"].values():
        entry["stage1"] = {k: entry["stage1"][k] for k in ("actions", "certificates")}
    again = STUDY(params, data)._method(method, None, derived=body)
    assert _climb(again) == _climb(live)


def test_the_recorded_route_replays_the_recorded_week_and_checks_its_digest():
    params, data, method, _study, live = _run()
    assert STUDY(params, data)._method(method, None, recorded=live) == live
    forged = copy.deepcopy(live)
    week = next(w for w in forged["decisions"] if "stage1" in w)
    week["stage1"]["program_sha256"] = "0"*64
    with pytest.raises(RecordedCheckRefusal, match="different stage-one program"):
        STUDY(params, data)._method(method, None, recorded=forged)


def _refusal(params, data, method, body):
    with pytest.raises(RecordedCheckRefusal) as caught:
        STUDY(params, data)._method(method, None, derived=body)
    return caught.value


def test_a_program_digest_mismatch_refuses_and_is_never_reused():
    params, data, method, study, live = _run()
    body = _entries(live, method, study)
    day = next(iter(body["decisions"]))
    forged = copy.deepcopy(body)
    forged["decisions"][day]["stage1"]["program_sha256"] = "0"*64
    refusal = _refusal(params, data, method, forged)
    assert refusal.check == "stage1_program_digest" and refusal.week == day
    # A genuinely different stage-one program (the radius) under the unchanged record: its digest differs.
    other = copy.deepcopy(method)
    other["kelly"]["local_search"]["start"] = {**method["kelly"]["local_search"]["start"], "rho": .05}
    other_params = copy.deepcopy(params)
    other_params["methods"] = [other]
    refusal = _refusal(other_params, data, other, body)
    assert refusal.check == "stage1_program_digest"
    # A changed solver parameter is a different program too.
    solver_params = copy.deepcopy(params)
    solver_params["solver"]["absolute_gap"] = solver_params["solver"]["absolute_gap"]*2
    refusal = _refusal(solver_params, data, method, body)
    assert refusal.check == "stage1_program_digest"


def test_a_selection_that_differs_from_its_own_digest_or_is_undeclared_refuses():
    params, data, method, study, live = _run()
    body = _entries(live, method, study)
    day = next(iter(body["decisions"]))
    forged = copy.deepcopy(body)
    selection = forged["decisions"][day]["stage1"]["selection"]
    selection["value"] += 1.
    assert _refusal(params, data, method, forged).check == "stage1_selection_digest"
    # The method does not declare the key: a file carrying a selection is a different shape, refused.
    plain_params, plain_data, plain, _s, _r = _run(record=False)
    with pytest.raises(ValueError, match="stage-one evidence"):
        STUDY(plain_params, plain_data)._method(plain, None, derived=body)


def test_the_record_size_per_week_is_reported_for_the_synthetic_world():
    _p, _d, method, study, live = _run()
    body = _entries(live, method, study)
    for entry in body["decisions"].values():
        extra = {k: entry["stage1"][k] for k in ("selection", "program_sha256", "selection_sha256")}
        assert 0 < len(json.dumps(extra, separators=(",", ":"))) < 20000

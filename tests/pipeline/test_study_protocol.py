"""Durable packet transitions and research evidence integrity."""

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dskit.pipeline.base import value_hash
from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract, StudyProtocolControl
from dskit.pipeline.workflow import path_hash


class FixtureContract(StudyContract):
    def resolve(self, study):
        packet = next(p for p in study["packets"] if p["id"] == study["selected_packet"])
        bounds = {"values": {"start": packet["start"], "end_before": packet["end_before"]}}
        if packet["id"] == "dev_2019":
            bounds["history"] = {"start": "2018-12-01", "end_before": packet["start"]}
        return {"missing": [], "read_bounds": bounds,
            "cohort": ["a", "b"], "source_pins": {"immutable": "fixture"}}

    def validate_result(self, study, results, audit, provenance):
        return True


def _implementation_pins(*objects):
    import importlib
    pins = {}
    for obj in objects:
        module = importlib.import_module(obj.__module__) if isinstance(obj, type) else obj
        source = Path(module.__file__).resolve()
        root = source.parent
        if not (root / "__init__.py").is_file():
            pins[str(source)] = path_hash(str(source))
            continue
        while (root.parent / "__init__.py").is_file():
            root = root.parent
        for path in root.rglob("*.py"):
            pins[str(path.resolve())] = path_hash(str(path.resolve()))
    return pins


@pytest.fixture
def study(tmp_path):
    packets = [
        {"id": "dev", "start": "2020-01-01", "end_before": "2021-01-01",
         "purpose": "development", "predecessor": None},
        {"id": "r1", "start": "2021-01-01", "end_before": "2021-02-01",
         "purpose": "adaptation", "predecessor": "dev"},
        {"id": "r2", "start": "2022-01-01", "end_before": "2022-02-01",
         "purpose": "confirmation", "predecessor": "r1"}]
    return {"contract": __name__+":FixtureContract",
            "registry": {"path": str(tmp_path/"registry.sqlite"),
                         "id": "8936212a-8c1f-4d51-bc5c-95966b9d68a2", "domain": "fixture"},
            "packets": packets, "selected_packet": "dev", "domain": {},
            "code": _implementation_pins(StudyContract, FixtureContract), "dependencies": {"pytest": pytest.__version__}}


def _initialize(study):
    registry = ExposureRegistry(study["registry"])
    registry.initialize(study["packets"], [{"start": "2020-01-01", "end_before": "2021-01-01",
                                           "evidence": "recorded prior inspection"}], "2024-01-01")
    return registry


def _claim(study, registry):
    plan = StudyContract.build(study)
    return plan, registry.claim(plan, registry.freeze(plan))


def test_missing_reset_domain_and_malformed_history_refuse(study):
    registry = ExposureRegistry(study["registry"])
    with pytest.raises(ValueError, match="missing"):
        registry.snapshot()
    registry = _initialize(study)
    with pytest.raises(ValueError, match="already exists"):
        _initialize(study)
    wrong = {**study["registry"], "id": "9936212a-8c1f-4d51-bc5c-95966b9d68a2"}
    with pytest.raises(ValueError, match="identity"):
        ExposureRegistry(wrong).snapshot()
    import sqlite3
    with sqlite3.connect(study["registry"]["path"]) as c:
        c.execute("UPDATE state SET body = replace(body, '2024-01-01', '2027-01-01')")
    with pytest.raises(ValueError, match="drift"):
        registry.snapshot()


def test_atomic_claim_race_and_content_binding(study):
    registry = _initialize(study)
    plan = StudyContract.build(study)
    request = registry.freeze(plan)
    def claim():
        try:
            return registry.claim(plan, request)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    permits = [p for p in results if p is not None]
    assert len(permits) == 1
    permit = permits[0]
    assert permit["classification"] == "development"
    registry.bind(plan, permit, "inputs", {"hash": "a"})
    with pytest.raises(ValueError, match="changed"):
        registry.bind(plan, permit, "inputs", {"hash": "b"})
    with pytest.raises(ValueError, match="active"):
        registry.freeze(plan)
    forged = copy.deepcopy(permit)
    forged["classification"] = "fresh"
    with pytest.raises(ValueError, match="digest"):
        registry.verify(plan, forged)


def test_frontier_review_failure_retire_and_alias_history(study):
    registry = _initialize(study)
    later = {**study, "selected_packet": "r2"}
    with pytest.raises(ValueError, match="frontier"):
        registry.freeze(StudyContract.build(later))
    plan, permit = _claim(study, registry)
    registry.finish(plan, permit, {"complete": False})
    with pytest.raises(ValueError, match="attested close result receipt"):
        registry.dispose("dev", "review", "no")
    registry.dispose("dev", "abandon", "injected fixture failure")
    first = {**study, "selected_packet": "r1"}
    first_plan = StudyContract.build(first)
    assert registry.preview(first_plan)["classification"] == "fresh"
    registry.dispose("r1", "retire", "skip without inspecting")
    assert not registry.preview(first_plan)["ready"]
    next_plan, next_permit = _claim(later, registry)
    assert next_permit["classification"] == "fresh"
    registry.finish(next_plan, next_permit, {"complete": True})
    with pytest.raises(ValueError, match="active"):
        registry.freeze(plan)
    with pytest.raises(ValueError, match="attested close result receipt"):
        registry.dispose("r2", "review", "fixture reviewed")
    registry.dispose("r2", "abandon", "unit fixture has no persisted close receipt")
    alias = copy.deepcopy(study)
    alias["domain"]["alias"] = "different_source_name"
    alias_plan = StudyContract.build(alias)
    assert registry.preview(alias_plan)["classification"] == "development"
    assert registry.snapshot()["frontier"] == 3


def test_implementation_pins_require_imported_complete_source(study, tmp_path):
    valid = StudyContract.build(study)
    assert valid["runnable"]
    unrelated = tmp_path / "unrelated.py"
    unrelated.write_text("VALUE = 1\n")
    substituted = copy.deepcopy(study)
    substituted["code"] = {str(unrelated): path_hash(str(unrelated))}
    with pytest.raises(ValueError, match="implementation pins"):
        StudyContract.build(substituted)


def test_import_development_preserves_frontier_and_appends_prior_exposure(study):
    registry = _initialize(study)
    original = registry.snapshot()
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    imported_study = {**study, "packets": study["packets"]+[imported_packet],
                      "selected_packet": "dev_2019"}
    plan = StudyContract.build(imported_study)
    evidence = [{"start": "2018-12-01", "end_before": "2019-03-01",
                 "evidence": "pinned historical development inventory"}]
    result = registry.import_development(plan, evidence, original["revision"], "admit prior 2019 exposure")
    after = registry.snapshot()
    assert result["frontier"] == original["frontier"] == after["frontier"]
    assert after["packets"] == study["packets"]+[imported_packet]
    assert after["prior"] == original["prior"]+evidence
    assert after["development_imports"] == ["dev_2019"]
    assert after["development_import_reasons"] == {"dev_2019": "admit prior 2019 exposure"}
    assert registry.preview(plan)["classification"] == "development"
    with pytest.raises(ValueError, match="stale"):
        registry.import_development(plan, evidence, original["revision"], "retry")
    registry.dispose("dev", "retire", "advance existing schedule")
    assert registry.preview(StudyContract.build({**imported_study, "selected_packet": "r1"}))["classification"] == "fresh"
    assert registry.preview(plan)["classification"] == "development"


@pytest.mark.parametrize("bad_evidence", [
    [],
    [{"start": "2018-12-01", "end_before": "2019-01-10", "evidence": "too short"}],
    [{"start": "2019-01-01", "end_before": "2019-03-01", "evidence": "misses history"}],
    [{"start": "2018-12-01", "end_before": "2024-01-02", "evidence": "protected"}],
])
def test_development_import_refuses_incomplete_prior_evidence_atomically(study, bad_evidence):
    registry = _initialize(study)
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    imported_study = {**study, "packets": study["packets"]+[imported_packet],
                      "selected_packet": "dev_2019"}
    plan = StudyContract.build(imported_study)
    before = registry.snapshot()
    with pytest.raises(ValueError):
        registry.import_development(plan, bad_evidence, before["revision"], "must reject")
    assert registry.snapshot() == before


def test_development_import_rejects_active_stale_and_forged_schedule(study):
    registry = _initialize(study)
    plan, _ = _claim(study, registry)
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    imported_study = {**study, "packets": study["packets"]+[imported_packet],
                      "selected_packet": "dev_2019"}
    import_plan = StudyContract.build(imported_study)
    good = [{"start": "2018-12-01", "end_before": "2019-03-01", "evidence": "pinned"}]
    before = registry.snapshot()
    with pytest.raises(ValueError, match="inactive"):
        registry.import_development(import_plan, good, before["revision"], "active refusal")
    registry.dispose("dev", "abandon", "release test claim")
    state = registry.snapshot()
    forged = copy.deepcopy(import_plan)
    forged["study"]["packets"][0]["start"] = "2020-02-01"
    with pytest.raises(ValueError, match="digest|changed|unbound"):
        registry.import_development(forged, good, state["revision"], "forged plan")
    changed_prefix = copy.deepcopy(imported_study)
    changed_prefix["packets"][0]["end_before"] = "2020-12-31"
    changed_prefix_plan = StudyContract.build(changed_prefix)
    with pytest.raises(ValueError, match="append to the existing schedule"):
        registry.import_development(changed_prefix_plan, good, state["revision"], "changed prefix")
    overlapping = {**imported_study, "packets": study["packets"]+[
        {**imported_packet, "id": "overlap", "start": "2020-06-01", "end_before": "2020-07-01"}],
        "selected_packet": "overlap"}
    with pytest.raises(ValueError, match="overlapping"):
        StudyContract.build(overlapping)


def test_development_import_rejects_cross_registry_plan(study):
    local = copy.deepcopy(study)
    wrong_config = {**local["registry"], "path": local["registry"]["path"]+".other",
                    "id": "9936212a-8c1f-4d51-bc5c-95966b9d68a2"}
    wrong = ExposureRegistry(wrong_config)
    wrong.initialize(local["packets"], [{"start": "2020-01-01", "end_before": "2021-01-01",
                                          "evidence": "other registry prior"}], "2024-01-01")
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    imported_study = {**local, "packets": local["packets"]+[imported_packet],
                       "selected_packet": "dev_2019"}
    plan = StudyContract.build(imported_study)
    before = wrong.snapshot()
    with pytest.raises(ValueError, match="identity mismatch"):
        wrong.import_development(plan, [{"start": "2018-12-01", "end_before": "2019-03-01",
                                         "evidence": "pinned"}], before["revision"], "wrong registry")
    assert wrong.snapshot() == before


def test_frontier_skips_imported_history_and_advances_past_it(study):
    registry = _initialize(study)
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    later_packets = [
        {"id": "r3", "start": "2023-01-01", "end_before": "2023-02-01",
         "purpose": "adaptation", "predecessor": "dev_2019"},
        {"id": "r4", "start": "2023-02-01", "end_before": "2023-03-01",
         "purpose": "confirmation", "predecessor": "r3"}]
    schedule = study["packets"]+[imported_packet]+later_packets
    import_study = {**study, "packets": study["packets"]+[imported_packet],
                    "selected_packet": "dev_2019"}
    state = registry.snapshot()
    registry.import_development(StudyContract.build(import_study), [
        {"start": "2018-12-01", "end_before": "2019-03-01", "evidence": "pinned"}],
        state["revision"], "append historical development")
    extension = {**study, "packets": schedule, "selected_packet": "r3"}
    registry.extend(StudyContract.build(extension), "append later fresh packets")
    registry.dispose("dev", "retire", "advance original development")
    registry.dispose("r1", "retire", "advance first fresh packet")
    registry.dispose("r2", "retire", "advance second fresh packet")
    plan3 = StudyContract.build({**extension, "selected_packet": "r3"})
    assert registry.preview(plan3)["classification"] == "fresh"
    registry.dispose("r3", "retire", "advance past imported historical packet")
    assert registry.snapshot()["frontier"] == 5
    plan4 = StudyContract.build({**extension, "selected_packet": "r4"})
    assert registry.preview(plan4)["classification"] == "fresh"


def test_development_import_control_action_is_strict_and_persistent(study):
    registry = _initialize(study)
    imported_packet = {"id": "dev_2019", "start": "2019-01-01", "end_before": "2019-02-01",
                       "purpose": "development", "predecessor": "r2"}
    imported_study = {**study, "packets": study["packets"]+[imported_packet],
                      "selected_packet": "dev_2019"}
    state = registry.snapshot()
    params = {"study": imported_study, "action": "import_development",
              "prior_exposure": [{"start": "2018-12-01", "end_before": "2019-03-01",
                                   "evidence": "pinned prior outcome inventory"}],
              "expected_revision": state["revision"], "reason": "record prior development"}
    assert StudyProtocolControl.validate_params(params) == []
    result = StudyProtocolControl("control", params).run(None, {})["control"]
    assert result["packet"] == imported_packet
    assert registry.snapshot()["development_imports"] == ["dev_2019"]
    assert any("control parameters" in error for error in
               StudyProtocolControl.validate_params({**params, "end_before": "2020-01-01"}))


def test_fresh_release_uses_latest_boundary_across_all_prior_exposures(study):
    local = copy.deepcopy(study)
    local["registry"]["path"] += ".latest-boundary"
    registry = ExposureRegistry(local["registry"])
    registry.initialize(local["packets"], [
        {"start": "2019-01-01", "end_before": "2020-01-01", "evidence": "older exposure"},
        {"start": "2020-01-01", "end_before": "2022-01-01", "evidence": "later exposure"}],
        "2024-01-01")
    registry.dispose("dev", "retire", "advance development packet")
    fresh = StudyContract.build({**local, "selected_packet": "r1"})
    readiness = registry.preview(fresh)
    assert readiness["ready"] is False
    assert "previous reads overlap" in readiness["reason"]
    omitted = copy.deepcopy(study)
    omitted["code"].pop(next(path for path in omitted["code"] if path.endswith("study_protocol.py")))
    with pytest.raises(ValueError, match="implementation pins"):
        StudyContract.build(omitted)
    other_checkout = copy.deepcopy(study)
    other_checkout["code"] = {path.replace("/home/russell/wt/", "/tmp/other-checkout/"): digest
                              for path, digest in study["code"].items()}
    with pytest.raises(ValueError, match="implementation pins"):
        StudyContract.build(other_checkout)


def test_plan_code_dependency_and_result_identity(study):
    registry = _initialize(study)
    plan = StudyContract.build(study)
    assert registry.snapshot()["revision"] == 0
    assert registry.preview(plan)["ready"]
    bad = copy.deepcopy(study)
    bad["code"][__file__] = "0"*64
    with pytest.raises(ValueError, match="code"):
        StudyContract.build(bad)
    bad = copy.deepcopy(study)
    bad["dependencies"]["pytest"] = "0"
    with pytest.raises(ValueError, match="dependency"):
        StudyContract.build(bad)
    plan, permit = _claim(study, registry)
    wrong = copy.deepcopy(study)
    wrong["domain"]["new_behavior"] = True
    with pytest.raises(ValueError, match="permit"):
        registry.verify(StudyContract.build(wrong), permit)
    result = {"complete": True, "data": [1, 2]}
    registry.finish(plan, permit, result)
    assert registry.verify(plan, permit, active=False)["result"] == value_hash(result)

def test_same_identity_retry_preserves_failed_publication(study):
    registry = _initialize(study)
    plan, permit = _claim(study, registry)
    registry.finish(plan, permit, {"complete": False})
    revision = registry.snapshot()["revision"]
    changed = copy.deepcopy(study)
    changed["domain"]["changed"] = True
    with pytest.raises(ValueError, match="identity"):
        registry.retry(StudyContract.build(changed), permit, revision)
    next_permit = registry.retry(plan, permit, revision)
    assert next_permit["id"] != permit["id"]
    assert next_permit["classification"] == "replay"
    assert registry.verify(plan, permit, active=False)["result"] == value_hash({"complete": False})
    with pytest.raises(ValueError, match="inactive"):
        registry.verify(plan, permit)
    registry.verify(plan, next_permit)


def test_schedule_extension_cannot_rewrite_history(study):
    registry = _initialize(study)
    extended = copy.deepcopy(study)
    extended["packets"].append({"id": "r3", "start": "2023-01-01", "end_before": "2023-02-01",
                               "purpose": "adaptation", "predecessor": "r2"})
    registry.extend(StudyContract.build(extended), "new future window")
    assert len(registry.snapshot()["packets"]) == 4
    extended["packets"][0]["start"] = "2019-01-01"
    with pytest.raises(ValueError, match="append-only"):
        registry.extend(StudyContract.build(extended), "try to rewrite past")
    historical = copy.deepcopy(study)
    historical["packets"].append({"id": "r3", "start": "2023-01-01", "end_before": "2023-02-01",
                                   "purpose": "adaptation", "predecessor": "r2"})
    historical["packets"].append({"id": "dev_old", "start": "2018-01-01", "end_before": "2018-02-01",
                                   "purpose": "development", "predecessor": "r3"})
    historical["selected_packet"] = "dev_old"
    with pytest.raises(ValueError, match="prior-exposure import"):
        registry.extend(StudyContract.build(historical), "bypass historical import")


def _substitute_read_bounds(plan, *, reseal=False):
    from dskit.pipeline.study_protocol import _seal

    changed = copy.deepcopy(plan)
    changed["read_bounds"]["values"]["end_before"] = "2020-12-31"
    if reseal:
        changed.pop("sha256")
        changed = _seal(changed)
    return changed


def test_registry_rebuilds_plan_identity_before_claim_and_verify(study):
    registry = _initialize(study)
    plan = StudyContract.build(study)
    frozen = registry.freeze(plan)
    revision = registry.snapshot()["revision"]
    altered = _substitute_read_bounds(plan)
    with pytest.raises(ValueError, match="plan|bound|changed|digest"):
        registry.claim(altered, frozen)
    assert registry.snapshot()["revision"] == revision

    permit = registry.claim(plan, frozen)
    for altered in (_substitute_read_bounds(plan), _substitute_read_bounds(plan, reseal=True)):
        revision_after_claim = permit["registry_revision_after"]
        with pytest.raises(ValueError, match="plan|bound|changed|digest"):
            registry.verify(altered, permit)
        with pytest.raises(ValueError, match="plan|bound|changed|digest"):
            registry.bind(altered, permit, "inputs", {"x": 1})
        with pytest.raises(ValueError, match="plan|bound|changed|digest"):
            registry.finish(altered, permit, {"complete": False})
        with pytest.raises(ValueError, match="plan|bound|changed|digest"):
            registry.retry(altered, permit, revision_after_claim)
        assert registry.snapshot()["revision"] == revision_after_claim


def test_registry_plan_identity_guard_covers_schedule_mutators(study):
    registry = _initialize(study)
    extension = {**study, "packets": study["packets"] + [{
        "id": "r3", "start": "2023-01-01", "end_before": "2023-02-01",
        "purpose": "adaptation", "predecessor": "r2"}], "selected_packet": "r3"}
    extended = StudyContract.build(extension)
    altered_extension = _substitute_read_bounds(extended, reseal=True)
    revision = registry.snapshot()["revision"]
    with pytest.raises(ValueError, match="plan|bound|changed|digest"):
        registry.extend(altered_extension, "must authenticate extension")
    assert registry.snapshot()["revision"] == revision

    wrong_registry = copy.deepcopy(study)
    wrong_registry["registry"]["id"] = "9936212a-8c1f-4d51-bc5c-95966b9d68a2"
    foreign_plan = StudyContract.build(wrong_registry)
    with pytest.raises(ValueError, match="registry|identity"):
        registry.freeze(foreign_plan)
    assert registry.snapshot()["revision"] == revision

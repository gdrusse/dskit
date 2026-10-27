"""Durable packet transitions and research evidence integrity."""

import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from dskit.pipeline.base import value_hash
from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract
from dskit.pipeline.workflow import path_hash


class FixtureContract(StudyContract):
    def resolve(self, study):
        packet = next(p for p in study["packets"] if p["id"] == study["selected_packet"])
        return {"missing": [], "read_bounds": {"values": {
            "start": packet["start"], "end_before": packet["end_before"]}},
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

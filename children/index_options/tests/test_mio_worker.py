"""Worker bundles, derived actions and the recorded document path (ADR-0297).

Every test is synthetic (the test_mio_packets fixture, registry domain
``synthetic:packet``); the worker runs as a subprocess on the bundle's own
code tree, exactly as it would on another host.
"""

import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from dskit.onboarding.artifacts import PinnedPayloadSource
from dskit.pipeline.base import value_hash
from dskit.pipeline.driver import run_document
from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract
from index_options.ledger_studies import MIOPacketStudy, WeeklyOptionPortfolioStudy
from index_options.mio_worker import BUNDLE_TOKEN, MIOMethodBundle, MIOMethodWorker
from index_options.observations import MIOPacketInputs
from test_ledger_studies import _packet_clock_fixture
from test_mio_packets import _document, fixed_world, study  # noqa: F401  (the synthetic packet fixture)

SECOND_METHOD = {"id": "second", "rho": 0., "portfolio_fraction": .5, "name_fraction": 1.,
                 "component_fraction": None}


def _two_methods(spec):
    spec["domain"]["params"]["methods"] = spec["domain"]["params"]["methods"] + [SECOND_METHOD]
    return spec


def _init(spec):
    registry = ExposureRegistry(spec["registry"])
    registry.initialize(spec["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01",
                                          "evidence": "synthetic prior use"}], "2020-01-01")
    plan = StudyContract.build(spec)
    return registry, plan, registry.freeze(plan)


def _close_document(tmp_path, name, spec, plan, request, recorded=None):
    params = {"study": spec} if recorded is None else {"study": spec, "recorded": str(recorded)}
    return _document(tmp_path, name, {
        "open_packet": {"uses": "dskit.pipeline.study_protocol:StudyPacketOpen",
                        "params": {"study": spec, "frozen_plan": plan, "claim_request": request}},
        "intake": {"uses": "index_options.observations:MIOPacketInputs",
                   "params": {"study": spec}, "inputs": {"permit": "$open_packet.permit"}},
        "study": {"uses": "index_options.ledger_studies:MIOPacketStudy", "params": params,
                  "inputs": {"permit": "$open_packet.permit", "bounded_inputs": "$intake.bounded_inputs",
                             "provenance": "$intake.provenance"}},
        "close": {"uses": "dskit.pipeline.study_protocol:StudyPacketClose", "params": {"study": spec},
                  "inputs": {"permit": "$open_packet.permit", "results": "$study.results",
                             "audit": "$study.audit", "provenance": "$intake.provenance"}}})


def _receipt(ran, doc):
    carry = json.loads((Path(ran.run_dir) / "carry.json").read_text())
    return json.loads((Path(ran.run_dir) / carry["close"]["receipt"]["path"]).read_text())


def _events(caplog, kind):
    return [json.loads(r.getMessage()[len("MIO_EVENT "):]) for r in caplog.records
            if r.getMessage().startswith("MIO_EVENT ") and f'"type":"{kind}"' in r.getMessage()]


def _worker(tmp_path, bundle_root, method, attempt=1, recorded=None, unit=None, print_body=False, check=False,
            priced_out=None):
    recorded = recorded or tmp_path / "recorded"
    spec = {"bundle_root": str(bundle_root), "method": method, "attempt": attempt,
            "work_dir": str(tmp_path / f"work-{method}.{attempt}"), "out": str(recorded / f"{method}.json"),
            "unit": unit or {"quarter": "synthetic", "arm": "fixture", "method": method}}
    if priced_out is not None:
        spec["priced_out"] = str(priced_out)
    spec_path = tmp_path / f"spec-{method}.{attempt}.json"
    spec_path.write_text(json.dumps(spec))
    env = {**os.environ, "PYTHONPATH": str(bundle_root / MIOMethodBundle.CODE_DIR),
           "PYTHONDONTWRITEBYTECODE": "1", "XDG_CACHE_HOME": str(tmp_path / "worker-cache")}
    argv = [sys.executable, "-B", "-m", "index_options.mio_worker", str(spec_path)]
    if print_body:
        argv.append("--print")
    if check:
        argv.append("--check")
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=900, cwd=str(tmp_path))


def _rehash(bundle_root):
    """Re-sign a tampered bundle so only the semantic check can refuse it."""
    manifest = json.loads((bundle_root / MIOMethodBundle.MANIFEST_FILE).read_text())
    from dskit.pipeline.workflow import path_hash
    files = {rel: path_hash(str(bundle_root / rel)) for rel in manifest["files"]}
    (bundle_root / MIOMethodBundle.MANIFEST_FILE).write_text(
        json.dumps({"files": files, "sha256": value_hash(files)}, sort_keys=True))


def test_actions_and_derived_reproduce_a_local_solve_exactly():
    params, inputs = _packet_clock_fixture()
    method = params["methods"][0]
    baseline = WeeklyOptionPortfolioStudy(params, inputs)._method(method, None)
    weeks = WeeklyOptionPortfolioStudy(params, inputs).weeks
    actions = MIOMethodWorker.actions(baseline, weeks)
    assert actions["method"] == method["id"] and actions["weeks"] == weeks
    assert actions["decisions"], "the fixture solves at least one week"
    for entry in actions["decisions"].values():
        assert set(entry) == {"actions", "certificates"}
        assert entry["actions"] is None or all(type(v) is int for q in entry["actions"]["primary_quantities"] for v in q)
    derived = WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=actions)
    assert derived == baseline
    # the frozen exact replay accepts the derived result like a local one
    assert WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=derived) == baseline
    with pytest.raises(ValueError, match="exclusive"):
        WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, recorded=baseline, derived=actions)
    day = next(iter(actions["decisions"]))
    tampered = copy.deepcopy(actions)
    if tampered["decisions"][day]["actions"] is not None:
        tampered["decisions"][day]["actions"]["primary_quantities"][0][0] += 1
        with pytest.raises(ValueError):
            WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=tampered)
    extra = copy.deepcopy(actions)
    extra["decisions"][weeks[-1]] = copy.deepcopy(actions["decisions"][day])
    with pytest.raises(ValueError, match="ineligible week"):
        WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=extra)
    missing = copy.deepcopy(actions)
    del missing["decisions"][day]
    with pytest.raises(ValueError, match="lack an eligible week"):
        WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=missing)
    other = copy.deepcopy(actions)
    other["method"] = "someone_else"
    with pytest.raises(ValueError, match="calendar differs"):
        WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=other)
    for edit in (lambda a: a.update(weeks=a["weeks"][:-1]),            # a calendar of another length
                 lambda a: a.update(weeks=list(reversed(a["weeks"]))),  # the same dates in another order
                 lambda a: a.update(weeks=[*a["weeks"][:-1], "2030-01-07"]),
                 lambda a: a.update(extra=1),                           # another top-level key set
                 lambda a: a["decisions"].update({"2030-01-07": a["decisions"][day]}),   # a day outside the calendar
                 lambda a: a["decisions"][day].update(extra=1)):
        wrong = copy.deepcopy(actions)
        edit(wrong)
        with pytest.raises(ValueError, match="calendar differs"):
            WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=wrong)
    dims = copy.deepcopy(actions)
    dims["decisions"][day]["certificates"][0]["variables"] += 1
    with pytest.raises(ValueError, match="dimensions"):
        WeeklyOptionPortfolioStudy(params, inputs)._method(method, None, derived=dims)


def test_portable_digests_ignore_host_index_bytes_and_roots(study, tmp_path):  # noqa: F811
    bounded = {"inputs": {"X": {"forecasts": [], "contexts": [], "bars": [],
                                "chain_index": {"index": {"rows": 3, "columns": ["a"], "path": "/h1/X.sqlite",
                                                          "sha256": "aa"},
                                                "source_pin": {"root": "/home/x/ob", "manifest_sha256": "m",
                                                               "files": {"c.parquet": "f"}}}}}, "clocks": {}}
    other = copy.deepcopy(bounded)
    other["inputs"]["X"]["chain_index"]["index"].update(path="/Volumes/mio-ram/X.sqlite", sha256="bb")
    other["inputs"]["X"]["chain_index"]["source_pin"]["root"] = "/Volumes/mio-ram/bundle/sources/root0"
    assert MIOPacketInputs.portable_inputs_sha256(bounded) == MIOPacketInputs.portable_inputs_sha256(other)
    other["inputs"]["X"]["chain_index"]["index"]["rows"] = 4
    assert MIOPacketInputs.portable_inputs_sha256(bounded) != MIOPacketInputs.portable_inputs_sha256(other)
    manifest = MIOMethodBundle(study, tmp_path / "bundle").build()
    rewritten = json.loads((tmp_path / "bundle" / MIOMethodBundle.STUDY_FILE).read_text())
    assert rewritten["code"] != study["code"]
    assert all(name.startswith(BUNDLE_TOKEN + "/code/") for name in rewritten["code"]), "host-free pins"
    assert rewritten["domain"]["sources"]["chain"]["pin"]["root"].startswith(BUNDLE_TOKEN + "/sources/")
    relocated = MIOMethodWorker.relocate(rewritten, tmp_path / "bundle")
    assert all(name.startswith(str(tmp_path / "bundle" / "code")) for name in relocated["code"])
    identity = json.loads((tmp_path / "bundle" / MIOMethodBundle.IDENTITY_FILE).read_text())
    assert identity["study_sha256"] == MIOPacketInputs.portable_study_sha256(study), "the action file names the original pins"
    assert identity["metadata_stamp_columns"] == {}, "the exemption map is always written, here empty: no stamp column in the fixture"
    assert MIOPacketInputs.portable_study_sha256(relocated) != identity["study_sha256"], \
        "the fixture's files carry a protected 2026 row, so the bundle pins cut copies with their own digests"
    with pytest.raises(ValueError, match="outside the bundle"):
        MIOMethodWorker.relocate(study, tmp_path / "bundle")
    assert set(manifest["files"]) >= {MIOMethodBundle.STUDY_FILE, MIOMethodBundle.PLAN_FILE}
    changed = copy.deepcopy(study)
    changed["domain"]["params"]["methods"][0]["rho"] = .5
    assert MIOPacketInputs.portable_study_sha256(changed) != MIOPacketInputs.portable_study_sha256(study)


def test_worker_refuses_code_dependency_and_manifest_drift(study, tmp_path):  # noqa: F811
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    spec = {"bundle_root": str(bundle_root), "method": "nominal", "attempt": 1,
            "work_dir": str(tmp_path / "work"), "out": str(tmp_path / "recorded" / "nominal.json")}
    with pytest.raises(ValueError, match="attempt"):
        MIOMethodWorker({**spec, "attempt": 0})
    with pytest.raises(ValueError, match="absolute"):
        MIOMethodWorker({**spec, "work_dir": "relative/work"})
    pinned = bundle_root / "code" / "index_options" / "mio_contract.py"
    original = pinned.read_bytes()
    pinned.write_bytes(original + b"\n# drift\n")
    proc = _worker(tmp_path, bundle_root, "nominal")
    assert proc.returncode != 0 and "bundle file drift" in proc.stderr, proc.stderr
    _rehash(bundle_root)  # a re-signed bundle still fails the study's own code pins
    proc = _worker(tmp_path, bundle_root, "nominal")
    assert proc.returncode != 0 and "code pin drift" in proc.stderr, proc.stderr
    pinned.write_bytes(original)
    study_file = bundle_root / MIOMethodBundle.STUDY_FILE
    body = json.loads(study_file.read_text())
    body["dependencies"] = {"pytest": "0.0.0"}
    study_file.write_text(json.dumps(body, sort_keys=True))
    _rehash(bundle_root)
    proc = _worker(tmp_path, bundle_root, "nominal")
    assert proc.returncode != 0 and "dependency pin drift" in proc.stderr, proc.stderr
    assert not (tmp_path / "recorded").exists(), "a refused worker writes nothing"


def test_worker_then_recorded_document_closes_with_provenance(study, tmp_path, caplog, monkeypatch):  # noqa: F811
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    study = _two_methods(study)
    methods = [m["id"] for m in study["domain"]["params"]["methods"]]
    # A plain run on registry A: its receipt and registry must stay byte-identical afterwards.
    plain_study = copy.deepcopy(study)
    registry_a, plan_a, request_a = _init(plain_study)
    plain = run_document(_close_document(tmp_path, "plain", plain_study, plan_a, request_a), asof="2019-02-09", journal=False)
    assert plain.state == "ran", plain
    receipt_a = _receipt(plain, None)
    receipt_path = Path(plain.run_dir) / json.loads((Path(plain.run_dir) / "carry.json").read_text())["close"]["receipt"]["path"]
    frozen = {p: p.read_bytes() for p in (receipt_path, Path(plain_study["registry"]["path"]))}
    # The recorded run uses its own registry B and a bundle built from that study.
    recorded_study = copy.deepcopy(study)
    recorded_study["registry"] = {**study["registry"], "path": str(tmp_path / "exposure-b.sqlite")}
    bundle_root = tmp_path / "bundle"
    manifest = MIOMethodBundle(recorded_study, bundle_root).build()
    recorded = tmp_path / "recorded"
    bodies = {}
    for method in reversed(methods):  # arrival order must not matter
        proc = _worker(tmp_path, bundle_root, method, print_body=(method == methods[0]))
        assert proc.returncode == 0, proc.stderr
        bodies[method] = json.loads((recorded / f"{method}.json").read_text())
        assert (recorded / f"{method}.json.sha256").read_text().split()[0] == hashlib.sha256(
            (recorded / f"{method}.json").read_bytes()).hexdigest()
        assert (recorded / f"{method}.json.done").exists()
        assert not (recorded / f"{method}.result.json").exists(), "no economics beside what operators tail"
        progress = (recorded / f"{method}.json.progress").read_text().splitlines()
        assert "week " in "\n".join(progress) and " index " in progress[0], "a heartbeat precedes the first week"
        assert all(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ", line) for line in progress), progress
        assert all(re.fullmatch(r"\S+ (week \d+/\d+|index building)", line) for line in progress), \
            "M2: a week count only; the per-week status word would show a holdout quarter's timeline"
        assert bodies[method]["bundle_manifest_sha256"] == manifest["sha256"]
        assert bodies[method]["format"] == MIOPacketStudy.RECORDED_FORMAT
        if method == methods[0]:
            printed = json.loads(proc.stdout)
            assert set(printed) == {"method", "actions_sha256"}, "hash-only stdout; no digest that carries timing"
            timeless = {day: {"actions": e["actions"], "termination": [c["termination"] for c in e["certificates"]]}
                        for day, e in bodies[method]["actions"]["decisions"].items()}
            assert printed["actions_sha256"] == value_hash(timeless), "integer actions and terminations only"
            assert "decisions" not in proc.stdout and "objective" not in proc.stdout
    registry_b, plan_b, request_b = _init(recorded_study)
    assert all(b["plan_sha256"] == plan_b["sha256"] for b in bodies.values())
    doc = _close_document(tmp_path, "recorded", recorded_study, plan_b, request_b, recorded=recorded)
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"]
    assert [r["id"] for r in art["results"]] == methods, "config order, not arrival order"
    assert all(r["status"] == "completed" and r["replay_parity"] for r in art["results"])
    assert sorted(art["audit"]["recorded_from"]) == sorted(methods)
    for method in methods:
        origin = art["audit"]["recorded_from"][method]
        assert origin["sha256"] == hashlib.sha256((recorded / f"{method}.json").read_bytes()).hexdigest()
        assert origin["attempt"] == 1 and origin["bundle_manifest_sha256"] == manifest["sha256"]
        assert origin["host"]["hostname"] == bodies[method]["host"]["hostname"]
    assert not _events(caplog, "recorded_rejected") and not _events(caplog, "recorded_absent")
    assert len(_events(caplog, "replay_verified")) == len(methods)
    # Same host: the derived result equals the worker's own solve and the plain run's.
    for row in art["results"]:
        assert value_hash(row["result"]) == bodies[row["id"]]["result_sha256"]
    def without_timing(result):
        result = copy.deepcopy(result)
        for decision in result["decisions"]:
            for certificate in decision.get("certificates", []):
                certificate.pop("seconds", None)
        return value_hash(result)
    plain_results = {r["id"]: without_timing(r["result"]) for r in receipt_a["results"]}
    assert {r["id"]: without_timing(r["result"]) for r in art["results"]} == plain_results
    for path, raw in frozen.items():
        assert path.read_bytes() == raw, f"closed evidence changed: {path}"
    assert registry_a.snapshot()["claims"] and registry_b.snapshot()["claims"]
    assert MIOPacketStudy.validate_params({"study": recorded_study, "recorded": "relative/dir"})
    assert not MIOPacketStudy.validate_params({"study": recorded_study, "recorded": None})
    assert MIOPacketStudy.validate_params({"study": recorded_study, "other": 1})


@pytest.mark.parametrize("study", ["hedged_program"], indirect=True)
@pytest.mark.parametrize("tamper", [False, True])
@pytest.mark.parametrize("base", ["kelly050_ls_lp", "kelly050_ls_fast_lp"])
def test_local_search_replay_hedge_worker_packet_requires_valid_stage_one(study, tmp_path, monkeypatch, tamper, base):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from test_ledger_studies import (FAST_LOCAL_FORMULATIONS, FORMULATIONS, LOCAL_FORMULATIONS, _for_kelly, _PARITY,
                                     _REPLAY_BLOCK)

    method = base.removesuffix("_lp") + "_replay_hedge_lp"
    formulation = {**LOCAL_FORMULATIONS, **FAST_LOCAL_FORMULATIONS}[base]
    params = _for_kelly(study["domain"]["params"])
    params.update(methods=[{"id": method, **formulation, "hedge": dict(_REPLAY_BLOCK)}],
                  hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    bundle = tmp_path / "bundle"
    manifest = MIOMethodBundle(study, bundle).build()
    proc = _worker(tmp_path, bundle, method, check=True)
    assert proc.returncode == 0, proc.stderr
    check = json.loads(proc.stdout)
    assert check["method"] == method and check["weeks"] >= 1
    assert len(check["objectives"][0]) == 2 and check["terminations"][0][-1] == "local_search"
    recorded = tmp_path / "recorded"
    path = recorded / f"{method}.json"
    body = json.loads(path.read_text())
    assert body["bundle_manifest_sha256"] == manifest["sha256"]
    decision = next(iter(body["actions"]["decisions"].values()))
    assert set(decision["stage1"]) == {"actions", "certificates"}
    if tamper:
        decision["stage1"]["certificates"][0]["variables"] += 1
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(raw)
        path.with_suffix(".json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")

    def no_resolve(*args, **kwargs):
        pytest.fail("a paired worker packet must never re-solve its integer program in WSL")

    monkeypatch.setattr(TransportPortfolioSolve, "run", no_resolve)
    _, plan, request = _init(study)
    doc = _close_document(tmp_path, "paired-recorded", study, plan, request, recorded=recorded)
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    receipt = _receipt(ran, doc)
    assert receipt["complete"] is not tamper
    if tamper:
        assert receipt["results"][0]["status"] == "failed"
        assert receipt["audit"]["recorded_rejections"][0]["check"]
        assert receipt["audit"]["recorded_from"] == {}
    else:
        assert receipt["results"][0]["replay_parity"]
        assert receipt["audit"]["recorded_from"][method]["bundle_manifest_sha256"] == manifest["sha256"]


@pytest.mark.parametrize("study", ["hedged_program"], indirect=True)
@pytest.mark.parametrize("tamper", [False, True])
def test_multistart_replay_hedge_worker_packet_carries_no_stage_one(study, tmp_path, monkeypatch, tamper):
    """ADR-0315: the paired multi-start worker returns its final quantities and one certificate; WSL reruns the search
    (never a solve) and a tampered quantity closes the method without complete evidence."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from test_ledger_multistart import MULTISTART_HEDGED
    from test_ledger_studies import FORMULATIONS, _for_kelly, _PARITY

    method = "kelly050_ls_multistart_replay_hedge_lp"
    params = _for_kelly(study["domain"]["params"])
    params.update(methods=[{"id": method, **MULTISTART_HEDGED[method]}],
                  hedge_parity=dict(_PARITY), locked_method_ids=list(FORMULATIONS))
    bundle = tmp_path / "bundle"
    manifest = MIOMethodBundle(study, bundle).build()
    proc = _worker(tmp_path, bundle, method, check=True)
    assert proc.returncode == 0, proc.stderr
    check = json.loads(proc.stdout)
    assert check["weeks"] >= 1 and len(check["objectives"][0]) == 1 and check["terminations"][0][-1] == "local_search"
    path = tmp_path / "recorded" / f"{method}.json"
    body = json.loads(path.read_text())
    decision = next(iter(body["actions"]["decisions"].values()))
    assert "stage1" not in decision and len(decision["certificates"]) == 1
    if tamper:
        decision["actions"] = [[0]*len(row) for row in decision["actions"]]
        raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(raw)
        path.with_suffix(".json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  " + path.name + "\n")
    monkeypatch.setattr(TransportPortfolioSolve, "run", lambda *a, **k: pytest.fail("no solve in WSL"))
    _, plan, request = _init(study)
    doc = _close_document(tmp_path, "multistart-recorded", study, plan, request, recorded=tmp_path / "recorded")
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    receipt = _receipt(ran, doc)
    assert receipt["complete"] is not tamper
    if not tamper:
        assert receipt["results"][0]["replay_parity"]
        assert receipt["audit"]["recorded_from"][method]["bundle_manifest_sha256"] == manifest["sha256"]


@pytest.mark.parametrize("tamper", ["quantity", "sidecar", "plan", "inputs", "study", "absent", "schema", "empty_sidecar",
                                    "pairs_none"])
def test_tampered_or_mismatched_recorded_falls_back_to_local_solve(study, tmp_path, caplog, monkeypatch, tamper):  # noqa: F811
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    recorded = tmp_path / "recorded"
    proc = _worker(tmp_path, bundle_root, "nominal")
    assert proc.returncode == 0, proc.stderr
    path = recorded / "nominal.json"
    body = json.loads(path.read_text())

    def resign(new_body):
        raw = json.dumps(new_body, sort_keys=True, separators=(",", ":")).encode()
        path.write_bytes(raw)
        (recorded / "nominal.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  nominal.json\n")

    if tamper == "quantity":
        day = next(d for d, e in body["actions"]["decisions"].items() if e["actions"] is not None)
        body["actions"]["decisions"][day]["actions"]["primary_quantities"][0][0] += 1
        resign(body)
    elif tamper == "sidecar":
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "plan":
        body["plan_sha256"] = "0" * 64
        resign(body)
    elif tamper == "inputs":
        body["portable_inputs_sha256"] = "0" * 64
        resign(body)
    elif tamper == "study":
        body["study_sha256"] = "0" * 64
        resign(body)
    elif tamper == "schema":
        body.pop("timeouts")
        resign(body)
    elif tamper == "absent":
        path.unlink()
    elif tamper == "empty_sidecar":  # used to raise IndexError out of the packet
        (recorded / "nominal.json.sha256").write_text("")
    elif tamper == "pairs_none":  # a non-ValueError from the solver's derivation (TypeError) must fall back too
        day = next(d for d, e in body["actions"]["decisions"].items() if e["actions"] is not None)
        body["actions"]["decisions"][day]["actions"]["pairs"] = None
        resign(body)
    registry, plan, request = _init(study)
    doc = _close_document(tmp_path, "recorded", study, plan, request, recorded=recorded)
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and art["results"][0]["status"] == "completed" and art["results"][0]["replay_parity"]
    assert art["audit"]["recorded_from"] == {}
    if tamper == "absent":
        assert _events(caplog, "recorded_absent") and not _events(caplog, "recorded_rejected")
    else:
        rejected = _events(caplog, "recorded_rejected")
        assert len(rejected) == 1, rejected
        assert not re.search(r"\d", rejected[0]["error"]), "no number in a logged refusal"
        row = {k: rejected[0][k] for k in ("method", "week", "check", "category", "ratio")}
        assert art["audit"]["recorded_rejections"] == [row], "the document hands the coordinator the same fields"
        if tamper == "quantity":  # one more contract moves the objective by more than the tolerance: pinned, not just "some refusal"
            assert row["check"] == "certificate_objective" and row["category"] == "numeric", row
        files = {"sidecar": "sidecar_digest", "empty_sidecar": "sidecar_digest", "plan": "plan_digest",
                 "inputs": "bounded_inputs", "study": "study_identity", "schema": "action_file_schema"}
        if tamper in files:
            assert (row["category"], row["check"], row["week"], row["ratio"]) == ("file", files[tamper], None, None)
        else:
            assert re.fullmatch(r"\d{4}-\d\d-\d\d", row["week"]), "a decision-week refusal names its week"
            assert row["category"] in ("invalid_trade", "tolerance", "numeric", "replay", "other", "certificate")
        if tamper == "pairs_none":
            assert (row["category"], row["check"]) == ("other", "derived_actions")
    if tamper == "absent":
        assert "recorded_rejections" not in art["audit"]
    assert len(_events(caplog, "replay_verified")) == 1
    assert registry.snapshot()["claims"], "the registry chain still validates"


@pytest.mark.parametrize("kind", ["tolerance", "trade"])
def test_a_rejection_records_its_check_week_category_and_a_ratio_never_a_dollar_value(
        study, tmp_path, caplog, monkeypatch, kind):  # noqa: F811
    """Owner ruling ~18:35: a tolerance-size miss reads as a small ratio; an invalid trade names its category."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    recorded = tmp_path / "recorded"
    assert _worker(tmp_path, bundle_root, "nominal").returncode == 0
    path = recorded / "nominal.json"
    body = json.loads(path.read_text())
    tolerance = study["domain"]["params"]["solver"]["parity_tolerance"]
    day = next(d for d, e in body["actions"]["decisions"].items() if e["actions"] is not None)
    entry = body["actions"]["decisions"][day]
    if kind == "tolerance":
        entry["certificates"][0]["objective"] += 2.5*tolerance
    else:
        entry["actions"]["primary_quantities"][0][0] = 10**6
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    (recorded / "nominal.json.sha256").write_text(hashlib.sha256(raw).hexdigest() + "  nominal.json\n")
    registry, plan, request = _init(study)
    doc = _close_document(tmp_path, "recorded", study, plan, request, recorded=recorded)
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and art["audit"]["recorded_from"] == {}, "the method was solved locally, and says so"
    [event] = _events(caplog, "recorded_rejected")
    [row] = art["audit"]["recorded_rejections"]
    assert row["week"] == day and row["method"] == "nominal"
    if kind == "tolerance":
        assert (row["category"], row["check"]) == ("tolerance", "certificate_objective")
        assert row["ratio"] == pytest.approx(2.5, rel=.05), "a tolerance-size miss is a small ratio"
    else:
        assert (row["category"], row["check"], row["ratio"]) == ("invalid_trade", "capacity", None)
    assert {k: event[k] for k in row} == row and not re.search(r"\d", event["error"])


def test_stamp_is_one_utc_format_and_heartbeat_beats_until_stopped():
    import calendar
    import time
    from index_options.mio_worker import STAMP_FORMAT, Heartbeat, stamp

    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", stamp())
    assert calendar.timegm(time.strptime(stamp(1_700_000_000.), STAMP_FORMAT)) == 1_700_000_000
    beats = []
    with Heartbeat(lambda: beats.append(1), 0.02):
        time.sleep(0.25)
    count = len(beats)
    assert count >= 3, "beats immediately and then every interval"
    time.sleep(0.1)
    assert len(beats) == count, "stopped on exit"
    try:
        with Heartbeat(lambda: beats.append(1), 0.02):
            raise RuntimeError("boom")
    except RuntimeError:
        pass


def test_blinded_cache_studies_are_refused_before_any_payload_is_read(study, tmp_path, monkeypatch):  # noqa: F811
    """ADR-0293: no bundle or worker touches forecast payloads before an active-permit verification."""
    from dskit.onboarding.artifacts import PinnedPayloadSource

    blinded = copy.deepcopy(study)
    blinded["domain"]["predictive"] = {"policy": "owner_selected_blinded_cache"}
    touched = []
    monkeypatch.setattr(PinnedPayloadSource, "file", lambda *a, **k: touched.append(a) or (_ for _ in ()).throw(AssertionError("payload read")))
    monkeypatch.setattr(StudyContract, "build", lambda *a, **k: (_ for _ in ()).throw(AssertionError("plan built")))
    with pytest.raises(ValueError, match="ADR-0293"):
        MIOMethodBundle(blinded, tmp_path / "blinded").build()
    assert not touched and not (tmp_path / "blinded").exists()
    monkeypatch.undo()
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    study_file = bundle_root / MIOMethodBundle.STUDY_FILE
    body = json.loads(study_file.read_text())
    body["domain"]["predictive"] = {"policy": "owner_selected_blinded_cache"}
    study_file.write_text(json.dumps(body, sort_keys=True))  # not re-signed: the policy check must come first
    proc = _worker(tmp_path, bundle_root, "nominal")
    assert proc.returncode != 0 and "blinded-cache study refused" in proc.stderr and "bundle file drift" not in proc.stderr, proc.stderr
    assert not (tmp_path / "recorded").exists()


def test_recorded_rejection_text_carries_no_numbers():
    """The rejection event is logged; a refusal message must not smuggle a dollar value or any number."""
    text = "recorded actions exceed capacity: $1,234.56 > 0.5e-3 at week 2019-01-02 (-17 contracts)"
    cleaned = MIOPacketStudy.scrub(text)
    assert not re.search(r"\d", cleaned) and "recorded actions exceed capacity" in cleaned


def test_a_hung_stage_stops_beating_after_its_hard_cap_so_it_looks_stale():
    import time
    from index_options.mio_worker import Heartbeat

    beats = []
    with Heartbeat(lambda: beats.append(1), 0.02, max_seconds=0.15):
        time.sleep(0.5)  # the main thread is "hung": the daemon thread must not vouch for it past the cap
        stopped_at = len(beats)
        time.sleep(0.2)
        assert len(beats) == stopped_at and 2 <= stopped_at <= 12
    assert MIOMethodWorker.HEARTBEAT_MAX_SECONDS == 3600. and MIOMethodWorker.HEARTBEAT_SECONDS == 60.
    with pytest.raises(ValueError, match="worker spec"):  # no heartbeat knobs: the period and the cap are constants
        MIOMethodWorker({"bundle_root": "/b", "method": "m", "attempt": 1, "work_dir": "/w", "out": "/o",
                         "heartbeat_max_seconds": 5})


def test_bundle_ships_no_row_dated_on_or_after_the_cap_and_never_alters_the_source(study, tmp_path, monkeypatch):  # noqa: F811
    """The fixture plants a 2026-01-01 row in every pinned file: whole-file bundling would ship it."""
    import pyarrow.parquet as pq
    from index_options import mio_contract

    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    cap = mio_contract.OWNER_TAIL_CAP
    shipped = sorted((bundle_root / "sources").rglob("*.parquet"))
    assert shipped, "payload files are bundled"
    for path in shipped:
        table = pq.read_table(path)
        for name in table.column_names:
            if name in MIOMethodBundle.DATE_COLUMNS:
                assert max(str(v) for v in table.column(name).to_pylist()) < cap, (path.name, name)
    pin = study["domain"]["sources"]["forecasts"]["pin"]
    original = next(PinnedPayloadSource(pin).directory.rglob("forecasts.parquet"))
    assert "2026-01-01" in {str(v) for v in pq.read_table(original, columns=["quote_date"]).column(0).to_pylist()}, \
        "the pinned source file itself is untouched"
    rewritten = json.loads((bundle_root / MIOMethodBundle.STUDY_FILE).read_text())
    assert rewritten["domain"]["sources"]["forecasts"]["pin"]["files"] != pin["files"], "cut copies have their own digests"
    assert MIOMethodBundle(study, bundle_root).build() == json.loads((bundle_root / MIOMethodBundle.MANIFEST_FILE).read_text()), \
        "a rebuild is idempotent"
    # a later cap that no row reaches ships the original bytes unchanged
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", "2027-01-01")
    uncut = tmp_path / "uncut"
    MIOMethodBundle(study, uncut).build()
    assert json.loads((uncut / MIOMethodBundle.STUDY_FILE).read_text())["domain"]["sources"]["forecasts"]["pin"]["files"] == pin["files"]


def test_bundle_refuses_a_packet_after_the_cap_and_a_json_payload_naming_a_later_date(study, tmp_path, monkeypatch):  # noqa: F811
    from index_options import mio_contract
    from index_options.mio_worker import PayloadCapError

    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", "2019-01-02")  # the fixture's packet ends 2019-01-08
    with pytest.raises(PayloadCapError, match="packet ending by"):
        MIOMethodBundle(study, tmp_path / "late").build()
    text = tmp_path / "events.json"
    text.write_text('[{"effective": "2024-03-01"}]')
    with pytest.raises(PayloadCapError, match="names a date"):
        MIOMethodBundle.refuse_dates_from(text, "2024-01-01", "events.json")
    text.write_text('[{"effective": "2019-03-01"}]')
    MIOMethodBundle.refuse_dates_from(text, "2024-01-01", "events.json")


def _parquet(path, **columns):
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table(columns), path)
    return path


def test_the_cap_fails_closed_on_a_date_column_it_does_not_know_by_name(tmp_path):
    """A string column named trade_date held '2024-02-01' and the file shipped whole: now it refuses."""
    from index_options.mio_worker import PayloadCapError

    sneaky = _parquet(tmp_path/"a.parquet", symbol=["X", "X"], trade_date=["2019-02-01", "2024-02-01"])
    with pytest.raises(PayloadCapError, match="trade_date"):
        MIOMethodBundle.cut_parquet(sneaky, tmp_path/"a.cut.parquet", "2024-01-01")
    # the same column on a row that a KNOWN date column already cuts away does not ship, so it does not refuse
    cut = _parquet(tmp_path/"b.parquet", date=["2019-02-01", "2024-02-01"], trade_date=["2019-02-01", "2024-02-01"])
    assert MIOMethodBundle.cut_parquet(cut, tmp_path/"b.cut.parquet", "2024-01-01") is True
    import pyarrow.parquet as pq
    assert pq.read_table(tmp_path/"b.cut.parquet").column("trade_date").to_pylist() == ["2019-02-01"]
    # a file whose unknown string columns hold no late date, and no known date column, still ships whole
    clean = _parquet(tmp_path/"c.parquet", symbol=["X"], note=["no date here"], trade_date=["2019-02-01"])
    assert MIOMethodBundle.cut_parquet(clean, tmp_path/"c.cut.parquet", "2024-01-01") is False


def test_a_late_date_in_a_known_column_is_still_cut_beside_clean_unknown_columns(tmp_path):
    import pyarrow.parquet as pq

    mixed = _parquet(tmp_path/"m.parquet", expiration=["2019-02-01", "2024-02-01"], leg=["a", "b"])
    assert MIOMethodBundle.cut_parquet(mixed, tmp_path/"m.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"m.cut.parquet").column("leg").to_pylist() == ["a"]


def test_a_dictionary_typed_string_column_is_cut_or_refused_like_a_plain_one(tmp_path):
    """dictionary<string> fell into neither list and the file shipped whole with a 2024 row."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from index_options.mio_worker import PayloadCapError

    def encoded(values):
        return pa.array(values).dictionary_encode()

    known = _parquet(tmp_path/"k.parquet", expiration=encoded(["2019-02-01", "2024-02-01"]), leg=["a", "b"])
    assert MIOMethodBundle.cut_parquet(known, tmp_path/"k.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"k.cut.parquet").column("leg").to_pylist() == ["a"]
    unknown = _parquet(tmp_path/"u.parquet", symbol=["X", "X"], trade_date=encoded(["2019-02-01", "2024-02-01"]))
    with pytest.raises(PayloadCapError, match="trade_date"):
        MIOMethodBundle.cut_parquet(unknown, tmp_path/"u.cut.parquet", "2024-01-01")
    clean = _parquet(tmp_path/"c.parquet", symbol=["X"], trade_date=encoded(["2019-02-01"]))
    assert MIOMethodBundle.cut_parquet(clean, tmp_path/"c.cut.parquet", "2024-01-01") is False
    wide = _parquet(tmp_path/"w.parquet", date=pa.array(["2019-02-01", "2025-01-01"], pa.large_string()), leg=["a", "b"])
    assert MIOMethodBundle.cut_parquet(wide, tmp_path/"w.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"w.cut.parquet").column("leg").to_pylist() == ["a"]
    dates = pa.array([dt.date(2019, 2, 1), dt.date(2024, 2, 1)]).dictionary_encode()
    typed = _parquet(tmp_path/"d.parquet", day=dates, leg=["a", "b"])
    assert MIOMethodBundle.cut_parquet(typed, tmp_path/"d.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"d.cut.parquet").column("leg").to_pylist() == ["a"]


@pytest.mark.parametrize("zone", [None, "UTC", "America/New_York", "Asia/Tokyo"])
def test_a_timestamp_column_is_cut_with_or_without_a_zone_and_never_skips_the_file(tmp_path, zone):
    """A tz-aware timestamp raised ArrowInvalid on the cap cast; the UTC instant AND the local wall clock are both cut."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    utc = dt.timezone.utc
    moments = [dt.datetime(2019, 2, 1, 15, tzinfo=utc), dt.datetime(2023, 12, 31, 12, tzinfo=utc),
               dt.datetime(2024, 1, 1, 1, tzinfo=utc), dt.datetime(2024, 6, 1, tzinfo=utc)]
    column = pa.array(moments, pa.timestamp("us", tz=zone)) if zone else pa.array(
        [m.replace(tzinfo=None) for m in moments], pa.timestamp("us"))
    path = _parquet(tmp_path/"t.parquet", at=column, leg=["a", "b", "c", "d"])
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"t.cut.parquet", "2024-01-01") is True
    kept = pq.read_table(tmp_path/"t.cut.parquet").column("leg").to_pylist()
    assert kept == ["a", "b"] or (zone == "Asia/Tokyo" and kept == ["a"]), kept  # Tokyo noon 12-31 UTC is 2024-01-01 local


@pytest.mark.parametrize("make", [
    lambda pa: pa.array([["2024-02-01"], []], pa.list_(pa.string())),
    lambda pa: pa.array([{"d": "2024-02-01"}, {"d": None}]),
    lambda pa: pa.array([b"2024-02-01", b"x"], pa.binary()),
    lambda pa: pa.array([b"2024-02-01", b"x"], pa.large_binary()),
    lambda pa: pa.array([[("k", "2024-02-01")], []], pa.map_(pa.string(), pa.string())),
    lambda pa: pa.array([[1], [2]]),
])
def test_an_unrecognized_column_type_fails_closed(tmp_path, make):
    import pyarrow as pa
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"n.parquet", mystery=make(pa), leg=["a", "b"])
    with pytest.raises(PayloadCapError, match="mystery"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"n.cut.parquet", "2024-01-01")
    assert not (tmp_path/"n.cut.parquet").exists() and not (tmp_path/"n.cut.parquet.cut").exists()


def test_plain_numeric_boolean_and_decimal_columns_do_not_refuse(tmp_path):
    import decimal
    import pyarrow as pa

    path = _parquet(tmp_path/"p.parquet", n=[1, 2], x=[0.5, 1.5], ok=[True, False],
                    price=pa.array([decimal.Decimal("1.25"), decimal.Decimal("2.5")], pa.decimal128(10, 2)))
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"p.cut.parquet", "2024-01-01") is False


@pytest.mark.parametrize("name, body, ships", [
    ("late.csv", "date,x\n2024-02-01,1\n", False),
    ("early.csv", "date,x\n2019-02-01,1\n", True),
    ("late.txt", "expires 2025-03-04", False),
    ("late.tsv", "d\t2024-01-01\n", False),
    ("packed.csv.gz", "not text we can scan", False),
    ("blob.bin", "anything", False),
])
def test_a_payload_that_is_neither_parquet_nor_a_checkable_text_file_never_ships_by_default(tmp_path, name, body, ships):
    from index_options.mio_worker import PayloadCapError

    source = tmp_path/"src"/name
    source.parent.mkdir()
    source.write_text(body)
    bundle = MIOMethodBundle({}, tmp_path/"bundle")
    files = {}
    if ships:
        bundle._put_payload(files, source, "p/"+name, "2024-01-01")
        assert (tmp_path/"bundle"/"p"/name).read_text() == body and "p/"+name in files
    else:
        with pytest.raises(PayloadCapError, match="date|cannot be checked"):
            bundle._put_payload(files, source, "p/"+name, "2024-01-01")
        assert not files and not (tmp_path/"bundle"/"p"/name).exists(), "nothing is staged on a refusal"


def test_a_failing_worker_prints_one_scrubbed_line_and_no_traceback(monkeypatch, tmp_path, capsys):
    from index_options import mio_worker

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"bundle_root": "/b", "method": "m", "attempt": 1, "work_dir": "/w", "out": "/o"}))

    def boom(self):
        raise ValueError("uncertified primary objective: optimal, gap=1234.56 and 7e-3")
    monkeypatch.setattr(MIOMethodWorker, "run", boom)
    assert mio_worker.main([str(spec)]) == 1
    captured = capsys.readouterr()
    assert captured.err == "ValueError: uncertified primary objective: optimal, gap=# and #\n"
    assert captured.out == "" and "Traceback" not in captured.err and "1234" not in captured.err


def test_a_worker_file_that_derives_but_fails_the_replay_is_solved_locally_not_failed(study, tmp_path, caplog, monkeypatch):  # noqa: F811
    """It used to mark the method failed, the packet incomplete and the arm abandoned, because of a worker file."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    assert _worker(tmp_path, bundle_root, "nominal").returncode == 0
    real = WeeklyOptionPortfolioStudy._method

    def lying(self, method, progress, *, recorded=None, derived=None, events=None):
        result = real(self, method, progress, recorded=recorded, derived=derived, events=events)
        if derived is not None:
            result = {**result, "curve": [{**point, "equity": point.get("equity", 0) + 1.5} for point in result["curve"]]}
        return result

    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_method", lying)
    registry, plan, request = _init(study)
    doc = _close_document(tmp_path, "recorded", study, plan, request, recorded=tmp_path / "recorded")
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and art["results"][0]["status"] == "completed" and art["audit"]["recorded_from"] == {}
    assert len(_events(caplog, "recorded_rejected")) == 1 and not _events(caplog, "method_failed")


# --- owner_selected_fixed_forecasts end to end: real selection, read_bounded, worker unit, document (ADR-0297) ------

def _open_fixed(spec):
    registry = ExposureRegistry(spec["registry"])
    registry.initialize(spec["packets"], [{"start": "2019-01-01", "end_before": "2024-01-01",
                                           "evidence": "synthetic prior use"}], "2024-01-01")
    plan = StudyContract.build(spec)
    return plan, registry.freeze(plan)


@pytest.mark.parametrize("quarter, seeds, names, missing", [
    ("2020Q1", (11,), ("X",), ()),
    ("2020Q1", (11, 29, 47), ("X", "Y"), ("Y",)),
    ("2023Q4", (11, 29, 47), ("X",), ()),
], ids=["2020q1-one-seed", "2020q1-three-seeds-missing-name", "2023q4-capped"])
def test_fixed_forecasts_end_to_end(tmp_path, monkeypatch, caplog, quarter, seeds, names, missing):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    world = fixed_world(tmp_path, monkeypatch, quarter, seeds=seeds, names=names, missing=missing)
    spec = world["study"]
    d, packet = spec["domain"], spec["packets"][0]
    # Real selection through the plan, then the real bounded read of the 13-point CDF.
    plan = StudyContract.build(spec)
    assert plan["runnable"]
    capped = quarter == "2023Q4"
    if capped:
        assert plan["read_bounds"]["chain"]["end_before"] == "2024-01-01"
    bounded = MIOPacketInputs.read_bounded(spec, plan["read_bounds"], packet, tmp_path / "wsl-quotes", None)
    rows = bounded["inputs"]["X"]["forecasts"]
    assert rows and all(r["fold"] == world["fold"] and r["band"] == "val" and r["fitted"] is True for r in rows)
    assert set(rows[0]) == {"symbol", "quote_date", "expiry", "fold", "band", "fitted",
                            *("method_cdf_" + label for label in d["params"]["bridge"]["grid_labels"])}
    assert len(rows) == (1 if capped else 2), "a row settling in 2024 has no forecast (owner ruling)"
    assert all(r["expiry"] < "2024-01-01" for r in rows)
    assert all(not bounded["inputs"][name]["forecasts"] for name in missing), "a missing name reads no forecast"
    # A real bundle and a real worker subprocess on the bundle's own code: the policy gets worker units.
    bundle_root = tmp_path / "bundle"
    manifest = MIOMethodBundle(spec, bundle_root).build()
    if capped:
        import pyarrow.parquet as pq
        pin = spec["domain"]["sources"]["chain"]["pin"]
        cut = next(bundle_root.glob(f"sources/*/raw/{pin['source']}/*/payload/files/chain-X.parquet"))
        whole = PinnedPayloadSource(pin).file("chain-X.parquet")
        assert max(pq.read_table(whole, columns=["expiration"])["expiration"].to_pylist()) >= "2024-01-01"
        assert max(pq.read_table(cut, columns=["expiration"])["expiration"].to_pylist()) < "2024-01-01", \
            "the bundle ships a cut copy of the market file"
    assert not list(bundle_root.glob("sources/**/receipt.json")), "only the two bound forecast files are staged"
    method = d["params"]["methods"][0]["id"]
    proc = _worker(tmp_path, bundle_root, method)
    assert proc.returncode == 0, proc.stderr
    recorded = tmp_path / "recorded"
    body = json.loads((recorded / f"{method}.json").read_text())
    assert body["plan_sha256"] == plan["sha256"] and body["bundle_manifest_sha256"] == manifest["sha256"]
    assert body["actions"]["decisions"], "the forecast reached the solver; this is not an empty week"
    # A real registry document accepts the worker's actions: it re-derives and replays them.
    plan, request = _open_fixed(spec)
    doc = _close_document(tmp_path, "fixed", spec, plan, request, recorded=recorded)
    ran = run_document(doc, asof="2023-12-31" if capped else "2020-02-15", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and [r["id"] for r in art["results"]] == [method]
    assert art["results"][0]["status"] == "completed" and art["results"][0]["replay_parity"]
    assert sorted(art["audit"]["recorded_from"]) == [method], "the document used the worker's unit"
    assert value_hash(art["results"][0]["result"]) == body["result_sha256"]
    trades = art["results"][0]["result"]["trades"]
    assert trades and all(t["symbol"] == "X" and t["close_date"] < "2024-01-01" for t in trades), "the CDF drove a real book"
    assert art["audit"].get("tail_capped_at") == ("2024-01-01" if capped else None)
    assert not _events(caplog, "recorded_rejected")


def _tamper_pin(world):
    spec, q = world["study"], world["quarter"]
    for pin in (spec["domain"]["sources"]["forecasts"]["pin"], spec["domain"]["sources"]["contexts"]["pin"],
                spec["domain"]["predictive"]["receipt"]["source"]):
        pin["files"][f"{q}/forecasts.parquet"] = "f" * 64


@pytest.mark.parametrize("kwargs, after, match", [
    ({"receipt": lambda b: b.update(bridge_fold=b["bridge_fold"] + 1)}, None, "fold"),
    ({"forecasts": lambda rows: [{**r, "fold": 3} for r in rows], "contexts": lambda rows: [{**r, "fold": 3} for r in rows]}, None, "fold"),
    ({}, lambda world: world["study"]["domain"]["params"]["bridge"].update(fold=3), "fold"),
    ({"receipt": lambda b: b.update(recipe_sha256="e" * 64)}, None, "recipe"),
    ({}, lambda world: world["study"]["domain"]["predictive"].update(recipe_sha256="e" * 64), "recipe"),
    ({}, _tamper_pin, "pin"),
    ({"contexts": lambda rows: [{**rows[0], "settlement_date": "2024-01-03"}, *rows[1:]]}, None, "2024-01-01"),
    ({"forecasts": lambda rows: [{**rows[0], "expiry": "2024-01-03"}, *rows[1:]]}, None, "2024-01-01"),
    ({"receipt": lambda b: b.update(evaluation_computed=True)}, None, "evaluation"),
], ids=["receipt-fold", "row-fold", "document-fold", "receipt-recipe", "document-recipe", "tampered-pin",
        "context-2024", "forecast-2024", "evaluation-computed"])
def test_fixed_forecasts_are_refused_before_any_bundle_or_document(tmp_path, monkeypatch, kwargs, after, match):
    world = fixed_world(tmp_path, monkeypatch, **kwargs)
    if after:
        after(world)
    spec = world["study"]
    with pytest.raises(ValueError, match=match):
        StudyContract.build(spec)
    with pytest.raises(ValueError, match=match):
        MIOMethodBundle(spec, tmp_path / "bundle").build()
    assert not (tmp_path / "bundle").exists(), "a refused forecast leaves no bundle to hand a worker"


# --- radius_scale on a worker-returned unit (ADR-0297) --------------------------------------------------------

VOL_METHOD = {"id": "vol_scaled", "rho": .5, "radius_scale": "reference_scale", "portfolio_fraction": 1.,
              "name_fraction": 1., "component_fraction": None}


def test_a_volatility_scaled_unit_returns_actions_the_document_derives_and_replays(study, tmp_path, caplog, monkeypatch):  # noqa: F811
    """rho .5 on a spot-scaled ball (50) holds no book; on the volatility ball (rho x spot x .02 = 1) it holds one."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    study["domain"]["params"]["methods"] = [VOL_METHOD]
    registry, plan, request = _init(study)
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    proc = _worker(tmp_path, bundle_root, VOL_METHOD["id"])
    assert proc.returncode == 0, proc.stderr
    recorded = tmp_path / "recorded"
    body = json.loads((recorded / "vol_scaled.json").read_text())
    held = [e for e in body["actions"]["decisions"].values()
            if e["actions"] and any(any(q) for q in e["actions"]["primary_quantities"])]
    assert held, "the volatility-scaled ball leaves a book worth holding"
    assert all(type(v) is int for e in held for q in e["actions"]["primary_quantities"] for v in q)
    doc = _close_document(tmp_path, "vol", study, plan, request, recorded=recorded)
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and sorted(art["audit"]["recorded_from"]) == ["vol_scaled"], \
        "the document derived the worker's actions on the same scale instead of solving locally"
    assert not _events(caplog, "recorded_rejected") and len(_events(caplog, "replay_verified")) == 1
    assert art["results"][0]["replay_parity"] and art["results"][0]["result"]["trades"]
    # The same document with the spot scale (and the same rho) is another study: the unit is not the document's.
    spot = copy.deepcopy(study)
    del spot["domain"]["params"]["methods"][0]["radius_scale"]
    assert MIOPacketInputs.portable_study_sha256(spot) != MIOPacketInputs.portable_study_sha256(study)


def test_fine_forecasts_run_end_to_end_and_the_solver_is_no_larger(tmp_path, monkeypatch, caplog, capsys):
    """The 25-point CDF crosses the real read, a worker unit and the document; the program size is the 13-point one."""
    from test_mio_packets import FINE_LABELS, fine_study

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    sizes, trades = {}, {}
    for grid in ("coarse", "fine"):
        base = tmp_path / grid
        world = fixed_world(base, monkeypatch, "2020Q1", fine=True)
        spec = fine_study(world) if grid == "fine" else world["study"]
        d, packet = spec["domain"], spec["packets"][0]
        plan = StudyContract.build(spec)
        assert plan["runnable"]
        bounded = MIOPacketInputs.read_bounded(spec, plan["read_bounds"], packet, base / "wsl-quotes", None)
        rows = bounded["inputs"]["X"]["forecasts"]
        assert {c for c in rows[0] if c.startswith("method_cdf_")} == {"method_cdf_" + l for l in d["params"]["bridge"]["grid_labels"]}
        assert len(d["params"]["bridge"]["grid_labels"]) == (25 if grid == "fine" else 13)
        bundle_root = base / "bundle"
        MIOMethodBundle(spec, bundle_root).build()
        method = d["params"]["methods"][0]["id"]
        proc = _worker(base, bundle_root, method)
        assert proc.returncode == 0, proc.stderr
        recorded = base / "recorded"
        body = json.loads((recorded / f"{method}.json").read_text())
        assert body["actions"]["decisions"]
        sizes[grid] = {day: (c["variables"], c["constraints"]) for day, e in body["actions"]["decisions"].items()
                       for c in e["certificates"]}
        plan, request = _open_fixed(spec)
        doc = _close_document(base, "fixed", spec, plan, request, recorded=recorded)
        caplog.clear()
        ran = run_document(doc, asof="2020-02-15", journal=False)
        assert ran.state == "ran", ran
        art = _receipt(ran, doc)
        assert art["complete"] and sorted(art["audit"]["recorded_from"]) == [method]
        assert not _events(caplog, "recorded_rejected")
        trades[grid] = art["results"][0]["result"]["trades"]
        assert trades[grid], "the CDF drove a real book"
    assert sizes["fine"] == sizes["coarse"], "the support is the listed strikes, not the CDF points: same variables and rows"
    with capsys.disabled():
        print(f"\nsolver size (variables, constraints) per decision day, 13 vs 25 points: {sizes['coarse']} | {sizes['fine']}")


# --- the two diversification formulations as worker units (owner ruling ~17:30) ------------------------------------

NAME25_METHOD = {"id": "name25", "rho": 0., "portfolio_fraction": 1., "name_fraction": .25, "component_fraction": None}
BOOK05_METHOD = {"id": "book05", "rho": 0., "portfolio_fraction": 1., "name_fraction": 1., "component_fraction": None,
                 "shock": {"moves": [-.1, .1], "loss_fraction": .05, "beta_lookback": 2, "scope": "filled_account"}}


BOOK_HELD_METHOD = {**BOOK05_METHOD, "id": "book_held", "shock": {**BOOK05_METHOD["shock"], "loss_fraction": .5}}


@pytest.mark.parametrize("method", [NAME25_METHOD, BOOK05_METHOD, BOOK_HELD_METHOD],
                         ids=["name25", "shock05_filled_account", "filled_account_with_held_books"])
def test_the_diversification_formulations_run_as_worker_units_the_document_derives_and_replays(
        study, tmp_path, caplog, monkeypatch, method):  # noqa: F811
    """The filled-account scope needs the held books' bars each week: the bundle carries them, so the worker's
    unit is derived and replayed by the document like any other (no fallback to a local solve)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    study["domain"]["params"]["methods"] = [method]
    registry, plan, request = _init(study)
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    proc = _worker(tmp_path, bundle_root, method["id"])
    assert proc.returncode == 0, proc.stderr
    recorded = tmp_path / "recorded"
    doc = _close_document(tmp_path, "diversified", study, plan, request, recorded=recorded)
    caplog.clear()
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    art = _receipt(ran, doc)
    assert art["complete"] and sorted(art["audit"]["recorded_from"]) == [method["id"]], "derived from the worker's actions"
    assert not _events(caplog, "recorded_rejected") and "recorded_rejections" not in art["audit"]
    result = art["results"][0]["result"]
    assert art["results"][0]["replay_parity"] and result["decisions"]
    if "shock" in method:
        scopes = {d["shock"]["scope"] for d in result["decisions"] if "shock" in d and "scope" in d["shock"]}
        assert scopes == {"filled_account"}, "the scope that needs the open books ran on the derived path"
        if method is BOOK_HELD_METHOD:  # the 5% cap holds nothing on the tiny synthetic account; a loose cap does
            assert any(d["shock"].get("held_ids") for d in result["decisions"] if "shock" in d), \
                "a later week priced open books from the bundle's bars"


# -- the rules-lens round: metadata pull stamps, non-ISO date text, and the boundaries of the cut -------------------------
@pytest.mark.parametrize("name", ["created_at", "acquired_at"])
@pytest.mark.parametrize("kind", ["text", "timestamp"])
def test_a_named_metadata_pull_stamp_is_never_a_market_date(tmp_path, name, kind):
    """Major 2: real files carry 2026 pull stamps; they raised PayloadCapError (text) or cut every row (timestamp)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    assert name in MIOMethodBundle.METADATA_STAMP_COLUMNS
    stamps = (["2026-11-05T08:00:00Z"] * 2 if kind == "text" else
              pa.array([dt.datetime(2026, 11, 5, 8)] * 2, pa.timestamp("us")))
    path = _parquet(tmp_path/"a.parquet", date=["2019-02-01", "2019-02-02"], leg=["a", "b"], **{name: stamps})
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"a.cut.parquet", "2024-01-01") is False, "ships whole, rows kept"
    assert MIOMethodBundle.metadata_stamps(pq.read_schema(path)) == [name], "and the exemption is named, not silent"
    # beside a market date past the cap the market date still cuts the row, and the stamp is carried along untouched
    mixed = _parquet(tmp_path/"m.parquet", date=["2019-02-01", "2024-02-01"], leg=["a", "b"], **{name: stamps})
    assert MIOMethodBundle.cut_parquet(mixed, tmp_path/"m.cut.parquet", "2024-01-01") is True
    cut = pq.read_table(tmp_path/"m.cut.parquet")
    assert cut.column("leg").to_pylist() == ["a"] and name in cut.column_names


def test_an_unnamed_column_with_a_date_at_or_past_the_cap_still_refuses_beside_a_metadata_stamp(tmp_path):
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"u.parquet", date=["2019-02-01"], created_at=["2026-11-05"], fetched_on=["2026-11-05"])
    with pytest.raises(PayloadCapError, match="fetched_on"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"u.cut.parquet", "2024-01-01")


def test_the_metadata_stamp_list_is_explicit_and_every_exempted_column_is_recorded_in_the_bundle(tmp_path):
    assert MIOMethodBundle.METADATA_STAMP_COLUMNS == ("created_at", "acquired_at")
    assert not set(MIOMethodBundle.METADATA_STAMP_COLUMNS) & set(MIOMethodBundle.DATE_COLUMNS), "never also a market date"
    source = _parquet(tmp_path/"src.parquet", date=["2019-02-01"], created_at=["2026-11-05"], acquired_at=["2026-11-05"])
    bundle = MIOMethodBundle({}, tmp_path/"bundle")
    files = {}
    bundle._put_payload(files, source, "p/src.parquet", "2024-01-01")
    assert bundle.metadata_stamp_columns == {"p/src.parquet": ["created_at", "acquired_at"]}


def test_known_at_is_an_availability_date_so_it_is_cut_or_refused_like_any_date_column(tmp_path):
    """known_at is a point-in-time availability date in this repo (contracts.py, cdf_study.py), not a pull stamp."""
    import pyarrow as pa
    from index_options.mio_worker import PayloadCapError

    assert "known_at" not in MIOMethodBundle.METADATA_STAMP_COLUMNS
    stamps = pa.array([dt.datetime(2019, 2, 1), dt.datetime(2026, 11, 5)], pa.timestamp("us"))
    typed = _parquet(tmp_path/"t.parquet", leg=["a", "b"], known_at=stamps)
    assert MIOMethodBundle.cut_parquet(typed, tmp_path/"t.cut.parquet", "2024-01-01") is True, "the 2026 row is cut"
    text = _parquet(tmp_path/"x.parquet", date=["2019-02-01"], known_at=["2026-11-05"])
    with pytest.raises(PayloadCapError, match="known_at"):
        MIOMethodBundle.cut_parquet(text, tmp_path/"x.cut.parquet", "2024-01-01")


@pytest.mark.parametrize("values", [["2019-02-01", "Jan 5 2025"], ["12/31/2019"], ["20250105"], ["2019-02-01", ""],
                                    ["2019-2-1"], ["2019-02-01 garbage"], ["x2019-02-01"]])
@pytest.mark.parametrize("name", ["date", "quote_date", "expiry", "expiration", "settlement_date"])
def test_a_named_date_column_holding_non_iso_text_refuses(tmp_path, name, values):
    """C6: text compared as strings let '12/31/2019' pass and 'Jan 5 2025' fall out by accident."""
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"n.parquet", **{name: values}, leg=["a"]*len(values))
    with pytest.raises(PayloadCapError, match=f"{name}.*ISO"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"n.cut.parquet", "2024-01-01")
    assert not (tmp_path/"n.cut.parquet").exists()


def test_iso_dates_with_a_time_part_and_nulls_in_a_named_column_are_accepted(tmp_path):
    import pyarrow.parquet as pq

    path = _parquet(tmp_path/"i.parquet", date=["2019-02-01T10:00:00", "2023-12-31T23:59:59", "2024-01-01T00:00:00", None],
                    leg=["a", "b", "c", "d"])
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"i.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"i.cut.parquet").column("leg").to_pylist() == ["a", "b", "d"], "a null date is kept"


def test_the_cap_day_itself_is_cut_in_every_date_type_and_the_day_before_is_kept(tmp_path):
    """Boundary: ``<`` not ``<=`` (a row dated exactly 2024-01-01 is the first protected day)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    cases = {"text": pa.array(["2023-12-31", "2024-01-01"]),
             "date": pa.array([dt.date(2023, 12, 31), dt.date(2024, 1, 1)]),
             "naive": pa.array([dt.datetime(2023, 12, 31, 23, 59, 59), dt.datetime(2024, 1, 1)], pa.timestamp("us")),
             "utc": pa.array([dt.datetime(2023, 12, 31, 23, 59, 59, tzinfo=dt.timezone.utc),
                              dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)], pa.timestamp("us", tz="UTC"))}
    for kind, column in cases.items():
        path = _parquet(tmp_path/f"{kind}.parquet", date=column, leg=["before", "cap"])
        assert MIOMethodBundle.cut_parquet(path, tmp_path/f"{kind}.cut.parquet", "2024-01-01") is True, kind
        assert pq.read_table(tmp_path/f"{kind}.cut.parquet").column("leg").to_pylist() == ["before"], kind


def test_a_zoned_timestamp_is_cut_on_its_wall_clock_date_and_on_its_utc_instant_separately(tmp_path):
    """Wall date at or past the cap with the instant before it (Tokyo), and the reverse (New York): each is cut."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    utc = dt.timezone.utc
    tokyo = _parquet(tmp_path/"t.parquet", at=pa.array([dt.datetime(2023, 12, 1, tzinfo=utc), dt.datetime(2023, 12, 31, 20, tzinfo=utc)],
                                 pa.timestamp("us", tz="Asia/Tokyo")), leg=["keep", "a"])  # 2023-12-31 20:00 UTC is 2024-01-01 05:00 in Tokyo: the instant is before the cap, the wall date is not
    assert MIOMethodBundle.cut_parquet(tokyo, tmp_path/"t.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"t.cut.parquet").column("leg").to_pylist() == ["keep"]
    york = _parquet(tmp_path/"y.parquet", at=pa.array([dt.datetime(2023, 12, 1, tzinfo=utc), dt.datetime(2024, 1, 1, 2, tzinfo=utc)],
                                pa.timestamp("us", tz="America/New_York")), leg=["keep", "a"])  # wall 2023-12-31 21:00, instant 2024-01-01 02:00 UTC
    assert MIOMethodBundle.cut_parquet(york, tmp_path/"y.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"y.cut.parquet").column("leg").to_pylist() == ["keep"]
    both = _parquet(tmp_path/"b.parquet", at=pa.array([dt.datetime(2023, 12, 30, 12, tzinfo=utc)], pa.timestamp("us", tz="Asia/Tokyo")),
                    leg=["a"])
    assert MIOMethodBundle.cut_parquet(both, tmp_path/"b.cut.parquet", "2024-01-01") is False, "before on both clocks: kept"


def test_an_unknown_string_column_naming_exactly_the_cap_day_refuses_and_the_day_before_does_not(tmp_path):
    """Boundary of the unknown-string scan: ``>=`` (a ``>`` would ship a row dated 2024-01-01)."""
    from index_options.mio_worker import PayloadCapError

    edge = _parquet(tmp_path/"e.parquet", symbol=["X"], note=["settles 2024-01-01"])
    with pytest.raises(PayloadCapError, match="note"):
        MIOMethodBundle.cut_parquet(edge, tmp_path/"e.cut.parquet", "2024-01-01")
    before = _parquet(tmp_path/"p.parquet", symbol=["X"], note=["settles 2023-12-31"])
    assert MIOMethodBundle.cut_parquet(before, tmp_path/"p.cut.parquet", "2024-01-01") is False


def test_a_column_of_an_unknown_type_stops_the_whole_payload_and_stages_nothing(tmp_path):
    """The unknown-type refusal through ``_put_payload`` (the build path), not only ``cut_parquet``."""
    import pyarrow as pa
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"src.parquet", leg=["a"], mystery=pa.array([[1]]))
    bundle, files = MIOMethodBundle({}, tmp_path/"bundle"), {}
    with pytest.raises(PayloadCapError, match="mystery"):
        bundle._put_payload(files, path, "p/src.parquet", "2024-01-01")
    assert not files and not (tmp_path/"bundle"/"p"/"src.parquet").exists()


# -- skeptic round 3: the date cut's edges (E, F-w) ------------------------------------------------------------------------
def test_a_named_stamp_column_of_a_nested_type_still_refuses(tmp_path):
    """E: the stamp exemption skipped the type check, so a struct or list named ``created_at`` shipped unchecked."""
    import pyarrow as pa
    from index_options.mio_worker import PayloadCapError

    for values in (pa.array([{"when": "2026-11-05"}]), pa.array([["2026-11-05"]])):
        path = _parquet(tmp_path/"n.parquet", date=["2019-02-01"], created_at=values)
        with pytest.raises(PayloadCapError, match="created_at"):
            MIOMethodBundle.cut_parquet(path, tmp_path/"n.cut.parquet", "2024-01-01")


def test_stamp_names_match_exactly_so_a_capitalised_stamp_is_a_market_date_and_an_emptied_file_refuses(tmp_path):
    """E and w3: ``Created_At`` is not in the list. As a timestamp it is cut; a cut that empties the file refuses (it used to write
    an empty parquet and ship it)."""
    import pyarrow as pa
    from index_options.mio_worker import PayloadCapError

    stamps = pa.array([dt.datetime(2026, 11, 5, 8)] * 2, pa.timestamp("us"))
    path = _parquet(tmp_path/"c.parquet", leg=["a", "b"], Created_At=stamps)
    assert MIOMethodBundle.metadata_stamps(__import__("pyarrow.parquet", fromlist=["x"]).read_schema(path)) == []
    with pytest.raises(PayloadCapError, match="every row"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"c.cut.parquet", "2024-01-01")
    assert not (tmp_path/"c.cut.parquet").exists()


def test_a_file_emptied_by_a_recognised_market_date_column_ships_empty_with_its_schema_and_is_recorded(tmp_path):
    """Major 4: a file whose rows are all at or past the cap is a legitimate empty cut, not a refusal of the whole arm."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    for kind, column in {"text": ["2024-01-01", "2025-03-01"], "date": pa.array([dt.date(2024, 1, 1), dt.date(2025, 3, 1)]),
                         "zoned": pa.array([dt.datetime(2024, 1, 2), dt.datetime(2026, 1, 1)], pa.timestamp("us", tz="UTC"))}.items():
        path = _parquet(tmp_path/f"{kind}.parquet", date=column, leg=["a", "b"], created_at=["2026-11-05"] * 2)
        assert MIOMethodBundle.cut_parquet(path, tmp_path/f"{kind}.cut.parquet", "2024-01-01") is True, kind
        out = pq.read_table(tmp_path/f"{kind}.cut.parquet")
        assert out.num_rows == 0 and out.schema.equals(pq.read_schema(path)), kind
    source = _parquet(tmp_path/"late.parquet", date=["2025-01-01"], leg=["a"])
    bundle, files = MIOMethodBundle({}, tmp_path/"bundle"), {}
    bundle._put_payload(files, source, "p/late.parquet", "2024-01-01")
    assert pq.read_table(tmp_path/"bundle"/"p"/"late.parquet").num_rows == 0 and "p/late.parquet" in files
    assert bundle.emptied_payload_files == ["p/late.parquet"]


def test_an_emptied_file_is_recorded_in_identity_json(tmp_path, monkeypatch, study):  # noqa: F811
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    bundle = MIOMethodBundle(study, tmp_path / "bundle")
    bundle.emptied_payload_files.append("sources/root0/x.parquet")  # what _put_payload appends for an emptied file
    bundle.build()
    identity = json.loads((tmp_path / "bundle" / "identity.json").read_text())
    assert identity["emptied_payload_files"] == ["sources/root0/x.parquet"]


@pytest.mark.parametrize("columns", [
    {"Created_At": [dt.datetime(2026, 11, 5)] * 2},                      # a case variant of a stamp: not exempt, not a market date
    {"fetched": [dt.datetime(2026, 11, 5)] * 2},                         # an unrecognised timestamp name
    {"known_at": [dt.date(2025, 1, 1)] * 2},                             # an availability date, not a market date
    {"Date": ["2025-01-01"] * 2}])                                        # a case variant of a market-date name
def test_an_emptied_cut_from_an_unrecognised_column_still_refuses(tmp_path, columns):
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"u.parquet", leg=["a", "b"], **columns)
    with pytest.raises(PayloadCapError, match="every row|not a known date column"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"u.cut.parquet", "2024-01-01")
    assert not (tmp_path/"u.cut.parquet").exists()


def test_a_recognised_column_that_keeps_rows_cannot_hide_an_unrecognised_one_emptying_the_file(tmp_path):
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"m.parquet", date=["2019-01-01", "2019-01-02"], leg=["a", "b"],
                    Created_At=[dt.datetime(2026, 11, 5)] * 2)
    with pytest.raises(PayloadCapError, match="every row"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"m.cut.parquet", "2024-01-01")


def test_a_lone_late_row_is_an_empty_cut_in_a_zoned_column_and_a_lone_early_row_is_kept(tmp_path):
    """Item 10: the single-row case, zoned, behaves as item 4 defines."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    late = _parquet(tmp_path/"l.parquet", date=pa.array([dt.datetime(2024, 1, 1, 12)], pa.timestamp("us", tz="America/New_York")), leg=["a"])
    assert MIOMethodBundle.cut_parquet(late, tmp_path/"l.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"l.cut.parquet").num_rows == 0
    early = _parquet(tmp_path/"e.parquet", date=pa.array([dt.datetime(2019, 1, 1, 12)], pa.timestamp("us", tz="America/New_York")), leg=["a"])
    assert MIOMethodBundle.cut_parquet(early, tmp_path/"e.cut.parquet", "2024-01-01") is False


def test_refuse_dates_from_refuses_a_text_payload_over_256_mib_without_reading_it(tmp_path, monkeypatch):
    from index_options.mio_worker import PayloadCapError

    big = tmp_path/"big.json"
    big.write_text("{}")
    monkeypatch.setattr(os.path, "getsize", lambda p: 256 * 2**20 + 1)
    with pytest.raises(PayloadCapError, match="too large"):
        MIOMethodBundle.refuse_dates_from(big, "2024-01-01", "big.json")
    monkeypatch.setattr(os.path, "getsize", lambda p: 256 * 2**20)
    MIOMethodBundle.refuse_dates_from(big, "2024-01-01", "big.json")  # exactly the limit still reads


@pytest.mark.parametrize("text, expect_cut", [("2023-12-31T20:00:00-05:00", True), ("2023-12-31T20:00:00-0500", True),
                                              ("2023-12-31T18:00:00-05:00", False), ("2024-01-01T01:00:00+02:00", True),
                                              ("2023-12-31T23:30:00Z", False)])
def test_zoned_date_text_is_cut_on_its_utc_instant_like_a_zoned_timestamp(tmp_path, text, expect_cut):
    """E: 20:00 in New York on 2023-12-31 is 2024-01-01 01:00 UTC; comparing the text as a string kept the row."""
    import pyarrow.parquet as pq

    path = _parquet(tmp_path/"z.parquet", date=["2019-02-01", text], leg=["keep", "row"])
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"z.cut.parquet", "2024-01-01") is expect_cut
    if expect_cut:
        assert pq.read_table(tmp_path/"z.cut.parquet").column("leg").to_pylist() == ["keep"]


def test_an_unnamed_text_column_is_scanned_for_every_date_in_each_value_not_only_the_first(tmp_path):
    from index_options.mio_worker import PayloadCapError

    path = _parquet(tmp_path/"s.parquet", date=["2019-02-01"], note=["2019-05-01 to 2025-03-01"])
    with pytest.raises(PayloadCapError, match="note"):
        MIOMethodBundle.cut_parquet(path, tmp_path/"s.cut.parquet", "2024-01-01")
    fine = _parquet(tmp_path/"f.parquet", date=["2019-02-01"], note=["2019-05-01 to 2019-06-01"])
    assert MIOMethodBundle.cut_parquet(fine, tmp_path/"f.cut.parquet", "2024-01-01") is False


def test_a_null_zoned_timestamp_keeps_its_row_and_a_text_date_exactly_at_the_cap_refuses(tmp_path):
    """w6: a null in a zoned column is kept (not dropped); w13: ``>=`` in the text scan, a date ON the cap refuses."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from index_options.mio_worker import PayloadCapError

    zoned = pa.array([dt.datetime(2019, 2, 1), None, dt.datetime(2026, 1, 1)], pa.timestamp("us", tz="UTC"))
    path = _parquet(tmp_path/"n.parquet", leg=["a", "null", "late"], at=zoned)
    assert MIOMethodBundle.cut_parquet(path, tmp_path/"n.cut.parquet", "2024-01-01") is True
    assert pq.read_table(tmp_path/"n.cut.parquet").column("leg").to_pylist() == ["a", "null"]
    on_cap = tmp_path/"cap.json"
    on_cap.write_text('{"d": "2024-01-01"}')
    with pytest.raises(PayloadCapError):
        MIOMethodBundle.refuse_dates_from(on_cap, "2024-01-01", "cap.json")
    before = tmp_path/"before.json"
    before.write_text('{"d": "2023-12-31"}')
    MIOMethodBundle.refuse_dates_from(before, "2024-01-01", "before.json")


def test_check_mode_prints_per_week_objectives_and_termination_words_not_an_actions_digest(tmp_path, monkeypatch, study):  # noqa: F811
    """Mac certificate (owner ruling): a short worker check compared within a tolerance, never by an exact match of actions."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    bundle_root = tmp_path / "bundle"
    MIOMethodBundle(study, bundle_root).build()
    method = study["domain"]["params"]["methods"][0]["id"]
    first = _worker(tmp_path, bundle_root, method, check=True)
    (tmp_path / "again").mkdir()
    second = _worker(tmp_path / "again", bundle_root, method, check=True)
    assert first.returncode == 0 and second.returncode == 0, (first.stderr, second.stderr)
    shown = json.loads(first.stdout)
    assert set(shown) == {"method", "weeks", "objectives", "terminations"} and shown["method"] == method
    assert shown["weeks"] == len(shown["objectives"]) == len(shown["terminations"]) >= 1
    assert all(isinstance(week, list) and week and all(w is None or isinstance(w, (int, float)) for w in week)
               for week in shown["objectives"]), "every certificate of every week, not only the first"
    assert all(isinstance(week, list) and all(isinstance(word, str) for word in week) for week in shown["terminations"])
    assert "actions_sha256" not in first.stdout
    flat = lambda doc: [w for week in doc["objectives"] for w in week]  # noqa: E731
    assert json.loads(second.stdout) == shown or all(
        abs((a or 0) - (b or 0)) <= .01 * max(1., abs(a or 0), abs(b or 0))
        for a, b in zip(flat(shown), flat(json.loads(second.stdout)))), "two runs agree within 1%"
    compute_run = _skill_module_or_none()
    if compute_run is not None:  # the shared skill's own comparison, with the tolerance the owner's gate uses
        near = json.loads(first.stdout)
        far = {**near, "objectives": [[(w or 0) * 1.05 + 5 for w in week] for week in near["objectives"]]}
        assert compute_run.compare_outputs(first.stdout.encode(), first.stdout.encode(), .01)[0]
        assert not compute_run.compare_outputs(first.stdout.encode(), json.dumps(far).encode(), .01)[0]
        drift = {**near, "objectives": [[(w or 0) * 1.004 for w in week] for week in near["objectives"]]}
        assert compute_run.compare_outputs(first.stdout.encode(), json.dumps(drift).encode(), .01)[0], "inside 1%: passes"


@pytest.mark.parametrize("decisions", [
    {},                                                                                      # zero weeks
    {"2020-01-06": {"actions": {}, "certificates": []}},                                     # a week with no certificate
    {"2020-01-06": {"actions": {}, "certificates": [{"objective": 1., "termination": "optimal"}]},
     "2020-01-13": {"actions": {}, "certificates": []}}])                                    # one empty week among solved ones
def test_check_mode_exits_nonzero_when_no_week_was_solved(tmp_path, monkeypatch, capsys, decisions):
    from index_options import mio_worker

    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    monkeypatch.setattr(mio_worker.MIOMethodWorker, "__init__", lambda self, spec: None)
    monkeypatch.setattr(mio_worker.MIOMethodWorker, "run", lambda self: {"method": "m", "actions": {"decisions": decisions}})
    assert mio_worker.main([str(spec), "--check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "no solved week" in captured.err


def test_check_mode_reports_every_certificate_of_a_week(tmp_path, monkeypatch, capsys):
    from index_options import mio_worker

    spec = tmp_path / "spec.json"
    spec.write_text("{}")
    certs = [{"objective": 1., "termination": "optimal"}, {"objective": 2., "termination": "maxTimeLimit"}]
    monkeypatch.setattr(mio_worker.MIOMethodWorker, "__init__", lambda self, spec: None)
    monkeypatch.setattr(mio_worker.MIOMethodWorker, "run",
                        lambda self: {"method": "m", "actions": {"decisions": {"2020-01-06": {"actions": {}, "certificates": certs}}}})
    assert mio_worker.main([str(spec), "--check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"method": "m", "weeks": 1, "objectives": [[1., 2.]],
                                                   "terminations": [["optimal", "maxTimeLimit"]]}


def _skill_module_or_none():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import mio_parallel_queue
    real = "/mnt/c/Users/russe/.codex/skills/mac-compute-worker/scripts"
    return mio_parallel_queue._skill_module(real, "compute_run") if os.path.isdir(real) else None


def test_a_check_week_result_is_not_digested_as_evidence_by_the_worker():
    params, inputs = _packet_clock_fixture()
    params["decision_weeks"] = ["2019-01-02"]
    result = WeeklyOptionPortfolioStudy(params, inputs)._method(params["methods"][0], None)
    assert "decision_weeks" in result
    with pytest.raises(ValueError, match="check_document_not_evidence"):
        MIOMethodWorker.evidence_guard(result, False)
    assert MIOMethodWorker.evidence_guard(result, True) is True        # a --check timing run says so
    plain, inputs = _packet_clock_fixture()
    assert MIOMethodWorker.evidence_guard(
        WeeklyOptionPortfolioStudy(plain, inputs)._method(plain["methods"][0], None), False) is False


def _priced_method(study, method):
    """Configure ``study`` for one Kelly local-search method (ADR-0307, its FAST variant or ADR-0315) and return it."""
    from test_ledger_multistart import MULTISTART_HEDGED
    from test_ledger_studies import (FAST_LOCAL_FORMULATIONS, FORMULATIONS, LOCAL_FORMULATIONS, _for_kelly, _PARITY,
                                     _REPLAY_BLOCK)

    if method in MULTISTART_HEDGED:
        formulation = MULTISTART_HEDGED[method]
    else:
        base = method.replace("_replay_hedge_lp", "_lp")
        formulation = {**LOCAL_FORMULATIONS, **FAST_LOCAL_FORMULATIONS}[base]
        if base != method:
            formulation = {**formulation, "hedge": dict(_REPLAY_BLOCK)}
    params = _for_kelly(study["domain"]["params"])
    params.update(methods=[{"id": method, **formulation}], locked_method_ids=list(FORMULATIONS))
    if "hedge" in formulation:
        params["hedge_parity"] = dict(_PARITY)
    return study


@pytest.mark.parametrize("study", ["hedged_program"], indirect=True)
@pytest.mark.parametrize("method", ["kelly050_ls_lp", "kelly050_ls_replay_hedge_lp", "kelly050_ls_fast_replay_hedge_lp",
                                    "kelly050_ls_multistart_replay_hedge_lp"])
def test_worker_pricing_equals_the_wsl_priced_replay_of_the_same_actions_exactly(study, tmp_path, monkeypatch, method):
    """ADR-0297 Amendment 1: a worker that prices its own actions writes an UNOFFICIAL file whose full result and
    economics digests equal the WSL official result of the same action file, bit for bit on one host; the action file
    and the WSL path are untouched (WSL accepts it, never re-solves, and the receipt carries no worker marker)."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from index_options.mio_worker import PRICED_FORMAT, UNOFFICIAL_LABEL

    study = _priced_method(study, method)
    bundle = tmp_path / "bundle"
    manifest = MIOMethodBundle(study, bundle).build()
    files_before = set(json.loads((bundle / MIOMethodBundle.MANIFEST_FILE).read_text())["files"])
    priced_path = tmp_path / "priced" / f"{method}.priced.json"
    proc = _worker(tmp_path, bundle, method, priced_out=priced_path)
    assert proc.returncode == 0, proc.stderr
    recorded = tmp_path / "recorded"
    assert (recorded / f"{method}.json.done").exists() and not Path(str(priced_path) + ".refused").exists()
    raw = priced_path.read_bytes()
    assert Path(str(priced_path) + ".sha256").read_text().split()[0] == hashlib.sha256(raw).hexdigest()
    priced = json.loads(raw)
    assert priced["format"] == PRICED_FORMAT and priced["unofficial"] is True and priced["official"] is False
    assert priced["priced_on"] in {"mac", "worker"} and priced["priced_on"] == MIOMethodWorker.priced_on()
    assert priced["label"] == UNOFFICIAL_LABEL
    action_raw = (recorded / f"{method}.json").read_bytes()
    assert priced["action_file_sha256"] == hashlib.sha256(action_raw).hexdigest()
    action = json.loads(action_raw)
    assert "unofficial" not in action and "economics" not in action, "the action file format is unchanged"
    assert priced["bundle_manifest_sha256"] == manifest["sha256"] == action["bundle_manifest_sha256"]
    assert priced["economics"]["contracts"] > 0 and priced["economics"]["pnl"] != 0, "a priced book, not cash"
    assert sum(w["pnl"] for w in priced["economics"]["per_week"]) == pytest.approx(priced["economics"]["pnl"], abs=1e-6)
    # WSL official pricing of the SAME action file (never a re-solve).
    monkeypatch.setattr(TransportPortfolioSolve, "run", lambda *a, **k: pytest.fail("no solve in WSL"))
    _, plan, request = _init(study)
    doc = _close_document(tmp_path, "priced-recorded", study, plan, request, recorded=recorded)
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    receipt = _receipt(ran, doc)
    assert receipt["complete"] and receipt["results"][0]["replay_parity"]
    official = receipt["results"][0]["result"]
    origin = receipt["audit"]["recorded_from"][method]
    compared = MIOMethodWorker.compare_priced(priced, official, origin)
    assert compared["result_identical"] and compared["economics_identical"], compared
    assert compared["abs_diff_usd"] == {"pnl": 0., "stress_pnl": 0., "final_equity": 0.}
    assert MIOMethodWorker.economics(official) == priced["economics"]
    assert "unofficial" not in json.dumps(receipt), "the official receipt carries no worker-pricing marker"
    assert files_before == set(json.loads((bundle / MIOMethodBundle.MANIFEST_FILE).read_text())["files"]), \
        "pricing ships no extra bundle file"
    with pytest.raises(ValueError, match="another action file"):
        MIOMethodWorker.compare_priced(priced, official, {**origin, "sha256": "0"*64})
    with pytest.raises(ValueError, match="unofficial"):
        MIOMethodWorker.compare_priced({**priced, "unofficial": False}, official)
    with pytest.raises(ValueError, match="another method"):
        MIOMethodWorker.compare_priced({**priced, "method": "other"}, official)


def test_worker_pricing_is_opt_in_validated_and_a_refusal_keeps_the_action_file(tmp_path, monkeypatch):
    base = {"bundle_root": str(tmp_path / "b"), "method": "m", "attempt": 1, "work_dir": str(tmp_path / "w"),
            "out": str(tmp_path / "out" / "m.json")}
    for bad in ("relative/m.priced.json", base["out"], 3):
        with pytest.raises(ValueError, match="priced_out"):
            MIOMethodWorker({**base, "priced_out": bad})
    worker = MIOMethodWorker({**base, "priced_out": str(tmp_path / "priced" / "m.priced.json")})
    worker.timing_check = True
    assert worker._write_priced(None, None, None, None, {}, b"") is None
    assert not (tmp_path / "priced" / "m.priced.json").exists()
    assert "timing" in (tmp_path / "priced" / "m.priced.json.refused").read_text()
    import index_options.mio_worker as mw
    monkeypatch.setattr(mw.platform, "system", lambda: "Darwin")
    assert MIOMethodWorker.priced_on() == "mac"
    monkeypatch.setattr(mw.platform, "system", lambda: "Linux")
    assert MIOMethodWorker.priced_on() == "worker"

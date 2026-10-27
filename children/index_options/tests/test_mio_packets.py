"""Synthetic execution of the same JSON packet spine used by real debugging."""

import copy
import json
from pathlib import Path

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.artifacts import payload_files
from dskit.pipeline.base import value_hash
from dskit.pipeline.document import load_document
from dskit.pipeline.driver import run_document, resolve_json_artifact, RunAttestation
from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract
from dskit.pipeline.workflow import path_hash
from index_options.observations import MIOPacketInputs
from index_options.ledger_studies import MIOPacketStudy, WeeklyOptionPortfolioStudy
from test_ledger_studies import _packet_clock_fixture


@pytest.fixture
def study(tmp_path, request):
    import pyarrow as pa
    import pyarrow.parquet as pq

    params, data = _packet_clock_fixture()
    archive = tmp_path/"origin"
    archive.mkdir()
    sessions = params.pop("clocks")
    pairs = params.pop("selected_pairs")
    params["bridge"]["end_before"] = sessions["entry_end_before"]
    if getattr(request, "param", None) == "expiry_basis":
        data["X"]["forecasts"] = data["X"]["forecasts"][:1]
        data["X"]["contexts"] = data["X"]["contexts"][:1]
        data["X"]["chain"] = [r for r in data["X"]["chain"]
                              if not (r["date"] == "2019-02-01" and r["expiration"] == "2019-02-01")]
    for key, rows in data["X"].items():
        # A protected row in the same file must never materialize.
        future = copy.deepcopy(rows[0])
        for col in ("date", "quote_date", "expiry", "expiration", "settlement_date"):
            if col in future:
                future[col] = "2026-01-01"
        pq.write_table(pa.Table.from_pylist(rows+[future]), archive/(key+".parquet"))
    pq.write_table(pa.table({"date": sessions["sessions"]+["2026-01-01"]}), archive/"sessions.parquet")
    root = OnboardingRoot.create(str(tmp_path/"ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "fixture", "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(archive), "as_of": "2026-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "fixture", "files", "backfill")
    acquired = payload_files(root, "fixture", "files")
    pin = {"root": root.root, "source": "fixture", "stream": "files",
           "snapshot": acquired["snapshot"], "manifest_sha256": acquired["manifest_sha256"],
           "files": acquired["sha256"]}
    sources = {role: {"pin": copy.deepcopy(pin),
                     "files": {("X" if role in {"bars", "chain"} else "shared"): role+".parquet"}}
               for role in ("forecasts", "contexts", "chain", "bars", "sessions")}
    return {"contract": "index_options.mio_contract:MIOStudyContract",
            "registry": {"path": str(tmp_path/"exposure.sqlite"),
                         "id": "27e530ba-7ed9-4fd0-93bd-4b91ffdc1cf0", "domain": "synthetic:packet"},
            "packets": [{"id": "dev", "start": "2019-01-02", "end_before": "2019-01-08",
                         "purpose": "development", "predecessor": None}],
            "selected_packet": "dev", "code": {__file__: path_hash(__file__)},
            "dependencies": {"pytest": pytest.__version__},
            "domain": {"params": params, "pairs": pairs, "history_start": sessions["history_start"],
                       "tail_padding_days": 0, "sources": sources, "synthetic": True,
                       "predictive": {"policy": "synthetic_fixture"},
                       "admission": {"mode": "debug_fixture_not_admitted", "evidence": None},
                       "report": {"primary_metric": None}}}


def _open(study):
    registry = ExposureRegistry(study["registry"])
    registry.initialize(study["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01",
                                           "evidence": "synthetic prior use"}], "2020-01-01")
    plan = StudyContract.build(study)
    permit = registry.claim(plan, registry.freeze(plan))
    return registry, plan, permit


def _document(tmp_path, name, pipeline):
    path = tmp_path/(name+".json")
    path.write_text(json.dumps({"name": name, "pipeline": pipeline,
                               "outputs": {"run_root": str(tmp_path/"runs")}}))
    return load_document(str(path))


@pytest.mark.parametrize("study", [None, "expiry_basis"], indirect=True)
def test_real_json_spine_and_attested_comparison(study, tmp_path):
    registry = ExposureRegistry(study["registry"])
    registry.initialize(study["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01",
                                           "evidence": "synthetic prior use"}], "2020-01-01")
    plan = StudyContract.build(study)
    request = registry.freeze(plan)
    close = _document(tmp_path, "publish", {
        "open_packet": {"uses": "dskit.pipeline.study_protocol:StudyPacketOpen",
                       "params": {"study": study, "frozen_plan": plan, "claim_request": request}},
        "intake": {"uses": "index_options.observations:MIOPacketInputs",
                   "params": {"study": study}, "inputs": {"permit": "$open_packet.permit"}},
        "study": {"uses": "index_options.ledger_studies:MIOPacketStudy", "params": {"study": study},
                  "inputs": {"permit": "$open_packet.permit", "bounded_inputs": "$intake.bounded_inputs",
                             "provenance": "$intake.provenance"}},
        "close": {"uses": "dskit.pipeline.study_protocol:StudyPacketClose", "params": {"study": study},
                  "inputs": {"permit": "$open_packet.permit", "results": "$study.results", "audit": "$study.audit",
                             "provenance": "$intake.provenance"}}})
    ran = run_document(close, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    receipt = {"run_dir": str(ran.run_dir), "document_hash": close.hash, "node": "close", "port": "receipt"}
    compare = _document(tmp_path, "compare", {
        "results": {"uses": "dskit.pipeline.study_protocol:StudyPacketResults",
                    "params": {"study": study, "result_receipts": [receipt]}},
        "compare": {"uses": "index_options.ledger_studies:MIOPacketCompare",
                    "params": {"study": study},
                    "inputs": {"results": "$results.results", "identities": "$results.identities"}}})
    compared = run_document(compare, asof="2019-02-09", journal=False)
    assert compared.state == "ran", compared
    manifest = RunAttestation(compared.run_dir).attested_output("compare", "report", compare.hash)
    report = resolve_json_artifact(compared.run_dir, manifest)
    assert report["winner"] is None
    assert report["packets"][0]["complete"]
    assert report["packets"][0]["methods"][0]["filled_books"] == 1
    assert registry.snapshot()["active"] == report["packets"][0]["permit"]["id"]


def test_direct_node_mutated_inputs_and_missing_pins_refuse(study):
    reader = MIOPacketInputs("intake", {"study": study})
    with pytest.raises((ValueError, KeyError)):
        reader.run(None, {"permit": {}})
    registry, plan, permit = _open(study)
    intake = reader.run(None, {"permit": permit})
    intake["bounded_inputs"]["inputs"]["X"]["bars"][0]["close"] *= 2
    with pytest.raises(ValueError, match="intake"):
        MIOPacketStudy("study", {"study": study}).run(None, {"permit": permit, **intake})
    unbound = copy.deepcopy(study)
    unbound["domain"]["predictive"] = None
    assert StudyContract.build(unbound)["runnable"] is False
    unbound["domain"]["sources"]["bars"] = None
    assert "bars" in StudyContract.build(unbound)["missing"]


def test_whole_method_failure_is_retained_and_blocks_completion(study, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    study["domain"]["params"]["methods"].append({
        **study["domain"]["params"]["methods"][0], "id": "broken"})
    registry, plan, permit = _open(study)
    intake = MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    original = WeeklyOptionPortfolioStudy._method
    def fail(self, method, *args, **kwargs):
        if method["id"] == "broken":
            raise RuntimeError("injected failure")
        return original(self, method, *args, **kwargs)
    monkeypatch.setattr(WeeklyOptionPortfolioStudy, "_method", fail)
    result = MIOPacketStudy("study", {"study": study}).run(None, {"permit": permit, **intake})
    assert [r["status"] for r in result["results"]] == ["completed", "failed"]
    assert not MIOStudyContract().validate_result(study, **result, provenance=intake["provenance"])
    with pytest.raises(ValueError, match="active"):
        registry.freeze(plan)


def test_metadata_plan_never_reads_payload_and_byte_drift_refuses(study, monkeypatch):
    from dskit.onboarding.artifacts import PinnedPayloadSource
    original = PinnedPayloadSource.file
    def metadata(self, relative, *, verify=False):
        assert verify is False
        return original(self, relative, verify=verify)
    with monkeypatch.context() as patch:
        patch.setattr(PinnedPayloadSource, "file", metadata)
        assert StudyContract.build(study)["runnable"]
    registry, plan, permit = _open(study)
    binding = study["domain"]["sources"]["bars"]
    path = Path(original(PinnedPayloadSource(binding["pin"]), binding["files"]["X"]))
    path.write_bytes(path.read_bytes()+b"changed")
    with pytest.raises(ValueError, match="drift"):
        MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    assert registry.snapshot()["claims"][permit["id"]]["status"] == "claimed_exposed"

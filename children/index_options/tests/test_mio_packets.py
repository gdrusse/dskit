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
from index_options.mio_contract import MIOModelZooSelection
from index_options.ledger_studies import MIOPacketStudy, WeeklyOptionPortfolioStudy
from test_ledger_studies import _packet_clock_fixture


def _implementation_pins(*objects):
    import importlib
    pins = {}
    for obj in objects:
        module = importlib.import_module(obj.__module__) if isinstance(obj, type) else (importlib.import_module(obj) if isinstance(obj, str) else obj)
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
def study(tmp_path, request):
    import pyarrow as pa
    import pyarrow.parquet as pq

    params, data = _packet_clock_fixture()
    archive = tmp_path/"origin"
    archive.mkdir()
    sessions = params.pop("clocks")
    pairs = params.pop("selected_pairs")
    params["bridge"]["end_before"] = sessions["entry_end_before"]
    (archive/"inventory.json").write_text(json.dumps({
        "policy": "all_supported_no_predictive_gate",
        "candidates": [{"pair": pair, "missing_roles": []} for pair in pairs]}))
    if getattr(request, "param", None) == "expiry_basis":
        data["X"]["forecasts"] = data["X"]["forecasts"][:1]
        data["X"]["contexts"] = data["X"]["contexts"][:1]
        data["X"]["chain"] = [r for r in data["X"]["chain"]
                              if not (r["date"] == "2019-02-01" and r["expiration"] == "2019-02-01")]
    if getattr(request, "param", None) == "shallow_long_leg":
        _shallow_long_leg(data["X"])
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
            "selected_packet": "dev", "code": _implementation_pins(StudyContract, "index_options.mio_contract"),
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
def test_real_json_spine_and_attested_comparison(study, tmp_path, caplog):
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
    import json
    replay_events = [json.loads(record.getMessage()[len("MIO_EVENT "):])
                     for record in caplog.records
                     if record.getMessage().startswith("MIO_EVENT ")
                     and '"type":"replay_verified"' in record.getMessage()]
    assert replay_events and all(e["phase"] == "provisional"
                                 and e["publication"] == "pending"
                                 and e["provisional"] for e in replay_events)
    receipt = {"run_dir": str(ran.run_dir), "document_hash": close.hash, "node": "close", "port": "receipt"}
    compare = _document(tmp_path, "compare", {
        "results": {"uses": "dskit.pipeline.study_protocol:StudyPacketResults",
                    "params": {"study": study, "result_receipts": [receipt]}},
        "compare": {"uses": "index_options.ledger_studies:MIOPacketCompare",
                    "params": {"study": study},
                    "inputs": {"results": "$results.results", "identities": "$results.identities"}}})
    result_artifact = resolve_json_artifact(receipt["run_dir"],
        RunAttestation(receipt["run_dir"]).attested_output("close", "receipt", close.hash))
    permit = result_artifact["permit"]
    assert registry.snapshot()["claims"][permit["id"]]["status"] == "claimed_exposed"
    with pytest.raises(ValueError, match="attested close result receipt"):
        registry.dispose("dev", "review", "premature")
    mismatch = {**receipt, "document_hash": "0" * 64}
    with pytest.raises(ValueError):
        registry.dispose("dev", "review", "wrong receipt", mismatch)
    wrong_node = {**receipt, "node": "study", "port": "audit"}
    with pytest.raises(ValueError, match="StudyPacketClose.receipt"):
        registry._attested_result(wrong_node)
    compared = run_document(compare, asof="2019-02-09", journal=False)
    assert registry.snapshot()["claims"][permit["id"]]["status"] == "completed_pending_review"
    registry.dispose("dev", "review", "published complete result", receipt)
    assert compared.state == "ran", compared
    manifest = RunAttestation(compared.run_dir).attested_output("compare", "report", compare.hash)
    report = resolve_json_artifact(compared.run_dir, manifest)
    assert report["winner"] is None
    assert report["packets"][0]["complete"]
    assert report["packets"][0]["methods"][0]["filled_books"] == 1
    assert registry.snapshot()["active"] is None
    assert registry.snapshot()["frontier"] == 1


def test_mio_contract_requires_full_imported_code_pins(study):
    assert StudyContract.build(study)["runnable"]
    missing = copy.deepcopy(study)
    missing["code"].pop(next(path for path in missing["code"] if path.endswith("mio_contract.py")))
    with pytest.raises(ValueError, match="implementation pins"):
        StudyContract.build(missing)
    unrelated = copy.deepcopy(study)
    unrelated["code"] = {__file__: path_hash(__file__)}
    with pytest.raises(ValueError, match="implementation pins"):
        StudyContract.build(unrelated)


def _research_universe(study, tmp_path, name="admission", overrides=None):
    inventory_pin = study["domain"]["sources"]["forecasts"]["pin"]
    pairs = copy.deepcopy(study["domain"]["pairs"])
    candidates = [{"pair": pair, "included": True, "reason": "supported"} for pair in pairs]
    bridge = study["domain"]["params"]["bridge"]
    receipt = {"policy": "all_supported_no_predictive_gate", "candidates": candidates,
               "pairs": pairs, "liquidity": {key: bridge[key] for key in
                                               ("min_quote_size", "max_relative_spread", "max_abs_z")},
               "pair_sha256": value_hash(pairs),
               "inventory": {"source": inventory_pin, "file": "inventory.json"}}
    receipt.update(overrides or {})
    source_root = Path(tmp_path)/name
    source_root.mkdir()
    (source_root/"admission.json").write_text(json.dumps(receipt))
    root = OnboardingRoot(inventory_pin["root"])
    registry = root.registry()
    version = registry.register("source_config", {
        "name": name, "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(source_root), "as_of": "2026-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(version, "active", origin="test")
    run_acquisition(root, registry, name, "files", "backfill")
    payload = payload_files(root, name, "files")
    pin = {"root": root.root, "source": name, "stream": "files",
           "snapshot": payload["snapshot"], "manifest_sha256": payload["manifest_sha256"],
           "files": payload["sha256"]}
    return {"mode": "research_universe", "evidence": {
        "pair_sha256": value_hash(pairs), "source": pin, "file": "admission.json"}}


def test_research_universe_requires_complete_pinned_outcome_blind_inventory(study, tmp_path):
    study["domain"]["admission"] = _research_universe(study, tmp_path)
    assert StudyContract.build(study)["runnable"]
    omitted = copy.deepcopy(study)
    omitted["domain"]["admission"] = _research_universe(
        omitted, tmp_path, "admission-omitted", {"candidates": []})
    with pytest.raises(ValueError, match="omits, injects"):
        StudyContract.build(omitted)
    forged = copy.deepcopy(study)
    forged["domain"]["admission"] = _research_universe(
        forged, tmp_path, "admission-forged", {"performance_gate": "best_return"})
    with pytest.raises(ValueError, match="outcome-blind"):
        StudyContract.build(forged)
    fresh = copy.deepcopy(study)
    fresh["packets"][0]["purpose"] = "adaptation"
    with pytest.raises(ValueError, match="development-only"):
        StudyContract.build(fresh)


def test_json_artifact_persistence_failure_keeps_claim_recoverable(study, tmp_path, monkeypatch):
    import dskit.pipeline.driver as driver
    registry = ExposureRegistry(study["registry"])
    registry.initialize(study["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01",
                                           "evidence": "synthetic prior use"}], "2020-01-01")
    plan = StudyContract.build(study)
    request = registry.freeze(plan)
    close = _document(tmp_path, "publish_failure", {
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
    persist = driver._persist_json_artifacts
    def fail_close(run_dir, node_key, outputs):
        if node_key == "close":
            raise OSError("injected output persistence failure")
        return persist(run_dir, node_key, outputs)
    monkeypatch.setattr(driver, "_persist_json_artifacts", fail_close)
    failed = run_document(close, asof="2019-02-09", journal=False)
    assert failed.state in {"error", "failed"}
    state = registry.snapshot()
    old_id = state["active"]
    old = state["claims"][old_id]
    assert old["status"] == "claimed_exposed"
    assert old["result"] is None and "publication" not in old
    retry = registry.retry(plan, old["permit"], state["revision"])
    state = registry.snapshot()
    assert state["frontier"] == 0 and state["prior"][0]["evidence"] == "synthetic prior use"
    assert state["claims"][old_id]["status"] == "retry_superseded_exposed"
    assert state["active"] == retry["id"]
    registry.dispose("dev", "abandon", "retry output publication after injected failure")
    state = registry.snapshot()
    assert state["frontier"] == 1 and state["active"] is None
    assert state["claims"][old_id]["status"] == "retry_superseded_exposed"


def test_model_zoo_selection_uses_complete_imported_code_pins():
    pins = _implementation_pins(StudyContract, "index_options.mio_contract")
    valid = {"source": None, "audit": "audit.json", "recipe": {},
             "training_protocol": {}, "implementation": pins}
    with pytest.raises(TypeError):
        MIOModelZooSelection("selection", valid).run(None, {})
    incomplete = copy.deepcopy(valid)
    incomplete["implementation"].pop(next(path for path in pins if path.endswith("mio_contract.py")))
    with pytest.raises(ValueError, match="implementation pins"):
        MIOModelZooSelection("selection", incomplete).run(None, {})
    changed = copy.deepcopy(valid)
    code_path = next(path for path in pins if path.endswith("mio_contract.py"))
    changed["implementation"][code_path] = "0" * 64
    with pytest.raises(ValueError, match="code pin drift"):
        MIOModelZooSelection("selection", changed).run(None, {})


def _owner_selected_study(tmp_path, monkeypatch, *, invalid=None):
    import hashlib
    import index_options.mio_contract as contract

    payloads = {}
    pin = {"root": "fixture", "source": "saved-halves", "snapshot": "snapshot",
           "stream": "files", "manifest_sha256": "a"*64, "files": {}}

    def add_file(relative, content):
        path = tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
        path.write_text(raw)
        digest = hashlib.sha256(raw.encode()).hexdigest()
        pin["files"][relative] = digest
        payloads[relative] = path
        return digest

    common_method = {"by": "disp", "components": ["model", "market_cal", "pooled", "baseline"],
                     "kind": "quantile_average", "learn": "rolling", "levels": 199,
                     "max_learning_rows": 100000, "min_rows": 5000, "refit_days": 21}
    sources = []
    for name, start, end in (("H1", "2019-01-02", "2019-07-01"),
                             ("H2", "2019-07-01", "2020-01-01")):
        forecast = f"original/{name}/forecast.parquet"
        context = f"original/{name}/context.parquet"
        forecast_sha = add_file(forecast, name+" forecast bytes")
        context_sha = add_file(context, name+" context bytes")
        recipe = {"source": f"/original/{name}/rows.parquet", "source_sha256": context_sha,
                  "output": f"/original/{name}/output", "end_before": end, "tables": [],
                  "periods": {name: [start, end]}, "ranked_folds": [2], "method": common_method}
        if invalid == "method_drift" and name == "H2":
            recipe["method"] = {**common_method, "refit_days": 22}
        run_dir = tmp_path/"runs"/name
        run_dir.mkdir(parents=True)
        document_hash = "hash-"+name
        config = {"pipeline": {"method": {"uses": "dskit.pipeline.libs.cdf_grid_methods:GridCDFMethod",
                                            "params": recipe}}}
        (run_dir/"config.json").write_text(json.dumps(config))
        (run_dir/"resolved.json").write_text(json.dumps({"document_hash": document_hash}))
        sources.append({"id": name, "quote_start": start, "quote_end_before": end,
                        "source_fold": 2 if invalid != "source_fold" or name != "H1" else 1,
                        "ranked_folds": [2], "forecast": {"relpath": forecast, "sha256": forecast_sha},
                        "context": {"relpath": context, "sha256": context_sha},
                        "producer": {"run": str(run_dir), "resolved": {"document_hash":
                                    "mismatch" if invalid == "resolved_drift" and name == "H1" else document_hash}},
                        "recipe_params": recipe})
    stitched_forecast = "stitched/forecast.parquet"
    stitched_context = "stitched/context.parquet"
    forecast_sha = add_file(stitched_forecast, "stitched forecast")
    context_sha = add_file(stitched_context, "stitched context")
    prep = {"checks": {key: key != "unique_forecast_keys" or invalid != "failed_check"
                        for key in ("exact_source_row_equality", "unique_forecast_keys", "unique_context_keys",
                                    "matching_forecast_context_keys", "half_bounds", "label_maturity",
                                    "declared_universe_inventory")},
            "declared_cohort": ["A", "B"],
            "halves": {"H1": {"row_count": 10, "expected_names": ["A"], "missing_names": ["B"]},
                       "H2": {"row_count": 11, "expected_names": ["B"], "missing_names": ["A"]}}}
    prep_file = "receipt/preparation.json"
    prep_sha = add_file(prep_file, prep)
    receipt = {"policy": "owner_selected_saved_zoo_outputs",
               "selection_basis": "owner_selected_no_score_ranking",
               "development_window": {"start": "2019-01-02", "end_before": "2020-01-01"},
               "bridge_fold": 2,
               "stitched": {"forecast": {"relpath": stitched_forecast, "sha256": forecast_sha},
                            "context": {"relpath": stitched_context, "sha256": context_sha}},
               "sources": sources, "preparation": {"file": prep_file, "sha256": prep_sha}}
    if invalid == "interval_gap":
        receipt["sources"][0]["quote_end_before"] = "2019-06-30"
    receipt_file = "receipt/selection.json"
    receipt_sha = add_file(receipt_file, receipt)

    class Source:
        def __init__(self, source_pin):
            self.pin = source_pin

        def file(self, relative, *, verify=False):
            path = payloads[relative]
            if verify:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                assert digest == self.pin["files"][relative]
            return path

    class Attestation:
        def __init__(self, run_dir):
            self.run_dir = run_dir

        def node_output_for_document(self, node, document_hash):
            return (invalid != "producer_unattested" and node == "method"
                    and document_hash.startswith("hash-"))

        def attested_output(self, node, port, document_hash):
            return {"sha256": "b"*64} if (node, port) == ("method", "summary") else None

    monkeypatch.setattr(contract, "PinnedPayloadSource", Source)
    monkeypatch.setattr(contract, "RunAttestation", Attestation)
    study = {"selected_packet": "dev", "packets": [{"id": "dev", "purpose": "development",
              "start": "2019-01-02", "end_before": "2020-01-01"}],
             "domain": {"params": {"bridge": {"fold": 2}}, "sources": {
                 "forecasts": {"pin": pin, "files": {"shared": stitched_forecast}},
                 "contexts": {"pin": pin, "files": {"shared": stitched_context}}},
                 "synthetic": False,
                 "predictive": {"policy": "owner_selected_saved_zoo_outputs",
                                "receipt": {"source": pin, "file": receipt_file, "sha256": receipt_sha}}}}
    return study


def test_owner_selected_saved_halves_receipt_binds_producers_and_preparation(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    study = _owner_selected_study(tmp_path, monkeypatch)
    receipt = MIOStudyContract.selection(study)
    assert receipt["selection_basis"] == "owner_selected_no_score_ranking"
    assert [item["id"] for item in receipt["sources"]] == ["H1", "H2"]


def test_legacy_best_zoo_selection_path_remains_accepted(monkeypatch):
    import index_options.mio_contract as contract
    from index_options.mio_contract import MIOStudyContract

    pin = {"root": "fixture", "source": "legacy", "snapshot": "snap", "stream": "files",
           "manifest_sha256": "a"*64,
           "files": {"forecast.parquet": "b"*64, "context.parquet": "c"*64}}
    training = {"components": [], "folds": [{"fold": 2, "val_start": "2019-01-02",
                "val_end": "2019-01-07"}], "artifacts": {}, "label_maturity": {}, "seed": 1, "monitor": {}}
    producer = {"run": "producer", "resolved": {"document_hash": "doc"}}
    receipt = {"policy": "best_model_zoo_output", "forecast_relpath": "forecast.parquet",
               "forecast_sha256": "b"*64, "forecast_source_manifest_sha256": pin["manifest_sha256"],
               "recipe_sha256": value_hash({}), "recipe_params": {},
               "training_protocol_sha256": value_hash(training), "training_protocol": training,
               "context_sha256": "c"*64, "producer": producer}

    class Source:
        def __init__(self, source_pin):
            self.pin = source_pin

    class Attestation:
        def __init__(self, run_dir):
            self.run_dir = run_dir

        def attested_output(self, node, port, document_hash):
            return {"sha256": "d"*64}

        def node_output_for_document(self, node, document_hash):
            return True

    monkeypatch.setattr(contract, "PinnedPayloadSource", Source)
    monkeypatch.setattr(contract, "RunAttestation", Attestation)
    monkeypatch.setattr(contract, "resolve_json_artifact", lambda *args: receipt)
    study = {"selected_packet": "dev", "packets": [{"id": "dev", "purpose": "development",
              "start": "2019-01-02", "end_before": "2019-01-08"}],
             "domain": {"synthetic": False, "sources": {
                 "forecasts": {"pin": pin, "files": {"shared": "forecast.parquet"}},
                 "contexts": {"pin": pin, "files": {"shared": "context.parquet"}}},
                 "params": {"bridge": {"fold": 2}},
                 "predictive": {"run_dir": "selection", "document_hash": "selection-doc",
                                "node": "selection", "port": "selection", "sha256": "d"*64}}}
    assert MIOStudyContract.selection(study) == receipt


@pytest.mark.parametrize("invalid", ["source_fold", "interval_gap", "method_drift", "failed_check",
                                      "producer_unattested", "resolved_drift"])
def test_owner_selected_saved_halves_refuse_provenance_or_preparation_drift(tmp_path, monkeypatch, invalid):
    from index_options.mio_contract import MIOStudyContract
    study = _owner_selected_study(tmp_path, monkeypatch, invalid=invalid)
    with pytest.raises(ValueError):
        MIOStudyContract.selection(study)


def test_owner_selected_receipt_digest_must_match_pinned_manifest(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    study = _owner_selected_study(tmp_path, monkeypatch)
    study["domain"]["predictive"]["receipt"]["sha256"] = "0"*64
    with pytest.raises(ValueError, match="receipt digest"):
        MIOStudyContract.selection(study)


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


def test_sqlite_quote_index_matches_legacy_weekly_account(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from dskit.pipeline.libs.parquet import SQLiteParquetIndex
    from index_options.ledger_studies import _AllQuotedOptionContext

    params, original = _packet_clock_fixture()
    baseline = WeeklyOptionPortfolioStudy(params, original).run()["methods"][0]
    archive = tmp_path / "chain.parquet"
    pq.write_table(pa.Table.from_pylist(original["X"]["chain"]), archive, row_group_size=2)
    node = _AllQuotedOptionContext("prepare", {**params["bridge"], "symbol": "X"})
    factors = {}
    descriptor = SQLiteParquetIndex.build(str(archive), str(tmp_path / "quotes.sqlite"),
        ["symbol", "date", "expiration", "type", "strike_raw", "strike", "bid", "ask",
         "bid_size", "ask_size", "basis_factor", "underlying_price"],
        {"date": {"start": params["clocks"]["history_start"],
                   "end_before": params["clocks"]["tail_end_before"]},
         "expiration": {"end_before": params["clocks"]["tail_end_before"]}},
        where=__import__("pyarrow.dataset", fromlist=["field"]).field("symbol") == "X",
        unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
        partition_by=("date", "expiration"), float_keys=("strike_raw",),
        validator=lambda row: node._validate_quote_row(row, factors))
    from dskit.pipeline.libs.parquet import SQLiteParquetIndex as _Index
    debug_index = _Index(descriptor)
    debug_index.close()
    indexed_inputs = copy.deepcopy(original)
    indexed_inputs["X"].pop("chain")
    indexed_inputs["X"]["chain_index"] = {"index": descriptor, "basis_factors": factors}
    indexed = WeeklyOptionPortfolioStudy(params, indexed_inputs)
    events = []
    actual = indexed._method(params["methods"][0], None, events=events.append)
    def without_timing(result):
        result = copy.deepcopy(result)
        for decision in result["decisions"]:
            for certificate in decision.get("certificates", []):
                certificate.pop("seconds", None)
        return result
    assert without_timing(actual) == without_timing(baseline)
    emitted = [e for e in events if e["type"] == "weekly_decision"]
    assert len(emitted) == len(indexed.weeks)
    for event, decision in zip(emitted, actual["decisions"]):
        expected = {k: decision["selection"][k] for k in ("value", "bound", "gap")
                    if k in decision.get("selection", {})}
        assert event["audited_selection"] == expected
        assert event["timeout_reason"] == decision.get("selection", {}).get("timeout")
        expected_books = [{**{k: book.get(k) for k in
                            ("id", "symbol", "expiry", "credit", "reserve", "contracts")},
                           "legs": [{k: leg.get(k) for k in ("right", "strike", "sign", "quantity")}
                                    for leg in book.get("legs", [])]}
                          for book in decision.get("books", [])]
        assert event["selected_books"] == expected_books
    daily = [e for e in events if e["type"] == "daily_account_snapshot"]
    assert len(daily) == len(indexed.calendar)
    assert all("realized_pnl_usd" in e and "funding_usd" in e
               and "funding_cash_usd" not in e and "twr" not in e for e in daily)
    assert sum(e["type"] == "marked_equity_point" for e in events) == len(indexed.calendar)
    indexed.close_quote_indexes(verify=True)


def test_filled_account_shock_packet_matches_direct_study(study):
    """The packet runs the filled-account scope exactly as the direct study does."""
    study["domain"]["params"]["methods"][0]["shock"] = {
        "moves": [-.1, .1], "loss_fraction": .08, "beta_lookback": 2, "scope": "filled_account"}
    registry, plan, permit = _open(study)
    intake = MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    packet = MIOPacketStudy("study", {"study": study}).run(None, {"permit": permit, **intake})
    assert [(r["status"], r["replay_parity"]) for r in packet["results"]] == [("completed", True)]
    bounded = intake["bounded_inputs"]
    d = study["domain"]
    direct = WeeklyOptionPortfolioStudy(
        {**d["params"], "selected_pairs": d["pairs"], "clocks": bounded["clocks"]}, bounded["inputs"])
    expected = direct._method(d["params"]["methods"][0], None)
    direct.close_quote_indexes()
    def without_timing(result):
        result = copy.deepcopy(result)
        for decision in result["decisions"]:
            for certificate in decision.get("certificates", []):
                certificate.pop("seconds", None)
        return result
    actual = packet["results"][0]["result"]
    assert without_timing(actual) == without_timing(expected)
    assert actual["decisions"][0]["shock"]["scope"] == "filled_account"
    assert actual["decisions"][0]["shock_admission"]["status"] == "admissible"

def test_stress_coverage_selects_only_pinned_envelope():
    from index_options.mio_stress_coverage import select_numeric_jobs
    envelope = "scenarios[0] cannot price leg 'leg_0': standardized moneyness is outside numeric envelope [-3, 3]"
    jobs = [{"key": "one", "preflight_refusals": [envelope]},
            {"key": "two", "preflight_refusals": ["missing_or_invalid_quote_iv_leg_0"]},
            {"key": "three", "preflight_refusals": []},
            {"key": "four", "preflight_refusals": [envelope, "missing_or_invalid_quote_iv_leg_0"]}]
    assert [j["key"] for j in select_numeric_jobs(jobs)] == ["one"]


def test_stress_coverage_exact_books_and_all_causes():
    from index_options.mio_stress_coverage import aggregate_weekly
    daily = {"cohort": "c", "method": "m", "date": "2019-06-17",
             "open_ids": '["d:A:e", "d:B:e"]', "cash": "100", "funding": "200",
             "contributions": "150", "equity": "240"}
    rows = [
        {"cohort": "c", "method": "m", "kind": "weekly_open_book", "date": daily["date"],
         "scenario": "down_12_vol_up_15", "group_id": "d:A:e", "status": "priced",
         "model_base_value_usd": "80", "scenario_full_value_usd": "30",
         "reason": "", "key": "one"},
        {"cohort": "c", "method": "m", "kind": "weekly_open_book", "date": daily["date"],
         "scenario": "down_12_vol_up_15", "group_id": "d:B:e", "status": "priced",
         "model_base_value_usd": "60", "scenario_full_value_usd": "20",
         "reason": "", "key": "two"},
    ]
    result = aggregate_weekly(daily, "down_12_vol_up_15", rows)
    assert result["status"] == "complete"
    assert result["model_baseline_equity_usd"] == 240
    assert result["hypothetical_scenario_equity_usd"] == 150
    assert result["loss_usd"] == 90
    assert result["loss_over_funding"] == .45
    rows[1].update(status="refused", reason="missing_or_invalid_quote_iv_leg_1; "
                   "scenarios[0] cannot price leg 'leg_0': standardized moneyness is outside numeric envelope [-3, 3]")
    result = aggregate_weekly(daily, "down_12_vol_up_15", rows)
    assert result["status"] == "incomplete"
    assert result["model_baseline_equity_usd"] is None
    assert set(result["refusal_causes"]) == {"source_iv_unavailable", "numeric_envelope"}
    assert result["unpriced_books"][0]["group_id"] == "d:B:e"
    with pytest.raises(ValueError, match="open_ids"):
        aggregate_weekly(daily, "down_12_vol_up_15", rows[:1])
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_weekly(daily, "down_12_vol_up_15", rows + rows[:1])
    cash = {**daily, "open_ids": "[]"}
    assert aggregate_weekly(cash, "down_12_vol_up_15", [])["status"] == "cash_only"

def test_stress_coverage_keeps_new_numeric_refusal_cause():
    from index_options.mio_stress_coverage import _causes
    reasons = (_causes("scenarios[0] cannot price leg 'leg_0': standardized moneyness "
                       "is outside numeric envelope [-3, 3]; scenarios[0] cannot price "
                       "leg 'leg_0': standardized moneyness is outside stable-tail "
                       "numeric range [-37, 37]"))
    assert [(r["cause"], r["phase"], r["leg_id"]) for r in reasons] == [
        ("numeric_envelope", "scenario", "leg_0"),
        ("numeric_stable_range", "scenario", "leg_0"),
    ]


def test_stress_coverage_null_ratios_cash_expiry_and_pin_drift(tmp_path):
    from index_options.mio_stress_coverage import CoverageDriver, aggregate_weekly
    daily = {"cohort": "c", "method": "m", "date": "2019-06-17",
             "open_ids": '["d:A:e"]', "cash": "0", "funding": "0",
             "contributions": "0", "equity": "0"}
    row = {"cohort": "c", "method": "m", "kind": "weekly_open_book",
           "date": daily["date"], "scenario": "unchanged", "group_id": "d:A:e",
           "status": "priced", "model_base_value_usd": "0",
           "scenario_full_value_usd": "0", "reason": ""}
    result = aggregate_weekly(daily, "unchanged", [row])
    assert result["status"] == "complete"
    assert result["loss_over_funding"] is None
    assert result["loss_over_model_baseline_equity"] is None
    assert result["loss_over_contributions"] is None
    assert set(result["ratio_null_reasons"]) == {
        "loss_over_funding", "loss_over_model_baseline_equity",
        "loss_over_contributions"}
    assert aggregate_weekly({**daily, "open_ids": "[]"},
                            "expiry_down_15", [])["status"] == "cash_only"
    root = tmp_path / "original"
    root.mkdir()
    pin = tmp_path / "pin.json"
    pin.write_text("{}")
    driver = CoverageDriver(root / "new", original_root=root, pin_path=pin,
                            pin_sha256="0" * 64, expected_envelope=1,
                            expected_eligible=1)
    with pytest.raises(ValueError, match="results pin SHA-256"):
        driver.originals("limited36")


def test_stress_coverage_selection_rejects_duplicate_and_missing(tmp_path, monkeypatch):
    from index_options.mio_stress_coverage import CoverageDriver
    root = tmp_path / "original"
    out = root / "new" / "limited36"
    out.mkdir(parents=True)
    driver = CoverageDriver(root / "new", root, tmp_path / "pin.json", "0" * 64, 2, 1)
    monkeypatch.setattr(driver, "_input_hashes", lambda cohort: {"pin": "abc"})
    selection = {"schema_version": 1, "cohort": "limited36",
                 "original_results_pin_sha256": "0" * 64,
                 "input_sha256": {"pin": "abc"},
                 "jobs": [{"key": "x", "status": "eligible"}]}
    (out / "selection.json").write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="coverage count"):
        driver._selection("limited36")
    selection["jobs"].append({"key": "x", "status": "opt_in_refused"})
    (out / "selection.json").write_text(json.dumps(selection))
    with pytest.raises(ValueError, match="duplicate selection"):
        driver._selection("limited36")


def _shallow_long_leg(data, depth=3):
    """Show only `depth` contracts on the bought 90 put at the first fill session."""
    for row in data["chain"]:
        if row["date"] == "2019-01-03" and row["strike"] == 90.:
            row["ask_size"] = depth


def test_weekly_fill_sizing_is_validated_and_defaults_to_all_or_none():
    """ADR-0282 Amendment 1: an absent knob keeps the legacy all-or-none refusal."""
    params, inputs = _packet_clock_fixture()
    _shallow_long_leg(inputs["X"])
    legacy = WeeklyOptionPortfolioStudy(params, inputs).run()["methods"][0]
    assert [(b["reason"], b["contracts"]) for b in legacy["rejected"]] == [("insufficient_fill_size", 8)]
    explicit = WeeklyOptionPortfolioStudy({**params, "fill_sizing": "all_or_none"}, inputs).run()["methods"][0]
    for result in (legacy, explicit):
        for decision in result["decisions"]:
            for certificate in decision.get("certificates", []):
                certificate.pop("seconds", None)
    assert explicit == legacy
    for bad in ("partial", None, 1):
        with pytest.raises(ValueError, match="fill sizing"):
            WeeklyOptionPortfolioStudy.validate_configuration({
                **{k: v for k, v in params.items() if k not in ("clocks", "selected_pairs")},
                "fill_sizing": bad})


@pytest.mark.parametrize("study", ["shallow_long_leg"], indirect=True)
def test_available_size_packet_downsizes_and_replays_like_the_direct_study(study):
    """The packet fills the gcd-downsized book and matches the direct study."""
    study["domain"]["params"]["fill_sizing"] = "available_size"
    registry, plan, permit = _open(study)
    intake = MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    packet = MIOPacketStudy("study", {"study": study}).run(None, {"permit": permit, **intake})
    assert [(r["status"], r["replay_parity"]) for r in packet["results"]] == [("completed", True)]
    actual = packet["results"][0]["result"]
    trade = actual["trades"][0]
    assert [(leg["quantity"], leg["selected_quantity"]) for leg in trade["legs"]] == [(3, 4), (3, 4)]
    assert trade["contracts"] == 6 and trade["fees"] == pytest.approx(6*.65)
    assert actual["rejected"] == []
    # Hand-computed 3x3 of the 4x4 put spread: sell 100P at 4.00, buy 90P at 0.12.
    # Premium 1200 - 36 = 1164; credit 1164 - 3.90 = 1160.10 (selected 1546.80);
    # reserve 10 x 100 x 3 - 1160.10 = 1839.90; stress 0.25 x (0.02 + 0.20) x 300.
    for key, value in (("premium", 1164.), ("credit", 1160.1), ("decision_credit", 1546.8),
                       ("reserve", 1839.9), ("max_loss", 1839.9), ("stress_extra", 16.5),
                       ("pnl", 1160.1)):
        assert trade[key] == pytest.approx(value, abs=1e-9), key
    by_day = {point["date"]: point for point in actual["curve"]}
    assert by_day["2019-01-03"]["cash"] == pytest.approx(10000.+1160.1, abs=1e-9)
    assert by_day["2019-01-03"]["reserve"] == pytest.approx(1839.9, abs=1e-9)
    assert actual["final_equity"] == pytest.approx(actual["contributions"]+1160.1, abs=1e-9)
    bounded, d = intake["bounded_inputs"], study["domain"]
    direct = WeeklyOptionPortfolioStudy(
        {**d["params"], "selected_pairs": d["pairs"], "clocks": bounded["clocks"]}, bounded["inputs"])
    expected = direct._method(d["params"]["methods"][0], None)
    direct.close_quote_indexes()
    for result in (actual, expected):
        for decision in result["decisions"]:
            for certificate in decision.get("certificates", []):
                certificate.pop("seconds", None)
    assert actual == expected



def _pinned_forecast_study(tmp_path, monkeypatch, mutate=None):
    """Pinned metadata fixture with a fixed neural fit and one completed Q1 producer."""
    import hashlib
    study = _owner_selected_study(tmp_path, monkeypatch)
    pin = study["domain"]["predictive"]["receipt"]["source"]
    selection_file = "receipt/selection.json"
    receipt = json.loads((tmp_path/selection_file).read_text())
    def save(relative, value):
        path = tmp_path/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        pin["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        return pin["files"][relative]
    # The legacy source fixture's verifier uses this shared directory for all files.
    import index_options.mio_contract as contract
    class Source:
        def __init__(self, source_pin):
            self.pin = source_pin
        def file(self, relative, *, verify=False):
            path = tmp_path/relative
            if verify and hashlib.sha256(path.read_bytes()).hexdigest() != self.pin["files"][relative]:
                raise ValueError("payload drift")
            return path
    monkeypatch.setattr(contract, "PinnedPayloadSource", Source)
    fold = {"fold": 3, "role": "scored", "train_start": "2017-01-03", "train_end": "2019-12-31",
            "val_start": "2021-01-04", "val_end": "2021-03-31"}
    fold_sha = save("training/folds.jsonl", json.dumps(fold)+"\n")
    monitor = {"seeds": [29], "losses": [{"kind": "crps", "weight": 1.0}], "epochs": 2}
    fit_config = {"study": {"fold_table": {"sha256": fold_sha}}, "training": monitor,
                  "candidates": [{"id": "fixed_model"}]}
    fit_sha = save("training/config.json", fit_config)
    fit_proof = {"config_sha256": fit_sha, "band_counts": [10, 2, 3], "band_dates": [
        {"quote_date": ["2017-01-03", "2019-08-22"], "settlement_date": ["2017-01-06", "2019-08-23"]},
        {"quote_date": ["2019-08-30", "2019-12-26"], "settlement_date": ["2019-09-06", "2019-12-27"]},
        {"quote_date": ["2021-01-04", "2021-03-25"], "settlement_date": ["2021-01-08", "2021-03-26"]}]}
    fit_proof_sha = save("training/verified.json", fit_proof)
    item = receipt["sources"][0]
    item.update(id="Q1", quote_start="2021-01-04", quote_end_before="2021-04-01", source_fold=3, ranked_folds=[3])
    components = [{"name": name, "columns": [name+"_cdf"]} for name in ("model", "market_cal", "pooled", "baseline")]
    recipe = item["recipe_params"]
    recipe.update(ranked_folds=[3], end_before="2021-04-01", components=components)
    run = Path(item["producer"]["run"])
    (run/"config.json").write_text(json.dumps({"pipeline": {"method": {
        "uses": "dskit.pipeline.libs.cdf_grid_methods:GridCDFMethod", "params": recipe}}}))
    m53_proof = {"document_hash": item["producer"]["resolved"]["document_hash"],
                 "files": {"forecasts.parquet": {"sha256": item["forecast"]["sha256"]}},
                 "identity_rows_exact": True, "pit_maps_exact_reproduction": True,
                 "rolling_masks_verified": True, "fitted_flags_exact": True}
    save("training/m53-verified.json", m53_proof)
    save("training/m53-provenance.json", {"rows_sha256": item["context"]["sha256"],
          "neural_fit_verification_sha256": fit_proof_sha, "all_columns_exact_readback": True})
    training = {"components": {"model": {"config": "training/config.json", "fold_file": "training/folds.jsonl",
                "verification": "training/verified.json"},
                **{c["name"]: c for c in components if c["name"] != "model"},
                "combination": {"method": recipe["method"], "evidence": {"Q1": {
                    "provenance": "training/m53-provenance.json", "verification": "training/m53-verified.json"}}}},
                "folds": [fold], "seed": 29, "monitor": monitor,
                "artifacts": {k: v for k, v in pin["files"].items() if k.startswith("training/")},
                "label_maturity": {"training_end_before": "2020-01-01", "validation_end_before": "2021-04-01",
                                   "rolling": "strictly_settled_prior_validation"}}
    receipt.pop("development_window")
    receipt.update(policy="owner_selected_pinned_forecasts", forecast_window={"start": "2021-01-04", "end_before": "2021-04-01"},
                   bridge_fold=3, sources=[item], training_protocol=training)
    prep = {"checks": {key: True for key in ("exact_source_row_equality", "unique_forecast_keys", "unique_context_keys",
            "matching_forecast_context_keys", "source_bounds", "label_maturity", "declared_universe_inventory")},
            "declared_cohort": ["A", "B"], "sources": {"Q1": {"row_count": 3, "expected_names": ["A"], "missing_names": ["B"]}}}
    receipt["preparation"]["sha256"] = save("receipt/preparation.json", prep)
    study["packets"][0].update(purpose="confirmation", start="2021-01-04", end_before="2021-04-01")
    study["domain"]["params"].update(symbols=["A", "B"])
    study["domain"]["params"]["bridge"].update(fold=3, start="2021-01-04", end_before="2021-04-01", protected_end_before="2024-01-01")
    study["domain"]["predictive"]["policy"] = receipt["policy"]
    if mutate:
        mutate(study, receipt, prep, fit_proof)
        receipt["preparation"]["sha256"] = save("receipt/preparation.json", prep)
        training["artifacts"]["training/verified.json"] = save("training/verified.json", fit_proof)
    study["domain"]["predictive"]["receipt"]["sha256"] = save(selection_file, receipt)
    return study


def test_pinned_forecast_window_accepts_q1_and_keeps_missing_names(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    study = _pinned_forecast_study(tmp_path, monkeypatch)
    receipt = MIOStudyContract.selection(study)
    assert receipt["forecast_window"] == {"start": "2021-01-04", "end_before": "2021-04-01"}
    assert receipt["bridge_fold"] == 3
    assert study["domain"]["params"]["symbols"] == ["A", "B"]


@pytest.mark.parametrize("case", ["bool_fold", "zero_fold", "bad_date", "gap", "outside_packet", "wrong_source_fold",
    "wrong_rank", "duplicate_source", "empty_sources", "missing_name", "extra_source_coverage", "failed_maturity",
    "empty_training", "empty_artifacts", "seed", "monitor", "fold_summary", "train_label_boundary", "val_label_boundary",
    "negative_fold", "zero_interval", "incomplete_interval", "protected", "source_bool", "unknown_policy",
    "cohort_partition", "training_roles", "combination_identity", "producer_bound", "producer_config", "producer_resolved"])
def test_pinned_forecast_window_refuses_inconsistent_evidence(tmp_path, monkeypatch, case):
    from index_options.mio_contract import MIOStudyContract
    def mutate(study, receipt, prep, fit_proof):
        training = receipt["training_protocol"]
        if case == "bool_fold":
            receipt["bridge_fold"] = True
        elif case == "zero_fold":
            receipt["bridge_fold"] = 0
        elif case == "bad_date":
            receipt["forecast_window"]["start"] = "20210104"
        elif case == "gap":
            receipt["sources"][0]["quote_start"] = "2021-01-05"
        elif case == "outside_packet":
            receipt["forecast_window"]["end_before"] = "2021-04-02"
        elif case == "wrong_source_fold":
            receipt["sources"][0]["source_fold"] = 2
        elif case == "wrong_rank":
            receipt["sources"][0]["ranked_folds"] = [2]
        elif case == "duplicate_source":
            receipt["sources"].append(copy.deepcopy(receipt["sources"][0]))
        elif case == "empty_sources":
            receipt["sources"] = []
        elif case == "missing_name":
            prep["declared_cohort"] = ["A"]
        elif case == "extra_source_coverage":
            prep["sources"]["extra"] = prep["sources"]["Q1"]
        elif case == "failed_maturity":
            prep["checks"]["label_maturity"] = False
        elif case == "empty_training":
            training["components"] = {}
        elif case == "empty_artifacts":
            training["artifacts"] = {}
        elif case == "seed":
            training["seed"] = 30
        elif case == "monitor":
            training["monitor"] = {"seeds": [30]}
        elif case == "fold_summary":
            training["folds"] = [{**training["folds"][0], "train_end": "2020-12-31"}]
        elif case == "train_label_boundary":
            fit_proof["band_dates"][1]["settlement_date"][1] = "2020-01-01"
        elif case == "val_label_boundary":
            fit_proof["band_dates"][2]["settlement_date"][1] = "2021-04-01"
        elif case == "negative_fold":
            receipt["bridge_fold"] = -3
        elif case == "zero_interval":
            receipt["sources"][0]["quote_end_before"] = "2021-01-04"
        elif case == "incomplete_interval":
            receipt["sources"][0]["quote_end_before"] = "2021-03-31"
        elif case == "protected":
            study["domain"]["params"]["bridge"]["protected_end_before"] = "2021-03-31"
        elif case == "source_bool":
            receipt["sources"][0]["source_fold"] = True
        elif case == "unknown_policy":
            study["domain"]["predictive"]["policy"] = "guess"
        elif case == "cohort_partition":
            prep["sources"]["Q1"]["missing_names"] = []
        elif case == "training_roles":
            training["components"]["model"]["config"] = "not-pinned.json"
        elif case == "combination_identity":
            training["components"]["combination"]["method"] = {}
        elif case in ("producer_bound", "producer_config", "producer_resolved"):
            item = receipt["sources"][0]
            run = Path(item["producer"]["run"])
            if case == "producer_bound":
                item["recipe_params"]["end_before"] = "2026-01-01"
                config = json.loads((run/"config.json").read_text())
                config["pipeline"]["method"]["params"] = item["recipe_params"]
                (run/"config.json").write_text(json.dumps(config))
            elif case == "producer_config":
                config = json.loads((run/"config.json").read_text())
                config["pipeline"]["method"]["uses"] = "other:Node"
                (run/"config.json").write_text(json.dumps(config))
            else:
                (run/"resolved.json").write_text("{}")
    study = _pinned_forecast_study(tmp_path, monkeypatch, mutate)
    with pytest.raises(ValueError):
        MIOStudyContract.selection(study)


def test_pinned_q1_real_onboarding_producer_and_packet(study, tmp_path, monkeypatch):
    """A completed synthetic producer and acquired receipt drive the unchanged packet spine."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from datetime import date, timedelta
    from dskit.onboarding.artifacts import PinnedPayloadSource
    from index_options.mio_contract import MIOStudyContract

    # Reuse metadata construction only; restore both evidence owners before any execution.
    metadata_root = tmp_path/"metadata"
    with monkeypatch.context() as patch:
        _pinned_forecast_study(metadata_root, patch)
    receipt_path = metadata_root/"receipt/selection.json"
    receipt = json.loads(receipt_path.read_text())
    def save(relative, value):
        path = metadata_root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path_hash(str(path))
    def shifted(value):
        return (date.fromisoformat(value)+timedelta(days=733)).isoformat()

    params, data = _packet_clock_fixture()
    clocks, pairs = params.pop("clocks"), params.pop("selected_pairs")
    for rows in data["X"].values():
        for row in rows:
            for key in ("date", "quote_date", "expiry", "expiration", "settlement_date"):
                if key in row:
                    row[key] = shifted(row[key])
            if "fold" in row:
                row["fold"] = 3
    start, end = shifted(clocks["entry_start"]), shifted(clocks["entry_end_before"])
    bridge = params["bridge"]
    bridge.update(start=start, end_before=end, fold=3, protected_end_before="2024-01-01")
    source_rows = []
    for i, (forecast, context) in enumerate(zip(data["X"]["forecasts"], data["X"]["contexts"])):
        source_rows.append({**context, "row_id": i, "y": 0.0, "disp": 0.2, "market_eligible": True,
            "actual_calendar_dte": (date.fromisoformat(context["expiry"])-date.fromisoformat(context["quote_date"])).days,
            **{key.replace("method_", "mdl_"): value for key, value in forecast.items() if "_cdf_" in key}})
    frame = pd.DataFrame(source_rows)
    historical = frame.iloc[[0]].copy()
    historical["quote_date"], historical["settlement_date"], historical["expiry"], historical["band"] = (
        "2019-01-02", "2019-02-01", "2019-02-01", "fit")
    historical["row_id"] = 2
    frame = pd.concat([historical, frame], ignore_index=True)
    context = metadata_root/"original/H1/context.parquet"
    frame.to_parquet(context, index=False)
    item = receipt["sources"][0]
    recipe = {**item["recipe_params"], "source": str(context), "source_sha256": path_hash(str(context)),
        "output": str(tmp_path/"producer-output"), "end_before": "2021-04-01", "periods": {},
        "columns": {"identity": ["row_id", "symbol", "quote_date", "expiry"], "date": "quote_date",
                    "settlement": "settlement_date", "fold": "fold", "band": "band", "outcome": "y",
                    "dte": "actual_calendar_dte", "group": "symbol", "eligible": "market_eligible"},
        "z_grid": bridge["z_grid"], "grid_labels": bridge["grid_labels"], "score_weights": [1.0]*13,
        "pit_tail": 4.0, "fallback": "model", "comparators": ["model"],
        "components": [{"name": name, "source": "mdl"} for name in ("model", "market_cal", "pooled", "baseline")]}
    producer = _document(tmp_path, "q1-producer", {
        "method": {"uses": "dskit.pipeline.libs.cdf_grid_methods:GridCDFMethod", "params": recipe}})
    produced = run_document(producer, asof="2021-04-01", journal=False)
    assert produced.state == "ran"
    configured = json.loads((Path(produced.run_dir)/"config.json").read_text())["pipeline"]["method"]["params"]
    resolved = json.loads((Path(produced.run_dir)/"resolved.json").read_text())
    forecast = metadata_root/"original/H1/forecast.parquet"
    forecast.write_bytes((tmp_path/"producer-output/forecasts.parquet").read_bytes())
    item.update(quote_start=start, quote_end_before=end, recipe_params=configured,
                producer={"run": str(produced.run_dir), "resolved": resolved})
    for role, path in (("forecast", forecast), ("context", context)):
        item[role]["sha256"] = path_hash(str(path))
        output = metadata_root/receipt["stitched"][role]["relpath"]
        selected = pd.read_parquet(path).query("band == 'val'")
        selected.to_parquet(output, index=False)
        receipt["stitched"][role]["sha256"] = path_hash(str(output))
    receipt["forecast_window"] = {"start": start, "end_before": end}
    training = receipt["training_protocol"]
    for spec in configured["components"]:
        if spec["name"] != "model":
            training["components"][spec["name"]] = spec
    training["artifacts"]["training/m53-provenance.json"] = save("training/m53-provenance.json", {
        "rows_sha256": item["context"]["sha256"],
        "neural_fit_verification_sha256": training["artifacts"]["training/verified.json"],
        "all_columns_exact_readback": True})
    training["artifacts"]["training/m53-verified.json"] = save("training/m53-verified.json", {
        "document_hash": producer.hash, "files": {"forecasts.parquet": {"sha256": item["forecast"]["sha256"]}},
        "identity_rows_exact": True, "pit_maps_exact_reproduction": True,
        "rolling_masks_verified": True, "fitted_flags_exact": True})
    prep_path = metadata_root/"receipt/preparation.json"
    prep = json.loads(prep_path.read_text())
    prep.update(declared_cohort=["X"], sources={"Q1": {
        "row_count": 2, "expected_names": ["X"], "missing_names": []}})
    receipt["preparation"]["sha256"] = save("receipt/preparation.json", prep)
    save("receipt/selection.json", receipt)
    save("admission.json", {"pairs": pairs, "quote_start": "2019-01-02",
         "quote_end_before": "2020-01-01", "settlement_end_before": "2020-01-01"})
    for role in ("chain", "bars"):
        pq.write_table(pa.Table.from_pylist(data["X"][role]), metadata_root/(role+".parquet"))
    pq.write_table(pa.table({"date": [shifted(d) for d in clocks["sessions"]]}), metadata_root/"sessions.parquet")
    root = OnboardingRoot.create(str(tmp_path/"q1-ob"))
    sources = root.registry()
    vid = sources.register("source_config", {"name": "q1-fixture", "catalog_source": "synthetic",
        "connector": "localblobs", "config": {"path": str(metadata_root),
        "as_of": "2021-04-01T00:00:00+00:00"}}, origin="test")
    sources.transition(vid, "active", origin="test")
    run_acquisition(root, sources, "q1-fixture", "files", "backfill")
    acquired = payload_files(root, "q1-fixture", "files")
    pin = {"root": root.root, "source": "q1-fixture", "stream": "files", "snapshot": acquired["snapshot"],
           "manifest_sha256": acquired["manifest_sha256"], "files": acquired["sha256"]}
    study["packets"][0].update(start=start, end_before=end, purpose="confirmation")
    domain = study["domain"]
    domain.update(params=params, pairs=pairs, history_start=shifted(clocks["history_start"]), synthetic=False,
        predictive={"policy": receipt["policy"], "receipt": {"source": pin, "file": "receipt/selection.json",
                    "sha256": pin["files"]["receipt/selection.json"]}},
        admission={"mode": "externally_admitted_2019", "evidence": {
            "source": pin, "file": "admission.json", "pair_sha256": value_hash(pairs)}})
    for role in domain["sources"]:
        relative = receipt["stitched"][{"forecasts": "forecast", "contexts": "context"}[role]]["relpath"] if role in {
            "forecasts", "contexts"} else role+".parquet"
        domain["sources"][role] = {"pin": pin, "files": {("X" if role in {"bars", "chain"} else "shared"): relative}}
    assert MIOStudyContract.selection(study)["bridge_fold"] == 3
    assert not MIOStudyContract().resolve(study)["missing"]
    plan = StudyContract.build(study)
    assert plan["runnable"]
    registry = ExposureRegistry(study["registry"])
    registry.initialize(study["packets"], [], "2024-01-01")
    request = registry.freeze(plan)
    pipeline = {
        "open_packet": {"uses": "dskit.pipeline.study_protocol:StudyPacketOpen",
            "params": {"study": study, "frozen_plan": plan, "claim_request": request}},
        "intake": {"uses": "index_options.observations:MIOPacketInputs", "params": {"study": study},
            "inputs": {"permit": "$open_packet.permit"}},
        "study": {"uses": "index_options.ledger_studies:MIOPacketStudy", "params": {"study": study},
            "inputs": {"permit": "$open_packet.permit", "bounded_inputs": "$intake.bounded_inputs",
                       "provenance": "$intake.provenance"}},
        "close": {"uses": "dskit.pipeline.study_protocol:StudyPacketClose", "params": {"study": study},
            "inputs": {"permit": "$open_packet.permit", "results": "$study.results",
                       "audit": "$study.audit", "provenance": "$intake.provenance"}}}
    doc = _document(tmp_path, "q1-packet", pipeline)
    ran = run_document(doc, asof="2021-04-01", journal=False)
    assert ran.state == "ran"
    manifest = RunAttestation(ran.run_dir).attested_output("close", "receipt", doc.hash)
    result = resolve_json_artifact(ran.run_dir, manifest)
    assert result["complete"] and result["results"][0]["replay_parity"]
    actual = result["results"][0]["result"]
    assert len(actual["trades"]) == 1
    assert actual["final_equity"] == pytest.approx(actual["contributions"]+actual["pnl"])
    assert actual["trades"][0]["decision_date"] == start
    # Receipt drift must fail through each public facade before a second claim or payload access.
    receipt_file = Path(PinnedPayloadSource(pin).file("receipt/selection.json"))
    receipt_file.write_text(receipt_file.read_text()+" ")
    snapshot = registry.snapshot()
    for call in (lambda: MIOStudyContract.selection(study), lambda: MIOStudyContract().resolve(study),
                 lambda: StudyContract.build(study),
                 lambda: MIOPacketInputs("input", {"study": study}).run(None, {"permit": result["permit"]}),
                 lambda: MIOPacketStudy("study", {"study": study}).run(None, {"permit": result["permit"]})):
        with pytest.raises(ValueError):
            call()
    assert registry.snapshot() == snapshot


@pytest.mark.parametrize("case", ["training_bytes", "receipt_digest", "manifest_substitution", "producer_completion"])
def test_pinned_forecast_window_rechecks_bound_artifacts(tmp_path, monkeypatch, case):
    import index_options.mio_contract as contract
    study = _pinned_forecast_study(tmp_path, monkeypatch)
    if case == "training_bytes":
        (tmp_path/"training/config.json").write_text("{}")
    elif case == "receipt_digest":
        study["domain"]["predictive"]["receipt"]["sha256"] = "0"*64
    elif case == "manifest_substitution":
        pin = copy.deepcopy(study["domain"]["sources"]["contexts"]["pin"])
        pin["manifest_sha256"] = "0"*64
        study["domain"]["sources"]["contexts"]["pin"] = pin
    else:
        monkeypatch.setattr(contract.RunAttestation, "node_output_for_document", lambda *args: False)
    with pytest.raises(ValueError):
        contract.MIOStudyContract.selection(study)

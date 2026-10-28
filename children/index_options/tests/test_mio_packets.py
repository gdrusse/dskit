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
    expiries = sorted({row["expiry"] for row in original["X"]["forecasts"]})
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

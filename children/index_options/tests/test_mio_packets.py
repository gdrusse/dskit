"""Synthetic execution of the same JSON packet spine used by real debugging."""

import copy
import hashlib
import json
from pathlib import Path

import pytest

from dskit.onboarding import OnboardingRoot, run_acquisition
from dskit.onboarding.artifacts import PinnedPayloadSource, payload_files
from dskit.pipeline.base import value_hash
from dskit.pipeline.document import load_document
from dskit.pipeline.driver import run_document, resolve_json_artifact, RunAttestation
from dskit.pipeline.node import NodeContext
from dskit.pipeline.study_protocol import ExposureRegistry, StudyContract
from dskit.pipeline.workflow import path_hash
from index_options.observations import MIOPacketInputs
from index_options.mio_contract import MIOModelZooSelection, OptionPolicyStudyContract
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
    if getattr(request, "param", None) == "long_history":
        # 62 weekday closes before the fixture's first session: enough prior history for a 60-session beta lookback.
        from datetime import date, timedelta
        day, early = date.fromisoformat(sessions["history_start"]), []
        while len(early) < 62:
            day -= timedelta(days=1)
            if day.weekday() < 5:
                early.append(day.isoformat())
        early.reverse()
        data["X"]["bars"] = [{**data["X"]["bars"][0], "date": d} for d in early] + data["X"]["bars"]
        sessions["sessions"] = early + sessions["sessions"]
        sessions["history_start"] = early[0]
    policy_integration = getattr(request, "param", None) == "policy_integration"
    if policy_integration:
        from datetime import date, timedelta
        offset = timedelta(days=40)
        shifted_sessions = sorted({(date.fromisoformat(d)+offset).isoformat()
                                   for d in sessions["sessions"] if d >= "2019-01-02"})
        sessions["sessions"] = sorted(set(sessions["sessions"]) | set(shifted_sessions))
        for name in ("forecasts", "contexts"):
            moved = copy.deepcopy(data["X"][name])
            for row in moved:
                row["quote_date"] = (date.fromisoformat(row["quote_date"])+offset).isoformat()
                row["expiry"] = (date.fromisoformat(row["expiry"])+offset).isoformat()
                if "settlement_date" in row:
                    row["settlement_date"] = (date.fromisoformat(row["settlement_date"])+offset).isoformat()
            data["X"][name].extend(moved)
        for name in ("chain", "bars"):
            moved = copy.deepcopy([row for row in data["X"][name]
                                   if row["date"] >= "2019-01-02"])
            for row in moved:
                row["date"] = (date.fromisoformat(row["date"])+offset).isoformat()
                for key in ("expiration", "expiry", "settlement_date"):
                    if key in row:
                        row[key] = (date.fromisoformat(row[key])+offset).isoformat()
            data["X"][name].extend(moved)
        # Add a later held-out quarter while retaining the +40-day rows as lookback history.
        offset = timedelta(days=30)
        sessions["sessions"] = sorted(set(sessions["sessions"]) | {
            (date.fromisoformat(d)+offset).isoformat()
            for d in sessions["sessions"] if d >= "2019-02-11"})
        for name in ("forecasts", "contexts"):
            moved = copy.deepcopy([row for row in data["X"][name]
                                   if row["quote_date"] >= "2019-02-11"])
            for row in moved:
                row["quote_date"] = (date.fromisoformat(row["quote_date"])+offset).isoformat()
                row["expiry"] = (date.fromisoformat(row["expiry"])+offset).isoformat()
                if "settlement_date" in row:
                    row["settlement_date"] = (date.fromisoformat(row["settlement_date"])+offset).isoformat()
            data["X"][name].extend(moved)
        for name in ("chain", "bars"):
            moved = copy.deepcopy([row for row in data["X"][name]
                                   if row["date"] >= "2019-02-11"])
            for row in moved:
                row["date"] = (date.fromisoformat(row["date"])+offset).isoformat()
                for key in ("expiration", "expiry", "settlement_date"):
                    if key in row:
                        row[key] = (date.fromisoformat(row[key])+offset).isoformat()
            if name == "bars":
                existing_dates = {row["date"] for row in data["X"][name]}
                moved = [row for row in moved if row["date"] not in existing_dates]
            data["X"][name].extend(moved)
    if getattr(request, "param", None) == "shallow_long_leg":
        _shallow_long_leg(data["X"])
    if getattr(request, "param", None) == "split":
        for row in data["X"]["chain"]:
            if row["date"] >= "2019-01-09":
                row["strike_raw"] /= 10
                row["basis_factor"] = 10.
        for row in data["X"]["bars"]:
            if row["date"] >= "2019-01-09":
                row["basis_factor"] = 10.
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
    packets = [{"id": "dev", "start": "2019-01-02", "end_before": "2019-01-08",
                "purpose": "development", "predecessor": None}]
    if policy_integration:
        packets.extend([
            {"id": "eval", "start": "2019-03-13", "end_before": "2019-03-20",
             "purpose": "development", "predecessor": "dev"},
            {"id": "policy-eval", "start": "2019-03-13", "end_before": "2019-03-20",
             "purpose": "development", "predecessor": "eval"},
        ])
    return {"contract": "index_options.mio_contract:MIOStudyContract",
            "registry": {"path": str(tmp_path/"exposure.sqlite"),
                         "id": "27e530ba-7ed9-4fd0-93bd-4b91ffdc1cf0", "domain": "synthetic:packet"},
            "packets": packets,
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
def test_real_json_spine_and_attested_comparison(study, tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
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
    run_index = Path(ran.run_dir) / "artifacts/mio-quotes/X.sqlite"
    cached = list((tmp_path / "cache-home/dskit/sqlite-parquet-index").rglob("*.sqlite"))
    assert len(cached) == 1 and run_index.stat().st_ino == cached[0].stat().st_ino
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
    from index_options.mio_contract import OptionPolicyStudyContract
    assert OptionPolicyStudyContract._receipt(receipt) == result_artifact
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


def test_mio_dimensions_node_closes_exact_cash_only_coverage(study, tmp_path, monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    from dskit.pipeline.study_protocol import StudyPacketClose
    from index_options.ledger_studies import MIOPacketDimensions

    monkeypatch.setattr("index_options.mio_contract.dimensions_memory_cap_bytes",
                        lambda: 5 * 1024**3)
    monkeypatch.setattr(TransportPortfolioSolve, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("dimensions node must not call integer MIO")))
    _, plan, permit = _open(study)
    ctx = NodeContext(name="dimensions", asof="2019-02-09", run_dir=str(tmp_path/"dimensions"))
    intake = MIOPacketInputs("intake", {"study": study}).run(ctx, {"permit": permit})
    node = MIOPacketDimensions("dimensions", {"study": study})
    result = node.run(ctx, {"permit": permit,
        "bounded_inputs": intake["bounded_inputs"], "provenance": intake["provenance"]})
    manifest = result["results"][0]["dimensions"]
    assert manifest["scope"] == "saved_mio_quarter"
    assert manifest["expected_row_count"] == len(manifest["rows"])
    assert manifest["request_row_count"] + manifest["no_request_row_count"] == manifest["expected_row_count"]
    assert manifest["cash_advances"] == manifest["measurement_calls"] == manifest["request_row_count"]
    assert manifest["model_calls"] == manifest["integer_optimizer_calls"] == 0
    assert all(row["row_type"] in {"request", "no_request"} for row in manifest["rows"])

    # The existing execution binding makes a changed/reordered manifest fail
    # before structural close validation can publish it.
    tampered = copy.deepcopy(result["audit"])
    tampered["expected_keys"] = list(reversed(tampered["expected_keys"]))
    close = StudyPacketClose("close", {"study": study})
    with pytest.raises(ValueError, match="unverified execution or intake"):
        close.run(ctx, {"permit": permit, "results": result["results"],
                        "audit": tampered, "provenance": intake["provenance"]})


@pytest.mark.parametrize("status", ["no_eligible_candidates", "kelly_risk_input"])
def test_dimensions_contract_rejects_missing_reordered_and_fabricated_rows(study, status):
    from dskit.pipeline.base import value_hash
    from index_options.mio_contract import MIOStudyContract

    methods = study["domain"]["params"]["methods"]
    expected = [[None, method["id"], "2019-01-02"] for method in methods]
    rows = [{"row_type": "no_request", "sequence_index": i, "cohort_id": None,
             "method_id": method["id"], "decision_date": "2019-01-02",
             "status": status, "candidate_count": 0}
            for i, method in enumerate(methods)]
    provenance = {"plan_sha256": "a"*64, "inputs_sha256": "b"*64,
                  "source_pins_sha256": "c"*64}
    row_counts = {"X": {"forecasts": 0, "contexts": 0, "bars": 0, "chain_rows": 0}}
    manifest, audit = MIOStudyContract.dimensions_manifest(
        "saved_mio_quarter", {"sha256": provenance["plan_sha256"]}, provenance,
        rows, expected, row_counts, 0.1, 1024, 5*1024**3,
        {"cash_advances": 0, "measurement_calls": 0, "model_calls": 0,
         "integer_optimizer_calls": 0})
    result = [{"id": "option-request-dimensions", "status": "completed",
               "dimensions": manifest}]
    assert MIOStudyContract().validate_dimensions_result(study, result, audit, provenance)
    altered = copy.deepcopy(audit)
    altered["expected_keys"] = altered["expected_keys"] + [[None, "forged-method", "2019-01-02"]]
    with pytest.raises(ValueError, match="dimensions manifest is not bound|reordered dimensions key"):
        MIOStudyContract().validate_dimensions_result(study, result, altered, provenance)
    omitted = copy.deepcopy(result)
    omitted[0]["dimensions"]["rows"].pop()
    with pytest.raises(ValueError, match="dimensions manifest is not bound|omit or add"):
        MIOStudyContract().validate_dimensions_result(study, omitted, audit, provenance)
    assert value_hash(audit["expected_keys"]) == audit["expected_keys_sha256"]


def test_mio_quote_index_reuses_verified_bytes_across_run_dirs(study, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    _, _, first_permit = _open(study)
    first_dir = tmp_path / "first-run"
    first = MIOPacketInputs("intake", {"study": study}).run(
        NodeContext(name="intake", asof="2019-02-09", run_dir=str(first_dir)),
        {"permit": first_permit})["bounded_inputs"]["inputs"]["X"]["chain_index"]["index"]
    repeated = copy.deepcopy(study)
    repeated["registry"]["path"] = str(tmp_path / "separate-exposure.sqlite")
    _, _, second_permit = _open(repeated)
    second_dir = tmp_path / "second-run"
    from dskit.pipeline.libs.parquet import DateBoundedParquet
    original_batches = DateBoundedParquet.batches
    def no_second_quote_build(self, *args, **kwargs):
        if "strike_raw" in self.columns:
            raise AssertionError("quote index rebuilt instead of reusing cache")
        yield from original_batches(self, *args, **kwargs)
    monkeypatch.setattr(DateBoundedParquet, "batches", no_second_quote_build)
    second = MIOPacketInputs("intake", {"study": repeated}).run(
        NodeContext(name="intake", asof="2019-02-09", run_dir=str(second_dir)),
        {"permit": second_permit})["bounded_inputs"]["inputs"]["X"]["chain_index"]["index"]
    assert first["sha256"] == second["sha256"] and first["rows"] == second["rows"]
    assert first["path"] != second["path"]
    assert Path(first["path"]).stat().st_ino == Path(second["path"]).stat().st_ino


def test_mio_quote_intake_runs_when_optional_cache_is_unavailable(study, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "/proc/dskit-index-cache-unavailable")
    _, _, permit = _open(study)
    result = MIOPacketInputs("intake", {"study": study}).run(
        NodeContext(name="intake", asof="2019-02-09", run_dir=str(tmp_path / "run")),
        {"permit": permit})
    descriptor = result["bounded_inputs"]["inputs"]["X"]["chain_index"]["index"]
    assert Path(descriptor["path"]).is_file()


def test_mio_quote_intake_ignores_relative_xdg_cache_home(study, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", "relative-cache")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    _, _, permit = _open(study)
    result = MIOPacketInputs("intake", {"study": study}).run(
        NodeContext(name="intake", asof="2019-02-09", run_dir=str(tmp_path / "run")),
        {"permit": permit})
    descriptor = result["bounded_inputs"]["inputs"]["X"]["chain_index"]["index"]
    cache = tmp_path / "home" / ".cache" / "dskit" / "sqlite-parquet-index"
    assert Path(descriptor["path"]).is_file() and len(list(cache.rglob("*.sqlite"))) == 1


def test_mio_cache_code_pin_identity_is_checkout_portable(tmp_path):
    portable = []
    for checkout in ("one", "two"):
        root = tmp_path / checkout
        pins = {}
        for relative, contents in (("dskit/__init__.py", ""),
                                   ("dskit/pipeline/__init__.py", ""),
                                   ("dskit/pipeline/study_protocol.py", "study"),
                                   ("index_options/__init__.py", ""),
                                   ("index_options/observations.py", "quotes")):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents)
            pins[str(path.resolve())] = path_hash(str(path))
        portable.append(MIOPacketInputs._portable_code_pins(pins))
    assert portable[0] == portable[1]
    assert set(portable[0]) == {"dskit/__init__.py", "dskit/pipeline/__init__.py",
                                "dskit/pipeline/study_protocol.py", "index_options/__init__.py",
                                "index_options/observations.py"}


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


def test_policy_training_accepts_only_subset_of_authenticated_full2019_receipt(tmp_path, monkeypatch):
    from index_options.mio_contract import OptionPolicyStudyContract

    study = _owner_selected_study(tmp_path, monkeypatch)
    study["domain"]["policy"] = {"mode": "training"}
    study["packets"][0].update(start="2019-01-02", end_before="2019-10-01")
    receipt = OptionPolicyStudyContract.selection(study)
    assert receipt["development_window"] == {
        "start": "2019-01-02", "end_before": "2020-01-01"}

    study["packets"][0].update(start="2018-12-31", end_before="2019-10-01")
    with pytest.raises(ValueError, match="bounded subset"):
        OptionPolicyStudyContract.selection(study)


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


@pytest.mark.parametrize("missing_symbol", [False, True])
def test_pinned_q1_real_onboarding_producer_and_packet(study, tmp_path, monkeypatch, missing_symbol):
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
    if missing_symbol:
        params["symbols"].append("Y")
        pairs.append({**pairs[0], "id": "Y30", "symbol": "Y"})
        data["Y"] = {role: [{**row, "symbol": "Y"} for row in data["X"][role]]
                     for role in ("chain", "bars")}
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
    prep.update(declared_cohort=params["symbols"], sources={"Q1": {
        "row_count": 2, "expected_names": ["X"],
        "missing_names": ["Y"] if missing_symbol else []}})
    receipt["preparation"]["sha256"] = save("receipt/preparation.json", prep)
    save("receipt/selection.json", receipt)
    save("admission.json", {"pairs": pairs, "quote_start": "2019-01-02",
         "quote_end_before": "2020-01-01", "settlement_end_before": "2020-01-01"})
    for role in ("chain", "bars"):
        rows = [row for rows in data.values() for row in rows[role]]
        pq.write_table(pa.Table.from_pylist(rows), metadata_root/(role+".parquet"))
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
        keys = params["symbols"] if role in {"bars", "chain"} else ["shared"]
        domain["sources"][role] = {"pin": pin, "files": dict.fromkeys(keys, relative)}
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
    if missing_symbol:
        intake = json.loads((Path(ran.run_dir)/"carry.json").read_text())["intake"]["bounded_inputs"]
        missing = intake["inputs"]["Y"]
        assert missing["forecasts"] == missing["contexts"] == []
        assert missing["chain_index"]["selected_rows"] == 0
        assert missing["chain_index"]["forecast_expiries"] == []
        assert missing["bars"] and missing["chain_index"]["basis_factors"]
        assert [row["date"] for row in actual["curve"]] == [
            d for d in intake["clocks"]["sessions"]
            if start <= d < intake["clocks"]["tail_end_before"]]
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

def test_mio_packet_binds_and_reads_pinned_split_source(study, tmp_path):
    """A split event enters through the claim's source pins and bounded intake."""
    origin = tmp_path / "split-origin"
    origin.mkdir()
    event = {"symbol": "X", "root_before": "X", "root_after": "X",
             "deliverable_shares": 100, "published": "2018-12-31", "effective": "2019-01-07",
             "ratio": 10, "strike_divisor": 10, "multiplier": 100,
             "basis_before": 1.0, "basis_after": 10.0}
    (origin / "adjustments.json").write_text(json.dumps([event]))
    (origin / "memo.txt").write_text("Synthetic standard split evidence")
    root = OnboardingRoot.create(str(tmp_path / "split-ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "fixture-split", "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(origin), "as_of": "2019-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "fixture-split", "files", "backfill")
    acquired = payload_files(root, "fixture-split", "files")
    pin = {"root": root.root, "source": "fixture-split", "stream": "files",
           "snapshot": acquired["snapshot"], "manifest_sha256": acquired["manifest_sha256"],
           "files": acquired["sha256"]}
    study["domain"]["sources"]["corporate_actions"] = {
        "pin": pin, "files": {"shared": "adjustments.json", "memo": "memo.txt"}}
    _, plan, permit = _open(study)
    assert plan["runnable"] and "corporate_actions" in plan["source_pins"]
    intake = MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    assert intake["bounded_inputs"]["adjustments"] == [event]
    assert intake["provenance"]["adjustments_sha256"] == value_hash([event])


@pytest.mark.parametrize("study", ["split"], indirect=True)
def test_split_packet_runs_and_replays_with_pinned_source(study, tmp_path, caplog):
    """The attested packet and replay use the same pinned ex-date conversion."""
    origin = tmp_path / "split-origin"
    origin.mkdir()
    event = {"symbol": "X", "root_before": "X", "root_after": "X",
             "deliverable_shares": 100, "published": "2018-12-31", "effective": "2019-01-09",
             "ratio": 10, "strike_divisor": 10, "multiplier": 100,
             "basis_before": 1., "basis_after": 10.}
    (origin / "adjustments.json").write_text(json.dumps([event]))
    (origin / "memo.txt").write_text("Synthetic standard split evidence")
    root = OnboardingRoot.create(str(tmp_path / "split-ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": "fixture-split", "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(origin), "as_of": "2019-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, "fixture-split", "files", "backfill")
    acquired = payload_files(root, "fixture-split", "files")
    pin = {"root": root.root, "source": "fixture-split", "stream": "files",
           "snapshot": acquired["snapshot"], "manifest_sha256": acquired["manifest_sha256"],
           "files": acquired["sha256"]}
    study["domain"]["sources"]["corporate_actions"] = {
        "pin": pin, "files": {"shared": "adjustments.json", "memo": "memo.txt"}}
    exposure = ExposureRegistry(study["registry"])
    exposure.initialize(study["packets"], [{"start": "2019-01-01", "end_before": "2020-01-01",
                                            "evidence": "synthetic prior use"}], "2020-01-01")
    plan = StudyContract.build(study)
    assert plan["runnable"]
    doc = _document(tmp_path, "split-packet", {
        "open_packet": {"uses": "dskit.pipeline.study_protocol:StudyPacketOpen",
                       "params": {"study": study, "frozen_plan": plan,
                                  "claim_request": exposure.freeze(plan)}},
        "intake": {"uses": "index_options.observations:MIOPacketInputs",
                   "params": {"study": study}, "inputs": {"permit": "$open_packet.permit"}},
        "study": {"uses": "index_options.ledger_studies:MIOPacketStudy",
                  "params": {"study": study},
                  "inputs": {"permit": "$open_packet.permit",
                             "bounded_inputs": "$intake.bounded_inputs",
                             "provenance": "$intake.provenance"}},
        "close": {"uses": "dskit.pipeline.study_protocol:StudyPacketClose",
                  "params": {"study": study},
                  "inputs": {"permit": "$open_packet.permit", "results": "$study.results",
                             "audit": "$study.audit", "provenance": "$intake.provenance"}}})
    ran = run_document(doc, asof="2019-02-09", journal=False)
    assert ran.state == "ran", ran
    receipt = RunAttestation(ran.run_dir).attested_output("close", "receipt", doc.hash)
    result = resolve_json_artifact(ran.run_dir, receipt)
    assert result["audit"]["adjustments_sha256"] == value_hash([event])
    method = result["results"][0]
    assert method["status"] == "completed"
    assert any(book.get("applied_adjustments") for book in method["result"]["trades"])
    assert any('"type":"replay_verified"' in record.getMessage() for record in caplog.records)


def test_policy_contract_requires_reviewed_probe_before_training(study):
    from index_options.mio_contract import OptionPolicyStudyContract
    from index_options.rl_env import OptionStructuredEntryEnv

    packet = study["packets"][0]
    method_id = study["domain"]["params"]["methods"][0]["id"]
    study["domain"]["policy"] = {
        "mode": "training", "method_id": method_id, "evaluation": None,
        "artifact": None, "baselines": [], "probe_receipt": None,
        "training": {
            "window": {"start": packet["start"], "end_before": packet["end_before"]},
            "maturity_end_before": packet["end_before"],
            "env": "index_options.rl_env:OptionStructuredEntryEnv",
            "env_schema": OptionStructuredEntryEnv.PAYLOAD_SCHEMA, "feature_transform": "identity",
            "env_bounds": {"max_coordinates": 1, "max_quantity": 1, "max_features": 1,
                           "reward_scale": 1.0, "max_edits": 1}, "algo": "PPO", "algo_params": {"gamma": 1},
            "policy": "MlpPolicy", "seed": 7, "total_timesteps": 12,
        },
    }
    with pytest.raises(ValueError, match="attested feasibility probe receipt"):
        OptionPolicyStudyContract()._validate_policy(study)
    study["domain"]["policy"]["mode"] = "probe"
    study["domain"]["policy"]["training"]["maturity_end_before"] = "2019-12-31"
    with pytest.raises(ValueError, match="exceed selected packet read tail"):
        OptionPolicyStudyContract()._validate_policy(study)



def test_policy_contract_requires_settled_evaluation_result_shape(study):
    from index_options.mio_contract import OptionPolicyStudyContract

    method_id = study["domain"]["params"]["methods"][0]["id"]
    study["domain"]["policy"] = {"mode": "evaluation", "method_id": method_id}
    with pytest.raises(ValueError, match="closed option-policy"):
        OptionPolicyStudyContract()._validate_policy(study)


@pytest.mark.parametrize("study", ["policy_integration"], indirect=True)
def test_policy_packet_train_attested_eval_replay_and_close(study, tmp_path, monkeypatch):
    """Exercise real SB3 learning, model restoration, accounting replay and packet closure."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "policy-cache"))
    from index_options.rl_env import OptionStructuredEntryEnv

    training = {
        "window": {"start": "2019-01-02", "end_before": "2019-01-08"},
        "maturity_end_before": "2019-02-09",
        "env": "index_options.rl_env:OptionStructuredEntryEnv",
        "env_schema": OptionStructuredEntryEnv.PAYLOAD_SCHEMA,
        "feature_transform": "signed_log1p",
        "env_bounds": {"max_coordinates": 64, "max_quantity": 16,
            "max_features": 4096, "reward_scale": 100., "max_edits": 64},
        "algo": "PPO", "algo_params": {"gamma": 1., "n_steps": 8, "batch_size": 8},
        "policy": "MlpPolicy", "seed": 13, "total_timesteps": 128,
    }
    study["domain"]["params"]["solver"]["group_screen"] = "lp"
    method_id = study["domain"]["params"]["methods"][0]["id"]
    packet_for = {p["id"]: p for p in study["packets"]}

    def configured(selected, registry_path, *, mode, artifact=None, baselines=None,
                   probe_receipt=None):
        doc = copy.deepcopy(study)
        doc["contract"] = "index_options.mio_contract:OptionPolicyStudyContract" if mode != "baseline" else "index_options.mio_contract:MIOStudyContract"
        doc["selected_packet"] = selected
        doc["registry"]["path"] = str(registry_path)
        import uuid
        doc["registry"]["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, str(registry_path)))
        packet = copy.deepcopy(packet_for[selected])
        # Each synthetic run has an isolated registry; test receipt matching by
        # exact window/economics while allowing a distinct packet identity.
        packet["predecessor"] = None
        doc["packets"] = [packet]
        if selected in {"eval", "policy-eval"}:
            # Keep evaluation reads strictly after training-label maturity while retaining
            # the intervening +40-day bars as causal lookback history.
            doc["domain"]["history_start"] = "2019-02-10"
        doc["domain"]["params"]["bridge"].update(
            start=packet["start"], end_before=packet["end_before"],
            protected_end_before="2020-01-01")
        if mode == "baseline":
            doc["domain"].pop("policy", None)
            return doc
        if mode == "probe":
            policy = {"mode": "probe", "method_id": method_id, "training": training,
                      "evaluation": None, "artifact": None, "baselines": [],
                      "probe_receipt": None}
        elif mode == "training":
            policy = {"mode": "training", "method_id": method_id, "training": training,
                      "evaluation": None, "artifact": None, "baselines": [],
                      "probe_receipt": probe_receipt}
        else:
            policy = {"mode": "evaluation", "method_id": method_id, "training": training,
                "evaluation": {"window": {"start": packet["start"],
                                            "end_before": packet["end_before"]},
                    "env": training["env"], "split": "test", "deterministic": True,
                    "max_episode_steps": 4, "n_episodes": 1, "seed": 17,
                    "feature_transform": training["feature_transform"],
                    "env_bounds": training["env_bounds"]},
                "artifact": artifact, "baselines": baselines,
                "probe_receipt": probe_receipt}
        doc["domain"]["policy"] = policy
        return doc

    def run_close(doc, dirname, node_name, *, review=False):
        registry = ExposureRegistry(doc["registry"])
        try:
            registry.snapshot()
            initialized = True
        except (OSError, ValueError):
            initialized = False
        if not initialized:
            registry.initialize(doc["packets"],
                [{"start": "2018-01-01", "end_before": "2020-01-01",
                  "evidence": "synthetic integration fixture"}], "2021-01-01")
        plan = StudyContract.build(doc)
        assert plan["runnable"], plan
        claim = registry.freeze(plan)
        module = "index_options.ledger_studies" if node_name == "MIOPacketStudy" else "index_options.mio_contract"
        pipeline = _document(tmp_path, dirname, {
            "open_packet": {"uses": "dskit.pipeline.study_protocol:StudyPacketOpen",
                "params": {"study": doc, "frozen_plan": plan, "claim_request": claim}},
            "intake": {"uses": "index_options.observations:MIOPacketInputs",
                "params": {"study": doc}, "inputs": {"permit": "$open_packet.permit"}},
            "study": {"uses": f"{module}:{node_name}", "params": {"study": doc},
                "inputs": {"permit": "$open_packet.permit",
                    "bounded_inputs": "$intake.bounded_inputs",
                    "provenance": "$intake.provenance"}},
            "close": {"uses": "dskit.pipeline.study_protocol:StudyPacketClose",
                "params": {"study": doc}, "inputs": {"permit": "$open_packet.permit",
                    "results": "$study.results", "audit": "$study.audit",
                    "provenance": "$intake.provenance"}}})
        ran = run_document(pipeline, asof="2019-04-01", journal=False)
        assert ran.state == "ran", ran.error
        manifest = RunAttestation(ran.run_dir).attested_output("close", "receipt", pipeline.hash)
        assert manifest is not None
        receipt_data = resolve_json_artifact(ran.run_dir, manifest)
        assert receipt_data["complete"] is True, {
            "results": receipt_data.get("results"), "audit": receipt_data.get("audit")}
        receipt_ref = {"run_dir": str(ran.run_dir), "document_hash": pipeline.hash,
                       "node": "close", "port": "receipt"}
        if review:
            registry.dispose(doc["selected_packet"], "review",
                             "synthetic first-request feasibility evidence reviewed",
                             receipt_ref)
        return pipeline, ran, receipt_data, receipt_ref

    def direct_train_setup(doc, run_dir, *, bypass_probe_gate=False):
        from dskit.pipeline.base import value_hash
        from index_options.observations import MIOPacketInputs
        from index_options.mio_contract import OptionPolicyStudyContract
        from index_options.rl_env import OptionStructuredEntryEnv

        plan = StudyContract.build(doc)
        registry = ExposureRegistry(doc["registry"])
        permit = registry.claim(plan, registry.freeze(plan))
        ctx = type("Context", (), {"run_dir": str(run_dir)})()
        intake = MIOPacketInputs("intake", {"study": doc}).run(ctx, {"permit": permit})
        bounded, provenance = intake["bounded_inputs"], intake["provenance"]
        artifact_root = str(run_dir/"artifacts"/"mio-quotes")
        if bypass_probe_gate:
            # Model a previously assembled, valid canonical dataset presented
            # directly to SB3. This bypass is only for constructing that
            # transport object; the direct execution facade below remains real.
            from index_options.ledger_studies import MIOPacketStudy

            def active_claim_only(self, study, plan, permit, provenance, bounded,
                                  artifact_root, phase, *, run_dir=None):
                auth_registry, params = MIOPacketStudy.validate_authenticated_intake(
                    study, plan, permit, provenance, bounded, artifact_root)
                return auth_registry, params, None

            with monkeypatch.context() as local:
                local.setattr(OptionPolicyStudyContract, "authorize_execution",
                              active_claim_only)
                dataset, canonical = OptionStructuredEntryEnv.packet_dataset(
                    doc, plan, permit, provenance, bounded, artifact_root,
                    phase="training", run_dir=str(run_dir))
        else:
            dataset, canonical = OptionStructuredEntryEnv.packet_dataset(
                doc, plan, permit, provenance, bounded, artifact_root,
                phase="training", run_dir=str(run_dir))
        assert dataset["canonical_payload_sha256"] == canonical
        if bypass_probe_gate:
            # The direct facade receives the canonical packet shape, but not
            # its reviewed-probe authorization.
            pass
        spec = doc["domain"]["policy"]["training"]
        env_params = {**spec["env_bounds"],
                      "feature_transform": spec["feature_transform"],
                      "dataset": dataset, "dataset_sha256": value_hash(dataset),
                      "source_hashes": OptionStructuredEntryEnv.source_identity(),
                      "split": "train"}
        node_params = {"algo": spec["algo"], "algo_params": spec["algo_params"],
                       "env": spec["env"], "env_params": env_params,
                       "policy": spec["policy"], "seed": spec["seed"],
                       "total_timesteps": spec["total_timesteps"]}
        return registry, plan, permit, ctx, env_params, node_params, bounded, provenance, artifact_root

    dimensions_doc = configured("dev", tmp_path/"dimensions-registry.sqlite", mode="probe")
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
    monkeypatch.setattr("index_options.mio_contract.dimensions_memory_cap_bytes",
                        lambda: 5 * 1024**3)
    with monkeypatch.context() as dimensions_guard:
        dimensions_guard.setattr(TransportPortfolioSolve, "run", lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("policy dimensions must not call integer MIO")))
        _, _, dimensions_close, dimensions_receipt = run_close(
            dimensions_doc, "policy-dimensions", "OptionPolicyDimensionsPacket", review=True)
    dimension_manifest = dimensions_close["results"][0]["dimensions"]
    assert dimension_manifest["scope"] == "policy_training_window"
    assert dimension_manifest["request_row_count"] + dimension_manifest["no_request_row_count"] == len(
        dimension_manifest["rows"])
    assert dimension_manifest["model_calls"] == dimension_manifest["integer_optimizer_calls"] == 0
    assert dimensions_receipt["port"] == "receipt"

    registry_path = tmp_path/"train-registry.sqlite"
    probe_doc = configured("dev", registry_path, mode="probe")
    probe_pipeline, probe_run, probe_close, probe_receipt = run_close(
        probe_doc, "policy-probe", "OptionPolicyFeasibilityProbePacket", review=True)
    assert probe_close["results"][0]["probe"]["feasible"] is True
    assert probe_close["results"][0]["probe"]["step_calls"] == 0
    assert probe_close["results"][0]["probe"]["learn_calls"] == 0

    train_doc = configured("dev", registry_path, mode="training",
                           probe_receipt=probe_receipt)
    (direct_registry, direct_plan, direct_permit, direct_ctx, direct_env, direct_params,
     direct_bounded, direct_provenance, direct_artifact_root) = direct_train_setup(
        train_doc, tmp_path/"direct-reviewed-train")
    from dskit.pipeline.libs.sb3 import Sb3Train

    reached = []
    def stop_after_authorized_hook(key, algo):
        reached.append("algo")
        raise RuntimeError("authorization passed")
    with monkeypatch.context() as direct_patch:
        direct_patch.setattr(Sb3Train, "_resolve_algo",
                             staticmethod(stop_after_authorized_hook))
        direct_node = Sb3Train("direct_train", direct_params)
        with pytest.raises(RuntimeError, match="authorization passed"):
            direct_node.run_train(direct_ctx, {})
    assert reached == ["algo"]

    from index_options.rl_env import OptionStructuredEntryEnv
    wrong_root_ctx = type("Context", (), {
        "run_dir": str(tmp_path/"wrong-current-run")})()
    wrong_root_problems = OptionStructuredEntryEnv.validate_execution_context(
        wrong_root_ctx, direct_params, "train")
    assert any("quote root differs" in problem for problem in wrong_root_problems)

    changed_config = copy.deepcopy(direct_params)
    changed_config["total_timesteps"] += 1
    changed_problems = OptionStructuredEntryEnv.validate_execution_context(
        direct_ctx, changed_config, "train")
    assert any("SB3 node settings differ" in problem for problem in changed_problems)

    changed_provenance = copy.deepcopy(direct_provenance)
    changed_provenance["inputs_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="unverified bounded intake"):
        OptionStructuredEntryEnv.packet_dataset(
            train_doc, direct_plan, direct_permit, changed_provenance,
            direct_bounded, direct_artifact_root, phase="training",
            run_dir=direct_ctx.run_dir)

    direct_registry.dispose("dev", "abandon", "direct facade authorization check")
    with pytest.raises(ValueError, match="inactive packet permit"):
        OptionStructuredEntryEnv.packet_dataset(
            train_doc, direct_plan, direct_permit, direct_provenance,
            direct_bounded, direct_artifact_root, phase="training",
            run_dir=direct_ctx.run_dir)

    train_pipeline, train_run, train_close, _ = run_close(
        train_doc, "policy-train", "OptionPolicyTrainPacket")
    assert train_close["results"][0]["training"]["completed_episodes"] > 0
    policy_artifact = train_close["results"][0]["artifact"]
    training_receipt = {"run_dir": str(train_run.run_dir),
        "document_hash": train_pipeline.hash, "node": "close", "port": "receipt"}
    assert OptionPolicyStudyContract._receipt(training_receipt)["complete"] is True

    baseline_doc = configured("eval", tmp_path/"baseline-registry.sqlite", mode="baseline")
    baseline_method = copy.deepcopy(baseline_doc["domain"]["params"]["methods"][0])
    baseline_doc["domain"]["params"]["methods"] = [
        baseline_method,
        {**copy.deepcopy(baseline_method), "id": "alternative_a"},
        {**copy.deepcopy(baseline_method), "id": "alternative_b"},
    ]
    baseline_pipeline, baseline_run, baseline_close, _ = run_close(
        baseline_doc, "policy-baseline", "MIOPacketStudy")
    baseline_receipt = {"run_dir": str(baseline_run.run_dir),
        "document_hash": baseline_pipeline.hash, "node": "close", "port": "receipt",
        "result_sha256": value_hash(baseline_close["results"])}
    artifact_ref = {**policy_artifact, "training_receipt": training_receipt}

    eval_doc = configured("policy-eval", tmp_path/"eval-registry.sqlite", mode="evaluation",
                          artifact=artifact_ref, baselines=[baseline_receipt],
                          probe_receipt=probe_receipt)
    eval_pipeline, eval_run, eval_close, _ = run_close(
        eval_doc, "policy-eval", "OptionPolicyEvalPacket")
    result = eval_close["results"][0]
    assert result["replay_parity"] is True
    assert result["result"]["curve"][-1]["open_ids"] == []
    assert eval_close["audit"]["open_books"] == 0
    assert result["result"]["unbounded_books"] == 0
    assert eval_close["audit"]["runtime_checks"] == {
        "artifact_state": True, "decision_replay": True, "settled_account": True}


    # A completed but abandoned probe is not an execution authorization. A
    # fresh, active train claim with the same economics/source still must stop
    # before the direct SB3 facade resolves a model or constructs an env.
    abandoned_registry_path = tmp_path/"abandoned-probe-registry.sqlite"
    abandoned_probe = configured("dev", abandoned_registry_path, mode="probe")
    _, _, abandoned_close, abandoned_ref = run_close(
        abandoned_probe, "abandoned-policy-probe",
        "OptionPolicyFeasibilityProbePacket", review=False)
    assert abandoned_close["results"][0]["probe"]["feasible"] is True
    ExposureRegistry(abandoned_probe["registry"]).dispose(
        "dev", "abandon", "completed probe intentionally not reviewed")
    abandoned_train = configured("dev", abandoned_registry_path, mode="training",
                                 probe_receipt=abandoned_ref)
    (abandoned_registry, abandoned_plan, abandoned_permit, abandoned_ctx,
     abandoned_env, abandoned_node_params, bounded, provenance, artifact_root) = (
        direct_train_setup(abandoned_train, tmp_path/"direct-abandoned-train",
                           bypass_probe_gate=True))

    # Direct public construction and packet preparation must independently
    # reject the abandoned publication even though the new claim is active.
    with pytest.raises(ValueError, match="published and reviewed"):
        OptionStructuredEntryEnv(**abandoned_env)
    with pytest.raises(ValueError, match="published and reviewed"):
        OptionStructuredEntryEnv.packet_dataset(
            abandoned_train, abandoned_plan, abandoned_permit, provenance, bounded,
            artifact_root, phase="training", run_dir=abandoned_ctx.run_dir)

    reached = []
    def forbidden_effect(key, *args, **kwargs):
        reached.append(key)
        raise AssertionError("model/environment work began before authorization")
    with monkeypatch.context() as direct_patch:
        direct_patch.setattr(Sb3Train, "_resolve_algo", forbidden_effect)
        direct_patch.setattr(Sb3Train, "_build_env", forbidden_effect)
        direct_node = Sb3Train("direct_abandoned_train", abandoned_node_params)
        with pytest.raises(ValueError, match="published and reviewed"):
            direct_node.run_train(abandoned_ctx, {})
    assert reached == []


def test_blinded_payload_gate_keeps_legacy_synthetic_contract(study):
    from index_options.mio_contract import MIOStudyContract
    verify = getattr(MIOStudyContract, "verify_blinded_payloads", None)
    assert callable(verify), "post-claim blinded payload verifier is missing"
    assert verify(study, None, None) is None

def _blinded_cache_study(tmp_path, monkeypatch, payload_fault=None):
    """Small persisted two-quarter ancestry; no training or market outcomes."""
    import hashlib
    import io
    import pandas as pd
    import index_options.mio_contract as contract
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore

    archive = tmp_path/"blinded"
    archive.mkdir()
    pin = {"root": "fixture", "source": "blind", "stream": "files", "snapshot": "one",
           "manifest_sha256": "a"*64, "files": {}}
    metadata = {}

    def save(name, value):
        path = archive/name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = value if isinstance(value, bytes) else json.dumps(value).encode()
        path.write_bytes(data)
        pin["files"][name] = hashlib.sha256(data).hexdigest()
        return {"file": name, "sha256": pin["files"][name]}

    def evidence(original, value, lines=False):
        name = "evidence"+original
        item = save(name, (json.dumps(value)+"\n").encode() if lines else value)
        metadata[original] = item
        return item

    def parquet(frame):
        buffer = io.BytesIO()
        frame.to_parquet(buffer, index=False)
        return buffer.getvalue()

    def publish(original, identity, files):
        directory = "stores"+original
        store = AtomicFitStore(archive/directory, identity)
        record = store.publish({k: v if isinstance(v, bytes) else json.dumps(v).encode()
                                for k, v in files.items()})
        for path in store.path.rglob("*"):
            if path.is_file():
                rel = path.relative_to(archive).as_posix()
                pin["files"][rel] = AtomicFitStore.file_hash(path)
                if path.suffix == ".json":
                    metadata[original+"/"+path.relative_to(store.path).as_posix()] = {
                        "file": rel, "sha256": pin["files"][rel]}
        return record

    labels, grid = ["low", "mid", "high"], [-3., 0., 3.]
    keys = ["symbol", "quote_date", "expiry"]
    template = {"method": {"by": "disp", "components": ["model", "market_cal", "pooled", "baseline"],
                           "kind": "quantile_average", "learn": "fit_band", "levels": 199,
                           "max_learning_rows": 100000, "min_rows": 5000},
                "tables": [], "grid_labels": labels, "z_grid": grid, "fallback": "model"}
    warm_info = {"source": "/original/annual.parquet", "sha256": "b"*64}
    warm = "/warmup/11"
    warm_identity = {"manifest": value_hash(warm_info), "role": "unranked_annual_2019_warmup"}
    publish(warm, warm_identity, {"forecasts.parquet": parquet(pd.DataFrame({
        "symbol": ["X"], "quote_date": ["2019-12-01"], "expiry": ["2019-12-06"]})),
        "receipt.json": {"identity": warm_identity, "source": warm_info,
                         "role": "unranked_annual_2019_warmup", "evaluation_computed": False,
                         "map_cutoff": "2019-01-01"}})
    jobs, roles, receipts, bank = [], {}, {}, [warm]
    panel_rows = [{"symbol": "X", "quote_date": start, "expiry": expiry, "spot": 100.,
                   "reference_scale": .2, "settlement_date": expiry}
                  for start, expiry in [("2020-01-02", "2020-04-03"), ("2020-04-01", "2020-07-03")]]
    panel = save("panel.parquet", parquet(pd.DataFrame(panel_rows)))
    for number, (start, end) in enumerate([("2020-01-01", "2020-04-01"), ("2020-04-01", "2020-07-01")], 2):
        from datetime import date, timedelta
        name = "q"+str(number)
        fold_path, cfg_path, spec_path = "/"+name+"/fold.jsonl", "/"+name+"/fit.json", "/"+name+"/request.json"
        fold = {"fold": number, "role": "scored", "train_start": "2017-01-03",
                "train_end": (date.fromisoformat(start)-timedelta(days=1)).isoformat(),
                "val_start": start, "val_end": (date.fromisoformat(end)-timedelta(days=1)).isoformat()}
        fpin = evidence(fold_path, fold, lines=True)
        cfg = {"date_upper_exclusive": start, "end_upper_exclusive": start, "refit_full_train": True,
               "training": {"patience": None, "seeds": [11], "epochs": 2},
               "study": {"fold_table": {"path": fold_path, "sha256": fpin["sha256"]}},
               "arms": {"features": {"features": ["a", "b"]}}, "tickers": ["X"],
               "panel_sha256": panel["sha256"]}
        cfg_pin = evidence(cfg_path, cfg)
        current = {role: "/"+name+"/"+role for role in ("fit", "completed", "component", "blend")}
        spec = {"start": start, "end_before": end, "fold": number, "arm": "features",
                "output": current["component"], "z_grid": grid, "grid_labels": labels}
        spec_pin = evidence(spec_path, spec)
        job = {"name": name, "seed": 11, "start": start, "end_before": end, "fold": number,
               "fit_config": cfg_path, "fit_config_sha256": cfg_pin["sha256"],
               "forecast_config": spec_path, "forecast_config_sha256": spec_pin["sha256"],
               "epoch_pin": {"epochs": 2, "monitor_settlement_max": "2019-12-27"}}
        epoch_dates = {"quote_date": ["2017-01-03", "2019-12-26"],
                       "settlement_date": ["2017-01-06", "2019-12-27"]}
        ep_state = evidence("/"+name+"/epoch-state.json",
                            {"settings": {"seeds": [11]}, "best_epochs": [2]})
        ep_meta = evidence("/"+name+"/epoch-meta.json", {"band_dates": [epoch_dates, epoch_dates]})
        job["epoch_pin"].update(state="/"+name+"/epoch-state.json", state_sha256=ep_state["sha256"],
                                metadata="/"+name+"/epoch-meta.json", metadata_sha256=ep_meta["sha256"])
        jobs.append(job)
        roles[name] = current
        dates = {"quote_date": ["2017-01-03", "2019-12-26"],
                 "settlement_date": ["2017-01-06", "2019-12-27"]}
        fit_meta = {"band_counts": [10, 2, 0], "band_dates": [
            dates, dates, {"quote_date": [None, None], "settlement_date": [None, None]}],
            "features": ["a", "b"], "groups": ["X"]}
        fit_identity = {"job": job, "kind": "trained"}
        fit_record = publish(current["fit"], fit_identity, {"metadata.json": fit_meta, "weights.bin": b"weights"})
        checkpoint = value_hash(fit_record)
        publish(current["completed"], fit_identity,
                {"summary.json": {"checkpoint": checkpoint, "settings": cfg["training"]}})
        frame = pd.DataFrame([panel_rows[number-2]])
        if payload_fault == "component_outcome" and number == 3:
            frame["actual"] = .42
        component_identity = {"experiment": value_hash(cfg), "request": spec, "checkpoint": checkpoint}
        component_meta = {"identity": component_identity, "groups": ["X"], "label_cutoff": start,
                          "calibration": {"label_cutoff": start}, "evaluation_computed": False,
                          "forecast_identity": value_hash(frame[keys].values.tolist()), "rows": len(frame),
                          "forecast_columns": list(frame)}
        publish(current["component"], component_identity,
                {"receipt.json": component_meta, "forecasts.parquet": parquet(frame)})
        blend_identity = {"job": job, "bank": {p: metadata[p+"/complete.json"]["sha256"] for p in bank},
                          "target": metadata[current["component"]+"/complete.json"]["sha256"],
                          "blend_template": value_hash(template), "label_cutoff": start}
        params = {**template, "source": "scratch", "source_sha256": "c"*64, "output": "scratch-out",
                  "end_before": end, "forecast_only": True, "label_cutoff": start, "ranked_folds": [number]}
        forecast = frame[keys].assign(fold=number, band="val", method_cdf_low=.1, method_cdf_mid=.5,
                                      method_cdf_high=.9, fitted=True)
        if number == 3 and payload_fault:
            if payload_fault == "cdf":
                forecast["method_cdf_mid"] = .99
            elif payload_fault == "forecast_outcome":
                forecast["actual"] = .42
            elif payload_fault == "flag":
                forecast["fitted"] = 1
            elif payload_fault == "fold":
                forecast["fold"] = 2
        blend_meta = {"identity": blend_identity, "params": params, "evaluation_computed": False,
                      "label_cutoff": start, "forecast_identity": component_meta["forecast_identity"]}
        publish(current["blend"], blend_identity, {"receipt.json": blend_meta,
                "forecasts.parquet": parquet(forecast), "fit.json": {"method": template["method"],
                "folds": {str(number): {"learn": "fit_band", "state": {}}}}})
        receipts[name] = {"schema_version": 1, "job": job, "elapsed_seconds": 1., "attempts": [],
                          "artifacts": {p+"/complete.json": metadata[p+"/complete.json"]["sha256"]
                                        for p in [*current.values(), *bank]},
                          "host": "WSL2", "evaluation_computed": False}
        bank.append(current["component"])
    manifest = {"jobs": jobs, "seeds": [11], "primary_seed": 11, "warmup": {"11": warm_info},
                "blend_template": template}
    mpin = save("manifest.json", manifest)
    lock = save("lock.json", {"status": "closed", "open_findings": [], "manifest_sha256": mpin["sha256"],
                              "reviews": {"correctness": "d"*64, "integration": "e"*64}})
    records = [{"job": j["name"], "ancestry_verified": True, "outcomes_absent": True,
                "cdf_valid": True, "one_quarter_state": True, "evaluation_computed": False} for j in jobs]
    coverage = save("coverage.json", {"manifest_sha256": mpin["sha256"], "jobs": len(jobs),
        "records": records, "evaluation_computed": False, "evaluation_values_inspected": False,
        "protected_reads": False})
    for name, proof in receipts.items():
        proof["manifest_sha256"] = mpin["sha256"]
        receipts[name] = save("receipts/"+name+".json", proof)
    job = jobs[-1]
    forecast_file = "stores"+roles[job["name"]]["blend"]+"/forecasts.parquet"
    context = pd.DataFrame([panel_rows[-1]]).assign(fold=job["fold"], band="val")
    if payload_fault == "projection":
        context["spot"] = 101.
    elif payload_fault == "context_outcome":
        context["actual"] = .42
    elif payload_fault == "missing_context":
        context = context.iloc[:0]
    context_pin = save("context.parquet", parquet(context))
    from index_options.mio_contract import MIOStudyContract
    projections = {}
    project_roles = {warm+"/receipt.json": "warmup"}
    for name, current in roles.items():
        project_roles.update({current["fit"]+"/metadata.json": "fit",
                              current["completed"]+"/summary.json": "completed",
                              current["component"]+"/receipt.json": "component",
                              current["blend"]+"/receipt.json": "blend",
                              "/"+name+"/epoch-meta.json": "epoch"})
    for original, role in project_roles.items():
        ref = metadata[original]
        raw = json.loads((archive/ref["file"]).read_text())
        projections[original] = save("projections"+original, {
            "role": role, "source_sha256": ref["sha256"],
            "value": MIOStudyContract.blinded_metadata(role, raw)})
    receipt = {"schema": "blinded-quarterly-mio/v1", "job": job["name"], "seed": 11,
               "forecast_window": {"start": job["start"], "end_before": job["end_before"]},
               "manifest": mpin, "lock": lock, "coverage": coverage, "job_receipts": receipts,
               "metadata": metadata, "projections": projections, "roles": roles, "warmup": warm, "panel": panel,
               "stitched": {"forecast": {"relpath": forecast_file, "sha256": pin["files"][forecast_file]},
                            "context": {"relpath": context_pin["file"], "sha256": context_pin["sha256"]}}}
    receipt_pin = save("receipt.json", receipt)
    study = {"packets": [{"id": "q", "start": job["start"], "end_before": job["end_before"]}],
             "selected_packet": "q", "registry": {},
             "domain": {"synthetic": False, "params": {"symbols": ["X"], "bridge": {
                 "fold": job["fold"], "start": job["start"], "end_before": job["end_before"],
                 "protected_end_before": "2024-01-01", "grid_labels": labels, "z_grid": grid}},
                 "sources": {role: {"pin": pin, "files": {"shared": receipt["stitched"][key]["relpath"]}}
                             for role, key in (("forecasts", "forecast"), ("contexts", "context"))}}}
    study["domain"]["predictive"] = {"policy": "owner_selected_blinded_cache",
        "receipt": {"source": pin, **receipt_pin}, "expected": {
            "manifest_sha256": mpin["sha256"], "lock_sha256": lock["sha256"],
            "coverage_sha256": coverage["sha256"], "job": job["name"], "seed": 11}}

    class Source:
        def __init__(self, source_pin):
            self.pin = source_pin

        def file(self, relative, *, verify=False):
            path = archive/relative
            if verify and AtomicFitStore.file_hash(path) != self.pin["files"][relative]:
                raise ValueError("payload drift")
            return path

    monkeypatch.setattr(contract, "PinnedPayloadSource", Source)
    return study, archive, receipt, save


def test_blinded_cache_metadata_and_postclaim_payloads(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore
    study, archive, receipt, _ = _blinded_cache_study(tmp_path, monkeypatch)
    # Selection must not hash raw payloads or perform model inference.
    original = AtomicFitStore.verify_pinned
    monkeypatch.setattr(AtomicFitStore, "verify_pinned", lambda *a: pytest.fail("preclaim payload read"))
    verified = MIOStudyContract.selection(study)
    assert verified["job"]["name"] == "q3"
    monkeypatch.setattr(AtomicFitStore, "verify_pinned", original)
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda s, p: p))
    monkeypatch.setattr(ExposureRegistry, "__init__", lambda self, spec: None)
    calls = []
    monkeypatch.setattr(ExposureRegistry, "verify", lambda self, p, permit: calls.append(permit))
    assert MIOStudyContract.verify_blinded_payloads(study, {}, "active") == {
        "job": "q3", "seed": 11, "rows": 1, "evaluation_computed": False}
    assert calls == ["active"]
    # Crossing-quarter expiry is preserved by structural verification.
    import pandas as pd
    assert pd.read_parquet(archive/receipt["stitched"]["forecast"]["relpath"]).expiry.tolist() == ["2020-07-03"]


@pytest.mark.parametrize("field,value", [
    ("seed", True), ("seed", 29), ("job", "q2"), ("manifest_sha256", "0"*64),
    ("lock_sha256", "0"*64), ("coverage_sha256", "0"*64)])
def test_blinded_cache_independent_authority_refuses(tmp_path, monkeypatch, field, value):
    from index_options.mio_contract import MIOStudyContract
    study, _, _, _ = _blinded_cache_study(tmp_path, monkeypatch)
    study["domain"]["predictive"]["expected"][field] = value
    with pytest.raises(ValueError):
        MIOStudyContract.selection(study)


@pytest.mark.parametrize("change", ["quarter", "fold_bool", "grid", "unknown", "missing_bank",
                                   "partial_coverage", "training_cutoff", "seed_mix"])
def test_blinded_cache_metadata_counterexamples(tmp_path, monkeypatch, change):
    from index_options.mio_contract import MIOStudyContract
    study, archive, receipt, save = _blinded_cache_study(tmp_path, monkeypatch)
    if change in {"quarter", "fold_bool", "grid"}:
        key, val = {"quarter": ("start", "2020-04-02"), "fold_bool": ("fold", True),
                    "grid": ("z_grid", [-4., 0., 4.])}[change]
        study["domain"]["params"]["bridge"][key] = val
    elif change == "unknown":
        receipt["unknown"] = True
    elif change == "missing_bank":
        del receipt["roles"]["q2"]
    elif change == "partial_coverage":
        proof = json.loads((archive/"coverage.json").read_text())
        proof["records"] = proof["records"][:1]
        proof["jobs"] = 1
        receipt["coverage"] = save("coverage.json", proof)
        study["domain"]["predictive"]["expected"]["coverage_sha256"] = receipt["coverage"]["sha256"]
    else:
        path = receipt["metadata"]["/q3/fit.json"]["file"]
        cfg = json.loads((archive/path).read_text())
        cfg["date_upper_exclusive" if change == "training_cutoff" else "training"] = (
            "2020-04-02" if change == "training_cutoff" else {"seeds": [29], "epochs": 2, "patience": None})
        receipt["metadata"]["/q3/fit.json"] = save(path, cfg)
    reference = save("receipt.json", receipt)
    study["domain"]["predictive"]["receipt"].update(reference)
    with pytest.raises(ValueError):
        MIOStudyContract.selection(study)


@pytest.mark.parametrize("change", ["weights", "missing", "extra", "context", "actual", "forecast"])
def test_blinded_cache_postclaim_corruption_refuses(tmp_path, monkeypatch, change):
    import pandas as pd
    from index_options.mio_contract import MIOStudyContract
    from dskit.pipeline.libs.cdf_experiment import IntegrityError
    study, archive, receipt, _ = _blinded_cache_study(tmp_path, monkeypatch)
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda s, p: p))
    monkeypatch.setattr(ExposureRegistry, "__init__", lambda self, spec: None)
    monkeypatch.setattr(ExposureRegistry, "verify", lambda *a: None)
    if change in {"weights", "missing", "extra"}:
        target = archive/"stores/q2/fit/weights.bin"
        if change == "missing":
            target.unlink()
        elif change == "extra":
            (target.parent/"extra").write_text("extra")
        else:
            target.write_bytes(b"changed")
    else:
        relative = receipt["stitched"]["forecast" if change == "forecast" else "context"]["relpath"]
        path = archive/relative
        frame = pd.read_parquet(path)
        frame["actual" if change == "actual" else "spot" if change == "context" else "method_cdf_low"] = 123.
        frame.to_parquet(path, index=False)
    with pytest.raises((ValueError, IntegrityError)):
        MIOStudyContract.verify_blinded_payloads(study, {}, {})


def test_blinded_cache_bad_permit_precedes_payload_reads(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    from dskit.pipeline.libs.cdf_experiment import AtomicFitStore
    study, _, _, _ = _blinded_cache_study(tmp_path, monkeypatch)
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda s, p: p))
    monkeypatch.setattr(ExposureRegistry, "__init__", lambda self, spec: None)
    def refused(*args):
        raise ValueError("inactive claim")
    monkeypatch.setattr(ExposureRegistry, "verify", refused)
    monkeypatch.setattr(AtomicFitStore, "verify_pinned", lambda *a: pytest.fail("payload read before claim"))
    with pytest.raises(ValueError, match="inactive claim"):
        MIOStudyContract.verify_blinded_payloads(study, {}, {})

@pytest.mark.parametrize("fault", ["cdf", "forecast_outcome", "component_outcome", "flag", "fold",
                                  "projection", "context_outcome", "missing_context"])
def test_blinded_cache_rehashed_invalid_payloads_refuse(tmp_path, monkeypatch, fault):
    """Valid hashes cannot excuse an invalid schema or fabricated context."""
    from index_options.mio_contract import MIOStudyContract
    from dskit.pipeline.libs.cdf_experiment import IntegrityError
    study, _, _, _ = _blinded_cache_study(tmp_path, monkeypatch, payload_fault=fault)
    MIOStudyContract.selection(study)
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda s, p: p))
    monkeypatch.setattr(ExposureRegistry, "__init__", lambda self, spec: None)
    monkeypatch.setattr(ExposureRegistry, "verify", lambda *a: None)
    with pytest.raises((ValueError, IntegrityError)):
        MIOStudyContract.verify_blinded_payloads(study, {}, {})


def test_blinded_gate_is_shared_by_intake_and_authenticated_facade(study, tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path/"cache"))
    registry, plan, permit = _open(study)
    calls = []
    def verified(config, current, authority):
        assert registry.verify(current, authority)["status"] == "claimed_exposed"
        calls.append(authority["id"])
    monkeypatch.setattr(MIOStudyContract, "verify_blinded_payloads", staticmethod(verified))
    ctx = NodeContext(name="intake", asof="2019-02-09", run_dir=str(tmp_path/"run"))
    outputs = MIOPacketInputs("intake", {"study": study}).run(ctx, {"permit": permit})
    assert calls == [permit["id"]]
    MIOPacketStudy.validate_authenticated_intake(
        study, plan, permit, outputs["provenance"], outputs["bounded_inputs"],
        str(tmp_path/"run/artifacts/mio-quotes"))
    assert calls == [permit["id"], permit["id"]]
    bad = copy.deepcopy(outputs["provenance"])
    bad["inputs_sha256"] = "0"*64
    with pytest.raises(ValueError, match="unverified bounded intake"):
        MIOPacketStudy.validate_authenticated_intake(
            study, plan, permit, bad, outputs["bounded_inputs"], None)
    assert len(calls) == 2


def test_blinded_selection_never_reads_mixed_state_records(tmp_path, monkeypatch):
    import index_options.mio_contract as contract
    study, archive, receipt, _ = _blinded_cache_study(tmp_path, monkeypatch)
    forbidden = {receipt["metadata"][original]["file"] for original in receipt["projections"]}
    forbidden |= {item["file"] for original, item in receipt["metadata"].items()
                  if original.endswith("/epoch-state.json") or original.endswith("/blend/fit.json")}
    source_type = contract.PinnedPayloadSource
    original = source_type.file
    calls = []
    def traced(self, relative, *, verify=False):
        calls.append((relative, verify))
        assert not (relative in forbidden and verify), relative
        return original(self, relative, verify=verify)
    monkeypatch.setattr(source_type, "file", traced)
    original_open = Path.open
    protected_paths = {archive/relative for relative in forbidden}
    def no_state_open(path, *args, **kwargs):
        assert path not in protected_paths, str(path)
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_state_open)
    contract.MIOStudyContract.selection(study)
    assert calls and forbidden


@pytest.mark.parametrize("role", ["warmup", "fit", "epoch", "completed", "component", "blend"])
def test_blinded_false_structural_projection_refuses_after_claim(tmp_path, monkeypatch, role):
    import index_options.mio_contract as contract
    study, archive, receipt, save = _blinded_cache_study(tmp_path, monkeypatch)
    key = next(key for key, ref in receipt["projections"].items()
               if json.loads((archive/ref["file"]).read_text())["role"] == role)
    ref = receipt["projections"][key]
    projection = json.loads((archive/ref["file"]).read_text())
    # Keep the structural projection plausible and its independent wrapper pinned,
    # while disagreeing with the original immutable producer payload.
    if role == "fit":
        projection["value"]["band_counts"][0] += 1
    elif role == "epoch":
        projection["value"]["band_dates"][0]["quote_date"][0] = "2017-01-04"
    elif role == "warmup":
        projection["value"]["map_cutoff"] = "2018-12-31"
    elif role == "completed":
        projection["value"]["settings"]["epochs"] += 1
    elif role == "component":
        projection["value"]["rows"] += 1
    else:
        projection["value"]["params"]["source"] = "different-scratch"
    receipt["projections"][key] = save(ref["file"], projection)
    study["domain"]["predictive"]["receipt"].update(save("receipt.json", receipt))
    monkeypatch.setattr(StudyContract, "verify_plan", staticmethod(lambda s, p: p))
    monkeypatch.setattr(ExposureRegistry, "__init__", lambda self, spec: None)
    monkeypatch.setattr(ExposureRegistry, "verify", lambda *a: None)
    with pytest.raises(ValueError):
        contract.MIOStudyContract.verify_blinded_payloads(study, {}, {})


def test_blinded_structural_projection_rejects_learned_extra_fields(tmp_path, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    study, archive, receipt, save = _blinded_cache_study(tmp_path, monkeypatch)
    key = "/q3/fit/metadata.json"
    ref = receipt["projections"][key]
    projection = json.loads((archive/ref["file"]).read_text())
    projection["value"]["medians"] = [1., 2.]
    receipt["projections"][key] = save(ref["file"], projection)
    study["domain"]["predictive"]["receipt"].update(save("receipt.json", receipt))
    with pytest.raises(ValueError, match="structural projection"):
        MIOStudyContract.selection(study)


def test_settlement_tail_is_capped_at_the_protected_boundary_and_nothing_past_it_is_read(study, tmp_path, monkeypatch):
    """A tail that would reach protected data is capped at it (2023Q4: options expiring before 2024-01-01 only)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-home"))
    from index_options import mio_contract
    cap = "2019-02-05"
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", cap)  # only the owner's boundary is capped, not any date
    study["domain"]["params"]["bridge"]["protected_end_before"] = cap
    # the unchanged derived tail (2019-01-08 + 32 days = 2019-02-09) reaches past the cap
    _, plan, permit = _open(study)
    assert plan["runnable"]
    assert all(plan["read_bounds"][role]["end_before"] == cap for role in ("chain", "bars", "sessions"))
    ctx = NodeContext(name="intake", asof="2019-02-09", run_dir=str(tmp_path / "run"))
    intake = MIOPacketInputs("intake", {"study": study}).run(ctx, {"permit": permit})
    bounded = intake["bounded_inputs"]
    assert bounded["clocks"]["tail_end_before"] == cap
    for symbol in bounded["inputs"].values():
        assert all(row["date"] < cap for row in symbol["bars"])
        assert all(row["settlement_date"] < cap for row in symbol["contexts"] if "settlement_date" in row)
        assert symbol["chain_index"]["bounds"]["expiration"] == {"end_before": cap}
    out = MIOPacketStudy("study", {"study": study}).run(
        ctx, {"permit": permit, "bounded_inputs": bounded, "provenance": intake["provenance"]})
    assert out["results"][0]["status"] == "completed"
    assert out["audit"]["tail_capped_at"] == cap, "the cap shows in the audit"


def test_settlement_tail_equal_to_the_owner_cap_is_not_capped_and_other_boundaries_refuse(study, monkeypatch):
    from datetime import date, timedelta
    from index_options import mio_contract
    from index_options.mio_contract import MIOStudyContract

    end = MIOStudyContract.packet(study)["end_before"]
    tail = (date.fromisoformat(end) + timedelta(days=max(x["dte_max"] for x in study["domain"]["pairs"])
                                                + study["domain"]["tail_padding_days"])).isoformat()
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", tail)
    study["domain"]["params"]["bridge"]["protected_end_before"] = tail
    assert MIOStudyContract.settlement_tail(study) == (tail, False), "tail exactly equal to the cap: nothing was cut"
    one_less = (date.fromisoformat(tail) - timedelta(days=1)).isoformat()
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", one_less)
    study["domain"]["params"]["bridge"]["protected_end_before"] = one_less
    assert MIOStudyContract.settlement_tail(study) == (one_less, True)
    # a boundary that is not the owner cap is never capped, whatever its date
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", "2024-01-01")
    with pytest.raises(ValueError, match="reaches protected data"):
        MIOStudyContract.settlement_tail(study)


def test_policy_probe_maturity_is_checked_against_the_capped_tail(study, monkeypatch):
    from index_options import mio_contract
    from index_options.mio_contract import OptionPolicyStudyContract
    from index_options.rl_env import OptionStructuredEntryEnv

    packet = study["packets"][0]
    cap = "2019-02-05"  # the unchanged derived tail is 2019-02-09
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", cap)
    study["domain"]["params"]["bridge"]["protected_end_before"] = cap
    study["domain"]["policy"] = {
        "mode": "probe", "method_id": study["domain"]["params"]["methods"][0]["id"], "evaluation": None,
        "artifact": None, "baselines": [], "probe_receipt": None,
        "training": {
            "window": {"start": packet["start"], "end_before": packet["end_before"]},
            "maturity_end_before": cap, "env": "index_options.rl_env:OptionStructuredEntryEnv",
            "env_schema": OptionStructuredEntryEnv.PAYLOAD_SCHEMA, "feature_transform": "identity",
            "env_bounds": {"max_coordinates": 1, "max_quantity": 1, "max_features": 1,
                           "reward_scale": 1.0, "max_edits": 1}, "algo": "PPO", "algo_params": {"gamma": 1},
            "policy": "MlpPolicy", "seed": 7, "total_timesteps": 12}}
    OptionPolicyStudyContract()._validate_policy(study)  # labels maturing exactly at the cap pass
    study["domain"]["policy"]["training"]["maturity_end_before"] = "2019-02-06"
    with pytest.raises(ValueError, match="exceed selected packet read tail"):
        OptionPolicyStudyContract()._validate_policy(study)


def test_settlement_tail_refusals_notice_and_owner_cap(study, tmp_path, monkeypatch, caplog):
    """ADR-0297: the boundary is the document's protected_end_before; a 2023Q4-style tail under a later one refuses."""
    import logging
    from datetime import date, timedelta
    from index_options import mio_contract
    from index_options.mio_contract import MIOStudyContract

    tail, capped = MIOStudyContract.settlement_tail(study)
    assert capped is False and tail < study["domain"]["params"]["bridge"]["protected_end_before"]
    end = MIOStudyContract.packet(study)["end_before"]
    # a packet that itself reaches protected data
    study["domain"]["params"]["bridge"]["protected_end_before"] = "2019-01-07"
    assert end > "2019-01-07"
    with pytest.raises(ValueError, match="packet reaches protected data"):
        MIOStudyContract.settlement_tail(study)
    # the owner cap: the tail (packet end + dte + padding) passes the cap, the packet end does not
    uncapped = (date.fromisoformat(end) + timedelta(days=max(x["dte_max"] for x in study["domain"]["pairs"])
                                                   + study["domain"]["tail_padding_days"])).isoformat()
    monkeypatch.setattr(mio_contract, "OWNER_TAIL_CAP", end)
    study["domain"]["params"]["bridge"]["protected_end_before"] = uncapped
    with pytest.raises(ValueError, match="needs protected_end_before no later than"):
        MIOStudyContract.settlement_tail(study)
    study["domain"]["params"]["bridge"]["protected_end_before"] = end
    assert MIOStudyContract.settlement_tail(study) == (end, True)
    with caplog.at_level(logging.INFO, logger="index_options.mio_contract"):
        MIOStudyContract().resolve(study)
    notices = [r.getMessage() for r in caplog.records if "tail capped" in r.getMessage()]
    assert len(notices) == 1 and end in notices[0] and not any(c in notices[0] for c in "$"), notices


# --- owner_selected_fixed_forecasts: hash-pinned fixed_loc forecasts (ADR-0297) ------------------------------------

FIXED_POLICY = "owner_selected_fixed_forecasts"
FIXED_COMMIT = "0123456789abcdef0123456789abcdef01234567"
FIXED_BASIS = "owner_approved_fixed_loc_after_score_ranked_zoo_and_hpo"
FIXED_LABELS = ["zm3", "zm2.5", "zm2", "zm1.5", "zm1", "zm0.5", "zp0", "zp0.5", "zp1", "zp1.5", "zp2", "zp2.5", "zp3"]
#: The builder's second grid (ForecastGrid.finer): every 0.25 z over the same span, labelled by its zlabel rule.
FINE_Z = [-3.+.25*i for i in range(25)]
FINE_LABELS = ["z"+("m" if z < 0 else "p")+format(abs(z), "g") for z in FINE_Z]
FINE_FILE = "forecasts-fine.parquet"
FIXED_RECIPE = {"kind": "fixed_loc", "learning_rate": 3e-4, "dropout": .1, "weight_decay": .1, "hidden": 64,
                "width": 16, "epochs": 1, "seeds": [11], "average": "quantile_average"}
#: The builder's quarter table (quarterly_forecasts.Quarter): the first decision of each fixture packet.
FIXED_QUARTERS = {"2019Q4": (29, "2019-10-01", "2020-01-01", "2019-10-08"),
                  "2020Q1": (2, "2020-01-01", "2020-04-01", "2020-01-08"),
                  "2020Q2": (3, "2020-04-01", "2020-07-01", "2020-04-08"),
                  "2023Q4": (17, "2023-10-01", "2024-01-01", "2023-11-29")}


def _move_dates(node, days):
    """Return ``node`` with every ISO date string moved ``days`` later (the fixture keeps its weekdays)."""
    from datetime import date, timedelta
    import re

    if isinstance(node, dict):
        return {k: _move_dates(v, days) for k, v in node.items()}
    if isinstance(node, list):
        return [_move_dates(v, days) for v in node]
    if isinstance(node, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", node):
        return (date.fromisoformat(node)+timedelta(days=days)).isoformat()
    return node


def _lock_file(path, symbols):
    import hashlib

    wire = json.dumps(list(symbols), separators=(",", ":")).encode()
    path.write_text(json.dumps({"symbols": list(symbols), "count": len(symbols), "sha256": hashlib.sha256(wire).hexdigest()}))
    return path


def _pin(root, registry, source, folder, **config):
    from dskit.onboarding.artifacts import payload_files

    vid = registry.register("source_config", {
        "name": source, "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(folder), "as_of": "2026-01-01T00:00:00+00:00", **config}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, source, "files", "backfill")
    acquired = payload_files(root, source, "files")
    return {"root": root.root, "source": source, "stream": "files", "snapshot": acquired["snapshot"],
            "manifest_sha256": acquired["manifest_sha256"], "files": acquired["sha256"]}


def fixed_world(tmp_path, monkeypatch, quarter="2020Q1", *, seeds=(11,), names=("X",), missing=(), receipt=None,
                forecasts=None, contexts=None, neighbour_receipt=None, fine=False, fine_forecasts=None):
    """Write synthetic forecast files in exactly the builder's contract and a study bound to them.

    Dates come from the weekly fixture moved into ``quarter``; rows that would settle in 2024 get no forecast
    (owner ruling). ``receipt``, ``forecasts`` and ``contexts`` edit the written quarter's receipt dict and row
    lists before pinning. ``fine`` also writes each quarter's ``forecasts-fine.parquet`` (25 points) and its receipt record; ``fine_forecasts`` edits the written quarter's fine rows. Returns the study, the ``mio-bindings`` dict and the forecast folder.
    """
    from datetime import date
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from index_options import ticker_lock

    tmp_path.mkdir(parents=True, exist_ok=True)
    fold, start, end_before, decision = FIXED_QUARTERS[quarter]
    params, data = _packet_clock_fixture()
    days = (date.fromisoformat(decision)-date(2019, 1, 2)).days
    params, data = _move_dates(params, days), _move_dates(data, days)
    clocks, pairs = params.pop("clocks"), params.pop("selected_pairs")
    bridge = params["bridge"]
    bridge.update(fold=fold, protected_end_before="2024-01-01", grid_labels=FIXED_LABELS,
                  end_before=clocks["entry_end_before"], require_fitted=True)
    lock_path = _lock_file(tmp_path/"synthetic-lock.json", names)
    monkeypatch.setattr(ticker_lock, "DEFAULT_LOCK", lock_path)

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def table(rows, fold_number, edit):
        rows = [dict(r) for r in rows if r["expiry"] < "2024-01-01"]
        for row in rows:
            row.update(fold=fold_number)
            for old, new in zip(map(str, range(13)), FIXED_LABELS):
                if "method_cdf_"+old in row:
                    row["method_cdf_"+new], row["fitted"] = row.pop("method_cdf_"+old), True
        return edit(rows) if edit else rows

    root_dir = tmp_path/"forecast-root"
    recipe = {**FIXED_RECIPE, "seeds": list(seeds)}
    identity = value_hash(recipe)
    grid = bridge["z_grid"]

    def write_quarter(name, number, first, last, edit_receipt, edit_forecasts, edit_contexts):
        folder = root_dir/name
        folder.mkdir(parents=True)
        fs, cs = table(data["X"]["forecasts"], number, edit_forecasts), table(data["X"]["contexts"], number, edit_contexts)
        for rows, leaf in ((fs, "forecasts.parquet"), (cs, "contexts.parquet")):
            pd.DataFrame(rows).to_parquet(folder/leaf, index=False, compression="zstd")
        if fine:
            import math
            wide = [{**{k: r[k] for k in ("symbol", "quote_date", "expiry", "fold", "band", "fitted")},
                     **{"method_cdf_"+label: .5*(1+math.erf(z/2**.5)) for label, z in zip(FINE_LABELS, FINE_Z)}}
                    for r in fs]
            wide = fine_forecasts(wide) if fine_forecasts and folder.name == quarter else wide
            pd.DataFrame(wide).to_parquet(folder/FINE_FILE, index=False, compression="zstd")
        body = {"schema": "quarterly-forecast/v1", "quarter": name, "bridge_fold": number,
                "forecast_window": {"start": first, "end_before": last}, "label_cutoff": first,
                "recipe": recipe, "recipe_sha256": identity, "fit_document_sha256": "a"*64, "panel_sha256": "b"*64,
                "fold_table_sha256": "c"*64, "fit_manifest_sha256": "d"*64, "device": "cuda",
                "forecasts_sha256": sha(folder/"forecasts.parquet"), "contexts_sha256": sha(folder/"contexts.parquet"),
                "hardware": {"device": "cuda"}, "synthetic": False, "code_commit": FIXED_COMMIT, "ticker_lock_sha256": sha(lock_path),
                "owner_name_exceptions": {}, "coverage_note": "rows reaching the limit are absent",
                "seed_average_status": "open", "rows": len(fs),
                "expected_names": [n for n in names if n not in missing], "missing_names": list(missing),
                "evaluation_computed": False, "z_grid": grid, "grid_labels": FIXED_LABELS}
        if fine:
            body["fine_grid"] = {"file": FINE_FILE, "sha256": sha(folder/FINE_FILE), "z_grid": FINE_Z,
                                 "grid_labels": FINE_LABELS, "seed_average": recipe["average"], "scored": False}
        if edit_receipt:
            edit_receipt(body)
        (folder/"receipt.json").write_text(json.dumps(body, sort_keys=True, indent=1))

    write_quarter(quarter, fold, start, end_before, receipt, forecasts, contexts)
    other = next(q for q in ("2020Q1", "2020Q2") if q != quarter)
    write_quarter(other, FIXED_QUARTERS[other][0], *FIXED_QUARTERS[other][1:3], neighbour_receipt, None, None)
    market = tmp_path/"market"
    market.mkdir()
    for name in names:
        for role in ("chain", "bars"):
            rows = [{**row, "symbol": name} for row in data["X"][role]]
            pq.write_table(pa.Table.from_pylist(rows), market/f"{role}-{name}.parquet")
    pq.write_table(pa.table({"date": clocks["sessions"]}), market/"sessions.parquet")
    root = OnboardingRoot.create(str(tmp_path/"ob"))
    registry = root.registry()
    pin = _pin(root, registry, "synthetic-forecasts", root_dir, include=[
        "*/forecasts.parquet", "*/contexts.parquet", "*/receipt.json", *(["*/"+FINE_FILE] if fine else [])])
    held = _pin(root, registry, "synthetic-market", market)
    binding = {"forecasts": {"pin": pin, "files": {"shared": f"{quarter}/forecasts.parquet"}},
               "contexts": {"pin": pin, "files": {"shared": f"{quarter}/contexts.parquet"}},
               "receipt": {"source": pin, "file": f"{quarter}/receipt.json", "sha256": pin["files"][f"{quarter}/receipt.json"]}}
    sources = {"forecasts": copy.deepcopy(binding["forecasts"]), "contexts": copy.deepcopy(binding["contexts"]),
               "sessions": {"pin": copy.deepcopy(held), "files": {"shared": "sessions.parquet"}}}
    for role in ("chain", "bars"):
        sources[role] = {"pin": copy.deepcopy(held), "files": {name: f"{role}-{name}.parquet" for name in names}}
    params["symbols"] = list(names)
    pairs = [{**pairs[0], "id": name+"30", "symbol": name} for name in names]
    packet = {"id": "dev", "start": clocks["entry_start"], "end_before": clocks["entry_end_before"],
              "purpose": "development", "predecessor": None}
    study = {"contract": "index_options.mio_contract:MIOStudyContract",
             "registry": {"path": str(tmp_path/"exposure.sqlite"), "id": "6f6a1f0e-7a1b-4a57-9d3e-0c6b6c1f8f11",
                          "domain": "test:fixed-forecasts"},
             "packets": [packet], "selected_packet": "dev",
             "code": _implementation_pins(StudyContract, "index_options.mio_contract"),
             "dependencies": {"pytest": pytest.__version__},
             "domain": {"params": params, "pairs": pairs, "history_start": clocks["history_start"],
                        "tail_padding_days": 0, "sources": sources, "synthetic": False,
                        "predictive": {"policy": FIXED_POLICY, "receipt": copy.deepcopy(binding["receipt"]),
                                       "recipe_sha256": identity, "selection_basis": FIXED_BASIS},
                        "admission": {"mode": "debug_fixture_not_admitted", "evidence": None},
                        "report": {"primary_metric": None}}}
    return {"study": study, "binding": binding, "folder": root_dir/quarter, "recipe_sha256": identity,
            "fold": fold, "quarter": quarter}


def _select(study):
    from index_options.mio_contract import MIOStudyContract
    return MIOStudyContract.selection(study)


@pytest.mark.parametrize("seeds", [(11,), (11, 29, 47)], ids=["one-seed", "three-seeds"])
@pytest.mark.parametrize("quarter", ["2019Q4", "2020Q1", "2023Q4"])
def test_fixed_forecasts_pass_on_the_contract_as_written(tmp_path, monkeypatch, quarter, seeds):
    disclose = (lambda b: b.update(tuning_disclosure="The HPO tuned on 2019: favored, not independent.")) \
        if quarter == "2019Q4" else None
    world = fixed_world(tmp_path, monkeypatch, quarter, seeds=seeds, receipt=disclose)
    receipt = _select(world["study"])
    assert receipt["recipe_sha256"] == world["recipe_sha256"] and receipt["recipe"]["seeds"] == list(seeds)
    assert receipt["bridge_fold"] == FIXED_QUARTERS[quarter][0]
    assert StudyContract.build(world["study"])["runnable"]


def _value(path, value):
    def edit(body):
        node = body
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value
    return edit


def _drop(key):
    return lambda body: body.pop(key)


@pytest.mark.parametrize("edit, match", [
    (_value(["schema"], "quarterly-forecast/v2"), "schema"),
    (_drop("device"), "fields"),
    (_drop("ticker_lock_sha256"), "fields"),
    (_drop("code_commit"), "fields"),     # the real builder always writes it
    (_value(["code_commit"], None), "code_commit"),   # a synthetic or dirty-tree run has none
    (_value(["code_commit"], ""), "code_commit"),
    (_value(["code_commit"], "HEAD"), "code_commit"),
    (_value(["code_commit"], FIXED_COMMIT.upper()), "code_commit"),
    (_value(["code_commit"], 1234), "code_commit"),
    (_value(["synthetic"], True), "synthetic"),
    (_value(["forecasts_sha256"], "f"*64), "digests"),
    (_value(["contexts_sha256"], "f"*64), "digests"),
    (_value(["ticker_lock_sha256"], "f"*64), "ticker lock"),
    (_value(["owner_name_exceptions"], {"X": "a ruling"}), "exceptions"),
    (_value(["extra"], 1), "fields"),
    (_value(["bridge_fold"], 3), "must agree"),
    (_value(["bridge_fold"], 2.0), "must agree"),
    (_value(["quarter"], "2020Q2"), "must agree"),
    (_value(["quarter"], "2019Q3"), "outside the quarters"),
    (_value(["quarter"], "2019Q1"), "outside the quarters"),
    (_value(["quarter"], "2019Q4"), "tuning_disclosure"),  # 2019 needs its disclosure; then fold and window differ
    (_value(["tuning_disclosure"], "stray"), "tuning_disclosure"),  # a 2020Q1 receipt may not carry one
    (_value(["quarter"], "2024Q1"), "outside the quarters"),
    (_value(["forecast_window", "start"], "2020-01-09"), "must agree"),
    (_value(["forecast_window", "start"], "2019-12-31"), "must agree"),
    (_value(["forecast_window", "end_before"], "2020-03-31"), "must agree"),
    (_value(["label_cutoff"], "2020-01-02"), "cutoff"),
    (_value(["evaluation_computed"], True), "evaluation"),
    (_value(["evaluation_computed"], 0), "evaluation"),
    (_value(["z_grid"], [-3+.5*i for i in range(12)]+[3.5]), "grid"),
    (_value(["grid_labels"], FIXED_LABELS[::-1]), "grid"),
    (_value(["recipe_sha256"], "e"*64), "recipe"),
    (_value(["recipe", "kind"], "m53"), "recipe"),
    (_value(["recipe", "seeds"], []), "recipe"),
    (_value(["recipe", "seeds"], [11, 11]), "recipe"),
    (_value(["rows"], 99), "rows"),
    (_value(["expected_names"], []), "ticker"),
    (_value(["missing_names"], ["X"]), "ticker"),
], ids=lambda v: None if callable(v) else str(v)[:12])
def test_fixed_receipt_refusals(tmp_path, monkeypatch, edit, match):
    def self_consistent(body):
        edit(body)
        if body.get("recipe", {}) != FIXED_RECIPE and body.get("recipe_sha256") != "e"*64:
            # a changed recipe with its own hash is refused for what it says, not for a stale hash
            body["recipe_sha256"] = value_hash(body["recipe"])
    world = fixed_world(tmp_path, monkeypatch, receipt=self_consistent)
    with pytest.raises(ValueError, match=match):
        _select(world["study"])
    with pytest.raises(ValueError, match=match):
        StudyContract.build(world["study"])


@pytest.mark.parametrize("edit", [
    lambda body: body["recipe"].update(learning_rate=1e-3),   # the body changed, its recorded hash did not
    lambda body: body["recipe"].update(seeds=[11, 12]),
    lambda body: body.update(recipe_sha256=value_hash({**body["recipe"], "dropout": .5})),   # a hash of another body
], ids=["body-lr", "body-seeds", "hash-of-another-body"])
def test_the_recipe_body_must_hash_to_the_documents_pin(tmp_path, monkeypatch, edit):
    """`_fixed_recipe` checks the receipt's recorded hash AND the hash of the recipe body it carries."""
    world = fixed_world(tmp_path, monkeypatch, receipt=edit)
    with pytest.raises(ValueError, match="recipe"):
        _select(world["study"])


def _builder_receipt_keys(source):
    """The key set of the ``receipt = {...}`` literal in the forecast builder's source, and its optional keys."""
    import ast
    tree = ast.parse(source)
    keys, optional = set(), set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "receipt" for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            keys |= {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        if (isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript) and getattr(t.value, "id", None) == "receipt"
                                                 for t in node.targets)):
            optional.add(node.targets[0].slice.value)
    return keys, optional


def test_the_accepted_receipt_keys_are_exactly_the_builders():
    """Regression: the builder writes ``code_commit`` and the contract once refused every real receipt for it.

    The builder is a sibling branch until it merges: read its source from ``index_options.quarterly_forecasts``
    when present, else from the path in MIO_FORECAST_BUILDER_SOURCE; otherwise this check cannot run.
    """
    import importlib.util
    import os
    from index_options.mio_contract import MIOStudyContract
    spec = importlib.util.find_spec("index_options.quarterly_forecasts")
    path = spec.origin if spec else os.environ.get("MIO_FORECAST_BUILDER_SOURCE")
    if not path or not os.path.exists(path):
        pytest.skip("the forecast builder source is not available")
    keys, optional = _builder_receipt_keys(open(path).read())
    assert keys and "code_commit" in keys, "the receipt literal was not found"
    assert keys == MIOStudyContract._FIXED_RECEIPT_KEYS
    assert optional <= MIOStudyContract._FIXED_OPTIONAL_KEYS


def test_the_synthetic_builder_receipt_literal_carries_the_keys_the_fixture_writes(tmp_path, monkeypatch):
    """The fixture is the builder's contract: its receipt has every required key, code_commit included."""
    from index_options.mio_contract import MIOStudyContract
    world = fixed_world(tmp_path, monkeypatch)
    written = json.loads((world["folder"]/"receipt.json").read_text())
    assert set(written) == MIOStudyContract._FIXED_RECEIPT_KEYS and written["code_commit"] == FIXED_COMMIT


@pytest.mark.parametrize("edit", [
    lambda r: r.update(note="harmless extra key"),
    lambda r: r.update(seeds=[29]),
    lambda r: r.update(learning_rate=1e-3),
], ids=["extra-key", "other-seed", "other-rate"])
def test_a_receipt_recipe_edited_without_its_hash_is_refused(tmp_path, monkeypatch, edit):
    """The recipe's own bytes must hash to the pin: a recipe that still passes the kind and seed checks, kept under the
    right ``recipe_sha256``, differs from the document's recipe and is refused (a pin-only check would pass it)."""
    def edited(body):
        edit(body["recipe"])
    world = fixed_world(tmp_path, monkeypatch, receipt=edited)
    with pytest.raises(ValueError, match="recipe"):
        _select(world["study"])


@pytest.mark.parametrize("edit, match", [
    (lambda b: b.pop("tuning_disclosure"), "tuning_disclosure"),
    (lambda b: b.update(tuning_disclosure=""), "tuning_disclosure"),
    (lambda b: b.update(tuning_disclosure=["tuned"]), "tuning_disclosure"),
    (lambda b: b.update(bridge_fold=26), "must agree"),  # the builder's FOLD_2019Q1: not Q4's 29
    (lambda b: b.update(bridge_fold=5), "must agree"),
], ids=["missing", "empty", "not-text", "wrong-fold-26", "wrong-fold-5"])
def test_a_2019q4_receipt_needs_its_tuning_disclosure_and_fold_29(tmp_path, monkeypatch, edit, match):
    """The HPO tuned on 2019: the builder's receipt says so, the contract refuses one that does not."""
    def tuned(body):
        body["tuning_disclosure"] = "The HPO tuned on 2019: favored, not independent."
        edit(body)
    world = fixed_world(tmp_path, monkeypatch, "2019Q4", receipt=tuned)
    with pytest.raises(ValueError, match=match):
        _select(world["study"])


@pytest.mark.parametrize("edit", [
    lambda r: r.update(kind="m53"),
    lambda r: r.update(seeds=[]),
    lambda r: r.update(seeds=[11, 11]),
    lambda r: r.update(seeds=[11.0]),
    lambda r: r.update(average="median"),
])
def test_a_recipe_the_document_also_pins_must_still_be_a_fixed_loc_recipe(tmp_path, monkeypatch, edit):
    recipe = copy.deepcopy(FIXED_RECIPE)
    edit(recipe)

    def bad(body):
        body.update(recipe=recipe, recipe_sha256=value_hash(recipe))
    study = fixed_world(tmp_path, monkeypatch, receipt=bad)["study"]
    study["domain"]["predictive"]["recipe_sha256"] = value_hash(recipe)
    with pytest.raises(ValueError, match="recipe"):
        _select(study)


def _rows(column, value):
    def edit(rows):
        rows[0][column] = value
        return rows
    return edit


@pytest.mark.parametrize("role, edit, match", [
    ("forecasts", _rows("quote_date", "2024-01-02"), "2024-01-01"),
    ("forecasts", _rows("expiry", "2024-01-02"), "2024-01-01"),
    ("contexts", _rows("settlement_date", "2024-01-02"), "2024-01-01"),
    ("contexts", _rows("expiry", "2024-01-02"), "2024-01-01"),
    # the cap day itself is already protected: ``>= OWNER_TAIL_CAP``, not ``>``
    ("forecasts", _rows("quote_date", "2024-01-01"), "at or past 2024-01-01"),
    ("forecasts", _rows("expiry", "2024-01-01"), "at or past 2024-01-01"),
    ("contexts", _rows("settlement_date", "2024-01-01"), "at or past 2024-01-01"),
    ("contexts", _rows("expiry", "2024-01-01"), "at or past 2024-01-01"),
    ("forecasts", _rows("quote_date", "2020-04-01"), "window"),
    ("forecasts", _rows("quote_date", "2019-12-31"), "window"),
    ("forecasts", _rows("fold", 3), "fold"),
    ("contexts", _rows("fold", 3), "fold"),
    ("forecasts", _rows("band", "fit"), "band"),
    ("forecasts", _rows("fitted", False), "fitted"),
    ("forecasts", _rows("expiry", "2020-2-7"), "canonical"),
    ("forecasts", _rows("symbol", "Q"), "ticker"),
    ("forecasts", lambda rows: rows[:-1], "pairing|rows differ"),
    ("contexts", lambda rows: rows[:-1], "rows differ"),
    ("contexts", _rows("expiry", "2020-02-09"), "pairing"),
    ("contexts", _rows("settlement_date", None), "null"),
    ("forecasts", lambda rows: [{k: v for k, v in r.items() if k != "method_cdf_zp3"} for r in rows], "columns"),
    ("contexts", lambda rows: [{**r, "y": .1} for r in rows], "columns"),
], ids=lambda v: None if callable(v) else str(v)[:12])
def test_fixed_row_refusals(tmp_path, monkeypatch, role, edit, match):
    world = fixed_world(tmp_path, monkeypatch, **{role: edit})
    with pytest.raises(ValueError, match=match):
        _select(world["study"])


def test_fixed_rows_repeated_in_both_files_do_not_pair(tmp_path, monkeypatch):
    twice = lambda rows: rows+[rows[0]]  # noqa: E731
    with pytest.raises(ValueError, match="pairing"):
        _select(fixed_world(tmp_path, monkeypatch, forecasts=twice, contexts=twice)["study"])


def test_a_consistently_wrong_fold_for_the_quarter_is_refused(tmp_path, monkeypatch):
    """Receipt, rows and document all say fold 3: the quarter's own table still says 2."""
    three = lambda rows: [{**r, "fold": 3} for r in rows]  # noqa: E731
    study = fixed_world(tmp_path, monkeypatch, receipt=_value(["bridge_fold"], 3), forecasts=three, contexts=three)["study"]
    study["domain"]["params"]["bridge"]["fold"] = 3
    with pytest.raises(ValueError, match="must agree"):
        _select(study)


def test_a_receipt_body_that_differs_from_its_own_recipe_hash_is_refused(tmp_path, monkeypatch):
    study = fixed_world(tmp_path, monkeypatch, receipt=_value(["recipe", "epochs"], 2))["study"]
    with pytest.raises(ValueError, match="recipe"):
        _select(study)


def test_a_receipt_filed_under_another_quarter_is_refused(tmp_path, monkeypatch):
    """Its body says 2020Q1 and every other check holds; it sits in the 2020Q2 folder."""
    moved = lambda b: b.update(quarter="2020Q1", bridge_fold=2, label_cutoff="2020-01-01",  # noqa: E731
                               forecast_window={"start": "2020-01-01", "end_before": "2020-04-01"})
    world = fixed_world(tmp_path, monkeypatch, neighbour_receipt=moved)
    pin = world["binding"]["receipt"]["source"]
    world["study"]["domain"]["predictive"]["receipt"].update(
        file="2020Q2/receipt.json", sha256=pin["files"]["2020Q2/receipt.json"])
    with pytest.raises(ValueError, match="another quarter"):
        _select(world["study"])


def test_a_receipt_recording_a_second_grid_still_serves_its_13_point_document(tmp_path, monkeypatch):
    """The builder may record ``fine_grid``; this policy reads only ``forecasts.parquet`` until a fine route exists."""
    record = {"file": "forecasts-fine.parquet", "sha256": "f"*64, "z_grid": [], "grid_labels": [],
              "seed_average": "quantile_average", "scored": False}
    assert _select(fixed_world(tmp_path, monkeypatch, receipt=lambda b: b.update(fine_grid=record))["study"])


def test_fixed_date_columns_must_be_the_contract_strings(tmp_path, monkeypatch):
    from datetime import date as day

    def typed(rows):
        for row in rows:
            row["quote_date"] = day.fromisoformat(row["quote_date"])
        return rows
    with pytest.raises(ValueError, match="canonical"):
        _select(fixed_world(tmp_path, monkeypatch, forecasts=typed)["study"])


@pytest.mark.parametrize("edit, match", [
    (lambda s: s["domain"]["params"]["bridge"].update(fold=3), "fold"),
    (lambda s: s["domain"]["params"]["bridge"].update(fold=2.0), "fold"),
    (lambda s: s["domain"]["params"]["bridge"].update(protected_end_before="2020-03-31"), "protected"),
    (lambda s: s["domain"]["params"]["bridge"].update(grid_labels=FIXED_LABELS[::-1]), "grid"),
    (lambda s: s["domain"]["params"]["bridge"].update(z_grid=[-2.5+.5*i for i in range(13)]), "grid"),
    (lambda s: s["domain"]["predictive"].update(recipe_sha256="e"*64), "recipe"),
    (lambda s: s["domain"]["predictive"].pop("recipe_sha256"), "fixed-forecast selection"),
    (lambda s: s["domain"]["predictive"].update(selection_basis="owner_selected_no_score_ranking"), "fixed-forecast selection"),
    (lambda s: s["domain"]["predictive"].update(saved_method={}), "fixed-forecast selection"),
    (lambda s: s["packets"][0].update(start="2019-12-31"), "window"),
    (lambda s: s["packets"][0].update(end_before="2020-04-02"), "window"),
    (lambda s: s["packets"][0].update(purpose="holdout"), "purpose"),
    (lambda s: s["domain"]["params"].update(symbols=["X", "Y"]), "ticker"),
])
def test_fixed_document_refusals(tmp_path, monkeypatch, edit, match):
    study = fixed_world(tmp_path, monkeypatch)["study"]
    edit(study)
    with pytest.raises(ValueError, match=match):
        _select(study)


def test_fixed_pins_are_verified_and_bound_to_the_receipts_quarter(tmp_path, monkeypatch):
    world = fixed_world(tmp_path, monkeypatch)
    study, q = world["study"], world["quarter"]
    source = PinnedPayloadSource(study["domain"]["sources"]["forecasts"]["pin"])

    def attempt(edit, match):
        changed = copy.deepcopy(study)
        edit(changed)
        with pytest.raises(ValueError, match=match):
            _select(changed)

    def repin_file(s, relative, digest):
        for pin in (s["domain"]["sources"]["forecasts"]["pin"], s["domain"]["sources"]["contexts"]["pin"],
                    s["domain"]["predictive"]["receipt"]["source"]):
            pin["files"][relative] = digest

    attempt(lambda s: repin_file(s, f"{q}/forecasts.parquet", "f"*64), "pin absent from manifest")
    attempt(lambda s: s["domain"]["sources"]["contexts"]["pin"]["files"].update({f"{q}/contexts.parquet": "f"*64}),
            "share one pinned manifest")
    attempt(lambda s: s["domain"]["predictive"]["receipt"].update(sha256="f"*64), "digest differs")
    attempt(lambda s: s["domain"]["sources"]["forecasts"]["files"].update(shared="2020Q2/forecasts.parquet"), "quarter")
    attempt(lambda s: s["domain"]["sources"]["contexts"]["files"].update(shared="2020Q2/contexts.parquet"), "quarter")
    attempt(lambda s: s["domain"]["predictive"]["receipt"].update(
        file="2020Q2/receipt.json", sha256=source.pin["files"]["2020Q2/receipt.json"]), "fold")
    for leaf in ("forecasts.parquet", "contexts.parquet", "receipt.json"):
        path = Path(source.file(f"{q}/{leaf}"))
        original = path.read_bytes()
        path.unlink()
        path.write_bytes(original+b"\0")  # never write through a hard link
        with pytest.raises(ValueError, match="drift"):
            _select(study)
        path.unlink()
        path.write_bytes(original)
    assert _select(study)["quarter"] == q, "restored bytes pass"


def test_fixed_ticker_list_equals_the_lock_and_missing_names_partition_it(tmp_path, monkeypatch):
    names = ("X", "Y")
    world = fixed_world(tmp_path/"ok", monkeypatch, names=names, missing=("Y",))
    assert _select(world["study"])["missing_names"] == ["Y"], "a missing name has no rows and stays on the list"
    for tag, kwargs, match in [("silent", {"receipt": _value(["missing_names"], [])}, "ticker"),
                               ("both", {"receipt": _value(["expected_names"], ["X", "Y"])}, "ticker"),
                               ("repeat", {"receipt": _value(["missing_names"], ["Y", "Y"])}, "ticker"),
                               ("outsider", {"receipt": _value(["missing_names"], ["Z"])}, "ticker")]:
        study = fixed_world(tmp_path/tag, monkeypatch, names=names, missing=("Y",), **kwargs)["study"]
        with pytest.raises(ValueError, match=match):
            _select(study)
    # an expected name with no rows is a silent cut; so is a document on part of the list
    study = fixed_world(tmp_path/"no-rows", monkeypatch, names=names)["study"]
    with pytest.raises(ValueError, match="ticker"):
        _select(study)
    study = fixed_world(tmp_path/"part", monkeypatch, names=names, missing=("Y",))["study"]
    study["domain"]["params"]["symbols"] = ["X"]
    with pytest.raises(ValueError, match="ticker"):
        _select(study)


def test_fixed_selection_reads_the_real_locked_list(tmp_path, monkeypatch):
    """The lock is never synthetic in production: the real 166-name file passes, and one name short refuses."""
    from index_options import ticker_lock

    real = ticker_lock.TickerLock.load(ticker_lock.DEFAULT_LOCK).symbols
    assert len(real) == 166
    default = ticker_lock.DEFAULT_LOCK
    first, last = real[0], real[-1]

    def rows_for_first(rows):
        return [{**r, "symbol": first} for r in rows]
    world = fixed_world(tmp_path, monkeypatch, names=("X",), forecasts=rows_for_first, contexts=rows_for_first,
                        receipt=lambda b: b.update(expected_names=[first], missing_names=list(real[1:]),
                                                   ticker_lock_sha256=hashlib.sha256(default.read_bytes()).hexdigest()))
    monkeypatch.setattr(ticker_lock, "DEFAULT_LOCK", default)
    study = world["study"]
    study["domain"]["params"]["symbols"] = list(real)
    assert _select(study)["expected_names"] == [first]
    study["domain"]["params"]["symbols"] = list(real[:-1])
    with pytest.raises(ValueError, match="ticker"):
        _select(study)
    assert last not in study["domain"]["params"]["symbols"]


def test_existing_policies_refuse_a_fixed_forecast_receipt_exactly_as_before(tmp_path, monkeypatch):
    """A fixed-forecast receipt is no back door into the other policies, and they keep their own refusals."""
    from index_options.mio_contract import MIOStudyContract, OptionPolicyStudyContract

    world = fixed_world(tmp_path, monkeypatch)
    ref = world["study"]["domain"]["predictive"]["receipt"]
    for policy, message in [("owner_selected_pinned_forecasts", "invalid pinned forecast receipt"),
                            ("owner_selected_saved_zoo_outputs", "invalid owner-selected development receipt"),
                            ("owner_selected_blinded_cache", "explicit blinded producer authority required")]:
        study = copy.deepcopy(world["study"])
        study["domain"]["predictive"] = {"policy": policy, "receipt": ref}
        with pytest.raises(ValueError, match=message):
            MIOStudyContract.selection(study)
    study = copy.deepcopy(world["study"])
    study["domain"]["predictive"] = {"policy": "best_model_zoo_output", "receipt": ref}
    with pytest.raises(ValueError, match="bound model-zoo selection receipt required"):
        MIOStudyContract.selection(study)
    # the RL/policy contract keeps its 2019-only route: a fixed forecast is the plain selection, not that one
    policy_study = copy.deepcopy(world["study"])
    policy_study["domain"]["policy"] = {"mode": "training"}
    assert OptionPolicyStudyContract.selection(policy_study)["quarter"] == world["quarter"]
    # _stitched_sources' refusals and its success are untouched by sharing the binding rule
    source, _ = MIOStudyContract._receipt_source(world["study"], {"policy": "x", "receipt": ref})
    sources = world["study"]["domain"]["sources"]
    good = {"forecast": {"relpath": sources["forecasts"]["files"]["shared"],
                         "sha256": sources["forecasts"]["pin"]["files"][sources["forecasts"]["files"]["shared"]]},
            "context": {"relpath": sources["contexts"]["files"]["shared"],
                        "sha256": sources["contexts"]["pin"]["files"][sources["contexts"]["files"]["shared"]]}}
    assert MIOStudyContract._stitched_sources(world["study"], source, {"stitched": good}) is None
    for stitched, message in [(None, "pins required"), ({"forecast": good["forecast"]}, "pins required"),
                              ({**good, "context": None}, "does not match"),
                              ({**good, "context": {**good["context"], "sha256": "0"*64}}, "does not match"),
                              ({**good, "context": {**good["context"], "extra": 1}}, "does not match")]:
        with pytest.raises(ValueError, match=message):
            MIOStudyContract._stitched_sources(world["study"], source, {"stitched": stitched})
    with pytest.raises(KeyError):
        MIOStudyContract._stitched_sources(world["study"], source, {})



# --- the 25-point grid: forecasts-fine.parquet bound by its own route (ADR-0297) --------------------------------------

def fine_study(world, quarter=None, file=FINE_FILE):
    """Return the world's study re-bound as the builder's ``mio-bindings-*-fine`` binding says: the 25-point file and grid."""
    study = copy.deepcopy(world["study"])
    study["domain"]["sources"]["forecasts"]["files"]["shared"] = f"{quarter or world['quarter']}/{file}"
    bridge = study["domain"]["params"]["bridge"]
    bridge["z_grid"], bridge["grid_labels"] = list(FINE_Z), list(FINE_LABELS)
    return study


@pytest.mark.parametrize("quarter", ["2020Q1", "2023Q4"])
def test_the_fine_file_binds_through_its_own_route_with_its_own_hash(tmp_path, monkeypatch, quarter):
    world = fixed_world(tmp_path, monkeypatch, quarter, fine=True)
    receipt = _select(fine_study(world))
    pinned = world["study"]["domain"]["sources"]["forecasts"]["pin"]["files"]
    assert receipt["fine_grid"]["sha256"] == pinned[f"{quarter}/{FINE_FILE}"]
    assert StudyContract.build(fine_study(world))["runnable"]
    assert _select(world["study"]), "the 13-point document of the same snapshot is unchanged"


def _coarse_grid(study):
    bridge = study["domain"]["params"]["bridge"]
    bridge["z_grid"], bridge["grid_labels"] = [-3+.5*i for i in range(13)], list(FIXED_LABELS)


@pytest.mark.parametrize("edit, match", [
    # the fine file under the 13-point grid, and the 13-point file under the fine grid
    (lambda s: (_coarse_grid(s), s["domain"]["sources"]["forecasts"]["files"].update(shared="2020Q1/"+FINE_FILE)),
     "another quarter"),
    (lambda s: s["domain"]["sources"]["forecasts"]["files"].update(shared="2020Q1/forecasts.parquet"), "another quarter"),
    (lambda s: s["domain"]["sources"]["forecasts"]["files"].update(shared="2020Q2/"+FINE_FILE), "another quarter"),
    (lambda s: s["domain"]["params"]["bridge"].update(z_grid=FINE_Z[:-1]+[3.25]), "grid"),
    (lambda s: s["domain"]["params"]["bridge"].update(grid_labels=FINE_LABELS[::-1]), "grid"),
], ids=["coarse-grid-fine-file", "fine-grid-coarse-file", "other-quarter-fine-file", "z-grid-off", "labels-reversed"])
def test_a_fine_binding_that_does_not_match_its_file_and_grid_is_refused(tmp_path, monkeypatch, edit, match):
    world = fixed_world(tmp_path, monkeypatch, fine=True)
    study = fine_study(world)
    edit(study)
    with pytest.raises(ValueError, match=match):
        _select(study)


@pytest.mark.parametrize("edit, match", [
    (lambda b: b["fine_grid"].update(sha256="e"*64), "digest"),
    (lambda b: b["fine_grid"].update(scored=True), "fine"),
    (lambda b: b["fine_grid"].update(seed_average="linear_pool"), "fine"),
    (lambda b: b["fine_grid"].update(file="forecasts-other.parquet"), "fine"),
    (lambda b: b["fine_grid"].update(extra=1), "fine"),
    (lambda b: b["fine_grid"].update(z_grid=[z for z in FINE_Z if z != 0.]), "grid"),
    (lambda b: b["fine_grid"].update(grid_labels=FINE_LABELS[:-1]), "grid"),
    (lambda b: b["fine_grid"].pop("scored"), "fine"),
    (lambda b: b.pop("fine_grid"), "grid"),
    (lambda b: b.update(forecasts_sha256="e"*64), "digest"),
], ids=["sha", "scored", "average", "file", "extra-key", "dropped-point", "labels", "no-scored", "no-record", "coarse-sha"])
def test_a_fine_receipt_record_that_does_not_describe_the_file_is_refused(tmp_path, monkeypatch, edit, match):
    world = fixed_world(tmp_path, monkeypatch, fine=True, receipt=edit)
    with pytest.raises(ValueError, match=match):
        _select(fine_study(world))


def _relabel(points):
    return ["z"+("m" if z < 0 else "p")+format(abs(z), "g") for z in points]


@pytest.mark.parametrize("points, labels", [
    ([-3.25]+FINE_Z[1:], None),                                    # starts before the scored span
    (FINE_Z[:-1]+[3.25], None),                                    # ends after it
    ([z for z in FINE_Z if z != 0.], None),                        # a scored point (0) is missing
    ([FINE_Z[0], FINE_Z[2], FINE_Z[1]]+FINE_Z[3:], None),          # not strictly increasing
    (FINE_Z, FINE_LABELS[:-1]+[FINE_LABELS[0]]),                   # a repeated label
    (FINE_Z, FINE_LABELS[:-1]),                                    # one label short
    ([FINE_Z[0]], None),                                           # fewer than two points
], ids=["span-start", "span-end", "scored-point-missing", "unordered", "repeated-label", "short-labels", "one-point"])
def test_a_fine_grid_that_is_not_a_finer_grid_over_the_scored_span_is_refused(tmp_path, monkeypatch, points, labels):
    """The record and the document agree with each other, so only the shape of the grid itself is wrong."""
    labels = labels or _relabel(points)

    def record(body):
        body["fine_grid"].update(z_grid=points, grid_labels=labels)
    world = fixed_world(tmp_path, monkeypatch, fine=True, receipt=record)
    study = fine_study(world)
    study["domain"]["params"]["bridge"].update(z_grid=list(points), grid_labels=list(labels))
    with pytest.raises(ValueError, match="does not describe a finer grid"):
        _select(study)


@pytest.mark.parametrize("rows, match", [
    (lambda rows: [{k: v for k, v in r.items() if k != "method_cdf_"+FINE_LABELS[3]} for r in rows], "columns"),
    (lambda rows: [{**r, "extra": 1.} for r in rows], "columns"),
    (lambda rows: rows[:1] + rows[:1], "rows|pairing"),
    (lambda rows: [{**r, "fitted": False} for r in rows], "fitted"),
    (lambda rows: [{**r, "fold": 9} for r in rows], "fold"),
    (lambda rows: [{**r, "expiry": "2024-01-03"} for r in rows], "2024-01-01"),
], ids=["column-gone", "column-added", "rows-differ", "unfitted", "fold", "2024-row"])
def test_an_honestly_pinned_fine_file_with_the_wrong_structure_is_refused(tmp_path, monkeypatch, rows, match):
    world = fixed_world(tmp_path, monkeypatch, fine=True, fine_forecasts=rows)
    with pytest.raises(ValueError, match=match):
        _select(fine_study(world))


def test_a_fine_file_changed_after_it_was_pinned_is_refused(tmp_path, monkeypatch):
    import pandas as pd

    world = fixed_world(tmp_path, monkeypatch, fine=True)
    pin = world["study"]["domain"]["sources"]["forecasts"]["pin"]
    path = PinnedPayloadSource(pin).file(f"{world['quarter']}/{FINE_FILE}")      # the acquired snapshot's own copy
    frame = pd.read_parquet(path)
    frame.loc[:, "method_cdf_"+FINE_LABELS[3]] = 0.
    frame.to_parquet(path, index=False)
    with pytest.raises(ValueError):
        _select(fine_study(world))
    assert _select(world["study"]), "the untouched 13-point file still verifies"


def test_resolve_hands_the_settlement_tail_to_the_configuration_validator(study, monkeypatch):
    from index_options.mio_contract import MIOStudyContract
    from index_options.ledger_studies import WeeklyOptionPortfolioStudy as W

    seen, real = [], W.validate_configuration.__func__
    monkeypatch.setattr(W, "validate_configuration",
                        classmethod(lambda cls, p, **kw: (seen.append(kw), real(cls, p, **kw))[1]))
    MIOStudyContract().resolve(study)
    assert seen and seen[0]["tail_end_before"] == MIOStudyContract.settlement_tail(study)[0]

"""ADR-0313 stage I5: the calibrated edge inside the weekly MIO study (SYNTHETIC DATA ONLY).

Parity first (no block: nothing moves), then the ``edge_maps`` source role and its leakage rule, then the calibration
itself on a tiny synthetic world whose maps are written by the REAL walk child.
"""

import json

import pytest

from index_options import ledger_studies
from test_ledger_studies import FORMULATIONS, _timeless_digest, _weekly_portfolio_fixture

#: Keys a calibrated run adds to a record; a run without the block carries none of them anywhere.
CALIBRATION_WORDS = ("value_adjust", "expected_raw", "calibration_adjust", "radius_premium", "calibration",
                     "method_inputs", "edge_maps")


def test_no_block_means_no_calibration_key_in_any_program_or_record(monkeypatch):
    """P1: the nine formulations' programs and results carry no calibration word at all."""
    from dskit.pipeline.libs.pyomo import TransportPortfolioSolve

    for name, method in FORMULATIONS.items():
        params, inputs = _weekly_portfolio_fixture()
        params["methods"][0].update(method, id=name)
        if "shock" in method:
            params["methods"][0]["shock"] = {**method["shock"], "beta_lookback": 2}
        seen, run = [], TransportPortfolioSolve.run
        monkeypatch.setattr(TransportPortfolioSolve, "run",
                            lambda self, ctx, inp, run=run, seen=seen: (seen.append(inp), run(self, ctx, inp))[1])
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        result = study._method(params["methods"][0], None)
        monkeypatch.setattr(TransportPortfolioSolve, "run", run)
        assert seen and all("value_adjust" not in g for program in seen for g in program["groups"]), name
        text = json.dumps(result)
        assert not [w for w in CALIBRATION_WORDS if w in text], name
        assert _timeless_digest(result)


# --- stage I5.2: the edge_maps source role (contract, read bound, provenance, intake) ---------------------------------

def _publish(tmp_path, name, files):
    """Onboard ``files`` ({relative: bytes}) as a localblobs source; return the pin dict."""
    from dskit.onboarding import OnboardingRoot, run_acquisition
    from dskit.onboarding.artifacts import payload_files

    origin = tmp_path / f"{name}-origin"
    origin.mkdir()
    for relative, raw in files.items():
        (origin / relative).write_bytes(raw)
    root = OnboardingRoot.create(str(tmp_path / f"{name}-ob"))
    registry = root.registry()
    vid = registry.register("source_config", {
        "name": name, "catalog_source": "synthetic", "connector": "localblobs",
        "config": {"path": str(origin), "as_of": "2019-01-01T00:00:00+00:00"}}, origin="test")
    registry.transition(vid, "active", origin="test")
    run_acquisition(root, registry, name, "files", "backfill")
    got = payload_files(root, name, "files")
    return {"root": root.root, "source": name, "stream": "files", "snapshot": got["snapshot"],
            "manifest_sha256": got["manifest_sha256"], "files": got["sha256"]}


CAL = {"source": "edge_maps", "state": "expanding_fill", "edge_scale": "notional", "regressors": 1, "line": "bucket",
       "form": "slope", "variant": "point", "warmup": "no_trade", "min_warm_weeks": 8}


def _map(t, warm=True, w=0.5):
    """A minimal ``map-<t>.json`` body of the shape the walk writes (one bucket, one state)."""
    return {"t": t, "regimes": {}, "states": {"expanding_fill": {"t": t, "warm": warm, "pooled": {},
                                                                 "buckets": [{"w": w, "se_w": 0.1, "w_lcb": w - 0.1}],
                                                                 "names": {}}}}


def _calibrated_packet(study, tmp_path, maps, summary=None, bind=True, declare=True):
    """The ``study`` fixture with a calibration method and (when ``bind``) a bound ``edge_maps`` role."""
    import copy

    from index_options.edge_calibration import EdgeMapStream  # noqa: F401  (import check)

    study = copy.deepcopy(study)
    params = study["domain"]["params"]
    if declare:
        params["methods"] = [{**params["methods"][0], "id": "cal_arm", "calibration": dict(CAL)}]
        params["locked_method_ids"] = list(FORMULATIONS)
        params["bridge"]["protected_end_before"] = "2019-03-01"
    if bind:
        stream = (json.dumps({"maps": maps}, sort_keys=True) + "\n").encode()
        raw_summary = (json.dumps(summary or {"first": "2019-01-02"}, sort_keys=True) + "\n").encode()
        pin = _publish(tmp_path, "edge-maps", {"edge-maps.json": stream, "summary.json": raw_summary})
        study["domain"]["sources"]["edge_maps"] = {"pin": pin, "files": {"shared": "edge-maps.json",
                                                                          "summary": "summary.json"}}
    return study


from test_mio_packets import _open, study  # noqa: E402,F401  (the packet fixture and its opener)


def test_a_calibrated_method_needs_the_edge_maps_role_and_the_role_needs_a_calibrated_method(study, tmp_path):
    from dskit.pipeline.study_protocol import StudyContract

    with pytest.raises(ValueError, match="edge_maps_unbound"):
        StudyContract.build(_calibrated_packet(study, tmp_path, {}, bind=False))
    with pytest.raises(ValueError, match="edge_maps_unused"):
        StudyContract.build(_calibrated_packet(study, tmp_path, {}, declare=False))


def test_the_plan_pins_both_files_and_bounds_the_maps_to_the_entry_window(study, tmp_path):
    packet = _calibrated_packet(study, tmp_path, {"2019-01-02": _map("2019-01-02")})
    _, plan, _ = _open(packet)
    assert plan["runnable"] and set(plan["source_pins"]["edge_maps"]["files"]) == {"shared", "summary"}
    assert plan["read_bounds"]["edge_maps"] == {"start": "2019-01-02", "end_before": "2019-01-08"}


def test_without_the_role_the_plan_and_the_provenance_are_unchanged(study, tmp_path):
    _, plan, permit = _open(study)
    assert "edge_maps" not in plan["source_pins"] and "edge_maps" not in plan["read_bounds"]
    from index_options.observations import MIOPacketInputs

    got = MIOPacketInputs("intake", {"study": study}).run(None, {"permit": permit})
    assert "method_inputs" not in got["bounded_inputs"] and "method_inputs_sha256" not in got["provenance"]


def test_a_map_outside_the_read_bound_never_reaches_the_packet(study, tmp_path):
    """Maps dated before the entry window or at its end are dropped; the in-window map and the summary are hashed."""
    from dskit.pipeline.base import value_hash
    from index_options.observations import MIOPacketInputs

    summary = {"first": "2019-01-02", "marker": 1}
    maps = {"2018-12-31": _map("2018-12-31", w=0.1), "2019-01-02": _map("2019-01-02"),
            "2019-01-08": _map("2019-01-08", w=9.0), "2019-01-15": _map("2019-01-15", w=9.0)}
    packet = _calibrated_packet(study, tmp_path, maps, summary)
    _, plan, permit = _open(packet)
    got = MIOPacketInputs("intake", {"study": packet}).run(None, {"permit": permit})
    inputs = got["bounded_inputs"]["method_inputs"]
    assert list(inputs["edge_maps"]) == ["2019-01-02"] and inputs["edge_walk_summary"] == summary
    assert got["provenance"]["method_inputs_sha256"] == value_hash(inputs)
    # poisoning a map outside the bound changes nothing the packet is handed
    poisoned = {**maps, "2019-01-08": _map("2019-01-08", w=-5.0), "2019-01-15": _map("2019-01-15", w=-5.0)}
    (tmp_path / "again").mkdir()
    other = _calibrated_packet(study, tmp_path / "again", poisoned, summary)
    other["registry"] = {**other["registry"], "path": str(tmp_path / "again" / "exposure.sqlite")}
    again = MIOPacketInputs("intake", {"study": other}).run(None, {"permit": _open(other)[2]})
    assert again["bounded_inputs"]["method_inputs"] == inputs


def test_a_map_whose_own_date_differs_from_its_key_is_refused_at_intake(study, tmp_path):
    from index_options.observations import MIOPacketInputs

    packet = _calibrated_packet(study, tmp_path, {"2019-01-02": _map("2019-01-03")})
    _, _, permit = _open(packet)
    with pytest.raises(ValueError, match="calibration_map_date"):
        MIOPacketInputs("intake", {"study": packet}).run(None, {"permit": permit})


def test_the_authenticated_intake_rechecks_the_map_hash_and_bound_iff_declared(study, tmp_path):
    import copy

    from index_options.observations import MIOPacketInputs

    packet = _calibrated_packet(study, tmp_path, {"2019-01-02": _map("2019-01-02")})
    registry, plan, permit = _open(packet)
    got = MIOPacketInputs("intake", {"study": packet}).run(None, {"permit": permit})
    bounded, provenance = got["bounded_inputs"], got["provenance"]
    check = ledger_studies.MIOPacketStudy.validate_authenticated_intake
    # tampering with a map after intake breaks the input identity
    forged = copy.deepcopy(bounded)
    forged["method_inputs"]["edge_maps"]["2019-01-02"]["states"]["expanding_fill"]["buckets"][0]["w"] = 0.9
    with pytest.raises(ValueError, match="unverified bounded intake"):
        check(packet, plan, permit, provenance, forged, None)

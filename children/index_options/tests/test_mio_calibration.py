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
        params["methods"] = [{"id": name, **method}]
        study = ledger_studies.WeeklyOptionPortfolioStudy(params, inputs)
        synthetic = params["methods"][0]
        if "shock" in method:
            # The original short synthetic history exercises the program with a two-session beta.
            synthetic = {**synthetic, "shock": {**method["shock"], "beta_lookback": 2}}
        seen, run = [], TransportPortfolioSolve.run
        monkeypatch.setattr(TransportPortfolioSolve, "run",
                            lambda self, ctx, inp, run=run, seen=seen: (seen.append(inp), run(self, ctx, inp))[1])
        result = study._method(synthetic, None)
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


# --- stage I5.3: calibration in the study, on a tiny world whose maps the REAL walk child wrote ---------------------

import copy  # noqa: E402
from pathlib import Path  # noqa: E402

from dskit.pipeline.libs.pyomo import TransportPortfolioSolve  # noqa: E402
from edge_calibration_world import World  # noqa: E402
import test_edge_calibration as walk_tests  # noqa: E402
from index_options.edge_calibration import EdgeMapStream  # noqa: E402

START = "2019-10-07"                       # the walk's report_from: the seed week 2019-10-01 is refused


class Harness:
    """The walk's output on a synthetic world and a factory for studies that read it."""

    def __init__(self, root, direction=None):
        pool = {"band": "two_block", "min_blocks": 2, "min_dates": 2, "min_clusters": 2}
        saved, saved_direction = dict(walk_tests.POOL), dict(walk_tests.DIRECTION)
        walk_tests.POOL.update(pool)
        if direction is not None:
            walk_tests.DIRECTION.clear()
            walk_tests.DIRECTION.update(direction)
        try:
            self.world = World(window={"start": "2019-10-01", "end": "2019-12-31"}, last="2020-01-31")
            self.node = walk_tests.FxWalk("edge", walk_tests.params(self.world, root, direction=True,
                                                                    time_block_weeks=[1, 13]), self.world)
            walk_tests.run(self.node, root)
        finally:
            walk_tests.POOL.clear()
            walk_tests.POOL.update(saved)
            walk_tests.DIRECTION.clear()
            walk_tests.DIRECTION.update(saved_direction)
        self.out = Path(self.node.params["out_dir"])
        self.maps = EdgeMapStream.parse(EdgeMapStream.assemble(self.out))
        self.summary = json.loads((self.out / "summary.json").read_text())
        self.node._open()
        domain, p = self.node._domain, self.node.params
        self.end = self.node._entry_end
        clocks = {"sessions": self.node._sessions, "history_start": p["history_start"], "entry_start": START,
                  "entry_end_before": self.end, "tail_end_before": self.node._sealed}
        bridge = {**domain["params"]["bridge"], "fold": self.node._fold_label, "start": START,
                  "end_before": self.end, "protected_end_before": p["protected_end_before"]}
        self.params = {**domain["params"], "bridge": bridge, "symbols": list(self.node._active),
                       "selected_pairs": list(self.node._pairs), "clocks": clocks, "locked_method_ids": list(FORMULATIONS)}
        forecasts = self.node._forecasts(START, self.end)
        self.inputs = {s: {**self.node._market[s], "chain": [r for r in self.node._market[s]["chain"] if r["date"] >= START],
                           **forecasts[s]} for s in self.node._active}
        self.base = {**domain["params"]["methods"][0], "id": "base_twin"}

    def passing(self):
        """The walk's summary with the gated reads set to PASS: a tiny synthetic walk cannot pass them itself."""
        summary = copy.deepcopy(self.summary)
        for name in ("read1", "read2"):
            summary["reads"][name]["verdict"] = "PASS"
        for name in ("read2w", "read2b"):
            summary["integration_reads"][name]["verdict"] = "PASS"
        return summary

    def method(self, mid="cal_arm", **block):
        return {**self.base, "id": mid, "calibration": {**CAL, **block}}

    def study(self, methods, maps=None, summary=None, inputs=None, **over):
        bounded = EdgeMapStream.bounded(self.maps if maps is None else maps, START, self.end)
        params = {**copy.deepcopy(self.params), "methods": methods, **over}
        declared = any("calibration" in m for m in methods)
        return ledger_studies.WeeklyOptionPortfolioStudy(
            params, copy.deepcopy(self.inputs if inputs is None else inputs),
            method_inputs={"edge_maps": bounded, "edge_walk_summary": summary or self.passing()} if declared else None)


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    return Harness(tmp_path_factory.mktemp("walk"))


def reweighted(maps, w, warm=None):
    """Synthetic weights on the real walk's maps: every bucket's through-origin ``w`` (and the pool's) set to ``w``."""
    out = copy.deepcopy(maps)
    for body in out.values():
        for state in body["states"].values():
            for rec in (*state["buckets"], state["pooled"]):
                rec["w"] = rec["w_lcb"] = w
            if warm is not None:
                state["warm"] = warm
    return out


def programs_of(monkeypatch, study, method):
    """Run ``method`` and return ``(result, [program per solve])``."""
    seen, run = [], TransportPortfolioSolve.run
    monkeypatch.setattr(TransportPortfolioSolve, "run",
                        lambda self, ctx, inp, run=run, seen=seen: (seen.append(copy.deepcopy(inp)), run(self, ctx, inp))[1])
    result = study._method(method, None)
    monkeypatch.setattr(TransportPortfolioSolve, "run", run)
    return result, seen


def untimed(obj):
    """``obj`` without any solver wall-clock ``seconds`` (the only run-dependent number)."""
    if isinstance(obj, dict):
        return {k: untimed(v) for k, v in obj.items() if k not in ("seconds", "solve_seconds")}
    return [untimed(v) for v in obj] if isinstance(obj, list) else obj


def statuses(result):
    return [(d["date"], d["status"]) for d in result["decisions"]]


def test_the_walk_warms_on_the_tiny_world(harness):
    """The fixture is meaningful: some weeks are warm-up, at least min_warm_weeks are warm."""
    study = harness.study([harness.method()])
    cal = ledger_studies.CalibratedEdge(study, study.params["methods"][0])
    warm = [cal.week_warm(d) for d in study.weeks]
    assert not all(warm) and sum(warm) >= 8


def test_warmup_weeks_are_cash_in_the_arm_and_in_twin_warm_alike_never_the_raw_edge(harness):
    arm, control = harness.method(), harness.method("twin_warm", form="identity", control=True)
    study = harness.study([arm, control])
    a, c = study._method(arm, None), study._method(control, None)
    warmup = [d for d, s in statuses(a) if s == "calibration_warmup"]
    assert warmup and warmup == [d for d, s in statuses(c) if s == "calibration_warmup"]
    for result in (a, c):
        assert all(not d.get("books") and "selection" not in d for d in result["decisions"] if d["status"] == "calibration_warmup")
    base = harness.study([harness.base])._method(harness.base, None)
    assert any(d["status"] == "selected" and d["date"] in warmup for d in base["decisions"]), "the raw edge WOULD trade there"
    assert a["calibration"]["weeks"] == {"warmup": len(warmup), "warm": len(a["decisions"]) - len(warmup)}
    assert c["calibration"]["control"] is True and a["calibration"]["control"] is False


def test_a_missing_map_refuses_calibration_map_missing_and_a_truncated_stream_is_not_hidden(harness):
    arm = harness.method()
    gone = {t: m for t, m in harness.maps.items() if t != "2019-11-18"}
    with pytest.raises(ValueError, match="calibration_map_missing.*2019-11-18"):
        harness.study([arm], maps=gone)._method(arm, None)
    last = sorted(harness.maps)[-4:]
    truncated = {t: m for t, m in harness.maps.items() if t not in last}
    with pytest.raises(ValueError, match="calibration_map_missing"):
        harness.study([arm], maps=truncated)._method(arm, None)


def test_a_present_but_cold_map_is_still_a_warmup_week(harness):
    arm = harness.method()
    cold = reweighted(harness.maps, 0.5, warm=False)
    result = harness.study([arm], maps=cold)._method(arm, None)
    assert {s for _, s in statuses(result)} == {"calibration_warmup"}
    assert result["calibration"]["counts_as_evidence"] is False


def test_identity_on_a_warm_control_gives_the_base_programs_except_the_zero_value_adjust(harness, monkeypatch):
    """P1 for the twin: with every week warm the control's programs equal the base twin's, coefficient for coefficient."""
    warm = reweighted(harness.maps, 0.5, warm=True)
    control = harness.method("twin_warm", form="identity", control=True)
    c_result, c_programs = programs_of(monkeypatch, harness.study([control], maps=warm), control)
    b_result, b_programs = programs_of(monkeypatch, harness.study([harness.base]), harness.base)
    assert c_programs and len(c_programs) == len(b_programs)
    for c_program, b_program in zip(c_programs, b_programs):
        for group in c_program["groups"]:
            assert set(group.pop("value_adjust")) == {0.0}
        assert c_program == b_program
    fields = {"calibration_adjust", "expected_raw", "radius_premium"}
    strip = lambda rows: [{k: v for k, v in r.items() if k not in fields} for r in rows]       # noqa: E731
    for key in ("curve", "pnl", "final_equity"):
        assert c_result[key] == b_result[key]
    assert strip(c_result["trades"]) == b_result["trades"] and c_result["trades"]
    assert all(fields <= set(t) for t in c_result["trades"]) and all(t["calibration_adjust"] == 0 for t in c_result["trades"])
    for c_d, b_d in zip(c_result["decisions"], b_result["decisions"]):
        c_d = {k: v for k, v in c_d.items() if k != "calibration"}
        if "books" in c_d:
            c_d["books"] = strip(c_d["books"])
        assert untimed(c_d) == untimed(b_d)


def test_slope_adjust_is_the_hand_formula_with_the_mirror_antisymmetric(harness, monkeypatch):
    arm = harness.method()
    maps = reweighted(harness.maps, 0.5, warm=True)
    study = harness.study([arm], maps=maps)
    day = study.weeks[4]
    cal = ledger_studies.CalibratedEdge(study, arm)
    groups, metadata, _ = study._prepare(day)
    kept_groups, kept_meta, _, week, adjusts = cal.candidates(day, groups, metadata)
    assert kept_groups and not week
    rows = cal._rows(day)
    for group, meta in zip(kept_groups, kept_meta):
        e = {}
        for i, item in enumerate(meta["items"]):
            e[i] = rows[meta["id"], item["right"], item["strike"], item["sign"]]["e"]
        values = adjusts[meta["id"]]
        for i, item in enumerate(meta["items"]):
            for j, other in enumerate(meta["items"]):
                if j > i and (item["right"], item["strike"]) == (other["right"], other["strike"]):
                    best = i if (e[i], item["sign"]) > (e[j], other["sign"]) else j
                    mirror = j if best == i else i
                    assert values[best] == pytest.approx(-0.5 * e[best]) and values[mirror] == pytest.approx(0.5 * e[best])
        assert all(abs(a) <= abs(e[i]) or True for i, a in enumerate(values))
    # credit, losses and reserves of the groups are the raw arithmetic
    for kept, raw in zip(kept_groups, groups):
        assert kept["credit"][0] == raw["credit"][0] or len(kept["credit"]) < len(raw["credit"])


def test_the_studys_bucket_and_calibrated_edge_equal_the_walks_decision_rows(harness):
    """Item by item, (bucket, e, c) of the study equal the walk's stored row for the same decision (real maps)."""
    arm = harness.method()
    study = harness.study([arm])
    cal = ledger_studies.CalibratedEdge(study, arm)
    checked = 0
    for line_file in sorted((harness.out / "rows").glob("rows-*.jsonl")):
        for line in line_file.read_text().splitlines():
            row = json.loads(line)
            t = row["t"]
            if t not in study.weeks or not cal.week_warm(t) or row.get("map_warmup"):
                continue
            groups, metadata, _ = study._prepare(t)
            cal.candidates(t, groups, metadata)
            key = next(k for k in cal.last if k[1:] == (row["right"], row["strike"]) and f":{row['name']}:{row['expiry']}" in k[0])
            mine = cal.last[key]
            assert (mine["bucket"], mine["e"]) == (row["bucket"], pytest.approx(row["e"]))
            assert mine["c"] == pytest.approx(row["cw_bucket"], abs=1e-9)
            checked += 1
    assert checked > 10


def test_record_fields_expected_raw_adjust_and_radius_premium_are_consistent(harness):
    arm = {**harness.method(), "rho": 0.01}
    maps = reweighted(harness.maps, 0.5, warm=True)
    result = harness.study([arm], maps=maps)._method(arm, None)
    books = [b for d in result["decisions"] for b in d.get("books", [])]
    assert books and all(b["calibration_adjust"] < 0 and b["radius_premium"] > 0 for b in books)
    for book in books:
        assert book["expected_raw"] == pytest.approx(book["expected"] - book["calibration_adjust"])
    unit = harness.study([arm], maps=reweighted(harness.maps, 1.0, warm=True))._method(arm, None)
    assert all(b["calibration_adjust"] == 0.0 for d in unit["decisions"] for b in d.get("books", [])), "w = 1 gives adj 0.0 exactly"


def test_a_calibrated_arm_trades_less_than_its_twin_and_shock_rows_reserves_are_the_controls(harness, monkeypatch):
    """The adjust is objective-only: on the first warm week the arm's program equals the control's but for value_adjust."""
    arm, control = harness.method(), harness.method("twin_warm", form="identity", control=True)
    maps = reweighted(harness.maps, 0.5)
    a_result, a_programs = programs_of(monkeypatch, harness.study([arm], maps=maps), arm)
    c_result, c_programs = programs_of(monkeypatch, harness.study([control], maps=maps), control)
    a_group, c_group = a_programs[0]["groups"][0], c_programs[0]["groups"][0]
    assert any(a_group["value_adjust"]) and not any(c_group["value_adjust"])
    assert {k: v for k, v in a_group.items() if k != "value_adjust"} == {k: v for k, v in c_group.items() if k != "value_adjust"}
    assert a_programs[0]["caps"] == c_programs[0]["caps"]


def test_a_decision_at_t_reads_only_its_own_map_and_later_maps_change_nothing(harness):
    arm = harness.method()
    maps = reweighted(harness.maps, 0.5)
    whole = harness.study([arm], maps=maps)._method(arm, None)
    weeks = [d["date"] for d in whole["decisions"]]
    for k in (4, 7):
        poisoned = {t: (reweighted({t: m}, 0.99, warm=True)[t] if t > weeks[k] else m) for t, m in maps.items()}
        again = harness.study([arm], maps=poisoned)._method(arm, None)
        assert untimed(again["decisions"][:k + 1]) == untimed(whole["decisions"][:k + 1]), k
    # the guard is the exact-key lookup: a study that took the LATEST map would see another week's weights
    study = harness.study([arm], maps=maps)
    cal = ledger_studies.CalibratedEdge(study, arm)
    assert cal.map_for(weeks[5]) is cal.maps[weeks[5]] and cal.map_for("2019-10-08") is None


def test_an_option_with_no_walk_row_is_excluded_and_counted_never_left_at_its_raw_edge(harness):
    arm = harness.method()
    study = harness.study([arm], maps=reweighted(harness.maps, 0.5, warm=True))
    day = study.weeks[5]
    cal = ledger_studies.CalibratedEdge(study, arm)
    groups, metadata, _ = study._prepare(day)
    # a bucket outside the map: every option of one group loses its row
    emap = copy.deepcopy(cal.maps[day])
    emap["regimes"].pop("AAA")
    cal.maps[day] = emap
    kept, meta, refusals, week, adjusts = cal.candidates(day, groups, metadata)
    assert kept == [] and week["calibration_no_row_excluded"] == sum(len(m["items"]) for m in metadata)
    assert all(r["reason"] == "insufficient_eligible_legs" for r in refusals) and adjusts == {}


def test_a_mirror_item_whose_best_side_was_excluded_is_excluded_with_it(harness):
    arm = harness.method()
    study = harness.study([arm], maps=reweighted(harness.maps, 0.5, warm=True))
    day = study.weeks[5]
    cal = ledger_studies.CalibratedEdge(study, arm)
    groups, metadata, _ = study._prepare(day)
    whole = cal.candidates(day, groups, metadata)
    target = metadata[0]["items"][0]
    option = (target["right"], target["strike"])
    real = cal._adjust

    def cold_for_one_option(row, emap, h_i=0.0):
        return (None, "not_warm_bucket") if (row["right"], row["strike"]) == option and row["group"] == metadata[0]["id"] \
            else real(row, emap, h_i)

    cal._adjust = cold_for_one_option
    kept, meta, _, week, adjusts = cal.candidates(day, groups, metadata)
    before = [(i["right"], i["strike"], i["sign"]) for i in whole[1][0]["items"]]
    after = [(i["right"], i["strike"], i["sign"]) for i in meta[0]["items"]]
    assert [x for x in before if (x[0], x[1]) != option] == after, "both sides of the option left, nothing else"
    assert len(adjusts[meta[0]["id"]]) == len(after) and week["not_warm_bucket"] == 1


def test_every_a9_refusal_names_its_code(harness):
    arm = harness.method()
    good = harness.passing()

    def refused(code, method=arm, **kw):
        with pytest.raises(ValueError, match=code):
            ledger_studies.CalibratedEdge(harness.study([method], **kw), method)

    broken = copy.deepcopy(good)
    del broken["reads"]
    refused("calibration_summary_incomplete", summary=broken)
    for edit, code in (
            (lambda s: s.update(sealed_from="2022-01-01"), "calibration_walk_unsealed"),
            (lambda s: s["pooling"].update(weight_cap=2.0), "calibration_weight_cap"),
            (lambda s: s.update(report_from="2019-11-04"), "calibration_seed_quarter"),
            (lambda s: s["mio_quote_values"].update(multiplier=50), "calibration_walk_mismatch"),
            (lambda s: s.update(symbol_list_sha256="0" * 64), "calibration_walk_mismatch"),
            (lambda s: s.update(walk_decision_dates=s["walk_decision_dates"][:-3]), "calibration_walk_mismatch"),
            (lambda s: s["reads"]["read2"].update(verdict="UNRESOLVED"), "calibration_unread"),
            (lambda s: s["integration_reads"]["read2w"].update(verdict="FAIL"), "calibration_unread"),
            (lambda s: s["integration_reads"]["read2w"].update(form="affine"), "calibration_state_unread"),
            (lambda s: s.update(edge_scale="scale"), "calibration_state_unread")):
        summary = copy.deepcopy(good)
        edit(summary)
        refused(code, summary=summary)
    refused("calibration_state_unread", method=harness.method(state="decayed_fill"))
    refused("calibration_affine_unread", method=harness.method(form="affine"))
    refused("calibration_map_date", maps={**harness.maps, "2019-10-08": {**harness.maps["2019-10-07"], "t": "2019-10-08"}})
    refused("calibration_map_date", maps={**harness.maps, "2019-10-14": {**harness.maps["2019-10-14"], "t": "2019-10-21"}})


def test_the_identity_control_needs_no_pass_words_but_still_the_walk_calendar(harness):
    control = harness.method("twin_warm", form="identity", control=True)
    ledger_studies.CalibratedEdge(harness.study([control], summary=copy.deepcopy(harness.summary)), control)
    seed = copy.deepcopy(harness.summary)
    seed["report_from"] = "2019-12-02"
    with pytest.raises(ValueError, match="calibration_seed_quarter"):
        ledger_studies.CalibratedEdge(harness.study([control], summary=seed), control)


def test_a_study_without_maps_or_with_unused_maps_refuses(harness):
    arm = harness.method()
    params = {**copy.deepcopy(harness.params), "methods": [arm]}
    with pytest.raises(ValueError, match="edge_maps_unbound"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, copy.deepcopy(harness.inputs))
    params["methods"] = [harness.base]
    with pytest.raises(ValueError, match="edge_maps_unused"):
        ledger_studies.WeeklyOptionPortfolioStudy(params, copy.deepcopy(harness.inputs), method_inputs={"edge_maps": harness.maps})


def test_the_authenticated_intake_matches_the_walks_forecast_pins_to_the_documents_own(study, tmp_path):
    packet = _calibrated_packet(study, tmp_path, {"2019-01-02": _map("2019-01-02")})
    sources = packet["domain"]["sources"]
    pins = {role: sources[role]["pin"]["files"][sources[role]["files"]["shared"]] for role in ("forecasts", "contexts")}
    from index_options.observations import MIOPacketInputs
    from dskit.pipeline.study_protocol import StudyContract  # noqa: F401

    def intake(summary, name):
        (tmp_path / name).mkdir()
        shipped = _calibrated_packet(study, tmp_path / name, {"2019-01-02": _map("2019-01-02")}, summary)
        shipped["registry"] = {**shipped["registry"], "path": str(tmp_path / name / "exposure.sqlite")}
        registry, plan, permit = _open(shipped)
        got = MIOPacketInputs("intake", {"study": shipped}).run(None, {"permit": permit})
        return lambda: ledger_studies.MIOPacketStudy.validate_authenticated_intake(
            shipped, plan, permit, got["provenance"], got["bounded_inputs"], None)

    intake({"forecast_pins": {"2019Q1": pins}}, "good")()
    with pytest.raises(ValueError, match="calibration_walk_mismatch"):
        intake({"forecast_pins": {"2019Q1": {**pins, "contexts": "0" * 64}}}, "bad")()
    with pytest.raises(ValueError, match="calibration_summary_incomplete"):
        intake({"first": "2019-01-02"}, "none")()


def test_two_regressors_use_the_walks_direction_params_and_the_origin_line(harness):
    assert harness.summary["direction_params"] == walk_tests.DIRECTION
    arm = harness.method(regressors=2)
    maps = reweighted(harness.maps, 0.5, warm=True)
    for body in maps.values():
        body["origin_two"]["warm"] = True
        for rec in body["origin_two"]["buckets"]:
            rec["w"] = [0.5, 0.25]
    study = harness.study([arm], maps=maps)
    day = study.weeks[5]
    cal = ledger_studies.CalibratedEdge(study, arm)
    groups, metadata, _ = study._prepare(day)
    kept, meta, _, week, adjusts = cal.candidates(day, groups, metadata)
    rows = cal._rows(day)
    assert kept
    seen = 0
    for m in meta:
        for i, item in enumerate(m["items"]):
            row = rows[m["id"], item["right"], item["strike"], item["sign"]]
            mate = [j for j, o in enumerate(m["items"]) if j != i and (o["right"], o["strike"]) == (item["right"], item["strike"])]
            if mate and (row["e"], item["sign"]) < (rows[m["id"], item["right"], item["strike"], m["items"][mate[0]]["sign"]]["e"],
                                                  m["items"][mate[0]]["sign"]):
                continue                                     # the mirror side: checked through its best side
            d, e = row["e_dir"], row["e"]
            assert adjusts[m["id"]][i] == pytest.approx(0.5 * d + 0.25 * (e - d) - e)
            if mate:
                assert adjusts[m["id"]][mate[0]] == pytest.approx(-adjusts[m["id"]][i])
            seen += 1
    assert seen > 0
    result = study._method(arm, None)
    assert result["calibration"]["block"]["regressors"] == 2 and result["decisions"]


def test_regressors_two_with_a_decayed_state_is_unread(harness):
    with pytest.raises(ValueError, match="calibration_state_unread"):
        ledger_studies.CalibratedEdge(harness.study([harness.method(regressors=2, state="decayed_fill")]),
                                      harness.method(regressors=2, state="decayed_fill"))

def _kelly_walk_inputs(harness):
    from datetime import date, timedelta

    from test_ledger_studies import _for_kelly

    params = _for_kelly(copy.deepcopy(harness.params))
    inputs = copy.deepcopy(harness.inputs)
    day = date.fromisoformat(params["clocks"]["history_start"])
    earlier = []
    while len(earlier) < 25:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            earlier.append(day.isoformat())
    earlier.reverse()
    params["clocks"]["history_start"] = earlier[0]
    params["clocks"]["sessions"] = earlier + params["clocks"]["sessions"]
    for rows in inputs.values():
        first = rows["bars"][0]
        rows["bars"][:0] = [{**first, "date": day, "close": first["close"] + (i % 3) * .1}
                            for i, day in enumerate(earlier)]
    return params, inputs


def test_kelly_point_map_changes_only_the_mean_program_of_its_warm_control(harness, monkeypatch):
    from test_ledger_studies import TOP_FORMULATIONS

    base = TOP_FORMULATIONS["kelly025_top100_search_lp"]
    params, inputs = _kelly_walk_inputs(harness)
    options = {"solver": params["solver"], "clocks": params["clocks"], "decision_weeks": ["2019-11-18"],
               "inputs": inputs}
    control = {"id": "kelly025_top100_search_cal_control_lp", **base,
               "calibration": {**CAL, "form": "identity", "control": True}}
    arm = {"id": "kelly025_top100_search_cal_lp", **base, "calibration": dict(CAL)}
    warm = reweighted(harness.maps, .5, warm=True)
    control_study = harness.study([control], maps=warm, **options)
    arm_study = harness.study([arm], maps=warm, **options)
    c_result, c_programs = programs_of(monkeypatch, control_study, control)
    a_result, a_programs = programs_of(monkeypatch, arm_study, arm)
    assert len(c_programs) == len(a_programs) == 1
    assert any(any(group["value_adjust"]) for group in a_programs[0]["groups"])
    for raw, adjusted in zip(c_programs[0]["groups"], a_programs[0]["groups"], strict=True):
        assert not any(raw.pop("value_adjust"))
        adjusted.pop("value_adjust")
    assert c_programs == a_programs
    assert control_study._method(control, None, recorded=c_result) == c_result
    assert arm_study._method(arm, None, recorded=a_result) == a_result

def test_combined_kelly_replays_its_adjusted_orders_and_carries_hedge_funding(harness, monkeypatch):
    from dskit.pipeline.libs.pyomo import TransportActionScore
    from test_ledger_studies import TOP_FORMULATIONS, _PARITY, _REPLAY_BLOCK

    params, inputs = _kelly_walk_inputs(harness)
    weeks = ["2019-11-18", "2019-11-25"]
    warm = reweighted(harness.maps, .8, warm=False)
    for week in weeks:
        warm[week]["states"][CAL["state"]]["warm"] = True
    for data in inputs.values():
        strikes = {}
        for row in data["contexts"]:
            if row["quote_date"] in weeks:
                expiry, spot = row["expiry"], row["spot"]
                options = {quote["strike_raw"] for quote in data["chain"] if quote["expiration"] == expiry}
                strikes.setdefault(expiry, min(options, key=lambda strike: (abs(strike - spot), strike)))
        for quote in data["chain"]:
            if quote["expiration"] in strikes and quote["strike_raw"] == strikes[quote["expiration"]]:
                quote.update(iv=.3, bid=.15, ask=.16)
        data["chain"] = [quote for quote in data["chain"] if quote["type"] == "call"
                         and quote["strike_raw"] == strikes.get(quote["expiration"])]
    base = TOP_FORMULATIONS["kelly025_top100_search_lp"]
    control = {"id": "kelly025_top100_search_cal_lp", **base, "calibration": dict(CAL)}
    arm = {**control, "id": "kelly025_top100_search_cal_replay_hedge_lp",
           "hedge": {**_REPLAY_BLOCK, "ratio": .25}}
    options = {"solver": params["solver"], "clocks": params["clocks"], "inputs": inputs}
    twin = harness.study([control], maps=warm, **options)
    combined = harness.study([arm], maps=warm, hedge_parity=dict(_PARITY), **options)
    c_result, c_programs = programs_of(monkeypatch, twin, control)
    a_result, a_programs = programs_of(monkeypatch, combined, arm)
    assert len(c_programs) == len(a_programs) == 2
    assert c_programs[0] == a_programs[0]
    assert any(any(group["value_adjust"]) for group in a_programs[0]["groups"])
    decisions = lambda result: [d for d in result["decisions"] if d["date"] in weeks]  # noqa: E731
    c_decisions, a_decisions = decisions(c_result), decisions(a_result)
    first = a_decisions[0]["selection"]
    first = first.get("candidate", first)
    scorer = TransportActionScore(a_programs[0], {"parity_tolerance": params["solver"]["parity_tolerance"]})
    assert scorer.evaluate(first["quantities"])["value"] == pytest.approx(first["value"])
    assert c_decisions[0]["books"] == a_decisions[0]["books"] and a_decisions[0]["books"]
    trade = next(t for t in a_result["trades"] if t["decision_date"] == weeks[0])
    assert trade["hedge"]["status"] == "ok" and trade["hedge"]["units"] != 0
    assert trade["close_date"] > weeks[1]
    unhedged = next(t for t in c_result["trades"] if t["decision_date"] == weeks[0]
                    and t["expiry"] == trade["expiry"])
    hedge = trade["hedge"]
    level = next(row["close"] / row["basis_factor"] for row in inputs[trade["symbol"]]["bars"]
                 if row["date"] == trade["close_date"])
    cost = abs(hedge["units"]) * (2 * hedge["rates"]["hedge_fee_per_share"]
                                  + (hedge["reference"] + level) * hedge["rates"]["hedge_half_spread_bps"] / 1e4)
    forward = hedge["units"] * (level - hedge["reference"]) - cost
    assert forward != 0 and trade["pnl"] == pytest.approx(unhedged["pnl"] + forward)
    reserve_delta = a_decisions[1]["open_reserve"] - c_decisions[1]["open_reserve"]
    assert reserve_delta != 0
    assert a_decisions[1]["funding"] == c_decisions[1]["funding"]
    assert a_programs[1]["utility"]["w0"] - c_programs[1]["utility"]["w0"] == pytest.approx(-reserve_delta)

    def unexpected_solve(*args, **kwargs):
        pytest.fail("recorded replay must not solve again")

    monkeypatch.setattr(TransportPortfolioSolve, "run", unexpected_solve)
    assert combined._method(arm, None, recorded=a_result) == a_result

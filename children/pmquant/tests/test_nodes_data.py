"""The data-side kinds under the TOOLKIT'S conformance bar, their domain
behaviour over the synthetic world, and one document end to end:
ladder source -> settlement -> filter -> event-bank -> eligibility ->
inventory, with an event-close time split.

Every probe reads a REAL acquisition: the synthetic connector is pulled
once per session into a pristine onboarding root, and each test gets its
own copy to rewrite (``move``) and append to (``grow``) — the two ways a
store changes between resolve and execute.
"""

import glob
import json
import os
import shutil
from dataclasses import replace

import pytest

from dskit.onboarding import stream_dir
from dskit.pipeline import OutputsConfig, run_document
from dskit.pipeline.base import ConfigError
from dskit.pipeline.conformance import NodeProbe, conformance_suite
from dskit.pipeline.document import load_document
from dskit.pipeline.node import NodeContext
from dskit.pipeline.records import MarketRecord

from pmquant import nodes_data
from pmquant.books import (
    DecisionEpochRecord,
    ladders_from_book_json,
    market_record_from_epoch,
    records_from_pit_rows,
)
from pmquant.ladder.protocols import (
    DEFAULT_DUR_CAPS_H,
    DEFAULT_MIN_ABS_LEAD_S,
    LEAD_FRACS,
    LeadGrid,
    lead_key,
    venue_of,
)
from pmquant.nodes_data import (
    MARKETS_STREAM,
    NODE_KINDS,
    PIT_STREAM,
    Inventory,
    LadderSource,
    Settlement,
)
from pmquant.testing import SyntheticLadderWorld, acquire_synthetic

#: The independent role census — cross-checked against the classes so a
#: mislabelled role cannot silently exit its checks.
EXPECTED_ROLES = {
    "pmquant-ladder-source": "data",
    "pmquant-settlement": "labels",
    "pmquant-inventory": "report",
}

SOURCE = "synthetic"

#: The probe world: both venues, few events — conformance runs it dozens
#: of times.
PROBE_WORLD = {"series": ["KXSYNA", "POLYSYNC"], "events_per_series": 6, "rungs": 2}

#: The behaviour world: the connector's defaults (3 series, 60 events, 3 rungs).
FULL_WORLD = {}

#: One pristine acquisition per session basetemp, copied per test.
_PRISTINE = {}


def _ctx(tmp_path):
    return NodeContext(name="test", asof="2026-03-10", run_dir=str(tmp_path / "run"))


def _stream_file(root, stream):
    """The one acquired member of ``stream`` under ``root``."""
    (path,) = glob.glob(os.path.join(stream_dir(root, SOURCE), "*", f"{stream}.jsonl"))
    return path


def _pristine_root(basetemp):
    key = str(basetemp)
    if key not in _PRISTINE:
        path = os.path.join(key, f"pmquant-pristine-{os.getpid()}")
        acquire_synthetic(path, PROBE_WORLD)
        _PRISTINE[key] = path
    return _PRISTINE[key]


def _acquired_root(tmp_path):
    """This test's own copy of the pristine acquisition (idempotent per tmp_path)."""
    root = tmp_path / "ob"
    if not root.exists():
        shutil.copytree(_pristine_root(tmp_path.parent), root)
    return str(root)


def _load_lines(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _rewrite(path, mutate):
    """Rewrite one jsonl member in place through ``mutate(rows)``, mtime restored."""
    stat = os.stat(path)
    rows = _load_lines(path)
    mutate(rows)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    os.utime(path, (stat.st_atime, stat.st_mtime))


def _append(path, row):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _is_kx_lead(row):
    return row["data"]["series"].startswith("KX") and row["data"]["kind"] == "lead"


def _envelopes(world, venue):
    """The world's records for one venue, through the child's own projection."""
    records = []
    for series in world.series:
        if venue_of(series) != venue:
            continue
        natives = records_from_pit_rows(world.pit_rows(series), venue, SOURCE)
        records.extend(market_record_from_epoch(venue, rec) for rec in natives)
    return records


_INVENTORY_WORLD = SyntheticLadderWorld(**PROBE_WORLD)
_INVENTORY_RECORDS = _envelopes(_INVENTORY_WORLD, "kalshi")


def probes(tmp_path):
    """One NodeProbe per kind over this test's copy of the acquisition."""
    root = _acquired_root(tmp_path)
    pit_path = _stream_file(root, PIT_STREAM)
    markets_path = _stream_file(root, MARKETS_STREAM)
    source_params = {"root": root, "source": SOURCE, "venue": "kalshi"}
    labels_params = {"root": root, "source": SOURCE}

    def move_pit():
        def mutate(rows):
            row = next(r for r in rows if _is_kx_lead(r))
            row["data"]["staleness_ms"] += 1
            row["data"]["chosen_ts_ms"] -= 1

        _rewrite(pit_path, mutate)

    def grow_pit():
        template = [r for r in _load_lines(pit_path) if _is_kx_lead(r)][-1]
        row = json.loads(json.dumps(template))
        data = row["data"]
        data["event_ticker"] = "KXSYNA-991231"
        data["contract_ticker"] = "KXSYNA-991231-R0"
        data["epoch_ts_ms"] += 86_400_000
        data["chosen_ts_ms"] += 86_400_000
        _append(pit_path, row)

    def move_markets():
        def mutate(rows):
            rows[0]["data"]["last_price"] = round(rows[0]["data"]["last_price"] + 0.001, 4)

        _rewrite(markets_path, mutate)

    def grow_markets():
        template = [
            r for r in _load_lines(markets_path) if r["data"]["series_ticker"] == "KXSYNA"
        ][-1]
        row = json.loads(json.dumps(template))
        data = row["data"]
        data["event_ticker"] = "KXSYNA-991231"
        data["ticker"] = "KXSYNA-991231-R0"
        data["result"] = "yes"
        data["open_time"] = "2099-12-30T00:00:00Z"
        data["close_time"] = "2099-12-31T00:00:00Z"
        _append(markets_path, row)

    return {
        "pmquant-ladder-source": NodeProbe(
            params=dict(source_params),
            required=("root", "source", "venue"),
            make=lambda: LadderSource("ladder_source", dict(source_params)),
            move=move_pit,
            grow=grow_pit,
            size=lambda out: len(out["records"]),
            runnable=True,
        ),
        "pmquant-settlement": NodeProbe(
            params=dict(labels_params),
            required=("root", "source"),
            make=lambda: Settlement("settlement", dict(labels_params)),
            move=move_markets,
            grow=grow_markets,
            runnable=True,
        ),
        "pmquant-inventory": NodeProbe(
            params={"min_events": 2},
            required=("min_events",),
            inputs={
                "records": list(_INVENTORY_RECORDS),
                "outcomes": dict(_INVENTORY_WORLD.outcomes()),
            },
            stream_ports=("records",),
            runnable=True,
        ),
    }


TestConformance = conformance_suite(
    registry=NODE_KINDS,
    module="pmquant.nodes_data",
    probes=probes,
    expected_roles=EXPECTED_ROLES,
    name="TestConformance",
)


# -- fixtures over the full world ---------------------------------------------


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    """The default world, acquired once for the behaviour tests."""
    world = SyntheticLadderWorld(**FULL_WORLD)
    root, _registry, source = acquire_synthetic(
        str(tmp_path_factory.mktemp("full") / "ob"), FULL_WORLD
    )
    return world, root.root, source


def _ladder(full, venue, **over):
    world, root, source = full
    params = {"root": root, "source": source, "venue": venue, **over}
    return LadderSource("ladder_source", params)


# -- the ladder source --------------------------------------------------------


def test_auto_instruments_scope_by_venue(full):
    world, _root, _source = full
    kalshi = _ladder(full, "kalshi")
    poly = _ladder(full, "polymarket")
    assert kalshi.instruments() == ["KXSYNA", "KXSYNB"]
    assert poly.instruments() == ["POLYSYNC"]
    out = kalshi.run(None, {})
    assert out["instruments"] == ["KXSYNA", "KXSYNB"]
    assert {r.instrument for r in out["records"]} == {"KXSYNA", "KXSYNB"}
    assert {r.venue for r in out["records"]} == {"kalshi"}
    expected = sum(len(world.pit_rows(s)) for s in ("KXSYNA", "KXSYNB"))
    assert len(out["records"]) == expected
    # The fingerprint names the universe, so two venues over ONE stream differ.
    fk, fp = kalshi.fingerprint(), poly.fingerprint()
    assert fk["sha256"] == fp["sha256"]  # the whole-stream digest — the safe direction
    assert fk["instruments"] != fp["instruments"]
    assert fk["rows"] == expected


def test_a_wrong_venue_or_universe_refuses_at_plan_time(full):
    _world, root, source = full
    base = {"root": root, "source": source}
    problems = LadderSource.validate_params({**base, "venue": "nyse"})
    assert any("venue" in p for p in problems), problems
    with pytest.raises(ConfigError, match="venue"):
        LadderSource("bad", base)
    for bad in ("all", "*", [], ["KXSYNA", "KXSYNA"], [1], "KXSYNA"):
        problems = LadderSource.validate_params({**base, "venue": "kalshi", "instruments": bad})
        assert any("instruments" in p for p in problems), (bad, problems)
    # An explicit instrument of the OTHER venue is refused before any scan.
    problems = LadderSource.validate_params(
        {**base, "venue": "kalshi", "instruments": ["POLYSYNC"]}
    )
    assert any("POLYSYNC" in p and "polymarket" in p for p in problems), problems
    assert LadderSource.validate_params(
        {**base, "venue": "kalshi", "instruments": ["KXSYNB"]}
    ) == []


def test_an_auto_universe_that_resolves_empty_refuses_by_name(tmp_path):
    """A venue that owns no series in the stream is a wiring defect (wrong
    stream, wrong venue, nothing acquired) — never a source that emits
    nothing and lets the run report success."""
    root, _registry, source = acquire_synthetic(
        str(tmp_path / "ob"), {"series": ["KXSYNA"], "events_per_series": 2, "rungs": 2}
    )
    node = LadderSource(
        "ladder_source", {"root": root.root, "source": source, "venue": "polymarket"}
    )
    with pytest.raises(ValueError, match="polymarket") as excinfo:
        node.fingerprint()
    assert PIT_STREAM in str(excinfo.value) and "KXSYNA" in str(excinfo.value)
    assert LadderSource(
        "ladder_source", {"root": root.root, "source": source, "venue": "kalshi"}
    ).instruments() == ["KXSYNA"]


def test_an_explicit_instrument_with_no_rows_refuses_by_name(full):
    node = _ladder(full, "kalshi", instruments=["KXSYNA", "KXNOPE"])
    with pytest.raises(ValueError, match="KXNOPE"):
        node.fingerprint()
    narrow = _ladder(full, "kalshi", instruments=["KXSYNB"])
    assert narrow.instruments() == ["KXSYNB"]
    assert {r.instrument for r in narrow.run(None, {})["records"]} == {"KXSYNB"}


def test_the_stream_default_has_one_name(full, monkeypatch):
    """``PIT_STREAM`` is read by the knob gate AND the scan: rebinding it
    moves both, so a document declaring no ``stream`` follows the constant."""
    _world, root, source = full
    monkeypatch.setattr(nodes_data, "PIT_STREAM", 5)
    problems = LadderSource.validate_params({"root": root, "source": source, "venue": "kalshi"})
    assert any("stream" in p for p in problems), problems
    monkeypatch.setattr(nodes_data, "PIT_STREAM", "ghost")
    node = LadderSource("ladder_source", {"root": root, "source": source, "venue": "kalshi"})
    assert node.stream() == "ghost"
    with pytest.raises(Exception, match="ghost"):
        node.fingerprint()
    monkeypatch.setattr(nodes_data, "MARKETS_STREAM", "ghost")
    problems = Settlement.validate_params({"root": root, "source": source, "stream": None})
    assert any("stream" in p for p in problems), problems


def test_records_carry_native_epochs_with_the_mirror_applied(full):
    world, _root, _source = full
    for venue, series in (("kalshi", "KXSYNA"), ("polymarket", "POLYSYNC")):
        out = _ladder(full, venue, instruments=[series]).run(None, {})
        books = {
            (r["contract_ticker"], r["epoch_ts_ms"]): r
            for r in world.pit_rows(series)
            if r["kind"] == "lead"
        }
        leads = [r for r in out["records"] if r.native.epoch_kind == "lead"]
        settles = [r for r in out["records"] if r.native.epoch_kind == "settle"]
        assert len(leads) == len(books) and len(settles) == len(world.outcomes()) / 3
        for rec in leads:
            assert isinstance(rec, MarketRecord)
            assert isinstance(rec.native, DecisionEpochRecord)
            assert rec.venue == venue and rec.instrument == series
            assert rec.contract == rec.native.contract_ticker
            assert rec.group == rec.native.event_ticker == rec.contract.rsplit("-", 1)[0]
            assert rec.asof_ms == rec.native.epoch_ts_ms
            assert lead_key(rec.lead_frac) in {lead_key(f) for f in LEAD_FRACS}
            row = books[(rec.contract, rec.asof_ms)]
            fp = json.loads(row["book_json"])["orderbook_fp"]
            yes_levels, no_levels = ladders_from_book_json(row["book_json"])
            assert rec.native.yes_levels == yes_levels
            assert rec.native.no_levels == no_levels
            assert rec.bid == pytest.approx(fp["yes_dollars"][0][0])
            assert rec.usable == row["usable"] and rec.reason == row["reason"]
            if venue == "polymarket":
                own_ask = fp["yes_asks"][0][0] if fp["yes_asks"] else None
                if own_ask is None:
                    assert rec.ask is None and rec.mid is None and not rec.usable
                else:
                    assert rec.ask == pytest.approx(own_ask)
                    assert rec.native.no_levels[0][0] == pytest.approx(1.0 - own_ask)
            else:
                no_bid = fp["no_dollars"][0][0] if fp["no_dollars"] else None
                if no_bid is None:
                    assert rec.ask is None and not rec.usable
                else:
                    assert rec.ask == pytest.approx(1.0 - no_bid)
        for rec in settles:
            assert rec.lead_frac is None and not rec.usable and rec.reason == "settle"
            assert rec.bid is None and rec.ask is None
        # Unusable rows RIDE ALONG — cutting is the document's job.
        assert any(not r.usable for r in leads)


def test_data_edge_is_the_newest_settle_and_bounds_span_each_event(full):
    world, _root, _source = full
    node = _ladder(full, "kalshi")
    kx_events = [e for s in ("KXSYNA", "KXSYNB") for e in world.events(s)]
    assert node.data_edge() == max(world.close_ms(e) for e in kx_events)
    grid = LeadGrid(LEAD_FRACS, DEFAULT_DUR_CAPS_H, DEFAULT_MIN_ABS_LEAD_S)
    bounds = node.event_bounds()
    assert set(bounds) == set(kx_events)
    for event in kx_events:
        open_ms, close_ms = world.open_ms(event), world.close_ms(event)
        assert bounds[event].open_ms == grid.epochs(open_ms, close_ms)[0][1]
        assert bounds[event].close_ms == close_ms
    assert LadderSource.supported_split_kinds == ("time", "trailing")


# -- settlement ---------------------------------------------------------------


def test_settlement_maps_yes_no_and_excludes_open_contracts(full, tmp_path):
    world, root, source = full
    node = Settlement("settlement", {"root": root, "source": source})
    out = node.run(None, {})
    assert out["outcomes"] == world.outcomes()
    assert out["instruments"] == ["KXSYNA", "KXSYNB", "POLYSYNC"]
    assert out["metrics"] == {
        "n_settled": len(world.outcomes()),
        "n_instruments": 3,
        "n_rows": len(world.outcomes()),
    }
    (row,) = [r for r in out["rows"] if r["ticker"] == world.contracts("KXSYNA-260105")[0]]
    assert row["close_ms"] == world.close_ms("KXSYNA-260105")
    assert row["open_ms"] == world.open_ms("KXSYNA-260105")
    assert row["strike_type"] == "less" and row["floor_strike"] is None

    # A copy of the store with one OPEN contract and one unknown result.
    copy = tmp_path / "ob"
    shutil.copytree(root, copy)
    path = _stream_file(str(copy), MARKETS_STREAM)
    lines = _load_lines(path)
    open_ticker = lines[0]["data"]["ticker"]

    def mutate(rows):
        rows[0]["data"]["result"] = ""

    _rewrite(path, mutate)
    opened = Settlement("settlement", {"root": str(copy), "source": source}).run(None, {})
    assert open_ticker not in opened["outcomes"]
    assert open_ticker in {r["ticker"] for r in opened["rows"]}
    assert opened["metrics"]["n_settled"] == len(world.outcomes()) - 1

    def corrupt(rows):
        rows[1]["data"]["result"] = "void"

    _rewrite(path, corrupt)
    bad = Settlement("settlement", {"root": str(copy), "source": source})
    with pytest.raises(ValueError, match=lines[1]["data"]["ticker"]) as excinfo:
        bad.run(None, {})
    assert "void" in str(excinfo.value)


def test_a_wired_instruments_input_wins_over_the_param(full):
    world, root, source = full
    node = Settlement("settlement", {"root": root, "source": source, "instruments": ["KXSYNB"]})
    out = node.run(None, {"instruments": ["KXSYNA", "POLYSYNC"]})
    assert out["instruments"] == ["KXSYNA", "POLYSYNC"]
    assert {r["series_ticker"] for r in out["rows"]} == {"KXSYNA", "POLYSYNC"}
    assert set(out["outcomes"]) == {
        c for s in ("KXSYNA", "POLYSYNC") for e in world.events(s) for c in world.contracts(e)
    }
    unwired = node.run(None, {})
    assert unwired["instruments"] == ["KXSYNB"]
    with pytest.raises(ValueError, match="KXNOPE"):
        node.run(None, {"instruments": ["KXNOPE"]})
    for bad in ("KXSYNA", [], ["KXSYNA", "KXSYNA"], [1], (r for r in ["KXSYNA"])):
        assert node.validate_inputs({"instruments": bad}), bad
    assert node.validate_inputs({}) == []
    assert node.validate_inputs({"instruments": ["KXSYNA"]}) == []
    assert node.fingerprint() == Settlement(
        "settlement", {"root": root, "source": source}
    ).fingerprint()  # the whole stream, whatever the universe


# -- inventory ----------------------------------------------------------------


def _inventory(records, outcomes, tmp_path, **params):
    node = Inventory("inventory", {"min_events": 10, **params})
    return node, node.run(_ctx(tmp_path), {"records": records, "outcomes": outcomes})


def test_inventory_counts_the_synthetic_world(full, tmp_path):
    world, _root, _source = full
    records = _envelopes(world, "kalshi") + _envelopes(world, "polymarket")
    node, out = _inventory(records, world.outcomes(), tmp_path)
    assert set(out["inventory"]) == set(world.series)
    for series, row in out["inventory"].items():
        assert row["events_settled"] == world.events_per_series
        # Every lead has a yes-side book, so one-sidedness never costs usability...
        assert row["usable"] == row["events_settled"]
        # ...but it can cost both sides, and the usable flag, at a whole lead.
        assert row["tradeable"] <= row["two_sided"] <= row["usable"]
        assert row["eligible"] is (row["usable"] >= 10)
        assert row["eligible"]
    assert out["metrics"] == {"n_series": 3, "n_eligible": 3}
    path = os.path.join(node.artifact_dir(_ctx(tmp_path)), "inventory.json")
    with open(path, encoding="utf-8") as fh:
        artifact = json.load(fh)
    assert artifact["series"] == out["inventory"]
    assert artifact["min_events"] == 10
    assert artifact["lead_fracs"] == [lead_key(f) for f in LEAD_FRACS]

    _node, high = _inventory(records, world.outcomes(), tmp_path, min_events=61)
    assert high["metrics"]["n_eligible"] == 0
    assert not any(v["eligible"] for v in high["inventory"].values())


def test_inventory_lead_layers_move_independently(tmp_path):
    """Hand-built rows: an event with NO book at one lead (all rungs) leaves
    ``usable``; one with only ONE-SIDED books at a lead leaves ``two_sided``
    and ``tradeable`` but stays usable; an unsettled event leaves them all."""
    world = SyntheticLadderWorld(series=["KXSYNA"], events_per_series=4, rungs=2,
                                 one_sided_rate=0.0)
    e_nobook, e_onesided, e_open, e_fine = world.events("KXSYNA")
    target_lead = lead_key(LEAD_FRACS[5])
    rows = []
    for row in world.pit_rows("KXSYNA"):
        row = dict(row)
        at_target = row["kind"] == "lead" and lead_key(row["lead_frac"]) == target_lead
        if row["event_ticker"] == e_nobook and at_target:
            row.update(book_json=None, admissible=False, quality_ok=False,
                       usable=False, reason="no_book")
        if row["event_ticker"] == e_onesided and at_target:
            fp = json.loads(row["book_json"])["orderbook_fp"]
            fp["no_dollars"] = []
            row.update(book_json=json.dumps({"orderbook_fp": fp}), quality_ok=False,
                       usable=False, reason="low_quality")
        rows.append(row)
    records = [
        market_record_from_epoch("kalshi", rec)
        for rec in records_from_pit_rows(rows, "kalshi", SOURCE)
    ]
    outcomes = {c: v for c, v in world.outcomes().items() if not c.startswith(e_open)}
    _node, out = _inventory(records, outcomes, tmp_path, min_events=2)
    assert out["inventory"] == {
        "KXSYNA": {
            "events_settled": 3,
            "usable": 2,
            "two_sided": 1,
            "tradeable": 1,
            "eligible": True,
        }
    }
    # A narrower declared grid that skips the damaged lead sees no damage.
    fracs = [f for f in LEAD_FRACS if lead_key(f) != target_lead]
    _node, out = _inventory(records, outcomes, tmp_path, min_events=2, lead_fracs=fracs)
    assert out["inventory"]["KXSYNA"]["usable"] == 3
    assert out["inventory"]["KXSYNA"]["two_sided"] == 3


def test_inventory_validates_its_knobs_and_inputs():
    assert any("min_events" in p for p in Inventory.validate_params({}))
    assert any("min_events" in p for p in Inventory.validate_params({"min_events": 0}))
    for bad in ([], [1.5], [0.5, 0.5], [0.2, 0.5], "x", [0.5, "a"]):
        problems = Inventory.validate_params({"min_events": 1, "lead_fracs": bad})
        assert any("lead_fracs" in p for p in problems), (bad, problems)
    assert Inventory.validate_params({"min_events": 1, "lead_fracs": [0.9, 0.1]}) == []
    node = Inventory("inventory", {"min_events": 1})
    assert node.validate_inputs({"records": (r for r in []), "outcomes": {}})
    assert node.validate_inputs({"records": [], "outcomes": []})
    assert node.validate_inputs({"records": [], "outcomes": {}}) == []


# -- one document, end to end -------------------------------------------------


def test_inventory_document_end_to_end(full, tmp_path):
    world, root, source = full
    closes = [world.close_ms(e) for e in world.events("KXSYNA")]
    doc = {
        "name": "pmquant-inventory-e2e",
        "notes": "ladder source -> settlement -> filter -> bank -> eligibility -> inventory.",
        "pipeline": {
            "ladder_records": {
                "uses": "pmquant-ladder-source",
                "params": {"root": root, "source": source, "venue": "kalshi"},
            },
            "settlements": {
                "uses": "pmquant-settlement",
                "inputs": {"instruments": "$ladder_records.instruments"},
                "params": {"root": root, "source": source},
            },
            "bankable": {
                "uses": "filter",
                "inputs": {"records": "$ladder_records.records"},
                "params": {"require_usable": True},
            },
            "bank": {
                "uses": "event-bank",
                "inputs": {"events": "$bankable.records", "outcomes": "$settlements.outcomes"},
                "params": {"strictly_before": "$splits.train_end_ms"},
            },
            "eligible_family": {
                "uses": "eligibility",
                "inputs": {"banked": "$bank.counts"},
                "params": {"min_events": 10},
            },
            "inventory": {
                "uses": "pmquant-inventory",
                "inputs": {
                    "records": "$ladder_records.records",
                    "outcomes": "$settlements.outcomes",
                },
                "params": {"min_events": 10},
            },
        },
        "splits": {
            "kind": "time",
            "train_end_ms": closes[36],
            "val_end_ms": closes[48],
            "test_end_ms": closes[-1] + 1,
            "policy": "event-close",
        },
    }
    path = tmp_path / "run-inventory.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    document = load_document(str(path))
    document = replace(document, outputs=OutputsConfig(run_root=str(tmp_path / "runs")))
    result = run_document(document, asof="2026-03-10")
    assert result.state == "ran", (result.state, result.error)
    assert result.outputs["eligible_family"]["verdict"] == "GO"
    assert result.outputs["eligible_family"]["instruments"] == ["KXSYNA", "KXSYNB"]
    assert result.outputs["settlements"]["instruments"] == ["KXSYNA", "KXSYNB"]
    assert result.outputs["inventory"]["metrics"] == {"n_series": 2, "n_eligible": 2}
    assert os.path.isfile(os.path.join(result.run_dir, "artifacts", "inventory", "inventory.json"))
    assert os.path.isfile(os.path.join(result.run_dir, "result.json"))

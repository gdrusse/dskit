"""ADR-0311 stage 3: ``EdgeReliabilityWalk`` on a synthetic world (tests 1-5 and 12, leakage first).

The world below is a pure function of (name, date, option): every decision row, fill quote and
settlement bar is derived from a hash of its own date, so a test can poison or delete everything
dated at or after ``T`` and watch what the walk does. The hooks log every call, so "the fill quote
is read only at admission" is asserted from the call log, not from the output.
"""

import datetime
import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

import dskit.pipeline.libs.edge_walk as ew
from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.edge_walk import EdgeReliabilityWalk
from dskit.pipeline.node import NodeContext

FIRST = datetime.date(2019, 10, 4)  # a Friday
N_WEEKS = 30
CAL = [(FIRST + datetime.timedelta(days=7 * k)).isoformat() for k in range(N_WEEKS)]
NAMES = ("AAA", "BBB", "CCC")
STRIKES = (90.0, 100.0, 110.0)
RIGHTS = ("call", "put")
EXPIRY_DAYS = (14, 21, 13)  # two Fridays and a Thursday (a holiday expiry settles Thursday)
ABS_Z = {90.0: 1.6, 100.0: 0.0, 110.0: 0.5}
STATES = ("expanding_fill", "decayed_fill", "expanding_decision", "decayed_decision")
EDGES = {"dte_edges": [8, 22], "abs_z_edges": [1.0], "spread_edges": [0.15]}
POOL = {"min_clusters": 2, "min_dates": 2, "tau2_floor": 1e-6, "weight_cap": 1.0, "lcb_k": 1.0, "band": "two"}
ADJUST = {"name_prior_clusters": 26, "name_min_clusters": 2}
REGIME = {"regime_lookback_weeks": 4, "regime_min_weeks": 2}


def u(*key):
    """A deterministic number in [0, 1) from a key."""
    return int(hashlib.sha256(repr(key).encode()).hexdigest()[:12], 16) / 16**12


def add_days(day, n):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=n)).isoformat()


class World:
    """Decision rows, fill quotes and bars, all dated; poisoned or deleted from a date on."""

    def __init__(self, poison_from=None, delete_from=None, quote_override=None, extra_rows=None,
                 no_rows_before=None):
        self.poison_from = poison_from
        self.delete_from = delete_from
        self.quote_override = quote_override
        self.extra_rows = extra_rows or {}
        self.no_rows_before = no_rows_before

    def _bad(self, d):
        return self.poison_from is not None and d >= self.poison_from

    def _check_alive(self, d):
        assert self.delete_from is None or d < self.delete_from, f"read data dated {d}, which is deleted"

    @staticmethod
    def factor(name, d):
        return 2.0 if name == "CCC" and d >= "2020-02-09" else 1.0

    def row(self, name, t, expiry, right, strike, sign, fill=None):
        """One decision row; ``fill`` overrides the fill session (default: the Monday after ``t``)."""
        key = (name, t, expiry, right, strike)
        price = 1.0 + 4.0 * u("p", *key)
        x = (u("x", *key) - 0.5) * 10.0
        spread = 0.05 + 0.3 * (u("s", *key) > 0.5)
        v = 0.2 * (1.0 + 0.5 * u("v", name, t))
        e = x if sign > 0 else -x - 1.3
        credit = -sign * price * 100.0 - 0.65
        if self._bad(t):
            e, credit, v, spread = 1e9, -1e9, 1e6, 0.99
        return {
            "group": f"{name}|{t}|{expiry}", "name": name, "expiry": expiry, "settle_date": expiry,
            "fill_date": fill or add_days(t, 3), "factor": self.factor(name, t), "right": right,
            "strike": strike, "sign": sign, "e": e, "dte": float((datetime.date.fromisoformat(expiry)
                                                                 - datetime.date.fromisoformat(t)).days),
            "abs_z": ABS_Z[strike], "spread": spread, "v": v, "credit": credit, "multiplier": 100.0,
        }

    def rows_at(self, t):
        if self.delete_from is not None and t >= self.delete_from:
            return []
        if self.no_rows_before is not None and t < self.no_rows_before:
            return []
        rows = []
        for name in NAMES:
            for days in EXPIRY_DAYS:
                expiry = add_days(t, days)
                for right in RIGHTS:
                    for strike in STRIKES:
                        for sign in (1, -1):
                            rows.append(self.row(name, t, expiry, right, strike, sign))
        rows.extend(self.extra_rows.get(t, []))
        return rows

    def fill_credit(self, r):
        d = r["fill_date"]
        self._check_alive(d)
        if self.factor(r["name"], d) != r["factor"]:
            return "basis"
        key = (r["name"], d, r["expiry"], r["right"], r["strike"])
        if u("miss", *key) < 0.04:
            return "quote"
        price = 1.0 + 4.0 * u("q", *key)
        if self.quote_override is not None:
            price = self.quote_override(*key) or price
        if self._bad(d):
            price = 1e6
        return -r["sign"] * price * 100.0 - 0.65

    def level(self, r):
        s = r["settle_date"]
        self._check_alive(s)
        if self.factor(r["name"], s) != r["factor"]:
            return "basis_changed"
        if r["name"] == "BBB" and s == CAL[9]:
            return "no_settlement_bar"
        level = 100.0 + 24.0 * (u("L", r["name"], s) - 0.5)
        return 1e7 if self._bad(s) else level


class SynthWalk(EdgeReliabilityWalk):
    """The walk on synthetic hooks; ``events`` logs every hook call in order."""

    def __init__(self, key, params, world, pins=None, fail_at=None, on_decide=None):
        super().__init__(key, params)
        self.world = world
        self.pins = pins or {}
        self.fail_at = fail_at
        self.on_decide = on_decide
        self.events = []

    def _input_pins(self):
        return dict(self.pins)

    def _next_session(self, t):
        self.events.append(("next_session", t))
        return add_days(t, 3)

    def _decision_calendar(self):
        self.events.append(("calendar",))
        return list(CAL)

    def _decision_rows(self, t):
        self.events.append(("decide", t))
        if self.fail_at == t:
            raise RuntimeError("interrupted")
        if self.on_decide is not None:
            self.on_decide(self, t)
        return self.world.rows_at(t)

    def _fill_credits(self, rows):
        self.events.append(("fill", [(r["settle_date"], r["fill_date"], r["name"], r["decision_date"],
                                      r["expiry"], r["right"], r["strike"]) for r in rows]))
        return [self.world.fill_credit(r) for r in rows]

    def _settlement_levels(self, rows):
        self.events.append(("level", [(r["settle_date"], r["name"]) for r in rows]))
        return [self.world.level(r) for r in rows]


def params(out_dir, **over):
    p = {
        "window": {"start": CAL[0], "end": CAL[-1]}, "report_from": CAL[13], "sealed_from": "2021-07-01",
        "out_dir": str(out_dir), "half_life_weeks": 26, "buckets": dict(EDGES), "regime": dict(REGIME),
        "pooling": dict(POOL), "name_adjust": dict(ADJUST), "checkpoint_every": 1, "ram_gb_limit": 64,
    }
    p.update(over)
    return p


def make(tmp_path, world=None, end=CAL[-1], out="out", pins=None, fail_at=None, on_decide=None, **over):
    over.setdefault("window", {"start": CAL[0], "end": end})
    return SynthWalk("walk", params(tmp_path / out, **over), world or World(), pins=pins,
                     fail_at=fail_at, on_decide=on_decide)


def run(node, tmp_path):
    return node.run(NodeContext(name="walk", asof=CAL[-1], run_dir=str(tmp_path)), {})


def tree(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def maps_upto(root, last):
    return {k: v for k, v in tree(root).items() if k.startswith("map-") and k[4:14] <= last}


def checkpoint(root):
    return json.loads((Path(root) / "checkpoint.json").read_text())


def fill_weeks(events):
    """Map each settlement date to the decision week whose settle step first joined it."""
    out, pending = {}, []
    for ev in events:
        if ev[0] == "fill":
            pending.extend(ev[1])
        elif ev[0] == "decide":
            for row in pending:
                out.setdefault(row[0], ev[1])
            pending = []
    return out


T = CAL[14]


# --------------------------------------------------------------------------- leakage first


class TestPoisonedFuture:
    def test_poisoned_rows_dated_at_or_after_t_change_nothing_at_t(self, tmp_path):
        clean, dirty = make(tmp_path, World(), end=T, out="clean"), make(
            tmp_path, World(poison_from=T), end=T, out="dirty")
        run(clean, tmp_path)
        run(dirty, tmp_path)
        assert maps_upto(tmp_path / "clean", T) == maps_upto(tmp_path / "dirty", T)
        assert f"map-{T}.json" in maps_upto(tmp_path / "clean", T)
        assert checkpoint(tmp_path / "clean")["states"] == checkpoint(tmp_path / "dirty")["states"]
        assert set(checkpoint(tmp_path / "clean")["states"]) == set(STATES)

    def test_positive_control_the_poison_does_move_later_maps(self, tmp_path):
        late = add_days(T, 35)
        run(make(tmp_path, World(), end=late, out="clean"), tmp_path)
        run(make(tmp_path, World(poison_from=T), end=late, out="dirty"), tmp_path)
        assert maps_upto(tmp_path / "clean", T) == maps_upto(tmp_path / "dirty", T)
        assert maps_upto(tmp_path / "clean", late) != maps_upto(tmp_path / "dirty", late)

    def test_deleting_everything_dated_at_or_after_t_is_byte_identical(self, tmp_path):
        seen = {}

        def capture(label):
            def hook(walk, t):
                if t == T:
                    seen[label] = (walk._regime.to_dict(), json.dumps(
                        {k: [s.to_dict(), a.to_dict()] for k, (s, a) in walk._states.items()}, sort_keys=True))
            return hook

        run(make(tmp_path, World(), end=T, out="clean", on_decide=capture("clean")), tmp_path)
        run(make(tmp_path, World(delete_from=T), end=T, out="cut", on_decide=capture("cut")), tmp_path)
        assert maps_upto(tmp_path / "clean", T) == maps_upto(tmp_path / "cut", T)
        assert checkpoint(tmp_path / "clean")["states"] == checkpoint(tmp_path / "cut")["states"]
        assert seen["clean"] == seen["cut"]


class TestFillTiming:
    def test_a_fill_quote_of_an_unsettled_row_changes_nothing_at_t(self, tmp_path):
        f = add_days(CAL[13], 3)  # fill of rows decided the week before T; their expiries are >= T
        over = lambda name, d, *rest: 777.0 if (name == "AAA" and d == f) else None  # noqa: E731
        run(make(tmp_path, World(), end=T, out="clean"), tmp_path)
        run(make(tmp_path, World(quote_override=over), end=T, out="moved"), tmp_path)
        assert tree(tmp_path / "clean")["map-" + T + ".json"] == tree(tmp_path / "moved")["map-" + T + ".json"]
        assert checkpoint(tmp_path / "clean")["states"] == checkpoint(tmp_path / "moved")["states"]
        later = add_days(T, 14)  # the same rows settle before T + 14: now the quote matters
        run(make(tmp_path, World(), end=later, out="clean2"), tmp_path)
        run(make(tmp_path, World(quote_override=over), end=later, out="moved2"), tmp_path)
        assert tree(tmp_path / "clean2")[f"map-{later}.json"] != tree(tmp_path / "moved2")[f"map-{later}.json"]

    def test_a_settled_rows_fill_quote_moves_the_fill_state_only(self, tmp_path):
        f = add_days(CAL[10], 3)  # rows decided at CAL[10] expire CAL[12] / CAL[13] ... < T
        over = lambda name, d, *rest: 777.0 if (name == "AAA" and d == f) else None  # noqa: E731
        run(make(tmp_path, World(), end=T, out="clean"), tmp_path)
        run(make(tmp_path, World(quote_override=over), end=T, out="moved"), tmp_path)
        a = json.loads((tmp_path / "clean" / f"map-{T}.json").read_text())["states"]
        b = json.loads((tmp_path / "moved" / f"map-{T}.json").read_text())["states"]
        assert a["expanding_fill"] != b["expanding_fill"]
        assert a["decayed_fill"] != b["decayed_fill"]
        assert a["expanding_decision"] == b["expanding_decision"]
        assert a["decayed_decision"] == b["decayed_decision"]
        sa, sb = checkpoint(tmp_path / "clean")["states"], checkpoint(tmp_path / "moved")["states"]
        assert sa["expanding_decision"] == sb["expanding_decision"]

    def test_a_row_whose_fill_session_is_its_expiry_is_counted_and_never_admitted(self, tmp_path):
        t = CAL[5]
        monday = add_days(t, 3)
        extra = World().row("AAA", t, monday, "call", 100.0, 1)
        node = make(tmp_path, World(extra_rows={t: [extra]}))
        summary = run(node, tmp_path)["summary"]
        assert summary["counts"]["no_fill_quote.session"] >= 1
        assert summary["counts"]["no_fill_quote"] >= 1
        joined = [row for ev in node.events if ev[0] == "fill" for row in ev[1]]
        assert all(row[1] < row[0] for row in joined)  # a fill session is always before its settlement
        assert not any(row[3] == t and row[0] == monday for row in joined)

    def test_the_fill_quote_is_only_read_for_admitted_rows(self, tmp_path):
        node = make(tmp_path)
        run(node, tmp_path)
        for week, ev in _fill_with_week(node.events):
            for s, f, *_ in ev:
                assert s < week, f"settlement {s} joined at {week}"
                assert f < week, f"fill quote dated {f} read at {week}"


def _fill_with_week(events):
    out, held = [], []
    for ev in events:
        if ev[0] == "fill":
            held.append(ev[1])
        elif ev[0] == "decide":
            out.extend((ev[1], rows) for rows in held)
            held = []
    return out


class TestBoundary:
    def test_same_day_settlement_is_admitted_the_next_week(self, tmp_path):
        node = make(tmp_path)
        run(node, tmp_path)
        weeks = fill_weeks(node.events)
        for k in range(2, N_WEEKS - 1):
            assert weeks[CAL[k]] == CAL[k + 1], CAL[k]  # settles on week k's own close: not at k

    def test_a_thursday_expiry_is_admitted_at_the_next_friday(self, tmp_path):
        node = make(tmp_path)
        run(node, tmp_path)
        weeks = fill_weeks(node.events)
        for k in range(2, N_WEEKS):
            assert weeks[add_days(CAL[k], -1)] == CAL[k]

    def test_resuming_with_a_window_that_ends_before_the_cursor_refuses_state_from_future(self, tmp_path):
        run(make(tmp_path), tmp_path)
        node = make(tmp_path, end=CAL[10])
        with pytest.raises(ValueError, match="state_from_future") as err:
            run(node, tmp_path)
        assert err.value.code == "state_from_future"

    def test_a_pending_row_that_settles_at_or_before_its_decision_is_never_pending(self, tmp_path):
        t = CAL[5]
        row = World().row("AAA", t, add_days(t, -1), "call", 100.0, 1)
        node = make(tmp_path, World(extra_rows={t: [row]}))
        counts = run(node, tmp_path)["summary"]["counts"]
        assert counts["no_fill_quote.session"] >= 1


class TestSeed:
    def test_seed_rows_feed_the_state_but_no_reported_table(self, tmp_path):
        node = make(tmp_path)
        out = run(node, tmp_path)["summary"]
        decided = set()
        for p in sorted((tmp_path / "out" / "rows").glob("*.jsonl")):
            for line in p.read_text().splitlines():
                decided.add(json.loads(line)["t"])
        assert decided and min(decided) >= CAL[13]
        assert out["counts"]["rows_reported"] > 0
        seed_sum = sum(1 for ev in node.events if ev[0] == "fill")
        assert seed_sum > 0

    def test_removing_the_seed_changes_maps_but_not_reported_counts(self, tmp_path):
        with_seed = run(make(tmp_path, World(), out="a"), tmp_path)["summary"]
        without = run(make(tmp_path, World(no_rows_before=CAL[13]), out="b"), tmp_path)["summary"]
        assert with_seed["counts"]["rows_reported"] == without["counts"]["rows_reported"]
        assert tree(tmp_path / "a")[f"map-{CAL[16]}.json"] != tree(tmp_path / "b")[f"map-{CAL[16]}.json"]


# --------------------------------------------------------------------------- test 12


class TestResume:
    @pytest.mark.parametrize("every", [1, 4])
    def test_resume_is_byte_identical_to_an_uninterrupted_run(self, tmp_path, every):
        run(make(tmp_path, out="whole", checkpoint_every=every), tmp_path)
        broken = make(tmp_path, out="cut", fail_at=CAL[17], checkpoint_every=every)
        with pytest.raises(RuntimeError, match="interrupted"):
            run(broken, tmp_path)
        again = make(tmp_path, out="cut", checkpoint_every=every)
        run(again, tmp_path)
        assert tree(tmp_path / "whole") == tree(tmp_path / "cut")
        decided = [ev[1] for ev in again.events if ev[0] == "decide"]
        assert decided and decided[0] <= CAL[17] and decided[0] > CAL[0]  # resumed, not restarted

    def test_a_changed_param_refuses_checkpoint_mismatch(self, tmp_path):
        with pytest.raises(RuntimeError):
            run(make(tmp_path, fail_at=CAL[8]), tmp_path)
        for over in ({"half_life_weeks": 13}, {"buckets": {**EDGES, "spread_edges": [0.2]}},
                     {"pooling": {**POOL, "lcb_k": 2.0}}, {"report_from": CAL[12]}):
            with pytest.raises(ValueError, match="checkpoint_mismatch") as err:
                run(make(tmp_path, **over), tmp_path)
            assert err.value.code == "checkpoint_mismatch"

    def test_changed_input_pins_refuse_and_unchanged_pins_resume(self, tmp_path):
        pins = {"mio_document": "aa" * 32}
        with pytest.raises(RuntimeError):
            run(make(tmp_path, pins=pins, fail_at=CAL[8]), tmp_path)
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, pins={"mio_document": "bb" * 32}), tmp_path)
        run(make(tmp_path, pins=pins), tmp_path)

    def test_operational_knobs_may_change_on_resume(self, tmp_path):
        run(make(tmp_path, out="whole"), tmp_path)
        with pytest.raises(RuntimeError):
            run(make(tmp_path, out="cut", fail_at=CAL[8]), tmp_path)
        run(make(tmp_path, out="cut", ram_gb_limit=32, checkpoint_every=3), tmp_path)
        assert tree(tmp_path / "whole") == tree(tmp_path / "cut")

    def test_a_missing_pending_shard_refuses_checkpoint_mismatch(self, tmp_path):
        with pytest.raises(RuntimeError):
            run(make(tmp_path, fail_at=CAL[8]), tmp_path)
        shard = sorted((tmp_path / "out" / "pending").rglob("*.json"))[0]
        shard.unlink()
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path), tmp_path)


class TestSideRuleAndCounters:
    def test_one_row_per_option_the_larger_edge_side(self, tmp_path):
        node = make(tmp_path)
        counts = run(node, tmp_path)["summary"]["counts"]
        rows = [json.loads(line) for p in sorted((tmp_path / "out" / "rows").glob("*.jsonl"))
                for line in p.read_text().splitlines()]
        keys = [(r["t"], r["name"], r["expiry"], r["right"], r["strike"]) for r in rows]
        assert len(keys) == len(set(keys))
        world = World()
        for r in rows[:50]:
            mirror = [x for x in world.rows_at(r["t"]) if (x["name"], x["expiry"], x["right"], x["strike"])
                      == (r["name"], r["expiry"], r["right"], r["strike"])]
            assert r["e"] == max(x["e"] for x in mirror)
        assert counts["side_duplicate"] > 0

    def test_each_admitted_row_is_joined_once(self, tmp_path):
        node = make(tmp_path)
        counts = run(node, tmp_path)["summary"]["counts"]
        joined = [row for ev in node.events if ev[0] == "fill" for row in ev[1]]
        assert len(joined) == counts["rows_admitted"]
        assert len({(r[2], r[3], r[4], r[5], r[6]) for r in joined}) == len(joined)

    def test_counted_codes_are_never_silent(self, tmp_path):
        counts = run(make(tmp_path), tmp_path)["summary"]["counts"]
        for code in ("no_fill_quote.quote", "no_fill_quote.basis", "basis_changed", "no_settlement_bar",
                     "regime_warmup", "rows_decided", "rows_admitted", "rows_reported", "side_duplicate"):
            assert counts.get(code, 0) > 0, code
        assert counts["no_fill_quote"] == sum(v for k, v in counts.items() if k.startswith("no_fill_quote."))

    def test_the_calendar_is_read_once_and_a_bad_one_refuses(self, tmp_path):
        node = make(tmp_path)
        run(node, tmp_path)
        assert [ev for ev in node.events if ev[0] == "calendar"] == [("calendar",)]

        class Unsorted(SynthWalk):
            def _decision_calendar(self):
                return [CAL[1], CAL[0], *CAL[2:]]

        bad = Unsorted("walk", params(tmp_path / "x"), World())
        with pytest.raises(ValueError, match="calendar_mismatch") as err:
            run(bad, tmp_path)
        assert err.value.code == "calendar_mismatch"

    def test_a_fill_session_not_after_the_decision_refuses_calendar_mismatch(self, tmp_path):
        t = CAL[3]
        row = World().row("AAA", t, add_days(t, 14), "call", 100.0, 1, fill=t)
        with pytest.raises(ValueError, match="calendar_mismatch"):
            run(make(tmp_path, World(extra_rows={t: [row]})), tmp_path)

    def test_decay_is_two_to_the_minus_one_over_the_half_life(self, tmp_path):
        run(make(tmp_path, end=CAL[3]), tmp_path)
        states = checkpoint(tmp_path / "out")["states"]
        assert states["decayed_fill"]["state"]["decay"] == 2 ** (-1 / 26)
        assert states["expanding_fill"]["state"]["decay"] is None
        assert states["decayed_decision"]["adjust"]["decay"] == 2 ** (-1 / 26)


class TestDataRightsAndParams:
    def test_a_window_past_the_seal_refuses_before_any_hook_opens(self, tmp_path):
        node = make(tmp_path, window={"start": CAL[0], "end": "2021-07-01"})
        with pytest.raises(ValueError, match="window_outside_data_rights") as err:
            run(node, tmp_path)
        assert err.value.code == "window_outside_data_rights"
        assert node.events == []

    def test_sealed_from_is_required_and_capped(self, tmp_path):
        p = params(tmp_path / "x")
        del p["sealed_from"]
        with pytest.raises(ValueError, match="sealed_from_missing"):
            run(SynthWalk("walk", p, World()), tmp_path)
        with pytest.raises(ValueError, match="development_seal_exceeded"):
            run(make(tmp_path, sealed_from="2021-07-02"), tmp_path)

    def test_report_from_must_be_after_the_window_start(self, tmp_path):
        with pytest.raises(ValueError, match="report_from_invalid") as err:
            run(make(tmp_path, report_from=CAL[0]), tmp_path)
        assert err.value.code == "report_from_invalid"

    def test_bad_bucket_edges_refuse(self, tmp_path):
        node = make(tmp_path, buckets={**EDGES, "dte_edges": [22, 8]})
        with pytest.raises(ValueError, match="bucket_edges_invalid"):
            run(node, tmp_path)
        assert node.events == []

    def test_unknown_params_are_a_config_error(self, tmp_path):
        with pytest.raises(ConfigError):
            SynthWalk("walk", params(tmp_path / "x", surprise=1), World())

    def test_rows_settling_beyond_the_seal_are_dropped_and_counted(self, tmp_path):
        t = CAL[5]
        row = World().row("AAA", t, "2021-07-02", "call", 100.0, 1)
        counts = run(make(tmp_path, World(extra_rows={t: [row]})), tmp_path)["summary"]["counts"]
        assert counts["beyond_seal"] == 1


class TestResources:
    def test_ram_limit_refuses(self, tmp_path):
        with pytest.raises(ValueError, match="ram_limit") as err:
            run(make(tmp_path, ram_gb_limit=1e-9), tmp_path)
        assert err.value.code == "ram_limit"

    def test_every_write_is_atomic(self, tmp_path, monkeypatch):
        written = []
        real = ew.atomic_write

        def spy(path, raw):
            written.append(Path(path).resolve())
            return real(path, raw)

        monkeypatch.setattr(ew, "atomic_write", spy)
        run(make(tmp_path, end=CAL[10]), tmp_path)
        files = {p.resolve() for p in (tmp_path / "out").rglob("*") if p.is_file()}
        assert files and files <= set(written)
        assert not [p for p in files if ".tmp" in p.name]

    def test_a_failed_checkpoint_write_leaves_the_last_good_checkpoint(self, tmp_path, monkeypatch):
        real = ew.atomic_write
        state = {"n": 0}

        def flaky(path, raw):
            if Path(path).name == "checkpoint.json":
                state["n"] += 1
                if state["n"] == 6:
                    raise OSError("disk")
            return real(path, raw)

        monkeypatch.setattr(ew, "atomic_write", flaky)
        with pytest.raises(OSError):
            run(make(tmp_path), tmp_path)
        assert checkpoint(tmp_path / "out")["cursor"] == CAL[4]
        monkeypatch.setattr(ew, "atomic_write", real)
        run(make(tmp_path), tmp_path)
        run(make(tmp_path, out="whole"), tmp_path)
        assert tree(tmp_path / "out") == tree(tmp_path / "whole")

    def test_a_100k_row_week_is_not_held_in_memory(self, tmp_path):
        names = [f"N{k:04d}" for k in range(1000)]
        t, expiry = CAL[0], CAL[2]

        class Big(SynthWalk):
            def _decision_rows(self, tt):
                if tt != t:
                    return []
                return [{"group": f"{n}|{expiry}", "name": n, "expiry": expiry, "settle_date": expiry,
                         "fill_date": add_days(t, 3), "factor": 1.0, "right": "call", "strike": 50.0 + k,
                         "sign": 1, "e": 0.01 * k, "dte": 14.0, "abs_z": 0.5, "spread": 0.1, "v": 0.3,
                         "credit": -1.0, "multiplier": 100.0} for n in names for k in range(100)]

        node = Big("walk", params(tmp_path / "big", window={"start": t, "end": t}, report_from=CAL[1]), World())
        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        run(node, tmp_path)
        retained = tracemalloc.get_traced_memory()[0] - before
        tracemalloc.stop()
        assert retained < 3_000_000
        assert (tmp_path / "big" / "checkpoint.json").stat().st_size < 200_000
        assert sum(1 for _ in (tmp_path / "big" / "pending").rglob("*.json")) == 1


def test_public_surface_and_hooks():
    assert ew.__all__ == ["NODE_KINDS", "EdgeReliabilityWalk"]
    assert ew.NODE_KINDS == ()
    assert EdgeReliabilityWalk._BASE_PARAMS == (
        "window", "report_from", "sealed_from", "out_dir", "half_life_weeks", "buckets", "regime", "pooling",
        "name_adjust", "checkpoint_every", "ram_gb_limit")
    assert EdgeReliabilityWalk.__abstractmethods__ == frozenset(
        {"_decision_calendar", "_decision_rows", "_fill_credits", "_settlement_levels", "_next_session",
         "_input_pins"})
    with pytest.raises(TypeError):
        EdgeReliabilityWalk("walk", {})


# --------------------------------------------------------------------------- ADR-0311 fix round


def _left_fold(items, start=0):
    """Python 3.11's naive ``sum`` (3.12+ compensates): a strict left fold."""
    acc = start
    for v in items:
        acc = acc + v
    return acc


class TestFrozenResidual:
    """ADR test 7: the name sums fold ``r - c`` with the BUCKET-ONLY ``c`` stored at the row's own decision."""

    def _oracle(self, out, state_key, r_key, fill_state):
        root = Path(out)
        maps = {}
        sums = {}
        for path in sorted((root / "rows").glob("rows-*.jsonl")):
            for line in path.read_text().splitlines():
                row = json.loads(line)
                if row["code"] is not None:
                    continue
                if row["t"] not in maps:
                    maps[row["t"]] = json.loads((root / f"map-{row['t']}.json").read_text())["states"][state_key]
                bucket = maps[row["t"]]["buckets"][row["bucket"]]
                cb = bucket["a"] + bucket["b"] * row["e"]
                u = row[r_key] - cb
                rec = sums.setdefault(row["name"], [0.0] * 5)
                rec[0] += 1
                rec[1] += row["e"]
                rec[2] += u
                rec[3] += row["e"] ** 2
                rec[4] += row["e"] * u
        return sums

    @pytest.mark.parametrize("state_key,r_key", [("expanding_fill", "r"), ("expanding_decision", "r_dec")])
    def test_name_sums_equal_an_independent_bucket_only_recomputation(self, tmp_path, state_key, r_key):
        run(make(tmp_path, World(no_rows_before=CAL[1]), report_from=CAL[1]), tmp_path)
        out = tmp_path / "out"
        warm_names = [m for p in sorted(out.glob("map-*.json"))
                      for m in json.loads(p.read_text())["states"][state_key]["names"].values()
                      if any(abs(x) > 1e-9 for x in m)]
        assert warm_names, "the world never produced a nonzero name adjustment: the test would be vacuous"
        want = self._oracle(out, state_key, r_key, state_key)
        got = checkpoint(out)["states"][state_key]["adjust"]["names"]
        assert set(got) == set(want)
        for name, rec in want.items():
            assert got[name]["s"] == pytest.approx(rec, rel=1e-9, abs=1e-9)

    def test_swapping_in_the_name_adjusted_value_is_caught(self, tmp_path, monkeypatch):
        real = ew.EdgeReliabilityWalk._fold

        def swapped(self, t, s, name, cells):
            return real(self, t, s, name, [(c[0][:13] + [c[0][14]] + c[0][14:], *c[1:]) for c in cells])

        run(make(tmp_path, World(no_rows_before=CAL[1]), report_from=CAL[1], out="good"), tmp_path)
        monkeypatch.setattr(ew.EdgeReliabilityWalk, "_fold", swapped)
        run(make(tmp_path, World(no_rows_before=CAL[1]), report_from=CAL[1], out="bad"), tmp_path)
        good = checkpoint(tmp_path / "good")["states"]["expanding_fill"]["adjust"]["names"]
        bad = checkpoint(tmp_path / "bad")["states"]["expanding_fill"]["adjust"]["names"]
        assert good != bad  # the stored bucket-only value and the name-adjusted one really differ here


class TestDeterminism:
    def test_resume_is_byte_identical_under_a_naive_float_sum(self, tmp_path, monkeypatch):
        import builtins

        with monkeypatch.context() as m:
            m.setattr(builtins, "sum", _left_fold)
            run(make(tmp_path, out="whole"), tmp_path)
            with pytest.raises(RuntimeError):
                run(make(tmp_path, out="cut", fail_at=CAL[17]), tmp_path)
            run(make(tmp_path, out="cut"), tmp_path)
        assert tree(tmp_path / "whole") == tree(tmp_path / "cut")


class TestSummaryManifest:
    def test_the_digest_covers_only_files_this_run_wrote(self, tmp_path):
        clean = run(make(tmp_path, out="clean"), tmp_path)["summary"]
        dirty = tmp_path / "dirty"
        (dirty / "rows").mkdir(parents=True)
        (dirty / "map-2099-01-01.json").write_text("{}")
        (dirty / "rows" / "rows-2099-01-01.jsonl").write_text("{}\n")
        again = run(make(tmp_path, out="dirty"), tmp_path)["summary"]
        assert again["output_sha256"] == clean["output_sha256"]

    def test_the_digest_survives_a_resume(self, tmp_path):
        whole = run(make(tmp_path, out="whole"), tmp_path)["summary"]
        with pytest.raises(RuntimeError):
            run(make(tmp_path, out="cut", fail_at=CAL[17]), tmp_path)
        (tmp_path / "cut" / "map-2099-01-01.json").write_text("{}")
        cut = run(make(tmp_path, out="cut"), tmp_path)["summary"]
        assert cut["output_sha256"] == whole["output_sha256"]


    def test_a_missing_written_file_refuses_checkpoint_mismatch(self, tmp_path):
        with pytest.raises(RuntimeError):
            run(make(tmp_path, fail_at=CAL[8]), tmp_path)
        (tmp_path / "out" / f"map-{CAL[2]}.json").unlink()
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path), tmp_path)


class TestNextSession:
    def test_a_fill_session_that_is_not_the_next_session_refuses(self, tmp_path):
        t = CAL[5]
        late = World().row("AAA", t, add_days(t, 14), "call", 100.0, 1, fill=add_days(t, 4))
        with pytest.raises(ValueError, match="fill_not_next_session") as err:
            run(make(tmp_path, World(extra_rows={t: [late]})), tmp_path)
        assert err.value.code == "fill_not_next_session"

    def test_the_next_session_is_asked_once_per_decision(self, tmp_path):
        node = make(tmp_path, end=CAL[6])
        run(node, tmp_path)
        asked = [ev[1] for ev in node.events if ev[0] == "next_session"]
        assert asked == CAL[:7]


class TestRamPeaks:
    def test_ram_is_checked_after_the_settle_join_and_the_decide_build(self, tmp_path, monkeypatch):
        import sys

        callers = []

        def spy(self_or_none=None):
            callers.append(sys._getframe(2).f_code.co_name)
            return 0.0

        monkeypatch.setattr(EdgeReliabilityWalk, "_rss_gb", staticmethod(spy))
        run(make(tmp_path, end=CAL[6]), tmp_path)
        assert {"_week", "_admit", "_decide"} <= set(callers)
        assert callers.count("_decide") == 2 * 7  # after the hook returns and after the shards are built


class TestCheckpointVersion:
    def test_a_checkpoint_of_another_version_refuses(self, tmp_path):
        run(make(tmp_path, end=CAL[6]), tmp_path)
        path = tmp_path / "out" / "checkpoint.json"
        blob = json.loads(path.read_text())
        blob["version"] = ew._VERSION + 1
        path.write_text(json.dumps(blob))
        with pytest.raises(ValueError, match="checkpoint_mismatch") as err:
            run(make(tmp_path, end=CAL[8]), tmp_path)
        assert err.value.code == "checkpoint_mismatch"
        blob["version"] = ew._VERSION - 1
        path.write_text(json.dumps(blob))
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, end=CAL[8]), tmp_path)

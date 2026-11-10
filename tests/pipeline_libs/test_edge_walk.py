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
DIR_STATES = tuple("dir_" + k for k in STATES)
EXTRA_STATES = ("reported_fill", "reported_dir", "forward", "fill_sensitivity", "reported_sensitivity", "dollar")
FOURTEEN = STATES + DIR_STATES + EXTRA_STATES
EDGES = {"dte_edges": [8, 22], "abs_z_edges": [1.0], "spread_edges": [0.15]}
POOL = {"min_clusters": 2, "min_dates": 2, "tau2_floor": 1e-6, "weight_cap": 1.0, "lcb_k": 1.0, "band": "two_block",
        "min_blocks": 2, "ci_levels": [0.90, 0.95]}
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
                 no_rows_before=None, mult_factor=1.0, skip_name_before=None):
        self.mult_factor = mult_factor
        self.skip_name_before = skip_name_before or {}
        self.poison_from = poison_from
        self.delete_from = delete_from
        self.quote_override = quote_override
        self.extra_rows = extra_rows or {}
        self.no_rows_before = no_rows_before

    def _bad(self, d):
        return self.poison_from is not None and d >= self.poison_from

    def _check_alive(self, d):
        assert self.delete_from is None or d < self.delete_from, f"read data dated {d}, which is deleted"

    def multiplier(self, name):
        return 100.0 * self.mult_factor if name == "AAA" else 100.0

    @staticmethod
    def spot(name):
        return {"AAA": 50.0, "BBB": 100.0, "CCC": 200.0}[name]

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
        mult = self.multiplier(name)
        e = (x if sign > 0 else -x - 1.3) * mult / 100.0
        credit = -sign * price * mult - 0.65 * mult / 100.0
        e_dir = 0.5 * e + (u("d", *key) - 0.5) * mult / 100.0
        code = None
        if u("nd", *key) < 0.03:
            e_dir, code = None, "leg_no_iv"
        sigma2 = 1e-6 * (1.0 + u("sg", *key))
        scale = self.spot(name) * mult
        if self._bad(t):
            e, credit, v, spread = 1e9, -1e9, 1e6, 0.99
            e_dir = None if e_dir is None else 1e9
        return {
            "group": f"{name}|{t}|{expiry}", "name": name, "expiry": expiry, "settle_date": expiry,
            "fill_date": fill or add_days(t, 3), "factor": self.factor(name, t), "right": right,
            "strike": strike, "sign": sign, "e": e, "dte": float((datetime.date.fromisoformat(expiry)
                                                                 - datetime.date.fromisoformat(t)).days),
            "abs_z": ABS_Z[strike], "spread": spread, "v": v, "credit": credit, "multiplier": mult,
            "e_dir": e_dir, "direction_code": code, "sigma2": sigma2, "scale": scale, "mid": price,
            "fee": 0.65, "cdf_repaired": False,
        }

    def rows_at(self, t):
        if self.delete_from is not None and t >= self.delete_from:
            return []
        if self.no_rows_before is not None and t < self.no_rows_before:
            return []
        rows = []
        for name in NAMES:
            if t < self.skip_name_before.get(name, ""):
                continue
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
        if u("size", *key) < 0.03:
            return "size"
        price = 1.0 + 4.0 * u("q", *key)
        if self.quote_override is not None:
            price = self.quote_override(*key) or price
        if self._bad(d):
            price = 1e6
        return -r["sign"] * price * r["multiplier"] - 0.65 * r["multiplier"] / 100.0, price

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
        "direction_regressor": True,
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
    """The map files up to ``last``; the ``regimes`` of ``last`` itself read its own decision rows, so they are set aside."""
    out = {}
    for k, v in tree(root).items():
        if k.startswith("map-") and k[4:14] <= last:
            if k[4:14] == last:
                blob = json.loads(v)
                blob.pop("regimes")
                v = json.dumps(blob, sort_keys=True).encode()
            out[k] = v
    return out


def manifest(root):
    return json.loads((Path(root) / "checkpoint.json").read_text())


def checkpoint(root):
    """The manifest plus the decoded arrays in the shape ``states[name] = {"state", "adjust"}``."""
    blob = manifest(root)
    tree = ew._npz_tree((Path(root) / blob["npz"]["file"]).read_bytes())
    blob["states"] = {k: {"state": v, "adjust": tree["adjust"].get(k)} for k, v in tree["states"].items()}
    blob["regime"] = tree["regime"]
    return blob


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
        assert set(checkpoint(tmp_path / "clean")["states"]) == set(FOURTEEN)

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
                        {k: [st.to_dict(), walk._adjust[k].to_dict() if k in walk._adjust else None]
                     for k, st in walk._states.items()}, sort_keys=True))
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
                         "credit": -1.0, "multiplier": 100.0, "scale": 1e4, "e_dir": 0.0, "direction_code": None} for n in names for k in range(100)]

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
        "name_adjust", "checkpoint_every", "ram_gb_limit", "direction_regressor", "edge_scale", "row_weight",
        "time_block_weeks", "min_fill_rate", "min_fill_sample", "ci_level", "bh_q", "bootstrap_draws",
        "bootstrap_seed", "tail_level_floor", "b0_floor", "hac_lags")
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
                e = row["e"] / row["scale"]
                cb = bucket["a"] + bucket["b"] * e
                u = row[r_key] / row["scale"] - cb
                w = row["w"]
                rec = sums.setdefault(row["name"], [0.0] * 5)
                rec[0] += w
                rec[1] += w * e
                rec[2] += w * u
                rec[3] += w * e ** 2
                rec[4] += w * e * u
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
            return real(self, t, s, name, [(c[0][:ew._CB] + [c[0][ew._CF]] + c[0][ew._CB + 1:], *c[1:])
                                           for c in cells])

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
        assert callers.count("_decide") == 3 * 7  # before the hook, after it returns, after the shards are built


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


# --------------------------------------------------------------------------- ADR-0311 A1 F3.1: states and params


def captured_states(tmp_path, at=8, **over):
    seen = {}

    def hook(walk, t):
        if t == CAL[at]:
            seen["states"] = dict(walk._states)
            seen["adjust"] = dict(walk._adjust)

    run(make(tmp_path, end=CAL[at], on_decide=hook, **over), tmp_path)
    return seen


class TestStatesAndParams:
    def test_fourteen_states_with_direction_and_nine_without(self, tmp_path):
        seen = captured_states(tmp_path, out="a")
        assert tuple(seen["states"]) == FOURTEEN and len(seen["states"]) == 14
        assert set(seen["adjust"]) == set(STATES)  # the name adjustment belongs to the one-regressor primary map
        seen = captured_states(tmp_path, out="b", direction_regressor=False)
        assert tuple(seen["states"]) == STATES + tuple(k for k in EXTRA_STATES if k != "reported_dir")
        assert len(seen["states"]) == 9
        assert set(checkpoint(tmp_path / "b")["states"]) == set(seen["states"])

    def test_state_shapes_follow_the_state_list(self, tmp_path):
        st = captured_states(tmp_path)["states"]
        for key, state in st.items():
            assert state.regressors == (2 if key.startswith("dir_") or key == "reported_dir" else 1), key
            assert state.block_lengths == 2, key
            assert (state.decay is not None) == ("decayed" in key), key

    def test_defaults_are_the_amendments(self, tmp_path):
        node = make(tmp_path)
        node.params.pop("checkpoint_every")
        assert node._param("time_block_weeks") == [4, 13]
        assert node._param("hac_lags") == 2
        assert node._param("b0_floor") == node._param("tail_level_floor") == 0.5
        assert node._param("min_fill_rate") == 0.9 and node._param("min_fill_sample") == 20
        assert node._param("ci_level") == 0.95 and node._param("bh_q") == 0.10
        assert node._param("bootstrap_draws") == 999 and node._param("bootstrap_seed") == 20261109
        assert node._param("edge_scale") == "notional" and node._param("row_weight") == "group_equal"
        assert node._param("checkpoint_every") == 4

    @pytest.mark.parametrize("over,code", [
        ({"edge_scale": "bogus"}, "edge_scale_invalid"),
        ({"row_weight": "bogus"}, "row_weight_invalid"),
        ({"time_block_weeks": []}, "time_block_invalid"),
        ({"time_block_weeks": [13, 4]}, "time_block_invalid"),
        ({"time_block_weeks": [4, 4]}, "time_block_invalid"),
        ({"time_block_weeks": [4, 1.5]}, "time_block_invalid"),
        ({"time_block_weeks": [0, 4]}, "time_block_invalid"),
        ({"time_block_weeks": [2, 4]}, "time_block_no_quarter"),
        ({"pooling": {**POOL, "band": "three"}}, "band_invalid"),
        ({"pooling": {**POOL, "min_blocks": 1}}, "min_blocks_invalid"),
        ({"pooling": {**POOL, "ci_levels": [1.2]}}, "ci_levels_invalid"),
        ({"pooling": {**POOL, "ci_levels": []}}, "ci_levels_invalid"),
        ({"ci_level": 1.0}, "ci_level_invalid"),
        ({"bh_q": 0.0}, "bh_q_invalid"),
        ({"min_fill_rate": 0.0}, "min_fill_rate_invalid"),
        ({"min_fill_rate": 1.5}, "min_fill_rate_invalid"),
        ({"min_fill_sample": 1}, "min_fill_sample_invalid"),
        ({"bootstrap_draws": 98}, "bootstrap_invalid"),
        ({"bootstrap_seed": 1.5}, "bootstrap_invalid"),
        ({"tail_level_floor": 0.0}, "tail_level_floor_invalid"),
        ({"b0_floor": 1.5}, "b0_floor_invalid"),
        ({"hac_lags": -1}, "hac_lags_invalid"),
    ])
    def test_each_bad_knob_refuses_with_its_code_before_any_hook(self, tmp_path, over, code):
        node = make(tmp_path, **over)
        with pytest.raises(ValueError, match=code) as err:
            run(node, tmp_path)
        assert err.value.code == code
        assert node.events == []

    @pytest.mark.parametrize("over", [
        {"direction_regressor": False}, {"edge_scale": "none"}, {"row_weight": "none"},
        {"time_block_weeks": [4, 13, 26]}, {"min_fill_rate": 0.8}, {"min_fill_sample": 10}, {"ci_level": 0.9},
        {"bh_q": 0.2}, {"bootstrap_draws": 500}, {"bootstrap_seed": 7}, {"tail_level_floor": 0.4},
        {"b0_floor": 0.4}, {"hac_lags": 3},
    ])
    def test_every_new_knob_is_part_of_the_params_hash(self, tmp_path, over):
        run(make(tmp_path, fail_at=CAL[8]) if False else make(tmp_path, end=CAL[6]), tmp_path)
        with pytest.raises(ValueError, match="checkpoint_mismatch") as err:
            run(make(tmp_path, end=CAL[8], **over), tmp_path)
        assert err.value.code == "checkpoint_mismatch"

    def test_an_absent_knob_and_its_default_hash_alike(self, tmp_path):
        run(make(tmp_path, end=CAL[6]), tmp_path)
        run(make(tmp_path, end=CAL[8], hac_lags=2, bh_q=0.10), tmp_path)  # equal to the defaults: resumes

    def test_a_checkpoint_of_another_version_names_both_versions(self, tmp_path):
        run(make(tmp_path, end=CAL[6]), tmp_path)
        path = tmp_path / "out" / "checkpoint.json"
        blob = json.loads(path.read_text())
        blob["version"] = 2
        path.write_text(json.dumps(blob))
        with pytest.raises(ValueError, match="checkpoint_mismatch") as err:
            run(make(tmp_path, end=CAL[8]), tmp_path)
        assert "version 2" in str(err.value) and f"version {ew._VERSION}" in str(err.value)
        assert ew._VERSION == 6


class TestRowFeeds:
    def test_a_row_without_a_direction_edge_leaves_only_the_two_regressor_states(self, tmp_path):
        counts = run(make(tmp_path), tmp_path)["summary"]["counts"]
        assert counts["no_direction_edge"] > 0
        assert counts["no_direction_edge"] == sum(v for k, v in counts.items() if k.startswith("no_direction_edge."))
        assert counts["no_direction_edge.leg_no_iv"] == counts["no_direction_edge"]
        seen = captured_states(tmp_path, out="seen")
        a, b = seen["states"]["expanding_fill"].fit(None), seen["states"]["dir_expanding_fill"].fit(None)
        assert 0 < b["rows"] < a["rows"]

    def test_with_direction_off_the_primary_states_are_unchanged(self, tmp_path):
        on = captured_states(tmp_path, out="on")["states"]
        off = captured_states(tmp_path, out="off", direction_regressor=False)["states"]
        for key in STATES + ("dollar",):
            assert on[key].to_dict() == off[key].to_dict(), key

    def test_side_strata_partition_every_state(self, tmp_path):
        for key, state in captured_states(tmp_path)["states"].items():
            buy, sell, pooled = state.fit("buy"), state.fit("sell"), state.fit(None)
            assert buy["rows"] + sell["rows"] == pooled["rows"], key
            assert buy["n"] + sell["n"] == pytest.approx(pooled["n"], rel=1e-12), key

    def test_group_equal_gives_each_group_weight_one_and_none_gives_rows(self, tmp_path):
        eq = captured_states(tmp_path, out="eq")["states"]["dollar"].fit(None)
        one = captured_states(tmp_path, out="one", row_weight="none")["states"]["dollar"].fit(None)
        assert one["n"] == one["rows"]
        assert eq["rows"] == one["rows"]
        assert eq["n"] == pytest.approx(eq["rows"] / 6, rel=1e-12)  # six rows per group after the side rule
        assert eq["n_groups"] == pytest.approx(eq["rows"] / 6, rel=1e-12)

    def test_the_top_unit_belongs_to_the_reported_states_that_need_it(self, tmp_path):
        st = captured_states(tmp_path, at=22)["states"]
        top, rep = st["reported_fill"].fit("top"), st["reported_fill"].fit(None)
        assert 0 < top["rows"] < rep["rows"]
        assert top["rows"] <= 0.2 * rep["rows"]
        assert st["expanding_fill"].fit("top")["rows"] == 0

    def test_reported_states_hold_reported_rows_only(self, tmp_path):
        st = captured_states(tmp_path, at=22)["states"]
        assert 0 < st["reported_fill"].fit(None)["rows"] < st["expanding_fill"].fit(None)["rows"]
        assert st["reported_dir"].fit(None)["rows"] <= st["reported_fill"].fit(None)["rows"]

    def test_blocks_are_the_settlement_date_blocks_of_both_lengths(self, tmp_path):
        node = make(tmp_path, end=CAL[10])
        run(node, tmp_path)
        joined = {row[0] for ev in node.events if ev[0] == "fill" for row in ev[1]}
        start = datetime.date.fromisoformat(CAL[0])
        for idx, weeks in enumerate((4, 13)):
            want = {((datetime.date.fromisoformat(s) - start).days // (7 * weeks)) for s in joined}
            state = checkpoint(tmp_path / "out")["states"]["dollar"]["state"]["pooled"]
            assert len(state["blocks"][idx]) == len(want), weeks
            assert {int(k) for k in state["blocks"][idx]} == want, weeks

    def test_scaling_one_name_leaves_the_notional_slope_and_moves_the_dollar_one(self, tmp_path):
        base = captured_states(tmp_path, out="b0", World=None) if False else None
        del base

        def fits(world, out):
            seen = {}

            def hook(walk, t):
                if t == CAL[12]:
                    seen["f"] = {k: walk._states[k].fit(None)["b"] for k in ("expanding_fill", "dollar")}

            run(make(tmp_path, world, end=CAL[12], out=out, on_decide=hook), tmp_path)
            return seen["f"]

        a, b = fits(World(), "s1"), fits(World(mult_factor=7.0), "s7")
        assert a["expanding_fill"] == pytest.approx(b["expanding_fill"], rel=1e-9)
        assert abs(a["dollar"] - b["dollar"]) > 1e-3


# --------------------------------------------------------------------------- F3.2: numpy cluster sums


def _all_state_dicts(tmp_path, out, **over):
    seen = {}

    def hook(walk, t):
        if t == CAL[20]:
            seen["s"] = {k: st.to_dict() for k, st in walk._states.items()}
            seen["a"] = {k: a.to_dict() for k, a in walk._adjust.items()}

    run(make(tmp_path, end=CAL[20], out=out, on_decide=hook, **over), tmp_path)
    return seen


def _close(a, b, path=""):
    if isinstance(a, dict):
        assert a.keys() == b.keys(), path
        for k in a:
            _close(a[k], b[k], f"{path}/{k}")
    elif isinstance(a, (list, tuple)):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]")
    elif isinstance(a, float):
        assert a == pytest.approx(b, rel=1e-12, abs=1e-12), path
    else:
        assert a == b, path


class TestNumpySums:
    def test_the_walk_folds_through_add_cluster_sums_not_add_cluster(self, tmp_path, monkeypatch):
        from dskit.pipeline.edge_reliability import LinearEdgeState

        calls = {"rows": 0, "sums": 0}
        rows_real, sums_real = LinearEdgeState.add_cluster, LinearEdgeState.add_cluster_sums

        def rows(self, *a, **k):
            calls["rows"] += 1
            return rows_real(self, *a, **k)

        def sums(self, *a, **k):
            calls["sums"] += 1
            return sums_real(self, *a, **k)

        monkeypatch.setattr(LinearEdgeState, "add_cluster", rows)
        monkeypatch.setattr(LinearEdgeState, "add_cluster_sums", sums)
        run(make(tmp_path, end=CAL[10]), tmp_path)
        assert calls["rows"] == 0 and calls["sums"] > 0

    @pytest.mark.parametrize("over", [{}, {"row_weight": "none"}, {"edge_scale": "none"}])
    def test_the_numpy_path_equals_the_stdlib_twin_to_1e_12_in_every_state(self, tmp_path, monkeypatch, over):
        fast = _all_state_dicts(tmp_path, "fast", **over)
        monkeypatch.setattr(EdgeReliabilityWalk, "_numpy_sums", False)
        slow = _all_state_dicts(tmp_path, "slow", **over)
        assert set(fast["s"]) == set(FOURTEEN)
        _close(fast["s"], slow["s"])
        _close(fast["a"], slow["a"])
        assert any(st["pooled"]["rows"] > 0 for st in fast["s"].values())

    def test_a_100k_row_week_is_admitted_with_bounded_retained_memory(self, tmp_path):
        names = [f"N{k:04d}" for k in range(1000)]
        t, expiry = CAL[0], CAL[1]

        class Big(SynthWalk):
            def _decision_rows(self, tt):
                if tt != t:
                    return []
                return [{"group": f"{n}|{expiry}|{k // 6}", "name": n, "expiry": expiry, "settle_date": expiry,
                         "fill_date": add_days(t, 3), "factor": 1.0, "right": "call", "strike": 50.0 + k,
                         "sign": 1, "e": 0.01 * k, "dte": 14.0, "abs_z": 0.5, "spread": 0.1, "v": 0.3,
                         "credit": -1.0, "multiplier": 100.0, "scale": 1e4, "e_dir": 0.001 * k,
                         "direction_code": None} for n in names for k in range(100)]

            def _fill_credits(self, rows):
                return [-100.0 * (1 + 0.001 * r["strike"]) for r in rows]

            def _settlement_levels(self, rows):
                return [100.0 + r["strike"] % 7 for r in rows]

            def _next_session(self, tt):
                return add_days(tt, 3)

        class Short(Big):
            pass

        # settle date must follow the fill session: expiry two weeks out
        expiry = add_days(t, 14)
        node = Short("walk", params(tmp_path / "big", window={"start": t, "end": CAL[3]}, report_from=CAL[1],
                                    direction_regressor=False), World())
        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        out = run(node, tmp_path)["summary"]
        retained = tracemalloc.get_traced_memory()[0] - before
        tracemalloc.stop()
        assert out["counts"]["rows_admitted"] == 100_000
        assert retained < 60_000_000


# --------------------------------------------------------------------------- F3.3: .npz checkpoint


def npz_files(root):
    return sorted(Path(root).glob("checkpoint-*.npz"))


class TestNpzCheckpoint:
    def test_one_manifest_and_one_npz_holding_the_state_arrays(self, tmp_path):
        import numpy as np

        run(make(tmp_path, end=CAL[12]), tmp_path)
        files = npz_files(tmp_path / "out")
        assert len(files) == 1
        blob = manifest(tmp_path / "out")
        assert blob["npz"]["file"] == files[0].name
        assert blob["npz"]["sha256"] == hashlib.sha256(files[0].read_bytes()).hexdigest()
        assert "states" not in blob and (tmp_path / "out" / "checkpoint.json").stat().st_size < 50_000
        with np.load(files[0], allow_pickle=False) as z:
            assert set(z.files) == {"floats", "ints", "tree"}
            assert z["floats"].dtype == np.float64 and len(z["floats"]) > 1000

    def test_every_four_weeks_writes_at_weeks_4_8_and_keeps_one(self, tmp_path):
        seen = []

        def hook(walk, t):
            seen.append((t, sorted(p.name for p in Path(walk._out).glob("checkpoint*"))))

        run(make(tmp_path, end=CAL[12], checkpoint_every=4, on_decide=hook), tmp_path)
        by_week = dict(seen)
        assert by_week[CAL[3]] == []                                  # nothing before the first checkpoint
        assert by_week[CAL[4]] == ["checkpoint-" + CAL[3] + ".npz", "checkpoint.json"]   # after the 4th week
        assert by_week[CAL[7]] == ["checkpoint-" + CAL[3] + ".npz", "checkpoint.json"]
        assert by_week[CAL[8]] == ["checkpoint-" + CAL[7] + ".npz", "checkpoint.json"]   # the older one is gone
        assert all(len([n for n in names if n.endswith(".npz")]) <= 1 for names in by_week.values())

    def test_consumed_shards_go_only_after_the_new_checkpoint_is_durable(self, tmp_path, monkeypatch):
        real = ew.atomic_write
        marks = {}

        def spy(path, raw):
            if Path(path).name == "checkpoint.json":
                marks["shards_at_manifest_write"] = len(list((tmp_path / "out" / "pending").rglob("*.json")))
            return real(path, raw)

        monkeypatch.setattr(ew, "atomic_write", spy)
        run(make(tmp_path, end=CAL[12], checkpoint_every=4), tmp_path)
        monkeypatch.setattr(ew, "atomic_write", real)
        assert marks["shards_at_manifest_write"] > sum(len(v) for v in manifest(tmp_path / "out")["pending"].values())

    def test_a_crash_between_the_npz_and_the_manifest_leaves_the_previous_checkpoint_readable(
            self, tmp_path, monkeypatch):
        real = ew.atomic_write
        state = {"n": 0}

        def flaky(path, raw):
            if Path(path).name == "checkpoint.json":
                state["n"] += 1
                if state["n"] == 3:
                    raise OSError("disk")
            return real(path, raw)

        monkeypatch.setattr(ew, "atomic_write", flaky)
        with pytest.raises(OSError):
            run(make(tmp_path, checkpoint_every=2), tmp_path)
        assert (tmp_path / "out" / manifest(tmp_path / "out")["npz"]["file"]).exists()
        monkeypatch.setattr(ew, "atomic_write", real)
        run(make(tmp_path, checkpoint_every=2), tmp_path)
        run(make(tmp_path, out="whole", checkpoint_every=2), tmp_path)
        assert tree(tmp_path / "out") == tree(tmp_path / "whole")
        assert len(npz_files(tmp_path / "out")) == 1

    def _broken(self, tmp_path):
        with pytest.raises(RuntimeError):
            run(make(tmp_path, fail_at=CAL[9], checkpoint_every=4), tmp_path)
        return tmp_path / "out"

    def test_a_tampered_npz_refuses(self, tmp_path):
        out = self._broken(tmp_path)
        f = npz_files(out)[0]
        raw = bytearray(f.read_bytes())
        raw[len(raw) // 2] ^= 0xFF
        f.write_bytes(bytes(raw))
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, checkpoint_every=4), tmp_path)

    def test_a_missing_npz_refuses(self, tmp_path):
        out = self._broken(tmp_path)
        npz_files(out)[0].unlink()
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, checkpoint_every=4), tmp_path)

    @pytest.mark.parametrize("key", ["npz", "pending", "manifest", "counts", "pins", "params_hash", "cursor"])
    def test_a_manifest_lacking_any_key_refuses(self, tmp_path, key):
        out = self._broken(tmp_path)
        blob = json.loads((out / "checkpoint.json").read_text())
        del blob[key]
        (out / "checkpoint.json").write_text(json.dumps(blob))
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, checkpoint_every=4), tmp_path)

    def test_the_manifest_content_hash_catches_an_edited_map_file(self, tmp_path):
        out = self._broken(tmp_path)
        m = out / f"map-{CAL[1]}.json"
        m.write_text(m.read_text().replace("0", "1", 1))
        with pytest.raises(ValueError, match="checkpoint_mismatch") as err:
            run(make(tmp_path, checkpoint_every=4), tmp_path)
        assert "content" in str(err.value)

    def test_the_content_hash_catches_an_edited_pending_shard(self, tmp_path):
        out = self._broken(tmp_path)
        shard = sorted((out / "pending").rglob("*.json"))[0]
        shard.write_text(shard.read_text().replace("0", "9", 1))
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, checkpoint_every=4), tmp_path)

    @pytest.mark.parametrize("every", [1, 4])
    def test_resume_is_byte_identical_with_unsorted_arrival(self, tmp_path, every):
        late = {"AAA": CAL[6], "BBB": CAL[3]}
        run(make(tmp_path, World(skip_name_before=late), out="whole", checkpoint_every=every), tmp_path)
        with pytest.raises(RuntimeError):
            run(make(tmp_path, World(skip_name_before=late), out="cut", fail_at=CAL[17], checkpoint_every=every),
                tmp_path)
        run(make(tmp_path, World(skip_name_before=late), out="cut", checkpoint_every=every), tmp_path)
        assert tree(tmp_path / "whole") == tree(tmp_path / "cut")

    def test_the_npz_bytes_do_not_depend_on_the_clock(self, tmp_path):
        import time

        run(make(tmp_path, end=CAL[8], out="a"), tmp_path)
        time.sleep(2.1)
        run(make(tmp_path, end=CAL[8], out="b"), tmp_path)
        assert npz_files(tmp_path / "a")[0].read_bytes() == npz_files(tmp_path / "b")[0].read_bytes()


# --------------------------------------------------------------------------- F3.4: fill sensitivity (D2)


def cluster_calls(tmp_path, out="sens", end=CAL[-1], **over):
    """Run the walk and log every ``add_cluster_sums`` call on the sensitivity state (t, name, s)."""
    from dskit.pipeline.edge_reliability import LinearEdgeState

    calls = []
    real = LinearEdgeState.add_cluster_sums

    def spy(self, t, name, s, f, blocks, sums):
        if self is node_box["node"]._states.get("fill_sensitivity"):
            calls.append((t, name, s))
        return real(self, t, name, s, f, blocks, sums)

    node_box = {}
    node = make(tmp_path, out=out, end=end, **over)
    node_box["node"] = node
    LinearEdgeState.add_cluster_sums = spy
    try:
        res = run(node, tmp_path)["summary"]
    finally:
        LinearEdgeState.add_cluster_sums = real
    return node, calls, res


class TestFillSensitivity:
    def test_q95_by_hand_pooled_fallback_and_the_imputed_credit(self):
        vals = [0.01 * k for k in range(20)] + [0.5, 0.6, 0.7]
        buckets = [0] * 20 + [1] * 3
        got = EdgeReliabilityWalk._freeze_q95(buckets, vals, 20)
        assert got["b"] == {"0": pytest.approx(0.18 + 0.05 * 0.01, abs=1e-12)}   # p = 0.95 * 19 = 18.05
        ordered = sorted(vals)
        p = 0.95 * 22
        assert got["pool"] == pytest.approx(ordered[21] + (p - 21) * (ordered[22] - ordered[21]), abs=1e-12)
        imp = EdgeReliabilityWalk._imputed_credit
        assert imp(1, 2.0, 0.10, 100.0, 0.65) == pytest.approx(-2.2 * 100 - 0.65)      # buy at mid (1 + q)
        assert imp(-1, 2.0, 0.10, 100.0, 0.65) == pytest.approx(1.8 * 100 - 0.65)      # sell at mid (1 - q)
        assert imp(-1, 2.0, 1.5, 100.0, 0.65) == pytest.approx(-0.65)                  # floored at a zero price

    def test_a_cluster_enters_the_sensitivity_state_whole_once_after_every_contributing_week_is_complete(
            self, tmp_path):
        node, calls, _ = cluster_calls(tmp_path)
        assert len(calls) == len(set((n, s) for _, n, s in calls)) > 50     # once each
        latest = {}
        for ev in node.events:
            if ev[0] == "fill":
                for row in ev[1]:
                    latest[row[3]] = max(latest.get(row[3], ""), row[0])      # decision week -> last settlement
        weeks_of = {}
        for ev in node.events:
            if ev[0] == "fill":
                for s, f, name, dec, *_ in ev[1]:
                    weeks_of.setdefault((name, s), set()).add(dec)
        for t, name, s in calls:
            assert s < t
            assert all(latest[w] < t for w in weeks_of[(name, s)]), (t, name, s)

    def test_session_and_basis_rows_enter_no_sensitivity_state(self, tmp_path):
        node, calls, res = cluster_calls(tmp_path)
        c = res["counts"]
        sens = node._states["fill_sensitivity"].fit(None)["rows"]
        excluded = (c["no_fill_quote.basis"] + c["basis_changed"] + c["no_settlement_bar"]
                    + c.get("fill_unimputable", 0))
        assert sens + c.get("imputation_unflushed_rows", 0) == c["rows_admitted"] - excluded
        assert c["fill_imputed"] > 0 and c["no_fill_quote.size"] > 0

    def test_the_imputed_rows_are_the_difference_between_the_fill_and_the_sensitivity_states(self, tmp_path):
        node, _, res = cluster_calls(tmp_path)
        a, b = node._states["expanding_fill"].fit(None), node._states["fill_sensitivity"].fit(None)
        assert b["rows"] > a["rows"] - res["counts"].get("imputation_unflushed_rows", 0)
        assert node._states["fill_sensitivity"].decay is None and node._states["reported_sensitivity"].decay is None

    def test_q95_is_frozen_once_per_week_and_dropped_after_its_last_cluster(self, tmp_path, monkeypatch):
        seen = {}
        real = EdgeReliabilityWalk._flush_sensitivity

        def spy(self, t):
            seen[t] = ({w: json.dumps(q, sort_keys=True) for w, q in self._q95.items()}, dict(self._refs))
            return real(self, t)

        monkeypatch.setattr(EdgeReliabilityWalk, "_flush_sensitivity", spy)
        run(make(tmp_path), tmp_path)
        weeks = {w for q, _ in seen.values() for w in q}
        assert weeks
        week = max(sorted(weeks), key=lambda w: sum(w in q for q, _ in seen.values()))
        holders = [t for t, (q, _) in seen.items() if week in q]
        assert len(holders) >= 2                                              # lives across several admissions
        assert len({seen[t][0][week] for t in holders}) == 1                   # never recomputed
        assert week not in seen[CAL[-1]][0]                                    # dropped after the last cluster
        assert all(r > 0 for _, refs in seen.values() for r in refs.values())

    def test_a_resume_between_two_admissions_of_one_week_is_byte_identical(self, tmp_path):
        run(make(tmp_path, out="whole", checkpoint_every=3), tmp_path)
        for cut in (CAL[10], CAL[14]):
            with pytest.raises(RuntimeError):
                run(make(tmp_path, out=f"cut{cut}", fail_at=cut, checkpoint_every=3), tmp_path)
            assert list((tmp_path / f"cut{cut}" / "sensitivity").rglob("*.json"))     # a buffer is in flight at the cut
            run(make(tmp_path, out=f"cut{cut}", checkpoint_every=3), tmp_path)
            assert tree(tmp_path / "whole") == tree(tmp_path / f"cut{cut}")

    def test_the_buffer_shard_round_trips_and_a_missing_one_refuses(self, tmp_path):
        with pytest.raises(RuntimeError):
            run(make(tmp_path, fail_at=CAL[12], checkpoint_every=4), tmp_path)
        shards = sorted((tmp_path / "out" / "sensitivity").rglob("*.json"))
        assert shards
        listed = manifest_sens(tmp_path / "out")
        assert len(shards) >= len(listed)
        victim = next(p for p in shards if (p.parent.name, p.stem) in listed)
        victim.unlink()
        with pytest.raises(ValueError, match="checkpoint_mismatch"):
            run(make(tmp_path, checkpoint_every=4), tmp_path)

    def test_the_window_end_counts_what_could_not_be_flushed(self, tmp_path):
        node, _, res = cluster_calls(tmp_path)
        unflushed = res["counts"]["imputation_unflushed"]
        assert unflushed == sum(len(v) for v in node._sens.values()) > 0
        later = max(s for s in node._sens)
        assert later > CAL[-2]

    def test_a_poisoned_future_changes_no_sensitivity_buffer_or_q95_at_t(self, tmp_path):
        seen = {}

        def capture(label):
            def hook(walk, t):
                if t == T:
                    seen[label] = json.dumps([walk._sens, walk._q95, walk._refs, walk._adv, walk._twin.to_dict()],
                                             sort_keys=True)
            return hook

        run(make(tmp_path, World(), end=T, out="c", on_decide=capture("c")), tmp_path)
        run(make(tmp_path, World(poison_from=T), end=T, out="d", on_decide=capture("d")), tmp_path)
        assert seen["c"] == seen["d"]

    def test_the_twin_holds_real_fills_of_exactly_the_clusters_the_sensitivity_state_holds(self, tmp_path):
        node, _, _ = cluster_calls(tmp_path)
        twin, rep = node._twin.fit(None), node._states["reported_sensitivity"].fit(None)
        assert 0 < twin["rows"] < rep["rows"]
        assert twin["clusters"] == rep["clusters"] or twin["clusters"] < rep["clusters"]


def manifest_sens(root):
    tree_ = ew._npz_tree((Path(root) / manifest(root)["npz"]["file"]).read_bytes())
    return {(s_, name) for s_, names in tree_["sens"]["index"].items() for name in names}


# --------------------------------------------------------------------------- F3.5: reads and outputs


def csv_rows(path):
    lines = Path(path).read_text().splitlines()
    head = lines[0].split(",")
    return [dict(zip(head, ln.split(","))) for ln in lines[1:]]


def reported_rows(root):
    return [json.loads(ln) for p in sorted((Path(root) / "rows").glob("*.jsonl")) for ln in p.read_text().splitlines()]


@pytest.fixture(scope="module")
def full_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("full")
    holder = {}

    def hook(walk, t):
        holder["node"] = walk

    node = make(tmp, on_decide=hook, report_from=CAL[2])
    summary = run(node, tmp)["summary"]
    return {"node": node, "summary": summary, "out": tmp / "out", "tmp": tmp}


class TestReadsAndOutputs:
    def test_summary_carries_the_two_gated_reads_and_the_counts(self, full_run):
        s = full_run["summary"]
        r1 = s["reads"]["read1"]
        assert set(r1) == {"b0", "ltop", "fill_ok", "sensitivity_agrees", "verdict"}
        assert r1["verdict"] in ("PASS", "FAIL", "UNRESOLVED")
        assert s["confirmatory_reads"] == 2
        assert set(s["reads"]["read2"]) == {"gated", "slope", "verdict"}
        assert (s["reads"]["read2"]["gated"]) == (r1["verdict"] != "PASS")
        assert s["bh_tested_buckets"] > 0
        assert s["bh_positive_buckets"] + s["bh_negative_buckets"] <= s["bh_tested_buckets"]
        for key in ("b0", "ltop"):
            if r1[key] is not None:
                assert set(r1[key]) == {"t", "p_gt", "p_lt", "scheme"}     # p-values, never a slope value

    def test_fill_rate_axes_each_partition_the_counts(self, full_run):
        c = full_run["summary"]["counts"]
        rows = csv_rows(full_run["out"] / "fill-rate.csv")
        for axis in ("bucket", "decile", "quarter", "group_size"):
            sel = [r for r in rows if r["axis"] == axis]
            assert sum(int(r["decided"]) for r in sel) == c["rows_decided"] + c.get("no_fill_quote.session", 0), axis
            assert sum(int(r["quote"]) for r in sel) == c.get("no_fill_quote.quote", 0), axis
            assert sum(int(r["size"]) for r in sel) == c.get("no_fill_quote.size", 0), axis
            assert sum(int(r["session"]) for r in sel) == c.get("no_fill_quote.session", 0), axis
            assert sum(int(r["basis"]) for r in sel) == c.get("no_fill_quote.basis", 0), axis
            assert sum(int(r["filled"]) for r in sel) + sum(int(r["other"]) for r in sel) + sum(
                int(r["quote"]) + int(r["size"]) + int(r["basis"]) for r in sel) == c["rows_admitted"], axis
        assert {int(r["value"]) for r in rows if r["axis"] == "group_size"} == {6}
        assert {int(r["value"]) for r in rows if r["axis"] == "decile"} <= set(range(10))

    def test_the_reported_axes_count_only_reported_decisions(self, full_run):
        rep = {r["axis"] + ":" + r["value"]: r for r in csv_rows(full_run["out"] / "fill-rate.csv")}
        done = reported_rows(full_run["out"])
        assert int(rep["reported:all"]["filled"]) == sum(1 for r in done if r["code"] is None)
        assert int(rep["reported:top"]["decided"]) > 0
        assert int(rep["reported:top"]["decided"]) < int(rep["reported:all"]["decided"])

    def test_read_one_statistics_are_the_batch_values_on_reported_rows(self, full_run):
        import numpy as np

        done = [r for r in reported_rows(full_run["out"]) if r["code"] is None]
        st = full_run["node"]._states["reported_fill"]
        w = np.array([r["w"] for r in done])
        e = np.array([r["e"] / r["scale"] for r in done])
        y = np.array([r["r"] / r["scale"] for r in done])
        xm, ym = (w * e).sum() / w.sum(), (w * y).sum() / w.sum()
        slope = (w * (e - xm) * (y - ym)).sum() / (w * (e - xm) ** 2).sum()
        assert st.fit(None)["b"] == pytest.approx(slope, rel=1e-9)               # map_warmup rows included
        top = np.array([r["top"] for r in done])
        assert top.any()
        assert st.fit_ratio("top")["L"] == pytest.approx((w * y)[top].sum() / (w * e)[top].sum(), rel=1e-9)

    def test_warm_up_rows_leave_read_two_and_the_forward_table_only(self, full_run):
        done = [r for r in reported_rows(full_run["out"]) if r["code"] is None]
        warm = [r for r in done if r["map_warmup"]]
        assert warm and len(warm) < len(done)
        node = full_run["node"]
        assert node._states["forward"].fit(None)["rows"] == len(done) - len(warm)
        assert node._states["reported_fill"].fit(None)["rows"] == len(done)
        fwd = csv_rows(full_run["out"] / "forward-reliability.csv")
        top = next(r for r in fwd if r["variant"] == "c_bucket" and r["side"] == "all" and r["quarter"] == "all"
                   and r["bin"] == "all")
        assert int(top["n"]) == len(done) - len(warm)

    def test_bh_counts_equal_an_independent_step_up(self, full_run):
        from dskit.pipeline.stats import student_t_sf

        st = full_run["node"]._states["expanding_fill"]
        ps = {}
        for b in range(24):
            f = st.fit(b)
            if f["theta"] is None:
                continue
            sw = st.sandwich(b, f["theta"], "two_block", block=0)
            if sw is None or not sw["cov"][1][1] > 0:
                continue
            ps[b] = (f["b"], min(1.0, 2 * student_t_sf(abs(f["b"] / sw["cov"][1][1] ** 0.5), sw["df"])))
        order = sorted(ps, key=lambda k: ps[k][1])
        m, last = len(order), -1
        for rank, k in enumerate(order, 1):
            if ps[k][1] <= 0.10 * rank / m:
                last = rank
        rej = order[:last] if last > 0 else []
        s = full_run["summary"]
        assert s["bh_tested_buckets"] == m
        assert s["bh_positive_buckets"] == sum(1 for k in rej if ps[k][0] > 0)
        assert s["bh_negative_buckets"] == sum(1 for k in rej if ps[k][0] < 0)

    def test_direction_tables_and_the_e4_input(self, full_run):
        d = full_run["summary"]["direction"]["e4_input"]
        if d is not None:
            for key in ("w_dir", "w_dir_shrunk"):
                rec = d[key]
                assert set(rec["bands"]) == {"0.9", "0.95"}
                for band in rec["bands"].values():
                    assert band["lcb"] < rec["value"] < band["ucb"]
                assert rec["bands"]["0.95"]["ucb"] - rec["bands"]["0.95"]["lcb"] > (
                    rec["bands"]["0.9"]["ucb"] - rec["bands"]["0.9"]["lcb"])
            assert d["g_eff_blocks_13"] > 1
        rows = csv_rows(full_run["out"] / "direction-slope.csv")
        assert [r["bucket"] for r in rows][0] == "pooled" and len(rows) == 25
        side = csv_rows(full_run["out"] / "side-slope.csv")
        node = full_run["node"]
        for r in side:
            st = {"reported_fill_common": node._twin, **node._integ, **node._states}[r["state"]]
            assert int(r["rows"]) == st.fit(r["unit"])["rows"]
        assert any(r["state"] == "dir_expanding_fill" for r in side)
        div = csv_rows(full_run["out"] / "divergence.csv")
        assert {"dollar", "fill_sensitivity", "expanding_decision"} <= {r["state"] for r in div}

    def test_the_outputs_are_listed_with_their_hashes(self, full_run):
        outs = full_run["summary"]["outputs"]
        assert set(outs) >= {"fill-rate.csv", "side-slope.csv", "divergence.csv", "direction-slope.csv",
                             "forward-reliability.csv"}
        for name, sha in outs.items():
            assert hashlib.sha256((full_run["out"] / name).read_bytes()).hexdigest() == sha

    def test_a_low_fill_rate_makes_read_one_unresolved_and_read_two_gated(self, tmp_path):
        s = run(make(tmp_path, min_fill_rate=1.0), tmp_path)["summary"]
        assert s["counts"]["low_fill_rate"] == 1
        assert s["reads"]["read1"]["fill_ok"] is False
        assert s["reads"]["read1"]["verdict"] == "UNRESOLVED"
        assert s["reads"]["read2"] == {"gated": True, "slope": None, "verdict": "UNRESOLVED"}

    def test_read_one_does_not_depend_on_any_map(self, tmp_path):
        a = run(make(tmp_path, out="a"), tmp_path)["summary"]["reads"]["read1"]
        b = run(make(tmp_path, out="b", pooling={**POOL, "lcb_k": 3.0, "tau2_floor": 1e-3}),
                tmp_path)["summary"]["reads"]["read1"]
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_sensitivity_disagreement_on_both_sides_is_unresolved(self):
        agrees = EdgeReliabilityWalk._agrees
        up = {"gt0": True, "lt_floor": False}
        down = {"gt0": False, "lt_floor": True}
        flat = {"gt0": False, "lt_floor": False}
        ok = {"b0": up, "ltop": up}
        assert agrees(ok, {"b0": up, "ltop": up})
        assert not agrees(ok, {"b0": up, "ltop": flat})                  # PASS side not established by the sensitivity
        assert not agrees({"b0": down, "ltop": down}, {"b0": down, "ltop": flat})   # FAIL side likewise
        assert not agrees({"b0": flat, "ltop": up}, {"b0": flat, "ltop": None})
        assert agrees({"b0": flat, "ltop": flat}, {"b0": None, "ltop": None})
        assert agrees({"b0": flat, "ltop": flat}, {"b0": up, "ltop": down})   # only the fill state's side binds

    def test_the_walk_passes_the_fill_and_sensitivity_checks_to_read_one(self, tmp_path, monkeypatch):
        calls = []

        def stub(state, block, level, draws, seed, b0, tl, ok=True):
            calls.append((state, ok))
            stat = {"t": 1.0, "p_gt": 0.01, "p_lt": 0.9, "scheme": "webb", "gt0": True, "lt_floor": False}
            return {"b0": dict(stat), "ltop": dict(stat), "verdict": "PASS" if ok else "UNRESOLVED"}

        monkeypatch.setattr(ew, "read_one", stub)
        s = run(make(tmp_path, min_fill_rate=0.5), tmp_path)["summary"]
        node_states = [c[0].regressors for c in calls]
        assert len(calls) == 3 and set(node_states) == {1}
        assert s["reads"]["read1"]["fill_ok"] and s["reads"]["read1"]["sensitivity_agrees"]
        assert s["reads"]["read1"]["verdict"] == "PASS" and calls[2][1] is True
        assert s["reads"]["read2"]["gated"] is False


# --------------------------------------------------------------------------- F3.6: next-session bound, RAM before the hook


class TestNextSessionBound:
    @pytest.mark.parametrize("nxt,ok", [(3, True), (7, True), (0, False), (-1, False), (8, False), (14, False)])
    def test_the_next_session_lies_after_t_and_not_after_the_next_decision(self, tmp_path, nxt, ok):
        class Odd(SynthWalk):
            def _next_session(self, t):
                return add_days(t, nxt)

        node = Odd("walk", params(tmp_path / "x", window={"start": CAL[0], "end": CAL[6]}),
                   World(no_rows_before="2099-01-01"))
        if ok:
            run(node, tmp_path)
        else:
            with pytest.raises(ValueError, match="calendar_mismatch") as err:
                run(node, tmp_path)
            assert err.value.code == "calendar_mismatch"

    def test_the_last_decision_has_no_upper_bound_but_still_needs_nxt_after_t(self, tmp_path):
        class Far(SynthWalk):
            def _next_session(self, t):
                return add_days(t, 30)

        node = Far("walk", params(tmp_path / "x", window={"start": CAL[0], "end": CAL[0]}), World(no_rows_before=CAL[9]))
        with pytest.raises(ValueError, match="calendar_mismatch"):   # CAL[1] is the next decision date
            run(node, tmp_path)


class TestRamBeforeTheHook:
    def test_the_ram_check_runs_before_the_decision_hook_allocates(self, tmp_path, monkeypatch):
        order = []
        real = EdgeReliabilityWalk._check_ram

        def spy(self):
            order.append("ram")
            return real(self)

        monkeypatch.setattr(EdgeReliabilityWalk, "_check_ram", spy)

        class Probe(SynthWalk):
            def _decision_rows(self, t):
                order.append("hook")
                return super()._decision_rows(t)

        run(Probe("walk", params(tmp_path / "x", window={"start": CAL[0], "end": CAL[0]}), World()), tmp_path)
        assert order.index("ram") < order.index("hook")

    def test_a_ram_limit_breach_before_the_hook_never_opens_the_hook(self, tmp_path):
        node = make(tmp_path, ram_gb_limit=1e-9, end=CAL[0])
        with pytest.raises(ValueError, match="ram_limit"):
            run(node, tmp_path)
        assert not any(ev[0] == "decide" for ev in node.events)


class TestTwoBlockIsLoudWhenOnlyOneBlockIsPopulated:
    def test_a_single_populated_block_is_counted_each_week_it_holds(self, tmp_path):
        s = run(make(tmp_path, end=CAL[6]), tmp_path)["summary"]
        assert s["counts"].get("two_block_single_block", 0) >= 1
        s2 = run(make(tmp_path, out="late"), tmp_path)["summary"]
        assert s2["counts"].get("two_block_single_block", 0) < N_WEEKS


# --------------------------------------------------------------------------- F3.7: integration asks (ADR-0313 X1, X2, X6, X7)


class TestIntegrationAsks:
    def test_x1_every_map_carries_the_regimes_of_the_names_decided_that_week(self, full_run):
        from dskit.pipeline.edge_reliability import TrailingRegime

        regime = TrailingRegime(REGIME)
        root = full_run["out"]
        for t in CAL:
            vols = {n: 0.2 * (1.0 + 0.5 * u("v", n, t)) for n in NAMES}
            want = {n: list(regime.of(n, t, v)) for n, v in vols.items()}
            got = json.loads((root / f"map-{t}.json").read_text())["regimes"]
            assert got == want, t
            for n, v in vols.items():
                regime.add(n, t, v)

    def test_x6_cw_bucket_is_the_stored_origin_weight_times_the_scaled_edge(self, full_run):
        done = [r for r in reported_rows(full_run["out"]) if r["code"] is None]
        maps = {}
        for r in done[:400]:
            m = maps.setdefault(r["t"], json.loads((full_run["out"] / f"map-{r['t']}.json").read_text()))
            w = m["states"]["expanding_fill"]["buckets"][r["bucket"]]["w"]
            assert r["cw_bucket"] == pytest.approx(w * r["e"] / r["scale"], rel=1e-12, abs=1e-15)
            assert "cw" not in r
        assert any(r["cw_bucket"] != 0 for r in done)

    def test_x2_the_decision_line_is_the_stored_no_intercept_two_regressor_line(self, full_run):
        done = [r for r in reported_rows(full_run["out"]) if r["code"] is None]
        maps, seen = {}, 0
        for r in done:
            m = maps.setdefault(r["t"], json.loads((full_run["out"] / f"map-{r['t']}.json").read_text()))
            if r["e_dir"] is None:
                assert r["c2w"] is None
                continue
            wd, ws = m["origin_two"]["buckets"][r["bucket"]]["w"]
            d, e = r["e_dir"] / r["scale"], r["e"] / r["scale"]
            assert r["c2w"] == pytest.approx(wd * d + ws * (e - d), rel=1e-12, abs=1e-15)
            assert 0.0 <= wd <= 1.0 and 0.0 <= ws <= 1.0                      # clipped to [0, weight_cap]
            seen += wd > 0 or ws > 0
        assert seen > 0

    def test_integration_reads_are_descriptive_and_leave_the_confirmatory_count_at_two(self, full_run):
        s, node = full_run["summary"], full_run["node"]
        assert s["confirmatory_reads"] == 2 and len(s["states"]) == 14
        ir = s["integration_reads"]
        assert set(ir) == {"read2w", "read2b", "affine_side"}
        assert (ir["read2w"]["state"], ir["read2w"]["line"], ir["read2w"]["form"]) == ("forward_w", "cw_bucket", "slope")
        assert (ir["read2b"]["state"], ir["read2b"]["form"]) == ("forward_b2", "no_intercept")
        assert ir["affine_side"]["form"] == "affine" and ir["affine_side"]["verdict"] in ("PASS", "UNRESOLVED")
        assert all(v["verdict"] in ("PASS", "FAIL", "UNRESOLVED") for v in ir.values())
        assert node._integ["forward_w"].fit(None)["rows"] == node._states["forward"].fit(None)["rows"]
        assert 0 < node._integ["forward_b2"].fit(None)["rows"] <= node._states["forward"].fit(None)["rows"]

    def test_x7_the_walk_summary_keys_a_study_reads(self, full_run):
        s = full_run["summary"]
        assert s["sealed_from"] == "2021-07-01" and s["report_from"] == CAL[2]
        assert s["window"] == {"start": CAL[0], "end": CAL[-1]}
        assert s["edge_scale"] == "notional" and s["row_weight"] == "group_equal"
        assert {"weight_cap", "band", "min_blocks", "ci_levels"} <= set(s["pooling"])
        assert s["buckets"] == EDGES and s["regime"] == REGIME
        assert s["walk_decision_dates"] == CAL and "decision_weeks" not in s
        warm = {}
        for t in CAL:
            m = json.loads((full_run["out"] / f"map-{t}.json").read_text())
            q = f"{t[:4]}Q{(int(t[5:7]) - 1) // 3 + 1}"
            for k, rec in {**m["states"], **m["direction"]}.items():
                warm.setdefault(q, {}).setdefault(k, 0)
                warm[q][k] += bool(rec["warm"])
        assert s["warm_weeks"] == {q: dict(sorted(v.items())) for q, v in sorted(warm.items())}
        assert s["reads"]["read1"]["verdict"] in ("PASS", "FAIL", "UNRESOLVED")

    def test_the_new_states_resume_byte_identically(self, tmp_path):
        run(make(tmp_path, out="whole", checkpoint_every=4), tmp_path)
        with pytest.raises(RuntimeError):
            run(make(tmp_path, out="cut", fail_at=CAL[17], checkpoint_every=4), tmp_path)
        run(make(tmp_path, out="cut", checkpoint_every=4), tmp_path)
        assert tree(tmp_path / "whole") == tree(tmp_path / "cut")


# --------------------------------------------------------------------------- F3 fix round: verdicts where they occur


class SignalWalk(SynthWalk):
    """A world whose fill-session credit is ``beta x e + noise`` (settlement at the strike: no intrinsic value).

    ``beta`` 1 is a real signal (read 1 PASS), ``beta`` 0 none (FAIL). ``miss(row)`` -> bool makes a row's fill quote
    absent; ``mid_of(row, e)`` rewrites the decision mid (which prices the sensitivity imputation).
    """

    beta = 1.0
    miss = None
    mid_of = None

    def _decision_rows(self, t):
        rows = super()._decision_rows(t)
        self._e = getattr(self, "_e", {})
        for r in rows:
            self._e[self._key(r, t)] = r["e"]
            if self.mid_of is not None:
                r["mid"] = self.mid_of(r, r["e"])
        return rows

    @staticmethod
    def _key(r, t):
        return (r["name"], t, r["expiry"], r["right"], r["strike"], r["sign"])

    def _fill_credits(self, rows):
        out = []
        for r in rows:
            key = self._key(r, r["decision_date"])
            e = self._e[key]
            if self.miss is not None and self.miss(r, e, key):
                out.append("quote")
                continue
            noise = (u("noise", *key) - 0.5) * 0.2
            out.append((self.beta * e + noise, 1.0 + u("mm", r["name"], r["expiry"], r["strike"])))
        return out

    def _settlement_levels(self, rows):
        return [r["strike"] for r in rows]


def signal(tmp_path, tag, beta=1.0, miss=None, mid_of=None, **over):
    over.setdefault("report_from", CAL[2])
    over.setdefault("window", {"start": CAL[0], "end": CAL[-1]})
    node = SignalWalk("w", params(tmp_path / tag, **over), World())
    node.beta, node.miss, node.mid_of = beta, miss, mid_of
    return node, run(node, tmp_path)["summary"]


@pytest.fixture(scope="module")
def sig_pass(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sigpass")
    return signal(tmp, "o", 1.0)


@pytest.fixture(scope="module")
def sig_fail(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sigfail")
    return signal(tmp, "o", 0.0)


def _skew_mid(r, e):
    """A decision mid that makes an imputed credit track ``e`` (sells with a positive edge); others keep theirs."""
    return 10.0 * e / r["multiplier"] if (r["sign"] < 0 and e > 0) else r["mid"]


def _miss_skewed(r, e, key):
    return r["sign"] < 0 and e > 0


class TestVerdictsInASignalWorld:
    def test_a_real_signal_passes_read_one_and_opens_the_gated_reads(self, sig_pass):
        _, s = sig_pass
        one = s["reads"]["read1"]
        assert one["fill_ok"] and one["sensitivity_agrees"] and one["verdict"] == "PASS"
        assert s["reads"]["read2"]["gated"] is False
        ir = s["integration_reads"]
        assert ir["read2w"]["gated"] is False and ir["read2b"]["gated"] is False and ir["affine_side"]["gated"] is False

    def test_no_signal_fails_read_one_and_gates_every_follow_up_read(self, sig_fail):
        _, s = sig_fail
        assert s["reads"]["read1"]["verdict"] == "FAIL"
        assert s["reads"]["read2"] == {"gated": True, "slope": None, "verdict": "UNRESOLVED"}
        for key, rec in s["integration_reads"].items():
            assert rec["gated"] is True and rec["verdict"] == "UNRESOLVED", key
        assert s["integration_reads"]["affine_side"]["intercept"] is None
        assert s["integration_reads"]["affine_side"]["sides_positive"] is False

    def test_a_low_fill_rate_alone_makes_a_real_signal_unresolved(self, tmp_path):
        # unfilled rows with no decision mid cannot be imputed, so the sensitivity state cannot disagree
        node, s = signal(tmp_path, "lo", 1.0, miss=lambda r, e, k: u("m", *k) < 0.3, mid_of=lambda r, e: None)
        one = s["reads"]["read1"]
        assert one["fill_ok"] is False and one["sensitivity_agrees"] is True and one["verdict"] == "UNRESOLVED"
        assert s["reads"]["read2"]["gated"] is True
        assert all(v["gated"] for v in s["integration_reads"].values())
        # the control: the same state with the fill check passed is a PASS, so the floor alone decided it
        assert node._safe_read(node._states["reported_fill"], True)["verdict"] == "PASS"

    def test_the_material_floors_decide_the_fail_verdict(self, tmp_path):
        _, s = signal(tmp_path, "tiny", 0.0, b0_floor=1e-9, tail_level_floor=1e-9)
        assert s["reads"]["read1"]["verdict"] == "UNRESOLVED"        # nothing is established below a floor of ~0

    def test_sensitivity_disagreement_on_the_pass_side_is_unresolved(self, tmp_path):
        node, s = signal(tmp_path, "dp", 1.0, miss=lambda r, e, k: u("m", *k) < 0.5, min_fill_rate=0.1)
        one = s["reads"]["read1"]
        assert one["fill_ok"] and one["sensitivity_agrees"] is False and one["verdict"] == "UNRESOLVED"
        assert node._safe_read(node._states["reported_fill"], True)["verdict"] == "PASS"   # only the sensitivity blocks it
        assert s["reads"]["read2"]["gated"] is True

    def test_sensitivity_disagreement_on_the_fail_side_is_unresolved(self, tmp_path):
        node, s = signal(tmp_path, "df", 0.0, miss=_miss_skewed, mid_of=_skew_mid, min_fill_rate=0.1)
        one = s["reads"]["read1"]
        assert one["fill_ok"] and one["sensitivity_agrees"] is False and one["verdict"] == "UNRESOLVED"
        assert node._safe_read(node._states["reported_fill"], True)["verdict"] == "FAIL"
        assert s["reads"]["read2"]["gated"] is True


class TestImputationIsWiredToTheFrozenQ95:
    def test_every_imputed_credit_is_booked_at_a_frozen_q95_and_some_are_positive(self, tmp_path, monkeypatch):
        frozen, used = [], []
        real_freeze, real_imp = EdgeReliabilityWalk._freeze_q95, EdgeReliabilityWalk._imputed_credit

        def freeze(buckets, values, min_sample):
            out = real_freeze(buckets, values, min_sample)
            frozen.append({out["pool"], *out["b"].values()})
            return out

        def imp(sign, mid, q95, multiplier, fee):
            used.append(q95)
            return real_imp(sign, mid, q95, multiplier, fee)

        monkeypatch.setattr(EdgeReliabilityWalk, "_freeze_q95", staticmethod(freeze))
        monkeypatch.setattr(EdgeReliabilityWalk, "_imputed_credit", staticmethod(imp))
        _, s = signal(tmp_path, "imp", 1.0, miss=lambda r, e, k: u("m", *k) < 0.3, min_fill_rate=0.1)
        assert s["counts"]["fill_imputed"] == len(used) > 0
        every = set().union(*frozen)
        assert set(used) <= every
        assert max(used) > 0.0                                  # a lookup replaced by 0.0 books every row at the mid


class TestE4InputBeforeWarmUp:
    def test_a_pool_that_is_not_warm_reports_no_shrunk_band(self, tmp_path):
        _, s = signal(tmp_path, "cold", 1.0, pooling={**POOL, "min_clusters": 10_000})
        e4 = s["direction"]["e4_input"]
        assert e4["warm"] is False and e4["w_dir_shrunk"] is None
        assert e4["w_dir"]["bands"]                     # the own-fit band is still an estimate

    def test_a_warm_pool_reports_it(self, sig_pass):
        e4 = sig_pass[1]["direction"]["e4_input"]
        assert e4["warm"] is True and e4["w_dir_shrunk"]["bands"]


class TestAffineSideIsPerSide:
    def _stub(self, monkeypatch, node, intercept, gt0):
        seen = []
        stat = {"t": 1.0, "se": 1.0, "p_gt": 0.01, "p_lt": 0.9, "scheme": "webb", "draws": 99, "value": 0.5}

        def icpt(state, unit=None):
            seen.append(unit)
            return {"p": 0.01, "reject": intercept[unit]}

        monkeypatch.setattr(node, "_intercept_test", icpt)
        monkeypatch.setattr(node, "_slope_tests", lambda state, unit=None: {**stat, "gt0": gt0[unit], "lt_floor": False})
        return seen

    def test_both_sides_need_their_own_intercept_and_their_own_positive_slope(self, sig_pass, monkeypatch):
        node = sig_pass[0]
        yes = {"buy": True, "sell": True, None: True}
        seen = self._stub(monkeypatch, node, yes, {"buy": True, "sell": True, None: True})
        assert node._integration_reads(False)["affine_side"]["verdict"] == "PASS" and set(seen) == {"buy", "sell"}
        self._stub(monkeypatch, node, {"buy": True, "sell": False, None: True}, {"buy": True, "sell": True, None: True})
        assert node._integration_reads(False)["affine_side"]["verdict"] == "UNRESOLVED"       # a pooled intercept is not enough
        self._stub(monkeypatch, node, yes, {"buy": True, "sell": False, None: True})
        side = node._integration_reads(False)["affine_side"]
        assert side["sides_positive"] is False and side["verdict"] == "UNRESOLVED"            # a bare b > 0 is not a band

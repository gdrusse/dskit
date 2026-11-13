"""The multi-start Kelly local search (ADR-0315): LP-rounded starts, the pair add, the cash floor, determinism."""

import time

import numpy as np
import pytest

from dskit.pipeline.libs.pyomo import TransportPortfolioSolve
from index_options.kelly_local import KellyLocalSearch
from index_options.kelly_multistart import KellyMultiStart
from test_kelly_local import PARAMS, _exact, _program, _scorer

MULTI = {"top_groups": 4, "starts": 32, "climbed": 4, "pairs_evaluated": 32, "scales": [1., .5, .25]}


def _spread_program():
    """One group whose short leg needs its long cover (a linear row): no single add beats cash, the spread does."""
    program = _program(n_groups=1, n_pairs=2, seed=1, capacity=3, joint=False)
    group = program["groups"][0]
    expected = np.asarray(group["masses"]) @ np.asarray(group["losses"])
    group["credit"][3] = float(expected[3])-.5     # the cover alone loses half a dollar
    group["credit"][0] = float(expected[0])+3.     # the short earns three, but only beside its cover
    group["credit"][1] = group["credit"][2] = -50.
    group["linear_rows"] = [{"coefficients": [1., 0., 0., -1.], "upper": 0.}]
    return program


def test_the_declaration_is_validated():
    assert KellyMultiStart.validate_multistart(PARAMS, MULTI) == []
    for params, multi in (({**PARAMS, "restarts": 1}, MULTI), ({**PARAMS, "moves": 0}, MULTI),
                          (PARAMS, {**MULTI, "top_groups": 0}), (PARAMS, {**MULTI, "starts": 1}),
                          (PARAMS, {**MULTI, "climbed": 0}), (PARAMS, {**MULTI, "pairs_evaluated": -1}),
                          (PARAMS, {**MULTI, "scales": []}), (PARAMS, {**MULTI, "scales": [0.]}),
                          (PARAMS, {**MULTI, "scales": [1.5]}), (PARAMS, {**MULTI, "scales": [.5, .5]}),
                          (PARAMS, {**MULTI, "scales": [True]}), (PARAMS, {**MULTI, "climbed": 1.}),
                          (PARAMS, {**MULTI, "extra": 1}), (PARAMS, None),
                          ({**PARAMS, "cash_floor": True}, MULTI), ({**PARAMS, "repair": False}, MULTI),
                          (PARAMS, {**MULTI, "evaluations": 0}), (PARAMS, {**MULTI, "evaluations": 1.5})):
        assert KellyMultiStart.validate_multistart(params, multi), (params, multi)
    with pytest.raises(ValueError, match="restarts must be 0"):
        KellyMultiStart(_scorer(_program()), {**PARAMS, "restarts": 1}, MULTI)


def test_the_lp_books_are_the_screens_relaxation_and_the_bound_is_unchanged():
    """One LP per group: its value is exactly `_group_lp_bound` (the screen's), and its book attains it within caps."""
    scorer = _scorer(_program(n_groups=5, n_pairs=3, seed=2, capacity=4))
    bounds, books = KellyMultiStart(scorer, PARAMS, MULTI).lp_books()
    for group, bound, book in zip(scorer.groups, bounds, books):
        assert bound == TransportPortfolioSolve._group_lp_bound(group)
        assert TransportPortfolioSolve._group_lp_relaxation(group)[0] == bound
        assert ((book >= 0) & (book <= np.asarray(group["capacity"]))).all()


def test_the_starts_are_cash_then_each_kept_groups_rounded_book_then_unions_then_seeded_combinations():
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    search = KellyMultiStart(scorer, PARAMS, MULTI)
    bounds, books = search.lp_books()
    kept, starts = search.starts(bounds, books)
    assert kept == sorted(range(6), key=lambda g: (-bounds[g], scorer.groups[g]["id"]))[:MULTI["top_groups"]]
    assert starts[0] == ("cash", {}) and len(starts) == MULTI["starts"]
    assert len({tuple(sorted(s.items())) for _k, s in starts}) == len(starts)   # no duplicates
    g = kept[0]
    rounded = np.minimum(np.floor(books[g]+.5), 5).astype(int)
    for x, y in scorer.groups[g]["exclusive"]:
        if rounded[x] and rounded[y]:
            rounded[y if rounded[x] >= rounded[y] else x] = 0
    assert starts[1] == ("group", {int(search.off[g]+i): int(v) for i, v in enumerate(rounded) if v})
    kinds = [k for k, _s in starts]
    assert kinds == sorted(kinds, key=["cash", "group", "union", "random"].index) and "random" in kinds
    # A start that holds both exclusive sides never appears; every start sits inside its kept groups.
    allowed = {int(i) for g in kept for i in range(search.off[g], search.off[g+1])}
    assert all(set(s) <= allowed for _k, s in starts)
    # Fewer starts than the structured list: it is cut in order, with no random draw.
    short = KellyMultiStart(scorer, PARAMS, {**MULTI, "starts": 3}).starts(bounds, books)[1]
    assert short == starts[:3]


def test_two_runs_and_two_instances_agree_and_the_seed_moves_only_the_random_starts():
    program = _program(n_groups=6, n_pairs=3, seed=3, capacity=5)
    first = KellyMultiStart(_scorer(program), PARAMS, MULTI).run_multistart()
    search = KellyMultiStart(_scorer(program), PARAMS, MULTI)
    for again in (search.run_multistart(), search.run_multistart()):
        assert again["quantities"] == first["quantities"] and again["record"] == first["record"]
    assert "seconds" not in first["record"]
    other = KellyMultiStart(_scorer(program), {**PARAMS, "seed": 8}, MULTI)
    bounds, books = other.lp_books()
    mine = KellyMultiStart(_scorer(program), PARAMS, MULTI).starts(bounds, books)[1]
    theirs = other.starts(bounds, books)[1]
    structured = [s for s in mine if s[0] != "random"]
    assert theirs[:len(structured)] == structured and theirs != mine
    assert other.digest(theirs) != KellyMultiStart.digest(mine) == first["record"]["multistart"]["starts_digest"]


def test_the_search_records_its_starts_and_climbs_and_its_score_is_the_scorers():
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=3, capacity=5))
    out = KellyMultiStart(scorer, PARAMS, MULTI).run_multistart()
    record, multi = out["record"], out["record"]["multistart"]
    assert record["start_kind"] == "multistart" and record["restarts"] == 0
    assert set(record["move_counts"]) == {"add", "remove", "swap", "pair"}
    assert sum(record["move_counts"].values()) == record["moves"] == sum(c["moves"] for c in multi["climbs"])
    assert record["end_value"] == pytest.approx(_exact(scorer, out["quantities"]), abs=1e-9)
    assert len(multi["climbs"]) == MULTI["climbed"] and multi["starts"] == MULTI["starts"]
    assert [c["start_value"] for c in multi["climbs"]] == sorted((c["start_value"] for c in multi["climbs"]),
                                                                 reverse=True)
    chosen = next(c for c in multi["climbs"] if c["start"] == multi["chosen_start"])
    assert chosen["end_value"] == record["end_value"] == max(c["end_value"] for c in multi["climbs"])
    assert record["start_value"] == chosen["start_value"] and not multi["cash_floor"]
    assert all(c["end_value"] >= c["start_value"] for c in multi["climbs"])
    assert record["end_value"] >= multi["cash_value"]
    assert scorer.check_feasible(out["quantities"])["feasible"]
    assert out["screen"] == {"rule": "lp", "candidates": 6, "kept": multi["lp_live"], "screened": 6-multi["lp_live"],
                             "fallback": None}


def test_the_pair_add_reaches_a_spread_no_single_move_can_open():
    """From cash no add, removal or swap improves (ADR-0307's search stays in cash); the pair move opens the spread
    and the multi-start search reaches the enumerated optimum."""
    scorer = _scorer(_spread_program())
    assert KellyLocalSearch(scorer, PARAMS).run(None)["quantities"] == [[0, 0, 0, 0]]
    search = KellyMultiStart(scorer, PARAMS, MULTI)
    search._load(np.zeros(search.N, dtype=np.int64))
    gain, changes = search._best_move()
    assert changes == {0: 1, 3: 1} and gain > 0
    off = KellyMultiStart(scorer, PARAMS, {**MULTI, "pairs_evaluated": 0})
    off._load(np.zeros(off.N, dtype=np.int64))
    assert off._best_move() is None
    out = search.run_multistart()
    best = max(_exact(scorer, book) for book in scorer.catalogue(10000))
    assert out["record"]["end_value"] == pytest.approx(best, abs=1e-9)
    cash_climb = next(c for c in out["record"]["multistart"]["climbs"] if c["kind"] == "cash")
    assert cash_climb["end_value"] == pytest.approx(best, abs=1e-9) and out["record"]["move_counts"]["pair"] > 0


def test_a_book_scoring_below_cash_is_never_traded(monkeypatch):
    """Only a losing start is offered and one move is allowed: the climb ends below cash, so the week holds cash."""
    program = _program(n_groups=2, n_pairs=1, seed=0, capacity=3, joint=False)
    for group in program["groups"]:
        group["credit"] = [-abs(c)-5. for c in group["credit"]]
    scorer = _scorer(program)
    search = KellyMultiStart(scorer, {**PARAMS, "moves": 1}, MULTI)
    monkeypatch.setattr(search, "starts", lambda bounds, books: ([0], [("group", {0: 3, 2: 3})]))
    out = search.run_multistart()
    multi = out["record"]["multistart"]
    assert multi["climbs"][0]["end_value"] < multi["cash_value"] and multi["cash_floor"]
    assert out["quantities"] == [[0, 0], [0, 0]] and multi["chosen_start"] is None
    assert out["record"]["end_value"] == multi["cash_value"] == out["record"]["start_value"]
    # Without the override every book loses and cash is itself a start: cash, with no floor needed.
    plain = KellyMultiStart(scorer, PARAMS, MULTI).run_multistart()
    assert plain["quantities"] == [[0, 0], [0, 0]] and not plain["record"]["multistart"]["cash_floor"]


def test_an_infeasible_start_is_scored_as_nothing_and_never_climbed():
    """A binding reserve cap makes the full LP books infeasible; they are counted and skipped, and the book holds."""
    scorer = _scorer(_program(n_groups=4, n_pairs=2, seed=5, capacity=6, reserve_cap=300.))
    out = KellyMultiStart(scorer, PARAMS, MULTI).run_multistart()
    multi = out["record"]["multistart"]
    assert multi["starts_feasible"] < multi["starts"]
    assert scorer.check_feasible(out["quantities"])["feasible"]


def test_the_search_refuses_a_score_the_scorer_does_not_confirm(monkeypatch):
    scorer = _scorer(_program(n_groups=3, n_pairs=2, seed=4))
    search = KellyMultiStart(scorer, PARAMS, MULTI)
    monkeypatch.setattr(search, "value", lambda: 1e12)
    with pytest.raises(ValueError, match="exact recompute"):
        search.run_multistart()


def test_a_synthetic_week_runs_in_seconds():
    """Timing on a synthetic week (100 groups by 40 items): the LPs, 64 starts and 4 climbs, without any MILP."""
    scorer = _scorer(_program(n_groups=100, n_pairs=20, seed=9, capacity=8, reserve_cap=4000.))
    began = time.perf_counter()
    out = KellyMultiStart(scorer, PARAMS, {**MULTI, "top_groups": 16, "starts": 64}).run_multistart()
    assert time.perf_counter()-began < 120.
    assert out["record"]["end_value"] >= out["record"]["multistart"]["cash_value"]


def test_an_evaluation_budget_caps_the_climbs_deterministically():
    """ADR-0315: ``evaluations`` is a count, so two runs agree exactly, the climbs stop near it and the book is feasible."""
    scorer = _scorer(_program())
    free = KellyMultiStart(scorer, PARAMS, MULTI).run_multistart()
    cap = max(1, free["record"]["evaluations"]//4)
    runs = [KellyMultiStart(scorer, PARAMS, {**MULTI, "evaluations": cap}).run_multistart() for _ in range(2)]
    assert runs[0]["quantities"] == runs[1]["quantities"] and runs[0]["record"] == runs[1]["record"]
    assert runs[0]["record"]["evaluations"] < free["record"]["evaluations"]
    assert runs[0]["record"]["budget_exhausted"]
    assert scorer.check_feasible(runs[0]["quantities"])["feasible"]
    assert runs[0]["record"]["end_value"] >= runs[0]["record"]["multistart"]["cash_value"]


# --- ADR-0315 Amendment 1: feasible starts ---------------------------------------------------------------------

FEASIBLE = {"singles": 8, "units": True, "repair_contracts": 16}
MULTI2 = {**MULTI, "feasible_starts": FEASIBLE}
#: A two-item add shortlist, as narrow against this fixture as 64 is against a production week's 144,000 items.
NARROW = {**PARAMS, "adds_evaluated": 2}


def _stuck_program(seed=2):
    """The defect of the real 2021 weeks in small: every LP start breaks the shared cap, and cash cannot leave.

    The shared reserve cap is far below any rounded LP book. Group ``G0`` pays sixty times the others on the same
    reserve, so its contracts lead the add shortlist on marginal utility while their own dispersion charge (which the
    first-order estimate drops at cash) makes each of them lose; the contracts that do beat cash sit behind them.
    """
    program = _program(n_groups=4, n_pairs=2, seed=seed, capacity=8, reserve_cap=60., joint=False)
    group = program["groups"][0]
    group["losses"] = (np.asarray(group["losses"])*60.).tolist()
    group["credit"] = [c*60. for c in group["credit"]]
    return program


def test_the_feasible_starts_block_is_validated():
    assert KellyMultiStart.validate_multistart(PARAMS, MULTI2) == []
    for block in (None, {}, {**FEASIBLE, "extra": 1}, {**FEASIBLE, "singles": -1}, {**FEASIBLE, "singles": 1.},
                  {**FEASIBLE, "units": 1}, {**FEASIBLE, "repair_contracts": 0}, {**FEASIBLE, "repair_contracts": True},
                  {k: v for k, v in FEASIBLE.items() if k != "units"}):
        assert KellyMultiStart.validate_multistart(PARAMS, {**MULTI, "feasible_starts": block}), block


@pytest.mark.parametrize("seed", [0, 2])
def test_starts_that_all_break_the_shared_cap_end_at_cash_and_amendment_1_finds_the_positive_book(seed):
    """ADR-0315 as registered: one feasible start (cash), no move, objective exactly cash. Amendment 1 on the same
    program and budgets repairs the starts and trades a positive book the scorer accepts."""
    scorer = _scorer(_stuck_program(seed))
    old = KellyMultiStart(scorer, NARROW, MULTI).run_multistart()
    multi = old["record"]["multistart"]
    assert multi["starts"] > 1 and multi["starts_feasible"] == 1 and multi["chosen_kind"] == "cash"
    assert old["record"]["end_value"] == multi["cash_value"] == 0. and old["record"]["moves"] == 0
    assert not any(q for group in old["quantities"] for q in group) and not multi["cash_floor"]
    assert KellyLocalSearch(scorer, NARROW).run(None)["record"]["end_value"] == 0.     # ADR-0307 from cash: stuck too
    new = KellyMultiStart(scorer, NARROW, MULTI2).run_multistart()
    record, fixed = new["record"], new["record"]["multistart"]
    assert record["end_value"] > 1. and any(q for group in new["quantities"] for q in group)
    assert scorer.check_feasible(new["quantities"])["feasible"]
    assert record["end_value"] == pytest.approx(_exact(scorer, new["quantities"]), abs=1e-9)
    assert fixed["starts_feasible"] < fixed["starts"] and fixed["starts_repaired"] > 0
    assert fixed["chosen_kind"] != "cash" and not fixed["cash_floor"]
    best_single = max(row["value"] for row in fixed["start_stats"] if row["kind"] == "single")
    assert record["end_value"] >= best_single > 0.


def test_amendment_1_records_what_became_of_every_start():
    scorer = _scorer(_stuck_program())
    search = KellyMultiStart(scorer, NARROW, MULTI2)
    multi = search.run_multistart()["record"]["multistart"]
    stats = multi["start_stats"]
    assert len(stats) == multi["starts"] and stats[0] == {"kind": "cash", "contracts": 0, "status": "feasible",
                                                          "halvings": 0, "removed": 0, "kept": 0, "value": 0.}
    assert [row["kind"] for row in stats] == sorted((row["kind"] for row in stats),
                                                    key=["cash", "group", "union", "random", "single", "unit"].index)
    assert {"single", "unit"} <= {row["kind"] for row in stats}
    for status, count in (("feasible", multi["starts_feasible"]), ("repaired", multi["starts_repaired"]),
                          ("emptied", multi["starts_emptied"])):
        assert sum(row["status"] == status for row in stats) == count
        assert sum(tally[status] for tally in multi["start_kinds"].values()) == count
    for row in stats:
        assert (row["value"] is None) == (row["status"] == "emptied") and row["kept"] <= row["contracts"]
        if row["status"] == "feasible":
            assert row["kept"] == row["contracts"] and not row["halvings"] and not row["removed"]
        if row["status"] == "repaired":
            assert 0 < row["kept"] < row["contracts"] and (row["halvings"] or row["removed"])
        if row["kind"] == "single":
            assert row["contracts"] == 1 and row["status"] == "feasible" and row["value"] > 0.
    # Cash is always climbed; a book is climbed once however many starts end at it.
    climbs = multi["climbs"]
    assert any(c["kind"] == "cash" for c in climbs) and len(climbs) <= MULTI["climbed"]+1
    assert multi["starts_distinct"] <= multi["starts"]-multi["starts_emptied"]
    assert len({c["start"] for c in climbs}) == len(climbs)


def test_a_repaired_start_is_a_book_the_scorer_accepts_and_scores_the_same():
    scorer = _scorer(_stuck_program())
    search = KellyMultiStart(scorer, NARROW, MULTI2)
    bounds, books = search.lp_books()
    kept, starts = search.starts(bounds, books)
    cash = np.zeros(search.N, dtype=np.int64)
    search._load(cash)
    repaired = 0
    for _kind, start in starts[1:]:
        assert search._trial(start) is None
        book, value, halvings, removed = search._made_feasible(start)
        assert not search.q.any()                                    # cash is loaded again
        if not book:
            assert value is None
            continue
        repaired += 1
        assert all(0 < v <= start[k] for k, v in book.items()) and (halvings or removed)
        nested = search._nested(np.array([book.get(i, 0) for i in range(search.N)]))
        assert scorer.check_feasible(nested)["feasible"]
        assert value == pytest.approx(_exact(scorer, nested), abs=1e-9) == pytest.approx(search._trial(book), abs=1e-9)
    assert repaired


def test_the_single_starts_are_the_exact_best_contract_of_each_group_from_cash():
    scorer = _scorer(_stuck_program())
    search = KellyMultiStart(scorer, NARROW, {**MULTI, "feasible_starts": {**FEASIBLE, "singles": 3}})
    search._load(np.zeros(search.N, dtype=np.int64))
    exact = {i: search._trial({i: 1}) for i in range(search.N)}
    best = {}
    for i, value in exact.items():
        g = int(search.gid[i])
        if value is not None and value > 1e-9*search.w0 and (g not in best or value > exact[best[g]]):
            best[g] = i
    expected = sorted(best.values(), key=lambda i: -exact[i])[:3]
    assert [next(iter(s)) for s in search._singles()] == expected and expected
    off = KellyMultiStart(scorer, NARROW, {**MULTI, "feasible_starts": {**FEASIBLE, "singles": 0, "units": False}})
    bounds, books = off.lp_books()
    kept, starts = off.starts(bounds, books)
    off._load(np.zeros(off.N, dtype=np.int64))
    assert off.extra_starts(kept, books, starts) == starts
    only_units = KellyMultiStart(scorer, NARROW, {**MULTI, "feasible_starts": {**FEASIBLE, "singles": 0}})
    only_units._load(np.zeros(only_units.N, dtype=np.int64))
    units = only_units.extra_starts(kept, books, [])
    assert [k for k, _s in units] == ["unit"]*len(units) and all(set(s.values()) == {1} for _k, s in units)


def test_amendment_1_is_deterministic_and_leaves_the_registered_search_alone():
    """Two runs and two instances agree; without the block the record has none of the new fields, the start list is
    the amended list's prefix, and the estimates are ADR-0307's own."""
    import json

    program = _stuck_program()
    first = KellyMultiStart(_scorer(program), NARROW, MULTI2).run_multistart()
    search = KellyMultiStart(_scorer(program), NARROW, MULTI2)
    for again in (search.run_multistart(), search.run_multistart()):
        assert again["quantities"] == first["quantities"] and again["record"] == first["record"]
    assert json.loads(json.dumps(first["record"])) == first["record"]
    plain = KellyMultiStart(_scorer(program), NARROW, MULTI)
    old = plain.run_multistart()["record"]["multistart"]
    new = first["record"]["multistart"]
    assert set(new)-set(old) == {"starts_repaired", "starts_emptied", "starts_distinct", "start_kinds", "start_stats"}
    assert "feasible_starts" not in old["declared"] and old["starts_digest"] != new["starts_digest"]
    bounds, books = plain.lp_books()
    kept, starts = plain.starts(bounds, books)
    assert search.starts(bounds, books) == (kept, starts)
    search._load(np.zeros(search.N, dtype=np.int64))
    assert search.extra_starts(kept, books, starts)[:len(starts)] == starts
    plain._load(np.zeros(plain.N, dtype=np.int64))
    for mine, theirs in zip(plain._estimates(), KellyLocalSearch._estimates(plain)):
        assert (mine == theirs).all()
    assert not hasattr(plain, "add_spread")


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("cap", [30., 120., 5000.])
def test_amendment_1_is_never_below_the_registered_search_on_the_synthetic_fixtures(seed, cap):
    """Cash is always climbed and the pair shortlist is unchanged, so these fixtures lose nothing; not a theorem."""
    scorer = _scorer(_program(n_groups=6, n_pairs=3, seed=seed, capacity=8, reserve_cap=cap))
    old = KellyMultiStart(scorer, PARAMS, MULTI).run_multistart()["record"]["end_value"]
    new = KellyMultiStart(scorer, PARAMS, MULTI2).run_multistart()
    assert new["record"]["end_value"] >= old-1e-9 and scorer.check_feasible(new["quantities"])["feasible"]

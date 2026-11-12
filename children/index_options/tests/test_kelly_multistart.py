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

"""``family-availability`` combination counts (ADR-0226): parity and limits.

The ``combinations`` port is pinned in ``test_family_availability.py``. Here it
is checked against a naive subset enumeration on seeded random availability
masks (the seed is a pytest parameter, so a failure names it), plus the edge
cases: the all-no date, zero rows, ``emit`` modes, the empty set equalling the
cohort total, the ``max_families`` refusal, deterministic ordering, and n=13
finishing quickly with one flat array rather than 2^n joins.
"""

import random
import time

import pytest

from dskit.pipeline.base import ConfigError
from dskit.pipeline.kinds_availability import FamilyAvailability

SEEDS = [0, 1, 2, 3, 4, 5, 6, 7]


def names(n):
    return [f"fam{i:02d}" for i in range(n)]


def build(n, masks, **comb):
    """Return (params, rows): one row per date; family i present iff mask bit i."""
    fams = names(n)
    fields = [f"x{i:02d}" for i in range(n)]
    p = {
        "families": {f: {"fields": [x]} for f, x in zip(fams, fields)},
        "schema_fields": ["date", *fields],
        "date_field": "date",
    }
    if comb:
        p["combinations"] = comb
    rows = []
    for d, mask in enumerate(masks):
        r = {"date": f"d{d:05d}"}
        for i, x in enumerate(fields):
            r[x] = 1.0 if mask[i] else None
        rows.append(r)
    return p, rows


def run(n, masks, **comb):
    p, rows = build(n, masks, **comb)
    return FamilyAvailability("fa", params=p).run(None, {"records": rows})


def naive(n, masks):
    """Subset enumeration in ascending-bitmask order, by direct set tests."""
    fams = names(n)
    out = []
    for bits in range(1 << n):
        idx = [i for i in range(n) if bits >> i & 1]
        count = sum(all(m[i] for i in idx) for m in masks)
        out.append(([fams[i] for i in idx], count))
    return out


def random_masks(rng, n, dates):
    return [[rng.random() < 0.6 for _ in range(n)] for _ in range(dates)]


@pytest.mark.parametrize("seed", SEEDS)
def test_parity_with_naive_enumeration(seed):
    rng = random.Random(seed)
    n = rng.randint(1, 6)
    masks = random_masks(rng, n, rng.randint(1, 40))
    got = run(n, masks)["combinations"]
    want = naive(n, masks)
    assert [(r["combination"], r["dates"]) for r in got] == want
    assert [r["size"] for r in got] == [len(c) for c, _ in want]


def test_all_no_date_counts_only_in_empty_set():
    out = run(3, [[False] * 3])["combinations"]
    assert len(out) == 8
    assert out[0]["combination"] == [] and out[0]["dates"] == 1
    assert all(r["dates"] == 0 for r in out[1:])


def test_zero_rows():
    out = run(2, [])
    assert [(r["combination"], r["dates"]) for r in out["combinations"]] == [
        ([], 0),
        (["fam00"], 0),
        (["fam01"], 0),
        (["fam00", "fam01"], 0),
    ]
    assert out["cohort"] == [{"dates": 0, "rows": 0, "first_date": None, "last_date": None}]


def test_empty_set_equals_cohort_total():
    masks = random_masks(random.Random(11), 4, 25)
    out = run(4, masks)
    assert out["combinations"][0]["combination"] == []
    assert out["combinations"][0]["dates"] == out["cohort"][0]["dates"] == 25


def test_emit_defaults_to_all():
    masks = [[True, False], [True, False]]
    default = run(2, masks)["combinations"]
    explicit = run(2, masks, emit="all")["combinations"]
    assert default == explicit
    assert len(default) == 4


def test_emit_nonzero_drops_zero_counts_keeps_empty_set():
    masks = [[True, False], [True, False]]
    out = run(2, masks, emit="nonzero")["combinations"]
    assert [(r["combination"], r["dates"]) for r in out] == [
        ([], 2),
        (["fam00"], 2),
    ]
    out = run(2, [[False, False]], emit="nonzero")["combinations"]
    assert [(r["combination"], r["dates"]) for r in out] == [([], 1)]
    assert run(2, [], emit="nonzero")["combinations"][0]["dates"] == 0


@pytest.mark.parametrize("seed", SEEDS[:3])
def test_nonzero_is_filter_of_all(seed):
    rng = random.Random(seed)
    masks = random_masks(rng, 5, 8)
    full = run(5, masks, emit="all")["combinations"]
    cut = run(5, masks, emit="nonzero")["combinations"]
    assert cut == [r for r in full if r["dates"] > 0 or r["size"] == 0]


def test_max_families_refusal():
    p, _ = build(3, [], max_families=2)
    assert FamilyAvailability.validate_params(p)
    with pytest.raises(ConfigError):
        FamilyAvailability("fa", params=p)
    p, _ = build(3, [], max_families=3)
    assert FamilyAvailability.validate_params(p) == []


def test_ordering_is_deterministic_and_input_order_free():
    rng = random.Random(5)
    masks = random_masks(rng, 4, 12)
    first = run(4, masks)["combinations"]
    shuffled = masks[:]
    rng.shuffle(shuffled)
    p, rows = build(4, shuffled)
    again = FamilyAvailability("fa", params=p).run(None, {"records": rows})
    assert again["combinations"] == first
    assert run(4, masks)["combinations"] == first
    assert [r["combination"] for r in first] == [c for c, _ in naive(4, masks)]
    for r in first:
        assert r["combination"] == sorted(r["combination"])


def test_family_declaration_order_does_not_change_ordering():
    p, rows = build(3, [[True, True, False]])
    p["families"] = dict(reversed(list(p["families"].items())))
    out = FamilyAvailability("fa", params=p).run(None, {"records": rows})
    assert out["combinations"][1]["combination"] == ["fam00"]


def test_thirteen_families_fast_and_flat():
    n = 13
    rng = random.Random(13)
    masks = random_masks(rng, n, 300)
    start = time.perf_counter()
    out = run(n, masks)["combinations"]
    assert time.perf_counter() - start < 5.0
    assert len(out) == 1 << n
    assert out[0]["dates"] == 300
    assert out[-1]["combination"] == names(n)
    for bits in rng.sample(range(1 << n), 40):
        idx = [i for i in range(n) if bits >> i & 1]
        assert out[bits]["dates"] == sum(all(m[i] for i in idx) for m in masks)

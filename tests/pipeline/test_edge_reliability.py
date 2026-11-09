"""ADR-0311 stage 1: settled-before gate, fixed buckets, incremental linear edge state.

The expected values are restated independently here, never read from the
module under test: a batch weighted least squares in matrix form, the
cluster sandwich summed row by row (CR1 ``G/(G-1) * (n-1)/(n-2)``, two-way =
name + date - (name, date)), and bucket indices by arithmetic. The public
``clustered_calibration_slope`` is the second oracle for the expanding state.
"""

import datetime
import json
import math

import numpy as np
import pytest

import dskit.pipeline.edge_reliability as er
from dskit.pipeline.edge_reliability import (
    LinearEdgeState,
    ReliabilityBuckets,
    SettledBefore,
    _Refusal,
)
from dskit.pipeline.ordering import clustered_calibration_slope

EDGES = {"dte_edges": [8, 22], "abs_z_edges": [1.0], "spread_edges": [0.15]}
FIRST_SETTLE = datetime.date(2020, 1, 10)  # a Friday
N_SETTLE = 8
N_BUCKETS = 4


def _iso(day):
    return day.isoformat()


def _clusters():
    """Synthetic (name, settle date) clusters spanning several buckets."""
    rng = np.random.default_rng(11)
    names = ["AA", "BB", "CC", "DD", "EE", "FF"]
    out = []
    for k in range(N_SETTLE):
        settle = FIRST_SETTLE + datetime.timedelta(days=7 * k)
        shock = rng.normal(0, 3)
        for name in names:
            if rng.random() < 0.15:
                continue
            skill = {"AA": 0.8, "BB": 0.2, "CC": -0.3, "DD": 0.5, "EE": 0.0, "FF": 0.6}[name]
            m = int(rng.integers(4, 11))
            e = rng.normal(0, 2, m)
            bucket = rng.integers(0, 3, m)
            r = 0.3 + skill * e + shock + rng.normal(0, 1.5, m) + 0.4 * bucket
            out.append({
                "name": name, "settle": _iso(settle),
                "fill": _iso(settle - datetime.timedelta(days=3)),
                "bucket": [int(b) for b in bucket], "e": [float(x) for x in e],
                "r": [float(x) for x in r],
            })
    return out


def _weeks():
    """Decision Fridays: the week after each settle date, plus two empty weeks."""
    return [_iso(FIRST_SETTLE + datetime.timedelta(days=7 * (k + 1))) for k in range(N_SETTLE + 2)]


def _drive(state, clusters, weeks, lam=None, upto=None):
    """Run the walk's order: advance, then add every cluster settled strictly before t.

    Returns the oracle rows (with weights at the final week) and the week index.
    """
    gate = SettledBefore("2021-07-01")
    arrival = {}
    for k, t in enumerate(weeks if upto is None else weeks[: upto + 1]):
        state.advance(t)
        for i, c in enumerate(clusters):
            if i not in arrival and gate.admits(c["settle"], t):
                state.add_cluster(t, c["name"], c["settle"], c["fill"], c["bucket"], c["e"], c["r"])
                arrival[i] = k
    last = len(weeks) - 1 if upto is None else upto
    rows = []
    for i, k0 in arrival.items():
        c = clusters[i]
        w = 1.0 if lam is None else lam ** (last - k0)
        for b, e, r in zip(c["bucket"], c["e"], c["r"]):
            rows.append({"name": c["name"], "date": c["settle"], "bucket": b, "e": e, "r": r, "w": w})
    return rows


def _wls(rows):
    x = np.array([[1.0, q["e"]] for q in rows])
    y = np.array([q["r"] for q in rows])
    w = np.array([q["w"] for q in rows])
    xtwx = x.T @ (w[:, None] * x)
    beta = np.linalg.solve(xtwx, x.T @ (w * y))
    return x, y, w, xtwx, beta


def _cov(rows, labels, beta):
    """CR1 sandwich of the weighted fit for one clustering (row-by-row)."""
    x, y, w, xtwx, _ = _wls(rows)
    resid = y - x @ beta
    n = len(rows)
    sums = {}
    for xi, ri, wi, lab in zip(x, resid, w, labels):
        sums.setdefault(lab, np.zeros(2))
        sums[lab] = sums[lab] + wi * xi * ri
    meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((2, 2)))
    g = len(sums)
    bread = np.linalg.inv(xtwx)
    return g / (g - 1) * (n - 1) / (n - 2) * bread @ meat @ bread


def _expected(rows, way):
    _, _, _, _, beta = _wls(rows)
    names = [q["name"] for q in rows]
    dates = [q["date"] for q in rows]
    pairs = list(zip(names, dates))
    if way == "one":
        return beta, _cov(rows, pairs, beta)
    two = _cov(rows, names, beta) + _cov(rows, dates, beta) - _cov(rows, pairs, beta)
    return beta, two


def _arr(m):
    return np.array([list(r) for r in m], float)


UNITS = [None, 0, 1, 2]


def _unit_rows(rows, unit):
    return rows if unit is None else [q for q in rows if q["bucket"] == unit]


# ---------------------------------------------------------------- parity (ADR test 8)


class TestExpandingParity:
    @pytest.fixture(scope="class")
    def built(self):
        clusters, weeks = _clusters(), _weeks()
        state = LinearEdgeState(N_BUCKETS, None)
        rows = _drive(state, clusters, weeks)
        return state, rows

    @pytest.mark.parametrize("unit", UNITS)
    def test_fit_equals_batch_least_squares(self, built, unit):
        state, rows = built
        sel = _unit_rows(rows, unit)
        _, _, _, _, beta = _wls(sel)
        fit = state.fit(unit)
        assert fit["n"] == len(sel)
        assert abs(fit["a"] - beta[0]) < 1e-9
        assert abs(fit["b"] - beta[1]) < 1e-9

    @pytest.mark.parametrize("way", ["one", "two"])
    @pytest.mark.parametrize("unit", UNITS)
    def test_sandwich_equals_direct_computation(self, built, unit, way):
        state, rows = built
        sel = _unit_rows(rows, unit)
        beta, cov = _expected(sel, way)
        out = state.sandwich(unit, (beta[0], beta[1]), way)
        assert out["rule"] == ("one_way" if way == "one" else "two_way")
        assert np.max(np.abs(_arr(out["cov"]) - cov)) < 1e-9 * max(1.0, np.max(np.abs(cov)))

    def test_slope_se_equals_clustered_calibration_slope(self, built):
        state, rows = built
        e = [q["e"] for q in rows]
        r = [q["r"] for q in rows]
        pairs = [f"{q['name']}|{q['date']}" for q in rows]
        names = [q["name"] for q in rows]
        dates = [q["date"] for q in rows]
        fit = state.fit(None)
        theta = (fit["a"], fit["b"])
        one = clustered_calibration_slope(r, e, [pairs])
        two = clustered_calibration_slope(r, e, [names, dates])
        s1 = state.sandwich(None, theta, "one")
        s2 = state.sandwich(None, theta, "two")
        assert math.isclose(fit["b"], one["slope"], rel_tol=1e-9)
        assert math.isclose(math.sqrt(s1["cov"][1][1]), one["slope_se"], rel_tol=1e-9)
        assert math.isclose(math.sqrt(s2["cov"][1][1]), two["slope_se"], rel_tol=1e-9)
        assert s1["df"] == one["df"]
        assert s2["df"] == two["df"] and s2["rule"] == two["variance_rule"]

    def test_sandwich_at_another_theta_uses_that_theta(self, built):
        state, rows = built
        theta = (0.1, 0.4)  # a shrunk point, not the unit's own fit
        x, y, w, xtwx, _ = _wls(rows)
        resid = y - x @ np.array(theta)
        pairs = [(q["name"], q["date"]) for q in rows]
        sums = {}
        for xi, ri, lab in zip(x, resid, pairs):
            sums[lab] = sums.get(lab, np.zeros(2)) + xi * ri
        meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((2, 2)))
        g, n = len(sums), len(rows)
        bread = np.linalg.inv(xtwx)
        want = g / (g - 1) * (n - 1) / (n - 2) * bread @ meat @ bread
        got = _arr(state.sandwich(None, theta, "one")["cov"])
        assert np.max(np.abs(got - want)) < 1e-9 * np.max(np.abs(want))

    def test_state_is_identical_at_every_intermediate_week(self):
        clusters, weeks = _clusters(), _weeks()
        for upto in (2, 4, 6):
            state = LinearEdgeState(N_BUCKETS, None)
            rows = _drive(state, clusters, weeks, upto=upto)
            _, _, _, _, beta = _wls(rows)
            assert abs(state.fit(None)["b"] - beta[1]) < 1e-9


class TestDecayedParity:
    LAM = 2 ** (-1 / 3)

    @pytest.fixture(scope="class")
    def built(self):
        clusters, weeks = _clusters(), _weeks()
        state = LinearEdgeState(N_BUCKETS, self.LAM)
        rows = _drive(state, clusters, weeks, lam=self.LAM)
        return state, rows

    @pytest.mark.parametrize("unit", UNITS)
    def test_fit_equals_explicitly_weighted_batch(self, built, unit):
        state, rows = built
        sel = _unit_rows(rows, unit)
        _, _, _, _, beta = _wls(sel)
        fit = state.fit(unit)
        assert abs(fit["a"] - beta[0]) < 1e-9 and abs(fit["b"] - beta[1]) < 1e-9
        assert math.isclose(fit["n"], sum(q["w"] for q in sel), rel_tol=1e-12)

    @pytest.mark.parametrize("way", ["one", "two"])
    @pytest.mark.parametrize("unit", UNITS)
    def test_sandwich_equals_weighted_sandwich(self, built, unit, way):
        state, rows = built
        sel = _unit_rows(rows, unit)
        beta, cov = _expected(sel, way)
        got = _arr(state.sandwich(unit, (beta[0], beta[1]), way)["cov"])
        assert np.max(np.abs(got - cov)) < 1e-9 * max(1.0, np.max(np.abs(cov)))

    @pytest.mark.parametrize("way", ["one", "two"])
    def test_cluster_products_decay_by_lambda_squared(self, way):
        # Two clusters age k weeks: n scales lambda^k; the meat (cluster cross-products, name and date
        # sums) scales lambda^2k and the bread squared 1/lambda^2k, so cov at a fixed theta is unchanged.
        lam = 0.5
        st = LinearEdgeState(1, lam)
        st.advance("2020-02-07")
        st.add_cluster("2020-02-07", "AA", "2020-02-01", "2020-01-30", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0])
        st.add_cluster("2020-02-07", "BB", "2020-02-01", "2020-01-30", [0] * 3, [0.5, 3.0, 5.0], [2.0, 1.0, 6.0])
        st.add_cluster("2020-02-07", "AA", "2020-02-02", "2020-01-30",
                       [0] * 3, [1.5, 2.5, 3.5], [0.0, 4.0, 1.0])
        before = _arr(st.sandwich(0, (0.3, 0.7), way)["cov"])
        n0 = st.fit(0)["n"]
        for t in ("2020-02-14", "2020-02-21"):
            st.advance(t)
        assert math.isclose(st.fit(0)["n"], n0 * lam**2, rel_tol=1e-12)
        after = _arr(st.sandwich(0, (0.3, 0.7), way)["cov"])
        assert np.max(np.abs(after - before)) < 1e-9 * np.max(np.abs(before))

    def test_older_clusters_weigh_less_than_newer_ones(self):
        st = LinearEdgeState(1, 0.5)
        st.advance("2020-02-07")
        st.add_cluster("2020-02-07", "AA", "2020-02-01", "2020-01-30", [0, 0], [0.0, 1.0], [0.0, 1.0])
        st.advance("2020-02-14")
        st.add_cluster("2020-02-14", "BB", "2020-02-08", "2020-02-05", [0, 0], [0.0, 1.0], [5.0, 5.0])
        # weights 0.5 (old) and 1 (new): weighted mean of r at e=0 is (0.5*0 + 1*5)/1.5
        fit = st.fit(0)
        assert math.isclose(fit["n"], 3.0, rel_tol=1e-12)


class TestIncremental:
    def test_state_is_built_from_running_sums_not_history(self):
        clusters, weeks = _clusters(), _weeks()
        st = LinearEdgeState(N_BUCKETS, None)
        _drive(st, clusters, weeks)
        blob = st.to_dict()
        # no row-level history: the dump holds sums, one 5-vector per name and per date, and the seen keys
        text = json.dumps(blob)
        assert len(text) < 60_000

    def test_round_trip_is_byte_identical_and_continues_identically(self):
        clusters, weeks = _clusters(), _weeks()
        a = LinearEdgeState(N_BUCKETS, 0.9)
        _drive(a, clusters, weeks, lam=0.9, upto=5)
        b = LinearEdgeState.from_dict(json.loads(json.dumps(a.to_dict())))
        assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)
        for st in (a, b):
            for t in weeks[6:]:
                st.advance(t)
                for c in clusters:
                    if c["settle"] < t and (c["name"], c["settle"]) not in st._seen:
                        st.add_cluster(t, c["name"], c["settle"], c["fill"], c["bucket"], c["e"], c["r"])
        assert json.dumps(a.to_dict(), sort_keys=True) == json.dumps(b.to_dict(), sort_keys=True)

    def test_zero_variance_unit_refuses_a_fit_and_a_sandwich(self):
        st = LinearEdgeState(2, None)
        st.advance("2020-02-07")
        st.add_cluster("2020-02-07", "AA", "2020-02-01", "2020-01-30", [1, 1, 1], [2.0, 2.0, 2.0], [1.0, 2.0, 3.0])
        fit = st.fit(1)
        assert fit["a"] is None and fit["b"] is None and fit["n"] == 3
        assert st.sandwich(1, (0.0, 0.0), "one") is None

    def test_unused_unit_has_no_rows(self):
        st = LinearEdgeState(3, None)
        assert st.fit(2)["n"] == 0 and st.fit(2)["a"] is None

    def test_too_few_clusters_returns_no_sandwich(self):
        st = LinearEdgeState(1, None)
        st.advance("2020-02-07")
        st.add_cluster("2020-02-07", "AA", "2020-02-01", "2020-01-30", [0] * 4, [1.0, 2.0, 3.0, 4.0], [1.0, 3.0, 2.0, 5.0])
        assert st.sandwich(0, (0.0, 1.0), "one") is None


# ---------------------------------------------------------------- leakage guard (ADR tests 1, 4)


class TestLeakageGuard:
    T = "2020-03-06"

    def _state(self):
        st = LinearEdgeState(2, None)
        st.advance(self.T)
        return st

    @pytest.mark.parametrize("settle", ["2020-03-06", "2020-03-07", "2020-04-01"])
    def test_settlement_at_or_after_t_refuses_state_from_future(self, settle):
        with pytest.raises(_Refusal) as exc:
            self._state().add_cluster(self.T, "AA", settle, "2020-02-01", [0], [1.0], [1.0])
        assert exc.value.code == "state_from_future"

    @pytest.mark.parametrize("fill", ["2020-03-01", "2020-03-02"])
    def test_fill_at_or_after_settlement_refuses_state_from_future(self, fill):
        with pytest.raises(_Refusal) as exc:
            self._state().add_cluster(self.T, "AA", "2020-03-01", fill, [0], [1.0], [1.0])
        assert exc.value.code == "state_from_future"

    def test_day_before_t_and_fill_before_settlement_is_admitted(self):
        st = self._state()
        st.add_cluster(self.T, "AA", "2020-03-05", "2020-03-04", [0, 1], [1.0, 2.0], [1.0, 2.0])
        assert st.fit(None)["n"] == 2

    def test_refusal_leaves_the_state_untouched(self):
        st = self._state()
        before = json.dumps(st.to_dict(), sort_keys=True)
        with pytest.raises(_Refusal):
            st.add_cluster(self.T, "AA", self.T, "2020-03-01", [0], [1.0], [1.0])
        assert json.dumps(st.to_dict(), sort_keys=True) == before

    def test_poisoned_future_changes_nothing_at_t(self):
        clusters = _clusters()
        weeks = _weeks()
        t = weeks[4]
        poison = []
        for c in clusters:
            if c["settle"] >= t:
                p = dict(c)
                p["e"] = [1e12 * (i + 1) for i in range(len(c["e"]))]
                p["r"] = [-1e15] * len(c["r"])
                poison.append(p)
        assert poison  # the fixture has rows settling at or after t
        clean = LinearEdgeState(N_BUCKETS, 0.9)
        dirty = LinearEdgeState(N_BUCKETS, 0.9)
        _drive(clean, clusters, weeks, lam=0.9, upto=4)
        # the same walk with the future rows replaced by absurd values
        swapped = [_poison(c) if c["settle"] >= t else c for c in clusters]
        _drive(dirty, swapped, weeks, lam=0.9, upto=4)
        assert json.dumps(clean.to_dict(), sort_keys=True) == json.dumps(dirty.to_dict(), sort_keys=True)
        for p in poison:
            with pytest.raises(_Refusal) as exc:
                dirty.add_cluster(t, p["name"], p["settle"], p["fill"], p["bucket"], p["e"], p["r"])
            assert exc.value.code == "state_from_future"
        assert json.dumps(clean.to_dict(), sort_keys=True) == json.dumps(dirty.to_dict(), sort_keys=True)

    def test_time_cannot_run_backwards(self):
        st = self._state()
        with pytest.raises(ValueError, match="backward"):
            st.advance("2020-02-28")

    def test_a_cluster_arrives_whole_once(self):
        st = self._state()
        st.add_cluster(self.T, "AA", "2020-03-01", "2020-02-26", [0], [1.0], [1.0])
        with pytest.raises(ValueError, match="already"):
            st.add_cluster(self.T, "AA", "2020-03-01", "2020-02-26", [0], [2.0], [2.0])


def _poison(c):
    p = dict(c)
    p["e"] = [1e12 * (i + 1) for i in range(len(c["e"]))]
    p["r"] = [-1e15] * len(c["r"])
    return p


class TestAddClusterInputs:
    @pytest.mark.parametrize("bad", [
        dict(name=""), dict(name=3), dict(buckets=[5]), dict(buckets=[-1]), dict(buckets=[True]),
        dict(e=[float("nan")]), dict(r=[float("inf")]), dict(e=[1.0, 2.0]), dict(buckets=[]),
        dict(e=[]), dict(settle="20200301"), dict(fill="soon"),
    ])
    def test_bad_cluster_is_a_value_error(self, bad):
        args = dict(name="AA", settle="2020-03-01", fill="2020-02-28", buckets=[0], e=[1.0], r=[1.0])
        args.update(bad)
        st = LinearEdgeState(2, None)
        st.advance("2020-03-06")
        with pytest.raises(ValueError):
            st.add_cluster("2020-03-06", args["name"], args["settle"], args["fill"],
                           args["buckets"], args["e"], args["r"])

    @pytest.mark.parametrize("n_buckets", [0, -1, 2.5, True, "3", None])
    def test_bad_bucket_count(self, n_buckets):
        with pytest.raises(ValueError):
            LinearEdgeState(n_buckets, None)

    @pytest.mark.parametrize("decay", [0.0, 1.0, 1.5, -0.2, True, "0.9", float("nan")])
    def test_bad_decay(self, decay):
        with pytest.raises(ValueError):
            LinearEdgeState(2, decay)

    def test_bad_way_and_unit(self):
        st = LinearEdgeState(2, None)
        with pytest.raises(ValueError):
            st.sandwich(None, (0.0, 0.0), "three")
        with pytest.raises(ValueError):
            st.fit(2)
        with pytest.raises(ValueError):
            st.fit(-1)


# ---------------------------------------------------------------- SettledBefore


class TestSettledBefore:
    def test_admits_only_strictly_before_t(self):
        g = SettledBefore("2021-07-01")
        assert g.admits("2020-03-05", "2020-03-06") is True
        assert g.admits("2020-03-06", "2020-03-06") is False  # same day is excluded
        assert g.admits("2020-03-07", "2020-03-06") is False

    def test_dates_and_iso_strings_agree(self):
        g = SettledBefore("2021-07-01")
        d = datetime.date
        assert g.admits(d(2020, 3, 5), d(2020, 3, 6)) is True
        assert g.admits(d(2020, 3, 6), d(2020, 3, 6)) is False
        assert g.admits(d(2020, 3, 5), "2020-03-06") is True
        assert g.admits("2020-03-06", d(2020, 3, 6)) is False

    def test_boundary_is_label_reaches_not_a_restated_comparison(self, monkeypatch):
        g = SettledBefore("2021-07-01")
        monkeypatch.setattr(er, "label_reaches", lambda end, start: True)
        assert g.admits("2000-01-01", "2020-03-06") is False
        monkeypatch.setattr(er, "label_reaches", lambda end, start: False)
        assert g.admits("2030-01-01", "2020-03-06") is True

    def test_holiday_thursday_before_friday_t_is_admitted(self):
        assert SettledBefore("2021-07-01").admits("2020-12-24", "2020-12-25") is True

    @pytest.mark.parametrize("bad", ["", "2020-3-6", "20200306", "soon", None, 3, "2020-03-06T00:00"])
    def test_bad_dates_refuse(self, bad):
        with pytest.raises(ValueError):
            SettledBefore("2021-07-01").admits(bad, "2020-03-06")

    def test_window_inside_the_rights_passes(self):
        SettledBefore("2021-07-01").check_window(["2019-10-01", "2020-03-06", "2021-06-30"])

    @pytest.mark.parametrize("dates", [["2021-07-01"], ["2020-01-01", "2021-12-31"], ["2024-01-01"]])
    def test_date_at_or_after_sealed_from_refuses(self, dates):
        with pytest.raises(_Refusal) as exc:
            SettledBefore("2021-07-01").check_window(dates)
        assert exc.value.code == "window_outside_data_rights"

    def test_2024_is_refused_whatever_the_config(self, monkeypatch):
        monkeypatch.setattr(er, "DEVELOPMENT_SEAL", "2030-01-01")
        gate = SettledBefore("2025-06-01")
        gate.check_window(["2023-12-31"])
        with pytest.raises(_Refusal) as exc:
            gate.check_window(["2024-01-01"])
        assert exc.value.code == "window_outside_data_rights"

    def test_sealed_from_is_required(self):
        for missing in (None, ""):
            with pytest.raises(_Refusal) as exc:
                SettledBefore(missing)
            assert exc.value.code == "sealed_from_missing"

    def test_seal_cannot_exceed_the_development_seal(self):
        assert er.DEVELOPMENT_SEAL == "2021-07-01"
        with pytest.raises(_Refusal) as exc:
            SettledBefore("2021-07-02")
        assert exc.value.code == "development_seal_exceeded"
        SettledBefore("2021-07-01")
        SettledBefore("2020-01-01")

    def test_malformed_sealed_from_is_a_value_error(self):
        with pytest.raises(ValueError):
            SettledBefore("July 2021")


# ---------------------------------------------------------------- ReliabilityBuckets


def _index(d, z, s, g):
    return ((d * 2 + z) * 2 + s) * 2 + g


class TestReliabilityBuckets:
    def _b(self, **kw):
        return ReliabilityBuckets({**EDGES, **kw})

    def test_params_tuple(self):
        assert ReliabilityBuckets._PARAMS == ("dte_edges", "abs_z_edges", "spread_edges")

    def test_24_distinct_buckets_in_range(self):
        b = self._b()
        seen = set()
        for dte in (3, 12, 40):
            for z in (0.2, 1.7):
                for sp in (0.05, 0.3):
                    for reg in ("low", "high"):
                        seen.add(b.of(dte, z, sp, reg))
        assert seen == set(range(24))

    @pytest.mark.parametrize("dte,bin_", [(1, 0), (7, 0), (7.9, 0), (8, 1), (21, 1), (21.99, 1), (22, 2), (90, 2)])
    def test_dte_edges_start_a_bin(self, dte, bin_):
        assert self._b().of(dte, 0.0, 0.0, "low") == _index(bin_, 0, 0, 0)

    @pytest.mark.parametrize("z,bin_", [(0.0, 0), (1.0, 0), (1.0000001, 1), (5.0, 1)])
    def test_abs_z_edge_belongs_to_the_inner_bin(self, z, bin_):
        assert self._b().of(10, z, 0.0, "low") == _index(1, bin_, 0, 0)

    @pytest.mark.parametrize("sp,bin_", [(0.0, 0), (0.15, 0), (0.1500001, 1), (0.5, 1)])
    def test_spread_edge_belongs_to_the_tight_bin(self, sp, bin_):
        assert self._b().of(10, 0.0, sp, "low") == _index(1, 0, bin_, 0)

    def test_regime_axis(self):
        b = self._b()
        assert b.of(10, 0.0, 0.0, "low") == _index(1, 0, 0, 0)
        assert b.of(10, 0.0, 0.0, "high") == _index(1, 0, 0, 1)

    def test_edges_are_fixed_config_values_not_data(self):
        b = self._b()
        first = b.of(10, 0.5, 0.1, "low")
        for v in (1e9, -1e9, 0.0, 3.3):  # extreme calls teach it nothing
            b.of(v, abs(v), abs(v), "high")
        assert b.of(10, 0.5, 0.1, "low") == first
        c = ReliabilityBuckets({"dte_edges": [5, 30], "abs_z_edges": [0.5], "spread_edges": [0.2]})
        assert c.of(10, 0.5, 0.1, "low") == _index(1, 0, 0, 0)
        assert c.of(31, 0.51, 0.21, "high") == _index(2, 1, 1, 1)

    @pytest.mark.parametrize("edges", [
        {"dte_edges": [22, 8]}, {"dte_edges": [8, 8]}, {"dte_edges": [8]}, {"dte_edges": [8, 22, 40]},
        {"dte_edges": [8, float("nan")]}, {"dte_edges": [8, float("inf")]}, {"dte_edges": []},
        {"abs_z_edges": [1.0, 2.0]}, {"abs_z_edges": []}, {"spread_edges": [0.1, 0.2]},
        {"spread_edges": "q50"}, {"spread_edges": ["q50"]}, {"dte_edges": ["q33", "q66"]},
        {"abs_z_edges": [{"quantile": 0.5}]}, {"dte_edges": {"quantiles": [0.33, 0.66]}},
        {"abs_z_edges": [True]}, {"spread_edges": None}, {"dte_edges": 8},
        {"abs_z_edges": ["1.0"]},
    ])
    def test_bad_edges_refuse_bucket_edges_invalid(self, edges):
        with pytest.raises(_Refusal) as exc:
            ReliabilityBuckets({**EDGES, **edges})
        assert exc.value.code == "bucket_edges_invalid"

    @pytest.mark.parametrize("missing", ["dte_edges", "abs_z_edges", "spread_edges"])
    def test_every_edge_set_is_required(self, missing):
        params = {k: v for k, v in EDGES.items() if k != missing}
        with pytest.raises(_Refusal) as exc:
            ReliabilityBuckets(params)
        assert exc.value.code == "bucket_edges_invalid"

    def test_unknown_key_refuses(self):
        with pytest.raises(ValueError, match="unknown"):
            ReliabilityBuckets({**EDGES, "vol_edges": [1.0]})

    @pytest.mark.parametrize("params", [None, [], "dte_edges", 3])
    def test_params_must_be_a_mapping(self, params):
        with pytest.raises(ValueError):
            ReliabilityBuckets(params)

    @pytest.mark.parametrize("call", [
        (float("nan"), 0.0, 0.0, "low"), (10, float("inf"), 0.0, "low"), (10, 0.0, float("nan"), "low"),
        (True, 0.0, 0.0, "low"), (10, -0.5, 0.0, "low"), (10, 0.0, 0.0, "medium"), (10, 0.0, 0.0, 1),
        ("10", 0.0, 0.0, "low"),
    ])
    def test_bad_inputs_refuse(self, call):
        with pytest.raises(ValueError):
            self._b().of(*call)


# ---------------------------------------------------------------- surface


def test_public_surface():
    assert sorted(er.__all__) == sorted(
        ["DEVELOPMENT_SEAL", "LinearEdgeState", "ReliabilityBuckets", "SettledBefore",
         "TrailingRegime"])
    for name in er.__all__:
        assert hasattr(er, name)


# ------------------------------------------------- TrailingRegime (stage 2b)

from dskit.pipeline.edge_reliability import TrailingRegime  # noqa: E402

REGIME = {"regime_lookback_weeks": 52, "regime_min_weeks": 13}
MONDAY0 = datetime.date(2019, 1, 7)


def _wk(i):
    return (MONDAY0 + datetime.timedelta(weeks=i)).isoformat()


def _regime(**over):
    return TrailingRegime({**REGIME, **over})


def _fill_regime(reg, values, name="AA"):
    for i, v in enumerate(values):
        reg.add(name, _wk(i), v)
    return reg


class TestTrailingRegime:
    def test_strictly_before_own_observation_never_counts(self):
        reg = _fill_regime(_regime(), [1.0] * 13)
        # Own value at t = 13 is added AFTER the query; adding it must not move t's answer.
        before = reg.of("AA", _wk(13), 5.0)
        reg.add("AA", _wk(13), 5.0)
        assert reg.of("AA", _wk(13), 5.0) == before == ("high", False)

    def test_poisoned_future_changes_nothing_at_t(self):
        clean = _fill_regime(_regime(), [1.0, 2.0, 3.0] * 5)
        dirty = _fill_regime(_regime(), [1.0, 2.0, 3.0] * 5)
        for k in range(15, 20):
            dirty.add("AA", _wk(k), 1e9)
        for v in (0.5, 2.0, 2.5):
            assert dirty.of("AA", _wk(15), v) == clean.of("AA", _wk(15), v)

    def test_window_is_exactly_52_weeks(self):
        # 100 at week 0, then 1.0 for weeks 1..52: at t = week 53 the window is weeks 1..52
        # (t-52w .. t-1w) so the 100 has dropped out; at t = week 52 week 0 is still in.
        vals = [100.0] + [1.0] * 52
        reg = _fill_regime(_regime(), vals)
        # week 52: window weeks 0..51 = {100, 1x51}: median 1.0, v=1.0 not above
        assert reg.of("AA", _wk(52), 1.0) == ("low", False)
        # week 53 window = weeks 1..52, all 1.0: v slightly above is high
        assert reg.of("AA", _wk(53), 1.0001) == ("high", False)
        # window of 52 observations, not 53: count at week 53
        assert reg.window_size("AA", _wk(53)) == 52
        assert reg.window_size("AA", _wk(52)) == 52

    def test_window_start_is_inclusive_week_and_excludes_older(self):
        reg = _fill_regime(_regime(regime_lookback_weeks=4, regime_min_weeks=1),
                           [10.0, 1.0, 1.0, 1.0, 1.0])
        # t = week 5: window weeks 1..4 (4 obs); week 0 (value 10) is out
        assert reg.window_size("AA", _wk(5)) == 4
        assert reg.of("AA", _wk(5), 1.5) == ("high", False)

    def test_fewer_than_minimum_is_low_with_warmup_flag(self):
        reg = _fill_regime(_regime(), [1.0] * 12)
        assert reg.of("AA", _wk(12), 99.0) == ("low", True)
        assert reg.warmups == 1
        reg.add("AA", _wk(12), 1.0)
        assert reg.of("AA", _wk(13), 99.0) == ("high", False)  # exactly 13 prior
        assert reg.warmups == 1

    def test_unknown_name_is_warmup_low(self):
        assert _regime().of("ZZ", _wk(3), 5.0) == ("low", True)

    def test_names_are_independent(self):
        reg = _fill_regime(_regime(), [1.0] * 13, "AA")
        _fill_regime(reg, [9.0] * 13, "BB")
        assert reg.of("AA", _wk(13), 5.0)[0] == "high"
        assert reg.of("BB", _wk(13), 5.0)[0] == "low"

    def test_tie_at_median_is_low_odd_and_even(self):
        reg = _fill_regime(_regime(), [1.0, 2.0, 3.0] * 4 + [2.0])  # 13 obs, median 2
        assert reg.of("AA", _wk(13), 2.0) == ("low", False)
        assert reg.of("AA", _wk(13), 2.0001) == ("high", False)
        even = _fill_regime(_regime(regime_min_weeks=2), [1.0, 3.0])  # median 2
        assert even.of("AA", _wk(2), 2.0) == ("low", False)
        assert even.of("AA", _wk(2), 2.5) == ("high", False)

    def test_annualization_matches_sqrt_365_over_dte_median_over_expiries(self):
        v = TrailingRegime.annualize([(0.02, 7), (0.04, 28), (0.05, 91)])
        want = sorted([0.02 * math.sqrt(365 / 7), 0.04 * math.sqrt(365 / 28),
                       0.05 * math.sqrt(365 / 91)])[1]
        assert v == pytest.approx(want)
        assert TrailingRegime.annualize([(0.02, 10), (0.04, 10)]) == pytest.approx(
            0.03 * math.sqrt(36.5))

    @pytest.mark.parametrize("pairs", [[], [(0.1, 0)], [(0.1, -3)], [(float("nan"), 5)],
                                       [(-0.1, 5)], [(True, 5)]])
    def test_annualize_refuses_bad_input(self, pairs):
        with pytest.raises(ValueError):
            TrailingRegime.annualize(pairs)

    def test_incremental_state_is_bounded(self):
        reg = _regime()
        for i in range(400):
            reg.add("AA", _wk(i), float(i % 7 + 1))
        assert reg.stored("AA") <= 52 + 1

    def test_matches_batch_median_over_long_series(self):
        rng = np.random.default_rng(5)
        series = rng.uniform(0.1, 1.0, 200).tolist()
        reg = _regime()
        for i, v in enumerate(series):
            got = reg.of("AA", _wk(i), v)
            prior = series[max(0, i - 52):i]
            if len(prior) < 13:
                assert got == ("low", True)
            else:
                assert got == (("high" if v > float(np.median(prior)) else "low"), False)
            reg.add("AA", _wk(i), v)

    def test_out_of_order_add_refuses(self):
        reg = _fill_regime(_regime(), [1.0] * 3)
        with pytest.raises(ValueError, match="out of order"):
            reg.add("AA", _wk(1), 1.0)
        with pytest.raises(ValueError, match="out of order"):
            reg.add("AA", _wk(2), 1.0)  # same date twice

    def test_query_before_evicted_window_refuses(self):
        reg = _fill_regime(_regime(), [1.0] * 80)
        with pytest.raises(ValueError, match="evicted"):
            reg.of("AA", _wk(10), 1.0)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, 0.0, True, "1"])
    def test_bad_value_refuses(self, bad):
        with pytest.raises(ValueError):
            _regime().add("AA", _wk(0), bad)
        with pytest.raises(ValueError):
            _regime().of("AA", _wk(0), bad)

    def test_params_reject_unknown_missing_and_bad(self):
        with pytest.raises(ValueError, match="unknown params"):
            TrailingRegime({**REGIME, "quantile": 0.5})
        with pytest.raises(ValueError):
            TrailingRegime({"regime_lookback_weeks": 52})
        for over in ({"regime_lookback_weeks": 0}, {"regime_min_weeks": 0},
                     {"regime_min_weeks": 60}, {"regime_lookback_weeks": 52.0},
                     {"regime_lookback_weeks": True}):
            with pytest.raises(ValueError):
                _regime(**over)
        with pytest.raises(ValueError):
            TrailingRegime([])

    def test_params_tuple_and_surface(self):
        assert TrailingRegime._PARAMS == ("regime_lookback_weeks", "regime_min_weeks")
        assert er.__all__[-1] == "TrailingRegime"

    def test_does_not_mutate_caller_params(self):
        p = dict(REGIME)
        TrailingRegime(p)
        assert p == REGIME

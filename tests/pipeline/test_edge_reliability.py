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
    EdgeCalibrationMap,
    LinearEdgeState,
    NameAdjustment,
    PartialPooling,
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
    sums, tot_w = {}, {}
    for xi, ri, wi, lab in zip(x, resid, w, labels):
        sums.setdefault(lab, np.zeros(2))
        sums[lab] = sums[lab] + wi * xi * ri
        tot_w[lab] = tot_w.get(lab, 0.0) + wi
    meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((2, 2)))
    g, n = _kish(list(tot_w.values())), _kish(w)  # Kish counts on cluster total / row weights (part C)
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


def _kish(v):
    v = np.asarray(v, float)
    return v.sum() ** 2 / (v ** 2).sum()


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

    def test_slope_se_equals_clustered_calibration_slope(self):
        # the public oracle uses raw counts, so the clusters must be of equal size (Kish G_eff == G exactly)
        st = LinearEdgeState(N_BUCKETS, None)
        rows = _drive_k2(st, _k2_clusters(equal=True), _weeks())
        e = [q["e"] for q in rows]
        r = [q["r"] for q in rows]
        pairs = [f"{q['name']}|{q['date']}" for q in rows]
        names = [q["name"] for q in rows]
        dates = [q["date"] for q in rows]
        fit = st.fit(None)
        theta = (fit["a"], fit["b"])
        one = clustered_calibration_slope(r, e, [pairs])
        two = clustered_calibration_slope(r, e, [names, dates])
        s1 = st.sandwich(None, theta, "one")
        s2 = st.sandwich(None, theta, "two")
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
        sums, tw = {}, {}
        for xi, ri, lab in zip(x, resid, pairs):
            sums[lab] = sums.get(lab, np.zeros(2)) + xi * ri
            tw[lab] = tw.get(lab, 0.0) + 1.0
        meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((2, 2)))
        g, n = _kish(list(tw.values())), len(rows)
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
    assert set(er.__all__) >= {"DEVELOPMENT_SEAL", "LinearEdgeState", "ReliabilityBuckets", "SettledBefore"}
    for name in er.__all__:
        assert hasattr(er, name)


# ================================================================ stage 2a: pooling, name adjustment, map
# Oracles below restate the maths independently (numpy, row by row); only the stage-1 state is read.

POOL = {"min_clusters": 4, "min_dates": 3, "tau2_floor": 1e-6, "weight_cap": 1.0, "lcb_k": 1.0}
NAME = {"name_prior_clusters": 26, "name_min_clusters": 2}
LAM = 0.9


def _built(lam=None, upto=None):
    state = LinearEdgeState(N_BUCKETS, lam)
    rows = _drive(state, _clusters(), _weeks(), lam=lam, upto=upto)
    return state, rows


def _shrink_oracle(state, buckets, floor, min_clusters):
    """theta* = theta_b + V (T + V)^-1 (theta_0 - theta_b), T by method of moments over eligible buckets."""
    f0 = state.fit(None)
    th0 = np.array([f0["a"], f0["b"]])
    elig = {}
    for b in buckets:
        f = state.fit(b)
        if f["clusters"] < min_clusters or f["a"] is None:
            continue
        sw = state.sandwich(b, (f["a"], f["b"]), "two")
        if sw is not None:
            elig[b] = (np.array([f["a"], f["b"]]), _arr(sw["cov"]))
    if len(elig) >= 2:
        th = np.array([v[0] for v in elig.values()])
        mean_v = np.mean([np.diag(v[1]) for v in elig.values()], axis=0)
        tdiag = np.maximum(np.var(th, axis=0, ddof=1) - mean_v, floor)
    else:
        tdiag = np.full(2, floor)
    t = np.diag(tdiag)
    out = {}
    for b in buckets:
        if b in elig:
            thb, v = elig[b]
            out[b] = (thb + v @ np.linalg.solve(t + v, th0 - thb), t @ np.linalg.solve(t + v, v))
        else:
            out[b] = (th0, None)
    return out, tdiag


class TestPartialPoolingParams:
    @pytest.mark.parametrize("bad", [
        {**POOL, "extra": 1}, {k: v for k, v in POOL.items() if k != "lcb_k"},
        {**POOL, "min_clusters": 1}, {**POOL, "min_dates": 0}, {**POOL, "tau2_floor": 0.0},
        {**POOL, "weight_cap": 0.0}, {**POOL, "lcb_k": -1}, {**POOL, "lcb_k": float("nan")},
        {**POOL, "min_clusters": True}, "x", None,
    ])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            PartialPooling(bad)

    def test_params_tuple(self):
        assert PartialPooling._PARAMS == ("min_clusters", "min_dates", "tau2_floor", "weight_cap", "lcb_k")
        assert NameAdjustment._PARAMS == ("name_prior_clusters", "name_min_clusters")


class TestPartialPooling:
    def test_matches_independent_shrinkage(self):
        state, _ = _built()
        res = PartialPooling(POOL).fit(state)
        want, _ = _shrink_oracle(state, range(N_BUCKETS), POOL["tau2_floor"], POOL["min_clusters"])
        assert res["warm"] is True
        for b in range(3):
            assert not res["buckets"][b]["pooled"]
            assert np.allclose([res["buckets"][b]["a"], res["buckets"][b]["b"]], want[b][0], rtol=1e-9)
            assert res["buckets"][b]["se_b"] == pytest.approx(math.sqrt(want[b][1][1][1]), rel=1e-9)

    def test_empty_bucket_takes_the_pool(self):
        state, _ = _built()
        res = PartialPooling(POOL).fit(state)
        f0 = state.fit(None)
        b3 = res["buckets"][3]
        assert b3["pooled"] is True and b3["clusters"] == 0
        assert (b3["a"], b3["b"]) == (f0["a"], f0["b"])

    def test_tiny_bucket_shrinks_to_the_pool(self):
        state, rows = _built()
        # a 5th bucket with 3 clusters (< min_clusters 4) and wild numbers
        state = LinearEdgeState(5, None)
        _drive(state, _clusters(), _weeks())
        for k, name in enumerate(["AA", "BB", "CC"]):
            state.add_cluster(_weeks()[-1], name, "2020-03-06", "2020-03-02", [4, 4], [1.0, 2.0], [50.0, 90.0 + k])
        res = PartialPooling(POOL).fit(state)
        f0 = state.fit(None)
        assert res["buckets"][4]["pooled"] is True and res["buckets"][4]["clusters"] == 3
        assert (res["buckets"][4]["a"], res["buckets"][4]["b"]) == (f0["a"], f0["b"])

    def test_zero_variance_bucket_keeps_its_slope(self):
        state = LinearEdgeState(3, None)
        t = "2020-03-20"
        rng = np.random.default_rng(5)
        k = 0
        for wk in range(5):
            for name in ["AA", "BB", "CC"]:
                day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk))
                e = rng.normal(0, 2, 6)
                state.add_cluster(t, name, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk - 3)),
                                  [0] * 3 + [1] * 3, [float(x) for x in e],
                                  [float(1.0 + 2.0 * x) for x in e[:3]] + [float(rng.normal(0, 3) - 4 * x) for x in e[3:]])
                k += 1
        res = PartialPooling(POOL).fit(state)
        assert res["buckets"][0]["a"] == pytest.approx(1.0, abs=1e-7)
        assert res["buckets"][0]["b"] == pytest.approx(2.0, abs=1e-7)
        assert not res["buckets"][0]["pooled"]

    def test_warm_up_returns_the_prior(self):
        state, _ = _built(upto=1)  # only the first settle date is in
        assert state.fit(None)["dates"] < POOL["min_dates"]
        res = PartialPooling(POOL).fit(state)
        assert res["warm"] is False
        assert all(x["a"] == 0.0 and x["b"] == 0.0 and x["w"] == 0.0 for x in res["buckets"])
        assert res["pooled"]["a"] == 0.0 and res["pooled"]["b"] == 0.0

    def test_warm_up_waits_for_clusters_too(self):
        state, _ = _built()
        res = PartialPooling({**POOL, "min_clusters": 10_000}).fit(state)
        assert res["warm"] is False and res["buckets"][0]["b"] == 0.0

    def test_tau2_floor_binds(self):
        # identical buckets: across-bucket variance 0, so T is the floor and the result is the pool
        state = LinearEdgeState(2, None)
        rng = np.random.default_rng(3)
        for wk in range(5):
            for name in ["AA", "BB", "CC"]:
                e = [float(x) for x in rng.normal(0, 2, 5)]
                r = [float(0.2 + 0.7 * x + rng.normal(0, 1)) for x in e]
                day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk))
                state.add_cluster("2020-03-20", name, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk - 2)),
                                  [0] * 5, e, r)
                state.add_cluster("2020-03-20", name + "x", day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk - 2)),
                                  [1] * 5, e, r)
        floor = 1e-9
        res = PartialPooling({**POOL, "tau2_floor": floor}).fit(state)
        _, tdiag = _shrink_oracle(state, range(2), floor, POOL["min_clusters"])
        assert np.allclose(tdiag, floor)
        f0 = state.fit(None)
        assert res["buckets"][0]["b"] == pytest.approx(f0["b"], rel=1e-4)
        loose = PartialPooling({**POOL, "tau2_floor": 1e6}).fit(state)  # huge floor: no shrinkage
        assert loose["buckets"][0]["b"] == pytest.approx(state.fit(0)["b"], rel=1e-6)

    def test_origin_slope_cap_and_clip(self):
        state = LinearEdgeState(2, None)
        rng = np.random.default_rng(8)
        for wk in range(6):
            for name in ["AA", "BB", "CC", "DD"]:
                e = [float(x) for x in rng.normal(0, 2, 6)]
                day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk))
                fill = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk - 2))
                state.add_cluster("2020-03-20", name, day, fill, [0] * 6, e, [float(3.0 * x + rng.normal(0, .5)) for x in e])
        res = PartialPooling(POOL).fit(state)
        assert res["buckets"][0]["w"] == 1.0  # raw slope ~3, capped at 1
        capped = PartialPooling({**POOL, "weight_cap": 0.5}).fit(state)
        assert capped["buckets"][0]["w"] == 0.5
        neg = LinearEdgeState(1, None)
        for wk in range(6):
            for name in ["AA", "BB", "CC", "DD"]:
                e = [float(x) for x in rng.normal(0, 2, 6)]
                day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk))
                neg.add_cluster("2020-03-20", name, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * wk - 2)),
                                [0] * 6, e, [float(-1.5 * x + rng.normal(0, .5)) for x in e])
        res = PartialPooling(POOL).fit(neg)
        assert res["buckets"][0]["w"] == 0.0 and res["buckets"][0]["w_lcb"] == 0.0

    def test_origin_slope_matches_independent_sandwich(self):
        state, rows = _built()
        res = PartialPooling({**POOL, "weight_cap": 100.0}).fit(state)
        f0 = _origin_oracle(rows)
        assert res["pooled"]["w"] == pytest.approx(f0[0], rel=1e-9)
        assert res["pooled"]["se_w"] == pytest.approx(math.sqrt(f0[1]), rel=1e-9)

    def test_lcb_variants(self):
        state, _ = _built()
        for k in (0.0, 1.0, 2.5):
            res = PartialPooling({**POOL, "lcb_k": k, "weight_cap": 100.0}).fit(state)
            for x in res["buckets"][:3]:
                assert x["b_lcb"] == pytest.approx(x["b"] - k * x["se_b"], rel=1e-12)
                assert x["w_lcb"] == pytest.approx(max(x["w"] - k * x["se_w"], 0.0), rel=1e-12)

    def test_decayed_state_is_pooled_from_its_own_sums(self):
        state, _ = _built(lam=LAM)
        res = PartialPooling(POOL).fit(state)
        want, _ = _shrink_oracle(state, range(N_BUCKETS), POOL["tau2_floor"], POOL["min_clusters"])
        assert np.allclose([res["buckets"][1]["a"], res["buckets"][1]["b"]], want[1][0], rtol=1e-9)

    def test_does_not_change_the_state(self):
        state, _ = _built()
        before = json.dumps(state.to_dict(), sort_keys=True)
        PartialPooling(POOL).fit(state)
        assert json.dumps(state.to_dict(), sort_keys=True) == before


def _origin_oracle(rows):
    """Slope through the origin and its two-way CR1 variance, row by row."""
    e = np.array([q["e"] for q in rows])
    r = np.array([q["r"] for q in rows])
    wt = np.array([q["w"] for q in rows])
    see = np.sum(wt * e * e)
    w = np.sum(wt * e * r) / see

    def one(labels):
        s, tw = {}, {}
        for lab, ei, ri, wi in zip(labels, e, r, wt):
            s[lab] = s.get(lab, 0.0) + wi * ei * (ri - w * ei)
            tw[lab] = tw.get(lab, 0.0) + wi
        g, n = _kish(list(tw.values())), _kish(wt)
        return g / (g - 1) * (n - 1) / (n - 2) * sum(v * v for v in s.values()) / see ** 2

    names = [q["name"] for q in rows]
    dates = [q["date"] for q in rows]
    return w, one(names) + one(dates) - one(list(zip(names, dates)))


# ---------------------------------------------------------------- name adjustment


def _name_clusters(name, g, slope_da=0.5, slope_db=0.3, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(g):
        e = [float(x) for x in rng.normal(0, 2, 5)]
        c = [float(0.1 * x) for x in e]
        u = [slope_da + slope_db * x + float(rng.normal(0, .4)) for x in e]
        day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
        out.append({"name": name, "settle": day, "e": e, "c": c, "r": [ci + ui for ci, ui in zip(c, u)], "u": u, "age": g - 1 - k})
    return out


def _ols(e, u, w):
    x = np.array([[1.0, v] for v in e])
    wt = np.array(w)
    return np.linalg.solve(x.T @ (wt[:, None] * x), x.T @ (wt * np.array(u)))


class TestNameAdjustment:
    def _fed(self, clusters, decay=None, upto=None):
        adj = NameAdjustment(NAME, decay=decay)
        t_last = None
        for k, c in enumerate(clusters):
            t = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k + 3))
            adj.advance(t)
            adj.add_cluster(t, c["name"], c["settle"], c["e"], c["r"], c["c"])
            t_last = t
        return adj, t_last

    @pytest.mark.parametrize("bad", [{**NAME, "x": 1}, {"name_prior_clusters": 26},
                                     {**NAME, "name_prior_clusters": -1}, {**NAME, "name_min_clusters": 0},
                                     {**NAME, "name_prior_clusters": float("inf")}, None])
    def test_rejects(self, bad):
        with pytest.raises(ValueError):
            NameAdjustment(bad)

    def test_shrinks_toward_zero_by_effective_clusters(self):
        cl = _name_clusters("AA", 10)
        adj, _ = self._fed(cl)
        raw = _ols([v for c in cl for v in c["e"]], [v for c in cl for v in c["u"]], [1.0] * 50)
        got = adj.adjust("AA")
        assert np.allclose(got, raw * 10 / (10 + 26), rtol=1e-9)

    def test_zero_below_min_clusters_and_for_unknown_name(self):
        adj, _ = self._fed(_name_clusters("AA", 1))
        assert adj.adjust("AA") == (0.0, 0.0)
        assert adj.adjust("ZZ") == (0.0, 0.0)

    def test_singular_name_is_zero(self):
        adj = NameAdjustment(NAME)
        for k in range(3):
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            adj.add_cluster("2020-06-01", "AA", day, [1.0, 1.0], [2.0, 3.0], [0.5, 0.5])
        assert adj.adjust("AA") == (0.0, 0.0)

    def test_decayed_uses_kish_effective_clusters_and_weights(self):
        cl = _name_clusters("AA", 8)
        adj, _ = self._fed(cl, decay=LAM)
        w = [LAM ** (c["age"]) for c in cl for _ in c["e"]]
        raw = _ols([v for c in cl for v in c["e"]], [v for c in cl for v in c["u"]], w)
        cw = np.array([LAM ** c["age"] for c in cl])
        g_eff = cw.sum() ** 2 / (cw ** 2).sum()
        assert np.allclose(adj.adjust("AA"), raw * g_eff / (g_eff + 26), rtol=1e-9)
        assert adj.effective_clusters("AA") == pytest.approx(g_eff, rel=1e-12)

    def test_one_names_cluster_moves_only_that_name(self):
        adj, t = self._fed(_name_clusters("AA", 6, seed=1) + _name_clusters("BB", 6, seed=2))
        before_b = adj.adjust("BB")
        before_a = adj.adjust("AA")
        extra = _name_clusters("AA", 7, seed=9)[-1]
        adj.add_cluster("2021-01-01", "AA", "2020-12-25", extra["e"], extra["r"], extra["c"])
        assert adj.adjust("BB") == before_b
        assert adj.adjust("AA") != before_a

    def test_only_settlements_strictly_before_t(self):
        adj = NameAdjustment(NAME)
        before = json.dumps(adj.to_dict(), sort_keys=True)
        for s in ("2020-03-06", "2020-03-07"):
            with pytest.raises(_Refusal) as exc:
                adj.add_cluster("2020-03-06", "AA", s, [1.0, 2.0], [1.0, 2.0], [0.0, 0.0])
            assert exc.value.code == "state_from_future"
        assert json.dumps(adj.to_dict(), sort_keys=True) == before

    def test_residual_is_frozen_at_decision_time(self):
        """The residual uses the c stored at t_i; a later bucket map never rewrites it."""
        state, _ = _built(upto=5)
        pooling = PartialPooling(POOL)
        m1 = EdgeCalibrationMap.build("2020-02-14", state, pooling, NameAdjustment(NAME))
        e, r = [1.0, -2.0, 3.0], [2.0, 0.5, 1.0]
        c_frozen = [m1.bucket_only(1, v) for v in e]
        adj = NameAdjustment(NAME)
        for k in range(3):
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            adj.add_cluster("2020-06-01", "AA", day, e, r, c_frozen)
        frozen = json.dumps(adj.to_dict(), sort_keys=True)
        da, db = adj.adjust("AA")
        # the bucket map moves on (more data); the stored residuals and the adjustment do not
        state2, _ = _built()
        m2 = EdgeCalibrationMap.build("2020-04-01", state2, pooling, adj)
        assert [m2.bucket_only(1, v) for v in e] != c_frozen
        assert json.dumps(adj.to_dict(), sort_keys=True) == frozen
        assert adj.adjust("AA") == (da, db)
        u = [ri - ci for ri, ci in zip(r, c_frozen)]
        want = _ols(e * 3, u * 3, [1.0] * 9) * 3 / (3 + 26)
        assert np.allclose([da, db], want, rtol=1e-9)

    def test_dump_roundtrip_continues_identically(self):
        adj, _ = self._fed(_name_clusters("AA", 6), decay=LAM)
        again = NameAdjustment.from_dict(json.loads(json.dumps(adj.to_dict())))
        assert json.dumps(again.to_dict(), sort_keys=True) == json.dumps(adj.to_dict(), sort_keys=True)
        for a in (adj, again):
            a.advance("2021-01-01")
        assert again.adjust("AA") == adj.adjust("AA")

    def test_duplicate_cluster_and_bad_inputs_refuse(self):
        adj = NameAdjustment(NAME)
        adj.add_cluster("2020-06-01", "AA", "2020-05-01", [1.0, 2.0], [1.0, 2.0], [0.0, 0.0])
        with pytest.raises(ValueError):
            adj.add_cluster("2020-06-01", "AA", "2020-05-01", [1.0, 2.0], [1.0, 2.0], [0.0, 0.0])
        for bad in ([[1.0], [1.0, 2.0], [0.0, 0.0]], [[1.0, float("nan")], [1.0, 2.0], [0.0, 0.0]], [[], [], []]):
            with pytest.raises(ValueError):
                adj.add_cluster("2020-06-01", "BB", "2020-05-02", *bad)


# ---------------------------------------------------------------- calibration map


class TestEdgeCalibrationMap:
    def _map(self, lam=None, adj=None):
        state, _ = _built(lam=lam)
        return state, EdgeCalibrationMap.build("2020-04-01", state, PartialPooling(POOL), adj or NameAdjustment(NAME, decay=lam))

    def test_is_frozen(self):
        _, m = self._map()
        for attr, val in (("t", "x"), ("buckets", ()), ("newattr", 1)):
            with pytest.raises((AttributeError, TypeError)):
                setattr(m, attr, val)

    def test_calibrated_adds_the_name_adjustment(self):
        adj = NameAdjustment(NAME)
        for k, c in enumerate(_name_clusters("AA", 5)):
            adj.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"])
        _, m = self._map(adj=adj)
        da, db = adj.adjust("AA")
        b = m.buckets[1]
        assert m.calibrated(1, "AA", 2.0) == pytest.approx((b.a + da) + (b.b + db) * 2.0, rel=1e-12)
        assert m.calibrated(1, "ZZ", 2.0) == pytest.approx(b.a + b.b * 2.0, rel=1e-12)
        assert m.bucket_only(1, 2.0) == pytest.approx(b.a + b.b * 2.0, rel=1e-12)

    def test_counts_weights_and_se_present(self):
        state, m = self._map()
        f = state.fit(1)
        b = m.buckets[1]
        assert (b.clusters, b.rows) == (f["clusters"], f["rows"])
        assert b.se_b > 0 and b.se_a > 0 and 0.0 <= b.w <= POOL["weight_cap"]
        assert m.t == "2020-04-01" and m.warm is True and m.pooled.clusters == state.fit(None)["clusters"]
        assert m.names == {} or all(len(v) == 2 for v in m.names.values())

    def test_variants(self):
        _, m = self._map()
        b = m.buckets[2]
        assert m.origin(2, 3.0) == pytest.approx(b.w * 3.0, rel=1e-12)
        assert m.origin(2, 3.0, lcb=True) == pytest.approx(b.w_lcb * 3.0, rel=1e-12)
        assert m.lower(2, 3.0) == pytest.approx(b.a + b.b_lcb * 3.0, rel=1e-12)
        assert m.origin(2, 0.0) == 0.0  # zero predicted, zero calibrated

    def test_warm_up_is_zero_everywhere(self):
        state, _ = _built(upto=1)
        m = EdgeCalibrationMap.build("2020-01-24", state, PartialPooling(POOL), NameAdjustment(NAME))
        assert m.warm is False
        for b in range(N_BUCKETS):
            assert m.calibrated(b, "AA", 5.0) == 0.0 and m.origin(b, 5.0) == 0.0 and m.lower(b, 5.0) == 0.0

    def test_roundtrip_json(self):
        _, m = self._map()
        blob = json.loads(json.dumps(m.to_dict()))
        again = EdgeCalibrationMap.from_dict(blob)
        assert json.dumps(again.to_dict(), sort_keys=True) == json.dumps(m.to_dict(), sort_keys=True)
        assert again.calibrated(1, "AA", 1.5) == m.calibrated(1, "AA", 1.5)

    def test_later_state_changes_do_not_move_a_built_map(self):
        state, m = self._map()
        before = json.dumps(m.to_dict(), sort_keys=True)
        state.add_cluster("2020-06-01", "ZZ", "2020-05-01", "2020-04-28", [1, 1], [1.0, 2.0], [9.0, 9.0])
        assert json.dumps(m.to_dict(), sort_keys=True) == before

    def test_decayed_and_expanding_differ(self):
        _, mx = self._map()
        _, md = self._map(lam=LAM)
        assert md.buckets[1].b != mx.buckets[1].b

    def test_bad_bucket_and_input_refuse(self):
        _, m = self._map()
        for bad in (-1, N_BUCKETS, True, 1.5):
            with pytest.raises(ValueError):
                m.bucket_only(bad, 1.0)
        with pytest.raises(ValueError):
            m.bucket_only(1, float("nan"))


def test_stage_2a_public_surface():
    assert {"PartialPooling", "NameAdjustment", "EdgeCalibrationMap"} <= set(er.__all__)
    assert er.__all__[-4:] == ["PartialPooling", "NameAdjustment", "EdgeCalibrationMap", "TrailingRegime"]


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


class TestTrailingRegimeDump:
    """ADR-0311 stage 3: the regime history must survive a checkpoint."""

    def _filled(self):
        reg = er.TrailingRegime({"regime_lookback_weeks": 4, "regime_min_weeks": 2})
        day = datetime.date(2020, 1, 3)
        for k in range(9):
            for name in ("AA", "BB"):
                reg.of(name, (day + datetime.timedelta(days=7 * k)).isoformat(), 0.2 + 0.01 * k)
                reg.add(name, (day + datetime.timedelta(days=7 * k)).isoformat(), 0.2 + 0.01 * k)
        return reg

    def test_round_trip_continues_identically(self):
        reg = self._filled()
        blob = json.loads(json.dumps(reg.to_dict()))
        back = er.TrailingRegime.from_dict(blob)
        assert back.to_dict() == reg.to_dict()
        t = "2020-03-06"
        assert back.of("AA", t, 0.5) == reg.of("AA", t, 0.5)
        back.add("AA", t, 0.5)
        reg.add("AA", t, 0.5)
        assert back.to_dict() == reg.to_dict()
        assert back.warmups == reg.warmups

    def test_malformed_dump_refuses(self):
        with pytest.raises(ValueError):
            er.TrailingRegime.from_dict({"params": {}})


# ------------------------------------------------- ADR-0311 fix round (stage 1-3 review findings)


def _left_fold(items, start=0):
    """Python 3.11's naive ``sum`` (3.12+ compensates): a strict left fold."""
    acc = start
    for v in items:
        acc = acc + v
    return acc


def _tiny_state(per_date, decay=None, n_buckets=1):
    """A state of ``per_date`` dates x names A, B: one-row clusters with chosen edges."""
    state = LinearEdgeState(n_buckets, decay)
    for k in range(per_date):
        day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
        fill = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k - 3))
        for j, name in enumerate(("A", "B")):
            state.add_cluster("2021-01-01", name, day, fill, [0, 0], [1.0 + k + j, 2.0 - j * k],
                              [0.5 * k + j, 3.0 - k * j + 0.25])
    return state


LOW_FLOOR = {**POOL, "tau2_floor": 1e-15, "weight_cap": 100.0}


class TestDeterminismAcrossResume:
    """Finding 1: no float accumulation may depend on dict insertion order."""

    def _state(self):
        rng = np.random.default_rng(21)
        names = ["ZZ", "MM", "AA", "QQ", "BB", "XX", "CC", "KK", "DD", "WW", "EE", "JJ"]
        state = LinearEdgeState(4, 0.93)
        for k in range(8):
            t = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k + 1))
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            fill = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k - 3))
            state.advance(t)
            order = list(names)
            rng.shuffle(order)  # unsorted first arrival
            for i, name in enumerate(order):
                scale = 10.0 ** (i % 6 - 2)
                e = [float(x) * scale for x in rng.normal(0, 2, 8)]
                r = [float((0.4 + j % 4) * x + rng.normal(0, 1) * scale) for j, x in enumerate(e)]
                state.add_cluster(t, name, day, fill, [j % 4 for j in range(8)], e, r)
        return state

    def test_resumed_pooling_equals_fresh_under_naive_sum(self, monkeypatch):
        import builtins

        fresh = self._state()
        resumed = LinearEdgeState.from_dict(json.loads(json.dumps(fresh.to_dict())))
        assert list(fresh._pooled.names.cells) != sorted(fresh._pooled.names.cells)  # the test really has unsorted arrival
        with monkeypatch.context() as m:
            m.setattr(builtins, "sum", _left_fold)
            a = json.dumps(PartialPooling(LOW_FLOOR).fit(fresh), sort_keys=True)
            b = json.dumps(PartialPooling(LOW_FLOOR).fit(resumed), sort_keys=True)
        assert a == b

    def test_resumed_sandwich_and_origin_equal_fresh_under_naive_sum(self, monkeypatch):
        import builtins

        fresh = self._state()
        resumed = LinearEdgeState.from_dict(json.loads(json.dumps(fresh.to_dict())))
        with monkeypatch.context() as m:
            m.setattr(builtins, "sum", _left_fold)
            for b in (None, 0, 1, 2, 3):
                th = (0.1, 0.2)
                assert fresh.sandwich(b, th, "two") == resumed.sandwich(b, th, "two")
                assert PartialPooling._origin(fresh._unit(b)) == PartialPooling._origin(resumed._unit(b))


class TestBoundaryExact:
    """Finding 9: each gate is pinned at its exact boundary."""

    def test_max_one_way_takes_the_wider_one_way_matrix(self):
        rng = np.random.default_rng(0)
        state, rows = LinearEdgeState(1, None), []
        for k in range(3):
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            for n in ("A", "B", "C"):
                if rng.random() < 0.3:
                    continue
                m = int(rng.integers(1, 4))
                e, r = [float(x) for x in rng.normal(0, 2, m)], [float(x) for x in rng.normal(0, 2, m)]
                state.add_cluster("2021-01-01", n, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k - 3)),
                                  [0] * m, e, r)
                rows += [{"name": n, "date": day, "e": a, "r": c, "w": 1.0} for a, c in zip(e, r)]
        f = state.fit(None)
        sw = state.sandwich(None, (f["a"], f["b"]), "two")
        assert sw["rule"] == "max_one_way"
        _, _, _, _, beta = _wls(rows)
        by_name = _cov(rows, [q["name"] for q in rows], beta)
        by_date = _cov(rows, [q["date"] for q in rows], beta)
        wide = by_name if by_name[1, 1] >= by_date[1, 1] else by_date
        assert by_name[1, 1] != by_date[1, 1]
        assert np.allclose(_arr(sw["cov"]), wide, rtol=1e-9)

    def test_warm_up_min_dates_is_inclusive(self):
        state = _tiny_state(3)
        pool = {**POOL, "min_clusters": 2, "min_dates": 3}
        assert state.fit(None)["dates"] == 3
        assert PartialPooling(pool).fit(state)["warm"] is True
        assert PartialPooling({**pool, "min_dates": 4}).fit(state)["warm"] is False

    def test_warm_up_min_clusters_is_inclusive(self):
        state = _tiny_state(3)
        assert state.fit(None)["clusters"] == 6
        pool = {**POOL, "min_clusters": 6, "min_dates": 1}
        assert PartialPooling(pool).fit(state)["warm"] is True
        assert PartialPooling({**pool, "min_clusters": 7}).fit(state)["warm"] is False

    def test_bucket_stands_alone_at_exactly_min_clusters(self):
        state = _tiny_state(3)
        pool = {**POOL, "min_clusters": 6, "min_dates": 1}
        pp = PartialPooling(pool)
        assert pp._enough(state.fit(0)["clusters"]) is True
        assert PartialPooling({**pool, "min_clusters": 7})._enough(6) is False
        assert PartialPooling(pool).fit(state)["buckets"][0]["pooled"] is False

    def test_name_adjustment_applies_at_exactly_name_min_clusters(self):
        cl = _name_clusters("AA", 2)
        adj = NameAdjustment({**NAME, "name_min_clusters": 2})
        for c in cl:
            adj.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"])
        assert adj.adjust("AA") != (0.0, 0.0)
        tight = NameAdjustment({**NAME, "name_min_clusters": 3})
        for c in cl:
            tight.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"])
        assert tight.adjust("AA") == (0.0, 0.0)

    def test_cold_map_carries_no_name_adjustment(self):
        state = _tiny_state(3)
        adj = NameAdjustment({**NAME, "name_min_clusters": 2})
        for c in _name_clusters("AA", 4):
            adj.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"])
        assert adj.adjust("AA") != (0.0, 0.0)
        cold = {**POOL, "min_clusters": 100}
        cmap = EdgeCalibrationMap.build("2021-01-01", state, PartialPooling(cold), adj)
        assert cmap.warm is False
        assert dict(cmap.names) == {}
        assert cmap.calibrated(0, "AA", 3.0) == 0.0

    def test_near_constant_edge_is_no_variance_not_a_garbage_slope(self):
        state = LinearEdgeState(1, None)
        for k in range(3):
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            for n in ("A", "B"):
                state.add_cluster("2021-01-01", n, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k - 3)),
                                  [0] * 3, [0.7] * 3, [float(k + j) for j in range(3)])
        n, se, _, see = state._pooled.tot[:4]
        assert 0.0 < see - se * se / n <= 1e-12 * see  # rounding noise, positive
        f = state.fit(None)
        assert f["a"] is None and f["b"] is None
        assert state.sandwich(None, (0.0, 0.0), "one") is None

    def test_sandwich_needs_three_rows(self):
        state = LinearEdgeState(1, None)
        for k, name in enumerate(("A", "B")):
            state.add_cluster("2021-01-01", name, "2020-01-10", "2020-01-07", [0], [float(k + 1)], [float(2 * k)])
        assert state._pooled.rows == 2 and state._pooled.clusters == 2
        assert state.sandwich(None, (0.0, 0.0), "one") is None


class TestOriginVarianceFactor:
    """Finding 11: the through-origin sandwich uses the same CR1 factor as the line sandwich."""

    def test_matches_clustered_calibration_slope_when_the_lines_coincide(self):
        # sum(e) = 0 and sum(r) = 0 make the intercept fit pass through the origin
        state, e_all, r_all, names, dates = LinearEdgeState(1, None), [], [], [], []
        rng = np.random.default_rng(4)
        for k in range(4):
            day = _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k))
            for n in ("A", "B", "C"):
                x, y = [float(v) for v in rng.normal(0, 2, 2)], [float(v) for v in rng.normal(0, 2, 2)]
                e, r = [x[0], -x[0], x[1], -x[1]], [y[0], -y[0], y[1], -y[1]]
                state.add_cluster("2021-01-01", n, day, _iso(FIRST_SETTLE + datetime.timedelta(days=7 * k - 3)),
                                  [0] * 4, e, r)
                e_all += e
                r_all += r
                names += [n] * 4
                dates += [day] * 4
        res = clustered_calibration_slope(r_all, e_all, [names, dates])
        out = PartialPooling({**POOL, "weight_cap": 100.0}).fit(state)["pooled"]
        assert out["w"] == pytest.approx(res["slope"], rel=1e-9)
        assert out["se_w"] == pytest.approx(res["slope_se"], rel=1e-9)

    def test_needs_three_rows(self):
        state = LinearEdgeState(1, None)
        for k, name in enumerate(("A", "B")):
            state.add_cluster("2021-01-01", name, "2020-01-10", "2020-01-07", [0], [float(k + 1)], [float(2 * k)])
        assert PartialPooling._origin(state._pooled) is None


class TestRegimeWarmupCountedOnce:
    """Finding 10: a repeated query of one (name, t) is one warm-up."""

    def test_repeat_query_does_not_double_count(self):
        reg = _regime(regime_min_weeks=3)
        assert reg.of("AA", _wk(0), 1.0) == ("low", True)
        assert reg.of("AA", _wk(0), 1.0) == ("low", True)
        assert reg.warmups == 1
        reg.of("BB", _wk(0), 1.0)
        reg.of("AA", _wk(1), 1.0)
        assert reg.warmups == 3

    def test_the_count_survives_a_dump(self):
        reg = _regime(regime_min_weeks=3)
        reg.of("AA", _wk(0), 1.0)
        back = TrailingRegime.from_dict(json.loads(json.dumps(reg.to_dict())))
        back.of("AA", _wk(0), 1.0)
        assert back.warmups == 1 and back.to_dict() == reg.to_dict()


# ================================================================ ADR-0311 Amendment 1, F1.1: two regressors
# Oracles are restated here in numpy, row by row; only the state under test is read from the module.


def _k2_clusters(equal=True, seed=5, b_dir=0.0, b_shape=0.8, n_settle=N_SETTLE):
    """Synthetic (name, settle date) clusters with a direction edge, a shape edge and a forward error variance."""
    rng = np.random.default_rng(seed)
    names = ["AA", "BB", "CC", "DD", "EE", "FF"]
    out = []
    for k in range(n_settle):
        settle = FIRST_SETTLE + datetime.timedelta(days=7 * k)
        shock = rng.normal(0, 1.0)
        for name in names:
            if not equal and rng.random() < 0.1:
                continue
            m = 6 if equal else int(rng.integers(3, 9))
            e_dir = rng.normal(0, 1.5, m)
            shape = rng.normal(0, 1.0, m)
            bucket = rng.integers(0, 3, m)
            r = 0.2 + b_dir * e_dir + b_shape * shape + shock + rng.normal(0, 0.8, m) + 0.1 * bucket
            out.append({
                "name": name, "settle": _iso(settle), "fill": _iso(settle - datetime.timedelta(days=3)),
                "bucket": [int(b) for b in bucket], "e": [float(x) for x in e_dir + shape],
                "e_dir": [float(x) for x in e_dir], "r": [float(x) for x in r],
                "sigma2": [float(x) for x in np.abs(rng.normal(0.1, 0.02, m))],
            })
    return out


def _drive_k2(state, clusters, weeks, lam=None, **extra):
    """Like ``_drive`` but passes ``e_dir`` and ``sigma2`` (and any ``extra`` per-cluster keys)."""
    gate = SettledBefore("2021-07-01")
    arrival, rows = {}, []
    for k, t in enumerate(weeks):
        state.advance(t)
        for i, c in enumerate(clusters):
            if i not in arrival and gate.admits(c["settle"], t):
                kw = {key: c[key] for key in ("e_dir", "sigma2", "weights") if key in c}
                if state.regressors == 1:
                    kw.pop("e_dir", None), kw.pop("sigma2", None)
                state.add_cluster(t, c["name"], c["settle"], c["fill"], c["bucket"], c["e"], c["r"], **kw)
                arrival[i] = k
    last = len(weeks) - 1
    for i, k0 in arrival.items():
        c = clusters[i]
        for j in range(len(c["e"])):
            base = c["weights"][j] if "weights" in c else 1.0
            w = base * (1.0 if lam is None else lam ** (last - k0))
            rows.append({"name": c["name"], "date": c["settle"], "bucket": c["bucket"][j], "e": c["e"][j],
                         "e_dir": c["e_dir"][j], "e_shape": c["e"][j] - c["e_dir"][j], "r": c["r"][j],
                         "w": w, "sigma2": c["sigma2"][j] if "sigma2" in c else None})
    return rows


def _x2(rows):
    return np.array([[1.0, q["e_dir"], q["e_shape"]] for q in rows])


def _wls2(rows):
    x, y, w = _x2(rows), np.array([q["r"] for q in rows]), np.array([q["w"] for q in rows])
    xtwx = x.T @ (w[:, None] * x)
    return x, y, w, xtwx, np.linalg.solve(xtwx, x.T @ (w * y))


def _cov_k2(rows, labels, beta):
    """CR1 ``G_eff/(G_eff-1) (n_eff-1)/(n_eff-3)`` sandwich, Kish counts on row / cluster total weights."""
    x, y, w, xtwx, _ = _wls2(rows)
    resid = y - x @ beta
    sums, tot_w = {}, {}
    for xi, ri, wi, lab in zip(x, resid, w, labels):
        sums[lab] = sums.get(lab, np.zeros(3)) + wi * xi * ri
        tot_w[lab] = tot_w.get(lab, 0.0) + wi
    meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((3, 3)))
    g_eff, n_eff = _kish(list(tot_w.values())), _kish(w)
    bread = np.linalg.inv(xtwx)
    return g_eff / (g_eff - 1) * (n_eff - 1) / (n_eff - 3) * bread @ meat @ bread


def _expected_k2(rows, way):
    _, _, _, _, beta = _wls2(rows)
    names, dates = [q["name"] for q in rows], [q["date"] for q in rows]
    pairs = list(zip(names, dates))
    if way == "one":
        return beta, _cov_k2(rows, pairs, beta)
    by_name, by_date = _cov_k2(rows, names, beta), _cov_k2(rows, dates, beta)
    two = by_name + by_date - _cov_k2(rows, pairs, beta)
    if min(np.linalg.det(two[:i, :i]) for i in (1, 2, 3)) <= 0.0:  # not positive definite: the wider one-way
        two = by_name if by_name[1, 1] >= by_date[1, 1] else by_date
    return beta, two


class TestTwoRegressorParity:
    """A-T2 (expanding) and A-T3 (decayed): the 3x3 fit and bands equal batch weighted least squares."""

    LAM = 2 ** (-1 / 3)

    @pytest.fixture(scope="class", params=["expanding", "decayed"])
    def built(self, request):
        lam = None if request.param == "expanding" else self.LAM
        state = LinearEdgeState(N_BUCKETS, lam, regressors=2)
        rows = _drive_k2(state, _k2_clusters(), _weeks(), lam=lam)
        return state, rows

    @pytest.mark.parametrize("unit", UNITS)
    def test_fit_equals_batch(self, built, unit):
        state, rows = built
        sel = _unit_rows(rows, unit)
        _, _, w, _, beta = _wls2(sel)
        fit = state.fit(unit)
        assert math.isclose(fit["n"], w.sum(), rel_tol=1e-12)
        assert np.allclose([fit["a"], fit["b_dir"], fit["b_shape"]], beta, atol=1e-9, rtol=0)
        assert np.allclose(fit["theta"], beta, atol=1e-9, rtol=0)

    @pytest.mark.parametrize("way", ["one", "two"])
    @pytest.mark.parametrize("unit", UNITS)
    def test_sandwich_equals_row_by_row_cr1(self, built, unit, way):
        state, rows = built
        sel = _unit_rows(rows, unit)
        beta, cov = _expected_k2(sel, way)
        out = state.sandwich(unit, list(beta), way)
        assert out["rule"] == ("one_way" if way == "one" else "two_way")
        assert np.max(np.abs(_arr(out["cov"]) - cov)) < 1e-9 * max(1.0, np.max(np.abs(cov)))

    def test_sandwich_at_a_shrunk_theta(self, built):
        state, rows = built
        theta = np.array([0.1, 0.2, 0.5])
        x, y, w, xtwx, _ = _wls2(rows)
        resid = y - x @ theta
        sums = {}
        for xi, ri, wi, q in zip(x, resid, w, rows):
            key = (q["name"], q["date"])
            sums[key] = sums.get(key, np.zeros(3)) + wi * xi * ri
        meat = sum((np.outer(s, s) for s in sums.values()), np.zeros((3, 3)))
        tw = {}
        for wi, q in zip(w, rows):
            tw[(q["name"], q["date"])] = tw.get((q["name"], q["date"]), 0.0) + wi
        g_eff, n_eff = _kish(list(tw.values())), _kish(w)
        bread = np.linalg.inv(xtwx)
        want = g_eff / (g_eff - 1) * (n_eff - 1) / (n_eff - 3) * bread @ meat @ bread
        got = _arr(state.sandwich(None, list(theta), "one")["cov"])
        assert np.max(np.abs(got - want)) < 1e-9 * np.max(np.abs(want))


class TestTwoRegressorSingularity:
    """A-T4: exact collinearity or a constant regressor gives no two-regressor fit; near collinearity fits."""

    def _state(self, e_dir_of_e, k=2):
        rng = np.random.default_rng(3)
        st = LinearEdgeState(2, None, regressors=k)
        st.advance("2021-01-01")
        for g in range(8):
            e = [float(x) for x in rng.normal(0, 2, 6)]
            ed = [e_dir_of_e(x, i) for i, x in enumerate(e)]
            r = [float(0.3 * x + rng.normal(0, 1)) for x in e]
            kw = {"e_dir": ed} if k == 2 else {}
            st.add_cluster("2021-01-01", "AA" if g % 2 else "BB", _iso(FIRST_SETTLE + datetime.timedelta(days=7 * g)),
                           _iso(FIRST_SETTLE + datetime.timedelta(days=7 * g - 3)), [0] * 6, e, r, **kw)
        return st

    @pytest.mark.parametrize("make", [
        pytest.param(lambda x, i: 0.0, id="e_dir_zero"),
        pytest.param(lambda x, i: x, id="e_dir_is_e"),
        pytest.param(lambda x, i: 0.4 * x, id="shape_proportional"),
    ])
    def test_no_fit_two_regressors_while_one_regressor_fits(self, make):
        two, one = self._state(make), self._state(make, k=1)
        f = two.fit(0)
        assert f["theta"] is None and f["b_dir"] is None and f["b_shape"] is None
        assert two.sandwich(0, [0.0, 0.0, 0.0], "one") is None
        assert one.fit(0)["b"] is not None

    def test_near_collinear_fits_with_inflated_band(self):
        loose = self._state(lambda x, i: x / 1.5 + 1.0 * math.sin(3.0 * i))
        tight = self._state(lambda x, i: x / 1.5 + 0.02 * math.sin(3.0 * i))
        fl, ft = loose.fit(0), tight.fit(0)
        assert ft["theta"] is not None and ft["corr_dir_shape"] > 0.99 > abs(fl["corr_dir_shape"])
        sl, st_ = loose.sandwich(0, fl["theta"], "one"), tight.sandwich(0, ft["theta"], "one")
        assert st_ is not None and st_["cov"][1][1] > 3 * sl["cov"][1][1]


class TestTwoRegressorRecoveryAndReliability:
    """A-T5 recovery, A-T24 by hand."""

    def test_true_slopes_are_recovered_within_band(self):
        state = LinearEdgeState(N_BUCKETS, None, regressors=2)
        _drive_k2(state, _k2_clusters(b_dir=0.0, b_shape=0.8, n_settle=40, seed=9), [_iso(FIRST_SETTLE + datetime.timedelta(days=7 * k + 1)) for k in range(41)])
        f = state.fit(None)
        sw = state.sandwich(None, f["theta"], "two")
        se = [math.sqrt(sw["cov"][i][i]) for i in range(3)]
        assert abs(f["b_dir"] - 0.0) < 4 * se[1] and abs(f["b_shape"] - 0.8) < 4 * se[2]

    def test_attenuation_by_forward_error_is_corrected(self):
        rng = np.random.default_rng(17)
        st = LinearEdgeState(1, None, regressors=2)
        st.advance("2021-01-01")
        true_dir, var_err, var_sig = 0.9, 0.5, 1.0
        for g in range(200):
            m = 6
            sig = rng.normal(0, np.sqrt(var_sig), m)              # true direction edge
            err = rng.normal(0, np.sqrt(var_err), m)               # planted forward error
            shape = rng.normal(0, 1, m)
            e_dir_obs = sig + err
            r = true_dir * sig + 0.5 * shape + rng.normal(0, 0.5, m)
            e = e_dir_obs + shape
            st.add_cluster("2021-01-01", f"N{g % 20}", _iso(FIRST_SETTLE + datetime.timedelta(days=g)),
                           _iso(FIRST_SETTLE + datetime.timedelta(days=g - 1)), [0] * m,
                           [float(x) for x in e], [float(x) for x in r], e_dir=[float(x) for x in e_dir_obs],
                           sigma2=[var_err] * m)
        f = st.fit(None)
        planted = var_sig / (var_sig + var_err)
        assert abs(f["b_dir"] - true_dir * planted) < 0.1
        assert abs(f["reliability_dir"] - planted) < 0.1
        assert abs(f["b_dir_corrected"] - true_dir) < 0.15
        assert f["b_dir"] < f["b_dir_corrected"]

    def test_serr_reliability_and_correlation_by_hand(self):
        st = LinearEdgeState(1, None, regressors=2)
        st.advance("2021-01-01")
        e_dir, shape = [1.0, 2.0, 4.0, 3.0], [0.5, -1.0, 2.0, 1.5]
        r = [1.0, 0.0, 3.0, 2.0]
        sig = [0.25, 0.5, None, 0.25]
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 4,
                       [a + b for a, b in zip(e_dir, shape)], r, e_dir=e_dir, sigma2=sig)
        st.add_cluster("2021-01-01", "BB", "2020-01-11", "2020-01-08", [0] * 4,
                       [a + b for a, b in zip(e_dir, shape)], [x + 0.3 for x in r], e_dir=[x + 0.2 for x in e_dir],
                       sigma2=[0.1] * 4)
        f = st.fit(None)
        d = np.array(e_dir + [x + 0.2 for x in e_dir])
        s = np.array(shape + [x - 0.2 for x in shape])
        serr = 0.25 + 0.5 + 0.25 + 0.4          # the None row is skipped
        cdd = ((d - d.mean()) ** 2).sum()
        assert math.isclose(f["reliability_dir"], max((cdd - serr) / cdd, 0.0), rel_tol=1e-12)
        assert math.isclose(f["corr_dir_shape"], np.corrcoef(d, s)[0, 1], rel_tol=1e-9)
        assert st.counts["sigma2_missing"] == 1

    def test_reliability_is_floored_at_zero(self):
        st = LinearEdgeState(1, None, regressors=2)
        st.advance("2021-01-01")
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 2.0, 2.5],
                       e_dir=[0.5, 1.5, 0.2], sigma2=[50.0] * 3)
        f = st.fit(None)
        assert f["reliability_dir"] == 0.0 and f["b_dir_corrected"] is None


class TestTwoRegressorInputs:
    def _st(self, k):
        st = LinearEdgeState(2, None, regressors=k)
        st.advance("2021-01-01")
        return st

    def _add(self, st, **kw):
        base = dict(t="2021-01-01", name="AA", settle_date="2020-01-10", fill_date="2020-01-07",
                    buckets=[0, 0, 1], e=[1.0, 2.0, 3.0], r=[1.0, 2.0, 2.0])
        base.update(kw)
        st.add_cluster(base.pop("t"), base.pop("name"), base.pop("settle_date"), base.pop("fill_date"),
                       base.pop("buckets"), base.pop("e"), base.pop("r"), **base)

    @pytest.mark.parametrize("kw", [
        {}, {"e_dir": [0.1, 0.2]}, {"e_dir": [0.1, None, 0.3]}, {"e_dir": [0.1, float("nan"), 0.3]},
        {"e_dir": [0.1, 0.2, 0.3], "sigma2": [0.1, 0.2]}, {"e_dir": [0.1, 0.2, 0.3], "sigma2": [0.1, -1.0, 0.2]},
    ])
    def test_bad_two_regressor_input_is_a_value_error_and_leaves_the_state(self, kw):
        st = self._st(2)
        with pytest.raises(ValueError):
            self._add(st, **kw)
        assert st.fit(None)["rows"] == 0

    def test_one_regressor_state_refuses_e_dir_and_sigma2(self):
        for kw in ({"e_dir": [0.1, 0.2, 0.3]}, {"sigma2": [0.1, 0.2, 0.3]}):
            with pytest.raises(ValueError):
                self._add(self._st(1), **kw)

    @pytest.mark.parametrize("k", [0, 3, True, 1.5, None])
    def test_bad_regressors(self, k):
        with pytest.raises(ValueError):
            LinearEdgeState(2, None, regressors=k)

    def test_dump_round_trip_two_regressors(self):
        state = LinearEdgeState(N_BUCKETS, 0.9, regressors=2)
        _drive_k2(state, _k2_clusters(), _weeks(), lam=0.9)
        back = LinearEdgeState.from_dict(json.loads(json.dumps(state.to_dict())))
        assert json.dumps(back.to_dict(), sort_keys=True) == json.dumps(state.to_dict(), sort_keys=True)
        assert back.fit(None)["theta"] == state.fit(None)["theta"]


def test_solve_is_one_gaussian_elimination_for_any_dimension():
    rng = np.random.default_rng(2)
    for n in (1, 2, 3, 5):
        a = rng.normal(size=(n, n)) + n * np.eye(n)
        b = rng.normal(size=n)
        got = er._solve([list(map(float, row)) for row in a], [float(x) for x in b])
        assert np.allclose(got, np.linalg.solve(a, b), atol=1e-12)
        m = rng.normal(size=(n, 2))
        got2 = er._solve([list(map(float, row)) for row in a], [[float(x) for x in row] for row in m])
        assert np.allclose(np.array(got2), np.linalg.solve(a, m), atol=1e-12)
    with pytest.raises(ValueError):
        er._solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 1.0])
    assert not hasattr(er, "_mat2_solve") and not hasattr(er, "_mat2_mul")


# ================================================================ F1.2: weights, Kish counts, side and top strata


def _clusters_k2(equal=False, seed=5):
    return _k2_clusters(equal=equal, seed=seed)


def _with_group_weights(clusters):
    """group_equal base weights: rows of one (cluster, strike-group) carry 1/m; here groups of 2-3 rows."""
    rng = np.random.default_rng(31)
    out = []
    for c in clusters:
        m = len(c["e"])
        sizes, left = [], m
        while left > 0:
            g = min(left, int(rng.integers(1, 4)))
            sizes.append(g)
            left -= g
        w = [1.0 / g for g in sizes for _ in range(g)]
        out.append({**c, "weights": w})
    return out


class TestKishCounts:
    """A-T6, A-T7: Kish effective counts by hand; the CR1 factor and df use them."""

    def test_cluster_total_weights_by_hand(self):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0])
        st.add_cluster("2021-01-01", "BB", "2020-01-11", "2020-01-08", [0] * 5, [0.5, 3.0, 5.0, 1.0, 2.0],
                       [2.0, 1.0, 6.0, 0.0, 1.0])
        st.add_cluster("2021-01-01", "CC", "2020-01-12", "2020-01-09", [0] * 4, [0.2, 1.5, 2.5, 1.0],
                       [1.0, 0.0, 3.0, 1.0])
        f = st.fit(None)
        assert math.isclose(f["g_eff"], (3 + 5 + 4) ** 2 / (9 + 25 + 16), rel_tol=1e-14)
        assert f["n_eff"] == 12.0 and f["n_groups"] == 12.0 and f["clusters"] == 3
        sw = st.sandwich(None, (0.1, 0.2), "one")
        assert math.isclose(sw["df"], f["g_eff"] - 1, rel_tol=1e-14)

    def test_equal_clusters_give_exactly_g(self):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        for k, name in enumerate("ABCD"):
            st.add_cluster("2021-01-01", name, _iso(FIRST_SETTLE + datetime.timedelta(days=k)),
                           "2020-01-01", [0] * 4, [1.0, 2.0, 4.0, 3.0 + k], [1.0, 3.0, 2.0, 5.0])
        f = st.fit(None)
        assert f["g_eff"] == 4.0 and f["g_eff_names"] == 4.0 and f["g_eff_dates"] == 4.0 and f["n_eff"] == 16.0

    def test_row_weights_give_n_eff_by_hand(self):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        w = [1.0, 2.0, 3.0]
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0],
                       weights=w)
        st.add_cluster("2021-01-01", "BB", "2020-01-11", "2020-01-08", [0] * 2, [0.5, 3.0], [2.0, 1.0], weights=[2.0, 2.0])
        f = st.fit(None)
        ws = np.array(w + [2.0, 2.0])
        assert math.isclose(f["n_eff"], ws.sum() ** 2 / (ws ** 2).sum(), rel_tol=1e-14)
        assert math.isclose(f["g_eff"], (6 + 4) ** 2 / (36 + 16), rel_tol=1e-14)
        assert math.isclose(f["n"], 10.0, rel_tol=1e-14) and math.isclose(f["n_groups"], 10.0)

    def test_kish_is_scale_invariant_worked_example(self):
        # five equal groups of 142 rows (weight 1/142) give n_eff = 710; a 142-row and a 4-row group give 15.6
        assert math.isclose(er.kish_effective(5.0, 710 / 142 ** 2), 710.0, rel_tol=1e-12)
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        rng = np.random.default_rng(4)
        for name, m in (("AA", 142), ("BB", 4)):
            st.add_cluster("2021-01-01", name, _iso(FIRST_SETTLE + datetime.timedelta(days=m)), "2020-01-01", [0] * m,
                           [float(x) for x in rng.normal(size=m)], [float(x) for x in rng.normal(size=m)],
                           weights=[1.0 / m] * m)
        f = st.fit(None)
        assert math.isclose(f["n_eff"], 4.0 / (1 / 142 + 0.25), rel_tol=1e-12) and round(f["n_eff"], 1) == 15.6
        assert math.isclose(f["g_eff"], 2.0, rel_tol=1e-12) and math.isclose(f["n_groups"], 2.0, rel_tol=1e-12)

    def test_kish_helper_edges(self):
        assert er.kish_effective(0.0, 0.0) == 0.0 and er.kish_effective(3.0, 0.0) == 0.0
        assert er.kish_effective(4.0, 8.0) == 2.0

    def test_pure_aging_changes_no_count_and_no_sandwich(self):
        clusters = _clusters_k2()
        st = LinearEdgeState(N_BUCKETS, 0.8, regressors=2)
        _drive_k2(st, clusters, _weeks(), lam=0.8)
        keys = ("n_eff", "g_eff", "g_eff_names", "g_eff_dates")
        before = {k: st.fit(None)[k] for k in keys}
        theta = [0.2, 0.1, 0.5]
        cov0 = _arr(st.sandwich(None, theta, "two")["cov"])
        for k in range(1, 6):
            st.advance(_iso(datetime.date(2021, 1, 1) + datetime.timedelta(days=7 * k)))
        after = {k: st.fit(None)[k] for k in keys}
        for k in keys:
            assert math.isclose(before[k], after[k], rel_tol=1e-12)
        assert np.max(np.abs(_arr(st.sandwich(None, theta, "two")["cov"]) - cov0)) < 1e-12 * np.max(np.abs(cov0))

    def test_one_fresh_cluster_beside_an_aged_one_has_no_sandwich(self):
        st = LinearEdgeState(1, 0.5, regressors=1)
        st.advance("2020-02-07")
        st.add_cluster("2020-02-07", "AA", "2020-02-01", "2020-01-30", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0])
        t = datetime.date(2020, 2, 7)
        for k in range(1, 51):
            st.advance(_iso(t + datetime.timedelta(days=7 * k)))
        st.add_cluster(_iso(t + datetime.timedelta(days=7 * 50)), "BB", "2020-02-05", "2020-02-01" if False else "2020-01-30",
                       [0] * 3, [0.5, 3.0, 5.0], [2.0, 1.0, 6.0])
        f = st.fit(None)
        assert f["clusters"] == 2 and f["rows"] == 6 and f["g_eff"] < 2.0
        assert st.sandwich(None, (0.1, 0.2), "one") is None
        assert st.sandwich(None, (0.1, 0.2), "two") is None


class TestWeightedParity:
    """A-T1/A-T3/A-T18: Kish-factor sandwiches equal the explicitly weighted batch, incl. group weights."""

    LAM = 2 ** (-1 / 3)

    @pytest.mark.parametrize("lam", [None, LAM])
    @pytest.mark.parametrize("weighted", [False, True])
    @pytest.mark.parametrize("unit", UNITS)
    @pytest.mark.parametrize("way", ["one", "two"])
    def test_sandwich_equals_weighted_row_by_row(self, lam, weighted, unit, way):
        clusters = _clusters_k2(equal=False)
        if weighted:
            clusters = _with_group_weights(clusters)
        st = LinearEdgeState(N_BUCKETS, lam, regressors=2)
        rows = _drive_k2(st, clusters, _weeks(), lam=lam)
        sel = _unit_rows(rows, unit)
        beta, cov = _expected_k2(sel, way)
        fit = st.fit(unit)
        assert np.allclose(fit["theta"], beta, atol=1e-9, rtol=0)
        out = st.sandwich(unit, list(beta), way)
        assert np.max(np.abs(_arr(out["cov"]) - cov)) < 1e-9 * max(1.0, np.max(np.abs(cov)))
        w = np.array([q["w"] for q in sel])
        assert math.isclose(fit["n_eff"], _kish(w), rel_tol=1e-12)
        tw = {}
        for q in sel:
            tw[(q["name"], q["date"])] = tw.get((q["name"], q["date"]), 0.0) + q["w"]
        assert math.isclose(fit["g_eff"], _kish(list(tw.values())), rel_tol=1e-12)
        for key, dim in (("g_eff_names", "name"), ("g_eff_dates", "date")):
            tt = {}
            for q in sel:
                tt[q[dim]] = tt.get(q[dim], 0.0) + q["w"]
            assert math.isclose(fit[key], _kish(list(tt.values())), rel_tol=1e-12)
        if way == "two":
            assert out["df"] == pytest.approx(min(fit["g_eff_names"], fit["g_eff_dates"]) - 1, rel=1e-12)

    def test_group_equal_weights_give_each_group_total_weight_one(self):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        # two groups in one cluster: 4 rows at 1/4 and 2 rows at 1/2
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 6, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                       [1.0, 0.0, 2.0, 1.0, 5.0, 5.0], weights=[0.25] * 4 + [0.5] * 2)
        f = st.fit(None)
        assert math.isclose(f["n"], 2.0, rel_tol=1e-14) and math.isclose(f["n_groups"], 2.0)

    def test_a_big_and_a_small_group_move_the_fit_equally(self):
        # group means of e at -1 (142 rows) and +1 (4 rows), equal total weight 1: a unit shift of r in either group
        # moves the weighted intercept by the same amount (1/2); unweighted, the big group dominates
        def intercept(d_big, d_small, weighted=True):
            st = LinearEdgeState(1, None)
            st.advance("2021-01-01")
            for name, m, centre, d in (("AA", 142, -1.0, d_big), ("BB", 4, 1.0, d_small)):
                e = [centre + 0.3 * math.sin(2.0 * i) for i in range(m)]
                st.add_cluster("2021-01-01", name, _iso(FIRST_SETTLE + datetime.timedelta(days=m)), "2020-01-01",
                               [0] * m, e, [0.5 * x + d for x in e], weights=[1.0 / m] * m if weighted else None)
            return st.fit(None)["a"]

        base = intercept(0.0, 0.0)
        assert intercept(1.0, 0.0) - base == pytest.approx(0.5, abs=1e-2)
        assert intercept(0.0, 1.0) - base == pytest.approx(0.5, abs=1e-2)
        base_u = intercept(0.0, 0.0, False)
        assert abs((intercept(1.0, 0.0, False) - base_u) - (intercept(0.0, 1.0, False) - base_u)) > 0.1


class TestSideAndTopUnits:
    """A-T18: side units equal batch fits on the buy and sell rows; the top unit on the flagged rows."""

    def test_units_equal_batch_fits(self):
        rng = np.random.default_rng(8)
        st = LinearEdgeState(2, None, regressors=1)
        st.advance("2021-01-01")
        rows = []
        for g in range(10):
            m = 6
            e = rng.normal(0, 2, m)
            r = 0.3 * e + rng.normal(0, 1, m)
            sides = [1 if i % 2 == 0 else -1 for i in range(m)]
            top = [bool(x > 1.0) for x in e]
            name, day = f"N{g % 3}", _iso(FIRST_SETTLE + datetime.timedelta(days=g))
            st.add_cluster("2021-01-01", name, day, "2020-01-01", [i % 2 for i in range(m)], [float(x) for x in e],
                           [float(x) for x in r], sides=sides, top=top)
            rows += [{"name": name, "date": day, "e": float(a), "e_dir": 0.0, "e_shape": 0.0, "r": float(c), "w": 1.0,
                      "side": s, "top": t} for a, c, s, t in zip(e, r, sides, top)]

        def ols(sel):
            x = np.array([[1.0, q["e"]] for q in sel])
            return np.linalg.solve(x.T @ x, x.T @ np.array([q["r"] for q in sel]))

        for key, sel in (("buy", [q for q in rows if q["side"] == 1]), ("sell", [q for q in rows if q["side"] == -1]),
                         ("top", [q for q in rows if q["top"]])):
            f = st.fit(key)
            assert f["rows"] == len(sel)
            assert np.allclose([f["a"], f["b"]], ols(sel), atol=1e-9)

    def test_no_sides_or_top_leaves_the_units_empty(self):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0])
        for key in ("buy", "sell", "top"):
            assert st.fit(key)["rows"] == 0

    @pytest.mark.parametrize("kw", [
        {"weights": [1.0, 0.0, 1.0]}, {"weights": [1.0, -1.0, 1.0]}, {"weights": [1.0, float("nan"), 1.0]},
        {"weights": [1.0, 1.0]}, {"sides": [1, 0, -1]}, {"sides": [1, 1]}, {"sides": [1, True, -1]},
        {"top": [True, False]}, {"top": [1, 0, 1]},
    ])
    def test_bad_weights_sides_top_are_value_errors(self, kw):
        st = LinearEdgeState(1, None)
        st.advance("2021-01-01")
        with pytest.raises(ValueError):
            st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0],
                           **kw)
        assert st.fit(None)["rows"] == 0


class TestWeightedNameAdjustment:
    def test_weighted_adjustment_equals_weighted_batch_and_kish_on_cluster_weights(self):
        cl = _name_clusters("AA", 6)
        adj = NameAdjustment(NAME)
        ws = []
        for k, c in enumerate(cl):
            w = [float(1 + (i + k) % 3) for i in range(len(c["e"]))]
            ws.append(w)
            adj.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"], weights=w)
        wt = [v for w in ws for v in w]
        raw = _ols([v for c in cl for v in c["e"]], [v for c in cl for v in c["u"]], wt)
        cw = np.array([sum(w) for w in ws])
        g_eff = cw.sum() ** 2 / (cw ** 2).sum()
        assert adj.effective_clusters("AA") == pytest.approx(g_eff, rel=1e-12)
        assert np.allclose(adj.adjust("AA"), raw * g_eff / (g_eff + 26), rtol=1e-9)

    @pytest.mark.parametrize("w", [[1.0, 0.0, 1.0, 1.0, 1.0], [1.0, -1.0, 1.0, 1.0, 1.0], [1.0, 1.0]])
    def test_bad_weights_refuse(self, w):
        adj = NameAdjustment(NAME)
        c = _name_clusters("AA", 1)[0]
        with pytest.raises(ValueError):
            adj.add_cluster("2021-01-01", "AA", c["settle"], c["e"], c["r"], c["c"], weights=w)

    def test_gate_reads_kish_helper(self):
        # three clusters of weights 10, 1, 1: G_eff = 144 / 102 = 1.41 < name_min_clusters 2 -> no adjustment
        adj = NameAdjustment({**NAME, "name_min_clusters": 2})
        for k, m in enumerate((10, 1, 1)):
            e = [float(i) for i in range(m + 1)]
            adj.add_cluster("2021-01-01", "AA", _iso(FIRST_SETTLE + datetime.timedelta(days=k)), e,
                            [2.0 * x for x in e], [0.0] * len(e), weights=[m / (m + 1.0)] * (m + 1))
        assert adj.effective_clusters("AA") == pytest.approx(144 / 102, rel=1e-12)
        assert adj.adjust("AA") == (0.0, 0.0)


class TestRunningKishSums:
    """A-T8 (names and dates), A-T1 dump keys."""

    def test_running_sum_of_squared_unit_weights_equals_the_recomputed_one_after_every_arrival(self):
        clusters = _with_group_weights(_clusters_k2(equal=False))
        st = LinearEdgeState(N_BUCKETS, 0.9, regressors=2)
        gate = SettledBefore("2021-07-01")
        arrived, totals, arrived_at = set(), {}, {}
        for k, t in enumerate(_weeks()):
            st.advance(t)
            for i, c in enumerate(clusters):
                if i not in arrived and gate.admits(c["settle"], t):
                    st.add_cluster(t, c["name"], c["settle"], c["fill"], c["bucket"], c["e"], c["r"],
                                   e_dir=c["e_dir"], weights=c["weights"])
                    arrived.add(i)
                    arrived_at[i] = k
                    u = st._pooled
                    for table in (u.names, u.dates):
                        want = sum(cell[0] ** 2 for cell in table.cells.values())
                        assert math.isclose(table.w2, want, rel_tol=1e-12)
                    totals[i] = sum(c["weights"]) * 0.9 ** 0  # total row weight of the cluster at arrival
                    # the (name, date) clusters' squared weights, in the arrival units of this eager state
                    assert u.cw2 > 0.0
        want_cw2 = sum((totals[i] * 0.9 ** (len(_weeks()) - 1 - arrived_at[i])) ** 2 for i in totals)
        assert math.isclose(st._pooled.cw2, want_cw2, rel_tol=1e-9)

    def test_k1_dump_differs_from_stage_1_only_by_new_keys(self):
        st = LinearEdgeState(2, None)
        st.advance("2021-01-01")
        st.add_cluster("2021-01-01", "AA", "2020-01-10", "2020-01-07", [0] * 3, [1.0, 2.0, 4.0], [1.0, 3.0, 2.0])
        d = st.to_dict()
        stage1_top = {"n_buckets", "decay", "t", "seen", "pooled", "buckets"}
        assert stage1_top <= set(d) and set(d) - stage1_top == {"regressors", "block_lengths", "counts"}
        stage1_unit = {"tot", "cross", "rows", "clusters", "names", "dates"}
        assert stage1_unit <= set(d["pooled"])
        assert d["pooled"]["tot"] == [3.0, 7.0, 6.0, 21.0, 1.0 + 6.0 + 8.0, 1.0 + 9.0 + 4.0]

    def test_a_dump_without_the_new_keys_is_refused(self):
        st = LinearEdgeState(2, None)
        blob = st.to_dict()
        for key in ("regressors", "block_lengths", "counts"):
            bad = {k: v for k, v in blob.items() if k != key}
            with pytest.raises(ValueError):
                LinearEdgeState.from_dict(bad)


def test_kish_in_the_public_surface():
    assert "kish_effective" in er.__all__ and er.__all__[-4:] == [
        "PartialPooling", "NameAdjustment", "EdgeCalibrationMap", "TrailingRegime"]

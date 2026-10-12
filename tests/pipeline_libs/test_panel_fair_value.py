"""PanelHorizonFairValue: P(YES) of a binary contract from a horizon forecaster's CDF, at a decision instant.

Synthetic hourly panel and a tiny CPU net (test_panel_torch's scaffold). Expected probabilities are restated
independently with ``statistics.NormalDist`` from the mixture's own parameters, never from the node.
"""

import json
import math
from statistics import NormalDist

import pytest

np = pytest.importorskip("numpy")
torch = pytest.importorskip("torch")

from dskit.pipeline.base import ConfigError  # noqa: E402
from dskit.pipeline.libs.array_panel import ArrayPanel  # noqa: E402
from dskit.pipeline.libs.horizon_cdf import HorizonForecaster, MixtureHorizons  # noqa: E402
from dskit.pipeline.libs.panel_torch import (  # noqa: E402
    HORIZON_RULES,
    NODE_KINDS,
    PanelHorizonFairValue,
    TorchHorizonForecaster,
)
from dskit.pipeline.libs.predictive_cdf import mixture_family  # noqa: E402

from .test_panel_torch import BOUNDS, STEP, build_panel, ctx, fit  # noqa: E402

_N = NormalDist()
PANEL_SPEC = {"root": "r", "source": "s", "stream": "st", "relpath": "unused"}
GROUPS = {"A": 10, "B": 20}
OFFSET = -1
REF = 100.0
CHUNK = 7
BASE = {
    "panel": PANEL_SPEC, "group_field": "series", "group_codes": GROUPS, "time_field": "decision_ms",
    "row_offset_ms": OFFSET, "horizon_field": "lead", "horizon_unit_ms": STEP, "horizon_rule": "nearest",
    "reference_field": "spot", "payoff_field": "payoff", "lower_field": "floor", "upper_field": "cap",
    "fair_field": "fair", "chunk_rows": CHUNK,
}


@pytest.fixture(autouse=True)
def store_stand_in(monkeypatch):
    """Open ``spec["relpath"]`` directly: the store round trip is array_panel's own test."""
    monkeypatch.setattr(ArrayPanel, "from_source", classmethod(lambda cls, spec: cls.open(spec["relpath"])))


@pytest.fixture
def path(tmp_path):
    return build_panel(tmp_path)


@pytest.fixture
def trained(tmp_path, path):
    node, out = fit(tmp_path, path, epochs=2)
    return out


def spec(path, **over):
    return {**BASE, "panel": {**PANEL_SPEC, "relpath": path}, **over}


def decision(panel, row, lead=1, **over):
    """A decision row for global panel row ``row``: the instant just after the panel row's bar closes."""
    code = int(panel.group[row])
    series = {v: k for k, v in GROUPS.items()}[code]
    return {"series": series, "decision_ms": int(panel.time[row]) - OFFSET, "lead": lead, "spot": REF,
            "payoff": "above", "floor": REF, "cap": None, **over}


def val_rows(path, group=10, count=5):
    panel = ArrayPanel.open(path)
    rows = np.flatnonzero(np.asarray(panel.group) == group)
    rows = rows[np.asarray(panel.time)[rows] > BOUNDS["val_start_ms"]]
    return panel, [int(r) for r in rows[:count]]


def run(params, rows, tmp_path, model=None):
    node = PanelHorizonFairValue("fair", params)
    inputs = {"records": rows} if model is None else {"records": rows, "model": model}
    return node.run(ctx(tmp_path, "fv"), inputs)


def gaussian_cdf(dist, row, h, z):
    w = torch.softmax(dist.logw[row, h], -1).tolist()
    return sum(wk * _N.cdf((z - m) / s) for wk, m, s in zip(w, dist.mu[row, h].tolist(), dist.sigma[row, h].tolist()))


# --- the number ----------------------------------------------------------------------------


def test_no_registered_kinds_and_the_rule_registry():
    assert NODE_KINDS == ()
    assert set(HORIZON_RULES) == {"nearest", "floor", "exact"}


@pytest.mark.parametrize("payoff,lower,upper", [("above", 0.0, None), ("below", None, 0.0), ("between", -0.5, 0.5)])
def test_probability_is_the_mixture_cdf_at_the_strike_in_scale_units(tmp_path, path, trained, payoff, lower, upper):
    panel, rows = val_rows(path)
    forecaster = trained["model"]
    dist = forecaster.forecast(panel, np.array(rows), 8)
    out = run(spec(path), [decision(panel, r, lead=3, payoff=payoff,
                                    floor=None if lower is None else REF * math.exp(lower * float(panel.reference[r])),
                                    cap=None if upper is None else REF * math.exp(upper * float(panel.reference[r])))
                           for r in rows], tmp_path, model=forecaster)
    for i, r in enumerate(rows):
        survive = lambda k: 1.0 - gaussian_cdf(dist, i, 1, k)   # horizon 3 is the panel's second column
        want = {"above": survive(0.0), "below": 1.0 - survive(0.0), "between": survive(-0.5) - survive(0.5)}[payoff]
        row = out["records"][i]
        assert row["fair_status"] == "ok" and row["fair"] == pytest.approx(want, abs=2e-6)
        assert row["fair_horizon"] == 3 and row["fair_scale"] == pytest.approx(float(panel.reference[r]))
    assert out["census"]["priced"] == len(rows)


def test_the_artifact_route_restores_the_same_model_and_pins_its_hash(tmp_path, path, trained):
    panel, rows = val_rows(path)
    sidecar = json.load(open(trained["artifact_path"].replace(".pt", ".json")))
    via_port = run(spec(path), [decision(panel, r) for r in rows], tmp_path, model=trained["model"])
    via_file = run(spec(path, artifact=trained["artifact_path"], state_hash=sidecar["state_hash"]),
                   [decision(panel, r) for r in rows], tmp_path)
    assert [r["fair"] for r in via_file["records"]] == [r["fair"] for r in via_port["records"]]
    with pytest.raises(ValueError, match="state_hash"):
        run(spec(path, artifact=trained["artifact_path"], state_hash="0" * 64), [decision(panel, rows[0])], tmp_path)


def test_exactly_one_model_source(tmp_path, path, trained):
    panel, rows = val_rows(path)
    with pytest.raises(ValueError, match="model"):
        run(spec(path), [decision(panel, rows[0])], tmp_path)
    both = spec(path, artifact=trained["artifact_path"], state_hash="x")
    with pytest.raises(ValueError, match="model"):
        run(both, [decision(panel, rows[0])], tmp_path, model=trained["model"])


def test_artifact_needs_its_hash_and_a_port_model_refuses_one(path):
    problems = PanelHorizonFairValue.validate_params(spec(path, artifact="m.pt"))
    assert any("state_hash" in p for p in problems)
    problems = PanelHorizonFairValue.validate_params(spec(path, state_hash="abc"))
    assert any("state_hash" in p for p in problems)


# --- the leakage guard ---------------------------------------------------------------------


def test_a_decision_inside_the_training_range_is_refused(tmp_path, path, trained):
    panel = ArrayPanel.open(path)
    inside = int(np.flatnonzero((np.asarray(panel.group) == 10) & (np.asarray(panel.time) <= BOUNDS["train_end_ms"]))[-1])
    with pytest.raises(ValueError, match="train_end"):
        run(spec(path), [decision(panel, inside)], tmp_path, model=trained["model"])


def test_the_guard_reaches_past_train_end_by_the_label_span(tmp_path, path, trained):
    panel = ArrayPanel.open(path)
    edge = BOUNDS["train_end_ms"] + panel.label_span_ms
    times = np.asarray(panel.time)
    row_at = int(np.flatnonzero((np.asarray(panel.group) == 10) & (times == edge))[0])
    after = row_at + 1
    with pytest.raises(ValueError, match="train_end"):
        run(spec(path), [decision(panel, row_at)], tmp_path, model=trained["model"])
    assert run(spec(path), [decision(panel, after)], tmp_path, model=trained["model"])["records"][0]["fair_status"] == "ok"


def test_a_forecaster_that_does_not_state_its_train_end_is_refused(tmp_path, path):
    panel, rows = val_rows(path)
    with pytest.raises(ValueError, match="train_end"):
        run(spec(path), [decision(panel, rows[0])], tmp_path, model=_Stub(train_end_ms=None))


# --- stub forecasters: horizons, missing rows, NaN ------------------------------------------


class _Stub(HorizonForecaster):
    """Horizon h has a Gaussian at mean ``0.1 * h`` and scale 1: the chosen column is readable from the number."""

    def __init__(self, train_end_ms=BOUNDS["train_end_ms"], nan=False, horizons=2):
        self.train_end_ms, self._nan, self._h = train_end_ms, nan, horizons

    def usable(self, panel, rows):
        return np.asarray(panel.run)[np.asarray(rows)] >= 1

    def forecast(self, panel, rows, chunk_rows):
        n = len(rows)
        mu = (torch.arange(1, self._h + 1, dtype=torch.float64) * 0.1).reshape(1, -1, 1).expand(n, -1, 1).clone()
        sigma = torch.ones_like(mu)
        if self._nan:
            mu[0, 0, 0] = float("nan")
        return MixtureHorizons(torch.zeros_like(mu), mu, sigma, mixture_family({"kind": "gaussian"}))


def test_the_horizon_column_follows_the_rule(tmp_path, path):
    panel, rows = val_rows(path, count=1)
    for lead, rule, bars in ((1, "nearest", 1), (3, "nearest", 3), (2, "floor", 1), (3, "exact", 3)):
        out = run(spec(path, horizon_rule=rule), [decision(panel, rows[0], lead=lead)], tmp_path, model=_Stub())
        row = out["records"][0]
        assert row["fair_status"] == "ok" and row["fair_horizon"] == bars
        # the stub's second column is horizon 3: mean 0.2; the first is horizon 1: mean 0.1
        mean = 0.1 if bars == 1 else 0.2
        assert row["fair"] == pytest.approx(1.0 - _N.cdf((0.0 - mean) / 1.0), abs=1e-9)


@pytest.mark.parametrize("lead,rule,status", [(2, "exact", "horizon_out_of_range"), (9, "nearest", "horizon_out_of_range"),
                                              (0.4, "nearest", "horizon_out_of_range"), (None, "nearest", "bad_input"),
                                              ("x", "nearest", "bad_input")])
def test_a_lead_the_rule_cannot_place_is_named_not_clamped(tmp_path, path, lead, rule, status):
    panel, rows = val_rows(path, count=1)
    out = run(spec(path, horizon_rule=rule), [decision(panel, rows[0], lead=lead)], tmp_path, model=_Stub())
    assert out["records"][0]["fair"] is None and out["records"][0]["fair_status"] == status


def test_missing_rows_are_named_and_counted_never_priced(tmp_path, path):
    panel, rows = val_rows(path, count=2)
    gap = decision(panel, rows[0], decision_ms=int(panel.time[rows[0]]) + 1000 * STEP)
    other = decision(panel, rows[1], series="Z")
    bad = decision(panel, rows[1], payoff="sideways")
    flat = decision(panel, rows[1], spot=-1.0)
    good = decision(panel, rows[1])
    out = run(spec(path), [gap, other, bad, flat, good], tmp_path, model=_Stub())
    assert [r["fair_status"] for r in out["records"]] == ["no_panel_row", "unknown_group", "bad_input", "bad_input", "ok"]
    assert [r["fair"] is None for r in out["records"]] == [True, True, True, True, False]
    assert out["census"]["by_status"]["no_panel_row"] == 1 and out["census"]["priced"] == 1


def test_a_row_the_model_cannot_window_is_named(tmp_path, path):
    class NoWindow(_Stub):
        def usable(self, panel, rows):
            return np.zeros(len(rows), dtype=bool)

    panel, rows = val_rows(path, count=1)
    out = run(spec(path), [decision(panel, rows[0])], tmp_path, model=NoWindow())
    assert out["records"][0]["fair_status"] == "window_gap"


def test_a_one_row_distribution_for_a_multi_row_chunk_is_refused(tmp_path, path):
    """A row-constant distribution would price every row from one forecast: refused, never broadcast."""
    class OneRow(_Stub):
        def forecast(self, panel, rows, chunk_rows):
            return super().forecast(panel, rows[:1], chunk_rows)

    panel, rows = val_rows(path, count=3)
    with pytest.raises(ValueError, match="returned"):
        run(spec(path), [decision(panel, r) for r in rows], tmp_path, model=OneRow())


def test_a_non_finite_cdf_is_refused_not_dropped(tmp_path, path):
    panel, rows = val_rows(path, count=2)
    with pytest.raises(ValueError, match="finite"):
        run(spec(path), [decision(panel, r) for r in rows], tmp_path, model=_Stub(nan=True))


def test_a_non_finite_scale_is_named(tmp_path, path):
    panel, rows = val_rows(path, count=1)
    broken = build_panel(tmp_path, name="broken")
    array = np.load(f"{broken}/reference.npy")
    array[rows[0]] = float("nan")
    np.save(f"{broken}/reference.npy", array)
    out = run(spec(broken), [decision(panel, rows[0])], tmp_path, model=_Stub())
    assert out["records"][0]["fair_status"] == "bad_scale"


# --- params --------------------------------------------------------------------------------


@pytest.mark.parametrize("over,fragment", [
    ({"typo": 1}, "typo"), ({"horizon_rule": "nope"}, "horizon_rule"), ({"group_codes": {}}, "group_codes"),
    ({"group_codes": {"A": True}}, "group_codes"), ({"chunk_rows": 0}, "chunk_rows"),
    ({"horizon_unit_ms": 0}, "horizon_unit_ms"), ({"row_offset_ms": 1.5}, "row_offset_ms"),
    ({"fair_field": "spot"}, "fair_field"), ({"panel": "p"}, "panel"),
])
def test_default_deny_params(path, over, fragment):
    with pytest.raises(ConfigError, match=fragment):
        PanelHorizonFairValue("fair", spec(path, **over))


@pytest.mark.parametrize("missing", sorted(set(BASE) - {"panel"}) + ["panel"])
def test_every_knob_is_required(path, missing):
    params = spec(path)
    params.pop(missing)
    with pytest.raises(ConfigError, match=missing):
        PanelHorizonFairValue("fair", params)


def test_the_forecaster_states_its_train_end(trained):
    forecaster = trained["model"]
    assert isinstance(forecaster, TorchHorizonForecaster)
    assert forecaster.train_end_ms == BOUNDS["train_end_ms"]


def test_usable_rejects_short_runs_and_untrained_groups(tmp_path, path, trained):
    panel = ArrayPanel.open(path)
    group10 = np.flatnonzero(np.asarray(panel.group) == 10)
    rows = np.array([group10[0], group10[-1]])
    assert list(trained["model"].usable(panel, rows)) == [False, True]

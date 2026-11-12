"""Feature faults of the forecast input-fault study (ADR-0316 Decision B1).

Every frame is a toy panel small enough to list by hand: two groups, three sessions, two features. The
expected rows are written out here, never read back from the code under test.
"""
import subprocess
import sys

import pytest

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

from dskit.pipeline.base import ConfigError, value_hash
from dskit.pipeline.libs.cdf_faults import (FeatureFault, MissingFeatures, ScaledFeatures,
                                            StaleFeatures)

IDENTITY = ["symbol", "quote_date", "expiry"]
DATE = "quote_date"


def toy():
    """A has all three sessions; B has no 2001-01-02 row. The feature values encode (group, session)."""
    rows = [("A", "2001-01-01", "E1", 1., 10., 7.),
            ("A", "2001-01-02", "E1", 2., 20., 7.),
            ("A", "2001-01-03", "E1", 3., 30., 7.),
            ("B", "2001-01-01", "E2", 4., 40., 7.),
            ("B", "2001-01-03", "E2", 6., 60., 7.)]
    return pd.DataFrame(rows, columns=[*IDENTITY, "f1", "f2", "other"], index=[10, 11, 12, 13, 14])


def values(frame):
    return {(r.symbol, r.quote_date): (r.f1, r.f2, r.other) for r in frame.itertuples()}


# ---------------------------------------------------------------- the hook and default-deny construction

def test_the_hook_is_abstract_and_each_fault_is_one_subclass():
    with pytest.raises(TypeError):
        FeatureFault(columns=["f1"])

    class Partial(FeatureFault):
        pass

    with pytest.raises(TypeError):
        Partial(columns=["f1"])
    for cls in (StaleFeatures, MissingFeatures, ScaledFeatures):
        assert issubclass(cls, FeatureFault)


def test_the_params_tuple_is_base_plus_extra_up_the_bases():
    assert FeatureFault.params_tuple() == ("columns",)
    assert StaleFeatures.params_tuple() == ("columns", "sessions")
    assert MissingFeatures.params_tuple() == ("columns",)
    assert ScaledFeatures.params_tuple() == ("columns", "factor")


def test_unknown_missing_and_bad_values_are_all_listed_at_once():
    with pytest.raises(ConfigError) as caught:
        StaleFeatures(columns=[], sessions=0, colour="red")
    text = "\n".join(caught.value.errors)
    assert len(caught.value.errors) == 3
    assert "colour" in text and "columns" in text and "sessions" in text
    with pytest.raises(ConfigError) as caught:
        ScaledFeatures()
    assert "columns" in "\n".join(caught.value.errors) and "factor" in "\n".join(caught.value.errors)


@pytest.mark.parametrize("columns", [[], "f1", ["f1", "f1"], ["f1", ""], ["f1", 2], None, ("f1",)])
def test_columns_must_be_a_nonempty_unique_list_of_names(columns):
    with pytest.raises(ConfigError, match="columns"):
        MissingFeatures(columns=columns)


@pytest.mark.parametrize("sessions", [0, -1, True, 1.5, "2", None])
def test_sessions_is_a_positive_integer(sessions):
    with pytest.raises(ConfigError, match="sessions"):
        StaleFeatures(columns=["f1"], sessions=sessions)


@pytest.mark.parametrize("factor", [1.0, 1, True, float("nan"), float("inf"), -float("inf"), "2", None])
def test_factor_is_a_finite_number_that_is_not_one(factor):
    with pytest.raises(ConfigError, match="factor"):
        ScaledFeatures(columns=["f1"], factor=factor)


def test_a_zero_or_negative_factor_is_a_legal_fault():
    assert ScaledFeatures(columns=["f1"], factor=0).params["factor"] == 0
    assert ScaledFeatures(columns=["f1"], factor=-2.5).params["factor"] == -2.5


def test_columns_must_be_features_of_the_arm():
    fault = MissingFeatures(columns=["f1", "typo", "other"])
    problems = fault.feature_problems(["f1", "f2"])
    assert len(problems) == 1 and "typo" in problems[0] and "other" in problems[0]
    assert MissingFeatures(columns=["f2"]).feature_problems(["f1", "f2"]) == []


def test_the_constructor_copies_its_params():
    columns = ["f1"]
    fault = MissingFeatures(columns=columns)
    columns.append("f2")
    assert fault.params["columns"] == ["f1"]


# ---------------------------------------------------------------- canonical object for hashing

def test_canonical_object_names_the_class_and_every_param():
    assert StaleFeatures(columns=["f1", "f2"], sessions=2).to_obj() == {
        "class": "dskit.pipeline.libs.cdf_faults:StaleFeatures",
        "params": {"columns": ["f1", "f2"], "sessions": 2}}
    assert ScaledFeatures(columns=["f1"], factor=3).to_obj()["params"] == {"columns": ["f1"], "factor": 3}
    assert MissingFeatures(columns=["f1"]).to_obj() == {
        "class": "dskit.pipeline.libs.cdf_faults:MissingFeatures", "params": {"columns": ["f1"]}}


def test_every_knob_and_the_class_change_the_hash():
    hashes = [value_hash(f.to_obj()) for f in (
        StaleFeatures(columns=["f1"], sessions=1), StaleFeatures(columns=["f1"], sessions=2),
        StaleFeatures(columns=["f2"], sessions=1), StaleFeatures(columns=["f1", "f2"], sessions=1),
        MissingFeatures(columns=["f1"]), ScaledFeatures(columns=["f1"], factor=2),
        ScaledFeatures(columns=["f1"], factor=3), ScaledFeatures(columns=["f2"], factor=2))]
    assert len(set(hashes)) == len(hashes)


# ---------------------------------------------------------------- missing and scaled

def test_missing_blanks_exactly_the_declared_columns_and_keeps_every_row():
    frame = toy()
    out = MissingFeatures(columns=["f1"]).apply(frame, IDENTITY, DATE)
    assert list(out.index) == [10, 11, 12, 13, 14]
    assert out["f1"].isna().all()
    pd.testing.assert_series_equal(out["f2"], frame["f2"])
    pd.testing.assert_series_equal(out["other"], frame["other"])


def test_scaled_multiplies_exactly_the_declared_columns():
    frame = toy()
    out = ScaledFeatures(columns=["f1", "f2"], factor=-2).apply(frame, IDENTITY, DATE)
    assert list(out["f1"]) == [-2., -4., -6., -8., -12.]
    assert list(out["f2"]) == [-20., -40., -60., -80., -120.]
    pd.testing.assert_series_equal(out["other"], frame["other"])


@pytest.mark.parametrize("fault", [MissingFeatures(columns=["f1", "f2"]),
                                   ScaledFeatures(columns=["f1"], factor=3),
                                   StaleFeatures(columns=["f1", "f2"], sessions=1)])
def test_a_fault_never_mutates_its_input(fault):
    frame = toy()
    before = frame.copy(deep=True)
    out = fault.apply(frame, IDENTITY, DATE)
    pd.testing.assert_frame_equal(frame, before)
    out[["f1", "f2"]] = 0.
    pd.testing.assert_frame_equal(frame, before)


def test_a_column_the_frame_lacks_or_an_identity_column_refuses():
    with pytest.raises(ValueError, match="nope"):
        MissingFeatures(columns=["nope"]).apply(toy(), IDENTITY, DATE)
    with pytest.raises(ValueError, match="expiry"):
        ScaledFeatures(columns=["expiry"], factor=2).apply(toy(), IDENTITY, DATE)


# ---------------------------------------------------------------- stale

def test_stale_one_session_is_listed_by_hand():
    out = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(toy(), IDENTITY, DATE)
    # A on 01-02 takes A's 01-01 row; A on 01-03 takes A's 01-02 row. B on 01-03 has no B row on the
    # global previous date 01-02, and nothing precedes 01-01, so those rows leave.
    assert values(out) == {("A", "2001-01-02"): (1., 10., 7.), ("A", "2001-01-03"): (2., 20., 7.)}
    assert list(out.index) == [11, 12]
    assert list(out.columns) == list(toy().columns)


def test_stale_two_sessions_counts_over_the_global_distinct_dates():
    out = StaleFeatures(columns=["f1", "f2"], sessions=2).apply(toy(), IDENTITY, DATE)
    # Global dates are 01-01, 01-02, 01-03; two sessions back from 01-03 is 01-01 for both groups, although
    # B has only two own dates.
    assert values(out) == {("A", "2001-01-03"): (1., 10., 7.), ("B", "2001-01-03"): (4., 40., 7.)}
    assert list(out.index) == [12, 14]


def test_stale_beyond_the_history_keeps_no_row():
    out = StaleFeatures(columns=["f1"], sessions=3).apply(toy(), IDENTITY, DATE)
    assert out.empty and list(out.columns) == list(toy().columns)


def test_stale_changes_only_the_declared_columns():
    out = StaleFeatures(columns=["f1"], sessions=1).apply(toy(), IDENTITY, DATE)
    assert values(out) == {("A", "2001-01-02"): (1., 20., 7.), ("A", "2001-01-03"): (2., 30., 7.)}


def test_stale_pairs_on_the_identity_keys_other_than_the_date():
    frame = toy()
    # A second expiry for A on both dates: each row must take the row with ITS expiry, not A's other one.
    extra = pd.DataFrame([("A", "2001-01-01", "E9", 91., 910., 7.), ("A", "2001-01-02", "E9", 92., 920., 7.)],
                         columns=frame.columns, index=[20, 21])
    out = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(pd.concat([frame, extra]), IDENTITY, DATE)
    got = {(r.symbol, r.quote_date, r.expiry): (r.f1, r.f2) for r in out.itertuples()}
    assert got == {("A", "2001-01-02", "E1"): (1., 10.), ("A", "2001-01-03", "E1"): (2., 20.),
                   ("A", "2001-01-02", "E9"): (91., 910.)}


def test_stale_never_reads_a_later_row():
    frame = toy()
    base = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(frame, IDENTITY, DATE)
    # Rewrite every row from the last global date on: nothing taken from an earlier date may change.
    later = frame.copy()
    later.loc[later[DATE] == "2001-01-03", ["f1", "f2"]] = [-999., -999.]
    changed = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(later, IDENTITY, DATE)
    assert values(changed)[("A", "2001-01-02")] == values(base)[("A", "2001-01-02")]
    assert set(changed.index) == set(base.index)
    # And every value taken is the value of a strictly earlier date of the same identity.
    source = {(r.symbol, r.expiry, r.quote_date): r.f1 for r in frame.itertuples()}
    for r in base.itertuples():
        earlier = [d for (g, e, d), v in source.items() if (g, e) == (r.symbol, r.expiry) and d < r.quote_date]
        assert r.f1 in [source[(r.symbol, r.expiry, d)] for d in earlier]


def test_stale_result_index_names_the_kept_rows_and_identity_survives():
    frame = toy()
    out = StaleFeatures(columns=["f1"], sessions=1).apply(frame, IDENTITY, DATE)
    assert out.index.is_unique and set(out.index) <= set(frame.index)
    pd.testing.assert_frame_equal(out[IDENTITY], frame.loc[out.index, IDENTITY])


def test_stale_on_an_unsorted_frame_gives_the_same_rows():
    frame = toy()
    shuffled = frame.sample(frac=1., random_state=0)
    a = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(frame, IDENTITY, DATE)
    b = StaleFeatures(columns=["f1", "f2"], sessions=1).apply(shuffled, IDENTITY, DATE)
    assert list(b.index) == [i for i in shuffled.index if i in set(b.index)]    # the input's order of the kept rows
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())


# ---------------------------------------------------------------- purity

def test_import_pulls_no_heavy_library_and_no_evaluation_package():
    code = ("import sys, dskit.pipeline.libs.cdf_faults as m; "
            "bad = [n for n in ('numpy','pandas','torch','sklearn','dskit.evaluation') if n in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


# ================================================================ ForecastFaultStudy (ADR-0316 Amendment 2)

import copy
import hashlib
import inspect
import json
from pathlib import Path

from dskit.pipeline.libs.cdf_experiment import AtomicFitStore, CDFExperiment, IntegrityError
from dskit.pipeline.libs.cdf_faults import MONOTONE_TOLERANCE, REFUSAL_CODES, ForecastFaultStudy
from dskit.pipeline.libs.predictive_cdf import ChronologicalCDFStudy, TorchCDF
from tests.pipeline_libs.test_cdf_experiment import experiment_config  # noqa: F401  (the shared fixture)

NAME = "dskit.pipeline.libs.cdf_faults:"
STALE, MISSING, SCALED = ["rv_5", "ret_lag_0"], ["rv_5", "momentum_5"], ["rv_5"]
Z, W, TAIL = [-1., 0., .5, 2.], [1., 3., 2., 1.5], 4.   # uneven spacing, uneven weights


class InfFault(FeatureFault):
    """A test fault that writes an infinity."""

    def apply(self, frame, identity, date):
        out = frame.copy()
        out[self.params["columns"]] = float("inf")
        return out


class HugeFault(FeatureFault):
    """A test fault whose value is finite as float64 and overflows float32."""

    def apply(self, frame, identity, date):
        out = frame.copy()
        out[self.params["columns"]] = 1e300
        return out


class MarkFault(FeatureFault):
    """A test fault that writes a marker value a patched model answers with a broken CDF."""

    def apply(self, frame, identity, date):
        out = frame.copy()
        out[self.params["columns"]] = 12345.
        return out


class BoomFault(FeatureFault):
    """A test fault that fails in a way no refusal code covers."""

    def apply(self, frame, identity, date):
        raise RuntimeError("boom")


class InventFault(FeatureFault):
    """A test fault that returns a row the band never had."""

    def apply(self, frame, identity, date):
        out = frame.copy()
        out.iloc[0, out.columns.get_loc("symbol")] = "ZZZ"
        return out


def me(cls):
    return cls.__module__ + ":" + cls.__name__


def entry(name, cls, **params):
    return {"name": name, "class": cls if ":" in cls else NAME + cls, "params": params}


@pytest.fixture
def fitted(experiment_config):  # noqa: F811
    """A completed pooled fit whose evaluation band (2001-02-01..03, two groups) is non-empty.

    The expiry is constant per ticker so a stale row can pair with its earlier session, and one feature is
    all zero so a scaling of it is a fault that changes nothing.
    """
    cfg = experiment_config
    frame = pd.read_parquet(cfg["panel"])
    frame["expiry"] = "2001-06-01"
    frame["vl_amihud"] = 0.0
    frame.loc[(frame.symbol == "BBB") & (frame.quote_date == "2001-02-03"), "expiry"] = "2001-07-01"
    frame["reference_scale"] = 0.02 + 0.001*np.arange(len(frame))     # varies by row: a wrong row's scale shows
    frame.to_parquet(cfg["panel"], index=False)
    cfg["panel_sha256"] = AtomicFitStore.file_hash(cfg["panel"])
    cfg["date_upper_exclusive"] = cfg["end_upper_exclusive"] = "2001-03-01"
    CDFExperiment(cfg).run("pooled")
    return cfg


def document(cfg, tmp_path, faults=None, **change):
    doc = {"experiment": copy.deepcopy(cfg), "fold": 1, "arm": "base", "candidate": cfg["candidates"][0]["name"],
           "sealed_from": "2001-03-01", "grid": {"z_grid": Z, "weights": W, "tail": TAIL},
           "faults": faults if faults is not None else [entry("stale", "StaleFeatures", columns=STALE, sessions=1)],
           "min_retained": 0.5, "min_scored": 2, "output": str(tmp_path/"study")}
    doc.update(change)
    return doc


class Reference:
    """The expected numbers, recomputed with plain numpy from the saved checkpoint and medians."""

    def __init__(self, cfg):
        exp = CDFExperiment(cfg)
        frame = exp.load_panel()
        bands, self.groups, _ = exp._bands(frame, exp.study.plan.calendar(frame), 1, cfg["arms"]["base"])
        self.band = bands[2].reset_index(drop=True)
        root = Path(cfg["output"])/"pooled"/"base"/cfg["candidates"][0]["name"]/"1"/"pooled"
        self.model = TorchCDF.load_checkpoint(root/"fit/model")
        self.medians = json.loads((root/"fit/metadata.json").read_text())["medians"]
        self.features = cfg["arms"]["base"]["features"]

    def x(self, frame):
        raw = frame[self.features].to_numpy(float)
        x = np.where(np.isnan(raw), self.medians, raw).astype("float32")
        hot = np.zeros((len(frame), len(self.groups)), "float32")
        hot[np.arange(len(frame)), [self.groups.index(g) for g in frame["symbol"]]] = 1
        return np.column_stack((x, hot))

    def cdf(self, frame):
        return np.clip(self.model.curve(self.x(frame)).cdf(np.asarray(Z)), 0., 1.)

    def score(self, cdf, rows):
        y = (self.band["terminal_return"]/self.band["reference_scale"]).to_numpy()[rows]
        z, gap = np.asarray(Z), np.diff(Z)
        coefficient = np.zeros(len(z))          # trapezoid weights from the actual z spacing, times the declared weights
        coefficient[:-1] += gap/2
        coefficient[1:] += gap/2
        return ((cdf - (y[:, None] <= z[None, :])) ** 2) @ (coefficient*np.asarray(W))

    def stale(self, columns):
        """Rows with ``columns`` from the previous session of the same ticker and expiry."""
        rows, source = [], []
        for _, part in self.band.groupby(["symbol", "expiry"]):
            part = part.sort_values("quote_date")
            for i in range(1, len(part)):
                row = part.iloc[i].copy()
                row[columns] = part.iloc[i - 1][columns]
                rows.append(row)
                source.append(part.index[i])
        frame = pd.DataFrame(rows)
        for c in columns:
            frame[c] = frame[c].astype(float)
        return frame, np.array(source)

    def expected(self, faulted, rows):
        clean, fault = self.cdf(self.band)[rows], self.cdf(faulted)
        shift = np.abs(fault - clean)
        before, after = self.score(clean, rows), self.score(fault, rows)
        return shift, after.mean()/before.mean() - 1.


def stats(cfg, tmp_path, faults, **change):
    return ForecastFaultStudy(document(cfg, tmp_path, faults, **change)).assess()


def test_stale_study_matches_a_hand_recomputation(fitted, tmp_path):
    ref = Reference(fitted)
    out = stats(fitted, tmp_path, [entry("stale", "StaleFeatures", columns=STALE, sessions=1)])
    faulted, rows = ref.stale(STALE)
    shift, change = ref.expected(faulted, rows)
    record = out["faults"][0]
    assert out["clean"] == {"rows": 6, "dates": 3, "groups": 2}
    assert (record["name"], record["status"], record["rows"], record["paired_rows"]) == ("stale", "ran", 6, 3)
    assert record["retained_fraction"] == pytest.approx(3/6)
    assert record["changed_rows"] == 3
    assert record["cdf_shift_mean"] == pytest.approx({"value": shift.mean(), "n": 3, "dates": 2, "groups": 2})
    assert record["cdf_shift_max"]["value"] == pytest.approx(shift.max())
    assert record["score_change"] == pytest.approx({"value": change, "n": 3, "dates": 2, "groups": 2})


@pytest.mark.parametrize("kind", ["missing", "scaled"])
def test_missing_uses_the_saved_medians_and_scaled_multiplies(fitted, tmp_path, kind):
    ref = Reference(fitted)
    fault = (entry("m", "MissingFeatures", columns=MISSING) if kind == "missing"
             else entry("s", "ScaledFeatures", columns=SCALED, factor=3.))
    out = stats(fitted, tmp_path, [fault])
    frame = ref.band.copy()
    if kind == "missing":
        frame[MISSING] = np.nan      # the study fills them with the medians saved at fit, never refitted
    else:
        frame[SCALED] = frame[SCALED]*3.
    shift, change = ref.expected(frame, np.arange(6))
    record = out["faults"][0]
    assert (record["status"], record["paired_rows"], record["retained_fraction"], record["changed_rows"]) == ("ran", 6, 1.0, 6)
    assert record["cdf_shift_mean"]["value"] == pytest.approx(shift.mean())
    assert record["cdf_shift_max"]["value"] == pytest.approx(shift.max())
    assert record["score_change"]["value"] == pytest.approx(change)
    assert record["score_change"]["n"] == 6


def test_a_fault_that_changes_nothing_is_inert(fitted, tmp_path):
    out = stats(fitted, tmp_path, [entry("zero", "ScaledFeatures", columns=["vl_amihud"], factor=5.)])
    record = out["faults"][0]
    assert record["status"] == "inert" and record["changed_rows"] == 0
    assert record["cdf_shift_max"]["value"] == 0.


@pytest.mark.parametrize("fault, code", [
    (entry("f", me(InfFault), columns=["rv_5"]), "infinite_input"),
    (entry("f", me(HugeFault), columns=["rv_5"]), "infinite_input"),
    (entry("f", "StaleFeatures", columns=STALE, sessions=3), "no_paired_rows"),
])
def test_a_refusal_is_a_closed_code_and_later_faults_still_run(fitted, tmp_path, fault, code):
    out = stats(fitted, tmp_path, [fault, entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    first, second = out["faults"]
    assert first["status"] == "refused" and first["reason"] == code and code in REFUSAL_CODES
    assert first["score_change"]["value"] is None and first["cdf_shift_mean"]["value"] is None
    assert second["status"] == "ran"


def test_a_broken_faulted_forecast_is_invalid_forecast(fitted, tmp_path, monkeypatch):
    real = TorchCDF.curve

    class Broken:
        def __init__(self, curve):
            self.curve = curve

        def cdf(self, z):
            return np.full((len(self.curve.weights), len(z)), np.nan)

    monkeypatch.setattr(TorchCDF, "curve", lambda self, x: Broken(real(self, x)) if (x == 12345.).any() else real(self, x))
    out = stats(fitted, tmp_path, [entry("f", me(MarkFault), columns=["rv_5"])])
    assert out["faults"][0]["status"] == "refused" and out["faults"][0]["reason"] == "invalid_forecast"


def test_too_few_retained_rows_refuse(fitted, tmp_path):
    out = stats(fitted, tmp_path, [entry("f", "StaleFeatures", columns=STALE, sessions=1)], min_retained=0.9)
    record = out["faults"][0]
    assert (record["status"], record["reason"], record["paired_rows"]) == ("refused", "retained_below_minimum", 3)
    assert record["retained_fraction"] == pytest.approx(3/6)


def test_score_change_is_null_below_min_scored(fitted, tmp_path):
    out = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)], min_scored=7)
    assert out["faults"][0]["score_change"]["value"] is None and out["faults"][0]["score_change"]["n"] == 6


@pytest.mark.parametrize("fault, error", [(entry("f", me(BoomFault), columns=["rv_5"]), RuntimeError),
                                         (entry("f", me(InventFault), columns=["rv_5"]), IntegrityError)])
def test_any_other_failure_aborts_and_publishes_nothing(fitted, tmp_path, fault, error):
    with pytest.raises(error):
        stats(fitted, tmp_path, [entry("ok", "MissingFeatures", columns=MISSING), fault])
    assert not (tmp_path/"study").exists()


def test_a_clean_arm_failure_aborts(fitted, tmp_path, monkeypatch):
    real = TorchCDF.curve

    class Nan:
        def __init__(self, curve):
            self.curve = curve

        def cdf(self, z):
            return np.full((len(self.curve.weights), len(z)), np.nan)

    monkeypatch.setattr(TorchCDF, "curve", lambda self, x: Nan(real(self, x)))
    with pytest.raises(IntegrityError, match="clean"):
        stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert not (tmp_path/"study").exists()


def test_a_missing_fit_refuses_and_the_study_never_fits(experiment_config, tmp_path, monkeypatch):  # noqa: F811
    cfg = experiment_config
    cfg["date_upper_exclusive"] = cfg["end_upper_exclusive"] = "2001-03-01"

    def forbidden(*args, **kwargs):
        raise AssertionError("fit invoked")
    monkeypatch.setattr(TorchCDF, "fit", forbidden)
    with pytest.raises(IntegrityError, match="never fits"):
        stats(cfg, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert not (tmp_path/"study").exists() and not Path(cfg["output"]).exists()


def tree(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_no_refit_no_scoring_and_the_fit_tree_is_untouched(fitted, tmp_path, monkeypatch):
    before = tree(fitted["output"])

    def forbidden(*args, **kwargs):
        raise AssertionError("fit or study scoring invoked")
    monkeypatch.setattr(TorchCDF, "fit", forbidden)
    monkeypatch.setattr(ChronologicalCDFStudy, "scores", forbidden)
    stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert tree(fitted["output"]) == before


def all_keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from all_keys(inner)


def test_only_statistics_and_a_receipt_are_written_and_they_hold_no_level_label_or_row(fitted, tmp_path):
    ref = Reference(fitted)
    stats(fitted, tmp_path, [entry("stale", "StaleFeatures", columns=STALE, sessions=1),
                             entry("f", me(InfFault), columns=["rv_5"])])
    out = tmp_path/"study"
    files = sorted(p.name for p in out.rglob("*") if p.is_file())
    assert files == ["complete.json", "receipt.json", "statistics.json"]
    allowed = {"clean", "faults", "rows", "dates", "groups", "name", "status", "reason", "paired_rows",
               "retained_fraction", "changed_rows", "cdf_shift_mean", "cdf_shift_max", "score_change", "value", "n"}
    assert set(all_keys(json.loads((out/"statistics.json").read_text()))) <= allowed
    receipt = json.loads((out/"receipt.json").read_text())
    assert set(receipt) == {"identity", "experiment", "groups", "faults", "evaluation_identity", "rows",
                            "scores_written"} and receipt["scores_written"] is False
    text = "".join(p.read_text() for p in out.rglob("*.json"))
    for label in (ref.band["terminal_return"]/ref.band["reference_scale"]).tolist():
        assert repr(label) not in text
    assert not list(out.rglob("*.parquet"))


def test_a_repeat_returns_the_published_statistics_unchanged(fitted, tmp_path):
    faults = [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]
    first = stats(fitted, tmp_path, faults)
    before = tree(tmp_path/"study")
    assert stats(fitted, tmp_path, faults) == first and tree(tmp_path/"study") == before


def test_the_seal_refuses_an_experiment_that_reads_past_it(fitted, tmp_path):
    with pytest.raises(IntegrityError, match="sealed_from"):
        ForecastFaultStudy(document(fitted, tmp_path, sealed_from="2001-02-28"))


def test_the_document_is_default_deny_and_every_problem_is_listed(fitted, tmp_path):
    good = document(fitted, tmp_path)
    with pytest.raises(ConfigError, match="extra"):
        ForecastFaultStudy(dict(good, extra=1))
    with pytest.raises(ConfigError, match="faults"):
        ForecastFaultStudy({k: v for k, v in good.items() if k != "faults"})
    bad = document(fitted, tmp_path, [
        entry("a", "MissingFeatures", columns=["typo"]), entry("a", "StaleFeatures", columns=["rv_5"], sessions=0),
        entry("c", "MissingFeatures", columns=["quote_date"]), entry("d", "dskit.pipeline.libs.cdf_faults:FeatureFault"),
        {"name": "e", "class": NAME + "MissingFeatures", "params": {"columns": ["rv_5"], "colour": 1}},
        {"name": "f", "class": NAME + "ScaledFeatures"}],
        grid={"z_grid": [1., 0.], "weights": [1.], "tail": 0}, min_retained=0, min_scored=0,
        sealed_from="soon", fold=9, arm="nope", candidate="nope", output="")
    with pytest.raises(ConfigError) as caught:
        ForecastFaultStudy(bad)
    text = "\n".join(caught.value.errors)
    for needle in ("fold", "arm", "candidate", "sealed_from", "z_grid", "weights", "tail", "min_retained",
                   "min_scored", "output", "unique", "FeatureFault subclass", "colour", "keys must be exactly"):
        assert needle in text, needle
    assert len(caught.value.errors) >= 12
    with pytest.raises(ConfigError, match="non-empty"):
        ForecastFaultStudy(document(fitted, tmp_path, []))


def test_fault_columns_must_be_arm_features_and_not_identity(fitted, tmp_path):
    with pytest.raises(ConfigError) as caught:
        ForecastFaultStudy(document(fitted, tmp_path, [entry("a", "MissingFeatures", columns=["typo"]),
                                                       entry("c", "MissingFeatures", columns=["quote_date"]),
                                                       entry("s", "StaleFeatures", columns=["rv_5"], sessions=0)]))
    text = "\n".join(caught.value.errors)
    assert "typo" in text and "quote_date" in text and "sessions" in text and len(caught.value.errors) >= 3


def test_a_class_outside_the_hook_is_not_a_fault(fitted, tmp_path):
    with pytest.raises(ConfigError, match="FeatureFault subclass"):
        ForecastFaultStudy(document(fitted, tmp_path, [entry("x", "pathlib:Path", columns=["rv_5"])]))


CHANGES = [("sealed", lambda d: d.update(sealed_from="2001-03-02")),
           ("z", lambda d: d["grid"].update(z_grid=Z[:-1] + [Z[-1] + 1.])),
           ("weights", lambda d: d["grid"].update(weights=W[::-1])),
           ("tail", lambda d: d["grid"].update(tail=5.)),
           ("retained", lambda d: d.update(min_retained=0.6)),
           ("scored", lambda d: d.update(min_scored=3)),
           ("output", lambda d: d.update(output=d["output"] + "2")),
           ("name", lambda d: d["faults"][0].update(name="renamed")),
           ("class", lambda d: d["faults"][0].update(**{"class": NAME + "MissingFeatures"}, params={"columns": STALE})),
           ("columns", lambda d: d["faults"][0]["params"].update(columns=["rv_5"])),
           ("sessions", lambda d: d["faults"][0]["params"].update(sessions=2)),
           ("order", lambda d: d["faults"].reverse()),
           ("added", lambda d: d["faults"].append(entry("more", "MissingFeatures", columns=["rv_5"])))]


@pytest.mark.parametrize("name, edit", CHANGES, ids=[c[0] for c in CHANGES])
def test_every_knob_and_the_fault_order_change_the_identity(fitted, tmp_path, name, edit):
    base = document(fitted, tmp_path, [entry("stale", "StaleFeatures", columns=STALE, sessions=1),
                                       entry("m", "MissingFeatures", columns=MISSING)])
    changed = copy.deepcopy(base)
    edit(changed)
    assert ForecastFaultStudy(base).study_identity != ForecastFaultStudy(changed).study_identity


def test_the_identity_is_stable_for_one_document(fitted, tmp_path):
    doc = document(fitted, tmp_path)
    assert ForecastFaultStudy(doc).study_identity == ForecastFaultStudy(copy.deepcopy(doc)).study_identity


def test_the_cdf_validity_tolerance_is_the_one_forecast_uses():
    # The rule is restated because the pinned cdf_experiment.py cannot be refactored to share it.
    assert f"< -{MONOTONE_TOLERANCE!r}" in inspect.getsource(CDFExperiment.forecast)
    valid = ForecastFaultStudy._valid_cdf
    assert valid(np.array([[0., .5, 1.]]))
    assert not valid(np.array([[0., .5, .5 - 1e-9]]))
    assert valid(np.array([[0., .5, .5 - 1e-13]]))
    for bad in ([[0., np.nan, 1.]], [[-.1, .5, 1.]], [[0., .5, 1.1]]):
        assert not valid(np.array(bad))


def test_the_inherited_run_and_forecast_refuse_and_write_nothing(fitted, tmp_path):
    before = tree(fitted["output"])
    study = ForecastFaultStudy(document(fitted, tmp_path))
    with pytest.raises(IntegrityError, match="never fits"):
        study.run("pooled")
    with pytest.raises(IntegrityError, match="no forecast rows"):
        study.forecast({})
    assert tree(fitted["output"]) == before
    assert not (tmp_path/"study").exists()


# ---------------------------------------------------------------- final-lens round 1: boundaries and cross-checks

import shutil

from dskit.pipeline.libs.cdf_grid_methods import GridCDFMethod


class DupFault(FeatureFault):
    """A test fault that repeats a row's identity."""

    def apply(self, frame, identity, date):
        return pd.concat([frame, frame.iloc[:1]])


def plant(monkeypatch, column, value):
    """Make the first evaluation row carry ``value`` in ``column`` (the panel read bounds hide such a row)."""
    real = CDFExperiment._bands

    def patched(self, *args):
        bands, groups, admission = real(self, *args)
        val = bands[2].copy()
        val.iloc[0, val.columns.get_loc(column)] = value
        return (bands[0], bands[1], val), groups, admission
    monkeypatch.setattr(CDFExperiment, "_bands", patched)


@pytest.mark.parametrize("column, value, refused", [
    ("quote_date", "2001-03-01", True),
    ("settlement_date", "2001-03-01", True), ("settlement_date", "2001-02-28", False),
    ("quote_date", "2001-03-05", True), ("settlement_date", "2001-03-05", True)])
def test_the_row_seal_boundary_is_exact(fitted, tmp_path, monkeypatch, column, value, refused):
    plant(monkeypatch, column, value)
    faults = [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]
    if refused:
        with pytest.raises(IntegrityError, match="sealed_from"):
            stats(fitted, tmp_path, faults)
        assert not (tmp_path/"study").exists()
    else:
        assert stats(fitted, tmp_path, faults)["faults"][0]["status"] == "ran"


@pytest.mark.parametrize("column", ["quote_date", "settlement_date"])
@pytest.mark.parametrize("value, refused", [("2001-03-01", True), ("2001-02-28", False)])
def test_the_row_seal_compares_date_and_label_end_at_the_boundary(fitted, tmp_path, column, value, refused):
    # A planted date would move the band identity and fail the fit lookup first, so the method is called directly.
    study = ForecastFaultStudy(document(fitted, tmp_path))
    band = pd.DataFrame({"quote_date": ["2001-02-01"], "settlement_date": ["2001-02-02"]})
    band[column] = value
    if refused:
        with pytest.raises(IntegrityError, match="sealed_from"):
            study._seal_rows(band)
    else:
        study._seal_rows(band)


@pytest.mark.parametrize("date_upper, end_upper", [("2001-03-02", "2001-03-01"), ("2001-03-01", "2001-03-02")])
def test_each_experiment_bound_is_checked_against_the_seal(fitted, tmp_path, date_upper, end_upper):
    doc = document(fitted, tmp_path)
    doc["experiment"].update(date_upper_exclusive=date_upper, end_upper_exclusive=end_upper)
    with pytest.raises(IntegrityError, match="sealed_from"):
        ForecastFaultStudy(doc)


def test_changed_inputs_with_no_cdf_change_are_inert(fitted, tmp_path, monkeypatch):
    real, first = TorchCDF.curve, []

    def blind(self, x):                      # a model that answers every call from the first input it saw
        first.append(x)
        return real(self, first[0])
    monkeypatch.setattr(TorchCDF, "curve", blind)
    record = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=3.)])["faults"][0]
    assert record["status"] == "inert" and record["changed_rows"] == 6
    assert record["cdf_shift_max"]["value"] == 0. and record["score_change"]["value"] == 0.


def scoring(monkeypatch, edit):
    """Wrap the grid score owner: ``edit(call_number, scores)`` returns the scores a call gets (1 clean, 2 faulted)."""
    real = GridCDFMethod.grid_score_rows
    calls = []

    def wrapper(cdf, y, z_grid, weights, tail):
        calls.append(1)
        return edit(len(calls), np.array(real(cdf, y, z_grid, weights, tail), float))
    monkeypatch.setattr(GridCDFMethod, "grid_score_rows", staticmethod(wrapper))


@pytest.mark.parametrize("call", [1, 2])
def test_a_row_unscorable_in_either_arm_leaves_both_and_counts_follow_the_scored_rows(fitted, tmp_path, monkeypatch, call):
    ref = Reference(fitted)

    def edit(number, scores):
        if number == call:
            scores[:3] = np.nan           # every row of the first group
        return scores
    scoring(monkeypatch, edit)
    out = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=3.)])
    frame = ref.band.copy()
    frame[SCALED] = frame[SCALED]*3.
    rows = np.arange(3, 6)
    before, after = ref.score(ref.cdf(ref.band)[rows], rows), ref.score(ref.cdf(frame)[rows], rows)
    record = out["faults"][0]
    assert record["score_change"] == pytest.approx({"value": after.mean()/before.mean() - 1., "n": 3, "dates": 3, "groups": 1})
    assert (record["cdf_shift_mean"]["n"], record["cdf_shift_mean"]["groups"]) == (6, 2)


def test_a_clean_mean_that_is_not_positive_gives_no_score_change(fitted, tmp_path, monkeypatch):
    scoring(monkeypatch, lambda number, scores: scores*0. if number == 1 else scores)
    record = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=3.)])["faults"][0]
    assert record["score_change"]["value"] is None and record["score_change"]["n"] == 6


@pytest.mark.parametrize("min_scored, has_value", [(3, True), (4, False)])
def test_min_scored_equal_to_n_gives_a_value_and_one_more_gives_none(fitted, tmp_path, min_scored, has_value):
    record = stats(fitted, tmp_path, [entry("f", "StaleFeatures", columns=STALE, sessions=1)],
                   min_scored=min_scored)["faults"][0]
    assert record["score_change"]["n"] == 3 and (record["score_change"]["value"] is not None) is has_value


def test_retained_equal_to_the_minimum_is_accepted(fitted, tmp_path):
    record = stats(fitted, tmp_path, [entry("f", "StaleFeatures", columns=STALE, sessions=1)],
                   min_retained=0.5)["faults"][0]
    assert record["status"] == "ran" and record["retained_fraction"] == 0.5


def test_a_minimum_of_one_is_a_valid_document(fitted, tmp_path):
    record = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)],
                   min_retained=1)["faults"][0]
    assert record["status"] == "ran"


def test_labels_are_standardized_by_the_clean_reference_even_when_the_fault_scales_it(fitted, tmp_path):
    ref = Reference(fitted)
    frame = ref.band.copy()
    frame["reference_scale"] = frame["reference_scale"]*2.
    _, change = ref.expected(frame, np.arange(6))
    out = stats(fitted, tmp_path, [entry("r", "ScaledFeatures", columns=["reference_scale"], factor=2.)])
    assert out["faults"][0]["score_change"]["value"] == pytest.approx(change)


def fit_root(cfg):
    return Path(cfg["output"])/"pooled"/"base"/cfg["candidates"][0]["name"]/"1"/"pooled"


def resign(store):
    """Rewrite a store's manifest so its (edited) files verify; return the new record."""
    files = {str(p.relative_to(store)): AtomicFitStore.file_hash(p)
             for p in sorted(store.rglob("*")) if p.is_file() and p.name != "complete.json"}
    record = json.loads((store/"complete.json").read_text())
    record["files"] = files
    (store/"complete.json").write_text(json.dumps(record, sort_keys=True))
    return record


@pytest.mark.parametrize("case", ["checkpoint", "groups", "features"])
def test_each_restore_cross_check_refuses_on_its_own(fitted, tmp_path, case):
    from dskit.pipeline.base import value_hash
    root = fit_root(fitted)
    meta = json.loads((root/"fit/metadata.json").read_text())
    if case in ("groups", "features"):
        meta[case] = meta[case][::-1]
        (root/"fit/metadata.json").write_text(json.dumps(meta, sort_keys=True))
    record = resign(root/"fit")
    summary = json.loads((root/"completed/summary.json").read_text())
    summary["checkpoint"] = "0"*64 if case == "checkpoint" else value_hash(record)    # leave exactly one clause false
    (root/"completed/summary.json").write_text(json.dumps(summary, sort_keys=True))
    resign(root/"completed")
    with pytest.raises(IntegrityError, match="fit metadata or identity mismatch"):
        stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert not (tmp_path/"study").exists()


def test_a_missing_or_corrupt_completed_store_refuses(fitted, tmp_path):
    root = fit_root(fitted)
    faults = [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]
    (root/"completed/summary.json").write_text("{}")
    with pytest.raises(IntegrityError, match="mismatch"):
        stats(fitted, tmp_path, faults)
    shutil.rmtree(root/"completed")
    with pytest.raises(IntegrityError, match="never fits"):
        stats(fitted, tmp_path, faults)
    assert not (tmp_path/"study").exists()


def test_a_tampered_statistics_file_refuses_on_a_repeat(fitted, tmp_path):
    faults = [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]
    stats(fitted, tmp_path, faults)
    path = tmp_path/"study/statistics.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(IntegrityError, match="mismatch"):
        stats(fitted, tmp_path, faults)


def test_a_fault_that_repeats_an_identity_aborts(fitted, tmp_path):
    with pytest.raises(IntegrityError, match="repeated an identity"):
        stats(fitted, tmp_path, [entry("d", me(DupFault), columns=["rv_5"])])
    assert not (tmp_path/"study").exists()


def test_unhashable_values_are_listed_never_raised(fitted, tmp_path):
    bad = document(fitted, tmp_path, [{"name": ["x"], "class": NAME + "MissingFeatures", "params": {"columns": ["rv_5"]}}],
                   arm=[1], candidate=[2], fold=[3], min_scored=0)
    with pytest.raises(ConfigError) as caught:
        ForecastFaultStudy(bad)
    text = "\n".join(caught.value.errors)
    for needle in ("fold", "arm", "candidate", "min_scored", "name"):
        assert needle in text, needle


@pytest.mark.parametrize("fold", [True, 1.0])
def test_a_fold_must_equal_a_plan_fold_with_the_same_type(fitted, tmp_path, fold):
    with pytest.raises(ConfigError, match="fold"):
        ForecastFaultStudy(document(fitted, tmp_path, fold=fold))


@pytest.mark.parametrize("where", ["equal", "inside"])
def test_the_output_may_not_be_the_experiment_output_or_inside_it(fitted, tmp_path, where):
    output = fitted["output"] if where == "equal" else fitted["output"] + "/study"
    with pytest.raises(ConfigError, match="output"):
        ForecastFaultStudy(document(fitted, tmp_path, output=output))


# ---------------------------------------------------------------- mutation sweep: every guard and aggregation pinned

import fcntl


def test_params_must_be_a_mapping():
    assert "mapping" in MissingFeatures.validate_params("x")[0]
    assert "mapping" in StaleFeatures.validate_params(None)[0]


def test_an_identity_that_lacks_the_date_still_protects_the_date_column():
    with pytest.raises(ValueError, match="quote_date"):
        MissingFeatures(columns=["quote_date"]).apply(toy(), ["symbol", "expiry"], DATE)


def test_stale_refuses_a_frame_that_repeats_an_identity():
    frame = toy()
    with pytest.raises(ValueError):
        StaleFeatures(columns=["f1"], sessions=1).apply(pd.concat([frame, frame.iloc[[1]]]), IDENTITY, DATE)


def test_stale_without_a_declared_column_changes_only_what_it_declares():
    out = StaleFeatures(columns=["f2"], sessions=1).apply(toy(), IDENTITY, DATE)
    assert list(out["f1"]) == [2., 3.] and list(out["f2"]) == [10., 20.]


@pytest.mark.parametrize("document_", ["x", None, 5, [], {"experiment": 5}, {"experiment": None}])
def test_a_document_that_is_not_a_mapping_with_an_experiment_refuses(document_):
    with pytest.raises(ConfigError, match="experiment mapping"):
        ForecastFaultStudy(document_)


def test_the_study_keeps_its_own_copy_of_the_document(fitted, tmp_path):
    doc = document(fitted, tmp_path)
    study = ForecastFaultStudy(doc)
    doc["grid"]["tail"] = -1
    doc["faults"].append("garbage")
    assert study.document["grid"]["tail"] == TAIL and len(study.document["faults"]) == 1


@pytest.mark.parametrize("path", [5, None, "nocolon", "a:b:c", ":Name", "json:dumps", "pathlib:Path",
                                  "no_such_module_zz:X", NAME + "Nope", NAME + "FeatureFault"])
def test_a_class_path_that_is_not_a_fault_class_is_listed(fitted, tmp_path, path):
    bad = {"name": "x", "class": path, "params": {"columns": ["rv_5"]}}
    with pytest.raises(ConfigError, match="FeatureFault subclass"):
        ForecastFaultStudy(document(fitted, tmp_path, [bad]))


@pytest.mark.parametrize("name", ["", "a/b", "a\\b", ".", "..", 5, None])
def test_a_fault_name_must_be_a_safe_string(fitted, tmp_path, name):
    with pytest.raises(ConfigError, match=r"name must be a safe"):
        ForecastFaultStudy(document(fitted, tmp_path, [entry("x", "MissingFeatures", columns=["rv_5"]) | {"name": name}]))


def test_the_faults_list_and_each_entry_have_an_exact_shape(fitted, tmp_path):
    for faults in ("x", None, {}, ()):
        doc = document(fitted, tmp_path)
        doc["faults"] = faults
        with pytest.raises(ConfigError, match="faults must be a non-empty list"):
            ForecastFaultStudy(doc)
    good = entry("x", "MissingFeatures", columns=["rv_5"])
    for bad in (dict(good, extra=1), {k: v for k, v in good.items() if k != "params"}, "x", None, 5):
        with pytest.raises(ConfigError, match=r"faults\[0\] keys must be exactly"):
            ForecastFaultStudy(document(fitted, tmp_path, [bad]))
    with pytest.raises(ConfigError, match="mapping"):
        ForecastFaultStudy(document(fitted, tmp_path, [dict(good, params="x")]))


def test_identity_keys_may_not_be_fault_columns_even_when_the_identity_lacks_the_date(fitted, tmp_path):
    doc = document(fitted, tmp_path, [entry("x", "MissingFeatures", columns=["quote_date"])])
    with pytest.raises(ConfigError, match="identity or date columns"):
        ForecastFaultStudy(doc)


BAD_SETTINGS = [("sealed_from", "20010301"), ("sealed_from", "2001-3-1"), ("sealed_from", 5), ("sealed_from", None),
                ("sealed_from", "soon"), ("min_retained", 0), ("min_retained", -.1), ("min_retained", 1.5),
                ("min_retained", True), ("min_retained", float("nan")), ("min_retained", "x"),
                ("min_scored", 0), ("min_scored", -1), ("min_scored", True), ("min_scored", 1.5), ("min_scored", "2"),
                ("output", ""), ("output", 5), ("output", None)]


@pytest.mark.parametrize("key, value", BAD_SETTINGS, ids=[f"{k}={v!r}" for k, v in BAD_SETTINGS])
def test_each_scalar_setting_refuses_its_bad_values(fitted, tmp_path, key, value):
    with pytest.raises(ConfigError, match=key):
        ForecastFaultStudy(document(fitted, tmp_path, **{key: value}))


@pytest.mark.parametrize("key, value", [("min_retained", 1), ("min_retained", 1e-9), ("min_scored", 1),
                                        ("sealed_from", "2001-12-31")])
def test_each_scalar_setting_accepts_its_edge_values(fitted, tmp_path, key, value):
    ForecastFaultStudy(document(fitted, tmp_path, **{key: value}))


BAD_GRIDS = [None, "x", [], {"z_grid": Z, "weights": W}, {"z_grid": Z, "weights": W, "tail": 4., "extra": 1},
             {"z_grid": tuple(Z), "weights": W, "tail": 4.}, {"z_grid": [0.], "weights": [1.], "tail": 4.},
             {"z_grid": [0., 0., 1., 2.], "weights": W, "tail": 4.}, {"z_grid": [2., 1., 0., -1.], "weights": W, "tail": 4.},
             {"z_grid": [0., True, 2., 3.], "weights": W, "tail": 4.}, {"z_grid": [0., float("nan"), 2., 3.], "weights": W, "tail": 4.},
             {"z_grid": Z, "weights": W[:-1], "tail": 4.}, {"z_grid": Z, "weights": tuple(W), "tail": 4.},
             {"z_grid": Z, "weights": [1., -1., 1., 1.], "tail": 4.}, {"z_grid": Z, "weights": [0., 0., 0., 0.], "tail": 4.},
             {"z_grid": Z, "weights": [1., "x", 1., 1.], "tail": 4.}, {"z_grid": Z, "weights": W, "tail": 0},
             {"z_grid": Z, "weights": W, "tail": -1.}, {"z_grid": Z, "weights": W, "tail": True},
             {"z_grid": Z, "weights": W, "tail": "x"}, {"z_grid": Z, "weights": W, "tail": float("inf")}]


@pytest.mark.parametrize("grid", BAD_GRIDS, ids=range(len(BAD_GRIDS)))
def test_each_grid_rule_refuses(fitted, tmp_path, grid):
    with pytest.raises(ConfigError, match="grid"):
        ForecastFaultStudy(document(fitted, tmp_path, grid=grid))


@pytest.mark.parametrize("grid", [{"z_grid": [0., 1.], "weights": [0., 1.], "tail": 1e-9},
                                  {"z_grid": [-1, 0, 1], "weights": [1, 0, 0], "tail": 2}])
def test_edge_grids_are_valid(fitted, tmp_path, grid):
    ForecastFaultStudy(document(fitted, tmp_path, grid=grid))


def test_a_symlink_into_the_experiment_output_is_still_inside_it(fitted, tmp_path):
    link = tmp_path/"link"
    link.symlink_to(fitted["output"], target_is_directory=True)
    with pytest.raises(ConfigError, match="output"):
        ForecastFaultStudy(document(fitted, tmp_path, output=str(link/"study")))
    doc = document(fitted, tmp_path, output=fitted["output"] + "/study")
    doc["experiment"]["output"] = str(link)
    with pytest.raises(ConfigError, match="output"):
        ForecastFaultStudy(doc)


def test_a_missing_fit_store_refuses_even_when_completed_exists(fitted, tmp_path):
    shutil.rmtree(fit_root(fitted)/"fit")
    with pytest.raises(IntegrityError, match="never fits"):
        stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])


def test_a_corrupt_fit_store_refuses(fitted, tmp_path):
    (fit_root(fitted)/"fit/metadata.json").write_text("{}")
    with pytest.raises(IntegrityError, match="mismatch"):
        stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])


@pytest.mark.parametrize("what", ["groups", "empty"])
def test_no_admitted_group_or_no_evaluation_row_refuses(fitted, tmp_path, monkeypatch, what):
    real = CDFExperiment._bands

    def patched(self, *args):
        bands, groups, admission = real(self, *args)
        if what == "groups":
            return bands, [], admission
        return (bands[0], bands[1], bands[2].iloc[0:0]), groups, admission
    monkeypatch.setattr(CDFExperiment, "_bands", patched)
    with pytest.raises(IntegrityError, match="no admitted groups or no evaluation rows"):
        stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert not (tmp_path/"study").exists()


def test_provenance_is_checked_again_when_the_study_runs(fitted, tmp_path):
    study = ForecastFaultStudy(document(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]))
    study.config["panel_sha256"] = "0"*64
    with pytest.raises(IntegrityError, match="panel hash mismatch"):
        study.assess()
    assert not (tmp_path/"study").exists()


def test_the_receipt_records_identities_and_counts_exactly(fitted, tmp_path):
    from dskit.pipeline.base import value_hash
    ref = Reference(fitted)
    faults = [entry("a", "ScaledFeatures", columns=SCALED, factor=2.), entry("b", "MissingFeatures", columns=MISSING)]
    study_id = ForecastFaultStudy(document(fitted, tmp_path, faults)).study_identity
    out = stats(fitted, tmp_path, faults)
    store = tmp_path/"study"
    receipt = json.loads((store/"receipt.json").read_text())
    checkpoint = value_hash(json.loads((fit_root(fitted)/"fit/complete.json").read_text()))
    assert receipt == {"identity": {"study": study_id, "checkpoint": checkpoint},
                       "experiment": CDFExperiment(fitted).identity, "groups": ["AAA", "BBB"], "faults": ["a", "b"],
                       "evaluation_identity": value_hash(ref.band[IDENTITY].values.tolist()), "rows": 6,
                       "scores_written": False}
    assert json.loads((store/"complete.json").read_text())["identity"] == value_hash(receipt["identity"])
    assert json.loads((store/"statistics.json").read_text()) == out


def test_the_output_may_be_nested_under_directories_that_do_not_exist_yet(fitted, tmp_path):
    out = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)],
                output=str(tmp_path/"deep"/"er"/"study"))
    assert (tmp_path/"deep"/"er"/"study"/"statistics.json").exists() and out["faults"][0]["status"] == "ran"


def test_a_study_already_running_on_the_output_is_refused(fitted, tmp_path):
    study = ForecastFaultStudy(document(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]))
    with (tmp_path/"study.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(BlockingIOError):
            study.assess()
    assert not (tmp_path/"study").exists()


def test_cdf_values_are_clipped_to_the_unit_interval_before_they_are_judged(fitted, tmp_path, monkeypatch):
    class Wide:
        def __init__(self, n):
            self.n = n

        def cdf(self, z):
            return np.tile([-.5, .2, .7, 1.5], (self.n, 1))
    monkeypatch.setattr(TorchCDF, "curve", lambda self, x: Wide(len(x)))
    record = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])["faults"][0]
    assert record["status"] == "inert" and record["cdf_shift_max"]["value"] == 0.


def test_refused_records_carry_exactly_their_known_fields(fitted, tmp_path):
    none = {"value": None, "n": 0, "dates": 0, "groups": 0}
    out = stats(fitted, tmp_path, [entry("inf", me(InfFault), columns=["rv_5"]),
                                   entry("none", "StaleFeatures", columns=STALE, sessions=3),
                                   entry("few", "StaleFeatures", columns=STALE, sessions=1)], min_retained=0.9)
    inf, nothing, few = out["faults"]
    base = {"status": "refused", "rows": 6, "changed_rows": None, "cdf_shift_mean": none, "cdf_shift_max": none,
            "score_change": none}
    assert inf == dict(base, name="inf", reason="infinite_input", paired_rows=None, retained_fraction=None)
    assert nothing == dict(base, name="none", reason="no_paired_rows", paired_rows=0, retained_fraction=0.)
    assert few == dict(base, name="few", reason="retained_below_minimum", paired_rows=3, retained_fraction=0.5)


def test_an_overflow_to_float32_after_pairing_keeps_the_paired_counts(fitted, tmp_path):
    record = stats(fitted, tmp_path, [entry("huge", me(HugeFault), columns=["rv_5"])])["faults"][0]
    assert (record["reason"], record["rows"], record["paired_rows"], record["retained_fraction"]) == (
        "infinite_input", 6, 6, 1.0)
    assert record["changed_rows"] is None


def test_the_refusal_codes_are_the_closed_set():
    assert REFUSAL_CODES == ("infinite_input", "invalid_forecast", "no_paired_rows", "retained_below_minimum")


def test_a_ran_record_has_exactly_its_fields_and_the_clean_extent_is_counted(fitted, tmp_path):
    out = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)])
    assert set(out) == {"clean", "faults"} and out["clean"] == {"rows": 6, "dates": 3, "groups": 2}
    assert set(out["faults"][0]) == {"name", "status", "rows", "paired_rows", "retained_fraction", "changed_rows",
                                     "cdf_shift_mean", "cdf_shift_max", "score_change"}
    for key in ("cdf_shift_mean", "cdf_shift_max", "score_change"):
        assert set(out["faults"][0][key]) == {"value", "n", "dates", "groups"}
        assert out["faults"][0][key]["n"] == 6 and out["faults"][0][key]["dates"] == 3
        assert out["faults"][0][key]["groups"] == 2


# ---------------------------------------------------------------- mutation sweep, second pass

import warnings


class ReverseScale(FeatureFault):
    """A test fault that scales a column and returns the rows in reverse order (pairing is by identity)."""

    def apply(self, frame, identity, date):
        out = frame.copy()
        out[self.params["columns"]] = out[self.params["columns"]]*3.
        return out.iloc[::-1]


class PartialFault(FeatureFault):
    """A test fault that drops the first row and changes only the last."""

    def apply(self, frame, identity, date):
        out = frame.iloc[1:].copy()
        out.iloc[-1, out.columns.get_loc(self.params["columns"][0])] *= 3.
        return out


def test_the_unknown_param_message_lists_the_allowed_names():
    with pytest.raises(ConfigError) as caught:
        StaleFeatures(columns=["f1"], sessions=1, colour="red")
    assert "allowed: ['columns', 'sessions']" in "\n".join(caught.value.errors)


def test_stale_refuses_a_missing_or_identity_column():
    with pytest.raises(ValueError, match="nope"):
        StaleFeatures(columns=["nope"], sessions=1).apply(toy(), IDENTITY, DATE)
    with pytest.raises(ValueError, match="expiry"):
        StaleFeatures(columns=["expiry"], sessions=1).apply(toy(), IDENTITY, DATE)


@pytest.mark.parametrize("arm", ["nope", [1], None, 5])
def test_an_invalid_arm_does_not_add_feature_problems(fitted, tmp_path, arm):
    bad = document(fitted, tmp_path, [entry("x", "MissingFeatures", columns=["rv_5"])], arm=arm)
    with pytest.raises(ConfigError) as caught:
        ForecastFaultStudy(bad)
    assert caught.value.errors == ["arm is not an arm of the experiment"]


@pytest.mark.parametrize("column", ["symbol", "expiry", "quote_date"])
def test_each_identity_column_is_refused_as_a_fault_column(fitted, tmp_path, column):
    with pytest.raises(ConfigError, match="identity or date columns"):
        ForecastFaultStudy(document(fitted, tmp_path, [entry("x", "MissingFeatures", columns=[column])]))


def test_the_date_column_is_refused_even_when_the_identity_lacks_it(fitted, tmp_path):
    doc = document(fitted, tmp_path, [entry("x", "MissingFeatures", columns=["quote_date"])])
    doc["experiment"]["study"]["identity"] = ["symbol", "expiry"]
    with pytest.raises(ConfigError, match="identity or date columns"):
        ForecastFaultStudy(doc)


def test_pairing_is_by_identity_not_by_row_position(fitted, tmp_path):
    plain = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=3.)])["faults"][0]
    other = stats(fitted, tmp_path, [entry("r", me(ReverseScale), columns=SCALED)], output=str(tmp_path/"other"))["faults"][0]
    assert other["status"] == "ran" and other["paired_rows"] == 6
    for key in ("cdf_shift_mean", "cdf_shift_max", "score_change"):
        assert other[key]["value"] == pytest.approx(plain[key]["value"])
    assert other["changed_rows"] == plain["changed_rows"]


def test_changed_rows_counts_only_the_paired_rows_that_differ(fitted, tmp_path):
    record = stats(fitted, tmp_path, [entry("p", me(PartialFault), columns=SCALED)])["faults"][0]
    assert (record["paired_rows"], record["changed_rows"]) == (5, 1)


def test_the_maximum_shift_carries_its_own_counts(fitted, tmp_path):
    record = stats(fitted, tmp_path, [entry("f", "StaleFeatures", columns=STALE, sessions=1)])["faults"][0]
    assert (record["cdf_shift_max"]["n"], record["cdf_shift_max"]["dates"], record["cdf_shift_max"]["groups"]) == (3, 2, 2)


def test_no_scorable_row_gives_no_value_and_raises_no_warning(fitted, tmp_path, monkeypatch):
    scoring(monkeypatch, lambda number, scores: scores*np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        record = stats(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=3.)])["faults"][0]
    assert record["score_change"] == {"value": None, "n": 0, "dates": 0, "groups": 0}


def test_the_declared_fold_reaches_the_band_builder(fitted, tmp_path, monkeypatch):
    class Stop(Exception):
        pass
    seen = []

    def spy(self, frame, calendar, fold, arm):
        seen.append(fold)
        raise Stop
    monkeypatch.setattr(CDFExperiment, "_bands", spy)
    study = ForecastFaultStudy(document(fitted, tmp_path))
    study.document["fold"] = 7
    with pytest.raises(Stop):
        study.assess()
    assert seen == [7]


def test_a_shared_lock_also_blocks_a_second_study(fitted, tmp_path):
    study = ForecastFaultStudy(document(fitted, tmp_path, [entry("s", "ScaledFeatures", columns=SCALED, factor=2.)]))
    with (tmp_path/"study.lock").open("w") as held:
        fcntl.flock(held, fcntl.LOCK_SH)
        with pytest.raises(BlockingIOError):
            study.assess()

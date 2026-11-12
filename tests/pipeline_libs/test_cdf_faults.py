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
    pd.testing.assert_frame_equal(a.sort_index(), b.sort_index())


# ---------------------------------------------------------------- purity

def test_import_pulls_no_heavy_library_and_no_evaluation_package():
    code = ("import sys, dskit.pipeline.libs.cdf_faults as m; "
            "bad = [n for n in ('numpy','pandas','torch','sklearn','dskit.evaluation') if n in sys.modules]; "
            "print(bad); sys.exit(1 if bad else 0)")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

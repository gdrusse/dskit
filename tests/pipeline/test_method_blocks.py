"""MethodBlocks (ADR-0313 I1): one attach owner, default-deny, identity when absent, one seal owner."""

import ast
import pathlib
import subprocess
import sys

import pytest

from dskit.pipeline import method_blocks as mb
from dskit.pipeline.base import ConfigError
from dskit.pipeline.hedge_overlay import HedgeOverlay, NullHedgeOverlay
from dskit.pipeline.method_blocks import (
    BlockSpec,
    DelegatedBlockSpec,
    MethodBlocks,
    NullBlock,
)

CAL = {"source": "edge_maps", "state": "expanding_fill", "edge_scale": "notional", "regressors": 1,
       "line": "bucket", "form": "slope", "variant": "point", "warmup": "no_trade", "min_warm_weeks": 8}
LIM = {"beta_delta_fraction": 0.5, "vega_fraction_per_pt": 0.02, "dte_edges": [8, 22],
       "beta_lookback": 60, "scope": "account"}
HEDGE = {"kind": "fixed_delta_forward", "mode": "program", "ratio": 1.0,
         "delta_source": "black76_chain_forward", "iv_source": "vendor", "rate": 0.0,
         "margin_fraction": 0.15, "round_units": True, "condition": None,
         "small_delta": 0.0, "hedge_cost_source": "placeholder"}
CLOCKS = {"tail_end_before": "2021-07-01"}
CTX = {"locked_method_ids": ["a1"], "clocks": CLOCKS}


def owner():
    return MethodBlocks.standard(hedge=HedgeOverlay)


def codes(exc):
    return [e.split(":")[0] for e in exc.value.errors]


def full_hedge():
    import tests.pipeline.test_hedge_overlay as th
    return dict(th.BLOCK)


# ---- parity when absent ----
def test_absent_blocks_are_identity():
    m = {"id": "new", "rho": 0.1}
    before = dict(m)
    o = owner()
    assert o.validate(m) is None
    assert m == before
    res = o.resolve(m)
    assert set(res) == {"hedge", "calibration", "exposure_limits"}
    assert all(isinstance(v, NullBlock) or isinstance(v, NullHedgeOverlay) for v in res.values())
    assert not any(getattr(v, "present", False) for v in res.values())
    assert all(v.record() == {} for v in res.values())
    assert o.present(m) == ()


def test_absent_needs_no_context_and_ignores_locked_id():
    o = owner()
    assert o.validate({"id": "a1"}) is None  # a locked id with no block is untouched
    assert o.validate({"id": "a1"}, {"locked_method_ids": ["a1"]}) is None


def test_unregistered_owner_ignores_nothing():
    empty = MethodBlocks()
    assert empty.resolve({"id": "x"}) == {}
    assert empty.validate({"id": "x"}) is None


# ---- refusals ----
def test_unknown_block_key_refused():
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibrate": {}}, {"method_keys": ["id", "rho"]})
    assert codes(e) == ["block_unknown"]
    assert owner().validate({"id": "n", "rho": 0.1}, {"method_keys": ["id", "rho"]}) is None


def test_unknown_inner_key_refused_and_missing_key():
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": {**CAL, "extra": 1}}, CTX)
    assert "block_unknown_key" in codes(e)
    bad = dict(CAL)
    del bad["form"]
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": bad}, CTX)
    assert "block_missing_key" in codes(e)


@pytest.mark.parametrize("key,value", [("form", "bogus"), ("regressors", 3), ("regressors", True),
                                        ("variant", "x"), ("min_warm_weeks", 0), ("min_warm_weeks", 1.5),
                                        ("source", "disk"), ("warmup", "raw"), ("line", "z")])
def test_calibration_value_rejections(key, value):
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": {**CAL, key: value}}, CTX)
    assert "block_param_value" in codes(e)


def test_block_must_be_mapping():
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "exposure_limits": [1]}, CTX)
    assert "block_type" in codes(e)


@pytest.mark.parametrize("key,value", [("dte_edges", [22, 8]), ("dte_edges", []), ("beta_lookback", 0),
                                        ("scope", "book"), ("beta_delta_fraction", -1), ("vega_fraction_per_pt", float("nan"))])
def test_limits_value_rejections(key, value):
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "exposure_limits": {**LIM, key: value}}, CTX)
    assert "block_param_value" in codes(e)


def test_identity_control_pairing():
    ok = {**CAL, "form": "identity", "control": True}
    assert owner().validate({"id": "n", "calibration": ok}, CTX) is None
    for bad in ({**CAL, "form": "identity"}, {**CAL, "control": True}, {**CAL, "form": "identity", "control": False}):
        with pytest.raises(ConfigError) as e:
            owner().validate({"id": "n", "calibration": bad}, CTX)
        assert "calibration_identity_not_control" in codes(e)


def test_valid_blocks_pass():
    m = {"id": "n", "calibration": CAL, "exposure_limits": LIM}
    assert owner().validate(m, CTX) is None
    res = owner().resolve(m)
    assert res["calibration"].present and res["calibration"].params == CAL
    assert not res["hedge"].params


def test_locked_formulation_refused_and_ids_missing():
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "a1", "calibration": CAL}, CTX)
    assert "block_on_locked_formulation" in codes(e)
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": CAL}, {"clocks": CLOCKS})
    assert "locked_ids_missing" in codes(e)
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": CAL}, {"clocks": CLOCKS, "locked_method_ids": "a1"})
    assert "locked_ids_missing" in codes(e)


def test_seal_refusals():
    m = {"id": "n", "exposure_limits": LIM}
    for clocks in (None, {}, {"tail_end_before": "2021-07-02"}, {"tail_end_before": "bad"}):
        with pytest.raises(ConfigError) as e:
            owner().validate(m, {"locked_method_ids": [], "clocks": clocks})
        assert "integration_seal" in codes(e)
    assert owner().seal({"tail_end_before": "2021-07-01"}) == []
    assert owner().seal({"tail_end_before": "2021-06-30"}) == []


def test_cross_checks():
    o = owner()
    cases = [
        ({"calibration": CAL, "kelly": {}}, "calibration_with_kelly"),
        ({"calibration": CAL, "sizer": {}}, "calibration_with_sizer"),
        ({"hedge": full_hedge(), "sizer": {}}, "hedge_with_sizer"),
        ({"exposure_limits": LIM, "kelly": {}}, "exposure_limits_with_kelly"),
        ({"calibration": {**CAL, "variant": "lcb"}, "rho": 0.1}, "calibration_double_discount"),
        ({"calibration": {**CAL, "variant": "lcb"}, "risk_charge": {}}, "calibration_double_discount"),
        ({"calibration": CAL, "hedge": full_hedge()}, "hedge_needs_direction_map"),
    ]
    for extra, code in cases:
        with pytest.raises(ConfigError) as e:
            o.validate({"id": "n", **extra}, CTX)
        assert code in codes(e), code


def test_cross_checks_allowed_combinations():
    o = owner()
    o.validate({"id": "n", "calibration": {**CAL, "variant": "lcb"}, "rho": 0.0}, CTX)
    o.validate({"id": "n", "calibration": {**CAL, "regressors": 2}, "hedge": full_hedge()}, CTX)
    replay = {**full_hedge(), "mode": "replay"}
    o.validate({"id": "n", "calibration": CAL, "hedge": replay}, CTX)


# ---- hook dispatch ----
def test_hook_dispatch_and_hedge_delegation():
    o = owner()
    res = o.resolve({"id": "n", "hedge": full_hedge()})
    assert type(res["hedge"]).__name__ == "FixedDeltaForwardHedge"
    assert isinstance(o.resolve({"id": "n"})["hedge"], NullHedgeOverlay)
    with pytest.raises(ConfigError) as e:
        o.validate({"id": "n", "hedge": {**full_hedge(), "ratio": 2}}, CTX)
    assert "hedge_ratio_range" in codes(e)  # the hedge owner's own code, not re-implemented


def test_custom_kind_registers_through_hook_without_branching():
    class Probe(BlockSpec):
        KEY = "probe"
        _PARAMS = ("n",)

        @classmethod
        def check_values(cls, problems, block):
            if block["n"] != 1:
                problems.append(mb.coded(mb.PARAM_VALUE, "n must be 1"))

    o = MethodBlocks()
    o.register(Probe)
    assert o.keys() == ("probe",)
    assert o.validate({"id": "x", "probe": {"n": 1}}, {"locked_method_ids": [], "clocks": CLOCKS}) is None
    with pytest.raises(ConfigError):
        o.validate({"id": "x", "probe": {"n": 2}}, {"locked_method_ids": [], "clocks": CLOCKS})
    with pytest.raises(ValueError):
        o.register(Probe)  # one owner per key
    assert isinstance(DelegatedBlockSpec, type)


def test_params_tuples():
    assert mb.CalibrationBlock._PARAMS == ("source", "state", "edge_scale", "regressors", "line", "form",
                                           "variant", "warmup", "min_warm_weeks", "control")
    assert mb.ExposureLimitsBlock._PARAMS == ("beta_delta_fraction", "vega_fraction_per_pt", "dte_edges",
                                               "beta_lookback", "scope")


# ---- seal single owner ----
def test_seal_single_owner():
    from dskit.pipeline import method_blocks
    from children.index_options.index_options import mio_greeks  # noqa: F401
    assert method_blocks.DEVELOPMENT_SEAL == "2021-07-01"
    assert mio_greeks.DEVELOPMENT_SEAL is method_blocks.DEVELOPMENT_SEAL


def test_no_other_definition_of_seal():
    root = pathlib.Path(mb.__file__).parents[2]
    owners = []
    paths = [p for top in ("dskit", "children") for p in root.glob(f"{top}/**/*.py")
             if "tests" not in p.relative_to(root).parts]
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "DEVELOPMENT_SEAL" for t in node.targets):
                owners.append(path.name)
    assert owners == ["method_blocks.py"], (
        f"DEVELOPMENT_SEAL is also defined in {owners}: replace the local definition with "
        "`from dskit.pipeline.method_blocks import DEVELOPMENT_SEAL`")


@pytest.mark.parametrize("extra", [
    {"hedge": None}, {"hedge": None, "calibration": CAL}, {"calibration": None},
    {"hedge": None, "calibration": None}, {"exposure_limits": []}, {"hedge": "x", "calibration": CAL}])
def test_every_present_block_must_be_a_mapping(extra):
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", **extra}, CTX)
    assert "block_type" in codes(e)


@pytest.mark.parametrize("value", [1.0, 2.0, True, "1", None])
def test_regressors_exact_int(value):
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": {**CAL, "regressors": value}}, CTX)
    assert "block_param_value" in codes(e)


@pytest.mark.parametrize("rho", ["0.5", True, None, float("nan")])
def test_lcb_requires_real_number_rho(rho):
    with pytest.raises(ConfigError) as e:
        owner().validate({"id": "n", "calibration": {**CAL, "variant": "lcb"}, "rho": rho}, CTX)
    assert "block_param_value" in codes(e)


def test_all_and_purity():
    assert sorted(mb.__all__) == sorted(["MethodBlocks", "BlockSpec", "DelegatedBlockSpec", "NullBlock",
                                         "CalibrationBlock", "ExposureLimitsBlock", "DEVELOPMENT_SEAL", "REFUSALS"])
    for name in mb.__all__:
        assert hasattr(mb, name)
    code = ("import sys\nfor m in ('numpy','pandas','scipy','pyomo','sklearn'): sys.modules[m]=None\n"
            "import dskit.pipeline.method_blocks")
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0


def test_a_derived_document_may_not_change_the_locked_ids_or_reuse_one():
    owner = mb.MethodBlocks
    nine = ["a", "b"]
    assert owner.lock_problems(nine, nine, ["new"], ["a"]) == []
    assert owner.lock_problems(nine, ["b", "a"], ["new"], ["a"])[0].startswith("locked_method_ids_changed")
    assert owner.lock_problems(nine, ["a"], ["new"])[0].startswith("locked_method_ids_changed")
    assert owner.lock_problems(nine, None, ["new"])[0].startswith("locked_method_ids_changed")
    assert owner.lock_problems(nine, nine, ["a"])[0].startswith("locked_method_id_collision")
    assert owner.lock_problems(nine, nine, ["old"], ["old"])[0].startswith("locked_method_id_collision")
    for alias in (" A ", "A", "a "):
        assert owner.lock_problems(nine, nine, [alias])[0].startswith("locked_method_id_collision")
    assert owner.lock_problems(nine, nine, [" OLD "], ["old"])[0].startswith("locked_method_id_collision")


def test_a_date_past_the_development_seal_is_a_violation():
    assert mb.MethodBlocks.seal_violations([mb.DEVELOPMENT_SEAL, "2019-01-01"]) == []
    assert mb.MethodBlocks.seal_violations(["2030-01-01"])[0].startswith("development_seal_violation")
    assert {"locked_method_ids_changed", "locked_method_id_collision", "development_seal_violation"} <= set(mb.REFUSALS)

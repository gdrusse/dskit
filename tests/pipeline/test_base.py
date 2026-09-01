"""Config grammar — tagged families, shape-vs-resolve, error accumulation,
canonical round-trip, hash identity, unknown-key rejection, immutability."""

import dataclasses
import json

import pytest

from dskit.pipeline import (
    OPTIMIZER_KINDS,
    SPLIT_KINDS,
    ConfigError,
    DataConfig,
    HPOConfig,
    ModelConfig,
    OptimizationConfig,
    PipelineConfig,
    RandomSplitConfig,
    StatTestConfig,
    TimeSplitConfig,
    ValidationConfig,
    config_hash,
    register_optimizer_kind,
    split_from_obj,
)


class _Rec:
    """Duck-typed record for split_of: asof_ms + cluster."""

    def __init__(self, asof_ms=0, cluster="c"):
        self.asof_ms = asof_ms
        self.cluster = cluster


def _pipeline(**overrides):
    base = dict(
        name="t",
        data=DataConfig(venue="markets", data_dir="/tmp/x", instruments=("A",)),
        splits=TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3),
        model=ModelConfig(name="m", model_dir="/tmp/m"),
    )
    base.update(overrides)
    return PipelineConfig(**base)


@pytest.fixture
def scratch_kind():
    """Register a throwaway optimizer kind; always unregister."""
    name = "scratch-kind"

    def validator(params):
        errs = []
        if "x" not in params:
            errs.append("optimization.params.x is required")
        elif not isinstance(params["x"], int):
            errs.append("optimization.params.x must be an int")
        return errs

    register_optimizer_kind(name, validator)
    try:
        yield name
    finally:
        OPTIMIZER_KINDS.pop(name, None)


class TestShapeValidation:
    def test_valid_configs_construct(self):
        cfg = _pipeline()
        assert cfg.hpo is None and cfg.optimization is None  # optional children

    def test_invalid_cannot_exist_no_separate_validate_call(self):
        with pytest.raises(ConfigError):
            DataConfig(venue="", data_dir="/tmp/x")

    def test_errors_accumulate_with_paths(self):
        with pytest.raises(ConfigError) as exc:
            DataConfig(venue="", data_dir=7, instruments=("", 3))
        msg = str(exc.value)
        assert "3 problems" in msg
        assert "data.venue" in msg and "data.data_dir" in msg
        assert "data.instruments" in msg
        assert len(exc.value.errors) == 3

    def test_composition_type_checks(self):
        with pytest.raises(ConfigError, match="splits"):
            _pipeline(splits="not a split")
        with pytest.raises(ConfigError, match="data is required"):
            _pipeline(data=None)

    def test_frozen_and_slots(self):
        cfg = _pipeline()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.name = "other"
        with pytest.raises((AttributeError, TypeError)):
            cfg.novel_attr = 1


class TestSplitFamily:
    def test_time_split_leak_impossible(self):
        with pytest.raises(ConfigError, match="strictly ascending"):
            TimeSplitConfig(train_end_ms=5, val_end_ms=5, test_end_ms=9)
        with pytest.raises(ConfigError, match="strictly ascending"):
            TimeSplitConfig(train_end_ms=9, val_end_ms=5, test_end_ms=20)

    def test_time_split_assignment(self):
        s = TimeSplitConfig(train_end_ms=10, val_end_ms=20, test_end_ms=30)
        assert s.split_of(_Rec(asof_ms=10)) == "train"
        assert s.split_of(_Rec(asof_ms=11)) == "val"
        assert s.split_of(_Rec(asof_ms=30)) == "test"
        assert s.split_of(_Rec(asof_ms=31)) is None

    def test_train_start_bounds_train_from_the_left(self):
        s = TimeSplitConfig(
            train_start_ms=5, train_end_ms=10, val_end_ms=20, test_end_ms=30
        )
        assert s.split_of(_Rec(asof_ms=4)) is None
        assert s.split_of(_Rec(asof_ms=5)) == "train"
        assert s.split_of(_Rec(asof_ms=10)) == "train"
        assert "train_start_ms" not in TimeSplitConfig(
            train_end_ms=10, val_end_ms=20, test_end_ms=30
        ).to_obj()

    def test_time_split_cal_band_assignment(self):
        # ADR-0034: the fourth name exists only when the band is declared.
        s = TimeSplitConfig(
            train_end_ms=10, cal_start_ms=17, val_end_ms=20, test_end_ms=30
        )
        assert s.split_of(_Rec(asof_ms=11)) == "val"
        assert s.split_of(_Rec(asof_ms=16)) == "val"
        assert s.split_of(_Rec(asof_ms=17)) == "cal"
        assert s.split_of(_Rec(asof_ms=20)) == "cal"
        assert s.split_of(_Rec(asof_ms=21)) == "test"

    def test_time_split_from_obj_still_default_denies(self):
        # the allowlist widened for cal_start_ms; unknown keys still refuse
        with pytest.raises(ConfigError, match="cal_end_ms"):
            TimeSplitConfig.from_obj(
                {
                    "kind": "time",
                    "train_end_ms": 1,
                    "val_end_ms": 2,
                    "test_end_ms": 3,
                    "cal_end_ms": 2,
                }
            )

    def test_random_split_needs_test_remainder(self):
        with pytest.raises(ConfigError, match="positive test remainder"):
            RandomSplitConfig(train_frac=0.7, val_frac=0.3)

    def test_random_split_frac_domains(self):
        with pytest.raises(ConfigError, match="train_frac"):
            RandomSplitConfig(train_frac=0.0, val_frac=0.2)
        with pytest.raises(ConfigError, match="val_frac"):
            RandomSplitConfig(train_frac=0.5, val_frac=1.5)

    def test_random_split_is_deterministic_and_cluster_level(self):
        s = RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=7)
        r1 = _Rec(asof_ms=1, cluster="EV-1")
        r2 = _Rec(asof_ms=999, cluster="EV-1")  # same cluster, later record
        assert s.split_of(r1) == s.split_of(r2)  # cluster decides, not time
        assert s.split_of(r1) == s.split_of(r1)

    def test_random_split_seed_moves_assignments(self):
        clusters = [f"EV-{i}" for i in range(200)]
        a = RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=0)
        b = RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=1)
        assignments_a = [a.split_of(_Rec(cluster=c)) for c in clusters]
        assignments_b = [b.split_of(_Rec(cluster=c)) for c in clusters]
        assert assignments_a != assignments_b
        # roughly the configured proportions (deterministic, loose bounds)
        assert 80 <= assignments_a.count("train") <= 160
        assert assignments_a.count("test") >= 10

    def test_split_from_obj_dispatch(self):
        t = TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3)
        r = RandomSplitConfig(train_frac=0.5, val_frac=0.2, seed=3)
        assert split_from_obj(t.to_obj()) == t
        assert split_from_obj(r.to_obj()) == r

    def test_split_from_obj_unknown_or_missing_kind(self):
        with pytest.raises(ConfigError, match="known kinds"):
            split_from_obj({"kind": "walk-forward"})
        with pytest.raises(ConfigError, match="'kind'"):
            split_from_obj({"train_end_ms": 1})

    def test_kind_tags_are_pinned(self):
        with pytest.raises(ConfigError, match="kind"):
            TimeSplitConfig(train_end_ms=1, val_end_ms=2, test_end_ms=3, kind="random")
        assert set(SPLIT_KINDS) == {"time", "random"}


class TestOptimizerFamily:
    def test_kind_is_required(self):
        with pytest.raises(ConfigError, match="optimization.kind"):
            OptimizationConfig(kind="")

    def test_unregistered_kind_passes_shape_only(self):
        cfg = OptimizationConfig(kind="mean-variance", params={"risk_aversion": 3})
        assert cfg.params["risk_aversion"] == 3

    def test_registered_kind_validates_params(self, scratch_kind):
        with pytest.raises(ConfigError, match="params.x is required"):
            OptimizationConfig(kind=scratch_kind)
        with pytest.raises(ConfigError, match="params.x must be an int"):
            OptimizationConfig(kind=scratch_kind, params={"x": "no"})
        ok = OptimizationConfig(kind=scratch_kind, params={"x": 5})
        assert ok.params == {"x": 5}

    def test_duplicate_kind_registration_raises(self, scratch_kind):
        with pytest.raises(ValueError, match="already registered"):
            register_optimizer_kind(scratch_kind, lambda p: [])

    def test_bad_registration_inputs(self):
        with pytest.raises(ValueError, match="non-empty string"):
            register_optimizer_kind("", lambda p: [])
        with pytest.raises(ValueError, match="callable"):
            register_optimizer_kind("k2", "not callable")


class TestRoundTripAndHash:
    def test_full_round_trip_preserves_identity(self):
        cfg = _pipeline(
            hpo=HPOConfig(n_trials=8, seeds=(0, 1), space={"lr": [1e-4, 1e-3]}),
            optimization=OptimizationConfig(kind="k", params={"a": 1}),
            validation=ValidationConfig(metric="brier"),
            stat_test=StatTestConfig(alpha=0.1),
        )
        again = PipelineConfig.from_obj(json.loads(json.dumps(cfg.to_obj())))
        assert again == cfg and again.hash == cfg.hash

    def test_random_split_round_trips_through_pipeline(self):
        cfg = _pipeline(splits=RandomSplitConfig(train_frac=0.6, val_frac=0.2, seed=2))
        assert PipelineConfig.from_obj(cfg.to_obj()) == cfg

    def test_notes_never_change_identity(self):
        a = _pipeline()
        b = _pipeline(notes="a comment")
        c = _pipeline(
            data=DataConfig(
                venue="markets",
                data_dir="/tmp/x",
                instruments=("A",),
                notes="data comment",
            )
        )
        assert a.hash == b.hash == c.hash

    def test_everything_else_is_hash_material(self):
        base = _pipeline()
        assert base.hash != _pipeline(name="t2").hash
        assert (
            base.hash
            != _pipeline(splits=RandomSplitConfig(train_frac=0.5, val_frac=0.2)).hash
        )
        assert base.hash != _pipeline(optimization=OptimizationConfig(kind="k")).hash
        k1 = _pipeline(optimization=OptimizationConfig(kind="k", params={"a": 1}))
        k2 = _pipeline(optimization=OptimizationConfig(kind="k", params={"a": 2}))
        assert k1.hash != k2.hash

    def test_unknown_keys_rejected_loudly(self):
        with pytest.raises(ConfigError, match="unknown key"):
            PipelineConfig.from_obj({**_pipeline().to_obj(), "training": {}})
        with pytest.raises(ConfigError, match="unknown key"):
            DataConfig.from_obj({"venue": "v", "data_dir": "/d", "data_dri": "/d"})
        with pytest.raises(ConfigError, match="required key"):
            PipelineConfig.from_obj({"name": "x"})

    def test_nan_in_open_dict_refused(self):
        cfg = _pipeline(
            optimization=OptimizationConfig(kind="k", params={"bad": float("nan")})
        )
        with pytest.raises(ConfigError, match="serializable"):
            config_hash(cfg)

    def test_open_dicts_are_copied_not_shared(self):
        params = {"a": [1, 2]}
        cfg = ModelConfig(name="m", model_dir="/tmp/m", params=params)
        obj = cfg.to_obj()
        obj["params"]["a"].append(3)
        assert cfg.params["a"] == [1, 2]


class TestResolveLayer:
    def test_data_dir_env_check(self, tmp_path):
        ok = DataConfig(venue="v", data_dir=str(tmp_path))
        assert ok.resolve() == str(tmp_path)
        missing = DataConfig(venue="v", data_dir=str(tmp_path / "nope"))
        with pytest.raises(FileNotFoundError, match="does not exist"):
            missing.resolve()

    def test_model_dir_created_only_when_owned(self, tmp_path):
        cfg = ModelConfig(name="m", model_dir=str(tmp_path / "models"))
        with pytest.raises(FileNotFoundError, match="create=True"):
            cfg.resolve()
        assert cfg.resolve(create=True) == str(tmp_path / "models")

"""base.py: hash parity with the pipeline (ADR-0010), errors, atomic writes."""

import json
import os

import pytest

from dskit.assets.base import (
    AssetError,
    _check_dict,
    _check_str,
    _check_unknown,
    _raise_if,
    atomic_write_json,
    canonical_hash,
    utc_now,
)
from dskit.pipeline.base import config_hash


class _Shim:
    """Wraps a plain obj in the pipeline's config protocol, so the SAME
    object can be hashed by both implementations."""

    def __init__(self, obj):
        self._obj = obj

    def to_obj(self):
        return self._obj


def test_hash_parity_with_pipeline():
    # The ADR-0010 contract: byte-for-byte the pipeline's recipe.
    obj = {
        "kind": "dataset",
        "payload": {"name": "prices", "notes": "stripped", "n": 1.5,
                    "nested": {"notes": "also stripped", "keep": [1, 2]}},
        "refs": {"source": "ab" * 32},
        "notes": "top-level stripped too",
    }
    assert canonical_hash(obj) == config_hash(_Shim(obj), exclude=())


def test_hash_ignores_key_order_and_notes():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1, "notes": "doc"}
    assert canonical_hash(a) == canonical_hash(b)


def test_hash_refuses_nan_and_non_json():
    with pytest.raises(AssetError):
        canonical_hash({"x": float("nan")})
    with pytest.raises(AssetError):
        canonical_hash({"x": object()})


def test_asset_error_reports_every_problem():
    errors = []
    _check_str(errors, "a", 1)
    _check_dict(errors, "b", [])
    _check_unknown(errors, {"bad": 1}, ("good",), "c")
    with pytest.raises(AssetError) as exc:
        _raise_if(errors)
    assert len(exc.value.errors) == 3
    assert "3 problems" in str(exc.value)


def test_checkers_pass_valid_input_silently():
    errors = []
    _check_str(errors, "a", "ok")
    _check_str(errors, "b", "", non_empty=False)
    _check_dict(errors, "c", {"k": 1})
    _check_unknown(errors, {"good": 1}, ("good",))
    _raise_if(errors)  # no raise


def test_atomic_write_round_trip_and_no_temp_debris(tmp_path):
    path = tmp_path / "r.json"
    atomic_write_json(str(path), {"v": 1, "nested": [1, 2]})
    assert json.loads(path.read_text()) == {"v": 1, "nested": [1, 2]}
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]


def test_atomic_write_refuses_unserializable_leaving_nothing(tmp_path):
    path = tmp_path / "r.json"
    with pytest.raises(AssetError):
        atomic_write_json(str(path), {"x": object()})
    assert not list(tmp_path.iterdir())


def test_utc_now_shape():
    stamp = utc_now()
    assert stamp.endswith("+00:00") and "T" in stamp

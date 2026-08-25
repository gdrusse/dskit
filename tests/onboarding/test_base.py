"""base.py: durability discipline, digests, bitemporal parsing, modes."""

import json
import os

import pytest

from dskit.assets.base import canonical_hash as assets_hash
from dskit.onboarding.base import (
    AssetError,
    MODES,
    _check_iso,
    _check_mode,
    _check_segment,
    canonical_hash,
    durable_write_bytes,
    durable_write_json,
    file_digest,
    parse_utc,
)


def test_hash_is_the_assets_hash():
    # ADR-0013: reuse, not a copy — the very same function object.
    assert canonical_hash is assets_hash


def test_durable_write_json_round_trip_no_debris(tmp_path):
    path = tmp_path / "m.json"
    durable_write_json(str(path), {"a": 1, "nested": [1, 2]})
    assert json.loads(path.read_text()) == {"a": 1, "nested": [1, 2]}
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp-")]


def test_durable_write_json_refuses_unserializable_leaving_nothing(tmp_path):
    with pytest.raises(AssetError):
        durable_write_json(str(tmp_path / "m.json"), {"x": object()})
    assert not list(tmp_path.iterdir())


def test_durable_write_bytes_exact_bytes_and_overwrite(tmp_path):
    path = tmp_path / "b.bin"
    durable_write_bytes(str(path), b"\x00\x01")
    durable_write_bytes(str(path), b"\x02")  # atomic replace, not append
    assert path.read_bytes() == b"\x02"


def test_durable_write_bytes_refuses_non_bytes(tmp_path):
    with pytest.raises(AssetError):
        durable_write_bytes(str(tmp_path / "b.bin"), "text")


def test_file_digest_matches_hashlib(tmp_path):
    import hashlib

    path = tmp_path / "f"
    path.write_bytes(b"payload bytes")
    assert file_digest(str(path)) == hashlib.sha256(b"payload bytes").hexdigest()


def test_file_digest_missing_file():
    with pytest.raises(AssetError):
        file_digest("/nonexistent/nope")


def test_parse_utc_date_and_datetime_comparable():
    # Naive dates are UTC — the bitemporal comparison must never crash.
    assert parse_utc("2026-01-02") < parse_utc("2026-01-02T00:00:01+00:00")


def test_parse_utc_normalizes_offsets():
    assert parse_utc("2026-01-02T01:00:00+01:00") == parse_utc("2026-01-02T00:00:00")


def test_parse_utc_refuses_garbage():
    for bad in ("not-a-date", "", None, 20260102):
        with pytest.raises(AssetError):
            parse_utc(bad)


def test_modes_vocabulary_closed():
    assert MODES == ("backfill", "live")
    errors = []
    _check_mode(errors, "incremental")
    assert errors and "backfill" in errors[0]


def test_segment_checker_refuses_separators_and_case():
    for bad in ("Bad", "a/b", "", "a b", ".hidden", "prices\n"):
        errors = []
        _check_segment(errors, "x", bad)
        assert errors, bad
    errors = []
    _check_segment(errors, "x", "vendor-2_ok")
    assert not errors


def test_iso_checker_optional_vs_required():
    errors = []
    _check_iso(errors, "d", "", required=False)  # empty ok when optional
    _check_iso(errors, "d", "2026-01-01")
    assert not errors
    _check_iso(errors, "d", "", required=True)
    _check_iso(errors, "d", "nope")
    assert len(errors) == 2

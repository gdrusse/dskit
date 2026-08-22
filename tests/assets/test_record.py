"""record.py: content identity, payload governance, tamper evidence."""

import pytest

from dskit.assets import AssetError, AssetRecord, check_payload, default_model

VID = "ab" * 32  # a well-formed (if arbitrary) version_id


# -- normal ----------------------------------------------------------------


def test_identity_is_content_only():
    bare = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={})
    provenanced = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={},
                              registered_at="2026-08-22T00:00:00+00:00",
                              origin="cli", notes="doc")
    assert bare.version_id() == provenanced.version_id()
    assert len(bare.version_id()) == 64


def test_check_payload_accepts_a_valid_default_model_record():
    m = default_model()
    check_payload(m.kinds["feature"], {"name": "mom_20d"}, {"entity": VID})
    check_payload(m.kinds["entity"], {"name": "AAPL", "description": "equity"}, {})


def test_file_round_trip_preserves_identity_and_provenance():
    r = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={},
                    registered_at="t1", origin="cli")
    back = AssetRecord.from_obj(r.to_obj())
    assert back == r and back.version_id() == r.version_id()


# -- edge ------------------------------------------------------------------


def test_bool_is_not_number():
    from dskit.assets import FieldSpec, KindSpec
    spec = KindSpec(fields={"name": FieldSpec(type="string", required=True),
                            "rows": FieldSpec(type="number")})
    with pytest.raises(AssetError, match="must be number, got True"):
        check_payload(spec, {"name": "v1", "rows": True}, {})


def test_errors_accumulate():
    m = default_model()
    with pytest.raises(AssetError) as exc:
        check_payload(m.kinds["feature"], {"window": 20}, {})
    # undeclared field + missing name + missing entity ref, in one report
    assert len(exc.value.errors) == 3


# -- failure ---------------------------------------------------------------


def test_reserved_notes_name_refused():
    m = default_model()
    with pytest.raises(AssetError, match="reserved"):
        check_payload(m.kinds["entity"], {"name": "x", "notes": "d"}, {})


def test_alias_passed_as_ref_refused():
    m = default_model()
    with pytest.raises(AssetError, match="sha256 hex"):
        check_payload(m.kinds["feature"], {"name": "x"}, {"entity": "AAPL"})


def test_tampered_file_refused():
    r = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={})
    obj = r.to_obj()
    obj["payload"]["name"] = "edited"
    with pytest.raises(AssetError, match="does not match"):
        AssetRecord.from_obj(obj)


def test_unknown_file_keys_refused():
    r = AssetRecord(kind="entity", payload={"name": "AAPL"}, refs={})
    with pytest.raises(AssetError, match="unknown key"):
        AssetRecord.from_obj({**r.to_obj(), "extra": 1})

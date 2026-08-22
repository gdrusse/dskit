"""model.py: round-trip stability, default-deny parsing, governance rules."""

import pytest

from dskit.assets import (
    AssetError,
    AssetModel,
    FieldSpec,
    KindSpec,
    RefSpec,
    load_model,
    model_hash,
)


def minimal_model():
    return AssetModel(
        name="minimal",
        kinds={
            "source": KindSpec(fields={"name": FieldSpec(type="string", required=True)}),
            "dataset": KindSpec(
                fields={"name": FieldSpec(type="string", required=True),
                        "rows": FieldSpec(type="number")},
                refs={"source": RefSpec(kind="source", required=True)},
                states=("draft", "certified"), initial="draft",
                transitions={"draft": ("certified",)},
            ),
        },
    )


# -- normal ----------------------------------------------------------------


def test_round_trip_is_hash_identical():
    m = minimal_model()
    assert model_hash(AssetModel.from_obj(m.to_obj())) == model_hash(m)


def test_notes_never_change_identity():
    m = minimal_model()
    noted = AssetModel.from_obj({**m.to_obj(), "notes": "documentation"})
    assert model_hash(noted) == model_hash(m)


def test_load_model_reads_a_file(tmp_path):
    import json
    path = tmp_path / "model.json"
    path.write_text(json.dumps(minimal_model().to_obj()))
    assert model_hash(load_model(str(path))) == model_hash(minimal_model())


def test_record_only_kind_has_no_lifecycle():
    ks = KindSpec(fields={"name": FieldSpec(type="string", required=True)})
    assert ks.states == () and "lifecycle" not in ks.to_obj()


# -- edge ------------------------------------------------------------------


def test_errors_accumulate_across_kinds_with_paths():
    with pytest.raises(AssetError) as exc:
        AssetModel.from_obj({"name": "bad", "kinds": {
            "a": {"fields": {"name": {"type": "str", "required": True}}},
            "b": {"fields": {"name": {"type": "string"}}, "typo": 1},
        }})
    messages = exc.value.errors
    assert any("kinds.a: fields.name" in m for m in messages)
    assert any("unknown key" in m for m in messages)


def test_transitions_from_obj_lists_become_tuples():
    m = AssetModel.from_obj(minimal_model().to_obj())
    assert m.kinds["dataset"].transitions == {"draft": ("certified",)}


# -- failure ---------------------------------------------------------------


def test_kind_without_required_string_name_refused():
    with pytest.raises(AssetError, match="ADR-0009"):
        KindSpec(fields={"label": FieldSpec(type="string", required=True)})


def test_ref_to_undeclared_kind_refused():
    with pytest.raises(AssetError, match="undeclared kind"):
        AssetModel(name="x", kinds={"a": KindSpec(
            fields={"name": FieldSpec(type="string", required=True)},
            refs={"e": RefSpec(kind="entity")})})


def test_bad_field_type_refused():
    with pytest.raises(AssetError, match="type must be one of"):
        FieldSpec(type="integer")


def test_bad_lifecycle_refused():
    fields = {"name": FieldSpec(type="string", required=True)}
    with pytest.raises(AssetError, match="initial"):
        KindSpec(fields=fields, states=("a", "b"), initial="c")
    with pytest.raises(AssetError, match="declared state"):
        KindSpec(fields=fields, states=("a", "b"), initial="a",
                 transitions={"z": ("a",)})
    with pytest.raises(AssetError, match="record-only"):
        KindSpec(fields=fields, initial="a")


def test_empty_model_refused():
    with pytest.raises(AssetError, match="at least one"):
        AssetModel(name="x", kinds={})


def test_load_model_refuses_missing_and_invalid_files(tmp_path):
    with pytest.raises(AssetError, match="cannot read"):
        load_model(str(tmp_path / "absent.json"))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(AssetError, match="not valid JSON"):
        load_model(str(bad))

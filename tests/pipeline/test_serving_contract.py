"""ADR-0091: ``ServingContract`` and the one entry class that supplies it.

The contract is four fields and deliberately no fifth: the required
universe is the SERVE DOCUMENT's business (§5.2/D3), and a pure,
document-blind classmethod could only have inferred it from the dedupe
``key_fields`` — which may contain time. So the tests here prove three
separate things:

* the dataclass itself — frozen, typed, non-empty entity keys, round
  trips through ``to_obj``/``from_obj``;
* ``ObservationRows.serving_contract`` returns exactly the contract
  SEAM-DESIGN §4 specifies, is pure, and refuses the two cases that
  would leave a served run without a watermark;
* the ``since_ms`` window the serving override addresses: a training
  document that will be served DECLARES it (as ``null``), because
  ``apply_param_override`` may only address params that already exist.
"""

import builtins
import dataclasses
import os
import socket

import pytest

from dskit.pipeline import node as node_module
from dskit.pipeline.base import ConfigError
from dskit.pipeline.libs.observations import DEFAULT_TS_OUT, ObservationRows

#: The entry params the serving tests are written against.
OBS_PARAMS = {
    "root": "./ob",
    "source": "alpaca",
    "stream": "bars",
    "key_fields": ["symbol", "ts"],
    "ts_field": "ts",
    "since_ms": None,
}


def contract_class():
    """The dataclass under test, or an AttributeError naming what is missing."""
    return node_module.ServingContract


def a_contract(**over):
    """A valid contract, one keyword away from any refusal under test."""
    fields = {
        "source_binding": {
            "kind": "onboarding-stream",
            "root": "./ob",
            "source": "alpaca",
            "stream": "bars",
        },
        "entity_key_fields": ("symbol",),
        "event_time_field": "asof_ms",
        "digest_recipe": {
            "kind": "stream-digest",
            "key_fields": ["symbol", "ts"],
            "ts_field": "ts",
            "ts_unit": "iso",
        },
    }
    fields.update(over)
    return contract_class()(**fields)


def boom(*args, **kwargs):
    """Stand-in for any I/O primitive: being called at all is the defect."""
    raise AssertionError(f"I/O attempted: {args!r} {kwargs!r}")


# ---------------------------------------------------------------------------
# The dataclass
# ---------------------------------------------------------------------------


class TestServingContract:
    def test_it_declares_exactly_the_four_fields(self):
        names = [f.name for f in dataclasses.fields(contract_class())]
        assert names == [
            "source_binding",
            "entity_key_fields",
            "event_time_field",
            "digest_recipe",
        ]

    def test_it_is_frozen(self):
        contract = a_contract()
        with pytest.raises(dataclasses.FrozenInstanceError):
            contract.event_time_field = "other"

    def test_no_universe_field_exists_anywhere_on_it(self):
        names = {f.name for f in dataclasses.fields(contract_class())}
        assert not names & {"universe", "required_keys", "required_universe"}
        rendered = a_contract().to_obj()
        assert not set(rendered) & {"universe", "required_keys", "required_universe"}

    def test_to_obj_round_trips_through_from_obj(self):
        contract = a_contract()
        assert contract_class().from_obj(contract.to_obj()) == contract

    def test_to_obj_renders_the_four_fields(self):
        rendered = a_contract().to_obj()
        assert rendered["source_binding"]["stream"] == "bars"
        assert list(rendered["entity_key_fields"]) == ["symbol"]
        assert rendered["event_time_field"] == "asof_ms"
        assert rendered["digest_recipe"]["kind"] == "stream-digest"

    def test_an_empty_entity_key_projection_refuses(self):
        with pytest.raises(ConfigError):
            a_contract(entity_key_fields=())

    def test_a_non_dict_source_binding_refuses(self):
        with pytest.raises(ConfigError):
            a_contract(source_binding="onboarding-stream")

    def test_an_absent_event_time_field_refuses(self):
        with pytest.raises(ConfigError):
            a_contract(event_time_field="")


# ---------------------------------------------------------------------------
# ObservationRows — the one entry class
# ---------------------------------------------------------------------------


class TestObservationRowsContract:
    def test_the_entry_class_is_the_mutable_read(self):
        assert ObservationRows.serving_effect(dict(OBS_PARAMS), {}) == "entry_read"

    def test_every_field_of_the_contract_by_hand(self):
        contract = ObservationRows.serving_contract(dict(OBS_PARAMS), {})
        assert contract.source_binding == {
            "kind": "onboarding-stream",
            "root": "./ob",
            "source": "alpaca",
            "stream": "bars",
        }
        assert contract.entity_key_fields == ("symbol",)
        assert contract.event_time_field == DEFAULT_TS_OUT == "asof_ms"
        assert contract.digest_recipe == {
            "kind": "stream-digest",
            "key_fields": ["symbol", "ts"],
            "ts_field": "ts",
            "ts_unit": "iso",
        }

    def test_the_declared_ts_out_and_ts_unit_are_honoured(self):
        params = dict(OBS_PARAMS, ts_out="bar_ms", ts_unit="ms")
        contract = ObservationRows.serving_contract(params, {})
        assert contract.event_time_field == "bar_ms"
        assert contract.digest_recipe["ts_unit"] == "ms"
        assert contract.digest_recipe["ts_field"] == "ts"

    def test_the_time_field_never_leaks_into_the_entity_projection(self):
        params = dict(OBS_PARAMS, key_fields=["symbol", "venue", "ts"])
        contract = ObservationRows.serving_contract(params, {})
        assert contract.entity_key_fields == ("symbol", "venue")
        # The dedupe key is still recorded verbatim in the digest recipe —
        # that is a different question from entity identity.
        assert contract.digest_recipe["key_fields"] == ["symbol", "venue", "ts"]

    def test_it_returns_the_declared_contract_type(self):
        contract = ObservationRows.serving_contract(dict(OBS_PARAMS), {})
        assert isinstance(contract, contract_class())

    def test_it_carries_no_universe_of_its_own(self):
        contract = ObservationRows.serving_contract(dict(OBS_PARAMS), {})
        rendered = contract.to_obj()
        assert not set(rendered) & {"universe", "required_keys", "required_universe"}
        assert "required_keys" not in contract.digest_recipe

    def test_an_absent_ts_field_refuses_because_a_watermark_needs_one(self):
        params = dict(OBS_PARAMS)
        del params["ts_field"]
        with pytest.raises(ValueError):
            ObservationRows.serving_contract(params, {})

    def test_a_null_ts_field_refuses_too(self):
        with pytest.raises(ValueError):
            ObservationRows.serving_contract(dict(OBS_PARAMS, ts_field=None), {})

    def test_an_empty_entity_projection_refuses(self):
        params = dict(OBS_PARAMS, key_fields=["ts"], ts_field="ts")
        with pytest.raises(ValueError):
            ObservationRows.serving_contract(params, {})

    def test_the_contract_is_pure(self, monkeypatch):
        monkeypatch.setattr(builtins, "open", boom)
        monkeypatch.setattr(os, "listdir", boom)
        monkeypatch.setattr(os, "scandir", boom)
        monkeypatch.setattr(os, "stat", boom)
        monkeypatch.setattr(socket, "socket", boom)
        error = None
        try:
            contract = ObservationRows.serving_contract(dict(OBS_PARAMS), {})
        except Exception as exc:  # reported, never swallowed
            contract, error = None, repr(exc)
        monkeypatch.undo()
        assert error is None
        assert contract.entity_key_fields == ("symbol",)


# ---------------------------------------------------------------------------
# The window a served document must DECLARE (SEAM-DESIGN §4)
# ---------------------------------------------------------------------------


class TestDeclaredWindow:
    def test_since_ms_declared_as_null_validates(self):
        assert ObservationRows.validate_params(dict(OBS_PARAMS)) == []

    def test_a_serving_override_needs_the_key_to_exist_already(self):
        from dskit.pipeline.driver import apply_param_override

        params = dict(OBS_PARAMS)
        del params["since_ms"]
        with pytest.raises(ValueError) as exc:
            apply_param_override(params, "bars", ("since_ms",), 1_700_000_000_000)
        assert "is not an existing param" in str(exc.value)
        assert "never create them" in str(exc.value)
        assert "since_ms" not in params

    def test_a_declared_null_window_accepts_the_serving_override(self):
        from dskit.pipeline.driver import apply_param_override

        params = dict(OBS_PARAMS)
        assert params["since_ms"] is None
        apply_param_override(params, "bars", ("since_ms",), 1_700_000_000_000)
        assert params["since_ms"] == 1_700_000_000_000
        assert ObservationRows.validate_params(params) == []

"""connector.py: the envelope, default-deny config, and resolution."""

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding import (
    MAX_BACKOFF_S,
    MESSAGE_TYPES,
    PROTOCOL,
    Connector,
    check_config,
    check_message,
    connector,
    resolve_connector,
)
from dskit.onboarding.libs import kalshi, polymarket, predexon, restapi, schwab

from .fake_connector import FakeConnector, file_message, record


# -- check_message ----------------------------------------------------------


def test_valid_record_message():
    assert check_message(record("s", "2026-01-01", {"v": 1})) == "RECORD"


def test_record_kind_defaults_and_vocabulary():
    assert check_message(record("s", "2026-01-01", kind="forecast")) == "RECORD"
    with pytest.raises(AssetError, match="RECORD.kind"):
        check_message(record("s", "2026-01-01", kind="guess"))


def test_unknown_type_is_skippable_not_an_error():
    assert check_message({"protocol": PROTOCOL, "type": "TRACE", "x": 1}) is None


def test_wrong_protocol_refused():
    with pytest.raises(AssetError, match="protocol"):
        check_message({"protocol": 2, "type": "RECORD"})


def test_known_type_with_unknown_key_refused():
    msg = record("s", "2026-01-01")
    msg["extra"] = True
    with pytest.raises(AssetError, match="unknown key"):
        check_message(msg)


def test_each_shape_checked():
    with pytest.raises(AssetError, match="STATE.state"):
        check_message({"protocol": PROTOCOL, "type": "STATE", "state": "not-a-dict"})
    with pytest.raises(AssetError, match="SCHEMA.stream"):
        check_message({"protocol": PROTOCOL, "type": "SCHEMA",
                       "stream": "", "schema": {}})
    with pytest.raises(AssetError, match="ERROR.message"):
        check_message({"protocol": PROTOCOL, "type": "ERROR", "message": ""})
    with pytest.raises(AssetError, match="effective_date"):
        check_message({"protocol": PROTOCOL, "type": "RECORD", "stream": "s",
                       "effective_date": "yesterday", "data": {}})


def test_all_problems_reported_at_once():
    with pytest.raises(AssetError) as exc:
        check_message({"protocol": 9, "type": "RECORD", "stream": "",
                       "effective_date": "bad", "data": []})
    assert len(exc.value.errors) >= 3


# -- FILE messages (ADR-0082) --------------------------------------------------


def test_file_is_a_known_message_type():
    assert "FILE" in MESSAGE_TYPES
    assert check_message(file_message("weights", "sub/model.bin", "/tmp/x")) == "FILE"


@pytest.mark.parametrize(
    "relpath",
    ["", "/abs/path", "../escape", "a/../b", "./here", "a//b", "a\\b",
     "trailing/", "nul\x00byte", ".", "C:/x", "a/b:c", "a:b"],
)
def test_file_relpath_must_be_a_safe_relative_posix_path(relpath):
    with pytest.raises(AssetError, match="relpath"):
        check_message(file_message("weights", relpath, "/tmp/x"))


@pytest.mark.parametrize("path", ["", 3, None])
def test_file_path_must_be_a_non_empty_string(path):
    with pytest.raises(AssetError, match="path"):
        check_message(file_message("weights", "model.bin", path))


def test_file_message_refuses_unknown_keys_and_a_missing_stream():
    msg = file_message("weights", "model.bin", "/tmp/x")
    msg["size"] = 3
    with pytest.raises(AssetError, match="size"):
        check_message(msg)
    with pytest.raises(AssetError, match="stream"):
        check_message(file_message("", "model.bin", "/tmp/x"))


# -- check_config -----------------------------------------------------------


def test_config_default_deny_and_secrets():
    conn = FakeConnector()
    check_config(conn, {"token": "MY_ENV_VAR", "flavor": "x"})  # ok
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {"typo": 1})
    with pytest.raises(AssetError, match="secret"):
        check_config(conn, {"token": 123})  # secrets are env-var NAMES


def test_config_required_knobs():
    class Needy(FakeConnector):
        def spec(self):
            return {"params": {"path": {"required": True}}}

    with pytest.raises(AssetError, match="required knob"):
        check_config(Needy(), {})


def test_malformed_spec_is_the_connectors_fault_but_loud():
    class Sloppy(FakeConnector):
        def spec(self):
            return {"params": {"k": {"requierd": True}}}  # typo'd knob key

    with pytest.raises(AssetError, match="unknown key"):
        check_config(Sloppy(), {})


def test_storage_block_is_platform_reserved():
    # ADR-0036: allowed without a spec declaration, shape-checked here.
    conn = FakeConnector()
    check_config(conn, {"storage": {"payload_codec": "gzip"}})  # ok
    check_config(conn, {"storage": {}})  # empty block is fine
    with pytest.raises(AssetError, match="storage.payload_codec"):
        check_config(conn, {"storage": {"payload_codec": "zstd"}})
    with pytest.raises(AssetError, match="unknown key"):
        check_config(conn, {"storage": {"codec": "gzip"}})


def test_a_spec_may_not_declare_the_reserved_keys():
    class Squatter(FakeConnector):
        def spec(self):
            return {"params": {"storage": {"notes": "mine now"}}}

    with pytest.raises(AssetError, match="reserved platform key"):
        check_config(Squatter(), {})

    class NoteSquatter(FakeConnector):
        def spec(self):
            return {"params": {"notes": {}}}

    with pytest.raises(AssetError, match="reserved platform key"):
        check_config(NoteSquatter(), {})


# -- resolve_connector ------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        "alpaca",
        "alpaca_quotes",
        "huggingface",
        "kalshi",
        "localfiles",
        "localtables",
        "polymarket",
        "predexon",
        "restapi",
        "schwab",
    ],
)
def test_resolve_registered_kind(kind):
    cls = resolve_connector(kind)
    assert issubclass(cls, Connector)


def test_resolve_class_reference():
    assert resolve_connector(
        "tests.onboarding.fake_connector:FakeConnector") is FakeConnector


def test_resolve_unknown_kind_names_the_registry():
    with pytest.raises(AssetError, match="registered"):
        resolve_connector("no-such-kind")


def test_resolve_refuses_non_connector():
    with pytest.raises(AssetError, match="four-verb"):
        resolve_connector("tests.onboarding.fake_connector:NotAConnector")


def test_resolve_refuses_missing_attribute():
    with pytest.raises(AssetError, match="no attribute"):
        resolve_connector("tests.onboarding.fake_connector:Ghost")


# -- MAX_BACKOFF_S ----------------------------------------------------------


def test_max_backoff_is_one_name_across_every_pack():
    # `is`, not `==`: a pack that restated 60.0 locally would pass equality
    # and drift silently; identity proves each one BINDS the contract's name.
    assert (
        kalshi.MAX_BACKOFF_S is predexon.MAX_BACKOFF_S
        is polymarket.MAX_BACKOFF_S is restapi.MAX_BACKOFF_S
        is schwab.MAX_BACKOFF_S is connector.MAX_BACKOFF_S is MAX_BACKOFF_S
    )
    # The documented ceiling, restated on purpose — an assertion sourced from
    # its subject would assert nothing.
    assert MAX_BACKOFF_S == 60.0

"""connector.py: the envelope, default-deny config, resolution, retry policy."""

import ast
import pathlib

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
from dskit.onboarding import libs
from dskit.onboarding.libs import (
    alpaca_quotes,
    kalshi,
    polymarket,
    predexon,
    restapi,
    schwab,
)

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


# -- the one retry policy (ADR-0101) ----------------------------------------
#
# Six packs each hand-rolled the same wait: an attempt counter, a doubling
# delay, the shared ceiling, and — in three of them — a numeric Retry-After.
# `connector.backoff` and `connector.retry_after` are now the one home; the
# scan below is what makes a seventh copy impossible.

#: Every pack that retries. `localfiles`/`localtables`/`huggingface`/`alpaca`
#: move no wait of their own and are scanned but not expected to import.
RETRYING_PACKS = (alpaca_quotes, kalshi, polymarket, predexon, restapi, schwab)


def _two(node):
    """Report whether an AST node is the literal 2."""
    return (
        isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and isinstance(node.value, (int, float))
        and node.value == 2
    )


def _named(node):
    """The bare name an AST node refers to, or ''."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _policy_offences(path):
    """Every way one pack still spells the retry policy instead of importing it."""
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.lstrip("_") in ("backoff", "retry_after"):
                found.append(f"line {node.lineno}: defines {node.name}()")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            if _two(node.left):
                found.append(f"line {node.lineno}: spells a doubling (2 ** n)")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
            if _two(node.left) or _two(node.right):
                found.append(f"line {node.lineno}: spells a doubling (x * 2)")
        elif isinstance(node, ast.AugAssign) and isinstance(node.op, ast.Mult):
            if _two(node.value):
                found.append(f"line {node.lineno}: spells a doubling (x *= 2)")
        elif isinstance(node, ast.Call) and _named(node.func) == "min":
            if any(_named(arg) == "MAX_BACKOFF_S" for arg in node.args):
                found.append(f"line {node.lineno}: clamps with the ceiling itself")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.strip().lower() == "retry-after":
                found.append(f"line {node.lineno}: names the Retry-After header")
    return found


def test_no_pack_spells_its_own_backoff_or_retry_after():
    # The drift pin. The six copies had already diverged on jitter, on which
    # outcomes retry, and on a NaN Retry-After; a source scan is what stops a
    # seventh from being written beside the one owner.
    offences = {}
    for path in sorted(pathlib.Path(libs.__file__).parent.glob("*.py")):
        problems = _policy_offences(path)
        if problems:
            offences[path.name] = problems
    assert not offences, (
        "a connector pack may not compute its own wait — import `backoff` and "
        f"`retry_after` from dskit.onboarding.connector: {offences}"
    )


def test_the_policy_owner_is_the_only_module_that_spells_it():
    # Deliberate independent restatement: the scan must find the expression
    # SOMEWHERE, or it is passing because it looks for the wrong shape.
    assert _policy_offences(pathlib.Path(connector.__file__))


def test_every_retrying_pack_binds_the_one_owner():
    # `is`, not a name check: a pack that redefined `backoff` locally would
    # still have the attribute — identity proves it BINDS the contract's.
    for pack in RETRYING_PACKS:
        assert pack.backoff is connector.backoff, pack.__name__
    for pack in (kalshi, polymarket, predexon):
        assert pack.retry_after is connector.retry_after, pack.__name__


def test_the_backoff_base_has_one_name():
    # restapi keeps `_BACKOFF` as its documented test seam, but the VALUE is
    # the contract's — an alias, never a second 0.5.
    assert restapi._BACKOFF is connector.DEFAULT_BACKOFF_S
    # Restated on purpose: an assertion sourced from its subject asserts nothing.
    assert connector.DEFAULT_BACKOFF_S == 0.5


def test_backoff_doubles_from_the_base_and_stops_at_the_ceiling():
    assert [connector.backoff(n) for n in range(1, 9)] == [
        0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0, MAX_BACKOFF_S
    ]
    assert connector.backoff(1, 2.0) == 2.0
    assert connector.backoff(6, 2.0) == MAX_BACKOFF_S
    assert connector.backoff(4, 0) == 0.0
    # No attempt count, however large, escapes the ceiling — or overflows the
    # float that `base * 2 ** (attempt - 1)` would have built.
    assert connector.backoff(4096) == MAX_BACKOFF_S


@pytest.mark.parametrize("attempt", [0, -1, 1.0, True, None, "1"])
def test_backoff_refuses_an_attempt_that_is_not_one_based(attempt):
    # The convention is the one thing a call site can break silently: at
    # attempt 0 the doubling would halve the first wait, not raise.
    with pytest.raises(AssetError, match="attempt"):
        connector.backoff(attempt)


def test_retry_after_reads_a_numeric_header_whatever_its_case():
    assert connector.retry_after({"Retry-After": "3"}, 9.0) == 3.0
    assert connector.retry_after({"retry-after": "3"}, 9.0) == 3.0
    assert connector.retry_after({"RETRY-AFTER": 3}, 9.0) == 3.0


def test_retry_after_is_capped_by_the_one_ceiling_and_floored_at_zero():
    assert connector.retry_after({"Retry-After": "100000"}, 9.0) == MAX_BACKOFF_S
    assert connector.retry_after({"Retry-After": "inf"}, 9.0) == MAX_BACKOFF_S
    assert connector.retry_after({"Retry-After": "-5"}, 9.0) == 0.0


@pytest.mark.parametrize("headers", [
    {},
    None,
    {"Retry-After": "soon"},
    {"Retry-After": "Fri, 31 Dec 1999 23:59:59 GMT"},
    {"Retry-After": None},
    {"Retry-After": "nan"},
    object(),
])
def test_an_unusable_retry_after_falls_back_to_the_ordinary_backoff(headers):
    # NaN is the one the copies disagreed on: `min(max(nan, 0.0), cap)` is
    # NaN, which reaches time.sleep as a ValueError. Unusable means fall back.
    assert connector.retry_after(headers, 9.0) == 9.0

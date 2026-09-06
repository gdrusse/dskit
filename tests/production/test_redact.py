"""`redact.py` — credentials resolve here and never leave here (§5.0).

Three kinds of credential: environment values named (never held) by the
document, webhook URLs, and proof bytes. `redact(text)` is what every log
line, alert body and recorded `reason` passes through, so the tests below
are the ones that keep "no secret ever reaches a record" true at the
source.

Scope note: the CROSS-MODULE proofs — that no secret reaches a ledger
record, an alert payload or the CLI's output — belong in
`test_ledger.py` / `test_alerts.py` / `test_main.py` against real records,
not as stubs here; a stub would assert against a fake and prove nothing.
`test_ledger.py` carries the ledger half against a real chain; the other
two arrive with `alerts.py` and `__main__.py`.
"""

import logging

import pytest

from dskit.pipeline.base import EnvConfig
from dskit.pipeline.env import Secrets
from dskit.production.base import ProductionError
from dskit.production.redact import (
    REDACTED,
    get_logger,
    redact,
    register_secret,
    resolve_secrets,
)


@pytest.fixture()
def env_file(tmp_path):
    """An `env.env_file` in the document's shape: `KEY=VALUE` lines."""
    path = tmp_path / "serve.env"
    path.write_text(
        "# credentials live here, never in the document\n"
        "DSKIT_TEST_API_KEY=ak_live_7Q2sVhas9WdKe\n"
        "export DSKIT_TEST_API_SECRET='sk_live_ZZtop4242'\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def env_config(env_file):
    return EnvConfig(
        env_file=str(env_file),
        require=("DSKIT_TEST_API_KEY", "DSKIT_TEST_API_SECRET"),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def test_resolution_reads_the_documents_env_file(env_config):
    """The document declares WHERE credentials live and WHICH names must
    exist — never a value (`dskit.pipeline.env`, reused whole)."""
    secrets = resolve_secrets(env_config)
    assert isinstance(secrets, Secrets)
    assert secrets["DSKIT_TEST_API_KEY"] == "ak_live_7Q2sVhas9WdKe"


def test_a_required_name_that_is_unset_refuses(tmp_path, monkeypatch):
    """§5.0: `plan` refuses when any name in `env.require` is unset —
    discovering a missing credential at the first live submit is the
    failure this check exists to prevent."""
    monkeypatch.delenv("DSKIT_TEST_ABSENT_NAME", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    config = EnvConfig(env_file=str(empty), require=("DSKIT_TEST_ABSENT_NAME",))
    with pytest.raises(ProductionError) as excinfo:
        resolve_secrets(config)
    assert "DSKIT_TEST_ABSENT_NAME" in str(excinfo.value)


def test_the_process_environment_wins_over_the_file(env_config, monkeypatch):
    monkeypatch.setenv("DSKIT_TEST_API_KEY", "ak_from_the_process_env")
    secrets = resolve_secrets(env_config)
    assert secrets["DSKIT_TEST_API_KEY"] == "ak_from_the_process_env"


def test_only_the_declared_names_are_treated_as_credentials(env_config, monkeypatch):
    """`env.require` is the document's declaration of WHICH names hold
    credentials, and it is the set `redact` learns. Sweeping every value
    in the process environment instead turns `redact` into a shredder —
    a one-character value like a path separator then masks every line —
    so resolution registers the declared names and nothing else."""
    monkeypatch.setenv("DSKIT_TEST_NOT_REQUIRED", "plain_operational_value_9")
    resolve_secrets(env_config)
    assert redact("mode=plain_operational_value_9") == (
        "mode=plain_operational_value_9"
    )


def test_a_required_name_that_is_set_but_empty_refuses(tmp_path, monkeypatch):
    """A present-but-empty credential passes `load_env`'s presence check
    and would then be registered as a zero-length secret — which masks
    nothing and hides the fact that the deployment has no key. It is the
    same failure as an unset name, and refuses the same way."""
    monkeypatch.delenv("DSKIT_TEST_EMPTY_NAME", raising=False)
    path = tmp_path / "blank.env"
    path.write_text("DSKIT_TEST_EMPTY_NAME=\n", encoding="utf-8")
    config = EnvConfig(env_file=str(path), require=("DSKIT_TEST_EMPTY_NAME",))
    with pytest.raises(ProductionError) as excinfo:
        resolve_secrets(config)
    assert "DSKIT_TEST_EMPTY_NAME" in str(excinfo.value)


def test_the_secrets_facade_redacts_its_own_repr(env_config):
    """Why the pipeline's façade is reused rather than a dict: it resists
    display and is not JSON-serializable, so it cannot ride into an
    artifact by accident."""
    secrets = resolve_secrets(env_config)
    assert "ak_live_7Q2sVhas9WdKe" not in repr(secrets)


# ---------------------------------------------------------------------------
# redact()
# ---------------------------------------------------------------------------


def test_every_resolved_value_is_redacted(env_config):
    resolve_secrets(env_config)
    line = "submit failed: key=ak_live_7Q2sVhas9WdKe secret=sk_live_ZZtop4242"
    masked = redact(line)
    assert "ak_live_7Q2sVhas9WdKe" not in masked
    assert "sk_live_ZZtop4242" not in masked
    assert REDACTED in masked
    assert "submit failed" in masked, "redaction masks the credential, not the line"


def test_a_registered_proof_is_redacted():
    """Proof bytes are a credential (§5.0): a maker's signature in a log
    line is a signature an attacker can replay."""
    register_secret(b"MAKER-PROOF-9c1f2ab4-BYTES")
    assert "MAKER-PROOF-9c1f2ab4-BYTES" not in redact(
        "arm_request proof=MAKER-PROOF-9c1f2ab4-BYTES accepted"
    )


def test_a_registered_string_secret_is_redacted():
    register_secret("tr_live_registered_by_hand")
    assert "tr_live_registered_by_hand" not in redact("token=tr_live_registered_by_hand")


def test_a_webhook_url_is_redacted_even_though_no_one_registered_it():
    """A webhook URL IS the credential — its path is the bearer token, and
    the sink only ever holds the env-var NAME, so nothing would have
    registered the value."""
    url = "https://hooks.example.com/services/T0A1B2/B3C4D5/xUq9TokenPath"
    masked = redact(f"alert delivery to {url} failed")
    assert url not in masked
    assert "xUq9TokenPath" not in masked
    assert "B3C4D5" not in masked


def test_an_http_webhook_url_is_redacted_too():
    url = "http://10.0.0.9:8080/hook/2f7c1a9e"
    assert "2f7c1a9e" not in redact(f"POST {url}")


def test_redact_is_idempotent():
    """Every log line, alert body and recorded reason passes through this
    function, and some pass through twice — masking a mask must not
    corrupt the text."""
    register_secret("sk_idempotence_probe_1")
    once = redact("value=sk_idempotence_probe_1 url=https://h.example.com/a/b/c")
    assert redact(once) == once


def test_a_secret_containing_another_is_masked_whole():
    """Longest first: masking `ak_live_1` before `ak_live_1_extended`
    would leave `[REDACTED]_extended` — the tail of a credential, in a
    log line, looking redacted."""
    register_secret("ak_prefix_2931")
    register_secret("ak_prefix_2931_and_the_rest")
    masked = redact("key=ak_prefix_2931_and_the_rest")
    assert "and_the_rest" not in masked
    assert masked == f"key={REDACTED}"


def test_an_empty_secret_refuses_rather_than_masking_everything():
    """A zero-length secret matches at every position; registering one
    would replace the whole line with markers and lose the message."""
    with pytest.raises(ProductionError):
        register_secret("")


def test_a_secret_that_is_part_of_the_marker_refuses():
    """Masking must be idempotent, and a secret inside `[REDACTED]` would
    make the second pass rewrite the first pass's own marker."""
    with pytest.raises(ProductionError):
        register_secret(REDACTED)
    with pytest.raises(ProductionError):
        register_secret("REDACT")


@pytest.mark.parametrize("bad", [None, 42, ["a"]])
def test_register_secret_refuses_a_value_that_is_not_text(bad):
    with pytest.raises(ProductionError):
        register_secret(bad)


def test_get_logger_refuses_a_module_that_is_not_a_name():
    with pytest.raises(ProductionError):
        get_logger("")


def test_redact_leaves_text_without_a_secret_alone():
    assert redact("tick 41 decided in 12 ms") == "tick 41 decided in 12 ms"


def test_redact_refuses_a_non_string():
    with pytest.raises(ProductionError):
        redact(object())


# ---------------------------------------------------------------------------
# The logging helper
# ---------------------------------------------------------------------------


def test_a_secret_never_reaches_a_log_line(caplog):
    """The whole point of routing logging through this module: the value
    is masked in the RECORD, so every handler — file, stderr, a child's
    own — sees the masked text."""
    register_secret("ak_never_logged_4718")
    logger = get_logger("executor")
    caplog.set_level(logging.INFO)
    logger.info("submitting with key=%s", "ak_never_logged_4718")
    assert "ak_never_logged_4718" not in caplog.text
    assert REDACTED in caplog.text


def test_a_webhook_url_never_reaches_a_log_line(caplog):
    logger = get_logger("alerts")
    caplog.set_level(logging.WARNING)
    logger.warning("sink failed: https://hooks.example.com/T9/B8/secretpath")
    assert "secretpath" not in caplog.text


def test_the_logger_is_named_under_the_package(caplog):
    """`logging.getLogger("dskit.production.<module>")` (§5.0), so an
    operator can raise or silence the whole package by one name."""
    logger = get_logger("ledger")
    assert logger.name == "dskit.production.ledger"
    assert isinstance(logger, logging.Logger)


def test_two_calls_return_one_logger_with_one_filter():
    """A helper that stacked a filter per call would redact once per
    import and slow every line."""
    first = get_logger("loop")
    second = get_logger("loop")
    assert first is second
    assert len(first.filters) == 1

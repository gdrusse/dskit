"""Credentials resolve here and never leave here (plan §5.0).

The serve document names WHERE credentials live (``env.env_file``) and
WHICH variable names must exist (``env.require``) — never a value. This
module turns that reference into values exactly once, through the
pipeline's own :func:`dskit.pipeline.env.load_env`, and remembers every
resolved value so that :func:`redact` can mask it wherever it might
surface afterwards: a log line, an alert body, a recorded ``reason``.

Three kinds of credential are masked. Values of the names in
``env.require`` — and ONLY those names: sweeping every value in the
process environment would turn ``redact`` into a shredder, since a
one-character value like a path separator then masks every line. Proof
bytes, registered by whoever verifies them (a maker's signature in a log
line is a signature an attacker can replay). And webhook URLs, masked by
shape without anyone registering them, because the URL's path IS the
bearer token and the sink only ever holds the env-var NAME.

Every module logs through :func:`get_logger`, whose one filter masks the
RECORD, so every handler — file, stderr, a child's own — sees the masked
text. Masking is idempotent: some lines pass through twice.

Import cost: stdlib plus ``dskit.pipeline.env``.
"""

import logging
import re

from dskit.pipeline.env import load_env
from dskit.production.base import ProductionError, _check_str

__all__ = ["REDACTED", "get_logger", "redact", "register_secret", "resolve_secrets"]

#: What a masked credential is replaced with.
REDACTED = "[REDACTED]"

#: The package's logger namespace: ``dskit.production.<module>``.
_LOGGER_PREFIX = "dskit.production"

#: An http(s) URL up to whitespace or a quote — the whole URL is the
#: credential, so the whole URL is masked.
_URL = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)

#: A stack of exception text in the standard shape, for masking tracebacks.
_FORMATTER = logging.Formatter()


class _Vault:
    """The registered credential values, kept longest-first for masking."""

    def __init__(self):
        self._values = set()
        self._longest_first = ()

    def add(self, value):
        """Remember ``value``; longer secrets are masked before shorter ones."""
        self._values.add(value)
        self._longest_first = tuple(sorted(self._values, key=len, reverse=True))

    def mask(self, text):
        """Return ``text`` with every registered value replaced."""
        for value in self._longest_first:
            text = text.replace(value, REDACTED)
        return text


_VAULT = _Vault()


def register_secret(value):
    """Remember a credential so :func:`redact` masks it from now on.

    Parameters
    ----------
    value : str or bytes
        The credential — an env-var value, a proof, a token. Bytes are
        matched by their UTF-8 text (undecodable bytes are replaced and
        then match nothing, harmlessly).

    Raises
    ------
    ProductionError
        If ``value`` is neither str nor bytes, is empty, or is a substring
        of :data:`REDACTED` — either would make masking non-idempotent.
    """
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        raise ProductionError([f"a secret is a str or bytes, got {type(value).__name__}"])
    if not value:
        raise ProductionError(["an empty secret cannot be masked"])
    if value in REDACTED:
        raise ProductionError(
            [f"a secret that is a substring of {REDACTED!r} cannot be masked"]
        )
    _VAULT.add(value)


def redact(text):
    """Return ``text`` with every credential masked.

    Registered values (longest first, so a secret containing another is
    masked whole) and then every http(s) URL are replaced with
    :data:`REDACTED`. Text without a credential comes back unchanged, and
    masking a masked line changes nothing.

    Parameters
    ----------
    text : str
        A log line, an alert body, a recorded reason.

    Returns
    -------
    str
        The masked text.

    Raises
    ------
    ProductionError
        If ``text`` is not a string — a non-string here is a caller that
        would have leaked a repr.
    """
    if not isinstance(text, str):
        raise ProductionError([f"redact expects a str, got {type(text).__name__}"])
    return _URL.sub(REDACTED, _VAULT.mask(text))


def resolve_secrets(env_config):
    """Materialise the document's ``env`` reference and register its values.

    Reads ``env.env_file`` when present with the process environment
    winning over it (the dotenv convention), refuses if any name in
    ``env.require`` is unset or empty — discovering a missing credential
    at the first live submit is the failure this exists to prevent — and
    registers exactly the required names' values as credentials.

    Parameters
    ----------
    env_config : dskit.pipeline.base.EnvConfig or None
        The document's ``env`` section; ``None`` resolves the process
        environment alone, requiring and registering nothing.

    Returns
    -------
    dskit.pipeline.env.Secrets
        The redacting façade: lookups work, display and JSON do not.

    Raises
    ------
    ProductionError
        Listing every missing required name, or on an unreadable or
        malformed env file, or a required name whose value is empty.
    """
    try:
        secrets = load_env(env_config)
    except (ValueError, OSError) as exc:
        raise ProductionError([str(exc)]) from exc
    required = () if env_config is None else tuple(env_config.require)
    problems = [
        f"env.require: {name} is set but empty — a credential with no value"
        for name in required
        if not secrets[name]
    ]
    if problems:
        raise ProductionError(problems)
    for name in required:
        register_secret(secrets[name])
    return secrets


class _RedactingFilter(logging.Filter):
    """Mask the rendered message, exception text and stack of every record."""

    def filter(self, record):
        """Render and mask ``record`` in place; always let it through."""
        try:
            message = record.getMessage()
        except Exception:  # a malformed format call must not leak its args
            message = f"{record.msg} {record.args!r}"
        record.msg = redact(message)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact(_FORMATTER.formatException(record.exc_info))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


#: One filter instance for the package; ``Logger.addFilter`` is idempotent
#: for the same object, so two calls never stack two filters.
_FILTER = _RedactingFilter()


def get_logger(module):
    """Return the package logger for ``module``, with its redacting filter.

    Parameters
    ----------
    module : str
        The module's short name (``"executor"``); the logger is named
        ``dskit.production.<module>`` so an operator can raise or silence
        the whole package by one name.

    Returns
    -------
    logging.Logger
        The same logger on every call, carrying exactly one filter.

    Raises
    ------
    ProductionError
        If ``module`` is not a non-empty string.
    """
    problems = []
    _check_str(problems, "module", module)
    if problems:
        raise ProductionError(problems)
    logger = logging.getLogger(f"{_LOGGER_PREFIX}.{module}")
    logger.addFilter(_FILTER)
    return logger

"""The connector contract — four verbs, one message envelope (ADR-0013).

The Airbyte/Singer consensus, repo-idiom: a :class:`Connector` declares
its config knobs (``spec``), fails fast (``check``), names its streams
(``discover``), and yields message dicts (``read``). The CONTRACT is
data, not an ABI — connectors run in-process today, and the same
envelope is subprocess-ready later.

The envelope: every message is a plain dict with ``"protocol": 1`` and a
``"type"``. Known types and their required shapes:

========  =====================================================
RECORD    ``stream`` (str), ``effective_date`` (ISO str),
          ``data`` (dict), optional ``kind``:
          ``"observation"`` (default) | ``"forecast"`` — a
          DECLARED fact, like mode; forecasts are segregated at
          save time (ADR-0014/OQ-6), never inferred from dates.
STATE     ``state`` (dict) — opaque to the platform. Fivetran
          semantics: *everything before this is durable*; the
          platform persists it only after the snapshot is.
SCHEMA    ``stream`` (str), ``schema`` (dict).
LOG       ``message`` (str), optional ``level`` (str).
ERROR     ``message`` (str) — aborts the acquisition.
FILE      ``stream`` (str), ``relpath`` (str), ``path`` (str) — a
          file the connector holds locally (ADR-0082). The
          platform COPIES it into the snapshot at
          ``payload/<stream>/<relpath>`` before the manifest is
          built, so it is digested and verified like every
          other payload byte. ``relpath`` is POSIX-relative: no
          leading ``/``, no empty, ``.`` or ``..`` segment, no
          backslash, no ``:`` (a drive or stream escape on some
          platforms). ``path`` is machine-local provenance and
          is never echoed into bronze.
========  =====================================================

Unknown types are SKIPPABLE — the forward-compat valve: a newer
connector can emit types an older platform ignores. Unknown KEYS on a
known type are refused (default-deny) — a typo is an error.

Retry: a pull's waits are policy, not per-pack taste, so this module
owns them too (ADR-0101). :data:`MAX_BACKOFF_S` is the one ceiling and
:func:`backoff` / :func:`retry_after` are the one rule; a connector pack
imports them and spells no wait of its own. This is ADR-0101's narrow
first step; its full graduation moves the whole policy — jitter, a token
budget, the ambiguous-write rule — into onboarding for
``dskit.production.resilience`` to re-export.

Secrets: ``spec()`` flags which knobs are secret. A secret knob's value
is the NAME of an environment variable, resolved by the connector inside
``check``/``read`` — secret material never enters config, stores, or
hashes (spec deliverable 7).

Import cost: stdlib + this package.
"""

from __future__ import annotations

import abc
import importlib
import math
import re

from .base import (
    AssetError,
    MODES,  # noqa: F401  (re-export: implementers branch on the mode vocabulary)
    _check_dict,
    _check_iso,
    _check_str,
    _check_unknown,
    _raise_if,
)
from .codec import storage_problems

__all__ = [
    "Connector",
    "DEFAULT_BACKOFF_S",
    "DEFAULT_CONNECTORS",
    "MAX_BACKOFF_S",
    "MESSAGE_TYPES",
    "MODES",
    "PROTOCOL",
    "RECORD_KINDS",
    "backoff",
    "check_config",
    "check_message",
    "resolve_connector",
    "retry_after",
]

#: The envelope protocol this platform speaks. A connector emitting a
#: different protocol number is refused loudly, not misread quietly.
PROTOCOL = 1

#: Known message types. Anything else is skippable (forward-compat).
MESSAGE_TYPES = ("RECORD", "STATE", "SCHEMA", "LOG", "ERROR", "FILE")

#: Record kinds — observation vs forecast is DECLARED on the message,
#: mirroring the mode ruling: segregation is never date arithmetic.
RECORD_KINDS = ("observation", "forecast")

#: The ceiling, in seconds, any pack applies to a SINGLE wait — its own
#: exponential backoff and a server-sent ``Retry-After`` included. A hostile
#: or buggy server may ask for hours; a pull never grants more than this.
MAX_BACKOFF_S = 60.0

#: The FIRST wait, in seconds, an exponential backoff starts from. One
#: name for the base four packs share; ``predexon`` reads its own from
#: config and ``alpaca_quotes`` starts wider, and both pass ``base_s``.
DEFAULT_BACKOFF_S = 0.5

#: Registered connector kinds -> import references. Tier-2 connector
#: packs add entries here; a project's own connectors use
#: ``pkg.module:Class`` directly and register nothing.
DEFAULT_CONNECTORS = {
    "alpaca": "dskit.onboarding.libs.alpaca:AlpacaBarsConnector",
    "alpaca_quotes": "dskit.onboarding.libs.alpaca_quotes:AlpacaQuoteMinutesConnector",
    "huggingface": "dskit.onboarding.libs.huggingface:HuggingFaceHubConnector",
    "kalshi": "dskit.onboarding.libs.kalshi:KalshiConnector",
    "localfiles": "dskit.onboarding.libs.localfiles:LocalFilesConnector",
    "localtables": "dskit.onboarding.libs.localtables:LocalTablesConnector",
    "polymarket": "dskit.onboarding.libs.polymarket:PolymarketConnector",
    "predexon": "dskit.onboarding.libs.predexon:PredexonConnector",
    "restapi": "dskit.onboarding.libs.restapi:RestApiConnector",
    "schwab": "dskit.onboarding.libs.schwab:SchwabBarsConnector",
}

#: ``pkg.module:ClassName`` — the pipeline's class-reference shape.
_CLASS_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")

# Allowed keys per known message type — default-deny, like params.
_MESSAGE_KEYS = {
    "RECORD": ("protocol", "type", "stream", "effective_date", "data", "kind"),
    "STATE": ("protocol", "type", "state"),
    "SCHEMA": ("protocol", "type", "stream", "schema"),
    "LOG": ("protocol", "type", "level", "message"),
    "ERROR": ("protocol", "type", "message"),
    "FILE": ("protocol", "type", "stream", "relpath", "path"),
}

# Allowed keys in one spec() knob declaration.
_KNOB_KEYS = ("required", "secret", "notes")


class Connector(abc.ABC):
    """What any connector must provide — the four-verb seam of ADR-0013.

    Implementations are cheap, stateless objects; all inputs arrive as
    arguments, all outputs leave as messages. Cost discipline per verb:
    ``spec`` is import-cheap; ``check`` may touch the network but moves
    no data; ``discover`` is cheap; heavy imports live INSIDE ``read``
    (the tier-2 rule, same as pipeline nodes).
    """

    @abc.abstractmethod
    def spec(self) -> dict:
        """Declare the allowed config knobs, default-deny.

        Returns
        -------
        dict
            ``{"params": {<knob>: {"required": bool, "secret": bool,
            "notes": str}}}`` — every key optional per knob, all other
            knob keys refused. Config supplied at run time is validated
            against exactly this by :func:`check_config`.
        """

    @abc.abstractmethod
    def check(self, config) -> None:
        """Fail fast: can we connect with this config? Raise
        :class:`~dskit.assets.base.AssetError` on failure; move no data."""

    @abc.abstractmethod
    def discover(self, config) -> list:
        """The streams this source offers.

        Returns
        -------
        list of dict
            ``{"stream": str, "schema": dict, "primary_key": list}`` each.
        """

    @abc.abstractmethod
    def read(self, config, streams, state, mode):
        """Yield envelope messages for the requested streams.

        Parameters
        ----------
        config : dict
            Knobs already validated by :func:`check_config`.
        streams : list of str
            Which streams to pull.
        state : dict
            The last persisted checkpoint for this (source, stream,
            mode) — opaque content this connector wrote via STATE
            messages; ``{}`` on first pull.
        mode : str
            ``"backfill"`` or ``"live"`` — the declared acquisition
            mode. The platform keys checkpoints per mode, so the two
            cursors never interfere; a connector may also branch on it.

        Yields
        ------
        dict
            Envelope messages (see the module docstring).
        """


def check_config(connector, config) -> None:
    """Validate run-time config against a connector's ``spec()``, default-deny.

    Checks: the spec itself is well-shaped, config keys are declared
    knobs, required knobs are present, and secret knobs hold strings
    (env-var NAMES — never secret material). Two keys are PLATFORM
    RESERVED and always allowed in config — ``notes`` (the repo's
    comment standard, ADR-0017) and ``storage`` (the codec block,
    ADR-0036; shape-checked here, stripped before the connector ever
    sees config) — and a spec may declare neither as a knob.

    Parameters
    ----------
    connector : Connector
        The connector whose spec governs.
    config : dict
        The knobs supplied at run time.

    Raises
    ------
    AssetError
        Listing every violation at once.
    """
    errors = []
    if not isinstance(connector, Connector):
        errors.append(f"connector must be a Connector, got {type(connector).__name__}")
    _check_dict(errors, "config", config)
    _raise_if(errors)

    spec = connector.spec()
    _check_dict(errors, "spec()", spec)
    _raise_if(errors)
    _check_unknown(errors, spec, ("params",), "spec()")
    params = spec.get("params", {})
    _check_dict(errors, "spec().params", params)
    _raise_if(errors)
    for knob, decl in sorted(params.items()):
        if knob in ("notes", "storage"):
            errors.append(
                f"spec().params.{knob}: reserved platform key (ADR-0017/0036) "
                "— a connector may not declare it as a knob"
            )
        _check_dict(errors, f"spec().params.{knob}", decl)
        if isinstance(decl, dict):
            _check_unknown(errors, decl, _KNOB_KEYS, f"spec().params.{knob}")
    _raise_if(errors)

    _check_unknown(errors, config, tuple(params) + ("notes", "storage"), "config")
    if "storage" in config:
        errors.extend(storage_problems(config["storage"]))
    missing = sorted(k for k, d in params.items()
                     if d.get("required", False) and k not in config)
    if missing:
        errors.append(f"config missing required knob(s) {missing}")
    for knob in sorted(config):
        if knob in params and params[knob].get("secret", False):
            if not isinstance(config[knob], str) or not config[knob]:
                errors.append(
                    f"config.{knob} is a secret knob — its value must be the "
                    f"NAME of an environment variable, got {config[knob]!r}"
                )
    _raise_if(errors)


def check_message(msg):
    """Validate one envelope message; return its type, or None to skip.

    A known type is checked strictly (default-deny keys, required
    shapes); an unknown type returns None — the caller skips it, the
    forward-compat valve. A wrong protocol or a malformed known type
    raises: those are bugs, not future features.

    Parameters
    ----------
    msg : dict
        One message as yielded by ``read``.

    Returns
    -------
    str or None
        The message type, or None for a skippable unknown type.

    Raises
    ------
    AssetError
        Listing every problem with a malformed message.
    """
    errors = []
    _check_dict(errors, "message", msg)
    _raise_if(errors)
    if msg.get("protocol") != PROTOCOL:
        errors.append(
            f"message protocol must be {PROTOCOL}, got {msg.get('protocol')!r}"
        )
    mtype = msg.get("type")
    if not isinstance(mtype, str) or not mtype:
        errors.append(f"message type must be a non-empty string, got {mtype!r}")
        _raise_if(errors)  # cannot branch without a type
    if mtype not in MESSAGE_TYPES:
        # Skippable only on OUR protocol — a foreign protocol's unknown
        # type is a wiring error, not a future feature.
        _raise_if(errors)
        return None

    _check_unknown(errors, msg, _MESSAGE_KEYS[mtype], f"{mtype} message")
    if mtype == "RECORD":
        _check_str(errors, "RECORD.stream", msg.get("stream", ""))
        _check_dict(errors, "RECORD.data", msg.get("data"))
        _check_iso(errors, "RECORD.effective_date", msg.get("effective_date", ""))
        kind = msg.get("kind", "observation")
        if kind not in RECORD_KINDS:
            errors.append(
                f"RECORD.kind must be one of {list(RECORD_KINDS)}, got {kind!r}"
            )
    elif mtype == "STATE":
        _check_dict(errors, "STATE.state", msg.get("state"))
    elif mtype == "SCHEMA":
        _check_str(errors, "SCHEMA.stream", msg.get("stream", ""))
        _check_dict(errors, "SCHEMA.schema", msg.get("schema"))
    elif mtype in ("LOG", "ERROR"):
        _check_str(errors, f"{mtype}.message", msg.get("message", ""))
    elif mtype == "FILE":
        _check_str(errors, "FILE.stream", msg.get("stream", ""))
        errors.extend(_file_relpath_problems(msg.get("relpath")))
        _check_str(errors, "FILE.path", msg.get("path", ""))
    _raise_if(errors)
    return mtype


def _file_relpath_problems(value):
    """Why ``value`` is not a safe POSIX-relative path — empty when it is."""
    if not isinstance(value, str) or not value:
        return [f"FILE.relpath must be a non-empty string, got {value!r}"]
    if "\\" in value or "\x00" in value:
        return [f"FILE.relpath must use '/' separators and carry no NUL byte, got {value!r}"]
    if value.startswith("/"):
        return [f"FILE.relpath must be relative — no leading '/' — got {value!r}"]
    if any(part in ("", ".", "..") for part in value.split("/")):
        return [f"FILE.relpath may not hold an empty, '.' or '..' segment, got {value!r}"]
    if ":" in value:
        return [f"FILE.relpath may not hold ':' in a segment (a drive or alternate-stream "
                f"escape on some platforms), got {value!r}"]
    return []


def resolve_connector(ref):
    """Turn a ``connector`` reference into a Connector subclass.

    A registered kind name is looked up in :data:`DEFAULT_CONNECTORS`;
    a ``pkg.module:ClassName`` reference is imported directly — the
    pipeline's ``uses`` idiom exactly. Import = registration: a
    project's connector needs no entry here.

    Parameters
    ----------
    ref : str
        A registered kind (``"localfiles"``) or an import reference
        (``"my_pkg.connectors:VendorAPI"``).

    Returns
    -------
    type
        The Connector subclass (not an instance).

    Raises
    ------
    AssetError
        If the reference is unknown, unimportable, or resolves to
        something that is not a Connector subclass.
    """
    errors = []
    _check_str(errors, "connector ref", ref)
    _raise_if(errors)
    if not _CLASS_REF.match(ref):
        target = DEFAULT_CONNECTORS.get(ref)
        if target is None:
            raise AssetError(
                [f"unknown connector kind {ref!r} — registered: "
                 f"{sorted(DEFAULT_CONNECTORS)}; or use pkg.module:Class"]
            )
        ref = target
    module_name, attr = ref.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise AssetError([f"cannot import connector {ref!r}: {exc}"]) from exc
    cls = getattr(module, attr, None)
    if cls is None:
        raise AssetError(
            [f"connector {ref!r}: module {module_name!r} has no attribute {attr!r}"]
        )
    if not (isinstance(cls, type) and issubclass(cls, Connector)):
        raise AssetError(
            [f"connector {ref!r} is not a Connector subclass — the four-verb "
             "contract (spec/check/discover/read) is the seam"]
        )
    return cls


# -- the retry policy: one backoff, one Retry-After (ADR-0101) ---------------

#: The header a server sends to ask for a specific wait. Matched
#: case-insensitively: ``HTTPError.headers`` normalises case and a plain
#: mapping does not, and the three packs this replaces looked it up three
#: different ways.
_RETRY_AFTER_HEADER = "retry-after"


def backoff(attempt, base_s=DEFAULT_BACKOFF_S):
    """Seconds to wait after the ``attempt``-th failure — doubling, capped.

    The one exponential every pack shares: ``base_s`` doubled once per
    failure so far, never above :data:`MAX_BACKOFF_S`. The six copies this
    replaces all drew the same curve and disagreed only on how they
    INDEXED it — three counted attempts from zero, three from one — so the
    ONE-based convention wins, matching both the ``after N attempt(s)``
    message a pack already reports and ``dskit.production.resilience``,
    which the full graduation of ADR-0101 would merge this into.

    Two knobs are deliberately absent. There is no jitter: none of the six
    had any, so adding it would change every pack's waits — it belongs to
    the full graduation, not to this step. And there is no ``cap_s``: one
    ceiling for every wait is what :data:`MAX_BACKOFF_S` means.

    Parameters
    ----------
    attempt : int
        The ONE-based number of the attempt that just failed, so the first
        wait is ``base_s`` itself.
    base_s : float, optional
        The first wait, in seconds, ``>= 0``; defaults to
        :data:`DEFAULT_BACKOFF_S`. A pack that starts wider, or reads its
        base from config, passes its own.

    Returns
    -------
    float
        Seconds in ``[0, MAX_BACKOFF_S]``.

    Raises
    ------
    AssetError
        If ``attempt`` is not an integer of one or more. Refusing zero is
        the point of the check: the doubling would HALVE the first wait
        rather than fail, which is exactly the silent drift one owner
        exists to prevent.

    Examples
    --------
    The default base, doubling until the ceiling binds::

        [backoff(n) for n in (1, 2, 3, 99)]
        # -> [0.5, 1.0, 2.0, 60.0]
    """
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise AssetError(
            ["backoff attempt must be an int >= 1 — the ONE-based number of "
             f"the attempt that just failed — got {attempt!r}"]
        )
    # Doubled by repeated multiplication, not ``base_s * 2 ** (attempt - 1)``:
    # a long-running pull's attempt count would overflow that exponent long
    # before it changed an answer the ceiling has already pinned.
    wait = float(base_s)
    for _ in range(attempt - 1):
        if wait >= MAX_BACKOFF_S:
            break
        wait *= 2
    return min(wait, MAX_BACKOFF_S)


def retry_after(headers, fallback):
    """Seconds a server's ``Retry-After`` asks for — ``fallback`` when unusable.

    Only a NUMERIC ``Retry-After`` is honoured, floored at zero and capped
    at :data:`MAX_BACKOFF_S`, so no server parks a pull for hours; the
    HTTP-date form reads as unusable, as it did in all three copies.

    Where the copies disagreed, the safe reading wins: a ``NaN`` header is
    unusable and falls back. ``polymarket`` already guarded it, while
    ``kalshi`` and ``predexon`` turned it into a zero-second wait — an
    immediate retry against a server that had just asked for one — because
    ``max(0.0, nan)`` is ``0.0``. Those two now wait the ordinary backoff
    (ADR-0101; the one behaviour change of that step).

    Parameters
    ----------
    headers : mapping or None
        The response headers. Anything without an ``items()`` — ``None``
        included — is read as carrying no header, so a caller never has to
        guard the shape a failed request handed it.
    fallback : float
        Seconds to use when no usable header is present. Callers pass
        :func:`backoff` for the attempt, so an absent, malformed or
        unusable header lands on the ordinary exponential.

    Returns
    -------
    float
        The capped header value, else ``fallback`` unchanged.

    Examples
    --------
    A server's own answer, and the fallback when it gives none::

        retry_after({"Retry-After": "3"}, backoff(1))   # 3.0
        retry_after({}, backoff(1))                     # 0.5
    """
    items = getattr(headers, "items", None)
    if not callable(items):
        return fallback
    for name, value in items():
        if str(name).strip().lower() != _RETRY_AFTER_HEADER:
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return fallback
        if math.isnan(seconds):
            return fallback
        return min(max(seconds, 0.0), MAX_BACKOFF_S)
    return fallback

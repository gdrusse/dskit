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
========  =====================================================

Unknown types are SKIPPABLE — the forward-compat valve: a newer
connector can emit types an older platform ignores. Unknown KEYS on a
known type are refused (default-deny) — a typo is an error.

Secrets: ``spec()`` flags which knobs are secret. A secret knob's value
is the NAME of an environment variable, resolved by the connector inside
``check``/``read`` — secret material never enters config, stores, or
hashes (spec deliverable 7).

Import cost: stdlib + this package.
"""

from __future__ import annotations

import abc
import importlib
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
    "DEFAULT_CONNECTORS",
    "MAX_BACKOFF_S",
    "MESSAGE_TYPES",
    "MODES",
    "PROTOCOL",
    "RECORD_KINDS",
    "check_config",
    "check_message",
    "resolve_connector",
]

#: The envelope protocol this platform speaks. A connector emitting a
#: different protocol number is refused loudly, not misread quietly.
PROTOCOL = 1

#: Known message types. Anything else is skippable (forward-compat).
MESSAGE_TYPES = ("RECORD", "STATE", "SCHEMA", "LOG", "ERROR")

#: Record kinds — observation vs forecast is DECLARED on the message,
#: mirroring the mode ruling: segregation is never date arithmetic.
RECORD_KINDS = ("observation", "forecast")

#: The ceiling, in seconds, any pack applies to a SINGLE wait — its own
#: exponential backoff and a server-sent ``Retry-After`` included. A hostile
#: or buggy server may ask for hours; a pull never grants more than this.
MAX_BACKOFF_S = 60.0

#: Registered connector kinds -> import references. Tier-2 connector
#: packs add entries here; a project's own connectors use
#: ``pkg.module:Class`` directly and register nothing.
DEFAULT_CONNECTORS = {
    "alpaca": "dskit.onboarding.libs.alpaca:AlpacaBarsConnector",
    "alpaca_quotes": "dskit.onboarding.libs.alpaca_quotes:AlpacaQuoteMinutesConnector",
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
    _raise_if(errors)
    return mtype


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

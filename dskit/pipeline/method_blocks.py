"""One attach owner for per-method optional feature blocks (ADR-0313 section D, stage I1).

A *block* is an optional JSON mapping on a method document (``calibration``, ``hedge``,
``exposure_limits``). :class:`MethodBlocks` is the ONE owner that validates and resolves them:

* default-deny: an unknown block key, an unknown or missing key inside a block, a wrong type or
  range refuses, every refusal a stable code (:data:`REFUSALS`) that starts its message;
* each kind registers a :class:`BlockSpec` through :meth:`MethodBlocks.register` (a hook: the owner
  never branches on kind); ``hedge`` is a :class:`DelegatedBlockSpec` that hands validation and
  resolution to the hedge owner (``HedgeOverlay``), so its params are not restated here;
* absent block: :meth:`~MethodBlocks.validate` is the identity (returns ``None``, touches nothing) and
  :meth:`~MethodBlocks.resolve` returns a :class:`NullBlock` per key, so call sites never branch and
  every output stays byte-identical;
* blocks are allowed only on NEW method ids (``block_on_locked_formulation``, ids carried by the
  named context parameter ``locked_method_ids``) and only under the development seal
  (``integration_seal``: ``clocks.tail_end_before <= DEVELOPMENT_SEAL``).

Unknown-method-key refusal (``block_unknown``) runs only when the context carries ``method_keys``;
stage I2 must always pass it (I2 pins this with a test), or an unknown key slips through silently.

:data:`DEVELOPMENT_SEAL` is defined here and nowhere else; every module that needs it imports it.
Import cost: stdlib only.
"""

from abc import ABC
from datetime import date

from dskit.pipeline.base import ConfigError, value_hash
from dskit.pipeline.records import number_ok

__all__ = ["MethodBlocks", "BlockSpec", "DelegatedBlockSpec", "NullBlock", "CalibrationBlock",
           "ExposureLimitsBlock", "DEVELOPMENT_SEAL", "REFUSALS"]

#: The first date the owner has NOT opened for development economics (RE-ENTRY: no 2022-2023 MIO
#: economics before the owner's 3 picks). The one owner; it moves only when the owner lifts it.
DEVELOPMENT_SEAL = "2021-07-01"
#: Stable refusal codes; each starts its message.
UNKNOWN_BLOCK, UNKNOWN_KEY, MISSING_KEY = "block_unknown", "block_unknown_key", "block_missing_key"
BLOCK_TYPE, PARAM_VALUE = "block_type", "block_param_value"
LOCKED, LOCKED_IDS_MISSING, SEAL = "block_on_locked_formulation", "locked_ids_missing", "integration_seal"
LOCKED_CHANGED, LOCKED_COLLISION = "locked_method_ids_changed", "locked_method_id_collision"
LOCKED_SPEC_CHANGED = "locked_formulation_spec_changed"
SEAL_VIOLATION = "development_seal_violation"
IDENTITY_NOT_CONTROL = "calibration_identity_not_control"
WITH_KELLY, WITH_SIZER = "calibration_with_kelly", "calibration_with_sizer"
HEDGE_WITH_SIZER, LIMITS_WITH_KELLY = "hedge_with_sizer", "exposure_limits_with_kelly"
DOUBLE_DISCOUNT, NEEDS_DIRECTION = "calibration_double_discount", "hedge_needs_direction_map"
REFUSALS = (UNKNOWN_BLOCK, UNKNOWN_KEY, MISSING_KEY, BLOCK_TYPE, PARAM_VALUE, LOCKED, LOCKED_IDS_MISSING,
            LOCKED_CHANGED, LOCKED_COLLISION, LOCKED_SPEC_CHANGED, SEAL_VIOLATION, SEAL, IDENTITY_NOT_CONTROL,
            WITH_KELLY, WITH_SIZER, HEDGE_WITH_SIZER, LIMITS_WITH_KELLY, DOUBLE_DISCOUNT, NEEDS_DIRECTION)
#: Method keys the cross-block rules read (formulation keys of the child's method document).
_KELLY, _SIZER, _RHO, _RISK_CHARGE = "kelly", "sizer", "rho", "risk_charge"


def coded(code, message):
    """Return a problem string starting with its stable ``code``."""
    return f"{code}: {message}"


def _int_ok(value, floor=1):
    """Say whether ``value`` is an int (not a bool) of at least ``floor``."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= floor


def _strip_owner(problem):
    """Drop a leading ``ClassName: `` (no underscore) so the owner's own code starts the problem."""
    head, sep, rest = problem.partition(": ")
    return rest if sep and head.isidentifier() and "_" not in head else problem


class NullBlock:
    """The resolved object of an absent block: present is false, every record is empty."""

    present = False
    params = {}

    def __init__(self, key=None):
        self.key = key

    def record(self):
        """Return ``{}``: an absent block adds no field anywhere."""
        return {}


class BlockSpec(ABC):
    """One block kind: its key, its params tuple and its value rules (the hook a kind registers).

    Subclasses set ``KEY``, ``_PARAMS`` (every key the block may carry) and optionally ``_OPTIONAL``
    (the subset that may be absent), and override :meth:`check_values`.
    """

    KEY = None
    _PARAMS = ()
    _OPTIONAL = ()

    @classmethod
    def validate(cls, block):
        """Return every problem a block has, each starting with its refusal code."""
        if not isinstance(block, dict):
            return [coded(BLOCK_TYPE, f"{cls.KEY} block must be a mapping, got {type(block).__name__}")]
        problems = []
        unknown = sorted(k for k in block if k not in cls._PARAMS)
        if unknown:
            problems.append(coded(UNKNOWN_KEY, f"{cls.KEY}: unknown key(s) {unknown} - allowed: {sorted(cls._PARAMS)}"))
        missing = [k for k in cls._PARAMS if k not in block and k not in cls._OPTIONAL]
        if missing:
            problems.append(coded(MISSING_KEY, f"{cls.KEY}: missing required key(s) {missing}"))
        if not unknown and not missing:
            cls.check_values(problems, block)
        return problems

    @classmethod
    def check_values(cls, problems, block):
        """Append the value problems of a block whose keys are exactly right (default: none)."""

    @classmethod
    def resolve(cls, block):
        """Return the resolved object of a present block (a frozen copy of its params)."""
        return _Resolved(cls.KEY, block)

    @classmethod
    def null(cls):
        """Return the resolved object of an absent block."""
        return NullBlock(cls.KEY)

    @staticmethod
    def bad(problems, key, why):
        """Append a ``block_param_value`` problem naming ``key``."""
        problems.append(coded(PARAM_VALUE, f"{key} {why}"))


class _Resolved:
    """A present block: its key and params."""

    present = True

    def __init__(self, key, params):
        self.key = key
        self.params = dict(params)

    def record(self):
        """Return the block's params, the one field a result carries for it."""
        return {self.key: dict(self.params)}


class CalibrationBlock(BlockSpec):
    """``calibration`` (ADR-0313 D): the calibrated-edge objective term's params."""

    KEY = "calibration"
    _PARAMS = ("source", "state", "edge_scale", "regressors", "line", "form", "variant", "warmup",
               "min_warm_weeks", "control")
    _OPTIONAL = ("control",)
    FORMS, LINES, VARIANTS = ("slope", "affine", "identity"), ("bucket", "bucket_name"), ("point", "lcb")

    @classmethod
    def check_values(cls, problems, block):
        """Check each calibration param; ``control`` is true exactly with ``form == "identity"``."""
        if block["source"] != "edge_maps":
            cls.bad(problems, "source", f"must be 'edge_maps', got {block['source']!r}")
        for name in ("state", "edge_scale"):
            if not isinstance(block[name], str) or not block[name]:
                cls.bad(problems, name, f"must be a non-empty string, got {block[name]!r}")
        if not (_int_ok(block["regressors"]) and block["regressors"] in (1, 2)):
            cls.bad(problems, "regressors", f"must be 1 or 2, got {block['regressors']!r}")
        for name, allowed in (("line", cls.LINES), ("form", cls.FORMS), ("variant", cls.VARIANTS)):
            if block[name] not in allowed:
                cls.bad(problems, name, f"must be one of {list(allowed)}, got {block[name]!r}")
        if block["warmup"] != "no_trade":
            cls.bad(problems, "warmup", f"must be 'no_trade', got {block['warmup']!r}")
        if not _int_ok(block["min_warm_weeks"]):
            cls.bad(problems, "min_warm_weeks", f"must be an int >= 1, got {block['min_warm_weeks']!r}")
        control = block.get("control")
        if "control" in block and not isinstance(control, bool):
            cls.bad(problems, "control", f"must be a bool, got {control!r}")
        elif (block["form"] == "identity") != (control is True):
            problems.append(coded(IDENTITY_NOT_CONTROL, "form 'identity' requires \"control\": true and "
                                                        "\"control\": true is allowed only with form 'identity'"))


class ExposureLimitsBlock(BlockSpec):
    """``exposure_limits`` (ADR-0313 D): the Greeks-limit rows' params."""

    KEY = "exposure_limits"
    _PARAMS = ("beta_delta_fraction", "vega_fraction_per_pt", "dte_edges", "beta_lookback", "scope")
    SCOPES = ("account",)

    @classmethod
    def check_values(cls, problems, block):
        """Check each limit param."""
        for name in ("beta_delta_fraction", "vega_fraction_per_pt"):
            if not (number_ok(block[name]) and block[name] >= 0):
                cls.bad(problems, name, f"must be a finite number >= 0, got {block[name]!r}")
        edges = block["dte_edges"]
        if not (isinstance(edges, list) and edges and all(_int_ok(e) for e in edges)
                and all(a < b for a, b in zip(edges, edges[1:]))):
            cls.bad(problems, "dte_edges", f"must be a non-empty strictly increasing list of ints >= 1, got {edges!r}")
        if not _int_ok(block["beta_lookback"]):
            cls.bad(problems, "beta_lookback", f"must be an int >= 1, got {block['beta_lookback']!r}")
        if block["scope"] not in cls.SCOPES:
            cls.bad(problems, "scope", f"must be one of {list(cls.SCOPES)}, got {block['scope']!r}")


class DelegatedBlockSpec(BlockSpec):
    """A kind whose params, validation and resolution belong to another owner (``hedge`` -> ``HedgeOverlay``).

    The owner class must offer ``validate_params(block) -> [problem]`` and ``from_method(method)``
    (its resolved object, a Null object when the key is absent). Build one with :meth:`bind`.
    """

    OWNER = None

    @classmethod
    def bind(cls, key, owner):
        """Return a spec subclass for ``key`` that delegates to ``owner``."""
        return type(f"Delegated_{key}", (cls,), {"KEY": key, "OWNER": owner})

    @classmethod
    def validate(cls, block):
        """Return the owner's own problems (its codes, unchanged), by building its object."""
        try:
            cls.OWNER.from_method({cls.KEY: block})
        except ConfigError as exc:
            return [_strip_owner(e) for e in exc.errors]
        return []

    @classmethod
    def resolve(cls, block):
        """Return the owner's resolved object for ``{key: block}``."""
        return cls.OWNER.from_method({cls.KEY: block})

    @classmethod
    def null(cls):
        """Return the owner's Null object."""
        return cls.OWNER.from_method({})


class MethodBlocks:
    """Registry and single owner of a method's optional blocks (default-deny, identity when absent).

    Parameters
    ----------
    specs : iterable of BlockSpec subclasses, optional
        Kinds to register at construction (each key once).

    Examples
    --------
    Validate and resolve a research method::

        owner = MethodBlocks.standard(hedge=HedgeOverlay)
        owner.validate(method, {"locked_method_ids": nine, "clocks": clocks})   # None or ConfigError
        resolved = owner.resolve(method)    # {"hedge": ..., "calibration": ..., "exposure_limits": ...}
    """

    def __init__(self, specs=()):
        self._specs = {}
        for spec in specs:
            self.register(spec)

    @classmethod
    def standard(cls, hedge=None):
        """Return an owner with ``calibration``, ``exposure_limits`` and, given its owner class, ``hedge``."""
        owner = cls([CalibrationBlock, ExposureLimitsBlock])
        if hedge is not None:
            owner.register(DelegatedBlockSpec.bind("hedge", hedge))
        return owner

    def register(self, spec):
        """Register one kind by its ``KEY``; a second owner for a key is refused."""
        if not (isinstance(spec, type) and issubclass(spec, BlockSpec)) or not spec.KEY:
            raise ValueError("register takes a BlockSpec subclass with a KEY")
        if spec.KEY in self._specs:
            raise ValueError(f"block kind {spec.KEY!r} is already registered")
        self._specs[spec.KEY] = spec

    def keys(self):
        """Return the registered block keys in registration order."""
        return tuple(self._specs)

    def present(self, method):
        """Return the registered block keys the method carries."""
        return tuple(k for k in self._specs if k in method)

    def validate(self, method, context=None):
        """Refuse a method's blocks with every problem at once; return ``None`` when clean or absent.

        ``context``: ``method_keys`` (optional: every non-block key a method may carry; when given,
        any other key is ``block_unknown``), and, read only when a block is present,
        ``locked_method_ids`` (ids that may carry no block) and ``clocks`` (for the seal).
        """
        context = context or {}
        problems = []
        if "method_keys" in context:
            allowed = set(context["method_keys"]) | set(self._specs)
            unknown = sorted(k for k in method if k not in allowed)
            if unknown:
                problems.append(coded(UNKNOWN_BLOCK, f"unknown method key(s) {unknown}"))
        keys = self.present(method)
        if not keys:
            if problems:
                raise ConfigError(problems)
            return None
        for key in keys:
            if isinstance(method[key], dict):
                problems.extend(self._specs[key].validate(method[key]))
            else:
                problems.append(coded(BLOCK_TYPE, f"{key} block must be a mapping, got {type(method[key]).__name__}"))
        problems.extend(self._locked(method, context.get("locked_method_ids")))
        problems.extend(self.seal(context.get("clocks")))
        if not problems:
            problems.extend(self.cross_checks(method))
        if problems:
            raise ConfigError(problems)
        return None

    def _locked(self, method, locked):
        """Return the locked-formulation problems of a method that carries a block."""
        if not isinstance(locked, (list, tuple)):
            return [coded(LOCKED_IDS_MISSING, "a document with a block must carry locked_method_ids (a list)")]
        if self._id_key(method.get("id")) in {self._id_key(i) for i in locked}:
            return [coded(LOCKED, f"method {method.get('id')!r} is a locked formulation and may carry no block")]
        return []

    @staticmethod
    def _id_key(method_id):
        return method_id.strip().casefold() if isinstance(method_id, str) else None

    @classmethod
    def lock_problems(cls, pinned, merged, new_ids, existing_ids=()):
        """Check the ordered locked declaration and refuse new ids that alias locked or source ids."""
        problems = []
        ordered = list(pinned)
        if not isinstance(merged, list) or merged != ordered:
            problems.append(coded(LOCKED_CHANGED, f"locked_method_ids must equal the ordered declaration {ordered}, "
                                                  f"got {merged!r}"))
        reserved = {cls._id_key(i) for i in (*ordered, *existing_ids)}
        clash = sorted({i for i in new_ids if cls._id_key(i) in reserved})
        if clash:
            problems.append(coded(LOCKED_COLLISION, f"method id(s) {clash} are locked or already used: a research arm takes a NEW id"))
        return problems

    @classmethod
    def spec_hash(cls, method):
        """Hash the whole method spec, with JSON integers and equivalent floats treated alike."""
        return value_hash(cls._spec_numbers(method))

    @classmethod
    def _spec_numbers(cls, value):
        if isinstance(value, dict):
            return {k: cls._spec_numbers(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._spec_numbers(v) for v in value]
        return float(value) if type(value) is int else value

    @classmethod
    def locked_spec_problems(cls, pinned, methods):
        """Check present locked specs against their pins and refuse aliases of their ids."""
        canonical = {cls._id_key(i): i for i in pinned}
        problems = []
        for method in methods:
            method_id = method["id"]
            locked = canonical.get(cls._id_key(method_id))
            if locked is None:
                continue
            if method_id != locked:
                problems.append(coded(LOCKED_COLLISION, f"method {method_id!r} aliases locked formulation {locked!r}"))
            elif cls.spec_hash(method) != pinned[locked]:
                problems.append(coded(LOCKED_SPEC_CHANGED, f"formulation {locked!r} differs from its pinned spec"))
        return problems

    @staticmethod
    def seal_violations(ends):
        """Return the ``development_seal_violation`` problems of the dates (ISO strings) a document reaches past the seal."""
        late = sorted(e for e in ends if not isinstance(e, str) or e > DEVELOPMENT_SEAL)
        return [coded(SEAL_VIOLATION, f"{late} is past the development seal {DEVELOPMENT_SEAL}")] if late else []

    @staticmethod
    def seal(clocks):
        """Return the ``integration_seal`` problems of a document's clocks (none when sealed)."""
        end = clocks.get("tail_end_before") if isinstance(clocks, dict) else None
        try:
            ok = isinstance(end, str) and date.fromisoformat(end) <= date.fromisoformat(DEVELOPMENT_SEAL)
        except ValueError:
            ok = False
        if ok:
            return []
        return [coded(SEAL, f"a document with a block must declare clocks.tail_end_before <= {DEVELOPMENT_SEAL}, got {end!r}")]

    def cross_checks(self, method):
        """Return the pairwise refusals between a method's blocks and its formulation keys."""
        problems = []
        has = lambda key: key in method and key in self._specs  # noqa: E731
        cal, hedge = method.get("calibration"), method.get("hedge")
        if has("calibration") and _KELLY in method:
            problems.append(coded(WITH_KELLY, "a calibration block cannot combine with kelly"))
        if has("calibration") and _SIZER in method:
            problems.append(coded(WITH_SIZER, "a calibration block cannot combine with sizer until the sizer mean lands"))
        if has("hedge") and _SIZER in method:
            problems.append(coded(HEDGE_WITH_SIZER, "a hedge block cannot combine with sizer"))
        if has("exposure_limits") and _KELLY in method:
            problems.append(coded(LIMITS_WITH_KELLY, "an exposure_limits block cannot combine with kelly"))
        if has("calibration") and cal.get("variant") == "lcb":
            rho = method.get(_RHO)
            if _RHO in method and not number_ok(rho):
                problems.append(coded(PARAM_VALUE, f"rho must be a finite number with variant 'lcb', got {rho!r}"))
            elif (_RHO in method and rho > 0) or _RISK_CHARGE in method:
                problems.append(coded(DOUBLE_DISCOUNT, "variant 'lcb' with rho > 0 or a risk_charge discounts the "
                                                       "forecast uncertainty twice; use the point map"))
        if has("calibration") and has("hedge") and hedge.get("mode") == "program" and cal.get("regressors") != 2:
            problems.append(coded(NEEDS_DIRECTION, "a program-mode hedge with calibration needs regressors == 2"))
        return problems

    def resolve(self, method):
        """Return ``{key: resolved object}`` for every registered kind (a Null object when absent)."""
        return {key: (spec.resolve(method[key]) if key in method else spec.null())
                for key, spec in self._specs.items()}

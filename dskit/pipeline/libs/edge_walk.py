"""Walk-forward edge calibration, tier 2 (ADR-0311 stage 3 and Amendment 1 stage F3).

:class:`EdgeReliabilityWalk` replays a decision calendar week by week. At each decision session ``t`` it
(a) settles every pending row that settled strictly before ``t`` (the fill-session quote and the settlement
level are read HERE and only here), (b) folds the settled ``(name, settlement date)`` clusters, whole and once,
into the fourteen :class:`~dskit.pipeline.edge_reliability.LinearEdgeState` objects of D7 (nine without
``direction_regressor``): P1-P4 ``{expanding, decayed} x {fill, decision}``, X1-X4 the same with the
two-regressor map, R1 and R2 (reported rows only), R4 (the forward line), S1 and S3 (the fill sensitivity) and
D1 (dollars), plus a common-cluster twin of S3 and the integration-owned forward lines (ADR-0313), (c) snapshots
one frozen map per primary state, then (d) decides: the day's rows get their bucket, the snapshot's calibrated
edge and their stored regressors and wait, keyed by settlement date, in an on-disk shard. The map at ``t`` is
computed before a single row of ``t`` is read, so nothing dated at or after ``t`` can reach it.

Cluster sums are formed with numpy (``np.add.at`` by bucket and side) and handed to ``add_cluster_sums``; the
stdlib ``add_cluster`` twin is pinned equal to 1e-12. A ``quote`` or ``size`` row has no fill: it waits in a
``sensitivity/`` buffer shard until every decision week that contributes to its cluster is complete, then enters
the sensitivity states, booked at its decision mid moved against the trade by that week's frozen ``q95``.

The domain enters only through six ``@abstractmethod`` hooks, here synthetic in the tests and a thin child in a
real run. A row is a mapping with ``group, name, expiry, settle_date, fill_date, factor, right, strike, sign, e,
dte, abs_z, spread, v, credit, multiplier`` (``credit`` is the decision-price credit in dollars, ``right`` is
``call`` or ``put``, ``sign`` is +1 to buy and -1 to sell) and, per the amendment, ``scale`` (required unless
``edge_scale`` is ``none``), ``e_dir`` with ``direction_code`` when it is absent, ``sigma2``, ``mid``, ``fee``
and ``cdf_repaired``. Realized edge is ``sign * payoff * multiplier + credit`` with the fill-session credit
(primary) or the decision credit (secondary).

Checkpoint and resume: ONE checkpoint, ``checkpoint-<cursor>.npz`` (every state array, the regime history, the
sensitivity buffer index and the frozen ``q95``) plus the small ``checkpoint.json`` manifest (params hash, pins,
cursor, the sha256 of the ``.npz``, a content hash of every written file and pending shard). The ``.npz`` lands
first, then the manifest, then the superseded ``.npz`` and the consumed shards are deleted, so a crash leaves the
previous checkpoint intact. A resume refuses ``checkpoint_mismatch`` on a changed param, pin or checkpoint
version (the message names both versions), on any hash that differs, and ``state_from_future`` when the
checkpoint is later than the requested window. Every file lands through ``atomic_write``.

Wiring is by import path; :data:`NODE_KINDS` is empty. Import cost: stdlib and the pipeline core (numpy inside
methods only).
"""

import hashlib
import io
import json
import os
import zipfile
from abc import abstractmethod
from collections import namedtuple

from dskit.pipeline.edge_reliability import (
    EdgeCalibrationMap,
    LinearEdgeState,
    NameAdjustment,
    PartialPooling,
    ReliabilityBuckets,
    SettledBefore,
    TrailingRegime,
    _iso,
    _Refusal,
    _cr1,
    _statistic_tests,
    block_index,
    origin_two,
    read_one,
    three_way_verdict,
    wild_cluster_test,
)
from dskit.pipeline.node import Node, atomic_write, reject_unknown_params
from dskit.pipeline.records import number_ok
from dskit.pipeline.stats import benjamini_hochberg, student_t_critical, student_t_sf

__all__ = ["NODE_KINDS", "EdgeReliabilityWalk"]

#: No node kind is registered: a document names ``dskit.pipeline.libs.edge_walk:EdgeReliabilityWalk``.
NODE_KINDS = ()

#: Bump on ANY change to the checkpoint's content or meaning; a checkpoint of another version refuses.
_VERSION = 6
#: The four primary one-regressor states, in the order every stored per-row list holds them.
_PRIMARY = ("expanding_fill", "decayed_fill", "expanding_decision", "decayed_decision")
#: X1-X4: the same four cells with the two-regressor map (direction and shape edge).
_DIRECTION = tuple("dir_" + k for k in _PRIMARY)
#: R1, R2, R4, S1, S3, D1 (ADR-0311 Amendment 1 D7).
_EXTRA = ("reported_fill", "reported_dir", "forward", "fill_sensitivity", "reported_sensitivity", "dollar")
#: The fourteen states of D7 (nine when ``direction_regressor`` is false: no X1-X4 and no R2).
_STATES = _PRIMARY + _DIRECTION + _EXTRA
_Spec = namedtuple("_Spec", "k decayed price rows adjust top")
#: Per state: regressors, decayed, price, which rows it takes, a name adjustment, a ``top`` unit.
_SPECS = {
    **{k: _Spec(1, "decayed" in k, "decision" if "decision" in k else "fill", "all", True, False) for k in _PRIMARY},
    **{k: _Spec(2, "decayed" in k, "decision" if "decision" in k else "fill", "all", False, False)
       for k in _DIRECTION},
    "reported_fill": _Spec(1, False, "fill", "reported", False, True),
    "reported_dir": _Spec(2, False, "fill", "reported", False, False),
    "forward": _Spec(1, False, "fill", "forward", False, False),
    "fill_sensitivity": _Spec(1, False, "fill", "sens", False, False),
    "reported_sensitivity": _Spec(1, False, "fill", "sens_reported", False, True),
    "dollar": _Spec(1, False, "fill", "all", False, False),
    # integration-owned forward lines (ADR-0313 X2, X6): outside the fourteen, never part of a confirmatory read
    "forward_w": _Spec(1, False, "fill", "forward", False, False),
    "forward_b2": _Spec(1, False, "fill", "forward", False, False),
}
_FORWARD_COLUMN = {"forward": "cb0", "forward_w": "cw", "forward_b2": "c2w"}
_FILL_CODES = ("session", "basis", "quote", "size")
_LEVEL_CODES = ("basis_changed", "no_settlement_bar")
_DIRECTION_CODES = ("leg_no_iv", "leg_below_min_years", "no_quote", "duplicate_quote")
_ROW_KEYS = ("group", "name", "expiry", "settle_date", "fill_date", "factor", "right", "strike", "sign", "e",
             "dte", "abs_z", "spread", "v", "credit", "multiplier")
#: A pending row is a list in this order (the decision date ``t`` leads it, the settlement date is its shard's key).
(_T, _GROUP, _NAME, _EXPIRY, _FILL, _FACTOR, _RIGHT, _STRIKE, _SIGN, _E, _BUCKET, _CREDIT, _MULT, _CB, _CF, _EDIR,
 _DCODE, _SIGMA2, _SCALE, _BASE, _TOP, _REPAIRED, _WARM, _MID, _FEE, _C2, _DEC, _GSIZE, _CW, _C2W) = range(30)
#: Params that cannot change a number of the output (where it lands, how often it is saved, the RAM cap).
_OPERATIONAL = ("checkpoint_every", "ram_gb_limit", "out_dir")
_REQUIRED = ("window", "report_from", "out_dir", "buckets", "regime", "pooling", "name_adjust")
_DEFAULTS = {
    "half_life_weeks": 26, "checkpoint_every": 4, "ram_gb_limit": 6, "direction_regressor": False,
    "edge_scale": "notional", "row_weight": "group_equal", "time_block_weeks": [4, 13], "min_fill_rate": 0.9,
    "min_fill_sample": 20, "ci_level": 0.95, "bh_q": 0.10, "bootstrap_draws": 999, "bootstrap_seed": 20261109,
    "tail_level_floor": 0.5, "b0_floor": 0.5, "hac_lags": 2,
}
_EDGE_SCALES = ("notional", "scale", "none")
_ROW_WEIGHTS = ("group_equal", "none")


def _canon(obj):
    """Canonical JSON text: sorted keys, no NaN, so equal content dumps equal."""
    return json.dumps(obj, sort_keys=True, allow_nan=False, separators=(",", ":"))


_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_MIN_INTS = 8


def _pack(obj, floats, ints):
    """Return ``obj`` with every float list and long int list moved into ``floats`` / ``ints`` by reference."""
    if isinstance(obj, dict):
        return {k: _pack(v, floats, ints) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        if obj and all(type(v) is float for v in obj):
            ref = {"@f": [len(floats), len(obj)]}
            floats.extend(obj)
            return ref
        if len(obj) >= _MIN_INTS and all(type(v) is int for v in obj):
            ref = {"@i": [len(ints), len(obj)]}
            ints.extend(obj)
            return ref
        return [_pack(v, floats, ints) for v in obj]
    return obj


def _unpack(obj, floats, ints):
    """Invert :func:`_pack` given the two arrays."""
    if isinstance(obj, dict):
        if len(obj) == 1 and "@f" in obj:
            a, n = obj["@f"]
            return floats[a:a + n].tolist()
        if len(obj) == 1 and "@i" in obj:
            a, n = obj["@i"]
            return ints[a:a + n].tolist()
        return {k: _unpack(v, floats, ints) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unpack(v, floats, ints) for v in obj]
    return obj


def _npz_bytes(tree):
    """Return the checkpoint ``.npz`` bytes of a JSON-safe ``tree``.

    Holds float64 ``floats``, int64 ``ints`` and the ``tree`` text as uint8, stored with a fixed zip
    timestamp so equal content is equal bytes.
    """
    import numpy as np

    floats, ints = [], []
    body = _canon(_pack(tree, floats, ints)).encode()
    parts = {"floats": np.array(floats, dtype=np.float64), "ints": np.array(ints, dtype=np.int64),
             "tree": np.frombuffer(body, dtype=np.uint8)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, arr in parts.items():
            raw = io.BytesIO()
            np.save(raw, arr, allow_pickle=False)
            zf.writestr(zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_TIME), raw.getvalue())
    return buf.getvalue()


def _npz_tree(raw):
    """Return the tree stored by :func:`_npz_bytes`."""
    import numpy as np

    with np.load(io.BytesIO(raw), allow_pickle=False) as z:
        tree = json.loads(z["tree"].tobytes().decode())
        return _unpack(tree, z["floats"], z["ints"])


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class EdgeReliabilityWalk(Node):
    """Walk-forward map from predicted edge to realized edge, with checkpoint and resume (ADR-0311).

    Role ``transform``; no inputs (the hooks read the data). Output ``summary``: counts per code, the
    params hash, the input pins and a digest of the written maps and row shards; the same dict is
    written to ``summary.json``. Files under ``out_dir``: ``map-<t>.json`` (all four maps at ``t``),
    ``rows/rows-<s>.jsonl`` (rows decided at or after ``report_from``, written when they settle),
    ``pending/<s>/<t>.json`` (rows waiting for settlement), ``checkpoint.json``, ``summary.json``.

    Parameters
    ----------
    params : dict
        ``window`` (``start``, ``end`` decision dates), ``report_from`` (after ``window.start``; earlier
        decisions are a seed: state only), ``sealed_from`` (required at run, at most the development
        seal), ``out_dir``, ``half_life_weeks`` (default 26; the decayed states age by ``2 ** (-1 /
        half_life_weeks)`` per decision step), ``buckets``, ``regime``, ``pooling``, ``name_adjust``
        (the params of the core classes), ``checkpoint_every`` (weeks, default 1), ``ram_gb_limit``
        (default 6).

    Raises
    ------
    ValueError
        Run-level refusals carry a stable ``code``: ``sealed_from_missing``, ``development_seal_exceeded``,
        ``window_outside_data_rights`` (before any hook runs), ``report_from_invalid``,
        ``bucket_edges_invalid``, ``calendar_mismatch``, ``fill_not_next_session``, ``checkpoint_mismatch``,
        ``state_from_future``,
        ``ram_limit``.

    Examples
    --------
    A child supplies the four hooks and a JSON params block::

        class MyWalk(EdgeReliabilityWalk):
            def _decision_calendar(self): ...
            def _decision_rows(self, t): ...
            def _fill_credits(self, rows): ...
            def _settlement_levels(self, rows): ...
            def _next_session(self, t): ...
            def _input_pins(self): ...

        MyWalk("walk", params).run(ctx, {})["summary"]["counts"]
    """

    role = "transform"
    outputs = ("summary",)
    _BASE_PARAMS = ("window", "report_from", "sealed_from", "out_dir", "half_life_weeks", "buckets", "regime",
                    "pooling", "name_adjust", "checkpoint_every", "ram_gb_limit", "direction_regressor", "edge_scale",
                    "row_weight", "time_block_weeks", "min_fill_rate", "min_fill_sample", "ci_level", "bh_q",
                    "bootstrap_draws", "bootstrap_seed", "tail_level_floor", "b0_floor", "hac_lags")
    _EXTRA_PARAMS = ()
    #: Form the cluster sums with numpy (the production path); False selects the stdlib twin the tests pin.
    _numpy_sums = True

    # -- the hooks -------------------------------------------------------------------------

    @abstractmethod
    def _decision_calendar(self):
        """Return every decision date of the study's calendar, ISO text, strictly increasing."""
        raise NotImplementedError

    @abstractmethod
    def _decision_rows(self, t):
        """Return the rows decided at session ``t`` (one dict per option and side; see the module text).

        Reads only data dated at or before ``t``. The walk keeps the larger-``e`` side of an option.
        """
        raise NotImplementedError

    @abstractmethod
    def _fill_credits(self, rows):
        """Return, per settled row, its fill-session credit or why it has none.

        A credit in dollars, or one of ``session``, ``basis``, ``quote`` (the reason no usable fill
        quote exists).

        Each row also carries ``decision_date``. Called once per settlement date, only for rows
        already admitted, so every fill quote it reads is dated before the decision week.
        """
        raise NotImplementedError

    @abstractmethod
    def _settlement_levels(self, rows):
        """Return, per settled row, the settlement level of its underlying or why it has none.

        A level, or ``basis_changed`` or ``no_settlement_bar``. Called with the rows of
        :meth:`_fill_credits`, same order.
        """
        raise NotImplementedError

    @abstractmethod
    def _next_session(self, t):
        """Return the first session of the study's own calendar after decision session ``t`` (ISO text).

        Every row's ``fill_date`` must equal it; anything else refuses ``fill_not_next_session``.
        """
        raise NotImplementedError

    @abstractmethod
    def _input_pins(self):
        """Return the input pins (name -> sha256) the checkpoint must match.

        A child pins every input file its hooks read; a child with no input files returns ``{}``
        on purpose, never by omission.
        """
        raise NotImplementedError

    # -- params ----------------------------------------------------------------------------

    @classmethod
    def _allowed(cls):
        return tuple(cls._BASE_PARAMS) + tuple(cls._EXTRA_PARAMS)

    @classmethod
    def validate_params(cls, params):
        """Problems with the declared knobs, empty when none.

        Parameters
        ----------
        params : dict
            The node's ``params`` block.

        Returns
        -------
        list of str
            One message per problem.
        """
        problems = []
        reject_unknown_params(problems, params, cls._allowed())
        for key in _REQUIRED:
            if key not in params:
                problems.append(f"{key} is required")
        for key in ("window", "buckets", "regime", "pooling", "name_adjust"):
            if key in params and not isinstance(params[key], dict):
                problems.append(f"{key} must be an object, got {params[key]!r}")
        window = params.get("window")
        if isinstance(window, dict) and (set(window) != {"start", "end"}):
            problems.append(f"window must hold exactly start and end, got {sorted(window)}")
        if "out_dir" in params and (not isinstance(params["out_dir"], str) or not params["out_dir"]):
            problems.append(f"out_dir must be a non-empty path, got {params['out_dir']!r}")
        for key in ("half_life_weeks", "ram_gb_limit"):
            v = params.get(key, 1)
            if isinstance(v, bool) or not number_ok(v) or v <= 0:
                problems.append(f"{key} must be a number > 0, got {v!r}")
        every = params.get("checkpoint_every", _DEFAULTS["checkpoint_every"])
        if "direction_regressor" in params and not isinstance(params["direction_regressor"], bool):
            problems.append(f"direction_regressor must be true or false, got {params['direction_regressor']!r}")
        if isinstance(every, bool) or not isinstance(every, int) or every < 1:
            problems.append(f"checkpoint_every must be an integer >= 1, got {every!r}")
        return problems

    def _param(self, key):
        return self.params.get(key, _DEFAULTS.get(key))

    def _params_hash(self):
        """Hash every param that can move an output, defaults resolved (not ``window.end``, not the operational knobs)."""
        body = {k: self._param(k) for k in self._allowed()
                if k not in _OPERATIONAL and (k in self.params or k in _DEFAULTS)}
        body["window"] = {"start": self.params["window"]["start"]}
        return hashlib.sha256(_canon(body).encode()).hexdigest()

    # -- run -------------------------------------------------------------------------------

    def run(self, ctx, inputs):
        """Walk the window, resuming from a checkpoint in ``out_dir`` if one exists.

        Parameters
        ----------
        ctx : NodeContext
            Unused beyond the node contract.
        inputs : dict
            Unused (the hooks read the data).

        Returns
        -------
        dict
            ``{"summary": ...}``.

        Raises
        ------
        ValueError
            A coded refusal, see the class text.
        """
        self._setup()
        weeks = self._weeks()
        self._out = self.params["out_dir"]
        for sub in ("rows", "pending"):
            os.makedirs(os.path.join(self._out, sub), exist_ok=True)
        self._consumed = []
        self._manifest = {}
        self._shard_sha = {}
        cursor = self._resume(weeks)
        todo = [t for t in weeks if cursor is None or t > cursor]
        every = self._param("checkpoint_every")
        for k, t in enumerate(todo):
            self._week(t)
            if (k + 1) % every == 0 or k == len(todo) - 1:
                self._checkpoint(t)
        if todo == [] and cursor is not None:
            self._checkpoint(cursor)
        extras = self._finish()
        summary = self._summary(weeks)
        summary.update(extras)
        atomic_write(os.path.join(self._out, "summary.json"), (_canon(summary) + "\n").encode())
        return {"summary": summary}

    def _setup(self):
        """Validate everything that needs no data, before any hook runs."""
        p = self.params
        self._gate = SettledBefore(p.get("sealed_from"))
        self._gate.check_window([p["window"]["start"], p["window"]["end"], p["report_from"]])
        start, end, rep = (_iso(x, n) for x, n in ((p["window"]["start"], "window.start"),
                                                   (p["window"]["end"], "window.end"),
                                                   (p["report_from"], "report_from")))
        if end < start:
            raise _Refusal("calendar_mismatch", f"window.end {end} is before window.start {start}")
        if rep <= start:
            raise _Refusal("report_from_invalid", f"report_from {rep} is not after window.start {start}")
        self._start, self._end, self._report = start, end, rep
        self._check_knobs()
        self._buckets = ReliabilityBuckets(p["buckets"])
        self._pool = PartialPooling(p["pooling"])
        self._regime = TrailingRegime(p["regime"])
        lam = 2 ** (-1 / self._param("half_life_weeks"))
        self._blocks = list(self._param("time_block_weeks"))
        self._direction = bool(self._param("direction_regressor"))
        self._states, self._adjust = {}, {}
        for name in self._state_names():
            spec = _SPECS[name]
            decay = lam if spec.decayed else None
            self._states[name] = LinearEdgeState(24, decay, spec.k, len(self._blocks))
            if spec.adjust:
                self._adjust[name] = NameAdjustment(p["name_adjust"], decay=decay)
        self._twin = LinearEdgeState(24, None, 1, len(self._blocks))
        self._integ = {k: LinearEdgeState(24, None, 1, len(self._blocks))
                       for k in ("forward_w", "forward_b2") if self._direction or k == "forward_w"}
        self._warm = {}
        self._adv, self._q95, self._refs, self._sens = {}, {}, {}, {}
        self._open_weeks = set()
        self._fr = {}
        self._pending = {}
        self._counts = {}

    def _state_names(self):
        """Return the states this run holds, in order: all fourteen, or nine without ``direction_regressor``."""
        return tuple(k for k in _STATES if self._direction or not (k in _DIRECTION or k == "reported_dir"))

    def _check_knobs(self):
        """Refuse a bad knob with its stable code, before any hook runs."""
        p, get = self.params, self._param

        def bad(code, what):
            raise _Refusal(code, what)

        def num(v):
            return not isinstance(v, bool) and number_ok(v)

        def whole(v, low):
            return isinstance(v, int) and not isinstance(v, bool) and v >= low

        if get("edge_scale") not in _EDGE_SCALES:
            bad("edge_scale_invalid", f"edge_scale must be one of {list(_EDGE_SCALES)}, got {get('edge_scale')!r}")
        if get("row_weight") not in _ROW_WEIGHTS:
            bad("row_weight_invalid", f"row_weight must be one of {list(_ROW_WEIGHTS)}, got {get('row_weight')!r}")
        weeks = get("time_block_weeks")
        if (not isinstance(weeks, list) or not weeks or not all(whole(w, 1) for w in weeks)
                or any(b <= a for a, b in zip(weeks, weeks[1:]))):
            bad("time_block_invalid", f"time_block_weeks must be a strictly increasing list of integers >= 1, got {weeks!r}")
        pool = p["pooling"]
        if "band" in pool and pool["band"] not in PartialPooling._BANDS:
            bad("band_invalid", f"pooling.band must be one of {list(PartialPooling._BANDS)}, got {pool['band']!r}")
        if "min_blocks" in pool and not whole(pool["min_blocks"], 2):
            bad("min_blocks_invalid", f"pooling.min_blocks must be an integer >= 2, got {pool['min_blocks']!r}")
        if "ci_levels" in pool:
            lv = pool["ci_levels"]
            if not isinstance(lv, (list, tuple)) or not lv or not all(num(x) and 0.0 < x < 1.0 for x in lv):
                bad("ci_levels_invalid", f"pooling.ci_levels must be a non-empty list in (0, 1), got {lv!r}")
        for key, code in (("ci_level", "ci_level_invalid"), ("bh_q", "bh_q_invalid")):
            if not (num(get(key)) and 0.0 < get(key) < 1.0):
                bad(code, f"{key} must be a number in (0, 1), got {get(key)!r}")
        if not (num(get("min_fill_rate")) and 0.0 < get("min_fill_rate") <= 1.0):
            bad("min_fill_rate_invalid", f"min_fill_rate must be in (0, 1], got {get('min_fill_rate')!r}")
        if not whole(get("min_fill_sample"), 2):
            bad("min_fill_sample_invalid", f"min_fill_sample must be an integer >= 2, got {get('min_fill_sample')!r}")
        if not whole(get("bootstrap_draws"), 99) or not whole(get("bootstrap_seed"), -(2 ** 63)):
            bad("bootstrap_invalid", "bootstrap_draws must be an integer >= 99 and bootstrap_seed an integer")
        for key in ("tail_level_floor", "b0_floor"):
            if not (num(get(key)) and 0.0 < get(key) <= 1.0):
                bad(f"{key}_invalid", f"{key} must be a number in (0, 1], got {get(key)!r}")
        if not whole(get("hac_lags"), 0):
            bad("hac_lags_invalid", f"hac_lags must be an integer >= 0, got {get('hac_lags')!r}")

    def _weeks(self):
        """Read the calendar and return the decision dates inside the window."""
        cal = list(self._decision_calendar())
        days = [_iso(d, "calendar date") for d in cal]
        if any(b <= a for a, b in zip(days, days[1:])):
            raise _Refusal("calendar_mismatch", "the decision calendar is not strictly increasing")
        self._following = dict(zip(days, days[1:]))
        weeks = [d for d in days if self._start <= d <= self._end]
        if not weeks:
            raise _Refusal("calendar_mismatch", "no decision date in the window")
        self._gate.check_window(weeks)
        return weeks

    def _count(self, code, n=1):
        self._counts[code] = self._counts.get(code, 0) + n

    # -- checkpoint ------------------------------------------------------------------------

    def _resume(self, weeks):
        """Load a matching checkpoint and return its cursor, or ``None`` for a fresh run."""
        path = os.path.join(self._out, "checkpoint.json")
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            blob = json.load(fh)
        if blob.get("version") != _VERSION:
            raise _Refusal("checkpoint_mismatch", f"the checkpoint is version {blob.get('version')!r}, "
                           f"this build writes version {_VERSION}")
        try:
            return self._resume_from(blob, weeks)
        except (KeyError, TypeError) as exc:
            raise _Refusal("checkpoint_mismatch", f"the checkpoint manifest is malformed: {exc!r}") from exc

    def _resume_from(self, blob, weeks):
        """Verify the manifest against this run and the files on disk, load the state, return the cursor."""
        pins = self._input_pins()
        if blob["params_hash"] != self._params_hash() or blob["pins"] != json.loads(_canon(pins)):
            raise _Refusal("checkpoint_mismatch", "params or input pins differ from the checkpoint's")
        cursor = blob["cursor"]
        if cursor > weeks[-1]:
            raise _Refusal("state_from_future", f"the checkpoint is at {cursor}, after the window end {weeks[-1]}")
        if cursor not in weeks:
            raise _Refusal("calendar_mismatch", f"checkpoint cursor {cursor} is not a decision date")
        path = os.path.join(self._out, blob["npz"]["file"])
        if not os.path.exists(path):
            raise _Refusal("checkpoint_mismatch", f"checkpoint arrays {blob['npz']['file']} are missing")
        with open(path, "rb") as fh:
            raw = fh.read()
        if _sha(raw) != blob["npz"]["sha256"]:
            raise _Refusal("checkpoint_mismatch", "the checkpoint arrays' sha256 differs from the manifest's")
        tree = _npz_tree(raw)
        if sorted(tree["states"]) != sorted(self._states):
            raise _Refusal("checkpoint_mismatch", "the state layout differs from the checkpoint's")
        for name, rec in tree["states"].items():
            self._states[name] = LinearEdgeState.from_dict(rec)
        for name, rec in tree["adjust"].items():
            self._adjust[name] = NameAdjustment.from_dict(rec)
        self._regime = TrailingRegime.from_dict(tree["regime"])
        self._integ = {k: LinearEdgeState.from_dict(tree["integ"][k]) for k in self._integ}
        self._warm = tree["warm"]
        sens = tree["sens"]
        self._twin = LinearEdgeState.from_dict(sens["twin"])
        self._adv, self._q95, self._refs = sens["adv"], sens["q95"], sens["refs"]
        self._sens, self._open_weeks = sens["index"], set(sens["open"])
        self._fr = sens["fill_rate"]
        for s, names in sorted(self._sens.items()):
            for name, entry in sorted(names.items()):
                self._verify(os.path.join(self._out, "sensitivity", s, f"{name}.json"), entry["sha"],
                             f"sensitivity shard {s}/{name}")
        self._pending = {s: sorted(ts) for s, ts in blob["pending"].items()}
        self._shard_sha = {(s, t): sha for s, ts in blob["pending"].items() for t, sha in ts.items()}
        self._counts = dict(blob["counts"])
        self._manifest = dict(blob["manifest"])
        for rel, sha in sorted(self._manifest.items()):
            self._verify(os.path.join(self._out, rel), sha, f"written file {rel}")
        for (s, t), sha in sorted(self._shard_sha.items()):
            self._verify(self._shard(s, t), sha, f"pending shard {s}/{t}")
        return cursor

    @staticmethod
    def _verify(path, sha, what):
        """Refuse ``checkpoint_mismatch`` when ``path`` is missing or its content hash differs."""
        if not os.path.exists(path):
            raise _Refusal("checkpoint_mismatch", f"{what} is missing")
        with open(path, "rb") as fh:
            if _sha(fh.read()) != sha:
                raise _Refusal("checkpoint_mismatch", f"{what} differs in content from the manifest's hash")

    def _checkpoint(self, cursor):
        """Write the one checkpoint atomically (arrays, then manifest), then drop what it supersedes."""
        tree = {"states": {k: st.to_dict() for k, st in self._states.items()},
                "adjust": {k: a.to_dict() for k, a in self._adjust.items()}, "regime": self._regime.to_dict(),
                "integ": {k: st.to_dict() for k, st in self._integ.items()}, "warm": self._warm,
                "sens": {"twin": self._twin.to_dict(), "adv": self._adv, "q95": self._q95, "refs": self._refs,
                         "index": self._sens, "open": sorted(self._open_weeks), "fill_rate": self._fr}}
        raw = _npz_bytes(tree)
        name = f"checkpoint-{cursor}.npz"
        atomic_write(os.path.join(self._out, name), raw)
        blob = {
            "version": _VERSION, "params_hash": self._params_hash(), "pins": self._input_pins(),
            "cursor": cursor, "npz": {"file": name, "sha256": _sha(raw)},
            "pending": {s: {t: self._shard_sha[(s, t)] for t in ts} for s, ts in self._pending.items()},
            "counts": self._counts, "manifest": dict(sorted(self._manifest.items())),
        }
        atomic_write(os.path.join(self._out, "checkpoint.json"), (_canon(blob) + "\n").encode())
        for old in os.listdir(self._out):
            if old.startswith("checkpoint-") and old.endswith(".npz") and old != name:
                os.unlink(os.path.join(self._out, old))
        for path in self._consumed:
            if os.path.exists(path):
                os.unlink(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass
        self._consumed = []

    def _write(self, rel, raw):
        """Write a map or row file atomically and record its content hash in the manifest."""
        atomic_write(os.path.join(self._out, rel), raw)
        self._manifest[rel] = _sha(raw)

    def _shard(self, s, t):
        return os.path.join(self._out, "pending", s, f"{t}.json")

    def _check_ram(self):
        """Refuse ``ram_limit`` when the resident set exceeds ``ram_gb_limit``."""
        gb = self._rss_gb()
        limit = self._param("ram_gb_limit")
        if gb > limit:
            raise _Refusal("ram_limit", f"resident memory {gb:.3f} GB exceeds ram_gb_limit {limit}")

    @staticmethod
    def _rss_gb():
        """Return the resident set of this process in GB (the peak where the current is unavailable)."""
        try:
            with open("/proc/self/statm") as fh:
                return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e9
        except (OSError, ValueError, IndexError):
            import resource
            import sys

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return peak / (1e9 if sys.platform == "darwin" else 1e6)

    # -- one week --------------------------------------------------------------------------

    def _week(self, t):
        """Age the states, settle, snapshot, then decide, in that order."""
        for st in (*self._states.values(), self._twin, *self._integ.values()):
            st.advance(t)
        for adj in self._adjust.values():
            adj.advance(t)
        self._settle(t)
        self._check_ram()
        maps = {k: EdgeCalibrationMap.build(t, self._states[k], self._pool, self._adjust[k]) for k in _PRIMARY}
        if self._pool.band == "two_block":
            pooled = self._states["expanding_fill"].fit(None)
            if pooled["rows"] > 0 and pooled["g_eff_blocks"][0] <= 1.0:
                self._count("two_block_single_block")
        fits = {k: EdgeCalibrationMap.build(t, self._states[k], self._pool, None)
                for k in _DIRECTION if k in self._states}
        body = {"t": t, "states": {k: m.to_dict() for k, m in maps.items()}}
        if fits:
            body["direction"] = {k: m.to_dict() for k, m in fits.items()}
            body["origin_two"] = origin_two(self._pool, self._states["dir_expanding_fill"],
                                            fits["dir_expanding_fill"].warm)
        counts = self._warm.setdefault(self._quarter(t), {})
        for k, m in (*maps.items(), *fits.items()):
            counts[k] = counts.get(k, 0) + bool(m.warm)
        self._decide(t, maps, fits, body)

    def _settle(self, t):
        """Join and fold every pending shard whose settlement date is strictly before ``t``."""
        for s in sorted(s for s in self._pending if self._gate.admits(s, t)):
            rows = []
            for dt in sorted(self._pending[s]):
                with open(self._shard(s, dt)) as fh:
                    rows.extend([dt, *r] for r in json.load(fh)["rows"])
                self._consumed.append(self._shard(s, dt))
            self._admit(t, s, rows)
            for dt in self._pending.pop(s):
                self._shard_sha.pop((s, dt), None)
        self._complete_weeks()
        self._flush_sensitivity(t)

    @staticmethod
    def _fill_answer(fill):
        """Split a fill hook answer into ``(credit or code, fill mid or None)``."""
        if isinstance(fill, (tuple, list)):
            if len(fill) != 2:
                raise ValueError(f"a fill answer is a credit, a code, or (credit, fill mid), got {fill!r}")
            return fill[0], fill[1]
        return fill, None

    def _admit(self, t, s, rows):
        """Join one settlement date's rows to their fill quote and settlement level, then fold clusters."""
        views = [{"group": r[_GROUP], "name": r[_NAME], "expiry": r[_EXPIRY], "settle_date": s,
                  "fill_date": r[_FILL], "factor": r[_FACTOR], "right": r[_RIGHT],
                  "strike": r[_STRIKE], "sign": r[_SIGN], "multiplier": r[_MULT], "decision_date": r[0]} for r in rows]
        fills = list(self._fill_credits(views))
        levels = list(self._settlement_levels(views))
        if len(fills) != len(rows) or len(levels) != len(rows):
            raise ValueError("the fill and settlement hooks must return one answer per row")
        self._check_ram()
        self._count("rows_admitted", len(rows))
        clusters, out, sens = {}, [], {}
        for r, answer, level in zip(rows, fills, levels):
            fill, mid_f = self._fill_answer(answer)
            code = None
            if isinstance(fill, str):
                if fill not in _FILL_CODES:
                    raise ValueError(f"unknown fill code {fill!r}; allowed {list(_FILL_CODES)}")
                code = f"no_fill_quote.{fill}"
                self._count("no_fill_quote")
            elif isinstance(level, str):
                if level not in _LEVEL_CODES:
                    raise ValueError(f"unknown settlement code {level!r}; allowed {list(_LEVEL_CODES)}")
                code = level
            if code is None and (isinstance(fill, bool) or not number_ok(fill)
                                 or isinstance(level, bool) or not number_ok(level)):
                raise ValueError(f"fill credit and settlement level must be finite numbers, got {fill!r}, {level!r}")
            intrinsic = None
            if not isinstance(level, str):
                strike = r[_STRIKE]
                payoff = max(level - strike, 0.0) if r[_RIGHT][:1].lower() == "c" else max(strike - level, 0.0)
                intrinsic = r[_SIGN] * payoff * r[_MULT]
            self._fill_row(r[_T], r[_BUCKET], r[_DEC], r[_GSIZE], self._status(code))
            if code is not None:
                self._count(code)
                out.append((r, code, None, None, None, intrinsic))
                if code in ("no_fill_quote.quote", "no_fill_quote.size") and intrinsic is None:
                    self._count("fill_unimputable")
                if code in ("no_fill_quote.quote", "no_fill_quote.size") and intrinsic is not None:
                    sens.setdefault(r[_NAME], []).append([r[_T], r[_BUCKET], r[_E], r[_SCALE], r[_BASE], r[_SIGN],
                                                         bool(r[_TOP]), intrinsic, code[-4:], None, r[_MID],
                                                         r[_FEE], r[_MULT], r[_FILL]])
                continue
            cell = (r, None, intrinsic + fill, intrinsic + r[_CREDIT], mid_f, intrinsic)
            out.append(cell)
            clusters.setdefault(r[_NAME], []).append(cell)
            if mid_f is not None and r[_MID] is not None and r[_MID] > 0:
                adv = self._adv.setdefault(r[_T], {"b": [], "v": []})
                adv["b"].append(r[_BUCKET])
                adv["v"].append(max(0.0, r[_SIGN] * (float(mid_f) - r[_MID]) / r[_MID]))
            sens.setdefault(r[_NAME], []).append([r[_T], r[_BUCKET], r[_E], r[_SCALE], r[_BASE], r[_SIGN],
                                                 bool(r[_TOP]), intrinsic, "fill", fill, r[_MID], r[_FEE], r[_MULT], r[_FILL]])
        for name in sorted(clusters):
            self._fold(t, s, name, clusters[name])
        self._buffer(s, sens)
        self._write_rows(s, out)

    # -- fill-rate diagnostics (D2 ii) -------------------------------------------------------

    _FR_FIELDS = ("decided", "filled", "quote", "size", "session", "basis", "other")

    @staticmethod
    def _status(code):
        """Map an admission code to a fill-rate field (``filled`` when there is none)."""
        if code is None:
            return "filled"
        return {"no_fill_quote.quote": "quote", "no_fill_quote.size": "size",
                "no_fill_quote.basis": "basis"}.get(code, "other")

    @staticmethod
    def _quarter(day):
        return f"{day[:4]}Q{(int(day[5:7]) - 1) // 3 + 1}"

    def _fill_row(self, t, bucket, decile, gsize, status):
        """Count one row under its bucket, within-week decile, decision quarter and group size (and reported)."""
        keys = [f"bucket:{bucket}", f"decile:{decile}", f"quarter:{self._quarter(t)}", f"group_size:{gsize}"]
        if t >= self._report:
            keys.append("reported:all")
            if decile == 9:
                keys.append("reported:top")
        for key in keys:
            rec = self._fr.setdefault(key, dict.fromkeys(self._FR_FIELDS, 0))
            if status == "session":
                rec["decided"] += 1
            rec[status] += 1

    def _fill_rates(self):
        """Return ``(overall, top, ok)``: the reported fill rates and whether both reach ``min_fill_rate``."""
        def rate(key):
            rec = self._fr.get(key)
            if rec is None:
                return None
            den = rec["filled"] + rec["quote"] + rec["size"]
            return rec["filled"] / den if den else None

        overall, top = rate("reported:all"), rate("reported:top")
        floor = self._param("min_fill_rate")
        return overall, top, overall is not None and top is not None and overall >= floor and top >= floor

    # -- the fill sensitivity (D2) ----------------------------------------------------------

    def _buffer(self, s, sens):
        """Write the sensitivity buffer shard of each ``(name, s)`` cluster; it waits for its decision weeks."""
        for name in sorted(sens):
            rows = sens[name]
            raw = _canon({"rows": rows}).encode()
            rel = f"sensitivity/{s}/{name}.json"
            os.makedirs(os.path.join(self._out, "sensitivity", s), exist_ok=True)
            atomic_write(os.path.join(self._out, rel), raw)
            self._sens.setdefault(s, {})[name] = {"sha": _sha(raw), "weeks": sorted({r[0] for r in rows})}
            self._count("imputation_pending")

    @staticmethod
    def _freeze_q95(buckets, values, min_sample):
        """Return one decision week's ``q95_bw``: a value per bucket with ``min_sample`` filled rows, and the pooled one.

        Each value is the 95th percentile (``stats.sample_quantile``) of the adverse relative mid move.
        """
        from dskit.pipeline.stats import sample_quantile

        by = {}
        for b, v in zip(buckets, values):
            by.setdefault(int(b), []).append(v)
        return {"pool": sample_quantile(list(values), 0.95) if values else 0.0,
                "b": {str(b): sample_quantile(v, 0.95) for b, v in sorted(by.items()) if len(v) >= min_sample}}

    @staticmethod
    def _imputed_credit(sign, mid, q95, multiplier, fee):
        """Return the credit of an unfilled row booked at its decision mid moved against the trade by ``q95``."""
        fill = mid * (1.0 + q95) if sign > 0 else max(0.0, mid * (1.0 - q95))
        return -sign * fill * multiplier - fee

    def _complete_weeks(self):
        """Freeze ``q95_bw`` for every decision week whose last row has been admitted (once, stored)."""
        waiting = {t for ts in self._pending.values() for t in ts}
        for t in sorted(self._open_weeks):
            if t in waiting:
                continue
            adv = self._adv.pop(t, {"b": [], "v": []})
            self._q95[t] = self._freeze_q95(adv["b"], adv["v"], self._param("min_fill_sample"))
            self._refs[t] = sum(1 for names in self._sens.values() for e in names.values() if t in e["weeks"])
            if self._refs[t] == 0:
                del self._q95[t], self._refs[t]
            self._open_weeks.remove(t)

    def _flush_sensitivity(self, t):
        """Admit, whole and once, every buffered cluster whose contributing decision weeks are all complete."""
        for s in sorted(self._sens):
            for name in sorted(self._sens[s]):
                entry = self._sens[s][name]
                if all(w in self._q95 for w in entry["weeks"]):
                    self._admit_sensitivity(t, s, name, entry)
                    del self._sens[s][name]
            if not self._sens[s]:
                del self._sens[s]

    def _admit_sensitivity(self, t, s, name, entry):
        """Book one buffered cluster at its frozen ``q95`` and feed the sensitivity states and the common twin."""
        import numpy as np

        path = os.path.join(self._out, "sensitivity", s, f"{name}.json")
        self._verify(path, entry["sha"], f"sensitivity shard {s}/{name}")
        with open(path) as fh:
            rows = json.load(fh)["rows"]
        self._consumed.append(path)
        credit, keep = [], []
        for r in rows:
            wk = r[0]
            if r[8] == "fill":
                credit.append(r[9])
                keep.append(True)
                continue
            if r[10] is None:
                self._count("fill_unimputable")
                credit.append(0.0)
                keep.append(False)
                continue
            q = self._q95[wk]["b"].get(str(r[1]))
            if q is None:
                q = self._q95[wk]["pool"]
                self._count("q95_pooled")
            self._count("fill_imputed")
            credit.append(self._imputed_credit(r[5], r[10], q, r[12], r[11]))
            keep.append(True)
        for wk in entry["weeks"]:
            self._refs[wk] -= 1
            if self._refs[wk] == 0:
                del self._refs[wk], self._q95[wk]
        group_equal = self._param("row_weight") == "group_equal"
        keep = np.array(keep)
        n = len(rows)
        filled = np.array([r[8] == "fill" for r in rows])
        col = {
            "t": np.array([r[0] >= self._report for r in rows]), "e": np.array([r[2] for r in rows], float),
            "scale": np.array([r[3] for r in rows], float),
            "w": np.array([r[4] for r in rows], float) if group_equal else np.ones(n),
            "bucket": np.array([r[1] for r in rows], int), "side": np.array([r[5] for r in rows], int),
            "top": np.array(([r[6] for r in rows]), bool),
            "fill": np.array([r[7] + c for r, c in zip(rows, credit)], float), "dec": np.zeros(n),
            "edir": np.zeros(n), "sig": np.full(n, np.nan), "cb0": np.zeros(n), "cbs": [None] * n,
            "fdate": [r[13] for r in rows],
        }
        blocks = [block_index(s, self._start, w) for w in self._blocks]
        targets = [("fill_sensitivity", self._states["fill_sensitivity"], _SPECS["fill_sensitivity"], keep),
                   ("reported_sensitivity", self._states["reported_sensitivity"],
                    _SPECS["reported_sensitivity"], keep & col["t"]),
                   ("reported_fill_common", self._twin, _SPECS["reported_fill"], keep & col["t"] & filled)]
        self._feed(np, t, s, name, blocks, col, targets)

    def _takes(self, spec, row):
        """Whether a state with ``spec`` holds this settled row (the row sets of D7)."""
        if spec.rows == "all":
            return True
        rep = row[_T] >= self._report
        return rep if spec.rows == "reported" else rep and not row[_WARM]

    def _fold(self, t, s, name, cells):
        """Fold one whole ``(name, s)`` cluster into every state that takes rows of it, and the name sums.

        The per-unit moment vectors are formed with numpy (``np.add.at`` by bucket and side, no Python row
        loop over the states) and handed to :meth:`LinearEdgeState.add_cluster_sums`; ``_numpy_sums = False``
        selects the stdlib twin :meth:`_fold_lists`, which a test pins equal to 1e-12.
        """
        if not self._numpy_sums:
            return self._fold_lists(t, s, name, cells)
        import numpy as np

        blocks = [block_index(s, self._start, w) for w in self._blocks]
        group_equal = self._param("row_weight") == "group_equal"
        rows = [c[0] for c in cells]
        col = {
            "t": np.array([r[_T] >= self._report for r in rows]), "warm": np.array([bool(r[_WARM]) for r in rows]),
            "has_dir": np.array([r[_EDIR] is not None for r in rows]),
            "e": np.array([r[_E] for r in rows], float), "scale": np.array([r[_SCALE] for r in rows], float),
            "edir": np.array([0.0 if r[_EDIR] is None else r[_EDIR] for r in rows], float),
            "sig": np.array([np.nan if r[_SIGMA2] is None else r[_SIGMA2] for r in rows], float),
            "w": np.array([r[_BASE] for r in rows], float) if group_equal else np.ones(len(rows)),
            "bucket": np.array([r[_BUCKET] for r in rows], int), "side": np.array([r[_SIGN] for r in rows], int),
            "top": np.array([bool(r[_TOP]) for r in rows]), "cb0": np.array([r[_CB][0] for r in rows], float),
            "cw": np.array([r[_CW] for r in rows], float),
            "c2w": np.array([np.nan if r[_C2W] is None else r[_C2W] for r in rows], float),
            "fill": np.array([c[2] for c in cells], float), "dec": np.array([c[3] for c in cells], float),
            "fdate": [r[_FILL] for r in rows],
        }
        col["cbs"] = [r[_CB] for r in rows]
        targets = []
        for key, state in (*self._states.items(), *self._integ.items()):
            spec = _SPECS[key]
            if spec.rows in ("sens", "sens_reported"):
                continue
            use = np.ones(len(rows), bool) if spec.rows == "all" else col["t"].copy()
            if spec.rows == "forward":
                use &= ~col["warm"]
            if spec.k == 2 or key == "forward_b2":
                use &= col["has_dir"]
            targets.append((key, state, spec, use))
        self._feed(np, t, s, name, blocks, col, targets)

    def _feed(self, np, t, s, name, blocks, col, targets):
        """Hand each target state its rows' per-unit sums (and the name sums of a primary state)."""
        group_equal = self._param("row_weight") == "group_equal"
        for key, state, spec, use in targets:
            if not use.any():
                continue
            sc = np.ones(int(use.sum())) if key == "dollar" else col["scale"][use]
            e = col["e"][use] / sc
            y = (col["dec"] if spec.price == "decision" else col["fill"])[use] / sc
            if key in _FORWARD_COLUMN:
                e = col[_FORWARD_COLUMN[key]][use]
            w = col["w"][use]
            d = col["edir"][use] / sc if spec.k == 2 else None
            sig = col["sig"][use] if spec.k == 2 else None
            sums = self._unit_sums(np, spec, e, y, w, d, sig, col["bucket"][use], col["side"][use], col["top"][use])
            if sig is not None:
                state.counts["sigma2_missing"] += int(np.isnan(sig).sum())
            fdate = max(f for f, u in zip(col["fdate"], use) if u)
            state.add_cluster_sums(t, name, s, fdate, blocks, sums)
            if key in self._adjust:
                c = [b[_PRIMARY.index(key)] for b, u in zip(col["cbs"], use) if u]
                self._adjust[key].add_cluster(t, name, s, e.tolist(), y.tolist(), c, w.tolist() if group_equal else None)

    @staticmethod
    def _unit_sums(np, spec, e, y, w, d, sig, bucket, side, top):
        """Return ``{unit: sums}`` for ``add_cluster_sums`` from one state's rows of a cluster (numpy)."""
        z = [e] if spec.k == 1 else [d, e - d]
        cols = [w] + [w * v for v in z]
        cols += [w * z[i] * z[j] for i in range(spec.k) for j in range(i, spec.k)]
        cols += [w * y] + [w * v * y for v in z]
        cols += [w * y * y, w * np.nan_to_num(sig) if sig is not None else np.zeros_like(w), w * w,
                 np.ones_like(w), w]
        m = np.stack(cols, axis=1)
        p = 5 if spec.k == 1 else 9
        acc = np.zeros((24, m.shape[1]))
        np.add.at(acc, bucket, m)
        cnt = np.bincount(bucket, minlength=24)
        sd = np.zeros((2, m.shape[1]))
        np.add.at(sd, (side < 0).astype(int), m)
        units = {None: m.sum(axis=0)}
        counts = {None: len(w)}
        for b in np.flatnonzero(cnt):
            units[int(b)], counts[int(b)] = acc[b], int(cnt[b])
        for name, idx, mask in (("buy", 0, side > 0), ("sell", 1, side < 0)):
            if mask.any():
                units[name], counts[name] = sd[idx], int(mask.sum())
        if spec.top and top.any():
            units["top"], counts["top"] = m[top].sum(axis=0), int(top.sum())
        # LinearEdgeState layout: k=1 (N, E, R, EE, ER); k=2 (N, D, S, DD, DS, SS, R, DR, SR)
        pick = [0, 1, 3, 2, 4] if spec.k == 1 else list(range(9))
        out = {}
        for key, v in units.items():
            v = [float(x) for x in v]
            out[key] = {"vec": [v[i] for i in pick], "rr": v[p], "serr": v[p + 1], "rw2": v[p + 2],
                        "rows": counts[key], "groups": v[p + 4]}
        return out

    def _fold_lists(self, t, s, name, cells):
        """Fold one cluster through ``add_cluster`` (row lists, stdlib): the twin the numpy path is pinned against."""
        blocks = [block_index(s, self._start, w) for w in self._blocks]
        group_equal = self._param("row_weight") == "group_equal"
        for key, state in self._states.items():
            spec = _SPECS[key]
            if spec.rows in ("sens", "sens_reported"):
                continue
            use = [c for c in cells if self._takes(spec, c[0])
                   and (spec.k == 1 or c[0][_EDIR] is not None)]
            if not use:
                continue
            dollar = key == "dollar"
            sc = [1.0 if dollar else c[0][_SCALE] for c in use]
            idx = 3 if spec.price == "decision" else 2
            e = [c[0][_E] / f for c, f in zip(use, sc)]
            y = [c[idx] / f for c, f in zip(use, sc)]
            if key == "forward":
                e = [c[0][_CB][0] for c in use]
            weights = [c[0][_BASE] for c in use] if group_equal else None
            e_dir = [c[0][_EDIR] / f for c, f in zip(use, sc)] if spec.k == 2 else None
            sigma2 = [c[0][_SIGMA2] for c in use] if spec.k == 2 else None
            top = [bool(c[0][_TOP]) for c in use] if spec.top else None
            state.add_cluster(t, name, s, max(c[0][_FILL] for c in use), [c[0][_BUCKET] for c in use], e, y,
                              blocks, e_dir=e_dir, weights=weights, sides=[c[0][_SIGN] for c in use], top=top,
                              sigma2=sigma2)
            if key in self._adjust:
                self._adjust[key].add_cluster(t, name, s, e, y, [c[0][_CB][_PRIMARY.index(key)] for c in use],
                                              weights)

    def _write_rows(self, s, cells):
        """Write the reported rows of one settlement date (decided at or after ``report_from``)."""
        keep = sorted((c for c in cells if c[0][_T] >= self._report),
                      key=lambda c: (c[0][_T], c[0][_NAME], c[0][_EXPIRY], c[0][_RIGHT], c[0][_STRIKE]))
        if not keep:
            return
        lines = [_canon({"t": r[_T], "name": r[_NAME], "expiry": r[_EXPIRY], "right": r[_RIGHT],
                         "strike": r[_STRIKE], "sign": r[_SIGN], "bucket": r[_BUCKET], "e": r[_E],
                         "c": r[_CF][0], "r": rf, "r_dec": rd, "code": code, "scale": r[_SCALE], "w": r[_BASE],
                         "e_dir": r[_EDIR], "top": r[_TOP], "map_warmup": r[_WARM], "c_bucket": r[_CB][0],
                         "c2": None if r[_C2] is None else r[_C2][0], "cw": r[_CW], "c2w": r[_C2W]})
                 for r, code, rf, rd, _, __ in keep]
        self._count("rows_reported", len(lines))
        self._write(f"rows/rows-{s}.jsonl", ("\n".join(lines) + "\n").encode())

    # -- decide ----------------------------------------------------------------------------

    def _row(self, raw, t, nxt):
        """Validate one hook row (its fill session must be ``nxt``, the next session) and return it as a clean dict."""
        if not isinstance(raw, dict) or any(k not in raw for k in _ROW_KEYS):
            raise ValueError(f"a decision row needs {list(_ROW_KEYS)}, got {raw!r}")
        row = dict(raw)
        for key in ("expiry", "settle_date", "fill_date"):
            row[key] = _iso(row[key], key)
        for key in ("name", "group", "right"):
            if not isinstance(row[key], str) or not row[key]:
                raise ValueError(f"{key} must be a non-empty string, got {row[key]!r}")
        if row["right"][:1].lower() not in ("c", "p"):
            raise ValueError(f"right must be a call or a put, got {row['right']!r}")
        if row["sign"] not in (1, -1):
            raise ValueError(f"sign must be 1 or -1, got {row['sign']!r}")
        for key in ("factor", "strike", "e", "dte", "abs_z", "spread", "v", "credit", "multiplier"):
            if isinstance(row[key], bool) or not number_ok(row[key]):
                raise ValueError(f"{key} must be a finite number, got {row[key]!r}")
        if self._param("edge_scale") == "none":
            row["scale"] = 1.0
        else:
            sc = row.get("scale")
            if isinstance(sc, bool) or not number_ok(sc) or not sc > 0:
                raise ValueError(f"scale must be a finite number > 0 under edge_scale {self._param('edge_scale')}, got {sc!r}")
        row.setdefault("e_dir", None)
        row.setdefault("direction_code", None)
        row.setdefault("sigma2", None)
        row.setdefault("mid", None)
        row.setdefault("fee", 0.0)
        row.setdefault("cdf_repaired", False)
        if self._direction:
            if row["e_dir"] is None:
                if row["direction_code"] not in _DIRECTION_CODES:
                    raise ValueError(f"a row without e_dir needs direction_code in {list(_DIRECTION_CODES)}, "
                                     f"got {row['direction_code']!r}")
            elif isinstance(row["e_dir"], bool) or not number_ok(row["e_dir"]):
                raise ValueError(f"e_dir must be a finite number or None, got {row['e_dir']!r}")
        for key in ("mid", "sigma2"):
            v = row[key]
            if v is not None and (isinstance(v, bool) or not number_ok(v) or v < 0):
                raise ValueError(f"{key} must be None or a finite number >= 0, got {v!r}")
        if isinstance(row["fee"], bool) or not number_ok(row["fee"]):
            raise ValueError(f"fee must be a finite number, got {row['fee']!r}")
        if row["fill_date"] <= t:
            raise _Refusal("calendar_mismatch", f"fill session {row['fill_date']} is not after the decision {t}")
        if row["fill_date"] != nxt:
            raise _Refusal("fill_not_next_session", f"fill session {row['fill_date']} is not the next session {nxt} after {t}")
        return row

    def _decide(self, t, maps, fits, body):
        """Bucket and map the day's rows, then store them in shards keyed by settlement date."""
        nxt = _iso(self._next_session(t), "next session")
        following = self._following.get(t)
        if not t < nxt or (following is not None and nxt > following):
            raise _Refusal("calendar_mismatch", f"the next session {nxt} after {t} must lie after {t} and not after "
                           f"the next decision date {following}")
        self._check_ram()
        raw = self._decision_rows(t)
        self._check_ram()
        best = {}
        for item in raw:
            row = self._row(item, t, nxt)
            key = (row["name"], row["expiry"], row["right"], row["strike"])
            old = best.get(key)
            if old is None:
                best[key] = row
                continue
            self._count("side_duplicate")
            if (row["e"], row["sign"]) > (old["e"], old["sign"]):
                best[key] = row
        del raw
        vols = {}
        for key in sorted(best):
            row = best[key]
            if vols.setdefault(row["name"], row["v"]) != row["v"]:
                raise ValueError(f"{row['name']}: v differs within one decision session")
        regimes = {n: self._regime.of(n, t, v) for n, v in sorted(vols.items())}
        body["regimes"] = {n: list(x) for n, x in regimes.items()}
        self._write(f"map-{t}.json", (_canon(body) + "\n").encode())
        sizes = {}
        for row in best.values():
            sizes[row["group"]] = sizes.get(row["group"], 0) + 1
        kept, ranked = [], []
        for key in sorted(best):
            row = best[key]
            if row["settle_date"] >= self._gate.sealed_from:
                self._count("beyond_seal")
                continue
            ranked.append(key)
            if row["fill_date"] >= row["settle_date"]:
                self._count("no_fill_quote")
                self._count("no_fill_quote.session")
                continue
            kept.append(key)
        order = sorted(ranked, key=lambda k: (best[k]["e"], k))
        decile = {k: min(9, (10 * i) // len(order)) for i, k in enumerate(order)}
        kept_set = set(kept)
        for key in ranked:
            if key in kept_set:
                continue
            row = best[key]
            b = self._buckets.of(row["dte"], row["abs_z"], row["spread"], regimes[row["name"]][0])
            self._fill_row(t, b, decile[key], sizes[row["group"]], "session")
        shards = {}
        warm = maps["expanding_fill"]
        group_equal = self._param("row_weight") == "group_equal"
        for key in kept:
            row = best[key]
            regime, cold = regimes[row["name"]]
            if cold:
                self._count("regime_warmup")
            b = self._buckets.of(row["dte"], row["abs_z"], row["spread"], regime)
            if warm.warm and warm.buckets[b].pooled:
                self._count("pooled_bucket")
            e, name, sc = row["e"] / row["scale"], row["name"], row["scale"]
            cb = [maps[k].bucket_only(b, e) for k in _PRIMARY]
            cf = [maps[k].calibrated(b, name, e) for k in _PRIMARY]
            c2 = None
            if self._direction:
                if row["e_dir"] is None:
                    self._count("no_direction_edge")
                    self._count(f"no_direction_edge.{row['direction_code']}")
                else:
                    d = row["e_dir"] / sc
                    c2 = []
                    for k in _DIRECTION:
                        c2.append(fits[k].bucket_only_two(b, d, e - d))
            cw = maps["expanding_fill"].origin(b, e)
            c2w = None
            if c2 is not None:
                wb = body["origin_two"]["buckets"][b]["w"]
                c2w = wb[0] * d + wb[1] * (e - d)
            shards.setdefault(row["settle_date"], []).append(
                [row["group"], name, row["expiry"], row["fill_date"], row["factor"], row["right"], row["strike"],
                 row["sign"], row["e"], b, row["credit"], row["multiplier"], cb, cf, row["e_dir"],
                 row["direction_code"], row["sigma2"], sc, 1.0 / sizes[row["group"]] if group_equal else 1.0,
                 decile[key] == 9, bool(row["cdf_repaired"]), not warm.warm, row["mid"], row["fee"], c2,
                 decile[key], sizes[row["group"]], cw, c2w])
            self._count("rows_decided")
            self._fill_row(t, b, decile[key], sizes[row["group"]], "decided")
        self._check_ram()
        for name, v in vols.items():
            self._regime.add(name, t, v)
        if shards:
            self._open_weeks.add(t)
        for s, rows in shards.items():
            os.makedirs(os.path.join(self._out, "pending", s), exist_ok=True)
            raw = _canon({"rows": rows}).encode()
            atomic_write(self._shard(s, t), raw)
            self._shard_sha[(s, t)] = _sha(raw)
            self._pending[s] = sorted({*self._pending.get(s, []), t})

    # -- reads and tables -------------------------------------------------------------------

    def _quarter_block(self):
        """Return the index of the quarter (13-week) block length, else the last one."""
        return self._blocks.index(13) if 13 in self._blocks else len(self._blocks) - 1

    def _safe_read(self, state, ok):
        """Run read 1 on a state; a statistic that cannot be formed leaves the read unresolved."""
        try:
            return read_one(state, self._quarter_block(), self._param("ci_level"), self._param("bootstrap_draws"),
                            self._param("bootstrap_seed"), self._param("b0_floor"),
                            self._param("tail_level_floor"), ok)
        except ValueError:
            return {"b0": None, "ltop": None, "verdict": "UNRESOLVED"}

    @staticmethod
    def _agrees(fill, sens):
        """Whether the sensitivity state's tests establish every side the fill state's establish (both sides)."""
        for stat in ("b0", "ltop"):
            f, g = fill.get(stat), sens.get(stat)
            if f is None:
                continue
            if g is None:
                if f["gt0"] or f["lt_floor"]:
                    return False
                continue
            if (f["gt0"] and not g["gt0"]) or (f["lt_floor"] and not g["lt_floor"]):
                return False
        return True

    @staticmethod
    def _sandwich(state, b, theta, way, block=0):
        """Return ``state.sandwich(...)``, or ``None`` where it divides by zero (a one-effective-pair table).

        An F1 defect, reported not fixed here: with a single populated ``(name, block)`` pair the pair table's
        Kish count is 1 and ``_cr1`` divides by ``g_eff - 1 = 0`` instead of returning ``None``.
        """
        try:
            return state.sandwich(b, theta, way, block=block)
        except ZeroDivisionError:
            return None

    @staticmethod
    def _brief(stat):
        return None if stat is None else {k: stat[k] for k in ("t", "p_gt", "p_lt", "scheme")}

    def _slope_read(self, state):
        """Return ``{"slope", "verdict"}`` of the forward slope of ``r`` on a stored line (quarter blocks, floor)."""
        out = {"slope": None, "verdict": "UNRESOLVED"}
        q = self._quarter_block()
        fit = state.fit(None)
        try:
            if fit["theta"] is None or fit["g_eff_blocks"][q] <= 1.0 or fit["n_eff"] <= 2.0:
                return out
            tests = _statistic_tests(fit["b"], state.block_vectors(None, q), fit["theta"], state.bread(None), 1,
                                     _cr1(fit["g_eff_blocks"][q], fit["n_eff"], 1), "ols", self._param("b0_floor"),
                                     self._param("ci_level"), self._param("bootstrap_draws"),
                                     self._param("bootstrap_seed"))
        except ValueError:
            return out
        return {"slope": self._brief(tests), "verdict": three_way_verdict([tests["gt0"]], [tests["lt_floor"]])}

    def _read_two(self, gated):
        """Read 2: the forward slope of ``r`` on ``c`` (R4), gated on read 1 PASS."""
        if gated:
            return {"gated": True, "slope": None, "verdict": "UNRESOLVED"}
        return {"gated": False, **self._slope_read(self._states["forward"])}

    def _integration_reads(self):
        """Integration-owned descriptive reads (ADR-0313 X2, X6); they add no confirmatory read."""
        out = {"read2w": {"state": "forward_w", "line": "cw_bucket", "form": "slope",
                          **self._slope_read(self._integ["forward_w"])}}
        if self._direction:
            out["read2b"] = {"state": "forward_b2", "line": "c2w_bucket", "form": "no_intercept",
                             **self._slope_read(self._integ["forward_b2"])}
        st, q = self._states["forward"], self._quarter_block()
        fit = st.fit(None)
        side = {"state": "forward", "line": "c_bucket", "form": "affine", "intercept": None,
                "sides_positive": False, "verdict": "UNRESOLVED"}
        try:
            if fit["theta"] is not None and fit["g_eff_blocks"][q] > 1.0 and fit["n_eff"] > 2.0:
                test = wild_cluster_test(st.block_vectors(None, q), fit["theta"], st.bread(None), 0, 0.0,
                                         "two_sided", self._param("ci_level"), self._param("bootstrap_draws"),
                                         self._param("bootstrap_seed"),
                                         factor=_cr1(fit["g_eff_blocks"][q], fit["n_eff"], 1))
                side["intercept"] = {"p": test["p"], "reject": test["reject"]}
        except ValueError:
            pass
        buy, sell = st.fit("buy"), st.fit("sell")
        side["sides_positive"] = bool(buy["b"] is not None and sell["b"] is not None and buy["b"] > 0 and sell["b"] > 0)
        if side["intercept"] and side["intercept"]["reject"] and side["sides_positive"]:
            side["verdict"] = "PASS"
        out["affine_side"] = side
        return out

    def _bh(self):
        """Benjamini-Hochberg over the bucket slopes of the primary state (unshrunk own fits, name + block_4 band)."""
        st, pvals, sign = self._states["expanding_fill"], {}, {}
        for b in range(24):
            fit = st.fit(b)
            if fit["theta"] is None:
                continue
            sw = self._sandwich(st, b, fit["theta"], "two_block", block=0)
            if sw is None or not sw["cov"][1][1] > 0.0:
                continue
            t = fit["b"] / sw["cov"][1][1] ** 0.5
            pvals[str(b)] = min(1.0, 2.0 * student_t_sf(abs(t), sw["df"]))
            sign[str(b)] = fit["b"]
        if not pvals:
            return {"bh_positive_buckets": 0, "bh_negative_buckets": 0, "bh_tested_buckets": 0}
        rej = benjamini_hochberg(pvals, self._param("bh_q"))
        return {"bh_positive_buckets": sum(1 for k, v in rej.items() if v and sign[k] > 0),
                "bh_negative_buckets": sum(1 for k, v in rej.items() if v and sign[k] < 0),
                "bh_tested_buckets": len(pvals)}

    def _e4_input(self):
        """Return the pooled quarter-block band of ``w_dir`` (own fit and shrunk) at every configured level (part E)."""
        st, q = self._states["reported_dir"], self._quarter_block()
        fit = st.fit(None)
        if fit["theta"] is None:
            return None
        shrunk = self._pool.fit(st)["pooled"]["theta"][1]
        out = {"g_eff_blocks_13": fit["g_eff_blocks"][q], "n_eff": fit["n_eff"], "levels": list(self._pool.ci_levels)}
        for key, value in (("w_dir", fit["b_dir"]), ("w_dir_shrunk", shrunk)):
            theta = [fit["theta"][0], value, fit["theta"][2]]
            sw = self._sandwich(st, None, theta, "two_block", block=q)
            if sw is None:
                out[key] = None
                continue
            se, df = sw["cov"][1][1] ** 0.5, sw["df"]
            rec = {"value": value, "se": se, "df": df, "p_boot_0": None, "bands": {}}
            for lvl in self._pool.ci_levels:
                crit = student_t_critical(lvl, df)
                rec["bands"][f"{lvl:g}"] = {"lcb": value - crit * se, "ucb": value + crit * se}
            try:
                rec["p_boot_0"] = wild_cluster_test(st.block_vectors(None, q), fit["theta"], st.bread(None), 1, 0.0,
                                                    "two_sided", self._param("ci_level"),
                                                    self._param("bootstrap_draws"),
                                                    self._param("bootstrap_seed"))["p"]
            except ValueError:
                pass
            out[key] = rec
        return out

    @staticmethod
    def _cell(v):
        return "" if v is None else repr(v) if isinstance(v, float) else str(v)

    def _csv(self, name, header, rows):
        raw = ("\n".join([",".join(header)] + [",".join(self._cell(v) for v in r) for r in rows]) + "\n").encode()
        atomic_write(os.path.join(self._out, name), raw)
        return _sha(raw)

    def _finish(self):
        """Write the end-of-run tables and return the summary blocks (reads, BH, direction, fill rates)."""
        overall, top, fill_ok = self._fill_rates()
        if not fill_ok:
            self._count("low_fill_rate")
        twin = self._safe_read(self._twin, True)
        sens = self._safe_read(self._states["reported_sensitivity"], True)
        agrees = self._agrees(twin, sens)
        one = self._safe_read(self._states["reported_fill"], fill_ok and agrees)
        two = self._read_two(one["verdict"] != "PASS")
        extras = {
            "reads": {"read1": {"b0": self._brief(one["b0"]), "ltop": self._brief(one["ltop"]), "fill_ok": fill_ok,
                                "sensitivity_agrees": agrees, "verdict": one["verdict"]}, "read2": two},
            "confirmatory_reads": 2, "integration_reads": self._integration_reads(), **self._bh(),
            "fill_rate": {"reported": overall, "reported_top": top, "min_fill_rate": self._param("min_fill_rate")},
        }
        if self._direction:
            extras["direction"] = {"e4_input": self._e4_input()}
        outputs = {"fill-rate.csv": self._csv(
            "fill-rate.csv", ("axis", "value", *self._FR_FIELDS),
            [(*k.split(":", 1), *(rec[f] for f in self._FR_FIELDS)) for k, rec in sorted(self._fr.items())])}
        outputs.update(self._slope_tables())
        outputs["forward-reliability.csv"] = self._forward_table()
        extras["outputs"] = outputs
        return extras

    def _forward_table(self):
        """Write forward-reliability.csv: the stored calibrated edge scored forward on reported non-warm-up rows.

        Per variant of ``c`` (bucket-only, name-adjusted, two-regressor), side stratum and decision quarter:
        the count, the OLS slope of ``r`` on ``c`` (both per unit of notional), the rank IC, and ten bins of
        ``c`` by rank with their mean ``c`` and mean ``r``. Descriptive; no state is read.
        """
        import numpy as np

        data = []
        for rel in sorted(k for k in self._manifest if k.startswith("rows/")):
            with open(os.path.join(self._out, rel)) as fh:
                for line in fh:
                    r = json.loads(line)
                    if r["code"] is None and not r["map_warmup"]:
                        data.append(r)
        head = ("variant", "side", "quarter", "bin", "n", "mean_c", "mean_r", "slope", "rank_ic")
        rows = []
        for variant, key in (("c_bucket", "c_bucket"), ("c_name", "c"), ("c2_bucket", "c2")):
            for side in ("all", "buy", "sell"):
                for quarter in ["all", *sorted({self._quarter(r["t"]) for r in data})]:
                    sel = [r for r in data if r[key] is not None and (side == "all" or r["sign"] == (1 if side == "buy" else -1))
                           and (quarter == "all" or self._quarter(r["t"]) == quarter)]
                    if not sel:
                        continue
                    c = np.array([r[key] for r in sel], float)
                    y = np.array([r["r"] / r["scale"] for r in sel], float)
                    slope = ic = None
                    if len(sel) > 2 and c.std() > 0:
                        slope = float(((c - c.mean()) * (y - y.mean())).sum() / ((c - c.mean()) ** 2).sum())
                        rc, ry = np.argsort(np.argsort(c)), np.argsort(np.argsort(y))
                        if rc.std() > 0 and ry.std() > 0:
                            ic = float(np.corrcoef(rc, ry)[0, 1])
                    rows.append((variant, side, quarter, "all", len(sel), float(c.mean()), float(y.mean()), slope, ic))
                    order = np.argsort(c, kind="stable")
                    for b, idx in enumerate(np.array_split(order, 10)):
                        if len(idx):
                            rows.append((variant, side, quarter, b, len(idx), float(c[idx].mean()),
                                         float(y[idx].mean()), None, None))
        return self._csv("forward-reliability.csv", head, rows)

    def _slope_tables(self):
        """Write side-slope.csv, divergence.csv and (with direction) direction-slope.csv; return their hashes."""
        head = ("state", "unit", "rows", "n", "a", "b", "b_dir", "b_shape")
        side, div = [], []
        for key, st in (*self._states.items(), ("reported_fill_common", self._twin), *self._integ.items()):
            for unit in ("buy", "sell", None):
                f = st.fit(unit)
                row = (key, "pooled" if unit is None else unit, f["rows"], f["n"], f["a"], f.get("b"),
                       f.get("b_dir"), f.get("b_shape"))
                (div if unit is None else side).append(row)
        out = {"side-slope.csv": self._csv("side-slope.csv", head, side),
               "divergence.csv": self._csv("divergence.csv", head, div)}
        if self._direction:
            st, lv = self._states["reported_dir"], self._pool.ci_levels
            shr = self._pool.fit(st)
            cols = ["bucket", "rows", "w_dir", "w_dir_shrunk", "pooled", "df", "rule", "n_eff", "g_eff_blocks_4",
                    "b_shape", "reliability_dir", "b_dir_corrected", "corr_dir_shape"]
            cols += [f"{kind}_{lvl:g}" for lvl in lv for kind in ("lcb", "ucb")]
            rows = []
            for b in [None, *range(24)]:
                f = st.fit(b)
                rec = shr["pooled"] if b is None else shr["buckets"][b]
                sw = None if f["theta"] is None else self._sandwich(st, b, f["theta"], "two_block", block=0)
                row = [("pooled" if b is None else b), f["rows"], f["b_dir"], rec["theta"][1], rec["pooled"],
                       None if sw is None else sw["df"], None if sw is None else sw["rule"], f["n_eff"],
                       f["g_eff_blocks"][0], f["b_shape"], f["reliability_dir"], f["b_dir_corrected"],
                       f["corr_dir_shape"]]
                for lvl in lv:
                    band = rec["bands"][f"{lvl:g}"]
                    row += [None if band["lcb"] is None else band["lcb"][1],
                            None if band["ucb"] is None else band["ucb"][1]]
                rows.append(row)
            out["direction-slope.csv"] = self._csv("direction-slope.csv", cols, rows)
        return out

    # -- summary ---------------------------------------------------------------------------

    def _summary(self, weeks):
        digest = hashlib.sha256()
        for rel in sorted(self._manifest):
            digest.update(os.path.basename(rel).encode())
            with open(os.path.join(self._out, rel), "rb") as fh:
                digest.update(fh.read())
        counts = dict(self._counts)
        waiting = [(s, n) for s, names in self._sens.items() for n in names]
        if waiting:
            counts["imputation_unflushed"] = len(waiting)
            counts["imputation_unflushed_rows"] = sum(
                len(json.load(open(os.path.join(self._out, "sensitivity", s, f"{n}.json")))["rows"])
                for s, n in waiting)
        p = self.params
        return {"params_hash": self._params_hash(), "pins": self._input_pins(), "first": weeks[0],
                "sealed_from": p.get("sealed_from"), "report_from": p["report_from"], "window": dict(p["window"]),
                "edge_scale": self._param("edge_scale"), "row_weight": self._param("row_weight"),
                "pooling": {**PartialPooling._DEFAULTS, **p["pooling"]}, "buckets": p["buckets"],
                "regime": p["regime"], "walk_decision_dates": list(weeks),
                "warm_weeks": {q: dict(sorted(v.items())) for q, v in sorted(self._warm.items())},
                "last": weeks[-1], "weeks": len(weeks), "states": list(self._states),
                "counts": dict(sorted(counts.items())), "output_sha256": digest.hexdigest()}

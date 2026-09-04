"""``nodes_model`` — the ladder-q̂ spine as registered node kinds.

Six kinds, one per step a training document takes from records to a
prediction frame, each a thin doorway over machinery that lives
elsewhere:

* ``pmquant-ladder-panels`` (:class:`LadderPanels`) — records + outcomes
  + the settlement rows -> one event panel per settled event
  (:mod:`pmquant.ladder.panels`), cut into ``train/val/cal/test`` blocks
  by the DOCUMENT's split: one event, one block, on the event's close.
* ``pmquant-ladder-train`` (:class:`LadderTrain`) — the pack's
  :class:`~dskit.pipeline.libs.torch.DeclaredTrain` pinned to the ladder
  module and adapter, plus the one rule the pack lacks: checkpoint
  selection on ``claims_val_event_ll``, the per-event validation log-loss
  over the claims universe. The loop, the curve, the sidecar and the
  ``mode="load"`` contract are inherited verbatim.
* ``pmquant-ladder-predict`` (:class:`LadderPredict`) — restores a pinned
  artifact through the pack's own sidecar checks and emits the frozen
  prediction frame: one row per ``(event, step, visible rung)`` of the
  ELIGIBLE events, stamped with the block label and the artifact's
  ``state_hash``.
* ``pmquant-ensemble`` (:class:`Ensemble`) — the loud across-seed merge:
  identical cell sets, identical labels and asks, ``q`` averaged, an
  ``ensemble_id`` minted over the members' state hashes.
* ``pmquant-signal-qhat`` (:class:`SignalQhat`) — a prediction frame
  republished as a signal speaking both protocols (``predict(record)`` for
  the toolkit's ``validate``, ``q_hat(...)`` for the child's sizer).
* ``pmquant-market-implied`` (:class:`MarketImplied`) — the N0 null: the
  stated YES ask IS the belief.

Import = registration: importing this module claims the ``pmquant-*``
names above. Heavy libraries — and the two heavy child modules,
:mod:`pmquant.ladder.panels` (numpy inside its methods) and
:mod:`pmquant.models` (torch at its top) — are imported strictly inside
methods, so a document naming these kinds plans on a machine with neither
installed.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping

from dskit.pipeline.libs.torch import DeclaredPredict, DeclaredTrain
from dskit.pipeline.node import (
    Node,
    TrainableNode,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.split_policy import DEFAULT_SPLIT_POLICY, SPLIT_NAMES, SplitFrame

from pmquant.ladder.protocols import (
    DEFAULT_DUR_CAPS_H,
    DEFAULT_MIN_ABS_LEAD_S,
    LEAD_FRACS,
    LeadGrid,
    lead_key,
)

__all__ = [
    "CELL_KEY",
    "CLAIMS_MONITOR",
    "DEFAULT_PRICE_FIELD",
    "DEFAULT_REQUIRE",
    "LADDER_ADAPTER_REF",
    "LADDER_MODULE_REF",
    "LEADS_ALL",
    "NODE_KINDS",
    "PRED_ROW_KEYS",
    "PREDICT_KNOBS",
    "Q_EPS",
    "Ensemble",
    "LadderPanels",
    "LadderPredict",
    "LadderTrain",
    "MarketImplied",
    "MarketImpliedSignal",
    "QHatSignal",
    "SignalQhat",
]

#: The two classes a ladder document names — pinned by
#: ``tests/test_nodes_model.py`` to the objects in :mod:`pmquant.models`.
LADDER_ADAPTER_REF = "pmquant.models:LadderPanelAdapter"
LADDER_MODULE_REF = "pmquant.models:LadderQhatModule"

#: The checkpoint-selection statistic :class:`LadderTrain` adds to the
#: pack's monitors: the per-event val log-loss over ELIGIBLE events.
CLAIMS_MONITOR = "claims_val_event_ll"

#: The knobs a ladder panel fit never reads — refused by name when declared.
_ROW_KNOBS = ("features", "label")

#: :class:`LadderPredict`'s own knobs beyond the pack's.
PREDICT_KNOBS = ("block", "leads")
#: The only legal ``leads`` value: the lead axis is the sequence axis.
LEADS_ALL = "all"

#: The prediction-frame columns, in emission order.
PRED_ROW_KEYS = (
    "series",
    "event",
    "step",
    "lead",
    "rung",
    "contract",
    "y",
    "q",
    "ask",
    "ask_no",
    "ask_sz",
    "bid_sz",
    "partition",
    "block",
    "state_hash",
    "store_ver",
)

#: How many members :class:`Ensemble` expects when the document is silent.
DEFAULT_REQUIRE = 5
#: A member port: ``member_<int>``.
_MEMBER_PORT = re.compile(r"^member_(\d+)$")
#: The cell identity two members must agree on, and the columns that must
#: be IDENTICAL across members at every cell.
CELL_KEY = ("series", "event", "lead", "rung")
_AGREE_COLUMNS = ("y", "ask")
_ENSEMBLE_ID_HEX = 16

#: The record field :class:`SignalQhat` reads as the price, by default.
DEFAULT_PRICE_FIELD = "mid"
#: The clip a served belief is held inside.
Q_EPS = 1e-6


def _field(record, name):
    """Read one field off a record: mapping key first, then attribute."""
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _finite_or_none(value):
    """Turn a float cell into a JSON-safe value: ``None`` for NaN."""
    value = float(value)
    return None if math.isnan(value) else value


# ---------------------------------------------------------------------------
# panels
# ---------------------------------------------------------------------------


class LadderPanels(Node):
    """Cut records into event panels per block (role ``transform``).

    Every settled event becomes ONE panel item, assigned to a block by the
    run's declared split (``ctx.splits.split_of``) on the event's close
    instant with the event as its cluster — so one event lands in one
    block by construction. Under the default ``record`` policy that agrees
    with a record-level cut exactly when every record of an event is cut
    on the event's close; a document declaring ``policy: "event-close"``
    makes its record-level cuts agree too.

    Parameters
    ----------
    params : dict
        ``lead_fracs`` (list of floats, strictly decreasing in (0, 1) —
        the T axis; default :data:`~pmquant.ladder.protocols.LEAD_FRACS`),
        ``k_lvl`` (int >= 1; default the featurizer's), ``drop`` (null or
        a list of ablation group names), ``min_contracts`` (int >= 1;
        default the panel builder's).

    Examples
    --------
    The frozen recipe over the shared grid, dropping the context group::

        node = LadderPanels("panels", {"drop": ["context"]})
        out = node.run(ctx, {"records": records, "outcomes": outcomes,
                             "markets": rows, "family": ["KXA"]})
        len(out["train_rows"])   # the train-block panels
    """

    role = "transform"
    outputs = ("train_rows", "val_rows", "cal_rows", "test_rows", "vocab", "metrics")

    _PARAMS = ("drop", "k_lvl", "lead_fracs", "min_contracts")

    @classmethod
    def validate_params(cls, params):
        """List problems with the panel knobs, empty when none.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            One problem per defect.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems.extend(
            LeadGrid.problems(
                params.get("lead_fracs", list(LEAD_FRACS)),
                list(DEFAULT_DUR_CAPS_H),
                DEFAULT_MIN_ABS_LEAD_S,
            )
        )
        if "k_lvl" in params:
            check_int_param(problems, "k_lvl", params["k_lvl"], ge=1)
        if "min_contracts" in params:
            check_int_param(problems, "min_contracts", params["min_contracts"], ge=1)
        drop = params.get("drop")
        if drop is not None:
            from pmquant.ladder.panels import TAIL_GROUPS

            if (
                not isinstance(drop, list)
                or any(not isinstance(g, str) for g in drop)
                or set(drop) - set(TAIL_GROUPS)
            ):
                problems.append(
                    "drop must be null or a list of ablation group names from "
                    f"{sorted(TAIL_GROUPS)}, got {drop!r}"
                )
        return problems

    def validate_inputs(self, inputs):
        """List problems with the wired ports, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            One problem per port that is not the list/dict it must be. A
            one-shot iterable is refused BY NAME, never walked.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(
                "records must be a LIST of MarketRecord envelopes (a one-shot "
                f"iterable is refused by name), got {type(inputs.get('records')).__name__}"
            )
        if not isinstance(inputs.get("outcomes"), Mapping):
            problems.append(
                f"outcomes must be a dict of contract -> bool, got {inputs.get('outcomes')!r}"
            )
        if not isinstance(inputs.get("markets"), list):
            problems.append(
                "markets must be a LIST of settlement/strike rows, got "
                f"{type(inputs.get('markets')).__name__}"
            )
        family = inputs.get("family")
        if not isinstance(family, (list, tuple)) or any(not isinstance(s, str) for s in family):
            problems.append(
                f"family must be a list of series tickers (the eligible instruments), got {family!r}"
            )
        return problems

    def grid(self):
        """Build the lead grid the declared ``lead_fracs`` name.

        Returns
        -------
        LeadGrid
            Over ``lead_fracs`` (default :data:`~pmquant.ladder.protocols.LEAD_FRACS`).
        """
        return LeadGrid(self.params.get("lead_fracs", LEAD_FRACS))

    def run(self, ctx, inputs):
        """Build the panels and cut them into blocks.

        Parameters
        ----------
        ctx : NodeContext
            Must carry ``splits`` — the declared split is what assigns.
        inputs : dict
            ``records``, ``outcomes``, ``markets``, ``family``.

        Returns
        -------
        dict
            ``train_rows``/``val_rows``/``cal_rows``/``test_rows`` (lists of
            panel items; ``cal_rows`` is ``[]`` without a cal band),
            ``vocab`` (``{series: index}``) and ``metrics``.

        Raises
        ------
        ValueError
            Without a split on the run, or when the train block is EMPTY.
        """
        splits = getattr(ctx, "splits", None)
        if splits is None or not callable(getattr(splits, "split_of", None)):
            raise ValueError(
                f"{self.key}: no splits on the run — this node assigns every event "
                "to a block through the DOCUMENT's declared split (splits.split_of); "
                "declare a 'splits' section rather than have a cut invented here"
            )
        from pmquant.ladder.panels import (
            DEFAULT_K_LVL,
            DEFAULT_MIN_CONTRACTS,
            TokenFeaturizer,
            build_panel_items,
            build_panels,
        )

        grid = self.grid()
        built = build_panels(
            inputs["records"],
            inputs["outcomes"],
            inputs["markets"],
            grid,
            min_contracts=int(self.params.get("min_contracts", DEFAULT_MIN_CONTRACTS)),
        )
        featurizer = TokenFeaturizer(
            int(self.params.get("k_lvl", DEFAULT_K_LVL)),
            drop=tuple(self.params.get("drop") or ()),
        )
        items = build_panel_items(built.panels, featurizer, grid, eligible=inputs["family"])
        blocks = {name: [] for name in SPLIT_NAMES}
        unassigned = 0
        for panel, item in zip(built.panels, items):
            block = splits.split_of(SplitFrame(panel.close_ts_ms, panel.event))
            if block is None:
                unassigned += 1
                continue
            if block not in blocks:
                raise ValueError(
                    f"{self.key}: the split assigned {block!r}, not one of {list(SPLIT_NAMES)}"
                )
            blocks[block].append(item)
        if not blocks["train"]:
            raise ValueError(
                f"{self.key}: train_rows is EMPTY — the declared split put no settled "
                f"event in train over {built.counts['n_panels']} panel(s) "
                f"({unassigned} unassigned); fix the cuts rather than train on nothing"
            )
        metrics = {
            f"n_{name}_events": len(rows) for name, rows in blocks.items()
        }
        metrics.update(
            n_unassigned_events=unassigned,
            n_markets=len(built.vocab),
            n_leads=len(grid.lead_fracs),
            split_policy=getattr(splits, "policy", DEFAULT_SPLIT_POLICY),
            **built.counts,
        )
        self.log.info(
            "ladder panels: train %d / val %d / cal %d / test %d event(s) over %d market(s)",
            len(blocks["train"]),
            len(blocks["val"]),
            len(blocks["cal"]),
            len(blocks["test"]),
            len(built.vocab),
        )
        return {
            "train_rows": blocks["train"],
            "val_rows": blocks["val"],
            "cal_rows": blocks["cal"],
            "test_rows": blocks["test"],
            "vocab": built.vocab.to_dict(),
            "metrics": metrics,
        }


# ---------------------------------------------------------------------------
# train / predict — the declared seam, pinned to the ladder
# ---------------------------------------------------------------------------


def _ladder_ref_problems(params, *, require_adapter):
    """Pin ``adapter``/``module`` to the ladder pair and refuse the row knobs."""
    problems = []
    adapter = params.get("adapter")
    if adapter != LADDER_ADAPTER_REF and (require_adapter or adapter is not None):
        problems.append(
            f"adapter must be exactly {LADDER_ADAPTER_REF!r}"
            + (" (required" if require_adapter else " when declared (")
            + " — the pack's flat-vector default would build the wrong model and "
            f"bypass the panel adapter), got {adapter!r}"
        )
    module = params.get("module")
    if module != LADDER_MODULE_REF:
        problems.append(
            f"module must be exactly {LADDER_MODULE_REF!r} — this kind serves the "
            f"ladder-q̂ transformer and nothing else, got {module!r}"
        )
    for knob in _ROW_KNOBS:
        if knob in params:
            problems.append(
                f"{knob} is meaningless for a ladder panel fit — the panel IS the "
                "example; remove it"
            )
    return problems


class LadderTrain(DeclaredTrain):
    """Train the ladder-q̂ transformer, selecting on ``claims_val_event_ll`` (role ``train``).

    :class:`~dskit.pipeline.libs.torch.DeclaredTrain` with two
    tightenings: ``adapter`` and ``module`` are REQUIRED and pinned to the
    ladder pair (the pack's flat-vector default would silently train the
    wrong model), and the monitor vocabulary gains
    :data:`CLAIMS_MONITOR` — the per-event validation log-loss over the
    ELIGIBLE (claims-universe) events, scored at every epoch boundary
    through the adapter's ``event_logloss``. The pack's monitor machinery
    then does the rest: the best epoch's weights are snapshotted and
    restored before the artifact is written, and ``metrics`` carries
    ``selected_epoch``/``monitor_value``. Every declared epoch runs — only
    the checkpoint is selected.

    Parameters
    ----------
    params : dict
        The pack's knobs (``epochs``, ``lr``, ``loader``, ``optimizer``,
        ``device``, ``monitor``, ...) plus ``module`` (must be
        :data:`LADDER_MODULE_REF`), ``module_params``, ``adapter`` (must be
        :data:`LADDER_ADAPTER_REF`). ``features``/``label``/``loss`` are
        refused by name.

    Examples
    --------
    One seed of the frozen recipe::

        node = LadderTrain("seed_0", {
            "adapter": "pmquant.models:LadderPanelAdapter",
            "module": "pmquant.models:LadderQhatModule",
            "module_params": {"drop": "context"},
            "epochs": 8, "lr": 0.001, "monitor": "claims_val_event_ll",
            "loader": {"batch_size": 32, "shuffle": True, "seed": 0},
        })
        out = node.run(ctx, {"rows": train_rows, "val_rows": val_rows})
    """

    _MONITORS = DeclaredTrain._MONITORS + (CLAIMS_MONITOR,)

    @classmethod
    def _features_required(cls, params):
        """Never — the pinned adapter builds examples from panels.

        Pinned by test against ``LadderPanelAdapter.requires_features``.
        """
        return False

    @classmethod
    def validate_params(cls, params):
        """List problems with the fit's knobs, empty when none.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            The pack's problems plus the ladder pins.
        """
        problems = list(super().validate_params(params))
        problems.extend(_ladder_ref_problems(params, require_adapter=True))
        return problems

    def validate_train_inputs(self, inputs):
        """List problems with the fit's wired streams, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            The pack's problems, plus one when ``monitor`` is
            :data:`CLAIMS_MONITOR` and ``val_rows`` is missing or empty —
            the selection rule is silent without a val block.
        """
        problems = list(super().validate_train_inputs(inputs))
        if self.params.get("monitor") == CLAIMS_MONITOR:
            val = inputs.get("val_rows")
            if not isinstance(val, list) or not val:
                problems.append(
                    f"val_rows must be a non-empty LIST when monitor is {CLAIMS_MONITOR!r} "
                    "— the rule selects the checkpoint on the val block and is silent "
                    f"without it, got {type(val).__name__}"
                )
        return problems

    def _fit_datasets(self, adapter, inputs, features, label):
        """Prepare the splits, refusing a val block with nothing eligible to select on."""
        train_set, val_set = super()._fit_datasets(adapter, inputs, features, label)
        if (
            self.params.get("monitor") == CLAIMS_MONITOR
            and val_set is not None
            and not any(item["eligible"] for item in val_set.payload)
        ):
            raise ValueError(
                f"{self.key}: monitor {CLAIMS_MONITOR!r} averages over ELIGIBLE val "
                f"events and none of the {len(val_set)} val panel(s) is in the family "
                "— nothing to select on; widen the family or the val block"
            )
        return train_set, val_set

    def _score_epoch(self, fit):
        """Score an epoch as the pack does, then add the claims statistic.

        Parameters
        ----------
        fit : _Fit
            The live training frame.

        Returns
        -------
        tuple
            ``(val_loss, scored)`` with ``scored[CLAIMS_MONITOR]`` set
            whenever a val split exists (``nan`` -> recorded, never best).
        """
        val_loss, scored = super()._score_epoch(fit)
        if fit.val_set is None:
            return val_loss, scored
        scored = dict(scored or {})
        scored[CLAIMS_MONITOR] = fit.adapter.event_logloss(
            fit.module, fit.val_set, eligible_only=True, batch_size=fit.eval_batch_size
        )
        return val_loss, scored


def _prediction_rows(adapter, module, batches, *, block, state_hash):
    """Emit one frame row per visible cell of the prepared items."""
    items = batches.payload
    rows = []
    for cell in adapter.cells(module, batches):
        item = items[cell.item]
        rows.append(
            {
                "series": item["series"],
                "event": item["event"],
                "step": cell.step,
                "lead": float(item["lead_fracs"][cell.step]),
                "rung": cell.rung,
                "contract": item["contracts"][cell.rung],
                "y": cell.y,
                "q": cell.q,
                "ask": _finite_or_none(item["asks"][cell.step, cell.rung]),
                "ask_no": _finite_or_none(item["asks_no"][cell.step, cell.rung]),
                "ask_sz": float(item["ask_sz"][cell.step, cell.rung]),
                "bid_sz": float(item["bid_sz"][cell.step, cell.rung]),
                "partition": bool(item["is_partition"]),
                "block": block,
                "state_hash": state_hash,
                "store_ver": None,
            }
        )
    return rows


class LadderPredict(DeclaredPredict):
    """Restore a ladder artifact and emit the prediction frame (role ``signal``).

    :class:`~dskit.pipeline.libs.torch.DeclaredPredict`'s restore — the
    sidecar's recorded class, content hash and ``module``/``module_params``
    cross-checks fire unchanged — then one forward over the ELIGIBLE panel
    items and one row per ``(event, step, visible rung)``, in
    :data:`PRED_ROW_KEYS` order. Out-of-family events never leave.

    Parameters
    ----------
    params : dict
        The pack's inference knobs (``artifact``, ``module`` — must be
        :data:`LADDER_MODULE_REF` — ``module_params``, ``adapter`` when
        declared must be :data:`LADDER_ADAPTER_REF`) plus ``block`` (one of
        train/val/cal/test, REQUIRED — the label stamped on every row) and
        ``leads`` (must be ``"all"``).

    Examples
    --------
    The val frame of one trained seed::

        node = LadderPredict("pred_val_0", {
            "module": "pmquant.models:LadderQhatModule",
            "module_params": {"drop": "context"},
            "block": "val",
        })
        out = node.run(ctx, {"panel_rows": val_rows,
                             "artifact_path": trained["artifact_path"]})
    """

    outputs = ("pred_rows", "metrics")

    @classmethod
    def _allowed(cls):
        """Widen the pack's allowlist by :data:`PREDICT_KNOBS`."""
        return super()._allowed() + PREDICT_KNOBS

    @classmethod
    def validate_params(cls, params):
        """List problems with the inference knobs, empty when none.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            The pack's problems, the ladder pins, and the ``block``/``leads`` checks.
        """
        problems = list(super().validate_params(params))
        problems.extend(_ladder_ref_problems(params, require_adapter=False))
        block = params.get("block")
        if block not in SPLIT_NAMES:
            problems.append(
                f"block is required — one of {list(SPLIT_NAMES)}, the label stamped on "
                f"every emitted row, got {block!r}"
            )
        leads = params.get("leads", LEADS_ALL)
        if leads != LEADS_ALL:
            problems.append(
                f"leads must be {LEADS_ALL!r} — the lead axis is the model's sequence "
                f"axis and every lead is scored, got {leads!r}"
            )
        return problems

    def validate_common_inputs(self, inputs):
        """List problems with the ports, in either mode.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            The pack's pin-port problem plus one when ``panel_rows`` is not
            a list.
        """
        problems = list(super().validate_common_inputs(inputs))
        rows = (inputs or {}).get("panel_rows")
        if not isinstance(rows, list):
            problems.append(
                "panel_rows must be a LIST of panel items (wire a pmquant-ladder-panels "
                f"block output), got {type(rows).__name__}"
            )
        return problems

    def run_load(self, ctx, inputs):
        """Restore the pinned artifact and score the eligible panels.

        Parameters
        ----------
        ctx : NodeContext
            The run frame.
        inputs : dict
            ``panel_rows`` and, unless the document pins one, ``artifact_path``.

        Returns
        -------
        dict
            ``pred_rows`` (the frame) and ``metrics`` (``n_rows``,
            ``n_events``, ``n_eligible_events``, ``block``).

        Raises
        ------
        ValueError
            When the run carries a split and it assigns a wired event to a
            block other than the declared ``block`` — the label stamped
            on every row is a claim about where the events came from, and
            a ``pred_cal`` node wired to the test rows would otherwise
            hand the gate a mislabelled frame.
        """
        splits = getattr(ctx, "splits", None)
        if splits is not None and callable(getattr(splits, "split_of", None)):
            for item in inputs["panel_rows"]:
                assigned = splits.split_of(SplitFrame(item["close_ts_ms"], item["event"]))
                if assigned != self.params["block"]:
                    raise ValueError(
                        f"{self.key}: block is {self.params['block']!r} but the run's split "
                        f"assigns event {item['event']!r} (close {item['close_ts_ms']}) to "
                        f"{assigned!r} — panel_rows is wired from another block's output"
                    )
        reference = self.pinned_artifact(
            self.params.get("artifact"),
            (inputs or {}).get("artifact_path"),
            missing=(
                "no artifact reference — set mode='load' + artifact, params['artifact'], "
                "or wire inputs['artifact_path'] from a pmquant-ladder-train node"
            ),
        )
        module, sidecar = self._load_artifact(reference)
        adapter = self._restore_adapter(sidecar, reference)
        block = self.params["block"]
        items = inputs["panel_rows"]
        eligible = [item for item in items if item["eligible"]]
        rows = []
        if eligible:
            batches = adapter.prepare(eligible, sidecar["params"], where="panel_rows")
            rows = _prediction_rows(
                adapter, module, batches, block=block, state_hash=sidecar["state_hash"]
            )
        self.log.info(
            "ladder predict: %d row(s) over %d/%d eligible event(s), block %r, from %s",
            len(rows),
            len(eligible),
            len(items),
            block,
            reference,
        )
        return {
            "pred_rows": rows,
            "metrics": {
                "n_rows": len(rows),
                "n_events": len(items),
                "n_eligible_events": len(eligible),
                "block": block,
            },
        }


# ---------------------------------------------------------------------------
# ensemble
# ---------------------------------------------------------------------------


def _same(a, b):
    """Compare two cell values, treating ``None``/NaN absences as equal."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b


class Ensemble(Node):
    """Merge the seeds' prediction frames, loudly (role ``transform``).

    Every wired ``member_<int>`` port carries one seed's frame. The merge
    REFUSES anything but a clean average: a member with duplicate cells, a
    member covering a different cell set, or a member disagreeing on a
    label or an ask — those mean mixed stores or blocks, the silent
    mistake this node exists to make loud. The output is the first
    member's rows with ``q`` replaced by the across-member mean,
    ``state_hash`` dropped, and ``ensemble_id`` — sha256 over the sorted
    members' ``state_hash`` values — stamped on every row. The per-member
    beliefs land in ``seed_panel.json`` as context, never gate material.

    Parameters
    ----------
    params : dict
        ``require`` (int >= 1; default :data:`DEFAULT_REQUIRE`) — exactly
        how many members must be wired.

    Examples
    --------
    The frozen five-seed protocol::

        node = Ensemble("ens", {"require": 5})
        out = node.run(ctx, {f"member_{i}": frames[i] for i in range(5)})
    """

    role = "transform"
    outputs = ("pred_rows", "metrics")

    _PARAMS = ("require",)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``require``, empty when none.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            One problem per defect.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if "require" in params:
            check_int_param(problems, "require", params["require"], ge=1)
        return problems

    def validate_inputs(self, inputs):
        """List problems with the member ports, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            A problem per port not named ``member_<int>``, per member that
            is not a list, and one when the member count is not ``require``.
        """
        problems = []
        members = 0
        for port, value in (inputs or {}).items():
            if not _MEMBER_PORT.match(port):
                problems.append(
                    f"unknown input port {port!r} — members are wired as member_<int> "
                    "(member_0, member_1, ...)"
                )
                continue
            members += 1
            if not isinstance(value, list):
                problems.append(
                    f"{port} must be a LIST of prediction rows, got {type(value).__name__}"
                )
        require = self.params.get("require", DEFAULT_REQUIRE)
        if members != require:
            problems.append(
                f"{members} member(s) wired but require={require} — the ensemble "
                "protocol names how many seeds it merges; wire exactly that many "
                "member_<int> ports"
            )
        return problems

    @staticmethod
    def _cell_key(row, port):
        """Give a row's cell identity, refusing a row that lacks one."""
        try:
            return tuple(row[k] for k in CELL_KEY)
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"{port}: a prediction row lacks the cell key {list(CELL_KEY)}: {exc}"
            ) from None

    def _tables(self, inputs, ports):
        """Index every member by cell, refusing duplicates and mixed hashes."""
        tables, hashes = {}, {}
        for port in ports:
            table = {}
            for row in inputs[port]:
                key = self._cell_key(row, port)
                if key in table:
                    raise ValueError(
                        f"{self.key}: {port} has duplicate cell {key} — one row per "
                        "cell per member"
                    )
                table[key] = row
            tables[port] = table
            states = {row.get("state_hash") for row in inputs[port]}
            if len(states) != 1 or None in states:
                raise ValueError(
                    f"{self.key}: {port} must carry ONE state_hash on every row (found "
                    f"{sorted(map(str, states))}) — a member is one trained checkpoint"
                )
            hashes[port] = states.pop()
        by_hash = {}
        for port, state in hashes.items():
            by_hash.setdefault(state, []).append(port)
        twice = sorted(ports for ports in by_hash.values() if len(ports) > 1)
        if twice:
            raise ValueError(
                f"{self.key}: members {twice} carry ONE state_hash each — the same "
                "checkpoint wired twice is not a second seed; wire distinct artifacts"
            )
        return tables, hashes

    def _check_agreement(self, tables, ports):
        """Refuse a member whose cells or labels differ from the first's."""
        first, base = ports[0], tables[ports[0]]
        for port in ports[1:]:
            other = tables[port]
            if set(other) != set(base):
                missing = sorted(set(base) - set(other))[:5]
                extra = sorted(set(other) - set(base))[:5]
                raise ValueError(
                    f"{self.key}: {port} covers a different cell set than {first} "
                    f"({len(other)} vs {len(base)} cells; missing {missing}, extra "
                    f"{extra}) — mixed stores or blocks?"
                )
            for key, row in base.items():
                for column in _AGREE_COLUMNS:
                    if not _same(row.get(column), other[key].get(column)):
                        raise ValueError(
                            f"{self.key}: {port} disagrees with {first} on {column!r} at "
                            f"cell {key} — members must share one store"
                        )

    def run(self, ctx, inputs):
        """Average ``q`` across the members and stamp the ensemble identity.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; ``seed_panel.json`` lands in its artifact dir.
        inputs : dict
            ``member_<int>`` -> prediction rows.

        Returns
        -------
        dict
            ``pred_rows`` and ``metrics`` (``n_members``, ``n_cells``).

        Raises
        ------
        ValueError
            On duplicate cells, mixed state hashes, a differing cell set,
            or a label/ask disagreement — naming the member.
        """
        ports = sorted(
            (p for p in inputs if _MEMBER_PORT.match(p)),
            key=lambda p: int(_MEMBER_PORT.match(p).group(1)),
        )
        tables, hashes = self._tables(inputs, ports)
        self._check_agreement(tables, ports)
        ensemble_id = hashlib.sha256(
            json.dumps(sorted(hashes.values())).encode("utf-8")
        ).hexdigest()[:_ENSEMBLE_ID_HEX]
        merged, panel = [], []
        for row in inputs[ports[0]]:
            key = self._cell_key(row, ports[0])
            qs = [float(tables[p][key]["q"]) for p in ports]
            out = {k: v for k, v in row.items() if k != "state_hash"}
            out["q"] = sum(qs) / len(qs)
            out["ensemble_id"] = ensemble_id
            merged.append(out)
            panel.append(dict(zip(CELL_KEY, key), q=qs))
        self.write_artifact(
            ctx,
            "seed_panel.json",
            {"members": ports, "state_hash": hashes, "ensemble_id": ensemble_id, "cells": panel},
        )
        self.log.info(
            "ensemble %s: %d member(s) merged over %d cell(s)", ensemble_id, len(ports), len(merged)
        )
        return {"pred_rows": merged, "metrics": {"n_members": len(ports), "n_cells": len(merged)}}


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------


def _clip(q):
    """Hold a belief inside ``[Q_EPS, 1 - Q_EPS]``."""
    return min(max(float(q), Q_EPS), 1.0 - Q_EPS)


class QHatSignal:
    """A prediction frame served as a signal speaking both protocols.

    ``predict(record)`` is the toolkit's seam (the owned ``validate`` kind
    reads it); ``q_hat(contract, p_mid, asof_ms, lead_frac)`` is the
    child's sizer seam. Both look a cell up by ``(contract,
    lead_key(lead_frac))``; ``predict`` declines (``None``) when the
    record carries no price under ``price_field`` — pricing a book with no
    mid is a question with no answer — or when the cell is uncovered,
    while ``q_hat`` answers ``p_mid`` for an uncovered cell: trust the
    market, never a fabricated belief.

    Parameters
    ----------
    table : dict
        ``(contract, lead_key) -> q``.
    price_field : str
        The record field ``predict`` requires a price under.

    Examples
    --------
    ::

        signal = QHatSignal({("KXA-1-R0", 0.5): 0.62}, "mid")
        signal.predict({"contract": "KXA-1-R0", "lead_frac": 0.5, "mid": 0.45})   # 0.62
    """

    __slots__ = ("price_field", "table")

    def __init__(self, table, price_field):
        self.table = dict(table)
        self.price_field = str(price_field)

    def _lookup(self, contract, lead_frac):
        """Find a cell's belief, or ``None``."""
        if contract is None or lead_frac is None:
            return None
        try:
            key = (str(contract), lead_key(lead_frac))
        except (TypeError, ValueError):
            return None
        return self.table.get(key)

    def predict(self, record):
        """Answer one record's belief, or ``None`` for no coverage.

        Parameters
        ----------
        record : mapping or object
            Carries ``contract``, ``lead_frac`` and the price field.

        Returns
        -------
        float or None
            The clipped belief; ``None`` without a price or a covered cell.
        """
        if _field(record, self.price_field) is None:
            return None
        q = self._lookup(_field(record, "contract"), _field(record, "lead_frac"))
        return None if q is None else _clip(q)

    def q_hat(self, contract, p_mid, asof_ms, lead_frac=None):
        """Answer the sizer's question: the belief, or the market's price.

        Parameters
        ----------
        contract : str
            The contract ticker.
        p_mid : float
            The market's own price — returned unchanged for an uncovered cell.
        asof_ms : int
            The decision instant (accepted for the protocol; the table is
            keyed by lead).
        lead_frac : float or None
            The lead fraction.

        Returns
        -------
        float
            The clipped belief, or ``p_mid``.
        """
        q = self._lookup(contract, lead_frac)
        return float(p_mid) if q is None else _clip(q)


class SignalQhat(TrainableNode):
    """Republish a prediction frame as a q̂ signal (role ``signal``).

    Translates a protocol and persists nothing: ``mode="load"`` refuses by
    name — pin the MODEL node's artifact and wire its frame here.

    Parameters
    ----------
    params : dict
        ``price_field`` (non-empty str; default :data:`DEFAULT_PRICE_FIELD`)
        — the record field ``predict`` requires a price under.

    Examples
    --------
    ::

        node = SignalQhat("qhat", {"price_field": "mid"})
        signal = node.run(ctx, {"pred_rows": ensemble["pred_rows"]})["signal"]
    """

    role = "signal"
    outputs = ("signal",)

    _PARAMS = ("price_field",)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``price_field``, empty when none.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            One problem per defect.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        price_field = params.get("price_field", DEFAULT_PRICE_FIELD)
        if not isinstance(price_field, str) or not price_field:
            problems.append(
                f"price_field must be the record field carrying the price, got {price_field!r}"
            )
        return problems

    def validate_train_inputs(self, inputs):
        """List problems with ``pred_rows``, empty when none.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            One problem when ``pred_rows`` is not a list.
        """
        rows = inputs.get("pred_rows")
        if not isinstance(rows, list):
            return [
                "pred_rows must be a LIST of prediction rows (wire an ensemble or predict "
                f"node's pred_rows), got {type(rows).__name__}"
            ]
        return []

    def run_train(self, ctx, inputs):
        """Build the lookup signal from the frame.

        Parameters
        ----------
        ctx : NodeContext
            The run frame (unused).
        inputs : dict
            ``pred_rows``.

        Returns
        -------
        dict
            ``signal`` — a :class:`QHatSignal`.

        Raises
        ------
        ValueError
            On a row without ``contract``/``lead``/``q``, or a duplicate cell.
        """
        table = {}
        for i, row in enumerate(inputs["pred_rows"]):
            try:
                key = (str(row["contract"]), lead_key(row["lead"]))
                q = float(row["q"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self.key}: pred_rows[{i}] lacks a usable contract/lead/q: {exc}"
                ) from None
            if key in table:
                raise ValueError(
                    f"{self.key}: duplicate cell {key} in pred_rows — one belief per "
                    "(contract, lead)"
                )
            table[key] = q
        price_field = self.params.get("price_field", DEFAULT_PRICE_FIELD)
        self.log.info("signal-qhat: serving %d cell(s), price field %r", len(table), price_field)
        return {"signal": QHatSignal(table, price_field)}

    def run_load(self, ctx, inputs):
        """Refuse: there is nothing to restore."""
        raise ValueError(
            f"{self.key}: mode='load' has nothing to restore — signal-qhat translates "
            "a protocol; it persists nothing. Pin the MODEL node's artifact and wire "
            "its predictions here"
        )


class MarketImpliedSignal:
    """The N0 null: the stated YES ask IS the belief.

    Examples
    --------
    ::

        MarketImpliedSignal().predict({"contract": "X", "ask": 0.3})   # 0.3
    """

    def predict(self, record):
        """Answer the record's stated YES ask, or ``None`` without one.

        Parameters
        ----------
        record : mapping or object
            Carries ``ask``.

        Returns
        -------
        float or None
            The ask.
        """
        ask = _field(record, "ask")
        return None if ask is None else float(ask)

    def q_hat(self, contract, p_mid, asof_ms, lead_frac=None):
        """Answer the market's own price.

        Parameters
        ----------
        contract : str
            The contract ticker (unused).
        p_mid : float
            The market's price.
        asof_ms : int
            The decision instant (unused).
        lead_frac : float or None
            The lead fraction (unused).

        Returns
        -------
        float
            ``p_mid``.
        """
        return float(p_mid)


class MarketImplied(TrainableNode):
    """The D-006 null baseline (role ``signal``): no inputs, no knobs.

    Every model must beat this out of sample; ``mode="load"`` refuses by
    name because the null is computed per record and persists nothing.

    Parameters
    ----------
    params : dict
        Empty — any knob is refused.

    Examples
    --------
    ::

        node = MarketImplied("n0", {})
        signal = node.run(ctx, {})["signal"]
    """

    role = "signal"
    outputs = ("signal",)

    _PARAMS = ()

    @classmethod
    def validate_params(cls, params):
        """Refuse every knob by name.

        Parameters
        ----------
        params : dict
            The declared params.

        Returns
        -------
        list of str
            One problem when any knob is declared.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        return problems

    def validate_train_inputs(self, inputs):
        """Refuse any wired port — the null reads the record it is asked about.

        Parameters
        ----------
        inputs : dict
            The materialized inputs.

        Returns
        -------
        list of str
            One problem when a port is wired.
        """
        if inputs:
            return [
                "market-implied takes no inputs — the null is computed per record, got "
                f"ports {sorted(inputs)}"
            ]
        return []

    def run_train(self, ctx, inputs):
        """Serve the null.

        Parameters
        ----------
        ctx : NodeContext
            The run frame (unused).
        inputs : dict
            Empty.

        Returns
        -------
        dict
            ``signal`` — a :class:`MarketImpliedSignal`.
        """
        self.log.info("market-implied: the N0 null baseline")
        return {"signal": MarketImpliedSignal()}

    def run_load(self, ctx, inputs):
        """Refuse: the null persists nothing."""
        raise ValueError(
            f"{self.key}: mode='load' has nothing to restore — the N0 null IS the "
            "market's stated ask, computed per record (omit mode)"
        )


#: kind name -> class: what the registry, the conformance suite and a
#: document's ``uses`` all key off.
NODE_KINDS = {
    "pmquant-ladder-panels": LadderPanels,
    "pmquant-ladder-train": LadderTrain,
    "pmquant-ladder-predict": LadderPredict,
    "pmquant-ensemble": Ensemble,
    "pmquant-signal-qhat": SignalQhat,
    "pmquant-market-implied": MarketImplied,
}

# Import = registration (``owned`` deliberately NOT set).
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

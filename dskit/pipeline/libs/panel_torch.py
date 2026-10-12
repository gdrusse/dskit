"""The tier-2 panel-torch pack: fit a horizon head on an array panel, and score it on the common grid.

A model zoo over millions of minute rows cannot hand a trainer one Python dict per row, and cannot
materialise a lookback window as columns. This pack closes that gap with three pieces, each reusing
the seam that already owns the job:

* :class:`HorizonHead` -- WHAT the net predicts per row and horizon. :class:`MixtureHead` is a
  parametric mixture trained on :class:`~dskit.pipeline.libs.predictive_cdf.MixtureLossSpec` (the one
  weighting rule); :class:`QuantileHead` is a sorted set of knots trained on the pinball loss.
  Heads live in the :data:`HEADS` registry, so a new family is a subclass, never a branch.
* :class:`PanelAdapter` -- a :class:`~dskit.pipeline.libs.torch.TorchAdapter` whose "batches" are row
  indices into a resident :class:`~dskit.pipeline.libs.array_panel.DevicePanel`: the window of a row
  is gathered by index when the batch is cut, never stored.
* :class:`PanelTrain` -- a :class:`~dskit.pipeline.libs.torch.TorchTrain` family on that adapter. It
  reads no ``rows`` port: the panel, the bands (fit, calibration, validation) and the scaler are cut
  from the declared panel and bounds. Calibration rows feed the ``val_loss`` that patience watches;
  validation rows are never uploaded. ``row_stride`` / ``stride_offset`` thin ONLY the fit rows to one
  bar in k by instant (adjacent minutes are near-duplicates; the default 1 is the whole band). It
  also returns a :class:`TorchHorizonForecaster`, the object
  :class:`~dskit.pipeline.libs.horizon_cdf.CommonGrid` scores.

**Units.** Labels arrive already divided by their scale (the array-panel contract) and nothing here
rescales them. The tail wings of a mixture head's loss and the clip bounds of a quantile head are the
SAME declared values the scoring grid uses, and the grid refuses a forecaster that disagrees.

**Memory.** Only the selected columns are resident, once per fit, and the training panel is released
in a ``finally`` before the forecaster loads a panel of its own for the rows it is asked about.

Wiring is by import path (``dskit.pipeline.libs.panel_torch:PanelTrain``): :data:`NODE_KINDS` is
empty. torch is imported only inside methods.
"""

import importlib
import json
import math
import os
from abc import ABC, abstractmethod
from types import MappingProxyType

from dskit.pipeline.binary_curve import STATUS_OK, STATUS_SUFFIX, name_ok, named_payoff, output_collision_problems
from dskit.pipeline.binary_pricing import PAYOFFS
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.array_panel import (
    DEFAULT_MISSING,
    MISSING_STRATEGIES,
    ArrayPanel,
    ColumnSelector,
    DevicePanel,
    PanelBands,
    PanelScaler,
)
from dskit.pipeline.libs.horizon_cdf import HorizonForecaster, MixtureHorizons, QuantileHorizons
from dskit.pipeline.libs.predictive_cdf import (
    MLP_ACTIVATIONS,
    MixtureLossSpec,
    mixture_parts,
    mlp_stack,
)
from dskit.pipeline.libs.torch import TorchAdapter, TorchBatches, TorchTrain
from dskit.pipeline.libs.torch_ts import SequenceEncoderAdapter, quantile_pinball_loss
from dskit.pipeline.node import Node, check_int_param, reject_unknown_params
from dskit.pipeline.records import number_ok, price_ok

__all__ = [
    "CDF_SLACK",
    "DEFAULT_ASSET_INDICATOR",
    "DEFAULT_DEVICE",
    "DEFAULT_DROPOUT",
    "DEFAULT_ROW_STRIDE",
    "DEFAULT_STRIDE_OFFSET",
    "DEFAULT_STRIDE",
    "HEADS",
    "HORIZON_RULES",
    "HorizonHead",
    "HorizonRule",
    "MixtureHead",
    "NODE_KINDS",
    "PanelAdapter",
    "PanelHorizonFairValue",
    "PanelTrain",
    "QuantileHead",
    "TorchHorizonForecaster",
]

#: Deliberately EMPTY: wired by import path, nothing registers.
NODE_KINDS = ()

#: Bar stride of a sequence window unless it declares one.
DEFAULT_STRIDE = 1
#: Dropout after each trunk activation unless the trunk declares one.
DEFAULT_DROPOUT = 0.0
#: Whether a one-hot asset code is appended to the context unless declared.
DEFAULT_ASSET_INDICATOR = False
#: Keep every bar of the fit band unless ``row_stride`` says one in k, and at which phase.
DEFAULT_ROW_STRIDE = 1
DEFAULT_STRIDE_OFFSET = 0
#: Where the panel is resident unless the node declares a ``device``.
DEFAULT_DEVICE = "cpu"

#: The band the loss trains on, and the band the ``val_loss`` monitor reads.
_FIT_BAND, _MONITOR_BAND = "fit", "cal"
#: The panel-cut knobs of ``bounds``; :class:`PanelBands` owns what each means.
_BOUND_KEYS = ("train_end_ms", "val_start_ms", "val_end_ms", "cal_days", "min_run")
_SEQUENCE_KEYS = ("columns", "window", "stride")
_TRUNK_KEYS = ("hidden", "activation", "dropout")
#: The only monitors a panel fit has: it computes losses and no belief metrics.
_PANEL_MONITORS = ("train_loss", "val_loss")


def _unknown_keys(name, spec, allowed):
    """List one problem per key of ``spec`` outside ``allowed``, each prefixed with the block's ``name``."""
    found = []
    reject_unknown_params(found, spec, allowed)
    return [f"{name}: {problem}" for problem in found]


class HorizonHead(ABC):
    """What a net predicts per row and horizon, and how that prediction is trained and read.

    A subclass names its ``kind``, its spec keys, and implements the four hooks. The raw tensor a
    net emits is ``(B, H, per_horizon)``; ``width(H)`` is the flat size the final linear layer needs.
    Build any registered head from a spec with :meth:`build` and list a spec's problems with
    :meth:`check`; both dispatch through :data:`HEADS`, so no code branches on ``kind``.

    Attributes
    ----------
    kind : str
        The registry key and the spec's ``"kind"``.
    levels : tuple or None
        The quantile levels it emits, ``None`` for a parametric head.
    training_tail : list or None
        The tail wings its loss was trained on, ``None`` when it has none.
    components : int
        Mixture components per row and horizon, which sizes a scoring chunk.

    Parameters
    ----------
    spec : dict
        The head's spec (``kind`` plus the subclass's keys).

    Raises
    ------
    ValueError
        When :meth:`problems` finds any.

    Examples
    --------
    Build a quantile head and read its flat width::

        head = HorizonHead.build({"kind": "quantile", "quantiles": [0.1, 0.5, 0.9],
                                  "bounds": [-8.0, 8.0]})
        head.width(15)   # -> 45
    """

    kind = None
    _KEYS = ()
    _REQUIRED = ()
    levels = None
    training_tail = None
    components = 1

    def __init__(self, spec):
        problems = self.problems(spec)
        if problems:
            raise ValueError("; ".join(problems))
        self.spec = dict(spec)

    @classmethod
    def check(cls, spec):
        """List the problems with a head spec, empty when none.

        Parameters
        ----------
        spec : dict
            A head spec.

        Returns
        -------
        list of str
            One problem per broken key; a ``kind`` outside :data:`HEADS` is one problem.
        """
        if not isinstance(spec, dict) or spec.get("kind") not in HEADS:
            return [f"head.kind must be one of {sorted(HEADS)}, got "
                    f"{spec.get('kind') if isinstance(spec, dict) else spec!r}"]
        return HEADS[spec["kind"]].problems(spec)

    @classmethod
    def build(cls, spec):
        """Construct the head a spec names.

        Parameters
        ----------
        spec : dict
            A head spec.

        Returns
        -------
        HorizonHead
            An instance of the registered subclass.

        Raises
        ------
        ValueError
            With every problem :meth:`check` lists.
        """
        problems = cls.check(spec)
        if problems:
            raise ValueError("; ".join(problems))
        return HEADS[spec["kind"]](spec)

    @classmethod
    def _key_problems(cls, spec):
        """Default-deny the keys: unknown ones (naming the head that owns them), missing required."""
        if not isinstance(spec, dict):
            return [f"head must be a dict, got {spec!r}"]
        if spec.get("kind") != cls.kind:
            return [f"head.kind must be {cls.kind!r}, got {spec.get('kind')!r}"]
        problems = _unknown_keys("head", {k: v for k, v in spec.items() if k != "kind"}, cls._KEYS)
        for key in sorted(set(spec) - {"kind"} - set(cls._KEYS)):
            owners = [name for name, head in HEADS.items() if key in head._KEYS]
            problems.extend(f"head.{key} belongs to the {name} head, not the {cls.kind} head"
                            for name in owners)
        problems.extend(f"head.{key} is required" for key in cls._REQUIRED if key not in spec)
        return problems

    @classmethod
    @abstractmethod
    def problems(cls, spec):
        """List the problems with a spec of this kind, empty when none."""

    @abstractmethod
    def width(self, horizons):
        """Return the flat width of the final layer for ``horizons`` horizons."""

    @abstractmethod
    def loss(self, raw, y):
        """Return the scalar training loss of ``raw`` ``(B, H, per_horizon)`` against ``y`` ``(B, H)``."""

    @abstractmethod
    def distribution(self, raw):
        """Return the :class:`~dskit.pipeline.libs.horizon_cdf.HorizonDistribution` ``raw`` describes."""


class MixtureHead(HorizonHead):
    """A parametric mixture per row and horizon, trained on the composite proper-score loss.

    The loss IS :class:`~dskit.pipeline.libs.predictive_cdf.MixtureLossSpec` over the ``B * H``
    virtual rows, so the weighting rule has one owner. Spec keys, all required except
    ``tail_intervals``: ``components`` (int ``>= 1``), ``family`` (``{"kind": "gaussian"}`` or
    ``{"kind": "student", "degrees": k}``), ``losses`` (``nll`` / ``crps`` / ``tail_crps`` terms),
    ``min_scale`` (positive scale floor) and ``tail_intervals`` (required exactly when ``tail_crps``
    is a term; the same wings the scoring grid declares). Optional ``compile`` (bool, default off)
    runs the loss through ``torch.compile``: the same value, fused into a few kernels, which is the
    difference between a launch-bound and a compute-bound step.

    Examples
    --------
    A two-component Gaussian mixture on the negative log likelihood::

        head = MixtureHead({"kind": "mixture", "components": 2, "family": {"kind": "gaussian"},
                            "losses": [{"kind": "nll", "weight": 1.0}], "min_scale": 0.1})
        head.width(3)   # -> 18
    """

    kind = "mixture"
    _KEYS = ("components", "family", "losses", "tail_intervals", "min_scale", "compile")
    _REQUIRED = ("components", "family", "losses", "min_scale")

    @classmethod
    def problems(cls, spec):
        """List the problems with a mixture spec; the loss owner judges ``family``/``losses``/wings."""
        problems = cls._key_problems(spec)
        if problems:
            return problems
        check_int_param(problems, "head.components", spec["components"], ge=1)
        if not number_ok(spec["min_scale"]) or spec["min_scale"] <= 0:
            problems.append(f"head.min_scale must be a number > 0, got {spec['min_scale']!r}")
        if not isinstance(spec.get("compile", False), bool):
            problems.append(f"head.compile must be a bool, got {spec['compile']!r}")
        elif spec.get("compile") and not (isinstance(spec["family"], dict) and spec["family"].get("kind") == "gaussian"):
            # The Student CDF series fails Triton's pass manager (torch 2.11), so it is refused up front.
            problems.append("head.compile is supported for the gaussian family only")
        try:
            MixtureLossSpec(spec["losses"], spec["family"], spec.get("tail_intervals"))
        except Exception as exc:  # noqa: BLE001 - the loss owner's refusal is the message
            problems.append(f"head.losses/family/tail_intervals: {exc}")
        return problems

    def __init__(self, spec):
        super().__init__(spec)
        self.components = int(spec["components"])
        self.min_scale = float(spec["min_scale"])
        self._spec = MixtureLossSpec(spec["losses"], spec["family"], spec.get("tail_intervals"))
        self.training_tail = self._spec.tail_intervals
        self._loss_fn = None

    def width(self, horizons):
        """Return ``3 * components * horizons``: logits, means and raw scales per horizon."""
        return 3 * self.components * horizons

    def _parts(self, raw):
        """Return ``(logw, mu, sigma)``, each ``(B * H, K)``, from ``raw`` ``(B, H, 3K)``."""
        return mixture_parts(raw.reshape(-1, raw.shape[-1]), self.min_scale)

    def _loss_of(self, raw, y):
        """Return the composite loss over every row and horizon, as ``MixtureLossSpec`` computes it."""
        return self._spec.loss(y.reshape(-1), *self._parts(raw))

    def loss(self, raw, y):
        """Return :meth:`_loss_of`, compiled on first use when the spec says ``compile``."""
        if self._loss_fn is None:
            import torch
            self._loss_fn = torch.compile(self._loss_of) if self.spec.get("compile", False) else self._loss_of
        return self._loss_fn(raw, y)

    def distribution(self, raw):
        """Return the :class:`MixtureHorizons` ``raw`` describes."""
        b, h = raw.shape[:2]
        logw, mu, sigma = (t.reshape(b, h, self.components) for t in self._parts(raw))
        return MixtureHorizons(logw, mu, sigma, self._spec.family)


class QuantileHead(HorizonHead):
    """Sorted quantile knots per row and horizon, trained on the pinball loss.

    Sorting along the level axis is the non-crossing guarantee and is differentiable, so the fit
    trains the sorted values. Spec keys, both required: ``quantiles`` (``>= 2`` strictly increasing
    levels inside ``(0, 1)``) and ``bounds`` (``[z_min, z_max]``, the same pair the scoring grid
    declares, which clips the linear tail extension). A ``losses`` key is refused: the objective is
    the pinball loss.

    Examples
    --------
    Three levels clipped to eight standardized units::

        head = QuantileHead({"kind": "quantile", "quantiles": [0.1, 0.5, 0.9],
                             "bounds": [-8.0, 8.0]})
        head.width(3)   # -> 9
    """

    kind = "quantile"
    _KEYS = ("quantiles", "bounds")
    _REQUIRED = _KEYS

    @classmethod
    def problems(cls, spec):
        """List the problems with a quantile spec; the knot owner judges levels and bounds."""
        problems = cls._key_problems(spec)
        if problems:
            return problems
        try:
            import torch
        except ImportError:  # a plan machine may have no torch; the build settles it
            return problems
        levels = spec["quantiles"]
        try:
            QuantileHorizons(levels, torch.zeros(1, 1, len(levels)), spec["bounds"])
        except Exception as exc:  # noqa: BLE001 - the knot owner's refusal is the message
            problems.append(f"head.quantiles/bounds: {exc}")
        return problems

    def __init__(self, spec):
        super().__init__(spec)
        self.levels = tuple(float(q) for q in spec["quantiles"])
        self.bounds = tuple(float(b) for b in spec["bounds"])

    def width(self, horizons):
        """Return ``len(quantiles) * horizons``."""
        return len(self.levels) * horizons

    def loss(self, raw, y):
        """Return the mean pinball loss of the sorted knots."""
        import torch
        return quantile_pinball_loss(torch.sort(raw, dim=-1).values, y, self.levels)

    def distribution(self, raw):
        """Return the :class:`QuantileHorizons` of the sorted knots, clipped to ``bounds``."""
        import torch
        return QuantileHorizons(self.levels, torch.sort(raw, dim=-1).values, self.bounds)


#: Head kind -> class. A new family registers here; nothing branches on the kind.
HEADS = {head.kind: head for head in (MixtureHead, QuantileHead)}


class _EncoderSpec:
    """A sequence encoder, declared as ``{arch, width, arch_params}`` or ``{class, params}``."""

    _ARCH_PATH = "dskit.pipeline.libs.torch_ts:ArchSequenceEncoder"
    _ARCH_KEYS = ("arch", "width", "arch_params")
    _CLASS_KEYS = ("class", "params")

    def __init__(self, spec):
        if "class" in spec:
            self.path, self.kwargs = spec["class"], dict(spec["params"])
        else:
            self.path, self.kwargs = self._ARCH_PATH, dict(spec)

    @classmethod
    def problems(cls, spec):
        """List the problems with an encoder spec; an unimportable class is left to the build."""
        if not isinstance(spec, dict):
            return [f"encoder must be a dict, got {spec!r}"]
        if "class" in spec:
            keys, required = cls._CLASS_KEYS, cls._CLASS_KEYS
        else:
            keys, required = cls._ARCH_KEYS, cls._ARCH_KEYS[:2]
        problems = _unknown_keys("encoder", spec, keys)
        problems.extend(f"encoder.{k} is required" for k in required if k not in spec)
        for key in sorted(set(spec) - set(keys)):
            problems.append(f"encoder.{key} does not belong with "
                            f"{'class' if 'class' in spec else 'arch'}")
        if problems:
            return problems
        built = cls(spec)
        if not (isinstance(built.path, str) and ":" in built.path and isinstance(built.kwargs, dict)):
            return ["encoder.class must be 'module:Name' and encoder.params a dict"]
        found = built._class()
        if found is None:
            return []
        if not (isinstance(found, type) and issubclass(found, SequenceEncoderAdapter)):
            return [f"encoder.class {built.path!r} is not a SequenceEncoderAdapter"]
        try:
            found(**built.kwargs)
        except (ValueError, TypeError) as exc:
            problems.append(f"encoder: {exc}")
        return problems

    def _class(self):
        """Return the encoder class, or ``None`` when its module cannot be imported here."""
        module, _, name = self.path.partition(":")
        try:
            return getattr(importlib.import_module(module), name, None)
        except ImportError:
            return None

    def adapter(self):
        """Build the encoder adapter, refusing a class that is missing or not an adapter.

        Raises
        ------
        ValueError
            For an unimportable class or one that is not a ``SequenceEncoderAdapter``.
        """
        found = self._class()
        if not (isinstance(found, type) and issubclass(found, SequenceEncoderAdapter)):
            raise ValueError(f"encoder class {self.path!r} is not an importable SequenceEncoderAdapter")
        return found(**self.kwargs)


class _Layout:
    """Which panel columns feed the context and the sequence, resolved against one panel."""

    def __init__(self, params, panel):
        context, sequence = params.get("context"), params.get("sequence")
        self.context = ColumnSelector(context).select(panel.columns) if context is not None else []
        self.channels = (ColumnSelector(sequence["columns"]).select(panel.columns)
                         if sequence is not None else [])
        self.window, self.stride = PanelAdapter.window_stride(params)
        self.n_assets = len(params["assets"])
        self.indicator = params.get("asset_indicator", DEFAULT_ASSET_INDICATOR)
        self.horizons = len(panel.horizons)

    def scaled_columns(self):
        """Return the sorted union of context and channel columns, the scaler's columns."""
        return sorted(set(self.context) | set(self.channels))

    def shape(self):
        """Return the data-implied module shape, recorded in the artifact sidecar."""
        return {"context_width": len(self.context) + (self.n_assets if self.indicator else 0),
                "window": self.window, "channels": len(self.channels), "horizons": self.horizons}


class _PanelState:
    """What a fit learned from the panel that the weights do not hold: scaler, column names, cuts."""

    def __init__(self, features, channels, scaler_columns, mean, std, missing, horizons,
                 rows_sha256, fingerprint):
        self.features, self.channels = list(features), list(channels)
        self.scaler_columns = list(scaler_columns)
        self.mean, self.std, self.missing = list(mean), list(std), missing
        self.horizons, self.rows_sha256, self.fingerprint = list(horizons), rows_sha256, fingerprint

    @classmethod
    def from_fit(cls, panel, layout, scaler, bands):
        """Capture the state of a fit: names rather than indices, so another panel can be mapped."""
        names = panel.columns
        return cls([names[i] for i in layout.context], [names[i] for i in layout.channels],
                   [names[i] for i in scaler.columns], scaler.mean.tolist(), scaler.std.tolist(),
                   scaler.missing, panel.horizons, bands.rows_sha256(), panel.fingerprint())

    def to_manifest(self):
        """Return the JSON-small body the artifact sidecar records."""
        return {"scaler": {"columns": self.scaler_columns, "mean": self.mean, "std": self.std,
                           "missing": self.missing},
                "features": self.features, "channels": self.channels, "horizons": self.horizons,
                "rows_sha256": self.rows_sha256, "panel_fingerprint": self.fingerprint}

    @classmethod
    def from_manifest(cls, body):
        """Rebuild the state from :meth:`to_manifest`, raising KeyError when a field is absent."""
        scaler = body["scaler"]
        return cls(body["features"], body["channels"], scaler["columns"], scaler["mean"],
                   scaler["std"], scaler["missing"], body["horizons"], body["rows_sha256"],
                   body["panel_fingerprint"])

    def resolve(self, panel):
        """Map the trained column names onto ``panel``.

        Returns
        -------
        tuple
            ``(feature_indices, channel_indices, PanelScaler)`` for ``panel``.

        Raises
        ------
        ValueError
            When ``panel`` lacks a trained column or has another horizon set.
        """
        import numpy as np

        absent = sorted({*self.features, *self.channels, *self.scaler_columns} - set(panel.columns))
        if absent:
            raise ValueError(f"panel lacks the trained column(s) {absent}")
        if list(panel.horizons) != self.horizons:
            raise ValueError(f"panel horizons {list(panel.horizons)} differ from the trained {self.horizons}")
        index = {name: i for i, name in enumerate(panel.columns)}
        scaler = PanelScaler([index[n] for n in self.scaler_columns], np.asarray(self.mean),
                             np.asarray(self.std), self.missing)
        return [index[n] for n in self.features], [index[n] for n in self.channels], scaler


class _NetBuilder:
    """Builds the encoder + context + trunk + head-layer module a set of node params describes."""

    def __init__(self, params):
        self.params = params
        self.head = HorizonHead.build(params["head"])

    def _encoder(self, shape):
        """Return ``(module, width)`` of the sequence encoder, ``(None, 0)`` without a sequence."""
        spec = self.params.get("encoder")
        if spec is None or not shape["channels"]:
            return None, 0
        adapter = _EncoderSpec(spec).adapter()
        return (adapter.build_module(shape["window"], shape["channels"]),
                int(adapter.output_width(shape["channels"])))

    def module(self, shape):
        """Build the module for a data-implied ``shape``, with no budget check."""
        import torch

        encoder, width = self._encoder(shape)
        trunk = self.params["trunk"]
        horizons, context = shape["horizons"], shape["context_width"]
        stack = mlp_stack(context + width, [int(w) for w in trunk["hidden"]], trunk["activation"],
                          trunk.get("dropout", DEFAULT_DROPOUT), self.head.width(horizons))

        class PanelNet(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.encoder, self.stack = encoder, stack

            def forward(self, context_x, sequence):
                parts = [context_x] if context_x.shape[1] else []
                if self.encoder is not None:
                    encoded = self.encoder(sequence)
                    if encoded.dim() != 2 or encoded.shape != (len(sequence), width):
                        raise ValueError("encoder violated the (rows, width) contract")
                    parts.append(encoded)
                out = self.stack(parts[0] if len(parts) == 1 else torch.cat(parts, dim=1))
                return out.reshape(out.shape[0], horizons, -1)

        return PanelNet()

    @staticmethod
    def count(module):
        """Return the number of trainable parameters of ``module``."""
        return sum(p.numel() for p in module.parameters())

    def build(self, shape):
        """Build the module and hold it to ``max_parameters``.

        Raises
        ------
        ValueError
            ``parameter budget exceeded`` when the module has more parameters than declared.
        """
        module = self.module(shape)
        limit = self.params.get("max_parameters")
        if limit is not None and self.count(module) > limit:
            raise ValueError(f"parameter budget exceeded: {self.count(module)} > max_parameters {limit}")
        return module


class PanelAdapter(TorchAdapter):
    """Batches of row indices into a resident panel; windows are gathered when a batch is cut.

    The adapter's "split" is an index array into one :class:`DevicePanel` shared by the fit and
    calibration sets. It builds the bands, the scaler (fit on the fit band only) and the device
    panel on the first :meth:`prepare`, answers the data-implied module shape, and persists the
    scaler and column names beside the weights so a restored model can be applied to a panel.
    It computes its own objective through the head, so a node ``loss`` knob is refused.

    Parameters
    ----------
    params : dict
        The node's params (:class:`PanelTrain`'s knobs).

    Examples
    --------
    The trainer builds one for you; to read the shape a panel implies::

        shape = PanelAdapter.shape_for(params, ArrayPanel.open("/store/panel"))
        shape["context_width"]
    """

    requires_features = False
    #: The bands ``row_stride`` thins; the others are read whole (``cal`` feeds ``val_loss``).
    _THINNED_BANDS = (_FIT_BAND,)

    def __init__(self, params=None):
        super().__init__(params)
        self.bands = None
        self.state = None
        self._dev = None
        self._layout = None
        self._head = None

    @classmethod
    def open_panel(cls, spec):
        """Open the declared panel through the store (``ArrayPanel.from_source``)."""
        return ArrayPanel.from_source(spec)

    @classmethod
    def window_stride(cls, params):
        """Return ``(window, stride)`` from a ``sequence`` block, ``(1, 1)`` without one."""
        sequence = params.get("sequence")
        if sequence is None:
            return 1, 1
        return int(sequence["window"]), int(sequence.get("stride", DEFAULT_STRIDE))

    @classmethod
    def make_bands(cls, params, panel):
        """Cut the bands the params declare on ``panel``: the one place training and scoring agree."""
        return PanelBands(panel, params["assets"], **{k: params["bounds"][k] for k in _BOUND_KEYS})

    @classmethod
    def shape_for(cls, params, panel):
        """Return the module shape ``params`` imply on ``panel`` (context width, window, channels, horizons)."""
        return _Layout(params, panel).shape()

    @property
    def head(self):
        """The :class:`HorizonHead` the params declare, built once."""
        if self._head is None:
            self._head = HorizonHead.build(self.params["head"])
        return self._head

    @property
    def device_panel(self):
        """The resident training :class:`DevicePanel`, ``None`` before the first prepare and after release."""
        return self._dev

    def _open(self, params):
        """Open the panel, cut the bands, fit the scaler on the fit band, upload fit + calibration."""
        if self._dev is not None:
            return
        panel = self.open_panel(params["panel"])
        bands = self.make_bands(params, panel)
        layout = _Layout(params, panel)
        scaler = PanelScaler.fit(panel, layout.scaled_columns(), bands.fit_idx,
                                 params.get("missing", DEFAULT_MISSING))
        self._dev = DevicePanel.load(
            panel, bands, layout.context, layout.channels,
            bands.spans(_FIT_BAND) + bands.spans(_MONITOR_BAND), scaler,
            params.get("device") or DEFAULT_DEVICE, layout.window, layout.stride)
        self.bands, self._layout = bands, layout
        self.state = _PanelState.from_fit(panel, layout, scaler, bands)

    def band_rows(self, params, where):
        """Return the global rows of band ``where``, thinned by ``row_stride`` if the band is thinned.

        The scaler and the resident span stay cut from the whole band: thinning picks which rows
        the loss visits, and a thinned row still gathers its full window.
        """
        if where not in self._THINNED_BANDS:
            return self.bands.rows(where)
        return self.bands.thinned_rows(where, params.get("row_stride", DEFAULT_ROW_STRIDE),
                                       params.get("stride_offset", DEFAULT_STRIDE_OFFSET))

    def prepare(self, rows, params, *, where):
        """Return the index set of the ``fit`` or ``cal`` band; ``rows`` must be ``None``.

        The ``fit`` set is thinned by ``row_stride`` / ``stride_offset``; ``cal`` never is.

        Raises
        ------
        ValueError
            For wired rows, a band other than ``fit`` / ``cal`` (validation rows are never read), or
            a ``row_stride`` that leaves the fit band no rows.
        """
        import torch

        if rows is not None or where not in (_FIT_BAND, _MONITOR_BAND):
            raise ValueError(f"PanelAdapter reads the panel's '{_FIT_BAND}' and '{_MONITOR_BAND}' "
                             f"bands and no rows; got where={where!r}")
        self._open(params)
        index = torch.as_tensor(self.band_rows(params, where), dtype=torch.long,
                                device=self._dev.device)
        return TorchBatches(len(index), (self._dev, index))

    def module_params(self, batches, params):
        """Return the data-implied module shape."""
        return self._layout.shape()

    def select(self, batches, index):
        """Gather the batch at positions ``index`` of the split (the whole split when ``None``)."""
        dev, rows = batches.payload
        return dev.gather(rows if index is None else rows[index])

    def index_device(self, batches):
        """Return the resident panel's device: the order is uploaded once per epoch."""
        return batches.payload[0].device

    def to_device(self, batch, device):
        """Return ``batch`` unchanged: the panel is already resident where the fit runs."""
        return batch

    def _codes(self, group):
        """Return the declared asset codes on ``group``'s device and dtype, uploaded once."""
        import torch

        key = (group.dtype, group.device, tuple(self.params["assets"]))
        if getattr(self, "_code_key", None) != key:
            self._code_key = key
            self._code_tensor = torch.as_tensor(self.params["assets"], dtype=group.dtype, device=group.device)
        return self._code_tensor

    def model_inputs(self, batch):
        """Return ``(context, sequence)``: the flat features plus the one-hot asset code, if declared."""
        import torch

        context = batch.x
        if self.params.get("asset_indicator", DEFAULT_ASSET_INDICATOR):
            codes = self._codes(batch.group)
            context = torch.cat([context, (batch.group[:, None] == codes).to(context.dtype)], dim=1)
        return context, batch.seq

    def loss(self, module, batch):
        """Return the head's training loss on one gathered batch."""
        return self.head.loss(module(*self.model_inputs(batch)), batch.y)

    def predict(self, module, record):
        """Return ``None``: serving goes through the forecaster, not a per-record signal."""
        return None

    def release(self):
        """Free the resident training panel; safe to call twice."""
        if self._dev is not None:
            self._dev.release()
            self._dev = None
        self._code_key = self._code_tensor = None

    def save_state(self, prefix):
        """Return the panel state (scaler, column names, band hash) for the sidecar."""
        return {"panel": self.state.to_manifest()}

    def load_state(self, prefix, recorded):
        """Restore the panel state, raising when the sidecar holds none."""
        self.state = _PanelState.from_manifest(recorded["panel"])


class TorchHorizonForecaster(HorizonForecaster):
    """Forecast a fitted panel net for any rows of a panel, as a :class:`HorizonDistribution`.

    It owns a copy of nothing the fit held on a device: each :meth:`forecast` loads the rows' span
    (extended back by the window) into a panel of its own and releases it afterwards, so the
    training panel can be freed first. Windows are addressed by global row index, a row whose run is
    shorter than the window is refused, and a panel lacking a trained column is refused.

    Parameters
    ----------
    module : torch.nn.Module
        The fitted net.
    adapter : PanelAdapter
        Supplies the head, the saved scaler and the declared window, stride, assets and bounds.
    device : str
        Where to run the forward pass.

    Examples
    --------
    :class:`PanelTrain` returns one on its ``model`` output; to score it::

        summary = CommonGrid(grid_params).evaluate(model, panel, rows, y)
    """

    def __init__(self, module, adapter, device):
        self._module, self._adapter, self._device = module, adapter, device
        self.head = adapter.head
        self.levels, self.training_tail = self.head.levels, self.head.training_tail
        self.components = self.head.components
        self._bands = {}
        if adapter.bands is not None:
            self._bands[adapter.bands.panel.directory] = adapter.bands

    #: The sidecar's content hash when this forecaster was restored from an artifact, else ``None``.
    state_hash = None

    @property
    def train_end_ms(self):
        """The last instant the fit used: the ``bounds.train_end_ms`` its params recorded."""
        return self._adapter.params["bounds"]["train_end_ms"]

    @classmethod
    def restore(cls, artifact, device=None):
        """Restore a saved forecaster from its ``model.pt`` alone, its params read from the sidecar beside it.

        Parameters
        ----------
        artifact : str
            The path of a ``model.pt`` that :class:`PanelTrain` wrote (its ``model.json`` sits beside it).
        device : str, optional
            Replaces the device the fit recorded (a fit on ``cuda`` can be served on ``cpu``).

        Returns
        -------
        TorchHorizonForecaster
            With :attr:`state_hash` set to the sidecar's content hash.

        Raises
        ------
        ValueError
            The artifact is missing, its sidecar is unreadable, or the state does not restore (the loader's
            own refusals, which name the artifact).

        Examples
        --------
        Serve a fit on the CPU::

            forecaster = TorchHorizonForecaster.restore("models/gru/block-1/model.pt", device="cpu")
            forecaster.state_hash
            # -> the sha256 the sidecar recorded
        """
        sidecar_path = os.path.splitext(artifact)[0] + ".json"
        try:
            with open(sidecar_path, "r", encoding="utf-8") as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read the sidecar {sidecar_path!r} of artifact {artifact!r}: {exc}") from exc
        params = dict(sidecar["params"])
        if device is not None:
            params["device"] = device
        forecaster = PanelTrain("model", params, mode="load", artifact=artifact).run_load(None, {})["model"]
        forecaster.state_hash = sidecar["state_hash"]
        return forecaster

    def usable(self, panel, rows):
        """Say which ``rows`` it can forecast: a trained asset whose run covers the window.

        Parameters
        ----------
        panel : ArrayPanel
            The panel the rows index.
        rows : sequence of int
            Global row indices.

        Returns
        -------
        numpy.ndarray
            One bool per row; False for an untrained asset or a window that would bridge a gap
            (the two cases :meth:`forecast` refuses).
        """
        import numpy as np

        rows = np.asarray(rows, dtype=np.int64)
        window, stride = self._adapter.window_stride(self._adapter.params)
        trained = np.isin(np.asarray(panel.group)[rows], self._adapter.params["assets"])
        return trained & (np.asarray(panel.run)[rows] >= (window - 1) * stride + 1)

    def _bands_for(self, panel):
        """Return the bands cut on ``panel``, memoized per panel directory (the scan is not cheap)."""
        if panel.directory not in self._bands:
            self._bands[panel.directory] = self._adapter.make_bands(self._adapter.params, panel)
        return self._bands[panel.directory]

    def _spans(self, panel, rows):
        """Return one half-open ``(first, last + 1)`` span per group the rows touch."""
        import numpy as np

        groups = np.asarray(panel.group)[rows]
        return [(int(rows[groups == g].min()), int(rows[groups == g].max()) + 1)
                for g in np.unique(groups)]

    def forecast(self, panel, rows, chunk_rows):
        """Forecast ``rows`` of ``panel``.

        Parameters
        ----------
        panel : ArrayPanel
            The panel to read; it must hold the trained columns.
        rows : sequence of int
            Global row indices, each in a trained asset and with a run covering the window.
        chunk_rows : int
            Rows per forward pass.

        Returns
        -------
        HorizonDistribution
            One distribution per requested row, in order.

        Raises
        ------
        ValueError
            For a missing trained column, a row of an untrained asset, or a window that would
            bridge a gap.
        """
        import numpy as np
        import torch

        rows = np.asarray(rows, dtype=np.int64)
        features, channels, scaler = self._adapter.state.resolve(panel)
        untrained = sorted(set(np.asarray(panel.group)[rows].tolist()) - set(self._adapter.params["assets"]))
        if untrained:
            raise ValueError(f"rows of group(s) {untrained} were not trained on")
        window, stride = self._adapter.window_stride(self._adapter.params)
        dev = DevicePanel.load(panel, self._bands_for(panel), features, channels,
                               self._spans(panel, rows), scaler, self._device, window, stride)
        try:
            self._module.to(self._device).eval()
            with torch.no_grad():
                raw = torch.cat([self._module(*self._adapter.model_inputs(dev.gather(rows[i:i + chunk_rows])))
                                 for i in range(0, len(rows), chunk_rows)])
        finally:
            dev.release()
        return self.head.distribution(raw)


class _PanelKnobs:
    """Plan-time checks of the knobs :class:`PanelTrain` adds to the base trainer's."""

    _REQUIRED = ("panel", "assets", "bounds", "trunk", "head")

    @classmethod
    def problems(cls, params):
        """List the problems with the panel-specific knobs, empty when none."""
        problems = [f"{k} is required" for k in cls._REQUIRED if k not in params]
        for check in (cls.panel, cls.assets, cls.bounds, cls.inputs, cls.trunk, cls.head,
                      cls.scalars):
            problems.extend(check(params))
        return problems

    @classmethod
    def panel(cls, params):
        """Check ``panel`` is a dict; the store owner refuses unknown or missing keys at open."""
        value = params.get("panel")
        if value is None or is_node_ref(value) or isinstance(value, dict):
            return []
        return [f"panel must be a dict naming a localblobs stream, got {value!r}"]

    @classmethod
    def assets(cls, params):
        """Check ``assets`` and ``asset_indicator``."""
        problems, assets = [], params.get("assets")
        if assets is not None and not is_node_ref(assets) and not (
                isinstance(assets, list) and assets and len(set(assets)) == len(assets)
                and all(type(a) is int for a in assets)):
            problems.append(f"assets must be a non-empty list of distinct int group codes, got {assets!r}")
        flag = params.get("asset_indicator", DEFAULT_ASSET_INDICATOR)
        if not is_node_ref(flag) and not isinstance(flag, bool):
            problems.append(f"asset_indicator must be a bool, got {flag!r}")
        return problems

    @classmethod
    def bounds(cls, params):
        """Check ``bounds`` carries exactly the five cuts, each an int or a reference."""
        value = params.get("bounds")
        if value is None or is_node_ref(value):
            return []
        if not isinstance(value, dict):
            return [f"bounds must be a dict, got {value!r}"]
        problems = _unknown_keys("bounds", value, _BOUND_KEYS)
        for key in _BOUND_KEYS:
            if key not in value:
                problems.append(f"bounds.{key} is required")
            elif not is_node_ref(value[key]):
                check_int_param(problems, f"bounds.{key}", value[key], ge=0)
        return problems

    @classmethod
    def selector(cls, name, spec):
        """Check a column-selector spec by constructing it."""
        try:
            ColumnSelector(spec)
        except ValueError as exc:
            return [f"{name}: {exc}"]
        return []

    @classmethod
    def inputs(cls, params):
        """Check ``context`` / ``sequence`` / ``encoder`` and that the encoder and sequence pair up."""
        context, sequence, encoder = (params.get(k) for k in ("context", "sequence", "encoder"))
        if any(is_node_ref(v) for v in (context, sequence, encoder)):
            return []
        problems = []
        if context is None and sequence is None:
            problems.append("context: at least one of context or sequence must select columns")
        if context is not None:
            problems.extend(cls.selector("context", context) if isinstance(context, dict)
                            else [f"context must be a selector dict, got {context!r}"])
        if sequence is not None:
            problems.extend(cls.sequence(sequence))
        if sequence is not None and encoder is None:
            problems.append("encoder: a sequence needs an encoder (null encoder is the context-only MLP)")
        if encoder is not None and sequence is None:
            problems.append("encoder: an encoder needs a sequence to encode")
        if encoder is not None:
            problems.extend(_EncoderSpec.problems(encoder))
        return problems

    @classmethod
    def sequence(cls, sequence):
        """Check one ``sequence`` block."""
        if not isinstance(sequence, dict):
            return [f"sequence must be a dict, got {sequence!r}"]
        problems = _unknown_keys("sequence", sequence, _SEQUENCE_KEYS)
        for key in ("columns", "window"):
            if key not in sequence:
                problems.append(f"sequence.{key} is required")
        if "columns" in sequence:
            problems.extend(cls.selector("sequence.columns", sequence["columns"]) if isinstance(
                sequence["columns"], dict) else ["sequence.columns must be a selector dict"])
        for key in ("window", "stride"):
            if key in sequence:
                check_int_param(problems, f"sequence.{key}", sequence[key], ge=1)
        return problems

    @classmethod
    def trunk(cls, params):
        """Check the trunk: hidden widths, activation name, dropout."""
        value = params.get("trunk")
        if value is None or is_node_ref(value):
            return []
        if not isinstance(value, dict):
            return [f"trunk must be a dict, got {value!r}"]
        problems = _unknown_keys("trunk", value, _TRUNK_KEYS)
        hidden = value.get("hidden")
        if not (isinstance(hidden, list) and hidden):
            problems.append(f"trunk.hidden must be a non-empty list of widths, got {hidden!r}")
        else:
            for width in hidden:
                check_int_param(problems, "trunk.hidden entry", width, ge=1)
        if value.get("activation") not in MLP_ACTIVATIONS:
            problems.append(f"trunk.activation must be one of {sorted(MLP_ACTIVATIONS)}, "
                            f"got {value.get('activation')!r}")
        dropout = value.get("dropout", DEFAULT_DROPOUT)
        if not number_ok(dropout) or not 0 <= dropout < 1:
            problems.append(f"trunk.dropout must be a number in [0, 1), got {dropout!r}")
        return problems

    @classmethod
    def head(cls, params):
        """Check the head spec through its registered class."""
        value = params.get("head")
        return [] if value is None or is_node_ref(value) else HorizonHead.check(value)

    @classmethod
    def scalars(cls, params):
        """Check ``max_parameters``, ``missing``, and the ``row_stride`` / ``stride_offset`` pair."""
        problems = []
        if "max_parameters" in params and not is_node_ref(params["max_parameters"]):
            check_int_param(problems, "max_parameters", params["max_parameters"], ge=1)
        missing = params.get("missing", DEFAULT_MISSING)
        if not is_node_ref(missing) and missing not in MISSING_STRATEGIES:
            problems.append(f"missing must be one of {MISSING_STRATEGIES}, got {missing!r}")
        problems.extend(cls.row_stride(params))
        return problems

    @classmethod
    def row_stride(cls, params):
        """Check the thinning pair through :meth:`PanelBands.stride_problems`, which owns its bounds."""
        pair = tuple(params.get(k, d) for k, d in (("row_stride", DEFAULT_ROW_STRIDE),
                                                   ("stride_offset", DEFAULT_STRIDE_OFFSET)))
        return [] if any(is_node_ref(v) for v in pair) else PanelBands.stride_problems(*pair)


class PanelTrain(TorchTrain):
    """Fit an encoder + trunk + horizon head on an array panel, with no ``rows`` port.

    The panel, the bands and the scaler come from the declared knobs; windows are gathered by index
    from one resident :class:`DevicePanel`. The calibration band feeds ``val_loss`` (what
    ``patience`` watches); validation rows are never uploaded. ``features``, ``label`` and ``loss``
    are refused by name: the head owns the objective. ``mode: load`` restores the weights, the
    scaler and the column names; restate the trained params on a load node (they are cross-checked).

    Parameters
    ----------
    params : dict
        Required: ``panel`` (a ``localblobs`` stream: ``root``, ``source``, ``stream``,
        ``relpath``), ``assets`` (distinct int group codes), ``bounds`` (``train_end_ms``,
        ``val_start_ms``, ``val_end_ms``, ``cal_days``, ``min_run``), ``trunk`` (``hidden``,
        ``activation``, optional ``dropout``), ``head`` (see :class:`MixtureHead`,
        :class:`QuantileHead`), and ``context`` and/or ``sequence``. Optional: ``sequence``
        (``columns``, ``window``, optional ``stride``) with its ``encoder`` (``{arch, width,
        arch_params}`` or ``{class, params}`` of a ``SequenceEncoderAdapter``; absent for a
        context-only MLP), ``context`` (a column selector), ``asset_indicator`` (bool), ``max_parameters``
        (int), ``missing`` (``refuse`` or ``mean``), ``row_stride`` (int ``>= 1``, default 1) with
        ``stride_offset`` (int in ``[0, row_stride)``, default 0), which thin only the FIT rows to
        those whose bar index ``(time // step_ms) % row_stride`` equals the offset (calibration,
        validation, the scaler and every window are untouched), plus the base trainer's ``device``, ``epochs``,
        ``lr``, ``optimizer``, ``loader``, ``monitor`` (``train_loss`` or ``val_loss``),
        ``patience``.

    Examples
    --------
    A GRU over 60 two-minute bars plus the flat context, a mixture head::

        node = PanelTrain("model", {
            "panel": {"root": root, "source": "px", "stream": "files", "relpath": "panel"},
            "assets": [10], "bounds": {"train_end_ms": te, "val_start_ms": vs, "val_end_ms": ve,
                                       "cal_days": 30, "min_run": 119},
            "context": {"prefixes": ["f"]},
            "sequence": {"columns": {"prefixes": ["seq__"]}, "window": 60, "stride": 2},
            "encoder": {"arch": "gru", "width": 16, "arch_params": {"hidden_size": 16}},
            "trunk": {"hidden": [32], "activation": "relu"},
            "head": {"kind": "quantile", "quantiles": [0.1, 0.5, 0.9], "bounds": [-8.0, 8.0]},
            "epochs": 5, "monitor": "val_loss", "patience": 2})
        out = node.run(ctx, {})   # out["model"] is the TorchHorizonForecaster
    """

    outputs = ("signal", "artifact_path", "metrics", "model")
    _ADAPTER = PanelAdapter
    _BASE_PARAMS = tuple(p for p in TorchTrain._BASE_PARAMS if p not in ("features", "label"))
    _EXTRA_PARAMS = ("panel", "assets", "asset_indicator", "bounds", "sequence", "context",
                     "encoder", "trunk", "head", "max_parameters", "missing", "row_stride",
                     "stride_offset")
    _MONITORS = _PANEL_MONITORS

    @classmethod
    def _features_required(cls, params):
        """Say no: there is no ``features`` knob, the selectors name the columns."""
        return False

    @classmethod
    def validate_params(cls, params):
        """List the problems with the base knobs and the panel knobs, empty when none."""
        problems = list(super().validate_params(params))
        problems.extend(_PanelKnobs.problems(params))
        return problems

    @classmethod
    def parameter_count(cls, params):
        """Count the parameters ``params`` would train, reading only the panel manifest.

        Parameters
        ----------
        params : dict
            A full set of node params.

        Returns
        -------
        int
            The module's trainable parameter count; compare it with ``max_parameters`` to pick the
            first admissible architecture without fitting.

        Raises
        ------
        ValueError
            When the params have plan-time problems.
        """
        problems = cls.validate_params(params)
        if problems:
            raise ValueError("; ".join(problems))
        shape = cls._ADAPTER.shape_for(params, cls._ADAPTER.open_panel(params["panel"]))
        return _NetBuilder.count(_NetBuilder(params).module(shape))

    def validate_train_inputs(self, inputs):
        """Refuse a wired ``rows`` or ``val_rows``: this trainer reads the panel, not rows."""
        return [f"{port} is not a port of {type(self).__name__}: it reads the declared panel, wire nothing"
                for port in ("rows", "val_rows") if inputs.get(port) is not None]

    def build_module(self, params):
        """Build the net for the data-implied shape, held to ``max_parameters``."""
        if not self._data_params:
            raise ValueError(f"{self.key}: the module shape comes from the panel; fit (or load) first")
        return _NetBuilder(params).build(self._data_params)

    def _fit_datasets(self, adapter, inputs, features, label):
        """Return the fit and calibration index sets cut from the panel bands."""
        return (adapter.prepare(None, self.params, where=_FIT_BAND),
                adapter.prepare(None, self.params, where=_MONITOR_BAND))

    def _forecaster(self, signal):
        """Wrap a signal's module and adapter as the forecaster output."""
        return TorchHorizonForecaster(signal.module, signal.adapter,
                                      self.params.get("device") or DEFAULT_DEVICE)

    def run_train(self, ctx, inputs):
        """Fit, free the training panel, and add the forecaster as ``model``.

        Returns
        -------
        dict
            The base trainer's ``signal``, ``artifact_path``, ``metrics``, plus ``model``.
        """
        try:
            out = super().run_train(ctx, inputs)
        finally:
            if self._adapter is not None:
                self._adapter.release()
        out["model"] = self._forecaster(out["signal"])
        return out

    def run_load(self, ctx, inputs):
        """Restore the artifact, scaler and column names, and add the forecaster as ``model``."""
        out = super().run_load(ctx, inputs)
        out["model"] = self._forecaster(out["signal"])
        return out


#: How far outside ``[0, 1]`` a computed CDF may stray, in machine epsilons of its dtype, and be read as rounding
#: (it is then clipped into ``[0, 1]``); anything further is a defect and refuses.
CDF_SLACK = 16

#: The statuses :class:`PanelHorizonFairValue` writes, in the order its checks run.
_STATUSES = (STATUS_OK, "bad_input", "unknown_group", "horizon_out_of_range", "no_panel_row", "bad_scale",
             "window_gap")
_SOURCE_PARAMS = ("artifact", "state_hash", "device")
_NAME_PARAMS = ("group_field", "time_field", "horizon_field", "reference_field", "payoff_field", "lower_field",
                "upper_field", "fair_field")
_INT_PARAMS = (("horizon_unit_ms", 1), ("chunk_rows", 1))
_HORIZON_SUFFIX, _SCALE_SUFFIX = "_horizon", "_scale"


class HorizonRule(ABC):
    """How a requested horizon, in bars, picks one of a panel's label horizons.

    Abstract: a subclass names itself and supplies :meth:`pick`. The registry is :data:`HORIZON_RULES`, so a new
    rule is a subclass listed there, never a branch.

    Examples
    --------
    A rule that takes the next horizon up::

        class Ceiling(HorizonRule):
            name = "ceiling"
            def pick(self, target, horizons):
                above = [h for h in horizons if h >= target]
                return above[0] if above else None
    """

    #: The value a document's ``horizon_rule`` carries.
    name = ""

    @abstractmethod
    def pick(self, target, horizons):
        """Return the chosen horizon in bars, or None when the rule places ``target`` nowhere.

        Parameters
        ----------
        target : float
            The requested horizon in bars (positive, possibly fractional).
        horizons : list of int
            The panel's label horizons, ascending.

        Returns
        -------
        int or None
            One of ``horizons``, or None to refuse (never a clamp to the nearest end).
        """


class _Nearest(HorizonRule):
    """The horizon closest to the target (the shorter on a tie); a target outside the label range is refused."""

    name = "nearest"

    def pick(self, target, horizons):
        """See :meth:`HorizonRule.pick`."""
        if not horizons[0] <= target <= horizons[-1]:
            return None
        return min(horizons, key=lambda h: (abs(h - target), h))


class _Floor(HorizonRule):
    """The longest horizon not past the target; a target outside the label range is refused."""

    name = "floor"

    def pick(self, target, horizons):
        """See :meth:`HorizonRule.pick`."""
        if not horizons[0] <= target <= horizons[-1]:
            return None
        return max(h for h in horizons if h <= target)


class _Exact(HorizonRule):
    """The target itself, only when it is one of the label horizons."""

    name = "exact"

    def pick(self, target, horizons):
        """See :meth:`HorizonRule.pick`."""
        return int(target) if target in horizons else None


#: The horizon rules by name.
HORIZON_RULES = MappingProxyType({cls.name: cls() for cls in (_Nearest, _Floor, _Exact)})


class _Slot:
    """What the node worked out for one decision row: a status, and when ``ok`` what to evaluate."""

    __slots__ = ("status", "panel_row", "bars", "column", "scale", "reference", "levels", "geometry", "bounds", "fair")

    def __init__(self, status=STATUS_OK):
        self.status, self.panel_row, self.bars, self.column = status, None, None, None
        self.scale, self.reference, self.levels, self.geometry, self.bounds, self.fair = None, None, (), None, (), None


class _PanelIndex:
    """Finds the global row of a ``(group code, instant)`` in a panel stored sorted by time within each group."""

    def __init__(self, panel, codes):
        import numpy as np

        group, time = np.asarray(panel.group), np.asarray(panel.time)
        self._by_code = {}
        for code in sorted(set(codes)):
            rows = np.flatnonzero(group == code)
            if len(rows) > 1 and not bool((np.diff(time[rows]) > 0).all()):
                raise ValueError(f"panel group {code} is not sorted by strictly increasing time")
            self._by_code[code] = (rows, time[rows])

    def find(self, code, instant):
        """Return the global row of ``code`` at ``instant``, or None when the panel has no such row."""
        import numpy as np

        rows, times = self._by_code.get(code, (None, None))
        if rows is None or not len(rows):
            return None
        at = int(np.searchsorted(times, instant))
        return int(rows[at]) if at < len(rows) and int(times[at]) == instant else None


class PanelHorizonFairValue(Node):
    """P(YES) of a binary contract from a horizon forecaster's CDF at each decision instant (role ``transform``).

    For every decision row the node (1) takes the panel row of the row's asset at the decision instant, (2) picks the
    label horizon of the row's lead by the declared ``horizon_rule``, (3) asks the forecaster for that row's
    distribution, (4) reads the contract's strikes in the model's own unit,
    ``z = ln(strike / reference) / scale`` where ``scale`` is the panel's per-row ``reference`` (the divisor its labels
    were standardised by) and ``reference`` the row's current level, and (5) prices the contract through the payoff
    geometries of :data:`~dskit.pipeline.binary_pricing.PAYOFFS` from the survival ``P(S >= K) = 1 - F(z)`` at the
    chosen horizon. The CDF is evaluated AT the strike (exact for a parametric head, piecewise linear for a quantile
    head), so no knot grid is built or interpolated; ``CurveBinaryFairValue`` is the sibling for a curve that arrives
    as rows.

    Timing and approximations. The panel row for a decision instant ``I`` is the bar that closed just before it, so
    ``row_offset_ms`` (``-1`` for bars stamped by their last millisecond) maps ``I`` to the row's stamp. The horizon is
    the lead from ``I``, taken as a whole number of bars by the rule: it ignores the execution lag between the
    information instant and the fill (a few seconds), and it prices the level at the settlement instant, whereas an
    index that settles on the average of its last 60 seconds is centred about 30 seconds earlier and has less variance
    (about a third of that minute's point variance): the CDF is slightly too wide, most at short leads. Both are
    stated so a reader does not credit the model with precision it does not have.

    Look-ahead guard. A forecaster must state ``train_end_ms``; a decision whose panel stamp is not strictly after
    ``train_end_ms + panel.label_span_ms`` (the last training label reaches that far) refuses the whole run, and so does
    a forecaster that states none. Rows that cannot be priced for lack of data are NOT refusals: they are rows
    with ``<fair_field>`` None and a status in the census (an unknown group, a missing panel row, a window that would
    bridge a gap, a lead the rule cannot place, a non-positive scale or bad contract columns). A forecast that comes
    back non-finite, or a CDF outside ``[0, 1]`` beyond rounding, is a defect of the model and refuses.

    Inputs: ``records`` (decision rows) and, unless ``artifact`` is declared, ``model`` (a
    :class:`~dskit.pipeline.libs.horizon_cdf.HorizonForecaster`). Outputs: ``records`` (copies with ``<fair_field>``,
    ``<fair_field>_status``, ``<fair_field>_horizon`` (bars used) and ``<fair_field>_scale``) and ``census``.

    Parameters
    ----------
    params : dict
        All REQUIRED unless marked. ``panel`` (the :meth:`ArrayPanel.from_source` spec of the panel that holds the
        decision instants); ``group_field`` and ``group_codes`` (a row's cell there -> the panel's integer group
        code); ``time_field`` (the decision instant, epoch ms) and ``row_offset_ms`` (added to it to get the panel
        row's stamp); ``horizon_field`` (the lead, a positive number) and ``horizon_unit_ms`` (what one unit of it
        is, in ms); ``horizon_rule`` (a key of :data:`HORIZON_RULES`); ``reference_field`` (the level the strikes
        are measured against); ``payoff_field``, ``lower_field``, ``upper_field`` (as ``BinaryFairValue``);
        ``fair_field`` (the output column); ``chunk_rows`` (rows per forecast pass). OPTIONAL: ``artifact`` (a
        ``model.pt`` path to restore the forecaster from, instead of the ``model`` port) with ``state_hash`` (its
        sidecar's content hash, REQUIRED with ``artifact`` and refused without it, so the model is part of the
        document's identity) and ``device`` (replaces the fit's recorded device).

    Examples
    --------
    Price 15-minute up/down contracts from a saved forecaster, decisions two, five and ten minutes out::

        node = PanelHorizonFairValue("fair_gru", {
            "panel": {"root": store, "source": "panel-minute", "stream": "files", "relpath": "minute-w1w4"},
            "artifact": "models/gru/model.pt", "state_hash": "<sha256 from model.json>",
            "group_field": "series", "group_codes": {"KXBTC15M": 0, "KXETH15M": 1},
            "time_field": "decision_ms", "row_offset_ms": -1,
            "horizon_field": "lead_minutes", "horizon_unit_ms": 60000, "horizon_rule": "nearest",
            "reference_field": "spot_brti", "payoff_field": "payoff", "lower_field": "floor_strike",
            "upper_field": "cap_strike", "fair_field": "fair_gru", "chunk_rows": 512})
        out = node.run(ctx, {"records": decision_rows})
        # -> out["records"][0]["fair_gru"] is P(YES), or None with fair_gru_status saying why
    """

    role = "transform"
    outputs = ("records", "census")
    _PARAMS = ("panel", "group_codes", "row_offset_ms", "horizon_rule", *_NAME_PARAMS, *_SOURCE_PARAMS,
               *(name for name, _ in _INT_PARAMS))

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown, missing or unusable knob.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems += [f"{name} is required: a non-empty column name, got {params.get(name)!r}"
                     for name in _NAME_PARAMS if not name_ok(params.get(name))]
        if not isinstance(params.get("panel"), dict):
            problems.append(f"panel is required: an ArrayPanel.from_source spec dict, got {params.get('panel')!r}")
        codes = params.get("group_codes")
        if not (isinstance(codes, dict) and codes and all(isinstance(v, int) and not isinstance(v, bool)
                                                          for v in codes.values())):
            problems.append(f"group_codes is required: a non-empty map cell -> integer group code, got {codes!r}")
        offset = params.get("row_offset_ms")
        if isinstance(offset, bool) or not isinstance(offset, int):
            problems.append(f"row_offset_ms is required: an integer number of ms, got {offset!r}")
        for name, floor in _INT_PARAMS:
            check_int_param(problems, name, params.get(name), ge=floor)
        if params.get("horizon_rule") not in HORIZON_RULES:
            problems.append(f"horizon_rule is required: one of {sorted(HORIZON_RULES)}, got {params.get('horizon_rule')!r}")
        return problems + cls._source_problems(params) + cls._collision_problems(params)

    @classmethod
    def _source_problems(cls, params):
        """Problems with the artifact source: ``state_hash`` goes with ``artifact`` and nowhere else."""
        problems = []
        if "artifact" in params:
            if not name_ok(params["artifact"]):
                problems.append(f"artifact must be a non-empty path, got {params['artifact']!r}")
            if not name_ok(params.get("state_hash")):
                problems.append("state_hash is required with artifact: the sidecar's content hash that pins the model")
            if "device" in params and not name_ok(params["device"]):
                problems.append(f"device must be a non-empty device string, got {params['device']!r}")
        else:
            problems += [f"{name} is refused without artifact: a model from the `model` port has no sidecar to check"
                         for name in ("state_hash", "device") if name in params]
        return problems

    @classmethod
    def _collision_problems(cls, params):
        """Problems with an output column that would overwrite an input column."""
        name = params.get("fair_field")
        if not name_ok(name):
            return []
        written = (name, name + STATUS_SUFFIX, name + _HORIZON_SUFFIX, name + _SCALE_SUFFIX)
        named = [params.get(k) for k in _NAME_PARAMS if k != "fair_field"]
        return output_collision_problems("fair_field", name, written, named)

    def validate_inputs(self, inputs):
        """Refuse a ``records`` port that is not a list, and a model source that is not exactly one.

        Parameters
        ----------
        inputs : dict
            The wired ports.

        Returns
        -------
        list of str
            One problem per unusable port.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(f"records must be a list of rows, got {type(inputs.get('records')).__name__}")
        wired = inputs.get("model") is not None
        if wired == ("artifact" in self.params):
            problems.append("exactly one model source is required: wire the `model` port or declare `artifact`")
        return problems

    def _forecaster(self, inputs):
        """Return the forecaster: the wired one, or the one restored from ``artifact`` and checked against ``state_hash``."""
        problems = [p for p in self.validate_inputs(inputs) if "model source" in p]
        if problems:
            raise ValueError(f"{self.key}: {problems[0]}")
        if "artifact" not in self.params:
            return inputs["model"]
        forecaster = TorchHorizonForecaster.restore(self.params["artifact"], self.params.get("device"))
        if forecaster.state_hash != self.params["state_hash"]:
            raise ValueError(f"{self.key}: the artifact's state_hash is {forecaster.state_hash!r}, the document pins "
                             f"{self.params['state_hash']!r}: it is not the model the document names")
        return forecaster

    def _guard(self, forecaster, panel, records):
        """Refuse a forecaster with no stated train end, and any decision the training could have seen."""
        end = getattr(forecaster, "train_end_ms", None)
        if isinstance(end, bool) or not isinstance(end, int):
            raise ValueError(f"{self.key}: the forecaster does not state train_end_ms, so look-ahead cannot be ruled out")
        p, reach = self.params, end + panel.label_span_ms
        stamps = [row[p["time_field"]] + p["row_offset_ms"] for row in records
                  if number_ok(row.get(p["time_field"]))]
        inside = sorted(stamp for stamp in stamps if stamp <= reach)
        if inside:
            raise ValueError(f"{self.key}: {len(inside)} decision(s) are not after train_end_ms {end} plus the label span "
                             f"{panel.label_span_ms} ms (the last training label reaches {reach}); the earliest is at "
                             f"{inside[0]}: the model could have seen them")

    def _slot(self, row, panel, index):
        """Work out one decision row: its status and, when priceable so far, its panel row, horizon and strikes."""
        p = self.params
        instant, lead, reference = row.get(p["time_field"]), row.get(p["horizon_field"]), row.get(p["reference_field"])
        geometry = named_payoff(row.get(p["payoff_field"]))
        given = {"lower": row.get(p["lower_field"]), "upper": row.get(p["upper_field"])}
        if (not number_ok(instant) or not number_ok(lead) or lead <= 0 or not price_ok(reference) or geometry is None
                or geometry.bounds_problem(given["lower"], given["upper"]) is not None
                or not all(price_ok(given[b]) for b in geometry.bounds)):
            return _Slot("bad_input")
        try:
            code = p["group_codes"].get(row.get(p["group_field"]))
        except TypeError:
            code = None
        if code is None:
            return _Slot("unknown_group")
        bars = HORIZON_RULES[p["horizon_rule"]].pick(lead * p["horizon_unit_ms"] / panel.step_ms, panel.horizons)
        if bars is None:
            return _Slot("horizon_out_of_range")
        found = index.find(code, int(instant) + p["row_offset_ms"])
        if found is None:
            return _Slot("no_panel_row")
        scale = float(panel.reference[found])
        if not (math.isfinite(scale) and scale > 0):
            return _Slot("bad_scale")
        slot = _Slot()
        slot.panel_row, slot.bars, slot.column, slot.scale = found, bars, panel.horizons.index(bars), scale
        slot.geometry, slot.bounds = geometry, geometry.bounds
        slot.levels, slot.reference = tuple(float(given[b]) for b in geometry.bounds), float(reference)
        return slot

    @staticmethod
    def _windowed(forecaster, panel, slots):
        """Mark the slots whose rows the forecaster cannot forecast (``window_gap``); return those that remain."""
        import numpy as np

        live = [s for s in slots if s.status == STATUS_OK]
        if live:
            ok = np.asarray(forecaster.usable(panel, np.array([s.panel_row for s in live], dtype=np.int64)), dtype=bool)
            for slot, usable in zip(live, ok):
                if not usable:
                    slot.status = "window_gap"
        return [s for s in live if s.status == STATUS_OK]

    def _survivals(self, forecaster, panel, chunk):
        """Return the survival ``P(S >= K)`` at each strike of each slot in ``chunk``: a list of ``{level: s}``."""
        import numpy as np
        import torch

        rows = np.array([s.panel_row for s in chunk], dtype=np.int64)
        dist = forecaster.forecast(panel, rows, self.params["chunk_rows"])
        if dist.shape != (len(chunk), len(panel.horizons)):
            raise ValueError(f"{self.key}: the forecaster returned {dist.shape} for {len(chunk)} rows and "
                             f"{len(panel.horizons)} panel horizons")
        width = max(len(s.levels) for s in chunk)
        z = torch.zeros((len(chunk), 1, width), dtype=torch.float64)
        for i, slot in enumerate(chunk):
            for k, level in enumerate(slot.levels):
                z[i, 0, k] = math.log(level / slot.reference) / slot.scale
        z = z.to(device=dist.device, dtype=dist.dtype).expand(-1, len(panel.horizons), -1)
        cdf = dist.cdf(z)
        pick = torch.as_tensor([s.column for s in chunk], device=cdf.device)
        cdf = cdf[torch.arange(len(chunk), device=cdf.device), pick].double().cpu()
        slack = CDF_SLACK * torch.finfo(dist.dtype).eps
        if not bool(torch.isfinite(cdf).all()):
            bad = [i for i in range(len(chunk)) if not bool(torch.isfinite(cdf[i]).all())]
            raise ValueError(f"{self.key}: the forecaster's CDF is not finite for {len(bad)} row(s), first at panel row "
                             f"{chunk[bad[0]].panel_row}")
        if bool((cdf < -slack).any()) or bool((cdf > 1 + slack).any()):
            raise ValueError(f"{self.key}: the forecaster's CDF leaves [0, 1] by more than rounding ({slack:.2e})")
        cdf = cdf.clamp(0.0, 1.0)
        return [{level: 1.0 - float(cdf[i, k]) for k, level in enumerate(slot.levels)} for i, slot in enumerate(chunk)]

    def _price(self, forecaster, panel, slots):
        """Write ``fair`` on every slot still ``ok``, in row order chunks of ``chunk_rows``."""
        live = sorted(self._windowed(forecaster, panel, slots), key=lambda s: s.panel_row)
        size = self.params["chunk_rows"]
        for start in range(0, len(live), size):
            chunk = live[start:start + size]
            for slot, survival in zip(chunk, self._survivals(forecaster, panel, chunk)):
                given = dict(zip(slot.bounds, slot.levels))
                slot.fair = min(1.0, max(0.0, PAYOFFS[slot.geometry.name].yes_probability(
                    survival.__getitem__, given.get("lower"), given.get("upper"))))

    def run(self, ctx, inputs):
        """Price every decision row; rows that cannot be priced say why.

        Parameters
        ----------
        ctx : NodeContext
            Unused.
        inputs : dict
            ``records``: the decision rows; ``model``: the forecaster (unless ``artifact`` is declared).

        Returns
        -------
        dict
            ``{"records": [...], "census": {...}}``: new row dicts in input order, and the counts by status
            with the train end and the model hash the run used.

        Raises
        ------
        ValueError
            No model source or two; a model with a different ``state_hash``; a forecaster that states no
            ``train_end_ms``; a decision not after the training range; a non-finite or out-of-range CDF.
        """
        forecaster = self._forecaster(inputs)
        panel = ArrayPanel.from_source(self.params["panel"])
        records = [dict(row) for row in inputs["records"]]
        self._guard(forecaster, panel, records)
        index = _PanelIndex(panel, self.params["group_codes"].values())
        slots = [self._slot(row, panel, index) for row in records]
        self._price(forecaster, panel, slots)
        name = self.params["fair_field"]
        for row, slot in zip(records, slots):
            row.update({name: slot.fair, name + STATUS_SUFFIX: slot.status, name + _HORIZON_SUFFIX: slot.bars,
                        name + _SCALE_SUFFIX: slot.scale})
        statuses = [slot.status for slot in slots]
        census = {"rows": len(records), "priced": statuses.count(STATUS_OK),
                  "by_status": {status: statuses.count(status) for status in _STATUSES},
                  "train_end_ms": forecaster.train_end_ms, "state_hash": getattr(forecaster, "state_hash", None),
                  "horizon_rule": self.params["horizon_rule"]}
        self.log.info("priced %d of %d decision row(s)", census["priced"], census["rows"])
        return {"records": records, "census": census}

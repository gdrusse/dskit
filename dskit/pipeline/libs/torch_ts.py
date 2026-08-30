"""Time-series architecture zoo — the pack's catalog beside its engine.

``torch.py`` owns the artifact/loop protocol and stays byte-identical
(ADR-0041). This module is one node pair over a registry: ``arch`` is a
param, so ``space: {"model.arch": [...]}`` sweeps architectures. Every
net is defined INSIDE :meth:`_TsModel.build_module` (or a builder that
function calls) — the purity gate forbids ``nn.Module`` at module level
anywhere in ``dskit/pipeline/``, including inside a class body.

Every arch maps ``(B, seq_len, channels)`` to ``(B, 1)``. The flat row
is channel-major; the ONE reshape lives in ``build_module``.
"""

from __future__ import annotations

from dskit.pipeline.libs.torch import DEFAULT_LOSS, TorchPredict, TorchTrain
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    check_int_param,
)

__all__ = [
    "ARCHS",
    "NODE_KINDS",
    "TimeSeriesPredict",
    "TimeSeriesTrain",
    "register",
    "register_arch",
]

#: name -> {build, problems, defaults, doc}
_ARCHS = {}

#: head -> default loss import path. No register_head: a head selects a
#: default objective; a document overrides it by naming ``loss``.
_HEADS = {
    "regression": DEFAULT_LOSS,
    "binary": "torch.nn.functional:binary_cross_entropy_with_logits",
}

_ORDERS = ("recent_first", "chrono")
_MIN_SEQ = 2


def register_arch(name, build, *, problems, defaults, doc=""):
    """Register one architecture. ``problems`` and ``defaults`` are required.

    Parameters
    ----------
    name : str
        Node-key character class (underscores, not hyphens).
    build : callable
        ``(arch_params, seq_len, channels) -> nn.Module``. Must import
        torch and define the net INSIDE the callable.
    problems : callable
        ``(arch_params) -> list of str``.
    defaults : dict
        Merged UNDER a document's ``arch_params[name]``.
    doc : str
        One-line description.

    Returns
    -------
    None
    """
    if not isinstance(name, str) or not name.isidentifier() or "-" in name:
        raise ValueError(
            f"arch name must be a node-key identifier (underscores, not "
            f"hyphens), got {name!r}"
        )
    if not callable(problems) or not isinstance(defaults, dict):
        raise TypeError(
            "register_arch requires problems= and defaults= (keyword-only) "
            "so an arch cannot enter unvalidated"
        )
    _ARCHS[name] = {
        "build": build, "problems": problems, "defaults": dict(defaults),
        "doc": doc,
    }


def _int_ge(name, value, lo):
    """Problems when ``value`` is not an int >= ``lo``. Never arithmetic."""
    problems = []
    check_int_param(problems, name, value, ge=lo)
    return problems


def _arch_merged(name, block):
    """``defaults`` under the declared block — the ONE merge."""
    return {**_ARCHS[name]["defaults"], **(block or {})}


def _arch_block_problems(name, block):
    """Default-deny one ``arch_params`` sub-dict, then the arch's problems."""
    if not isinstance(block, dict):
        return [
            f"arch_params.{name} must be a mapping of that arch's "
            f"knobs, got {block!r}"
        ]
    allowed = _ARCHS[name]["defaults"]
    unknown = sorted(set(block) - set(allowed))
    if unknown:
        return [
            f"arch_params.{name}: unknown key(s) {unknown} — allowed: "
            f"{sorted(allowed)} (default-deny inside the block, I-227)"
        ]
    return [
        f"arch_params.{name}.{p}" if "." not in p else p
        for p in _ARCHS[name]["problems"](_arch_merged(name, block))
    ]


def _hidden_problems(params):
    return _int_ge("hidden_size", params["hidden_size"], 1) + _int_ge(
        "num_layers", params["num_layers"], 1
    )


def _build_dlinear(params, seq_len, channels):
    import torch

    kernel = int(params["kernel_size"])

    class DLinear(torch.nn.Module):
        def __init__(self):
            super().__init__()
            pad = kernel // 2
            self.avg = torch.nn.AvgPool1d(kernel, stride=1, padding=pad)
            self.trend = torch.nn.Linear(seq_len, 1)
            self.seas = torch.nn.Linear(seq_len, 1)

        def forward(self, x):
            # x: (B, seq, ch) -> per-channel mean, then average heads
            x = x.transpose(1, 2)
            trend = self.avg(x)
            if trend.size(-1) != seq_len:
                trend = trend[..., :seq_len]
            seas = x - trend
            return (
                self.trend(trend).mean(dim=1) + self.seas(seas).mean(dim=1)
            )

    return DLinear()


def _build_nlinear(params, seq_len, channels):
    import torch

    class NLinear(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.proj = torch.nn.Linear(seq_len * channels, 1)

        def forward(self, x):
            last = x[:, -1:, :]
            flat = (x - last).reshape(x.size(0), -1)
            return self.proj(flat) + last.mean(dim=-1)

    return NLinear()


def _build_mlp(params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])

    class MLP(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.net = torch.nn.Sequential(
                torch.nn.Linear(seq_len * channels, hidden),
                torch.nn.ReLU(),
                torch.nn.Linear(hidden, 1),
            )

        def forward(self, x):
            return self.net(x.reshape(x.size(0), -1))

    return MLP()


def _build_rnn(kind, params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])
    layers = int(params["num_layers"])
    cell = torch.nn.LSTM if kind == "lstm" else torch.nn.GRU

    class RNN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = cell(
                channels, hidden, layers, batch_first=True,
            )
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x):
            out, _ = self.rnn(x)
            return self.head(out[:, -1, :])

    return RNN()


def _build_attn(kind, params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])
    layers = int(params["num_layers"])
    cell = torch.nn.LSTM if kind == "lstm" else torch.nn.GRU

    class RNNAttn(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = cell(channels, hidden, layers, batch_first=True)
            self.query = torch.nn.Linear(hidden, 1)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x):
            out, _ = self.rnn(x)
            weights = torch.softmax(self.query(out).squeeze(-1), dim=-1)
            pooled = (out * weights.unsqueeze(-1)).sum(dim=1)
            return self.head(pooled)

    return RNNAttn()


def _build_tcn(params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])

    class TCN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv = torch.nn.Conv1d(channels, hidden, 3, padding=2)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x):
            y = torch.relu(self.conv(x.transpose(1, 2)))[..., :seq_len]
            return self.head(y[:, :, -1])

    return TCN()


def _build_cnn1d(params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])

    class CNN1d(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv = torch.nn.Conv1d(channels, hidden, 3, padding=1)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x):
            y = torch.relu(self.conv(x.transpose(1, 2)))
            return self.head(y.mean(dim=-1))

    return CNN1d()


def _build_patchtst(params, seq_len, channels):
    import torch

    hidden = int(params["hidden_size"])
    patch = int(params["patch_len"])

    class PatchTST(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Linear(patch * channels, hidden)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x):
            # x: (B, seq, ch) — take the last complete patch
            n = (x.size(1) // patch) * patch
            chunk = x[:, x.size(1) - n:, :] if n else x
            last = chunk[:, -patch:, :].reshape(x.size(0), -1)
            if last.size(-1) != patch * channels:
                last = torch.nn.functional.pad(
                    last, (0, patch * channels - last.size(-1))
                )
            return self.head(torch.relu(self.embed(last)))

    return PatchTST()


def _dlinear_problems(params):
    return _int_ge("kernel_size", params["kernel_size"], 1)


def _patch_problems(params):
    return _int_ge("hidden_size", params["hidden_size"], 1) + _int_ge(
        "patch_len", params["patch_len"], 1
    )


register_arch(
    "dlinear", _build_dlinear, problems=_dlinear_problems,
    defaults={"kernel_size": 3}, doc="Zeng et al. 2023 decomposition linear",
)
register_arch(
    "nlinear", _build_nlinear, problems=lambda p: [],
    defaults={}, doc="Zeng et al. 2023 last-value normalized linear",
)
register_arch(
    "mlp", _build_mlp, problems=lambda p: _int_ge(
        "hidden_size", p["hidden_size"], 1
    ),
    defaults={"hidden_size": 32}, doc="flat MLP over the reshaped window",
)
register_arch(
    "lstm", lambda p, s, c: _build_rnn("lstm", p, s, c),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="LSTM over (B, seq, ch), last step to a linear head",
)
register_arch(
    "gru", lambda p, s, c: _build_rnn("gru", p, s, c),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="GRU over (B, seq, ch), last step to a linear head",
)
register_arch(
    "lstm_attn", lambda p, s, c: _build_attn("lstm", p, s, c),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="LSTM plus attention over time",
)
register_arch(
    "gru_attn", lambda p, s, c: _build_attn("gru", p, s, c),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="GRU plus attention over time",
)
register_arch(
    "tcn", _build_tcn, problems=lambda p: _int_ge(
        "hidden_size", p["hidden_size"], 1
    ),
    defaults={"hidden_size": 16}, doc="dilated causal convolution",
)
register_arch(
    "cnn1d", _build_cnn1d, problems=lambda p: _int_ge(
        "hidden_size", p["hidden_size"], 1
    ),
    defaults={"hidden_size": 16}, doc="1D convolution over time",
)
register_arch(
    "patchtst", _build_patchtst, problems=_patch_problems,
    defaults={"hidden_size": 16, "patch_len": 2},
    doc="patched linear embed, last-patch head",
)

#: Public name set — the registry table, not a second copy of the keys.
ARCHS = _ARCHS


class _TsModel:
    """Shared ``build_module`` for the train/predict pair (ADR-0041)."""

    _EXTRA_PARAMS = (
        "arch", "arch_params", "channels", "head", "order", "seq_len",
    )

    def build_module(self, params):
        """Reshape the flat row, then the selected arch.

        The ``nn.Module`` subclasses below are local to this call so the
        purity gate never sees them at module (or class-body) level.
        """
        import torch

        seq_len = params["seq_len"]
        channels = params["channels"]
        order = params.get("order") or "recent_first"
        name = params["arch"]
        entry = _ARCHS[name]
        knobs = _arch_merged(
            name, (params.get("arch_params") or {}).get(name) or {},
        )
        inner = entry["build"](knobs, seq_len, channels)

        class _Window(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.inner = inner
                self.seq_len = seq_len
                self.channels = channels
                self.order = order

            def forward(self, x):
                if x.dim() == 1:
                    x = x.unsqueeze(0)
                x = x.reshape(x.size(0), channels, seq_len)
                if order == "recent_first":
                    x = torch.flip(x, dims=(-1,))
                x = x.transpose(1, 2)
                return self.inner(x)

        return _Window()


def _ts_problems(params, *, require_shape):
    """Shape/arch/head problems. Cross-knob arithmetic only after both clear."""
    problems = []
    if require_shape:
        for knob in ("arch", "head", "seq_len", "channels"):
            if knob not in params:
                problems.append(
                    f"{knob} is required on the trainer — the predictor "
                    "may omit it because the sidecar carries the module"
                )
    arch = params.get("arch")
    if arch is not None:
        if arch not in _ARCHS:
            problems.append(
                f"arch must be one of {sorted(_ARCHS)}, got {arch!r}"
            )
    head = params.get("head")
    if head is not None and head not in _HEADS:
        problems.append(
            f"head must be one of {sorted(_HEADS)}, got {head!r}"
        )
    order = params.get("order")
    if order is not None and order not in _ORDERS:
        problems.append(
            f"order must be one of {_ORDERS}, got {order!r}"
        )
    seq_ok = not _int_ge("seq_len", params["seq_len"], _MIN_SEQ) if (
        "seq_len" in params
    ) else False
    if "seq_len" in params:
        problems.extend(_int_ge("seq_len", params["seq_len"], _MIN_SEQ))
    ch_ok = False
    if "channels" in params:
        problems.extend(_int_ge("channels", params["channels"], 1))
        ch_ok = not _int_ge("channels", params["channels"], 1)
    arch_params = params.get("arch_params", {})
    if arch_params is not None and not isinstance(arch_params, dict):
        problems.append(
            f"arch_params must be a dict keyed by arch name, got {arch_params!r}"
        )
        arch_params = {}
    elif not isinstance(arch_params, dict):
        arch_params = {}
    checked = set()
    for name, block in arch_params.items():
        if name not in _ARCHS:
            problems.append(
                f"arch_params.{name} is not a registered arch "
                f"(known: {sorted(_ARCHS)})"
            )
            continue
        problems.extend(_arch_block_problems(name, block))
        checked.add(name)
    if arch in _ARCHS and arch not in checked:
        problems.extend(_arch_block_problems(arch, {}))
    features = params.get("features")
    if (
        seq_ok and ch_ok and isinstance(features, list) and features
        and all(isinstance(n, str) for n in features)
    ):
        expect = params["seq_len"] * params["channels"]
        if len(features) != expect:
            problems.append(
                f"len(features) must equal seq_len * channels "
                f"({params['seq_len']} * {params['channels']} = {expect}), "
                f"got {len(features)} — a derived channels would accept "
                "every divisor"
            )
    return problems


class TimeSeriesTrain(_TsModel, TorchTrain):
    """Fit one zoo architecture on a flat channel-major window.

    Parameters
    ----------
    params : dict
        ``arch``, ``head``, ``seq_len`` required; ``channels`` declared
        never derived; ``arch_params`` keyed by arch name; plus
        :class:`TorchTrain`'s knobs. ``order`` defaults to
        ``recent_first`` (lag 0 is the newest step).

    Examples
    --------
    Train DLinear on a 4-step univariate window::

        node = TimeSeriesTrain("model", {
            "arch": "dlinear", "head": "regression",
            "seq_len": 4, "channels": 1,
            "features": ["lag_0", "lag_1", "lag_2", "lag_3"],
            "epochs": 2,
        })
        out = node.run(ctx, {"rows": rows})
    """

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = list(super().validate_params(params))
        problems.extend(_ts_problems(params, require_shape=True))
        return problems

    def run_train(self, ctx, inputs):
        """Apply the head's default loss when the document omitted one."""
        head = self.params.get("head")
        if head in _HEADS and not self.params.get("loss"):
            self.params = {**self.params, "loss": _HEADS[head]}
        return super().run_train(ctx, inputs)


class TimeSeriesPredict(_TsModel, TorchPredict):
    """Serve a :class:`TimeSeriesTrain` artifact.

    ``arch`` / ``head`` / ``seq_len`` are optional — a predict node that
    pinned them would kill an ``arch`` sweep, because a rerun rebuilds
    descendants from their own params.

    Parameters
    ----------
    params : dict
        ``artifact`` required on load. Shape knobs may be omitted; the
        sidecar carries the module. Plus :class:`TorchPredict`'s knobs.

    Examples
    --------
    Load a zoo artifact without pinning ``arch``::

        node = TimeSeriesPredict("serve", {"artifact": "model.pt"})
        out = node.run(ctx, {})
    """

    @classmethod
    def validate_params(cls, params):
        """Problems with ``params``, empty when none."""
        problems = list(super().validate_params(params))
        problems.extend(_ts_problems(params, require_shape=False))
        return problems


NODE_KINDS = (
    ("torch-ts-train", TimeSeriesTrain),
    ("torch-ts-predict", TimeSeriesPredict),
)


def register(registry=None):
    """Claim the ``torch-ts-*`` kind names. Idempotent."""
    registry = DEFAULT_NODE_KINDS if registry is None else registry
    for name, cls in NODE_KINDS:
        if name not in registry:
            registry.register(name, cls, owned=False)
    return registry

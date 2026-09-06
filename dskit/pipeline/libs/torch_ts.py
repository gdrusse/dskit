"""Time-series architecture zoo — the pack's catalog beside its engine.

``torch.py`` owns the artifact/loop protocol. Its content pin moves only
on a deliberate engine change (ADR-0045 batched eval; ADR-0054 pinball
and patience). This module is one node pair over a registry: ``arch`` is
a param, so ``space: {"model.arch": [...]}`` sweeps architectures. Every
net is defined INSIDE :meth:`_TsModel.build_module` (or a builder that
function calls) — the purity gate forbids ``nn.Module`` at module level
anywhere in ``dskit/pipeline/``, including inside a class body.

Every arch maps ``(B, seq_len, channels)`` to ``(B, n_ahead)`` with
``n_ahead`` default 1 (ADR-0041's ``(B, 1)``). The flat row is
channel-major; the ONE reshape lives in ``build_module``.
"""

from __future__ import annotations

from dskit.pipeline.libs.torch import DEFAULT_LOSS, TorchPredict, TorchTrain
from dskit.pipeline.node import (
    DEFAULT_NODE_KINDS,
    check_int_param,
)

__all__ = [
    "ARCHS",
    "CategoricalEmbeddingMLPRegressor",
    "DEFAULT_SEQUENCE_PREFIX",
    "ZooEstimator",
    "DEFAULT_ORDER",
    "NODE_KINDS",
    "TimeSeriesPredict",
    "TimeSeriesTrain",
    "register",
    "register_arch",
    "zoo_regime",
]

#: name -> {build, problems, defaults, doc}
_ARCHS = {}

#: head -> default loss import path. No register_head: a head selects a
#: default objective; a document overrides it by naming ``loss``.
_HEADS = {
    "regression": DEFAULT_LOSS,
    "binary": "torch.nn.functional:binary_cross_entropy_with_logits",
}

#: Columns whose name starts with this form the time axis of
#: :class:`ZooEstimator`; everything else is a static covariate.
DEFAULT_SEQUENCE_PREFIX = "ret_lag_"
#: The estimator façade's training defaults, named ONCE so the
#: constructor, the default-deny check and the docstring cannot drift.
ESTIMATOR_DEFAULTS = {
    "arch_params": None,
    "order": None,
    "sequence_prefix": DEFAULT_SEQUENCE_PREFIX,
    "seq_len": None,
    "epochs": 15,
    "lr": 1e-3,
    "weight_decay": 0.0,
    "batch_size": 1024,
    "seed": 0,
    "seeds": None,
    "device": None,
    "standardize": True,
}

_ORDERS = ("recent_first", "chrono")
DEFAULT_ORDER = "recent_first"
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


def _build_dlinear(params, seq_len, channels, n_ahead):
    import torch

    kernel = int(params["kernel_size"])

    class DLinear(torch.nn.Module):
        def __init__(self):
            super().__init__()
            pad = kernel // 2
            self.avg = torch.nn.AvgPool1d(kernel, stride=1, padding=pad)
            self.trend = torch.nn.Linear(seq_len, n_ahead)
            self.seas = torch.nn.Linear(seq_len, n_ahead)

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


def _build_nlinear(params, seq_len, channels, n_ahead):
    import torch

    class NLinear(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.proj = torch.nn.Linear(seq_len * channels, n_ahead)

        def forward(self, x):
            last = x[:, -1:, :]
            flat = (x - last).reshape(x.size(0), -1)
            bias = last.reshape(x.size(0), -1).mean(dim=-1, keepdim=True)
            return self.proj(flat) + bias

    return NLinear()


def _build_mlp(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])

    class MLP(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.net = torch.nn.Sequential(
                torch.nn.Linear(seq_len * channels, hidden),
                torch.nn.ReLU(),
                torch.nn.Linear(hidden, n_ahead),
            )

        def forward(self, x):
            return self.net(x.reshape(x.size(0), -1))

    return MLP()


def _build_rnn(kind, params, seq_len, channels, n_ahead):
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
            self.head = torch.nn.Linear(hidden, n_ahead)

        def forward(self, x):
            out, _ = self.rnn(x)
            return self.head(out[:, -1, :])

    return RNN()


def _build_attn(kind, params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])
    layers = int(params["num_layers"])
    cell = torch.nn.LSTM if kind == "lstm" else torch.nn.GRU

    class RNNAttn(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.rnn = cell(channels, hidden, layers, batch_first=True)
            self.query = torch.nn.Linear(hidden, 1)
            self.head = torch.nn.Linear(hidden, n_ahead)

        def forward(self, x):
            out, _ = self.rnn(x)
            weights = torch.softmax(self.query(out).squeeze(-1), dim=-1)
            pooled = (out * weights.unsqueeze(-1)).sum(dim=1)
            return self.head(pooled)

    return RNNAttn()


def _build_tcn(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])

    class TCN(torch.nn.Module):
        def __init__(self):
            super().__init__()
            # Two causal dilated layers (k=2, d=1 then d=2). Receptive
            # field is 4, so every step of a seq_len-4 window can fire.
            self.c1 = torch.nn.Conv1d(channels, hidden, 2, dilation=1)
            self.c2 = torch.nn.Conv1d(hidden, hidden, 2, dilation=2)
            self.head = torch.nn.Linear(hidden, n_ahead)

        def forward(self, x):
            y = x.transpose(1, 2)
            y = torch.nn.functional.pad(y, (1, 0))
            y = torch.relu(self.c1(y))
            y = torch.nn.functional.pad(y, (2, 0))
            y = torch.relu(self.c2(y))
            return self.head(y[:, :, -1])

    return TCN()


def _build_cnn1d(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])

    class CNN1d(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.conv = torch.nn.Conv1d(channels, hidden, 3, padding=1)
            self.head = torch.nn.Linear(hidden, n_ahead)

        def forward(self, x):
            y = torch.relu(self.conv(x.transpose(1, 2)))
            return self.head(y.mean(dim=-1))

    return CNN1d()


def _build_patchtst(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])
    patch = int(params["patch_len"])

    class PatchTST(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embed = torch.nn.Linear(patch * channels, hidden)
            self.head = torch.nn.Linear(hidden, n_ahead)

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


def _build_transformer(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])
    nhead = int(params["nhead"])

    class TinyTransformer(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.in_proj = torch.nn.Linear(channels, hidden)
            layer = torch.nn.TransformerEncoderLayer(
                d_model=hidden,
                nhead=nhead,
                dim_feedforward=max(hidden * 2, 8),
                batch_first=True,
                dropout=0.0,
            )
            self.enc = torch.nn.TransformerEncoder(layer, num_layers=1)
            self.head = torch.nn.Linear(hidden, n_ahead)

        def forward(self, x):
            return self.head(self.enc(self.in_proj(x))[:, -1, :])

    return TinyTransformer()


def _build_tft(params, seq_len, channels, n_ahead):
    import torch

    hidden = int(params["hidden_size"])
    nhead = int(params["nhead"])
    dropout = float(params.get("dropout", 0.1))

    class TFTLite(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.vsn = torch.nn.Linear(channels, channels)
            self.in_proj = torch.nn.Linear(channels, hidden)
            self.lstm = torch.nn.LSTM(hidden, hidden, batch_first=True)
            self.attn = torch.nn.MultiheadAttention(
                hidden, nhead, dropout=dropout, batch_first=True,
            )
            self.gate = torch.nn.Linear(hidden * 2, hidden)
            self.head = torch.nn.Linear(hidden, n_ahead)
            self.drop = torch.nn.Dropout(dropout)

        def forward(self, x):
            weights = torch.softmax(self.vsn(x.mean(dim=1)), dim=-1)
            selected = x * weights.unsqueeze(1)
            hidden_seq = self.drop(torch.relu(self.in_proj(selected)))
            encoded, _ = self.lstm(hidden_seq)
            attended, _ = self.attn(encoded, encoded, encoded)
            last = encoded[:, -1]
            fused = torch.sigmoid(
                self.gate(torch.cat([last, attended[:, -1]], dim=-1))
            )
            return self.head(fused * last)

    return TFTLite()


def _dlinear_problems(params):
    return _int_ge("kernel_size", params["kernel_size"], 1)


def _patch_problems(params):
    return _int_ge("hidden_size", params["hidden_size"], 1) + _int_ge(
        "patch_len", params["patch_len"], 1
    )


def _transformer_problems(params):
    problems = _int_ge("hidden_size", params["hidden_size"], 1) + _int_ge(
        "nhead", params["nhead"], 1
    )
    hidden = params.get("hidden_size")
    nhead = params.get("nhead")
    if (
        isinstance(hidden, int)
        and isinstance(nhead, int)
        and nhead > 0
        and hidden % nhead != 0
    ):
        problems.append(
            f"hidden_size must be divisible by nhead, got {hidden} % {nhead}"
        )
    return problems


def _tft_problems(params):
    problems = _transformer_problems(params)
    drop = params.get("dropout", 0.1)
    if (
        isinstance(drop, bool)
        or not isinstance(drop, (int, float))
        or not (0.0 <= float(drop) < 1.0)
    ):
        problems.append(f"dropout must be in [0, 1), got {drop!r}")
    return problems


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
    "lstm", lambda p, s, c, h: _build_rnn("lstm", p, s, c, h),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="LSTM over (B, seq, ch), last step to a linear head",
)
register_arch(
    "gru", lambda p, s, c, h: _build_rnn("gru", p, s, c, h),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="GRU over (B, seq, ch), last step to a linear head",
)
register_arch(
    "lstm_attn", lambda p, s, c, h: _build_attn("lstm", p, s, c, h),
    problems=_hidden_problems, defaults={"hidden_size": 32, "num_layers": 1},
    doc="LSTM plus attention over time",
)
register_arch(
    "gru_attn", lambda p, s, c, h: _build_attn("gru", p, s, c, h),
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
register_arch(
    "transformer", _build_transformer, problems=_transformer_problems,
    defaults={"hidden_size": 16, "nhead": 2},
    doc="one-layer encoder, last-step head",
)
register_arch(
    "tft", _build_tft, problems=_tft_problems,
    defaults={"hidden_size": 16, "nhead": 2, "dropout": 0.1},
    doc="TFT-lite: channel VSN, LSTM encoder, gated attention (ADR-0051)",
)

#: Public name set — the registry table, not a second copy of the keys.
ARCHS = _ARCHS


def _seed_tuple(seeds, seed):
    """Resolve the seeds to fit: the declared set, else ``seed``.

    Parameters
    ----------
    seeds : sequence of int or None
        The declared seed set; ``None`` means one member.
    seed : int
        The single-member seed, read only when ``seeds`` is ``None``.

    Returns
    -------
    tuple of int
        One seed per member, in declaration order.

    Raises
    ------
    ValueError
        When ``seeds`` is empty or holds anything but whole numbers.
    """
    if seeds is None:
        return (int(seed),)
    if not isinstance(seeds, (list, tuple)) or not seeds:
        raise ValueError(
            "ZooEstimator: seeds must be a non-empty list of whole "
            f"numbers, got {seeds!r}"
        )
    bad = [
        s for s in seeds
        if isinstance(s, bool) or not isinstance(s, int)
    ]
    if bad:
        raise ValueError(
            f"ZooEstimator: seeds must be whole numbers, got {bad!r}"
        )
    return tuple(int(s) for s in seeds)


class ZooEstimator:
    """sklearn-shaped estimator over one zoo architecture (ADR-0061).

    The evaluation seams in this repo — walk-forward folds, Clark-West
    per fold, the ``h*`` walk — fit ``scan.estimator`` through
    ``cls(**params)`` / ``fit(X, y)`` / ``predict(X)``. This is the same
    ``_ARCHS`` registry the node pair uses, reached through that
    contract, so a sequence model can be compared with a ridge on
    identical folds without either side being rebuilt.

    The flat row splits BY NAME: columns starting with
    ``sequence_prefix`` become the time axis (ordered by their integer
    suffix, most recent first), and every other column is a static
    covariate broadcast as a constant channel over that axis — so the
    net sees ``(B, seq_len, 1 + n_static)``. The broadcast is a view,
    never a copy. Without ``feature_names`` the whole row is one
    channel, which is what a caller with no names can honestly claim.

    Parameters
    ----------
    arch : str
        A registered architecture (``ARCHS``).
    arch_params : dict, optional
        ``{arch: {knob: value}}`` — the node pair's shape, merged under
        that arch's registered defaults.
    sequence_prefix : str, optional
        Column-name prefix marking the time axis, default ``ret_lag_``.
    seq_len : int, optional
        Sequence length when no names are supplied; ``None`` uses the
        whole row as one channel.
    order : str, optional
        ``recent_first`` (default) or ``chrono`` — how the time axis is
        already ordered.
    epochs, lr, weight_decay, batch_size, seed : optional
        The training loop, defaults 15 / 1e-3 / 0.0 / 1024 / 0.
    seeds : sequence of int, optional
        Fit one member per seed and average their forecasts
        (ADR-0072). ``None`` (the default) fits the single member
        ``seed``. Every member shares the architecture, the
        schedule and the standardisation; only the initialisation
        and the batch shuffle differ, so the average removes seed
        luck without adding capacity.
    device : str, optional
        ``cuda`` when available by default.
    standardize : bool, optional
        Fit column mean/sd on train and apply them at predict, default
        True. A torch model on unstandardized columns underfits exactly
        the way an unstandardized ridge does.

    Raises
    ------
    ValueError
        On an unknown arch, an unknown knob, or a row width that is not
        a whole number of channels.

    Examples
    --------
    A GRU over a 3-step lag path plus one static covariate::

        model = ZooEstimator(arch="gru", epochs=2, batch_size=8)
        model.fit(
            [[0.1, 0.2, 0.3, 5.0], [0.2, 0.1, 0.0, 6.0]],
            [0.5, -0.5],
            feature_names=["ret_lag_0", "ret_lag_1", "ret_lag_2", "vol"],
        )
        model.predict([[0.1, 0.2, 0.3, 5.0]])
        # -> array([...])  # one float per row
    """

    def __init__(self, arch, **knobs):
        unknown = sorted(set(knobs) - set(ESTIMATOR_DEFAULTS))
        if unknown:
            raise ValueError(
                f"ZooEstimator: unknown knob(s) {unknown} — allowed: "
                f"{sorted(ESTIMATOR_DEFAULTS)} (default-deny, I-227)"
            )
        if arch not in _ARCHS:
            raise ValueError(
                f"arch must be one of {sorted(_ARCHS)}, got {arch!r}"
            )
        self.arch = arch
        for name, fallback in ESTIMATOR_DEFAULTS.items():
            setattr(self, name, knobs.get(name, fallback))
        block = (self.arch_params or {}).get(arch) or {}
        problems = _arch_block_problems(arch, block)
        if problems:
            raise ValueError(f"ZooEstimator {arch!r}: {problems}")
        self._knobs = _arch_merged(arch, block)
        self._seeds = _seed_tuple(self.seeds, self.seed)
        self._modules = ()
        self._layout = None
        self._center = None
        self._scale = None

    def _resolved_device(self):
        """Resolve the device: the declared one, else cuda when present."""
        import torch

        if self.device:
            return torch.device(self.device)
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    def _split(self, width, feature_names):
        """Column layout as ``(sequence indices, static indices)``."""
        if not feature_names:
            seq_len = self.seq_len or width
            if width % seq_len:
                raise ValueError(
                    f"ZooEstimator: {width} column(s) is not a whole number "
                    f"of {seq_len}-step channels — pass feature_names, or a "
                    "seq_len that divides the row"
                )
            return list(range(width)), []
        lagged = {}
        static = []
        for i, name in enumerate(feature_names):
            suffix = (
                name[len(self.sequence_prefix):]
                if name.startswith(self.sequence_prefix) else None
            )
            if suffix is not None and suffix.isdigit():
                lagged[int(suffix)] = i
            else:
                static.append(i)
        if not lagged:
            raise ValueError(
                f"ZooEstimator: no column starts with "
                f"{self.sequence_prefix!r} — there is no time axis to read"
            )
        steps = sorted(lagged)
        if steps != list(range(len(steps))):
            raise ValueError(
                f"ZooEstimator: {self.sequence_prefix}* steps are not "
                f"contiguous from 0, got {steps}"
            )
        return [lagged[s] for s in steps], static

    def _build(self, seq_len, channels):
        """Wrap the arch so a (sequence, static) pair becomes its window."""
        import torch

        inner = _ARCHS[self.arch]["build"](self._knobs, seq_len, channels, 1)
        flip = (self.order or DEFAULT_ORDER) == "recent_first"

        class _WindowFromParts(torch.nn.Module):
            """Assemble ``(B, seq, 1 + n_static)`` without materializing it."""

            def __init__(self):
                super().__init__()
                self.inner = inner

            def forward(self, seq, static):
                path = torch.flip(seq, dims=(-1,)) if flip else seq
                path = path.unsqueeze(-1)
                if static.shape[1]:
                    held = static.unsqueeze(1).expand(-1, path.size(1), -1)
                    path = torch.cat([path, held], dim=-1)
                return self.inner(path)

        return _WindowFromParts()

    def _parts(self, matrix):
        """Standardize, then split into ``(sequence, static)`` tensors."""
        import torch

        x = torch.as_tensor(matrix, dtype=torch.float32)
        if self._center is not None:
            x = (x - self._center) / self._scale
        seq_idx, static_idx = self._layout
        seq = x[:, seq_idx]
        static = x[:, static_idx] if static_idx else x[:, :0]
        return seq, static

    def fit(self, X, y, feature_names=None):
        """Train the architecture on one flat design matrix.

        Parameters
        ----------
        X : array-like
            Rows x columns, finite.
        y : array-like
            One target per row.
        feature_names : sequence of str, optional
            Column names, in order. Supplied by a caller whose fit
            signature check finds this parameter.

        Returns
        -------
        ZooEstimator
            ``self``, so the sklearn call chain works.
        """
        import numpy as np
        import torch

        x = np.asarray(X, dtype=np.float64)
        target = np.asarray(y, dtype=np.float64).reshape(-1)
        self._layout = self._split(x.shape[1], feature_names)
        if self.standardize:
            center = x.mean(axis=0)
            scale = x.std(axis=0)
            scale[~np.isfinite(scale) | (scale <= 0.0)] = 1.0
            self._center = torch.as_tensor(center, dtype=torch.float32)
            self._scale = torch.as_tensor(scale, dtype=torch.float32)
        device = self._resolved_device()
        seq, static = self._parts(x)
        labels = torch.as_tensor(target, dtype=torch.float32).reshape(-1, 1)
        self._modules = tuple(
            self._fit_one(seq, static, labels, device, seed)
            for seed in self._seeds
        )
        return self

    def _fit_one(self, seq, static, labels, device, seed):
        """Train one member from ``seed``.

        Parameters
        ----------
        seq, static : torch.Tensor
            The standardized lag path and the static covariates.
        labels : torch.Tensor
            One target per row, shaped ``(rows, 1)``.
        device : torch.device
            Where the member trains.
        seed : int
            Seeds both the initialisation and the batch shuffle.

        Returns
        -------
        torch.nn.Module
            The trained member.
        """
        import torch

        torch.manual_seed(int(seed))
        module = self._build(seq.shape[1], 1 + static.shape[1]).to(device)
        optimizer = torch.optim.Adam(
            module.parameters(),
            lr=float(self.lr),
            weight_decay=float(self.weight_decay),
        )
        loss_fn = torch.nn.MSELoss()
        rows = seq.shape[0]
        batch = max(int(self.batch_size), 1)
        generator = torch.Generator().manual_seed(int(seed))
        module.train()
        for _ in range(max(int(self.epochs), 1)):
            order = torch.randperm(rows, generator=generator)
            for start in range(0, rows, batch):
                take = order[start:start + batch]
                optimizer.zero_grad(set_to_none=True)
                out = module(seq[take].to(device), static[take].to(device))
                loss = loss_fn(out, labels[take].to(device))
                loss.backward()
                optimizer.step()
        return module

    def predict(self, X):
        """Forecast one value per row.

        Parameters
        ----------
        X : array-like
            Rows x columns, the fitted column layout.

        Returns
        -------
        numpy.ndarray
            One float64 per row — the mean over the fitted members.

        Raises
        ------
        RuntimeError
            When called before ``fit``.
        """
        import numpy as np

        if not self._modules:
            raise RuntimeError("ZooEstimator: predict before fit")
        seq, static = self._parts(np.asarray(X, dtype=np.float64))
        device = self._resolved_device()
        total = None
        for module in self._modules:
            member = self._predict_one(module, seq, static, device)
            total = member if total is None else total + member
        return total / float(len(self._modules))

    def _predict_one(self, module, seq, static, device):
        """One member's forecast over the fitted column layout.

        Parameters
        ----------
        module : torch.nn.Module
            A trained member.
        seq, static : torch.Tensor
            The standardized lag path and the static covariates.
        device : torch.device
            Where the member runs.

        Returns
        -------
        numpy.ndarray
            One float64 per row.
        """
        import numpy as np
        import torch

        batch = max(int(self.batch_size), 1)
        out = []
        module.eval()
        with torch.no_grad():
            for start in range(0, seq.shape[0], batch):
                stop = start + batch
                chunk = module(
                    seq[start:stop].to(device), static[start:stop].to(device),
                )
                out.append(chunk.reshape(-1).cpu().numpy())
        return (
            np.concatenate(out) if out else np.zeros(0, dtype=np.float64)
        ).astype(np.float64)


class CategoricalEmbeddingMLPRegressor:
    """Torch MLP over continuous columns plus one learned category embedding.

    The estimator follows sklearn's ``fit``/``predict`` shape while accepting
    the categorical-column index supplied by callers such as LightGBM. Only
    training rows determine category vocabulary and continuous standardization.

    Parameters
    ----------
    hidden_size : int
        Width of the hidden layer.
    embedding_dim : int
        Width of the learned categorical embedding.
    epochs, lr, weight_decay, batch_size, dropout, seed : numeric
        Training-loop and regularization controls.
    device : str or None
        Explicit Torch device; ``None`` selects CUDA when available.
    standardize : bool
        Whether to standardize continuous columns on training rows.

    Examples
    --------
    Fit two entities with one continuous feature::

        model = CategoricalEmbeddingMLPRegressor(epochs=2, device="cpu")
        model.fit([[1.0, 0], [2.0, 1]], [0.1, 0.2], categorical_feature=[1])
        model.predict([[1.5, 0]])  # one forecast
    """

    def __init__(
        self,
        hidden_size=64,
        embedding_dim=8,
        epochs=15,
        lr=1e-3,
        weight_decay=1e-4,
        batch_size=4096,
        dropout=0.1,
        seed=0,
        device=None,
        standardize=True,
    ):
        self.hidden_size = hidden_size
        self.embedding_dim = embedding_dim
        self.epochs = epochs
        self.lr = lr
        self.weight_decay = weight_decay
        self.batch_size = batch_size
        self.dropout = dropout
        self.seed = seed
        self.device = device
        self.standardize = standardize
        self._category_index = None
        self._continuous_indices = None
        self._categories = None
        self._center = None
        self._scale = None
        self._module = None

    def _resolved_device(self):
        import torch

        if self.device:
            return torch.device(self.device)
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    @staticmethod
    def _standardizer(matrix, block_rows=32768):
        """Return float32 population moments without a matrix-sized temp."""
        import numpy as np

        count = 0
        mean = np.zeros(matrix.shape[1], dtype=np.float64)
        m2 = np.zeros(matrix.shape[1], dtype=np.float64)
        for start in range(0, matrix.shape[0], block_rows):
            block = matrix[start : start + block_rows]
            block_count = block.shape[0]
            block_mean = block.mean(axis=0, dtype=np.float64)
            block_m2 = block.var(axis=0, dtype=np.float64) * block_count
            total = count + block_count
            delta = block_mean - mean
            m2 += block_m2 + delta * delta * count * block_count / total
            mean += delta * block_count / total
            count = total
        scale = np.sqrt(m2 / count)
        center = mean.astype(np.float32)
        scale = scale.astype(np.float32)
        scale[~np.isfinite(scale) | (scale <= 0.0)] = 1.0
        return center, scale

    def _split(self, matrix, *, fitting):
        import numpy as np

        x = np.asarray(matrix)
        if x.ndim != 2:
            raise ValueError("CategoricalEmbeddingMLPRegressor requires a 2-D matrix")
        raw = x[:, self._category_index]
        if not np.all(np.isfinite(raw)):
            raise ValueError("categorical feature contains non-finite values")
        if fitting:
            self._categories = np.unique(raw)
        positions = np.searchsorted(self._categories, raw)
        valid = positions < len(self._categories)
        valid[valid] &= self._categories[positions[valid]] == raw[valid]
        if not np.all(valid):
            raise ValueError("categorical feature contains an unseen category")
        # The scan already supplies float32 cache data.  Selecting the
        # continuous columns necessarily makes one compact copy; keep that
        # copy float32 and standardize it in place instead of first cloning
        # the full pooled matrix to float64.
        continuous = np.asarray(
            x[:, self._continuous_indices], dtype=np.float32
        )
        if self.standardize:
            if fitting:
                self._center, self._scale = self._standardizer(continuous)
            continuous -= self._center
            continuous /= self._scale
        return continuous, positions.astype(np.int64, copy=False)

    def _build(self, n_continuous, n_categories):
        import torch

        hidden = int(self.hidden_size)
        embedding = int(self.embedding_dim)
        dropout = float(self.dropout)
        if hidden < 1 or embedding < 1:
            raise ValueError("hidden_size and embedding_dim must be positive")
        if not 0.0 <= dropout < 1.0:
            raise ValueError("dropout must lie in [0, 1)")

        class EmbeddedMLP(torch.nn.Module):
            def __init__(self):
                super().__init__()
                self.embedding = torch.nn.Embedding(n_categories, embedding)
                self.net = torch.nn.Sequential(
                    torch.nn.Linear(n_continuous + embedding, hidden),
                    torch.nn.ReLU(),
                    torch.nn.Dropout(dropout),
                    torch.nn.Linear(hidden, 1),
                )

            def forward(self, continuous, category):
                joined = torch.cat([continuous, self.embedding(category)], dim=1)
                return self.net(joined)

        return EmbeddedMLP()

    def fit(self, X, y, categorical_feature=None, feature_names=None):
        """Fit on training rows and return this estimator.

        Parameters
        ----------
        X, y : array-like
            Training design matrix and one numeric target per row.
        categorical_feature : sequence of int
            Exactly one categorical-column index.
        feature_names : sequence of str or None
            Optional names checked against matrix width.

        Returns
        -------
        CategoricalEmbeddingMLPRegressor
            The fitted estimator.

        Raises
        ------
        ValueError
            If the matrix, category declaration, or knobs are invalid.
        """
        import numpy as np
        import torch

        x = np.asarray(X)
        target = np.asarray(y, dtype=np.float32).reshape(-1)
        categorical = list(categorical_feature or [])
        if x.ndim != 2 or x.shape[0] != target.size or x.shape[0] < 1:
            raise ValueError("X and y must contain the same non-empty row count")
        if feature_names is not None and len(feature_names) != x.shape[1]:
            raise ValueError("feature_names must match the design-matrix width")
        if len(categorical) != 1 or isinstance(categorical[0], bool):
            raise ValueError("exactly one categorical feature index is required")
        index = int(categorical[0])
        if index < 0 or index >= x.shape[1]:
            raise ValueError("categorical feature index is outside the matrix")
        self._category_index = index
        self._continuous_indices = [i for i in range(x.shape[1]) if i != index]
        continuous, category = self._split(x, fitting=True)
        device = self._resolved_device()
        torch.manual_seed(int(self.seed))
        self._module = self._build(continuous.shape[1], len(self._categories)).to(
            device
        )
        optimizer = torch.optim.Adam(
            self._module.parameters(),
            lr=float(self.lr),
            weight_decay=float(self.weight_decay),
        )
        features = torch.as_tensor(continuous, dtype=torch.float32)
        codes = torch.as_tensor(category, dtype=torch.long)
        labels = torch.as_tensor(target, dtype=torch.float32).reshape(-1, 1)
        batch = max(int(self.batch_size), 1)
        generator = torch.Generator().manual_seed(int(self.seed))
        self._module.train()
        for _ in range(max(int(self.epochs), 1)):
            order = torch.randperm(features.shape[0], generator=generator)
            for start in range(0, features.shape[0], batch):
                take = order[start : start + batch]
                optimizer.zero_grad(set_to_none=True)
                prediction = self._module(
                    features[take].to(device), codes[take].to(device)
                )
                loss = torch.nn.functional.mse_loss(prediction, labels[take].to(device))
                loss.backward()
                optimizer.step()
        return self

    def predict(self, X):
        """Return one forecast per row.

        Parameters
        ----------
        X : array-like
            Matrix with the fitted column layout and known categories.

        Returns
        -------
        numpy.ndarray
            One float64 forecast per row.

        Raises
        ------
        RuntimeError
            If called before fitting.
        """
        import numpy as np
        import torch

        if self._module is None:
            raise RuntimeError("CategoricalEmbeddingMLPRegressor: predict before fit")
        continuous, category = self._split(X, fitting=False)
        features = torch.as_tensor(continuous, dtype=torch.float32)
        codes = torch.as_tensor(category, dtype=torch.long)
        device = self._resolved_device()
        batch = max(int(self.batch_size), 1)
        output = []
        self._module.eval()
        with torch.no_grad():
            for start in range(0, features.shape[0], batch):
                stop = start + batch
                output.append(
                    self._module(
                        features[start:stop].to(device), codes[start:stop].to(device)
                    )
                    .reshape(-1)
                    .cpu()
                    .numpy()
                )
        return (
            np.concatenate(output).astype(np.float64)
            if output
            else np.zeros(0, dtype=np.float64)
        )


class _TsModel:
    """Shared ``build_module`` for the train/predict pair (ADR-0041)."""

    _EXTRA_PARAMS = (
        "arch", "arch_params", "channels", "n_ahead", "order", "seq_len",
    )

    def build_module(self, params):
        """Reshape the flat row, then the selected arch.

        The ``nn.Module`` subclasses below are local to this call so the
        purity gate never sees them at module (or class-body) level.
        """
        import torch

        seq_len = params["seq_len"]
        channels = params["channels"]
        order = params.get("order") or DEFAULT_ORDER
        name = params["arch"]
        entry = _ARCHS[name]
        knobs = _arch_merged(
            name, (params.get("arch_params") or {}).get(name) or {},
        )
        n_ahead = params.get("n_ahead", 1)
        n_ahead = 1 if n_ahead is None else int(n_ahead)
        inner = entry["build"](knobs, seq_len, channels, n_ahead)

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
            if knob not in params or params[knob] is None:
                problems.append(
                    f"{knob} is required on the trainer — the predictor "
                    "may omit it because the sidecar carries the module"
                )
    arch = params.get("arch")
    if arch is not None and (not isinstance(arch, str) or arch not in _ARCHS):
        problems.append(
            f"arch must be one of {sorted(_ARCHS)}, got {arch!r}"
        )
    head = params.get("head")
    if head is not None and (not isinstance(head, str) or head not in _HEADS):
        problems.append(
            f"head must be one of {sorted(_HEADS)}, got {head!r}"
        )
    if "n_ahead" in params:
        problems.extend(_int_ge("n_ahead", params["n_ahead"], 1))
        ahead = params.get("n_ahead")
        if (
            head == "binary"
            and isinstance(ahead, int)
            and ahead > 1
        ):
            problems.append("n_ahead > 1 is regression-only, got head='binary'")
        label = params.get("label")
        if (
            isinstance(ahead, int)
            and ahead > 1
            and isinstance(label, (list, tuple))
            and len(label) != ahead
        ):
            problems.append(
                f"len(label) must equal n_ahead ({ahead}), got {len(label)}"
            )
    order = params.get("order")
    if order is not None and (not isinstance(order, str) or order not in _ORDERS):
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
    if isinstance(arch, str) and arch in _ARCHS and arch not in checked:
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

    ``head`` is the trainer's: it selects the default loss. Predict
    does not allow it — the sidecar already carries the choice.

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

    _EXTRA_PARAMS = _TsModel._EXTRA_PARAMS + ("head",)

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

    ``arch`` / ``seq_len`` / ``channels`` are optional — a predict node
    that pinned them would kill an ``arch`` sweep, because a rerun
    rebuilds descendants from their own params. ``head`` is the
    trainer's.

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


def zoo_regime(params):
    """Architecture-pinning knobs from a trainer's ``params``.

    Serving compares this map across symbols. Defaults come from the
    registry and :data:`DEFAULT_ORDER` — the same merge
    :meth:`_TsModel.build_module` uses.

    Parameters
    ----------
    params : dict
        A zoo trainer's params (or a sidecar's ``params``).

    Returns
    -------
    dict
        ``arch``, ``seq_len``, ``channels``, ``head``, ``order``, plus
        the selected arch's merged knobs.
    """
    arch = params["arch"]
    block = _arch_merged(arch, (params.get("arch_params") or {}).get(arch) or {})
    return {
        "arch": arch,
        "seq_len": params["seq_len"],
        "channels": params["channels"],
        "head": params["head"],
        "order": params.get("order") or DEFAULT_ORDER,
        **block,
    }


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

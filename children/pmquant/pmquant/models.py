"""The ladder-q̂ transformer, as a DECLARABLE model on dskit's torch seam.

A document names two things here and nothing else about the architecture:
``module = "pmquant.models:LadderQhatModule"`` (the ``nn.Module``) and
``adapter = "pmquant.models:LadderPanelAdapter"`` (how panel items become
batches, what the objective is, and how a fitted model serves). Everything
around them — the deterministic epoch loop, the sidecar, ``mode="load"``
— is the pack's (:mod:`dskit.pipeline.libs.torch`) and is not restated.

The model is the parent program's frozen v3 recipe, ported verbatim:

* :class:`TokenEncoder` — token MLP, lead + market embeddings, a CAUSAL
  time encoder over the lead axis per contract (a pre-norm transformer
  under an upper-triangular mask, or a GRU), then per-step attention
  across the rungs keyed on TIME-VARYING visibility: a rung exists at a
  step only once it has shown a book, so nothing ever attends to a strike
  listed later.
* :class:`LawHead` — the settlement law BY CONSTRUCTION. A partition
  ladder keeps raw per-rung logits (softmax over visible rungs happens in
  :func:`q_from_logits` / :func:`head_loss`); a threshold ladder rebuilds
  its logits as monotone chains along each tail (``less`` non-decreasing,
  ``greater`` non-increasing in rung order), so a trained model cannot
  emit a ladder that violates its own settlement rule. Market adaptation
  is an affine ``(1 + s, b)`` pair of zero-initialized embeddings that
  weight decay shrinks toward the pooled head.
* :func:`head_loss` — winner-NLL over visible rungs (partition) and BCE
  on visible cells (threshold), EVENT-EQUAL weighted: one event is one
  example whatever its rung or lead count.

The adapter's serving surface is a ``(contract, lead) -> q`` table built
once by ``fitted`` and persisted beside ``model.pt`` — ``mode="load"``
consumes no inputs, so there is nothing to rebuild it from at load time,
and a restore that silently answered ``None`` everywhere would be a run
that reports a model it never consulted. It also exposes
``event_logloss``, the per-event validation log-loss over the claims
universe that ``pmquant-ladder-train`` selects its checkpoint on.

Import cost: torch at module top — the ONE sanctioned exception in this
child. This module is named by import path in documents and imported by
the declared-adapter seam at plan time; no node module imports it at its
own top.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import namedtuple
from collections.abc import Mapping

import torch
import torch.nn as nn

from dskit.pipeline.libs.torch import TorchAdapter, TorchBatches

from pmquant.ladder.panels import DEFAULT_K_LVL, PANEL_KEYS, TokenFeaturizer, collate_items
from pmquant.ladder.protocols import LEAD_ROUND_DP, STRIKE_CODES, lead_key

__all__ = [
    "DEFAULT_D_MODEL",
    "DEFAULT_N_TIME_LAYERS",
    "DEFAULT_TIME_ENC",
    "Q_CLIP",
    "SERVING_STATE_KEY",
    "SERVING_SUFFIX",
    "TIME_ENCODERS",
    "Cell",
    "LadderPanelAdapter",
    "LadderQhatModule",
    "LawHead",
    "TokenEncoder",
    "head_loss",
    "q_from_logits",
    "touches",
]

#: The causal sequence encoders the frozen recipe compared (E1).
TIME_ENCODERS = ("gru", "transformer")
DEFAULT_TIME_ENC = "transformer"
DEFAULT_D_MODEL = 64
DEFAULT_N_TIME_LAYERS = 2

#: Frozen architecture widths — never document knobs.
_TOKEN_HIDDEN = 96
_N_HEADS = 4
_FF_DIM = 128
_DROPOUT = 0.15
_HEAD_HIDDEN = 64
_WIDE_HIDDEN = 128
#: What a masked logit is filled with before a softmax over visible rungs.
_MASK_FILL = -1e9

#: The clip applied to ``q`` inside :meth:`LadderPanelAdapter.event_logloss`.
Q_CLIP = 1e-4

#: Where the serving table lands, relative to ``model.pt``'s stem, and the
#: sidecar key its manifest is recorded under.
SERVING_SUFFIX = ".serving.json"
SERVING_STATE_KEY = "serving_table"

#: One visible cell of a prepared split: the item's position in the split,
#: the step and rung, the belief and the label.
Cell = namedtuple("Cell", "item step rung q y")


def _mono_chain(raw, dec, mask):
    """Chain ``raw`` down by ``dec`` within each contiguous masked run along the last axis."""
    C = raw.shape[-1]
    pos = torch.arange(C, device=raw.device).expand_as(raw)
    first = mask & ~torch.roll(mask, 1, dims=-1)
    first[..., 0] = mask[..., 0]
    anchor = torch.cummax(torch.where(first, pos, torch.full_like(pos, -1)), -1).values
    anchor_c = anchor.clamp(min=0)
    d = torch.where(mask, dec, torch.zeros_like(dec))
    excl = torch.cumsum(d, -1) - d
    raw_a = raw.gather(-1, anchor_c)
    excl_a = excl.gather(-1, anchor_c)
    chained = raw_a - (excl - excl_a)
    return torch.where(mask & (anchor >= 0), chained, raw)


class TokenEncoder(nn.Module):
    """Tokens -> causal time encoding -> visible-rung attention (frozen v3).

    Parameters
    ----------
    n_markets : int
        Market-embedding vocabulary size (``len(MarketVocab)``).
    tok_f : int
        Token width ``F`` (:attr:`~pmquant.ladder.panels.TokenFeaturizer.n_features`).
    n_leads : int
        Length of the lead axis ``T``.
    time_enc : str
        ``"transformer"`` (the production recipe: pre-norm, explicit causal
        mask) or ``"gru"`` (causal by construction; E1's comparison).
    d : int
        Embedding width.
    n_layers : int
        Transformer layers (the GRU is single-layer by frozen spec).

    Examples
    --------
    The production encoder over the 41-column token and the 21-lead grid::

        enc = TokenEncoder(n_markets=90, tok_f=41, n_leads=21)
        state = enc(feats, seen, visible, market_id)   # (B, T, C, 64)
    """

    def __init__(
        self,
        n_markets,
        tok_f,
        n_leads,
        time_enc=DEFAULT_TIME_ENC,
        d=DEFAULT_D_MODEL,
        n_layers=DEFAULT_N_TIME_LAYERS,
    ):
        super().__init__()
        if time_enc not in TIME_ENCODERS:
            raise ValueError(f"time_enc must be one of {TIME_ENCODERS}, got {time_enc!r}")
        self.tok = nn.Sequential(
            nn.Linear(tok_f, _TOKEN_HIDDEN), nn.GELU(), nn.Linear(_TOKEN_HIDDEN, d)
        )
        self.missing = nn.Parameter(torch.zeros(d))
        self.lead_emb = nn.Embedding(n_leads, d)
        self.mkt_emb = nn.Embedding(n_markets, d)
        if time_enc == "gru":
            self.time = nn.GRU(d, d, num_layers=1, batch_first=True)
        else:
            layer = nn.TransformerEncoderLayer(
                d, _N_HEADS, _FF_DIM, batch_first=True, norm_first=True, dropout=_DROPOUT
            )
            self.time = nn.TransformerEncoder(layer, n_layers, enable_nested_tensor=False)
        self.rung = nn.MultiheadAttention(d, _N_HEADS, batch_first=True)
        self.rung_norm = nn.LayerNorm(d)
        self.register_buffer(
            "causal", torch.full((n_leads, n_leads), float("-inf")).triu(1)
        )

    def forward(self, feats, seen, visible, market_id):
        """Encode one collated batch.

        Parameters
        ----------
        feats : torch.Tensor
            ``(B, T, C, F)`` tokens.
        seen : torch.Tensor
            ``(B, T, C)`` bool — a real book in the cell; unseen cells take
            the learned ``missing`` token.
        visible : torch.Tensor
            ``(B, T, C)`` bool — the running OR of ``seen`` along time; the
            rung-attention key-padding mask at every step.
        market_id : torch.Tensor
            ``(B,)`` long.

        Returns
        -------
        torch.Tensor
            ``(B, T, C, d)`` states.
        """
        B, T, C, _ = feats.shape
        h = self.tok(feats)
        h = torch.where(seen[..., None], h, self.missing.expand_as(h))
        h = h + self.lead_emb.weight[None, :, None, :]
        h = h + self.mkt_emb(market_id)[:, None, None, :]
        h = h.permute(0, 2, 1, 3).reshape(B * C, T, -1)
        if isinstance(self.time, nn.GRU):
            h, _ = self.time(h)
        else:
            h = self.time(h, mask=self.causal)
        h = h.reshape(B, C, T, -1).permute(0, 2, 1, 3)
        s = h.reshape(B * T, C, -1)
        kp = ~visible.reshape(B * T, C)
        kp = kp & ~kp.all(-1, keepdim=True)  # an all-empty step attends to itself
        mixed, _ = self.rung(s, s, s, key_padding_mask=kp, need_weights=False)
        return self.rung_norm(s + mixed).reshape(B, T, C, -1)


class LawHead(nn.Module):
    """States + own executable asks -> law-shaped q̂ logits ``(B, T, C)``.

    Parameters
    ----------
    n_markets : int
        Market-embedding vocabulary size.
    d : int
        Encoder state width.
    wide_head : bool
        The E5b variant: a deeper trunk, the same two law outputs.

    Examples
    --------
    ::

        head = LawHead(n_markets=90, d=64)
        logit = head(state, ask_yes, ask_no, market_id, st_code, is_partition)
    """

    def __init__(self, n_markets, d=DEFAULT_D_MODEL, wide_head=False):
        super().__init__()
        if wide_head:
            self.trunk = nn.Sequential(
                nn.Linear(d + 2, _WIDE_HIDDEN),
                nn.GELU(),
                nn.Linear(_WIDE_HIDDEN, _HEAD_HIDDEN),
                nn.GELU(),
                nn.Linear(_HEAD_HIDDEN, 2),
            )
        else:
            self.trunk = nn.Sequential(
                nn.Linear(d + 2, _HEAD_HIDDEN), nn.GELU(), nn.Linear(_HEAD_HIDDEN, 2)
            )
        self.s_emb = nn.Embedding(n_markets, 1)  # scale = 1 + s
        self.b_emb = nn.Embedding(n_markets, 1)  # bias  = b
        nn.init.zeros_(self.s_emb.weight)
        nn.init.zeros_(self.b_emb.weight)

    def forward(self, state, ask_yes, ask_no, market_id, st_code, is_partition):
        """Turn states into law-shaped logits.

        Parameters
        ----------
        state : torch.Tensor
            ``(B, T, C, d)`` encoder states.
        ask_yes, ask_no : torch.Tensor
            ``(B, T, C)`` executable touches (0 where absent — presence is
            already in the state; these are calibration skips).
        market_id : torch.Tensor
            ``(B,)`` long.
        st_code : torch.Tensor
            ``(B, C)`` long strike codes
            (:data:`~pmquant.ladder.protocols.STRIKE_CODES`).
        is_partition : torch.Tensor
            ``(B,)`` bool.

        Returns
        -------
        torch.Tensor
            ``(B, T, C)`` logits: raw for partitions, monotone chains per
            threshold tail.
        """
        z = self.trunk(torch.cat([state, ask_yes[..., None], ask_no[..., None]], -1))
        raw, dec = z[..., 0], nn.functional.softplus(z[..., 1])
        scale = 1.0 + self.s_emb(market_id)[:, None, :]  # (B, 1, 1)
        bias = self.b_emb(market_id)[:, None, :]
        raw = raw * scale + bias
        greater = (st_code == STRIKE_CODES["greater"])[:, None, :].expand_as(raw)
        less = (st_code == STRIKE_CODES["less"])[:, None, :].expand_as(raw)
        mono_g = _mono_chain(raw, dec, greater)
        mono_l = _mono_chain(raw.flip(-1), dec.flip(-1), less.flip(-1)).flip(-1)
        out = torch.where(greater, mono_g, torch.where(less, mono_l, raw))
        return torch.where(is_partition[:, None, None].expand_as(raw), raw, out)


def q_from_logits(logit, visible, is_partition):
    """Turn logits into probabilities under each event's settlement law.

    Parameters
    ----------
    logit : torch.Tensor
        ``(B, T, C)``.
    visible : torch.Tensor
        ``(B, T, C)`` bool — the rungs a partition softmax runs over.
    is_partition : torch.Tensor
        ``(B,)`` bool.

    Returns
    -------
    torch.Tensor
        ``(B, T, C)``: sigmoid for threshold events, softmax over visible
        rungs for partition events.
    """
    q_thr = torch.sigmoid(logit)
    q_par = torch.softmax(logit.masked_fill(~visible, _MASK_FILL), -1)
    return torch.where(is_partition[:, None, None].expand_as(logit), q_par, q_thr)


def head_loss(logit, batch):
    """Compute the law-shaped, event-equal objective over one batch.

    Parameters
    ----------
    logit : torch.Tensor
        ``(B, T, C)`` from :class:`LawHead`.
    batch : dict
        A collated batch carrying ``visible``, ``contract_mask``, ``y``
        and ``is_partition``.

    Returns
    -------
    torch.Tensor
        A scalar: the partition branch (winner-NLL over visible rungs at
        the steps where the winner is listed, event-mean then mean over
        events) plus the threshold branch (BCE-with-logits on visible
        cells, event-mean then mean), divided by the number of branches
        present. A partition event whose labels do not name EXACTLY ONE
        winner (a bracket ladder that never settled YES, or a store that
        settled two) has no listed winner at any step and contributes
        nothing — ``argmax`` of such labels is a rung the law never
        chose, never a target.
    """
    part = batch["is_partition"]
    vis = batch["visible"] & batch["contract_mask"][:, None, :]
    total, n = logit.new_zeros(()), 0
    if part.any():
        lg = logit[part].masked_fill(~vis[part], _MASK_FILL)
        y_part = batch["y"][part]
        win = y_part.argmax(-1)
        one_winner = y_part.sum(-1) == 1
        pick = win[:, None, None].expand(-1, lg.shape[1], 1)
        win_vis = vis[part].gather(-1, pick).squeeze(-1)
        nll = -torch.log_softmax(lg, -1).gather(-1, pick).squeeze(-1)
        m = win_vis & vis[part].any(-1) & one_winner[:, None]
        if m.any():
            per_ev = (nll * m).sum(-1) / m.sum(-1).clamp(min=1)
            total = total + per_ev[m.any(-1)].mean()
            n += 1
    if (~part).any():
        m = vis[~part]
        if m.any():
            y = batch["y"][~part][:, None, :].expand_as(logit[~part])
            ce = (
                nn.functional.binary_cross_entropy_with_logits(
                    logit[~part], y, reduction="none"
                )
                * m
            )
            per_ev = ce.sum((1, 2)) / m.sum((1, 2)).clamp(min=1)
            total = total + per_ev[m.sum((1, 2)) > 0].mean()
            n += 1
    return total / max(n, 1)


def touches(batch, names):
    """Read both executable touches straight off the token columns.

    Parameters
    ----------
    batch : dict
        A collated batch (``feats (B, T, C, F)``).
    names : sequence of str
        The token column names (:meth:`TokenFeaturizer.feature_names`).

    Returns
    -------
    tuple of torch.Tensor
        ``(ask_yes, ask_no)``, each ``(B, T, C)``.
    """
    names = list(names)
    feats = batch["feats"]
    return feats[..., names.index("yes_touch")], feats[..., names.index("no_touch")]


class LadderQhatModule(nn.Module):
    """The ladder transformer as ONE importable ``nn.Module``.

    Composes :class:`TokenEncoder` and :class:`LawHead`; ``forward`` takes
    the collated panel batch and returns law-shaped logits ``(B, T, C)``.
    Every constructor knob is a JSON scalar, so a document declares the
    architecture in ``module_params`` and a search node reaches each knob
    by name.

    Parameters
    ----------
    n_markets : int
        Market-embedding vocabulary size — supplied by the adapter from
        the DATA (``module_params``), never a document knob.
    n_leads : int
        Length of the lead axis — data-supplied too.
    k_lvl : int
        Book levels per side entering a token (frozen recipe: 5). The
        token width is DERIVED from it through the featurizer, never
        restated.
    drop : str or list of str or None
        The ablation groups the panels were built with; recorded here so
        the artifact names its own featurization (the zeroing itself
        happens in the panels node, whose ``drop`` must agree).
    time_enc : str
        ``"transformer"`` (production) or ``"gru"``.
    d_model : int
        Encoder width.
    n_time_layers : int
        Transformer depth.
    wide_head : bool
        The deeper head trunk (E5b).

    Examples
    --------
    The production recipe over a 90-series vocab::

        module = LadderQhatModule(n_markets=90, n_leads=21, drop="context")
        logit = module(collate_items(items))   # (B, 21, C)
    """

    def __init__(
        self,
        n_markets,
        n_leads,
        k_lvl=DEFAULT_K_LVL,
        drop=None,
        time_enc=DEFAULT_TIME_ENC,
        d_model=DEFAULT_D_MODEL,
        n_time_layers=DEFAULT_N_TIME_LAYERS,
        wide_head=False,
    ):
        super().__init__()
        self.featurizer = TokenFeaturizer(int(k_lvl), drop=() if drop is None else drop)
        self.enc = TokenEncoder(
            int(n_markets),
            self.featurizer.n_features,
            int(n_leads),
            time_enc=str(time_enc),
            d=int(d_model),
            n_layers=int(n_time_layers),
        )
        self.head = LawHead(int(n_markets), d=int(d_model), wide_head=bool(wide_head))

    def forward(self, batch):
        """Score one collated batch.

        Parameters
        ----------
        batch : dict
            :func:`~pmquant.ladder.panels.collate_items` output.

        Returns
        -------
        torch.Tensor
            ``(B, T, C)`` law-shaped logits.

        Raises
        ------
        ValueError
            When the batch's layout identity (``featurizer``: the panels
            node's ``k_lvl``/``drop``) is not this module's — the artifact
            would name an ablation its tokens never got.
        """
        identity = tuple(batch["featurizer"])
        if identity != self.featurizer.identity:
            raise ValueError(
                f"the batch was featurized as (k_lvl, drop) = {identity!r} but this "
                f"module declares {self.featurizer.identity!r} — the panels node's "
                "k_lvl/drop and the model's module_params must agree"
            )
        state = self.enc(batch["feats"], batch["seen"], batch["visible"], batch["market_id"])
        ask_yes, ask_no = touches(batch, self.featurizer.feature_names())
        return self.head(
            state, ask_yes, ask_no, batch["market_id"], batch["st_code"], batch["is_partition"]
        )


def _field(record, name):
    """Read one field off a record: mapping key first, then attribute."""
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


class LadderPanelAdapter(TorchAdapter):
    """The dataset seam for ladder EVENT PANELS.

    An example is one EVENT — its whole rung x lead panel — so
    ``requires_features`` is ``False`` (there is no flat feature list to
    name) and ``applies_loss`` stays ``False`` (the objective is
    :func:`head_loss`; a document declaring ``loss`` is refused by name
    rather than ignored). The independence unit is the event by
    construction: batches are drawn over events and the loss weights every
    event equally.

    Parameters
    ----------
    params : dict or None
        The node's params; this adapter declares NO knobs of its own
        (``_PARAMS = ()``) — the market count is data, read off the items.

    Examples
    --------
    Prepare panel items and take the objective on the whole split::

        adapter = LadderPanelAdapter({})
        batches = adapter.prepare(items, {}, where="rows")
        loss = adapter.loss(module, adapter.select(batches, None))
    """

    requires_features = False
    _PARAMS = ()

    def __init__(self, params=None):
        super().__init__(params)
        self._serving = None
        self._vocab = None

    # -- dataset -----------------------------------------------------------

    def _vocab_problems(self, item, where, i):
        """Name how item ``i`` disagrees with its own vocab, or the fitted one."""
        series, market_id, vocab = str(item["series"]), int(item["market_id"]), item["vocab"]
        if vocab.get(series) != market_id:
            return (
                f"{where}: item {i} ({series!r}) carries market_id {market_id} but its own "
                f"vocab says {vocab.get(series)!r} — the item was not indexed by the vocab "
                "it carries"
            )
        if self._vocab is not None and self._vocab.get(series) != market_id:
            return (
                f"{where}: item {i} is series {series!r} at market_id {market_id}, but this "
                f"model was trained with the vocab {self._vocab} "
                + (
                    "which does not hold that series — an unseen market has no trained "
                    "embedding, and scoring it under another market's would be silent"
                    if series not in self._vocab
                    else f"where it is index {self._vocab[series]} — the series set "
                    "changed between training and now, shifting the embeddings"
                )
            )
        return None

    def prepare(self, rows, params, *, where):
        """Keep the panel items, count the rest, and hold them to ONE vocab.

        Parameters
        ----------
        rows : iterable
            Candidate items; one is usable when it is a mapping carrying
            every :data:`~pmquant.ladder.panels.PANEL_KEYS` key.
        params : dict
            The node's params (unused — the panel IS the example).
        where : str
            The port name, for the refusal.

        Returns
        -------
        TorchBatches
            The usable items as the payload, ``n_skipped`` for the rest.

        Raises
        ------
        ValueError
            When rows were given but none is a panel item — the port is
            wired from the wrong node; when the items carry two different
            vocabs (panels from two builds); when an item's ``market_id``
            is not what its vocab says; or, once this adapter is fitted or
            restored, when an item's series is absent from the trained
            vocab or sits at a different index there — an unseen or
            shifted market is refused BY NAME, never scored under another
            market's embedding.
        """
        rows = list(rows or [])
        usable, skipped = [], 0
        for row in rows:
            if isinstance(row, Mapping) and all(k in row for k in PANEL_KEYS):
                usable.append(row)
            else:
                skipped += 1
        if rows and not usable:
            first = rows[0]
            missing = (
                sorted(set(PANEL_KEYS) - set(first))
                if isinstance(first, Mapping)
                else list(PANEL_KEYS)
            )
            raise ValueError(
                f"{where}: no ladder panel item carried the keys {list(PANEL_KEYS)} "
                f"(first row lacks {missing}) — wire this port from "
                "pmquant-ladder-panels, whose items carry them"
            )
        for i, item in enumerate(usable):
            if dict(item["vocab"]) != dict(usable[0]["vocab"]):
                raise ValueError(
                    f"{where}: item {i} carries a different vocab than item 0 — panels "
                    "from two builds cannot share a fit; wire one pmquant-ladder-panels node"
                )
            problem = self._vocab_problems(item, where, i)
            if problem:
                raise ValueError(problem)
        return TorchBatches(len(usable), usable, n_skipped=skipped)

    def module_params(self, batches, params):
        """Name the two shape kwargs the DATA implies.

        Parameters
        ----------
        batches : TorchBatches
            The prepared TRAIN split.
        params : dict
            The node's params (unused).

        Returns
        -------
        dict
            ``{"n_markets": len(vocab), "n_leads": T}`` — the WHOLE vocab
            the panels were indexed by, not the markets the train split
            happens to hold, so a series first seen in val or test has an
            embedding row; merged UNDER the document's ``module_params``
            by the pack, so a declared value wins. ``{}`` for an empty
            split.
        """
        items = batches.payload
        if not items:
            return {}
        return {
            "n_markets": len(items[0]["vocab"]),
            "n_leads": int(items[0]["feats"].shape[0]),
        }

    def select(self, batches, index):
        """Collate the items at ``index`` (the whole split when ``None``).

        Parameters
        ----------
        batches : TorchBatches
            A prepared split.
        index : torch.Tensor or None
            Item positions.

        Returns
        -------
        dict
            :func:`~pmquant.ladder.panels.collate_items` output.
        """
        items = batches.payload
        chosen = items if index is None else [items[int(i)] for i in index]
        return collate_items(chosen)

    def to_device(self, batch, device):
        """Move every tensor of a collated batch.

        Parameters
        ----------
        batch : dict
            A collated batch.
        device : str or torch.device
            The destination.

        Returns
        -------
        dict
            The same keys, tensors moved.
        """
        return {k: (v.to(device) if torch.is_tensor(v) else v) for k, v in batch.items()}

    # -- objective ---------------------------------------------------------

    def loss(self, module, batch):
        """Compute :func:`head_loss` over one batch.

        Parameters
        ----------
        module : LadderQhatModule
            The module being fitted.
        batch : dict
            A collated batch.

        Returns
        -------
        torch.Tensor
            The scalar objective.
        """
        return head_loss(module(batch), batch)

    def beliefs(self, module, batch):
        """Read ``(q, y)`` over the VISIBLE cells of one batch.

        Parameters
        ----------
        module : LadderQhatModule
            The module.
        batch : dict
            A collated batch.

        Returns
        -------
        tuple of list
            Beliefs and matching 0/1 labels — the pack's per-epoch
            probability metrics' material.
        """
        cells = self._cells(module, batch)
        return [c.q for c in cells], [c.y for c in cells]

    def _cells(self, module, batch, offset=0):
        """Score one collated batch in eval mode; one :data:`Cell` per visible cell."""
        device = next(module.parameters()).device
        if batch["feats"].device != device:
            batch = self.to_device(batch, device)
        training = module.training
        module.eval()
        try:
            with torch.no_grad():
                visible = batch["visible"] & batch["contract_mask"][:, None, :]
                q = q_from_logits(module(batch), visible, batch["is_partition"])
        finally:
            module.train(training)
        where = visible.nonzero(as_tuple=False)
        b, t, c = where[:, 0], where[:, 1], where[:, 2]
        return [
            Cell(int(i) + offset, int(k), int(r), float(v), float(yy))
            for i, k, r, v, yy in zip(
                b.tolist(), t.tolist(), c.tolist(), q[b, t, c].tolist(), batch["y"][b, c].tolist()
            )
        ]

    def cells(self, module, batches, *, batch_size=None):
        """Score a prepared split cell by cell.

        Parameters
        ----------
        module : LadderQhatModule
            The module.
        batches : TorchBatches
            The split.
        batch_size : int or None
            Items per forward; ``None`` scores the whole split at once.

        Returns
        -------
        list of Cell
            Every visible cell, in item order, with ``item`` indexing the
            split's payload.
        """
        n = len(batches)
        if n == 0:
            return []
        step = n if not batch_size else int(batch_size)
        out = []
        for start in range(0, n, step):
            idx = torch.arange(start, min(start + step, n))
            out.extend(self._cells(module, self.select(batches, idx), offset=start))
        return out

    def event_logloss(self, module, batches, *, eligible_only=True, batch_size=None):
        """Compute the per-event mean binary log-loss, averaged over events.

        The ``claims_val_event_ll`` statistic: each event's mean over its
        visible cells (``q`` clipped to ``[Q_CLIP, 1 - Q_CLIP]``), then the
        mean over events — event-equal, like the objective.

        Parameters
        ----------
        module : LadderQhatModule
            The module.
        batches : TorchBatches
            The split to score.
        eligible_only : bool
            Score only items flagged ``eligible`` (the claims universe).
        batch_size : int or None
            Items per forward.

        Returns
        -------
        float
            The statistic, or ``nan`` when no event qualifies.
        """
        items = batches.payload
        per_event = {}
        for cell in self.cells(module, batches, batch_size=batch_size):
            if eligible_only and not items[cell.item]["eligible"]:
                continue
            q = min(max(cell.q, Q_CLIP), 1.0 - Q_CLIP)
            per_event.setdefault(cell.item, []).append(
                -(cell.y * math.log(q) + (1.0 - cell.y) * math.log(1.0 - q))
            )
        if not per_event:
            return float("nan")
        return sum(sum(v) / len(v) for v in per_event.values()) / len(per_event)

    # -- serving -----------------------------------------------------------

    def serving_table(self, module, batches, *, batch_size=None):
        """Build ``(contract, lead) -> q`` over one prepared split.

        Parameters
        ----------
        module : LadderQhatModule
            The module.
        batches : TorchBatches
            The split.
        batch_size : int or None
            Items per forward.

        Returns
        -------
        dict
            Keyed by ``(contract ticker, lead_key(lead_frac))`` over every
            visible cell.
        """
        items = batches.payload
        table = {}
        for cell in self.cells(module, batches, batch_size=batch_size):
            item = items[cell.item]
            contracts, fracs = item["contracts"], item["lead_fracs"]
            if cell.rung >= len(contracts) or cell.step >= len(fracs):
                continue
            table[(str(contracts[cell.rung]), lead_key(fracs[cell.step]))] = cell.q
        return table

    def fitted(self, module, train_batches, val_batches):
        """Materialize the serving table once the fit closes, and keep the vocab.

        Coverage is exactly the panels that were WIRED (train + val); a
        cell outside them answers ``None`` from :meth:`predict`. The
        train items' vocab becomes the adapter's: from here on
        :meth:`prepare` refuses a series the fit never indexed.

        Parameters
        ----------
        module : LadderQhatModule
            The fitted (selected) module.
        train_batches : TorchBatches
            The train split.
        val_batches : TorchBatches or None
            The val split, when wired.

        Returns
        -------
        dict
            The table, also kept on the adapter.

        Raises
        ------
        ValueError
            When the table is EMPTY — a model that answers nothing for
            everything while the run exits 0.
        """
        table = {}
        for batches in (train_batches, val_batches):
            if batches is not None and len(batches):
                table.update(self.serving_table(module, batches))
        if not table:
            raise ValueError(
                "the ladder fit produced an EMPTY serving table — no visible cell "
                "had a contract ticker to key a belief on; the fitted model would "
                "answer None for every lookup"
            )
        self._serving = table
        source = next(b for b in (train_batches, val_batches) if b is not None and len(b))
        self._vocab = {str(k): int(v) for k, v in source.payload[0]["vocab"].items()}
        return table

    def predict(self, module, record):
        """Look one ``(contract, lead_frac)`` up in the serving table.

        Parameters
        ----------
        module : LadderQhatModule
            Unused — the table answers.
        record : mapping or object
            Carries ``contract`` and ``lead_frac`` (key or attribute).

        Returns
        -------
        float or None
            The belief, or ``None`` for a cell the model never saw (no
            coverage — never a fabricated number).

        Raises
        ------
        ValueError
            When no table exists at all: a missing table means the model
            was never asked, and answering ``None`` everywhere would let a
            run report a model it never used.
        """
        if self._serving is None:
            raise ValueError(
                "the ladder serving table is missing: this adapter was neither fitted "
                "nor restored from an artifact — a missing table means the model was "
                "never asked, and None for every lookup would hide that"
            )
        contract, lead = _field(record, "contract"), _field(record, "lead_frac")
        if contract is None or lead is None:
            return None
        try:
            key = (str(contract), lead_key(lead))
        except (TypeError, ValueError):
            return None
        return self._serving.get(key)

    # -- fitted state the state_dict does not hold -------------------------

    def save_state(self, prefix):
        """Persist the serving table and the market vocab beside ``model.pt``.

        Parameters
        ----------
        prefix : str
            The artifact path with its extension stripped.

        Returns
        -------
        dict
            ``{"serving_table": {"file", "cells", "sha256"}}`` — recorded
            in the sidecar, hence under the artifact's content hash. The
            file also carries ``vocab``, the ``{series: market_id}`` map
            the weights are indexed by.

        Raises
        ------
        ValueError
            When there is no table (or no vocab) to write.
        """
        table = self._serving
        if not table or self._vocab is None:
            raise ValueError(
                "refusing to write a ladder artifact with no serving table — it would "
                "restore into a model that answers nothing"
            )
        path = f"{prefix}{SERVING_SUFFIX}"
        text = json.dumps(
            {
                "lead_key_dp": LEAD_ROUND_DP,
                "vocab": dict(sorted(self._vocab.items())),
                "cells": [[contract, lead, q] for (contract, lead), q in sorted(table.items())],
            },
            separators=(",", ":"),
            allow_nan=False,
        )
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return {
            SERVING_STATE_KEY: {
                "file": os.path.basename(path),
                "cells": len(table),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        }

    def load_state(self, prefix, recorded):
        """Restore the serving table, or RAISE naming what is missing.

        Parameters
        ----------
        prefix : str
            The artifact path with its extension stripped.
        recorded : dict
            The sidecar's ``adapter_state`` manifest.

        Returns
        -------
        dict
            The restored table, also kept on the adapter.

        Raises
        ------
        ValueError
            On a manifest with no serving-table entry, a missing file, a
            sha256 mismatch, an empty table, or a file without the vocab.
        """
        entry = (recorded or {}).get(SERVING_STATE_KEY)
        if not entry:
            raise ValueError(
                "this artifact's sidecar records no serving table — restoring it "
                "would give a model that answers None for every contract; re-fit"
            )
        path = f"{prefix}{SERVING_SUFFIX}"
        if not os.path.isfile(path):
            raise ValueError(
                f"the serving table {path!r} recorded in the sidecar is not on disk — "
                "the artifact is incomplete, not merely stale"
            )
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != entry.get("sha256"):
            raise ValueError(
                f"the serving table {path!r} has sha256 {digest}, but the sidecar records "
                f"{entry.get('sha256')!r} — these are not the beliefs this artifact wrote"
            )
        payload = json.loads(text)
        places = int(payload.get("lead_key_dp", LEAD_ROUND_DP))
        table = {
            (str(contract), round(float(lead), places)): float(q)
            for contract, lead, q in payload["cells"]
        }
        if not table:
            raise ValueError(f"the serving table {path!r} is empty")
        vocab = payload.get("vocab")
        if not isinstance(vocab, dict) or not vocab:
            raise ValueError(
                f"the serving table {path!r} records no market vocab — without the "
                "{series: market_id} map the weights were indexed by, a restored model "
                "cannot tell a series from the one whose embedding it would borrow"
            )
        self._serving = table
        self._vocab = {str(k): int(v) for k, v in vocab.items()}
        return table

"""``models`` — the nn.Module zoo the declared torch seam names.

This module is RUN-PATH ONLY: ``torch`` is imported at module top because
subclassing ``nn.Module`` needs it, so nothing in the child's import
surface (``__init__``, ``nodes``, ``connectors``) may import THIS module.
The pipeline reaches it exclusively through the document —
``"module": "intraday_poc.models:NextBarLSTM"`` — which
``DeclaredTrain``/``DeclaredPredict`` resolve inside ``run()`` (ADR-0025),
so documents naming it still plan on machines without torch.

The feature contract matches the pack's default ``RowVectorAdapter``:
each row is a FLAT vector of the last ``lookback`` one-bar log returns
(``ret_lag_0`` = most recent … ``ret_lag_{lookback-1}`` = oldest), and
the label is the NEXT bar's log return. The module owns the resequencing:
it flips the vector to chronological order, feeds it through the LSTM as
a ``[batch, lookback, 1]`` sequence, and reads the last hidden state.
"""

from __future__ import annotations

import torch

__all__ = ["NextBarLSTM"]


class NextBarLSTM(torch.nn.Module):
    """Next-bar-return LSTM over a flat lag vector. See module docs.

    Constructor knobs (the document's ``module_params``):

    - ``lookback`` (required) — how many lagged returns one row carries;
      cross-checked against the incoming width, refused on mismatch.
    - ``hidden_size`` — LSTM width; default 32.
    - ``num_layers`` — stacked LSTM depth; default 1.
    """

    def __init__(self, lookback: int, hidden_size: int = 32,
                 num_layers: int = 1):
        super().__init__()
        if not isinstance(lookback, int) or isinstance(lookback, bool) \
                or lookback < 2:
            raise ValueError(f"lookback must be an int >= 2, got {lookback!r}")
        self.lookback = lookback
        self.lstm = torch.nn.LSTM(
            input_size=1, hidden_size=hidden_size, num_layers=num_layers,
            batch_first=True,
        )
        self.head = torch.nn.Linear(hidden_size, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if x.dim() == 1:
            x = x.unsqueeze(0)
        if x.size(-1) != self.lookback:
            raise ValueError(
                f"expected {self.lookback} lagged features per row "
                f"(module_params.lookback), got {x.size(-1)} — the document's "
                "features list and lookback disagree"
            )
        # ret_lag_0 is the MOST RECENT return; the LSTM wants time ascending.
        seq = torch.flip(x, dims=(-1,)).unsqueeze(-1)   # [N, lookback, 1]
        out, _ = self.lstm(seq)
        return self.head(out[:, -1, :])                  # [N, 1]

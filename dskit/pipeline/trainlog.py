"""Per-epoch training telemetry — the loss curve an operator can watch.

A ``train``-role node that runs epochs must be able to answer, WHILE it
runs, the two questions the owner asks of every fit: is the training loss
falling, and is the VALIDATION loss falling with it. Before this module
the toolkit's trainers reported a single ``final_loss`` at the end and no
validation number at all — a fit that diverged at epoch 2 looked exactly
like one that converged, and the run report carried no curve to inspect
afterwards.

Two pieces, both stdlib-only (tier-1 rules, ``tests/pipeline/test_purity``):

* :func:`probability_metrics` — the evaluation metrics for a CALIBRATED
  probability model, computed from the metrics the toolkit already
  owns. ``logloss`` and ``brier`` come from :mod:`dskit.pipeline.metrics`
  (the same registry ``validate`` scores against, so a training curve and
  the run's verdict speak one language), and ``ece`` is the expected
  calibration error over the SAME decile buckets ``validate``'s
  reliability ledger uses (:func:`dskit.pipeline.kinds_stats._bucket`) —
  imported, never re-derived, so the two can never drift into reporting
  different calibration for one model. Deliberately absent: AUC. It is
  computed nowhere else in this codebase, it is rank-only, and a
  probability model that must be well CALIBRATED (sizing divides by the
  belief) is not measured by a metric invariant to monotone rescaling.
  The metrics are returned only when the labels really are binary; a
  regression fit gets its losses and no fabricated probability scores.

* :class:`TrainingCurve` — accumulates one row per epoch, STREAMS a
  compact line as each epoch closes (so it is visible during the run, not
  reconstructed after it), and renders the durable artifact payload the
  run report reads.

Verbosity is bounded by construction. ``max_lines`` strides the stream
when a fit runs more epochs than that — the first and last epoch always
print, and so does every improvement in the tracked objective — so a long
fit costs a fixed number of lines instead of one per epoch. The rows are
all kept regardless: the artifact is complete even when the stream is
sampled.

Import cost: stdlib + ``dskit.pipeline`` only.
"""

from __future__ import annotations

import math

from dskit.pipeline.kinds_stats import _bucket
from dskit.pipeline.metrics import brier, logloss

__all__ = [
    "DEFAULT_MAX_LINES",
    "TrainingCurve",
    "is_binary",
    "probability_metrics",
]

#: Streamed lines a single fit may spend before the stream strides.
DEFAULT_MAX_LINES = 20


def is_binary(labels) -> bool:
    """True when every label is exactly 0.0 or 1.0 (and there is one).

    The gate on the probability metrics: ``logloss``/``brier``/``ece`` are
    meaningful for a settled binary outcome and meaningless for a
    continuous target, and the toolkit's own metric functions REFUSE a
    non-binary ``y`` by name. Checking first is what lets one trainer
    serve both without either lying or crashing.
    """
    seen = False
    for y in labels:
        if isinstance(y, bool) or not isinstance(y, (int, float)):
            return False
        if float(y) not in (0.0, 1.0):
            return False
        seen = True
    return seen


def probability_metrics(preds, labels) -> dict:
    """Mean ``logloss``, ``brier`` and ``ece`` over one split's rows.

    Parameters
    ----------
    preds : sequence of float
        Beliefs in ``[0, 1]``. Values outside are CLAMPED rather than
        refused — a mid-training head can overshoot the unit interval, and
        a metric call that raised would abort the fit instead of showing
        the operator the very pathology they are watching for.
    labels : sequence of float
        Realized 0/1 outcomes, same length as ``preds``.

    Returns
    -------
    dict
        ``{"logloss": .., "brier": .., "ece": .., "n": ..}``, or ``{}``
        when the labels are not binary or there are no rows.

    Notes
    -----
    ``ece`` is the n-weighted mean over decile buckets of
    ``|mean(predicted) - mean(outcome)|`` — 0 is perfectly calibrated. It
    is the single-number reduction of the reliability table
    ``validate`` already writes into its evidence ledger.
    """
    preds = list(preds)
    labels = list(labels)
    if len(preds) != len(labels):
        raise ValueError(
            f"probability_metrics: {len(preds)} prediction(s) but "
            f"{len(labels)} label(s) — they must pair"
        )
    if not preds or not is_binary(labels):
        return {}

    ll_sum = 0.0
    br_sum = 0.0
    buckets: dict[str, dict] = {}
    for q_raw, y_raw in zip(preds, labels):
        if not isinstance(q_raw, (int, float)) or not math.isfinite(q_raw):
            return {}  # a diverged head: report the losses, invent nothing
        q = min(max(float(q_raw), 0.0), 1.0)
        y = float(y_raw)
        ll_sum += logloss(q, y)
        br_sum += brier(q, y)
        cell = buckets.setdefault(_bucket(q), {"n": 0, "q": 0.0, "y": 0.0})
        cell["n"] += 1
        cell["q"] += q
        cell["y"] += y

    n = len(preds)
    ece = sum(
        (cell["n"] / n) * abs(cell["q"] / cell["n"] - cell["y"] / cell["n"])
        for cell in buckets.values()
    )
    return {
        "logloss": ll_sum / n,
        "brier": br_sum / n,
        "ece": ece,
        "n": n,
    }


def _fmt(value) -> str:
    """A metric for the stream: 4 dp, or ``—`` when it is absent/unusable."""
    if value is None or not isinstance(value, (int, float)):
        return "—"
    if isinstance(value, bool) or not math.isfinite(value):
        return "—"
    return f"{value:.4f}"


class TrainingCurve:
    """One fit's per-epoch record: streamed as it runs, durable at the end.

    Parameters
    ----------
    key : str
        The node key, so a multi-node run's lines are attributable.
    log : logging.Logger
        The node's logger (``self.log``). Lines go through logging, not
        ``print``, so they land in the run's ``run.log`` as well as the
        operator's terminal.
    total_epochs : int
        Planned epochs — used to render ``epoch 3/20`` and to decide the
        stride.
    objective : str, optional
        Which recorded field names the BEST epoch (default ``val_loss``,
        falling back to ``train_loss`` when no validation is wired). Lower
        is better — every metric this module records is a loss.
    log_every : int, optional
        Stream at most one line per this many epochs (default 1). The
        stride from ``max_lines`` is applied on top, whichever is coarser.
    max_lines : int, optional
        Ceiling on streamed lines for the whole fit
        (:data:`DEFAULT_MAX_LINES`). 0 disables the stream entirely — the
        rows are still recorded and still land in the artifact.
    """

    def __init__(
        self,
        key,
        log,
        *,
        total_epochs,
        objective="val_loss",
        log_every=1,
        max_lines=DEFAULT_MAX_LINES,
    ):
        self.key = str(key)
        self.log = log
        self.total_epochs = int(total_epochs)
        self.objective = str(objective)
        self.rows: list[dict] = []
        self.best_epoch = None
        self.best_value = math.inf
        self._log_every = max(1, int(log_every))
        self._max_lines = max(0, int(max_lines))
        self._streamed = 0
        if self._max_lines and self.total_epochs > self._max_lines:
            # Stride so the whole fit costs ~max_lines, never one per epoch.
            self._log_every = max(
                self._log_every,
                math.ceil(self.total_epochs / self._max_lines),
            )

    # -- recording ---------------------------------------------------------

    def record(self, epoch, train_loss, *, val_loss=None, metrics=None, seconds=None):
        """Append one epoch's row and stream it when the stride allows.

        Returns the row, so a caller can early-stop on it without
        recomputing anything.
        """
        row = {"epoch": int(epoch), "train_loss": _num(train_loss)}
        if val_loss is not None:
            row["val_loss"] = _num(val_loss)
        for name, value in (metrics or {}).items():
            row[name] = _num(value)
        if seconds is not None:
            row["seconds"] = round(float(seconds), 3)

        tracked = row.get(self.objective)
        if tracked is None:
            tracked = row.get("train_loss")
        improved = (
            isinstance(tracked, (int, float))
            and math.isfinite(tracked)
            and tracked < self.best_value
        )
        if improved:
            self.best_value = float(tracked)
            self.best_epoch = row["epoch"]
        row["best"] = bool(improved)
        self.rows.append(row)

        if self._should_stream(row, improved):
            self._stream(row, improved)
        return row

    def _should_stream(self, row, improved) -> bool:
        if not self._max_lines:
            return False
        if row["epoch"] in (1, self.total_epochs):
            return True  # the two an operator always wants
        if improved:
            return True  # a new best is never silent
        return row["epoch"] % self._log_every == 0

    def _stream(self, row, improved) -> None:
        parts = [f"train {_fmt(row.get('train_loss'))}"]
        if "val_loss" in row:
            parts.append(f"val {_fmt(row['val_loss'])}")
        scored = [
            f"{name} {_fmt(row[name])}"
            for name in ("logloss", "brier", "ece")
            if name in row
        ]
        if scored:
            parts.append(" ".join(scored))
        if "seconds" in row:
            parts.append(f"{row['seconds']:.1f}s")
        self.log.info(
            "%s: epoch %d/%d | %s%s",
            self.key,
            row["epoch"],
            self.total_epochs,
            " | ".join(parts),
            "  <- best" if improved else "",
        )
        self._streamed += 1

    # -- output ------------------------------------------------------------

    def summary(self) -> dict:
        """The compact reduction for a node's ``metrics`` output."""
        out = {
            "epochs_run": len(self.rows),
            "best_epoch": self.best_epoch if self.best_epoch is not None else -1,
            "objective": self.objective,
        }
        if math.isfinite(self.best_value):
            out["best_" + self.objective] = self.best_value
        if self.rows:
            last = self.rows[-1]
            out["final_train_loss"] = last.get("train_loss")
            for name in ("val_loss", "logloss", "brier", "ece"):
                if name in last:
                    out["final_" + name] = last[name]
        return {k: v for k, v in out.items() if v is not None}

    def payload(self) -> dict:
        """The durable artifact body — every epoch, streamed or not."""
        return {
            "node": self.key,
            "objective": self.objective,
            "total_epochs": self.total_epochs,
            "epochs_run": len(self.rows),
            "best_epoch": self.best_epoch if self.best_epoch is not None else -1,
            "streamed_lines": self._streamed,
            "note": (
                "One row per epoch. 'best' marks an improvement in the tracked "
                "objective. The stream may be strided (streamed_lines < "
                "epochs_run); THIS table is always complete."
            ),
            "epochs": self.rows,
        }

    def log_final(self) -> None:
        """One closing line: where the fit ended and where it was best."""
        if not self.rows:
            self.log.info("%s: no epochs ran", self.key)
            return
        last = self.rows[-1]
        best = (
            f"best {self.objective} {_fmt(self.best_value)} @ epoch {self.best_epoch}"
            if self.best_epoch is not None
            else "no improving epoch"
        )
        self.log.info(
            "%s: %d epoch(s) done — final train %s%s — %s",
            self.key,
            len(self.rows),
            _fmt(last.get("train_loss")),
            f", val {_fmt(last['val_loss'])}" if "val_loss" in last else "",
            best,
        )


def _num(value):
    """A JSON-safe float — NaN/inf become None (write_artifact refuses NaN)."""
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None

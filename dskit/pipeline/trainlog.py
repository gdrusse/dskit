"""Per-epoch training evidence — the curve a run keeps (ADR-0025).

A training node that iterates (epochs, folds, timestep blocks) owes the
run more than its final number: WHICH epoch was best, whether the val
curve diverged from train, where early stopping fired. ``TrainingCurve``
is that record — an append-only list of per-epoch metric rows a trainer
fills as it goes and writes into the run's artifacts as
``trainlog.json``, beside the model it explains.

Deliberately JSON-plain: rows are ``{"epoch": int, <metric>: float}``
dicts, every value a finite number, so the artifact loads anywhere the
run dir travels and never needs this class to be read. The class exists
for the writing side — validation at append (a NaN in a curve poisons
every "best epoch" comparison downstream, so it is refused at the
instant it appears, naming the metric) and the ``best`` query early
stopping keys on.

Hot-path validation is fail-loud-on-first-problem with a plain
``ValueError`` (the ``records.py`` rule): curves are built inside
training loops, not edited by hands — the accumulate-everything
``ConfigError`` protocol is for configs.

Import cost: stdlib only.
"""

from __future__ import annotations

import math

__all__ = ["TrainingCurve"]


def _check_metrics(epoch, metrics):
    """The row's metrics as plain finite floats, or a refusal naming the
    offender — a curve must never carry a value ``best`` cannot rank."""
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(
            f"epoch {epoch}: metrics must be a non-empty dict of finite "
            f"numbers, got {metrics!r}"
        )
    row = {}
    for name, value in metrics.items():
        if not isinstance(name, str) or not name or name == "epoch":
            raise ValueError(
                f"epoch {epoch}: metric names must be non-empty strings "
                f"(and never 'epoch', which the row owns), got {name!r}"
            )
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(
                f"epoch {epoch}: metric {name!r} must be a finite number, "
                f"got {value!r} — NaN/inf cannot rank epochs and must never "
                "enter a curve"
            )
        row[name] = float(value)
    return row


class TrainingCurve:
    """An append-only per-epoch metric record.

    ``record(epoch, metrics)`` appends one row; epochs must be ints
    arriving in strictly increasing order (a curve that doubles back is
    two curves, or a bug). ``best(metric, select)`` answers the winning
    ``(epoch, value)`` — the early-stopping/best-weights question.
    ``to_obj()`` is the ``trainlog.json`` payload; ``from_obj`` restores
    one, re-validating every row (an artifact edited by hand fails the
    same way a live append would).
    """

    __slots__ = ("_rows",)

    def __init__(self):
        self._rows = []

    def __len__(self):
        return len(self._rows)

    @property
    def rows(self) -> tuple:
        """The recorded rows, oldest first (copies — the curve stays
        append-only)."""
        return tuple(dict(r) for r in self._rows)

    def record(self, epoch, metrics) -> None:
        """Append one epoch's metrics; refuses non-finite values, a
        non-int epoch, and any epoch not strictly after the last."""
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError(f"epoch must be an int >= 0, got {epoch!r}")
        if self._rows and epoch <= self._rows[-1]["epoch"]:
            raise ValueError(
                f"epoch {epoch} is not after the last recorded epoch "
                f"{self._rows[-1]['epoch']} — a curve is strictly ordered"
            )
        row = {"epoch": epoch}
        row.update(_check_metrics(epoch, metrics))
        self._rows.append(row)

    def best(self, metric, select="min"):
        """``(epoch, value)`` of the winning row for ``metric``.

        ``select`` is ``"min"``/``"max"``; ties go to the EARLIEST epoch
        (strict compare — the hpo-grid rule). Refuses an empty curve and
        a metric any recorded row lacks, by name: a best over partial
        coverage would silently rank a subset.
        """
        if select not in ("min", "max"):
            raise ValueError(f"select must be 'min' or 'max', got {select!r}")
        if not self._rows:
            raise ValueError(f"no epochs recorded — cannot rank {metric!r}")
        missing = [r["epoch"] for r in self._rows if metric not in r]
        if missing:
            raise ValueError(
                f"metric {metric!r} is missing from epoch(s) {missing} — "
                "a best over partial coverage would silently rank a subset"
            )
        winner = None
        for row in self._rows:
            value = row[metric]
            if (
                winner is None
                or (select == "min" and value < winner[1])
                or (select == "max" and value > winner[1])
            ):
                winner = (row["epoch"], value)
        return winner

    def to_obj(self) -> dict:
        """The ``trainlog.json`` payload: ``{"epochs": [rows...]}``."""
        return {"epochs": [dict(r) for r in self._rows]}

    @classmethod
    def from_obj(cls, obj) -> "TrainingCurve":
        """Restore a curve, re-validating every row (default-deny on the
        envelope: only ``epochs`` is a known key)."""
        if not isinstance(obj, dict):
            raise ValueError(f"trainlog payload must be a dict, got {obj!r}")
        unknown = sorted(set(obj) - {"epochs"})
        if unknown:
            raise ValueError(f"trainlog payload: unknown key(s) {unknown}")
        rows = obj.get("epochs", [])
        if not isinstance(rows, list):
            raise ValueError(f"trainlog epochs must be a list, got {rows!r}")
        curve = cls()
        for row in rows:
            if not isinstance(row, dict) or "epoch" not in row:
                raise ValueError(f"trainlog row must be a dict with 'epoch': {row!r}")
            metrics = {k: v for k, v in row.items() if k != "epoch"}
            curve.record(row["epoch"], metrics)
        return curve

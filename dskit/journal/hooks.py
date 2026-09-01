"""Callers pass primitives. This package never imports pipeline or onboarding.

Pipeline and onboarding function-import these after a successful (or
halted) command. Production is a context manager around ``live.main``.
Journal failure refuses the parent — unlike tracking sinks, which swallow.
"""

from __future__ import annotations

from contextlib import contextmanager

from .record import append_action

__all__ = [
    "production",
    "record_acquire",
    "record_execute",
    "record_production",
    "record_research",
]


def record_acquire(step, inputs="", outputs="", db_location="", notes=""):
    """Append an acquire-category row.

    Parameters
    ----------
    step : str
        Very short description (``alpaca backfill``, ``validate suite``).
    inputs, outputs, db_location, notes : str, optional

    Returns
    -------
    Action or None
    """
    return append_action(
        "acquire",
        step,
        inputs=inputs,
        outputs=outputs,
        db_location=db_location,
        notes=notes,
    )


def record_execute(step, inputs="", outputs="", db_location="", notes=""):
    """Append an execute-category row (one pipeline run or walk-forward).

    Parameters
    ----------
    step : str
        Usually the document ``name``.
    inputs, outputs, db_location, notes : str, optional

    Returns
    -------
    Action or None
    """
    return append_action(
        "execute",
        step,
        inputs=inputs,
        outputs=outputs,
        db_location=db_location,
        notes=notes,
    )


def record_research(step, inputs="", outputs="", db_location="", notes=""):
    """Append a research-category row.

    Parameters
    ----------
    step : str
        Title slug or short question.
    inputs, outputs, db_location, notes : str, optional

    Returns
    -------
    Action or None
    """
    return append_action(
        "research",
        step,
        inputs=inputs,
        outputs=outputs,
        db_location=db_location,
        notes=notes,
    )


def record_production(step, inputs="", outputs="", db_location="", notes=""):
    """Append a production-category row (one process, not per tick).

    Parameters
    ----------
    step : str
        ``paper loop`` or similar.
    inputs, outputs, db_location, notes : str, optional

    Returns
    -------
    Action or None
    """
    return append_action(
        "production",
        step,
        inputs=inputs,
        outputs=outputs,
        db_location=db_location,
        notes=notes,
    )


@contextmanager
def production(step, inputs="", outputs="", db_location="", notes=""):
    """Record one production process when the block ends.

    One row per process, never per tick. Failures still record, with
    the exception in ``notes``.

    Parameters
    ----------
    step : str
        Very short description.
    inputs, outputs, db_location, notes : str, optional

    Examples
    --------
    Wrap a live loop::

        from dskit.journal.hooks import production

        with production("paper loop", inputs="configs/run-train.json"):
            raise SystemExit(0)
    """
    err = None
    try:
        yield
    except BaseException as exc:
        err = exc
        raise
    finally:
        extra = notes or ""
        if err is not None and not isinstance(err, (SystemExit, KeyboardInterrupt)):
            extra = (extra + "; " if extra else "") + f"{type(err).__name__}: {err}"
        record_production(
            step,
            inputs=inputs,
            outputs=outputs,
            db_location=db_location,
            notes=extra,
        )

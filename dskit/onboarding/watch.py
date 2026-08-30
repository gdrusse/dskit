"""Recurring acquisition as repeated ordinary finite WORM pulls.

``watch`` adds timing, not another acquisition path. Every iteration delegates
to :func:`run_acquisition`, so validation, snapshots, evidence, and checkpoint
ordering stay identical to a one-shot pull. Errors propagate immediately;
there is no retry loop that could hide a data gap.
"""

from __future__ import annotations

import math
import time

from .acquire import run_acquisition
from .base import AssetError

__all__ = ["run_watch"]


def run_watch(
        root, registry, source, stream, mode, every_seconds, origin="watch",
        sleep=None, max_iterations=None, on_result=None):
    """Run finite acquisitions repeatedly at a declared interval.

    Parameters
    ----------
    root : OnboardingRoot
        Initialized onboarding root.
    registry : Registry
        Evidence registry for that root.
    source : str
        Active source-config alias.
    stream : str
        Stream acquired each iteration.
    mode : str
        ``backfill`` or ``live``.
    every_seconds : int or float
        Positive delay between completed pulls.
    origin : str
        Provenance stamp passed to each acquisition.
    sleep : callable or None
        Delay function; defaults to ``time.sleep``.
    max_iterations : int or None
        Optional finite bound, primarily for managed callers and tests.
    on_result : callable or None
        Receives each acquisition summary immediately.

    Returns
    -------
    dict
        Iteration count and final acquisition summary. An unbounded watch only
        returns if interrupted by an exception.

    Raises
    ------
    AssetError
        If timing arguments are malformed.
    """
    if (
        isinstance(every_seconds, bool)
        or not isinstance(every_seconds, (int, float))
        or not math.isfinite(every_seconds)
        or every_seconds <= 0
    ):
        raise AssetError(
            [f"every_seconds must be a positive number, got {every_seconds!r}"]
        )
    if (
        max_iterations is not None
        and (
            isinstance(max_iterations, bool)
            or not isinstance(max_iterations, int)
            or max_iterations < 1
        )
    ):
        raise AssetError(
            ["max_iterations must be None or an int >= 1, "
             f"got {max_iterations!r}"]
        )
    if on_result is not None and not callable(on_result):
        raise AssetError(["on_result must be callable or None"])
    sleep = time.sleep if sleep is None else sleep
    if not callable(sleep):
        raise AssetError(["sleep must be callable or None"])

    iterations = 0
    last = None
    while max_iterations is None or iterations < max_iterations:
        last = run_acquisition(
            root, registry, source, stream, mode, origin
        )
        iterations += 1
        if on_result is not None:
            on_result(last)
        if max_iterations is not None and iterations >= max_iterations:
            break
        sleep(every_seconds)
    return {"iterations": iterations, "last": last}

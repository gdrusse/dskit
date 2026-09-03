"""Drive the minute-quote backfill as a sequence of budgeted, resumable jobs.

One ``acquire`` is bounded by the source config's ``budget_seconds``, so a
sixteen-month, five-name pull is many jobs rather than one that loses
everything when it is interrupted. This driver runs them back to back and
stops when every symbol's cursor has reached the declared ``end``.

Usage::

    python tools/pull_quote_minutes.py --root <ob> [--jobs N] [--until SYMBOLS]

``--until`` stops early once the named symbols are complete, which is how
the decisive pair is reported before the rest of the cohort is spent on.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

SOURCE = "alpaca-sip-quotes"
STREAM = "quote_minutes"
MODE = "backfill"


def _state_path(root):
    return os.path.join(root, "state", SOURCE, f"{STREAM}-{MODE}.json")


def _cursors(root):
    try:
        with open(_state_path(root), encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return {}
    state = blob.get("state", blob)
    return state.get(STREAM, {}).get("cursors", {}) or {}


def main(argv=None):
    """Run budgeted acquisitions until the wanted symbols are complete."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--jobs", type=int, default=200)
    parser.add_argument("--until", default="")
    args = parser.parse_args(argv)

    from dskit.onboarding.acquire import find_active_source, run_acquisition
    from dskit.onboarding.connector import resolve_connector
    from dskit.onboarding.layout import OnboardingRoot

    root = OnboardingRoot(args.root)
    registry = root.registry(None)
    payload = registry.get(find_active_source(registry, SOURCE)).payload
    config = {k: v for k, v in payload["config"].items() if k != "storage"}
    connector = resolve_connector(payload["connector"])()
    knobs = connector.resolve_knobs(config)
    target = list(connector._sessions(knobs))[-1][1].isoformat().replace("+00:00", "Z")
    symbols = list(config["symbols"])
    wanted = [s for s in args.until.split(",") if s] or symbols
    print("last session closes", target, "symbols", symbols, flush=True)

    started = time.time()
    for job in range(1, args.jobs + 1):
        before = _cursors(args.root)
        t0 = time.time()
        summary = run_acquisition(root, registry, SOURCE, STREAM, MODE)
        after = _cursors(args.root)
        print(
            "job %d  %.0fs  records=%s  elapsed=%.1fmin  cursors=%s"
            % (
                job,
                time.time() - t0,
                summary.get("records"),
                (time.time() - started) / 60.0,
                json.dumps({k: after.get(k, "-")[:10] for k in symbols}),
            ),
            flush=True,
        )
        done = [s for s in wanted if after.get(s, "") == target]
        if len(done) == len(wanted):
            print("complete for", ",".join(wanted), flush=True)
            return 0
        if after == before and not summary.get("records"):
            print("no progress; stopping", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

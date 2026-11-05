"""Refuse any ticker list other than the one locked for this child (ADR-0295).

``configs/locked-ticker-list.json`` holds the list, its selection rule, its
exclusion counts and the history of how the population moved. Every new fit,
forecast and MIO document is checked here before it runs:
``python -m index_options.ticker_lock DOC [DOC ...]`` exits 0 or 1.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

__all__ = ["TickerLock", "main"]

DEFAULT_LOCK = Path(__file__).parents[1]/"configs"/"locked-ticker-list.json"
LIST_KEYS = ("tickers", "symbols", "universe", "underlyings")


class TickerLock:
    """One saved ticker list whose hash has been verified.

    Parameters
    ----------
    symbols : sequence of str
        The locked tickers, sorted and unique.
    """

    def __init__(self, symbols):
        self.symbols = tuple(symbols)

    @classmethod
    def load(cls, path=DEFAULT_LOCK):
        """Read a lock file, refusing a list that no longer matches its hash."""
        saved = json.loads(Path(path).read_text())
        wire = json.dumps(saved["symbols"], separators=(",", ":")).encode()
        if hashlib.sha256(wire).hexdigest() != saved["sha256"]:
            raise ValueError(f"{path}: the saved list does not match its hash")
        if len(saved["symbols"]) != saved["count"]:
            raise ValueError(f"{path}: the saved list does not match its count")
        return cls(saved["symbols"])

    @staticmethod
    def _lists(node):
        """Yield every ticker list in a document: a list of strings or a mapping keyed by ticker."""
        if isinstance(node, dict):
            for key, value in node.items():
                if key in LIST_KEYS and isinstance(value, (list, dict)) and value:
                    names = list(value)
                    if all(isinstance(name, str) for name in names):
                        yield key, names
                        continue
                yield from TickerLock._lists(value)
        elif isinstance(node, list):
            for value in node:
                yield from TickerLock._lists(value)

    def check(self, document, subset=False):
        """Return the number of lists checked; raise ``ValueError`` on any other list.

        ``subset`` admits a part of the locked list, for a per-batch build step only.
        """
        locked, found = set(self.symbols), 0
        for key, names in self._lists(document):
            found += 1
            names = set(names)
            if names-locked or (not subset and names != locked):
                raise ValueError(
                    f"{key}: not the locked ticker list ({len(names)} names; "
                    f"outside {sorted(names-locked)[:5]}, missing {sorted(locked-names)[:5]})")
        if not found:
            raise ValueError("the document has no ticker list to check")
        return found


def main(argv=None):
    """Check each document against the lock; return 0 if all pass, else 1."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("documents", nargs="+")
    parser.add_argument("--lock", default=str(DEFAULT_LOCK))
    parser.add_argument("--subset", action="store_true")
    args = parser.parse_args(argv)
    lock, failed = TickerLock.load(args.lock), False
    for path in args.documents:
        try:
            lock.check(json.loads(Path(path).read_text()), args.subset)
        except ValueError as error:
            print(f"{path}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())

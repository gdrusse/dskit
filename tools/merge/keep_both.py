"""Keep-both merge driver for ``docs/RE-ENTRY.md``.

Git invokes this as ``keep_both.py %O %A %B`` (ADR-0109). Every ``/wrap``
rewrites the re-entry note whole, so two parallel lanes always conflict
and hand resolution silently drops one side's true session record. The
driver keeps both — ours, a visible marker, theirs — and the next
``/wrap`` rewrites the file and prunes. No false resolution: nothing is
dropped, and the duplication is loud.
"""

from __future__ import annotations

import sys

MARKER = (
    "<!-- keep-both: the other merge side follows — "
    "prune at the next /wrap -->"
)


def _read(path):
    """Read a side's text; a missing side is empty, not an error."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def main(argv):
    """Write ours + marker + theirs to ``%A``.

    Parameters
    ----------
    argv : list of str
        ``[script, %O, %A, %B]`` — the paths git supplies.

    Returns
    -------
    int
        ``0`` unless the result cannot be written.
    """
    _base, ours_path, theirs_path = argv[1:4]
    ours = _read(ours_path)
    theirs = _read(theirs_path)
    if not theirs:
        merged = ours
    elif not ours:
        merged = theirs
    else:
        merged = (
            ours.rstrip("\n")
            + "\n\n"
            + MARKER
            + "\n\n"
            + theirs.lstrip("\n")
        )
    with open(ours_path, "w", encoding="utf-8") as fh:
        fh.write(merged if merged.endswith("\n") or not merged else merged + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

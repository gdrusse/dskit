"""Keep-both merge driver for ``docs/RE-ENTRY.md``.

Git invokes this as ``keep_both.py %O %A %B`` (ADR-0109). Every ``/wrap``
rewrites the re-entry note whole, so two parallel lanes always conflict
and hand resolution silently drops one side's true session record. The
driver keeps both — ours, a visible marker, theirs — and the next
``/wrap`` rewrites the file and prunes. No false resolution: nothing is
dropped, and the duplication is loud.
"""

from __future__ import annotations

import os
import sys

# `replace_file` is the ONE owner of "replace %A without truncating it"
# (CLAUDE.md: the second copy of a rule is the bug). Imported by path
# because these drivers are standalone scripts, not a package.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_union import replace_file  # noqa: E402

MARKER = (
    "<!-- keep-both: the other merge side follows — "
    "prune at the next /wrap -->"
)


def _read(path):
    """Read a side's text; a missing side is empty, not an error."""
    try:
        # surrogateescape, not strict: one pasted smart-quote used to raise
        # UnicodeDecodeError out of main(), leaving %A as OUR side alone with
        # no markers — a whole session record silently dropped.
        with open(path, encoding="utf-8", errors="surrogateescape") as fh:
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
        ``0`` when the merge landed, ``1`` when it could not be written.
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
    if merged and not merged.endswith("\n"):
        merged += "\n"
    try:
        # Non-truncating: a plain open(..., "w") empties the file the instant
        # it succeeds, so a failed write left RE-ENTRY.md cut mid-line with
        # one side only — and `git add` accepted it.
        replace_file(ours_path, merged.encode("utf-8", errors="surrogateescape"))
    except OSError as exc:
        print(
            f"keep-both: FAILED to write {ours_path}: {exc}\n"
            f"keep-both: *** {ours_path} may hold ONE SIDE ONLY. Do NOT "
            "`git add` it. Recover with `git checkout --merge`. ***",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

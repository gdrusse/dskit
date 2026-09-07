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

MARKER = (
    "<!-- keep-both: the other merge side follows — "
    "prune at the next /wrap -->"
)


def _read(path):
    """Read a side's text; a missing side is empty, not an error."""
    try:
        # surrogateescape, not strict: one pasted smart-quote used to raise
        # UnicodeDecodeError out of main(), leaving %A as OUR side alone with
        # no markers — a whole session record silently dropped. newline="":
        # without it every CR in a CRLF-committed file is rewritten to LF, so
        # a driver asked only to concatenate returns a whole-file diff.
        with open(path, encoding="utf-8", errors="surrogateescape",
                  newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


def _append_theirs(ours_path, theirs):
    """Last resort: append the marker and THEIR side, so both survive.

    Appending costs a few bytes where a full replace failed, and this
    driver already holds ``theirs`` in memory. Doing nothing would leave
    ``%A`` as our side alone — a clean-looking single session record,
    which is the exact loss this driver exists to prevent, and which the
    operator note's "look for the CONFLICT COULD NOT BE WRITTEN line"
    would never catch here.

    Parameters
    ----------
    ours_path : str
        The file to append to.
    theirs : str
        Their side's text.

    Returns
    -------
    bool
        Whether the append landed.
    """
    if not theirs:
        return False
    try:
        with open(ours_path, "ab") as fh:
            fh.write(
                b"\n\n"
                + MARKER.encode()
                + b"\n\n"
                + theirs.encode("utf-8", errors="surrogateescape")
            )
        return True
    except OSError:
        return False


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
    theirs = ""
    try:
        return _merge(ours_path, theirs_path)
    except Exception as exc:  # noqa: BLE001 - a side must never vanish
        # ANY failure, the deferred import included. Before this guard a
        # broken sibling module was fatal here and left %A as our side
        # alone, with no marker — a new loss introduced by the fix that
        # shared replace_file.
        print(f"keep-both: FAILED to merge {ours_path}: {exc}", file=sys.stderr)
        theirs = _read_quietly(theirs_path)
        if _append_theirs(ours_path, theirs):
            print(
                "keep-both: appended THEIR side after the marker — both sides "
                "are present, prune at the next /wrap",
                file=sys.stderr,
            )
        else:
            print(
                f"keep-both: *** {ours_path} may hold ONE SIDE ONLY. Do NOT "
                "`git add` it. Recover with `git checkout --merge`. ***",
                file=sys.stderr,
            )
        return 1


def _read_quietly(path):
    """Read a side, or empty when even that fails."""
    try:
        return _read(path)
    except Exception:  # noqa: BLE001 - the fallback must not raise
        return ""


def _merge(ours_path, theirs_path):
    """Build and store ours + marker + theirs; return the exit code."""
    # Deferred, not module-level: `replace_file` is the ONE owner of
    # "replace %A without truncating it" (CLAUDE.md — the second copy of a
    # rule is the bug), but importing it at module scope made a broken
    # sibling fatal. In here, main()'s guard catches it.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from journal_union import replace_file

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
    # Non-truncating: a plain open(..., "w") empties the file the instant it
    # succeeds, so a failed write left RE-ENTRY.md cut mid-line with one side
    # only — and `git add` accepted it. A failure raises to main()'s guard,
    # which appends their side rather than losing it.
    replace_file(ours_path, merged.encode("utf-8", errors="surrogateescape"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Keep every ``CLAUDE.md`` / ``AGENTS.md`` pair identical.

The owner rule is "keep AGENTS.md and CLAUDE.md aligned": Claude Code reads
the first name, Codex and the other OpenAI models, Cursor and OpenCode read
the second. Each pair is one document under two names. This module owns the
two rules that make that true, and ``tests/docs/test_budgets.py`` imports
them rather than restating them:

- ``mirrored``: the target text is the source text with the file name swapped
  on the title line and on any ``this file`` tree line, nowhere else, so prose
  that names both files stays correct in both copies;
- ``normalized``: the comparison form, with BOTH names replaced by one
  placeholder, so the pair test accepts either spelling of a prose mention.

Run ``python tools/docs/mirror_pairs.py`` after editing any ``CLAUDE.md`` (or
``--source AGENTS.md`` after editing an ``AGENTS.md``); ``--check`` only reports.
"""

from __future__ import annotations

import argparse
import os
import sys

NAMES = ("CLAUDE.md", "AGENTS.md")
PLACEHOLDER = "<AGENT-INSTRUCTIONS>"
SKIP_DIRS = {".git", "pipeline_runs", "__pycache__", "node_modules", "third_party"}


def mirrored(text, source, target):
    """Return ``text`` as the other copy of the pair.

    Parameters
    ----------
    text : str
        The source copy's full text.
    source : str
        The source file name, ``"CLAUDE.md"`` or ``"AGENTS.md"``.
    target : str
        The other name.

    Returns
    -------
    str
        The target copy: the name swapped on title lines (``# `` prefix) and on
        lines carrying ``this file``; every other line verbatim.
    """
    out = []
    for line in text.split("\n"):
        if line.startswith("# ") or "this file" in line:
            line = line.replace(source, target)
        out.append(line)
    return "\n".join(out)


def normalized(text):
    """Return ``text`` with both pair names replaced by one placeholder."""
    for name in NAMES:
        text = text.replace(name, PLACEHOLDER)
    return text


def pair_dirs(root):
    """Yield every directory under ``root`` holding either name, sorted."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        if any(name in filenames for name in NAMES):
            found.append(dirpath)
    return sorted(found)


def main(argv=None):
    """Mirror or check every pair; return the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", choices=NAMES, default="CLAUDE.md",
                        help="the copy that was edited (default CLAUDE.md)")
    parser.add_argument("--check", action="store_true",
                        help="report pairs that differ; write nothing")
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    args = parser.parse_args(argv)
    target = NAMES[1] if args.source == NAMES[0] else NAMES[0]
    differing = []
    for d in pair_dirs(args.root):
        src_path, dst_path = os.path.join(d, args.source), os.path.join(d, target)
        if not os.path.isfile(src_path):
            differing.append(f"{d}: no {args.source} beside {target}")
            continue
        with open(src_path, encoding="utf-8") as fh:
            want = mirrored(fh.read(), args.source, target)
        have = None
        if os.path.isfile(dst_path):
            with open(dst_path, encoding="utf-8") as fh:
                have = fh.read()
        if have is not None and normalized(have) == normalized(want):
            continue
        differing.append(os.path.relpath(dst_path, args.root))
        if not args.check:
            with open(dst_path, "w", encoding="utf-8") as fh:
                fh.write(want)
    verb = "differs" if args.check else "rewritten"
    for item in differing:
        print(f"{verb}: {item}")
    if not differing:
        print("every pair is aligned")
    return 1 if (args.check and differing) else 0


if __name__ == "__main__":
    sys.exit(main())

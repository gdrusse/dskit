"""``python -m dskit.journal`` — init, record, research, promote, render, exec.

* ``init [--root DIR]`` — marker + empty CSVs + generated README.
* ``record --category … --step …`` — one action row (hooks usually do this).
* ``research TITLE`` — write ``docs/research/<slug>.md`` and a row.
* ``promote ID --criteria empirical|judgemental|n/a`` — owner path row.
* ``render`` — rewrite README from CSV.
* ``exec --category … --step … -- CMD…`` — run a command, then record it.

Exit codes: **0** ok · **1** error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .base import CATEGORIES, CRITERIA, JournalError
from .locate import find_journal, init_journal
from .record import append_action, promote
from .render import render
from .research import write_research


def _root_arg(args):
    return os.path.abspath(args.root) if getattr(args, "root", None) else os.getcwd()


def cmd_init(args):
    """Create a journal in ``--root`` (default cwd).

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    root = init_journal(_root_arg(args))
    print(root.child_root)
    return 0


def cmd_record(args):
    """Append one action row.

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    action = append_action(
        args.category,
        args.step,
        inputs=args.inputs or "",
        outputs=args.outputs or "",
        db_location=args.db_location or "",
        notes=args.notes or "",
        start=_root_arg(args),
    )
    if action is None:
        raise JournalError(["no journal here and this is not a child — `init` first"])
    print(action.id)
    return 0


def cmd_research(args):
    """Write a research markdown file and a row.

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    body = None
    if args.body_file:
        try:
            with open(args.body_file, encoding="utf-8") as fh:
                body = fh.read()
        except OSError as exc:
            raise JournalError([f"cannot read --body-file: {exc}"]) from exc
    path = write_research(args.title, body=body, start=_root_arg(args))
    print(path)
    return 0


def cmd_promote(args):
    """Owner-only: put an action on the path to production.

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    row = promote(
        args.id,
        args.criteria,
        label=args.label,
        purpose=args.purpose,
        relevant_files=args.relevant_files,
        locked=args.locked,
        current_work=args.current_work,
        start=_root_arg(args),
    )
    print(row.id, row.criteria)
    return 0


def cmd_render(args):
    """Rewrite the generated README from CSV.

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    root = find_journal(start=_root_arg(args))
    if root is None:
        raise JournalError(["no journal here — `init` first"])
    render(root)
    print(root.readme)
    return 0


def cmd_exec(args):
    """Run ``CMD`` then record it.

    Parameters
    ----------
    args : argparse.Namespace

    Returns
    -------
    int
    """
    if not args.cmd:
        raise JournalError(["exec needs a command after `--`"])
    done = subprocess.run(args.cmd, cwd=_root_arg(args))
    extra = args.notes or ""
    extra = (extra + "; " if extra else "") + f"exit {done.returncode}"
    action = append_action(
        args.category,
        args.step,
        inputs=" ".join(args.cmd),
        outputs=args.outputs or "",
        db_location=args.db_location or "",
        notes=extra,
        start=_root_arg(args),
    )
    if action is not None:
        print(action.id)
    return done.returncode


def main(argv=None):
    """CLI entry.

    Parameters
    ----------
    argv : list of str or None

    Returns
    -------
    int
    """
    top = argparse.ArgumentParser(
        prog="python -m dskit.journal",
        description=__doc__.splitlines()[0],
    )
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create journal.json + empty CSVs")
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("record", help="append one action row")
    p.add_argument("--category", required=True, choices=CATEGORIES)
    p.add_argument("--step", required=True)
    p.add_argument("--inputs", default="")
    p.add_argument("--outputs", default="")
    p.add_argument("--db-location", dest="db_location", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("research", help="write docs/research/<slug>.md + a row")
    p.add_argument("title")
    p.add_argument("--root", default=".")
    p.add_argument(
        "--body-file",
        default="",
        help="markdown to write; omitted = a Question/Finding/Sources stub",
    )
    p.set_defaults(fn=cmd_research)

    p = sub.add_parser("promote", help="owner: put an action on the path")
    p.add_argument("id")
    p.add_argument("--criteria", required=True, choices=CRITERIA)
    p.add_argument("--label", required=True, help="short decision description")
    p.add_argument("--purpose", required=True)
    p.add_argument("--relevant-files", required=True)
    p.add_argument("--locked", required=True, choices=("Y", "N"))
    p.add_argument(
        "--current-work",
        default="",
        help="owner-only description of active work; agents must never update it",
    )
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_promote)

    p = sub.add_parser("render", help="rewrite README.md from CSV")
    p.add_argument("--root", default=".")
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("exec", help="run a command, then record it")
    p.add_argument("--category", required=True, choices=CATEGORIES)
    p.add_argument("--step", required=True)
    p.add_argument("--outputs", default="")
    p.add_argument("--db-location", dest="db_location", default="")
    p.add_argument("--notes", default="")
    p.add_argument("--root", default=".")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_exec)

    args = top.parse_args(argv)
    cmd = args.cmd if args.command == "exec" else None
    if args.command == "exec" and cmd and cmd[:1] == ["--"]:
        args.cmd = cmd[1:]
    try:
        return args.fn(args)
    except JournalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Sweep dskit for near-duplicates before something new lands.

Owner rule: redoing work that already exists somewhere in dskit is the
repeat failure. Anything a change ADDS -- a file, a public top-level
class or function, a registered kind -- is compared against every place
it could already live: HEAD, ``origin/main``, every local and
remote-tracking branch, and the uncommitted files of every other
worktree. Near matches are printed; the author decides.

Three modes:

- ``sweep TERM...`` -- proactive search before building anything.
- ``sweep --staged`` -- the same report for the staged diff.
- ``sweep --commit-msg FILE`` -- the commit-msg hook. When the staged
  diff adds something new, the message must carry a ``Sweep:`` trailer
  naming what was searched and why the new thing is not a duplicate.
  Pure edits, docs, tests, data, merges, reverts, cherry-picks and
  rebases pass untouched. Emergency escape: ``SWEEP_SKIP="<reason>"``,
  which is logged to the shared git dir AND stamped into the message as
  a ``Sweep-Skipped:`` trailer, so a skip is never silent.

Stdlib only, so the hook runs under any python3. Tunables (exempt paths,
generic names, thresholds) live in ``sweep.json`` beside this file.
"""

import argparse
import concurrent.futures
import datetime
import difflib
import fnmatch
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_SYMBOL = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_]\w*)\s*[(:]")
# PCRE is ~20x faster than ERE over many trees; ERE is the fallback for
# a git built without it.
_SYMBOL_GREP = {"-P": r"^(class|def|async def) [A-Za-z_]\w*\s*[(:]",
                "-E": r"^(class|def|async def) [A-Za-z_]"}
# A registered kind: register_*_kind("x", ...), a kind table row
# ("x-y": Cls / ("x-y", Cls)), or a class-level ``kind = "x"``.
_KINDS = (
    re.compile(r"""register_\w*kind\(\s*["']([\w.\-]+)["']"""),
    re.compile(r"""^\s*\(?\s*["']([a-z][\w.]*-[\w.\-]+)["']\s*[:,]\s*\(?\s*[A-Z]\w*"""),
    re.compile(r"""^\s+kind\s*=\s*["']([\w.\-]+)["']\s*$"""),
)
_KIND_GREP = {
    "-P": r"register_\w*kind\(|^\s*\(?\s*[\"'][a-z][\w.]*-[\w.-]+[\"']\s*[:,]"
          r"|^\s+kind\s*=\s*[\"']",
    "-E": r"register_[a-z_]*kind\(|^[[:space:]]*\(?[[:space:]]*[\"'][a-z]"
          r"[A-Za-z0-9_.]*-[A-Za-z0-9_.-]+[\"'][[:space:]]*[:,]"
          r"|^[[:space:]]+kind[[:space:]]*=[[:space:]]*[\"']",
}
# Everything but these is scrubbed before git runs in ANOTHER worktree:
# a hook inherits GIT_INDEX_FILE/GIT_DIR, which would point it at ours.
_GIT_ENV_KEEP = ("GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")


def _load_config(path=None):
    """Read ``sweep.json`` (tunables); ``notes`` is documentation only."""
    with open(path or os.path.join(HERE, "sweep.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _git(root, *args, env=None, timeout=None, check=True):
    """Run git in ``root``; return stdout text."""
    proc = subprocess.run(
        ["git", "-C", root, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _clean_env():
    """os.environ minus the per-repo GIT_* variables a hook inherits."""
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") or k in _GIT_ENV_KEEP
    }


def _tokens(name):
    """Split CamelCase / snake / kebab / path-ish names into lower tokens."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def _stem(path):
    """Basename with every extension removed."""
    return os.path.basename(path).split(".")[0]


def _kinds_in(line):
    """Kind names a source line registers."""
    return [m.group(1) for rx in _KINDS for m in [rx.search(line)] if m]


class RepoInventory:
    """Every path, top-level symbol and registered kind in reach.

    Parameters
    ----------
    root : str
        The worktree to sweep from; its own uncommitted files are excluded
        (they ARE the change under review).
    config : dict
        ``sweep.json`` contents; ``worktree_timeout_s`` bounds each
        other-worktree scan so a slow mount cannot stall a commit.

    Examples
    --------
    Build it once and look a name up::

        inv = RepoInventory("/home/me/wt/x", _load_config())
        inv.symbols["SessionFeatureCache"]
        # -> {"children/.../feature_cache.py": (40, {"origin/main", ...})}
    """

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.paths = {}  # path -> set(labels)
        self.symbols = {}  # name -> {path: (line, set(labels))}
        self.kinds = {}  # name -> {path: (line, set(labels))}
        self.sources = []
        trees = self._trees()
        self._scan_trees(trees)
        self._scan_worktrees()

    # -- committed state: HEAD, origin/main, every branch ---------------
    def _trees(self):
        """Map unique tree id -> labels of every ref that points at it."""
        refs = ["HEAD"]
        out = _git(
            self.root, "for-each-ref", "--format=%(refname:short)",
            "refs/heads", "refs/remotes",
        )
        refs += [r for r in out.split() if not r.endswith("/HEAD")]
        trees = {}
        for ref in refs:
            proc = subprocess.run(
                ["git", "-C", self.root, "rev-parse", "-q", "--verify",
                 ref + "^{tree}"],
                capture_output=True, text=True,
            )
            if proc.returncode == 0:
                trees.setdefault(proc.stdout.strip(), set()).add(ref)
        self.sources.append(f"{len(refs)} refs")
        return trees

    def _add(self, table, name, path, line, labels):
        """Record one definition site."""
        site = table.setdefault(name, {})
        if path in site:
            site[path][1].update(labels)
        else:
            site[path] = (line, set(labels))

    def _scan_trees(self, trees):
        """Paths, symbols and kinds of every tree, in three git calls/tree."""
        if not trees:
            return
        for tree, labels in trees.items():
            for path in _git(self.root, "ls-tree", "-r", "--name-only",
                             tree).splitlines():
                self.paths.setdefault(path, set()).update(labels)
        ids = list(trees)
        self._grep(ids, trees, _SYMBOL_GREP, self._on_symbol)
        self._grep(ids, trees, _KIND_GREP, self._on_kind)

    def _grep(self, ids, trees, pattern, sink):
        """Grep the .py files of every tree; feed matches to ``sink``."""
        for flavour in ("-P", "-E"):
            proc = subprocess.run(
                ["git", "-C", self.root, "grep", "-n", "-I", flavour, "-e",
                 pattern[flavour], *ids, "--", "*.py"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace",
            )
            if proc.returncode in (0, 1):  # 1 = no match, not an error
                break
        out = proc.stdout
        for raw in out.splitlines():
            parts = raw.split(":", 3)
            if len(parts) == 4 and parts[0] in trees:
                sink(parts[3], parts[1], int(parts[2]), trees[parts[0]])

    def _on_symbol(self, text, path, line, labels):
        m = _SYMBOL.match(text)
        if m:
            self._add(self.symbols, m.group(1), path, line, labels)

    def _on_kind(self, text, path, line, labels):
        for kind in _kinds_in(text):
            self._add(self.kinds, kind, path, line, labels)

    # -- uncommitted state of every OTHER worktree ----------------------
    def _worktrees(self):
        """Paths of every other live worktree."""
        out = _git(self.root, "worktree", "list", "--porcelain")
        here = os.path.realpath(self.root)
        found = []
        for line in out.splitlines():
            if line.startswith("worktree "):
                path = line[len("worktree "):]
                if os.path.realpath(path) != here and os.path.isdir(path):
                    found.append(path)
        return found

    def _dirty(self, wt):
        """(worktree, [untracked-or-modified paths]); [] on timeout."""
        try:
            out = _git(wt, "ls-files", "-o", "-m", "--exclude-standard",
                       env=_clean_env(),
                       timeout=self.config["worktree_timeout_s"], check=False)
        except subprocess.TimeoutExpired:
            return wt, None
        return wt, sorted(set(out.splitlines()))

    def _scan_worktrees(self):
        """Fold every other worktree's dirty files into the inventory."""
        wts = self._worktrees()
        skipped = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for wt, files in pool.map(self._dirty, wts):
                if files is None:
                    skipped += 1
                    continue
                label = "wt:" + os.path.basename(wt.rstrip("/"))
                for path in files:
                    self.paths.setdefault(path, set()).add(label)
                    if path.endswith(".py"):
                        self._scan_file(os.path.join(wt, path), path, label)
        note = f"{len(wts)} other worktrees"
        if skipped:
            note += f" ({skipped} timed out, not searched)"
        self.sources.append(note)

    def _scan_file(self, full, path, label):
        """Symbols and kinds of one uncommitted .py file."""
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                for n, text in enumerate(fh, 1):
                    self._on_symbol(text, path, n, {label})
                    self._on_kind(text, path, n, {label})
        except OSError:
            pass


class Sweep:
    """Score new names against an :class:`RepoInventory` and report.

    Parameters
    ----------
    inventory : RepoInventory
        What already exists.
    config : dict
        ``similarity`` (0-1 string ratio), ``token_overlap`` (0-1 Jaccard),
        ``max_hits_per_item``, ``generic_basenames``, ``generic_symbols``.

    Examples
    --------
    Report every place a term could already live::

        sweep = Sweep(RepoInventory(root, cfg), cfg)
        print(sweep.report([{"type": "term", "name": "ReplayDriver"}]))
    """

    def __init__(self, inventory, config):
        self.inv = inventory
        self.config = config
        self._generic_stems = {s.lower() for s in config["generic_basenames"]}
        self._generic_syms = {s.lower() for s in config["generic_symbols"]}

    def score(self, a, b):
        """Similarity of two names in [0, 1].

        Parameters
        ----------
        a, b : str
            Names in any case style.

        Returns
        -------
        float
            1.0 when equal once case and separators are dropped; 0.9 when
            one multi-token name contains the other; else the larger of the
            string ratio and the token Jaccard (Jaccard counts only with at
            least two shared tokens), or 0.0 below the thresholds.
        """
        ta, tb = _tokens(a), _tokens(b)
        na, nb = "".join(ta), "".join(tb)
        if not na or not nb:
            return 0.0
        if na == nb:
            return 1.0
        short, long_ = sorted((na, nb), key=len)
        if min(len(ta), len(tb)) >= 2 and len(short) >= 6 and short in long_:
            return 0.9
        best = 0.0
        sa, sb = set(ta), set(tb)
        shared = len(sa & sb)
        if shared >= 2:
            jac = shared / len(sa | sb)
            if jac >= self.config["token_overlap"]:
                best = jac
        cut = self.config["similarity"]
        sm = difflib.SequenceMatcher(None, na, nb)
        if sm.real_quick_ratio() >= cut and sm.quick_ratio() >= cut:
            r = sm.ratio()
            if r >= cut:
                best = max(best, r)
        return best

    def _name_hits(self, name, table):
        """[(score, name, path, line, labels)] for one name in a table."""
        hits = []
        for other, sites in table.items():
            s = self.score(name, other)
            if s <= 0:
                continue
            for path, (line, labels) in sites.items():
                hits.append((s, other, path, line, labels))
        return hits

    def _file_hits(self, path):
        """Near-named files, plus the same path anywhere else."""
        hits = []
        stem = _stem(path)
        generic = stem.lower() in self._generic_stems or len(stem) < 3
        for other, labels in self.inv.paths.items():
            if other == path:
                hits.append((1.01, other, other, 0, labels))
                continue
            if generic:
                continue
            ostem = _stem(other)
            if ostem.lower() in self._generic_stems:
                continue
            s = self.score(stem, ostem)
            if s > 0:
                hits.append((s, other, other, 0, labels))
        return hits

    def hits(self, item):
        """Ranked matches for one item (``type`` file/class/def/kind/term)."""
        kind, name = item["type"], item["name"]
        if kind == "file":
            found = self._file_hits(name)
        elif kind == "kind":
            found = self._name_hits(name, self.inv.kinds)
            found += self._name_hits(name, self.inv.symbols)
        elif kind == "term":
            found = self._name_hits(name, self.inv.symbols)
            found += self._name_hits(name, self.inv.kinds)
            found += self._file_hits(name)
        else:
            found = self._name_hits(name, self.inv.symbols)
            found += self._name_hits(name, self.inv.kinds)
        found.sort(key=lambda h: (-h[0], "origin/main" not in h[4], h[2]))
        return found[: self.config["max_hits_per_item"]]

    @staticmethod
    def _labels(labels):
        """origin/main first, then up to two more, then a count."""
        ordered = sorted(labels, key=lambda x: (x != "origin/main",
                                                x != "HEAD", x))
        shown = ", ".join(ordered[:3])
        return shown + (f" (+{len(ordered) - 3})" if len(ordered) > 3 else "")

    def report(self, items):
        """Human-readable report; also returns the total hit count.

        Parameters
        ----------
        items : list of dict
            ``{"type", "name", "path"?}`` from :func:`staged_items` or terms.

        Returns
        -------
        tuple of (str, int)
            The report text and the number of near matches found.
        """
        lines = ["sweep: searched " + ", ".join(self.inv.sources)]
        total = 0
        cap = self.config["max_items_reported"]
        for item in items[:cap]:
            where = f"  ({item['path']})" if item.get("path") else ""
            lines.append(f"  [{item['type']}] {item['name']}{where}")
            found = self.hits(item)
            total += len(found)
            if not found:
                lines.append("      no near matches")
            for s, other, path, line, labels in found:
                mark = "=" if s >= 1.0 else "~"
                loc = f"{path}:{line}" if line else path
                shown = "" if other == path else f"{other}  "
                lines.append(f"      {mark} {shown}{loc}  @ {self._labels(labels)}")
        if len(items) > cap:
            lines.append(f"  ... {len(items) - cap} more item(s) not shown")
        return "\n".join(lines), total


# -- the staged diff ---------------------------------------------------


def _exempt(path, config):
    """Tell whether a path is docs, tests or data (never needs a sweep)."""
    return any(fnmatch.fnmatch(path, g) for g in config["exempt_globs"])


def _base(root):
    """HEAD, or the empty tree before the first commit."""
    proc = subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify",
                           "HEAD"], capture_output=True, text=True)
    return "HEAD" if proc.returncode == 0 else EMPTY_TREE


def _head_names(root, base, path):
    """Symbol and kind names the committed version of ``path`` has."""
    if base == EMPTY_TREE:
        return set()
    proc = subprocess.run(["git", "-C", root, "show", f"{base}:{path}"],
                          capture_output=True, text=True, errors="replace")
    names = set()
    for text in proc.stdout.splitlines():
        m = _SYMBOL.match(text)
        if m:
            names.add(m.group(1))
        names.update(_kinds_in(text))
    return names


def staged_items(root, config):
    """List the new files, public top-level symbols and kinds the index adds.

    Parameters
    ----------
    root : str
        Worktree root (git's ``GIT_INDEX_FILE`` is honoured, so ``commit -a``
        and ``commit <paths>`` see the index git will actually commit).
    config : dict
        ``exempt_globs`` and ``generic_symbols`` from ``sweep.json``.

    Returns
    -------
    list of dict
        ``{"type": "file"|"class"|"def"|"kind", "name", "path"}``. A name
        that the diff also removes (a move) or that the committed file
        already had (a signature edit) is not new.
    """
    base = _base(root)
    added = _git(root, "diff", "--cached", "--name-only", "-M",
                 "--diff-filter=A", base).splitlines()
    items = [{"type": "file", "name": p} for p in added
             if not _exempt(p, config)]
    patch = _git(root, "diff", "--cached", "-U0", "-M", "--no-color",
                 "--diff-filter=AMR", base, "--", "*.py")
    plus, minus, path = [], set(), None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("--- ") or path is None or _exempt(path, config):
            continue
        if line.startswith("-"):
            m = _SYMBOL.match(line[1:])
            if m:
                minus.add(m.group(1))
            minus.update(_kinds_in(line[1:]))
        elif line.startswith("+"):
            text = line[1:]
            m = _SYMBOL.match(text)
            if m and not m.group(1).startswith("_"):
                kind = "class" if text.startswith("class") else "def"
                plus.append({"type": kind, "name": m.group(1), "path": path})
            for k in _kinds_in(text):
                plus.append({"type": "kind", "name": k, "path": path})
    generic = {s.lower() for s in config["generic_symbols"]}
    committed = {}
    for item in plus:
        if item["name"] in minus or item["name"].lower() in generic:
            continue
        if item["path"] not in committed:
            committed[item["path"]] = _head_names(root, base, item["path"])
        if item["name"] not in committed[item["path"]]:
            items.append(item)
    return items


# -- the commit-msg hook -----------------------------------------------


def _in_progress(root):
    """Name of an in-progress merge/revert/pick/rebase, or None."""
    for name in ("MERGE_HEAD", "REVERT_HEAD", "CHERRY_PICK_HEAD",
                 "rebase-merge", "rebase-apply"):
        rel = _git(root, "rev-parse", "--git-path", name).strip()
        if os.path.exists(rel if os.path.isabs(rel) else os.path.join(root, rel)):
            return name
    return None


def _message_lines(text):
    """Message lines as git will keep them (comments and scissors dropped)."""
    kept = []
    for line in text.splitlines():
        if line.startswith("# ------------------------ >8"):
            break
        if not line.startswith("#"):
            kept.append(line)
    return kept


def trailer_ok(text, config):
    """Check the message carries a real ``Sweep:`` acknowledgment.

    Parameters
    ----------
    text : str
        The commit message file contents.
    config : dict
        ``trailer`` (key) and ``min_trailer_words``.

    Returns
    -------
    bool
        A ``Sweep:`` line whose value has at least ``min_trailer_words``
        words -- enough to name what was searched and why it is new.
    """
    key = config["trailer"].lower() + ":"
    for line in _message_lines(text):
        if line.lower().startswith(key):
            value = line[len(key):].strip()
            if len(value.split()) >= config["min_trailer_words"]:
                return True
    return False


def _skip(root, msg_path, text, reason, items, config):
    """Honour SWEEP_SKIP: log it locally and stamp it into the message."""
    common = _git(root, "rev-parse", "--git-common-dir").strip()
    if not os.path.isabs(common):
        common = os.path.join(root, common)
    who = _git(root, "var", "GIT_AUTHOR_IDENT", check=False).strip()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    names = ",".join(i["name"] for i in items[:20])
    with open(os.path.join(common, config["skip_log"]), "a",
              encoding="utf-8") as fh:
        fh.write(f"{stamp}\t{branch}\t{who}\t{reason}\t{names}\n")
    body = text.rstrip("\n")
    with open(msg_path, "w", encoding="utf-8") as fh:
        fh.write(f"{body}\n\nSweep-Skipped: {reason}\n")


def commit_msg(root, msg_path, config):
    """Run the hook: 0 lets the commit through, 1 refuses it.

    Parameters
    ----------
    root : str
        Worktree root.
    msg_path : str
        The message file git passes to commit-msg.
    config : dict
        ``sweep.json``.

    Returns
    -------
    int
        Exit status.
    """
    with open(msg_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    first = next((ln for ln in _message_lines(text) if ln.strip()), "")
    if _in_progress(root) or re.match(r'(Merge |Revert ")', first):
        return 0
    items = staged_items(root, config)
    if not items:
        return 0
    report, _ = Sweep(RepoInventory(root, config), config).report(items)
    print(report, file=sys.stderr)
    if trailer_ok(text, config):
        print("sweep: acknowledged by the Sweep: trailer.", file=sys.stderr)
        return 0
    reason = os.environ.get(config["skip_env"], "").strip()
    if reason:
        _skip(root, msg_path, text, reason, items, config)
        print(f"sweep: SKIPPED ({reason}) -- logged and stamped as "
              "Sweep-Skipped.", file=sys.stderr)
        return 0
    print(
        "\nsweep: REFUSED. This commit adds new files/symbols/kinds.\n"
        "Check the matches above (and run tools/sweep/sweep <term> for\n"
        "anything else it might duplicate), then add a trailer such as:\n\n"
        "  Sweep: searched <terms> on origin/main, branches, worktrees;\n"
        "         <why the new thing is not a duplicate>\n\n"
        f"(one line, >= {config['min_trailer_words']} words). Emergency only: "
        f"{config['skip_env']}=\"<reason>\" git commit ... (logged).",
        file=sys.stderr,
    )
    return 1


def _mentions(root, term, config):
    """Files on origin/main (else HEAD) whose TEXT mentions ``term``."""
    ref = "origin/main"
    if subprocess.run(["git", "-C", root, "rev-parse", "-q", "--verify", ref],
                      capture_output=True).returncode != 0:
        ref = "HEAD"
    out = _git(root, "grep", "-l", "-I", "-i", "-F", "-e", term, ref,
               check=False).splitlines()
    cap = config["max_hits_per_item"] * 2
    lines = [f"  text mentions of {term!r} on {ref}: {len(out)} file(s)"]
    lines += ["      " + p.split(":", 1)[1] for p in out[:cap]]
    if len(out) > cap:
        lines.append(f"      ... {len(out) - cap} more "
                     f"(git grep -il {term!r} {ref})")
    return "\n".join(lines)


def main(argv=None):
    """CLI entry; see the module docstring for the three modes.

    Parameters
    ----------
    argv : list of str, optional
        Arguments without the program name.

    Returns
    -------
    int
        Exit status.
    """
    ap = argparse.ArgumentParser(prog="sweep", description=__doc__.split(
        "\n\n")[0])
    ap.add_argument("terms", nargs="*", help="names to search for")
    ap.add_argument("--staged", action="store_true",
                    help="report on the staged diff")
    ap.add_argument("--commit-msg", metavar="FILE",
                    help="commit-msg hook mode")
    ap.add_argument("--config", help="alternate sweep.json")
    args = ap.parse_args(argv)
    config = _load_config(args.config)
    root = _git(os.getcwd(), "rev-parse", "--show-toplevel").strip()
    if args.commit_msg:
        return commit_msg(root, args.commit_msg, config)
    if args.staged:
        items = staged_items(root, config)
        if not items:
            print("sweep: the staged diff adds nothing that needs a sweep.")
            return 0
    elif args.terms:
        items = [{"type": "term", "name": t} for t in args.terms]
    else:
        ap.print_help()
        return 1
    report, _ = Sweep(RepoInventory(root, config), config).report(items)
    print(report)
    for term in args.terms:
        print(_mentions(root, term, config))
    return 0


if __name__ == "__main__":
    sys.exit(main())

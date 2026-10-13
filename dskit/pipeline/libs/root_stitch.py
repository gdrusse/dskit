"""A config-driven rename map that joins an old root's series to its new root's (ADR-0265 Amendment 3).

A listed name changes (a ticker is retired and its successor takes the same business): the vendor files the
history under the old root up to a date and under the new root from a later one. A test that wants ONE series per
business reads both. :class:`RootStitch` is that map and nothing else: ``[{"old", "new", "effective"}]``, where
``effective`` is the first date the NEW name is in use. It names no company; the map is a JSON file a project owns
(read with :meth:`RootStitch.from_file`, pinned by the sha256 of its bytes like every other file a document reads).

**Reuse breaks.** The opposite problem: one root that is TWO companies (a delisted issuer's ticker taken later by
another). A ``breaks`` entry ``{"root", "effective", "earlier_as"}`` ends the root's series before ``effective`` and
starts a new one at it: the rows before ``effective`` are filed under the name ``earlier_as`` (a series of their
own, which a test may drop by name) and the root's own name keeps the later company. The dates come from a census
candidate read by a person (:class:`~dskit.pipeline.libs.chain_reuse.ReuseDetector`), never from memory.

**What it refuses.** An old name that is renamed twice, a chain (a name that is both an old and a new one: say
which root the series is under), an old equal to its new, a bad date. Against data, :meth:`check_windows` takes the
first and last date each raw root appears in and refuses an old root that runs to or past ``effective``, a new root
that starts before it, and any overlap between the two ranges: a hand-over never double counts a date and the
effective date in the file is proven by the data, not trusted.

Import cost: stdlib and the pipeline core.
"""

import hashlib
import json
from datetime import date

from dskit.pipeline.base import value_hash

__all__ = ["RootStitch"]

_KEYS = ("old", "new", "effective")
_BREAK_KEYS = ("root", "effective", "earlier_as")


class RootStitch:
    """The old-root -> new-root map, validated once.

    Parameters
    ----------
    entries : list of dict or None
        ``{"old": name, "new": name, "effective": "YYYY-MM-DD"}``. ``None`` or an empty list is the identity.
    breaks : list of dict or None
        ``{"root": name, "effective": "YYYY-MM-DD", "earlier_as": name}``: see the module docstring.

    Raises
    ------
    ValueError
        One message naming every problem (see the module docstring).

    Examples
    --------
    A hand-over on a known date::

        stitch = RootStitch([{"old": "OLDCO", "new": "NEWCO", "effective": "2022-06-09"}])
        stitch.name("OLDCO")        # "NEWCO"
        stitch.raws("NEWCO")        # ["OLDCO", "NEWCO"]
    """

    def __init__(self, entries=None, breaks=None, pending=None):
        entries = [] if entries is None else entries
        breaks = [] if breaks is None else breaks
        problems = self.problems(entries) + self.break_problems(breaks, entries)
        if problems:
            raise ValueError("; ".join(problems))
        self._rename = {e["old"]: e["new"] for e in entries}
        self._effective = {e["old"]: e["effective"] for e in entries}
        self._olds = {}
        for old, new in self._rename.items():
            self._olds.setdefault(new, []).append(old)
        self._entries = sorted(({k: e[k] for k in _KEYS} for e in entries), key=lambda e: e["old"])
        self._breaks = sorted(({k: b[k] for k in _BREAK_KEYS} for b in breaks), key=lambda b: b["root"])
        self._break = {b["root"]: b for b in self._breaks}
        self._earlier = {b["earlier_as"]: b["root"] for b in self._breaks}
        self._pending = [dict(x) for x in (pending or []) if isinstance(x, dict)]

    @staticmethod
    def problems(entries):
        """List what is wrong with a rename declaration, empty when nothing is."""
        if not isinstance(entries, (list, tuple)):
            return [f"the rename map must be a list of objects, got {entries!r}"]
        found, olds, news = [], [], []
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                found.append(f"rename {i} must be an object with old, new and effective, got {entry!r}")
                continue
            found += [f"rename {i}: unknown key {k!r}" for k in sorted(set(entry) - set(_KEYS))]
            names = [entry.get(k) for k in ("old", "new")]
            if not all(isinstance(n, str) and n for n in names):
                found.append(f"rename {i}: old and new must be non-empty strings, got {names!r}")
                continue
            if names[0] == names[1]:
                found.append(f"rename {i}: old and new are the same name {names[0]!r}")
            text = entry.get("effective")
            try:
                if not (isinstance(text, str) and date.fromisoformat(text).isoformat() == text):
                    raise ValueError(text)
            except ValueError:
                found.append(f"rename {i}: effective must be an ISO date YYYY-MM-DD, got {text!r}")
            olds.append(names[0])
            news.append(names[1])
        found += [f"{n!r} is renamed twice" for n in sorted({o for o in olds if olds.count(o) > 1})]
        found += [f"chain: {n!r} is both an old and a new root - name the final root directly"
                  for n in sorted(set(olds) & set(news))]
        return found

    @staticmethod
    def break_problems(breaks, entries):
        """List what is wrong with a reuse-break declaration (against the renames too), empty when nothing is."""
        if not isinstance(breaks, (list, tuple)):
            return [f"the break map must be a list of objects, got {breaks!r}"]
        found, roots, names = [], [], []
        renamed = {n for e in entries if isinstance(e, dict) for n in (e.get("old"), e.get("new"))}
        for i, entry in enumerate(breaks):
            if not isinstance(entry, dict):
                found.append(f"break {i} must be an object with root, effective and earlier_as, got {entry!r}")
                continue
            found += [f"break {i}: unknown key {k!r}" for k in sorted(set(entry) - set(_BREAK_KEYS))]
            root, early, text = entry.get("root"), entry.get("earlier_as"), entry.get("effective")
            if not (isinstance(root, str) and root):
                found.append(f"break {i}: root must be a non-empty string, got {root!r}")
            if not (isinstance(early, str) and early) or early == root:
                found.append(f"break {i}: earlier_as must be a non-empty name other than the root, got {early!r}")
            try:
                if not (isinstance(text, str) and date.fromisoformat(text).isoformat() == text):
                    raise ValueError(text)
            except ValueError:
                found.append(f"break {i}: effective must be an ISO date YYYY-MM-DD, got {text!r}")
            roots.append(root)
            names.append(early)
        found += [f"break root {r!r} is declared twice" for r in sorted({r for r in roots if roots.count(r) > 1})]
        found += [f"break root {r!r} is also in a rename - a series is renamed or cut, not both"
                  for r in sorted(set(roots) & renamed)]
        taken = [n for n in names if n in renamed or n in roots or names.count(n) > 1]
        found += [f"earlier_as {n!r} is already a root or series name" for n in sorted(set(taken), key=str)]
        return found

    def segments(self, raw):
        """Return the output series a raw root's rows go to, as ``(series, since, before)``.

        ``since`` and ``before`` are ISO dates bounding the segment (inclusive and exclusive) or ``None``. A
        renamed root is one segment; a break root is two (the earlier name until ``effective``, then its own).
        """
        if raw in self._break:
            b = self._break[raw]
            return [(b["earlier_as"], None, b["effective"]), (raw, b["effective"], None)]
        return [(self.name(raw), None, None)]

    def regime_start(self, series):
        """Return the first date of a series cut off by a break (the later company), else ``None``."""
        return self._break[series]["effective"] if series in self._break else None

    def pending(self):
        """Return the entries still awaiting proof (kept for the report, never applied)."""
        return [dict(x) for x in self._pending]

    def declared_roots(self):
        """Return every raw root the applied map names (old, new and break roots)."""
        return {*self._rename, *self._rename.values(), *self._break}

    def pending_roots(self):
        """Return every root the pending entries name."""
        return {r for x in self._pending for r in (x.get("old"), x.get("new"), x.get("root")) if isinstance(r, str)}

    def breaks(self):
        """Return the validated break entries, sorted by root."""
        return [dict(b) for b in self._breaks]

    def name(self, root):
        """Return the output series a raw root belongs to."""
        return self._rename.get(root, root)

    def raws(self, series):
        """Return the raw roots an output series reads, old roots first, then the series' own name."""
        if series in self._earlier:
            return [self._earlier[series]]
        return [*sorted(self._olds.get(series, ())), series]

    def renames(self):
        """Return the validated entries, sorted by old root."""
        return [dict(e) for e in self._entries]

    def digest(self):
        """Return an identity of the map that does not depend on its order."""
        return value_hash({"renames": self._entries, "breaks": self._breaks})

    def check_windows(self, windows, dates=None):
        """Prove the declared effective dates (renames and breaks) against the data.

        Parameters
        ----------
        windows : dict
            ``{raw root: (first date, last date)}`` as ISO text, for the roots present in the data. A root of
            the map that is absent is not checked against the other side.
        dates : callable, optional
            ``dates(root)`` returns the sorted observed dates of a raw root. It is asked only for a break root
            whose window straddles its ``effective`` date. There the later company's first row must be the
            ``effective`` date itself (the last earlier row is then before it by construction): a break that
            falls inside a run of observed dates, or in a gap whose far side starts later, is refused naming
            both neighbouring rows. A root with rows on one side only cannot be contradicted.

        Raises
        ------
        ValueError
            An old root runs to or past its effective date, a new root starts before it, or the ranges overlap.
        """
        problems = []
        for old, new in sorted(self._rename.items()):
            effective = self._effective[old]
            if old in windows and windows[old][1] >= effective:
                problems.append(f"{old!r} has data through {windows[old][1]}, on or after the effective date "
                                f"{effective} of its rename to {new!r}")
            if new in windows and windows[new][0] < effective:
                problems.append(f"{new!r} has data from {windows[new][0]}, before the effective date {effective} "
                                f"of the rename {old!r} -> {new!r}")
            if old in windows and new in windows and windows[old][1] >= windows[new][0]:
                problems.append(f"{old!r} ({windows[old][0]}..{windows[old][1]}) and {new!r} "
                                f"({windows[new][0]}..{windows[new][1]}) overlap")
        problems += self._break_problems(windows, dates)
        if problems:
            raise ValueError("; ".join(problems))

    def _break_problems(self, windows, dates):
        """List the breaks the observed dates of a straddled root contradict."""
        problems = []
        for root, b in sorted(self._break.items()):
            effective = b["effective"]
            if dates is None or root not in windows or not windows[root][0] < effective <= windows[root][1]:
                continue
            seen = sorted(dates(root))
            before, after = [d for d in seen if d < effective], [d for d in seen if d >= effective]
            if before and after and after[0] != effective:
                problems.append(f"break of {root!r} at {effective}: its last earlier row is {before[-1]} and its next "
                                f"row is {after[0]}, so the later company's first row is {after[0]}, not {effective}")
        return problems

    @staticmethod
    def file_digest(path):
        """Return the sha256 of a file's bytes."""
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    @classmethod
    def from_file(cls, path, key, sha256, breaks_key=None, pending_key=None):
        """Load the map from ``path`` (a JSON object whose ``key`` holds the list), refusing a changed file.

        Parameters
        ----------
        path : str
            The JSON file.
        key : str
            The member holding the list of renames.
        sha256 : str
            The expected sha256 of the file's bytes; any other content refuses.
        breaks_key : str, optional
            The member holding the list of reuse breaks; absent means none.
        pending_key : str, optional
            The member holding entries awaiting proof; they are only reported, never applied.

        Raises
        ------
        ValueError
            The hash differs, the key is missing, or the map is invalid.
        """
        found = cls.file_digest(path)
        if found != sha256:
            raise ValueError(f"{path}: sha256 {found} is not the pinned {sha256} - the rename map changed; "
                             "re-pin it deliberately")
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        if not isinstance(document, dict) or key not in document:
            raise ValueError(f"{path}: no key {key!r} holding the rename list")
        if breaks_key is not None and breaks_key not in document:
            raise ValueError(f"{path}: no key {breaks_key!r} holding the break list")
        if pending_key is not None and pending_key not in document:
            raise ValueError(f"{path}: no key {pending_key!r} holding the pending list")
        return cls(document[key], None if breaks_key is None else document[breaks_key],
                   None if pending_key is None else document[pending_key])

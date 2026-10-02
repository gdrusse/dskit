"""Data-file entries: an onboarded-store reference or a legacy path (ADR-0225).

Owner rule ("Data enters through onboarding"): a run names the onboarded
SOURCE and STREAM it reads, not a directory somewhere on the machine. A
config entry that names a file or a directory of files is therefore EITHER

- a **store reference**, ``{"source": ..., "stream": ..., "relpath": ...,
  "manifest_sha256": ...}`` (``relpath`` names one file, and is left out to
  name the whole stream's tree; ``manifest_sha256`` is an optional pin), or
- a **legacy path string**, which keeps reading exactly the bytes it names.

References resolve against the store root a run already declares
(``data.root``), through
:func:`dskit.onboarding.artifacts.payload_files`, once per
``(root, source, stream)`` per process. The snapshot's Merkle manifest
supplies every file's sha256, so a 1.7 GB archive is not re-hashed, and the
resolver remembers which snapshots it handed out so the run's provenance can
name them (:meth:`DataFiles.provenance`).

A bare string cannot say whether it names a file or a directory, so there is
no single ``resolve``: ask for a file with :meth:`DataFiles.path` and for a
directory with :meth:`DataFiles.tree`.

Import cost: ``dskit.onboarding`` (stdlib + that package).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dskit.onboarding import AssetError, file_digest, payload_files

__all__ = ["DataFiles", "DataSourceError", "DataTree", "archive_relpath",
           "clear_snapshot_cache", "entry_problems"]

#: The keys a store reference may carry.
REFERENCE_KEYS = ("source", "stream", "relpath", "manifest_sha256")

#: ``payload_files`` results by ``(absolute store root, source, stream)``. One process
#: reads one snapshot of each stream, even if a newer one is acquired meanwhile.
_SNAPSHOTS = {}


class DataSourceError(ValueError):
    """A data entry that cannot be resolved.

    Raised for a malformed reference, a store reference with no store root, a
    ``manifest_sha256`` pin that disagrees with the current snapshot, a
    source or stream the store cannot resolve, and a relpath the snapshot
    does not hold. The message names the source, stream and relpath.
    """


def clear_snapshot_cache():
    """Forget every resolved snapshot, so the next reference resolves afresh.

    Returns
    -------
    None
        A long-lived process calls this after acquiring a newer snapshot.
    """
    _SNAPSHOTS.clear()


def archive_relpath(symbol, year):
    """Return the archive file of one symbol and year inside its tree.

    Parameters
    ----------
    symbol : str
        The ticker, any case.
    year : int or str
        The calendar year.

    Returns
    -------
    str
        ``"<symbol lower>/options_<year>.parquet"``, the layout of the
        ``philippdubach-options`` archive.
    """
    return f"{str(symbol).lower()}/options_{year}.parquet"


def entry_problems(name, entry, *, tree=False):
    """List what is wrong with one data entry; empty when it is usable.

    Parameters
    ----------
    name : str
        The entry's config name, for the messages.
    entry : object
        A path string or a store reference dict.
    tree : bool, optional
        True for an entry that names a directory of files (no ``relpath``);
        False (default) for one that names a single file (``relpath``
        required in a reference).

    Returns
    -------
    list of str
        Every problem found.

    Examples
    --------
    ::

        entry_problems("surface", {"source": "s", "stream": "files",
                                   "relpath": "a.parquet"})   # []
        entry_problems("surface", {"source": "s"})            # four problems
    """
    if isinstance(entry, str):
        return [] if entry else [f"{name} must not be an empty path"]
    if not isinstance(entry, dict):
        return [f"{name} must be a path string or a store reference dict, "
                f"got {type(entry).__name__}"]
    problems = []
    unknown = sorted(set(entry) - set(REFERENCE_KEYS), key=str)
    if unknown:
        problems.append(f"{name} has unknown key(s) {unknown}; "
                        f"a store reference takes {list(REFERENCE_KEYS)}")
    for key in ("source", "stream"):
        if not isinstance(entry.get(key), str) or not entry.get(key):
            problems.append(f"{name}.{key} must be a non-empty string")
    if tree and "relpath" in entry:
        problems.append(f"{name} names a directory of files: it takes no relpath")
    elif not tree and "relpath" not in entry:
        problems.append(f"{name} names one file: it needs a relpath")
    relpath = entry.get("relpath")
    if "relpath" in entry and (
            not isinstance(relpath, str) or not relpath or relpath.startswith("/")
            or ".." in relpath.split("/")):
        problems.append(f"{name}.relpath must be a relative POSIX path without '..'")
    pin = entry.get("manifest_sha256")
    if "manifest_sha256" in entry and (
            not isinstance(pin, str) or re.fullmatch(r"[0-9a-f]{64}", pin) is None):
        problems.append(f"{name}.manifest_sha256 must be 64 lowercase hex characters")
    return problems


def _is_path(entry):
    """Return True for a legacy path (a string or path-like), False for a reference dict."""
    return isinstance(entry, (str, os.PathLike))


class DataTree:
    """The files below one data location, addressed by relpath.

    Built by :meth:`DataFiles.tree`: from a store reference (the files of
    one stream's current snapshot, digests from its manifest) or from a
    legacy directory (files read in place, digests computed on demand).

    Parameters
    ----------
    directory : str or Path, optional
        A legacy directory; the tree's files live below it.
    snapshot : dict, optional
        A :func:`~dskit.onboarding.artifacts.payload_files` result; the tree's
        files are that stream's.

    Examples
    --------
    ::

        tree = DataFiles("./ob").tree({"source": "philippdubach-options", "stream": "files"})
        rel = archive_relpath("QQQ", 2012)
        tree.path(rel)      # the snapshot's file
        tree.sha256(rel)    # the manifest's digest, nothing re-read
    """

    def __init__(self, *, directory=None, snapshot=None):
        self._directory = None if directory is None else Path(directory)
        self._snapshot = snapshot

    @property
    def store(self):
        """Return the snapshot identity of a store tree, None for a legacy directory.

        Returns
        -------
        dict or None
            ``source``, ``stream``, ``snapshot`` and ``manifest_sha256``.
        """
        if self._snapshot is None:
            return None
        return {key: self._snapshot[key]
                for key in ("source", "stream", "snapshot", "manifest_sha256")}

    def _where(self):
        got = self._snapshot
        return f"{got['source']}/{got['stream']} (snapshot {got['snapshot']})"

    def has(self, relpath):
        """Return whether the tree holds ``relpath`` (a legacy file must exist on disk)."""
        if self._snapshot is None:
            return self.path(relpath).exists()
        return relpath in self._snapshot["files"]

    def path(self, relpath):
        """Return the file's :class:`~pathlib.Path`.

        Raises
        ------
        DataSourceError
            When a store tree does not hold ``relpath`` (a legacy path is
            returned unchecked, as it always was; reading it raises).
        """
        if self._snapshot is None:
            return self._directory.joinpath(*relpath.split("/"))
        files = self._snapshot["files"]
        if relpath not in files:
            raise DataSourceError(
                f"{self._where()} has no file {relpath!r} ({len(files)} file(s) held)")
        return files[relpath]

    def sha256(self, relpath):
        """Return the file's hex sha256: the manifest's for a store file, else computed.

        Raises
        ------
        DataSourceError
            When a store tree lacks ``relpath`` or its manifest holds no
            digest for it.
        """
        if self._snapshot is None:
            return file_digest(str(self.path(relpath)))
        self.path(relpath)
        digest = self._snapshot["sha256"].get(relpath)
        if not isinstance(digest, str):
            raise DataSourceError(f"{self._where()}: the manifest holds no sha256 for {relpath!r}")
        return digest

    def label(self, relpath):
        """Return the name provenance files this file under.

        A legacy file is its path; a store file is
        ``store:<source>/<stream>/<relpath>``, independent of where the
        store lives (the snapshot identity is recorded beside it).
        """
        if self._snapshot is None:
            return str(self.path(relpath))
        return f"store:{self._snapshot['source']}/{self._snapshot['stream']}/{relpath}"


class DataFiles:
    """Resolve config data entries against a store root, remembering what it handed out.

    One instance per read: it holds the store root (``data.root``) and the
    snapshots its references resolved to. A legacy path string never touches
    the root.

    Parameters
    ----------
    root : str or Path, optional
        The onboarding store root. Required only when an entry is a store
        reference.

    Examples
    --------
    ::

        files = DataFiles("/home/russell/data/index_options/ob")
        surface = files.path({"source": "exact-expiry-tables", "stream": "files",
                              "relpath": "exact_expiry_surface.parquet"})
        files.provenance()
        # [{"source": "exact-expiry-tables", "stream": "files", "snapshot": "...",
        #   "manifest_sha256": "...", "files": 2}]
    """

    def __init__(self, root=None):
        self.root = None if root is None else os.fspath(root)
        self._seen = {}

    def _store(self, entry, *, tree):
        """Return the ``payload_files`` result a reference names, checking its pin."""
        problems = entry_problems("data entry", entry, tree=tree)
        if problems:
            raise DataSourceError("; ".join(problems))
        source, stream = entry["source"], entry["stream"]
        if self.root is None:
            raise DataSourceError(
                f"{source}/{stream}: a store reference needs a store root "
                "(data.root), none was given")
        key = (os.path.abspath(self.root), source, stream)
        got = _SNAPSHOTS.get(key)
        if got is None:
            try:
                got = payload_files(self.root, source, stream)
            except AssetError as exc:
                raise DataSourceError(
                    f"{source}/{stream} under {self.root!r}: {exc}") from exc
            _SNAPSHOTS[key] = got
        pin = entry.get("manifest_sha256")
        if pin is not None and pin != got["manifest_sha256"]:
            raise DataSourceError(
                f"{source}/{stream}: pinned manifest_sha256 {pin} but the current "
                f"snapshot {got['snapshot']} is {got['manifest_sha256']} — restore the "
                "pinned snapshot or re-pin deliberately")
        self._seen[(source, stream)] = {
            "source": source, "stream": stream, "snapshot": got["snapshot"],
            "manifest_sha256": got["manifest_sha256"], "files": len(got["files"])}
        return got

    def tree(self, entry):
        """Return the :class:`DataTree` a directory-like entry names.

        Parameters
        ----------
        entry : str, PathLike or dict
            A legacy directory, or a store reference without ``relpath``.

        Returns
        -------
        DataTree

        Raises
        ------
        DataSourceError
            For a malformed reference, a missing store root, a disagreeing
            pin, or a source/stream the store cannot resolve.
        """
        if _is_path(entry):
            if not os.fspath(entry):
                raise DataSourceError("a data directory must not be an empty path")
            return DataTree(directory=entry)
        return DataTree(snapshot=self._store(entry, tree=True))

    def _locate(self, entry, suffix):
        """Return ``(tree, relpath)`` of the one file an entry names, plus ``suffix``."""
        if _is_path(entry):
            if not os.fspath(entry):
                raise DataSourceError("a data file must not be an empty path")
            path = Path(os.fspath(entry)+suffix)
            return DataTree(directory=path.parent), path.name
        return DataTree(snapshot=self._store(entry, tree=False)), entry["relpath"]+suffix

    def path(self, entry, suffix=""):
        """Return the :class:`~pathlib.Path` of the file an entry names.

        Parameters
        ----------
        entry : str, PathLike or dict
            A legacy file path, or a store reference with ``relpath``.
        suffix : str, optional
            Appended to the path / relpath: the sibling file (for example
            ``".sources.json"``) in the same directory or stream.

        Returns
        -------
        Path

        Raises
        ------
        DataSourceError
            As :meth:`tree`, and when the store holds no such relpath.
        """
        tree, relpath = self._locate(entry, suffix)
        return tree.path(relpath)

    def has(self, entry, suffix=""):
        """Return whether the file (or its ``suffix`` sibling) exists; see :meth:`path`."""
        tree, relpath = self._locate(entry, suffix)
        return tree.has(relpath)

    def sha256(self, entry, suffix=""):
        """Return the file's hex sha256: the manifest's for a store file, else computed.

        Parameters
        ----------
        entry, suffix
            As :meth:`path`.

        Returns
        -------
        str
            Identical to a hash of the file's bytes; a store file's comes
            from the snapshot manifest without reading it.
        """
        tree, relpath = self._locate(entry, suffix)
        return tree.sha256(relpath)

    def provenance(self):
        """Return the snapshots this resolver handed out, for the run's provenance.

        Returns
        -------
        list of dict
            One per ``(source, stream)``, sorted: ``source``, ``stream``,
            ``snapshot`` (the acquisition id), ``manifest_sha256`` and
            ``files`` (the stream's file count). Empty when only legacy
            paths were read.
        """
        return [dict(self._seen[key]) for key in sorted(self._seen)]

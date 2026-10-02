r"""``localblobs`` — acquire files already on this machine as hashed binary artifacts.

A research input that lives on disk as a parquet, a zip or a sidecar JSON is
not a row stream, and importing it row by row (``localtables``) is slow for a
wide archive and loses the columnar file the readers want. This pack makes
the FILE itself the acquisition (ADR-0082): every matched file is copied into
the snapshot at ``payload/<stream>/<relpath>``, digested into the Merkle
manifest and re-hashable by ``verify``. Readers then name the source and
stream and resolve it to verified paths with
:func:`dskit.onboarding.artifacts.payload_files` instead of naming a
directory somewhere on the machine (ADR-0225).

One source is one stream. A ``relpath`` keeps its sub-directories, so a
per-symbol archive stays ``qqq/options_2012.parquet``. Only regular files
are acquired (a symlink to a file is followed, the bytes behind it are
copied); sub-directory symlinks are not descended. An unreadable directory
refuses rather than shrinking the inventory, and so does a name the FILE
envelope cannot carry (a ``:`` or ``\`` in a segment, invalid UTF-8) —
exclude it by glob if it should stay out.

Each RECORD is one inventory row ``{relpath, size, sha256}`` dated at the
declared ``as_of``; the machine path rides only on the FILE message, which
the platform never echoes into bronze.

Cursor semantics: the checkpoint is a fingerprint of the sorted
``(relpath, size, sha256)`` listing plus ``as_of`` and the selection; an
unchanged listing under the same declaration emits one LOG and the same
STATE (no new snapshot), a changed one re-emits every file so each snapshot
is a complete inventory. The logic is identical in both modes, but the
platform keys cursors per mode: pick one mode per source, or a ``live``
pull re-copies what ``backfill`` already holds.

Config knobs (default-deny, per ``spec()``):

- ``path`` (required) — the directory holding the files; machine-local
  provenance, never echoed into a record.
- ``as_of`` (required) — the ISO instant the inventory is declared current;
  it is every RECORD's ``effective_date`` and may not be in the future.
  Declared, never read from file times, so two pulls of the same bytes agree.
- ``stream`` — the stream name, default ``files``; a filesystem-safe segment
  (lowercase letters, digits, ``_`` and ``-``).
- ``include`` — glob patterns over the POSIX relpath (``fnmatch``: ``*``
  crosses ``/``), default every file. Omit it for the default; an empty
  list refuses.
- ``exclude`` — glob patterns to drop after ``include``, default none.

Import cost: stdlib only.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os

from ..base import (
    AssetError,
    _check_segment,
    _raise_if,
    file_digest,
    parse_utc,
    utc_now,
)
from ..connector import PROTOCOL, Connector, _file_relpath_problems

__all__ = ["DEFAULT_STREAM", "RECORD_FIELDS", "STATE_KEYS", "LocalBlobsConnector"]

#: The stream a source offers unless ``config.stream`` names another.
DEFAULT_STREAM = "files"

#: The RECORD fields — one inventory row per acquired file.
RECORD_FIELDS = ("relpath", "size", "sha256")

#: The checkpoint keys; an unchanged pull matches all of them.
STATE_KEYS = ("fingerprint", "as_of", "stream", "include", "exclude")


def _patterns(problems, name, value, default):
    """Return ``value`` as a list of non-empty globs, ``default`` when absent; record why not."""
    if value is None:
        return list(default)
    if not isinstance(value, list) or not value or not all(
        isinstance(p, str) and p for p in value
    ):
        problems.append(
            f"config.{name} must be a non-empty list of non-empty glob strings "
            f"(omit it for the default), got {value!r}"
        )
        return list(default)
    return list(value)


def _walk_failed(exc):
    """Refuse an unreadable directory — ``os.walk`` would silently skip it."""
    raise AssetError(
        [f"cannot list {exc.filename!r}: {exc.strerror or exc} — refusing a "
         "partial inventory"]
    ) from exc


def _relpaths(path, include, exclude):
    """Every regular file under ``path`` matching the globs, as sorted POSIX relpaths."""
    found = []
    for parent, _dirs, names in os.walk(path, onerror=_walk_failed):
        for name in names:
            full = os.path.join(parent, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, path).replace(os.sep, "/")
            if (any(fnmatch.fnmatchcase(rel, p) for p in include)
                    and not any(fnmatch.fnmatchcase(rel, p) for p in exclude)):
                found.append(rel)
    return sorted(found)


def _relpath_problems(rel):
    """Why the FILE envelope cannot carry ``rel`` — empty when it can."""
    try:
        rel.encode("utf-8")
    except UnicodeEncodeError:
        return [f"relpath {rel!r} is not valid UTF-8"]
    return _file_relpath_problems(rel)


def _selected(knobs):
    """Return the selection's sorted relpaths; refuse an empty one or a name the envelope cannot carry."""
    relpaths = _relpaths(knobs["path"], knobs["include"], knobs["exclude"])
    if not relpaths:
        raise AssetError(
            [f"include={knobs['include']!r} / exclude={knobs['exclude']!r} matched no "
             "file under the path — refusing to cursor past an empty selection"]
        )
    _raise_if([p for rel in relpaths for p in _relpath_problems(rel)])
    return relpaths


def _signature(full):
    """``(size, mtime_ns)`` of a file — what a mid-pull change would move."""
    try:
        info = os.stat(full)
    except OSError as exc:
        raise AssetError([f"cannot stat {full!r}: {exc}"]) from exc
    return info.st_size, info.st_mtime_ns


class LocalBlobsConnector(Connector):
    r"""Local files as hashed binary artifacts, one stream per source.

    See the module docstring for the full contract: knobs, cursor
    semantics and the relpath rule.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Register a per-symbol parquet archive as one stream, then acquire it::

        python -m dskit.onboarding register-source option-archive \
          --catalog-source option-archive --connector localblobs \
          --config '{"path": "/data/archive", "as_of": "2026-10-02T00:00:00+00:00",
                     "include": ["*/*.parquet"]}' --activate --root ./ob
        python -m dskit.onboarding acquire --source option-archive \
          --stream files --mode backfill --root ./ob
    """

    def spec(self):
        """Declare the knobs, default-deny.

        Returns
        -------
        dict
            ``{"params": {...}}`` — one entry per knob in the module docstring.
        """
        return {"params": {
            "path": {
                "required": True,
                "notes": "Directory holding the files. Machine-local provenance: it "
                         "is read at pull time and never lands in a record.",
            },
            "as_of": {
                "required": True,
                "notes": "ISO instant the inventory is declared current (not in the "
                         "future); every RECORD's effective_date. Declared, not read "
                         "from mtimes.",
            },
            "stream": {
                "notes": f"Stream name; default {DEFAULT_STREAM!r}. A filesystem-safe "
                         "segment: lowercase letters, digits, '_' and '-'.",
            },
            "include": {
                "notes": "Glob patterns over the POSIX relpath (fnmatch; '*' crosses "
                         "'/'); default every file. A non-empty list when given.",
            },
            "exclude": {
                "notes": "Glob patterns dropped after include; default none.",
            },
        }}

    def resolve_knobs(self, config):
        """Validate the knobs and return them normalised, or raise listing every problem.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        dict
            ``path`` (absolute), ``as_of`` (ISO UTC), ``stream``, ``include``,
            ``exclude``.

        Raises
        ------
        AssetError
            Listing every unusable knob value.
        """
        problems = []
        path = shown = config.get("path")
        if isinstance(path, str) and path:
            path = os.path.abspath(os.path.expanduser(path))
        if not (isinstance(path, str) and os.path.isdir(path)):
            problems.append(f"config.path must be an existing directory, got {shown!r}")
        stream = config.get("stream", DEFAULT_STREAM)
        _check_segment(problems, "config.stream", stream)
        as_of = config.get("as_of")
        as_of_iso = None
        try:
            when = parse_utc(as_of)
        except AssetError:
            problems.append(f"config.as_of must be an ISO-8601 instant, got {as_of!r}")
        else:
            if when > parse_utc(utc_now()):
                problems.append(
                    f"config.as_of {as_of!r} is in the future — an observation about "
                    "the future is a forecast; declare the instant the files were current"
                )
            as_of_iso = when.isoformat()
        include = _patterns(problems, "include", config.get("include"), ["*"])
        exclude = _patterns(problems, "exclude", config.get("exclude"), [])
        _raise_if(problems)
        return {"path": path, "as_of": as_of_iso, "stream": stream,
                "include": include, "exclude": exclude}

    def check(self, config):
        """Validate the knobs and that at least one file matches; move no data.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Raises
        ------
        AssetError
            On an unusable knob, an unreadable directory, a selection that
            matches no file, or a matched name the FILE envelope cannot carry.
        """
        _selected(self.resolve_knobs(config))

    def discover(self, config):
        """List the one stream and its inventory schema.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list of dict
            One entry: the stream, the RECORD fields, key ``[relpath]``.

        Raises
        ------
        AssetError
            On an unusable knob.
        """
        knobs = self.resolve_knobs(config)
        return [{
            "stream": knobs["stream"],
            "schema": {"fields": list(RECORD_FIELDS)},
            "primary_key": ["relpath"],
        }]

    def read(self, config, streams, state, mode):
        """Yield FILE + RECORD per matched file, then STATE.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list of str
            Must be exactly the configured stream; any other name refuses.
        state : dict
            The mode-keyed checkpoint from the last committed pull, or ``{}``.
        mode : str
            ``backfill`` or ``live``; the pull is identical in both.

        Yields
        ------
        dict
            Envelope messages: per file a FILE then a RECORD, then STATE; or
            LOG + STATE when the listing and the declaration are unchanged.

        Raises
        ------
        AssetError
            On an unknown stream, an unusable knob, an unreadable directory,
            a selection that matches no file (no STATE is emitted then, so
            the cursor stays where it was), a name the FILE envelope cannot
            carry, or a file that changed while it was being acquired.
        """
        knobs = self.resolve_knobs(config)
        stream = knobs["stream"]
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        for name in streams:
            if name != stream:
                raise AssetError(
                    [f"unknown stream {name!r} — this source offers {stream!r} only"]
                )
        inventory = []
        fingerprint = hashlib.sha256()
        for rel in _selected(knobs):
            full = os.path.join(knobs["path"], *rel.split("/"))
            signature = _signature(full)
            sha = file_digest(full)
            inventory.append((rel, full, signature, sha))
            fingerprint.update(f"{rel}\t{signature[0]}\t{sha}\n".encode("utf-8"))
        checkpoint = {
            "fingerprint": fingerprint.hexdigest(),
            "as_of": knobs["as_of"], "stream": stream,
            "include": knobs["include"], "exclude": knobs["exclude"],
        }
        if all(state.get(k) == checkpoint[k] for k in STATE_KEYS):
            yield {
                "protocol": PROTOCOL, "type": "LOG",
                "message": f"{len(inventory)} files under the same declaration and "
                           "digests — nothing new",
            }
            yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}
            return
        for rel, full, signature, sha in inventory:
            yield {"protocol": PROTOCOL, "type": "FILE", "stream": stream,
                   "relpath": rel, "path": full}
            # The platform has copied the file by the time we resume. A writer
            # that touched it since the digest would leave a RECORD sha that
            # is not the bytes in the manifest — refuse rather than commit it.
            if _signature(full) != signature:
                raise AssetError(
                    [f"{rel!r} changed while it was being acquired — re-run once "
                     "its writer is done"]
                )
            yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                   "effective_date": knobs["as_of"],
                   "data": {"relpath": rel, "size": signature[0], "sha256": sha}}
        yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}

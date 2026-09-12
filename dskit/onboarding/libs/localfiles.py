"""``localfiles`` — the reference connector: CSV/JSONL files in a directory.

Two jobs. First, a genuinely useful tier-1 connector: any directory of
``*.csv`` / ``*.jsonl`` files becomes a source, each file a stream named
by its stem, each row a RECORD. Second, the CONFORMANCE REFERENCE — the
connector the test suite drives through the full contract, and the one
to copy when writing your own.

Cursor semantics: state maps stream -> ``{"cursor": <max effective_date
emitted>}``; a pull emits only rows strictly after the cursor and
checkpoints once per stream. The logic is IDENTICAL in both modes —
the platform keys checkpoints per (source, stream, mode), so a backfill
walking one slice of history and a live pull walking forward hold
independent cursors without this connector doing anything (the ADR-0014
division of labor: modes are the platform's axis, not the connector's).

Config knobs (default-deny, per ``spec()``):

- ``path`` (required) — the directory of data files.
- ``effective_field`` (required) — the column/key holding each row's
  ISO ``effective_date``.
- ``forecast_streams`` — stream names whose records are DECLARED
  forecasts (``kind="forecast"``, segregated at save; OQ-6).
- ``encoding`` — file encoding, default utf-8.

CSV values are strings (stdlib ``csv`` does not guess types — a
validation suite's ``in_range`` on CSV data needs a JSONL source or a
downstream cast; the semantic seam again). JSONL rows must be objects.

Import cost: stdlib only.
"""

from __future__ import annotations

import csv
import json
import os

from ..base import AssetError, _check_dict, _raise_if, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = ["LocalFilesConnector"]

_EXTENSIONS = (".csv", ".jsonl")

#: Default ``config.encoding`` — shared by discover() and read() so the
#: fallback can only ever drift once, not twice.
_DEFAULT_ENCODING = "utf-8"


class LocalFilesConnector(Connector):
    """Files in a local directory, one stream per file.

    See the module docstring for the full contract: cursor semantics,
    config knobs, and the CSV/JSONL value-typing caveat.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Discover the streams under a directory of data files::

        connector = LocalFilesConnector()
        streams = connector.discover({
            "path": "/data/my_source",
            "effective_field": "date",
        })
    """

    def spec(self) -> dict:
        """Declare the default-deny local-files configuration catalogue.

        Returns
        -------
        dict
            Connector knob declarations.
        """
        return {
            "params": {
                "path": {
                    "required": True,
                    "notes": "Directory holding *.csv / *.jsonl data files.",
                },
                "effective_field": {
                    "required": True,
                    "notes": "Column/key carrying each row's ISO effective_date.",
                },
                "forecast_streams": {
                    "notes": "Stream names emitted as kind=forecast — a "
                             "declared fact, never inferred from dates.",
                },
                "encoding": {
                    "notes": "File encoding; default utf-8.",
                },
            },
        }

    # -- internals ---------------------------------------------------------

    def _dir(self, config) -> str:
        """Return ``config.path`` as an absolute, expanded directory path."""
        path = config.get("path")
        if not isinstance(path, str) or not path:
            raise AssetError([f"config.path must be a non-empty string, got {path!r}"])
        return os.path.abspath(os.path.expanduser(path))

    def _files(self, config) -> dict:
        """Stream name -> file path, for every recognized data file."""
        directory = self._dir(config)
        if not os.path.isdir(directory):
            raise AssetError([f"config.path is not a directory: {directory!r}"])
        out = {}
        for fname in sorted(os.listdir(directory)):
            stem, ext = os.path.splitext(fname)
            if ext in _EXTENSIONS and not fname.startswith("."):
                if stem in out:
                    raise AssetError(
                        [f"stream {stem!r} exists as both csv and jsonl — "
                         "one file per stream"]
                    )
                out[stem] = os.path.join(directory, fname)
        return out

    def _rows(self, path, encoding):
        """Yield (rownum, dict) from a csv or jsonl file."""
        if path.endswith(".csv"):
            with open(path, encoding=encoding, newline="") as fh:
                for i, row in enumerate(csv.DictReader(fh), start=1):
                    yield i, dict(row)
            return
        with open(path, encoding=encoding) as fh:
            for i, line in enumerate(fh, start=1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError as exc:
                    raise AssetError([f"{path}:{i} is not valid JSON: {exc}"]) from exc
                if not isinstance(obj, dict):
                    raise AssetError([f"{path}:{i} must be a JSON object"])
                yield i, obj

    # -- the four verbs ----------------------------------------------------

    def check(self, config) -> None:
        """Refuse a config whose directory is missing or empty of recognized files.

        Parameters
        ----------
        config : dict
            Knobs already validated by :func:`~dskit.onboarding.connector.check_config`.

        Raises
        ------
        AssetError
            If ``config.path`` does not exist, is not a directory,
            holds no ``*.csv``/``*.jsonl`` file, or holds both a
            ``.csv`` and a ``.jsonl`` file for the same stem.
        """
        if not self._files(config):
            raise AssetError(
                [f"no *.csv / *.jsonl files under {self._dir(config)!r}"]
            )

    def discover(self, config) -> list:
        """One stream per file; schema = the first row's keys.

        Parameters
        ----------
        config : dict
            Knobs already validated by :func:`~dskit.onboarding.connector.check_config`.

        Returns
        -------
        list of dict
            ``{"stream": str, "schema": {"fields": list of str},
            "primary_key": []}`` per file — this connector declares no
            primary key.

        Raises
        ------
        AssetError
            If ``config.path`` does not exist, is not a directory, a
            stem exists as both a ``.csv`` and a ``.jsonl`` file, or a
            file's first row is malformed JSON / not a JSON object.
        """
        encoding = config.get("encoding", _DEFAULT_ENCODING)
        out = []
        for stream, path in sorted(self._files(config).items()):
            fields = []
            for _i, row in self._rows(path, encoding):
                fields = sorted(row)
                break
            out.append({
                "stream": stream,
                "schema": {"fields": fields},
                "primary_key": [],
            })
        return out

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, then cursor-filtered RECORDs, then one STATE.

        Rows are sorted by effective date before emission so the cursor
        ("everything before this is durable") is honest — an unsorted
        source must not checkpoint past unemitted rows.

        Parameters
        ----------
        config : dict
            Knobs already validated by :func:`~dskit.onboarding.connector.check_config`.
        streams : list of str
            Which discovered streams to pull.
        state : dict
            The last persisted checkpoint, keyed by stream; ``{}`` on a
            first pull.
        mode : str
            ``"backfill"`` or ``"live"`` — unused here: this connector's
            cursor logic is identical in both modes (see the module
            docstring).

        Yields
        ------
        dict
            For each stream in turn: one SCHEMA message, then its
            cursor-filtered RECORD messages in ascending effective-date
            order; finally, after every stream, one STATE message
            carrying every stream's updated cursor.

        Raises
        ------
        AssetError
            If ``state`` is not a dict; a stream's ``state`` cursor
            does not parse as an ISO date/datetime; ``streams`` is
            empty or not a list; ``config.path`` does not exist, is not
            a directory, or holds a duplicate csv/jsonl stem; a
            requested stream was not discovered; a row's effective-date
            field is missing, empty, or does not parse as an ISO
            date/datetime; or a JSONL row is malformed JSON or not a
            JSON object.
        ValueError
            If a per-stream value in ``state`` is not itself a dict
            (only the outer ``state`` shape is checked).
        """
        errors = []
        _check_dict(errors, "state", state)
        if not isinstance(streams, list) or not streams:
            errors.append(f"streams must be a non-empty list, got {streams!r}")
        _raise_if(errors)
        files = self._files(config)
        encoding = config.get("encoding", _DEFAULT_ENCODING)
        eff_field = config.get("effective_field")
        forecast_streams = config.get("forecast_streams", [])
        new_state = {k: dict(v) for k, v in state.items()}

        for stream in streams:
            path = files.get(stream)
            if path is None:
                raise AssetError(
                    [f"unknown stream {stream!r} — discovered: {sorted(files)}"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            kind = "forecast" if stream in forecast_streams else "observation"

            fields = []
            for _i, row in self._rows(path, encoding):
                fields = sorted(row)
                break
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": fields}}

            rows = []
            for i, row in self._rows(path, encoding):
                eff = row.get(eff_field)
                if not isinstance(eff, str) or not eff:
                    raise AssetError(
                        [f"{path}:{i}: field {eff_field!r} missing or empty — "
                         "every row needs its effective_date"]
                    )
                rows.append((parse_utc(eff), eff, row))
            rows.sort(key=lambda t: t[0])

            emitted_max = cursor
            for eff_dt, eff, row in rows:
                if cursor_dt is not None and eff_dt <= cursor_dt:
                    continue  # already durable per the checkpoint
                yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                       "effective_date": eff, "kind": kind, "data": row}
                emitted_max = eff
            new_state.setdefault(stream, {})["cursor"] = emitted_max

        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

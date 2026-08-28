"""``connectors`` — the child's onboarding seam (four verbs, ADR-0013).

THIS FILE is where a real child's vendor logic goes: the API client, the
auth dance, the pagination — all of it behind the one ``Connector``
contract (``spec``/``check``/``discover``/``read``), so the platform's
durability, checkpointing, and validation never change per vendor. The
skeleton's :class:`SampleConnector` keeps the SHAPE with none of the
plumbing: a small deterministic in-code stream — no filesystem, no
network — so the template acquires real snapshots anywhere.

Cursor semantics, identical to the reference ``localfiles`` connector:
state maps stream -> ``{"cursor": <max effective_date emitted>}``; a pull
emits only rows strictly after the cursor and checkpoints once. The logic
is the same in both modes — the platform keys checkpoints per (source,
stream, mode), so backfill and live hold independent cursors without this
connector doing anything (ADR-0014).

Config knobs (default-deny, per ``spec()``):

- ``rows`` (required) — how many records one full pull yields.
- ``start_date`` — ISO date of the first record; the stream steps one day
  per row from here. Keep it in the past: records are observations, and
  acquisition refuses an observation dated after ``acquired_at``.

Import cost: stdlib + dskit. A real connector's heavy client import
belongs INSIDE ``read`` (the same rule as pipeline nodes' ``run``).
"""

from __future__ import annotations

from datetime import timedelta

from dskit.onboarding import PROTOCOL, AssetError, Connector, parse_utc

__all__ = ["SampleConnector"]

#: The one stream this source offers and its row shape. A real connector
#: discovers these from the vendor; the skeleton declares them.
_STREAM = "samples"
_FIELDS = ["day", "id", "value"]

_DEFAULT_START = "2026-01-01"


class SampleConnector(Connector):
    """A deterministic in-code source, one stream. See module docs."""

    def spec(self) -> dict:
        return {
            "params": {
                "rows": {
                    "required": True,
                    "notes": "How many records one full pull yields.",
                },
                "start_date": {
                    # BUILT from the constant, never restated: spec() is
                    # the knob catalogue a config author reads, and a
                    # note advertising a stale default is a config lie.
                    "notes": "ISO date of the first record (one day per "
                             f"row from here); default {_DEFAULT_START}.",
                },
            },
        }

    # -- internals ---------------------------------------------------------

    def _budget(self, config) -> int:
        rows = config.get("rows")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows < 1:
            raise AssetError([f"config.rows must be an int >= 1, got {rows!r}"])
        return rows

    def _rows(self, config):
        """Yield ``(effective_date, data)`` in date order — a real child
        replaces this with the vendor fetch, keeping the emission sorted
        so the cursor ("everything before this is durable") stays honest."""
        rows = self._budget(config)
        start = parse_utc(config.get("start_date", _DEFAULT_START))
        for i in range(rows):
            day = (start + timedelta(days=i)).date().isoformat()
            yield day, {
                "id": f"sample-{i:04d}",
                "day": day,
                "value": round(10.0 + 1.5 * (i % 4), 2),
            }

    # -- the four verbs ----------------------------------------------------

    def check(self, config) -> None:
        """Fail fast on knobs a pull would choke on; move no data. A real
        connector authenticates and pings the vendor here."""
        self._budget(config)
        parse_utc(config.get("start_date", _DEFAULT_START))

    def discover(self, config) -> list:
        """The streams on offer — a real connector asks the vendor."""
        return [{
            "stream": _STREAM,
            "schema": {"fields": list(_FIELDS)},
            "primary_key": ["id"],
        }]

    def read(self, config, streams, state, mode):
        """Emit SCHEMA, then cursor-filtered RECORDs, then one STATE."""
        if not isinstance(state, dict):
            raise AssetError([f"state must be a dict, got {state!r}"])
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        new_state = {k: dict(v) for k, v in state.items()}

        for stream in streams:
            if stream != _STREAM:
                raise AssetError(
                    [f"unknown stream {stream!r} — discovered: [{_STREAM!r}]"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None

            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": {"fields": list(_FIELDS)}}

            emitted_max = cursor
            for eff, data in self._rows(config):
                if cursor_dt is not None and parse_utc(eff) <= cursor_dt:
                    continue  # already durable per the checkpoint
                yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                       "effective_date": eff, "kind": "observation", "data": data}
                emitted_max = eff
            new_state.setdefault(stream, {})["cursor"] = emitted_max

        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

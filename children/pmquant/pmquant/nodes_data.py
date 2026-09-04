"""``nodes_data`` — the child's data-side kinds: ladder source, settlement labels, inventory.

The ladder markets this child models arrive as two acquired streams — the
point-in-time ledger (one row per contract per decision epoch, ``book_json``
and all) and the settlement/strike store (one row per contract) — and both
are read back through the toolkit's ``observations`` data kind
(:class:`~dskit.pipeline.libs.observations.ObservationRows`, ADR-0077):
bitemporal dedup, the memoized resolve/execute snapshot, the content
digest. What this module adds is only what the toolkit cannot know: each
stream's VOCABULARY (which fields key a row, which field is the instant,
which venue owns a series) and the PROJECTION into the child's own record
— a :class:`~pmquant.books.DecisionEpochRecord` inside the toolkit's
:class:`~dskit.pipeline.records.MarketRecord` envelope, the mirror rule
applied once, in ``books``, never here.

* ``pmquant-ladder-source`` (role ``data``) — the ledger, scoped to ONE
  venue. Records are emitted RAW: an unusable epoch rides along with its
  reason, because cutting is the document's job (``filter`` with
  ``require_usable``), never a source's. Its ``data_edge`` is the newest
  SETTLE epoch — open contracts trail the ledger — and its
  ``event_bounds`` are what make ``splits.policy: "event-close"`` legal.
* ``pmquant-settlement`` (role ``labels``) — the settlement store as the
  ``{contract: settled-YES?}`` map plus the strike rows. An OPEN contract
  is ABSENT from the map, never ``False``; a result outside the vocabulary
  refuses by ticker. The universe may be wired in (a ladder source's
  ``instruments``) and then wins over the param.
* ``pmquant-inventory`` (role ``report``) — per series, the settled /
  usable / two-sided / tradeable event counts over the declared lead
  grid, and the coverage mark ``usable >= min_events``. Verdict first.

The stream vocabulary (:data:`PIT_STREAM`, :data:`PIT_KEY_FIELDS`, …) is
declared HERE and imported by the writer double in :mod:`pmquant.testing`
— one name, so the reader can never look for a spelling the writer
abandoned. When the real connectors land they import these names too.

Import cost: stdlib + dskit + this package's plan-time modules
(``books``, ``fees``, ``ladder.protocols``); nothing heavy, so documents
naming these kinds plan on a bare install.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from dskit.onboarding import AssetError, parse_utc
from dskit.pipeline.document import is_node_ref
from dskit.pipeline.libs.observations import ObservationRows
from dskit.pipeline.node import (
    Node,
    check_int_param,
    register_node_kind,
    reject_unknown_params,
)
from dskit.pipeline.records import cluster_of
from dskit.pipeline.split_policy import event_bounds_from_records

from .books import market_record_from_epoch, records_from_pit_rows
from .ladder.protocols import (
    DEFAULT_DUR_CAPS_H,
    DEFAULT_MIN_ABS_LEAD_S,
    LEAD_FRACS,
    VENUES,
    LeadGrid,
    lead_key,
    scope_to_venue,
    venue_of,
)

__all__ = [
    "AUTO_UNIVERSE",
    "Inventory",
    "LadderSource",
    "MARKETS_STREAM",
    "MARKET_KEY_FIELDS",
    "MARKET_SHARED_FIELDS",
    "NODE_KINDS",
    "OPEN_RESULT",
    "PIT_KEY_FIELDS",
    "PIT_SHARED_FIELDS",
    "PIT_STREAM",
    "PIT_TS_FIELD",
    "REFUSED_UNIVERSES",
    "SETTLED_RESULTS",
    "Settlement",
    "datetime_to_ms",
    "iso_to_ms",
    "universe_problems",
]

#: The point-in-time ledger stream: one row per contract per decision
#: epoch. The ``stream`` knob's default on ``pmquant-ladder-source``.
PIT_STREAM = "pit"

#: The settlement/strike stream: one row per contract, the parent's
#: 14-column schema. The ``stream`` knob's default on ``pmquant-settlement``.
MARKETS_STREAM = "markets"

#: The ledger's dedup key — a fact about the stream, never a document
#: knob: a contract is read once per epoch, and a lead and a settle can
#: share an instant.
PIT_KEY_FIELDS = ("contract_ticker", "epoch_ts_ms", "kind")

#: The ledger field carrying each row's decision instant (epoch ms).
PIT_TS_FIELD = "epoch_ts_ms"

#: Ledger fields whose values repeat heavily — interned by the scan.
PIT_SHARED_FIELDS = ("series", "event_ticker", "contract_ticker")

#: The settlement store's dedup key: one row per contract.
MARKET_KEY_FIELDS = ("ticker",)

#: Settlement fields whose values repeat heavily — interned by the scan.
MARKET_SHARED_FIELDS = ("series_ticker", "event_ticker")

#: The ``instruments`` spelling meaning "every series the stream holds"
#: (scoped to the venue on the ladder source) — the knob's default.
AUTO_UNIVERSE = "auto"

#: Universe spellings refused by name: they read like "everything" but
#: are not the declared spelling, and a silent acceptance would make two
#: spellings of one universe hash differently.
REFUSED_UNIVERSES = ("all", "*")

#: The settled results and the bool each maps to. A result outside this
#: table that is not :data:`OPEN_RESULT` refuses — never a guessed label.
SETTLED_RESULTS = {"yes": True, "no": False}

#: The result an OPEN (unsettled) contract carries: absent from the
#: outcomes map, never ``False``.
OPEN_RESULT = ""

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def datetime_to_ms(when):
    """Convert an aware datetime to integer epoch milliseconds, exactly.

    Integer timedelta arithmetic, never ``int(when.timestamp() * 1000)``:
    the float round-trip lands ms-precision stamps one ms off in some
    decades, and a stored close instant must read back as itself.

    Parameters
    ----------
    when : datetime.datetime
        An aware datetime.

    Returns
    -------
    int
        Milliseconds since the Unix epoch; a sub-ms remainder floors.
    """
    delta = when - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000 + delta.microseconds // 1000


def iso_to_ms(text):
    """Convert an ISO-8601 instant (naive = UTC) to integer epoch milliseconds.

    Parameters
    ----------
    text : str
        ``"2026-01-05T00:00:00Z"``, ``"2026-01-05"``, or any spelling
        :func:`dskit.onboarding.parse_utc` reads.

    Returns
    -------
    int
        Milliseconds since the Unix epoch.

    Raises
    ------
    AssetError
        When ``text`` is not an ISO date/datetime.
    """
    return datetime_to_ms(parse_utc(text))


def universe_problems(value):
    """List problems with an ``instruments`` declaration, empty when none.

    The one rule both readers apply: :data:`AUTO_UNIVERSE`, or a non-empty
    list of distinct series tickers; the look-alikes in
    :data:`REFUSED_UNIVERSES` and an empty list are refused by name.

    Parameters
    ----------
    value : object
        The declared knob (or a wired input).

    Returns
    -------
    list of str
        One message per problem.
    """
    if isinstance(value, str):
        if value == AUTO_UNIVERSE:
            return []
        if value in REFUSED_UNIVERSES:
            return [
                f"instruments {value!r} is refused — spell the whole stream as "
                f"{AUTO_UNIVERSE!r}, or list the series explicitly"
            ]
        return [
            f"instruments must be {AUTO_UNIVERSE!r} or a non-empty list of series "
            f"tickers, got {value!r}"
        ]
    if not isinstance(value, (list, tuple)) or not value:
        return [
            f"instruments must be {AUTO_UNIVERSE!r} or a non-empty list of series "
            f"tickers, got {value!r}"
        ]
    problems = [
        f"instruments entries must be non-empty strings, got {name!r}"
        for name in value
        if not isinstance(name, str) or not name
    ]
    if not problems and len(set(value)) != len(value):
        problems.append(f"instruments must not repeat a series, got {list(value)!r}")
    return problems


def _resolve_universe(declared, present, *, venue, where, stream):
    """Settle the universe against the series the stream holds, or refuse by name."""
    if declared == AUTO_UNIVERSE:
        universe = sorted(present) if venue is None else scope_to_venue(present, venue)
        if not universe:
            raise ValueError(
                f"{where}: stream {stream!r} holds no "
                f"{'series' if venue is None else venue + ' series'} — an empty "
                "universe is a wiring defect (wrong stream, wrong venue, nothing "
                f"acquired yet), never an empty result (the stream holds "
                f"{sorted(present)[:8]})"
            )
        return universe
    missing = sorted(name for name in declared if name not in present)
    if missing:
        raise ValueError(
            f"{where}: instruments {missing} read ZERO rows from the stream — a "
            "declared series with no data is a defect to report, not an empty "
            f"universe (the stream holds {sorted(present)[:8]}"
            f"{' ...' if len(present) > 8 else ''})"
        )
    return sorted(declared)


def _with_stream_default(params, default):
    """Copy ``params`` with the ``stream`` default filled in (a declared value stands)."""
    return {**params, "stream": params.get("stream", default)}


def _series_of_pit_row(row):
    """Name a ledger row's series: its ``series`` field, else the contract's first segment."""
    return row.get("series") or str(row.get("contract_ticker", "")).split("-", 1)[0]


def _series_of_market_row(row):
    """Name a settlement row's series: ``series_ticker``, else the ticker's first segment."""
    return row.get("series_ticker") or str(row.get("ticker", "")).split("-", 1)[0]


def _field(record, name, default=None):
    """Read ``name`` attr-or-key, so envelopes and dict rows share one path."""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


class LadderSource(ObservationRows):
    """Emit one venue's point-in-time ladder records — the ``pmquant-ladder-source`` kind.

    Role ``data``. The toolkit's :class:`~dskit.pipeline.libs.observations.ObservationRows`
    owns the scan; this class fixes the ledger's vocabulary (so none of
    it is a document knob) and projects each row through
    :func:`~pmquant.books.records_from_pit_rows` and
    :func:`~pmquant.books.market_record_from_epoch` — the ONE book stack.
    Records are emitted in the scan's key order
    (``contract_ticker, epoch_ts_ms, kind``), RAW: an unusable epoch rides
    along with its ``reason``.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED) — the onboarding root; ``source`` (str,
        REQUIRED) — the registered source name; ``venue`` (str, REQUIRED,
        one of :data:`~pmquant.ladder.protocols.VENUES`) — the venue this
        source speaks for, which also decides which book encoding the
        stack expects; ``stream`` (str, default :data:`PIT_STREAM`);
        ``instruments`` (default :data:`AUTO_UNIVERSE` — every series the
        stream holds that the venue owns, refusing a series no venue
        claims — or an explicit list of distinct series, each of this
        venue, each of which must read at least one row).

    Examples
    --------
    Every Kalshi series the acquired ledger holds::

        node = LadderSource(
            "ladder_records",
            {"root": "./ob", "source": "predexon", "venue": "kalshi"},
        )
        node.fingerprint()["instruments"]   # ['KXHIGHNY', 'KXLOWTDEN', ...]
        out = node.run(ctx, {})
        # -> {"records": [MarketRecord(..., native=DecisionEpochRecord(...)), ...],
        #     "instruments": ['KXHIGHNY', ...]}
    """

    role = "data"
    outputs = ("records", "instruments")
    supported_split_kinds = ("time", "trailing")

    #: The knobs this kind exposes — the scan-shape knobs the pack offers
    #: (``key_fields``, ``ts_field``, ``ts_unit``, ``ts_out``,
    #: ``shared_fields``, ``since_ms``) are FACTS about the ledger,
    #: answered by the accessors below, and so are not declarable.
    _PARAMS = ("root", "source", "stream", "venue", "instruments")

    #: The resolved universe — set per INSTANCE by the first scan.
    _universe = None

    def stream(self):
        """Name the stream read — :data:`PIT_STREAM` unless the document says otherwise."""
        return self.params.get("stream", PIT_STREAM)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The pack's problems (unknown knobs, ``root``/``source``/
            ``stream`` shape), plus this kind's: an unknown ``venue``, a
            malformed universe, or an explicit instrument the venue does
            not own.
        """
        problems = super().validate_params(_with_stream_default(params, PIT_STREAM))
        venue = params.get("venue")
        if venue not in VENUES:
            problems.append(
                f"venue is required and must be one of {list(VENUES)}, got {venue!r}"
            )
        declared = params.get("instruments", AUTO_UNIVERSE)
        universe = universe_problems(declared)
        problems.extend(universe)
        if not universe and venue in VENUES and isinstance(declared, (list, tuple)):
            for name in declared:
                owner = venue_of(name, default=None)
                if owner != venue:
                    problems.append(
                        f"instrument {name!r} belongs to venue {owner!r}, not "
                        f"{venue!r} — a source speaks for one venue"
                        if owner is not None
                        else f"instrument {name!r} matches no declared venue prefix"
                    )
        return problems

    # -- the ledger's vocabulary, fixed -----------------------------------

    def key_fields(self):
        """Name the ledger's dedup key (tuple) — :data:`PIT_KEY_FIELDS`."""
        return PIT_KEY_FIELDS

    def ts_field(self):
        """Name the ledger's instant field (str) — :data:`PIT_TS_FIELD`."""
        return PIT_TS_FIELD

    def ts_unit(self):
        """Say the instant is already epoch milliseconds (str)."""
        return "ms"

    def shared_fields(self):
        """Name the interned fields (tuple) — :data:`PIT_SHARED_FIELDS`."""
        return PIT_SHARED_FIELDS

    # -- the projection -----------------------------------------------------

    def project(self, records):
        """Keep the universe's rows and wrap each in the child's envelope.

        Parameters
        ----------
        records : list of dict
            The deduplicated ledger rows, ``asof_ms`` stamped.

        Returns
        -------
        list of MarketRecord
            One envelope per kept row, in input order, each carrying its
            :class:`~pmquant.books.DecisionEpochRecord` as ``native``.

        Raises
        ------
        ValueError
            When an explicit instrument reads no rows, or (under
            :data:`AUTO_UNIVERSE`) a series in the stream matches no
            declared venue, or the venue owns no series in the stream —
            an empty universe is a wiring defect, never an empty result.
        """
        venue = self.params["venue"]
        present = {_series_of_pit_row(row) for row in records}
        universe = _resolve_universe(
            self.params.get("instruments", AUTO_UNIVERSE),
            present,
            venue=venue,
            where=self.key,
            stream=self.stream(),
        )
        keep = set(universe)
        rows = [row for row in records if _series_of_pit_row(row) in keep]
        natives = records_from_pit_rows(rows, venue, self.params["source"])
        self._universe = tuple(universe)
        return [market_record_from_epoch(venue, rec) for rec in natives]

    def instruments(self):
        """Give the resolved universe, sorted (list of str)."""
        self._scan()
        return list(self._universe)

    def fingerprint(self):
        """Answer the stream's identity plus the universe it is read for.

        Returns
        -------
        dict
            The pack's ``{"kind", "rows", "sha256"}`` — the digest covers
            the WHOLE stream, the safe direction — plus ``instruments``,
            so two documents reading one stream for two universes never
            share a run identity.
        """
        fp = super().fingerprint()
        fp["instruments"] = self.instruments()
        return fp

    def data_edge(self):
        """Give the newest SETTLE epoch, or ``None`` when nothing has settled.

        Returns
        -------
        int or None
            The maximum ``asof_ms`` over settle epochs. Open contracts
            trail the ledger, so a trailing split anchored on the newest
            lead would count its windows from a book nobody can settle.
        """
        edge = None
        for record in self._scan():
            if record.native.epoch_kind != "settle":
                continue
            edge = record.asof_ms if edge is None else max(edge, record.asof_ms)
        return edge

    def event_bounds(self):
        """Give each event's observed extent, for the event-close policy.

        Returns
        -------
        dict
            ``event_ticker -> EventBounds`` over the emitted records, via
            :func:`~dskit.pipeline.split_policy.event_bounds_from_records`.
        """
        return event_bounds_from_records(self._scan())

    def run(self, ctx, inputs):
        """Emit the memoized snapshot and its universe.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused — a source reads only its params.
        inputs : dict
            Empty: role ``data`` takes no inputs.

        Returns
        -------
        dict
            ``{"records": [MarketRecord, ...], "instruments": [str, ...]}``.
        """
        records = self._scan()
        self.log.info(
            "emitting %d %s ladder record(s) over %d series",
            len(records),
            self.params["venue"],
            len(self._universe),
        )
        return {"records": records, "instruments": self.instruments()}


class Settlement(ObservationRows):
    """Emit the settled-YES map and the strike rows — the ``pmquant-settlement`` kind.

    Role ``labels``. The store is the parent's 14-column settlement/strike
    schema (``ticker``, ``event_ticker``, ``series_ticker``,
    ``strike_type``, ``floor_strike``, ``cap_strike``, ``status``,
    ``result``, ``open_time``, ``close_time``, ``yes_sub_title``,
    ``yes_bid``, ``yes_ask``, ``last_price``); every emitted row also
    carries ``open_ms``/``close_ms``, the ISO instants as epoch ms. The
    fingerprint covers the WHOLE stream — a labels node learns its
    universe at execute, so identity must not depend on it.

    Parameters
    ----------
    params : dict
        ``root`` (str, REQUIRED); ``source`` (str, REQUIRED); ``stream``
        (str, default :data:`MARKETS_STREAM`); ``instruments`` (default
        :data:`AUTO_UNIVERSE` — every series the store holds — or an
        explicit list of distinct series, each of which must read rows).
        A wired ``instruments`` input (a ladder source's universe, a
        gate's family) WINS over the param.

    Examples
    --------
    Labels for exactly the series a ladder source reads::

        node = Settlement("settlements", {"root": "./ob", "source": "kalshi"})
        out = node.run(ctx, {"instruments": ["KXHIGHNY"]})
        # -> {"outcomes": {"KXHIGHNY-26JAN05-T50": True, ...},
        #     "rows": [{"ticker": ..., "close_ms": ..., ...}, ...],
        #     "instruments": ["KXHIGHNY"],
        #     "metrics": {"n_settled": ..., "n_instruments": 1, "n_rows": ...}}
    """

    role = "labels"
    outputs = ("outcomes", "rows", "instruments", "metrics")

    #: The knobs this kind exposes; the scan shape is fixed by the
    #: accessors below (see :class:`LadderSource`).
    _PARAMS = ("root", "source", "stream", "instruments")

    def stream(self):
        """Name the stream read — :data:`MARKETS_STREAM` unless the document says otherwise."""
        return self.params.get("stream", MARKETS_STREAM)

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            The pack's problems plus a malformed ``instruments``.
        """
        problems = super().validate_params(_with_stream_default(params, MARKETS_STREAM))
        problems.extend(universe_problems(params.get("instruments", AUTO_UNIVERSE)))
        return problems

    # -- the store's vocabulary, fixed --------------------------------------

    def key_fields(self):
        """Name the store's dedup key (tuple) — :data:`MARKET_KEY_FIELDS`."""
        return MARKET_KEY_FIELDS

    def ts_field(self):
        """Declare no instant field: settlement rows are not a time series (None)."""
        return None

    def shared_fields(self):
        """Name the interned fields (tuple) — :data:`MARKET_SHARED_FIELDS`."""
        return MARKET_SHARED_FIELDS

    # -- the projection -----------------------------------------------------

    def project(self, records):
        """Stamp ``open_ms``/``close_ms`` onto every row, in place.

        Parameters
        ----------
        records : list of dict
            The deduplicated settlement rows.

        Returns
        -------
        list of dict
            The same rows, each with its ISO ``open_time``/``close_time``
            also spelled as epoch milliseconds.

        Raises
        ------
        ValueError
            When a row's ``open_time`` or ``close_time`` is not an ISO
            instant, naming the ticker.
        """
        for row in records:
            for field in ("open_time", "close_time"):
                try:
                    row[field.replace("_time", "_ms")] = iso_to_ms(row.get(field))
                except AssetError as exc:
                    raise ValueError(
                        f"{self.key}: market {row.get('ticker')!r} carries an "
                        f"unreadable {field}: {exc}"
                    ) from exc
        return records

    def validate_inputs(self, inputs):
        """List problems with the optional ``instruments`` input, empty when none.

        Parameters
        ----------
        inputs : dict
            Possibly carrying ``instruments`` — a list of distinct series.

        Returns
        -------
        list of str
            One problem when the wired universe is not a non-empty list of
            distinct series tickers (a one-shot iterable is refused, never
            consumed).
        """
        wired = inputs.get("instruments")
        if wired is None:
            return []
        problems = universe_problems(wired)
        if isinstance(wired, str):
            problems = [
                f"a wired instruments input must be a list of series tickers, "
                f"got {wired!r}"
            ]
        return problems

    def _outcomes(self, rows):
        """Map settled tickers to settled-YES; skip open ones; refuse anything else."""
        outcomes = {}
        for row in rows:
            raw = row.get("result")
            result = OPEN_RESULT if raw is None else str(raw).strip().lower()
            if result == OPEN_RESULT:
                continue
            if result not in SETTLED_RESULTS:
                raise ValueError(
                    f"{self.key}: market {row.get('ticker')!r} carries result "
                    f"{raw!r} — a settlement is {sorted(SETTLED_RESULTS)} or "
                    f"{OPEN_RESULT!r} (open); anything else is a store defect to "
                    "report, not a label to guess"
                )
            outcomes[row["ticker"]] = SETTLED_RESULTS[result]
        return outcomes

    def run(self, ctx, inputs):
        """Resolve the universe, then emit its outcomes and rows.

        Parameters
        ----------
        ctx : NodeContext
            The run frame; unused.
        inputs : dict
            Optionally ``instruments`` — the wired universe, which wins
            over the param.

        Returns
        -------
        dict
            ``outcomes`` — ``{ticker: settled-YES?}`` for SETTLED contracts
            only; ``rows`` — the universe's settlement rows; ``instruments``
            — the sorted universe; ``metrics`` — ``n_settled``,
            ``n_instruments``, ``n_rows``.

        Raises
        ------
        ValueError
            When a declared or wired instrument reads no rows, or a row
            carries a result outside the vocabulary.
        """
        rows_all = self._scan()
        wired = inputs.get("instruments") if inputs else None
        declared = (
            list(wired) if wired is not None
            else self.params.get("instruments", AUTO_UNIVERSE)
        )
        present = {_series_of_market_row(row) for row in rows_all}
        universe = _resolve_universe(
            declared, present, venue=None, where=self.key, stream=self.stream()
        )
        keep = set(universe)
        rows = [row for row in rows_all if _series_of_market_row(row) in keep]
        outcomes = self._outcomes(rows)
        self.log.info(
            "settlement: %d of %d contract(s) settled over %d series",
            len(outcomes),
            len(rows),
            len(universe),
        )
        return {
            "outcomes": outcomes,
            "rows": rows,
            "instruments": universe,
            "metrics": {
                "n_settled": len(outcomes),
                "n_instruments": len(universe),
                "n_rows": len(rows),
            },
        }


class Inventory(Node):
    """Count settled / usable / two-sided / tradeable events per series.

    Role ``report`` — the ``pmquant-inventory`` kind. Every count is over
    SETTLED events (every contract of the event in ``outcomes``), at the
    DECLARED lead grid: an event is ``usable`` when at every declared lead
    at least one of its contracts has a book (either side present),
    ``two_sided`` when at every lead at least one has both sides, and
    ``tradeable`` when at every lead at least one carries the ledger's own
    ``usable`` flag. ``eligible`` is the coverage mark
    ``usable >= min_events`` — a report, never the gate a run applies
    (that is ``eligibility``'s job). Records without a native ladder book
    are skipped and counted, never crashed on.

    Parameters
    ----------
    params : dict
        ``min_events`` (int >= 1, REQUIRED — the bar must be stated);
        ``lead_fracs`` (list of float, default
        :data:`~pmquant.ladder.protocols.LEAD_FRACS` — the leads a
        usable event must carry, validated as a lead grid).

    Examples
    --------
    The parent's bar, over one venue's records and its settlements::

        node = Inventory("inventory", {"min_events": 50})
        out = node.run(ctx, {"records": records, "outcomes": outcomes})
        # -> {"inventory": {"KXHIGHNY": {"events_settled": 76, "usable": 76,
        #                                 "two_sided": 2, "tradeable": 2,
        #                                 "eligible": True}, ...},
        #     "metrics": {"n_series": 29, "n_eligible": 23}}
    """

    role = "report"
    outputs = ("inventory", "metrics")

    #: The class's own knobs — anything else is refused by name.
    _PARAMS = ("min_events", "lead_fracs")

    @classmethod
    def validate_params(cls, params):
        """List problems with ``params``, empty when none.

        Parameters
        ----------
        params : dict
            The node's declared params.

        Returns
        -------
        list of str
            One problem per unknown knob, a missing or non-positive
            ``min_events``, or a ``lead_fracs`` that is not a lead grid.
        """
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        if "min_events" not in params:
            problems.append(
                "min_events is required — the coverage bar must be stated "
                "explicitly, there is no default"
            )
        elif not is_node_ref(params["min_events"]):
            check_int_param(problems, "min_events", params["min_events"], ge=1)
        fracs = params.get("lead_fracs", LEAD_FRACS)
        if not is_node_ref(fracs):
            problems.extend(
                LeadGrid.problems(fracs, DEFAULT_DUR_CAPS_H, DEFAULT_MIN_ABS_LEAD_S)
            )
        return problems

    def validate_inputs(self, inputs):
        """List problems with the materialized inputs, empty when none.

        Parameters
        ----------
        inputs : dict
            ``records`` (a list of ladder envelopes) and ``outcomes`` (the
            ``{contract: settled-YES?}`` map).

        Returns
        -------
        list of str
            One problem per port of the wrong shape; a one-shot iterable
            is refused by name, never walked.
        """
        problems = []
        if not isinstance(inputs.get("records"), list):
            problems.append(
                f"records must be a list of ladder records, got {inputs.get('records')!r}"
            )
        if not isinstance(inputs.get("outcomes"), dict):
            problems.append(
                f"outcomes must be a dict of contract -> settled-YES?, got "
                f"{inputs.get('outcomes')!r}"
            )
        return problems

    def _lead_layers(self, records, fracs):
        """Group records by series and event into per-lead layer flags."""
        wanted = set(fracs)
        per_series = {}
        skipped = 0
        for record in records:
            native = _field(record, "native")
            yes_levels = getattr(native, "yes_levels", None)
            no_levels = getattr(native, "no_levels", None)
            if yes_levels is None or no_levels is None:
                skipped += 1
                continue
            series = _field(record, "instrument")
            event = cluster_of(record)
            contract = _field(record, "contract")
            info = per_series.setdefault(series, {}).setdefault(
                event, {"contracts": set(), "leads": {}}
            )
            info["contracts"].add(contract)
            lead_frac = _field(record, "lead_frac")
            if lead_frac is None:
                continue
            key = lead_key(lead_frac)
            if key not in wanted:
                continue
            layer = info["leads"].setdefault(key, {"book": False, "both": False, "usable": False})
            layer["book"] |= bool(yes_levels or no_levels)
            layer["both"] |= bool(yes_levels and no_levels)
            layer["usable"] |= bool(_field(record, "usable", False))
        return per_series, skipped

    def run(self, ctx, inputs):
        """Count the layers per series, write ``inventory.json``, and report.

        Parameters
        ----------
        ctx : NodeContext
            The run frame — the artifact lands under its run dir.
        inputs : dict
            As validated by :meth:`validate_inputs`.

        Returns
        -------
        dict
            ``inventory`` — ``{series: {events_settled, usable, two_sided,
            tradeable, eligible}}``; ``metrics`` — ``n_series``,
            ``n_eligible``.
        """
        min_events = self.params["min_events"]
        fracs = tuple(lead_key(f) for f in self.params.get("lead_fracs", LEAD_FRACS))
        outcomes = inputs["outcomes"]
        per_series, skipped = self._lead_layers(inputs["records"], fracs)
        inventory = {}
        for series in sorted(per_series):
            counts = {"events_settled": 0, "usable": 0, "two_sided": 0, "tradeable": 0}
            for info in per_series[series].values():
                contracts = info["contracts"]
                if not contracts or any(c not in outcomes for c in contracts):
                    continue
                counts["events_settled"] += 1
                leads = info["leads"]
                for name, flag in (("usable", "book"), ("two_sided", "both"), ("tradeable", "usable")):
                    if all(leads.get(f, {}).get(flag, False) for f in fracs):
                        counts[name] += 1
            inventory[series] = {**counts, "eligible": counts["usable"] >= min_events}
        n_eligible = sum(1 for row in inventory.values() if row["eligible"])
        self.log.info(
            "inventory: %d/%d series eligible (usable >= %s); %d record(s) without a "
            "ladder book skipped",
            n_eligible,
            len(inventory),
            min_events,
            skipped,
        )
        self.write_artifact(
            ctx,
            "inventory.json",
            {"min_events": min_events, "lead_fracs": list(fracs), "series": inventory},
        )
        return {
            "inventory": inventory,
            "metrics": {"n_series": len(inventory), "n_eligible": n_eligible},
        }


#: kind name -> class: what the registry, the conformance suite, and a
#: document's ``uses`` all key off. Prefixed with the child's name — the
#: registry is shared.
NODE_KINDS = {
    "pmquant-ladder-source": LadderSource,
    "pmquant-settlement": Settlement,
    "pmquant-inventory": Inventory,
}

# Import = registration (``owned`` deliberately NOT set): the moment this
# module imports — ``import pmquant``, or ``--adapter pmquant`` on the CLI
# — every kind above resolves by name.
for _name, _cls in NODE_KINDS.items():
    register_node_kind(_name, _cls)

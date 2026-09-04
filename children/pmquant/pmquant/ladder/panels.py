"""Event panels and the frozen token features — records -> the model's tensor.

The ladder model's example is one EVENT: its rungs on one axis, the lead
grid on the other, and in every cell the point-in-time book the recorder
read there. This module is the bridge from the child's record envelopes
to that tensor, in three steps that stay separate because each is
audited on its own:

* :func:`build_panels` groups lead-epoch records by event into
  :class:`EventPanel` objects — rungs in :func:`~pmquant.ladder.protocols.
  rung_sort_key` order (load-bearing: the settlement head runs each
  threshold tail as one contiguous run), the settled label per rung, the
  ladder geometry, and the strike features. Events that cannot be
  labelled or are too thin are SKIPPED AND COUNTED, never fabricated.
* :class:`TokenFeaturizer` turns one panel into ``feats (T, C, F)`` and
  ``seen (T, C)``. The 41-column order (at ``k_lvl`` 5) is FROZEN: a
  trained checkpoint's input layer is indexed by it, so reordering a
  column silently invalidates every artifact. Every side block is built
  from the EXECUTABLE ask ladder of that side, which is the mirror of the
  opposite side's resting bids (:func:`pmquant.books.asks_from_bids` —
  the one implementation of the mirror rule).
* :func:`build_panel_items` / :func:`collate_items` produce the dict
  items the torch adapter consumes and pad them on the contract axis.

The market vocabulary (:class:`MarketVocab`) is ``tuple(sorted(set(
series)))``, indexed by position. ADDING a series shifts every index at
or after the insertion point, so a checkpoint is only meaningful against
the vocab it was trained with — the vocab travels with the artifact
(``n_markets`` in the sidecar, the ``vocab`` output of the panels node).

Import cost: stdlib + :mod:`pmquant.books` + :mod:`pmquant.ladder.protocols`.
numpy and torch are imported strictly inside the functions that need
them — a document naming the panels node must plan with neither installed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from pmquant.books import asks_from_bids
from pmquant.ladder.protocols import STRIKE_CODES, LadderType, rung_sort_key, venue_of

__all__ = [
    "DEFAULT_K_LVL",
    "DEFAULT_MIN_CONTRACTS",
    "ITEM_KEYS",
    "PANEL_KEYS",
    "SOURCE_VENUE",
    "TAIL_GROUPS",
    "TAIL_NAMES",
    "EventPanel",
    "MarketVocab",
    "PanelBuild",
    "TokenFeaturizer",
    "build_panel_items",
    "build_panels",
    "collate_items",
    "side_feature_names",
]

#: Book levels per executable side entering a token — the frozen recipe.
DEFAULT_K_LVL = 5

#: An event with fewer rungs than this is not a ladder and is skipped.
DEFAULT_MIN_CONTRACTS = 2

#: The venue whose books carry ``source_f = 1.0``; every other declared
#: venue reads 0.0. Read through the venue TABLE, never a ticker prefix.
SOURCE_VENUE = "polymarket"

#: The E5 tail column names, in frozen order (base offset ``2 * side_f + 1``).
TAIL_NAMES = (
    "strike_z",
    "gap_z",
    "rung_pos",
    "log_n_contracts",
    "lead_frac",
    "dur_h",
    "ladder_mass",
    "ladder_center",
    "ladder_entropy",
    "source_f",
    "hour_sin",
    "hour_cos",
)

#: The ablation axis: group name -> the TAIL offsets it owns (0-based
#: within the tail). Interleaved rather than contiguous because the
#: layout is frozen by the checkpoints it trained.
TAIL_GROUPS = {
    "strike": (0, 1, 2, 3),
    "ladder": (6, 7, 8),
    "context": (4, 5, 9, 10, 11),
}

#: The keys a panel item MUST carry for the torch adapter to consume it.
#: ``featurizer`` is the layout identity (:attr:`TokenFeaturizer.identity`)
#: the model refuses to score under a different one; ``vocab`` is the
#: ``{series: market_id}`` map the items were indexed by, which the
#: adapter persists beside the artifact and checks every item against.
PANEL_KEYS = (
    "feats",
    "seen",
    "visible",
    "y",
    "market_id",
    "is_partition",
    "st_code",
    "eligible",
    "contracts",
    "lead_fracs",
    "featurizer",
    "vocab",
)

#: Every key :func:`build_panel_items` emits — :data:`PANEL_KEYS` plus the
#: provenance and the executable touches the prediction frame carries.
ITEM_KEYS = PANEL_KEYS + (
    "series",
    "event",
    "close_ts_ms",
    "asks",
    "asks_no",
    "ask_sz",
    "bid_sz",
)

_MS_PER_HOUR = 3.6e6


def side_feature_names(prefix, k_lvl):
    """Name one executable side's block, in frozen order.

    Parameters
    ----------
    prefix : str
        ``"yes"`` or ``"no"`` — the side whose ASKS the block describes.
    k_lvl : int
        Book levels entering the block.

    Returns
    -------
    tuple of str
        ``present, touch, off1..offK, logdep1..logdepK, n_levels,
        log_depth`` — ``2 * k_lvl + 4`` names.
    """
    return (
        f"{prefix}_present",
        f"{prefix}_touch",
        *(f"{prefix}_off{i + 1}" for i in range(k_lvl)),
        *(f"{prefix}_logdep{i + 1}" for i in range(k_lvl)),
        f"{prefix}_n_levels",
        f"{prefix}_log_depth",
    )


@dataclass(frozen=True, slots=True)
class MarketVocab:
    """The ``series -> embedding index`` map a checkpoint was trained with.

    Sorted by series name, so the map is reproducible for a FIXED series
    set — and fragile across a CHANGING one: adding or dropping a single
    series shifts every index at or after the insertion point, and a
    checkpoint scored against a different vocab silently swaps market
    embeddings. The vocab therefore travels with the artifact, and a
    document never restates it.

    Parameters
    ----------
    series : tuple of str
        Series tickers in embedding-index order; index ``i`` is ``series[i]``.

    Examples
    --------
    Build one from the series a run saw, and look a series up::

        vocab = MarketVocab.from_series(["KXB", "KXA"])
        vocab.series          # ('KXA', 'KXB')
        vocab.index("KXB")    # 1
    """

    series: tuple

    @classmethod
    def from_series(cls, series):
        """Build the vocab by sorting the distinct series names.

        Parameters
        ----------
        series : iterable of str
            Series tickers; duplicates collapse.

        Returns
        -------
        MarketVocab
            The sorted vocab.
        """
        return cls(tuple(sorted(set(str(s) for s in series))))

    def __len__(self):
        """Count the series in the vocab."""
        return len(self.series)

    def index(self, series):
        """Give the embedding index of one series.

        Parameters
        ----------
        series : str
            A series ticker.

        Returns
        -------
        int
            Its position in :attr:`series`.

        Raises
        ------
        ValueError
            When the series is not in the vocab — an unknown series has no
            trained embedding, and mapping it to index 0 would attribute
            another market's learned offset to it.
        """
        try:
            return self.series.index(series)
        except ValueError:
            raise ValueError(
                f"series {series!r} is not in the market vocab {list(self.series)}"
            ) from None

    def to_dict(self):
        """Give the map as ``{series: index}`` — the panels node's ``vocab``.

        Returns
        -------
        dict
            Series -> embedding index.
        """
        return {name: i for i, name in enumerate(self.series)}


@dataclass(slots=True)
class EventPanel:
    """One event's rungs x leads of point-in-time books, model-ready.

    Built by :func:`build_panels`; consumed by :class:`TokenFeaturizer`.
    Arrays are numpy (``float32`` / ``int64``), the cell maps are keyed by
    ``(rung, step)`` with ``rung`` the position in :attr:`contracts` and
    ``step`` the position on the lead grid.

    Parameters
    ----------
    series : str
        The series ticker (the instrument).
    event : str
        The event ticker (the dependence cluster).
    market_id : int
        The series' index in the run's :class:`MarketVocab`.
    close_ts_ms : int
        The event's close instant, epoch ms — the instant a split cuts on.
    ladder_type : LadderType
        The strike geometry the settlement head follows.
    contracts : list of str
        Rung tickers in canonical rung order.
    y : numpy.ndarray
        ``(C,) float32`` settled-YES labels.
    st_code : numpy.ndarray
        ``(C,) int64`` strike-type codes (:data:`~pmquant.ladder.protocols.STRIKE_CODES`).
    cells : dict
        ``(rung, step) -> (yes_levels, no_levels)`` — the RESTING BID
        ladders of the record read there.
    epoch_ts : dict
        ``(rung, step) -> epoch ms`` of that record.
    staleness : dict
        ``(rung, step) -> staleness ms`` (0 when the record carried none).
    strike_z : numpy.ndarray
        ``(C,) float32`` per-rung strike z-score (zeros when undefined).
    gap_z : numpy.ndarray
        ``(C,) float32`` normalized strike-gap deltas (zeros when undefined).
    dur_h : float
        Hours from the first observed epoch to the close, floored at 0.
    source_f : float
        ``1.0`` for a :data:`SOURCE_VENUE` series, else ``0.0``.

    Examples
    --------
    Panels are built, not hand-assembled — from records, outcomes, the
    markets rows and a grid::

        built = build_panels(records, outcomes, markets, LeadGrid())
        panel = built.panels[0]
        panel.contracts          # rung order
        panel.cells[(0, 0)]      # (yes_levels, no_levels) of rung 0 at step 0
    """

    series: str
    event: str
    market_id: int
    close_ts_ms: int
    ladder_type: LadderType
    contracts: list
    y: object
    st_code: object
    cells: dict
    epoch_ts: dict
    staleness: dict
    strike_z: object
    gap_z: object
    dur_h: float
    source_f: float

    @property
    def n_contracts(self):
        """Count the rungs."""
        return len(self.contracts)


@dataclass(frozen=True, slots=True)
class PanelBuild:
    """What :func:`build_panels` returns: the panels, the vocab, the counts.

    Parameters
    ----------
    panels : list of EventPanel
        In ``(series, event)`` order.
    vocab : MarketVocab
        Over the series of the panels built.
    counts : dict
        ``n_events_seen``, ``n_panels``, ``n_skipped_unsettled``,
        ``n_skipped_min_contracts``, ``n_off_grid_rows``,
        ``n_skipped_non_lead`` — every drop, counted.

    Examples
    --------
    ::

        built = build_panels(records, outcomes, markets, LeadGrid())
        built.counts["n_skipped_unsettled"]   # 0 when every event settled
    """

    panels: list
    vocab: MarketVocab
    counts: dict


def _finite(value):
    """Say whether ``value`` is a finite real number (bool excluded)."""
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _strike_value(strike_type, floor, cap):
    """Give the one strike a rung is placed by: floor, cap, or their mean."""
    fl = float(floor) if _finite(floor) else float("nan")
    cp = float(cap) if _finite(cap) else float("nan")
    if strike_type == "greater":
        return fl
    if strike_type == "less":
        return cp
    finite = [v for v in (fl, cp) if math.isfinite(v)]
    return sum(finite) / len(finite) if finite else float("nan")


def _strike_geometry(values):
    """Compute ``(strike_z, gap_z)`` from the per-rung strike values."""
    import numpy as np

    sv = np.asarray(values, dtype=np.float64)
    finite = np.isfinite(sv)
    if finite.sum() >= 2 and np.nanstd(sv) > 0:
        z = (sv - np.nanmean(sv)) / np.nanstd(sv)
        gaps = np.diff(sv)
        gmed = float(np.nanmedian(np.abs(gaps)))
        if not math.isfinite(gmed) or gmed == 0.0:
            gmed = 1.0
        gz = np.append(gaps / gmed - 1.0, 0.0)
    else:
        z = np.zeros(len(sv))
        gz = np.zeros(len(sv))
    return np.nan_to_num(z).astype(np.float32), np.nan_to_num(gz).astype(np.float32)


def _native(record):
    """Unwrap a record envelope to the venue record, or take it as given."""
    native = getattr(record, "native", None)
    return record if native is None else native


def _market_index(markets):
    """Index the markets rows by contract ticker, refusing a malformed row."""
    by_ticker = {}
    for i, row in enumerate(markets):
        if not isinstance(row, dict) or not row.get("ticker"):
            raise ValueError(f"markets[{i}] must be a dict with a 'ticker', got {row!r}")
        by_ticker[str(row["ticker"])] = row
    return by_ticker


def _group_leads(records, counts):
    """Group lead-epoch natives by ``(series, event)``, counting the rest."""
    events = {}
    for record in records:
        native = _native(record)
        if getattr(native, "epoch_kind", None) != "lead":
            counts["n_skipped_non_lead"] += 1
            continue
        key = (str(native.series), str(native.event_ticker))
        events.setdefault(key, []).append(native)
    return events


def _rung_rows(series, event, natives, by_ticker):
    """Order the event's contracts canonically, with their markets rows."""
    tickers = sorted({str(n.contract_ticker) for n in natives})
    rows = []
    for ticker in tickers:
        row = by_ticker.get(ticker)
        if row is None:
            raise ValueError(
                f"event {event!r} ({series}): contract {ticker!r} has no markets row — "
                "a rung without a strike cannot be ordered; wire the same "
                "settlement store the records came from"
            )
        rows.append((ticker, row))
    rows.sort(
        key=lambda tr: (
            rung_sort_key(
                tr[1].get("strike_type"), tr[1].get("floor_strike"), tr[1].get("cap_strike")
            ),
            tr[0],
        )
    )
    return rows


def _panel_of(series, event, natives, rows, outcomes, grid, counts):
    """Assemble one event's panel, or ``None`` when it is not settled."""
    import numpy as np

    contracts = [ticker for ticker, _ in rows]
    y = []
    for ticker in contracts:
        outcome = outcomes.get(ticker)
        if outcome is None:
            counts["n_skipped_unsettled"] += 1
            return None
        y.append(1.0 if outcome else 0.0)
    strike_types = [row.get("strike_type") for _, row in rows]
    between = STRIKE_CODES["between"]
    st_code = np.asarray(
        [STRIKE_CODES.get(st, between) for st in strike_types], dtype=np.int64
    )
    strike_z, gap_z = _strike_geometry(
        [
            _strike_value(row.get("strike_type"), row.get("floor_strike"), row.get("cap_strike"))
            for _, row in rows
        ]
    )
    close_ts_ms = max(int(row["close_ms"]) for _, row in rows)
    rung_of = {ticker: r for r, ticker in enumerate(contracts)}
    cells, epoch_ts, staleness = {}, {}, {}
    for native in natives:
        step = grid.position(native.lead_frac)
        if step is None:
            counts["n_off_grid_rows"] += 1
            continue
        key = (rung_of[str(native.contract_ticker)], step)
        if key in cells:
            raise ValueError(
                f"event {event!r}: two records for contract {native.contract_ticker!r} "
                f"at lead {native.lead_frac!r} — one book per decision instant"
            )
        cells[key] = (tuple(native.yes_levels), tuple(native.no_levels))
        epoch_ts[key] = int(native.epoch_ts_ms)
        stale = native.staleness_ms
        staleness[key] = int(stale) if _finite(stale) else 0
    first = min(epoch_ts.values()) if epoch_ts else close_ts_ms
    return EventPanel(
        series=series,
        event=event,
        market_id=-1,
        close_ts_ms=close_ts_ms,
        ladder_type=LadderType.classify(strike_types),
        contracts=contracts,
        y=np.asarray(y, dtype=np.float32),
        st_code=st_code,
        cells=cells,
        epoch_ts=epoch_ts,
        staleness=staleness,
        strike_z=strike_z,
        gap_z=gap_z,
        dur_h=max(0.0, (close_ts_ms - first) / _MS_PER_HOUR),
        source_f=1.0 if venue_of(series, default=None) == SOURCE_VENUE else 0.0,
    )


def build_panels(records, outcomes, markets, grid, *, min_contracts=DEFAULT_MIN_CONTRACTS):
    """Group lead-epoch records into settled event panels.

    Parameters
    ----------
    records : iterable
        :class:`~dskit.pipeline.records.MarketRecord` envelopes whose
        ``native`` is a :class:`~pmquant.books.DecisionEpochRecord` (bare
        natives are accepted too). Only ``epoch_kind == "lead"`` records
        become cells; the rest are counted.
    outcomes : dict
        ``contract -> bool`` settled-YES map. An event with a contract
        missing here (or ``None``) is unsettled and is skipped.
    markets : iterable of dict
        Rows carrying ``ticker, event_ticker, series_ticker, strike_type,
        floor_strike, cap_strike, close_ms, open_ms``; strikes may be
        ``None``/NaN. Every contract in ``records`` needs a row.
    grid : LeadGrid
        The lead axis; a record whose fraction is off-grid is skipped and
        counted.
    min_contracts : int
        Events with fewer rungs are skipped and counted (default
        :data:`DEFAULT_MIN_CONTRACTS`).

    Returns
    -------
    PanelBuild
        Panels in ``(series, event)`` order, the vocab over their series,
        and the counts of everything dropped.

    Raises
    ------
    ValueError
        On a contract without a markets row, a malformed markets row, or
        two records for one contract at one lead.
    """
    counts = {
        "n_events_seen": 0,
        "n_panels": 0,
        "n_skipped_unsettled": 0,
        "n_skipped_min_contracts": 0,
        "n_off_grid_rows": 0,
        "n_skipped_non_lead": 0,
    }
    by_ticker = _market_index(markets)
    events = _group_leads(records, counts)
    panels = []
    for (series, event) in sorted(events):
        counts["n_events_seen"] += 1
        natives = events[(series, event)]
        rows = _rung_rows(series, event, natives, by_ticker)
        if len(rows) < int(min_contracts):
            counts["n_skipped_min_contracts"] += 1
            continue
        panel = _panel_of(series, event, natives, rows, outcomes, grid, counts)
        if panel is not None:
            panels.append(panel)
    vocab = MarketVocab.from_series(p.series for p in panels)
    for panel in panels:
        panel.market_id = vocab.index(panel.series)
    counts["n_panels"] = len(panels)
    return PanelBuild(panels=panels, vocab=vocab, counts=counts)


class TokenFeaturizer:
    """The frozen token layout: one event panel -> ``feats (T, C, F)``, ``seen (T, C)``.

    Per executable side (YES asks, then NO asks): presence, the touch,
    the top-``k_lvl`` price offsets from the touch, the top-``k_lvl`` log
    depths, the level count and the log total depth; then one staleness
    scalar; then the twelve-column tail (:data:`TAIL_NAMES`). At
    ``k_lvl`` 5 that is 41 columns, and the order is FROZEN — a trained
    input layer is indexed by it.

    Parameters
    ----------
    k_lvl : int
        Book levels per side entering the token (>= 1; default
        :data:`DEFAULT_K_LVL`).
    drop : str or tuple of str
        Ablation groups (:data:`TAIL_GROUPS` names) whose tail columns are
        zeroed LAST, after every feature is computed. Unknown names refuse
        at construction.

    Examples
    --------
    The frozen recipe, and the production ablation that drops ``context``::

        featurizer = TokenFeaturizer(k_lvl=5, drop=("context",))
        featurizer.n_features                 # 41
        feats, seen = featurizer.encode(panel, LeadGrid())
    """

    def __init__(self, k_lvl=DEFAULT_K_LVL, drop=()):
        if isinstance(k_lvl, bool) or not isinstance(k_lvl, int) or k_lvl < 1:
            raise ValueError(f"k_lvl must be an int >= 1, got {k_lvl!r}")
        drop = (drop,) if isinstance(drop, str) else tuple(drop or ())
        unknown = sorted(set(drop) - set(TAIL_GROUPS))
        if unknown:
            raise ValueError(
                f"drop names unknown ablation group(s) {unknown} — the groups are "
                f"{sorted(TAIL_GROUPS)}"
            )
        self.k_lvl = int(k_lvl)
        self.side_f = 2 * self.k_lvl + 4
        self.drop = tuple(sorted(set(drop)))
        self._names = (
            *side_feature_names("yes", self.k_lvl),
            *side_feature_names("no", self.k_lvl),
            "staleness",
            *TAIL_NAMES,
        )
        base = self.tail_base
        self._drop_cols = sorted(
            base + offset for group in self.drop for offset in TAIL_GROUPS[group]
        )

    @property
    def tail_base(self):
        """Give the column index where the tail begins (``2 * side_f + 1``)."""
        return 2 * self.side_f + 1

    @property
    def n_features(self):
        """Count the token columns (``F``)."""
        return len(self._names)

    @property
    def identity(self):
        """Give the layout identity ``(k_lvl, drop)`` — what a batch and a model must agree on."""
        return (self.k_lvl, self.drop)

    def feature_names(self):
        """Name every column, in frozen order.

        Returns
        -------
        tuple of str
            The ``F`` names.
        """
        return self._names

    def column(self, name):
        """Locate one named column.

        Parameters
        ----------
        name : str
            A feature name.

        Returns
        -------
        int
            Its column index.

        Raises
        ------
        ValueError
            On a name this layout does not carry.
        """
        try:
            return self._names.index(name)
        except ValueError:
            raise ValueError(f"no token feature named {name!r}") from None

    def _side(self, asks):
        """Featurize one executable ask ladder (ascending) into its block."""
        import numpy as np

        f = np.zeros(self.side_f, dtype=np.float32)
        if not asks:
            return f
        px = np.asarray([price for price, _ in asks], dtype=np.float64)
        sz = np.asarray([depth for _, depth in asks], dtype=np.float64)
        k = min(self.k_lvl, len(px))
        f[0], f[1] = 1.0, px[0]
        f[2 : 2 + k] = np.abs(px[:k] - px[0])
        f[2 + self.k_lvl : 2 + self.k_lvl + k] = np.log1p(sz[:k])
        f[self.side_f - 2] = len(px)
        f[self.side_f - 1] = math.log1p(float(sz.sum()))
        return f

    def encode(self, panel, grid):
        """Encode one panel on ``grid``.

        Parameters
        ----------
        panel : EventPanel
            The event.
        grid : LeadGrid
            The lead axis (``T = len(grid.lead_fracs)``).

        Returns
        -------
        tuple
            ``(feats, seen)`` — ``feats (T, C, F) float32`` and
            ``seen (T, C) bool`` (a real book on at least one side).
            Visibility (the running OR of ``seen`` along time) is derived
            downstream, so featurization stays per-cell pure.
        """
        import numpy as np

        T, C = len(grid.lead_fracs), panel.n_contracts
        base = self.tail_base
        feats = np.zeros((T, C, self.n_features), dtype=np.float32)
        seen = np.zeros((T, C), dtype=bool)
        a_yes = np.full((T, C), np.nan, dtype=np.float32)
        for (r, k), (yes_levels, no_levels) in panel.cells.items():
            fy = self._side(asks_from_bids(no_levels, where="no_levels"))
            fn = self._side(asks_from_bids(yes_levels, where="yes_levels"))
            feats[k, r, : self.side_f] = fy
            feats[k, r, self.side_f : 2 * self.side_f] = fn
            feats[k, r, base - 1] = math.log1p(panel.staleness.get((r, k), 0) / 1000.0)
            seen[k, r] = bool(fy[0] or fn[0])
            if fy[0]:
                a_yes[k, r] = fy[1]
        self._tail(feats, a_yes, panel, grid, base)
        if self._drop_cols:
            feats[:, :, self._drop_cols] = 0.0
        return feats, seen

    @staticmethod
    def _tail(feats, a_yes, panel, grid, base):
        """Fill the twelve tail columns in place."""
        import numpy as np

        T, C = feats.shape[0], feats.shape[1]
        for r in range(C):
            feats[:, r, base + 0] = panel.strike_z[r]
            feats[:, r, base + 1] = panel.gap_z[r]
            feats[:, r, base + 2] = r / max(C - 1, 1)
        feats[:, :, base + 3] = math.log1p(C)
        for k in range(T):
            feats[k, :, base + 4] = grid.lead_fracs[k]
        feats[:, :, base + 5] = math.log1p(panel.dur_h)
        for k in range(T):
            pk = a_yes[k]
            fin = np.isfinite(pk)
            if not fin.any():
                continue
            s = float(pk[fin].sum())
            feats[k, :, base + 6] = s
            if s > 0:
                pn = np.where(fin, pk, 0.0) / s
                nz = pn[pn > 0]
                feats[k, :, base + 7] = float((np.arange(C) * pn).sum() / max(C - 1, 1))
                feats[k, :, base + 8] = float(-(nz * np.log(nz)).sum() / math.log(max(len(nz), 2)))
        feats[:, :, base + 9] = panel.source_f
        for (r, k), ts in panel.epoch_ts.items():
            hfrac = ((ts / _MS_PER_HOUR) % 24.0) / 24.0
            feats[k, r, base + 10] = math.sin(2 * math.pi * hfrac)
            feats[k, r, base + 11] = math.cos(2 * math.pi * hfrac)


def build_panel_items(panels, featurizer, grid, eligible):
    """Turn panels into the dict items the torch adapter consumes.

    Parameters
    ----------
    panels : iterable of EventPanel
        The events.
    featurizer : TokenFeaturizer
        The token layout.
    grid : LeadGrid
        The lead axis.
    eligible : iterable of str
        The claims-universe series; sets each item's ``eligible`` flag.

    Returns
    -------
    list of dict
        One item per panel with exactly :data:`ITEM_KEYS`: ``feats (T, C,
        F) float32``, ``seen``/``visible (T, C) bool`` (``visible`` is the
        running OR of ``seen`` along time), ``y (C,) float32``,
        ``market_id`` (int), ``is_partition`` (bool), ``st_code (C,)
        int64``, ``eligible`` (bool), ``contracts`` (list), ``lead_fracs``
        (tuple), ``featurizer`` (the layout identity), ``vocab`` (the ONE
        ``{series: market_id}`` dict over these panels, shared by every
        item), ``series``, ``event``, ``close_ts_ms``, and the executable
        touches ``asks``/``asks_no (T, C)`` (NaN where the side is absent)
        with the touch depths ``ask_sz``/``bid_sz`` (0 where absent).
    """
    import numpy as np

    panels = list(panels)
    eligible = set(eligible)
    vocab = {str(panel.series): int(panel.market_id) for panel in panels}
    c_yp, c_yt, c_yd = (featurizer.column(n) for n in ("yes_present", "yes_touch", "yes_logdep1"))
    c_np, c_nt, c_nd = (featurizer.column(n) for n in ("no_present", "no_touch", "no_logdep1"))
    items = []
    for panel in panels:
        feats, seen = featurizer.encode(panel, grid)
        yes_here, no_here = feats[..., c_yp] > 0, feats[..., c_np] > 0
        items.append(
            {
                "feats": feats,
                "seen": seen,
                "visible": np.logical_or.accumulate(seen, axis=0),
                "y": panel.y,
                "market_id": int(panel.market_id),
                "is_partition": panel.ladder_type is LadderType.PARTITION,
                "st_code": panel.st_code,
                "eligible": panel.series in eligible,
                "contracts": list(panel.contracts),
                "lead_fracs": tuple(grid.lead_fracs),
                "featurizer": featurizer.identity,
                "vocab": vocab,
                "series": panel.series,
                "event": panel.event,
                "close_ts_ms": int(panel.close_ts_ms),
                "asks": np.where(yes_here, feats[..., c_yt], np.nan).astype(np.float32),
                "asks_no": np.where(no_here, feats[..., c_nt], np.nan).astype(np.float32),
                "ask_sz": np.where(yes_here, np.expm1(feats[..., c_yd]), 0.0).astype(np.float32),
                "bid_sz": np.where(no_here, np.expm1(feats[..., c_nd]), 0.0).astype(np.float32),
            }
        )
    return items


def collate_items(items):
    """Pad a list of items on the contract axis into one tensor batch.

    Parameters
    ----------
    items : list of dict
        Panel items (:func:`build_panel_items`); arrays may already be
        torch tensors.

    Returns
    -------
    dict
        ``feats (B, T, Cm, F) float32``, ``seen``/``visible (B, T, Cm)
        bool``, ``y (B, Cm) float32``, ``market_id (B,) long``,
        ``is_partition (B,) bool``, ``st_code (B, Cm) long`` padded with
        the ``between`` code (the "neither tail" code the head ignores),
        ``contract_mask (B, Cm) bool``, ``eligible (B,) bool``, and
        ``featurizer`` — the items' shared layout identity, which the
        model checks against its own before scoring.

    Raises
    ------
    ValueError
        On an empty list, or items whose lead axis, token width or layout
        identity differ (panels from two grids or two layouts cannot
        share a batch).
    """
    import torch

    if not items:
        raise ValueError("collate_items: the item list is empty — nothing to batch")
    B = len(items)
    T, F = int(items[0]["feats"].shape[0]), int(items[0]["feats"].shape[-1])
    identity = tuple(items[0]["featurizer"])
    for i, item in enumerate(items):
        shape = tuple(int(s) for s in item["feats"].shape)
        if shape[0] != T or shape[-1] != F or tuple(item["featurizer"]) != identity:
            raise ValueError(
                f"collate_items: item {i} has feats shape {shape} under layout "
                f"{tuple(item['featurizer'])!r}, item 0 has (T={T}, ..., F={F}) under "
                f"{identity!r} — one grid and one token layout per batch"
            )
    Cm = max(int(len(item["y"])) for item in items)
    out = {
        "featurizer": identity,
        "feats": torch.zeros(B, T, Cm, F, dtype=torch.float32),
        "seen": torch.zeros(B, T, Cm, dtype=torch.bool),
        "visible": torch.zeros(B, T, Cm, dtype=torch.bool),
        "y": torch.zeros(B, Cm, dtype=torch.float32),
        "contract_mask": torch.zeros(B, Cm, dtype=torch.bool),
        "st_code": torch.full((B, Cm), STRIKE_CODES["between"], dtype=torch.long),
        "market_id": torch.tensor([int(item["market_id"]) for item in items], dtype=torch.long),
        "is_partition": torch.tensor([bool(item["is_partition"]) for item in items]),
        "eligible": torch.tensor([bool(item["eligible"]) for item in items]),
    }
    for i, item in enumerate(items):
        c = int(len(item["y"]))
        out["feats"][i, :, :c] = torch.as_tensor(item["feats"], dtype=torch.float32)
        out["seen"][i, :, :c] = torch.as_tensor(item["seen"], dtype=torch.bool)
        out["visible"][i, :, :c] = torch.as_tensor(item["visible"], dtype=torch.bool)
        out["y"][i, :c] = torch.as_tensor(item["y"], dtype=torch.float32)
        out["st_code"][i, :c] = torch.as_tensor(item["st_code"], dtype=torch.long)
        out["contract_mask"][i, :c] = True
    return out

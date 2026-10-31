"""Domain projections over DSKIT's existing immutable observation read seam."""

import math
from abc import ABC, abstractmethod
from collections import Counter
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from dskit.onboarding.base import parse_utc
from dskit.onboarding.libs.cboe import CHAIN_KEY_FIELDS, CHAIN_STREAM, QUOTE_TZ
from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.libs.observations import DEFAULT_TS_OUT, ObservationRows
from dskit.pipeline.node import check_int_param
from dskit.pipeline.records import number_ok, price_ok
from dskit.pipeline.study_protocol import StudyNode

from .contracts import CashIndexContract, _IndexQuote, _IndexSettlement

__all__ = ["MIOPacketInputs", "ChainQuoteRows", "ContractRows", "IndexCloseRows", "QuoteRows", "SettlementRows"]


def _settlement_date(expiry):
    """Return the last weekday on or before an OCC expiry (a Saturday expiry settles Friday)."""
    if not isinstance(expiry, str):
        raise ValueError(f"expiry must be an ISO date, got {expiry!r}")
    day = date.fromisoformat(expiry)
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


class _OptionRows(ObservationRows, ABC):
    """Fix schema vocabulary while inheriting scan, vintage and fingerprint."""

    _PARAMS = narrow_params(
        ObservationRows._PARAMS, "stream", "key_fields", "ts_field",
        "ts_unit", "ts_out", "shared_fields", "since_ms",
    )

    @abstractmethod
    def _row_type(self):
        """Name the domain owner validating these observation rows."""

    def ts_field(self):
        """Name the canonical observation instant.

        Returns
        -------
        str
            The effective-time field.
        """
        return "effective_at"

    def ts_unit(self):
        """Declare ISO input, never infer timestamp units.

        Returns
        -------
        str
            The parent reader's ISO unit.
        """
        return "iso"

    def ts_out(self):
        """Name the parent-owned timestamp projection.

        Returns
        -------
        str
            An explicit output field, collision-checked by the parent.
        """
        return "observation_ms"

    def shared_fields(self):
        """Disable undeclared field interning.

        Returns
        -------
        tuple
            No shared-field vocabulary.
        """
        return ()

    def since_ms(self):
        """Read the full frozen corpus, with no implicit event-time filter.

        Returns
        -------
        None
            No lower event-time bound.
        """
        return None

    def project(self, records):
        """Validate winning rows without reimplementing acquisition deduplication.

        Parameters
        ----------
        records : list of dict
            Parent reader's already deduplicated and timestamp-stamped rows.

        Returns
        -------
        list of dict
            Independently owned canonical domain rows.

        Raises
        ------
        ValueError
            If a winning row violates the synthetic domain contract.
        """
        return [dict(self._row_type()(record).data) for record in records]

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep this research-only reader out of served graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused; no configuration authorizes this synthetic source for serving.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"


class ContractRows(_OptionRows):
    """Read synthetic index-option reference versions.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required root/source; optional acquisition-vintage cutoff.

    Examples
    --------
    Construct without scanning until fingerprint/run::

        node = ContractRows("contracts", {"root": "./ob", "source": "index-fixture"})
    """

    def _row_type(self):
        """Use the common contract owner."""
        return CashIndexContract

    def stream(self):
        """Name the contract stream.

        Returns
        -------
        str
            Canonical contract stream.
        """
        return "contracts"

    def key_fields(self):
        """Keep contract revisions distinct.

        Returns
        -------
        tuple of str
            Corpus, contract identity and row version.
        """
        return ("corpus_id", "contract_id", "row_version")


class QuoteRows(_OptionRows):
    """Read synthetic quote observations without losing versions.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required root/source; optional acquisition-vintage cutoff.

    Examples
    --------
    Construct the quote reader::

        node = QuoteRows("quotes", {"root": "./ob", "source": "index-fixture"})
    """

    def _row_type(self):
        """Use the common synthetic quote owner."""
        return _IndexQuote

    def stream(self):
        """Name the quote stream.

        Returns
        -------
        str
            Canonical quote stream.
        """
        return "quotes"

    def key_fields(self):
        """Keep every timestamped quote revision.

        Returns
        -------
        tuple of str
            Corpus, contract, observation instant and row version.
        """
        return ("corpus_id", "contract_id", "effective_at", "row_version")


class SettlementRows(_OptionRows):
    """Read synthetic official-settlement analogues for ex-post diagnostics.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        Required root/source; optional acquisition-vintage cutoff.

    Examples
    --------
    Construct the outcome reader::

        node = SettlementRows("settlements", {"root": "./ob", "source": "index-fixture"})
    """

    def _row_type(self):
        """Use the common synthetic settlement owner."""
        return _IndexSettlement

    def stream(self):
        """Name the outcome stream.

        Returns
        -------
        str
            Canonical settlement stream.
        """
        return "settlements"

    def key_fields(self):
        """Keep expiry and revision in settlement identity.

        Returns
        -------
        tuple of str
            Corpus, settlement identity, expiry and row version.
        """
        return ("corpus_id", "settlement_id", "expiry", "row_version")


class IndexCloseRows(ObservationRows):
    """Read one index's daily closes from the Cboe pack's ``index_daily`` stream.

    Real data (ADR-0182). Index closes are not option rows, so this reader
    deliberately bypasses the synthetic-provenance contracts in
    ``contracts.py`` — those still validate the fixture track and are not
    loosened here. It projects the declared ``symbol`` into the envelope
    the numpy feature nodes read (``instrument``/``contract``/``group`` =
    the symbol, ``close``, ``asof_ms``, ``date``). A second instance
    (``symbol: "VIX"``) joins onto the first as ``iv_index`` through
    dskit's ``keyby`` (``key: date``, ``value: close``) and ``join`` kinds,
    with no child join or keying code.

    ``asof_ms`` is the date's UTC midnight: every daily feature and label
    shares that stamp, so the walk-forward cut is consistent; it is not a
    claim that the close was known at midnight.

    The optionshist pack's ``index_daily`` rows (ADR-0187) also carry
    ``dividend_amount`` and ``split_coefficient``: the dividend is copied
    when the row carries it (Cboe rows do not, so SPX and VIX output is
    unchanged), and a row whose split coefficient is present and not 1 is
    refused — the archive's closes are raw, so a read must start after the
    last split.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        ``root`` and ``source`` (required), ``symbol`` (required, e.g.
        ``"SPX"``), and the parent's optional ``since_ms`` /
        ``as_of_acquisition_ms``.

    Examples
    --------
    SPX closes::

        spx = IndexCloseRows("spx", {"root": "./ob", "source": "cboe-index", "symbol": "SPX"})
        rows = spx.run(None, {})["records"]
    """

    _PARAMS = narrow_params(
        ObservationRows._PARAMS, "stream", "key_fields", "ts_field", "ts_unit",
        "ts_out", "shared_fields",
    ) + ("symbol",)

    @classmethod
    def validate_params(cls, params):
        """Add the required ``symbol`` to the parent's checks.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = super().validate_params(params)
        symbol = params.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            problems.append(f"symbol is required and must be a non-empty string, got {symbol!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep this research reader out of served graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"

    def stream(self):
        """Name the pack's daily index stream.

        Returns
        -------
        str
            ``"index_daily"``.
        """
        return "index_daily"

    def key_fields(self):
        """One row per symbol and trading date.

        Returns
        -------
        tuple of str
            ``("symbol", "date")``.
        """
        return ("symbol", "date")

    def ts_field(self):
        """Stamp each row from its ISO trading date.

        Returns
        -------
        str
            ``"date"``.
        """
        return "date"

    def ts_unit(self):
        """Read ``date`` as ISO.

        Returns
        -------
        str
            ``"iso"``.
        """
        return "iso"

    def ts_out(self):
        """Write the instant to the envelope's decision field.

        Returns
        -------
        str
            ``"asof_ms"``.
        """
        return "asof_ms"

    def shared_fields(self):
        """Intern nothing.

        Returns
        -------
        tuple
            Empty.
        """
        return ()

    def project(self, records):
        """Keep the declared symbol's rows as envelope dicts, oldest first.

        Parameters
        ----------
        records : list of dict
            The pack's winning rows, ``date`` stamped onto ``asof_ms``.

        Returns
        -------
        list of dict
            ``instrument``, ``contract``, ``group``, ``close``, ``asof_ms``
            and ``date`` per row, plus ``dividend_amount`` when the row
            carries one, sorted by ``asof_ms``.

        Raises
        ------
        ValueError
            When a kept row's close is not a positive number, its
            ``dividend_amount`` is present and not a number >= 0 or
            ``None``, or its ``split_coefficient`` is present and not 1.
        """
        symbol = self.params["symbol"]
        out = []
        for record in records:
            if record.get("symbol") != symbol:
                continue
            close = record.get("close")
            if not price_ok(close):
                raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} close "
                                 f"must be a positive number, got {close!r}")
            row = {"instrument": symbol, "contract": symbol, "group": symbol,
                   "close": float(close), "asof_ms": record["asof_ms"],
                   "date": record["date"]}
            ohlc = ("open", "high", "low")
            present = [name for name in ohlc if record.get(name) is not None]
            if present:
                if len(present) != len(ohlc):
                    raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} carries "
                                     "an incomplete OHLC tuple")
                for name in ohlc:
                    if not price_ok(record[name]):
                        raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} "
                                         f"{name} must be a positive number")
                    row[name] = float(record[name])
            if record.get("volume") is not None:
                volume = record["volume"]
                if not number_ok(volume) or volume < 0:
                    raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} "
                                     "volume must be a number >= 0")
                row["volume"] = float(volume)
            if "dividend_amount" in record:
                dividend = record["dividend_amount"]
                if dividend is not None and (not number_ok(dividend) or dividend < 0):
                    raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} "
                                     f"dividend_amount must be a number >= 0 or null, "
                                     f"got {dividend!r}")
                row["dividend_amount"] = dividend
            coefficient = record.get("split_coefficient")
            if coefficient is not None and coefficient != 1:
                raise ValueError(f"{self.key}: {symbol} {record.get('date')!r} carries "
                                 f"split_coefficient {coefficient!r}; the archive's closes "
                                 "are raw, so the read must start after the last split")
            out.append(row)
        return sorted(out, key=lambda row: row["asof_ms"])


class ChainQuoteRows(ObservationRows):
    """Read one underlying's archived end-of-day option quotes for one DTE bucket.

    ADR-0187. The ``option_chain`` stream (the optionshist archive or a
    recorded Cboe chain), keyed ``(option, quote_time)`` and stamped from
    ``quote_time``, bounded AT INTAKE so a walk never holds the whole
    53M-row archive: the parent's ``keep_values`` keeps the declared
    ``symbol``'s rows, and ``admit`` keeps a row only when its ``root``
    equals the symbol, its strike sits on the 0.5 grid (adjusted
    deliverables do not), the calendar days from the quote's New York date
    to the SETTLEMENT date (the last weekday on or before the OCC expiry)
    lie in ``[dte_min, dte_max]``, its ``underlying_price`` is a positive
    number, and ``|ln(strike / underlying_price)|`` is within
    ``max_abs_log_moneyness``. Every rule's drop count is logged. Snapshot
    reuse is on: every fold of a walk reads one parsed snapshot per
    process. Forbidden for serving.

    Parameters
    ----------
    key : str
        Pipeline node key.
    params : dict
        ``root``, ``source``, ``symbol``, ``dte_min`` (int >= 1: no 0DTE),
        ``dte_max`` (int >= ``dte_min``) and ``max_abs_log_moneyness``
        (positive), all required; the parent's optional ``since_ms`` and
        ``as_of_acquisition_ms``.

    Examples
    --------
    SPY's 30-45 day bucket within 20% log-moneyness::

        chain = ChainQuoteRows("chain", {
            "root": "./ob", "source": "optionshist-chain", "symbol": "SPY",
            "dte_min": 30, "dte_max": 45, "max_abs_log_moneyness": 0.2,
        })
        rows = chain.run(None, {})["records"]
        # -> [{"instrument": "SPY", "date": ..., "expiry": ..., "settle_date": ...,
        #      "dte": 36, "right": "call", "strike": 590.0, "bid": ..., ...}, ...]
    """

    reuse_snapshot = True
    #: The strike grid a listed (unadjusted) contract sits on.
    STRIKE_GRID = 0.5
    #: Why an in-symbol row is dropped at intake, in the order the rules are checked.
    DROP_REASONS = ("root", "strike_grid", "dte", "no_underlying_price", "band")
    _PARAMS = narrow_params(
        ObservationRows._PARAMS, "stream", "key_fields", "ts_field", "ts_unit",
        "ts_out", "shared_fields",
    ) + ("symbol", "dte_min", "dte_max", "max_abs_log_moneyness")
    _dropped = None

    @classmethod
    def validate_params(cls, params):
        """Add the cell's four knobs to the parent's checks.

        Parameters
        ----------
        params : dict
            The candidate configuration.

        Returns
        -------
        list of str
            All problems; empty when usable.
        """
        problems = super().validate_params(params)
        symbol = params.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            problems.append(f"symbol is required and must be a non-empty string, got {symbol!r}")
        for knob in ("dte_min", "dte_max"):
            if knob not in params:
                problems.append(f"{knob} is required")
            else:
                check_int_param(problems, knob, params[knob], ge=1)
        lo, hi = params.get("dte_min"), params.get("dte_max")
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (lo, hi)) and lo > hi:
            problems.append(f"dte_min {lo} must not exceed dte_max {hi}")
        if "max_abs_log_moneyness" not in params:
            problems.append("max_abs_log_moneyness is required")
        elif not price_ok(params["max_abs_log_moneyness"]):
            problems.append("max_abs_log_moneyness must be a positive number, got "
                            f"{params['max_abs_log_moneyness']!r}")
        return problems

    @classmethod
    def serving_effect(cls, params, verified_run_evidence):
        """Keep this research reader out of served graphs.

        Parameters
        ----------
        params, verified_run_evidence : dict
            Unused.

        Returns
        -------
        str
            Always forbidden.
        """
        return "forbidden"

    def stream(self):
        """Name the chain stream (str)."""
        return CHAIN_STREAM

    def key_fields(self):
        """Name the chain's dedup key, the pack's (tuple of str)."""
        return CHAIN_KEY_FIELDS

    def ts_field(self):
        """Stamp each row from its quote instant (str)."""
        return "quote_time"

    def ts_unit(self):
        """Read ``quote_time`` as ISO (str)."""
        return "iso"

    def ts_out(self):
        """Write the instant to the envelope's decision field (str)."""
        return DEFAULT_TS_OUT

    def shared_fields(self):
        """Intern the strings every row of a chain repeats (tuple of str)."""
        return ("underlying", "root", "expiry", "right", "quote_time")

    def keep_values(self):
        """Read only the declared symbol's rows (dict)."""
        return {"underlying": [self.params["symbol"]]}

    def admit(self):
        """Return the intake rule for one cell, counting every drop by reason.

        Returns
        -------
        callable
            ``admit(data, stamp)``; a kept row gains ``date`` (the quote's
            New York date), ``settle_date`` and ``dte``.
        """
        symbol = self.params["symbol"]
        lo, hi = self.params["dte_min"], self.params["dte_max"]
        band, grid = self.params["max_abs_log_moneyness"], self.STRIKE_GRID
        counts = self._dropped = Counter()
        zone, days, settles = ZoneInfo(QUOTE_TZ), {}, {}

        def keep(data, stamp):
            if data.get("root") != symbol:
                counts["root"] += 1
                return False
            strike = data.get("strike")
            if not price_ok(strike) or abs(strike / grid - round(strike / grid)) > 1e-9:
                counts["strike_grid"] += 1
                return False
            quote_time, expiry = data["quote_time"], data.get("expiry")
            day = days.get(quote_time)
            if day is None:
                day = days[quote_time] = parse_utc(quote_time).astimezone(zone).date()
            settle = settles.get(expiry)
            if settle is None:
                settle = settles[expiry] = _settlement_date(expiry)
            dte = (settle - day).days
            if not lo <= dte <= hi:
                counts["dte"] += 1
                return False
            level = data.get("underlying_price")
            if not price_ok(level):
                counts["no_underlying_price"] += 1
                return False
            if abs(math.log(strike / level)) > band:
                counts["band"] += 1
                return False
            data["date"], data["settle_date"], data["dte"] = (
                day.isoformat(), settle.isoformat(), dte)
            return True

        return keep

    def project(self, records):
        """Copy each kept row into a fresh quote envelope.

        Parameters
        ----------
        records : list of dict
            The kept chain rows, stamped and carrying the intake's
            ``date`` / ``settle_date`` / ``dte``.

        Returns
        -------
        list of dict
            ``instrument``, ``date``, ``expiry``, ``settle_date``, ``dte``,
            ``right``, ``strike``, ``bid``, ``ask``, ``bid_size``,
            ``ask_size``, ``iv``, ``underlying_price`` and ``asof_ms`` per
            row, in the seam's order — new objects, never the shared
            snapshot's.
        """
        symbol = self.params["symbol"]
        if self._dropped is not None:
            self.log.info("chain intake for %s: kept %d row(s); dropped %s", symbol,
                          len(records), {r: self._dropped.get(r, 0) for r in self.DROP_REASONS})
        return [{"instrument": symbol, "date": r["date"], "expiry": r["expiry"],
                 "settle_date": r["settle_date"], "dte": r["dte"], "right": r["right"],
                 "strike": r["strike"], "bid": r["bid"], "ask": r["ask"],
                 "bid_size": r["bid_size"], "ask_size": r["ask_size"], "iv": r["iv"],
                 "underlying_price": r["underlying_price"], "asof_ms": r["asof_ms"]}
                for r in records]


class MIOPacketInputs(StudyNode):
    """Read one durably claimed option packet with pushed time/pair predicates.

    Parameters
    ----------
    params : dict
        Executable study. The permit must arrive from StudyPacketOpen.

    Examples
    --------
    bounded = MIOPacketInputs("intake", {"study": study}).run(ctx, {"permit": permit})
    """

    role = "transform"
    outputs = ("bounded_inputs", "provenance")

    @staticmethod
    def _portable_code_pins(pins):
        """Keep validated package paths relative so checkout moves reuse indexes."""
        from pathlib import Path

        portable = {}
        for name, digest in pins.items():
            source = Path(name)
            if not source.is_absolute():
                raise ValueError("absolute study code pin required")
            root = source.parent
            while (root / "__init__.py").is_file():
                root = root.parent
            relative = source.relative_to(root).as_posix()
            if relative in portable:
                raise ValueError("ambiguous portable study code pin")
            portable[relative] = digest
        return portable

    def fingerprint(self, ctx=None):
        """Return only configured metadata; never hash market bytes before claim."""
        from dskit.pipeline.base import value_hash
        return value_hash(self.params["study"]["domain"]["sources"])

    def run(self, ctx, inputs):
        """Read and verify the claimed packet, sharing identical quote indexes.

        Parameters
        ----------
        ctx : NodeContext
            Current run directory for run-local index evidence.
        inputs : dict
            Bound packet permit.

        Returns
        -------
        dict
            Bounded inputs and their source/plan provenance.
        """
        from dskit.pipeline.base import value_hash
        from dskit.pipeline.libs.parquet import DateBoundedParquet, SQLiteParquetIndex
        from dskit.pipeline.libs.option_ledger import StandardSplitAdjustment
        from dskit.onboarding.artifacts import PinnedPayloadSource
        import json
        from .mio_contract import MIOStudyContract
        import os
        import pyarrow as pa
        import pyarrow.dataset as ds
        from pathlib import Path

        plan, registry = self.plan(), self.registry()
        permit = inputs["permit"]
        registry.verify(plan, permit)
        if not plan["runnable"]:
            raise ValueError("unbound packet")
        d = self.params["study"]["domain"]
        packet = MIOStudyContract.packet(self.params["study"])
        symbols, pairs = d["params"]["symbols"], d["pairs"]
        bounds = plan["read_bounds"]
        tail = bounds["chain"]["end_before"]
        checked = {}

        def read(role, key, columns, temporal, where=None):
            binding = d["sources"][role]
            source = PinnedPayloadSource(binding["pin"])
            relative = binding["files"][key]
            path = source.file(relative, verify=True)
            checked[path] = (source, relative)
            rows = [row for batch in DateBoundedParquet(path, columns, temporal).batches(where=where)
                    for row in batch.to_pylist()]
            return [{k: v.isoformat() if isinstance(v, date) else v for k, v in r.items()} for r in rows]

        sessions = read("sessions", "shared", ["date"], {"date": bounds["sessions"]})
        calendar = [r["date"] for r in sessions]
        if not calendar or calendar != sorted(set(calendar)):
            raise ValueError("session source must be unique and ordered")
        identity = ["symbol", "quote_date", "expiry", "fold", "band"]
        delta = ds.field("expiry").cast(pa.date32()).cast(pa.int32())-ds.field("quote_date").cast(pa.date32()).cast(pa.int32())
        pair_filter = None
        for pair in pairs:
            term = ((ds.field("symbol") == pair["symbol"]) & (delta >= pair["dte_min"])
                    & (delta <= pair["dte_max"]))
            pair_filter = term if pair_filter is None else pair_filter | term
        allowed = pair_filter & (ds.field("fold") == d["params"]["bridge"]["fold"]) & (ds.field("band") == "val")
        temporal = {"quote_date": bounds["forecasts"], "expiry": {"end_before": tail}}
        forecasts = read("forecasts", "shared",
                         identity+["fitted"]+["method_cdf_"+s for s in d["params"]["bridge"]["grid_labels"]],
                         temporal, allowed)
        contexts = read("contexts", "shared", identity+["spot", "reference_scale", "settlement_date"],
                        {**temporal, "settlement_date": {"end_before": tail}}, allowed)
        adjustments = None
        if "corporate_actions" in d["sources"]:
            binding = d["sources"]["corporate_actions"]
            source = PinnedPayloadSource(binding["pin"])
            event_path = source.file(binding["files"]["shared"], verify=True)
            memo_path = source.file(binding["files"]["memo"], verify=True)
            checked[event_path] = (source, binding["files"]["shared"])
            checked[memo_path] = (source, binding["files"]["memo"])
            with open(event_path, encoding="utf-8") as handle:
                events = StandardSplitAdjustment.parse(json.load(handle), d["params"]["bridge"]["solver"]["multiplier"])
            if (not events or set(events)-set(symbols)
                    or any(not packet["start"] <= event.effective.isoformat() < tail
                           or event.effective.isoformat() not in calendar for event in events.values())):
                raise ValueError("split source is empty, outside the packet, or names another symbol")
            adjustments = [event.record() for event in events.values()]
        data = {}
        for symbol in symbols:
            fs = [r for r in forecasts if r["symbol"] == symbol]
            cs = [r for r in contexts if r["symbol"] == symbol]
            expiries = sorted({r["expiry"] for r in fs})
            bars = read("bars", symbol, ["symbol", "date", "close"],
                        {"date": bounds["bars"]}, ds.field("symbol") == symbol)
            binding = d["sources"]["chain"]
            source = PinnedPayloadSource(binding["pin"])
            relative = binding["files"][symbol]
            path = source.file(relative, verify=True)
            checked[path] = (source, relative)
            from .nodes import price_ok
            basis_factors = {}
            for batch in DateBoundedParquet(path, ["date", "basis_factor"],
                    {"date": bounds["chain"]}).batches(where=ds.field("symbol") == symbol):
                for row in batch.to_pylist():
                    day = row["date"].isoformat() if isinstance(row["date"], date) else row["date"]
                    factor = row["basis_factor"]
                    if not price_ok(factor):
                        raise ValueError("invalid daily basis metadata")
                    if day in basis_factors and basis_factors[day] != factor:
                        raise ValueError("inconsistent daily basis metadata")
                    basis_factors[day] = factor
            if ctx is not None and getattr(ctx, "run_dir", None):
                quote_root = Path(ctx.run_dir) / "artifacts" / "mio-quotes"
                cache_home = os.environ.get("XDG_CACHE_HOME")
                cache_base = (Path(cache_home) if cache_home and Path(cache_home).is_absolute()
                              else Path.home() / ".cache")
                reuse_root = cache_base / "dskit" / "sqlite-parquet-index"
            else:
                import tempfile
                quote_root = Path(tempfile.mkdtemp(prefix="mio-quote-index-")) / "artifacts" / "mio-quotes"
                reuse_root = None
            quote_path = str(quote_root / (symbol + ".sqlite"))
            from .ledger_studies import _AllQuotedOptionContext
            quote_node = _AllQuotedOptionContext("prepare", {**d["params"]["bridge"], "symbol": symbol})
            quote_factors = {}
            quote_columns = ["symbol", "date", "expiration", "type", "strike_raw", "strike", "bid", "ask",
                             "bid_size", "ask_size", "basis_factor", "underlying_price"]
            expiry_filter = ds.field("expiration").isin(expiries) if expiries else ds.scalar(False)
            quote_index = SQLiteParquetIndex.build(path, quote_path, quote_columns,
                {"date": bounds["chain"], "expiration": {"end_before": tail}},
                where=(ds.field("symbol") == symbol) & expiry_filter,
                unique_by=("symbol", "date", "expiration", "type", "strike_raw"),
                partition_by=("date", "expiration"), float_keys=("strike_raw",),
                validator=lambda row: quote_node._validate_quote_row(row, quote_factors),
                reuse_root=reuse_root,
                reuse_identity=({"verified_source_sha256": binding["pin"]["files"][relative],
                                 "predicate": {"symbol": symbol, "expiries": expiries},
                                 "validator": quote_node.params,
                                 "code": self._portable_code_pins(self.params["study"]["code"]),
                                 "dependencies": self.params["study"]["dependencies"]}
                                if reuse_root is not None else None),
                replay_validator=reuse_root is not None)
            for row in bars:
                if row["date"] in basis_factors:
                    row["basis_factor"] = basis_factors[row["date"]]
            data[symbol] = {"forecasts": fs, "contexts": cs,
                            "chain_index": {"index": quote_index, "symbol": symbol,
                                "relative": relative, "source_pin": binding["pin"],
                                "bounds": {"date": bounds["chain"], "expiration": {"end_before": tail}},
                                "expiry_inventory_sha256": value_hash(expiries),
                                "forecast_expiries": expiries, "selected_rows": quote_index["rows"],
                                "basis_factors": basis_factors},
                            "bars": bars}
            self.log.info("MIO quote intake complete for %s: %d indexed rows", symbol, quote_index["rows"])
        # Detect drift during the bounded scan as well as before it.
        for source, relative in checked.values():
            source.file(relative, verify=True)
        bounded = {"inputs": data, "clocks": {
            "sessions": calendar, "history_start": d["history_start"], "entry_start": packet["start"],
            "entry_end_before": packet["end_before"], "tail_end_before": tail}}
        if adjustments is not None:
            bounded["adjustments"] = adjustments
        provenance = {"plan_sha256": plan["sha256"], "inputs_sha256": value_hash(bounded),
                      "source_pins_sha256": value_hash(plan["source_pins"]), "read_bounds": bounds,
                      "counts": {s: {k: len(v) for k, v in rows.items()} for s, rows in data.items()}}
        if adjustments is not None:
            provenance["adjustments_sha256"] = value_hash(adjustments)
        registry.bind(plan, permit, "inputs", provenance)
        return {"bounded_inputs": bounded, "provenance": provenance}

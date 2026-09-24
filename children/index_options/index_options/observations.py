"""Domain projections over DSKIT's existing immutable observation read seam."""

from abc import ABC, abstractmethod

from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.libs.observations import ObservationRows
from dskit.pipeline.records import price_ok

from .contracts import CashIndexContract, _IndexQuote, _IndexSettlement

__all__ = ["ContractRows", "IndexCloseRows", "QuoteRows", "SettlementRows"]


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
            and ``date`` per row, sorted by ``asof_ms``.

        Raises
        ------
        ValueError
            When a kept row's close is not a positive number.
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
            out.append({"instrument": symbol, "contract": symbol, "group": symbol,
                        "close": float(close), "asof_ms": record["asof_ms"],
                        "date": record["date"]})
        return sorted(out, key=lambda row: row["asof_ms"])

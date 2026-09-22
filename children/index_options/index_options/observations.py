"""Domain projections over DSKIT's existing immutable observation read seam."""

from abc import ABC, abstractmethod

from dskit.pipeline.libs.numpy import narrow_params
from dskit.pipeline.libs.observations import ObservationRows

from .contracts import CashIndexContract, _IndexQuote, _IndexSettlement

__all__ = ["ContractRows", "QuoteRows", "SettlementRows"]


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

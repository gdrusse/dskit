"""The venue seams are structural: membership is a matter of signature,
never of importing this package — that IS the wrapper thesis. (Conformance
of a real adapter's classes lives with that adapter package.)"""

from dskit.pipeline import (
    Accounting,
    BinaryAccounting,
    DataSource,
    ExecutionModel,
    MarkToMarketAccounting,
    SettlementSource,
    SignalProvider,
    Sizer,
)


def test_a_bars_shaped_source_is_a_member_without_inheriting():
    class BarsSource:  # a future adapter: only the signature matters
        def records_for(self, instrument, start_ms, end_ms):
            yield from ()

    assert isinstance(BarsSource(), DataSource)


def test_signal_provider_shape():
    class FixedQHat:
        def q_hat(self, instrument, p_mid, asof_ms, lead_frac=None):
            return 0.5

    assert isinstance(FixedQHat(), SignalProvider)


def test_callables_satisfy_sizer_and_execution():
    def allocate(rows, **kwargs):
        return {}

    def fill(book, order):
        return {}

    assert isinstance(allocate, Sizer)
    assert isinstance(fill, ExecutionModel)


def test_settlement_and_accounting_shapes():
    class Marks:
        def outcomes_for(self, instrument):
            return {}

    assert isinstance(Marks(), SettlementSource)
    assert isinstance(BinaryAccounting(), Accounting)
    assert isinstance(MarkToMarketAccounting(), Accounting)


def test_non_members_are_rejected():
    class NotASource:
        def fetch(self, symbol):
            return []

    assert not isinstance(NotASource(), DataSource)

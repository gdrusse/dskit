"""`intraday_equities/metrics.py` — the child's half of the Phase 6 contract.

ADR-0118 splits the event/metric contract in two: the schema, the adapter
seam and the safe aggregates are generic and live in `dskit.production`;
what a bar, a lead and a decayed signal ARE is domain knowledge and lives
here. So this file asserts exactly two things:

* **The adapter carries no schema.** It maps the child's own field names
  onto catalogue names and nothing else — stamping, validation and the
  refusal of a field the catalogue never declared all belong to
  `EventAdapter`, and a test that found them re-implemented here would be
  finding the second copy that diverges.
* **Per-lead detail is DATA, never a label.** `SignalDecay` returns a
  mapping keyed by lead for the ledger and report artifacts; nothing here
  may hand a lead or a symbol to the metric registry, which refuses both.

No market data, no pipeline run: every record below is a synthetic dict.
"""

import pytest

from dskit.production import vocab
from dskit.production.base import ProductionError
from dskit.production.metrics import EventAdapter, EventReadings, Metrics

from intraday_equities.metrics import EquityEventAdapter, SignalDecay


def bar_record(expected=390, received=388, missing=2, late=1, **extra):
    """A synthetic session-coverage record in the child's own field names."""
    record = {
        "symbol": "AAA",
        "lead": "h01",
        "expected_bars": expected,
        "received_bars": received,
        "missing_bars": missing,
        "late_bars": late,
    }
    record.update(extra)
    return record


# ---------------------------------------------------------------------------
# The adapter: a mapping, and only a mapping
# ---------------------------------------------------------------------------


def test_it_is_the_generic_seam_and_supplies_only_the_field_hook():
    assert issubclass(EquityEventAdapter, EventAdapter)
    assert EquityEventAdapter({}).catalogue.version == vocab.EVENT_SCHEMA_VERSION


def test_params_are_default_deny():
    with pytest.raises(ProductionError):
        EquityEventAdapter({"lookback": 30})


def test_bar_counts_become_the_catalogues_generic_input_counts():
    """Tier 1 holds no domain word, so `bars` is the child's name for what
    the catalogue calls `inputs`. The mapping is what this file exists for."""
    event = EquityEventAdapter({}).event(bar_record())
    assert event["data"] == {
        "expected_inputs": 390,
        "received_inputs": 388,
        "missing_inputs": 2,
        "late_inputs": 1,
    }


def test_symbol_and_lead_ride_in_the_identity_category():
    event = EquityEventAdapter({}).event(bar_record())
    assert event["identity"]["symbol"] == "AAA"
    assert event["identity"]["lead"] == "h01"


def test_the_event_is_stamped_by_the_generic_seam_not_by_this_module():
    event = EquityEventAdapter({}).event(bar_record())
    assert event["schema_version"] == vocab.EVENT_SCHEMA_VERSION


def test_a_child_field_the_adapter_does_not_map_is_simply_not_a_reading():
    """A record carries plenty that is not a catalogue value — its own ids,
    its kind, its cache keys. Selecting is the adapter's job; the catalogue
    never sees them, so nothing refuses and nothing leaks."""
    event = EquityEventAdapter({}).event(bar_record(cache_key="abc", kind="coverage"))
    assert "abc" not in str(event)


def test_every_mapped_target_is_a_real_catalogue_field():
    """The mapping table is the one place a name could drift out of the
    catalogue; `EventAdapter.event` would refuse at runtime, and this
    refuses at test time over the whole table rather than one record."""
    catalogue = EquityEventAdapter({}).catalogue
    for child_field, (category, field) in EquityEventAdapter.FIELD_MAP.items():
        assert field in catalogue.fields(category), child_field


def test_the_adapter_owns_no_symbol_or_lead_label():
    """Plan §6: full per-name detail belongs in ledger and report
    artifacts. The registry refuses either as a label, so an adapter that
    tried would refuse at declaration rather than in production."""
    registry = Metrics()
    EventReadings(registry).record(EquityEventAdapter({}).event(bar_record()))
    for series in registry.snapshot().values():
        assert not any("symbol=" in key or "lead=" in key for key in series)


# ---------------------------------------------------------------------------
# Signal decay by lead — the domain metric, returned as data
# ---------------------------------------------------------------------------


def test_decay_is_each_leads_score_as_a_fraction_of_the_first_leads():
    profile = SignalDecay().profile({"h01": 0.4, "h02": 0.2, "h03": 0.1})
    assert profile == {"h01": 1.0, "h02": pytest.approx(0.5), "h03": pytest.approx(0.25)}


def test_it_keeps_the_leads_the_caller_gave_and_invents_none():
    assert set(SignalDecay().profile({"h01": 1.0, "h04": 0.5})) == {"h01", "h04"}


def test_a_missing_or_unusable_baseline_refuses_rather_than_returning_ones():
    """A decay profile without its baseline is a row of 1.0s that reads as
    'no decay' — the one answer a missing measurement must never give."""
    for scores in ({"h02": 0.2}, {"h01": 0.0, "h02": 0.2}, {"h01": float("nan")}):
        with pytest.raises(ProductionError):
            SignalDecay().profile(scores)


def test_an_unknown_lead_refuses_against_the_declared_head_names():
    with pytest.raises(ProductionError):
        SignalDecay().profile({"h01": 1.0, "h99": 0.5})


def test_the_profile_is_a_mapping_for_an_artifact_not_a_metric_label():
    profile = SignalDecay().profile({"h01": 1.0, "h02": 0.5})
    assert isinstance(profile, dict)
    assert not hasattr(SignalDecay(), "labels")

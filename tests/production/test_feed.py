"""§5.2 — the feed: one normalized binding, one snapshot, one freshness ladder.

What this file pins, and why each pin is load-bearing:

* **One binding.** ``FeedSpec.from_contract`` is the only place a
  ``ServingContract`` becomes release-bound state, so acquisition and the
  entry read cannot drift onto two locators (D4's "the run's data node
  reads the same onboarding root it was trained from").
* **Identity before rows.** Every pull re-resolves the source's ACTIVE
  alias and REFUSES when it no longer matches what the release pinned.
  That check runs BEFORE any fetch, so a swapped config can never deliver
  a single row.
* **The snapshot is over the exact rows descendants receive.**
  :func:`snapshot_entry` digests the entry's own output — not a second
  read — so mutating one row moves the batch's digests. D6's
  ``data_asof_ms`` is the OLDEST watermark, so one fresh instrument
  cannot hide a stale input.
* **The ladder is fail-closed.** Zero new records is ``live`` only while
  every key is fresh; a key the store does not carry at all is ``dead``,
  never ``live``; a connector or link failure is ``dead`` immediately.

Pinned names this file introduces (see the group report):

``EntrySourceFeed(params, *, root, registry, contract, spec, clock,
max_staleness_ms, dead_after_ms)``; the module-level
``snapshot_entry(contract, spec, entry_outputs, source_config_hash)``
(``ServingContract`` is a frozen pipeline dataclass with no methods, so
the snapshot cannot be one); ``active_source_identity(registry, source)``
— the ONE owner of "what does this alias resolve to now", used by both
``plan`` and every pull; ``ReplayFeed(params, *, tape)`` over a tape of
``(FeedResult, EntryBatch)`` pairs.
"""

from __future__ import annotations

import copy
import dataclasses
import inspect

import pytest

import dskit.onboarding.acquire as acquire_mod
import dskit.onboarding.observations as observations_mod
from dskit.production.base import ProductionError, canonical_hash
from dskit.production.clock import ReplayClock, TestClock
from dskit.production.feed import (
    DEFAULT_PULL_MODE,
    FEED_KINDS,
    EntrySourceFeed,
    Feed,
    FeedSpec,
    ReplayFeed,
    active_source_identity,
    snapshot_entry,
)
from dskit.production.records import EntryBatch, FeedResult, InputWatermark
from dskit.production.release import FEED_SPEC_KEYS
from dskit.production.vocab import FEED_STATUSES, PULL_MODES
from tests.production.conftest import (
    DAY_MS,
    LAST_ROW_MS,
    NOW_MS,
    SOURCE,
    STREAM,
    UNIVERSE,
    boom,
    build_onboarding_root,
    data_dir_of,
    iso_ms,
    register_source,
    source_rows,
    write_source_files,
)

VERSION = "1"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def spec_for(contract, keys=UNIVERSE, source_config_hash="a" * 64, version=VERSION):
    """A `FeedSpec` over `contract`, pinning `keys` as the universe."""
    return FeedSpec.from_contract(contract, keys, source_config_hash, version)


def bound_spec(contract, source_config_hash, keys=UNIVERSE):
    """A `FeedSpec` pinning the hash the registry currently resolves to."""
    return FeedSpec.from_contract(contract, keys, source_config_hash, VERSION)


def entry_outputs_of(training_run):
    """A deep copy of the entry node's outputs, safe to mutate."""
    return copy.deepcopy(training_run.outputs["bars"])


def feed_for(root, registry, contract, spec, clock, **over):
    """An `EntrySourceFeed` over a root, with both thresholds supplied."""
    params = over.pop("params", {})
    kwargs = {
        "root": root,
        "registry": registry,
        "contract": contract,
        "spec": spec,
        "clock": clock,
        "max_staleness_ms": DAY_MS,
        "dead_after_ms": 7 * DAY_MS,
    }
    kwargs.update(over)
    return EntrySourceFeed(params, **kwargs)


def locators(call):
    """The (root, source, stream) a recorded call carried, args or kwargs."""
    args, kwargs = call
    values = list(args) + [kwargs.get(n) for n in ("root", "registry", "source", "stream")]
    return [v for v in values if v is not None]


def repoint(root, tmp_path, rows):
    """Retire the ACTIVE source config and activate a new one over new files."""
    registry = root.registry()
    old = acquire_mod.find_active_source(registry, SOURCE)
    data = str(tmp_path / "moved")
    write_source_files(data, rows)
    registry.transition(old, "retired", origin="test")
    return register_source(root, data, origin="test")


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------


class TestFeedSeam:
    def test_feed_is_abstract_and_pull_is_its_only_abstract_hook(self):
        assert inspect.isabstract(Feed)
        with pytest.raises(TypeError):
            Feed()
        assert Feed.__abstractmethods__ == frozenset({"pull"})

    def test_a_subclass_supplying_pull_constructs(self):
        class Fixed(Feed):
            def pull(self, tick_at_ms):
                return None

        assert isinstance(Fixed(), Feed)

    def test_both_core_kinds_are_feeds(self):
        assert issubclass(EntrySourceFeed, Feed)
        assert issubclass(ReplayFeed, Feed)

    def test_the_registry_carries_exactly_the_two_core_kinds(self):
        assert FEED_KINDS.kinds() == ("entry-source", "replay")
        assert FEED_KINDS.family == "feed"
        assert FEED_KINDS.resolve("entry-source") is EntrySourceFeed
        assert FEED_KINDS.resolve("replay") is ReplayFeed

    def test_pull_returns_a_status_only_result(self, fresh_root, serving_contract, clock):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        digest = acquire_mod.find_active_source(registry, SOURCE)
        feed = feed_for(fresh_root, registry, contract,
                        bound_spec(contract, digest), clock,
                        params={"pull": "store"})
        result = feed.pull(NOW_MS)
        assert isinstance(result, FeedResult)
        assert result.status in FEED_STATUSES


# --------------------------------------------------------------------------
# FeedSpec — the release-bound binding
# --------------------------------------------------------------------------


class TestFeedSpec:
    def test_it_binds_the_eight_fields_in_section_5_2_order(self, serving_contract):
        names = tuple(f.name for f in dataclasses.fields(FeedSpec))
        assert names == FEED_SPEC_KEYS

    def test_from_contract_copies_the_contract_verbatim(self, serving_contract):
        spec = spec_for(serving_contract)
        assert spec.source_binding == serving_contract.source_binding
        assert spec.entity_key_fields == serving_contract.entity_key_fields
        assert spec.event_time_field == serving_contract.event_time_field
        assert spec.digest_recipe == serving_contract.digest_recipe

    def test_the_spec_does_not_share_the_contracts_mutable_dicts(self, serving_contract):
        spec = spec_for(serving_contract)
        before = dict(spec.source_binding)
        serving_contract.source_binding["stream"] = "tampered"
        try:
            assert spec.source_binding == before
        finally:
            serving_contract.source_binding["stream"] = STREAM

    def test_required_keys_are_normalised_to_a_sorted_tuple(self, serving_contract):
        spec = spec_for(serving_contract, keys=["INS2", "INS1"])
        assert spec.required_keys == ("INS1", "INS2")

    def test_the_required_keys_digest_is_the_canonical_hash_of_that_list(self, serving_contract):
        spec = spec_for(serving_contract, keys=["INS2", "INS1"])
        assert spec.required_keys_digest == canonical_hash(list(spec.required_keys))

    def test_a_different_universe_moves_the_digest(self, serving_contract):
        one = spec_for(serving_contract, keys=["INS1", "INS2"])
        two = spec_for(serving_contract, keys=["INS1", "INS3"])
        assert one.required_keys_digest != two.required_keys_digest

    @pytest.mark.parametrize(
        "keys", [(), ["INS1", "INS1"], ["INS1", ""], ["INS1", 7], "INS1"],
        ids=["empty", "duplicate", "blank", "non-string", "bare-string"],
    )
    def test_a_malformed_universe_refuses(self, serving_contract, keys):
        with pytest.raises(ProductionError):
            spec_for(serving_contract, keys=keys)

    @pytest.mark.parametrize("field", ["source_config_hash", "source_config_version"])
    def test_a_non_string_source_identity_refuses(self, serving_contract, field):
        kwargs = {"source_config_hash": "a" * 64, "version": VERSION}
        kwargs["source_config_hash" if field == "source_config_hash" else "version"] = 7
        with pytest.raises(ProductionError):
            spec_for(serving_contract, **kwargs)

    def test_it_is_frozen(self, serving_contract):
        spec = spec_for(serving_contract)
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.event_time_field = "other"

    def test_to_obj_is_exactly_what_the_release_manifest_carries(self, serving_contract):
        spec = spec_for(serving_contract)
        obj = spec.to_obj()
        assert tuple(obj) == FEED_SPEC_KEYS
        assert obj["required_keys"] == list(UNIVERSE)
        assert obj["entity_key_fields"] == list(serving_contract.entity_key_fields)

    def test_from_obj_round_trips(self, serving_contract):
        spec = spec_for(serving_contract)
        assert FeedSpec.from_obj(spec.to_obj()) == spec

    def test_from_obj_is_default_deny(self, serving_contract):
        obj = spec_for(serving_contract).to_obj()
        obj["locator"] = "s3://bucket"
        with pytest.raises(ProductionError):
            FeedSpec.from_obj(obj)

    @pytest.mark.parametrize("field", FEED_SPEC_KEYS)
    def test_from_obj_refuses_a_missing_field(self, serving_contract, field):
        obj = spec_for(serving_contract).to_obj()
        del obj[field]
        with pytest.raises(ProductionError):
            FeedSpec.from_obj(obj)


# --------------------------------------------------------------------------
# snapshot_entry — the EntryBatch over the entry's exact outputs
# --------------------------------------------------------------------------


class TestSnapshot:
    def test_it_returns_an_entry_batch_over_the_required_universe(
        self, serving_contract, training_run
    ):
        spec = spec_for(serving_contract)
        batch = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        assert isinstance(batch, EntryBatch)
        assert set(batch.watermarks_by_key) == set(UNIVERSE)
        assert all(isinstance(w, InputWatermark) for w in batch.watermarks_by_key.values())

    def test_each_watermark_is_that_keys_latest_event_time(
        self, serving_contract, training_run
    ):
        spec = spec_for(serving_contract)
        outputs = entry_outputs_of(training_run)
        batch = snapshot_entry(serving_contract, spec, outputs, "s" * 64)
        field = serving_contract.event_time_field
        for key, watermark in batch.watermarks_by_key.items():
            expected = max(r[field] for r in outputs["records"] if r["instrument"] == key)
            assert watermark.key == key
            assert watermark.latest_asof_ms == expected

    def test_data_asof_ms_is_the_oldest_watermark(self, serving_contract, training_run):
        spec = spec_for(serving_contract)
        outputs = entry_outputs_of(training_run)
        field = serving_contract.event_time_field
        # Age one key deliberately: the batch must follow the OLDEST, so a
        # fresh instrument cannot hide a stale one (D6).
        for row in outputs["records"]:
            if row["instrument"] == UNIVERSE[1] and row[field] == LAST_ROW_MS:
                row[field] = LAST_ROW_MS - DAY_MS
        batch = snapshot_entry(serving_contract, spec, outputs, "s" * 64)
        oldest = min(w.latest_asof_ms for w in batch.watermarks_by_key.values())
        assert batch.data_asof_ms == oldest
        assert batch.data_asof_ms == LAST_ROW_MS - DAY_MS

    def test_the_inputs_digest_hashes_the_exact_entry_output(
        self, serving_contract, training_run
    ):
        spec = spec_for(serving_contract)
        outputs = entry_outputs_of(training_run)
        batch = snapshot_entry(serving_contract, spec, outputs, "s" * 64)
        assert batch.inputs_digest == canonical_hash(outputs)

    def test_the_required_keys_digest_is_the_specs(self, serving_contract, training_run):
        spec = spec_for(serving_contract)
        batch = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        assert batch.required_keys_digest == spec.required_keys_digest

    def test_the_source_config_hash_is_carried_through(self, serving_contract, training_run):
        spec = spec_for(serving_contract)
        batch = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        assert batch.source_config_hash == "s" * 64

    def test_the_snapshot_is_deterministic(self, serving_contract, training_run):
        spec = spec_for(serving_contract)
        one = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        two = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        assert one == two

    def test_mutating_one_row_moves_that_keys_digest_and_the_coverage_digest(
        self, serving_contract, training_run
    ):
        """The metadata hashes the EXACT rows descendants receive."""
        spec = spec_for(serving_contract)
        clean = entry_outputs_of(training_run)
        dirty = entry_outputs_of(training_run)
        target = next(r for r in dirty["records"] if r["instrument"] == UNIVERSE[0])
        target["value"] = target["value"] + 1
        before = snapshot_entry(serving_contract, spec, clean, "s" * 64)
        after = snapshot_entry(serving_contract, spec, dirty, "s" * 64)
        assert after.inputs_digest != before.inputs_digest
        assert after.coverage_digest != before.coverage_digest
        moved = {
            key
            for key in UNIVERSE
            if after.watermarks_by_key[key].source_digest
            != before.watermarks_by_key[key].source_digest
        }
        assert moved == {UNIVERSE[0]}

    def test_the_coverage_digest_moves_with_the_key_set(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        wide = snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)
        narrow_rows = {
            "records": [r for r in outputs["records"] if r["instrument"] == UNIVERSE[0]]
        }
        narrow = snapshot_entry(
            serving_contract, spec_for(serving_contract, keys=[UNIVERSE[0]]),
            narrow_rows, "s" * 64,
        )
        assert narrow.coverage_digest != wide.coverage_digest

    def test_every_digest_is_a_sha256_hex(self, serving_contract, training_run):
        spec = spec_for(serving_contract)
        batch = snapshot_entry(serving_contract, spec, entry_outputs_of(training_run), "s" * 64)
        digests = [batch.coverage_digest, batch.inputs_digest, batch.required_keys_digest]
        digests += [w.source_digest for w in batch.watermarks_by_key.values()]
        for digest in digests:
            assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")

    def test_a_missing_required_key_refuses_naming_it(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        outputs["records"] = [
            r for r in outputs["records"] if r["instrument"] != UNIVERSE[1]
        ]
        with pytest.raises(ProductionError) as exc:
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)
        assert UNIVERSE[1] in str(exc.value)

    def test_an_extra_key_refuses_naming_it(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        stray = dict(outputs["records"][0])
        stray["instrument"] = "INS9"
        outputs["records"].append(stray)
        with pytest.raises(ProductionError) as exc:
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)
        assert "INS9" in str(exc.value)

    def test_a_row_missing_the_entity_key_refuses(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        del outputs["records"][0]["instrument"]
        with pytest.raises(ProductionError):
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)

    @pytest.mark.parametrize(
        "value", [None, "2026-01-05", 1.5, True],
        ids=["null", "iso-text", "fractional", "bool"],
    )
    def test_a_malformed_event_time_refuses(self, serving_contract, training_run, value):
        outputs = entry_outputs_of(training_run)
        outputs["records"][0][serving_contract.event_time_field] = value
        with pytest.raises(ProductionError):
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)

    def test_a_row_missing_the_event_time_field_refuses(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        del outputs["records"][0][serving_contract.event_time_field]
        with pytest.raises(ProductionError):
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)

    def test_a_non_mapping_row_refuses(self, serving_contract, training_run):
        outputs = entry_outputs_of(training_run)
        outputs["records"].append(["INS1", 1])
        with pytest.raises(ProductionError):
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)

    @pytest.mark.parametrize(
        "outputs",
        [{}, {"verdict": "GO"}, {"records": [], "extra": []}],
        ids=["no-ports", "no-row-port", "two-row-ports"],
    )
    def test_outputs_without_exactly_one_row_stream_refuse(self, serving_contract, outputs):
        with pytest.raises(ProductionError):
            snapshot_entry(serving_contract, spec_for(serving_contract), outputs, "s" * 64)


# --------------------------------------------------------------------------
# EntrySourceFeed — construction and the default-deny params block
# --------------------------------------------------------------------------


class TestEntrySourceParams:
    def build(self, root, contract, clock, **over):
        registry = root.registry()
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        return feed_for(root, registry, contract, spec, clock, **over)

    def test_the_default_pull_mode_is_the_one_named_constant(
        self, fresh_root, serving_contract, clock, monkeypatch, recorder
    ):
        """The default is a NAME both validation and the run read (no literal)."""
        assert DEFAULT_PULL_MODE in PULL_MODES
        contract = drop_in_contract(serving_contract, fresh_root.root)
        monkeypatch.setattr(
            acquire_mod, "run_acquisition",
            recorder.hook("run_acquisition", {"acq_id": None, "records": 0}),
        )
        default = self.build(fresh_root, contract, clock)
        named = self.build(fresh_root, contract, clock, params={"pull": DEFAULT_PULL_MODE})
        default.pull(LAST_ROW_MS)
        before = len(recorder.named("run_acquisition"))
        named.pull(LAST_ROW_MS)
        assert len(recorder.named("run_acquisition")) == 2 * before

    @pytest.mark.parametrize("mode", PULL_MODES)
    def test_every_pull_mode_constructs_and_pulls(
        self, fresh_root, serving_contract, clock, monkeypatch, mode
    ):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        monkeypatch.setattr(
            acquire_mod, "run_acquisition", lambda *a, **k: {"acq_id": None, "records": 0}
        )
        feed = self.build(fresh_root, contract, clock, params={"pull": mode})
        assert feed.pull(LAST_ROW_MS).status in FEED_STATUSES

    def test_an_off_vocabulary_pull_mode_refuses(self, fresh_root, serving_contract, clock):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        with pytest.raises(ProductionError):
            self.build(fresh_root, contract, clock, params={"pull": "websocket"})

    def test_an_unknown_param_refuses_by_name(self, fresh_root, serving_contract, clock):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        with pytest.raises(ProductionError) as exc:
            self.build(fresh_root, contract, clock, params={"poll_ms": 500})
        assert "poll_ms" in str(exc.value)

    def test_a_root_that_disagrees_with_the_binding_refuses(
        self, fresh_root, serving_contract, clock
    ):
        """One normalized binding: acquisition and the entry read ONE locator."""
        with pytest.raises(ProductionError) as exc:
            feed_for(fresh_root, fresh_root.registry(), serving_contract,
                     spec_for(serving_contract), clock)
        assert fresh_root.root in str(exc.value)

    def test_the_matching_root_constructs(self, synthetic_root, serving_contract, clock):
        feed = feed_for(synthetic_root, synthetic_root.registry(), serving_contract,
                        spec_for(serving_contract), clock)
        assert isinstance(feed, EntrySourceFeed)

    @pytest.mark.parametrize("threshold", ["max_staleness_ms", "dead_after_ms"])
    def test_both_thresholds_are_required_keywords(
        self, synthetic_root, serving_contract, clock, threshold
    ):
        kwargs = {
            "root": synthetic_root, "registry": synthetic_root.registry(),
            "contract": serving_contract, "spec": spec_for(serving_contract),
            "clock": clock, "max_staleness_ms": DAY_MS, "dead_after_ms": 7 * DAY_MS,
        }
        del kwargs[threshold]
        with pytest.raises(TypeError):
            EntrySourceFeed({}, **kwargs)


# --------------------------------------------------------------------------
# D4 — the ACTIVE alias must still resolve to what the release pinned
# --------------------------------------------------------------------------


class TestSourceIdentity:
    def test_active_source_identity_answers_the_alias_version_id(self, synthetic_root):
        registry = synthetic_root.registry()
        digest, version = active_source_identity(registry, SOURCE)
        assert digest == acquire_mod.find_active_source(registry, SOURCE)
        assert isinstance(version, str) and version

    def test_activating_a_new_config_moves_both_halves(self, fresh_root, tmp_path):
        registry = fresh_root.registry()
        before = active_source_identity(registry, SOURCE)
        repoint(fresh_root, tmp_path, source_rows())
        after = active_source_identity(registry, SOURCE)
        assert after[0] != before[0]
        assert after[1] != before[1]

    @pytest.mark.parametrize("mode", PULL_MODES)
    def test_a_pull_refuses_when_the_active_config_hash_drifted(
        self, fresh_root, tmp_path, serving_contract, clock, monkeypatch, mode
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        repoint(fresh_root, tmp_path, source_rows())
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": mode})
        monkeypatch.setattr(acquire_mod, "run_acquisition", boom)
        with pytest.raises(ProductionError):
            feed.pull(NOW_MS)

    def test_a_pull_refuses_when_only_the_version_drifted(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        digest, version = active_source_identity(registry, SOURCE)
        spec = FeedSpec.from_contract(contract, UNIVERSE, digest, version + "-old")
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "store"})
        monkeypatch.setattr(acquire_mod, "run_acquisition", boom)
        with pytest.raises(ProductionError):
            feed.pull(NOW_MS)

    def test_the_identity_check_runs_before_any_fetch(
        self, fresh_root, tmp_path, serving_contract, clock, monkeypatch
    ):
        """A swapped config never delivers a row: no acquisition is attempted."""
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        repoint(fresh_root, tmp_path, source_rows())
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        monkeypatch.setattr(acquire_mod, "run_acquisition", boom)
        monkeypatch.setattr(observations_mod, "scan_stream", boom)
        with pytest.raises(ProductionError):
            feed.pull(NOW_MS)


def drop_in_contract(contract, root_dir):
    """The same contract, re-bound to another onboarding root."""
    from dskit.pipeline.node import ServingContract

    binding = dict(contract.source_binding)
    binding["root"] = root_dir
    return ServingContract(
        source_binding=binding,
        entity_key_fields=contract.entity_key_fields,
        event_time_field=contract.event_time_field,
        digest_recipe=dict(contract.digest_recipe),
    )


# --------------------------------------------------------------------------
# the two pull modes
# --------------------------------------------------------------------------


class TestAcquirePull:
    def test_it_calls_run_acquisition_live_through_the_binding(
        self, fresh_root, serving_contract, clock, monkeypatch, recorder
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        answer = {"acq_id": "acq-1", "records": 3}
        monkeypatch.setattr(
            acquire_mod, "run_acquisition", recorder.hook("run_acquisition", answer)
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        feed.pull(NOW_MS)
        calls = recorder.named("run_acquisition")
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert kwargs.get("mode") == "live" or "live" in args
        seen = locators(calls[0])
        assert fresh_root in seen and registry in seen
        assert contract.source_binding["source"] in seen
        assert contract.source_binding["stream"] in seen

    def test_the_summary_becomes_the_feed_results_acq_id_and_count(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        monkeypatch.setattr(
            acquire_mod, "run_acquisition",
            lambda *a, **k: {"acq_id": "acq-7", "records": 4},
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        result = feed.pull(NOW_MS)
        assert result.acq_id == "acq-7"
        assert result.records_added == 4
        assert result.source_config_hash == spec.source_config_hash

    def test_at_ms_is_the_injected_clocks_reading(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        monkeypatch.setattr(
            acquire_mod, "run_acquisition", lambda *a, **k: {"acq_id": None, "records": 0}
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        clock.advance(5_000)
        result = feed.pull(NOW_MS)
        assert result.at_ms == clock.now_ms() == NOW_MS + 5_000

    def test_zero_new_records_is_live_while_every_key_is_fresh(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        monkeypatch.setattr(
            acquire_mod, "run_acquisition", lambda *a, **k: {"acq_id": None, "records": 0}
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        result = feed.pull(LAST_ROW_MS + DAY_MS)
        assert result.records_added == 0
        assert result.status == "live"

    def test_a_connector_failure_is_dead_immediately_and_never_raises(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))

        def explode(*args, **kwargs):
            raise OSError("connection reset by peer")

        monkeypatch.setattr(acquire_mod, "run_acquisition", explode)
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        result = feed.pull(LAST_ROW_MS)
        assert result.status == "dead"
        assert result.acq_id is None and result.records_added == 0

    def test_a_link_failure_beats_a_fresh_watermark(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        monkeypatch.setattr(
            acquire_mod, "run_acquisition",
            lambda *a, **k: (_ for _ in ()).throw(TimeoutError("no route")),
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "acquire"})
        assert feed.pull(LAST_ROW_MS).status == "dead"


class TestStorePull:
    def test_it_never_acquires(
        self, fresh_root, serving_contract, clock, monkeypatch
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        monkeypatch.setattr(acquire_mod, "run_acquisition", boom)
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "store"})
        result = feed.pull(LAST_ROW_MS)
        assert result.acq_id is None and result.records_added == 0

    def test_it_derives_staleness_through_scan_stream(
        self, fresh_root, serving_contract, clock, monkeypatch, recorder
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        real = observations_mod.scan_stream
        monkeypatch.setattr(
            observations_mod, "scan_stream", recorder.hook("scan_stream", real)
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "store"})
        feed.pull(LAST_ROW_MS)
        calls = recorder.named("scan_stream")
        assert calls
        seen = locators(calls[0])
        assert contract.source_binding["root"] in seen
        assert contract.source_binding["source"] in seen
        assert contract.source_binding["stream"] in seen

    def test_rows_a_watch_process_landed_are_seen_on_the_next_pull(
        self, fresh_root, serving_contract, clock
    ):
        registry = fresh_root.registry()
        contract = drop_in_contract(serving_contract, fresh_root.root)
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        feed = feed_for(fresh_root, registry, contract, spec, clock,
                        params={"pull": "store"},
                        max_staleness_ms=1, dead_after_ms=DAY_MS)
        assert feed.pull(LAST_ROW_MS + 10 * DAY_MS).status == "dead"
        # A separate watch process lands a newer row.
        grown = source_rows() + [
            {"ts": "2026-01-20", "instrument": key, "value": 9.0,
             "side": "buy", "qty": 1, "confidence": 0.5}
            for key in UNIVERSE
        ]
        write_source_files(data_dir_of(fresh_root), grown)
        acquire_mod.run_acquisition(fresh_root, registry, SOURCE, STREAM, "live")
        assert feed.pull(iso_ms("2026-01-20")).status == "live"


# --------------------------------------------------------------------------
# the freshness ladder
# --------------------------------------------------------------------------


class TestFreshnessLadder:
    def ladder_feed(self, root, contract, clock, **over):
        registry = root.registry()
        spec = bound_spec(contract, acquire_mod.find_active_source(registry, SOURCE))
        return feed_for(root, registry, contract, spec, clock,
                        params={"pull": "store"}, **over)

    @pytest.mark.parametrize(
        "age,status",
        [
            (0, "live"),
            (DAY_MS - 1, "live"),
            (DAY_MS, "live"),
            (DAY_MS + 1, "stale"),
            (3 * DAY_MS, "stale"),
            (7 * DAY_MS, "stale"),
            (7 * DAY_MS + 1, "dead"),
        ],
    )
    def test_the_status_ladder_reads_the_oldest_watermark(
        self, fresh_root, serving_contract, clock, age, status
    ):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        feed = self.ladder_feed(fresh_root, contract, clock)
        assert feed.pull(LAST_ROW_MS + age).status == status

    def test_the_ladder_measures_against_the_tick_not_the_clock(
        self, fresh_root, serving_contract, clock
    ):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        feed = self.ladder_feed(fresh_root, contract, clock)
        fresh = feed.pull(LAST_ROW_MS)
        clock.advance(30 * DAY_MS)
        assert feed.pull(LAST_ROW_MS).status == fresh.status == "live"
        assert feed.pull(LAST_ROW_MS + 30 * DAY_MS).status == "dead"

    def test_one_fresh_key_cannot_hide_a_stale_one(
        self, tmp_path, serving_contract, clock
    ):
        """The ladder follows the OLDEST watermark, key by key (D6)."""
        rows = [r for r in source_rows() if not (r["instrument"] == UNIVERSE[1]
                                                 and r["ts"] > "2026-01-02")]
        root = build_onboarding_root(str(tmp_path / "lagging"), rows=rows)
        contract = drop_in_contract(serving_contract, root.root)
        feed = self.ladder_feed(root, contract, clock)
        # INS1 reaches 2026-01-05, INS2 stops at 2026-01-02.
        assert feed.pull(LAST_ROW_MS).status != "live"

    def test_a_required_key_the_store_does_not_carry_is_dead(
        self, fresh_root, serving_contract, clock
    ):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        registry = fresh_root.registry()
        spec = FeedSpec.from_contract(
            contract, list(UNIVERSE) + ["INS9"],
            acquire_mod.find_active_source(registry, SOURCE), VERSION,
        )
        feed = feed_for(fresh_root, registry, contract, spec, clock, params={"pull": "store"})
        assert feed.pull(LAST_ROW_MS).status == "dead"

    def test_every_status_it_can_report_is_in_the_vocabulary(
        self, fresh_root, serving_contract, clock
    ):
        contract = drop_in_contract(serving_contract, fresh_root.root)
        feed = self.ladder_feed(fresh_root, contract, clock)
        seen = {feed.pull(LAST_ROW_MS + age).status
                for age in (0, 2 * DAY_MS, 30 * DAY_MS)}
        assert seen <= set(FEED_STATUSES)
        assert seen == {"live", "stale", "dead"}


# --------------------------------------------------------------------------
# ReplayFeed
# --------------------------------------------------------------------------


def taped(training_run=None, serving_contract=None, statuses=("live", "stale")):
    """A tape of recorded `FeedResult`s — the whole of what a replay feed holds."""
    return [
        FeedResult(status=status, acq_id=f"acq-{i}", records_added=i,
                   source_config_hash="s" * 64, at_ms=NOW_MS + i)
        for i, status in enumerate(statuses)
    ]


class TestReplayFeed:
    def test_it_replays_the_recorded_results_in_order(self, training_run, serving_contract):
        tape = taped()
        feed = ReplayFeed({}, tape=tape)
        assert [feed.pull(NOW_MS + i) for i in range(len(tape))] == tape

    def test_a_pull_moves_the_instant_the_replay_clock_shares(self):
        """D20: `ReplayClock` "never advances itself — the feed will", so a
        replayed tick is evaluated at the instant the recording evaluated it
        rather than at the replay process's start."""
        clock = TestClock(start_ms=NOW_MS)
        replay_clock = ReplayClock(manual_time=clock.time)
        feed = ReplayFeed({}, tape=taped(statuses=("live", "live")), time=clock.time)
        feed.pull(NOW_MS)
        assert replay_clock.now_ms() == NOW_MS
        feed.pull(NOW_MS)
        assert replay_clock.now_ms() == NOW_MS + 1

    def test_a_tape_carrying_anything_but_a_feed_result_refuses(self):
        """The rows are NOT on the tape: §5.13 gives `read_entry` to the
        decider, which re-executes the entry against the same immutable
        onboarding root, and the recorded `inputs_digest` proves the re-read
        matched — a stronger claim than replaying a recorded blob."""
        with pytest.raises(ProductionError):
            ReplayFeed({}, tape=[(taped()[0], object())])

    def test_a_time_that_is_not_a_manual_time_refuses(self):
        with pytest.raises(ProductionError):
            ReplayFeed({}, tape=taped(), time="now")

    def test_an_exhausted_tape_refuses(self, training_run, serving_contract):
        tape = taped(statuses=("live",))
        feed = ReplayFeed({}, tape=tape)
        feed.pull(NOW_MS)
        with pytest.raises(ProductionError):
            feed.pull(NOW_MS + 1)

    def test_it_touches_neither_the_store_nor_the_connector(
        self, training_run, serving_contract, monkeypatch
    ):
        monkeypatch.setattr(acquire_mod, "run_acquisition", boom)
        monkeypatch.setattr(observations_mod, "scan_stream", boom)
        feed = ReplayFeed({}, tape=taped())
        assert feed.pull(NOW_MS).status == "live"

    def test_an_unknown_param_refuses(self, training_run, serving_contract):
        with pytest.raises(ProductionError):
            ReplayFeed({"speed": 2}, tape=taped(training_run, serving_contract))

    def test_a_malformed_tape_entry_refuses(self, training_run, serving_contract):
        with pytest.raises(ProductionError):
            ReplayFeed({}, tape=[("live", None)])

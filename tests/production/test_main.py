"""`python -m dskit.production` — the CLI (§7), written from §4–§7 alone.

What this file proves, in §8's words: "release, control proofs, normal-exit
mutating-verb journal rows, hard-kill gap reporting, queued receipts,
recovery verbs, no semantic overrides" — plus the §4.3 assertion §10 places
here because every registry exists only now: every `uses` / `kind` /
`measure` site in §4.1's grammar resolves through one of the twenty
registries.

The document these tests serve is the conftest synthetic run re-rooted under
`tmp_path` and re-graded so nothing reads a wall clock: `schedule.clock`
names the registered `test` kind (semantics live in the DOCUMENT, never in a
CLI option — §7), the feed pulls from the `store` rather than acquiring into
the session-scoped root, and the staleness bounds admit source rows a day
old. Everything else is `minimal_document`'s.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid

import pytest

from dskit.production import __main__ as cli
from dskit.production.accounting import ACCOUNTING_KINDS
from dskit.production.alerts import ALERT_SINK_KINDS
from dskit.production.arming import APPROVAL_KINDS
from dskit.production.base import ProductionError, canonical_hash, parse_utc_ms
from dskit.production.cadence import CADENCE_KINDS
from dskit.production.clock import CLOCK_KINDS
from dskit.production.control import ControlInbox
from dskit.production.coordination import LEASE_KINDS
from dskit.production.decider import PROPOSER_KINDS
from dskit.production.document import ServeDocument
from dskit.production.executor import EXECUTOR_KINDS, FEE_KINDS
from dskit.production.feed import FEED_KINDS
from dskit.production.guards import GUARD_KINDS, MEASURE_KINDS
from dskit.production.health import HEARTBEAT_KINDS, PROBE_KINDS, InstanceLock
from dskit.production.ledger import LEDGER_KINDS, ServeRoot
from dskit.production.monitors import (
    CHUNKER_KINDS,
    MONITOR_KINDS,
    REFERENCE_KINDS,
    THRESHOLD_KINDS,
)
from dskit.production.outcomes import OUTCOME_SOURCE_KINDS
from dskit.production.readiness import UNWAIVABLE_ITEMS, checklist_digest
from dskit.production.release import (
    DOCUMENT_FILENAME,
    RELEASE_FILENAME,
    ReleaseManifest,
)
from dskit.production.resilience import SIGNER_KINDS, TRANSPORT_KINDS
from dskit.production.sessions import CALENDAR_KINDS
from dskit.production.vocab import CONTROL_PURPOSES, TRIP_REASONS
from tests.production.conftest import DAY_MS, NOW_MS, SERIES_ID, UNIVERSE

# --------------------------------------------------------------------------
# §7's verb table, restated independently (a shipped test never reads the
# proposal document — §5.16)
# --------------------------------------------------------------------------

#: The seventeen phase-1 verbs of §7, in the order that table lists them.
#: `outcomes` is not here: it is a phase-2 row, listed below with the ones
#: still unregistered.
PHASE_ONE_VERBS = (
    "validate",
    "plan",
    "serve",
    "arm-request",
    "approve-arm",
    "disarm",
    "halt",
    "reduce",
    "flatten-request",
    "approve-flatten",
    "execute-flatten",
    "resume",
    "status",
    "verify",
    "reconcile",
    "adopt",
    "ready",
)

#: The six verbs §7 marks [phase 2]. `outcomes` is the one §5.13.2's join
#: now honours, so it is registered; the other five are not, and offering
#: one would advertise a control nothing would take.
PHASE_TWO_VERBS = ("replay", "outcomes", "report", "approve-hold", "ack", "silence")
LANDED_PHASE_TWO_VERBS = ("outcomes", "ack", "silence", "approve-hold")
UNLANDED_PHASE_TWO_VERBS = tuple(
    verb for verb in PHASE_TWO_VERBS if verb not in LANDED_PHASE_TWO_VERBS
)

#: §7: "Only operational flags live on `serve`".
SERVE_FLAGS = ("--once", "--max-ticks", "--armed")

#: The verbs D22 names as mutating, each writing exactly one journal row.
MUTATING_VERBS = (
    "plan",
    "ready",
    "arm-request",
    "approve-arm",
    "disarm",
    "halt",
    "reduce",
    "flatten-request",
    "approve-flatten",
    "execute-flatten",
    "resume",
    "reconcile",
    "adopt",
    # §5.13.2: the `outcomes` verb MUTATES — it appends `outcome` records —
    # so it journals once, like every other mutating verb (D22).
    "outcomes",
    # §5.11.2: both alert verbs append a record and both journal once.
    "ack",
    "silence",
    # §5.5.1: `approve-hold` appends a `guard_state` release and "journals
    # like every other mutating verb".
    "approve-hold",
)

#: The read-only verbs of §7 — no journal row, no writer lock.
READ_ONLY_VERBS = ("validate", "status", "verify")

#: §5.13's exit codes, restated: 0 stopped · 1 error · 3 halted ·
#: 4 already running · 5 refused.
STOPPED, ERROR, HALTED, ALREADY_RUNNING, REFUSED = 0, 1, 3, 4, 5

#: The env var D11's conjunction reads beside `--armed`.
ARM_ENV = "DSKIT_PRODUCTION_ARM"


# --------------------------------------------------------------------------
# documents, proofs and checklists
# --------------------------------------------------------------------------


def _deep_update(obj, overrides):
    """Apply dotted-path overrides to a nested document object."""
    for path, value in overrides.items():
        cursor = obj
        keys = path.split(".")
        for key in keys[:-1]:
            cursor = cursor[key]
        cursor[keys[-1]] = value
    return obj


def a_checklist(tmp_path, passing=True, name="readiness.json"):
    """Write a checklist whose six unwaivable foundations pass (or do not)."""
    items = [
        {
            "item": item,
            "required": True,
            "evidence": f"proved-{item}" if passing else "",
            "waiver": None,
        }
        for item in UNWAIVABLE_ITEMS
    ]
    path = tmp_path / name
    path.write_text(json.dumps(items), encoding="utf-8")
    return str(path)


def document_obj(serve_document, tmp_path, **overrides):
    """The conftest run re-rooted under `tmp_path`, on the injected test clock."""
    obj = serve_document.to_obj()
    obj["placement"] = {"ledger_root": str(tmp_path / "serve")}
    obj["readiness"] = dict(obj["readiness"], checklist=a_checklist(tmp_path))
    obj["feed"] = {"uses": "entry-source", "params": {"pull": "store"}}
    _deep_update(
        obj,
        {
            "schedule.clock": {"uses": "test", "params": {"start_ms": NOW_MS}},
            "schedule.cadence": {"uses": "fixed-interval", "params": {"period_ms": 60_000}},
            "schedule.max_staleness_ms": 10 * DAY_MS,
            "schedule.dead_after_ms": 30 * DAY_MS,
            "execution.uses": "shadow",
        },
    )
    return _deep_update(obj, overrides)


def write_document(tmp_path, obj, name="serve.json"):
    """Write a serve document and return its path."""
    path = tmp_path / name
    path.write_text(json.dumps(obj), encoding="utf-8")
    return str(path)


@pytest.fixture
def journal():
    """The injected D22 seam: a recording fake, never the real ledger."""

    class Rows:
        def __init__(self):
            self.rows = []

        def __call__(self, **kwargs):
            self.rows.append(kwargs)
            return None

    return Rows()


@pytest.fixture
def doc_path(serve_document, tmp_path):
    """A shadow serve document over the synthetic run, ready to plan."""
    return write_document(tmp_path, document_obj(serve_document, tmp_path))


@pytest.fixture
def proof(tmp_path):
    """A maker proof file; `proof.checker` is a DIFFERENT principal's."""

    class Proofs:
        def __init__(self, base):
            self.maker = str(base / "maker.proof")
            self.checker = str(base / "checker.proof")
            (base / "maker.proof").write_bytes(b"maker-signature")
            (base / "checker.proof").write_bytes(b"checker-signature")

    return Proofs(tmp_path)


def serve_root_of(doc_path):
    """The `ServeRoot` a document's placement and series name."""
    document = ServeDocument.load(doc_path)
    return ServeRoot(document.placement.ledger_root, document.series_id)


def planned(doc_path, journal):
    """Run `plan` and return the release manifest it wrote."""
    assert cli.main(["plan", doc_path], journal_hook=journal) == STOPPED
    return release_of(doc_path)


def release_of(doc_path):
    """The single release the series holds, read back from disk."""
    root = serve_root_of(doc_path)
    releases = os.path.dirname(root.release_dir("x"))
    hashes = sorted(os.listdir(releases))
    assert len(hashes) == 1, f"expected one release, found {hashes}"
    with open(os.path.join(releases, hashes[0], RELEASE_FILENAME), encoding="utf-8") as fh:
        return ReleaseManifest.from_obj(json.load(fh))


def receipts(doc_path):
    """Every terminal receipt in the series' spool, by request id."""
    root = serve_root_of(doc_path)
    found = {}
    for directory in (root.commands_applied, root.commands_rejected):
        for name in sorted(os.listdir(directory)):
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                found[name.removesuffix(".json")] = json.load(fh)
    return found


def queued(doc_path):
    """Every command still in the inbox, by request id."""
    root = serve_root_of(doc_path)
    found = {}
    for name in sorted(os.listdir(root.commands_inbox)):
        with open(os.path.join(root.commands_inbox, name), encoding="utf-8") as fh:
            found[name.removesuffix(".json")] = json.load(fh)
    return found


def last_report(capsys):
    """The LAST JSON object on stdout — a verb's report, after any earlier one.

    Several tests run `plan` (which prints its hashes) before the verb under
    test, and `capsys` hands back everything since the previous read.
    """
    out = capsys.readouterr().out
    decoder, index, found = json.JSONDecoder(), 0, []
    while index < len(out):
        while index < len(out) and out[index].isspace():
            index += 1
        if index >= len(out):
            break
        obj, index = decoder.raw_decode(out, index)
        found.append(obj)
    assert found, "the verb printed no JSON report"
    return found[-1]


def envelopes(doc_path, kind=None):
    """Every ledger envelope of the series, optionally of one record kind."""
    root = serve_root_of(doc_path)
    rows = []
    for _index, path in root.segment_paths():
        with open(path, encoding="utf-8") as fh:
            rows.extend(json.loads(line) for line in fh if line.strip())
    return [row for row in rows if kind is None or row["kind"] == kind]


# ==========================================================================
# The verb table — §7's rows, and nothing else
# ==========================================================================


def _subcommands(parser):
    """The subparser map of a top-level argparse parser."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("the CLI declares no subcommands")


def _options(subparser):
    """Every option string one verb's parser accepts."""
    return {
        option
        for action in subparser._actions
        for option in action.option_strings
        if option != "-h" and option != "--help"
    }


def test_every_phase_one_verb_of_section_7_is_registered():
    """§7's table is the CLI's contract: a verb an operator is told exists
    and that the CLI does not offer is a control path that cannot be taken."""
    assert set(_subcommands(cli.build_parser())) >= set(PHASE_ONE_VERBS)


def test_no_unhonoured_phase_two_verb_is_registered():
    """§7 marks six verbs [phase 2]; offering one whose owner does not exist
    yet would advertise a control an operator could take and nothing would
    honour. `outcomes` has its owner (§5.13.2), so it is offered."""
    offered = set(_subcommands(cli.build_parser()))
    assert not offered & set(UNLANDED_PHASE_TWO_VERBS), sorted(
        offered & set(UNLANDED_PHASE_TWO_VERBS)
    )
    assert set(LANDED_PHASE_TWO_VERBS) <= offered


def test_the_cli_offers_exactly_the_verbs_whose_owners_exist():
    """No verb beyond §7's table: an undeclared verb is an undocumented
    control surface."""
    assert sorted(_subcommands(cli.build_parser())) == sorted(
        PHASE_ONE_VERBS + LANDED_PHASE_TWO_VERBS
    )


def test_an_unknown_verb_refuses():
    """argparse's own default-deny — a typo is an error, not a default."""
    with pytest.raises(SystemExit) as exc:
        cli.main(["arm"])
    assert exc.value.code != 0


def test_only_operational_flags_live_on_serve():
    """§7: "Only operational flags live on `serve` (`--once`, `--max-ticks`,
    `--armed`). Adapter selection and every semantic knob live in the
    document." A fourth flag here is a semantics-changing option."""
    assert _options(_subcommands(cli.build_parser())["serve"]) == set(SERVE_FLAGS)


@pytest.mark.parametrize("verb", PHASE_ONE_VERBS)
def test_no_verb_offers_a_semantic_override(verb):
    """§7: "no CLI option silently changes semantics". Every knob below is
    a graded document field; an option that set one would let two runs of
    one release compute different things."""
    forbidden = {
        "--adapter", "--rung", "--executor", "--accounting", "--clock", "--calendar",
        "--cadence", "--feed", "--lease", "--approval", "--guard", "--guards",
        "--monitor", "--monitors", "--threshold", "--run-dir", "--heads", "--entry",
        "--universe", "--proposer", "--fsync", "--scope", "--venue", "--account",
        "--max-staleness-ms", "--submit-timeout-ms", "--checklist", "--series-id",
    }
    offered = _options(_subcommands(cli.build_parser())[verb])
    assert not offered & forbidden, sorted(offered & forbidden)


# ==========================================================================
# validate — §4.2's identity
# ==========================================================================


def test_validate_prints_the_documents_identity_hash(doc_path, capsys, journal):
    """§4.2: `doc_hash = config_hash(document, exclude=…)`. `validate` is the
    verb that shows it, so an operator can tell two documents apart before
    anything is planned."""
    assert cli.main(["validate", doc_path], journal_hook=journal) == STOPPED
    assert ServeDocument.load(doc_path).doc_hash in capsys.readouterr().out


def test_validate_refuses_a_document_with_an_unknown_top_level_key(tmp_path, serve_document,
                                                                   capsys, journal):
    """§4.2: "`validate` refuses a top-level key that is in neither list, so
    the partition cannot silently drift"."""
    obj = document_obj(serve_document, tmp_path)
    obj["telemetry"] = {"on": True}
    assert cli.main(["validate", write_document(tmp_path, obj)], journal_hook=journal) == ERROR
    assert "telemetry" in capsys.readouterr().err


def test_validate_writes_no_journal_row(doc_path, journal):
    """D22: "read-only verbs … do not journal"."""
    cli.main(["validate", doc_path], journal_hook=journal)
    assert journal.rows == []


def test_validate_never_creates_the_serve_root(doc_path, journal):
    """`validate` is shape and identity only — it must not materialise a
    series a `plan` has not yet earned."""
    cli.main(["validate", doc_path], journal_hook=journal)
    assert not os.path.exists(ServeDocument.load(doc_path).placement.ledger_root)


# ==========================================================================
# plan — the immutable release (§5.3.1, D24)
# ==========================================================================


def test_plan_writes_the_immutable_release_directory(doc_path, journal):
    """§5.3.1: `plan` "resolves and verifies those inputs once, writes
    `release.json`, then computes `release_hash = canonical_hash(manifest)`";
    §5.8's tree puts `document.json` beside it."""
    manifest = planned(doc_path, journal)
    home = serve_root_of(doc_path).release_dir(manifest.release_hash)
    assert os.path.isfile(os.path.join(home, RELEASE_FILENAME))
    assert os.path.isfile(os.path.join(home, DOCUMENT_FILENAME))


def test_plan_names_the_release_by_its_own_hash(doc_path, capsys, journal):
    """D24: the release subdirectory IS `release_hash`; an operator arms
    that value, so `plan` has to print it."""
    manifest = planned(doc_path, journal)
    assert manifest.release_hash in capsys.readouterr().out


def test_the_release_binds_the_documents_identity(doc_path, journal):
    """D24: the manifest contains `doc_hash` — the release is bound to the
    document that planned it, so a re-graded document cannot be served
    under it."""
    assert planned(doc_path, journal).doc_hash == ServeDocument.load(doc_path).doc_hash


def test_the_release_binds_the_served_run_and_its_serving_derivation(doc_path, run_dir, journal):
    """D24: `run/serving hashes`. The serving hash is the DERIVED document's,
    which is what a tick re-executes; the run hash is the training run's."""
    from dskit.pipeline.document import load_document

    from dskit.production.decider import serving_document, serving_registry
    from dskit.pipeline.planner import plan as plan_document

    document = ServeDocument.load(doc_path)
    run = load_document(os.path.join(run_dir, "config.json"))
    served = serving_document(run, run_dir, list(document.serving.heads), {})
    plan_document(served, serving_registry(None))
    manifest = planned(doc_path, journal)
    assert manifest.run_hash == run.hash
    assert manifest.serving_hash == served.hash


def test_plan_pins_the_checklists_contents_not_its_path(doc_path, journal):
    """D24: "`plan` canonicalises the file at `document.readiness.checklist`
    into `checklist_digest`, and `ready` refuses a checklist whose digest
    differs — without it `doc_hash` covers the path and not the contents"."""
    document = ServeDocument.load(doc_path)
    assert planned(doc_path, journal).checklist_digest == checklist_digest(
        document.readiness.checklist
    )


def test_plan_pins_the_universe_the_feed_must_cover(doc_path, journal):
    """§5.2/D24: `required_keys` come from the serve document and are
    normalized before hashing, so a tick's coverage is the release's."""
    spec = planned(doc_path, journal).feed_spec
    assert tuple(spec["required_keys"]) == tuple(sorted(UNIVERSE))
    assert spec["required_keys_digest"] == canonical_hash(sorted(UNIVERSE))


def test_plan_pins_the_active_source_identity(doc_path, source_config_hash, journal):
    """D24: "Every pull proves the pinned source hash". `plan` reads it
    from the onboarding registry the entry's contract names."""
    manifest = planned(doc_path, journal)
    assert manifest.source_config["hash"] == source_config_hash
    assert manifest.feed_spec["source_config_hash"] == source_config_hash


def test_plan_binds_the_documents_expected_execution_scope(doc_path, journal):
    """§5.3.1: the manifest carries the "graded expected `ExecutionScope`" —
    §5.7.2's scope equality is checked against the RELEASE, not the document."""
    document = ServeDocument.load(doc_path)
    assert planned(doc_path, journal).execution_scope == document.coordination.scope


def test_plan_captures_the_runtime_fingerprint(doc_path, journal):
    """D24: "`plan` also derives a `RuntimeFingerprint`: Python
    implementation/version/cache tag/ABI, platform and libc, project/lock
    digests, and the complete installed-distribution inventory"."""
    fingerprint = planned(doc_path, journal).runtime_fingerprint
    assert fingerprint.python_version == ".".join(str(p) for p in sys.version_info[:3])
    assert fingerprint.distributions


def test_plan_digests_every_artifact_the_release_reads(doc_path, run_dir, journal):
    """D24: "every artifact digest/timestamp". The served run pins a fitted
    sidecar; a release that named none could not verify what it loads."""
    from tests.production.conftest import SIDECAR, TRAINABLE_NODE

    manifest = planned(doc_path, journal)
    name = f"artifacts/{TRAINABLE_NODE}/{SIDECAR}"
    assert name in manifest.artifacts
    assert os.path.isfile(os.path.join(run_dir, name))


def test_planning_the_same_document_twice_is_idempotent(doc_path, journal):
    """§5.3.1 + `write_release`: a release directory is immutable. Under one
    injected clock the same document yields the same manifest, so a re-plan
    must land on the same hash rather than refuse or fork the series."""
    first = planned(doc_path, journal)
    assert cli.main(["plan", doc_path], journal_hook=journal) == STOPPED
    assert release_of(doc_path).release_hash == first.release_hash


def test_plan_refuses_a_document_whose_run_dir_holds_no_run(tmp_path, serve_document, journal):
    """§5.3: the serving derivation reads the run's own `config.json`; a
    missing run is an error, never an empty release."""
    obj = document_obj(serve_document, tmp_path,
                       **{"serving.run_dir": str(tmp_path / "absent")})
    assert cli.main(["plan", write_document(tmp_path, obj)], journal_hook=journal) == ERROR


def test_plan_journals_one_production_row(doc_path, journal):
    """D22: "This covers `plan` (it writes an immutable release, so it is a
    mutating verb)"."""
    planned(doc_path, journal)
    assert len(journal.rows) == 1


def test_a_refusing_plan_still_journals(tmp_path, serve_document, journal):
    """D22: "Each mutating CLI captures its queue/synchronous result and
    calls `record_production` exactly once in `finally`; it does not use the
    existing context manager because that freezes outputs before the
    attempt"."""
    obj = document_obj(serve_document, tmp_path,
                       **{"serving.run_dir": str(tmp_path / "absent")})
    cli.main(["plan", write_document(tmp_path, obj)], journal_hook=journal)
    assert len(journal.rows) == 1


def test_the_journal_row_names_the_serve_series_root(doc_path, journal):
    """D22: "`db_location` the serve-series root"."""
    planned(doc_path, journal)
    assert journal.rows[0]["db_location"] == serve_root_of(doc_path).series_path


def test_the_journal_step_names_the_verb_and_the_rung(doc_path, journal):
    """D22: "`step` naming the verb and rung (within the 80-character
    `_STEP_MAX`)"."""
    planned(doc_path, journal)
    step = journal.rows[0]["step"]
    assert "plan" in step and ServeDocument.load(doc_path).rung in step
    assert len(step) <= 80


@pytest.mark.parametrize("field", ("step", "inputs", "outputs", "db_location", "notes"))
def test_every_journal_field_is_a_string(doc_path, journal, field):
    """D22: `record_production(step, inputs, outputs, db_location, notes)`
    "has a fixed field set … and this plan adds none". Every column of the
    action ledger is a string; a list would be refused by the store, and the
    row D22 promises would silently never land."""
    planned(doc_path, journal)
    assert isinstance(journal.rows[0][field], str)


# ==========================================================================
# Control verbs — proofs, payloads and queued receipts (§5.8)
# ==========================================================================


def control_argv(verb, doc_path, proof, request_id, extra=None):
    """The argv one control verb needs, with its authenticated inputs."""
    needs_proof = {
        "arm-request": ["--until", "2026-01-06T04:00:00Z", "--allow", UNIVERSE[0],
                        "--proof", proof.maker],
        "approve-arm": ["--proof", proof.checker],
        "reduce": ["--proof", proof.maker],
        "flatten-request": ["--proof", proof.maker],
        "approve-flatten": ["--proof", proof.checker],
        "execute-flatten": ["--proof", proof.maker],
        "resume": ["--acknowledge", "trip:operator", "--proof", proof.maker],
        "adopt": ["--break", "break-1", "--flow-kind", "adjustment", "--external",
                  "--proof", proof.maker],
        "halt": ["--reason", "operator"],
        "disarm": [],
        "reconcile": [],
        "ready": [],
        "outcomes": [],
        "ack": ["--fingerprint", "feed-stale", "--proof", proof.maker],
        "silence": ["--matcher", "source=feed", "--until", "2026-01-06T04:00:00Z",
                    "--proof", proof.maker],
        "approve-hold": ["--guard", "size", "--scope", "*", "--proof", proof.maker],
    }[verb]
    return [verb, doc_path, *needs_proof, "--request-id", request_id, *(extra or [])]


#: Verb -> the `CONTROL_PURPOSES` member it queues (§5.8, §5.6).
VERB_PURPOSE = {
    "arm-request": "arm_request",
    "approve-arm": "arm_approval",
    "disarm": "disarm",
    "halt": "halt",
    "reduce": "reduce",
    "flatten-request": "flatten_request",
    "approve-flatten": "flatten_approval",
    "execute-flatten": "execute_flatten",
    "resume": "resume",
    "reconcile": "reconcile",
    "adopt": "adopt",
    "ready": "ready",
    "outcomes": "outcomes",
    "ack": "ack",
    "silence": "silence",
    "approve-hold": "approve_hold",
}


def test_every_control_purpose_has_a_verb():
    """§5.8: the inbox is "the sole write path from a control CLI to a
    running serve process". A purpose with no verb is a record kind nothing
    can produce."""
    assert sorted(VERB_PURPOSE.values()) == sorted(CONTROL_PURPOSES)


@pytest.mark.parametrize("verb", sorted(VERB_PURPOSE))
def test_every_control_verb_queues_a_command_under_its_own_purpose(
    verb, doc_path, proof, journal, tmp_path
):
    """§5.8: "The caller supplies a UUID operation `request_id` … the CLI
    canonicalizes the command, stores its independent payload digest, and
    writes `commands/inbox/<request_id>.json`". Whichever path the command
    then takes, exactly one command exists under this verb's purpose."""
    planned(doc_path, journal)
    extra = _extra(verb, tmp_path, doc_path, proof, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()  # a serve process holds the lock: the command is queued
    try:
        code = cli.main(control_argv(verb, doc_path, proof, request_id, extra),
                        journal_hook=journal)
    finally:
        lock.release()
    assert code == STOPPED
    assert queued(doc_path)[request_id]["purpose"] == VERB_PURPOSE[verb]


def _extra(verb, tmp_path, doc_path=None, proof=None, journal=None):
    """The extra arguments a verb needs beyond its proof.

    `approve-arm` names a request the checker approves, so the maker's own
    command has to exist in the spool first — the CLI rebuilds it to digest
    what the checker is signing (§5.6).
    """
    if verb == "flatten-request":
        path = tmp_path / "reduction.json"
        path.write_text(json.dumps(a_reduction_plan()), encoding="utf-8")
        return ["--plan", str(path)]
    if verb == "approve-arm":
        maker_id = str(uuid.uuid4())
        cli.main(control_argv("arm-request", doc_path, proof, maker_id), journal_hook=journal)
        return ["--request", maker_id]
    if verb == "approve-flatten":
        return ["--request", str(uuid.uuid4())]
    if verb == "execute-flatten":
        path = tmp_path / "authorized.json"
        path.write_text(json.dumps(a_reduction_plan()), encoding="utf-8")
        return ["--authorization", "a" * 64, "--plan", str(path)]
    return []


def a_reduction_plan():
    """An empty reduction plan object, as `--plan FILE` carries it (§5.4)."""
    return {
        "release_hash": "a" * 64,
        "risk_state_digest": "b" * 64,
        "intents": [],
        "reduction_intent_digests": [],
        "expires_ms": NOW_MS + 3_600_000,
    }


def test_a_queued_command_carries_its_own_payload_digest(doc_path, proof, journal):
    """§5.8: "stores its independent payload digest … replay after a crash
    is idempotent only when both `request_id` and payload digest match"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(control_argv("halt", doc_path, proof, request_id), journal_hook=journal)
    finally:
        lock.release()
    command = queued(doc_path)[request_id]
    assert command["payload_digest"] == canonical_hash(command["payload"])


def test_a_queued_command_binds_the_release_being_served(doc_path, proof, journal):
    """§5.8/D24: "every submit checks manifest, arming, permit and lease
    bindings agree" — a command names the release it was raised against."""
    manifest = planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(control_argv("reconcile", doc_path, proof, request_id), journal_hook=journal)
    finally:
        lock.release()
    assert queued(doc_path)[request_id]["release_hash"] == manifest.release_hash


def test_an_authenticated_verb_reads_its_proof_from_the_named_file(doc_path, proof, journal):
    """§5.6: proofs are files an operator signs; the CLI carries the BYTES
    into the spool and §6 keeps them off the chain."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(control_argv("reduce", doc_path, proof, request_id), journal_hook=journal)
    finally:
        lock.release()
    import base64

    assert base64.b64decode(queued(doc_path)[request_id]["proof"]) == b"maker-signature"


def test_an_authenticated_verb_refuses_a_missing_proof_file(doc_path, proof, tmp_path, journal):
    """A proof that cannot be read is an error, never an empty proof: an
    unauthenticated transition is exactly what D11 forbids."""
    planned(doc_path, journal)

    class Absent:
        maker = checker = str(tmp_path / "no-such.proof")

    assert cli.main(control_argv("reduce", doc_path, Absent, str(uuid.uuid4())),
                    journal_hook=journal) == ERROR


def test_a_proof_never_reaches_stdout_or_stderr(doc_path, proof, capsys, journal):
    """§5.0/redact: "proof bytes never appear in a log line, alert body or
    ledger record" — nor in what an operator's terminal or CI captures."""
    planned(doc_path, journal)
    cli.main(control_argv("reduce", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    captured = capsys.readouterr()
    assert "maker-signature" not in captured.out + captured.err


def test_no_ledger_record_carries_a_proofs_bytes(doc_path, proof, journal):
    """§6: the chain carries `proof_digest`, never the proof."""
    planned(doc_path, journal)
    cli.main(control_argv("halt", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    assert "maker-signature" not in json.dumps(envelopes(doc_path))


def test_a_retry_of_one_request_id_returns_the_same_answer(doc_path, proof, journal):
    """§5.8: "The caller supplies a UUID operation `request_id` and reuses it
    only for retries" — a retry is idempotent, never a second act."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    argv = control_argv("reconcile", doc_path, proof, request_id)
    first = cli.main(argv, journal_hook=journal)
    second = cli.main(argv, journal_hook=journal)
    assert (first, second) == (STOPPED, STOPPED)
    assert len(receipts(doc_path)) + len(queued(doc_path)) == 1


def test_reusing_a_request_id_for_a_different_payload_refuses(doc_path, proof, journal):
    """§5.8: "reuse with a different payload refuses"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    cli.main(control_argv("halt", doc_path, proof, request_id,
                          ["--reason", "operator"]), journal_hook=journal)
    assert cli.main(control_argv("halt", doc_path, proof, request_id,
                                 ["--reason", "guard_halt"]), journal_hook=journal) == ERROR


# ==========================================================================
# halt — the out-of-band kill switch (§5.6, D12)
# ==========================================================================


def test_halt_creates_the_sentinel_before_it_queues_its_audit_command(
    doc_path, proof, journal, monkeypatch
):
    """§5.6: "`halt` additionally creates the out-of-band `HALT` sentinel
    atomically BEFORE queueing its audit command … Thus stopping does not
    depend on the decision loop, inbox health, or ledger availability"."""
    planned(doc_path, journal)
    sentinel = serve_root_of(doc_path).halt_sentinel
    seen = {}
    original = ControlInbox.queue

    def watched(self, command):
        seen["sentinel_at_queue"] = os.path.exists(sentinel)
        return original(self, command)

    monkeypatch.setattr(ControlInbox, "queue", watched)
    cli.main(control_argv("halt", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    assert seen["sentinel_at_queue"] is True


def test_halt_leaves_the_sentinel_on_when_the_spool_refuses(
    doc_path, proof, journal, monkeypatch
):
    """§5.6: the kill switch does not depend on "inbox health" — a spool
    that refuses must not undo the stop an operator already asked for."""
    planned(doc_path, journal)

    def refuse(self, command):
        raise ProductionError(["the spool is unwritable"])

    monkeypatch.setattr(ControlInbox, "queue", refuse)
    code = cli.main(control_argv("halt", doc_path, proof, str(uuid.uuid4())),
                    journal_hook=journal)
    assert code == ERROR
    assert os.path.exists(serve_root_of(doc_path).halt_sentinel)


def test_halt_refuses_a_reason_outside_the_closed_vocabulary(doc_path, proof, journal):
    """§5.6's trip reasons are closed; an invented reason is a record kind
    nothing downstream can classify."""
    with pytest.raises(SystemExit):
        cli.main(["halt", doc_path, "--reason", "because", "--request-id", str(uuid.uuid4())],
                 journal_hook=journal)


def test_every_trip_reason_is_offered_by_halt():
    """`--reason` is default-deny over the closed set, not free text."""
    action = [
        action
        for action in _subcommands(cli.build_parser())["halt"]._actions
        if "--reason" in action.option_strings
    ]
    assert action and set(action[0].choices) == set(TRIP_REASONS)


# ==========================================================================
# Synchronous application when no serve process holds the lock (§5.8)
# ==========================================================================


def test_a_control_verb_applies_synchronously_when_nothing_holds_the_lock(
    doc_path, proof, journal
):
    """§5.8: "If no serve process owns the lock, non-executing commands may
    acquire it and run the same `CommandProcessor` synchronously" — the
    serving process remains the sole ledger writer either way."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    assert cli.main(control_argv("halt", doc_path, proof, request_id),
                    journal_hook=journal) == STOPPED
    assert receipts(doc_path)[request_id]["status"] == "applied"


def test_a_synchronously_applied_command_reaches_the_chain(doc_path, proof, journal):
    """§5.8: the writer "appends and barriers the resulting records as the
    sole ledger writer, then atomically moves the file"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    cli.main(control_argv("halt", doc_path, proof, request_id), journal_hook=journal)
    requests = envelopes(doc_path, "control_request")
    assert [row["body"]["request_id"] for row in requests] == [request_id]


def test_a_command_the_series_state_refuses_exits_refused(doc_path, proof, journal):
    """§5.13: exit 5 is "a control verb refused because the series state
    forbids it" — `resume` acknowledges a trip that never happened."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    code = cli.main(control_argv("resume", doc_path, proof, request_id), journal_hook=journal)
    assert code == REFUSED
    assert receipts(doc_path)[request_id]["status"] == "rejected"


def test_a_queued_command_exits_zero_even_though_nothing_has_applied_it(
    doc_path, proof, journal
):
    """§7: "For mutating verbs, exit 0 means the request is durably queued
    or synchronously applied, not that an asynchronously queued command has
    taken effect"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        code = cli.main(control_argv("resume", doc_path, proof, request_id),
                        journal_hook=journal)
    finally:
        lock.release()
    assert code == STOPPED
    assert request_id in queued(doc_path) and request_id not in receipts(doc_path)


def test_execute_flatten_is_never_applied_synchronously(doc_path, proof, journal, tmp_path):
    """§5.8: "`execute-flatten` requires an active ready loop" — the CLI may
    take the lock for a NON-EXECUTING command only, so this one stays queued
    for the loop that owns its sequential cycle."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    code = cli.main(
        control_argv("execute-flatten", doc_path, proof, request_id,
                     _extra("execute-flatten", tmp_path, doc_path, proof, journal)),
        journal_hook=journal,
    )
    assert code == STOPPED
    assert request_id in queued(doc_path)


def test_execute_flatten_carries_the_signed_plan_in_its_payload(
    doc_path, proof, journal, tmp_path
):
    """§5.13: a reduction cycle "carries only the plan's legs, in
    maker-approved `index` order" — the loop reads that plan out of the
    consumed command, so the command has to carry it."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    path = tmp_path / "signed-plan.json"
    path.write_text(json.dumps(a_reduction_plan()), encoding="utf-8")
    cli.main(
        ["execute-flatten", doc_path, "--authorization", "a" * 64, "--plan", str(path),
         "--proof", proof.maker, "--request-id", request_id],
        journal_hook=journal,
    )
    assert queued(doc_path)[request_id]["payload"]["plan"] == a_reduction_plan()


# ==========================================================================
# ready — the release-bound GO (§5.13)
# ==========================================================================


def test_ready_records_the_verdict_and_exits_zero_on_go(doc_path, journal):
    """§5.13: "The evaluation is appended as a `readiness` record (§6) and
    barriered, so the GO is durable, release-bound and expiring"."""
    planned(doc_path, journal)
    assert cli.main(["ready", doc_path, "--request-id", str(uuid.uuid4())],
                    journal_hook=journal) == STOPPED
    assert [row["body"]["verdict"] for row in envelopes(doc_path, "readiness")] == ["go"]


def test_ready_exits_refused_on_no_go(tmp_path, serve_document, journal):
    """§5.13: "The checklist is a JSON file … evaluated to GO / NO-GO;
    NO-GO exits 5" — nothing is wrong, the checklist is simply not yet
    satisfied, which is why 5 and not 3."""
    obj = document_obj(serve_document, tmp_path)
    obj["readiness"] = dict(obj["readiness"], checklist=a_checklist(tmp_path, passing=False,
                                                                   name="nogo.json"))
    path = write_document(tmp_path, obj)
    planned(path, journal)
    assert cli.main(["ready", path, "--request-id", str(uuid.uuid4())],
                    journal_hook=journal) == REFUSED


def test_ready_refuses_a_checklist_whose_contents_changed(doc_path, journal):
    """D24: "`ready` refuses a checklist whose digest differs — without it
    `doc_hash` covers the path and not the contents, so a GO could be
    re-earned against a quietly shortened checklist"."""
    planned(doc_path, journal)
    document = ServeDocument.load(doc_path)
    with open(document.readiness.checklist, encoding="utf-8") as fh:
        items = json.load(fh)
    items[0]["evidence"] = "quietly-changed"
    with open(document.readiness.checklist, "w", encoding="utf-8") as fh:
        json.dump(items, fh)
    assert cli.main(["ready", doc_path, "--request-id", str(uuid.uuid4())],
                    journal_hook=journal) == REFUSED


# ==========================================================================
# Journal rows — D22's one row per mutating verb
# ==========================================================================


@pytest.mark.parametrize("verb", sorted(set(MUTATING_VERBS) - {"plan"}))
def test_every_mutating_verb_writes_exactly_one_journal_row(
    verb, doc_path, proof, journal, tmp_path
):
    """D22: "Each mutating CLI captures its queue/synchronous result and
    calls `record_production` exactly once in `finally`". Once — a second
    row would double-count an operator act in the child's ledger."""
    planned(doc_path, journal)
    extra = _extra(verb, tmp_path, doc_path, proof, journal)
    journal.rows.clear()
    cli.main(control_argv(verb, doc_path, proof, str(uuid.uuid4()), extra),
             journal_hook=journal)
    assert len(journal.rows) == 1


@pytest.mark.parametrize("verb", READ_ONLY_VERBS)
def test_no_read_only_verb_journals(verb, doc_path, journal):
    """D22: "read-only verbs — including phase 2's `report` and `replay` —
    do not journal"."""
    planned(doc_path, journal)
    journal.rows.clear()
    cli.main([verb, doc_path], journal_hook=journal)
    assert journal.rows == []


def test_a_control_verbs_journal_row_names_its_request(doc_path, proof, journal):
    """D22: each mutating CLI "captures its queue/synchronous result".
    `verify` reconciles the spool against those rows, so the row has to name
    the request whose outcome it recorded."""
    planned(doc_path, journal)
    journal.rows.clear()
    request_id = str(uuid.uuid4())
    cli.main(control_argv("reconcile", doc_path, proof, request_id), journal_hook=journal)
    assert request_id in journal.rows[0]["outputs"]


def test_the_real_journal_seam_is_reached_without_pytest(doc_path, tmp_path):
    """D22: "`dskit/journal/record.py` returns `None` under pytest before
    touching any store … tests assert against a recording fake and one
    non-pytest subprocess case proves the real call path". A hook that only
    ever ran against a fake would prove nothing about production."""
    # The default hook IS the journal's own `record_production`; outside a
    # child journal it answers None, so what this proves is that the real
    # function was called once, on a real (non-pytest) interpreter.
    script = tmp_path / "real_hook.py"
    script.write_text(
        "import sys\n"
        "import dskit.journal.hooks as hooks\n"
        "from dskit.production import __main__ as cli\n"
        "seen = []\n"
        "real = hooks.record_production\n"
        "def spy(**kw):\n"
        "    seen.append(kw)\n"
        "    return real(**kw)\n"
        "hooks.record_production = spy\n"
        "code = cli.main(['plan', sys.argv[1]])\n"
        "print(f'code={code} rows={len(seen)}')\n"
        "sys.exit(0 if code == 0 and len(seen) == 1 else 9)\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONPATH"] = os.getcwd()
    done = subprocess.run(
        [sys.executable, str(script), doc_path], capture_output=True, text=True, env=env,
        cwd=os.getcwd(), check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr


# ==========================================================================
# serve — the loop's exit codes (§5.13)
# ==========================================================================


def test_serve_runs_the_bounded_invocation_and_stops(doc_path, journal):
    """§5.13: "`--once` runs one tick; `--max-ticks N` bounds completed
    ticks", and a graceful stop is exit 0."""
    planned(doc_path, journal)
    assert cli.main(["serve", doc_path, "--max-ticks", "1"], journal_hook=journal) == STOPPED


def test_serve_records_one_terminal_tick_per_tick_start(doc_path, journal):
    """§5.13: "every started tick eventually has exactly one terminal `tick`
    and one `decision`"."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--max-ticks", "2"], journal_hook=journal)
    starts = [row["body"]["tick_id"] for row in envelopes(doc_path, "tick_start")]
    assert len(starts) == 2
    assert sorted(row["body"]["tick_id"] for row in envelopes(doc_path, "tick")) == sorted(starts)
    assert sorted(row["body"]["tick_id"] for row in envelopes(doc_path, "decision")) == sorted(starts)


def test_serve_exits_already_running_when_another_writer_holds_the_lock(doc_path, journal):
    """§5.13: exit 4 is `already running`. §5.8: "Only the process holding
    `serve.lock` may open the ledger for append; there is never a concurrent
    CLI ledger writer"."""
    planned(doc_path, journal)
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        assert cli.main(["serve", doc_path, "--once"], journal_hook=journal) == ALREADY_RUNNING
    finally:
        lock.release()


def test_serve_exits_halted_when_the_kill_switch_is_on(doc_path, proof, journal):
    """§5.13: exit 3 is `halted` — "a breaker-halted series needs operator
    action and refuses submissions", which is a different fact from a
    readiness NO-GO."""
    planned(doc_path, journal)
    cli.main(control_argv("halt", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    assert cli.main(["serve", doc_path, "--once"], journal_hook=journal) == HALTED


def test_serve_journals_the_process_and_its_final_head(doc_path, journal):
    """D22: "the process id plus final ledger head rendered into `notes` in
    one documented `production-v1 process=<id> head=<seq>:<hash>` form that
    `verify` parses back"."""
    planned(doc_path, journal)
    journal.rows.clear()
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    notes = journal.rows[-1]["notes"]
    head = envelopes(doc_path)[-1]
    assert notes.startswith("production-v1 process=")
    assert f"head={head['seq']}:{head['hash']}" in notes


def test_serve_never_journals_a_tick(doc_path, journal):
    """D22: "Serve never journals a tick or consumed command" — one
    production row per normally completed process."""
    planned(doc_path, journal)
    journal.rows.clear()
    cli.main(["serve", doc_path, "--max-ticks", "3"], journal_hook=journal)
    assert len(journal.rows) == 1


def test_serve_refuses_when_the_document_has_no_release(doc_path, journal):
    """§7: `serve` runs "the loop against the document's release"; a
    document that was never planned has none, and inventing one would serve
    unverified content (D24)."""
    assert cli.main(["serve", doc_path, "--once"], journal_hook=journal) == ERROR


def test_serve_builds_the_invocation_from_its_flags_and_the_environment(
    doc_path, journal, monkeypatch
):
    """§5.13: "`invocation` is the frozen `Invocation{armed,
    env_release_hash, once, max_ticks}` that `__main__` builds from
    `--armed`, `DSKIT_PRODUCTION_ARM`, `--once` and `--max-ticks`; without
    it `Arming.check_conjunction` would be evaluated by an object that
    cannot see two of its three inputs"."""
    manifest = planned(doc_path, journal)
    monkeypatch.setenv(ARM_ENV, manifest.release_hash)
    seen = {}
    original = cli.bundles_for

    def watched(*args, **kwargs):
        seen["invocation"] = kwargs["invocation"]
        return original(*args, **kwargs)

    monkeypatch.setattr(cli, "bundles_for", watched)
    cli.main(["serve", doc_path, "--armed", "--max-ticks", "1"], journal_hook=journal)
    invocation = seen["invocation"]
    assert (invocation.armed, invocation.env_release_hash) == (True, manifest.release_hash)
    assert (invocation.once, invocation.max_ticks) == (False, 1)


def test_an_unarmed_serve_reports_no_arm_and_no_environment_hash(doc_path, journal, monkeypatch):
    """The conjunction's three inputs are FACTS about how the process was
    invoked; absent must read as absent, never as a default arm."""
    planned(doc_path, journal)
    monkeypatch.delenv(ARM_ENV, raising=False)
    seen = {}
    original = cli.bundles_for
    monkeypatch.setattr(
        cli, "bundles_for",
        lambda *a, **k: (seen.setdefault("invocation", k["invocation"]), original(*a, **k))[1],
    )
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    assert seen["invocation"].armed is False
    assert seen["invocation"].env_release_hash is None
    assert seen["invocation"].once is True


# ==========================================================================
# status — read-only, and it never takes the writer lock (§7)
# ==========================================================================


def test_status_reports_the_rung_and_the_series(doc_path, capsys, journal):
    """§7: `status` shows "rung, breaker, health, last tick, pending refs,
    control inbox/results, head hash"."""
    planned(doc_path, journal)
    assert cli.main(["status", doc_path], journal_hook=journal) == STOPPED
    report = last_report(capsys)
    assert report["rung"] == ServeDocument.load(doc_path).rung
    assert report["series_id"] == SERIES_ID


def test_status_answers_while_a_serve_process_holds_the_lock(doc_path, capsys, journal):
    """§7: "Read-only verbs never take the writer lock" — `status` is the
    verb an operator runs WHILE the loop is serving, so contending for the
    lock would make it useless exactly when it is needed."""
    planned(doc_path, journal)
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        code = cli.main(["status", doc_path], journal_hook=journal)
    finally:
        lock.release()
    assert code == STOPPED
    assert last_report(capsys)["rung"]


def test_status_shows_a_pending_command_and_then_its_terminal_receipt(
    doc_path, proof, capsys, journal
):
    """§7: "`status` shows its terminal receipt" — the answer to "did my
    queued act take effect?"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(control_argv("reconcile", doc_path, proof, request_id), journal_hook=journal)
        cli.main(["status", doc_path], journal_hook=journal)
        pending = last_report(capsys)["control"]["pending"]
    finally:
        lock.release()
    assert [entry["request_id"] for entry in pending] == [request_id]
    cli.main(control_argv("reconcile", doc_path, proof, request_id), journal_hook=journal)
    cli.main(["status", doc_path], journal_hook=journal)
    results = last_report(capsys)["control"]["results"]
    assert results[0]["request_id"] == request_id
    assert results[0]["status"] in ("applied", "rejected")


def test_status_reports_the_head_the_series_reached(doc_path, journal, capsys):
    """§5.8: "Every cache also names its projected `head_seq/head_hash`" —
    the head an operator quotes when comparing a running series to its
    journal anchor."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    cli.main(["status", doc_path], journal_hook=journal)
    report = last_report(capsys)
    assert report["head"]["seq"] == envelopes(doc_path)[-1]["seq"]


def test_status_reports_the_kill_switch(doc_path, proof, journal, capsys):
    """§5.6: "the kill-switch file `HALT` in the serve root" is a fact an
    operator must be able to read without a running loop."""
    planned(doc_path, journal)
    cli.main(control_argv("halt", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    cli.main(["status", doc_path], journal_hook=journal)
    assert last_report(capsys)["halt_sentinel"] is True


# ==========================================================================
# verify — the chain, the journal anchor and the hard-kill gap
# ==========================================================================


def test_verify_accepts_an_undamaged_chain(doc_path, journal, capsys):
    """§7: `verify` "walk[s] the ledger chain"; §5.8: `verify()` returns
    "`first_bad_seq | None`"."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    assert cli.main(["verify", doc_path], journal_hook=journal) == STOPPED
    assert last_report(capsys)["first_bad_seq"] is None


def test_verify_locates_an_edited_record(doc_path, journal, capsys):
    """§5.8: "`verify()` returns `first_bad_seq | None` — the seq the walk
    EXPECTED at the first failing position"."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    root = serve_root_of(doc_path)
    _index, path = root.segment_paths()[0]
    lines = open(path, encoding="utf-8").read().splitlines()
    row = json.loads(lines[1])
    row["body"] = dict(row["body"], tampered=True)
    lines[1] = json.dumps(row)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    assert cli.main(["verify", doc_path], journal_hook=journal) == ERROR
    assert last_report(capsys)["first_bad_seq"] == 2


def test_verify_compares_the_head_to_the_journal_anchor(doc_path, journal, capsys):
    """§7: `verify` "compare[s] the head to the journal anchor". D22: the
    anchor is `production-v1 process=<id> head=<seq>:<hash>`."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    anchor = journal.rows[-1]["notes"]
    cli.main(["verify", doc_path, "--anchor", anchor], journal_hook=journal)
    assert last_report(capsys)["anchor"]["matches"] is True


def test_verify_refuses_an_anchor_that_is_not_on_the_chain(doc_path, journal, capsys):
    """A journal row claiming a head the chain never held is tamper
    evidence — the whole point of anchoring one store in the other."""
    planned(doc_path, journal)
    cli.main(["serve", doc_path, "--once"], journal_hook=journal)
    forged = f"production-v1 process=p1 head={envelopes(doc_path)[-1]['seq']}:{'0' * 64}"
    assert cli.main(["verify", doc_path, "--anchor", forged], journal_hook=journal) == ERROR
    assert last_report(capsys)["anchor"]["matches"] is False


def test_verify_reports_a_command_that_has_no_receipt(doc_path, proof, journal, capsys):
    """D22: "SIGKILL/power loss can leave a durable inbox command without a
    CLI journal row because the stores have no shared transaction; `verify`
    reports that gap"."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(control_argv("disarm", doc_path, proof, request_id), journal_hook=journal)
    finally:
        lock.release()
    cli.main(["verify", doc_path], journal_hook=journal)
    assert last_report(capsys)["commands_without_receipt"] == [request_id]


def test_verify_reports_no_gap_once_the_command_is_consumed(doc_path, proof, journal, capsys):
    """The gap is the SIGKILL case, not the ordinary pending one: a command
    that reached a terminal receipt is not a gap."""
    planned(doc_path, journal)
    cli.main(control_argv("disarm", doc_path, proof, str(uuid.uuid4())), journal_hook=journal)
    cli.main(["verify", doc_path], journal_hook=journal)
    assert last_report(capsys)["commands_without_receipt"] == []


# ==========================================================================
# The recovery verbs — reconcile and adopt (§5.9, D13)
# ==========================================================================


def test_reconcile_queues_one_reconciliation(doc_path, proof, journal):
    """§7: `reconcile <doc>` "queue[s] reconciliation"; §5.9 owns what it
    then does."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    assert cli.main(control_argv("reconcile", doc_path, proof, request_id),
                    journal_hook=journal) == STOPPED
    assert receipts(doc_path)[request_id]["purpose"] == "reconcile"


def test_adopt_carries_the_named_breaks_and_the_operators_classification(
    doc_path, proof, journal
):
    """§7: `adopt <doc> --break ID… --proof FILE` queues "authenticated
    adoption of named breaks"; §6 requires the cash-flow kind and the
    ours/external origin to come from the operator, never a default."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(
            ["adopt", doc_path, "--break", "break-1", "--break", "break-2",
             "--flow-kind", "deposit", "--external", "--proof", proof.maker,
             "--request-id", request_id],
            journal_hook=journal,
        )
    finally:
        lock.release()
    payload = queued(doc_path)[request_id]["payload"]
    assert payload["break_ids"] == ["break-1", "break-2"]
    assert payload["flow_kind"] == "deposit"
    assert payload["external"] is True


def test_adopt_requires_a_cash_flow_kind(doc_path, proof, journal):
    """§6: `CASH_FLOW_KINDS` are "the only kinds `adopt` can emit"; a
    default would let an operator bank money under a kind they never chose."""
    planned(doc_path, journal)
    with pytest.raises(SystemExit):
        cli.main(["adopt", doc_path, "--break", "b1", "--external", "--proof", proof.maker],
                 journal_hook=journal)


def test_adopt_requires_the_breaks_origin(doc_path, proof, journal):
    """§6's `adoption` record names whether the break was ours or the
    venue's; `--external` must be stated, never assumed."""
    planned(doc_path, journal)
    with pytest.raises(SystemExit):
        cli.main(["adopt", doc_path, "--break", "b1", "--flow-kind", "deposit",
                  "--proof", proof.maker], journal_hook=journal)


# ==========================================================================
# §4.3 — every selector in the grammar names a registry
# ==========================================================================

#: §4.3's families, restated: name -> the registry object. Twenty in phase
#: 1, plus the three §4.3 says phase 2 adds — `OUTCOME_SOURCE_KINDS`,
#: `LEDGER_KINDS` (§5.8.2) and `SIGNER_KINDS` (§5.12.1).
REGISTRIES = {
    "CLOCK_KINDS": CLOCK_KINDS,
    "CALENDAR_KINDS": CALENDAR_KINDS,
    "CADENCE_KINDS": CADENCE_KINDS,
    "FEED_KINDS": FEED_KINDS,
    "PROPOSER_KINDS": PROPOSER_KINDS,
    "GUARD_KINDS": GUARD_KINDS,
    "MEASURE_KINDS": MEASURE_KINDS,
    "EXECUTOR_KINDS": EXECUTOR_KINDS,
    "ACCOUNTING_KINDS": ACCOUNTING_KINDS,
    "APPROVAL_KINDS": APPROVAL_KINDS,
    "LEASE_KINDS": LEASE_KINDS,
    "MONITOR_KINDS": MONITOR_KINDS,
    "REFERENCE_KINDS": REFERENCE_KINDS,
    "CHUNKER_KINDS": CHUNKER_KINDS,
    "THRESHOLD_KINDS": THRESHOLD_KINDS,
    "PROBE_KINDS": PROBE_KINDS,
    "ALERT_SINK_KINDS": ALERT_SINK_KINDS,
    "HEARTBEAT_KINDS": HEARTBEAT_KINDS,
    "TRANSPORT_KINDS": TRANSPORT_KINDS,
    "FEE_KINDS": FEE_KINDS,
    "OUTCOME_SOURCE_KINDS": OUTCOME_SOURCE_KINDS,
    "LEDGER_KINDS": LEDGER_KINDS,
    "SIGNER_KINDS": SIGNER_KINDS,
}

#: Every `uses` / `kind` / `measure` site §4.1's grammar spells, with the
#: value the illustration gives it and the family §4.3 says owns it. The
#: list is restated here rather than read out of a document, because an
#: assertion sourced from its subject asserts nothing.
GRAMMAR_SELECTORS = (
    ("serving.proposer.uses", "intent-rows", "PROPOSER_KINDS"),
    ("feed.uses", "entry-source", "FEED_KINDS"),
    ("schedule.clock.uses", "wall", "CLOCK_KINDS"),
    ("schedule.calendar.uses", "weekly-sessions", "CALENDAR_KINDS"),
    ("schedule.cadence.uses", "aligned-bar", "CADENCE_KINDS"),
    ("guards.size.uses", "limit", "GUARD_KINDS"),
    ("guards.sane.uses", "range", "GUARD_KINDS"),
    ("guards.size.params.measure", "quantity", "MEASURE_KINDS"),
    ("guards.exposure.params.measure", "exposure_after", "MEASURE_KINDS"),
    ("guards.day_loss.params.measure", "pnl", "MEASURE_KINDS"),
    ("guards.stale.params.measure", "input_age_ms", "MEASURE_KINDS"),
    ("execution.uses", "paper", "EXECUTOR_KINDS"),
    ("execution.params.fees.kind", "bps", "FEE_KINDS"),
    ("accounting.uses", "paper", "ACCOUNTING_KINDS"),
    ("arming.approval.uses", "deny-all", "APPROVAL_KINDS"),
    ("coordination.lease.uses", "process", "LEASE_KINDS"),
    ("monitors.pred_shift.uses", "psi", "MONITOR_KINDS"),
    ("monitors.coverage.uses", "coverage", "MONITOR_KINDS"),
    ("monitors.pred_shift.params.reference.uses", "leading", "REFERENCE_KINDS"),
    ("monitors.pred_shift.params.window.kind", "count", "CHUNKER_KINDS"),
    ("monitors.pred_shift.params.threshold.kind", "alpha", "THRESHOLD_KINDS"),
    ("monitors.coverage.params.threshold.kind", "constant", "THRESHOLD_KINDS"),
    ("health.probes.disk.uses", "ledger-writable", "PROBE_KINDS"),
    ("health.probes.venue.uses", "executor-check", "PROBE_KINDS"),
    ("resilience.transport.uses", "urllib", "TRANSPORT_KINDS"),
    ("alerting.sinks.ops.uses", "webhook", "ALERT_SINK_KINDS"),
    ("heartbeat.emitters.file.uses", "file", "HEARTBEAT_KINDS"),
    ("outcomes.sources.settle.uses", "settlement", "OUTCOME_SOURCE_KINDS"),
    ("durability.ledger.uses", "jsonl", "LEDGER_KINDS"),
    ("execution.signer.uses", "hmac", "SIGNER_KINDS"),
)


@pytest.mark.parametrize("where,value,family", GRAMMAR_SELECTORS)
def test_every_selector_in_the_grammar_names_a_registry(where, value, family):
    """§4.3: "every `uses` in §4.1 resolves through exactly one of them …
    A `uses` or `kind` whose family has no registry is a validation error,
    not a default". §10 places this assertion here, after every registry
    exists."""
    resolved = REGISTRIES[family].resolve(value)
    assert isinstance(resolved, type), where
    assert value in REGISTRIES[family]


def test_the_grammar_reaches_every_one_of_the_declared_families():
    """§4.3 lists the families; one no grammar site selects would be a
    registry nothing can reach from a document."""
    assert sorted({family for _w, _v, family in GRAMMAR_SELECTORS}) == sorted(REGISTRIES)


def test_each_family_is_a_distinct_registry_with_its_own_name():
    """§4.3: "each family has its own registry" — two families sharing one
    object would let a document select a member of the wrong seam."""
    assert len({id(registry) for registry in REGISTRIES.values()}) == len(REGISTRIES)
    assert len({registry.family for registry in REGISTRIES.values()}) == len(REGISTRIES)


def test_registering_a_name_twice_refuses():
    """§4.3: "Registering a name twice refuses"."""
    registry = CLOCK_KINDS
    existing = registry.kinds()[0]
    with pytest.raises(ProductionError):
        registry.register(existing, registry.resolve(existing))


def test_an_unknown_selector_refuses_rather_than_defaulting():
    """§4.3: "A `uses` or `kind` whose family has no registry is a
    validation error, not a default"."""
    for registry in REGISTRIES.values():
        with pytest.raises(ProductionError):
            registry.resolve("no-such-kind")


def test_a_document_naming_an_unregistered_kind_refuses_when_it_is_resolved(
    serve_document, tmp_path, journal
):
    """§4.3: a `uses` naming no member of its family is "a validation error,
    not a default". §7 keeps `validate` to "shape and document identity" and
    §10 keeps document validation to "shape, default-deny and identity only"
    — a document may name a child class this host has never imported — so
    the refusal lands where the name is RESOLVED: composition."""
    obj = document_obj(serve_document, tmp_path,
                       **{"schedule.calendar": {"uses": "lunar-sessions"}})
    path = write_document(tmp_path, obj)
    assert cli.main(["validate", path], journal_hook=journal) == STOPPED
    assert cli.main(["plan", path], journal_hook=journal) == STOPPED
    assert cli.main(["serve", path, "--once"], journal_hook=journal) == ERROR


# ---------------------------------------------------------------------------
# `ack` and `silence` — §7's two phase-2 alert verbs (§5.11.2)
# ---------------------------------------------------------------------------


def queued_command(doc_path, request_id):
    """The stored inbox command, read while a lock-holder keeps it queued."""
    return queued(doc_path)[request_id]


def test_the_silence_verb_carries_the_matchers_the_window_and_the_comment(
    doc_path, proof, journal
):
    """§7: `silence <doc> --matcher K=V… --until TS --proof FILE`. The
    matchers repeat, and `--comment` is here because §5.16 gives the
    `Silence` a comment and a field with no producer is a plan defect."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()  # a serve process holds the lock: the command stays queued
    try:
        code = cli.main(
            [
                "silence", doc_path, "--matcher", "source=feed", "--matcher",
                "severity=warning", "--until", "2026-01-06T04:00:00Z",
                "--comment", "vendor maintenance", "--proof", proof.maker,
                "--request-id", request_id,
            ],
            journal_hook=journal,
        )
    finally:
        lock.release()
    assert code == STOPPED
    stored = queued_command(doc_path, request_id)
    assert stored["purpose"] == "silence"
    assert stored["payload"]["matchers"] == {"source": "feed", "severity": "warning"}
    assert stored["payload"]["ends_at_ms"] == parse_utc_ms("2026-01-06T04:00:00Z")
    assert stored["payload"]["comment"] == "vendor maintenance"


def test_the_silence_verb_refuses_a_matcher_that_is_not_a_key_value_pair(
    doc_path, proof, journal
):
    """A `--matcher` with no `=` would otherwise silence nothing and say
    nothing about why."""
    planned(doc_path, journal)
    code = cli.main(
        ["silence", doc_path, "--matcher", "source", "--until", "2026-01-06T04:00:00Z",
         "--proof", proof.maker],
        journal_hook=journal,
    )
    assert code == ERROR


def test_the_silence_verb_requires_at_least_one_matcher(doc_path, proof, journal):
    planned(doc_path, journal)
    with pytest.raises(SystemExit):
        cli.main(
            ["silence", doc_path, "--until", "2026-01-06T04:00:00Z", "--proof", proof.maker],
            journal_hook=journal,
        )


def test_the_ack_verb_carries_the_fingerprint_and_an_optional_window(
    doc_path, proof, journal
):
    """§7: `ack <doc> --fingerprint F --proof FILE [--for D]`, the duration
    in the ISO-8601 spelling `parse_iso_duration` already owns."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        code = cli.main(
            ["ack", doc_path, "--fingerprint", "feed-stale", "--for", "PT2H",
             "--reason", "paging the vendor", "--proof", proof.maker,
             "--request-id", request_id],
            journal_hook=journal,
        )
    finally:
        lock.release()
    assert code == STOPPED
    stored = queued_command(doc_path, request_id)
    assert stored["purpose"] == "ack"
    assert stored["payload"]["fingerprint"] == "feed-stale"
    assert stored["payload"]["for_ms"] == 7_200_000
    assert stored["payload"]["reason"] == "paging the vendor"


def test_an_ack_without_for_carries_no_window_and_lets_the_document_decide(
    doc_path, proof, journal
):
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        cli.main(
            ["ack", doc_path, "--fingerprint", "feed-stale", "--proof", proof.maker,
             "--request-id", request_id],
            journal_hook=journal,
        )
    finally:
        lock.release()
    assert "for_ms" not in queued_command(doc_path, request_id)["payload"]


def test_the_ack_verb_refuses_a_duration_that_is_not_iso_8601(doc_path, proof, journal):
    planned(doc_path, journal)
    code = cli.main(
        ["ack", doc_path, "--fingerprint", "feed-stale", "--for", "2h",
         "--proof", proof.maker],
        journal_hook=journal,
    )
    assert code == ERROR


def test_both_alert_verbs_are_authenticated(doc_path, journal):
    """§5.11.2: "a page suppressed by an unauthenticated caller is an
    outage with no evidence", so both carry `--proof` and argparse refuses
    a call without one."""
    planned(doc_path, journal)
    for argv in (
        ["ack", doc_path, "--fingerprint", "feed-stale"],
        ["silence", doc_path, "--matcher", "source=feed", "--until", "2026-01-06T04:00:00Z"],
    ):
        with pytest.raises(SystemExit):
            cli.main(argv, journal_hook=journal)


def test_the_approve_hold_verb_names_the_guard_and_the_scope_it_releases(
    doc_path, proof, journal
):
    """§7: `approve-hold <doc> --guard NAME --scope KEY --proof FILE`. The
    payload names the hold and nothing else — the instant is the consumed
    command's and the guard's own bound is the document's."""
    planned(doc_path, journal)
    request_id = str(uuid.uuid4())
    lock = InstanceLock(serve_root_of(doc_path).lock_path)
    lock.acquire()
    try:
        code = cli.main(
            ["approve-hold", doc_path, "--guard", "size", "--scope", "AAA",
             "--proof", proof.maker, "--request-id", request_id],
            journal_hook=journal,
        )
    finally:
        lock.release()
    assert code == STOPPED
    stored = queued_command(doc_path, request_id)
    assert stored["purpose"] == "approve_hold"
    assert stored["payload"] == {"guard": "size", "scope_key": "AAA"}


def test_the_approve_hold_verb_is_authenticated(doc_path, journal):
    """§5.5.1: ending a hold early is an operator overriding a safety
    verdict — "exactly the class of act D11 requires a verifier for" — so
    argparse refuses a call with no proof."""
    planned(doc_path, journal)
    with pytest.raises(SystemExit):
        cli.main(["approve-hold", doc_path, "--guard", "size", "--scope", "*"],
                 journal_hook=journal)


def test_approve_hold_refuses_a_release_the_series_cannot_honour(
    doc_path, proof, journal, capsys
):
    """No serve process holds the lock, so the CLI applies the command
    itself (§5.8). This document declares no guards and the fold holds no
    hold, so the handler REJECTS — and §7 gives a refused control verb
    exit 5, never a silent success. The receipt says which guard, so the
    refusal is actionable and no `guard_state` reaches the chain."""
    planned(doc_path, journal)
    code = cli.main(
        ["approve-hold", doc_path, "--guard", "size", "--scope", "*",
         "--proof", proof.maker],
        journal_hook=journal,
    )
    assert code == REFUSED
    report = last_report(capsys)
    assert report["purpose"] == "approve_hold"
    assert report["status"] == "rejected"
    assert "size" in report["reason"]
    assert envelopes(doc_path, kind="guard_state") == []

"""``python -m dskit.production`` — the serve process and the acts around it (§7).

Seventeen verbs, one shape. `validate` and `plan` turn a document into an
immutable release; `serve` runs the loop against it; `status`, `verify` and a
dozen authenticated control verbs are how an operator sees and steers a
running series.

Three rules from §7 shape every verb here.

**Only operational flags live on `serve`** — ``--once``, ``--max-ticks`` and
``--armed``. Adapter selection and every semantic knob live in the document,
so two runs of one release cannot compute different things because of how
somebody typed the command. There is no ``--rung``, no ``--executor``, no
``--clock``: a document names its own collaborators, and :mod:`compose` reads
the rung.

**Every mutating control verb goes through the durable inbox** (§5.8). The
CLI never writes the ledger: it canonicalises the command, stores its
independent payload digest and queues it. When a serve process holds
``serve.lock`` the command waits for the loop; when nothing holds it a
non-executing verb may take the lock itself and run the same
:class:`~dskit.production.control.CommandProcessor` synchronously — the sole
ledger writer either way. Exit 0 therefore means *durably queued or
synchronously applied*, never "it took effect"; ``status`` shows the terminal
receipt. ``execute-flatten`` is the exception §5.8 names: it owns a
sequential cycle against model ticks and so requires an active ready loop,
which is why it is only ever queued.

**Exit codes are §5.13's**: 0 stopped · 1 error · 3 halted · 4 already
running · 5 refused. 3 and 5 are deliberately different facts — a halted
series needs operator action, while a readiness NO-GO means nothing is wrong
and the checklist is simply not yet satisfied.

D22's journal seam runs through :meth:`Verb.invoke`: each mutating verb calls
``record_production`` exactly ONCE, in ``finally``, so a refusal is recorded
as faithfully as a success. The hook is injected — the default is the
journal's own function, imported at function depth so the package stays
importable without it — and ``serve`` does not journal here at all: the loop
writes the one production row per process, anchored to the ledger head it
actually reached.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import uuid
from abc import ABC, abstractmethod

from dskit.onboarding import OnboardingRoot
from dskit.pipeline.document import load_document
from dskit.pipeline.node import class_ref
from dskit.pipeline.planner import plan as plan_pipeline
from dskit.production.arming import (
    APPROVAL_KINDS,
    ArmRequest,
    verifier_fingerprint,
)
from dskit.production.base import (
    GENESIS_HASH,
    ProductionError,
    canonical_hash,
    parse_utc_ms,
)
from dskit.production.breaker import Breaker
from dskit.production.bundles import Invocation
from dskit.production.clock import CLOCK_KINDS
from dskit.production.compose import bundles_for, handlers_for
from dskit.production.control import EXECUTING_PURPOSES, CommandProcessor, ControlInbox
from dskit.production.coordination import LEASE_KINDS
from dskit.production.decider import (
    CONFIG_FILENAME,
    artifact_entries,
    artifact_prefix,
    serving_document,
    serving_registry,
)
from dskit.production.document import ServeDocument
from dskit.production.feed import FeedSpec, active_source_identity
from dskit.production.health import InstanceLock
from dskit.production.ledger import Checkpoint, ServeRoot, ledger_class
from dskit.production.loop import JOURNAL_NOTES, JOURNAL_STEP, ServeLoop
from dskit.production.policy import TransitionPolicy
from dskit.production.readiness import checklist_digest
from dskit.production.records import ReductionPlan
from dskit.production.state import Recovery
from dskit.production.redact import get_logger, redact, resolve_secrets
from dskit.production.release import (
    RELEASE_FILENAME,
    ReleaseManifest,
    RuntimeFingerprint,
    artifact_digest,
    fingerprint_class,
    write_release,
)
from dskit.production.vocab import (
    CASH_FLOW_KINDS,
    EXIT_CODES,
    READINESS_VERDICTS,
    TRIP_REASONS,
)

__all__ = ["VERBS", "build_parser", "main"]

_LOG = get_logger("main")

#: The environment half of D11's live conjunction, read beside ``--armed``.
ARM_ENV = "DSKIT_PRODUCTION_ARM"

#: What a control verb's journal row says beyond its request id (D22).
COMMAND_NOTES = "production-v1 purpose={purpose} status={status}"

#: The two readiness verdicts, named once here so the exit mapping and the
#: receipt reader cannot drift from ``vocab``.
_GO, _NO_GO = READINESS_VERDICTS

#: The receipt status §5.13 turns into exit 5 — "a control verb refused
#: because the series state forbids it".
_REJECTED = "rejected"

#: The head :data:`~dskit.production.loop.JOURNAL_NOTES` renders, read back.
#: A renderer and a parser that drift are an anchor nobody can use, so the
#: agreement is pinned at import rather than trusted.
_ANCHOR = re.compile(r"head=(\d+):([0-9a-f]{64})")
if not _ANCHOR.search(JOURNAL_NOTES.format(process_id="p", seq=1, head_hash="0" * 64)):
    raise ProductionError(
        [f"__main__ cannot parse the anchor loop.JOURNAL_NOTES renders: {JOURNAL_NOTES!r}"]
    )


# ---------------------------------------------------------------------------
# Reading a document, its series root and its release
# ---------------------------------------------------------------------------


def _clock(document):
    """Build the clock the document names; clocks take keywords, not a params dict."""
    site = document.schedule.clock
    return CLOCK_KINDS.resolve(site.uses)(**(dict(site.params) if site.params else {}))


def _serve_root(document):
    """Bind the series root the document's placement and series id name."""
    return ServeRoot(document.placement.ledger_root, document.series_id)


def _releases_dir(serve_root):
    """Ask the root where a release lives and take its parent — no path is built here."""
    return os.path.dirname(serve_root.release_dir("any"))


def _read_json(path):
    """Load a JSON file, raising a ProductionError that names it."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProductionError([f"cannot read {str(path)!r}: {exc}"]) from exc


def _release_for(document, serve_root):
    """Return the release this document was planned into.

    Parameters
    ----------
    document : ServeDocument
    serve_root : ServeRoot

    Returns
    -------
    ReleaseManifest
        The newest release whose ``doc_hash`` and ``series_id`` are this
        document's — a re-plan of one document differs only by when it was
        minted, and the freshest verification is the one that governs.

    Raises
    ------
    ProductionError
        When no release under the series matches, or two of them were
        minted at the same instant and nothing distinguishes them.
    """
    home = _releases_dir(serve_root)
    found = []
    for name in sorted(os.listdir(home) if os.path.isdir(home) else ()):
        manifest = ReleaseManifest.from_obj(_read_json(os.path.join(home, name,
                                                                   RELEASE_FILENAME)))
        if manifest.doc_hash == document.doc_hash and manifest.series_id == document.series_id:
            found.append(manifest)
    if not found:
        raise ProductionError(
            [f"no release under {home} was planned from document {document.doc_hash[:12]}… "
             "— run `plan` first (D24: only a content-and-runtime-bound release is served)"]
        )
    newest = max(manifest.created_ms for manifest in found)
    latest = [manifest for manifest in found if manifest.created_ms == newest]
    if len(latest) > 1:
        raise ProductionError(
            [f"{len(latest)} releases of document {document.doc_hash[:12]}… were minted at "
             f"{newest}; nothing distinguishes which one is served"]
        )
    return latest[0]


def _proof(path):
    """Read a proof file's bytes, raising a ProductionError that names it."""
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        raise ProductionError([f"cannot read proof {str(path)!r}: {exc}"]) from exc


def _universe(document):
    """Return ``serving.required_universe`` as a key list, reading the file when it names one."""
    declared = document.serving.required_universe
    return list(_read_json(declared)) if isinstance(declared, str) else list(declared)


def _adapter_entry(name):
    """Fingerprint the adapter package the release binds (D24's resolved adapter)."""
    try:
        module = importlib.import_module(name)
    except ImportError as exc:
        raise ProductionError([f"cannot import adapter {name!r}: {exc}"]) from exc
    home = getattr(module, "__path__", None)
    source = home[0] if home else getattr(module, "__file__", None)
    if source is None:
        raise ProductionError([f"adapter {name!r} has no source to digest"])
    return {"name": name, "digest": artifact_digest(source)}


def _fingerprint(registry, site):
    """Fingerprint one ``{uses, params}`` selector's class and params.

    ``arming.verifier_fingerprint`` is the one owner of "a class plus its
    params, canonically hashed"; the lease binds the same way, so it is
    imported rather than restated (CLAUDE.md: a function is never repeated
    across modules).
    """
    return verifier_fingerprint(registry.resolve(site.uses), dict(site.params or {}))


def _process_id():
    """Return a fresh id for this process — the ledger's envelope field and D22's anchor."""
    return str(uuid.uuid4())


def _journal_hook():
    """D22's default seam, imported at function depth (the package import rule)."""
    from dskit.journal.hooks import record_production

    return record_production


def _journal_rows(series_path):
    """Return the production journal rows recorded against one series root.

    Parameters
    ----------
    series_path : str
        The serve-series root a mutating verb records as ``db_location``.

    Returns
    -------
    list of Action
        Empty when this tree has no journal at all — not every project
        keeps one, and a missing journal is a state, not an error.
    """
    from dskit.journal.base import JournalError
    from dskit.journal.locate import find_journal
    from dskit.journal.store import read_actions

    try:
        root = find_journal()
        if root is None:
            return []
        return [
            row
            for row in read_actions(root)
            if row.category == "production" and row.db_location == series_path
        ]
    except (JournalError, OSError, ValueError) as exc:
        _LOG.warning("cannot read the journal anchor: %s", redact(str(exc)))
        return []


# ---------------------------------------------------------------------------
# The verb seam
# ---------------------------------------------------------------------------


class Verb(ABC):
    """One command line: parse, act, and — when it mutates — journal once.

    Parameters
    ----------
    args : argparse.Namespace
        The parsed command line.

    Attributes
    ----------
    args : argparse.Namespace
        As given.
    outputs : str
        What the act produced — a release hash, a request id — captured for
        D22's row and set by :meth:`run` as soon as it is known.
    notes : str
        The row's free text.
    db_location : str
        The serve-series root, once the document has named one.
    journal_hook : callable or None
        D22's injected seam, supplied by :meth:`invoke`.

    Examples
    --------
    Verbs are constructed by :func:`main` from the parsed arguments::

        parser = build_parser()
        verb = VERBS["validate"](parser.parse_args(["validate", "serve.json"]))
        verb.invoke(lambda **row: None)
        # -> 0
    """

    #: The subcommand name, exactly as §7 spells it.
    NAME = ""

    #: The one-line help argparse shows.
    HELP = ""

    #: Whether D22 counts this verb as mutating and therefore journalled.
    MUTATING = False

    def __init__(self, args):
        self.args = args
        self.outputs = ""
        self.notes = ""
        self.db_location = ""
        self.journal_hook = None

    @classmethod
    def add_arguments(cls, parser):
        """Declare this verb's own options; the base declares none."""

    @abstractmethod
    def run(self):
        """Do the work and return a ``vocab.EXIT_CODES`` value."""

    def invoke(self, journal_hook):
        """Run the verb, journalling exactly once when it mutates (D22).

        Parameters
        ----------
        journal_hook : callable
            ``record_production``'s signature; a test injects a recorder.

        Returns
        -------
        int
            A ``vocab.EXIT_CODES`` value.

        Raises
        ------
        ProductionError
            Whatever the verb raised — the journal row is written first,
            because a refused act is exactly the one worth recording.
        """
        self.journal_hook = journal_hook
        code = EXIT_CODES["error"]
        try:
            code = self.run()
            return code
        finally:
            if self.MUTATING:
                self._journal(code)

    def _journal(self, code):
        """Write D22's one row; a journal that refuses never changes the exit code."""
        try:
            self.journal_hook(
                step=JOURNAL_STEP.format(verb=self.NAME, rung=self._rung())[:80],
                inputs=str(self.args.document),
                outputs=str(self.outputs),
                db_location=str(self.db_location),
                notes=self.notes or f"exit={code}",
            )
        except Exception as exc:  # noqa: BLE001 - the ledger is authoritative, not the journal
            _LOG.error("could not write the journal row: %s", redact(str(exc)))

    def _rung(self):
        """Return the document's grade for the journal step, or the empty string."""
        try:
            return ServeDocument.load(self.args.document).rung
        except ProductionError:
            return ""


class DocumentVerb(Verb):
    """A verb that reads one serve document and nothing else."""

    def run(self):
        """Load the document and hand it to :meth:`act`."""
        return self.act(ServeDocument.load(self.args.document))

    @abstractmethod
    def act(self, document):
        """Do the work against a validated document."""


class SeriesVerb(DocumentVerb):
    """A verb that also binds the document's series root and its release."""

    def act(self, document):
        """Bind the series root and the release, then hand both to :meth:`over`."""
        serve_root = _serve_root(document)
        self.db_location = serve_root.series_path
        return self.over(document, serve_root, _release_for(document, serve_root))

    @abstractmethod
    def over(self, document, serve_root, release):
        """Do the work against a document, its series root and its release."""


# ---------------------------------------------------------------------------
# validate — shape and identity (§4.2)
# ---------------------------------------------------------------------------


class Validate(DocumentVerb):
    """`validate`: the document's shape and its identity hash, and nothing else.

    Examples
    --------
    ::

        main(["validate", "configs/serve-paper.json"])
        # -> 0, having printed {"name": …, "doc_hash": "…"}
    """

    NAME = "validate"
    HELP = "check a serve document's shape and print its identity hash"

    def act(self, document):
        """Print the document's name, series, rung and ``doc_hash`` (§4.2)."""
        print(json.dumps(
            {
                "name": document.name,
                "series_id": document.series_id,
                "rung": document.rung,
                "doc_hash": document.doc_hash,
            },
            indent=2,
        ))
        return EXIT_CODES["stopped"]


# ---------------------------------------------------------------------------
# plan — the immutable release (§5.3.1, D24)
# ---------------------------------------------------------------------------


class Plan(DocumentVerb):
    """`plan`: derive the served document and mint the release it binds (D24).

    Every input is resolved and verified once — the served derivation, the
    artifact digests, the resolved classes and their code, the entry's feed
    spec and the ACTIVE source identity, the approval and lease
    fingerprints, the checklist's contents and the runtime fingerprint —
    and only then is ``release_hash`` computed and the immutable directory
    written.

    Examples
    --------
    ::

        main(["plan", "configs/serve-paper.json"])
        # -> 0, having written serve/<series>/releases/<release_hash>/
    """

    NAME = "plan"
    HELP = "derive the serving document and write the immutable release"
    MUTATING = True

    def act(self, document):
        """Refuse a store that cannot honour the placement, then mint the release.

        The ledger question is asked FIRST and nothing is built from the
        answer: a document whose store cannot segment while its
        ``placement.rotate`` says it must (§5.8.2) has to refuse where the
        release is minted, not have a knob its author believed in quietly
        ignored at the first tick.
        """
        ledger_class(document)
        clock = _clock(document)
        serve_root = _serve_root(document)
        self.db_location = serve_root.series_path
        run_dir = document.serving.run_dir
        run = self._run_document(run_dir)
        served = serving_document(
            run, run_dir, list(document.serving.heads), dict(document.serving.replay or {})
        )
        the_plan = self._planned(served)
        artifacts = self._artifacts(the_plan, run_dir, clock.now_ms())
        contract = self._contract(document, the_plan, artifacts)
        registry = OnboardingRoot(contract.source_binding["root"]).registry()
        digest, version = active_source_identity(registry, contract.source_binding["source"])
        manifest = ReleaseManifest(
            series_id=document.series_id,
            doc_hash=document.doc_hash,
            run_hash=run.hash,
            serving_hash=served.hash,
            artifacts=artifacts,
            classes=self._classes(the_plan),
            adapter=_adapter_entry(document.serving.adapter),
            feed_spec=FeedSpec.from_contract(
                contract, _universe(document), digest, version
            ).to_obj(),
            source_config={"hash": digest, "version": version},
            execution_scope=document.coordination.scope,
            approval_fingerprint=_fingerprint(APPROVAL_KINDS, document.arming.approval),
            lease_fingerprint=_fingerprint(LEASE_KINDS, document.coordination.lease),
            checklist_digest=checklist_digest(document.readiness.checklist),
            runtime_fingerprint=RuntimeFingerprint.capture(),
            created_ms=clock.now_ms(),
        )
        home = write_release(serve_root.series_path, manifest, document)
        self.outputs = manifest.release_hash
        self.notes = f"doc_hash={manifest.doc_hash} serving_hash={manifest.serving_hash}"
        print(json.dumps(
            {
                "release_hash": manifest.release_hash,
                "doc_hash": manifest.doc_hash,
                "run_hash": manifest.run_hash,
                "serving_hash": manifest.serving_hash,
                "release_dir": str(home),
                "artifacts": sorted(manifest.artifacts),
            },
            indent=2,
        ))
        return EXIT_CODES["stopped"]

    @staticmethod
    def _run_document(run_dir):
        """Load the served run's own ``config.json``, refusing as a ProductionError."""
        path = os.path.join(run_dir, CONFIG_FILENAME)
        try:
            return load_document(path)
        except (OSError, ValueError) as exc:
            raise ProductionError([f"cannot load the run document {path}: {exc}"]) from exc

    @staticmethod
    def _planned(served):
        """Plan the served document against the serving registry (R13's owned kinds)."""
        try:
            return plan_pipeline(served, serving_registry(None))
        except ValueError as exc:
            raise ProductionError([f"the served document does not plan: {exc}"]) from exc

    @staticmethod
    def _artifacts(the_plan, run_dir, at_ms):
        """Digest every file the served document pins, named as the manifest names it.

        The timestamp is the planning instant read from the injected clock,
        never a filesystem mtime: §5.3.1 rules that "filesystem mtimes are
        never age authority", so what `max_artifact_age` measures is the age
        of the RELEASE, which is the thing a new plan refreshes.
        """
        artifacts = {}
        for key in the_plan.order:
            if not the_plan.document.expanded[key].artifact:
                continue
            for name, path in artifact_entries(run_dir, key).items():
                artifacts[name] = {"digest": artifact_digest(path), "timestamp_ms": at_ms}
        return artifacts

    @staticmethod
    def _classes(the_plan):
        """Bind every resolved node class to its reference and its code (D24)."""
        return {
            key: {
                "ref": class_ref(the_plan.resolved[key].cls),
                "code_digest": fingerprint_class(the_plan.resolved[key].cls),
            }
            for key in the_plan.order
        }

    @staticmethod
    def _contract(document, the_plan, artifacts):
        """Ask the entry class for the pure ``ServingContract`` the feed spec binds."""
        entry = document.serving.entry.node
        if entry not in the_plan.order:
            raise ProductionError(
                [f"serving.entry.node {entry!r} is not a node of the served document "
                 f"(nodes: {list(the_plan.order)})"]
            )
        spec = the_plan.document.expanded[entry]
        prefix = artifact_prefix(entry)
        evidence = {
            "mode": spec.mode,
            "artifact": spec.artifact,
            "artifact_pinned": any(name.startswith(prefix) for name in artifacts),
            "role": the_plan.role_of(entry),
        }
        cls = the_plan.resolved[entry].cls
        try:
            contract = cls.serving_contract(spec.params, evidence)
        except ValueError as exc:
            raise ProductionError([f"pipeline.{entry}: {exc}"]) from exc
        if contract is None:
            raise ProductionError(
                [f"pipeline.{entry}: {cls.__name__} offers no ServingContract — it cannot "
                 "serve as the entry"]
            )
        return contract


# ---------------------------------------------------------------------------
# serve — the loop (§5.13)
# ---------------------------------------------------------------------------


class Serve(SeriesVerb):
    """`serve`: run the loop against the document's release until it stops.

    This verb writes no journal row of its own: the loop writes D22's one
    production row per process, after the ``process`` stop record's barrier,
    so the row anchors the head the chain actually reached.

    Examples
    --------
    ::

        main(["serve", "configs/serve-paper.json", "--max-ticks", "10"])
        # -> 0
    """

    NAME = "serve"
    HELP = "run the loop against the document's release"

    @classmethod
    def add_arguments(cls, parser):
        """Declare §7's three operational flags — and no semantic knob."""
        parser.add_argument("--once", action="store_true", help="run exactly one tick")
        parser.add_argument("--max-ticks", type=int, default=None,
                            help="stop after N completed ticks")
        parser.add_argument("--armed", action="store_true",
                            help="one half of D11's live conjunction; the other halves are "
                                 f"{ARM_ENV} and a current ordinary arm")

    def over(self, document, serve_root, release):
        """Take the lock, compose the bundles this rung selects and run the loop."""
        lock = InstanceLock(serve_root.lock_path)
        try:
            lock.acquire()
        except ProductionError as exc:
            _LOG.error("cannot take the instance lock: %s", redact(str(exc)))
            return EXIT_CODES["already_running"]
        ledger = None
        process_id = _process_id()
        try:
            bundles = bundles_for(
                document,
                release,
                None,
                serve_root=serve_root,
                secrets=resolve_secrets(document.env),
                invocation=self._invocation(),
                process_id=process_id,
                clock=_clock(document),
                lock=lock,
                journal_hook=self.journal_hook,
            )
            ledger = bundles[5].ledger
            return ServeLoop(
                document, release, *bundles, lock=lock, process_id=process_id,
            ).run()
        finally:
            if ledger is not None:
                ledger.close()
            lock.release()

    def _invocation(self):
        """Build §5.13's frozen ``Invocation`` from the flags and the environment."""
        return Invocation(
            armed=bool(self.args.armed),
            env_release_hash=os.environ.get(ARM_ENV),
            once=bool(self.args.once),
            max_ticks=self.args.max_ticks,
        )


# ---------------------------------------------------------------------------
# The control verbs — one durable command each (§5.8)
# ---------------------------------------------------------------------------


class ControlVerb(SeriesVerb):
    """One authenticated operator act, queued as a durable command (§5.8).

    Subclasses supply the ``CONTROL_PURPOSES`` member they raise and the
    payload the maker signs; everything else — the caller's UUID, the
    canonical payload digest, the release binding, the proof bytes, the
    optional synchronous application and the exit code — is the same for
    every verb, which is why it lives here once.

    Examples
    --------
    ::

        main(["reconcile", "configs/serve-paper.json"])
        # -> 0 — queued, or applied under the lock when no loop holds it
    """

    MUTATING = True

    #: The ``vocab.CONTROL_PURPOSES`` member this verb raises.
    PURPOSE = ""

    #: Whether §5.6 makes this an authenticated (proof-carrying) act.
    AUTHENTICATED = True

    @classmethod
    def add_arguments(cls, parser):
        """Declare the caller's request id and, for an authenticated verb, its proof."""
        parser.add_argument("--request-id", default=None,
                            help="the caller's operation UUID; reuse it only for a retry")
        if cls.AUTHENTICATED:
            parser.add_argument("--proof", required=True,
                                help="file holding the principal's signed proof")
        cls.add_act_arguments(parser)

    @classmethod
    def add_act_arguments(cls, parser):
        """Declare the verb's own authenticated inputs; the base has none."""

    def over(self, document, serve_root, release):
        """Queue the command, then apply it here when no serve process holds the lock."""
        request_id = self.args.request_id or str(uuid.uuid4())
        self.outputs = request_id
        self.before_queue(document, serve_root)
        payload = self.payload(document, release)
        inbox = ControlInbox(serve_root, _clock(document))
        inbox.queue(
            {
                "request_id": request_id,
                "purpose": self.PURPOSE,
                "payload": payload,
                "payload_digest": canonical_hash(payload),
                "release_hash": release.release_hash,
                "proof": _proof(self.args.proof) if self.AUTHENTICATED else b"",
            }
        )
        receipt = self._apply(document, serve_root, release, request_id)
        status = "queued" if receipt is None else receipt["status"]
        self.notes = COMMAND_NOTES.format(purpose=self.PURPOSE, status=status)
        print(json.dumps(
            {"request_id": request_id, "purpose": self.PURPOSE, "status": status,
             "reason": "" if receipt is None else receipt["reason"]},
            indent=2,
        ))
        return self.exit_for(receipt)

    def before_queue(self, document, serve_root):
        """Act that must precede the queue; only ``halt`` has one (§5.6)."""

    @abstractmethod
    def payload(self, document, release):
        """Return the JSON-shaped payload this command carries."""

    def exit_for(self, receipt):
        """Map a terminal receipt to §5.13's exit code; a queued command is 0."""
        if receipt is not None and receipt["status"] == _REJECTED:
            return EXIT_CODES["refused"]
        return EXIT_CODES["stopped"]

    # -- the synchronous path ------------------------------------------------

    def _apply(self, document, serve_root, release, request_id):
        """Run the spool under the lock when nothing else holds it, else answer None.

        §5.8: "If no serve process owns the lock, non-executing commands may
        acquire it and run the same `CommandProcessor` synchronously". An
        EXECUTING purpose never takes this path — it "requires an active
        ready loop", and its cycle is the loop's to run.
        """
        if self.PURPOSE in EXECUTING_PURPOSES:
            return None
        lock = InstanceLock(serve_root.lock_path)
        try:
            lock.acquire()
        except ProductionError:
            return None
        ledger = None
        try:
            clock = _clock(document)
            bundles = bundles_for(
                document,
                release,
                None,
                serve_root=serve_root,
                secrets=resolve_secrets(document.env),
                invocation=Invocation(
                    armed=False, env_release_hash=None, once=True, max_ticks=1
                ),
                process_id=_process_id(),
                clock=clock,
                lock=lock,
                journal_hook=self.journal_hook,
            )
            recording = bundles[5]
            ledger = recording.ledger
            # The CLI is the writer for as long as it holds the lock, so it
            # starts the way the loop does (§5.13): replay the fold and close
            # what a crash left open BEFORE anything appends. A handler reads
            # `state.snapshot()`, and a fold that is behind the chain would
            # answer for a series that no longer exists.
            Recovery(
                recording.ledger, recording.state, recording.id_source,
                bundles[4].executor,
            ).run(clock)
            processor = CommandProcessor(
                recording.inbox,
                recording.ledger,
                recording.state,
                self._handlers(document, bundles, release),
                clock,
            )
            results = processor.process_pending(recording.state.snapshot())
            return {result["request_id"]: result for result in results}.get(request_id)
        finally:
            if ledger is not None:
                ledger.close()
            lock.release()

    @staticmethod
    def _handlers(document, bundles, release):
        """Return the dispatch table minus the purposes only a running loop can honour."""
        return {
            purpose: handler
            for purpose, handler in handlers_for(document, bundles, release=release).items()
            if purpose not in EXECUTING_PURPOSES
        }


class ArmRequestVerb(ControlVerb):
    """`arm-request`: the maker's half of D11's authenticated maker-checker arm."""

    NAME = "arm-request"
    HELP = "queue the maker's authenticated arm request for the document's rung"
    PURPOSE = "arm_request"

    @classmethod
    def add_act_arguments(cls, parser):
        """Declare the arm's expiry, its allowlist and any tightening overlay."""
        parser.add_argument("--until", required=True,
                            help="UTC instant the arm expires at, e.g. 2026-01-06T04:00:00Z")
        parser.add_argument("--allow", action="append", default=[],
                            help="a key the arm covers; repeat for each (may only narrow)")
        parser.add_argument("--overlay", default="{}",
                            help="JSON limits overlay; may only tighten a declared bound")

    def payload(self, document, release):
        """Return the ``ArmRequest`` fields, rebuilt and validated before queueing."""
        try:
            overlay = json.loads(self.args.overlay)
        except ValueError as exc:
            raise ProductionError([f"--overlay is not valid JSON: {exc}"]) from exc
        request = ArmRequest(
            release_hash=release.release_hash,
            rung=document.rung,
            allowlist=tuple(self.args.allow),
            limits_overlay=dict(overlay),
            requested_until_ms=parse_utc_ms(self.args.until),
            request_proof=_proof(self.args.proof),
        )
        body = request.to_obj()
        body.pop("request_proof", None)
        return body


class ApproveArm(ControlVerb):
    """`approve-arm`: the checker's half — the one call that mints authority."""

    NAME = "approve-arm"
    HELP = "queue the checker's approval of a named arm request"
    PURPOSE = "arm_approval"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the maker's request this approval covers."""
        parser.add_argument("--request", required=True,
                            help="the arm-request's request id")

    def payload(self, document, release):
        """Return the maker's id and the digest of the request the checker signed."""
        return {
            "request_id": self.args.request,
            "request_digest": self._request_digest(document, release),
        }

    def _request_digest(self, document, release):
        """Rebuild the maker's request from the spool and digest it (§5.6)."""
        stored = _stored_command(document, self.args.request)
        payload = dict(stored["payload"])
        return ArmRequest(
            release_hash=payload.get("release_hash"),
            rung=payload.get("rung"),
            allowlist=tuple(payload.get("allowlist") or ()),
            limits_overlay=dict(payload.get("limits_overlay") or {}),
            requested_until_ms=payload.get("requested_until_ms"),
            request_proof=stored["proof"],
        ).request_digest()


class Disarm(ControlVerb):
    """`disarm`: the safe demotion an operator may always take (§5.6)."""

    NAME = "disarm"
    HELP = "queue the safe demotion that ends the current arm"
    PURPOSE = "disarm"
    AUTHENTICATED = False

    def payload(self, document, release):
        """Carry nothing: ending an arm needs no authenticated content."""
        return {}


class Halt(ControlVerb):
    """`halt`: the out-of-band kill switch, and the audit command behind it.

    §5.6: the ``HALT`` sentinel is created BEFORE the command is queued, so
    "stopping does not depend on the decision loop, inbox health, or ledger
    availability". A spool that then refuses leaves the switch ON.
    """

    NAME = "halt"
    HELP = "set the out-of-band HALT sentinel and queue its audit command"
    PURPOSE = "halt"
    AUTHENTICATED = False

    @classmethod
    def add_act_arguments(cls, parser):
        """Declare the closed trip reason the audit record carries."""
        parser.add_argument("--reason", default="operator", choices=list(TRIP_REASONS),
                            help="why the series was halted")

    def before_queue(self, document, serve_root):
        """Turn the kill switch on through its one owner, touching no ledger."""
        created = Breaker(
            document, serve_root, ledger=None, state=None, clock=_clock(document),
            transition_policy=TransitionPolicy(),
        ).create_halt_sentinel()
        _LOG.warning("HALT sentinel %s", "created" if created else "was already present")

    def payload(self, document, release):
        """Carry the trip reason the breaker records."""
        return {"reason": self.args.reason}


class Reduce(ControlVerb):
    """`reduce`: the authenticated transition into ``reducing`` (D12)."""

    NAME = "reduce"
    HELP = "queue the authenticated transition into reducing"
    PURPOSE = "reduce"

    def payload(self, document, release):
        """Carry nothing beyond the proof: the transition is the act."""
        return {}


class FlattenRequest(ControlVerb):
    """`flatten-request`: the maker's reduction plan and the move to ``reducing``."""

    NAME = "flatten-request"
    HELP = "queue the maker's authenticated reduction plan"
    PURPOSE = "flatten_request"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the file holding the signed reduction plan."""
        parser.add_argument("--plan", required=True,
                            help="JSON file holding the ReductionPlan the maker signed")

    def payload(self, document, release):
        """Carry the plan, parsed by its own default-deny value object first."""
        return {"plan": ReductionPlan.from_obj(_read_json(self.args.plan)).to_obj()}


class ApproveFlatten(ControlVerb):
    """`approve-flatten`: the checker's authorization of the stored plan (§5.6)."""

    NAME = "approve-flatten"
    HELP = "queue the checker's authorization of a named reduction plan"
    PURPOSE = "flatten_approval"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the maker's flatten request this approval covers."""
        parser.add_argument("--request", required=True,
                            help="the flatten-request's request id")

    def payload(self, document, release):
        """Carry the maker's request id; the handler reads the plan from its receipt."""
        return {"request_id": self.args.request}


class ExecuteFlatten(ControlVerb):
    """`execute-flatten`: the queued cycle only an active ready loop may run (§5.8)."""

    NAME = "execute-flatten"
    HELP = "queue execution of an authorized reduction plan by a running loop"
    PURPOSE = "execute_flatten"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the authority granted and the signed plan its cycle runs."""
        parser.add_argument("--authorization", required=True,
                            help="the reduction authority id the approval issued")
        parser.add_argument("--plan", required=True,
                            help="JSON file holding the approved ReductionPlan")

    def payload(self, document, release):
        """Carry the authority id and the signed plan the loop's cycle walks."""
        return {
            "authorization_id": self.args.authorization,
            "plan": ReductionPlan.from_obj(_read_json(self.args.plan)).to_obj(),
        }


class Resume(ControlVerb):
    """`resume`: the authenticated reset after cooling-off (§5.6)."""

    NAME = "resume"
    HELP = "queue the authenticated reset of a named trip"
    PURPOSE = "resume"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the trip the operator acknowledges — a reset without one is not a reset."""
        parser.add_argument("--acknowledge", required=True,
                            help="the trip record id being acknowledged")

    def payload(self, document, release):
        """Carry the acknowledged trip id."""
        return {"acknowledges_trip_id": self.args.acknowledge}


class Reconcile(ControlVerb):
    """`reconcile`: one run against the venue and the document's mismatch policy."""

    NAME = "reconcile"
    HELP = "queue one reconciliation against the venue"
    PURPOSE = "reconcile"
    AUTHENTICATED = False

    def payload(self, document, release):
        """Carry nothing: the scope and the policy are the document's."""
        return {}


class Adopt(ControlVerb):
    """`adopt`: the authenticated adoption of named venue breaks (§5.9, D13)."""

    NAME = "adopt"
    HELP = "queue authenticated adoption of named reconciliation breaks"
    PURPOSE = "adopt"

    @classmethod
    def add_act_arguments(cls, parser):
        """Name the breaks, their cash-flow kind and whose side they came from."""
        parser.add_argument("--break", dest="breaks", action="append", required=True,
                            default=[], help="a break id to adopt; repeat for each")
        parser.add_argument("--flow-kind", required=True, choices=list(CASH_FLOW_KINDS),
                            help="the cash-flow kind the adoption banks")
        parser.add_argument("--external", action=argparse.BooleanOptionalAction,
                            required=True,
                            help="whether the break originated at the venue")

    def payload(self, document, release):
        """Carry the breaks and the operator's own classification of them."""
        return {
            "break_ids": list(self.args.breaks),
            "flow_kind": self.args.flow_kind,
            "external": bool(self.args.external),
        }


class Outcomes(ControlVerb):
    """`outcomes`: collect what resolved and record it up to the cut (§5.13.2)."""

    NAME = "outcomes"
    HELP = "collect and record outcomes up to the cut"
    PURPOSE = "outcomes"
    AUTHENTICATED = False

    @classmethod
    def add_act_arguments(cls, parser):
        """Declare the optional cut; without it the command's own queue instant is used."""
        parser.add_argument("--asof", default=None,
                            help="UTC instant to collect up to, e.g. 2026-01-06T04:00:00Z; "
                                 "defaults to when the command was queued")

    def payload(self, document, release):
        """Carry the cut when the operator named one; the sources are the document's."""
        if self.args.asof is None:
            return {}
        return {"asof_ms": parse_utc_ms(self.args.asof)}


class Ready(ControlVerb):
    """`ready`: the release-bound GO / NO-GO the action matrix reads (§5.13).

    §5.13: it "writes it the way §5.8 rules every control verb writes" —
    queued through the inbox, or applied here under the lock — and "NO-GO
    exits 5".
    """

    NAME = "ready"
    HELP = "evaluate and record the release-bound readiness checklist"
    PURPOSE = "ready"
    AUTHENTICATED = False

    def payload(self, document, release):
        """Carry nothing: the checklist and its waivers are the document's."""
        return {}

    def exit_for(self, receipt):
        """Exit 5 on a recorded NO-GO — nothing is wrong, the checklist is unmet."""
        if receipt is not None and receipt["reason"] == _NO_GO:
            return EXIT_CODES["refused"]
        return super().exit_for(receipt)


def _stored_command(document, request_id):
    """Return a queued or terminal command from the series' spool, by request id."""
    serve_root = _serve_root(document)
    inbox = ControlInbox(serve_root, _clock(document))
    receipt = inbox.receipt(request_id)
    if receipt is not None:
        return receipt
    for command in inbox.pending():
        if command["request_id"] == request_id:
            return command
    raise ProductionError(
        [f"this spool holds no command {request_id!r} — an approval names the request it covers"]
    )


# ---------------------------------------------------------------------------
# status and verify — read-only (§7)
# ---------------------------------------------------------------------------


class Status(DocumentVerb):
    """`status`: what the series looks like, without ever taking the writer lock.

    Everything here is read from durable state a serving process leaves
    behind — the head-bound caches of §5.8, the heartbeat file, the ``HALT``
    sentinel and the command spool — because this is the verb an operator
    runs WHILE the loop is serving.

    Examples
    --------
    ::

        main(["status", "configs/serve-paper.json"])
        # -> 0, having printed the rung, the head and the control spool
    """

    NAME = "status"
    HELP = "print the series' rung, breaker, health, head and control spool"

    def act(self, document):
        """Print §7's status report, taking no lock and opening no ledger."""
        serve_root = _serve_root(document)
        checkpoint = Checkpoint.load(serve_root.checkpoint_cache)
        inbox = ControlInbox(serve_root, _clock(document))
        print(json.dumps(
            {
                "series_id": document.series_id,
                "rung": document.rung,
                "release_hash": None if checkpoint is None else checkpoint.release_hash,
                "halt_sentinel": os.path.exists(serve_root.halt_sentinel),
                "breaker": _optional_json(serve_root.breaker_cache),
                "arming": _optional_json(serve_root.arming_cache),
                "health": _optional_json(serve_root.heartbeat_path),
                "last_tick_at": None if checkpoint is None else checkpoint.last_tick_at,
                "last_completed_tick_at": (
                    None if checkpoint is None else checkpoint.last_completed_tick_at
                ),
                "pending_refs": [] if checkpoint is None else list(checkpoint.pending),
                "head": {
                    "seq": 0 if checkpoint is None else checkpoint.head_seq,
                    "hash": GENESIS_HASH if checkpoint is None else checkpoint.head_hash,
                },
                "control": {
                    "pending": [
                        {
                            "request_id": command["request_id"],
                            "purpose": command["purpose"],
                            "queued_at_ms": command["queued_at_ms"],
                        }
                        for command in inbox.pending()
                    ],
                    "results": [
                        {
                            "request_id": request_id,
                            "purpose": receipt["purpose"],
                            "status": receipt["status"],
                            "reason": receipt["reason"],
                        }
                        for request_id, receipt in _terminal_receipts(serve_root, inbox)
                    ],
                },
            },
            indent=2,
        ))
        return EXIT_CODES["stopped"]


def _optional_json(path):
    """Return a JSON file's contents, or None when the series has not written it."""
    return _read_json(path) if os.path.exists(path) else None


def _terminal_receipts(serve_root, inbox):
    """Yield ``(request_id, receipt)`` for every consumed command, oldest queue first."""
    found = []
    for directory in (serve_root.commands_applied, serve_root.commands_rejected):
        for name in sorted(os.listdir(directory)):
            request_id = os.path.splitext(name)[0]
            receipt = inbox.receipt(request_id)
            if receipt is not None:
                found.append((receipt["queued_at_ms"], request_id, receipt))
    return [(request_id, receipt) for _at, request_id, receipt in sorted(found)]


class Verify(SeriesVerb):
    """`verify`: walk the chain, compare its head to the journal anchor, report gaps.

    Three answers in one report. The chain either walks clean or names the
    ``seq`` the walk expected at the first failing position. The journal's
    ``production-v1 process=<id> head=<seq>:<hash>`` anchor either sits on
    that chain or does not — a row claiming a head the chain never held is
    tamper evidence. And D22's acknowledged gap is reported rather than
    hidden: SIGKILL can leave a durable inbox command with no receipt,
    because the two stores share no transaction.

    Examples
    --------
    ::

        main(["verify", "configs/serve-paper.json"])
        # -> 0, having printed {"first_bad_seq": null, …}
    """

    NAME = "verify"
    HELP = "walk the ledger chain and compare its head to the journal anchor"

    @classmethod
    def add_arguments(cls, parser):
        """Allow an operator to quote an anchor instead of reading the journal."""
        parser.add_argument("--anchor", default=None,
                            help="a journal row's notes, e.g. "
                                 "'production-v1 process=… head=<seq>:<hash>'")

    def over(self, document, serve_root, release):
        """Walk the chain, place the anchor on it and list the receiptless commands."""
        ledger = ledger_class(document)(serve_root, _process_id(), release.release_hash,
                                        clock=_clock(document))
        try:
            first_bad = ledger.verify()
            head_seq, head_hash = ledger.head()
            anchor = self._anchor(serve_root, ledger)
        finally:
            ledger.close()
        gaps = [
            command["request_id"]
            for command in ControlInbox(serve_root, _clock(document)).pending()
        ]
        print(json.dumps(
            {
                "series_id": document.series_id,
                "first_bad_seq": first_bad,
                "head": {"seq": head_seq, "hash": head_hash},
                "anchor": anchor,
                "commands_without_receipt": gaps,
            },
            indent=2,
        ))
        if first_bad is not None or anchor["matches"] is False:
            return EXIT_CODES["error"]
        return EXIT_CODES["stopped"]

    def _anchor(self, serve_root, ledger):
        """Place the newest journal anchor on the chain, or report that there is none."""
        text = self.args.anchor or self._recorded_anchor(serve_root)
        if text is None:
            return {"notes": None, "seq": None, "hash": None, "matches": None}
        found = _ANCHOR.search(text)
        if found is None:
            return {"notes": text, "seq": None, "hash": None, "matches": False}
        seq, digest = int(found.group(1)), found.group(2)
        on_chain = any(
            envelope["seq"] == seq and envelope["hash"] == digest
            for envelope in ledger.scan()
        )
        return {"notes": text, "seq": seq, "hash": digest, "matches": on_chain}

    @staticmethod
    def _recorded_anchor(serve_root):
        """Return the newest journal row's notes for this series, or None."""
        rows = [row for row in _journal_rows(serve_root.series_path)
                if _ANCHOR.search(row.notes)]
        return rows[-1].notes if rows else None


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

#: §7's table, in the order it lists the verbs. The phase-2 rows still
#: absent (`replay`, `report`, `approve-hold`, `ack`, `silence`) are
#: deliberately so: a verb the CLI offers and nothing honours is a control
#: an operator would believe they had taken. `outcomes` is here because
#: §5.13.2's join now honours it.
VERBS = {
    verb.NAME: verb
    for verb in (
        Validate,
        Plan,
        Serve,
        ArmRequestVerb,
        ApproveArm,
        Disarm,
        Halt,
        Reduce,
        FlattenRequest,
        ApproveFlatten,
        ExecuteFlatten,
        Resume,
        Status,
        Verify,
        Reconcile,
        Adopt,
        Outcomes,
        Ready,
    )
}


def build_parser():
    """Return the argument parser §7's table describes.

    Returns
    -------
    argparse.ArgumentParser
        One subcommand per :data:`VERBS` entry, each carrying the serve
        document as its one positional argument.
    """
    top = argparse.ArgumentParser(
        prog="python -m dskit.production",
        description="serve, guard, act, record, monitor — one serve document at a time",
    )
    subcommands = top.add_subparsers(dest="verb", required=True)
    for name, verb in VERBS.items():
        parser = subcommands.add_parser(name, help=verb.HELP)
        parser.add_argument("document", help="the serve document (§4.1)")
        verb.add_arguments(parser)
        parser.set_defaults(verb=name)
    return top


def main(argv=None, *, journal_hook=None):
    """Run one command line and return its exit code.

    Parameters
    ----------
    argv : list of str or None
        The arguments after ``python -m dskit.production``; None reads
        ``sys.argv``.
    journal_hook : callable or None
        D22's injected seam — ``record_production``'s signature. None uses
        the journal's own function, imported at function depth.

    Returns
    -------
    int
        A ``vocab.EXIT_CODES`` value: 0 stopped, 1 error, 3 halted,
        4 already running, 5 refused.

    Examples
    --------
    ::

        main(["validate", "configs/serve-paper.json"])
        # -> 0
    """
    args = build_parser().parse_args(argv)
    hook = _journal_hook() if journal_hook is None else journal_hook
    try:
        return VERBS[args.verb](args).invoke(hook)
    except ProductionError as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return EXIT_CODES["error"]
    except (OSError, ValueError) as exc:
        print(f"error: {redact(f'{type(exc).__name__}: {exc}')}", file=sys.stderr)
        return EXIT_CODES["error"]


if __name__ == "__main__":
    sys.exit(main())

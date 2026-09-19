"""Final-model domain assembly — ten leads, one shared HPO grid (ADR-0114 Phase 2, reconciling ADR-0113's Phase 1).

This module owns exactly what ADR-0114's plan §5 assigns it and nothing
generic: the ten lead (``h01``..``h10``) head-name vocabulary, the real
24-combination LightGBM HPO grid (the plan-locked five dimensions, read
verbatim from ``configs/run-final-hpo.json``'s own ``hpo_space``), the
exact P16 lean-mask drop list (read from its one real source,
``configs/run-p16-feature-mask-zoo.json``, never hardcoded a second
time), the lead-specific outer-aligned forecast-accuracy score (squared-
error improvement vs. the training-mean baseline — never Spearman IC,
per the plan's §2), the ADR-0114 §11 item 1 ruling's simplicity order,
and synthetic frozen-winner refit helpers that can assemble ten fitted heads
for :func:`dskit.pipeline.libs.sklearn.write_bundle` when called directly.

Every generic mechanism — the candidate inventory, the trial ledger, the
one-standard-error selection rule, the cluster-robust standard error, the
multi-head bundle artifact — is IMPORTED, never re-derived: this file
calls into :mod:`dskit.pipeline.kinds_search`, :mod:`dskit.pipeline.stats`
and :mod:`dskit.pipeline.libs.sklearn`.

**No market data and no real release here.** The assembly helpers are
plain, directly-callable Python APIs exercised with synthetic rows and a
synthetic ``evaluate`` callback.  ``FinalRefit`` is wired by
``configs/run-final-refit.json``, whose pins are still PENDING, so the
shipped document still refuses to plan.  The node itself now owns the
complete ADR-0166 contract — an immutable completed-run attestation,
content-derived identities for the materialized refit rows, and ten
labelled input wires — and it can execute ONLY on the ``fixture`` release
channel: over synthetic rows, bound to a document that is deliberately not
the shipped final-HPO one, stamping ``deployment_eligible: false`` into
every head's hashed bundle training identity.  The ``production`` channel
refuses outright, because a real final-model release needs the signed
run-output attestation contract ADR-0122 specifies and nothing builds
yet.  Synthetic helper
assembly is not a completed real final-model release.  Nothing here reads
market data, fits a real model against real history, or loads a real P16
artifact.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone

from dskit.pipeline.kinds_search import (
    CandidateInventory,
    OneStandardErrorSelector,
    TrialLedger,
)
from dskit.pipeline.driver import (
    RunAttestation,
    resolve_json_artifact,
    row_set_identity,
)
from dskit.pipeline.document import load_document
from dskit.pipeline.libs.sklearn import ColumnSubsetEstimator
from dskit.pipeline.node import ConfigError, Node, reject_unknown_params
from dskit.pipeline.stats import cluster_bootstrap_t

__all__ = [
    "BUNDLE_FILENAME",
    "SealRefused",
    "ESTIMATOR_PATH",
    "EVIDENCE_FIELDS",
    "FIXTURE_CHANNEL",
    "GATE_FACTS",
    "FinalRefit",
    "HEADS",
    "HPO_LEDGER_OUTPUT",
    "PRODUCTION_CHANNEL",
    "RELEASE_CHANNELS",
    "RELEASE_ENTRY_POINTS",
    "WIRE_LABEL_FIELD",
    "boundary_flags",
    "build_candidate_inventory",
    "cluster_scores_by_day",
    "final_hpo_document_identity",
    "hpo_space",
    "lean_feature_drop",
    "permitted_for_refit",
    "refit_heads",
    "run_lead_selection",
    "simplicity_key",
    "squared_error_improvement",
]


class SealRefused(TypeError):
    """Raised by :meth:`FinalRefit.__init_subclass__`, and by nothing else.

    Examples
    --------
    Distinguish the seal's refusal from Python's own::

        try:
            build_the_subclass()
        except SealRefused as refusal:
            handled = str(refusal)
        # -> only the seal lands here; a metaclass conflict does not

    Notes
    -----
    A ``TypeError`` subclass because ``__init_subclass__`` must raise one for
    class creation to fail cleanly, and a DISTINCT type because the test suite
    has to tell a seal refusal from a refusal Python issued for its own reasons
    (a metaclass conflict, a layout conflict, a bad base). It matched on a
    substring of the message until round 10, which made the wording of a
    refusal load-bearing for the audit's own evidence: rewording a message
    turned every probe's answer into "Python refused it", and the probes would
    have gone on passing. The type cannot be reworded.
    """


#: "this name resolves to nothing at all" — distinct from resolving to
#: ``None``, which a subclass could supply deliberately.
_UNRESOLVED = object()


#: ``type``'s own accessors for the two structures the seal reads, bound once.
#: A metaclass can supply anything it likes for ``__mro__`` and ``__dict__``,
#: but these descriptors answer from the type's real slots and no metaclass
#: participates, so what the seal reads is what Python resolves through.
_REAL_MRO = type.__dict__["__mro__"].__get__
_REAL_DICT = type.__dict__["__dict__"].__get__


def _resolved_through_the_mro(cls, name):
    """Return what ``cls`` resolves ``name`` to, read from ``type``'s real slots.

    Parameters
    ----------
    cls : type
        The class to resolve through.
    name : str
        The attribute name.

    Returns
    -------
    object
        The first ``name`` found walking ``cls``'s real ``__mro__``, or
        :data:`_UNRESOLVED` when no entry carries it. No descriptor is
        invoked, so a ``property`` or ``classmethod`` is returned as the
        object it is rather than as what it would produce.

    Notes
    -----
    This replaces :func:`inspect.getattr_static`, which SKIPS any MRO entry
    whose metaclass shadows ``__dict__`` rather than trusting what it would
    return. A subclass whose metaclass supplied ``__dict__ = property(...)``
    was therefore skipped whole: the seal read ``FinalRefit``'s own member
    for every sealed name and found no violation, while Python resolved the
    subclass's live override on both the class-level and the instance-level
    path (round-7 review, 2026-09-19). ``GATE_FACTS`` carries the executed
    outcome for that attempt, and for every other.
    """
    for entry in _REAL_MRO(cls):
        contents = _REAL_DICT(entry)
        if name in contents:
            return contents[name]
    return _UNRESOLVED


def _sealed_violations(subclass):
    """Sealed names ``subclass`` resolves to something other than FinalRefit's own.

    Parameters
    ----------
    subclass : type
        The class to check.

    Returns
    -------
    list of str
        Every name in ``FinalRefit._FINAL_METHODS`` that ``subclass``
        resolves to a different object than ``FinalRefit`` does.

    Notes
    -----
    The comparison is ``is not`` — IDENTITY, never ``!=``. An object whose
    ``__eq__`` returns ``True`` for anything would otherwise pass for a
    sealed member while behaving as the attacker wrote it, so relaxing this
    one token to ``!=`` reopens every sealed name at once (round-4 review).
    """
    return [
        name
        for name in FinalRefit._FINAL_METHODS
        if _resolved_through_the_mro(subclass, name)
        is not _resolved_through_the_mro(FinalRefit, name)
    ]


#: The two names whose presence on a metaclass answers EVERY class-level
#: lookup it is asked, sealed or not. Neither is a descriptor question, so
#: :func:`_wins_class_level_lookup` decides them by name; both are in
#: :data:`FinalRefit._FINAL_METHODS`, so both reach it.
_LOOKUP_INTERCEPTORS = ("__getattr__", "__getattribute__")


def _wins_class_level_lookup(name, supplied, cls):
    """Whether a metaclass carrying ``supplied`` under ``name`` beats ``cls``'s MRO.

    Parameters
    ----------
    name : str
        The sealed name the metaclass carries.
    supplied : object
        What it carries under that name.
    cls : type
        The class whose own MRO the metaclass entry is competing with.

    Returns
    -------
    bool
        True when class-level lookup reaches it instead of the class's own.

    Notes
    -----
    Two language rules, and nothing this module invented.

    ``type.__getattribute__`` prefers a metaclass attribute over the class's
    own MRO when the attribute is a DATA descriptor, meaning its type defines
    ``__set__`` or ``__delete__`` — so a NON-data descriptor on the metaclass
    loses to a name the class's MRO carries, and is not a finding. That is why
    a metaclass ``__new__`` or ``__init__``, both plain functions, passes.

    It also prefers the metaclass attribute when the class's MRO carries the
    name NOWHERE, whatever kind of object it is, and that is the second
    clause. Round 11 shipped without it and was right only by coincidence:
    ``__getattr__`` is the one sealed name ``FinalRefit`` does not define, and
    it happened to be in :data:`_LOOKUP_INTERCEPTORS` already. Adding a sealed
    name this class does not carry would have reopened the hole silently.

    ``cls``, NOT ``FinalRefit``, because the rule being modelled is about the
    class under suspicion. Passing ``FinalRefit`` instead is an EQUIVALENT
    MUTANT and the round-12 sweep reports it as a survivor; the proof, so the
    next reviewer does not have to re-derive it: this verdict only decides an
    outcome when :func:`_sealed_violations` is empty, and that function returns
    exactly the sealed names ``cls`` resolves differently from ``FinalRefit``.
    Empty therefore MEANS the two resolutions agree on every sealed name, so
    the two readings cannot disagree at the only moment either is load-bearing.
    The argument holds only while that stays true of ``_sealed_violations``,
    which is why ``cls`` is passed rather than the constant.

    The exception is :data:`_LOOKUP_INTERCEPTORS`, which do not compete under
    their own name: a metaclass ``__getattribute__`` answers every class-level
    lookup the class is given, and a metaclass ``__getattr__`` answers every
    one that would otherwise fail.

    The descriptor test goes through :func:`_resolved_through_the_mro`, so it
    reads ``type(supplied)``'s real MRO dicts — the same slots
    ``_PyType_Lookup`` reads when the interpreter asks this question inside
    ``type.__getattribute__``. A ``__set__`` invented by a ``__getattr__`` or
    by a metaclass is visible to neither, so an object can neither claim to
    shadow nor hide that it does.
    """
    if name in _LOOKUP_INTERCEPTORS:
        return True
    if _resolved_through_the_mro(cls, name) is _UNRESOLVED:
        return True
    carrier = type(supplied)
    # A data descriptor that wins class-level lookup must first BE a descriptor
    # (its type defines __get__) and then be a DATA one (__set__ or __delete__).
    # `type.__getattribute__` consults `__get__` on the type first; an object
    # carrying `__set__`/`__delete__` without `__get__` is not a descriptor at
    # all, and the class's own MRO wins — so reading the two halves without the
    # `__get__` gate over-refuses (round-13 review).
    return (
        _resolved_through_the_mro(carrier, "__get__") is not _UNRESOLVED
        and (
            _resolved_through_the_mro(carrier, "__set__") is not _UNRESOLVED
            or _resolved_through_the_mro(carrier, "__delete__") is not _UNRESOLVED
        )
    )


def _metaclass_supplied(cls):
    """Sealed names ``cls``'s metaclass supplies that ``FinalRefit``'s does not.

    Parameters
    ----------
    cls : type
        The class to check.

    Returns
    -------
    list of str
        Every name in ``FinalRefit._FINAL_METHODS`` carried by some entry of
        ``type(cls)``'s real MRO that is not already an entry of
        ``type(FinalRefit)``'s, sorted and deduplicated.

    Notes
    -----
    :func:`_sealed_violations` walks ``cls.__mro__``. Class-level lookup does
    not: ``type.__getattribute__`` consults ``type(cls).__mro__`` FIRST, and a
    **data descriptor** found there wins outright over anything the class's own
    MRO carries. So a metaclass defining ``validate_params`` as an object with
    ``__get__`` and ``__set__`` answers ``Subclass.validate_params`` while the
    MRO walk still reads ``FinalRefit``'s own and reports no violation
    (round-10 review, 2026-09-19). The instance path is unaffected —
    ``object.__getattribute__`` never consults the metaclass — which is exactly
    why the measured reach was class-level only.

    Round 10 refused ANY sealed name a non-baseline metaclass carried, on the
    argument that classifying the object is one more thing to defeat. Round-11
    review measured what that cost, and it was too much twice over. It refused
    an ordinary registering metaclass — one whose ``__init__`` records the
    class it just built — while telling its author, untruthfully, that
    ``__init__`` "answers class-level lookup ahead of the MRO": a plain
    function is a NON-data descriptor and the class's own MRO wins. And it
    refused three declared attempts at ``super().__new__`` for merely defining
    ``__new__``, so their payload line never ran and three rows of
    :data:`GATE_FACTS` stopped testing what they name.

    So the shadowing question is asked — and it is not this module's
    classifier, it is the LANGUAGE's, read the way the interpreter reads it.
    :func:`_wins_class_level_lookup` walks ``type(obj)``'s real MRO through
    ``type``'s own descriptors, which is exactly what ``_PyType_Lookup`` does
    for ``__set__``/``__delete__`` inside ``type.__getattribute__``. An
    attacker supplies ``type(obj)``, but not what that lookup reads, and
    cannot make a non-shadowing object shadow.

    The baseline is ``type(FinalRefit)``'s own MRO, so ``ABCMeta``, ``type``
    and ``object`` are never findings, and a metaclass that adds no sealed
    name is not one either.

    Patching a baseline entry itself — assigning onto ``ABCMeta`` — is the
    already-declared trusted-name tier, the same tier as replacing this
    function's own name, and is not covered here.
    """
    baseline = _REAL_MRO(type(FinalRefit))
    supplied = []
    for entry in _REAL_MRO(type(cls)):
        if any(entry is known for known in baseline):
            continue
        contents = _REAL_DICT(entry)
        supplied += [
            name
            for name in FinalRefit._FINAL_METHODS
            if name in contents
            and _wins_class_level_lookup(name, contents[name], cls)
        ]
    return sorted(set(supplied))


def _unsealed_problems(cls):
    """Return the seal's verdict on ``cls``, taken WHEN THE GATE IS CONSULTED.

    Returns
    -------
    list of str
        One problem naming every sealed member ``cls`` resolves elsewhere,
        or empty.

    Notes
    -----
    A MODULE-LEVEL FUNCTION, not a method, and that is the whole of round 9's
    first fix. Round 8 asked ``cls._unsealed_problems()`` — resolved through
    the very class under suspicion — so the three mechanisms it had just closed
    could simply inject a compliant ``_unsealed_problems`` ALONGSIDE their real
    payload and the guard reported itself clean. Asking a compromised class
    whether it is compromised is not a check. Round-8 review demonstrated it on
    all four timing-evading shapes. A module-level call cannot be intercepted
    by the class it is asked about; replacing this NAME is the already-declared
    trusted-name tier, not a new one.

    ``__init_subclass__`` is invoked by ``type.__new__`` as
    ``super(cls, cls).__init_subclass__(...)``, so it runs only if ``cls``
    sits ahead of ``FinalRefit`` in ``cls.__mro__`` — and it runs BEFORE a
    metaclass ``__new__`` returns. A metaclass therefore decides whether
    the definition-time hook fires at all, and round-7 review demonstrated
    both halves: an ``mro()`` that rotates ``cls`` to the end skips the
    hook silently, and a ``__new__`` that assigns after ``super().__new__``
    lands after it. In BOTH cases :func:`_sealed_violations` still returns
    the violation — the check was right and its TIMING was wrong. So it is
    taken again here, from ``validate_params`` and from ``run``, which are
    the two places the gate is actually consulted.

    This closes the class of attempt that skips the definition-time hook.
    It does not make the seal an authority boundary: a caller who replaces
    ``run`` or ``validate_params`` outright is not using this gate at all,
    which is ADR-0122's ``uses:`` problem and not this one's.
    :data:`GATE_FACTS` carries the executed outcome for every attempt.
    """
    problems = []
    violations = _sealed_violations(cls)
    if violations:
        problems.append(
            f"{cls.__name__} may not override {', '.join(violations)} — it "
            "resolves them to something other than FinalRefit's own, so the "
            "release gate every refusal here goes through is not the one "
            "this class is running"
        )
    supplied = _metaclass_supplied(cls)
    if supplied:
        problems.append(
            f"{cls.__name__} may not take a metaclass supplying "
            f"{', '.join(supplied)} — {type(cls).__name__} answers "
            "class-level lookup ahead of the MRO the seal walks, so a "
            "sealed name may not appear there at all"
        )
    return problems


class FinalRefit(Node):
    """One frozen-winner refit over an attested completed run and ten labelled wires (ADR-0166, completing ADR-0116).

    Binds three things before it fits anything, and refuses when any of
    them is absent, incomplete or substituted:

    1. **An immutable completed upstream run.** The pinned final-HPO
       document identity must be reproduced by the run directory's own
       ``config.json``, its ``resolved.json`` must bind the same
       identity, the run must have reached a clean terminal ``"ran"``
       state, and each ``scan_hNN`` node must have recorded the pinned
       evidence under a record stamped with that same document identity
       AND corroborated by that run's carry
       (:meth:`~dskit.pipeline.driver.RunAttestation.attested_output`).
       Every artifact is re-read and re-digested from bytes at every
       call, so a run mutated after binding refuses.
    2. **Content-derived identities for the materialized refit rows.**
       Each wire's identity is
       :func:`~dskit.pipeline.driver.row_set_identity` over the rows'
       own JSON content — never their order, their producer's name, a
       path, or a label supplied beside them — and must equal the
       identity ``refit_identity.rows`` pins for that head.
    3. **Ten labelled input wires.** :data:`HEADS` is the only authority
       for the labels. Every row on a port carries its own
       :data:`WIRE_LABEL_FIELD`, and a missing, extra, empty, swapped or
       mislabelled wire refuses by name before any fit begins.

    **The production channel is closed.** Only :data:`FIXTURE_CHANNEL`
    can execute, and a fixture may never claim the shipped final-HPO
    document identity, so a fixture run and a real final-model release
    are runs of different documents. :data:`PRODUCTION_CHANNEL` refuses
    outright, because a real release needs the signed run-output
    attestation contract ADR-0122 specifies and nothing builds yet.
    Every head's hashed
    bundle training identity carries the channel and
    ``deployment_eligible: false``, and ``write_bundle``'s content hash
    covers ``training_identities``, so a written bundle cannot be
    relabelled without failing :func:`load_bundle`.

    **What this is NOT: a root of trust, or evidence of authorization.**
    Every refusal here is an IN-PROCESS check, and the seal on
    :data:`_FINAL_METHODS` is an accident-and-drift guard, **not an
    authority boundary**. :data:`GATE_FACTS` is the ONE authoritative
    statement of what this gate halts and where each attempt lands —
    declared as data, with a probe per entry that performs the attempt and
    asserts both declared fields. This paragraph cites it and does not
    restate it: a restatement is how one copy came to be false in round 4,
    three in round 5, and one more in round 6 that round 7 found. At least
    one declared entry opens a release entry point, and none of them edits a
    repository file, so the seal buys ordinary-caller safety and drift
    detection, never defence against a caller who wants past it.

    The run directory this node reads is UNAUTHENTICATED (ADR-0119's
    disclosed gap: nothing hash-chains ``nodes/*.json`` to
    ``resolved.json``), so anyone with write access to it can fabricate the
    evidence. A bundle's stamp records what the writing process believed,
    never that anyone authorized it. ADR-0122 — accepted 2026-09-12, and
    NOT built — states the rule this class obeys rather than contradicts:
    a Python resolver cannot be a root of trust, and release trust must
    begin outside Python.

    Parameters
    ----------
    params : dict
        ``release_channel`` (one of :data:`RELEASE_CHANNELS`, required),
        ``hpo_run_dir`` (str, required), ``hpo_document_sha256`` (str,
        required), ``hpo_evidence`` (dict keyed by exactly
        :data:`HEADS`, required), ``feature_order`` (list of str,
        required), ``categorical_feature`` (list of int, required),
        ``categorical_encoding`` (dict, required), ``predict_fixture``
        (list, required), ``refit_identity`` (dict, required), ``seed``
        (int >= 0, default 0).

    Examples
    --------
    Construct the node against an already attested fixture release::

        node = FinalRefit("refit", {
            "release_channel": FIXTURE_CHANNEL,
            "hpo_run_dir": run_dir, "hpo_document_sha256": document.hash,
            "hpo_evidence": manifests, "feature_order": ["f0", "f1"],
            "categorical_feature": [], "categorical_encoding": {},
            "predict_fixture": [[0.0, 1.0]], "refit_identity": identity,
        })
        node.run(ctx, wires)["manifest"]["heads"]
        # -> ['h01', 'h02', 'h03', 'h04', 'h05', 'h06', 'h07', 'h08', 'h09', 'h10']
    """

    #: Every name this class defines, plus the inherited hooks its release
    #: gate resolves through — including ``__getattribute__``, which a
    #: ``vars(cls)`` check could never surface. ``__init_subclass__``
    #: refuses a subclass that resolves any of them to something other than
    #: this class's own, because ``uses: "module:ClassName"`` accepts ANY
    #: class: a subclass overriding ``_channel_problems`` once constructed
    #: on the production channel and, with ``_release_identity`` also
    #: replaced, stamped ``deployment_eligible: True`` (round-1 and round-2
    #: reviews, 2026-09-18). The list is restated independently by
    #: ``tests/test_final_model.py``, and a test refuses any member of this
    #: class absent from it — an unsealed hook added later fails there, not
    #: in review. Read ``__init_subclass__``'s own docstring for what this
    #: does NOT cover; it is an accident guard, not an authority boundary.
    _FINAL_METHODS = (
        "__getattr__",
        "__getattribute__",
        "__init__",
        "__init_subclass__",
        "__new__",
        "_FINAL_METHODS",
        "_PARAMS",
        "_attestation",
        "_channel",
        "_channel_problems",
        "_estimator_params",
        "_hpo_template",
        "_identity_problems",
        "_lean_drop",
        "_release_identity",
        "_row_identities",
        "__setattr__",
        "_run_pin_problems",
        "_schema_problems",
        "_verified_hpo_outputs",
        "_winner_from_evidence",
        "_winners",
        "_wire_problems",
        "artifact_dir",
        "outputs",
        "role",
        "run",
        "validate_inputs",
        "validate_params",
    )

    def __init_subclass__(cls, **kwargs):
        """Refuse a subclass that resolves a sealed member to anything but this class's own.

        Each name in :data:`_FINAL_METHODS` is resolved by
        :func:`_resolved_through_the_mro` — a walk of the class's real
        ``__mro__``, reading each entry's real ``__dict__`` through
        ``type``'s own descriptors so no metaclass participates in either —
        and compared by IDENTITY with what ``FinalRefit`` itself resolves it
        to. The hook takes :func:`_unsealed_problems`, the SAME verdict
        ``validate_params`` and ``run`` take, so definition time and consult
        time cannot disagree about what a violation is; that verdict also
        covers :func:`_metaclass_supplied`, because class-level lookup
        consults the metaclass ahead of the MRO this walk reads.

        Scope lives in :data:`GATE_FACTS`, and this docstring states none of
        it. Each entry there names one attempt, carries the boolean for
        whether class creation is halted and the tuple of
        :data:`RELEASE_ENTRY_POINTS` the attempt opens, and has a probe in
        ``tests/test_final_model.py`` that performs the attempt for real and
        asserts both fields against what is observed. Prose is the wrong
        home for either: round-5 review inverted three sentences here with
        no test failing, and round-7 review found a fourth that had survived
        round 6 — one clause naming four mechanisms and their common outcome,
        which read exactly as plausibly with that outcome negated. A
        sentence carries no polarity a suite can see; a field beside a probe
        does.

        This is an accident-and-drift guard, and it is **not an authority
        boundary** — the same scope :mod:`dskit.pipeline.uncertainty_set`
        states for this idiom. The trust root for a real release is
        ADR-0122's out-of-Python launcher, which is not built.
        """
        super().__init_subclass__(**kwargs)
        problems = _unsealed_problems(cls)
        if problems:
            raise SealRefused(
                "; ".join(problems)
                + ". A subclass that replaces part of the release gate can "
                "stamp a release nothing earned. This is an accident guard "
                "checked at class definition, not a root of trust (ADR-0166)"
            )

    role = "train"
    outputs = ("bundle_path", "manifest")
    _PARAMS = (
        "release_channel",
        "hpo_run_dir",
        "hpo_document_sha256",
        "hpo_evidence",
        "feature_order",
        "categorical_feature",
        "categorical_encoding",
        "predict_fixture",
        "refit_identity",
        "seed",
    )

    @classmethod
    def validate_params(cls, params):
        """Return problems with the release channel, run pins, bundle schema knobs and refit identity."""
        problems = []
        reject_unknown_params(problems, params, cls._PARAMS)
        problems += _unsealed_problems(cls)
        problems += cls._channel_problems(params)
        problems += cls._run_pin_problems(params)
        problems += cls._schema_problems(params)
        problems += cls._identity_problems(params.get("refit_identity"))
        seed = params.get("seed", 0)
        if type(seed) is not int or seed < 0:
            problems.append("seed must be a nonnegative integer")
        return problems

    @classmethod
    def _channel_problems(cls, params):
        """Return the one gate that keeps a real release closed and a fixture unable to claim one."""
        channel = params.get("release_channel")
        if channel not in RELEASE_CHANNELS:
            return [
                f"release_channel must be one of {list(RELEASE_CHANNELS)!r}, "
                f"got {channel!r} — a release that does not say which channel "
                "it belongs to is not a release"
            ]
        try:
            shipped = final_hpo_document_identity()
        except ValueError as exc:
            return [f"the shipped final-HPO document is unreadable ({exc})"]
        pinned = params.get("hpo_document_sha256")
        if channel == PRODUCTION_CHANNEL:
            problems = [
                "FinalRefit is non-executable on the production channel: a real "
                "final-model release requires the signed run-output attestation "
                "contract ADR-0122 accepted on 2026-09-12 and not built (issuer, "
                "key authority, trusted clock, runtime identity, and an "
                "out-of-Python launch root). Driver run attestation alone does not "
                "authorize one, and no placeholder here can supply it"
            ]
            if pinned != shipped:
                problems.append(
                    "hpo_document_sha256 must be the shipped configs/"
                    f"run-final-hpo.json identity {shipped!r} on the production "
                    f"channel, got {pinned!r}"
                )
            return problems
        if pinned == shipped:
            return [
                "a fixture release may not claim the shipped final-HPO document "
                f"identity {shipped!r}: synthetic assembly is not a completed "
                "real final-model release, and the two must stay distinguishable"
            ]
        return []

    @classmethod
    def _run_pin_problems(cls, params):
        """Problems with the pins naming the completed upstream run and its ten evidence artifacts."""
        problems = []
        run_dir = params.get("hpo_run_dir")
        if not isinstance(run_dir, str) or not run_dir:
            problems.append("hpo_run_dir must be a non-empty path")
        elif _PENDING in run_dir.upper():
            problems.append(
                "FinalRefit is non-executable: hpo_run_dir is pending a real "
                "final-HPO run"
            )
        if not _is_sha256(params.get("hpo_document_sha256")):
            problems.append("hpo_document_sha256 must be a lowercase sha256 digest")
        evidence = params.get("hpo_evidence")
        if not isinstance(evidence, dict) or set(evidence) != set(HEADS):
            problems.append(f"hpo_evidence must be keyed by exactly {list(HEADS)!r}")
        elif any(not isinstance(value, dict) or not value for value in evidence.values()):
            problems.append(
                "FinalRefit is non-executable: every hpo_evidence pin must be a "
                "completed-run JSON-artifact manifest (the attestation the driver "
                "verifies), not a placeholder"
            )
        return problems

    @classmethod
    def _schema_problems(cls, params):
        """Problems with the bundle schema knobs every head shares."""
        problems = []
        features = params.get("feature_order")
        if (
            not isinstance(features, list)
            or not features
            or len(features) != len(set(features))
            or any(not isinstance(name, str) or not name for name in features)
        ):
            problems.append("feature_order must be a non-empty unique string list")
        categories = params.get("categorical_feature")
        if not isinstance(categories, list) or any(type(i) is not int for i in categories):
            problems.append("categorical_feature must be a list of integer indices")
        if not isinstance(params.get("categorical_encoding"), dict):
            problems.append("categorical_encoding must be a mapping")
        fixture = params.get("predict_fixture")
        if not isinstance(fixture, list) or not fixture:
            problems.append("predict_fixture must be a non-empty list")
        return problems

    @classmethod
    def _identity_problems(cls, identity):
        """Problems with the refit identity: data/cache pins, the locked calendar, and ten content-derived row pins."""
        fields = {
            "source", "cache", "rows", "train_start_ms", "refit_end_ms",
            "embargo_start_ms", "embargo_end_ms",
        }
        if not isinstance(identity, dict) or set(identity) != fields:
            return [f"refit_identity must carry exactly {sorted(fields)!r}"]
        problems = []
        for field in ("source", "cache"):
            value = identity[field]
            digest = value.get("sha256") if isinstance(value, dict) else None
            if not isinstance(value, dict) or not value or not _is_sha256(digest):
                problems.append(
                    f"refit_identity.{field} must be a non-empty identity with sha256"
                )
        rows = identity["rows"]
        if not isinstance(rows, dict) or set(rows) != set(HEADS):
            problems.append(
                f"refit_identity.rows must pin one content-derived row identity "
                f"per head, keyed by exactly {list(HEADS)!r}"
            )
        elif not all(_is_sha256(value) for value in rows.values()):
            problems.append(
                "FinalRefit is non-executable: every refit_identity.rows pin must "
                "be the lowercase sha256 that row set's own CONTENT produces "
                "(dskit.pipeline.driver.row_set_identity), not a placeholder"
            )
        elif len(set(rows.values())) != len(HEADS):
            problems.append(
                "refit_identity.rows pins two heads to the same materialized row "
                "set — ten wires, ten row sets"
            )
        if type(identity["train_start_ms"]) is not int or identity["train_start_ms"] < 0:
            problems.append("refit_identity.train_start_ms must be a nonnegative integer")
        expected = {
            "refit_end_ms": LOCKBOX_START_MS,
            "embargo_start_ms": EMBARGO_START_MS,
            "embargo_end_ms": EMBARGO_END_MS,
        }
        for field, value in expected.items():
            if identity[field] != value:
                problems.append(f"refit_identity.{field} must equal {value}")
        return problems

    def validate_inputs(self, inputs):
        """Require exactly ten wires, each LABELLED with its own head and inside the permitted window."""
        if not isinstance(inputs, dict) or set(inputs) != set(HEADS):
            return [f"inputs must be keyed by exactly {list(HEADS)!r}"]
        identity = self.params.get("refit_identity")
        start = identity.get("train_start_ms") if isinstance(identity, dict) else None
        problems = []
        for head in HEADS:
            problems += self._wire_problems(head, inputs[head], start)
        return problems

    def _wire_problems(self, head, rows, start):
        """Problems with one labelled wire: shape, its own label, and its rows' instants."""
        if not isinstance(rows, list) or not rows:
            return [f"{head} must be a non-empty list of labelled rows"]
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                return [f"{head} row {index} is not a mapping"]
            label = row.get(WIRE_LABEL_FIELD)
            if label != head:
                return [
                    f"{head} is wired to rows labelled {label!r}: a wire is bound "
                    f"by its {WIRE_LABEL_FIELD!r} label, so a swapped, duplicated "
                    "or mislabelled producer refuses rather than being refitted "
                    "under the wrong head"
                ]
            ts_ms = row.get("ts_ms")
            if type(ts_ms) is not int:
                return [f"{head} row {index} carries no integer ts_ms"]
            if start is not None and ts_ms < start:
                return [f"{head} contains a row before refit_identity.train_start_ms"]
            if not permitted_for_refit(ts_ms):
                return [
                    f"{head} contains a row at {ts_ms} — outside the permitted "
                    "pre-lockbox, post-embargo window"
                ]
        return []

    def _channel(self):
        """Return this release's channel, refusing the still-closed production one at every entry point."""
        problems = self._channel_problems(self.params)
        if problems:
            raise ValueError(f"FinalRefit: {problems[0]}")
        return self.params["release_channel"]

    def _attestation(self):
        """Return the read-only, fail-closed evidence reader over the pinned run directory."""
        return RunAttestation(self.params["hpo_run_dir"])

    def _verified_hpo_outputs(self):
        """Return the ten pinned evidence manifests, each re-earned from the completed run's own record and carry."""
        document_hash = self.params["hpo_document_sha256"]
        attestation = self._attestation()
        if not attestation.completed() or not attestation.binds_document_identity(
            document_hash
        ):
            raise ValueError(
                "FinalRefit: no trustworthy run attestation binds the HPO document, "
                "run, node outputs, and carry"
            )
        outputs = {}
        for head in HEADS:
            node_key = f"{_PRODUCER_PREFIX}{head}"
            attested = attestation.attested_output(
                node_key, HPO_LEDGER_OUTPUT, document_hash
            )
            if attested is None:
                raise ValueError(
                    f"FinalRefit: no trustworthy run attestation binds {node_key}'s "
                    f"producer record and carry to the pinned HPO document"
                )
            if attested != self.params["hpo_evidence"][head]:
                raise ValueError(
                    f"FinalRefit: {head}'s pinned evidence is not what {node_key} "
                    "recorded in that completed run — a substituted manifest is "
                    "not attested evidence"
                )
            outputs[head] = attested
        return outputs

    def _winner_from_evidence(self, head, evidence):
        """Rebuild the frozen inventory, ledger, and 1-SE ruling."""
        template = self._hpo_template()
        model = template["model"]
        inventory = CandidateInventory(
            model["hpo_space"], n_trials=model["hpo_trials"], seed=model["hpo_seed"]
        )
        ledger_obj, selection = evidence["ledger"], evidence["selection"]
        if not isinstance(ledger_obj, dict) or set(ledger_obj) != {
            "inventory_digest", "evidence_fields", "rows",
        }:
            raise ValueError(f"FinalRefit: {head} ledger has the wrong shape")
        if ledger_obj["inventory_digest"] != inventory.digest:
            raise ValueError(f"FinalRefit: {head} ledger inventory differs from pinned HPO")
        if tuple(ledger_obj["evidence_fields"]) != EVIDENCE_FIELDS:
            raise ValueError(f"FinalRefit: {head} ledger evidence fields differ from contract")
        ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
        for row in ledger_obj["rows"]:
            ledger.record(row["overrides"], row["score"], **{
                key: value for key, value in row.items() if key not in {"overrides", "score"}
            })
        if ledger.to_obj() != ledger_obj:
            raise ValueError(f"FinalRefit: {head} ledger is incomplete or noncanonical")
        ruled = OneStandardErrorSelector(select="max", simplicity_key=simplicity_key).select(ledger)
        if ruled.to_obj() != selection:
            raise ValueError(f"FinalRefit: {head} selection is not the pinned one-standard-error ruling")
        return ruled.selected_candidate

    def _hpo_template(self):
        templates = self._hpo_document["stages"]["finalist"]["params"]["templates"]
        matches = [row for row in templates if row.get("family") == "pooled-lightgbm"]
        if len(matches) != 1:
            raise ValueError("FinalRefit: pinned HPO document has no unique LightGBM recipe")
        return matches[0]

    def _winners(self):
        # The attestation comes FIRST: a run directory whose own config.json
        # no longer reproduces the identity its resolved.json claims must
        # refuse as an unattested run, not as a mismatched pin.
        attested = self._verified_hpo_outputs()
        source_document = load_document(
            os.path.join(self.params["hpo_run_dir"], "config.json")
        )
        if source_document.hash != self.params["hpo_document_sha256"]:
            raise ValueError("FinalRefit: final-HPO document identity does not match its pin")
        self._hpo_document = source_document.to_obj()
        winners = {}
        inventory_digest = None
        manifest_digests = [attested[head].get("sha256") for head in HEADS]
        if len(set(manifest_digests)) != len(HEADS):
            raise ValueError("FinalRefit: every head requires a distinct evidence manifest")
        for head in HEADS:
            manifest = attested[head]
            evidence = resolve_json_artifact(
                self.params["hpo_run_dir"], manifest
            )
            if not isinstance(evidence, dict) or set(evidence) != {
                "producer_key", "feature_order", "categorical_feature",
                "ledger", "selection",
            }:
                raise ValueError(f"FinalRefit: {head} evidence has the wrong shape")
            if evidence["producer_key"] != f"{_PRODUCER_PREFIX}{head}":
                raise ValueError(f"FinalRefit: {head} evidence has the wrong producer")
            if evidence["feature_order"] != self.params["feature_order"]:
                raise ValueError(f"FinalRefit: {head} feature order differs from the pin")
            if evidence["categorical_feature"] != self.params["categorical_feature"]:
                raise ValueError(f"FinalRefit: {head} category contract differs from the pin")
            ledger = evidence["ledger"]
            digest = ledger.get("inventory_digest") if isinstance(ledger, dict) else None
            if inventory_digest is None:
                inventory_digest = digest
            elif digest != inventory_digest:
                raise ValueError("FinalRefit: every head must share one candidate inventory")
            winners[head] = dict(self._winner_from_evidence(head, evidence))
        return winners

    def _estimator_params(self):
        """Return the bound document's own shared estimator constructor params."""
        params = dict(self._hpo_template()["model"]["estimator_params"])
        if params.pop("estimator", None) != ESTIMATOR_PATH:
            raise ValueError("FinalRefit: pinned HPO document has the wrong estimator")
        params.pop("drop", None)
        return params

    def _lean_drop(self):
        """Return the bound document's own column mask, read from the run it attested and never restated here."""
        drop = self._hpo_template()["model"]["estimator_params"].get("drop")
        if not isinstance(drop, list) or not drop or any(
            not isinstance(name, str) or not name for name in drop
        ):
            raise ValueError(
                "FinalRefit: pinned HPO document declares no column mask to refit under"
            )
        return list(drop)

    def _row_identities(self, inputs):
        """Each wire's CONTENT-derived identity, checked against its pin and against its nine siblings."""
        pinned = self.params["refit_identity"]["rows"]
        identities = {}
        for head in HEADS:
            identity = row_set_identity(list(inputs[head]))
            if identity != pinned[head]:
                raise ValueError(
                    f"FinalRefit: {head}'s materialized rows identify as "
                    f"{identity} but refit_identity.rows pins {pinned[head]} — "
                    "the rows supplied are not the rows this release binds"
                )
            identities[head] = identity
        if len(set(identities.values())) != len(HEADS):
            raise ValueError(
                "FinalRefit: two wires carry the identical materialized row set — "
                "ten wires, ten row sets"
            )
        return identities

    def _release_identity(self, channel, rows_sha256):
        """Return the bound window, data and channel facts every head's HASHED bundle training identity carries (ADR-0116)."""
        identity = self.params["refit_identity"]
        return {
            "release_channel": channel,
            "deployment_eligible": False,
            "rows_sha256": rows_sha256,
            "source": identity["source"],
            "cache": identity["cache"],
            "train_start_ms": identity["train_start_ms"],
            "refit_end_ms": identity["refit_end_ms"],
            "embargo_start_ms": identity["embargo_start_ms"],
            "embargo_end_ms": identity["embargo_end_ms"],
            "hpo_document_sha256": self.params["hpo_document_sha256"],
        }

    def run(self, ctx, inputs):
        """Refit the ten frozen winners once over attested rows, write one bundle, and prove it replays."""
        from dskit.pipeline.libs.sklearn import load_bundle, write_bundle

        unsealed = _unsealed_problems(type(self))
        if unsealed:
            raise ValueError(f"FinalRefit: {unsealed[0]}")
        channel = self._channel()
        problems = self.validate_inputs(inputs)
        if problems:
            raise ValueError(f"FinalRefit: {'; '.join(problems)}")
        winners = self._winners()
        identities = self._row_identities(inputs)
        shared = self._estimator_params()
        drop = self._lean_drop()
        feature_order = list(self.params["feature_order"])
        # One constructor recipe per head: the document's shared estimator
        # params under that head's OWN frozen winner, and nothing else.
        merged = {head: {**shared, **winners[head]} for head in HEADS}
        estimators, training_identities = refit_heads(
            {head: list(inputs[head]) for head in HEADS},
            merged,
            feature_order=feature_order,
            lean_drop=drop,
            estimator_path=ESTIMATOR_PATH,
            seed=self.params.get("seed", 0),
            categorical_feature=self.params["categorical_feature"],
        )
        surviving = [name for name in feature_order if name not in set(drop)]
        for head in HEADS:
            training_identities[head].update(
                self._release_identity(channel, identities[head])
            )
        path = os.path.join(self.artifact_dir(ctx), BUNDLE_FILENAME)
        manifest = write_bundle(
            path,
            list(HEADS),
            estimators,
            head_params={
                head: {"estimator": _WRAPPER_PATH, "estimator_params": dict(merged[head])}
                for head in HEADS
            },
            feature_order=feature_order,
            surviving_features={head: list(surviving) for head in HEADS},
            categorical_encoding=dict(self.params["categorical_encoding"]),
            training_identities=training_identities,
            predict_fixture=[list(row) for row in self.params["predict_fixture"]],
        )
        replayed = load_bundle(path)
        if replayed.manifest["predict_checksum"] != manifest["predict_checksum"]:
            raise ValueError(
                "FinalRefit: the written bundle does not replay to the beliefs it "
                "was written with"
            )
        return {"bundle_path": path, "manifest": manifest}


#: The ten independent LightGBM lead heads (ADR-0114 §2): one per direct
#: lead h=1..10, sharing feature schema, category rules, search space and
#: release identity — never trees or weights.
HEADS = tuple(f"h{i:02d}" for i in range(1, 11))

#: The two release channels. ``"fixture"`` is the only one that can
#: execute: it proves the machinery on deterministic synthetic rows and
#: may never claim the shipped final-HPO document identity.
#: ``"production"`` — a real final-model release — refuses outright,
#: because it needs the signed run-output attestation contract ADR-0122
#: accepted and nothing builds yet. Synthetic helper assembly is not a
#: completed real
#: final-model release, and the two stay distinguishable by the document
#: they are bound to AND by the channel stamped into every head's hashed
#: bundle training identity.
FIXTURE_CHANNEL = "fixture"
PRODUCTION_CHANNEL = "production"
RELEASE_CHANNELS = (FIXTURE_CHANNEL, PRODUCTION_CHANNEL)

#: The two entry points a release channel is decided at, named once so
#: :data:`GATE_FACTS` can declare WHERE an attempt lands rather than describe
#: it: ``validate_params`` resolves ``_channel_problems`` on the CLASS, and
#: ``run`` resolves ``_channel`` on the INSTANCE. The distinction is not
#: academic — two declared attempts open one of them and not the other.
RELEASE_ENTRY_POINTS = ("validate_params", "run")

#: Every fact this gate discloses about itself, as DATA the test suite
#: EXECUTES rather than prose it greps. Each entry is
#: ``(name, refuses, reach, attempt)``:
#:
#: * ``name`` identifies one attempt on the gate.
#: * ``refuses`` is whether class creation is halted by the seal itself —
#:   never by Python rejecting the construction for its own reasons, which
#:   the probes assert against separately.
#: * ``reach`` is the tuple of :data:`RELEASE_ENTRY_POINTS` the attempt
#:   actually opens on the production channel, in that order, and ``()``
#:   when it opens neither.
#: * ``attempt`` describes only WHAT IS TRIED — never what follows, which is
#:   ``refuses`` and ``reach`` alone.
#:
#: ``tests/test_final_model.py`` holds one probe per name, performs the
#: attempt for real, and asserts the observed refusal AND the observed reach
#: equal what is declared here, so changing either field contradicts an
#: executed fact.
#:
#: This replaces a prose limits list. Round-5 review inverted three sentences
#: in this module — "a custom metaclass CANNOT ...", "It fails OPEN, not
#: closed" — and the whole 116-test suite stayed green, because a substring
#: assertion cannot see polarity. Round-7 review then found the same defect
#: twice more, which is why ``reach`` is a field and not a sentence: round 6
#: moved the polarity into a boolean but left "does NOT reach run()" in
#: prose, and prose was wrong about it for a metaclass attempt it had
#: dropped from the list entirely. Read this tuple, not a paragraph.
#:
#: Every ``refuses=False`` entry is a real, reachable attempt, and none of
#: them edits a repository file: the probes run from the test suite and
#: reach the gate through the same import-and-``getattr`` that a document's
#: ``uses:`` performs.
GATE_FACTS = (
    ("refuses_an_override_in_the_subclass_body", True, (),
     "a direct subclass whose body supplies a sealed member"),
    ("refuses_an_override_from_a_mixin_in_the_mro", True, (),
     "a mixin earlier in the MRO supplying a sealed member"),
    ("refuses_an_override_at_any_subclass_depth", True, (),
     "a grandchild of an intermediate subclass supplying a sealed member"),
    ("refuses_an_override_of_an_inherited_hook", True, (),
     "a subclass supplying __getattribute__, which vars(cls) cannot surface"),
    ("refuses_an_object_whose_equality_is_rigged", True, (),
     "a sealed member supplied as an object whose __eq__ answers True"),
    ("refuses_a_subclass_that_shrinks_the_sealed_list", True, (),
     "a subclass body supplying _FINAL_METHODS = () beside an override"),
    ("refuses_a_subclass_that_replaces_init_subclass", True, (),
     "a subclass supplying its own __init_subclass__"),
    ("refuses_a_metaclass_that_shadows_the_class_dict", True, (),
     "a subclass supplying a sealed member in its own body, whose metaclass "
     "also supplies __dict__"),
    ("refuses_a_metaclass_that_doctors_the_mro", False, (),
     "a metaclass whose mro() drops a base that supplies a sealed member"),
    ("refuses_a_metaclass_that_rotates_the_class_out_of_its_own_mro", False, (),
     "a metaclass whose mro() puts the new class AFTER FinalRefit, so that "
     "type.__new__'s super(cls, cls).__init_subclass__ lookup finds nothing"),
    ("refuses_a_metaclass_that_injects_the_guard_beside_its_payload", False, (),
     "a metaclass __new__ that assigns a compliant _unsealed_problems beside "
     "the sealed member it is really after"),
    ("refuses_a_substituted_channel_resolver", False, (),
     "a metaclass __new__ that assigns _channel itself, the member run() "
     "resolves through the instance"),
    ("refuses_a_subclass_overriding_an_unsealed_node_hook", False, (),
     "a subclass supplying Node.serving_effect, which the gate never "
     "resolves through — sealing it would be over-reach"),
    ("refuses_a_mixin_whose_init_subclass_swallows_the_hook", False, (),
     "a mixin earlier in the MRO defining __init_subclass__ without calling "
     "super(): a mixin derives from nothing, so sealing cannot reach it"),
    ("refuses_post_hoc_assignment_on_this_class", False, RELEASE_ENTRY_POINTS,
     "assigning a sealed member onto FinalRefit itself after import — the "
     "shape run-final-refit.json wires, needing no subclass at all"),
    ("refuses_post_hoc_assignment_on_a_subclass", False, (),
     "assigning a sealed member onto an empty-bodied subclass after it is "
     "defined, which the definition-time check has already passed"),
    ("refuses_a_metaclass_that_injects_after_class_creation", False, (),
     "a metaclass __new__ that builds the class from an empty namespace and "
     "assigns a sealed member onto it before returning it"),
    ("refuses_per_instance_shadowing", False, ("run",),
     "assigning a sealed member onto one constructed instance"),
    ("refuses_a_metaclass_that_intercepts_class_attribute_access", True, (),
     "a metaclass __getattribute__ answering class-level lookups"),
    ("refuses_a_metaclass_that_supplies_a_sealed_name_as_a_data_descriptor",
     True, (),
     "a metaclass carrying validate_params as an object with __get__ and "
     "__set__, which type.__getattribute__ prefers over the whole class MRO"),
    ("refuses_a_metaclass_supplying_a_delete_only_data_descriptor", True, (),
     "a metaclass carrying validate_params as an object with __get__ and "
     "__delete__ but no __set__, which type.__getattribute__ still prefers "
     "over the whole class MRO"),
    ("refuses_a_metaclass_whose_own_metaclass_rigs_equality", True, (),
     "a metaclass supplying a sealed name whose own metaclass answers True "
     "to every ==, so a baseline comparison by equality would skip it"),
    ("refuses_a_metaclass_supplying_a_class_that_is_itself_a_data_descriptor",
     True, (),
     "a metaclass binding validate_params to a CLASS whose own metaclass "
     "defines __get__ and __set__, so the descriptor protocol lives one "
     "level further out than the payload"),
    ("refuses_a_metaclass_swapped_in_after_the_class_is_defined", False,
     ("validate_params",),
     "assigning a new metaclass onto an already-defined subclass with "
     "cls.__class__ = ..., which the definition-time hook has already passed "
     "and whose descriptor then answers in place of the member that would "
     "re-take the verdict"),
    ("shipped_configuration_refuses_to_plan", True, (),
     "loading configs/run-final-refit.json and planning it"),
)

#: The field every labelled wire row carries, naming the head that wire
#: belongs to. It is ordinary row CONTENT — it moves the wire's
#: content-derived identity like any other field — and never a
#: caller-supplied identity of its own.
WIRE_LABEL_FIELD = "head"

#: The producer node key prefix a head's evidence must have been recorded
#: under, and the output name it must have been recorded as: the
#: ``scan_h01``..``scan_h10`` search nodes of the attested final-HPO run.
_PRODUCER_PREFIX = "scan_"
HPO_LEDGER_OUTPUT = "hpo_ledger"

#: The one estimator this build refits, named once: ``_estimator_params``
#: checks the pinned document declares it and :func:`refit_heads` is
#: constructed with it, so the two can never drift apart. ``_WRAPPER_PATH``
#: is what :func:`refit_heads` actually persists (it always wraps the
#: estimator to enforce the column mask), which is the class the bundle
#: manifest must declare or ``load_bundle`` refuses it as relabelled.
ESTIMATOR_PATH = "lightgbm.LGBMRegressor"
_WRAPPER_PATH = "dskit.pipeline.libs.sklearn.ColumnSubsetEstimator"

#: The one bundle filename a refit writes into its own artifact directory.
BUNDLE_FILENAME = "final-model.joblib"

#: The marker a config placeholder carries until a real run fills it.
_PENDING = "PENDING"


def _is_sha256(value):
    """Whether the value is a lowercase 64-character hex digest."""
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


#: The one seed :func:`build_candidate_inventory` defaults to — matches
#: the ``hpo_seed`` already declared beside this grid in
#: ``configs/run-final-hpo.json``'s pooled-lightgbm template, so a caller
#: who never overrides ``seed`` reuses the same convention rather than an
#: arbitrary new one.
DEFAULT_INVENTORY_SEED = 0

#: The exact frozen inventory size ADR-0114 §2 locks: "the same exact 24
#: hyperparameter combinations."
FROZEN_CANDIDATE_COUNT = 24

#: Every per-candidate evidence field :func:`run_lead_selection` records,
#: beyond TrialLedger's own reserved ``overrides``/``score``. ``se`` is
#: OneStandardErrorSelector's own required field; ``diagnostics`` is the
#: full :func:`dskit.pipeline.stats.cluster_bootstrap_t` result (so the
#: ledger never re-derives what that call already returned); the rest are
#: the plan's own named diagnostics — "train/validation gaps, collapsed
#: prediction variance, and whether the selected value lies on a searched
#: boundary" (§2) plus the fit identity the plan's §6 test list requires
#: ("fit seed, cuts, row counts").
EVIDENCE_FIELDS = (
    "se",
    "diagnostics",
    "on_boundary",
    "fit_seed",
    "cuts",
    "n_rows",
    "train_val_gap",
    "collapsed_prediction_variance",
)

#: The one real source of the P16 lean mask — never a second hardcoded
#: copy of its 33 names (root CLAUDE.md's duplication rule).
_DEFAULT_LEAN_MASK_CONFIG = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "run-p16-feature-mask-zoo.json",
    )
)

#: The one real source of the locked 24-combination LightGBM HPO grid —
#: never a second hardcoded copy of its five dimensions (root CLAUDE.md's
#: duplication rule; the same rule :func:`lean_feature_drop` already
#: follows for the mask).
_DEFAULT_HPO_CONFIG = os.path.normpath(
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "configs",
        "run-final-hpo.json",
    )
)

#: The locked pre-March calendar (ADR-0114 §2, carried from the master
#: plan): fit/HPO/refit reads stop before the 2026-03-01 lockbox, and the
#: 2025-12-01 session is embargoed — read by neither the fit nor the HPO
#: window. UTC midnight boundaries; the plan does not name a session
#: timezone for this specific cut (unlike the capital-flow calendar's
#: explicit America/New_York, §11 item 2), so this module states the
#: assumption here rather than inventing a silent one.
def _epoch_ms(date_str):
    """One UTC-midnight epoch-ms boundary for a locked calendar date."""
    return int(
        datetime.strptime(date_str, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )


EMBARGO_START_MS = _epoch_ms("2025-12-01")
EMBARGO_END_MS = _epoch_ms("2025-12-02")
LOCKBOX_START_MS = _epoch_ms("2026-03-01")


def permitted_for_refit(ts_ms) -> bool:
    """Report whether ``ts_ms`` is inside the frozen-winner refit window.

    Parameters
    ----------
    ts_ms : int
        A row's event timestamp, epoch milliseconds.

    Returns
    -------
    bool
        ``True`` when ``ts_ms`` is strictly before the 2026-03-01
        lockbox AND outside the half-open 2025-12-01 embargo session.
    """
    if ts_ms >= LOCKBOX_START_MS:
        return False
    return not (EMBARGO_START_MS <= ts_ms < EMBARGO_END_MS)


def lean_feature_drop(config_path=None) -> tuple:
    """Return the exact P16 lean-mask column drop list, read from its one real source (ADR-0108) rather than hardcoded a second time.

    Parameters
    ----------
    config_path : str, optional
        Override path to the config (default: the shipped
        ``configs/run-p16-feature-mask-zoo.json`` beside this package).

    Returns
    -------
    tuple of str
        The 33 dropped column names, in the config's own declared order.

    Raises
    ------
    ValueError
        The config is missing, unreadable, or its ``"lean"`` template's
        shape does not match ADR-0108/ADR-0114 (not exactly one ``"lean"``
        template, the wrong family/estimator, or not exactly 33 names).

    Examples
    --------
    Read the shipped mask::

        drop = lean_feature_drop()
        len(drop)
        # -> 33
    """
    path = _DEFAULT_LEAN_MASK_CONFIG if config_path is None else config_path
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except OSError as exc:
        raise ValueError(f"lean_feature_drop: cannot read {path!r} ({exc})") from exc
    templates = (
        document.get("stages", {})
        .get("materialize", {})
        .get("params", {})
        .get("templates", [])
    )
    matches = [
        t for t in templates if isinstance(t, dict) and t.get("id") == "lean"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"lean_feature_drop: {path!r} must declare exactly one "
            f"templates[].id == 'lean', found {len(matches)}"
        )
    template = matches[0]
    model = template.get("model", {})
    if (
        template.get("family") != "pooled-lightgbm"
        or model.get("estimator")
        != "dskit.pipeline.libs.sklearn.ColumnSubsetEstimator"
    ):
        raise ValueError(
            f"lean_feature_drop: {path!r}'s 'lean' template does not match "
            "ADR-0108's pooled-lightgbm/ColumnSubsetEstimator shape"
        )
    drop = model.get("estimator_params", {}).get("drop")
    if (
        not isinstance(drop, list)
        or len(drop) != 33
        or not all(isinstance(name, str) and name for name in drop)
    ):
        raise ValueError(
            f"lean_feature_drop: {path!r}'s 'lean' template drop list is "
            "not the expected 33 non-empty column names"
        )
    return tuple(drop)


def hpo_space(config_path=None) -> dict:
    """Return the real five-dimension LightGBM HPO grid, read from its one real source (ADR-0114 §11.1) rather than hardcoded a second time.

    Parameters
    ----------
    config_path : str, optional
        Override path to the config (default: the shipped
        ``configs/run-final-hpo.json`` beside this package).

    Returns
    -------
    dict
        ``"learning_rate"``/``"num_leaves"``/``"min_child_samples"``/
        ``"reg_lambda"``/``"reg_alpha"`` -> that dimension's declared
        value list, in the config's own order — 324 combinations total.

    Raises
    ------
    ValueError
        The config is missing, unreadable, or its finalist template's
        shape does not match ADR-0114 (not exactly one
        ``family == "pooled-lightgbm"`` template, or no non-empty
        ``hpo_space`` mapping of dimension -> value list).

    Examples
    --------
    Read the shipped grid::

        space = hpo_space()
        sorted(space)
        # -> ['learning_rate', 'min_child_samples', 'num_leaves', 'reg_alpha', 'reg_lambda']
    """
    path = _DEFAULT_HPO_CONFIG if config_path is None else config_path
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except OSError as exc:
        raise ValueError(f"hpo_space: cannot read {path!r} ({exc})") from exc
    templates = (
        document.get("stages", {})
        .get("finalist", {})
        .get("params", {})
        .get("templates", [])
    )
    # Matched by FAMILY, not `id`: `id` is the finalist's candidate-name
    # component (ADR-0115 — it must read "lean" to match
    # FinalistCandidate's own "{id}-pooled-h{horizon}" recipe lookup
    # against the real P16 winner "lean-pooled-h10"), so pinning this
    # read to one fixed `id` string would refuse the very config it
    # exists to serve the moment that name changed for an unrelated
    # reason. `family == "pooled-lightgbm"` is the stable identity of
    # "the one LightGBM HPO recipe" this function's caller cares about.
    matches = [
        t for t in templates
        if isinstance(t, dict) and t.get("family") == "pooled-lightgbm"
    ]
    if len(matches) != 1:
        raise ValueError(
            f"hpo_space: {path!r} must declare exactly one "
            f"templates[] entry with family == 'pooled-lightgbm', found "
            f"{len(matches)}"
        )
    template = matches[0]
    model = template.get("model", {})
    space = model.get("hpo_space")
    if (
        not isinstance(space, dict)
        or not space
        or not all(
            isinstance(values, list) and values for values in space.values()
        )
    ):
        raise ValueError(
            f"hpo_space: {path!r}'s pooled-lightgbm template hpo_space is "
            "not a non-empty mapping of dimension -> non-empty value list"
        )
    return {name: list(values) for name, values in space.items()}


def final_hpo_document_identity(config_path=None) -> str:
    """Return the shipped final-HPO document's identity hash, read from the document itself rather than restated.

    The runtime pin behind the fixture/production split: a production
    release must BE this document's refit, and a fixture release may
    never claim to be. Reading it here means the identity lives in one
    place — ``configs/run-final-hpo.json`` — instead of being copied into
    a constant that drifts the day that document changes.

    Parameters
    ----------
    config_path : str, optional
        Override path to the config (default: the shipped
        ``configs/run-final-hpo.json`` beside this package).

    Returns
    -------
    str
        The document's lowercase sha256 identity hash — the same
        :attr:`dskit.pipeline.document.PipelineDocument.hash` the pipeline
        names run directories by.

    Raises
    ------
    ValueError
        The config is missing, unreadable, or is not a loadable pipeline
        document.

    Examples
    --------
    Read the shipped identity::

        len(final_hpo_document_identity())
        # -> 64
    """
    path = _DEFAULT_HPO_CONFIG if config_path is None else config_path
    try:
        return load_document(path).hash
    except (OSError, ConfigError, ValueError, TypeError) as exc:
        raise ValueError(
            f"final_hpo_document_identity: cannot read {path!r} ({exc})"
        ) from exc


def build_candidate_inventory(
    seed=DEFAULT_INVENTORY_SEED, n_trials=FROZEN_CANDIDATE_COUNT
) -> CandidateInventory:
    """Return the frozen candidate inventory every lead reuses byte-identically.

    ADR-0114 §2: "the candidate list is materialized once, ordered,
    hashed, and reused by every head. A lead must not draw its own random
    set."

    Parameters
    ----------
    seed : int
        The deterministic subsample-rank seed (default
        :data:`DEFAULT_INVENTORY_SEED`).
    n_trials : int
        The frozen inventory size (default :data:`FROZEN_CANDIDATE_COUNT`
        — the plan-locked 24).

    Returns
    -------
    CandidateInventory
        Deterministic: two calls with the same arguments produce
        byte-identical (equal-digest) inventories, over :func:`hpo_space`
        — the real 324-combination grid.

    Examples
    --------
    Build the plan-locked 24-combination inventory::

        inventory = build_candidate_inventory()
        len(inventory.combinations)
        # -> 24
    """
    return CandidateInventory(hpo_space(), n_trials=n_trials, seed=seed)


def squared_error_improvement(y, yhat, mu) -> float:
    """Return one decision's squared-error improvement of the model over the training-mean baseline.

    ADR-0114 §2: "that lead's equal-stock, training-mean-baseline
    forecast-accuracy contribution ... the same squared-error improvement
    semantics used by the outer model score — not Spearman IC."

    Parameters
    ----------
    y : float
        The realized outcome.
    yhat : float
        The candidate's prediction.
    mu : float
        The training-mean baseline's prediction (constant per fold).

    Returns
    -------
    float
        ``(y - mu) ** 2 - (y - yhat) ** 2`` — positive means the model
        beat the baseline on this one decision.

    Examples
    --------
    A model prediction closer to the outcome than the baseline::

        squared_error_improvement(y=3.0, yhat=2.8, mu=2.0)
        # -> 0.96
    """
    y, yhat, mu = float(y), float(yhat), float(mu)
    return (y - mu) ** 2 - (y - yhat) ** 2


def cluster_scores_by_day(rows) -> dict:
    """Group per-decision squared-error-improvement contributions by trading day — the cluster unit ADR-0114 §11 item 1 rules.

    Parameters
    ----------
    rows : list of mapping
        Each row carries ``day`` (str, the trading-day cluster key),
        ``y`` (float), ``yhat`` (float) and ``mu`` (float), for one lead
        and one HPO candidate.

    Returns
    -------
    dict
        ``{day: [contribution, ...]}``, ready for
        :func:`dskit.pipeline.stats.cluster_bootstrap_t`.

    Raises
    ------
    ValueError
        ``rows`` is empty, or a row is missing ``day``/``y``/``yhat``/
        ``mu``.

    Examples
    --------
    Two decisions on the same day::

        cluster_scores_by_day([
            {"day": "2026-01-05", "y": 1.0, "yhat": 0.9, "mu": 0.0},
            {"day": "2026-01-05", "y": -1.0, "yhat": -0.8, "mu": 0.0},
        ])
        # -> {"2026-01-05": [0.99, 0.96]}
    """
    if not rows:
        raise ValueError("cluster_scores_by_day: rows must be non-empty")
    out = {}
    for i, row in enumerate(rows):
        missing = [k for k in ("day", "y", "yhat", "mu") if k not in row]
        if missing:
            raise ValueError(
                f"cluster_scores_by_day: row {i} is missing {missing!r}"
            )
        out.setdefault(row["day"], []).append(
            squared_error_improvement(row["y"], row["yhat"], row["mu"])
        )
    return out


def boundary_flags(candidate, space=None) -> dict:
    """Report, per HPO dimension, whether ``candidate`` sits on a searched boundary (ADR-0114 §2).

    Parameters
    ----------
    candidate : mapping
        One candidate's overrides (:func:`hpo_space` keys -> a value
        drawn from that key's declared list).
    space : dict, optional
        The grid to check against (default :func:`hpo_space`).

    Returns
    -------
    dict
        ``{dimension: bool}`` — ``True`` when that dimension's value
        equals the declared grid's min or max.

    Examples
    --------
    A candidate at the smallest ``num_leaves`` and a mid ``learning_rate``::

        boundary_flags({
            "num_leaves": 4, "learning_rate": 0.01, "min_child_samples": 1000,
            "reg_lambda": 100.0, "reg_alpha": 0.1,
        })["num_leaves"]
        # -> True
    """
    space = hpo_space() if space is None else space
    return {
        name: candidate[name] in (min(values), max(values))
        for name, values in space.items()
    }


def simplicity_key(row) -> tuple:
    """Return the ADR-0114 §11 item 1 ruled simplicity key for one ledger row's candidate.

    ``(num_leaves, learning_rate, -min_child_samples, -reg_lambda,
    -reg_alpha)``, ascending (lowest wins). Fewer leaves is the primary,
    classic CART one-standard-error
    dimension; the three tie-breaks are negated so ascending order still
    reads "more conservative = simpler" throughout.

    Parameters
    ----------
    row : mapping
        A :class:`~dskit.pipeline.kinds_search.TrialLedger` row (its
        ``"overrides"`` carries the candidate).

    Returns
    -------
    tuple
        The five-element ordering key.

    Examples
    --------
    A row's own simplicity key::

        simplicity_key({"overrides": {
            "num_leaves": 8, "learning_rate": 0.01, "min_child_samples": 1000,
            "reg_lambda": 100.0, "reg_alpha": 0.1,
        }})
        # -> (8, 0.01, -1000, -100.0, -0.1)
    """
    overrides = row["overrides"]
    return (
        overrides["num_leaves"],
        overrides["learning_rate"],
        -overrides["min_child_samples"],
        -overrides["reg_lambda"],
        -overrides["reg_alpha"],
    )


def run_lead_selection(inventory, evaluate, *, n_boot, seed, alpha=0.05):
    """Score every candidate for one lead, independently, and select its frozen winner (ADR-0114 §2/§11.1).

    Builds one fresh :class:`~dskit.pipeline.kinds_search.TrialLedger`
    bound to ``inventory`` (so two leads never share ledger state), calls
    ``evaluate(candidate)`` once per candidate in the inventory's own
    canonical order, computes that candidate's score and standard error
    via :func:`dskit.pipeline.stats.cluster_bootstrap_t` over
    ``evaluate``'s returned ``cluster_scores`` (ADR-0114 §11 item 1: day
    is the cluster unit, the caller supplies per-day contributions), and
    selects the simplest candidate inside one standard error of the best
    via :class:`~dskit.pipeline.kinds_search.OneStandardErrorSelector`
    (``select="max"`` — this score is squared-error improvement, higher
    is better).

    Parameters
    ----------
    inventory : CandidateInventory
        The shared, frozen inventory (:func:`build_candidate_inventory`).
    evaluate : callable
        ``candidate -> mapping``, called once per candidate. The mapping
        must carry ``"cluster_scores"`` (the day -> contributions map for
        :func:`dskit.pipeline.stats.cluster_bootstrap_t`) and MAY carry
        any of :data:`EVIDENCE_FIELDS`' other names (``fit_seed``,
        ``cuts``, ``n_rows``, ``train_val_gap``,
        ``collapsed_prediction_variance``) — an omitted one is NOT
        defaulted here; it reaches ``TrialLedger.record`` unset and that
        ledger's own evidence contract refuses it by name (never
        re-derived in this module).
    n_boot : int
        Bootstrap replicates for :func:`cluster_bootstrap_t`.
    seed : int
        Base bootstrap seed for :func:`cluster_bootstrap_t`.
    alpha : float
        Bootstrap-t interval level (default 0.05).

    Returns
    -------
    tuple
        ``(TrialLedger, SelectionRecord)`` — the complete evidence ledger
        and this lead's frozen selection.

    Raises
    ------
    TypeError
        ``inventory`` is not an exact :class:`CandidateInventory`.
    ValueError
        ``evaluate`` returned something other than a mapping carrying
        ``"cluster_scores"``, or omitted a required evidence field (the
        ledger's own refusal).

    Examples
    --------
    Select over a one-candidate inventory with a canned evaluator::

        inventory = CandidateInventory(
            {"learning_rate": [0.01], "num_leaves": [8],
             "min_child_samples": [1000], "reg_lambda": [100.0],
             "reg_alpha": [0.1]},
        )
        def evaluate(candidate):
            return {
                "cluster_scores": {"2026-01-05": [0.5, 0.3]},
                "fit_seed": 0, "cuts": {"train_end": "2025-11-30"},
                "n_rows": 2, "train_val_gap": 0.01,
                "collapsed_prediction_variance": False,
            }
        ledger, selection = run_lead_selection(
            inventory, evaluate, n_boot=200, seed=0,
        )
        selection.selected_candidate["num_leaves"]
        # -> 8
    """
    if type(inventory) is not CandidateInventory:
        raise TypeError("run_lead_selection: inventory must be an exact CandidateInventory")
    ledger = TrialLedger(inventory, evidence_fields=EVIDENCE_FIELDS)
    for index, candidate in enumerate(inventory.combinations):
        overrides = dict(candidate)
        result = evaluate(overrides)
        if not isinstance(result, Mapping) or "cluster_scores" not in result:
            raise ValueError(
                f"run_lead_selection: evaluate({overrides!r}) must return a "
                "mapping carrying at least 'cluster_scores'"
            )
        diagnostics = cluster_bootstrap_t(
            result["cluster_scores"], n_boot, seed,
            label=f"candidate-{index}", alpha=alpha,
        )
        passthrough = {
            name: result[name]
            for name in ("fit_seed", "cuts", "n_rows", "train_val_gap",
                          "collapsed_prediction_variance")
            if name in result
        }
        ledger.record(
            overrides,
            score=diagnostics["mean"],
            se=diagnostics["se"],
            diagnostics=diagnostics,
            on_boundary=boundary_flags(overrides),
            **passthrough,
        )
    selection = OneStandardErrorSelector(
        select="max", simplicity_key=simplicity_key
    ).select(ledger)
    return ledger, selection


def refit_heads(
    rows_by_head,
    winners,
    *,
    feature_order,
    lean_drop,
    estimator_path=ESTIMATOR_PATH,
    seed=0,
    categorical_feature=(),
):
    """Refit all ten heads ONCE, each on only its own frozen winner, on permitted rows through 2026-02-28 — no search, no HPO (ADR-0114 §2).

    Every head shares ``feature_order``, ``lean_drop`` and
    ``categorical_feature`` (the identical feature/category contract);
    only each head's own ``winners[head]`` overrides and its own rows
    differ.

    Parameters
    ----------
    rows_by_head : dict
        ``head -> list of row``, keyed by EXACTLY :data:`HEADS`. Each row
        is a mapping carrying every name in ``feature_order``, a
        ``"label"`` (the fit target) and a ``"ts_ms"`` (epoch ms) this
        function validates against :func:`permitted_for_refit`.
    winners : dict
        ``head -> {param: value}``, keyed by EXACTLY :data:`HEADS` — that
        head's own frozen winner overrides (e.g. a
        ``SelectionRecord.selected_candidate``), forwarded verbatim to
        the wrapped estimator's constructor.
    feature_order : list of str
        The full candidate feature column order every head shares.
    lean_drop : list of str
        The exact P16 lean-mask drop list every head shares
        (:func:`lean_feature_drop`).
    estimator_path : str
        Dotted import path of the wrapped estimator class (default
        ``"lightgbm.LGBMRegressor"`` — this build's real recipe; a test
        double may pass any sklearn-shaped estimator instead).
    seed : int
        Forwarded as ``random_state`` unless a head's own ``winners``
        entry already names one.
    categorical_feature : list of int
        Indices into ``feature_order`` (before masking) naming native
        categorical columns, shared by every head.

    Returns
    -------
    tuple
        ``(estimators, training_identities)`` — ``head -> fitted
        ColumnSubsetEstimator`` and ``head -> JSON-safe identity dict``
        (``cut_ms``, ``seed``, ``n_rows``, ``winner``), the exact shape
        :func:`dskit.pipeline.libs.sklearn.write_bundle`'s
        ``estimators``/``training_identities`` arguments expect.

    Raises
    ------
    ValueError
        ``rows_by_head``/``winners`` are not keyed by exactly
        :data:`HEADS`, a head has zero rows, or any row carries a
        ``ts_ms`` outside the permitted pre-lockbox, post-embargo window.
    """
    import numpy as np

    if set(rows_by_head) != set(HEADS) or set(winners) != set(HEADS):
        raise ValueError(
            f"refit_heads requires rows_by_head and winners keyed by "
            f"exactly {list(HEADS)!r}; got rows "
            f"{sorted(rows_by_head)!r} and winners {sorted(winners)!r}"
        )
    feature_order = list(feature_order)
    categorical_feature = list(categorical_feature) if categorical_feature else None
    estimators = {}
    training_identities = {}
    for head in HEADS:
        rows = rows_by_head[head]
        if not rows:
            raise ValueError(f"refit_heads: head {head!r} has zero rows")
        matrix, targets, stamps = [], [], []
        for row in rows:
            ts_ms = row.get("ts_ms")
            if ts_ms is None or not permitted_for_refit(ts_ms):
                raise ValueError(
                    f"refit_heads: head {head!r} row carries ts_ms={ts_ms!r}, "
                    "outside the permitted pre-lockbox, post-embargo window "
                    f"(lockbox starts {LOCKBOX_START_MS}, embargo "
                    f"[{EMBARGO_START_MS}, {EMBARGO_END_MS}))"
                )
            matrix.append([row[name] for name in feature_order])
            targets.append(row["label"])
            stamps.append(ts_ms)

        params = dict(winners[head])
        params.setdefault("random_state", seed)
        estimator = ColumnSubsetEstimator(
            estimator_path, drop=list(lean_drop), **params
        )
        estimator.fit(
            np.array(matrix, dtype=float),
            np.array(targets, dtype=float),
            feature_names=feature_order,
            categorical_feature=categorical_feature,
        )
        estimators[head] = estimator
        training_identities[head] = {
            "cut_ms": max(stamps),
            "seed": seed,
            "n_rows": len(rows),
            "winner": dict(winners[head]),
        }
    return estimators, training_identities

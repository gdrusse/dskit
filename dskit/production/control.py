"""The durable command spool and the sole-writer processor (plan §5.8, D13, D15).

A control CLI runs in another process and shares nothing with the serve
loop but a filesystem, so §5.8 gives the two a spool: the CLI writes one
fsynced ``commands/inbox/<request_id>.json`` and is done; the process
holding ``serve.lock`` — the sole ledger writer — consumes it. Two objects
carry that, kept deliberately apart:

* :class:`ControlInbox` is the file side, testable alone and holding no
  ledger. ``queue`` creates the inbox file exclusively (``O_EXCL``) and
  fsyncs the file and its directory, so "success" means "durably
  queued"; ``pending`` lists what a writer must consume, in queue order;
  the two terminal verbs land a receipt in ``commands/applied`` or
  ``commands/rejected`` BEFORE the inbox entry is unlinked, and
  ``receipt`` reads one back. The caller's UUID ``request_id`` is reused
  only for a retry: the same id with the same command is idempotent (the
  queued path comes back and nothing is written twice), the same id with
  any difference refuses, and a legitimate repeat uses a new id.
* :class:`CommandProcessor` is the ledger side. Per command it appends the
  ``control_request`` record, dispatches to the handler injected for the
  command's purpose, appends the handler's records and the
  ``command_result``, crosses ONE ``ledger.barrier()`` and only then moves
  the file (D13: record before receipt). It owns no verb logic — the
  handler map IS the decision table, and the absence of a handler is the
  refusal, which is how a loop-less CLI refuses ``execute_flatten``
  without the processor knowing what a flatten is.

The proof bytes travel in the inbox file so the writer can re-verify
them; a record carries only their digest. The ``HALT`` sentinel is not the
spool's business: ``halt`` creates it before queueing its audit command,
and that ordering is ``__main__``'s.

Crash analysis, which is the whole point of the ordering: a crash before
the barrier leaves an inbox file with no receipt — the gap ``verify``
reports, re-consumed idempotently on restart because every record id is
kind-qualified by ``request_id`` (R9) and the ledger dedups on
``id + payload_digest``; a crash between the receipt and the unlink leaves
both, and ``pending`` skips any request that has a receipt. A receipt for
records that were never written is impossible.
"""

import base64
import hashlib
import json
import os
import re
import uuid

from dskit.onboarding.base import fsync_dir
from dskit.production import vocab
from dskit.production.base import (
    ProductionError,
    _check_dict,
    _check_str,
    _check_unknown,
    canonical_bytes,
    canonical_hash,
)
from dskit.production.redact import get_logger, redact

__all__ = ["EXECUTING_PURPOSES", "CommandProcessor", "ControlInbox"]

_log = get_logger("control")

#: The purposes that need a running, ready loop (§5.8): a synchronous
#: processor — a lock-taking CLI with no loop — is never given a handler
#: for one, and the missing handler is its refusal.
EXECUTING_PURPOSES = ("execute_flatten",)
if not set(EXECUTING_PURPOSES) <= set(vocab.CONTROL_PURPOSES):
    raise ProductionError(["control.py: EXECUTING_PURPOSES is not within vocab.CONTROL_PURPOSES"])

#: The two record kinds the processor writes, around the handler's own.
_REQUEST_KIND = "control_request"
_RESULT_KIND = "command_result"
if not {_REQUEST_KIND, _RESULT_KIND} <= set(vocab.RECORD_KINDS):
    raise ProductionError(["control.py: the control record kinds are not in vocab.RECORD_KINDS"])

#: The one status the processor answers on its own, and its pin.
_REJECTED = "rejected"
if _REJECTED not in vocab.COMMAND_STATUSES:
    raise ProductionError(["control.py: 'rejected' is not a vocab.COMMAND_STATUSES member"])

#: The six keys a caller supplies; ``queued_at_ms`` is the inbox's to stamp.
_CALLER_KEYS = ("request_id", "purpose", "payload", "payload_digest", "release_hash", "proof")
_QUEUED_AT = "queued_at_ms"
_STORED_KEYS = _CALLER_KEYS + (_QUEUED_AT,)
#: What a terminal verb adds to the stored command to make it a receipt.
_RECEIPT_KEYS = ("status", "reason", "emitted_record_ids")
_TERMINAL_KEYS = _STORED_KEYS + _RECEIPT_KEYS
#: The caller keys that must agree for a second ``queue`` to be a retry.
_COMPARED_KEYS = tuple(key for key in _CALLER_KEYS if key != "request_id")
#: The caller's three ledger keys (R1); a handler's record carries nothing else.
_LEDGER_KEYS = ("kind", "id", "body")
#: The payload key that bounds a command's own life, when the maker signs one.
_EXPIRES = "expires_ms"

#: Which ``ServeRoot`` queue a status lands in, and which inbox verb lands
#: it — tables over ``COMMAND_STATUSES``, never a status branch.
_QUEUES = {"applied": "commands_applied", "rejected": "commands_rejected"}
_MARKS = {"applied": "mark_applied", "rejected": "mark_rejected"}
if set(_QUEUES) != set(vocab.COMMAND_STATUSES) or set(_MARKS) != set(vocab.COMMAND_STATUSES):
    raise ProductionError(["control.py: the terminal tables do not cover vocab.COMMAND_STATUSES"])

_SUFFIX = ".json"
#: A receipt is staged under this suffix and renamed into place, so a
#: reader never sees a torn receipt and a crash leaves only garbage.
_STAGING = ".staging"
_DIGEST = re.compile(r"^[0-9a-f]{64}\Z")
_PERMISSIONS = 0o644


def _check_digest(problems, name, value):
    """Append a problem unless ``value`` is a lowercase hex sha256 digest."""
    if not isinstance(value, str) or not _DIGEST.match(value):
        problems.append(f"{name} must be a 64-hex sha256 digest, got {value!r}")


def _check_uuid(problems, name, value):
    """Append a problem unless ``value`` is a canonical UUID string."""
    ok = isinstance(value, str)
    if ok:
        try:
            ok = str(uuid.UUID(value)) == value
        except ValueError:
            ok = False
    if not ok:
        problems.append(f"{name} must be a canonical UUID string, got {value!r}")


def _check_instant(problems, name, value):
    """Append a problem unless ``value`` is an epoch-ms int (never a bool)."""
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{name} must be an epoch-ms int, got {value!r}")


def _check_ids(problems, name, value):
    """Append a problem unless ``value`` is a list of non-empty strings."""
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        problems.append(f"{name} must be a list of non-empty record ids, got {value!r}")


def _check_status(problems, name, value):
    """Append a problem unless ``value`` is a ``COMMAND_STATUSES`` member."""
    if value not in vocab.COMMAND_STATUSES:
        problems.append(f"{name} must be one of {list(vocab.COMMAND_STATUSES)}, got {value!r}")


def _check_receipt(problems, receipt, where):
    """Append every problem with a receipt's three fields."""
    _check_dict(problems, where, receipt)
    if not isinstance(receipt, dict):
        return
    _check_unknown(problems, receipt, _RECEIPT_KEYS, where=where)
    for key in _RECEIPT_KEYS:
        if key not in receipt:
            problems.append(f"{where}.{key} is required")
    _check_status(problems, f"{where}.status", receipt.get("status"))
    _check_str(problems, f"{where}.reason", receipt.get("reason"), non_empty=False)
    _check_ids(problems, f"{where}.emitted_record_ids", receipt.get("emitted_record_ids"))


def _check_record(problems, record, where):
    """Append every problem with a handler's ``{kind, id, body}`` record."""
    _check_dict(problems, where, record)
    if not isinstance(record, dict):
        return
    _check_unknown(problems, record, _LEDGER_KEYS, where=where)
    for key in _LEDGER_KEYS:
        if key not in record:
            problems.append(f"{where}.{key} is required")
    _check_str(problems, f"{where}.kind", record.get("kind"))
    _check_str(problems, f"{where}.id", record.get("id"))
    _check_dict(problems, f"{where}.body", record.get("body"))


class ControlInbox:
    """The durable spool between a control CLI and the serve process (§5.8).

    Five verbs and nothing that could append to a ledger: ``queue`` (the
    CLI's write), ``pending`` (the writer's read), ``mark_applied`` /
    ``mark_rejected`` (the writer's terminal moves) and ``receipt`` (what
    ``verify`` and a retrying CLI read back). The spool is the filesystem,
    not process memory — a second inbox over the same root sees the same
    commands.

    Parameters
    ----------
    serve_root : ServeRoot
        Owns the ``commands/`` layout; every path here is one of its
        accessors, never a concatenation.
    clock : Clock
        Stamps ``queued_at_ms`` — the only instant the inbox assigns, and
        a caller may not supply it.

    Attributes
    ----------
    serve_root : ServeRoot
        As given.

    Examples
    --------
    Queue a maker's arm request, consume it, and read the receipt::

        from dskit.production.base import canonical_hash
        from dskit.production.clock import WallClock
        from dskit.production.ledger import ServeRoot

        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        inbox = ControlInbox(serve, WallClock())
        payload = {"until_ms": 1_767_272_400_000, "allow": ["INS1"]}
        request_id = "00000000-0000-0000-0000-000000000001"
        path = inbox.queue({
            "request_id": request_id, "purpose": "arm_request", "payload": payload,
            "payload_digest": canonical_hash(payload), "release_hash": "b" * 64,
            "proof": b"signed-by-the-maker",
        })
        path == inbox.queue({...})  # True — a retry returns the queued path
        [c["purpose"] for c in inbox.pending()]  # ['arm_request']
        inbox.mark_rejected(request_id, {"status": "rejected", "reason": "unarmed",
                                         "emitted_record_ids": []})
        inbox.receipt(request_id)["status"]  # 'rejected'
        inbox.pending()  # ()
    """

    def __init__(self, serve_root, clock):
        self._serve_root = serve_root
        self._clock = clock

    @property
    def serve_root(self):
        """The ``ServeRoot`` whose ``commands/`` queues this inbox spools."""
        return self._serve_root

    # -- the CLI's write --------------------------------------------------------

    def queue(self, command):
        """Durably queue one command, or return where it already is.

        Parameters
        ----------
        command : dict
            Exactly ``request_id`` (a canonical UUID), ``purpose`` (a
            ``CONTROL_PURPOSES`` member), ``payload`` (a JSON-shaped dict,
            stored in its canonical JSON form), ``payload_digest`` (its
            ``canonical_hash``), ``release_hash`` (64 hex) and ``proof``
            (bytes; empty for an unauthenticated verb). ``queued_at_ms``
            is the inbox's to stamp and refuses when supplied.

        Returns
        -------
        str
            The path of the inbox file — or, for a retry of a command that
            already reached a terminal queue, the path of its receipt.

        Raises
        ------
        ProductionError
            Every shape problem at once; or a ``request_id`` already
            queued or terminal with a different purpose, payload,
            digest, release or proof — a repeat needs a new id.
        """
        stored = self._checked(command)
        request_id = stored["request_id"]
        terminal = self._terminal(request_id)
        if terminal is not None:
            path, receipt = terminal
            self._agree(stored, receipt, request_id)
            return path
        path = self._inbox_path(request_id)
        record = {**self._encode(stored), _QUEUED_AT: self._clock.now_ms()}
        try:
            self._create(path, record)
        except FileExistsError:
            self._agree(stored, self._read(path, request_id, _STORED_KEYS), request_id)
            return path
        fsync_dir(self._serve_root.commands_inbox)
        _log.info("queued control request %s (%s)", request_id, stored["purpose"])
        return path

    # -- the writer's read --------------------------------------------------------

    def pending(self):
        """Return every queued command that has no receipt, in queue order.

        Returns
        -------
        tuple of dict
            The seven stored keys per command, ``proof`` as the bytes
            that were queued; ordered by ``queued_at_ms`` then
            ``request_id``, so two writers replaying one spool agree.

        Raises
        ------
        ProductionError
            Naming an inbox entry that is not a ``<uuid>.json``, does not
            parse, disagrees with its file name, or carries a payload its
            digest does not cover — a dropped command is indistinguishable
            from one never sent, so nothing is skipped silently.
        """
        commands = []
        for name in sorted(os.listdir(self._serve_root.commands_inbox)):
            request_id = self._request_id_of(name)
            if self._terminal(request_id) is not None:
                continue
            commands.append(self._read(self._inbox_path(request_id), request_id, _STORED_KEYS))
        commands.sort(key=lambda command: (command[_QUEUED_AT], command["request_id"]))
        return tuple(commands)

    # -- the writer's terminal moves ---------------------------------------------

    def mark_applied(self, request_id, receipt):
        """Land the ``applied`` receipt, then retire the inbox entry.

        Parameters
        ----------
        request_id : str
            A queued, non-terminal request.
        receipt : dict
            Exactly ``status`` (``"applied"``), ``reason`` (str) and
            ``emitted_record_ids`` (list of str).

        Raises
        ------
        ProductionError
            If the request was never queued or is already terminal, or
            the receipt is malformed or does not say ``applied``.
        """
        self._mark(request_id, receipt, "applied")

    def mark_rejected(self, request_id, receipt):
        """Land the ``rejected`` receipt, then retire the inbox entry.

        Parameters
        ----------
        request_id : str
            A queued, non-terminal request.
        receipt : dict
            Exactly ``status`` (``"rejected"``), ``reason`` (str) and
            ``emitted_record_ids`` (list of str).

        Raises
        ------
        ProductionError
            If the request was never queued or is already terminal, or
            the receipt is malformed or does not say ``rejected``.
        """
        self._mark(request_id, receipt, "rejected")

    def receipt(self, request_id):
        """Return a request's terminal receipt, or None while it is pending or unknown.

        Parameters
        ----------
        request_id : str

        Returns
        -------
        dict or None
            The stored command (``proof`` as bytes) plus ``status``,
            ``reason`` and ``emitted_record_ids``.

        Raises
        ------
        ProductionError
            If the request has a receipt in BOTH terminal queues — two
            contradictory answers are not a state the spool resolves by
            preferring one directory — or a receipt does not parse.
        """
        terminal = self._terminal(request_id)
        return None if terminal is None else terminal[1]

    # -- validation ---------------------------------------------------------------

    def _checked(self, command):
        """Return the six caller fields of a well-formed command, payload canonicalised."""
        if not isinstance(command, dict):
            raise ProductionError(
                [f"a command is a dict of {list(_CALLER_KEYS)}, got {type(command).__name__}"]
            )
        problems = []
        _check_unknown(problems, command, _CALLER_KEYS, where="command")
        for key in _CALLER_KEYS:
            if key not in command:
                problems.append(f"command.{key} is required")
        _check_uuid(problems, "command.request_id", command.get("request_id"))
        if command.get("purpose") not in vocab.CONTROL_PURPOSES:
            problems.append(
                f"command.purpose must be one of {list(vocab.CONTROL_PURPOSES)}, "
                f"got {command.get('purpose')!r}"
            )
        payload = self._canonical_payload(problems, command.get("payload"))
        _check_digest(problems, "command.payload_digest", command.get("payload_digest"))
        if payload is not None and isinstance(command.get("payload_digest"), str):
            if command["payload_digest"] != canonical_hash(payload):
                problems.append("command.payload_digest does not match the payload")
        _check_digest(problems, "command.release_hash", command.get("release_hash"))
        if not isinstance(command.get("proof"), bytes):
            problems.append(f"command.proof must be bytes, got {command.get('proof')!r}")
        if problems:
            raise ProductionError(problems)
        return {**{key: command[key] for key in _CALLER_KEYS}, "payload": payload}

    @staticmethod
    def _canonical_payload(problems, payload):
        """Return ``payload`` in canonical JSON form, or None after appending why not."""
        _check_dict(problems, "command.payload", payload)
        if not isinstance(payload, dict):
            return None
        try:
            return json.loads(canonical_bytes(payload))
        except ProductionError as exc:
            problems.extend(f"command.payload: {problem}" for problem in exc.problems)
            return None

    def _agree(self, stored, existing, request_id):
        """Refuse unless ``existing`` is the same command as ``stored`` (a retry)."""
        differing = [key for key in _COMPARED_KEYS if existing[key] != stored[key]]
        if differing:
            raise ProductionError(
                [
                    f"request {request_id} is already queued with a different command "
                    f"({', '.join(differing)} differ) — a repeated command needs a new "
                    f"request_id; the same id is reused only for a retry"
                ]
            )

    # -- files ----------------------------------------------------------------------

    def _inbox_path(self, request_id):
        return os.path.join(self._serve_root.commands_inbox, request_id + _SUFFIX)

    def _terminal_path(self, request_id, status):
        directory = getattr(self._serve_root, _QUEUES[status])
        return os.path.join(directory, request_id + _SUFFIX)

    @staticmethod
    def _request_id_of(name):
        """Return the request id an inbox entry is named by, refusing a stray file."""
        problems = []
        request_id = name[: -len(_SUFFIX)] if name.endswith(_SUFFIX) else None
        _check_uuid(problems, f"commands/inbox/{name}", request_id)
        if problems:
            raise ProductionError([f"{problems[0]} — not a queued command; remove it"])
        return request_id

    def _terminal(self, request_id):
        """Return ``(path, receipt)`` for a terminal request, None for a pending or unknown one."""
        found = [
            (status, self._terminal_path(request_id, status))
            for status in vocab.COMMAND_STATUSES
            if os.path.exists(self._terminal_path(request_id, status))
        ]
        if len(found) > 1:
            raise ProductionError(
                [
                    f"request {request_id} has a receipt in every terminal queue "
                    f"({', '.join(status for status, _path in found)}) — contradictory "
                    f"receipts need an operator, not a preference"
                ]
            )
        if not found:
            return None
        _status, path = found[0]
        return path, self._read(path, request_id, _TERMINAL_KEYS)

    @staticmethod
    def _create(path, obj):
        """Create ``path`` exclusively with ``obj`` as JSON and fsync it; exists refuses."""
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PERMISSIONS)
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def _read(self, path, request_id, keys):
        """Return the decoded command (proof as bytes) a spool file holds, refusing a bad one."""
        problems = []
        try:
            with open(path, encoding="utf-8") as fh:
                stored = json.load(fh)
        except ValueError as exc:
            raise ProductionError([f"{path}: request {request_id} is not JSON ({exc})"]) from exc
        _check_dict(problems, f"request {request_id}", stored)
        if problems:
            raise ProductionError(problems)
        _check_unknown(problems, stored, keys, where=f"request {request_id}")
        for key in keys:
            if key not in stored:
                problems.append(f"request {request_id}: {key} is missing")
        if problems:
            raise ProductionError(problems)
        if stored["request_id"] != request_id:
            problems.append(
                f"request {request_id}: the file names request_id {stored['request_id']!r}"
            )
        if stored["purpose"] not in vocab.CONTROL_PURPOSES:
            problems.append(f"request {request_id}: purpose {stored['purpose']!r} is unknown")
        _check_dict(problems, f"request {request_id}: payload", stored["payload"])
        _check_digest(problems, f"request {request_id}: payload_digest", stored["payload_digest"])
        if not problems and stored["payload_digest"] != canonical_hash(stored["payload"]):
            problems.append(f"request {request_id}: payload_digest does not cover the payload")
        _check_digest(problems, f"request {request_id}: release_hash", stored["release_hash"])
        _check_instant(problems, f"request {request_id}: {_QUEUED_AT}", stored[_QUEUED_AT])
        proof = self._decode_proof(problems, request_id, stored["proof"])
        if keys is _TERMINAL_KEYS:
            _check_receipt(
                problems, {key: stored[key] for key in _RECEIPT_KEYS}, f"request {request_id}"
            )
        if problems:
            raise ProductionError(problems)
        return {**stored, "proof": proof}

    @staticmethod
    def _encode(stored):
        """Return ``stored`` with its proof base64-encoded, JSON-ready."""
        return {**stored, "proof": base64.b64encode(stored["proof"]).decode("ascii")}

    @staticmethod
    def _decode_proof(problems, request_id, text):
        """Return the proof bytes a stored file carries, or None after appending why not."""
        try:
            return base64.b64decode(text, validate=True)
        except (TypeError, ValueError):
            problems.append(f"request {request_id}: proof is not base64")
            return None

    def _mark(self, request_id, receipt, status):
        """Stage the receipt, rename it into ``status``'s queue, then unlink the inbox entry."""
        problems = []
        _check_uuid(problems, "request_id", request_id)
        _check_receipt(problems, receipt, "receipt")
        if isinstance(receipt, dict) and receipt.get("status") != status:
            problems.append(f"receipt.status must be {status!r} here, got {receipt.get('status')!r}")
        if problems:
            raise ProductionError(problems)
        if self._terminal(request_id) is not None:
            raise ProductionError([f"request {request_id} is already terminal"])
        inbox_path = self._inbox_path(request_id)
        if not os.path.exists(inbox_path):
            raise ProductionError([f"request {request_id} was never queued"])
        stored = self._read(inbox_path, request_id, _STORED_KEYS)
        record = {**self._encode(stored), **{key: receipt[key] for key in _RECEIPT_KEYS}}
        final = self._terminal_path(request_id, status)
        staged = final + _STAGING
        if os.path.exists(staged):
            os.unlink(staged)
        self._create(staged, record)
        os.replace(staged, final)
        fsync_dir(os.path.dirname(final))
        os.unlink(inbox_path)
        fsync_dir(self._serve_root.commands_inbox)
        _log.info("control request %s %s: %s", request_id, status, redact(receipt["reason"]))


class CommandProcessor:
    """The sole-writer consumer of the spool (§5.8, D13, D15).

    For each pending command, in queue order: append the
    ``control_request`` (``principal_digest`` null — verifying a principal
    is the handler's work, and the record is on the chain before the verb
    runs; ``proof_digest`` is the sha256 of the proof bytes, which never
    reach a record), dispatch to ``handlers[purpose]``, append the records
    it returns, append the ``command_result`` naming them, ``barrier()``
    once, and only then move the inbox file. A handler that raises
    ``ProductionError`` rejects its command and appends nothing of its
    own; a missing handler rejects it too. Anything else a handler does
    wrong — a status outside ``COMMAND_STATUSES``, a malformed record — is
    a contract violation and raises.

    Parameters
    ----------
    inbox : ControlInbox
        The spool being consumed.
    ledger : Ledger
        The chain the process holding ``serve.lock`` writes; it folds
        into ``state``.
    state : SeriesState
        The fold. The first command of a pass sees the ``view`` the loop
        gated on; each later command sees a fresh ``state.snapshot()``,
        because the previous command's records have folded and only a
        fresh fold carries them (§5.13 step 2's reasoning, applied here).
    handlers : dict of str to callable
        ``purpose -> handler(command, view) -> (records, status, reason)``
        — the whole decision table. ``records`` are ``{kind, id, body}``
        dicts the processor appends; ``status`` is a ``COMMAND_STATUSES``
        member; ``reason`` a string. A key outside ``CONTROL_PURPOSES``
        refuses at construction.
    clock : Clock
        Judges a payload's own ``expires_ms`` (the one verb-neutral
        expiry): a command whose maker-signed payload carries an int
        ``expires_ms`` already past is rejected before dispatch.

    Examples
    --------
    A processor with one verb, run once over the spool::

        def halt(command, view):
            return ((), "applied", "")

        processor = CommandProcessor(inbox, ledger, state, {"halt": halt}, WallClock())
        results = processor.process_pending(state.snapshot())
        [r["status"] for r in results]  # ['applied']
        processor.process_pending(state.snapshot())  # () — the spool is consumed
    """

    def __init__(self, inbox, ledger, state, handlers, clock):
        problems = []
        _check_dict(problems, "handlers", handlers)
        if isinstance(handlers, dict):
            _check_unknown(problems, handlers, vocab.CONTROL_PURPOSES, where="handlers")
            for purpose, handler in handlers.items():
                if not callable(handler):
                    problems.append(f"handlers[{purpose!r}] is not callable: {handler!r}")
        if problems:
            raise ProductionError(problems)
        self._inbox = inbox
        self._ledger = ledger
        self._state = state
        self._handlers = dict(handlers)
        self._clock = clock

    def process_pending(self, view):
        """Consume every pending command, in queue order.

        Parameters
        ----------
        view : StateView
            The fold the loop gated on; handed to the first command's
            handler. Later commands receive a fresh ``state.snapshot()``.

        Returns
        -------
        tuple of dict
            One ``command_result`` body per command, in the order they
            were consumed.

        Raises
        ------
        ProductionError
            On a malformed spool entry or a handler that violates its
            contract. A failed barrier propagates as the ``OSError`` it
            is, leaving the command pending with no receipt.
        """
        results = []
        current = view
        for command in self._inbox.pending():
            results.append(self._process(command, current))
            current = self._state.snapshot()
        return tuple(results)

    def _process(self, command, view):
        """Run one command through request → handler records → result → barrier → move."""
        request_id = command["request_id"]
        self._ledger.append(self._request_record(command))
        records, status, reason = self._dispatch(command, view)
        for record in records:
            self._ledger.append(record)
        body = {
            "request_id": request_id,
            "status": status,
            "reason": reason,
            "emitted_record_ids": [record["id"] for record in records],
        }
        self._ledger.append({"kind": _RESULT_KIND, "id": f"{_RESULT_KIND}:{request_id}", "body": body})
        self._ledger.barrier()
        receipt = {key: body[key] for key in _RECEIPT_KEYS}
        getattr(self._inbox, _MARKS[status])(request_id, receipt)
        _log.info("control request %s (%s) %s", request_id, command["purpose"], status)
        return body

    def _request_record(self, command):
        """Build the §6 ``control_request`` record: the request, its bindings, its digests."""
        request_id = command["request_id"]
        expires = command["payload"].get(_EXPIRES)
        return {
            "kind": _REQUEST_KIND,
            "id": f"{_REQUEST_KIND}:{request_id}",
            "body": {
                "request_id": request_id,
                "purpose": command["purpose"],
                "payload": command["payload"],
                "release_hash": command["release_hash"],
                "principal_digest": None,
                "proof_digest": hashlib.sha256(command["proof"]).hexdigest(),
                "expires_ms": expires if isinstance(expires, int) and not isinstance(expires, bool) else None,
            },
        }

    def _dispatch(self, command, view):
        """Return ``(records, status, reason)`` for one command from its handler, or a rejection."""
        purpose = command["purpose"]
        reason = self._expiry_problem(command)
        if reason is not None:
            return (), _REJECTED, reason
        handler = self._handlers.get(purpose)
        if handler is None:
            return (), _REJECTED, f"no handler for purpose {purpose!r} in this process"
        try:
            answer = handler(command, view)
        except ProductionError as exc:
            return (), _REJECTED, redact(str(exc))
        return self._checked_answer(answer, purpose)

    def _expiry_problem(self, command):
        """Return why the command's own ``expires_ms`` refuses it, or None."""
        declared = command["payload"].get(_EXPIRES)
        if declared is None:
            return None
        problems = []
        _check_instant(problems, f"payload.{_EXPIRES}", declared)
        if problems:
            return problems[0]
        now = self._clock.now_ms()
        if now > declared:
            return f"request {command['request_id']} expired at {declared} (now {now})"
        return None

    @staticmethod
    def _checked_answer(answer, purpose):
        """Return a handler's ``(records, status, reason)`` checked against its contract."""
        if isinstance(answer, (str, bytes)) or not isinstance(answer, (list, tuple)) or len(answer) != 3:
            raise ProductionError(
                [f"handler for {purpose!r} must return (records, status, reason), got {answer!r}"]
            )
        records, status, reason = answer
        problems = []
        _check_status(problems, f"handler for {purpose!r}: status", status)
        _check_str(problems, f"handler for {purpose!r}: reason", reason, non_empty=False)
        if isinstance(records, (str, bytes, dict)) or not isinstance(records, (list, tuple)):
            problems.append(f"handler for {purpose!r}: records must be a sequence, got {records!r}")
            records = ()
        for position, record in enumerate(records):
            _check_record(problems, record, f"handler for {purpose!r}: records[{position}]")
        if problems:
            raise ProductionError(problems)
        return tuple(records), status, redact(reason)

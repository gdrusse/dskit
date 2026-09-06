"""``libs/sqlite.py`` — the chain kept in one database file (§5.8.2).

The second :class:`~dskit.production.ledger.Ledger`, and the reason the
seam is an ABC. It is a pack rather than core because ``sqlite3`` ships
with Python but the SCHEMA and its pragmas are a library-shaped choice, and
§8's tier rule puts a named library's plumbing in ``libs/``: the module
names ``sqlite3`` only inside a method, so importing the production layer
never opens a database engine a serve document did not ask for.

Everything that makes a chain a chain — the twelve-field envelope, the
caller-content ``payload_digest`` and the idempotency it buys, the dense
``seq``, the ``prev_hash`` link, the graded barrier, the ``serve.lock``
writer lock, the snapshot cadence and the walk ``verify`` performs — is
inherited from :class:`~dskit.production.ledger.ChainLedger` rather than
written again here. Two implementations of one hash chain would be two
chains, and the conformance suite that folds the same records through both
stores exists to prove there is only one.

Three things are this store's own, and each is a refusal:

* **The pragmas are pinned.** ``journal_mode=WAL`` and
  ``synchronous=FULL`` are set on every connection and are reachable by no
  document key. ``document.durability.fsync`` grades the BARRIER cadence —
  how often the writer COMMITS — and never the pragma, because a chain
  whose durability can be lowered by a config key is not a chain.
  ``fsync: "none"`` therefore still means "commit lazily", not "lose the
  write", and stays legal only at ``shadow`` for the same reason it is in
  ``JsonlLedger``.
* **Append-only is enforced by the STORE.** Three triggers refuse an
  ``UPDATE``, a ``DELETE``, and an ``INSERT`` that does not extend the
  chain at its tail with the head's hash. JSONL gets that property from
  ``O_APPEND``; a table would otherwise have it from nothing but the good
  manners of whoever holds a connection, and a chain guarded only by its
  own writer is guarded by nobody.
* **``rotate`` refuses.** A sqlite chain is one file, and
  ``document.placement.rotate`` names a JSONL segmentation policy, so a
  document that selects this store while declaring a rotation refuses at
  ``plan`` rather than having a knob its author believed in quietly
  ignored.

Reads borrow the writer's connection while it is open, so a ``scan``
between appends sees the records already landed even under a lazy grade —
exactly what a reader of a JSONL segment sees before its fsync. After
``close()`` reads open a connection of their own, which is what keeps the
seam's promise that a stopped process can still be read.
"""

import json

from dskit.production.base import GENESIS_HASH
from dskit.production.ledger import LEDGER_KINDS, ChainLedger

__all__ = ["SqliteLedger"]

#: The one table §5.8.2 names, and the columns it names it with. ``seq`` is
#: ``INTEGER PRIMARY KEY``, so it IS the rowid and an ordered walk is an
#: index walk; ``id UNIQUE`` makes §6's "unique across the SERIES" the
#: store's promise rather than the writer's memory.
_TABLE = """
CREATE TABLE IF NOT EXISTS records (
    seq INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    id TEXT NOT NULL UNIQUE,
    envelope TEXT NOT NULL,
    hash TEXT NOT NULL,
    prev_hash TEXT NOT NULL
)
"""

#: ``scan(kind, since_seq)`` is one indexed query in both of its shapes:
#: by ``seq`` alone it walks the primary key, and by kind it walks this.
_INDEX = "CREATE INDEX IF NOT EXISTS records_by_kind ON records (kind, seq)"

#: What the store says when it refuses. The word the three refusals share
#: is what a caller reads in the exception.
_REFUSAL = "records is append-only"

#: The three triggers. The first two forbid rewriting history at all; the
#: third forbids writing it anywhere but the end, and only in a way that
#: continues the chain the file already holds — so a row inserted by
#: anything other than a ledger extending its own head is refused by the
#: file, not by the process.
_TRIGGERS = (
    f"""
CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
BEGIN SELECT RAISE(ABORT, '{_REFUSAL}: a record cannot be updated'); END
""",
    f"""
CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
BEGIN SELECT RAISE(ABORT, '{_REFUSAL}: a record cannot be deleted'); END
""",
    f"""
CREATE TRIGGER IF NOT EXISTS records_extends_the_chain BEFORE INSERT ON records
WHEN NEW.seq <> (SELECT IFNULL(MAX(seq), 0) + 1 FROM records)
  OR NEW.prev_hash <> IFNULL(
        (SELECT hash FROM records ORDER BY seq DESC LIMIT 1), '{GENESIS_HASH}')
BEGIN SELECT RAISE(ABORT, '{_REFUSAL}: a record lands at the tail, chained to the head'); END
""",
)

_INSERT = (
    "INSERT INTO records (seq, kind, id, envelope, hash, prev_hash) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_WALK = "SELECT envelope FROM records ORDER BY seq"
_SCAN = "SELECT envelope FROM records WHERE seq > ? ORDER BY seq"
_SCAN_KIND = "SELECT envelope FROM records WHERE seq > ? AND kind = ? ORDER BY seq"


class SqliteLedger(ChainLedger):
    """The serve chain in one database file, append-only by the store's own rules.

    Registered as ``sqlite`` in ``ledger.LEDGER_KINDS`` (§4.3: import is
    registration) and taking the constructor ``JsonlLedger`` takes, so
    ``compose.py`` builds either from one site and nothing downstream
    learns which it got. Opening takes ``serve.lock``, creates the table,
    its index and its three triggers when they are absent, and walks the
    rows once to recover the head, the idempotency index and the snapshot
    cadence.

    A commit is atomic, so this store has no torn tail to recover: a crash
    loses whole records rather than half a line, and what comes back is a
    shorter chain that still verifies.

    Parameters
    ----------
    serve_root : ServeRoot
        The layout; the chain lives at its ``database_path``.
    process_id : str
        The writing process's id, stamped on every envelope.
    release_hash : str
        The writing process's release, stamped on every envelope.
    clock : Clock
        Injected, as for every store.
    fsync : str or dict
        The BARRIER cadence — how often the writer commits. ``"every"``
        (default), ``"none"`` or ``{"batch": {"n", "ms"}}``. It never
        reaches a pragma.
    rotate : dict or None
        Must be ``None``: a chain in one file has nothing to segment.
    state : SeriesState or None
        The fold, handed every envelope exactly as it was written.
    snapshot_every : int or None
        The auto-snapshot cadence; ``None`` never snapshots.
    lock : health.InstanceLock or None
        A HELD instance lock on this series' ``serve.lock`` (R18).

    Raises
    ------
    ProductionError
        As ``ChainLedger``, plus a declared ``placement.rotate``.

    Examples
    --------
    The same three lines a JSONL chain takes, against a database::

        from dskit.production.clock import WallClock
        from dskit.production.ledger import ServeRoot

        serve = ServeRoot("./serve", "018f0f4e-7b21-7d3a-9c31-6d8f36d806a1")
        ledger = SqliteLedger(serve, "proc-1", "a" * 64, clock=WallClock())
        ledger.append({"kind": "tick_start", "id": "tick_start:t-1",
                       "body": {"tick_id": "t-1", "tick_at_ms": 0}})  # 1
        ledger.barrier()
        ledger.head()  # (1, '<sha256 hex>')
        ledger.close()
    """

    @classmethod
    def check_placement(cls, problems, rotate):
        """Refuse any rotation: this chain is one file.

        Parameters
        ----------
        problems : list of str
            The accumulator. Appended to in place.
        rotate : dict or None
            The document's ``placement.rotate``.

        Returns
        -------
        None
            A problem is appended when a rotation is declared at all.
        """
        if rotate is not None:
            problems.append(
                "placement.rotate names a segmentation policy, and this chain is "
                f"one file: {rotate!r} has nothing to rotate. Drop the key, or "
                "keep the chain in segments (§5.8.2)"
            )

    # -- open / close -------------------------------------------------------

    def _connect(self):
        """Open one connection with the two pinned pragmas and no implicit transaction."""
        import sqlite3

        connection = sqlite3.connect(self._root.database_path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _open(self):
        """Create the schema when it is absent, then recover the head from the rows."""
        self._connection = self._connect()
        self._connection.execute(_TABLE)
        self._connection.execute(_INDEX)
        for trigger in _TRIGGERS:
            self._connection.execute(trigger)
        self._recover()

    def _recover(self):
        """Walk the rows once: head, idempotency index and snapshot cadence."""
        for envelope in self._walk():
            if envelope is None:
                continue
            self._seq, self._head = envelope["seq"], envelope["hash"]
            self._index[envelope["id"]] = (envelope["payload_digest"], self._seq)
            self._note_kind(envelope["kind"], self._seq)

    def _shutdown(self):
        """Commit whatever a lazy grade was still holding, then close the connection."""
        if self._connection is None:
            return
        try:
            self._sync()
        finally:
            self._connection.close()
            self._connection = None

    # -- the write path -----------------------------------------------------

    def _store(self, envelope, line, at_ms):
        """Insert one row inside a transaction the barrier will commit."""
        if not self._connection.in_transaction:
            self._connection.execute("BEGIN IMMEDIATE")
        self._connection.execute(
            _INSERT,
            (
                envelope["seq"],
                envelope["kind"],
                envelope["id"],
                line.decode("utf-8"),
                envelope["hash"],
                envelope["prev_hash"],
            ),
        )

    def _sync(self):
        """Commit — which under ``synchronous=FULL`` in WAL is the platter."""
        if self._connection is not None and self._connection.in_transaction:
            self._connection.execute("COMMIT")

    # -- the read path ------------------------------------------------------

    def _query(self, statement, params):
        """Yield rows on the writer's connection, or on one of this read's own.

        While the ledger is open a read borrows the writer's connection, so
        it sees records that have landed but not yet been committed — what
        a reader of a JSONL segment sees before its fsync. After ``close()``
        there is no writer, everything is committed, and the read opens and
        closes a connection of its own.
        """
        borrowed = self._connection is not None
        connection = self._connection if borrowed else self._connect()
        try:
            yield from connection.execute(statement, params)
        finally:
            if not borrowed:
                connection.close()

    def _walk(self):
        """Yield every stored envelope in ``seq`` order, ``None`` for a damaged row."""
        for (raw,) in self._query(_WALK, ()):
            yield self._record_envelope(raw)

    def scan(self, kind=None, since_seq=0):
        """Yield envelopes in ``seq`` order (see :meth:`Ledger.scan`); one indexed query.

        Parameters
        ----------
        kind : str or None
            Keep only this record kind; ``None`` keeps every kind.
        since_seq : int
            EXCLUSIVE lower bound, so a snapshot's ``at_seq`` replays
            forward without repeating what the snapshot already holds.

        Returns
        -------
        iterator of dict
            The full twelve-field envelopes.
        """
        wanted = kind
        statement, params = (
            (_SCAN, (since_seq,))
            if wanted is None
            else (_SCAN_KIND, (since_seq, wanted))
        )
        for (raw,) in self._query(statement, params):
            yield json.loads(raw)


LEDGER_KINDS.register("sqlite", SqliteLedger)

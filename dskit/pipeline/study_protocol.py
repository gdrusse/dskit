"""Durable, domain-independent research packet release and evidence binding.

The registry coordinates cooperative local researchers. It cannot prevent
manual file reads or hostile local code. Initialization explicitly records
prior exposure; missing or differently initialized registries refuse.
"""

import copy
import importlib.metadata
import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from .base import import_ref, value_hash
from .driver import RunAttestation, resolve_json_artifact
from .node import JsonArtifact, Node, reject_unknown_params
from .workflow import path_hash

__all__ = ["StudyNode", "StudyContract", "ExposureRegistry", "StudyProtocolPlan",
           "StudyProtocolControl", "StudyPacketOpen", "StudyPacketClose",
           "StudyPacketResults"]


def _day(value):
    """Refuse ambiguous dates before lexical comparisons."""
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("canonical ISO date required")
    return value


def _seal(value):
    """Bind one JSON receipt with the framework's canonical hash."""
    return {**value, "sha256": value_hash(value)}


def _unseal(receipt):
    """Validate a receipt independently of its producer."""
    body = {k: v for k, v in receipt.items() if k != "sha256"}
    if receipt.get("sha256") != value_hash(body):
        raise ValueError("receipt digest mismatch")
    return body


class StudyContract(ABC):
    """Domain hook for metadata resolution and completed-result validation.

    Parameters
    ----------
    None

    Examples
    --------
    A child subclass implements resolve and validate_result and declares its
    import path in study.contract. Core never interprets observation units.
    """

    @abstractmethod
    def resolve(self, study):
        """Return missing, read_bounds, cohort and source_pins from metadata."""

    @abstractmethod
    def validate_result(self, study, results, audit, provenance):
        """Refuse a result that cannot close the entire declared cohort."""

    @classmethod
    def validate_amendment(cls, old_study, new_study):
        """Domain owners must explicitly constrain changed economics on a replay."""
        raise ValueError("this study contract does not allow amended replay")

    @classmethod
    def validate_implementation(cls, pins, modules):
        """Bind complete package source pins to the actually imported modules."""
        roots, expected = set(), set()
        for module in modules:
            if isinstance(module, type):
                module = importlib.import_module(module.__module__)
            source = Path(module.__file__).resolve()
            root = source.parent
            if not (root / "__init__.py").is_file():
                expected.add(str(source))
                continue
            while (root.parent / "__init__.py").is_file():
                root = root.parent
            roots.add(root)
        for root in roots:
            expected.update(str(path.resolve()) for path in root.rglob("*.py"))
        if set(pins) != expected:
            raise ValueError("implementation pins must cover the imported package source")
        for path, digest in pins.items():
            if Path(path).resolve().as_posix() != Path(path).as_posix() or path_hash(path) != digest:
                raise ValueError("code pin drift: " + path)

    @staticmethod
    def re_exposed(packet, earlier):
        """Return the id of the earlier non-development packet this development packet repeats.

        A re-exposure repeats one window exactly (equal start and end_before);
        any partial overlap stays an overlap. None when there is no such twin.
        """
        if packet["purpose"] != "development":
            return None
        return next((p["id"] for p in earlier
                     if p["purpose"] != "development" and p["start"] == packet["start"]
                     and p["end_before"] == packet["end_before"]), None)

    @classmethod
    def build(cls, study):
        """Resolve and hash a fully declared study without reading payload values.

        Parameters
        ----------
        study : dict
            Explicit contract, registry, schedule, selected packet, domain
            configuration and code/dependency content pins.

        Returns
        -------
        dict
            Stable plan identity and missing-input list.

        Raises
        ------
        ValueError
            Any supplied identity, date, code or dependency pin is invalid.
        """
        required = {"contract", "registry", "packets", "selected_packet",
                    "domain", "code", "dependencies"}
        if set(study)-{"notes"} != required:
            raise ValueError("study requires exactly the executable schema keys")
        hook = import_ref(study["contract"])
        if not isinstance(hook, type) or not issubclass(hook, cls):
            raise ValueError("study.contract must subclass StudyContract")
        schedule = study["packets"]
        if not isinstance(schedule, list) or not schedule:
            raise ValueError("nonempty packet schedule required")
        ids, previous = [], None
        for packet in schedule:
            if set(packet) != {"id", "start", "end_before", "purpose", "predecessor"}:
                raise ValueError("invalid packet schema")
            if (not isinstance(packet["id"], str) or not packet["id"] or packet["id"] in ids
                    or packet["purpose"] not in {"development", "adaptation", "confirmation"}
                    or packet["predecessor"] != previous
                    or not _day(packet["start"]) < _day(packet["end_before"])):
                raise ValueError("invalid packet order or identity")
            twin = cls.re_exposed(packet, schedule[:len(ids)])
            for prior_packet in schedule[:len(ids)]:
                if (prior_packet["id"] != twin
                        and packet["start"] < prior_packet["end_before"]
                        and prior_packet["start"] < packet["end_before"]):
                    raise ValueError("overlapping packet entries")
                if (packet["purpose"] != "development"
                        and prior_packet["purpose"] != "development"
                        and packet["start"] < prior_packet["start"]):
                    raise ValueError("non-development packets must be chronological")
            ids.append(packet["id"])
            previous = packet["id"]
        if study["selected_packet"] not in ids:
            raise ValueError("selected packet absent")
        missing = []
        if not study["code"]:
            missing.append("code")
        if study["code"]:
            cls.validate_implementation(study["code"], (cls, hook))
        if not study["dependencies"]:
            missing.append("dependencies")
        for package, version in study["dependencies"].items():
            if importlib.metadata.version(package) != version:
                raise ValueError("dependency pin drift: "+package)
        resolved = hook().resolve(study)
        if set(resolved) != {"missing", "read_bounds", "cohort", "source_pins"}:
            raise ValueError("invalid domain plan")
        missing.extend(resolved["missing"])
        packet = schedule[ids.index(study["selected_packet"])]
        for bounds in resolved["read_bounds"].values():
            if (set(bounds) != {"start", "end_before"}
                    or not _day(bounds["start"]) < _day(bounds["end_before"])):
                raise ValueError("invalid source read bounds")
        cohort = resolved["cohort"]
        if not cohort or len(set(cohort)) != len(cohort):
            missing.append("unique nonempty cohort")
        return _seal({"study": copy.deepcopy(study), "packet": packet,
                      "cohort_sha256": value_hash(cohort),
                      **resolved, "missing": missing, "runnable": not missing})

    @staticmethod
    def verify_plan(study, plan):
        """Rebuild a supplied plan and refuse identity drift or unresolved inputs."""
        _unseal(plan)
        current = StudyContract.build(study)
        if current != plan or not current["runnable"]:
            raise ValueError("unbound or changed frozen plan")
        return current


class ExposureRegistry:
    """Transactional shared evidence-domain registry with monotone exposure.

    Parameters
    ----------
    config : dict
        path, id and domain. The UUID is chosen once during explicit init.

    Examples
    --------
    registry = ExposureRegistry(study["registry"])
    receipt = registry.freeze(StudyContract.build(study))
    """

    def __init__(self, config):
        if (set(config) != {"path", "id", "domain"}
                or any(not isinstance(v, str) or not v for v in config.values())
                or not Path(config["path"]).is_absolute()):
            raise ValueError("explicit absolute shared registry and identity required")
        uuid.UUID(config["id"])
        self.config = copy.deepcopy(config)
        self.path = Path(config["path"])

    @contextmanager
    def _transaction(self, *, create=False):
        """Serialize transitions and validate the entire event hash chain."""
        if not create and not self.path.is_file():
            raise ValueError("missing registry; explicit historical import required")
        connection = sqlite3.connect(str(self.path), timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            if create:
                connection.execute("CREATE TABLE state (body TEXT NOT NULL)")
                connection.execute("CREATE TABLE events (revision INTEGER PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL)")
                state = None
            else:
                row = connection.execute("SELECT body FROM state").fetchone()
                if row is None:
                    raise ValueError("empty/reset registry")
                state = json.loads(row[0])
                if state["identity"] != self.config:
                    raise ValueError("registry identity/domain mismatch")
                previous, count = None, 0
                for revision, raw, digest in connection.execute("SELECT * FROM events ORDER BY revision"):
                    event = json.loads(raw)
                    if revision != count or event["previous"] != previous or value_hash(event) != digest:
                        raise ValueError("malformed registry history")
                    previous, count = digest, count+1
                if count != state["revision"]+1 or previous != state["event"]:
                    raise ValueError("registry revision/history mismatch")
                if event["state_sha256"] != value_hash({k: v for k, v in state.items() if k != "event"}):
                    raise ValueError("registry state drift")
            yield connection, state
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _save(connection, state, action):
        """Append state evidence and atomically publish its new revision."""
        previous = state.pop("event", None)
        state["revision"] += 1
        event = {"action": action, "revision": state["revision"], "previous": previous,
                 "state_sha256": value_hash(state)}
        digest = value_hash(event)
        connection.execute("INSERT INTO events VALUES (?, ?, ?)",
                           (state["revision"], json.dumps(event, sort_keys=True), digest))
        state["event"] = digest
        connection.execute("DELETE FROM state")
        connection.execute("INSERT INTO state VALUES (?)", (json.dumps(state, sort_keys=True),))

    def initialize(self, packets, prior_exposure, end_before):
        """Initialize once with an explicit schedule, prior reads and protected cutoff."""
        _day(end_before)
        if any(StudyContract.re_exposed(p, packets[:i]) for i, p in enumerate(packets)):
            raise ValueError("a re-exposure needs a disposed window: import it after disposition")
        if self.path.exists():
            raise ValueError("registry already exists; never initialize over history")
        if not isinstance(prior_exposure, list):
            raise ValueError("explicit prior exposure list required")
        for interval in prior_exposure:
            if (set(interval) != {"start", "end_before", "evidence"}
                    or not interval["evidence"]
                    or not _day(interval["start"]) < _day(interval["end_before"]) <= end_before):
                raise ValueError("invalid prior exposure import")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents two initializers from resetting each other.
        with self.path.open("xb"):
            pass
        with self._transaction(create=True) as (connection, _):
            state = {"identity": self.config, "revision": -1, "packets": packets,
                     "prior": prior_exposure, "end_before": end_before, "frontier": 0,
                     "active": None, "freezes": {}, "claims": {}, "dispositions": {},
                     "development_imports": [], "development_import_reasons": {}}
            self._save(connection, state, "initialize")
        return self.snapshot()

    def snapshot(self):
        """Read and verify state without advancing the revision."""
        with self._transaction() as (_, state):
            return copy.deepcopy(state)

    def _validate_plan(self, plan):
        """Rebuild a plan from its study and bind it to this registry."""
        if (not isinstance(plan, dict) or not isinstance(plan.get("study"), dict)
                or plan["study"].get("registry") != self.config):
            raise ValueError("plan registry identity mismatch")
        return StudyContract.verify_plan(plan["study"], plan)

    def _release(self, state, plan):
        """Apply one release rule for preview, freeze and atomic open."""
        plan = self._validate_plan(plan)
        if plan["study"]["packets"] != state["packets"]:
            raise ValueError("unbound plan or changed schedule")
        packet = plan["packet"]
        read_end = max(b["end_before"] for b in plan["read_bounds"].values())
        if read_end > state["end_before"]:
            raise ValueError("protected read boundary")
        if state["active"] is not None:
            raise ValueError("another packet claim is active")
        if packet["purpose"] == "development":
            if not any(i["start"] <= packet["start"] and read_end <= i["end_before"]
                       for i in state["prior"]):
                raise ValueError("development requires imported exposure covering all outcomes")
            return "development"
        next_packet = ExposureRegistry._next_frontier_packet(state)
        if next_packet is None or next_packet["id"] != packet["id"]:
            raise ValueError("packet is not the next shared frontier")
        ends = [i["end_before"] for i in state["prior"]]
        ends.extend(max(b["end_before"] for b in c["permit"]["read_bounds"].values())
                    for c in state["claims"].values())
        if ends and packet["start"] < max(ends):
            raise ValueError("previous reads overlap fresh entry")
        return "fresh"

    @staticmethod
    def _next_frontier(state):
        """Return the next frontier index and packet, skipping imported history."""
        imported = set(state.get("development_imports", ()))
        for index in range(state["frontier"], len(state["packets"])):
            packet = state["packets"][index]
            if packet["id"] not in imported:
                return index, packet
        return None, None

    @staticmethod
    def _next_frontier_packet(state):
        """Return the next frontier packet, skipping separately imported history."""
        return ExposureRegistry._next_frontier(state)[1]

    def import_development(self, plan, prior_exposure, expected_revision, reason):
        """Append one exposed historical development packet and its evidence atomically."""
        if not isinstance(plan, dict) or type(expected_revision) is not int:
            raise ValueError("complete plan and integer expected revision required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("development import reason required")
        if not isinstance(prior_exposure, list) or not prior_exposure:
            raise ValueError("explicit prior exposure evidence required")
        plan = self._validate_plan(plan)
        with self._transaction() as (connection, state):
            if expected_revision != state["revision"]:
                raise ValueError("stale registry revision")
            if state["active"] is not None:
                raise ValueError("development import requires inactive registry")
            old_packets = state["packets"]
            packets = plan["study"]["packets"]
            packet = plan["packet"]
            if (len(packets) != len(old_packets)+1
                    or packets[:len(old_packets)] != old_packets
                    or packets[-1] != packet or packet["purpose"] != "development"
                    or packet["id"] in {p["id"] for p in old_packets}
                    or packet["predecessor"] != old_packets[-1]["id"]):
                raise ValueError("exactly one new development packet must append to the existing schedule")
            twin = StudyContract.re_exposed(packet, old_packets)
            if twin is not None and twin not in state["dispositions"]:
                raise ValueError("re-exposure requires the original window to be disposed: " + twin)
            cutoff = state["end_before"]
            bounds = list(plan["read_bounds"].values())
            if not bounds:
                raise ValueError("development plan has no read bounds")
            for interval in prior_exposure:
                if (not isinstance(interval, dict)
                        or set(interval) != {"start", "end_before", "evidence"}
                        or not isinstance(interval["evidence"], str)
                        or not interval["evidence"].strip()
                        or not _day(interval["start"]) < _day(interval["end_before"]) <= cutoff):
                    raise ValueError("invalid prior development exposure evidence")
            for bound in bounds:
                _day(bound["start"])
                end = _day(bound["end_before"])
                if end > cutoff:
                    raise ValueError("development reads reach protected boundary")
                if not any(item["start"] <= bound["start"] and end <= item["end_before"]
                           for item in prior_exposure):
                    raise ValueError("prior exposure does not cover every development read bound")
            state["packets"] = copy.deepcopy(packets)
            state["prior"].extend(copy.deepcopy(prior_exposure))
            state.setdefault("development_imports", []).append(packet["id"])
            state.setdefault("development_import_reasons", {})[packet["id"]] = reason
            if twin is not None:
                state.setdefault("development_re_exposes", {})[packet["id"]] = twin
            self._save(connection, state, "import_development")
            return {"revision": state["revision"], "packet": copy.deepcopy(packet),
                    "frontier": state["frontier"], "reason": reason,
                    **({"re_exposes": twin} if twin is not None else {})}

    def preview(self, plan):
        """Report release readiness without any state or payload mutation."""
        state = self.snapshot()
        try:
            classification = self._release(state, plan)
            return {"ready": True, "classification": classification, "revision": state["revision"]}
        except ValueError as exc:
            return {"ready": False, "reason": str(exc), "revision": state["revision"]}

    def freeze(self, plan):
        """Freeze exactly one complete cohort after predecessor disposition."""
        plan = self._validate_plan(plan)
        with self._transaction() as (connection, state):
            classification = self._release(state, plan)
            predecessor = state["dispositions"].get(plan["packet"]["predecessor"])
            receipt = _seal({"plan_sha256": plan["sha256"], "cohort_sha256": plan["cohort_sha256"],
                             "predecessor": predecessor, "classification": classification,
                             "registry_revision": state["revision"], "registry": self.config})
            state["freezes"][receipt["sha256"]] = receipt
            self._save(connection, state, "freeze")
            return {"freeze": receipt, "expected_revision": state["revision"]}

    def claim(self, plan, request):
        """Compare-and-swap one frozen claim before a first payload read."""
        plan = self._validate_plan(plan)
        with self._transaction() as (connection, state):
            if set(request) != {"freeze", "expected_revision"} or request["expected_revision"] != state["revision"]:
                raise ValueError("stale registry revision")
            frozen = request["freeze"]
            _unseal(frozen)
            if state["freezes"].get(frozen["sha256"]) != frozen or frozen["plan_sha256"] != plan["sha256"]:
                raise ValueError("unknown or mismatched freeze receipt")
            classification = self._release(state, plan)
            if frozen["predecessor"] != state["dispositions"].get(plan["packet"]["predecessor"]):
                raise ValueError("predecessor disposition changed")
            prior_claims = [c for c in state["claims"].values() if c["permit"]["plan_sha256"] == plan["sha256"]]
            if prior_claims:
                classification = "replay"
            permit = _seal({"id": str(uuid.uuid4()), "registry": self.config,
                           "plan_sha256": plan["sha256"], "packet_id": plan["packet"]["id"],
                           "cohort_sha256": plan["cohort_sha256"], "freeze": frozen,
                           "source_pins_sha256": value_hash(plan["source_pins"]),
                           "read_bounds": plan["read_bounds"], "classification": classification,
                           "previous_frontier": state["frontier"],
                           "registry_revision_before": state["revision"],
                           "registry_revision_after": state["revision"]+1})
            state["claims"][permit["id"]] = {"permit": permit, "status": "claimed_exposed",
                                             "inputs": None, "execution": None, "result": None}
            state["active"] = permit["id"]
            self._save(connection, state, "claim")
            return permit

    def verify(self, plan, permit, *, active=True):
        """Verify exact permit, frozen plan and registry identity at any entrypoint."""
        plan = self._validate_plan(plan)
        _unseal(permit)
        state = self.snapshot()
        claim = state["claims"].get(permit["id"])
        if (permit["registry"] != self.config or permit["plan_sha256"] != plan["sha256"]
                or claim is None or claim["permit"] != permit
                or (active and (state["active"] != permit["id"] or claim["status"] != "claimed_exposed"))):
            raise ValueError("invalid, substituted or inactive packet permit")
        return claim

    def bind(self, plan, permit, kind, value):
        """Bind independently verified intake or execution exactly once to a claim."""
        self.verify(plan, permit)
        if kind not in {"inputs", "execution"}:
            raise ValueError("unsupported claim binding")
        with self._transaction() as (connection, state):
            claim = state["claims"][permit["id"]]
            if state["active"] != permit["id"] or claim["status"] != "claimed_exposed":
                raise ValueError("inactive claim")
            digest = value_hash(value)
            if claim[kind] is not None and claim[kind] != digest:
                raise ValueError("claim content changed")
            claim[kind] = digest
            self._save(connection, state, "bind_"+kind)

    def finish(self, plan, permit, result):
        """Close publication as complete or failed; both remain exposed until review."""
        self.verify(plan, permit)
        with self._transaction() as (connection, state):
            claim = state["claims"][permit["id"]]
            if state["active"] != permit["id"] or claim["status"] != "claimed_exposed":
                raise ValueError("inactive claim")
            claim["result"] = value_hash(result)
            claim["status"] = "completed_pending_review" if result["complete"] else "failed_exposed_pending_review"
            self._save(connection, state, "finish")
        return claim["result"]

    def publish(self, plan, permit, receipt):
        """Bind only a persisted, attested close artifact to its exact active claim."""
        plan = self._validate_plan(plan)
        artifact, manifest = self._attested_result(receipt)
        if artifact["permit"] != permit or artifact["plan"] != plan:
            raise ValueError("attested result does not match plan and permit")
        self.verify(plan, permit, active=False)
        if (artifact["provenance"] is None
                or value_hash({k: artifact[k] for k in ("results", "audit", "provenance")})
                != self.snapshot()["claims"][permit["id"]]["execution"]
                or value_hash(artifact["provenance"])
                != self.snapshot()["claims"][permit["id"]]["inputs"]):
            raise ValueError("attested result differs from registered execution")
        with self._transaction() as (connection, state):
            claim = state["claims"][permit["id"]]
            if state["active"] != permit["id"] or claim["status"] not in {"claimed_exposed", "completed_pending_review"}:
                raise ValueError("publication requires the active claim")
            publication = {"receipt": receipt, "artifact_sha256": manifest["sha256"]}
            if claim.get("publication") not in (None, publication):
                raise ValueError("published close receipt cannot change")
            if claim.get("result") not in (None, value_hash(artifact)):
                raise ValueError("published close result digest differs")
            claim["result"] = value_hash(artifact)
            claim["publication"] = publication
            claim["status"] = ("completed_pending_review" if artifact["complete"]
                               else "failed_exposed_pending_review")
            self._save(connection, state, "publish_attested_close")
            return copy.deepcopy(claim)

    def retry(self, plan, permit, expected_revision):
        """Retry an interrupted or failed active claim with the identical frozen plan."""
        plan = self._validate_plan(plan)
        _unseal(permit)
        with self._transaction() as (connection, state):
            old = state["claims"].get(permit["id"])
            if (expected_revision != state["revision"] or old is None or old["permit"] != permit
                    or state["active"] != permit["id"] or permit["plan_sha256"] != plan["sha256"]
                    or old["status"] not in {"claimed_exposed", "failed_exposed_pending_review", "completed_pending_review"}
                    or (old["status"] == "completed_pending_review" and old.get("publication") is not None)):
                raise ValueError("retry requires the same active frozen identity and current revision")
            body = _unseal(permit)
            body.update(id=str(uuid.uuid4()), classification="replay",
                        registry_revision_before=state["revision"],
                        registry_revision_after=state["revision"]+1)
            new = _seal(body)
            old["status"] = "retry_superseded_exposed"
            state["claims"][new["id"]] = {"permit": new, "status": "claimed_exposed",
                                          "inputs": None, "execution": None, "result": None}
            state["active"] = new["id"]
            self._save(connection, state, "same_identity_retry")
            return new

    def amend_retry(self, plan, permit, expected_revision, result_receipt, reason):
        """Replay a published failed result under one domain-approved amended plan."""
        plan = self._validate_plan(plan)
        if type(expected_revision) is not int or not isinstance(reason, str) or not reason.strip():
            raise ValueError("amended retry requires current revision and reason")
        artifact, manifest = self._attested_result(result_receipt)
        old_plan = artifact["plan"]
        _unseal(old_plan)
        _unseal(permit)
        if (artifact["complete"] is not False or artifact["permit"] != permit
                or old_plan["sha256"] != permit["plan_sha256"]
                or old_plan["packet"] != plan["packet"]
                or old_plan["cohort_sha256"] != plan["cohort_sha256"]
                or old_plan["source_pins"] != plan["source_pins"]
                or old_plan["read_bounds"] != plan["read_bounds"]
                or old_plan["study"]["registry"] != self.config
                or old_plan["study"]["contract"] != plan["study"]["contract"]
                or old_plan["study"]["packets"] != plan["study"]["packets"]
                or old_plan["study"]["selected_packet"] != plan["study"]["selected_packet"]
                or old_plan["study"]["dependencies"] != plan["study"]["dependencies"]):
            raise ValueError("amended retry changes protected plan identity")
        hook = import_ref(plan["study"]["contract"])
        hook.validate_amendment(old_plan["study"], plan["study"])
        with self._transaction() as (connection, state):
            old = state["claims"].get(permit["id"])
            publication = {"receipt": result_receipt, "artifact_sha256": manifest["sha256"]}
            if (state["revision"] != expected_revision or state["active"] != permit["id"]
                    or old is None or old["permit"] != permit
                    or old["status"] != "failed_exposed_pending_review"
                    or old.get("publication") != publication
                    or old["result"] != value_hash(artifact)
                    or old["inputs"] != value_hash(artifact["provenance"])
                    or old["execution"] != value_hash({k: artifact[k] for k in ("results", "audit", "provenance")})
                    or state["packets"] != plan["study"]["packets"]):
                raise ValueError("amended retry requires exact published failed claim and current revision")
            predecessor = state["dispositions"].get(plan["packet"]["predecessor"])
            if predecessor != permit["freeze"]["predecessor"]:
                raise ValueError("amended retry predecessor changed")
            frozen = _seal({"plan_sha256": plan["sha256"],
                            "cohort_sha256": plan["cohort_sha256"],
                            "predecessor": predecessor,
                            "classification": "amended_replay",
                            "registry_revision": state["revision"],
                            "registry": self.config})
            body = {k: copy.deepcopy(v) for k, v in permit.items() if k != "sha256"}
            body.update(id=str(uuid.uuid4()), plan_sha256=plan["sha256"],
                        freeze=frozen, classification="amended_replay",
                        source_pins_sha256=value_hash(plan["source_pins"]),
                        read_bounds=plan["read_bounds"],
                        registry_revision_before=state["revision"],
                        registry_revision_after=state["revision"]+1,
                        amended_from={"claim": permit["id"],
                                      "failed_receipt": result_receipt,
                                      "failed_manifest_sha256": manifest["sha256"],
                                      "reason": reason})
            new = _seal(body)
            old["status"] = "retry_superseded_exposed"
            state["freezes"][frozen["sha256"]] = frozen
            state["claims"][new["id"]] = {"permit": new, "status": "claimed_exposed",
                                          "inputs": None, "execution": None, "result": None}
            state["active"] = new["id"]
            self._save(connection, state, "published_failure_amended_replay")
            return new

    def extend(self, plan, reason):
        """Append future schedule entries without changing any existing packet."""
        plan = self._validate_plan(plan)
        if not reason:
            raise ValueError("complete plan and extension reason required")
        with self._transaction() as (connection, state):
            packets = plan["study"]["packets"]
            if (state["active"] is not None or len(packets) <= len(state["packets"])
                    or packets[:len(state["packets"])] != state["packets"]):
                raise ValueError("only an inactive append-only schedule extension is allowed")
            if any(packet["purpose"] == "development"
                   for packet in packets[len(state["packets"]):]):
                raise ValueError("development packets require the explicit prior-exposure import")
            state["packets"] = copy.deepcopy(packets)
            self._save(connection, state, "extend_schedule")
            return {"revision": state["revision"], "packets": packets, "reason": reason}

    def dispose(self, packet_id, action, reason, result_receipt=None):
        """Review success, abandon exposure or irrevocably retire the current frontier."""
        if action not in {"review", "abandon", "retire"} or not isinstance(reason, str) or not reason.strip():
            raise ValueError("explicit disposition and reason required")
        with self._transaction() as (connection, state):
            frontier_index, frontier_packet = self._next_frontier(state)
            current = frontier_packet["id"] if frontier_packet else None
            active = state["claims"].get(state["active"])
            if action == "retire":
                if active or packet_id != current:
                    raise ValueError("only the unread current frontier may be retired")
            else:
                if not active or active["permit"]["packet_id"] != packet_id:
                    raise ValueError("no matching active claim")
                if action == "review":
                    if result_receipt is None:
                        raise ValueError("attested close result receipt required")
                    artifact, manifest = self._attested_result(result_receipt)
                    if (artifact["permit"] != active["permit"] or artifact["complete"] is not True
                            or artifact["plan"]["sha256"] != active["permit"]["plan_sha256"]
                            or active["inputs"] != value_hash(artifact["provenance"])
                            or active["execution"] != value_hash({k: artifact[k] for k in ("results", "audit", "provenance")})
                            or (active["result"] is not None and active["result"] != value_hash(artifact))):
                        raise ValueError("attested result does not match registered complete claim")
                    publication = {"receipt": result_receipt, "artifact_sha256": manifest["sha256"]}
                    if active.get("publication") not in (None, publication):
                        raise ValueError("review receipt differs from registered publication")
                    active["result"] = value_hash(artifact)
                    active["publication"] = publication
                    active["status"] = "completed_pending_review"
                if (action == "abandon" and active["status"] == "completed_pending_review"
                        and active.get("publication") is not None):
                    raise ValueError("published completed result requires review")
                active["status"] = "reviewed_closed" if action == "review" else "abandoned_exposed"
                state["active"] = None
            receipt = _seal({"packet_id": packet_id, "action": action, "reason": reason,
                             "revision": state["revision"], "claim": active["permit"]["id"] if active else None})
            state["dispositions"][packet_id] = receipt
            if packet_id == current:
                state["frontier"] = frontier_index+1
            self._save(connection, state, action)
            return receipt

    @staticmethod
    def _attested_result(receipt):
        """Load one completed close artifact through its immutable run attestation."""
        if (not isinstance(receipt, dict) or set(receipt) != {"run_dir", "document_hash", "node", "port"}
                or not receipt["node"] or not receipt["port"]):
            raise ValueError("invalid result receipt")
        config = json.loads((Path(receipt["run_dir"]) / "config.json").read_text())
        node = config.get("pipeline", {}).get(receipt["node"])
        if (receipt["port"] != "receipt" or not isinstance(node, dict)
                or node.get("uses") != "dskit.pipeline.study_protocol:StudyPacketClose"):
            raise ValueError("receipt must attest StudyPacketClose.receipt")
        run = RunAttestation(receipt["run_dir"])
        manifest = run.attested_output(receipt["node"], receipt["port"], receipt["document_hash"])
        if manifest is None:
            raise ValueError("close result lacks completed run attestation")
        artifact = resolve_json_artifact(receipt["run_dir"], manifest)
        if not isinstance(artifact, dict) or not {"plan", "permit", "results", "audit", "provenance", "complete"} <= set(artifact):
            raise ValueError("invalid attested close artifact")
        return artifact, manifest


class StudyNode(Node):
    """Shared strict parameter handling; subclasses own their declared ports."""

    role = "transform"
    _PARAMS = ("study",)

    @classmethod
    def validate_params(cls, params):
        errors = []
        reject_unknown_params(errors, params, cls._PARAMS)
        if not isinstance(params.get("study"), dict):
            errors.append("study object required")
        return errors

    def plan(self):
        return StudyContract.build(self.params["study"])

    def registry(self):
        return ExposureRegistry(self.params["study"]["registry"])


class StudyProtocolPlan(StudyNode):
    """Metadata preview: node.run(ctx, {}) returns plan and release readiness."""

    outputs = ("plan", "readiness")

    def run(self, ctx, inputs):
        plan = self.plan()
        try:
            readiness = self.registry().preview(plan)
        except (ValueError, sqlite3.Error) as exc:
            readiness = {"ready": False, "reason": str(exc)}
        return {"plan": plan, "readiness": readiness}


class StudyProtocolControl(StudyNode):
    """Explicit JSON initialization, freeze and disposition; never reads payloads."""

    _PARAMS = ("study", "action", "prior_exposure", "end_before", "reason", "permit", "expected_revision", "result_receipt")
    outputs = ("control",)

    @classmethod
    def validate_params(cls, params):
        errors = super().validate_params(params)
        action = params.get("action")
        required = {
            "initialize": {"prior_exposure", "end_before"}, "freeze": set(),
            "review": {"reason", "result_receipt"}, "abandon": {"reason"}, "retire": {"reason"},
            "retry": {"permit", "expected_revision"},
            "amend_retry": {"permit", "expected_revision", "result_receipt", "reason"},
            "extend": {"reason"},
            "import_development": {"prior_exposure", "expected_revision", "reason"}}.get(action)
        if required is None:
            errors.append("explicit supported control action required")
        elif set(params)-{"study", "action"} != required:
            errors.append("control parameters must exactly match the selected action")
        return errors

    def run(self, ctx, inputs):
        p, registry = self.params, self.registry()
        action = p["action"]
        if action == "initialize":
            self.plan()
            result = registry.initialize(p["study"]["packets"], p["prior_exposure"], p["end_before"])
        elif action == "retry":
            result = {"permit": registry.retry(self.plan(), p["permit"], p["expected_revision"])}
        elif action == "amend_retry":
            result = {"permit": registry.amend_retry(
                self.plan(), p["permit"], p["expected_revision"],
                p["result_receipt"], p["reason"])}
        elif action == "extend":
            result = registry.extend(self.plan(), p["reason"])
        elif action == "import_development":
            if type(p["expected_revision"]) is not int:
                raise ValueError("integer expected registry revision required")
            result = registry.import_development(self.plan(), p["prior_exposure"],
                                                 p["expected_revision"], p["reason"])
        elif action == "freeze":
            plan = self.plan()
            if not plan["runnable"]:
                raise ValueError("cannot freeze unbound study")
            result = {"frozen_plan": plan, "claim_request": registry.freeze(plan)}
        else:
            result = registry.dispose(p["study"]["selected_packet"], action, p["reason"],
                                       p.get("result_receipt"))
        return {"control": result}


class StudyPacketOpen(StudyNode):
    """Revalidate a frozen request, then atomically claim its permitted reads."""

    _PARAMS = ("study", "frozen_plan", "claim_request")
    outputs = ("permit",)

    def run(self, ctx, inputs):
        request = inputs.get("control", self.params)
        if set(request) == {"permit"}:
            plan = self.plan()
            self.registry().verify(plan, request["permit"])
            return {"permit": request["permit"]}
        plan = StudyContract.verify_plan(self.params["study"], request["frozen_plan"])
        return {"permit": self.registry().claim(plan, request["claim_request"])}


class StudyPacketClose(StudyNode):
    """Publish all declared methods, including failures, after registry binding."""

    outputs = ("receipt",)

    def run(self, ctx, inputs):
        plan, registry = self.plan(), self.registry()
        permit = inputs["permit"]
        claim = registry.verify(plan, permit)
        result = {k: inputs[k] for k in ("results", "audit", "provenance")}
        if claim["execution"] != value_hash(result) or claim["inputs"] != value_hash(inputs["provenance"]):
            raise ValueError("unverified execution or intake")
        hook = import_ref(self.params["study"]["contract"])()
        complete = hook.validate_result(self.params["study"], **result)
        artifact = {"plan": plan, "permit": permit, **result, "complete": complete}
        return {"receipt": JsonArtifact(artifact)}


class StudyPacketResults(StudyNode):
    """Read only completed pipeline artifacts and reconcile registry publication."""

    _PARAMS = ("study", "result_receipts")
    outputs = ("results", "identities")

    def run(self, ctx, inputs):
        plan, registry = self.plan(), self.registry()
        results = []
        receipts = self.params["result_receipts"]
        if not isinstance(receipts, list) or not receipts:
            raise ValueError("nonempty result receipts required")
        for receipt in receipts:
            if set(receipt) != {"run_dir", "document_hash", "node", "port"}:
                raise ValueError("invalid result receipt")
            result, manifest = registry._attested_result(receipt)
            if result["plan"] != plan:
                raise ValueError("result plan differs")
            claim = registry.publish(plan, result["permit"], receipt)
            if (claim["result"] != value_hash(result)
                    or claim.get("publication", {}).get("artifact_sha256") != manifest["sha256"]):
                raise ValueError("result is not the registered publication")
            results.append(result)
        if len({r["permit"]["id"] for r in results}) != len(results):
            raise ValueError("duplicate result receipt")
        return {"results": results, "identities": [r["permit"] for r in results]}

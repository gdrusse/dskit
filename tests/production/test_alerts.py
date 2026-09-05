"""`alerts.py` — the sinks and the router that must never be able to stop a tick.

D17 is the whole argument: an alert is how an operator learns a symptom, and an
alert path that can BLOCK is a worse hazard than the symptom it reports. So
§5.11 makes four promises, and every test here is one of them:

- **Construction validates configuration only.** No socket, no DNS, no read of
  a credential's VALUE at construction — reachability is a health probe (§5.11,
  D17), never a constructor side effect. What construction *does* refuse is
  configuration: an unknown param, a network sink with no transport, and a sink
  whose `url_env` names a variable the process cannot resolve (§5.11's "a sink
  that names a var absent from `env.require` refuses"; `test_document.py`
  already pins the document-level half, this file pins the sink's own).
- **The URL is the credential.** `alert_endpoints.<sink>.url_env` is the env-var
  NAME; the value is resolved at SEND time and never stored, never logged and
  never written into the §6 `alert` record — whose body carries per-sink
  outcomes and nothing that could be replayed by whoever reads the ledger.
- **Nothing a sink does reaches the caller.** A sink that raises, answers a
  500, or blocks forever is swallowed and COUNTED — `alert_sink_failures_total`
  and `alerts_suppressed_total{why}` are the only evidence any of it happened
  (§5.11.1), which is why the accounting is pinned as hard as the delivery. A
  blocked sink is bounded by its own `timeout_s`, disabled, and NOT replaced:
  a permanently stuck call costs one daemon thread, once (D23).
- **Fewer notifications than alerts, by design.** Fingerprint dedup,
  `group_wait_s`, `repeat_interval_s` and the token bucket all withhold; each
  one that withholds must count, and `critical` bypasses the token bucket but
  not the dedup. The conservation test below is the strongest statement of
  that: every enqueued alert is delivered exactly once or counted exactly once.

Determinism: the router's worker thread is a thin wrapper around the
synchronous `process(now_ms)` driver, so every test here drives `process`
itself on a `TestClock` and starts no thread — except the two whose SUBJECT is
a thread (the hanging sink, and `start()`), which use real waits bounded well
under a second. No network: the `Transport` and the mail seam are injected
recorders.
"""

import inspect
import json
import logging
import threading
import time

import pytest

from dskit.pipeline import base as pipeline_base
from dskit.production import alerts as alerts_module
from dskit.production import vocab
from dskit.production.alerts import (
    ALERT_SINK_KINDS,
    DEFAULT_GROUP_WAIT_S,
    DEFAULT_QUEUE_MAXSIZE,
    DEFAULT_REPEAT_INTERVAL_S,
    DEFAULT_SINK_TIMEOUT_S,
    AlertRouter,
    AlertSink,
    EmailSink,
    LogSink,
    MemorySink,
    Notification,
    SinkOutcome,
    WebhookSink,
)
from dskit.production.base import ProductionError
from dskit.production.clock import TestClock
from dskit.production.document import (
    GROUP_WAIT_S_BOUNDS,
    REPEAT_INTERVAL_S_BOUNDS,
    ServeDocument,
)
from dskit.production.metrics import Metrics
from dskit.production.records import Alert
from dskit.production.redact import REDACTED, register_secret
from tests.production.test_document import example_document, minimal_document

#: The instant every test starts at; the `TestClock` never moves on its own.
NOW_MS = 1_767_225_600_000

#: The env-var NAME the §4.1 illustration uses, and a value shaped like a real
#: webhook — the path IS the bearer token, which is why it may never be stored.
URL_ENV = "OPS_WEBHOOK_URL"
WEBHOOK_URL = "https://hooks.example.test/services/T0000/B1111/ZzSecretPath"

#: The logger `redact.get_logger` gives this module (§5.0's namespace rule).
LOGGER_NAME = "dskit.production.alerts"

#: The two counters §5.11.1 says resolve here and nowhere else.
FAILURES = "alert_sink_failures_total"
SUPPRESSED = "alerts_suppressed_total"

#: The label values `vocab` declares for `alert_sink_failures_total{sink}` —
#: the core KIND names, never a document's instance name.
SINK_LABEL_VALUES = vocab.METRIC_LABEL_VALUES[FAILURES]["sink"]


# --------------------------------------------------------------------------
# doubles — every collaborator a sink or the router is handed
# --------------------------------------------------------------------------


class Env:
    """A `Secrets`-shaped resolver that RECORDS every value read.

    `dskit.pipeline.env.Secrets` supports exactly `in` and `[]`; this stands
    in for it so a test can prove construction never touched a value.
    """

    def __init__(self, values=None):
        self._values = dict(values or {URL_ENV: WEBHOOK_URL})
        self.reads = []

    def __contains__(self, name):
        return name in self._values

    def __getitem__(self, name):
        self.reads.append(name)
        return self._values[name]


class FakeTransport:
    """A `Transport`-shaped recorder: one `send`, a value back, no socket."""

    def __init__(self, answer=(200, {}, b""), error=None):
        self.answer = answer
        self.error = error
        self.calls = []

    def send(self, method, url, headers, body, timeout):
        """Record the call and answer (or raise) as configured."""
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": dict(timeout or {}),
            }
        )
        if self.error is not None:
            raise self.error
        return self.answer


class FakeMail:
    """An `smtplib`-shaped seam: `send_message(message)` and nothing else."""

    def __init__(self, error=None):
        self.error = error
        self.messages = []

    def send_message(self, message):
        """Record the message and raise if the test asked for a failure."""
        self.messages.append(message)
        if self.error is not None:
            raise self.error


class FakeLedger:
    """Records what the router appended, refusing anything but `{kind, id, body}`."""

    def __init__(self):
        self.records = []
        self.barriers = 0

    def append(self, record):
        """Append one record and return its dense 1-based seq."""
        assert set(record) == {"kind", "id", "body"}, record
        assert record["kind"] in vocab.RECORD_KINDS
        json.dumps(record)  # the body must be JSON-ready
        self.records.append(record)
        return len(self.records)

    def barrier(self):
        """Count a barrier; durability is `test_ledger.py`'s subject."""
        self.barriers += 1

    def of_kind(self, kind):
        """Every appended record of one kind, in order."""
        return [r for r in self.records if r["kind"] == kind]


class CountingSink(AlertSink):
    """A sink that answers as told and counts what it was asked to send."""

    def __init__(self, params=None, *, answer=True, error=None, **collaborators):
        super().__init__(params, **collaborators)
        self.answer = answer
        self.error = error
        self.sent = []
        self.closed = 0

    def send(self, alert):
        """Record the alert, then raise or answer as configured."""
        self.sent.append(alert)
        if self.error is not None:
            raise self.error
        return SinkOutcome(ok=self.answer, detail="counted")

    def close(self):
        """Count the close so idempotence is observable."""
        self.closed += 1


class HangingSink(AlertSink):
    """A sink whose `send` never returns until a test releases it.

    This is §8's "never-replying local socket" without a socket: what the
    plan bounds is a CALL that does not come back, and an `Event` nobody
    sets is that, deterministically and with no network.
    """

    def __init__(self, params=None, **collaborators):
        super().__init__(params, **collaborators)
        self.released = threading.Event()
        self.entered = threading.Event()
        self.calls = 0

    def send(self, alert):
        """Block until released; the router must not wait for that."""
        self.calls += 1
        self.entered.set()
        self.released.wait(10)
        return SinkOutcome(ok=True, detail="finally")


# --------------------------------------------------------------------------
# builders
# --------------------------------------------------------------------------


def an_alert(
    fingerprint="feed-stale",
    severity="warning",
    status="firing",
    summary="feed degraded",
    source="feed",
    tick_id=None,
    at_ms=NOW_MS,
    labels=None,
):
    """One `records.Alert`, with the fields a test cares about named."""
    return Alert(
        fingerprint=fingerprint,
        severity=severity,
        status=status,
        summary=summary,
        source=source,
        tick_id=tick_id,
        at_ms=at_ms,
        labels=dict(labels or {}),
    )


def serve_doc(sinks=None, routes=None, endpoints=None, require=None, **alerting):
    """A validated `ServeDocument` whose `alerting` block the test dictates."""
    obj = example_document()
    obj["alerting"]["sinks"] = dict(sinks if sinks is not None else {"ops": {"uses": "memory"}})
    obj["alerting"]["routes"] = list(
        routes
        if routes is not None
        else [{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES]
    )
    for key, value in alerting.items():
        if value is None:
            obj["alerting"].pop(key, None)
        else:
            obj["alerting"][key] = value
    if endpoints is None:
        obj.pop("alert_endpoints", None)
    else:
        obj["alert_endpoints"] = dict(endpoints)
    obj["env"]["require"] = list(require if require is not None else [URL_ENV])
    return ServeDocument.from_obj(obj)


def webhook_endpoint(timeout_s=5, template="slack", url_env=URL_ENV):
    """The `alert_endpoints` entry the §4.1 illustration writes for `ops`."""
    entry = {"url_env": url_env}
    if template is not None:
        entry["template"] = template
    if timeout_s is not None:
        entry["timeout_s"] = timeout_s
    return entry


def endpoint_view(timeout_s=5, template="slack", name="ops", uses="webhook"):
    """A single `document.alert_endpoints.<name>` view, straight from a document."""
    doc = serve_doc(
        sinks={name: {"uses": uses}},
        routes=[{"severity": s, "sinks": [name]} for s in vocab.SEVERITIES],
        endpoints={name: webhook_endpoint(timeout_s=timeout_s, template=template)},
    )
    return doc.alert_endpoints[name]


def a_webhook(transport=None, secrets=None, endpoint=None, params=None, clock=None):
    """A constructible `WebhookSink` with every collaborator injected."""
    return WebhookSink(
        params,
        endpoint=endpoint if endpoint is not None else endpoint_view(),
        transport=transport if transport is not None else FakeTransport(),
        clock=clock if clock is not None else TestClock(start_ms=NOW_MS),
        secrets=secrets if secrets is not None else Env(),
    )


def a_router(
    sinks,
    doc=None,
    clock=None,
    metrics=None,
    ledger=None,
    maxsize=None,
    **alerting,
):
    """An `AlertRouter` over the document's `alerting` block and the given sinks."""
    if doc is None:
        doc = serve_doc(
            sinks={name: {"uses": "memory"} for name in sinks},
            routes=[{"severity": s, "sinks": sorted(sinks)} for s in vocab.SEVERITIES],
            **alerting,
        )
    kwargs = {} if maxsize is None else {"maxsize": maxsize}
    return AlertRouter(
        doc.alerting,
        sinks,
        clock=clock if clock is not None else TestClock(start_ms=NOW_MS),
        metrics=metrics if metrics is not None else Metrics(),
        ledger=ledger,
        **kwargs,
    )


def counts(metrics, name):
    """`{label value: count}` for a single-label counter, from the snapshot."""
    series = metrics.snapshot().get(name, {})
    return {key.split("=", 1)[1]: value for key, value in series.items()}


def strings_in(obj):
    """Every string anywhere inside a JSON-shaped object."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for value in obj.values() for s in strings_in(value)] + list(obj)
    if isinstance(obj, (list, tuple)):
        return [s for value in obj for s in strings_in(value)]
    return []


@pytest.fixture
def clock():
    """The one instant every test moves by hand."""
    return TestClock(start_ms=NOW_MS)


@pytest.fixture
def metrics():
    """A fresh registry; `flush` is never called (no `log_dir`)."""
    return Metrics()


@pytest.fixture
def hanging():
    """A `HangingSink` whose blocked worker is released at teardown."""
    sink = HangingSink(
        None,
        endpoint=endpoint_view(timeout_s=0.05, name="stuck", uses="memory"),
    )
    yield sink
    sink.released.set()


# --------------------------------------------------------------------------
# the seam: AlertSink construction and the registry
# --------------------------------------------------------------------------


def test_alert_sink_is_abstract_and_send_is_the_hook():
    # `@abstractmethod send` (§5.11): an incomplete sink refuses to
    # CONSTRUCT rather than failing at the first alert.
    with pytest.raises(TypeError):
        AlertSink(None)

    class NoSend(AlertSink):
        pass

    with pytest.raises(TypeError):
        NoSend(None)
    assert isinstance(CountingSink(None), AlertSink)


def test_the_alert_sink_constructor_is_params_plus_four_keyword_collaborators():
    # Pinned exactly: `AlertSink(params, *, endpoint, transport, clock,
    # secrets)`. compose.py supplies the four by name, so their spelling
    # is part of the contract, not an implementation detail.
    parameters = inspect.signature(AlertSink.__init__).parameters
    assert list(parameters) == ["self", "params", "endpoint", "transport", "clock", "secrets"]
    assert parameters["params"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("endpoint", "transport", "clock", "secrets"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_a_sink_constructs_with_no_collaborators_at_all():
    # `log` and `memory` reach no network and need nothing injected, so the
    # four collaborators default to absent.
    sink = MemorySink(None)
    assert sink.send(an_alert()).ok is True


def test_the_registry_carries_exactly_the_four_core_kinds():
    assert ALERT_SINK_KINDS.family == "alert_sink"
    assert ALERT_SINK_KINDS.kinds() == ("email", "log", "memory", "webhook")
    for name in ALERT_SINK_KINDS.kinds():
        assert issubclass(ALERT_SINK_KINDS.resolve(name), AlertSink)
    with pytest.raises(ProductionError):
        ALERT_SINK_KINDS.resolve("pagerduty")


def test_alert_sink_kinds_is_not_the_pipelines_tracking_sink_registry():
    # §4.3: the name is `ALERT_SINK_KINDS` precisely because `SINK_KINDS`
    # is taken by the pipeline's tracking sinks and BOTH carry `memory`.
    assert "memory" in ALERT_SINK_KINDS
    assert ALERT_SINK_KINDS is not pipeline_base.SINK_KINDS
    assert not hasattr(alerts_module, "SINK_KINDS")
    assert not hasattr(pipeline_base, "ALERT_SINK_KINDS")


def test_every_registered_kind_reports_that_name_as_its_kind():
    # The `alert_sink_failures_total{sink}` label value is the KIND, so the
    # registry key and the sink's own answer must be one fact, not two.
    built = {
        "log": LogSink(None),
        "memory": MemorySink(None),
        "webhook": a_webhook(),
        "email": EmailSink(
            None, endpoint=endpoint_view(), secrets=Env(), mail_transport=FakeMail()
        ),
    }
    assert sorted(built) == list(ALERT_SINK_KINDS.kinds())
    for name, sink in built.items():
        assert isinstance(sink, ALERT_SINK_KINDS.resolve(name))
        assert sink.kind == name


def test_the_core_kind_names_are_exactly_the_metric_label_values():
    # §5.11.1 + `vocab`: the label's permitted values ARE the registry's
    # keys; a drift between them would silently drop a real failure into
    # the reserved `other` bucket.
    assert tuple(sorted(SINK_LABEL_VALUES)) == ALERT_SINK_KINDS.kinds()


def test_a_class_the_registry_does_not_name_falls_outside_the_label_table():
    # A child sink referenced by path is not a core kind: its label value
    # is not in the closed set, so `metrics` drops it to `other` by the
    # ordinary cardinality rule rather than growing the table.
    assert CountingSink(None).kind not in SINK_LABEL_VALUES


@pytest.mark.parametrize("cls", [LogSink, MemorySink])
def test_a_sink_refuses_an_unknown_param(cls):
    with pytest.raises(ProductionError) as exc:
        cls({"invented_knob": 1})
    assert "invented_knob" in str(exc.value)


def test_notes_are_accepted_in_a_sinks_params():
    assert MemorySink({"notes": "why this sink exists"}).kind == "memory"


# --------------------------------------------------------------------------
# construction validates configuration ONLY
# --------------------------------------------------------------------------


def test_constructing_a_webhook_touches_no_transport():
    # D17: "reachability is a dependency probe, never a constructor side
    # effect". A transport that raises on use proves nothing used it.
    transport = FakeTransport(error=OSError("connection refused"))
    a_webhook(transport=transport)
    assert transport.calls == []


def test_constructing_a_webhook_never_reads_the_url_value():
    # The URL is the credential: it is resolved at SEND time so that a
    # constructed-but-never-used sink holds nothing worth stealing.
    secrets = Env()
    sink = a_webhook(secrets=secrets)
    assert secrets.reads == []
    sink.send(an_alert())
    assert secrets.reads == [URL_ENV]


def test_a_webhook_refuses_at_construction_without_an_endpoint():
    with pytest.raises(ProductionError) as exc:
        WebhookSink(None, transport=FakeTransport(), secrets=Env())
    assert "endpoint" in str(exc.value)


def test_a_webhook_refuses_at_construction_without_a_transport():
    with pytest.raises(ProductionError) as exc:
        WebhookSink(None, endpoint=endpoint_view(), secrets=Env())
    assert "transport" in str(exc.value)


def test_a_webhook_refuses_a_url_env_the_process_cannot_resolve():
    # §5.11: "a sink that names a var absent from `env.require` refuses".
    # `test_document.py` pins the document-level half; this is the sink's
    # own, so a sink built by hand cannot skip the check.
    endpoint = endpoint_view()
    with pytest.raises(ProductionError) as exc:
        WebhookSink(None, endpoint=endpoint, transport=FakeTransport(), secrets=Env({}))
    assert URL_ENV in str(exc.value)


def test_an_email_sink_refuses_without_its_mail_transport():
    # `email` is a network sink like `webhook`; the seam is SMTP-shaped
    # rather than HTTP-shaped, which is the only difference.
    with pytest.raises(ProductionError) as exc:
        EmailSink(None, endpoint=endpoint_view(), secrets=Env())
    assert "mail_transport" in str(exc.value)


def test_a_sink_exposes_its_endpoints_timeout_and_template():
    sink = a_webhook(endpoint=endpoint_view(timeout_s=2.5, template="slack"))
    assert sink.timeout_s == 2.5
    assert sink.template == "slack"


def test_an_absent_endpoint_timeout_falls_back_to_the_one_named_default():
    # `alert_endpoints.<sink>.timeout_s` is optional, so the default lives
    # in exactly one name that validation and the send alike read.
    sink = a_webhook(endpoint=endpoint_view(timeout_s=None))
    assert sink.timeout_s == DEFAULT_SINK_TIMEOUT_S
    assert DEFAULT_SINK_TIMEOUT_S > 0


def test_a_sink_with_no_endpoint_still_has_a_timeout():
    assert MemorySink(None).timeout_s == DEFAULT_SINK_TIMEOUT_S


# --------------------------------------------------------------------------
# WebhookSink — what actually goes over the wire
# --------------------------------------------------------------------------


def test_the_webhook_posts_json_to_the_resolved_url():
    transport = FakeTransport()
    sink = a_webhook(transport=transport)
    outcome = sink.send(an_alert())
    assert isinstance(outcome, SinkOutcome)
    assert outcome.ok is True
    (call,) = transport.calls
    assert call["method"] == "POST"
    assert call["url"] == WEBHOOK_URL
    assert call["headers"].get("Content-Type") == "application/json"
    assert isinstance(call["body"], bytes)
    json.loads(call["body"].decode("utf-8"))


def test_the_webhook_deadline_is_the_endpoints_timeout_on_both_halves():
    # §5.11: core network sinks use transports with REAL socket deadlines.
    # One configured `timeout_s` bounds both halves of the §5.12 timeout.
    transport = FakeTransport()
    a_webhook(transport=transport, endpoint=endpoint_view(timeout_s=3)).send(an_alert())
    assert transport.calls[0]["timeout"] == {"connect_s": 3, "read_s": 3}


def test_the_posted_body_carries_the_alert():
    transport = FakeTransport()
    alert = an_alert(fingerprint="venue-down", severity="critical", summary="venue unreachable")
    a_webhook(transport=transport).send(alert)
    text = transport.calls[0]["body"].decode("utf-8")
    for value in ("venue-down", "critical", "venue unreachable", "firing"):
        assert value in text


def test_the_posted_body_carries_no_credential_and_no_url():
    # Every alert body passes through `redact` (§5.0): a summary that
    # quotes a secret, and the destination URL itself, are both masked.
    register_secret("super-secret-token")
    transport = FakeTransport()
    alert = an_alert(summary="auth failed with super-secret-token", labels={"url": WEBHOOK_URL})
    a_webhook(transport=transport).send(alert)
    text = transport.calls[0]["body"].decode("utf-8")
    assert "super-secret-token" not in text
    assert WEBHOOK_URL not in text
    assert REDACTED in text


def test_a_non_2xx_answer_is_a_failed_outcome_not_a_raise():
    sink = a_webhook(transport=FakeTransport(answer=(500, {}, b"boom")))
    outcome = sink.send(an_alert())
    assert outcome.ok is False
    assert "500" in outcome.detail


@pytest.mark.parametrize("status", [200, 201, 202, 204, 299])
def test_every_2xx_answer_is_a_success(status):
    transport = FakeTransport(answer=(status, {}, b""))
    assert a_webhook(transport=transport).send(an_alert()).ok is True
    assert len(transport.calls) == 1


def test_a_transport_raise_is_a_failed_outcome_with_a_redacted_detail():
    register_secret("another-secret")
    sink = a_webhook(transport=FakeTransport(error=OSError(f"refused by {WEBHOOK_URL} another-secret")))
    outcome = sink.send(an_alert())
    assert outcome.ok is False
    assert WEBHOOK_URL not in outcome.detail
    assert "another-secret" not in outcome.detail


def test_the_outcome_never_carries_the_url_on_success():
    outcome = a_webhook().send(an_alert())
    assert WEBHOOK_URL not in outcome.detail


# --------------------------------------------------------------------------
# the other three core sinks
# --------------------------------------------------------------------------


def test_memory_sink_keeps_what_it_was_sent():
    sink = MemorySink(None)
    first, second = an_alert(fingerprint="a"), an_alert(fingerprint="b")
    assert sink.send(first).ok is True
    sink.send(second)
    assert [a.fingerprint for a in sink.sent] == ["a", "b"]


@pytest.mark.parametrize("severity", vocab.SEVERITIES)
def test_log_sink_logs_at_the_level_the_severity_map_pins(severity, caplog):
    # §5.11 pins SEVERITIES to the `logging` levels; the log sink is where
    # that map stops being a table and starts being behaviour.
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    LogSink(None).send(an_alert(severity=severity, summary=f"level for {severity}"))
    records = [r for r in caplog.records if r.name == LOGGER_NAME]
    assert records[-1].levelno == vocab.SEVERITY_LEVELS[severity]["logging"]


def test_log_sink_never_writes_a_credential(caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    register_secret("log-sink-secret")
    LogSink(None).send(an_alert(summary="token log-sink-secret leaked"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "log-sink-secret" not in text
    assert REDACTED in text


def test_email_sink_sends_one_message_through_the_injected_seam():
    mail = FakeMail()
    sink = EmailSink(None, endpoint=endpoint_view(), secrets=Env(), mail_transport=mail)
    outcome = sink.send(an_alert(severity="error", summary="ledger unwritable"))
    assert outcome.ok is True
    assert len(mail.messages) == 1
    rendered = str(mail.messages[0])
    assert "ledger unwritable" in rendered


def test_email_sink_turns_a_transport_failure_into_a_failed_outcome():
    mail = FakeMail(error=OSError("smtp refused"))
    sink = EmailSink(None, endpoint=endpoint_view(), secrets=Env(), mail_transport=mail)
    assert sink.send(an_alert()).ok is False


@pytest.mark.parametrize("build", [lambda: LogSink(None), lambda: MemorySink(None)])
def test_close_is_idempotent(build):
    sink = build()
    sink.close()
    sink.close()


# --------------------------------------------------------------------------
# AlertRouter — configuration
# --------------------------------------------------------------------------


def test_the_router_refuses_a_route_naming_a_sink_it_was_not_given(clock, metrics):
    doc = serve_doc(
        sinks={"ops": {"uses": "memory"}, "pager": {"uses": "memory"}},
        routes=[{"severity": "critical", "sinks": ["ops", "pager"]}],
    )
    with pytest.raises(ProductionError) as exc:
        AlertRouter(doc.alerting, {"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert "pager" in str(exc.value)


def test_the_router_refuses_a_sink_the_document_does_not_declare(clock, metrics):
    doc = serve_doc()
    with pytest.raises(ProductionError) as exc:
        AlertRouter(
            doc.alerting,
            {"ops": MemorySink(None), "smuggled": MemorySink(None)},
            clock=clock,
            metrics=metrics,
        )
    assert "smuggled" in str(exc.value)


def test_the_cadence_defaults_are_named_once_and_inside_the_document_bounds():
    # §5.11: group_wait_s [0,600] default 30; repeat_interval_s
    # [60,86400] default 14400. The BOUNDS are `document.py`'s (one owner,
    # imported here); the DEFAULTS are this module's.
    assert DEFAULT_GROUP_WAIT_S == 30
    assert DEFAULT_REPEAT_INTERVAL_S == 14400
    assert GROUP_WAIT_S_BOUNDS[0] <= DEFAULT_GROUP_WAIT_S <= GROUP_WAIT_S_BOUNDS[1]
    low, high = REPEAT_INTERVAL_S_BOUNDS
    assert low <= DEFAULT_REPEAT_INTERVAL_S <= high


def test_an_absent_cadence_knob_uses_the_named_default(clock, metrics):
    # The minimal document declares neither knob nor a rate limit.
    doc = ServeDocument.from_obj(minimal_document())
    router = AlertRouter(doc.alerting, {"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert router.group_wait_s == DEFAULT_GROUP_WAIT_S
    assert router.repeat_interval_s == DEFAULT_REPEAT_INTERVAL_S


def test_the_declared_knobs_win_over_the_defaults(clock, metrics):
    doc = serve_doc(group_wait_s=0, repeat_interval_s=60)
    router = AlertRouter(doc.alerting, {"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert router.group_wait_s == 0
    assert router.repeat_interval_s == 60


def test_the_router_declares_both_counter_names_at_construction(clock, metrics):
    # §5.11.1: a swallowed failure that is not counted is invisible, so the
    # counters must exist before the first failure, not after it.
    a_router({"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert FAILURES in metrics.snapshot()
    assert SUPPRESSED in metrics.snapshot()


def test_construction_starts_no_thread(clock, metrics):
    before = threading.active_count()
    a_router({"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert threading.active_count() == before


# --------------------------------------------------------------------------
# AlertRouter — enqueue, group wait, dedup, repeat
# --------------------------------------------------------------------------


def test_raise_alert_enqueues_and_delivers_nothing(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    assert router.raise_alert(an_alert()) is True
    assert ops.sent == []


def test_raise_alert_refuses_something_that_is_not_an_alert(clock, metrics):
    router = a_router({"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    with pytest.raises(ProductionError):
        router.raise_alert({"fingerprint": "not-a-record"})


def test_group_wait_holds_the_first_notification_until_it_elapses(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=30)
    router.raise_alert(an_alert())
    assert router.process(NOW_MS) == ()
    assert router.process(NOW_MS + 29_999) == ()
    notifications = router.process(NOW_MS + 30_000)
    assert len(notifications) == 1
    assert isinstance(notifications[0], Notification)
    assert notifications[0].alert.fingerprint == "feed-stale"
    assert len(ops.sent) == 1


def test_a_zero_group_wait_delivers_at_the_next_process(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    assert len(router.process(NOW_MS)) == 1


def test_repeats_inside_the_group_wait_collapse_to_one_notification(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=30)
    for offset in (0, 1_000, 2_000):
        clock.set(NOW_MS + offset)
        router.raise_alert(an_alert(at_ms=NOW_MS + offset, summary=f"degraded at {offset}"))
    clock.set(NOW_MS + 30_000)
    notifications = router.process(NOW_MS + 30_000)
    assert len(notifications) == 1
    assert len(ops.sent) == 1


def test_two_fingerprints_are_two_notifications(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert(fingerprint="feed-stale"))
    router.raise_alert(an_alert(fingerprint="venue-down"))
    notifications = router.process(NOW_MS)
    assert sorted(n.alert.fingerprint for n in notifications) == ["feed-stale", "venue-down"]


def test_a_repeat_while_firing_waits_for_the_repeat_interval(clock, metrics):
    ops = MemorySink(None)
    router = a_router(
        {"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0, repeat_interval_s=60
    )
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    assert len(ops.sent) == 1

    clock.set(NOW_MS + 59_000)
    router.raise_alert(an_alert(at_ms=NOW_MS + 59_000))
    assert router.process(NOW_MS + 59_000) == ()
    assert len(ops.sent) == 1

    clock.set(NOW_MS + 60_000)
    router.raise_alert(an_alert(at_ms=NOW_MS + 60_000))
    assert len(router.process(NOW_MS + 60_000)) == 1
    assert len(ops.sent) == 2


def test_a_firing_fingerprint_is_visible_before_it_resolves(clock, metrics):
    router = a_router({"ops": MemorySink(None)}, clock=clock, metrics=metrics, group_wait_s=0)
    assert router.firing == ()
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    assert router.firing == ("feed-stale",)


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------


def test_per_severity_routes_choose_the_sinks(clock, metrics):
    pager, chat = MemorySink(None), MemorySink(None)
    doc = serve_doc(
        sinks={"pager": {"uses": "memory"}, "chat": {"uses": "memory"}},
        routes=[
            {"severity": "critical", "sinks": ["pager", "chat"]},
            {"severity": "warning", "sinks": ["chat"]},
        ],
        group_wait_s=0,
    )
    router = a_router({"pager": pager, "chat": chat}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert(fingerprint="w", severity="warning"))
    router.raise_alert(an_alert(fingerprint="c", severity="critical"))
    notifications = {n.alert.fingerprint: n for n in router.process(NOW_MS)}
    assert sorted(notifications["w"].outcomes) == ["chat"]
    assert sorted(notifications["c"].outcomes) == ["chat", "pager"]
    assert [a.fingerprint for a in pager.sent] == ["c"]
    assert sorted(a.fingerprint for a in chat.sent) == ["c", "w"]


def test_an_alert_whose_severity_has_no_route_reaches_no_sink(clock, metrics):
    # The §4.1 illustration routes only `critical` and `warning`; `info`
    # and `error` reach nobody. Nothing SUPPRESSED it — there is simply
    # nowhere configured to send it — so it is still a notification.
    ops = MemorySink(None)
    doc = serve_doc(routes=[{"severity": "critical", "sinks": ["ops"]}], group_wait_s=0)
    router = a_router({"ops": ops}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert(severity="info"))
    (notification,) = router.process(NOW_MS)
    assert notification.outcomes == {}
    assert ops.sent == []


def test_two_routes_for_one_severity_union_their_sinks(clock, metrics):
    a, b = MemorySink(None), MemorySink(None)
    doc = serve_doc(
        sinks={"a": {"uses": "memory"}, "b": {"uses": "memory"}},
        routes=[
            {"severity": "critical", "sinks": ["a"]},
            {"severity": "critical", "sinks": ["b"]},
        ],
        group_wait_s=0,
    )
    router = a_router({"a": a, "b": b}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert(severity="critical"))
    (notification,) = router.process(NOW_MS)
    assert sorted(notification.outcomes) == ["a", "b"]


# --------------------------------------------------------------------------
# the token bucket
# --------------------------------------------------------------------------


def test_the_token_bucket_withholds_beyond_the_burst(clock, metrics):
    ops = MemorySink(None)
    router = a_router(
        {"ops": ops},
        clock=clock,
        metrics=metrics,
        group_wait_s=0,
        rate_limit={"max_per_hour": 1, "burst": 2},
    )
    for index in range(4):
        clock.set(NOW_MS + index)
        router.raise_alert(an_alert(fingerprint=f"f{index}", at_ms=NOW_MS + index))
        router.process(NOW_MS + index)
    assert len(ops.sent) == 2
    assert counts(metrics, SUPPRESSED).get("rate_limit") == 2


def test_the_token_bucket_refills_at_max_per_hour(clock, metrics):
    ops = MemorySink(None)
    router = a_router(
        {"ops": ops},
        clock=clock,
        metrics=metrics,
        group_wait_s=0,
        rate_limit={"max_per_hour": 2, "burst": 1},
    )
    router.raise_alert(an_alert(fingerprint="f0"))
    router.process(NOW_MS)
    clock.set(NOW_MS + 1)
    router.raise_alert(an_alert(fingerprint="f1", at_ms=NOW_MS + 1))
    router.process(NOW_MS + 1)
    assert len(ops.sent) == 1

    # 2 per hour == one token per 1_800_000 ms.
    later = NOW_MS + 1_800_000
    clock.set(later)
    router.raise_alert(an_alert(fingerprint="f2", at_ms=later))
    router.process(later)
    assert len(ops.sent) == 2


def test_critical_bypasses_the_rate_limit(clock, metrics):
    ops = MemorySink(None)
    router = a_router(
        {"ops": ops},
        clock=clock,
        metrics=metrics,
        group_wait_s=0,
        rate_limit={"max_per_hour": 1, "burst": 1},
    )
    for index in range(4):
        clock.set(NOW_MS + index)
        router.raise_alert(
            an_alert(fingerprint=f"c{index}", severity="critical", at_ms=NOW_MS + index)
        )
        router.process(NOW_MS + index)
    assert len(ops.sent) == 4


def test_critical_does_not_bypass_dedup(clock, metrics):
    # §5.11 is explicit: `critical` bypasses the LIMIT, not the dedup.
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=30)
    for offset in (0, 1, 2, 3):
        clock.set(NOW_MS + offset)
        router.raise_alert(an_alert(severity="critical", at_ms=NOW_MS + offset))
    clock.set(NOW_MS + 30_000)
    router.process(NOW_MS + 30_000)
    assert len(ops.sent) == 1


def test_no_rate_limit_declared_means_no_limiting(clock, metrics):
    ops = MemorySink(None)
    doc = ServeDocument.from_obj(minimal_document())
    router = AlertRouter(
        doc.alerting, {"ops": ops}, clock=clock, metrics=metrics
    )
    assert router.group_wait_s == DEFAULT_GROUP_WAIT_S
    for index in range(20):
        at = NOW_MS + index * 60_000
        clock.set(at)
        router.raise_alert(an_alert(fingerprint=f"f{index}", severity="critical", at_ms=at))
        router.process(at + DEFAULT_GROUP_WAIT_S * 1000)
    assert len(ops.sent) == 20


# --------------------------------------------------------------------------
# overflow, sink failures, and the accounting that makes them visible
# --------------------------------------------------------------------------


def test_the_queue_is_bounded_by_a_named_default(clock, metrics):
    assert DEFAULT_QUEUE_MAXSIZE > 0
    router = a_router({"ops": MemorySink(None)}, clock=clock, metrics=metrics)
    assert router.maxsize == DEFAULT_QUEUE_MAXSIZE


def test_queue_overflow_is_swallowed_counted_and_never_raises(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, maxsize=2, group_wait_s=0)
    accepted = [router.raise_alert(an_alert(fingerprint=f"f{i}")) for i in range(5)]
    assert accepted == [True, True, False, False, False]
    assert counts(metrics, SUPPRESSED).get("queue_full") == 3
    assert len(router.process(NOW_MS)) == 2


def test_a_sink_that_raises_never_reaches_the_caller(clock, metrics):
    ops = CountingSink(None, error=RuntimeError("sink exploded"))
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    (notification,) = router.process(NOW_MS)
    assert notification.outcomes["ops"].ok is False
    assert sum(counts(metrics, FAILURES).values()) == 1


def test_a_failing_sink_does_not_stop_the_others(clock, metrics):
    # The whole point of swallowing: one broken destination must not cost
    # the operator the destination that works.
    bad = CountingSink(None, error=RuntimeError("boom"))
    good = MemorySink(None)
    doc = serve_doc(
        sinks={"bad": {"uses": "memory"}, "good": {"uses": "memory"}},
        routes=[{"severity": s, "sinks": ["bad", "good"]} for s in vocab.SEVERITIES],
        group_wait_s=0,
    )
    router = a_router({"bad": bad, "good": good}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert())
    (notification,) = router.process(NOW_MS)
    assert notification.outcomes["bad"].ok is False
    assert notification.outcomes["good"].ok is True
    assert len(good.sent) == 1


def test_an_ok_false_outcome_counts_as_a_failure_too(clock, metrics):
    ops = CountingSink(None, answer=False)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    assert sum(counts(metrics, FAILURES).values()) == 1


def test_a_core_sink_failure_is_counted_under_its_kind(clock, metrics):
    # `alert_sink_failures_total{sink}` takes the KIND, never the
    # document's instance name (`vocab`'s note on the label).
    doc = serve_doc(
        sinks={"ops": {"uses": "webhook"}},
        routes=[{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES],
        endpoints={"ops": webhook_endpoint()},
        group_wait_s=0,
    )
    sink = a_webhook(transport=FakeTransport(answer=(503, {}, b"")))
    router = a_router({"ops": sink}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    assert counts(metrics, FAILURES).get("webhook") == 1


def test_every_suppression_reason_is_a_member_of_the_vocabulary(clock, metrics):
    router = a_router(
        {"ops": MemorySink(None)},
        clock=clock,
        metrics=metrics,
        maxsize=1,
        group_wait_s=30,
        rate_limit={"max_per_hour": 1, "burst": 1},
    )
    for index in range(8):
        at = NOW_MS + index * 5_000
        clock.set(at)
        router.raise_alert(an_alert(fingerprint=f"f{index % 3}", at_ms=at))
        router.process(at)
    counted = counts(metrics, SUPPRESSED)
    # A run this tight MUST have withheld something; a silent registry
    # would make the assertion below true by holding nothing at all.
    assert sum(counted.values()) > 0
    for why in counted:
        assert why in vocab.ALERT_SUPPRESSIONS


def test_every_enqueued_alert_is_delivered_once_or_counted_once(clock, metrics):
    # The conservation law that makes "swallowed" auditable: a router that
    # loses an alert without counting it is exactly the failure §5.11.1
    # exists to make impossible, and no single mechanism test can catch it.
    ops = MemorySink(None)
    router = a_router(
        {"ops": ops},
        clock=clock,
        metrics=metrics,
        maxsize=4,
        group_wait_s=10,
        repeat_interval_s=60,
        rate_limit={"max_per_hour": 3, "burst": 2},
    )
    accepted = 0
    delivered = 0
    for index in range(30):
        at = NOW_MS + index * 3_000
        clock.set(at)
        alert = an_alert(
            fingerprint=f"f{index % 4}",
            severity=vocab.SEVERITIES[index % len(vocab.SEVERITIES)],
            at_ms=at,
        )
        accepted += 1 if router.raise_alert(alert) else 0
        delivered += len(router.process(at))
    # Flush anything still inside its group wait.
    end = NOW_MS + 30 * 3_000 + 10_000
    clock.set(end)
    delivered += len(router.process(end))
    assert accepted > 0 and delivered > 0
    assert delivered + sum(counts(metrics, SUPPRESSED).values()) == accepted


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def test_resolved_is_emitted_on_recovery(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    clock.set(NOW_MS + 5_000)
    assert router.resolve("feed-stale") is True
    notifications = router.process(NOW_MS + 5_000)
    assert [n.alert.status for n in notifications] == ["resolved"]
    assert [a.status for a in ops.sent] == ["firing", "resolved"]
    assert ops.sent[-1].fingerprint == "feed-stale"
    assert ops.sent[-1].severity == ops.sent[0].severity
    assert router.firing == ()


def test_resolving_a_fingerprint_that_never_fired_does_nothing(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    assert router.resolve("never-fired") is False
    assert router.process(NOW_MS) == ()
    assert ops.sent == []


def test_after_a_resolve_the_same_fingerprint_fires_again(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    router.resolve("feed-stale")
    router.process(NOW_MS)
    clock.set(NOW_MS + 1_000)
    router.raise_alert(an_alert(at_ms=NOW_MS + 1_000))
    router.process(NOW_MS + 1_000)
    assert [a.status for a in ops.sent] == ["firing", "resolved", "firing"]


# --------------------------------------------------------------------------
# the §6 `alert` record
# --------------------------------------------------------------------------


def test_the_alert_record_carries_the_alert_and_its_per_sink_outcomes(clock, metrics):
    ledger = FakeLedger()
    doc = serve_doc(
        sinks={"ops": {"uses": "webhook"}},
        routes=[{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES],
        endpoints={"ops": webhook_endpoint()},
        group_wait_s=0,
    )
    router = a_router(
        {"ops": a_webhook()}, doc=doc, clock=clock, metrics=metrics, ledger=ledger
    )
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    (record,) = ledger.of_kind("alert")
    body = record["body"]
    assert body["fingerprint"] == "feed-stale"
    assert body["status"] == "firing"
    assert body["severity"] == "warning"
    assert set(body["sinks"]) == {"ops"}
    assert set(body["sinks"]["ops"]) == {"ok", "detail"}
    assert body["sinks"]["ops"]["ok"] is True


def test_the_alert_record_body_never_carries_a_url_or_a_secret(clock, metrics):
    # §6 says "the `Alert` record + per-sink outcomes"; the endpoint is an
    # EXCLUDED section precisely because where an alert goes is a
    # credential, and the ledger outlives the process.
    register_secret("ledger-leak-token")
    ledger = FakeLedger()
    doc = serve_doc(
        sinks={"ops": {"uses": "webhook"}},
        routes=[{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES],
        endpoints={"ops": webhook_endpoint()},
        group_wait_s=0,
    )
    transport = FakeTransport(error=OSError(f"{WEBHOOK_URL} rejected ledger-leak-token"))
    router = a_router(
        {"ops": a_webhook(transport=transport)},
        doc=doc,
        clock=clock,
        metrics=metrics,
        ledger=ledger,
    )
    router.raise_alert(an_alert(summary="posting to ledger-leak-token failed"))
    router.process(NOW_MS)
    (record,) = ledger.of_kind("alert")
    for text in strings_in(record):
        assert WEBHOOK_URL not in text
        assert "ledger-leak-token" not in text
        assert URL_ENV not in text


def test_every_notification_gets_its_own_kind_qualified_record_id(clock, metrics):
    # R9: a record `id` is unique across the SERIES and kind-qualified, so
    # a re-notification cannot collide with the first one.
    ledger = FakeLedger()
    router = a_router(
        {"ops": MemorySink(None)},
        clock=clock,
        metrics=metrics,
        ledger=ledger,
        group_wait_s=0,
        repeat_interval_s=60,
    )
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    clock.set(NOW_MS + 60_000)
    router.raise_alert(an_alert(at_ms=NOW_MS + 60_000))
    router.process(NOW_MS + 60_000)
    router.resolve("feed-stale")
    router.process(NOW_MS + 60_000)
    ids = [r["id"] for r in ledger.of_kind("alert")]
    assert len(ids) == 3
    assert len(set(ids)) == 3
    assert all(record_id.startswith("alert:") for record_id in ids)


def test_the_ledger_is_optional_and_delivery_is_unchanged_without_one(clock, metrics):
    # `ledger=None` is the default: `validate`, `plan` and the tests build
    # a router long before a series exists to record into.
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=0)
    router.raise_alert(an_alert())
    assert len(router.process(NOW_MS)) == 1
    assert len(ops.sent) == 1


# --------------------------------------------------------------------------
# the bounded stuck sink — D17/D23's one-thread promise
# --------------------------------------------------------------------------


def test_a_hanging_sink_is_bounded_by_its_timeout(clock, metrics, hanging):
    doc = serve_doc(
        sinks={"stuck": {"uses": "tests.production.test_alerts:HangingSink"}},
        routes=[{"severity": s, "sinks": ["stuck"]} for s in vocab.SEVERITIES],
        endpoints={"stuck": webhook_endpoint(timeout_s=0.05)},
        group_wait_s=0,
    )
    router = a_router({"stuck": hanging}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert())
    started = time.monotonic()
    (notification,) = router.process(NOW_MS)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert notification.outcomes["stuck"].ok is False
    assert sum(counts(metrics, FAILURES).values()) == 1


def test_a_timed_out_sink_is_disabled_and_never_called_again(clock, metrics, hanging):
    doc = serve_doc(
        sinks={"stuck": {"uses": "tests.production.test_alerts:HangingSink"}},
        routes=[{"severity": s, "sinks": ["stuck"]} for s in vocab.SEVERITIES],
        endpoints={"stuck": webhook_endpoint(timeout_s=0.05)},
        group_wait_s=0,
    )
    router = a_router({"stuck": hanging}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert(fingerprint="one"))
    router.process(NOW_MS)
    assert hanging.entered.is_set()
    assert router.disabled_sinks == ("stuck",)

    threads_after_first = threading.active_count()
    for index in range(3):
        at = NOW_MS + (index + 1) * 1_000
        clock.set(at)
        router.raise_alert(an_alert(fingerprint=f"more{index}", at_ms=at))
        router.process(at)
    # No replacement worker for the one that is still stuck (D23).
    assert threading.active_count() <= threads_after_first
    assert hanging.calls == 1


def test_a_disabled_sink_does_not_disable_the_healthy_one(clock, metrics, hanging):
    good = MemorySink(None)
    doc = serve_doc(
        sinks={
            "stuck": {"uses": "tests.production.test_alerts:HangingSink"},
            "good": {"uses": "memory"},
        },
        routes=[{"severity": s, "sinks": ["stuck", "good"]} for s in vocab.SEVERITIES],
        endpoints={"stuck": webhook_endpoint(timeout_s=0.05)},
        group_wait_s=0,
    )
    router = a_router({"stuck": hanging, "good": good}, doc=doc, clock=clock, metrics=metrics)
    for index in range(3):
        at = NOW_MS + index * 1_000
        clock.set(at)
        router.raise_alert(an_alert(fingerprint=f"f{index}", at_ms=at))
        router.process(at)
    assert len(good.sent) == 3
    assert router.disabled_sinks == ("stuck",)


def test_close_returns_even_with_a_sink_still_stuck(clock, metrics, hanging):
    doc = serve_doc(
        sinks={"stuck": {"uses": "tests.production.test_alerts:HangingSink"}},
        routes=[{"severity": s, "sinks": ["stuck"]} for s in vocab.SEVERITIES],
        endpoints={"stuck": webhook_endpoint(timeout_s=0.05)},
        group_wait_s=0,
    )
    router = a_router({"stuck": hanging}, doc=doc, clock=clock, metrics=metrics)
    router.raise_alert(an_alert())
    router.process(NOW_MS)
    started = time.monotonic()
    router.close()
    router.close()
    assert time.monotonic() - started < 2.0


# --------------------------------------------------------------------------
# the worker thread — the thin wrapper around `process`
# --------------------------------------------------------------------------


def test_start_runs_one_worker_that_delivers_what_was_enqueued(metrics):
    # The only test that leans on the thread: the guarantee IS the thread.
    delivered = threading.Event()

    class Watched(CountingSink):
        def send(self, alert):
            """Deliver, then let the test stop waiting."""
            outcome = super().send(alert)
            delivered.set()
            return outcome

    ops = Watched(None)
    doc = serve_doc(
        sinks={"ops": {"uses": "tests.production.test_alerts:CountingSink"}},
        routes=[{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES],
        group_wait_s=0,
    )
    router = AlertRouter(
        doc.alerting, {"ops": ops}, clock=TestClock(start_ms=NOW_MS), metrics=metrics
    )
    before = threading.active_count()
    router.start()
    try:
        router.raise_alert(an_alert())
        assert delivered.wait(2.0) is True
        assert threading.active_count() >= before + 1
    finally:
        router.close()
    assert len(ops.sent) == 1


def test_close_closes_every_sink_and_is_idempotent(clock, metrics):
    ops = CountingSink(None)
    doc = serve_doc(
        sinks={"ops": {"uses": "tests.production.test_alerts:CountingSink"}},
        routes=[{"severity": s, "sinks": ["ops"]} for s in vocab.SEVERITIES],
    )
    router = AlertRouter(doc.alerting, {"ops": ops}, clock=clock, metrics=metrics)
    router.close()
    router.close()
    assert ops.closed >= 1


def test_drain_processes_at_the_routers_own_clock(clock, metrics):
    ops = MemorySink(None)
    router = a_router({"ops": ops}, clock=clock, metrics=metrics, group_wait_s=30)
    router.raise_alert(an_alert())
    assert router.drain() == ()
    clock.set(NOW_MS + 30_000)
    assert len(router.drain()) == 1
    assert len(ops.sent) == 1

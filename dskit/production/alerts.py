"""Alert sinks and the router that can never stop a tick (plan §5.11, D17).

An alert is how an operator learns a symptom, and an alert path that can
BLOCK is a worse hazard than the symptom it reports. Four rules follow,
and every class here serves one of them:

* **Construction validates configuration only.** A sink refuses an
  unknown param, a network sink refuses to exist without its transport,
  and a sink whose ``url_env`` names a variable the process cannot
  resolve refuses — but nothing opens a socket or reads a credential's
  VALUE at construction. Reachability is a health probe's job (D18).
* **The URL is the credential.** ``alert_endpoints.<sink>.url_env`` is
  the env-var NAME; the value is read at send time, never stored, and
  never written into the §6 ``alert`` record, whose body carries the
  alert and the per-sink outcomes and nothing else. Every body a sink
  renders and every record the router appends passes through
  :func:`redact_strings`.
* **Nothing a sink does reaches the caller.** A sink that raises,
  answers a non-2xx or never returns is swallowed and COUNTED —
  ``alert_sink_failures_total{sink}`` and ``alerts_suppressed_total{why}``
  are the only evidence (§5.11.1). A core kind sends inline, bounded by
  its transport's real socket deadline; a custom sink (a class reference
  outside :data:`ALERT_SINK_KINDS`) sends on its own
  :class:`SupervisedWorker`, and a call that outlives ``timeout_s``
  disables that sink for the life of the router without spawning a
  replacement, so a permanently stuck call costs one daemon thread (D23).
* **Fewer notifications than alerts, by design.** Per fingerprint, the
  first alert waits ``group_wait_s`` and collects its repeats into one
  notification; while the fingerprint is firing, a repeat inside
  ``repeat_interval_s`` is dropped and one after it is re-notified; a
  token bucket of ``rate_limit{max_per_hour, burst}`` withholds beyond
  the burst (``critical`` bypasses the bucket, never the dedup); and
  ``resolve(fingerprint)`` emits ``status: resolved`` at once, bypassing
  both. Every alert the router accepts is delivered exactly once or
  counted exactly once under one ``vocab.ALERT_SUPPRESSIONS`` reason:
  ``queue_full`` (``put_nowait`` overflow), ``group_wait`` (superseded
  before its first notification), ``repeat_interval`` (firing, too soon),
  ``dedup`` (superseded while a repeat was already due) or
  ``rate_limit``.

The router is driven synchronously: ``process(now_ms)`` drains the
bounded queue and delivers what is due, and the loop calls it once per
tick after ``observe``, outside every barrier — it is the ONLY path that
appends ``alert`` records (ruling R19). ``start()`` runs the same driver
on one daemon worker for a process that has no loop to drive it, and
therefore REFUSES when a ledger is attached: no auxiliary thread appends.
"""

import json
import math
import queue
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from urllib.parse import parse_qs, unquote, urlsplit

from dskit.pipeline.node import check_int_param, class_ref
from dskit.production import vocab
from dskit.production.base import (
    ProductionError,
    Registry,
    _check_str,
    reject_unknown_params,
)
from dskit.production.document import GROUP_WAIT_S_BOUNDS, REPEAT_INTERVAL_S_BOUNDS
from dskit.production.records import Alert
from dskit.production.redact import get_logger, redact

__all__ = [
    "ALERT_SINK_KINDS",
    "AlertRouter",
    "AlertSink",
    "CallResult",
    "DEFAULT_GROUP_WAIT_S",
    "DEFAULT_MAIL_SENDER",
    "DEFAULT_QUEUE_MAXSIZE",
    "DEFAULT_REPEAT_INTERVAL_S",
    "DEFAULT_SINK_TIMEOUT_S",
    "DEFAULT_TEMPLATE",
    "EmailSink",
    "LogSink",
    "MemorySink",
    "Notification",
    "SinkOutcome",
    "SupervisedWorker",
    "TEMPLATES",
    "WORKER_POLL_S",
    "WebhookSink",
    "check_timeout_s",
    "deadline",
    "redact_strings",
]

#: ``alerting.group_wait_s`` when the document is silent (§5.11); the
#: bounds are ``document.py``'s.
DEFAULT_GROUP_WAIT_S = 30
#: ``alerting.repeat_interval_s`` when the document is silent (§5.11).
DEFAULT_REPEAT_INTERVAL_S = 14400
#: How many accepted alerts may wait for ``process`` before ``raise_alert``
#: starts refusing (counted as ``queue_full``).
DEFAULT_QUEUE_MAXSIZE = 1024
#: ``alert_endpoints.<sink>.timeout_s`` when absent: the deadline of one
#: send, on both halves of the transport timeout.
DEFAULT_SINK_TIMEOUT_S = 5.0
#: ``alert_endpoints.<sink>.template`` when absent.
DEFAULT_TEMPLATE = "json"
#: The ``From`` header of an ``email`` sink when its params name none.
DEFAULT_MAIL_SENDER = "dskit-production@localhost"
#: How often the ``start()`` worker re-runs ``process`` when nothing wakes it.
WORKER_POLL_S = 0.1

#: The §5.11.1 counters, spelled once from the closed table.
_FAILURES = "alert_sink_failures_total"
_SUPPRESSED = "alerts_suppressed_total"
_NOTES = ("notes",)
_MS_PER_S = 1000
_MS_PER_HOUR = 3_600_000
_JSON_HEADERS = {"Content-Type": "application/json"}
#: The severity whose notifications the token bucket never withholds.
_BYPASSES_RATE_LIMIT = "critical"
_FIRING, _RESOLVED = vocab.ALERT_STATUSES

_log = get_logger("alerts")


# ---------------------------------------------------------------------------
# Small shared rules — one owner each, imported by health.py
# ---------------------------------------------------------------------------


def check_timeout_s(problems, name, value):
    """Append a problem unless ``value`` is a positive finite number.

    Parameters
    ----------
    problems : list of str
        The accumulator, appended to in place.
    name : str
        The knob's name, for the message.
    value : object
        The declared deadline in seconds; ``bool`` is refused.

    Returns
    -------
    None
        Problems are appended; the caller raises once.
    """
    ok = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
    if not ok:
        problems.append(f"{name} must be a positive number of seconds, got {value!r}")


def deadline(timeout_s):
    """Return the ``Transport.send`` timeout that bounds both halves by ``timeout_s``.

    Parameters
    ----------
    timeout_s : int or float
        One configured deadline.

    Returns
    -------
    dict
        ``{"connect_s": timeout_s, "read_s": timeout_s}`` (§5.12's shape).
    """
    return {"connect_s": timeout_s, "read_s": timeout_s}


def redact_strings(obj):
    """Return a copy of a JSON-shaped object with every string masked.

    Parameters
    ----------
    obj : dict or list or tuple or scalar
        The object; dict keys are kept, values and list items are walked.

    Returns
    -------
    object
        The same shape with :func:`dskit.production.redact.redact` applied
        to every ``str`` (tuples come back as lists).
    """
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {key: redact_strings(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_strings(item) for item in obj]
    return obj


def _failure_detail(exc):
    """Render an exception as a masked one-line detail."""
    return redact(f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SinkOutcome:
    """What one sink made of one alert.

    Parameters
    ----------
    ok : bool
        Whether the destination accepted the alert.
    detail : str
        A short masked reason — an HTTP status, an exception, ``"logged"``.
        Never a URL.

    Examples
    --------
    ::

        outcome = SinkOutcome(ok=False, detail="HTTP 503")
        outcome.to_obj()   # {'ok': False, 'detail': 'HTTP 503'}
    """

    ok: bool
    detail: str

    def to_obj(self):
        """Return the outcome as a JSON-ready dict.

        Returns
        -------
        dict
            ``{"ok": bool, "detail": str}``.
        """
        return {"ok": bool(self.ok), "detail": self.detail}


@dataclass(frozen=True)
class Notification:
    """One delivery the router made: the alert and every routed sink's outcome.

    Parameters
    ----------
    alert : records.Alert
        The alert delivered — the latest of its fingerprint's group.
    outcomes : dict of str to SinkOutcome
        Keyed by the document's sink name; empty when no route names the
        alert's severity.

    Examples
    --------
    ::

        notification = Notification(alert, {"ops": SinkOutcome(True, "HTTP 200")})
        notification.outcomes["ops"].ok   # True
    """

    alert: Alert
    outcomes: dict


@dataclass(frozen=True)
class CallResult:
    """What a :class:`SupervisedWorker` call came to.

    Parameters
    ----------
    value : object
        What the call returned, when it did.
    error : BaseException or None
        What it raised, when it did.
    timed_out : bool
        Whether the deadline passed first — or the worker was still busy
        with an earlier call that never came back.

    Examples
    --------
    ::

        result = SupervisedWorker("ops").call(lambda: 42, timeout_s=1.0)
        (result.value, result.error, result.timed_out)   # (42, None, False)
    """

    value: object = None
    error: BaseException = None
    timed_out: bool = False


class SupervisedWorker:
    """One daemon thread for one call site; a stuck call is never replaced.

    ``call(fn, timeout_s)`` runs ``fn`` on the worker and waits at most
    ``timeout_s``. If the deadline passes, the caller gets
    ``timed_out=True`` at once and the worker stays BUSY until ``fn``
    finally returns — every call in between is answered ``timed_out``
    immediately, without a second thread. This is D17's and D18's
    supervisor: a permanently stuck sink or probe costs one thread, once.

    Parameters
    ----------
    name : str
        The site the worker serves; names the thread.

    Examples
    --------
    ::

        worker = SupervisedWorker("ops")
        worker.call(lambda: "sent", timeout_s=0.5).value   # 'sent'
        worker.busy   # False
        worker.stop()
    """

    def __init__(self, name):
        problems = []
        _check_str(problems, "name", name)
        if problems:
            raise ProductionError(problems)
        self._name = name
        self._guard = threading.Lock()
        self._jobs = queue.Queue()
        self._thread = None
        self._busy = False

    @property
    def busy(self):
        """Return whether an earlier call has not come back yet."""
        with self._guard:
            return self._busy

    def call(self, fn, timeout_s):
        """Run ``fn()`` on the worker and wait at most ``timeout_s`` for it.

        Parameters
        ----------
        fn : callable
            A zero-argument callable.
        timeout_s : int or float
            The deadline in seconds.

        Returns
        -------
        CallResult
            The value or the exception, or ``timed_out`` when the deadline
            passed or the worker was still busy with an earlier call.
        """
        done = threading.Event()
        box = {}
        with self._guard:
            if self._busy:
                return CallResult(timed_out=True)
            self._busy = True
            self._jobs.put((fn, done, box))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run, name=f"dskit-production-{self._name}", daemon=True
                )
                self._thread.start()
        if not done.wait(timeout_s):
            return CallResult(timed_out=True)
        return CallResult(value=box.get("value"), error=box.get("error"))

    def stop(self):
        """Let the thread exit after its current call; never joins a stuck one.

        Returns
        -------
        None
            Idempotent; a worker that never started has nothing to stop.
        """
        with self._guard:
            if self._thread is not None:
                self._jobs.put(None)

    def _run(self):
        """Serve jobs until the ``None`` sentinel; free the worker after each."""
        while True:
            job = self._jobs.get()
            if job is None:
                return
            fn, done, box = job
            try:
                box["value"] = fn()
            except BaseException as exc:  # a sink may raise anything; nothing escapes
                box["error"] = exc
            finally:
                with self._guard:
                    self._busy = False
                done.set()


# ---------------------------------------------------------------------------
# Templates — how a webhook body is shaped
# ---------------------------------------------------------------------------


def _json_template(obj):
    """The whole alert, as is."""
    return obj


def _slack_template(obj):
    """A Slack incoming-webhook body: one ``text`` line carrying the alert."""
    labels = " ".join(f"{key}={value}" for key, value in sorted(obj["labels"].items()))
    text = (
        f"[{obj['severity']}] {obj['status']}: {obj['summary']} "
        f"(fingerprint {obj['fingerprint']}, source {obj['source']}"
        f"{', ' + labels if labels else ''})"
    )
    return {"text": text}


#: ``alert_endpoints.<sink>.template`` -> body renderer over the masked
#: alert dict. A template outside this table refuses at construction.
TEMPLATES = {"json": _json_template, "slack": _slack_template}


# ---------------------------------------------------------------------------
# The sink seam
# ---------------------------------------------------------------------------


class AlertSink(ABC):
    """The delivery seam: ``send(alert) -> SinkOutcome``; construction validates only.

    Every sink is built as ``cls(params, *, endpoint, transport, clock,
    secrets)`` — ``params`` is the ``alerting.sinks.<name>.params`` block
    (default-deny over ``_PARAMS`` plus ``notes``), the four collaborators
    arrive by name from ``compose.py`` and default to absent, because
    ``log`` and ``memory`` need none of them. ``endpoint`` is the
    ``document.alert_endpoints.<name>`` view (``url_env``, ``template``,
    ``timeout_s``); a subclass names the collaborators it cannot work
    without in ``_REQUIRES`` and the base refuses their absence. When both
    an endpoint and ``secrets`` are given, ``url_env`` must be resolvable —
    checked with ``in``, never by reading the value.

    Parameters
    ----------
    params : dict or None
        The params block; ``None`` means ``{}``.
    endpoint : object, optional
        The endpoint view: attributes ``url_env`` (str), ``template``
        (str or None) and ``timeout_s`` (number or None).
    transport : resilience.Transport, optional
        The socket seam a network sink posts through.
    clock : clock.Clock, optional
        Injected time, for a subclass that stamps.
    secrets : dskit.pipeline.env.Secrets, optional
        The resolver ``url_env`` is looked up in at send time.

    Attributes
    ----------
    KIND : str or None
        The registry name a core kind reports; ``None`` on the ABC, so a
        custom sink reports its class reference.

    Examples
    --------
    A child sink that hands alerts to its own pager client::

        class PagerSink(AlertSink):
            _PARAMS = ("service",)

            def send(self, alert):
                return SinkOutcome(ok=True, detail="paged")

        sink = PagerSink({"service": "trading"})
        sink.kind        # 'tests...:PagerSink' — its class reference
        sink.timeout_s   # 5.0 (DEFAULT_SINK_TIMEOUT_S)
    """

    _PARAMS = ()
    _REQUIRES = ()
    KIND = None

    def __init__(self, params, *, endpoint=None, transport=None, clock=None, secrets=None):
        params = {} if params is None else params
        problems = self.validate_params(params)
        given = {"endpoint": endpoint, "transport": transport, "clock": clock, "secrets": secrets}
        for name in self._REQUIRES:
            if given[name] is None:
                problems.append(f"a {self.kind} sink requires the {name} collaborator")
        url_env = None if endpoint is None else getattr(endpoint, "url_env", None)
        if endpoint is not None:
            self._check_endpoint(problems, endpoint, url_env)
        if secrets is not None and isinstance(url_env, str) and url_env not in secrets:
            problems.append(
                f"url_env {url_env!r} cannot be resolved — list it in env.require"
            )
        if problems:
            raise ProductionError(problems)
        self._params = dict(params)
        self._endpoint = endpoint
        self._transport = transport
        self._clock = clock
        self._secrets = secrets
        self._url_env = url_env
        timeout_s = None if endpoint is None else getattr(endpoint, "timeout_s", None)
        self._timeout_s = DEFAULT_SINK_TIMEOUT_S if timeout_s is None else timeout_s
        template = None if endpoint is None else getattr(endpoint, "template", None)
        self._template = DEFAULT_TEMPLATE if template is None else template

    @staticmethod
    def _check_endpoint(problems, endpoint, url_env):
        """Refuse an endpoint whose three keys are malformed."""
        _check_str(problems, "endpoint.url_env", url_env)
        timeout_s = getattr(endpoint, "timeout_s", None)
        if timeout_s is not None:
            check_timeout_s(problems, "endpoint.timeout_s", timeout_s)
        template = getattr(endpoint, "template", None)
        if template is not None and template not in TEMPLATES:
            problems.append(
                f"endpoint.template {template!r} is not one of {sorted(TEMPLATES)}"
            )

    @classmethod
    def validate_params(cls, params):
        """Return every problem with ``params``; empty when acceptable.

        Parameters
        ----------
        params : dict
            The params block as written in the document.

        Returns
        -------
        list of str
            Default-deny over ``_PARAMS`` plus ``notes``, then the
            subclass ``_check`` hook.
        """
        if not isinstance(params, dict):
            return [f"params must be an object (dict), got {params!r}"]
        problems = []
        reject_unknown_params(problems, params, tuple(cls._PARAMS) + _NOTES)
        cls._check(problems, params)
        return problems

    @classmethod
    def _check(cls, problems, params):
        """Append problems with the declared knobs; the base has none."""

    @property
    def kind(self):
        """Return the registry name of a core kind, else the class reference."""
        return self.KIND if self.KIND is not None else class_ref(type(self))

    @property
    def timeout_s(self):
        """Return the send deadline: the endpoint's, else ``DEFAULT_SINK_TIMEOUT_S``."""
        return self._timeout_s

    @property
    def template(self):
        """Return the body template: the endpoint's, else ``DEFAULT_TEMPLATE``."""
        return self._template

    @property
    def params(self):
        """Return a copy of the declared params."""
        return dict(self._params)

    def render(self, alert):
        """Return the alert's masked JSON-ready body under this sink's template.

        Parameters
        ----------
        alert : records.Alert
            The alert to render.

        Returns
        -------
        dict
            Every string masked; the shape ``TEMPLATES[template]`` gives.
        """
        return TEMPLATES[self._template](redact_strings(alert.to_obj()))

    def resolve_url(self):
        """Return the endpoint URL, read from ``secrets`` now and kept nowhere.

        Returns
        -------
        str
            ``secrets[url_env]``.

        Raises
        ------
        ProductionError
            If the sink was built without an endpoint or secrets.
        """
        if self._secrets is None or self._url_env is None:
            raise ProductionError([f"a {self.kind} sink has no endpoint to resolve"])
        return self._secrets[self._url_env]

    @abstractmethod
    def send(self, alert):
        """Deliver one alert and report the outcome.

        Parameters
        ----------
        alert : records.Alert
            Firing or resolved.

        Returns
        -------
        SinkOutcome
            Never raises by contract; the router swallows and counts a
            sink that does anyway.
        """

    def close(self):
        """Release what the sink holds; idempotent, nothing by default.

        Returns
        -------
        None
        """


class LogSink(AlertSink):
    """Log each alert at the ``logging`` level ``vocab.SEVERITY_LEVELS`` pins.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.

    Examples
    --------
    ::

        sink = LogSink(None)
        sink.send(alert).ok   # True — one line on dskit.production.alerts
    """

    KIND = "log"

    def send(self, alert):
        """Log the masked alert; see :meth:`AlertSink.send`."""
        level = vocab.SEVERITY_LEVELS[alert.severity]["logging"]
        _log.log(level, "%s", json.dumps(redact_strings(alert.to_obj()), sort_keys=True))
        return SinkOutcome(ok=True, detail="logged")


class MemorySink(AlertSink):
    """Keep every alert in ``sent`` — the test and development sink.

    Unbounded by design: it exists to be inspected, not deployed.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.

    Attributes
    ----------
    sent : list of records.Alert
        Every alert, in delivery order.

    Examples
    --------
    ::

        sink = MemorySink(None)
        sink.send(alert)
        sink.sent[0].fingerprint   # the alert's fingerprint
    """

    KIND = "memory"

    def __init__(self, params, *, endpoint=None, transport=None, clock=None, secrets=None):
        super().__init__(
            params, endpoint=endpoint, transport=transport, clock=clock, secrets=secrets
        )
        self.sent = []

    def send(self, alert):
        """Remember the alert; see :meth:`AlertSink.send`."""
        self.sent.append(alert)
        return SinkOutcome(ok=True, detail="kept")


class WebhookSink(AlertSink):
    """POST the templated alert to the URL ``secrets[url_env]`` resolves at send time.

    Requires an endpoint, a transport and secrets. One send is one
    ``transport.send("POST", url, headers, body, deadline(timeout_s))``:
    a 2xx is success, anything else a failed outcome naming the status,
    a raise a failed outcome carrying the masked exception. The URL never
    appears in an outcome.

    Parameters
    ----------
    params : dict or None
        No knobs; ``notes`` only.
    endpoint, transport, secrets
        Required; see :class:`AlertSink`.

    Examples
    --------
    ::

        sink = WebhookSink(
            None, endpoint=document.alert_endpoints["ops"],
            transport=UrllibTransport({}), secrets=resolve_secrets(document.env),
        )
        sink.send(alert).detail   # 'HTTP 200'
    """

    KIND = "webhook"
    _REQUIRES = ("endpoint", "transport", "secrets")

    def send(self, alert):
        """POST the rendered body within the endpoint's deadline; see :meth:`AlertSink.send`."""
        try:
            body = json.dumps(self.render(alert), sort_keys=True).encode("utf-8")
            status, _headers, _payload = self._transport.send(
                "POST", self.resolve_url(), dict(_JSON_HEADERS), body, deadline(self._timeout_s)
            )
        except Exception as exc:  # the transport's fault is an outcome, never a raise
            return SinkOutcome(ok=False, detail=_failure_detail(exc))
        return SinkOutcome(ok=200 <= status < 300, detail=f"HTTP {status}")


def _recipients(url):
    """Return the addresses a ``mailto:`` URL (or a ``to`` query) names."""
    parts = urlsplit(url)
    if parts.scheme == "mailto":
        listed = unquote(parts.path)
    else:
        listed = ",".join(parse_qs(parts.query).get("to", ()))
    return [address.strip() for address in listed.split(",") if address.strip()]


class EmailSink(AlertSink):
    """Send each alert as one message through an ``smtplib``-shaped seam.

    The seam is ``mail_transport.send_message(message)`` — an
    ``smtplib.SMTP`` (built by ``compose.py`` with a real ``timeout``) or
    a recorder. The endpoint URL resolved at send time is a ``mailto:``
    address list (or any URL with a ``to`` query); its addresses become
    the ``To`` header. Any raise is a failed outcome.

    Parameters
    ----------
    params : dict or None
        ``sender`` (str, the ``From`` header; default
        ``DEFAULT_MAIL_SENDER``).
    mail_transport : object
        Required; anything with ``send_message(message)``.
    endpoint, secrets
        Required; see :class:`AlertSink`.

    Examples
    --------
    ::

        sink = EmailSink(
            {"sender": "serve@example.com"}, endpoint=document.alert_endpoints["mail"],
            secrets=resolve_secrets(document.env),
            mail_transport=smtplib.SMTP("smtp.example.com", 587, timeout=5.0),
        )
        sink.send(alert).ok   # True
    """

    KIND = "email"
    _PARAMS = ("sender",)
    _REQUIRES = ("endpoint", "secrets")

    def __init__(self, params, *, mail_transport=None, **collaborators):
        super().__init__(params, **collaborators)
        if not callable(getattr(mail_transport, "send_message", None)):
            raise ProductionError(
                ["an email sink requires the mail_transport collaborator (send_message)"]
            )
        self._mail_transport = mail_transport
        self._sender = self._params.get("sender", DEFAULT_MAIL_SENDER)

    @classmethod
    def _check(cls, problems, params):
        """Refuse a blank sender."""
        if "sender" in params:
            _check_str(problems, "sender", params["sender"])

    def send(self, alert):
        """Build and send one message; see :meth:`AlertSink.send`."""
        try:
            obj = redact_strings(alert.to_obj())
            message = EmailMessage()
            message["Subject"] = f"[{obj['severity']}] {obj['status']}: {obj['summary']}"
            message["From"] = self._sender
            recipients = _recipients(self.resolve_url())
            if recipients:
                message["To"] = ", ".join(recipients)
            message.set_content(json.dumps(obj, indent=2, sort_keys=True))
            self._mail_transport.send_message(message)
        except Exception as exc:  # the relay's fault is an outcome, never a raise
            return SinkOutcome(ok=False, detail=_failure_detail(exc))
        return SinkOutcome(ok=True, detail="sent")


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------


class _TokenBucket:
    """``rate_limit{max_per_hour, burst}`` in exact integer token-milliseconds."""

    def __init__(self, max_per_hour, burst):
        self._rate = max_per_hour
        self._capacity = burst * _MS_PER_HOUR
        self._level = self._capacity
        self._last_ms = None

    def take(self, now_ms):
        """Spend one token if the bucket holds one; refill by the time elapsed first."""
        if self._last_ms is not None:
            self._level = min(self._capacity, self._level + (now_ms - self._last_ms) * self._rate)
        self._last_ms = now_ms
        if self._level < _MS_PER_HOUR:
            return False
        self._level -= _MS_PER_HOUR
        return True


class _Group:
    """One fingerprint's state: what waits, what fired, what is resolving."""

    __slots__ = ("pending", "due_ms", "firing", "last_alert", "last_notified_ms", "resolve_ms")

    def __init__(self):
        self.pending = None
        self.due_ms = None
        self.firing = False
        self.last_alert = None
        self.last_notified_ms = None
        self.resolve_ms = None


class AlertRouter:
    """Dedup, group, rate-limit and deliver alerts; swallow and count the rest.

    Parameters
    ----------
    document_alerting : object
        The ``document.alerting`` view: ``sinks`` (the declared names),
        ``routes`` (each ``severity`` + ``sinks``), and the optional
        ``group_wait_s``, ``repeat_interval_s`` and ``rate_limit``
        (``max_per_hour``, ``burst``).
    sinks : dict of str to AlertSink
        The built sinks, keyed by the document's names; every declared
        name that a route uses must be present, and nothing undeclared may.
    clock : clock.Clock
        Injected; stamps arrivals and resolutions.
    metrics : metrics.Metrics
        Where ``alert_sink_failures_total{sink}`` and
        ``alerts_suppressed_total{why}`` are declared at construction.
    ledger : ledger.Ledger or None, optional
        When given, ``process`` appends one ``alert`` record per
        notification; ``start()`` then refuses (ruling R19).
    maxsize : int, optional
        The bound of the arrival queue; default ``DEFAULT_QUEUE_MAXSIZE``.

    Examples
    --------
    Drive the router from the loop, one ``process`` per tick::

        router = AlertRouter(
            document.alerting, {"ops": MemorySink(None)}, clock=clock, metrics=Metrics(),
        )
        router.raise_alert(alert)          # True: queued, nothing delivered yet
        router.process(clock.now_ms())     # () until group_wait_s has elapsed
        router.group_wait_s                # 30 (DEFAULT_GROUP_WAIT_S)
    """

    def __init__(
        self,
        document_alerting,
        sinks,
        *,
        clock,
        metrics,
        ledger=None,
        maxsize=DEFAULT_QUEUE_MAXSIZE,
    ):
        problems = []
        sinks = self._checked_sinks(problems, document_alerting, sinks)
        routes = self._checked_routes(problems, document_alerting, sinks)
        group_wait_s = _knob(document_alerting, "group_wait_s", DEFAULT_GROUP_WAIT_S)
        repeat_interval_s = _knob(document_alerting, "repeat_interval_s", DEFAULT_REPEAT_INTERVAL_S)
        _check_bounded(problems, "alerting.group_wait_s", group_wait_s, GROUP_WAIT_S_BOUNDS)
        _check_bounded(
            problems, "alerting.repeat_interval_s", repeat_interval_s, REPEAT_INTERVAL_S_BOUNDS
        )
        bucket = self._checked_bucket(problems, document_alerting)
        check_int_param(problems, "maxsize", maxsize, ge=1)
        if not callable(getattr(clock, "now_ms", None)):
            problems.append(f"clock must provide now_ms(), got {clock!r}")
        if ledger is not None and not callable(getattr(ledger, "append", None)):
            problems.append(f"ledger must provide append(record), got {ledger!r}")
        if problems:
            raise ProductionError(problems)
        self._sinks = sinks
        self._routes = routes
        self._group_wait_ms = int(group_wait_s) * _MS_PER_S
        self._repeat_ms = int(repeat_interval_s) * _MS_PER_S
        self._bucket = bucket
        self._clock = clock
        self._ledger = ledger
        self._maxsize = int(maxsize)
        self._queue = queue.Queue(maxsize=self._maxsize)
        self._failures = metrics.counter(
            _FAILURES, labels=tuple(sorted(vocab.METRIC_LABEL_VALUES[_FAILURES]))
        )
        self._suppressed = metrics.counter(
            _SUPPRESSED, labels=tuple(sorted(vocab.METRIC_LABEL_VALUES[_SUPPRESSED]))
        )
        self._workers = {
            name: SupervisedWorker(name)
            for name, sink in sinks.items()
            if sink.kind not in ALERT_SINK_KINDS
        }
        self._disabled = []
        self._groups = {}
        self._ordinal = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._closed = False

    # -- construction checks ------------------------------------------------

    @staticmethod
    def _checked_sinks(problems, document_alerting, sinks):
        """Every given sink is declared and is an ``AlertSink``; return a copy."""
        if not isinstance(sinks, dict):
            problems.append(f"sinks must be a dict of name -> AlertSink, got {sinks!r}")
            return {}
        declared = getattr(document_alerting, "sinks", None) or {}
        for name, sink in sinks.items():
            if name not in declared:
                problems.append(f"sink {name!r} is not declared in alerting.sinks")
            if not isinstance(sink, AlertSink):
                problems.append(f"sink {name!r} must be an AlertSink, got {sink!r}")
        return dict(sinks)

    @staticmethod
    def _checked_routes(problems, document_alerting, sinks):
        """Every route names a given sink; return ``{severity: sorted sink names}``."""
        routes = {severity: set() for severity in vocab.SEVERITIES}
        for index, route in enumerate(getattr(document_alerting, "routes", None) or ()):
            severity = getattr(route, "severity", None)
            if severity not in routes:
                problems.append(f"alerting.routes[{index}].severity {severity!r} is unknown")
                continue
            for name in getattr(route, "sinks", None) or ():
                if name not in sinks:
                    problems.append(
                        f"alerting.routes[{index}] names sink {name!r}, which was not given"
                    )
                routes[severity].add(name)
        return {severity: tuple(sorted(names)) for severity, names in routes.items()}

    @staticmethod
    def _checked_bucket(problems, document_alerting):
        """Return the token bucket ``alerting.rate_limit`` declares, or ``None``."""
        rate_limit = getattr(document_alerting, "rate_limit", None)
        if rate_limit is None:
            return None
        max_per_hour = getattr(rate_limit, "max_per_hour", None)
        burst = getattr(rate_limit, "burst", None)
        before = len(problems)
        check_int_param(problems, "alerting.rate_limit.max_per_hour", max_per_hour, ge=1)
        check_int_param(problems, "alerting.rate_limit.burst", burst, ge=1)
        if len(problems) != before:
            return None
        return _TokenBucket(int(max_per_hour), int(burst))

    # -- properties ---------------------------------------------------------

    @property
    def group_wait_s(self):
        """Return the effective ``group_wait_s``."""
        return self._group_wait_ms // _MS_PER_S

    @property
    def repeat_interval_s(self):
        """Return the effective ``repeat_interval_s``."""
        return self._repeat_ms // _MS_PER_S

    @property
    def maxsize(self):
        """Return the arrival queue's bound."""
        return self._maxsize

    @property
    def firing(self):
        """Return the fingerprints notified and not yet resolved, sorted."""
        with self._lock:
            return tuple(
                sorted(
                    fingerprint
                    for fingerprint, group in self._groups.items()
                    if group.firing and group.resolve_ms is None
                )
            )

    @property
    def disabled_sinks(self):
        """Return the sinks disabled after a timed-out send, in that order."""
        return tuple(self._disabled)

    # -- the arrival side (any thread) --------------------------------------

    def raise_alert(self, alert):
        """Queue one alert for the next ``process``; never blocks, never delivers.

        Parameters
        ----------
        alert : records.Alert
            The alert.

        Returns
        -------
        bool
            ``True`` if queued; ``False`` when the bounded queue was full —
            counted under ``queue_full`` and swallowed.

        Raises
        ------
        ProductionError
            If ``alert`` is not a ``records.Alert``.
        """
        if not isinstance(alert, Alert):
            raise ProductionError([f"raise_alert expects a records.Alert, got {alert!r}"])
        try:
            self._queue.put_nowait((self._clock.now_ms(), alert))
        except queue.Full:
            self._suppress("queue_full")
            return False
        return True

    def resolve(self, fingerprint):
        """Mark a firing fingerprint resolved; the next ``process`` notifies at once.

        Parameters
        ----------
        fingerprint : str
            The dedup key.

        Returns
        -------
        bool
            ``True`` if the fingerprint was firing. A fingerprint still
            waiting for its first notification is dropped instead (counted
            under ``group_wait``) and answers ``False``, as does one that
            never fired.
        """
        with self._lock:
            group = self._groups.get(fingerprint)
            if group is None:
                return False
            if group.firing and group.resolve_ms is None:
                group.resolve_ms = self._clock.now_ms()
                return True
            if group.pending is not None and not group.firing:
                group.pending = None
                self._suppress("group_wait")
                del self._groups[fingerprint]
            return False

    # -- the delivery side (the loop thread, or the one worker) -------------

    def process(self, now_ms):
        """Drain the queue and deliver every notification due at ``now_ms``.

        The only path that appends ``alert`` records; the loop calls it
        once per tick after ``observe``, outside every barrier.

        Parameters
        ----------
        now_ms : int
            The instant, told by the caller.

        Returns
        -------
        tuple of Notification
            What was delivered, resolutions first.
        """
        with self._lock:
            self._drain()
            return tuple(self._flush(now_ms))

    def drain(self):
        """``process`` at the router's own clock.

        Returns
        -------
        tuple of Notification
            As :meth:`process`.
        """
        return self.process(self._clock.now_ms())

    def start(self):
        """Run ``process`` on one daemon worker, for a process with no loop to drive it.

        Raises
        ------
        ProductionError
            If a ledger is attached — an auxiliary thread never appends
            (ruling R19) — or the router is already started or closed.
        """
        if self._ledger is not None:
            raise ProductionError(
                ["start() refuses with a ledger attached: only the loop thread appends"]
            )
        if self._closed or self._thread is not None:
            raise ProductionError(["the router is already started or closed"])
        self._thread = threading.Thread(
            target=self._work, name="dskit-production-alerts", daemon=True
        )
        self._thread.start()

    def close(self):
        """Stop the worker, stop every sink worker and close every sink; idempotent.

        Returns
        -------
        None
            Returns even while a custom sink is still stuck: its daemon
            thread is abandoned, never joined.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            bound = sum(sink.timeout_s for sink in self._sinks.values()) + WORKER_POLL_S
            self._thread.join(bound)
        for worker in self._workers.values():
            worker.stop()
        for name, sink in self._sinks.items():
            try:
                sink.close()
            except Exception as exc:  # a sink's close is as untrusted as its send
                _log.warning("sink %s failed to close: %s", name, _failure_detail(exc))

    def _work(self):
        """The worker: ``process`` now, then sleep a slice, until stopped."""
        while not self._stop.is_set():
            self.process(self._clock.now_ms())
            self._stop.wait(WORKER_POLL_S)

    # -- admission ----------------------------------------------------------

    def _drain(self):
        """Admit every queued arrival into its fingerprint's group."""
        while True:
            try:
                arrived_ms, alert = self._queue.get_nowait()
            except queue.Empty:
                return
            self._admit(arrived_ms, alert)

    def _admit(self, arrived_ms, alert):
        """Place one arrival: open a group, supersede a pending one, or count it."""
        group = self._groups.setdefault(alert.fingerprint, _Group())
        firing = group.firing and group.resolve_ms is None
        if group.pending is not None:
            group.pending = alert
            self._suppress("dedup" if firing else "group_wait")
        elif firing:
            if arrived_ms - group.last_notified_ms >= self._repeat_ms:
                group.pending, group.due_ms = alert, arrived_ms
            else:
                self._suppress("repeat_interval")
        else:
            group.pending, group.due_ms = alert, arrived_ms + self._group_wait_ms

    def _suppress(self, why):
        """Count one withheld alert under its ``vocab.ALERT_SUPPRESSIONS`` reason."""
        self._suppressed.inc(why=why)

    # -- delivery -----------------------------------------------------------

    def _flush(self, now_ms):
        """Deliver resolutions, then every pending group that is due; yield notifications."""
        for fingerprint in list(self._groups):
            group = self._groups[fingerprint]
            if group.resolve_ms is not None:
                obj = group.last_alert.to_obj()
                obj["status"], obj["at_ms"] = _RESOLVED, group.resolve_ms
                yield self._notify(Alert.from_obj(obj), now_ms)
                group.firing, group.last_alert, group.resolve_ms = False, None, None
                if group.pending is None:
                    del self._groups[fingerprint]
        for group in list(self._groups.values()):
            if group.pending is None or now_ms < group.due_ms:
                continue
            alert, group.pending = group.pending, None
            if (
                alert.severity != _BYPASSES_RATE_LIMIT
                and self._bucket is not None
                and not self._bucket.take(now_ms)
            ):
                self._suppress("rate_limit")
                continue
            yield self._notify(alert, now_ms)
            group.firing, group.last_alert, group.last_notified_ms = True, alert, now_ms

    def _notify(self, alert, now_ms):
        """Send to every routed sink, record the outcomes, return the notification."""
        outcomes = {name: self._send(name, alert) for name in self._routes[alert.severity]}
        notification = Notification(alert, outcomes)
        if self._ledger is not None:
            self._ordinal += 1
            body = redact_strings(alert.to_obj())
            body["sinks"] = {name: outcome.to_obj() for name, outcome in outcomes.items()}
            self._ledger.append(
                {
                    "kind": "alert",
                    "id": f"alert:{alert.fingerprint}:{alert.status}:{now_ms}:{self._ordinal}",
                    "body": body,
                }
            )
        return notification

    def _send(self, name, alert):
        """One sink, one alert: inline for a core kind, supervised for a custom one."""
        sink = self._sinks[name]
        if name in self._disabled:
            outcome = SinkOutcome(ok=False, detail="disabled after a timed-out send")
        elif name not in self._workers:
            outcome = self._send_inline(sink, alert)
        else:
            outcome = self._send_supervised(name, sink, alert)
        if not isinstance(outcome, SinkOutcome):
            outcome = SinkOutcome(ok=False, detail="the sink returned no SinkOutcome")
        if not outcome.ok:
            self._failures.inc(sink=sink.kind)
            _log.warning("alert %s not delivered by %s: %s", alert.fingerprint, name, outcome.detail)
        return outcome

    @staticmethod
    def _send_inline(sink, alert):
        """A core kind sends on this thread, bounded by its transport's deadline."""
        try:
            return sink.send(alert)
        except Exception as exc:  # a sink's raise is an outcome, never the caller's
            return SinkOutcome(ok=False, detail=_failure_detail(exc))

    def _send_supervised(self, name, sink, alert):
        """A custom sink sends on its worker; a timeout disables it for good."""
        result = self._workers[name].call(lambda: sink.send(alert), sink.timeout_s)
        if result.timed_out:
            self._disabled.append(name)
            return SinkOutcome(ok=False, detail=f"timed out after {sink.timeout_s}s; disabled")
        if result.error is not None:
            return SinkOutcome(ok=False, detail=_failure_detail(result.error))
        return result.value


def _knob(section, name, default):
    """Read an optional document knob, falling back to its one named default."""
    value = getattr(section, name, None)
    return default if value is None else value


def _check_bounded(problems, name, value, bounds):
    """Append a problem unless ``value`` is an int inside ``bounds`` (inclusive)."""
    low, high = bounds
    check_int_param(problems, name, value, ge=low)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > high:
        problems.append(f"{name} must be at most {high}, got {value!r}")


# ---------------------------------------------------------------------------
# The registry — the open doorway (§4.3); ``SINK_KINDS`` is the pipeline's
# ---------------------------------------------------------------------------

ALERT_SINK_KINDS = Registry("alert_sink", AlertSink)
for _cls in (EmailSink, LogSink, MemorySink, WebhookSink):
    ALERT_SINK_KINDS.register(_cls.KIND, _cls)
del _cls

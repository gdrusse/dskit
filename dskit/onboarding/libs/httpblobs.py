r"""``httpblobs`` — one HTTP GET per entity, each response acquired as a hashed file.

Many sources are "the same URL, once per symbol/series/id": a chart per
ticker, a document per record. This pack declares that shape in config and
turns every response into a FILE artifact (ADR-0233): copied into the
snapshot at ``payload/<stream>/<relpath>``, digested into the Merkle
manifest and re-hashable by ``verify``, exactly like ``localblobs``. It
owns the polite-client mechanics once (throttle, retry with backoff, a
final answer on a client error) so no project writes an ad hoc pull script.

One entity yields one inventory RECORD ``{entity, status, http_status,
relpath, size, sha256, raw_sha256, origin, reason}`` dated at the declared
``as_of``. ``status`` is ``ok`` or ``refused``: a 4xx answer (a delisted
ticker's 404/410) or a transform that cannot reshape the body is RECORDED, with
no file, and the pull goes on, up to ``max_refused`` (default 0) of them. Any
other non-2xx (400/401/403/408 …) is a pull-level fault and aborts. Exhausted retries on a 429/5xx or a network
error abort the pull with no STATE, so the cursor stays put.

An optional ``transform`` (``pkg.module:Class``, built as
``Class(transform_params, as_of)``) is called ``transform(entity, body)``
and returns the bytes to store, so reshaping a response into the file a
reader wants lives in the caller's code, never here. With
``raw_relpath_template`` the untouched response is kept beside it; with
``cache_dir`` a raw response already on disk is used instead of the
network (a read-through cache: re-derive without re-fetching, resume a
long pull). TRUST: the cache is read as-is, with no hash or freshness check,
and ``as_of`` is only declared, so bumping ``as_of`` with a cache present
re-derives the OLD bytes under a new date; point it only at responses you
vouch for. An optional transform ``note(entity, body)`` string lands in the
RECORD ``note``. Where the bytes came from (cache or network) goes to one LOG,
never into a hashed record, so identical bytes hash identically. The throttle waits only between real network requests.

Cursor: the checkpoint is a digest of the whole declaration (url, entities,
templates, transform, ``as_of``); an unchanged declaration emits one LOG
and the same STATE and touches no network. Re-pull by changing ``as_of``.

Dates that advance (ADR-0266): ``entities_dates`` makes the entities the UTC
days ``YYYY-MM-DD`` from ``start``. ``backfill`` pulls ``start`` through
``as_of`` minus ``lag_days``, inclusive, so the declaration still fixes a
reproducible set (and, for the same days, digests exactly as the explicit list
would). ``live`` pulls the days after its own checkpoint ``{declaration,
through, absent}`` through UTC today minus ``lag_days``. With no checkpoint it
starts ``give_up_days`` before the backfill's last day, so a backfill day the
vendor refused (and may since have published) is tried again; the few ok days
in that span are simply re-fetched. Nothing due emits one LOG and the same
STATE and touches no network. A refused day (the vendor has not published it
yet) counts toward ``max_refused``, is retried by the next ``live`` pull, and
``through`` never passes it, so days after it are pulled again with it, until
it is older than ``give_up_days``: a day still refused then is logged by name,
recorded in the checkpoint's ``absent`` list and passed, never refetched, and
(even in the pull that gives up) never counted toward ``max_refused`` (a day
the vendor never publishes must not block the feed). Changing the declaration
(including ``as_of``) discards the live checkpoint: it restarts as above from
the new backfill end. The clock is this module's ``utc_now`` name, which tests
point at a fixed instant.

Config knobs (default-deny, per ``spec()``):

- ``url_template`` (required) — ``http(s)://…`` holding ``{entity}``
  (URL-quoted) and optionally ``{entity_lower}``; the query string rides in
  it.
- Path knobs (``entities_file``, ``cache_dir``) expand ``~`` and ``$VAR``
  (an unset variable refuses), so a config carries no machine path.
  ``entities_file`` must be absolute after expansion and is pinned by the
  REQUIRED ``entities_sha256`` (``connector.read_pinned_json``, shared with
  alpaca's ``symbols_file``): an edited universe refuses.
- ``entities`` — distinct non-empty strings; or, instead,
  ``entities_file`` (a JSON file, machine-local like ``localblobs``'s
  ``path``) with ``entities_key`` (dot-path to the list inside it; omit when
  the file IS the list), so a project's universe lives in ONE args file and
  never in a copy here; or ``entities_dates`` ``{start, lag_days}`` (see
  above; ``start`` required ``YYYY-MM-DD``, ``lag_days`` an int >= 0,
  default ``DEFAULT_LAG_DAYS``; ``give_up_days`` an int >= 1, default
  ``DEFAULT_GIVE_UP_DAYS``; no other key). Exactly one of the three
  sources.
- ``relpath_template`` (required) — POSIX relpath of the stored file; must
  name ``{entity}`` or ``{entity_lower}``.
- ``as_of`` (required) — ISO instant, not in the future.
- ``stream`` — default ``files``.
- ``raw_relpath_template`` — also keep the untouched response there.
- ``cache_dir`` — directory holding raw responses at
  ``raw_relpath_template``; needs it.
- ``transform`` / ``transform_params`` — see above.
- ``headers`` — static ``{str: str}`` request headers. No secrets: a
  credential never enters config.
- ``max_refused`` (default 0) — refusals tolerated before the pull aborts.
- ``max_bytes`` — optional cap on a response body; larger refuses. Redirects
  must stay on the request's host.
- ``throttle_s`` (default 0) — minimum seconds between network requests.
- ``timeout`` (default 30) — seconds per request.
- ``max_retries`` (default 3) — extra attempts on 429/5xx/network errors,
  waits from :func:`~dskit.onboarding.connector.backoff` or a numeric
  ``Retry-After`` (capped at ``MAX_BACKOFF_S``).

Every request goes through the single ``_fetch`` seam. Error text carries
URLs with the query string stripped.

Import cost: stdlib only.
"""

from __future__ import annotations

import hashlib
import importlib
import datetime
import json
import os
import re
import string
import tempfile
import time
import urllib.parse

from ..base import AssetError, _check_segment, _raise_if, parse_utc, utc_now
from ..connector import (
    DEFAULT_BACKOFF_S,
    PROTOCOL,
    Connector,
    _file_relpath_problems,
    backoff,
    read_pinned_json,
    retry_after,
    safe_url,
)
from .localblobs import DEFAULT_STREAM

__all__ = ["DEFAULT_STREAM", "RECORD_FIELDS", "HttpBlobsConnector", "safe_url"]

#: The RECORD fields — one inventory row per entity.
RECORD_FIELDS = ("entity", "status", "http_status", "relpath", "size", "sha256",
                 "raw_sha256", "note", "reason")

#: HTTP statuses worth retrying — throttling and transient server faults.
_RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Statuses that mean "this entity has no document" — a recorded refusal.
#: Every other non-2xx is a pull-level fault (bad request, auth, throttle
#: policy), so it aborts rather than quietly shrinking the inventory.
_MISSING_STATUSES = (404, 410)

#: This pack's backoff base, the contract's own; a module-level NAME so tests
#: zero it instead of sleeping through retries.
_BACKOFF = DEFAULT_BACKOFF_S

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3
DEFAULT_THROTTLE_S = 0
DEFAULT_MAX_REFUSED = 0
DEFAULT_LAG_DAYS = 0
DEFAULT_GIVE_UP_DAYS = 7
_PLACEHOLDERS = ("entity", "entity_lower")

#: The three mutually exclusive entity sources.
_ENTITY_SOURCES = ("entities", "entities_file", "entities_dates")

#: The ``entities_dates`` sub-keys (default-deny) and the day format they use.
_DATES_KEYS = ("start", "lag_days", "give_up_days")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: The relpath templates a pull renders per entity.
_TEMPLATE_KEYS = ("relpath_template", "raw_relpath_template")

#: The live checkpoint's key for the last day pulled in full.
_THROUGH = "through"

#: The live checkpoint's key for the days given up on (refused past ``give_up_days``).
_ABSENT = "absent"

_KNOBS = {
    "url_template": "http(s) URL holding {entity} (URL-quoted) and optionally {entity_lower}.",
    "entities": "Distinct non-empty strings, one request each (or entities_file).",
    "entities_file": "JSON file holding the entity list instead of entities.",
    "entities_dates": ("{start, lag_days, give_up_days}: the UTC days YYYY-MM-DD from start; backfill "
                       "ends at as_of - lag_days, live advances to today - lag_days and gives up on a "
                       "day still refused after give_up_days. Instead of entities. "
                       f"Defaults lag_days {DEFAULT_LAG_DAYS}, give_up_days {DEFAULT_GIVE_UP_DAYS}."),
    "entities_sha256": "REQUIRED with entities_file: sha256 of the file's bytes; a changed file refuses.",
    "entities_key": "Dot-path to the list inside entities_file; omit when the file is the list.",
    "relpath_template": "POSIX relpath of the stored file; names {entity} or {entity_lower}.",
    "as_of": "ISO instant the pull is declared current (not in the future); re-pull by changing it.",
    "stream": f"Stream name; default {DEFAULT_STREAM!r}.",
    "raw_relpath_template": "Also keep the untouched response at this relpath template.",
    "cache_dir": "Directory of raw responses at raw_relpath_template, used instead of the network.",
    "transform": "pkg.module:Class built as Class(transform_params, as_of); transform(entity, body) -> bytes.",
    "transform_params": "Dict handed to the transform class.",
    "headers": "Static {str: str} request headers. No secrets.",
    "throttle_s": f"Minimum seconds between network requests; default {DEFAULT_THROTTLE_S}.",
    "timeout": f"Seconds per request; default {DEFAULT_TIMEOUT}.",
    "max_refused": f"Most refused entities tolerated before the pull aborts; default {DEFAULT_MAX_REFUSED}.",
    "max_bytes": "Largest response body accepted, in bytes; default unlimited.",
    "max_retries": f"Extra attempts on 429/5xx/network errors; default {DEFAULT_MAX_RETRIES}.",
}
_REQUIRED = ("url_template", "relpath_template", "as_of")
_CLASS_REF = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_STATE_KEYS = ("declaration",)


def _number(problems, name, value, default, minimum, integer):
    """Validate a numeric knob; append a problem when unusable."""
    if value is None:
        return default
    ok = isinstance(value, int) if integer else isinstance(value, (int, float))
    if not ok or isinstance(value, bool) or value != value or value < minimum:
        problems.append(f"config.{name} must be a number >= {minimum}, got {value!r}")
        return default
    return value


def _placeholder_problems(name, template):
    """List why ``template`` uses a field other than the declared placeholders."""
    try:
        fields = [(f, spec, conv) for _, f, spec, conv in string.Formatter().parse(template)
                  if f is not None]
    except ValueError as exc:
        return [f"config.{name} has a bad placeholder: {exc}"]
    bad = [f for f, spec, conv in fields if f not in _PLACEHOLDERS or spec or conv]
    return [f"config.{name} may only use the placeholders {list(_PLACEHOLDERS)}, got {bad}"] if bad else []


def _expand(value):
    """Expand ``~`` and ``$VAR`` in a path knob; ``None`` when a variable is unset."""
    out = os.path.expanduser(os.path.expandvars(value))
    return None if "$" in out else out


def _render(template, entity):
    """Fill a relpath template for one entity."""
    return template.format(entity=entity, entity_lower=entity.lower())


def _days(first, last):
    """List the ISO dates ``first`` through ``last`` inclusive; ``[]`` when ``first`` is after ``last``."""
    count = (last - first).days + 1
    return [(first + datetime.timedelta(days=n)).isoformat() for n in range(max(count, 0))]


class HttpBlobsConnector(Connector):
    r"""One HTTP GET per entity; each response becomes a hashed file.

    See the module docstring for the full contract.

    Parameters
    ----------
    None
        The connector is stateless; every setting comes from config.

    Examples
    --------
    Pull a JSON document per symbol and lay it out as ``<symbol>/doc.json``::

        python -m dskit.onboarding register-source docs \
          --catalog-source docs --connector httpblobs \
          --config '{"url_template": "https://api.example.org/v1/{entity}",
                     "entities": ["abc", "xyz"],
                     "relpath_template": "{entity_lower}/doc.json",
                     "as_of": "2026-10-03T00:00:00+00:00", "throttle_s": 0.5}' \
          --activate --root ./ob
        python -m dskit.onboarding acquire --source docs --stream files \
          --mode backfill --root ./ob

    Pull one daily file per UTC day from 2023-06-20, through yesterday as of
    the declaration; each later ``--mode live`` pull adds the days since::

        --config '{"url_template": "https://data.example.org/{entity}.zip",
                   "entities_dates": {"start": "2023-06-20", "lag_days": 1},
                   "relpath_template": "{entity}.zip",
                   "as_of": "2026-10-12T19:00:00+00:00", "max_refused": 3}'
    """

    def spec(self):
        """Declare the knobs, default-deny.

        Returns
        -------
        dict
            ``{"params": {...}}``, one entry per knob in the module docstring.
        """
        return {"params": {
            name: ({"required": True, "notes": note} if name in _REQUIRED else {"notes": note})
            for name, note in _KNOBS.items()
        }}

    def resolve_knobs(self, config):
        """Validate the knobs and return them normalised, or raise listing every problem.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        dict
            Every knob with defaults filled, ``as_of`` as ISO UTC. ``entities``
            is the backfill set; ``dates`` is ``None`` or ``{"start", "lag_days",
            "give_up_days", "backfill_end"}`` (``datetime.date`` values) for ``entities_dates``.

        Raises
        ------
        AssetError
            Listing every unusable knob value.
        """
        problems = []
        url = config.get("url_template")
        if not (isinstance(url, str) and re.match(r"^https?://", url)
                and ("{entity}" in url or "{entity_lower}" in url)):
            problems.append("config.url_template must be an http(s) URL naming {entity}, "
                            f"got {url!r}")
        elif _placeholder_problems("url_template", url):
            problems.extend(_placeholder_problems("url_template", url))
        when = self._as_of(problems, config)
        entities, dates = self._entities(problems, config, when)
        stream = config.get("stream", DEFAULT_STREAM)
        _check_segment(problems, "config.stream", stream)
        templates = {k: config.get(k) for k in _TEMPLATE_KEYS if config.get(k) is not None}
        if not isinstance(templates.get("relpath_template"), str):
            problems.append("config.relpath_template must be a string naming {entity}")
        paths = self._relpaths(problems, templates, entities)
        if config.get("cache_dir") is not None and "raw_relpath_template" not in templates:
            problems.append("config.cache_dir needs raw_relpath_template (where raw responses sit)")
        as_of = when.isoformat() if when is not None else None
        headers = config.get("headers", {})
        if not (isinstance(headers, dict)
                and all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items())):
            problems.append("config.headers must be a {str: str} dict")
        transform = config.get("transform")
        if transform is not None and not (isinstance(transform, str) and _CLASS_REF.match(transform)):
            problems.append(f"config.transform must be 'pkg.module:Class', got {transform!r}")
        tparams = config.get("transform_params", {})
        if not isinstance(tparams, dict):
            problems.append("config.transform_params must be a dict")
        knobs = {
            "throttle_s": _number(problems, "throttle_s", config.get("throttle_s"),
                                  DEFAULT_THROTTLE_S, 0, False),
            "timeout": _number(problems, "timeout", config.get("timeout"), DEFAULT_TIMEOUT, 1, False),
            "max_retries": _number(problems, "max_retries", config.get("max_retries"),
                                   DEFAULT_MAX_RETRIES, 0, True),
            "max_refused": _number(problems, "max_refused", config.get("max_refused"),
                                   DEFAULT_MAX_REFUSED, 0, True),
            "max_bytes": _number(problems, "max_bytes", config.get("max_bytes"), None, 1, True),
        }
        _raise_if(problems)
        cache = config.get("cache_dir")
        if cache is not None and not (isinstance(cache, str) and _expand(cache)):
            problems.append(f"config.cache_dir must be a path whose $VARs are all set, got {cache!r}")
        _raise_if(problems)
        return {"url_template": url, "entities": list(entities), "stream": stream, "as_of": as_of,
                "paths": paths, "dates": dates, "headers": dict(headers), "transform": transform,
                "transform_params": dict(tparams),
                "cache_dir": os.path.abspath(_expand(cache)) if cache else None,
                **templates, **knobs}

    @staticmethod
    def _as_of(problems, config):
        """Parse the declared ``as_of`` to an aware datetime, or ``None`` after a problem."""
        try:
            when = parse_utc(config.get("as_of"))
        except AssetError:
            problems.append(f"config.as_of must be an ISO-8601 instant, got {config.get('as_of')!r}")
            return None
        if when > parse_utc(utc_now()):
            problems.append(f"config.as_of {config.get('as_of')!r} is in the future")
        return when

    @classmethod
    def _entities(cls, problems, config, when):
        """``(entities, dates)`` from the one declared source; ``([], None)`` after a problem."""
        given = [k for k in _ENTITY_SOURCES if config.get(k) is not None]
        if len(given) != 1:
            problems.append("give exactly one of " + ", ".join(f"config.{k}" for k in _ENTITY_SOURCES))
            return [], None
        source = given[0]
        if source != "entities_file":
            problems.extend(f"config.{k} needs entities_file"
                            for k in ("entities_key", "entities_sha256") if config.get(k) is not None)
        if source == "entities_dates":
            return cls._dates(problems, config["entities_dates"], when)
        inline = config.get("entities")
        if source == "entities_file":
            inline = read_pinned_json(config, problems, "entities_file", "entities_sha256")
            if inline is None:
                return [], None
            for part in (config.get("entities_key") or "").split("."):
                if part:
                    inline = inline.get(part) if isinstance(inline, dict) else None
        if not (isinstance(inline, list) and inline
                and all(isinstance(e, str) and e for e in inline)):
            problems.append(f"the entity list must be a non-empty list of strings, got {inline!r}")
            return [], None
        if len(set(inline)) != len(inline):
            problems.append("the entity list holds a duplicate")
        return inline, None

    @staticmethod
    def _dates(problems, spec, when):
        """``(backfill days, dates knob)`` from ``entities_dates``; ``([], None)`` after a problem."""
        if not isinstance(spec, dict):
            problems.append(f"config.entities_dates must be a {{start, lag_days}} dict, got {spec!r}")
            return [], None
        unknown = sorted(set(spec) - set(_DATES_KEYS), key=str)
        if unknown:
            problems.append(f"config.entities_dates has unknown key(s) {unknown}; "
                            f"allowed: {list(_DATES_KEYS)}")
        start = spec.get("start")
        try:
            if not (isinstance(start, str) and _DAY.match(start)):
                raise ValueError(start)
            first = datetime.date.fromisoformat(start)
        except ValueError:
            problems.append(f"config.entities_dates.start must be an ISO date YYYY-MM-DD, got {start!r}")
            first = None
        lag = _number(problems, "entities_dates.lag_days", spec.get("lag_days"),
                      DEFAULT_LAG_DAYS, 0, True)
        give_up = _number(problems, "entities_dates.give_up_days", spec.get("give_up_days"),
                          DEFAULT_GIVE_UP_DAYS, 1, True)
        if unknown or first is None or when is None:
            return [], None
        end = when.astimezone(datetime.timezone.utc).date() - datetime.timedelta(days=lag)
        if first > end:
            problems.append(f"config.entities_dates.start {start} is after as_of - lag_days "
                            f"({end.isoformat()}): the backfill would be empty")
            return [], None
        return _days(first, end), {"start": first, "lag_days": lag, "give_up_days": give_up,
                             "backfill_end": end}

    @staticmethod
    def _relpaths(problems, templates, entities):
        """Render every template per entity; flag unsafe, unnamed or colliding paths."""
        paths = {}
        seen = {}
        for key, tpl in templates.items():
            if not isinstance(tpl, str):
                continue
            if "{entity}" not in tpl and "{entity_lower}" not in tpl:
                problems.append(f"config.{key} must name {{entity}} or {{entity_lower}}, got {tpl!r}")
                continue
            if _placeholder_problems(key, tpl):
                problems.extend(_placeholder_problems(key, tpl))
                continue
            for ent in entities:
                rel = _render(tpl, ent)
                bad = _file_relpath_problems(rel)
                if bad:
                    problems.append(f"{key} for entity {ent!r}: {bad[0]}")
                if rel in seen:
                    problems.append(f"relpath {rel!r} collides ({key} vs {seen[rel]})")
                seen[rel] = key
                paths.setdefault(ent, {})[key] = rel
        for rel in seen:
            parts = rel.split("/")
            for n in range(1, len(parts)):
                if "/".join(parts[:n]) in seen:
                    problems.append(f"relpath clash: {'/'.join(parts[:n])!r} is a file and a "
                                    f"directory holding {rel!r}")
        return paths

    def check(self, config):
        """Validate the knobs and the transform reference; move no data.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Raises
        ------
        AssetError
            On any unusable knob or an unimportable transform.
        """
        knobs = self.resolve_knobs(config)
        self._transform(knobs)

    def discover(self, config):
        """List the one stream and its inventory schema.

        Parameters
        ----------
        config : dict
            Connector configuration.

        Returns
        -------
        list of dict
            One entry: the stream, the RECORD fields, key ``[entity]``.
        """
        knobs = self.resolve_knobs(config)
        return [{"stream": knobs["stream"], "schema": {"fields": list(RECORD_FIELDS)},
                 "primary_key": ["entity"]}]

    @staticmethod
    def _transform(knobs):
        """Build the declared transform object, or return ``None`` when none is declared."""
        ref = knobs["transform"]
        if ref is None:
            return None
        module, name = ref.split(":")
        try:
            cls = getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError) as exc:
            raise AssetError([f"config.transform {ref!r} cannot be imported: {exc}"]) from exc
        return cls(knobs["transform_params"], knobs["as_of"])

    @staticmethod
    def _declaration(knobs):
        """Digest of everything the pull computes from."""
        keys = ("url_template", "entities", "stream", "as_of", "paths", "headers", "transform",
                "transform_params", "cache_dir")
        blob = json.dumps({k: knobs[k] for k in keys}, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    # -- transport ---------------------------------------------------------

    def _fetch(self, url, headers, timeout, max_bytes):
        """One HTTP GET -> ``(status, headers, body bytes)``; the single network seam.

        Redirects stay on the request's host (a cross-host one refuses), and a
        body over ``max_bytes`` refuses rather than filling memory.
        """
        import urllib.error
        import urllib.request

        class SameHost(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
                    raise AssetError([f"refusing a redirect from {safe_url(req.full_url)} to "
                                      f"another host {safe_url(newurl)}"])
                return super().redirect_request(req, fp, code, msg, hdrs, newurl)

        opener = urllib.request.build_opener(SameHost)
        req = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(req, timeout=timeout) as resp:
                return resp.getcode(), dict(resp.headers.items()), self._body(resp, max_bytes, url)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), self._body(exc, max_bytes, url)

    @staticmethod
    def _body(resp, max_bytes, url):
        """Read a response body, refusing one larger than ``max_bytes``."""
        if max_bytes is None:
            return resp.read()
        body = resp.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise AssetError([f"response from {safe_url(url)} exceeds max_bytes {max_bytes}"])
        return body

    def _get(self, url, knobs, label):
        """GET with retry; ``(status, body)`` for a 2xx or missing-document answer, or raise."""
        last = None
        for attempt in range(knobs["max_retries"] + 1):
            try:
                status, headers, body = self._fetch(url, knobs["headers"], knobs["timeout"],
                                                  knobs["max_bytes"])
            except OSError as exc:
                last, headers = f"network error: {exc}", None
            else:
                if 200 <= status < 300 or status in _MISSING_STATUSES:
                    return status, body
                if status not in _RETRY_STATUSES:
                    raise AssetError([f"{label}: HTTP {status} from {safe_url(url)}: {body[:200]!r}"])
                last = f"HTTP {status}"
            if attempt < knobs["max_retries"]:
                time.sleep(retry_after(headers, backoff(attempt + 1, _BACKOFF)))
        raise AssetError([f"{label}: giving up on {safe_url(url)} after "
                          f"{knobs['max_retries'] + 1} attempt(s) — last failure: {last}"])

    def _cached(self, knobs, entity):
        """Raw bytes already on disk for ``entity``, or ``None``."""
        if not knobs["cache_dir"]:
            return None
        full = os.path.join(knobs["cache_dir"], *knobs["paths"][entity]["raw_relpath_template"].split("/"))
        if not os.path.isfile(full):
            return None
        with open(full, "rb") as fh:
            return fh.read()

    def _obtain(self, knobs, entity, last_request):
        """``(origin, status, body)`` for one entity, throttling real requests only."""
        body = self._cached(knobs, entity)
        if body is not None:
            return "cache", 200, body, last_request
        if last_request is not None:
            wait = knobs["throttle_s"] - (time.monotonic() - last_request)
            if wait > 0:
                time.sleep(wait)
        url = knobs["url_template"].format(entity=urllib.parse.quote(entity, safe=""),
                                           entity_lower=urllib.parse.quote(entity.lower(), safe=""))
        try:
            status, body = self._get(url, knobs, f"entity {entity!r}")
        finally:
            last_request = time.monotonic()
        return "network", status, body, last_request

    @staticmethod
    def _record(entity, **fields):
        """One inventory RECORD message for ``entity``."""
        data = {k: None for k in RECORD_FIELDS}
        data.update(fields, entity=entity)
        return data

    def read(self, config, streams, state, mode):
        """Yield FILE + RECORD per entity, then STATE.

        Parameters
        ----------
        config : dict
            Connector configuration.
        streams : list of str
            Must be exactly the configured stream.
        state : dict
            The mode-keyed checkpoint from the last committed pull, or ``{}``.
        mode : str
            ``backfill`` or ``live``. They pull the same entities, except under
            ``entities_dates``, where ``live`` pulls the days after its
            checkpoint (see the module docstring).

        Yields
        ------
        dict
            Envelope messages: per ok entity FILEs (payload, then raw when
            kept) and a RECORD; a RECORD alone for a refused one; then STATE.
            An unchanged declaration (or no live day due) yields LOG + STATE.

        Raises
        ------
        AssetError
            On an unknown stream, an unusable knob or live checkpoint, or
            retries exhausted for an entity (no STATE is emitted then).
        """
        knobs = self.resolve_knobs(config)
        stream = knobs["stream"]
        if not isinstance(streams, list) or not streams:
            raise AssetError([f"streams must be a non-empty list, got {streams!r}"])
        for name in streams:
            if name != stream:
                raise AssetError([f"unknown stream {name!r} — this source offers {stream!r} only"])
        if knobs["dates"] is not None and mode == "live":
            yield from self._read_live(knobs, state)
            return
        checkpoint = {"declaration": self._declaration(knobs)}
        if all(state.get(k) == checkpoint[k] for k in _STATE_KEYS):
            yield {"protocol": PROTOCOL, "type": "LOG",
                   "message": f"{len(knobs['entities'])} entities under the same declaration — "
                              "nothing new"}
            yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}
            return
        yield from self._pull(knobs)
        yield {"protocol": PROTOCOL, "type": "STATE", "state": checkpoint}

    def _read_live(self, knobs, state):
        """Pull the dates due since the live checkpoint, then STATE at the last unbroken day."""
        dates = knobs["dates"]
        declaration = self._declaration(knobs)
        through, absent = self._checkpoint(state, declaration, dates)
        today = parse_utc(utc_now()).astimezone(datetime.timezone.utc).date()

        def is_old(day):
            return (today - datetime.date.fromisoformat(day)).days > dates["give_up_days"]

        due = [d for d in _days(through + datetime.timedelta(days=1),
                                today - datetime.timedelta(days=dates["lag_days"]))
               if d not in absent]
        if due:
            refused = yield from self._pull(self._window(knobs, due), is_old)
            blocking = [d for d in refused if not is_old(d)]
            kept = due if not blocking else due[:due.index(blocking[0])]
            passed = [d for d in refused if is_old(d) and d in kept]
            for day in passed:
                yield {"protocol": PROTOCOL, "type": "LOG",
                       "message": f"giving up on {day}: still refused, over give_up_days "
                                  f"{dates['give_up_days']} old; recorded absent"}
            absent = absent + passed
            if kept:
                through = datetime.date.fromisoformat(kept[-1])
        else:
            yield {"protocol": PROTOCOL, "type": "LOG",
                   "message": f"no day due after {through.isoformat()} — nothing new"}
        yield {"protocol": PROTOCOL, "type": "STATE",
               "state": {"declaration": declaration, _THROUGH: through.isoformat(), _ABSENT: absent}}

    @staticmethod
    def _checkpoint(state, declaration, dates):
        """Return ``(through date, absent days)`` of the live checkpoint.

        With no checkpoint for this declaration the pull starts ``give_up_days``
        before the backfill end (never before ``start``), so a backfill day the
        vendor refused, and which may still be published, is tried again.
        """
        if state.get("declaration") != declaration or _THROUGH not in state:
            reach = datetime.timedelta(days=dates["give_up_days"] + 1)
            return max(dates["start"] - datetime.timedelta(days=1), dates["backfill_end"] - reach), []
        text = state[_THROUGH]
        if not (isinstance(text, str) and _DAY.match(text)):
            raise AssetError([f"live checkpoint {_THROUGH} must be a YYYY-MM-DD date, got {text!r}"])
        try:
            through = datetime.date.fromisoformat(text)
        except ValueError as exc:
            raise AssetError([f"live checkpoint {_THROUGH} {text!r} is not a date: {exc}"]) from exc
        absent = state.get(_ABSENT, [])
        if not (isinstance(absent, list) and all(isinstance(d, str) and _DAY.match(d) for d in absent)):
            raise AssetError([f"live checkpoint {_ABSENT} must be a list of YYYY-MM-DD dates, got {absent!r}"])
        return through, list(absent)

    def _window(self, knobs, days):
        """``knobs`` re-targeted at ``days``: their entities and rendered relpaths."""
        problems = []
        templates = {k: knobs[k] for k in _TEMPLATE_KEYS if k in knobs}
        paths = self._relpaths(problems, templates, days)
        _raise_if(problems)
        return {**knobs, "entities": days, "paths": paths}

    def _pull(self, knobs, is_old=None):
        """Yield FILE + RECORD + LOG for ``knobs['entities']``; return the refused entities in order.

        A refused entity for which ``is_old(entity)`` is true is recorded but does not count
        toward ``max_refused``.
        """
        transform = self._transform(knobs)
        stream = knobs["stream"]
        last_request = None
        origins = {"cache": 0, "network": 0}
        refused = []
        counted = 0
        with tempfile.TemporaryDirectory(prefix="httpblobs-") as stage:
            for index, entity in enumerate(knobs["entities"]):
                origin, status, body, last_request = self._obtain(knobs, entity, last_request)
                origins[origin] += 1
                rels = knobs["paths"][entity]
                outcome = self._outcome(entity, status, body, transform)
                if outcome["refused"]:
                    refused.append(entity)
                    counted += 0 if is_old is not None and is_old(entity) else 1
                    if counted > knobs["max_refused"]:
                        raise AssetError([f"{counted} entities refused, over max_refused "
                                          f"{knobs['max_refused']} (latest {entity!r}: "
                                          f"{outcome['refused']})"])
                    yield self._message(knobs, self._record(
                        entity, status="refused", http_status=status,
                        reason=outcome["refused"]))
                    continue
                files = [(rels["relpath_template"], outcome["payload"])]
                if "raw_relpath_template" in rels:
                    files.append((rels["raw_relpath_template"], body))
                for rel, data in files:
                    full = os.path.join(stage, str(index), *rel.split("/"))
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "wb") as fh:
                        fh.write(data)
                    yield {"protocol": PROTOCOL, "type": "FILE", "stream": stream,
                           "relpath": rel, "path": full}
                yield self._message(knobs, self._record(
                    entity, status="ok", http_status=status, note=outcome["note"],
                    relpath=rels["relpath_template"], size=len(outcome["payload"]),
                    sha256=hashlib.sha256(outcome["payload"]).hexdigest(),
                    raw_sha256=hashlib.sha256(body).hexdigest()))
        yield {"protocol": PROTOCOL, "type": "LOG",
               "message": f"{len(knobs['entities'])} entities: {origins['cache']} from cache, "
                          f"{origins['network']} from network, {len(refused)} refused"}
        return refused

    @staticmethod
    def _outcome(entity, status, body, transform):
        """``{"refused": reason|None, "payload": bytes, "note": str|None}`` for one response."""
        if not 200 <= status < 300:
            return {"refused": f"HTTP {status}", "payload": None, "note": None}
        if transform is None:
            return {"refused": None, "payload": body, "note": None}
        try:
            note = transform.note(entity, body) if hasattr(transform, "note") else None
            return {"refused": None, "payload": transform.transform(entity, body), "note": note}
        except Exception as exc:  # a reshape failure is one entity's refusal, not the pull's
            return {"refused": f"transform: {exc}", "payload": None, "note": None}

    @staticmethod
    def _message(knobs, data):
        """Wrap an inventory row as a RECORD message."""
        return {"protocol": PROTOCOL, "type": "RECORD", "stream": knobs["stream"],
                "effective_date": knobs["as_of"], "data": data}

"""``restapi`` — declarative JSON-over-HTTP connector (stdlib urllib).

Any REST-ish JSON API becomes a source by DECLARING its shape in config
(ADR-0017): the streams (endpoint paths), where the records live in each
response, how pages chain, and how the one credential is injected. No
subclassing, no per-API code — the Airbyte "low-code declarative source"
idea, shrunk to this platform's four-verb contract.

Config knobs (default-deny, per ``spec()``):

- ``base_url`` (required) — ``http(s)://…`` root; stream paths append.
- ``streams`` (required) — ``{name: declaration}``; each declaration is
  default-deny too: ``path`` (required), ``params`` (static query
  params), ``records_path`` (dot-path to the record list in the response
  body; absent means the body IS the list), ``schema``, ``primary_key``,
  ``notes``.
- ``effective_field`` (required) — key holding each record's ISO
  ``effective_date``.
- ``forecast_streams`` — stream names DECLARED forecasts (kind is a
  declared fact, never inferred from dates).
- ``pagination`` — ``{"strategy": …}``, a closed vocabulary:
  ``none`` (default; one request per stream);
  ``cursor`` — extract a token from the response at ``path``; inject it
  as query param ``param``, or, with no ``param``, treat it as the next
  absolute URL; stops when the token is absent/empty;
  ``page`` — page numbers in ``param`` from ``start`` (default 1),
  optionally sending ``size_param``/``page_size``; stops on an empty
  (or, when ``page_size`` is declared, short) page;
  ``offset`` — ``offset_param``/``limit_param``/``page_size``; stops on
  a short page.
- ``secret`` + ``auth_name`` / ``auth_in`` / ``auth_format`` — ONE
  credential: ``secret`` is the env-var NAME (the secret-knob contract —
  material never enters configs, stores, or hashes), formatted through
  ``auth_format`` (default ``"{secret}"``, e.g. ``"Bearer {secret}"``)
  into header or query param ``auth_name`` per ``auth_in``
  (``header`` | ``param``, default ``header``).
- ``since_param`` — query param that receives the stream's cursor so a
  server that can filter does; the client-side cursor filter still
  applies regardless, so an over-returning server stays harmless.
- ``timeout`` — request timeout in seconds (default 30).
- ``max_retries`` — extra attempts on 429/5xx/network errors with
  exponential backoff (default 3).

Cursor semantics are localfiles' exactly: state maps stream ->
``{"cursor": <max effective_date emitted>}`` and the logic is identical
in both modes — the platform keys checkpoints per (source, stream,
mode), ADR-0014. Pages are buffered per stream and sorted by effective
date before emission so the checkpoint ("everything before this is
durable") stays honest.

Every request goes through the single ``_fetch`` seam; retry and JSON
parsing sit above it, so tests script the transport with no network and
no mock library. Error messages carry URLs with the query string
stripped — a param-carried credential can never leak into an error.

Import cost: stdlib only.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse

from ..base import AssetError, _check_dict, _check_str, _check_unknown, _raise_if, parse_utc
from ..connector import PROTOCOL, Connector

__all__ = ["RestApiConnector"]

#: HTTP statuses worth retrying — throttling and transient server faults.
_RETRY_STATUSES = (429, 500, 502, 503, 504)

#: Backoff base in seconds, doubled per attempt. Module-level so the test
#: suite can zero it out instead of sleeping through retry scenarios.
_BACKOFF = 0.5

#: Default ``config.timeout`` (seconds) and ``config.max_retries`` — named
#: once so the module docstring and spec() notes can reference them and
#: never drift from what ``_conf`` actually applies.
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_RETRIES = 3

# Default-deny key sets for the nested declarations.
_STREAM_KEYS = ("path", "params", "records_path", "schema", "primary_key", "notes")
_PAGINATION_KEYS = {
    "none": ("strategy",),
    "cursor": ("strategy", "path", "param"),
    "page": ("strategy", "param", "start", "size_param", "page_size"),
    "offset": ("strategy", "offset_param", "limit_param", "page_size"),
}


def _safe(url):
    """A URL fit for an error message: query string stripped, because a
    param-injected credential must never leak through an exception."""
    return url.split("?", 1)[0]


def _pluck(obj, path):
    """Follow a dot-path through nested dicts; None where any hop is absent."""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class RestApiConnector(Connector):
    """A declared JSON API, one stream per endpoint. See module docs."""

    def spec(self) -> dict:
        return {
            "params": {
                "base_url": {
                    "required": True,
                    "notes": "http(s) root URL; each stream's path appends to it.",
                },
                "streams": {
                    "required": True,
                    "notes": "stream name -> {path, params, records_path, "
                             "schema, primary_key, notes} — the declared shape "
                             "of each endpoint.",
                },
                "effective_field": {
                    "required": True,
                    "notes": "Record key carrying each row's ISO effective_date.",
                },
                "forecast_streams": {
                    "notes": "Stream names emitted as kind=forecast — a "
                             "declared fact, never inferred from dates.",
                },
                "pagination": {
                    "notes": "{'strategy': 'none'|'cursor'|'page'|'offset', ...} "
                             "— how pages chain; see the module docs.",
                },
                "secret": {
                    "secret": True,
                    "notes": "NAME of the env var holding the credential — "
                             "material never enters config, stores, or hashes.",
                },
                "auth_name": {
                    "notes": "Header or query-param name receiving the "
                             "formatted credential; required with `secret`.",
                },
                "auth_in": {
                    "notes": "'header' (default) or 'param' — where the "
                             "credential goes.",
                },
                "auth_format": {
                    "notes": "Template with a {secret} placeholder; default "
                             "'{secret}', e.g. 'Bearer {secret}'.",
                },
                "since_param": {
                    "notes": "Query param that receives the stream's cursor "
                             "for server-side filtering; the client-side "
                             "filter still applies.",
                },
                "timeout": {
                    "notes": f"Request timeout in seconds; default "
                             f"{_DEFAULT_TIMEOUT}.",
                },
                "max_retries": {
                    "notes": "Extra attempts on 429/5xx/network errors, "
                             f"exponential backoff; default {_DEFAULT_MAX_RETRIES}.",
                },
            },
        }

    # -- config validation -------------------------------------------------

    def _conf(self, config) -> dict:
        """Validate every knob's SHAPE (check_config covers names) and
        return the resolved values with defaults applied. Defensive on
        every verb — a connector must not trust its caller."""
        errors = []
        _check_dict(errors, "config", config)
        _raise_if(errors)

        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            errors.append(f"config.base_url must be an http(s) URL, got {base_url!r}")

        streams = config.get("streams")
        _check_dict(errors, "config.streams", streams)
        if isinstance(streams, dict):
            if not streams:
                errors.append("config.streams must declare at least one stream")
            for name, decl in sorted(streams.items()):
                where = f"config.streams.{name}"
                _check_dict(errors, where, decl)
                if not isinstance(decl, dict):
                    continue
                _check_unknown(errors, decl, _STREAM_KEYS, where)
                _check_str(errors, f"{where}.path", decl.get("path", ""))
                if "params" in decl:
                    _check_dict(errors, f"{where}.params", decl["params"])
                if "records_path" in decl:
                    _check_str(errors, f"{where}.records_path", decl["records_path"])
                if "schema" in decl:
                    _check_dict(errors, f"{where}.schema", decl["schema"])
                if "primary_key" in decl and not isinstance(decl["primary_key"], list):
                    errors.append(f"{where}.primary_key must be a list")

        _check_str(errors, "config.effective_field", config.get("effective_field", ""))

        forecast_streams = config.get("forecast_streams", [])
        if not isinstance(forecast_streams, list):
            errors.append(
                f"config.forecast_streams must be a list, got {forecast_streams!r}"
            )

        pagination = config.get("pagination", {"strategy": "none"})
        _check_dict(errors, "config.pagination", pagination)
        if isinstance(pagination, dict):
            strategy = pagination.get("strategy")
            if strategy not in _PAGINATION_KEYS:
                errors.append(
                    f"config.pagination.strategy must be one of "
                    f"{sorted(_PAGINATION_KEYS)}, got {strategy!r}"
                )
            else:
                _check_unknown(errors, pagination, _PAGINATION_KEYS[strategy],
                               "config.pagination")
                if strategy == "cursor":
                    _check_str(errors, "config.pagination.path",
                               pagination.get("path", ""))
                elif strategy == "page":
                    _check_str(errors, "config.pagination.param",
                               pagination.get("param", ""))
                    start = pagination.get("start", 1)
                    if not isinstance(start, int) or start < 0:
                        errors.append(
                            f"config.pagination.start must be an int >= 0, got {start!r}"
                        )
                    if ("size_param" in pagination) != ("page_size" in pagination):
                        errors.append(
                            "config.pagination: size_param and page_size go together"
                        )
                if strategy in ("page", "offset") and "page_size" in pagination:
                    size = pagination["page_size"]
                    if not isinstance(size, int) or size < 1:
                        errors.append(
                            f"config.pagination.page_size must be an int >= 1, got {size!r}"
                        )
                if strategy == "offset":
                    _check_str(errors, "config.pagination.offset_param",
                               pagination.get("offset_param", ""))
                    _check_str(errors, "config.pagination.limit_param",
                               pagination.get("limit_param", ""))
                    if "page_size" not in pagination:
                        errors.append("config.pagination.page_size is required "
                                      "for the offset strategy")

        secret = config.get("secret")
        auth_in = config.get("auth_in", "header")
        auth_format = config.get("auth_format", "{secret}")
        if secret is not None:
            _check_str(errors, "config.secret", secret)
            _check_str(errors, "config.auth_name", config.get("auth_name", ""))
            if auth_in not in ("header", "param"):
                errors.append(
                    f"config.auth_in must be 'header' or 'param', got {auth_in!r}"
                )
            if not isinstance(auth_format, str) or "{secret}" not in auth_format:
                errors.append(
                    f"config.auth_format must contain '{{secret}}', got {auth_format!r}"
                )
        else:
            dangling = sorted(k for k in ("auth_name", "auth_in", "auth_format")
                              if k in config)
            if dangling:
                errors.append(
                    f"config {dangling} given without `secret` — the credential "
                    "knob anchors auth"
                )

        if "since_param" in config:
            _check_str(errors, "config.since_param", config["since_param"])

        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            errors.append(f"config.timeout must be a positive number, got {timeout!r}")
        max_retries = config.get("max_retries", _DEFAULT_MAX_RETRIES)
        if not isinstance(max_retries, int) or isinstance(max_retries, bool) or max_retries < 0:
            errors.append(
                f"config.max_retries must be an int >= 0, got {max_retries!r}"
            )
        _raise_if(errors)

        return {
            "base_url": base_url,
            "streams": streams,
            "effective_field": config["effective_field"],
            "forecast_streams": forecast_streams,
            "pagination": pagination,
            "secret": secret,
            "auth_name": config.get("auth_name"),
            "auth_in": auth_in,
            "auth_format": auth_format,
            "since_param": config.get("since_param"),
            "timeout": timeout,
            "max_retries": max_retries,
        }

    def _auth(self, cfg):
        """Resolve the credential -> (headers, query params) to inject.

        The env var is read here and nowhere else; its NAME travels in
        config, its VALUE travels only in the request.
        """
        if cfg["secret"] is None:
            return {}, {}
        value = os.environ.get(cfg["secret"], "")
        if not value:
            raise AssetError(
                [f"secret env var {cfg['secret']!r} is not set (or empty) — "
                 "config carries the NAME, the environment carries the material"]
            )
        formatted = cfg["auth_format"].format(secret=value)
        if cfg["auth_in"] == "header":
            return {cfg["auth_name"]: formatted}, {}
        return {}, {cfg["auth_name"]: formatted}

    # -- transport ---------------------------------------------------------

    def _fetch(self, url, headers, timeout):
        """One HTTP GET -> ``(status, body bytes)`` — the single network
        seam. Tests replace exactly this; retry/parse logic above it
        stays exercised. Network-lib import stays inside, tier-2 style."""
        import urllib.error
        import urllib.request

        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _get_json(self, url, headers, cfg, label):
        """GET with retry/backoff; parse the body as JSON or raise."""
        last = None
        for attempt in range(cfg["max_retries"] + 1):
            if attempt:
                time.sleep(_BACKOFF * (2 ** (attempt - 1)))
            try:
                status, body = self._fetch(url, headers, cfg["timeout"])
            except OSError as exc:
                # urllib's URLError is an OSError: connection refused,
                # DNS failure, timeout — all transient, all retryable.
                last = f"network error: {exc}"
                continue
            if status in _RETRY_STATUSES:
                last = f"HTTP {status}"
                continue
            if not 200 <= status < 300:
                raise AssetError(
                    [f"{label}: HTTP {status} from {_safe(url)}: {body[:200]!r}"]
                )
            try:
                return json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise AssetError(
                    [f"{label}: response from {_safe(url)} is not JSON: {exc}"]
                ) from exc
        raise AssetError(
            [f"{label}: giving up on {_safe(url)} after "
             f"{cfg['max_retries'] + 1} attempt(s) — last failure: {last}"]
        )

    def _url(self, base_url, path, params):
        """base + path + encoded query; a path may carry its own query."""
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params)}"
        return url

    def _records(self, body, records_path, label):
        """The record list out of one response body, path errors named."""
        if records_path:
            batch = _pluck(body, records_path)
            if not isinstance(batch, list):
                raise AssetError(
                    [f"{label}: records_path {records_path!r} does not lead to "
                     f"a list in the response"]
                )
        else:
            batch = body
            if not isinstance(batch, list):
                raise AssetError(
                    [f"{label}: response body is not a list — declare "
                     "records_path to point at the record list"]
                )
        for i, rec in enumerate(batch):
            if not isinstance(rec, dict):
                raise AssetError(
                    [f"{label}: record {i} is not an object, got "
                     f"{type(rec).__name__}"]
                )
        return batch

    def _pages(self, cfg, decl, params, headers, auth_params, stream):
        """All records for one stream, walking the declared pagination.

        Buffers every page — sorting by effective date needs the whole
        pull in hand before emission (the honest-cursor rule).
        """
        pag = cfg["pagination"]
        strategy = pag["strategy"]
        label = f"stream {stream!r}"
        records = []
        page = pag.get("start", 1)
        offset = 0
        token = None
        next_url = None
        while True:
            if next_url is not None:
                url = next_url
            else:
                q = dict(params)
                if strategy == "page":
                    q[pag["param"]] = page
                    if "size_param" in pag:
                        q[pag["size_param"]] = pag["page_size"]
                elif strategy == "offset":
                    q[pag["offset_param"]] = offset
                    q[pag["limit_param"]] = pag["page_size"]
                elif strategy == "cursor" and token is not None:
                    q[pag["param"]] = token
                url = self._url(cfg["base_url"], decl["path"], q)
            body = self._get_json(url, headers, cfg, label)
            batch = self._records(body, decl.get("records_path"), label)
            records.extend(batch)

            if strategy == "none":
                break
            if strategy == "cursor":
                prev, token = token, _pluck(body, pag["path"])
                if token is None or token == "":
                    break
                if token == prev:
                    raise AssetError(
                        [f"{label}: pagination token did not advance — "
                         "refusing an infinite loop"]
                    )
                if "param" not in pag:
                    if not isinstance(token, str) or not token.startswith(
                            ("http://", "https://")):
                        raise AssetError(
                            [f"{label}: without pagination.param the token must "
                             f"be an absolute next URL, got {_safe(str(token))!r}"]
                        )
                    # A next URL encodes the server's own query; only the
                    # credential (when param-carried) must ride along.
                    next_url = token
                    if auth_params:
                        sep = "&" if "?" in next_url else "?"
                        next_url += sep + urllib.parse.urlencode(auth_params)
            elif strategy == "page":
                if not batch or ("page_size" in pag and len(batch) < pag["page_size"]):
                    break
                page += 1
            elif strategy == "offset":
                if len(batch) < pag["page_size"]:
                    break
                offset += pag["page_size"]
        return records

    # -- the four verbs ----------------------------------------------------

    def check(self, config) -> None:
        """Fail fast: shapes valid, credential resolvable, one probe
        request to the first declared stream — body discarded."""
        cfg = self._conf(config)
        headers, auth_params = self._auth(cfg)
        stream = sorted(cfg["streams"])[0]
        decl = cfg["streams"][stream]
        params = dict(decl.get("params", {}))
        params.update(auth_params)
        url = self._url(cfg["base_url"], decl["path"], params)
        body = self._get_json(url, headers, cfg, f"stream {stream!r}")
        self._records(body, decl.get("records_path"), f"stream {stream!r}")

    def discover(self, config) -> list:
        """The declared streams, verbatim — no network (discover is cheap);
        a declaration without a schema discovers as empty fields."""
        cfg = self._conf(config)
        out = []
        for name in sorted(cfg["streams"]):
            decl = cfg["streams"][name]
            out.append({
                "stream": name,
                "schema": decl.get("schema", {"fields": []}),
                "primary_key": decl.get("primary_key", []),
            })
        return out

    def read(self, config, streams, state, mode):
        """Per stream: paginate, buffer, sort by effective date, emit
        SCHEMA then cursor-filtered RECORDs; one STATE at the end.

        The cursor filter runs client-side even when ``since_param``
        pushed it server-side — an over-returning server is harmless.
        """
        errors = []
        _check_dict(errors, "state", state)
        if not isinstance(streams, list) or not streams:
            errors.append(f"streams must be a non-empty list, got {streams!r}")
        _raise_if(errors)
        cfg = self._conf(config)
        headers, auth_params = self._auth(cfg)
        eff_field = cfg["effective_field"]
        new_state = {k: dict(v) for k, v in state.items()}

        for stream in streams:
            decl = cfg["streams"].get(stream)
            if decl is None:
                raise AssetError(
                    [f"unknown stream {stream!r} — declared: "
                     f"{sorted(cfg['streams'])}"]
                )
            cursor = state.get(stream, {}).get("cursor", "")
            cursor_dt = parse_utc(cursor) if cursor else None
            kind = "forecast" if stream in cfg["forecast_streams"] else "observation"

            params = dict(decl.get("params", {}))
            params.update(auth_params)
            if cfg["since_param"] and cursor:
                params[cfg["since_param"]] = cursor

            rows = []
            for i, rec in enumerate(
                    self._pages(cfg, decl, params, headers, auth_params, stream)):
                eff = rec.get(eff_field)
                if not isinstance(eff, str) or not eff:
                    raise AssetError(
                        [f"stream {stream!r}: record {i}: field {eff_field!r} "
                         "missing or empty — every record needs its "
                         "effective_date"]
                    )
                rows.append((parse_utc(eff), eff, rec))
            rows.sort(key=lambda t: t[0])

            schema = decl.get("schema") or (
                {"fields": sorted(rows[0][2])} if rows else {"fields": []}
            )
            yield {"protocol": PROTOCOL, "type": "SCHEMA", "stream": stream,
                   "schema": schema}

            emitted_max = cursor
            for eff_dt, eff, rec in rows:
                if cursor_dt is not None and eff_dt <= cursor_dt:
                    continue  # already durable per the checkpoint
                yield {"protocol": PROTOCOL, "type": "RECORD", "stream": stream,
                       "effective_date": eff, "kind": kind, "data": rec}
                emitted_max = eff
            new_state.setdefault(stream, {})["cursor"] = emitted_max

        yield {"protocol": PROTOCOL, "type": "STATE", "state": new_state}

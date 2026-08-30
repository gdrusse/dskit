"""OAuth2 refresh tokens without putting credentials into configuration.

The service receives environment-variable names, never values. It resolves
material only at the request boundary, stores token responses with durable
rename semantics, and refuses token files readable by another user. Initial
authorization stays manual: print :meth:`OAuth2TokenService.authorization_url`,
complete the browser flow, then pass the returned URL to ``exchange``.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import AssetError, durable_write_json

__all__ = ["OAuth2TokenService", "load_token", "save_token"]

_REFRESH_MARGIN_SECONDS = 60


def load_token(path):
    """Load one owner-only token file.

    Parameters
    ----------
    path : str
        Token JSON path resolved from the environment.

    Returns
    -------
    dict
        The persisted token object.

    Raises
    ------
    AssetError
        If the path is absent, unsafe, unreadable, or not a JSON object.
    """
    if not isinstance(path, str) or not path:
        raise AssetError(["token path must be a non-empty string"])
    try:
        info = os.stat(path)
    except OSError as exc:
        raise AssetError(
            [f"cannot read OAuth token file {path!r}: {exc}; authorize again"]
        ) from exc
    mode = stat.S_IMODE(info.st_mode)
    if not stat.S_ISREG(info.st_mode) or not mode & stat.S_IRUSR or mode & 0o077:
        raise AssetError(
            [f"OAuth token file {path!r} must be a regular owner-only file "
             "(mode 0600); authorize again after securing it"]
        )
    try:
        with open(path, encoding="utf-8") as fh:
            token = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AssetError(
            [f"OAuth token file {path!r} is unreadable or malformed; "
             "authorize again"]
        ) from exc
    if not isinstance(token, dict):
        raise AssetError(
            [f"OAuth token file {path!r} must contain a JSON object; "
             "authorize again"]
        )
    return token


def save_token(path, token):
    """Persist a token atomically with mode ``0600``.

    Parameters
    ----------
    path : str
        Destination token path.
    token : dict
        JSON-serializable token response.

    Returns
    -------
    None
        The durable file is complete on return.

    Raises
    ------
    AssetError
        If the path or token is malformed.
    """
    if not isinstance(path, str) or not path:
        raise AssetError(["token path must be a non-empty string"])
    if not isinstance(token, dict):
        raise AssetError(
            [f"token must be a JSON object, got {type(token).__name__}"]
        )
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        durable_write_json(path, token)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise AssetError(
            [f"cannot persist OAuth token file {path!r}: {exc}"]
        ) from exc


class OAuth2TokenService:
    """Authorization-code exchange and automatic refresh.

    Parameters
    ----------
    client_id_env : str
        Environment-variable name holding the OAuth client id.
    client_secret_env : str
        Environment-variable name holding the OAuth client secret.
    callback_url_env : str
        Environment-variable name holding the registered callback URL.
    token_path_env : str
        Environment-variable name holding the owner-only token path.
    authorization_url : str
        Provider authorization endpoint.
    token_url : str
        Provider token endpoint.
    scope : str
        Optional space-delimited OAuth scope.

    Examples
    --------
    Build a service whose configuration contains names, not secrets::

        service = OAuth2TokenService(
            client_id_env="VENDOR_CLIENT_ID",
            client_secret_env="VENDOR_CLIENT_SECRET",
            callback_url_env="VENDOR_CALLBACK_URL",
            token_path_env="VENDOR_TOKEN_PATH",
            authorization_url="https://vendor.example/oauth/authorize",
            token_url="https://vendor.example/oauth/token",
        )
    """

    def __init__(
            self, client_id_env, client_secret_env, callback_url_env,
            token_path_env, authorization_url, token_url, scope="",
            lookup=None):
        values = {
            "client_id_env": client_id_env,
            "client_secret_env": client_secret_env,
            "callback_url_env": callback_url_env,
            "token_path_env": token_path_env,
        }
        problems = [
            f"{name} must be a non-empty environment-variable name"
            for name, value in values.items()
            if not isinstance(value, str) or not value
        ]
        for name, value in (
                ("authorization_url", authorization_url),
                ("token_url", token_url)):
            if not isinstance(value, str) or not value.startswith(
                    ("http://", "https://")):
                problems.append(f"{name} must be an http(s) URL")
        if not isinstance(scope, str):
            problems.append(f"scope must be a string, got {type(scope).__name__}")
        if problems:
            raise AssetError(problems)
        self.client_id_env = client_id_env
        self.client_secret_env = client_secret_env
        self.callback_url_env = callback_url_env
        self.token_path_env = token_path_env
        self.authorization_url_endpoint = authorization_url
        self.token_url = token_url
        self.scope = scope
        self._lookup = os.environ.get if lookup is None else lookup

    def _now(self):
        """Current epoch seconds; a seam for deterministic expiry tests."""
        return time.time()

    def _settings(self):
        """Resolve named values, reporting names and never material."""
        names = {
            "client_id": self.client_id_env,
            "client_secret": self.client_secret_env,
            "callback_url": self.callback_url_env,
            "token_path": self.token_path_env,
        }
        values = {key: self._lookup(name, "") for key, name in names.items()}
        missing = [names[key] for key, value in values.items() if not value]
        if missing:
            raise AssetError(
                [f"OAuth environment variable(s) {missing} are missing or empty"]
            )
        return values

    def authorization_url(self):
        """Build the URL an operator opens for initial authorization.

        Returns
        -------
        str
            Provider URL containing public client and callback values.

        Raises
        ------
        AssetError
            If any named environment value is absent.
        """
        settings = self._settings()
        query = {
            "response_type": "code",
            "client_id": settings["client_id"],
            "redirect_uri": settings["callback_url"],
        }
        if self.scope:
            query["scope"] = self.scope
        return f"{self.authorization_url_endpoint}?" + urllib.parse.urlencode(query)

    def _post(self, form):
        """POST one token grant; response bodies never enter failures."""
        settings = self._settings()
        credentials = (
            f"{settings['client_id']}:{settings['client_secret']}".encode("utf-8")
        )
        request = urllib.request.Request(
            self.token_url,
            data=urllib.parse.urlencode(form).encode("utf-8"),
            headers={
                "Authorization": "Basic " + base64.b64encode(credentials).decode("ascii"),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = response.getcode()
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise AssetError(
                [f"OAuth token request failed (HTTP {exc.code}); authorize again"]
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise AssetError(
                ["OAuth token request failed at the network boundary; "
                 "authorize again if the refresh grant has expired"]
            ) from exc
        if not 200 <= status < 300:
            raise AssetError(
                [f"OAuth token request failed (HTTP {status}); authorize again"]
            )
        try:
            token = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AssetError(
                ["OAuth token endpoint returned malformed JSON; authorize again"]
            ) from exc
        if not isinstance(token, dict):
            raise AssetError(
                ["OAuth token endpoint returned a non-object; authorize again"]
            )
        return token

    def _stamp(self, token):
        """Validate a response and attach its absolute expiry."""
        access_token = token.get("access_token")
        expires_in = token.get("expires_in")
        problems = []
        if not isinstance(access_token, str) or not access_token:
            problems.append("OAuth token response lacks a non-empty access_token")
        if (
            isinstance(expires_in, bool)
            or not isinstance(expires_in, (int, float))
            or expires_in <= 0
        ):
            problems.append("OAuth token response lacks a positive expires_in")
        if problems:
            raise AssetError([f"{problem}; authorize again" for problem in problems])
        stamped = dict(token)
        stamped["obtained_at"] = self._now()
        stamped["expires_at"] = stamped["obtained_at"] + expires_in
        return stamped

    def _code(self, returned):
        """Extract and decode a code from a callback URL or raw value."""
        if not isinstance(returned, str) or not returned:
            raise AssetError(["authorization code must be a non-empty string"])
        if "://" not in returned and "?" not in returned:
            return urllib.parse.unquote(returned)
        query = urllib.parse.parse_qs(
            urllib.parse.urlsplit(returned).query, keep_blank_values=True
        )
        if query.get("error"):
            raise AssetError(
                [f"OAuth authorization failed with {query['error'][0]!r}; "
                 "authorize again"]
            )
        code = query.get("code", [""])[0]
        if not code:
            raise AssetError(
                ["OAuth callback URL has no authorization code; authorize again"]
            )
        return code

    def exchange(self, returned):
        """Exchange a manual authorization result and save the token.

        Parameters
        ----------
        returned : str
            Raw code or complete callback URL copied from the browser.

        Returns
        -------
        dict
            Validated token metadata, also persisted owner-only.

        Raises
        ------
        AssetError
            If the result, token response, or token path is invalid.
        """
        settings = self._settings()
        token = self._stamp(self._post({
            "grant_type": "authorization_code",
            "code": self._code(returned),
            "redirect_uri": settings["callback_url"],
        }))
        if not isinstance(token.get("refresh_token"), str) or not token["refresh_token"]:
            raise AssetError(
                ["OAuth authorization returned no refresh_token; authorize again"]
            )
        save_token(settings["token_path"], token)
        return token

    def ensure_access_token(self):
        """Return a fresh bearer token, refreshing and persisting if needed.

        Returns
        -------
        str
            Access-token material for the immediate provider request.

        Raises
        ------
        AssetError
            If the token is absent, unsafe, malformed, or cannot refresh.
        """
        settings = self._settings()
        token = load_token(settings["token_path"])
        access = token.get("access_token")
        expiry = token.get("expires_at")
        if (
            isinstance(access, str)
            and access
            and isinstance(expiry, (int, float))
            and not isinstance(expiry, bool)
            and expiry > self._now() + _REFRESH_MARGIN_SECONDS
        ):
            return access
        refresh = token.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise AssetError(
                ["OAuth token has no usable refresh grant; authorize again"]
            )
        response = self._post({
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        })
        merged = dict(token)
        merged.update(response)
        if not response.get("refresh_token"):
            merged["refresh_token"] = refresh
        refreshed = self._stamp(merged)
        save_token(settings["token_path"], refreshed)
        return refreshed["access_token"]

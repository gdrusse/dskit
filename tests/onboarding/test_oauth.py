"""OAuth2 refresh-token service: secure persistence and loud failures."""

import json
import os
import stat
import urllib.parse

import pytest

from dskit.assets.base import AssetError
from dskit.onboarding.oauth import OAuth2TokenService, load_token, save_token


@pytest.fixture
def oauth_env(monkeypatch, tmp_path):
    """Named OAuth values; only the names enter the service."""
    values = {
        "TEST_CLIENT_ID": "client-id",
        "TEST_CLIENT_SECRET": "client-SECRET",
        "TEST_CALLBACK": "https://127.0.0.1/callback",
        "TEST_TOKEN_PATH": str(tmp_path / "state" / "token.json"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def service(oauth_env):
    """A generic OAuth2 service with no live transport."""
    return OAuth2TokenService(
        client_id_env="TEST_CLIENT_ID",
        client_secret_env="TEST_CLIENT_SECRET",
        callback_url_env="TEST_CALLBACK",
        token_path_env="TEST_TOKEN_PATH",
        authorization_url="https://auth.example.test/authorize",
        token_url="https://auth.example.test/token",
        scope="read bars",
    )


def test_authorization_url_contains_public_values_only(service):
    url = service.authorization_url()
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query == {
        "client_id": ["client-id"],
        "redirect_uri": ["https://127.0.0.1/callback"],
        "response_type": ["code"],
        "scope": ["read bars"],
    }
    assert "client-SECRET" not in url


def test_exchange_accepts_redirect_url_and_saves_owner_only(service, oauth_env):
    seen = []
    service._post = lambda form: seen.append(dict(form)) or {
        "access_token": "access-SECRET",
        "refresh_token": "refresh-SECRET",
        "expires_in": 1800,
    }

    token = service.exchange(
        "https://127.0.0.1/callback?code=abc%40def&session=ignored"
    )

    assert seen == [{
        "grant_type": "authorization_code",
        "code": "abc@def",
        "redirect_uri": "https://127.0.0.1/callback",
    }]
    path = oauth_env["TEST_TOKEN_PATH"]
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert load_token(path)["access_token"] == "access-SECRET"
    assert token["expires_at"] > token["obtained_at"]


def test_ensure_access_token_refreshes_atomically(service, oauth_env, monkeypatch):
    path = oauth_env["TEST_TOKEN_PATH"]
    save_token(path, {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "expires_at": 10,
    })
    monkeypatch.setattr(service, "_now", lambda: 1000)
    seen = []
    service._post = lambda form: seen.append(dict(form)) or {
        "access_token": "new-access",
        "expires_in": 1800,
    }

    assert service.ensure_access_token() == "new-access"
    assert seen == [{
        "grant_type": "refresh_token",
        "refresh_token": "old-refresh",
    }]
    persisted = json.loads(open(path, encoding="utf-8").read())
    assert persisted["refresh_token"] == "old-refresh"
    assert persisted["access_token"] == "new-access"
    assert persisted["expires_at"] == 2800


def test_fresh_access_token_never_calls_transport(service, oauth_env, monkeypatch):
    save_token(oauth_env["TEST_TOKEN_PATH"], {
        "access_token": "still-fresh",
        "refresh_token": "refresh",
        "expires_at": 2000,
    })
    monkeypatch.setattr(service, "_now", lambda: 1000)
    service._post = lambda form: pytest.fail("fresh token must not refresh")
    assert service.ensure_access_token() == "still-fresh"


def test_token_and_environment_failures_name_remediation_not_material(
        service, oauth_env, monkeypatch):
    monkeypatch.delenv("TEST_CLIENT_SECRET")
    with pytest.raises(AssetError, match="TEST_CLIENT_SECRET") as exc:
        service.authorization_url()
    assert "client-SECRET" not in str(exc.value)

    monkeypatch.setenv("TEST_CLIENT_SECRET", "client-SECRET")
    path = oauth_env["TEST_TOKEN_PATH"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"access_token": "leaked-if-echoed"}')
    os.chmod(path, 0o644)
    with pytest.raises(AssetError, match="owner-only") as exc:
        load_token(path)
    assert "leaked-if-echoed" not in str(exc.value)

    os.chmod(path, 0o600)
    service._post = lambda form: (_ for _ in ()).throw(
        AssetError(["OAuth token request failed (HTTP 400); authorize again"])
    )
    with pytest.raises(AssetError, match="authorize again") as exc:
        service.ensure_access_token()
    assert "client-SECRET" not in str(exc.value)

"""Test flusso OAuth FIC.

Test unitari sulle funzioni pure (costruzione URL, parsing response).
Le chiamate HTTP sono simulate con httpx.MockTransport.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from freelance_budget.integrations.fatture_in_cloud.config import FicConfig
from freelance_budget.integrations.fatture_in_cloud.oauth import (
    FIC_AUTHORIZE_URL,
    FIC_TOKEN_URL,
    OAuthError,
    TokenPair,
    build_authorize_url,
    exchange_code_for_token,
    generate_state,
    refresh_access_token,
)
from freelance_budget.integrations.fatture_in_cloud.token_store import (
    load_token,
    save_token,
)


@pytest.fixture
def config(tmp_path):
    return FicConfig(
        client_id="test_client",
        client_secret="test_secret",
        redirect_uri="http://localhost:8000/auth/fic/callback",
        token_path=tmp_path / ".fic_token.json",
    )


# ---------- State + URL building ----------

def test_generate_state_returns_unique_tokens():
    s1, s2 = generate_state(), generate_state()
    assert s1 != s2
    assert len(s1) >= 32


def test_build_authorize_url_contains_all_required_params(config):
    url = build_authorize_url(config, state="abc123")
    parsed = urlparse(url)
    assert url.startswith(FIC_AUTHORIZE_URL)
    q = parse_qs(parsed.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["test_client"]
    assert q["redirect_uri"] == ["http://localhost:8000/auth/fic/callback"]
    assert q["state"] == ["abc123"]
    assert "issued_documents.invoices:r" in q["scope"][0]


def test_build_authorize_url_scope_space_separated(config):
    url = build_authorize_url(
        config, state="x", scopes=["issued_documents.invoices:r", "settings:r"]
    )
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["issued_documents.invoices:r settings:r"]


# ---------- Token exchange ----------

def _mock_transport(expected_body_keys: set, response_payload: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(FIC_TOKEN_URL)
        assert request.headers["content-type"] == "application/json"
        body = json.loads(request.content)
        assert set(body.keys()) >= expected_body_keys
        return httpx.Response(200, json=response_payload)
    return httpx.MockTransport(handler)


def test_exchange_code_for_token_sends_correct_body(config):
    transport = _mock_transport(
        expected_body_keys={"grant_type", "client_id", "client_secret", "redirect_uri", "code"},
        response_payload={
            "access_token": "AT-xyz",
            "refresh_token": "RT-abc",
            "expires_in": 3600,
            "scope": "issued_documents.invoices:r",
        },
    )
    with httpx.Client(transport=transport) as client:
        token = exchange_code_for_token(config, code="the_code", http_client=client)
    assert token.access_token == "AT-xyz"
    assert token.refresh_token == "RT-abc"
    assert token.scope == "issued_documents.invoices:r"
    assert not token.is_expired


def test_refresh_access_token_sends_refresh_grant(config):
    transport = _mock_transport(
        expected_body_keys={"grant_type", "client_id", "client_secret", "refresh_token"},
        response_payload={
            "access_token": "AT-new",
            "refresh_token": "RT-new",
            "expires_in": 7200,
            "scope": "issued_documents.invoices:r",
        },
    )
    with httpx.Client(transport=transport) as client:
        token = refresh_access_token(config, refresh_token="old_rt", http_client=client)
    assert token.access_token == "AT-new"


def test_token_endpoint_error_raises_oautherror(config):
    def handler(_request):
        return httpx.Response(400, json={"error": "invalid_grant"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        with pytest.raises(OAuthError, match="400"):
            exchange_code_for_token(config, code="bad", http_client=client)


# ---------- TokenPair ----------

def test_token_is_expired_true_when_past_expiry():
    t = TokenPair(
        access_token="x",
        refresh_token="y",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=5),
        scope="",
    )
    assert t.is_expired is True


def test_token_is_expired_true_within_margin():
    # Margine di 60s → anche se scade fra 30s, è considerato scaduto
    t = TokenPair(
        access_token="x",
        refresh_token="y",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
        scope="",
    )
    assert t.is_expired is True


def test_token_is_expired_false_when_far_from_expiry():
    t = TokenPair(
        access_token="x",
        refresh_token="y",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="",
    )
    assert t.is_expired is False


# ---------- Token store ----------

def test_save_and_load_token_roundtrip(tmp_path):
    path = tmp_path / "token.json"
    original = TokenPair(
        access_token="AT",
        refresh_token="RT",
        expires_at=datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc),
        scope="issued_documents.invoices:r",
    )
    save_token(original, path)
    assert path.exists()
    loaded = load_token(path)
    assert loaded is not None
    assert loaded.access_token == "AT"
    assert loaded.refresh_token == "RT"
    assert loaded.expires_at == original.expires_at
    assert loaded.scope == "issued_documents.invoices:r"


def test_load_token_returns_none_if_missing(tmp_path):
    assert load_token(tmp_path / "does_not_exist.json") is None

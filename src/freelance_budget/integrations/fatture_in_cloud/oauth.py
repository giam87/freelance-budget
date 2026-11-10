"""Flusso OAuth 2.0 Authorization Code per Fatture in Cloud.

Funzioni pure: costruzione URL e chiamate token endpoint. Niente persistenza
(se ne occupa token_store). Niente server HTTP (lo script authorize apre il
mini-server solo per la demo locale).

Riferimento: FIC SDK ufficiale, token endpoint con body JSON.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

import httpx

from .config import DEFAULT_SCOPES, FIC_AUTHORIZE_URL, FIC_TOKEN_URL, FicConfig


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_at: datetime   # UTC, tz-aware
    scope: str

    @property
    def is_expired(self) -> bool:
        # margine di 60s per evitare di inviare token che scadono durante il request
        return datetime.now(timezone.utc) >= (self.expires_at - timedelta(seconds=60))


def generate_state() -> str:
    """Genera token anti-CSRF per il parametro `state`."""
    return secrets.token_urlsafe(32)


def build_authorize_url(
    config: FicConfig,
    state: str,
    scopes: Iterable[str] = DEFAULT_SCOPES,
) -> str:
    """Costruisce l'URL di authorize da aprire nel browser."""
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(scopes),
        "state": state,
    }
    return f"{FIC_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(
    config: FicConfig,
    code: str,
    http_client: httpx.Client | None = None,
) -> TokenPair:
    """Scambia l'authorization code per access+refresh token."""
    body = {
        "grant_type": "authorization_code",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "code": code,
    }
    return _post_token(body, http_client)


def refresh_access_token(
    config: FicConfig,
    refresh_token: str,
    http_client: httpx.Client | None = None,
) -> TokenPair:
    body = {
        "grant_type": "refresh_token",
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "refresh_token": refresh_token,
    }
    return _post_token(body, http_client)


def _post_token(body: dict, http_client: httpx.Client | None) -> TokenPair:
    client = http_client or httpx.Client(timeout=30.0)
    try:
        response = client.post(
            FIC_TOKEN_URL,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    finally:
        if http_client is None:
            client.close()

    if response.status_code != 200:
        raise OAuthError(
            f"Token endpoint ha risposto {response.status_code}: {response.text}"
        )
    data = response.json()
    expires_in = int(data.get("expires_in", 3600))
    return TokenPair(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        scope=data.get("scope", ""),
    )


class OAuthError(Exception):
    pass

"""Test client HTTP FIC: refresh automatico, paginazione, errori."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from freelance_budget.integrations.fatture_in_cloud.client import (
    FicClient,
    FicClientError,
)
from freelance_budget.integrations.fatture_in_cloud.config import FIC_API_BASE, FicConfig
from freelance_budget.integrations.fatture_in_cloud.oauth import TokenPair


@pytest.fixture
def config(tmp_path):
    return FicConfig(
        client_id="cid",
        client_secret="csec",
        redirect_uri="http://localhost:8000/auth/fic/callback",
        token_path=tmp_path / "tok.json",
    )


def _valid_token() -> TokenPair:
    return TokenPair(
        access_token="AT",
        refresh_token="RT",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="issued_documents.invoices:r",
    )


def _expired_token() -> TokenPair:
    return TokenPair(
        access_token="AT_OLD",
        refresh_token="RT",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        scope="issued_documents.invoices:r",
    )


def test_raises_if_no_token(config):
    with pytest.raises(FicClientError, match="Token OAuth assente"):
        FicClient(config=config, token=None)


def test_list_companies_returns_parsed_list(config):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL(f"{FIC_API_BASE}/user/companies")
        assert request.headers["Authorization"] == "Bearer AT"
        return httpx.Response(200, json={"data": {"companies": [{"id": 1, "name": "ACME"}]}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        companies = client.list_companies()
    assert companies == [{"id": 1, "name": "ACME"}]


def test_list_issued_invoices_paginates_transparently(config):
    pages = {
        1: {"data": [{"id": 1}, {"id": 2}], "current_page": 1, "last_page": 2},
        2: {"data": [{"id": 3}], "current_page": 2, "last_page": 2},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages[page])

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        invoices = list(client.list_issued_invoices(company_id=42))
    assert [i["id"] for i in invoices] == [1, 2, 3]


def test_refreshes_expired_token_before_request(config, tmp_path):
    calls = {"refresh": 0, "api": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            calls["refresh"] += 1
            body = json.loads(request.content)
            assert body["grant_type"] == "refresh_token"
            assert body["refresh_token"] == "RT"
            return httpx.Response(200, json={
                "access_token": "AT_NEW",
                "refresh_token": "RT_NEW",
                "expires_in": 3600,
                "scope": "issued_documents.invoices:r",
            })
        if request.url.path == "/user/companies":
            calls["api"] += 1
            assert request.headers["Authorization"] == "Bearer AT_NEW"
            return httpx.Response(200, json={"data": {"companies": []}})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_expired_token(), http_client=http)
        client.list_companies()
    assert calls["refresh"] == 1
    assert calls["api"] == 1
    # Il token aggiornato è stato salvato su disco
    assert config.token_path.exists()
    saved = json.loads(config.token_path.read_text())
    assert saved["access_token"] == "AT_NEW"


def test_retries_once_on_401_with_refresh(config):
    call_sequence = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            call_sequence.append("refresh")
            return httpx.Response(200, json={
                "access_token": "AT_NEW",
                "refresh_token": "RT_NEW",
                "expires_in": 3600,
                "scope": "",
            })
        if request.url.path == "/user/companies":
            call_sequence.append(f"api:{request.headers['Authorization']}")
            if request.headers["Authorization"] == "Bearer AT":
                return httpx.Response(401, json={"error": "token_expired"})
            return httpx.Response(200, json={"data": {"companies": []}})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        client.list_companies()

    assert call_sequence == ["api:Bearer AT", "refresh", "api:Bearer AT_NEW"]


def test_get_issued_invoice_returns_detail(config):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/c/42/issued_documents/999"
        return httpx.Response(200, json={
            "data": {
                "id": 999,
                "number": 1,
                "amount_gross": 1560,
                "payments_list": [
                    {"amount": 1560, "status": "paid", "paid_date": "2026-03-10"}
                ],
            }
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        invoice = client.get_issued_invoice(company_id=42, document_id=999)
    assert invoice["id"] == 999
    assert invoice["payments_list"][0]["status"] == "paid"


def test_list_issued_invoices_passes_fields_param(config):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"data": [], "current_page": 1, "last_page": 1})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        list(client.list_issued_invoices(company_id=1, fields="payments_list"))
    assert captured["fields"] == "payments_list"


def test_api_error_raises_ficclienterror(config):
    def handler(_request):
        return httpx.Response(500, text="Internal Server Error")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = FicClient(config=config, token=_valid_token(), http_client=http)
        with pytest.raises(FicClientError, match="500"):
            client.list_companies()

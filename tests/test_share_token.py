"""Accesso con token condiviso (SHARE_TOKEN)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from freelance_budget.api import app as app_module


def _client(monkeypatch, token: str) -> TestClient:
    monkeypatch.setattr(app_module, "SHARE_TOKEN", token)
    return TestClient(app_module.app, base_url="https://testserver")


def test_senza_token_configurato_tutto_aperto(monkeypatch):
    assert _client(monkeypatch, "").get("/api/health").status_code == 200
    assert _client(monkeypatch, "").get("/openapi.json").status_code == 200


def test_senza_cookie_401(monkeypatch):
    c = _client(monkeypatch, "segreto")
    assert c.get("/openapi.json").status_code == 401
    assert c.get("/openapi.json?t=sbagliato").status_code == 401
    assert c.get("/api/health").status_code == 200  # healthcheck sempre libero


def test_link_con_token_imposta_cookie_e_pulisce_url(monkeypatch):
    c = _client(monkeypatch, "segreto")
    r = c.get("/openapi.json?t=segreto", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/openapi.json"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/openapi.json").status_code == 200  # cookie salvato dal client


def test_calendario_accetta_token_nell_url_senza_redirect(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        app_module, "dashboard",
        lambda: SimpleNamespace(scadenze_prossime=[], scadenze_anno_successivo=[]),
    )
    c = _client(monkeypatch, "segreto")
    assert c.get("/scadenze.ics").status_code == 401
    assert c.get("/scadenze.ics?t=sbagliato").status_code == 401
    r = c.get("/scadenze.ics?t=segreto", follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert "set-cookie" not in r.headers

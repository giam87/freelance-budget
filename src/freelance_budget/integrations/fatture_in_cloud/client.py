"""Client HTTP verso Fatture in Cloud API v2.

Gestisce:
  - caricamento access token dal token_store
  - refresh automatico quando il token è scaduto (o su 401)
  - paginazione degli endpoint list-style
  - serializzazione response → dict (la mappatura ai modelli di dominio vive in mapping.py)
"""
from __future__ import annotations

from typing import Any, Iterator

import httpx

from .config import FIC_API_BASE, FicConfig
from .oauth import TokenPair, refresh_access_token
from .token_store import load_token, save_token


class FicClientError(Exception):
    pass


class FicClient:
    def __init__(
        self,
        config: FicConfig,
        token: TokenPair | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.config = config
        self._token = token or load_token(config.token_path)
        if self._token is None:
            raise FicClientError(
                "Token OAuth assente. Esegui prima `python scripts/authorize_fic.py`."
            )
        self._http = http_client or httpx.Client(timeout=30.0)
        self._owns_http = http_client is None

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "FicClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Endpoint di alto livello
    # ------------------------------------------------------------------

    def list_companies(self) -> list[dict[str, Any]]:
        """GET /user/companies → lista aziende accessibili con questo token."""
        payload = self._get("/user/companies")
        # Secondo la doc, la response avvolge in `data`. Sono difensivo: supporto
        # entrambe le forme comuni per non crashare in caso di lievi variazioni.
        data = payload.get("data", payload)
        if isinstance(data, dict) and "companies" in data:
            return data["companies"]
        if isinstance(data, list):
            return data
        raise FicClientError(f"Formato inatteso response /user/companies: keys={list(payload.keys())}")

    def list_issued_invoices(
        self,
        company_id: int,
        per_page: int = 100,
        fields: str | None = None,
        fieldset: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Itera tutte le fatture emesse (tipo 'invoice') di una company.

        Gestisce paginazione trasparente: restituisce un iterator lazy.

        `fields`: elenco CSV di campi extra (es. "payments_list,amount_rivalsa").
        `fieldset`: alias preimpostato dell'SDK FIC. "basic" (default) è minimale,
        "detailed" include payments_list, ei_raw e altri campi annidati.
        Usare "detailed" per l'estrazione dell'incassato reale.
        """
        page = 1
        while True:
            params: dict[str, Any] = {
                "type": "invoice",
                "per_page": per_page,
                "page": page,
            }
            if fields:
                params["fields"] = fields
            if fieldset:
                params["fieldset"] = fieldset
            payload = self._get(
                f"/c/{company_id}/issued_documents",
                params=params,
            )
            items = payload.get("data", [])
            if not items:
                return
            yield from items

            # Paginazione: campi standard in API v2
            current = payload.get("current_page", page)
            last = payload.get("last_page", page)
            if current >= last:
                return
            page = current + 1

    def get_issued_invoice(
        self,
        company_id: int,
        document_id: int,
    ) -> dict[str, Any]:
        """GET dettaglio singola fattura emessa (include payments_list completa)."""
        payload = self._get(f"/c/{company_id}/issued_documents/{document_id}")
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise FicClientError(
                f"Formato inatteso response dettaglio fattura: {type(data).__name__}"
            )
        return data

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        self._ensure_fresh_token()
        response = self._request_with_auth("GET", path, params=params)
        if response.status_code == 401:
            # Token rifiutato nonostante nostro check → force refresh e retry una volta
            self._force_refresh()
            response = self._request_with_auth("GET", path, params=params)
        if response.status_code != 200:
            raise FicClientError(
                f"FIC API {path} ha risposto {response.status_code}: {response.text[:500]}"
            )
        return response.json()

    def _request_with_auth(self, method: str, path: str, **kwargs) -> httpx.Response:
        assert self._token is not None
        headers = kwargs.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {self._token.access_token}"
        headers["Accept"] = "application/json"
        return self._http.request(
            method, f"{FIC_API_BASE}{path}", headers=headers, **kwargs
        )

    def _ensure_fresh_token(self) -> None:
        assert self._token is not None
        if self._token.is_expired:
            self._force_refresh()

    def _force_refresh(self) -> None:
        assert self._token is not None
        if not self._token.refresh_token:
            raise FicClientError(
                "Token scaduto e refresh_token mancante. "
                "Rilanciare `python scripts/authorize_fic.py`."
            )
        new_token = refresh_access_token(
            self.config, self._token.refresh_token, http_client=self._http
        )
        save_token(new_token, self.config.token_path)
        self._token = new_token

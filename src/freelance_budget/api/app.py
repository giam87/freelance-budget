"""FastAPI app: espone la dashboard aggregata come JSON.

Design:
  - Un solo endpoint GET /api/dashboard per l'MVP.
  - Cache in-memory con TTL per evitare di martellare FIC a ogni refresh UI.
  - CORS aperto solo a localhost (dev Vite su 5173).
  - Errori FIC/config → 502/500 con ErrorResponse strutturato (niente stacktrace).
  - SHARE_TOKEN in .env → ogni richiesta (tranne /api/health) richiede il token:
    il link `/?t=<token>` imposta un cookie e ripulisce l'URL.
    Eccezione: /scadenze.ics accetta il token nell'URL senza redirect, perché
    le app calendario non conservano cookie.
"""
from __future__ import annotations

import hmac
import logging
import os
import time
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from ..integrations.fatture_in_cloud.client import FicClient, FicClientError
from ..integrations.fatture_in_cloud.config import FicConfig
from .dto import DashboardResponse, ErrorResponse
from .ics import scadenze_ics
from .services import DashboardService

log = logging.getLogger("freelance_budget.api")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
# Carica .env dalla root progetto: FicConfig.from_env legge os.environ,
# e uvicorn non carica .env da solo (a differenza degli script CLI).
load_dotenv(PROJECT_ROOT / ".env")

CACHE_TTL_SECONDS = int(os.environ.get("DASHBOARD_CACHE_TTL", "300"))
SHARE_TOKEN = os.environ.get("SHARE_TOKEN", "")
TOKEN_COOKIE = "fb_token"
ICS_PATH = "/scadenze.ics"

app = FastAPI(
    title="Freelance Budget API",
    version="0.1.0",
    description="Dashboard fiscale forfettario — MVP",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _token_ok(value: str | None) -> bool:
    return bool(value) and hmac.compare_digest(value.encode(), SHARE_TOKEN.encode())


@app.middleware("http")
async def require_share_token(request: Request, call_next):
    if not SHARE_TOKEN or request.url.path == "/api/health":
        return await call_next(request)
    if request.url.path == ICS_PATH and _token_ok(request.query_params.get("t")):
        return await call_next(request)
    if _token_ok(request.query_params.get("t")):
        # Link condiviso: salvo il token in cookie e tolgo ?t= dall'URL.
        resp = RedirectResponse(request.url.path, status_code=303)
        resp.set_cookie(
            TOKEN_COOKIE, SHARE_TOKEN, max_age=180 * 86400,
            httponly=True, secure=True, samesite="lax",
        )
        return resp
    if _token_ok(request.cookies.get(TOKEN_COOKIE)):
        return await call_next(request)
    return PlainTextResponse(
        "Accesso riservato: apri il link con il token che ti è stato condiviso.",
        status_code=401,
    )


class _Cache:
    """TTL cache per anno. Niente LRU, niente thread-safety avanzata:
    per l'MVP serve solo a evitare round-trip FIC quando l'utente refresha la UI.
    """

    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self._items: dict[int | None, tuple[float, DashboardResponse]] = {}

    def get(self, key: int | None = None) -> DashboardResponse | None:
        hit = self._items.get(key)
        if hit is None or time.time() >= hit[0]:
            return None
        return hit[1]

    def set(self, value: DashboardResponse, key: int | None = None) -> None:
        self._items[key] = (time.time() + self.ttl_seconds, value)

    def invalidate(self) -> None:
        self._items.clear()


_cache = _Cache(ttl_seconds=CACHE_TTL_SECONDS)


def get_dashboard_service() -> DashboardService:
    return DashboardService.from_config(PROJECT_ROOT)


def get_fic_client() -> FicClient:
    config = FicConfig.from_env(PROJECT_ROOT)
    return FicClient(config=config)


@app.get(
    "/api/dashboard",
    response_model=DashboardResponse,
    responses={
        502: {"model": ErrorResponse, "description": "Errore chiamando FIC"},
        500: {"model": ErrorResponse, "description": "Errore interno / config"},
    },
)
def dashboard(force_refresh: bool = False, anno: int | None = None) -> DashboardResponse:
    """`anno` omesso → anno in corso; anno passato → consuntivo al 31/12."""
    if anno is not None and not (2000 <= anno <= date.today().year):
        raise HTTPException(
            status_code=400,
            detail={"error": "anno_non_valido", "detail": f"Anno {anno} non analizzabile"},
        )
    if not force_refresh:
        cached = _cache.get(anno)
        if cached is not None:
            return cached

    try:
        service = get_dashboard_service()
    except Exception as e:
        log.exception("Config load failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "config_error",
                "detail": str(e),
                "hint": "Verifica config/tax_rules.yaml e config/user_settings.yaml",
            },
        )

    try:
        with get_fic_client() as client:
            response = service.build_dashboard(fic_client=client, anno=anno)
    except FicClientError as e:
        log.exception("FIC error")
        raise HTTPException(
            status_code=502,
            detail={
                "error": "fic_error",
                "detail": str(e),
                "hint": "Controlla il token OAuth (scripts/authorize_fic.py) e la connessione.",
            },
        )
    except Exception as e:
        log.exception("Unexpected error building dashboard")
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "detail": str(e)},
        )

    _cache.set(response, anno)
    return response


@app.post("/api/dashboard/invalidate")
def invalidate_cache() -> dict[str, Any]:
    _cache.invalidate()
    return {"ok": True}


@app.get(ICS_PATH, response_class=Response)
def scadenze_calendario() -> Response:
    """Scadenze F24 dell'anno in corso e del successivo, con promemoria 4 giorni prima."""
    d = dashboard()
    return Response(
        scadenze_ics([*d.scadenze_prossime, *d.scadenze_anno_successivo]),
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": 'inline; filename="scadenze.ics"'},
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Frontend compilato (web/dist). Montato per ultimo: le rotte /api sopra
# hanno la precedenza. html=True serve index.html per le rotte SPA.
_WEB_DIST = PROJECT_ROOT / "web" / "dist"
if _WEB_DIST.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")
    log.info("Frontend servito da %s", _WEB_DIST)
else:
    log.warning("web/dist assente: servita solo l'API")

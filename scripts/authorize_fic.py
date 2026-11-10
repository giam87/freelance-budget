"""Script CLI per il primo avvio: esegue il flusso OAuth FIC e salva il token.

Come funziona:
  1. Legge `.env` (client_id, secret, redirect_uri)
  2. Avvia un mini server HTTP sulla porta del redirect_uri
  3. Apre il browser sull'URL di authorize FIC
  4. L'utente autorizza l'app su FIC → FIC redirige al mini server con ?code=...
  5. Lo script scambia il code per access+refresh token
  6. Salva il token su file (default .fic_token.json) e termina

Uso:
  python scripts/authorize_fic.py

Requisiti:
  - File .env compilato (copia da .env.example)
  - Redirect URI registrato su FIC = http://localhost:8000/auth/fic/callback
"""
from __future__ import annotations

import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")

from freelance_budget.integrations.fatture_in_cloud.config import FicConfig  # noqa: E402
from freelance_budget.integrations.fatture_in_cloud.oauth import (  # noqa: E402
    build_authorize_url,
    exchange_code_for_token,
    generate_state,
)
from freelance_budget.integrations.fatture_in_cloud.token_store import save_token  # noqa: E402


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handler HTTP che cattura il callback OAuth sulla /auth/fic/callback."""

    expected_state: str = ""
    received_code: str | None = None
    received_error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/auth/fic/callback"):
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        error = params.get("error", [None])[0]
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if error:
            _CallbackHandler.received_error = (
                f"{error}: {params.get('error_description', [''])[0]}"
            )
            self._respond_html(f"<h1>Errore OAuth</h1><p>{_CallbackHandler.received_error}</p>")
            return

        if state != _CallbackHandler.expected_state:
            _CallbackHandler.received_error = "state mismatch (possibile CSRF)"
            self._respond_html("<h1>Errore</h1><p>state mismatch</p>")
            return

        if not code:
            _CallbackHandler.received_error = "code mancante nella response"
            self._respond_html("<h1>Errore</h1><p>code mancante</p>")
            return

        _CallbackHandler.received_code = code
        self._respond_html(
            "<h1>✓ Autorizzazione completata</h1>"
            "<p>Puoi chiudere questa scheda e tornare al terminale.</p>"
        )

    def log_message(self, format: str, *args) -> None:  # silenzia access log
        return

    def _respond_html(self, html: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


def main() -> int:
    config = FicConfig.from_env(ROOT)

    # Estraggo host e porta dal redirect_uri per il binding del mini server
    parsed = urlparse(config.redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000

    state = generate_state()
    _CallbackHandler.expected_state = state

    server = HTTPServer((host, port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    authorize_url = build_authorize_url(config, state=state)
    print(f"\nApro il browser su Fatture in Cloud per autorizzare l'app...")
    print(f"Se non si apre, incolla manualmente:\n  {authorize_url}\n")
    webbrowser.open(authorize_url)

    print(f"In ascolto su {host}:{port}{parsed.path} ... (Ctrl+C per annullare)")
    try:
        while (
            _CallbackHandler.received_code is None
            and _CallbackHandler.received_error is None
        ):
            thread.join(timeout=0.5)
    except KeyboardInterrupt:
        server.shutdown()
        print("\nAnnullato.")
        return 130
    finally:
        server.shutdown()

    if _CallbackHandler.received_error:
        print(f"\n❌ {_CallbackHandler.received_error}")
        return 1

    code = _CallbackHandler.received_code
    assert code is not None
    print("\nScambio code per access token...")
    token = exchange_code_for_token(config, code)
    save_token(token, config.token_path)

    print(f"✓ Token salvato in {config.token_path}")
    print(f"  Scopes concessi: {token.scope}")
    print(f"  Scade il: {token.expires_at.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

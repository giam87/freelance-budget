"""Configurazione client FIC caricata da variabili d'ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


FIC_API_BASE = "https://api-v2.fattureincloud.it"
FIC_AUTHORIZE_URL = f"{FIC_API_BASE}/oauth/authorize"
FIC_TOKEN_URL = f"{FIC_API_BASE}/oauth/token"

# Scope minimi per l'MVP. Ampliare quando servono fatture ricevute, clienti, ecc.
DEFAULT_SCOPES = ("issued_documents.invoices:r",)


class MissingConfigError(Exception):
    """Variabile d'ambiente obbligatoria assente."""


@dataclass(frozen=True)
class FicConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    token_path: Path

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "FicConfig":
        """Carica la config da variabili d'ambiente.

        Il chiamante deve aver già caricato `.env` (con python-dotenv) prima di
        invocare questo metodo — qui leggiamo solo `os.environ`.
        """
        root = project_root or Path.cwd()
        try:
            return cls(
                client_id=_require("FIC_CLIENT_ID"),
                client_secret=_require("FIC_CLIENT_SECRET"),
                redirect_uri=_require("FIC_REDIRECT_URI"),
                token_path=Path(os.environ.get("FIC_TOKEN_PATH") or root / ".fic_token.json"),
            )
        except MissingConfigError as e:
            raise MissingConfigError(
                f"{e}. Copia .env.example in .env e compila i valori."
            ) from e


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingConfigError(f"Variabile {name} mancante o vuota")
    return value

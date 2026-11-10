"""Persistenza del TokenPair su file JSON.

Per la demo teniamo il token in un file locale (`.fic_token.json`).
In produzione andrà sostituito con un secret store cifrato. L'interfaccia è
minima (save/load) così sostituire l'implementazione non tocca l'uso.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .oauth import TokenPair


def save_token(token: TokenPair, path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at.isoformat(),
                "scope": token.scope,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def load_token(path: Path) -> TokenPair | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    expires_at = datetime.fromisoformat(data["expires_at"])
    # Forziamo tz-aware per evitare confronti naive vs aware downstream
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return TokenPair(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", ""),
        expires_at=expires_at,
        scope=data.get("scope", ""),
    )

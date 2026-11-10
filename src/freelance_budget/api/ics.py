"""Scadenze F24 come calendario iCalendar (RFC 5545) a cui abbonarsi.

Un evento "tutto il giorno" per data di versamento, con promemoria alle 9:00
di 4 giorni prima. UID stabili per data: a ogni refresh il calendario aggiorna
gli importi invece di duplicare gli eventi.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

from .dto import ScadenzaDTO

GIORNI_PREAVVISO = 4


def _euro(v: Decimal) -> str:
    s = f"{v.quantize(Decimal('1'), ROUND_HALF_UP):,}".replace(",", ".")
    return f"{s} €"


def _testo(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Righe al massimo di 75 ottetti, le successive iniziano con uno spazio."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode()
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode())
            cur = b""
        cur += b
    out.append(cur.decode())
    return "\r\n ".join(out)


def scadenze_ics(scadenze: Iterable[ScadenzaDTO], now: datetime | None = None) -> str:
    per_data: dict = defaultdict(list)
    for s in scadenze:
        per_data[s.data].append(s)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")

    righe = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//freelance-budget//scadenze F24//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Scadenze fiscali",
        "X-WR-TIMEZONE:Europe/Rome",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for giorno in sorted(per_data):
        voci = per_data[giorno]
        totale = sum((v.importo_stimato for v in voci), Decimal("0"))
        stima = " (stima)" if any(v.stato == "prevista" for v in voci) else ""
        dettaglio = "\n".join(f"• {v.descrizione}: {_euro(v.importo_stimato)}" for v in voci)
        righe += [
            "BEGIN:VEVENT",
            f"UID:f24-{giorno.isoformat()}@freelance-budget",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{giorno:%Y%m%d}",
            f"DTEND;VALUE=DATE:{giorno + timedelta(days=1):%Y%m%d}",
            f"SUMMARY:{_testo(f'F24 · {_euro(totale)}{stima}')}",
            f"DESCRIPTION:{_testo(dettaglio)}",
            "TRANSP:TRANSPARENT",
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_testo(f'F24 tra {GIORNI_PREAVVISO} giorni: {_euro(totale)}')}",
            # evento tutto il giorno: l'inizio è la mezzanotte, -3g15h = 9:00 di 4 giorni prima
            f"TRIGGER:-P{GIORNI_PREAVVISO - 1}DT15H",
            "END:VALARM",
            "END:VEVENT",
        ]
    righe.append("END:VCALENDAR")
    return "\r\n".join(_fold(r) for r in righe) + "\r\n"

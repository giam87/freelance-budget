"""Calendario delle scadenze F24."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from freelance_budget.api.dto import ScadenzaDTO
from freelance_budget.api.ics import scadenze_ics


def _s(giorno: date, importo: str, descrizione: str) -> ScadenzaDTO:
    return ScadenzaDTO(
        tipo="acconto_2_inps", data=giorno, anno_imposta_riferimento=giorno.year,
        importo_stimato=Decimal(importo), descrizione=descrizione,
    )


def test_un_evento_per_data_con_totale_e_promemoria():
    ics = scadenze_ics([
        _s(date(2026, 11, 30), "1900.40", "II acconto INPS GS 2026 (50% di 80%, dovuto 2025)"),
        _s(date(2026, 11, 30), "1200.30", "II acconto imposta sostitutiva 2026"),
        _s(date(2027, 6, 30), "500.20", "Saldo INPS GS 2026"),
    ])
    assert ics.count("BEGIN:VEVENT") == 2
    assert "DTSTART;VALUE=DATE:20261130" in ics
    assert "UID:f24-2026-11-30@freelance-budget" in ics
    assert "SUMMARY:F24 · 3.101 € (stima)" in ics
    assert "TRIGGER:-P3DT15H" in ics  # 9:00 di 4 giorni prima
    assert "80%\\, dovuto" in ics  # virgole escapate
    righe = ics.split("\r\n")
    assert all(len(r.encode()) <= 75 for r in righe)
    # le righe spezzate si ricompongono senza perdere testo
    assert "Saldo INPS GS 2026: 500 €" in ics.replace("\r\n ", "")

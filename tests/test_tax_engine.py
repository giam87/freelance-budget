"""Test motore fiscale.

Caso di calibrazione principale: incassato 2023 reale dell'utente (€29.818).
Gli importi "pagati" 2023 riportati dall'utente (2.741 sost., 6.100 INPS) non
coincidono con il dovuto dell'anno perché sono versamenti F24 che mescolano
saldo anno precedente + acconti — gestiti altrove.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from freelance_budget.core.config_loader import (
    load_tax_rules_for_year,
    load_user_settings,
)
from freelance_budget.core.models import Payment
from freelance_budget.core.tax_engine import (
    aliquota_inps_applicabile,
    aliquota_sostitutiva_applicabile,
    arrotonda_centesimi,
    breakdown_da_incassato,
    breakdown_da_pagamenti,
    imponibile_lordo,
    imposta_sostitutiva_dovuta,
    inps_dovuta,
)


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "tax_rules.yaml"
SETTINGS_PATH = ROOT / "config" / "user_settings.yaml"


@pytest.fixture(scope="module")
def settings():
    return load_user_settings(SETTINGS_PATH, RULES_PATH)


@pytest.fixture
def rules_2023():
    return load_tax_rules_for_year(2023, RULES_PATH)


@pytest.fixture
def rules_2024():
    return load_tax_rules_for_year(2024, RULES_PATH)


# ---------- Config loading ----------

def test_settings_utente_caricate_correttamente(settings):
    assert settings.anno_apertura_piva == 2018
    assert settings.codice_ateco == "62.10.00"
    assert settings.coefficiente_redditivita == Decimal("0.67")
    assert settings.ha_altra_copertura_previdenziale is False


def test_rules_2023_aliquota_inps_2623(rules_2023):
    assert rules_2023.aliquota_inps_gs_senza_altra_copertura == Decimal("0.2623")
    assert rules_2023.aliquota_sostitutiva_ordinaria == Decimal("0.15")


def test_rules_2024_aliquota_inps_2607(rules_2024):
    assert rules_2024.aliquota_inps_gs_senza_altra_copertura == Decimal("0.2607")


# ---------- Selezione aliquote ----------

def test_aliquota_sostitutiva_utente_oltre_5_anni_usa_ordinaria(settings, rules_2023):
    # P.IVA 2018, anno 2023 → 5 anni pieni → ordinaria 15%
    assert aliquota_sostitutiva_applicabile(settings, rules_2023, 2023) == Decimal("0.15")


def test_aliquota_sostitutiva_primi_5_anni_usa_agevolata(settings, rules_2023):
    # Simulo un utente appena aperto nell'anno di calcolo
    from freelance_budget.core.models import Previdenza, UserSettings

    nuovo = UserSettings(
        anno_apertura_piva=2022,
        codice_ateco="62.10.00",
        coefficiente_redditivita=Decimal("0.67"),
        previdenza=Previdenza.INPS_GESTIONE_SEPARATA,
        ha_altra_copertura_previdenziale=False,
    )
    # 2022 → 0 anni, 2023 → 1 anno, ancora sotto i 5
    assert aliquota_sostitutiva_applicabile(nuovo, rules_2023, 2023) == Decimal("0.05")


def test_aliquota_inps_senza_altra_copertura(settings, rules_2023):
    assert aliquota_inps_applicabile(settings, rules_2023) == Decimal("0.2623")


# ---------- Calcoli puntuali ----------

def test_imponibile_lordo_applica_coefficiente():
    result = imponibile_lordo(Decimal("29818"), Decimal("0.67"))
    assert result == Decimal("19978.06")


def test_inps_dovuta_senza_massimale():
    # 19978.06 * 0.2623 = 5240.245138 → 5240.25
    result = inps_dovuta(Decimal("19978.06"), Decimal("0.2623"), None)
    assert arrotonda_centesimi(result) == Decimal("5240.25")


def test_inps_dovuta_applica_massimale():
    # Se imponibile supera il massimale, l'aliquota si applica al massimale
    result = inps_dovuta(
        imponibile_lordo_=Decimal("200000"),
        aliquota=Decimal("0.2623"),
        massimale=Decimal("113520"),
    )
    assert result == Decimal("113520") * Decimal("0.2623")


def test_imposta_sostitutiva_deduce_contributi():
    imp_lordo = Decimal("19978.06")
    inps_deducibile = Decimal("5240.25")
    result = imposta_sostitutiva_dovuta(imp_lordo, Decimal("0.15"), inps_deducibile)
    imponibile_netto = Decimal("19978.06") - Decimal("5240.25")  # 14737.81
    assert result == imponibile_netto * Decimal("0.15")
    assert arrotonda_centesimi(result) == Decimal("2210.67")


def test_imposta_sostitutiva_imponibile_negativo_azzera():
    # Contributi dedotti > imponibile lordo → imposta = 0, non negativa
    result = imposta_sostitutiva_dovuta(
        Decimal("1000"), Decimal("0.15"), Decimal("2000")
    )
    assert result == Decimal("0")


# ---------- Caso reale utente 2023 ----------

def test_caso_reale_2023_utente(settings, rules_2023):
    """Caso di calibrazione: incassato reale €29.818 nel 2023.

    Verifichiamo che il motore riproduca i numeri teorici attesi usando le
    regole 2023 (INPS GS 26,23% senza altra copertura, sostitutiva 15%).
    """
    breakdown = breakdown_da_incassato(
        incassato_anno=Decimal("29818"),
        anno=2023,
        settings=settings,
        rules=rules_2023,
    )
    assert breakdown.incassato_lordo == Decimal("29818")
    assert breakdown.coefficiente_redditivita == Decimal("0.67")
    assert breakdown.imponibile_lordo == Decimal("19978.06")
    # 19978.06 × 0.2623 = 5240.245138 → 5240.25
    assert arrotonda_centesimi(breakdown.inps_dovuta) == Decimal("5240.25")
    # (19978.06 − 5240.245138) × 0.15 = 2210.67... → 2210.67
    assert arrotonda_centesimi(breakdown.imposta_sostitutiva_dovuta) == Decimal("2210.67")
    # Totale accantonamento ≈ 5240.25 + 2210.67 = 7450.92
    assert arrotonda_centesimi(breakdown.totale_accantonamento) == Decimal("7450.92")
    # Presenza della nota che spiega la stima conservativa
    assert any("stima conservativa" in n.lower() for n in breakdown.note)


def test_breakdown_da_pagamenti_filtra_per_anno(settings, rules_2023):
    pagamenti = [
        Payment(
            invoice_id="F-001",
            data_incasso=date(2023, 3, 15),
            importo_incassato=Decimal("10000"),
        ),
        Payment(
            invoice_id="F-002",
            data_incasso=date(2023, 11, 20),
            importo_incassato=Decimal("19818"),
        ),
        # Fuori anno: non deve incidere
        Payment(
            invoice_id="F-003",
            data_incasso=date(2024, 1, 5),
            importo_incassato=Decimal("5000"),
        ),
    ]
    breakdown = breakdown_da_pagamenti(pagamenti, 2023, settings, rules_2023)
    assert breakdown.incassato_lordo == Decimal("29818")

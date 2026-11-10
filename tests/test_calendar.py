"""Test calendario fiscale (acconti, saldi, scadenzario orchestrato).

I valori di riferimento derivano dal caso reale 2023 dell'utente:
  - Dovuto imposta sostitutiva 2023 = €2.210,67
  - Dovuto INPS GS 2023           = €5.240,25
Da cui si generano le scadenze 2024.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from freelance_budget.core.calendar import (
    acconti_imposta_sostitutiva,
    acconti_inps_gs,
    saldo_imposta_sostitutiva,
    saldo_inps_gs,
    scadenzario_anno_versamento,
)
from freelance_budget.core.config_loader import load_regole_versamento
from freelance_budget.core.models import TipoScadenza
from freelance_budget.core.tax_engine import arrotonda_centesimi


ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "config" / "tax_rules.yaml"


@pytest.fixture(scope="module")
def regole():
    return load_regole_versamento(RULES_PATH)


# Valori teorici 2023 calcolati dal motore fiscale
DOVUTO_SOST_2023 = Decimal("2210.67")
DOVUTO_INPS_2023 = Decimal("5240.25")


# ---------- Acconti sostitutiva ----------

def test_acconti_sostitutiva_sopra_soglia_split_40_60(regole):
    scadenze = acconti_imposta_sostitutiva(
        dovuto_anno_precedente=DOVUTO_SOST_2023,
        anno_versamento=2024,
        regole=regole.imposta_sostitutiva,
    )
    assert len(scadenze) == 2
    a1, a2 = scadenze
    assert a1.tipo == TipoScadenza.ACCONTO_1_SOSTITUTIVA
    assert a1.data == date(2024, 6, 30)
    assert arrotonda_centesimi(a1.importo_stimato) == Decimal("884.27")
    assert a2.tipo == TipoScadenza.ACCONTO_2_SOSTITUTIVA
    assert a2.data == date(2024, 11, 30)
    assert arrotonda_centesimi(a2.importo_stimato) == Decimal("1326.40")
    # Somma acconti = dovuto precedente
    somma = a1.importo_stimato + a2.importo_stimato
    assert arrotonda_centesimi(somma) == DOVUTO_SOST_2023


def test_acconti_sostitutiva_sotto_soglia_unico_novembre(regole):
    # Dovuto anno prec €100 < soglia €257,52 → unico versamento a novembre
    scadenze = acconti_imposta_sostitutiva(
        dovuto_anno_precedente=Decimal("100"),
        anno_versamento=2024,
        regole=regole.imposta_sostitutiva,
    )
    assert len(scadenze) == 1
    assert scadenze[0].tipo == TipoScadenza.ACCONTO_UNICO_SOSTITUTIVA
    assert scadenze[0].data == date(2024, 11, 30)
    assert scadenze[0].importo_stimato == Decimal("100")
    assert "soglia" in scadenze[0].note[0].lower()


def test_acconti_sostitutiva_dovuto_zero_nessuna_scadenza(regole):
    scadenze = acconti_imposta_sostitutiva(
        dovuto_anno_precedente=Decimal("0"),
        anno_versamento=2024,
        regole=regole.imposta_sostitutiva,
    )
    assert scadenze == []


# ---------- Acconti INPS GS ----------

def test_acconti_inps_80pct_split_50_50(regole):
    scadenze = acconti_inps_gs(
        dovuto_anno_precedente=DOVUTO_INPS_2023,
        anno_versamento=2024,
        regole=regole.inps_gestione_separata,
    )
    assert len(scadenze) == 2
    a1, a2 = scadenze
    # Totale acconti = 5240.25 × 0.80 = 4192.20
    # Ciascuno = 4192.20 × 0.50 = 2096.10
    atteso = Decimal("5240.25") * Decimal("0.80") * Decimal("0.50")
    assert arrotonda_centesimi(a1.importo_stimato) == arrotonda_centesimi(atteso)
    assert arrotonda_centesimi(a2.importo_stimato) == arrotonda_centesimi(atteso)
    assert arrotonda_centesimi(a1.importo_stimato) == Decimal("2096.10")
    assert a1.data == date(2024, 6, 30)
    assert a2.data == date(2024, 11, 30)


# ---------- Saldi ----------

def test_saldo_sostitutiva_positivo(regole):
    # Ipotizzo dovuto 2024 = 3000, acconti già versati = 2210.67
    saldo = saldo_imposta_sostitutiva(
        dovuto_anno_imposta=Decimal("3000"),
        acconti_versati_anno_imposta=Decimal("2210.67"),
        anno_imposta=2024,
        regole=regole.imposta_sostitutiva,
    )
    assert saldo is not None
    assert saldo.data == date(2025, 6, 30)
    assert saldo.importo_stimato == Decimal("789.33")
    assert saldo.anno_imposta_riferimento == 2024


def test_saldo_sostitutiva_credito_ritorna_none(regole):
    # Dovuto < acconti → credito, nessuna scadenza di pagamento
    saldo = saldo_imposta_sostitutiva(
        dovuto_anno_imposta=Decimal("1500"),
        acconti_versati_anno_imposta=Decimal("2210.67"),
        anno_imposta=2024,
        regole=regole.imposta_sostitutiva,
    )
    assert saldo is None


def test_saldo_inps_positivo(regole):
    saldo = saldo_inps_gs(
        dovuto_anno_imposta=Decimal("5500"),
        acconti_versati_anno_imposta=Decimal("4192.20"),
        anno_imposta=2024,
        regole=regole.inps_gestione_separata,
    )
    assert saldo is not None
    assert saldo.data == date(2025, 6, 30)
    assert saldo.importo_stimato == Decimal("1307.80")


# ---------- Scadenzario orchestrato ----------

def test_scadenzario_2024_basato_su_dovuto_2023(regole):
    """Caso reale: nel 2024 pago saldo 2023 + acconti 2024 (basati su dovuto 2023)."""
    scadenze = scadenzario_anno_versamento(
        anno_versamento=2024,
        dovuto_sostitutiva_anno_precedente=DOVUTO_SOST_2023,
        dovuto_inps_anno_precedente=DOVUTO_INPS_2023,
        acconti_sost_versati_anno_precedente=Decimal("0"),  # ipotizzo anno prec senza acconti
        acconti_inps_versati_anno_precedente=Decimal("0"),
        regole=regole,
    )
    # Mi aspetto:
    #   30/6/2024: saldo sost 2023 + saldo inps 2023 + I acconto sost 2024 + I acconto inps 2024
    #   30/11/2024: II acconto sost 2024 + II acconto inps 2024
    tipi_per_data = {}
    for s in scadenze:
        tipi_per_data.setdefault(s.data, set()).add(s.tipo)

    assert date(2024, 6, 30) in tipi_per_data
    assert date(2024, 11, 30) in tipi_per_data
    assert TipoScadenza.SALDO_SOSTITUTIVA in tipi_per_data[date(2024, 6, 30)]
    assert TipoScadenza.SALDO_INPS in tipi_per_data[date(2024, 6, 30)]
    assert TipoScadenza.ACCONTO_1_SOSTITUTIVA in tipi_per_data[date(2024, 6, 30)]
    assert TipoScadenza.ACCONTO_1_INPS in tipi_per_data[date(2024, 6, 30)]
    assert TipoScadenza.ACCONTO_2_SOSTITUTIVA in tipi_per_data[date(2024, 11, 30)]
    assert TipoScadenza.ACCONTO_2_INPS in tipi_per_data[date(2024, 11, 30)]


def test_scadenzario_ordinato_per_data(regole):
    scadenze = scadenzario_anno_versamento(
        anno_versamento=2024,
        dovuto_sostitutiva_anno_precedente=DOVUTO_SOST_2023,
        dovuto_inps_anno_precedente=DOVUTO_INPS_2023,
        regole=regole,
    )
    date_list = [s.data for s in scadenze]
    assert date_list == sorted(date_list)


def test_scadenzario_totale_giugno_vs_novembre_2024(regole):
    """Verifica totali €: quanto devi avere pronto a giugno vs novembre 2024."""
    scadenze = scadenzario_anno_versamento(
        anno_versamento=2024,
        dovuto_sostitutiva_anno_precedente=DOVUTO_SOST_2023,
        dovuto_inps_anno_precedente=DOVUTO_INPS_2023,
        acconti_sost_versati_anno_precedente=Decimal("0"),
        acconti_inps_versati_anno_precedente=Decimal("0"),
        regole=regole,
    )
    tot_giugno = sum(
        (s.importo_stimato for s in scadenze if s.data == date(2024, 6, 30)),
        start=Decimal("0"),
    )
    tot_novembre = sum(
        (s.importo_stimato for s in scadenze if s.data == date(2024, 11, 30)),
        start=Decimal("0"),
    )
    # Giugno = saldo sost 2023 (2210.67) + saldo inps 2023 (5240.25)
    #        + I acconto sost 2024 (884.27) + I acconto inps 2024 (2096.10)
    #        = 10.431,29
    # Novembre = II acconto sost (1326.40) + II acconto inps (2096.10)
    #          = 3.422,50
    assert arrotonda_centesimi(tot_giugno) == Decimal("10431.29")
    assert arrotonda_centesimi(tot_novembre) == Decimal("3422.50")

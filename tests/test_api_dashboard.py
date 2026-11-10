"""Test endpoint /api/dashboard con FicClient mockato.

Isoliamo il servizio dalla rete costruendo DashboardService direttamente
da config reali (YAML di progetto) e iniettando un fake FicClient che
restituisce fatture con payments_list realistici.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import date
from pathlib import Path

import pytest

from freelance_budget.api.services import DashboardService


ROOT = Path(__file__).resolve().parents[1]


class FakeFicClient:
    def __init__(self, invoices: list[dict], company: dict | None = None):
        self._invoices = invoices
        self._company = company or {"id": 1318590, "name": "Test Freelance"}

    def list_companies(self):
        return [self._company]

    def list_issued_invoices(self, company_id: int, **kwargs):
        return iter(self._invoices)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fattura(
    invoice_id: int,
    emissione: str,
    net: str,
    gross: str,
    *,
    pagamenti: list[dict] | None = None,
    client_name: str = "Cliente X",
    vat_number: str | None = None,
):
    """Builder di fatture raw con payments_list opzionali.

    pagamenti: lista di dict con chiavi {amount, status, paid_date, due_date}.
    """
    return {
        "id": invoice_id,
        "number": f"{invoice_id}/2024",
        "date": emissione,
        "amount_net": net,
        "amount_gross": gross,
        "entity": {"name": client_name, "vat_number": vat_number},
        "payments_list": pagamenti or [],
    }


def _pagamento(amount: str, paid_date: str | None, status: str = "paid"):
    return {
        "amount": amount,
        "status": status,
        "paid_date": paid_date,
        "due_date": paid_date or "2099-12-31",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_incassato_separato_dal_fatturato():
    """Fattura emessa ma non pagata → in fatturato, NON in incassato."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-02-10", "2000", "2080", pagamenti=[
            _pagamento("2080", "2025-02-20", status="paid"),
        ]),
        _fattura(2, "2025-03-05", "3000", "3120", pagamenti=[
            _pagamento("3120", None, status="not_paid"),
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 4, 1)
    )

    agg_2025 = next(f for f in resp.fatturato_per_anno if f.anno == 2025)
    assert agg_2025.fatturato_lordo == Decimal("5200")  # entrambe emesse
    assert agg_2025.incassato == Decimal("2080")  # solo la pagata
    assert agg_2025.n_fatture == 2
    assert agg_2025.n_fatture_incassate == 1


def test_pagamento_tra_anni_diversi_conta_nell_anno_di_cassa():
    """Principio di cassa: fattura 2024 pagata nel 2025 conta nel 2025."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2024-12-20", "1000", "1040", pagamenti=[
            _pagamento("1040", "2025-01-15", status="paid"),
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 6, 1)
    )

    # Fatturato 2024, incassato 2025
    agg_2024 = next(f for f in resp.fatturato_per_anno if f.anno == 2024)
    agg_2025 = next(f for f in resp.fatturato_per_anno if f.anno == 2025)
    assert agg_2024.fatturato_lordo == Decimal("1040")
    assert agg_2024.incassato == Decimal("0")
    assert agg_2025.fatturato_lordo == Decimal("0")
    assert agg_2025.incassato == Decimal("1040")


def test_paid_partially_usa_amount_del_payment():
    """paid_partially: amount è l'importo pagato parziale, non il totale."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "5000", "5200", pagamenti=[
            _pagamento("2000", "2025-02-01", status="paid_partially"),
            _pagamento("3200", None, status="not_paid"),  # residuo non ancora pagato
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 4, 1)
    )
    agg = next(f for f in resp.fatturato_per_anno if f.anno == 2025)
    assert agg.fatturato_lordo == Decimal("5200")
    assert agg.incassato == Decimal("2000")  # solo la quota incassata


def test_status_odierno_vs_previsione_fine_anno():
    """Due breakdown distinti: uno sull'incassato YTD, uno sulla proiezione."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "5000", "5200", pagamenti=[
            _pagamento("5200", "2025-01-15", status="paid"),
        ]),
    ]
    # today = 31 marzo 2025 → 90/365 ≈ 24.6% dell'anno trascorso
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 3, 31)
    )

    assert resp.status_odierno.origine_base == "incassato"
    assert resp.status_odierno.base_utilizzata == Decimal("5200")
    assert resp.previsione_fine_anno.origine_base == "incassato_proiettato"
    assert resp.previsione_fine_anno.base_utilizzata > Decimal("5200")

    # La previsione deve essere coerente: accantonamento(previsione) > accantonamento(status)
    assert (
        resp.previsione_fine_anno.totale_accantonamento
        > resp.status_odierno.totale_accantonamento
    )


def test_trend_mensile_12_punti_per_anno():
    """Il trend mensile ritorna sempre 12 punti, anche se ci sono mesi a zero."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "1000", "1040", pagamenti=[
            _pagamento("1040", "2025-01-20", status="paid"),
        ]),
        _fattura(2, "2025-03-10", "2000", "2080", pagamenti=[
            _pagamento("2080", "2025-03-25", status="paid"),
        ]),
        _fattura(3, "2024-06-10", "500", "520", pagamenti=[
            _pagamento("520", "2024-06-30", status="paid"),
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 5, 1)
    )

    assert len(resp.incassato_mensile_anno_corrente) == 12
    assert len(resp.incassato_mensile_anno_precedente) == 12

    # Verifica mese corretto
    gen_2025 = resp.incassato_mensile_anno_corrente[0]
    assert gen_2025.mese == 1 and gen_2025.importo == Decimal("1040")
    mar_2025 = resp.incassato_mensile_anno_corrente[2]
    assert mar_2025.mese == 3 and mar_2025.importo == Decimal("2080")
    feb_2025 = resp.incassato_mensile_anno_corrente[1]
    assert feb_2025.importo == Decimal("0")

    giu_2024 = resp.incassato_mensile_anno_precedente[5]
    assert giu_2024.mese == 6 and giu_2024.importo == Decimal("520")


def test_soglia_forfettario_su_fatturato_non_incassato():
    """La soglia 85k si valuta sul FATTURATO emesso, non sull'incassato."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "50000", "52000", pagamenti=[]),  # non pagata
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 6, 1)
    )

    assert resp.soglia_forfettario.soglia == Decimal("85000")
    assert resp.soglia_forfettario.fatturato_anno_corrente == Decimal("52000")
    # 52000 / 85000 ≈ 0.6117...
    assert Decimal("0.61") < resp.soglia_forfettario.percentuale_utilizzo < Decimal("0.62")


def test_scadenze_basate_su_incassato_anno_precedente():
    """Le scadenze si calcolano sull'incassato dell'anno precedente (non sul fatturato)."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        # 2024: emessa e incassata
        _fattura(1, "2024-03-10", "20000", "20800", pagamenti=[
            _pagamento("20800", "2024-04-01", status="paid"),
        ]),
        _fattura(2, "2024-09-15", "10000", "10400", pagamenti=[
            _pagamento("10400", "2024-10-15", status="paid"),
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 5, 15)
    )

    assert resp.breakdown_anno_precedente is not None
    assert resp.breakdown_anno_precedente.anno_riferimento == 2024
    assert resp.breakdown_anno_precedente.base_utilizzata == Decimal("31200")
    assert len(resp.scadenze_prossime) > 0
    for s in resp.scadenze_prossime:
        assert s.data.year == 2025


def test_fattura_senza_paid_date_viene_skippata_nell_incassato():
    """status=paid ma paid_date null → saltata nell'incassato (dato inconsistente)."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-02-01", "1000", "1040", pagamenti=[
            _pagamento("1040", None, status="paid"),  # inconsistente
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 6, 1)
    )
    agg = next(f for f in resp.fatturato_per_anno if f.anno == 2025)
    assert agg.fatturato_lordo == Decimal("1040")
    assert agg.incassato == Decimal("0")


def test_ultime_fatture_esprimono_stato_pagamento():
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "1000", "1040", pagamenti=[
            _pagamento("1040", "2025-01-15", status="paid"),
        ]),
        _fattura(2, "2025-02-10", "2000", "2080", pagamenti=[
            _pagamento("500", "2025-02-20", status="paid_partially"),
        ]),
        _fattura(3, "2025-03-10", "3000", "3120", pagamenti=[
            _pagamento("3120", None, status="not_paid"),
        ]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 4, 1)
    )

    by_id = {r.id: r for r in resp.ultime_fatture}
    assert by_id[1].stato_pagamento == "paid"
    assert by_id[1].importo_incassato == Decimal("1040")
    assert by_id[2].stato_pagamento == "paid_partially"
    assert by_id[2].importo_incassato == Decimal("500")
    assert by_id[3].stato_pagamento == "not_paid"
    assert by_id[3].importo_incassato == Decimal("0")


def test_warning_incassato_zero_con_fatturato_positivo():
    """Se l'utente ha fatture emesse ma 0 payments_list → warning esplicito."""
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-02-01", "1000", "1040", pagamenti=[]),
    ]
    resp = service.build_dashboard(
        fic_client=FakeFicClient(invoices), today=date(2025, 6, 1)
    )
    joined = " ".join(resp.warnings)
    assert "paid_date" in joined or "prima nota" in joined


def test_nessuna_azienda_solleva_errore():
    service = DashboardService.from_config(ROOT)

    class NoCompaniesClient(FakeFicClient):
        def list_companies(self):
            return []

    with pytest.raises(ValueError, match="Nessuna azienda"):
        NoCompaniesClient([]).__enter__()  # noqa: E1101
        service.build_dashboard(
            fic_client=NoCompaniesClient([]), today=date(2025, 4, 1)
        )


def _tre_anni():
    """Incassato 2023, 2024, 2025 per avere dovuto N-2, N-1 e N."""
    return [
        _fattura(1, "2023-03-10", "20000", "20800", pagamenti=[
            _pagamento("20800", "2023-04-01"),
        ], client_name="Alfa"),
        _fattura(2, "2024-03-10", "30000", "31200", pagamenti=[
            _pagamento("31200", "2024-04-01"),
        ], client_name="Alfa"),
        _fattura(3, "2025-02-10", "10000", "10400", pagamenti=[
            _pagamento("10400", "2025-03-01"),
        ], client_name="Alfa"),
        _fattura(4, "2025-03-10", "30000", "31200", pagamenti=[
            _pagamento("31200", "2025-05-10"),
        ], client_name="Beta"),
        _fattura(5, "2025-11-10", "1000", "1040", pagamenti=[], client_name="Beta"),
    ]


def test_saldo_anno_precedente_sottrae_acconti_versati():
    """Saldo 2024 = dovuto 2024 − acconti 2024 (ricostruiti dal dovuto 2023)."""
    service = DashboardService.from_config(ROOT)
    resp = service.build_dashboard(
        fic_client=FakeFicClient(_tre_anni()), today=date(2025, 6, 1)
    )
    dovuto_2024 = resp.breakdown_anno_precedente.imposta_sostitutiva_dovuta
    saldo = next(s for s in resp.scadenze_prossime if s.tipo == "saldo_sostitutiva")
    assert Decimal("0") < saldo.importo_stimato < dovuto_2024
    # Acconti sostitutiva 2024 = 100% del dovuto 2023
    dovuto_2023 = service.build_dashboard(
        fic_client=FakeFicClient(_tre_anni()), today=date(2024, 6, 1)
    ).breakdown_anno_precedente.imposta_sostitutiva_dovuta
    assert saldo.importo_stimato == dovuto_2024 - dovuto_2023
    # Anno dopo: saldo 2025 + acconti 2026, tutti datati 2026
    assert resp.scadenze_anno_successivo
    assert all(s.data.year == 2026 for s in resp.scadenze_anno_successivo)


def test_anno_passato_e_consuntivo_al_31_dicembre():
    service = DashboardService.from_config(ROOT)
    resp = service.build_dashboard(
        fic_client=FakeFicClient(_tre_anni()), today=date(2026, 3, 1), anno=2025
    )
    assert resp.consuntivo is True
    assert resp.anno_corrente == 2025
    assert resp.proiezione_anno_corrente.incassato_proiettato == Decimal("41600")
    assert resp.status_odierno.base_utilizzata == resp.previsione_fine_anno.base_utilizzata
    assert all(f.data.year == 2025 for f in resp.ultime_fatture)
    assert resp.anni_disponibili == [2023, 2024, 2025]
    # Stesso periodo anno prima = tutto il 2024
    assert resp.proiezione_anno_corrente.incassato_stesso_periodo_prec == Decimal("31200")


def test_anno_futuro_rifiutato():
    service = DashboardService.from_config(ROOT)
    with pytest.raises(ValueError):
        service.build_dashboard(
            fic_client=FakeFicClient([]), today=date(2025, 3, 1), anno=2026
        )


def test_distribuzione_clienti_e_crediti_aperti():
    service = DashboardService.from_config(ROOT)
    resp = service.build_dashboard(
        fic_client=FakeFicClient(_tre_anni()), today=date(2025, 12, 1)
    )
    by_name = {c.cliente: c for c in resp.clienti}
    assert by_name["Beta"].fatturato == Decimal("32240")
    assert by_name["Beta"].n_fatture == 2
    assert by_name["Beta"].da_incassare == Decimal("1040")
    assert by_name["Alfa"].incassato == Decimal("10400")
    assert by_name["Alfa"].giorni_medi_incasso == Decimal("19.0")
    assert resp.clienti[0].cliente == "Beta"  # ordinati per fatturato
    assert sum(c.quota_fatturato for c in resp.clienti) == Decimal("1")
    assert resp.crediti_aperti == Decimal("1040")


def test_clienti_con_ragione_sociale_scritta_diversa_sono_uniti():
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "100", "100", client_name="Rossi srl"),
        _fattura(2, "2025-02-10", "200", "200", client_name="ROSSI S.R.L."),
    ]
    resp = service.build_dashboard(fic_client=FakeFicClient(invoices), today=date(2025, 3, 1))
    assert len(resp.clienti) == 1
    assert resp.clienti[0].fatturato == Decimal("300")


def test_clienti_con_stessa_piva_sono_uniti_anche_con_nomi_diversi():
    service = DashboardService.from_config(ROOT)
    invoices = [
        _fattura(1, "2025-01-10", "100", "100", client_name="Delta SPA", vat_number="01234567890"),
        _fattura(2, "2025-02-10", "200", "200",
                 client_name="DELTA - SOCIETA' PER AZIONI", vat_number="IT 01234567890"),
        _fattura(3, "2025-02-11", "50", "50", client_name="Delta SPA", vat_number="09999999999"),
    ]
    resp = service.build_dashboard(fic_client=FakeFicClient(invoices), today=date(2025, 3, 1))
    assert sorted(c.fatturato for c in resp.clienti) == [Decimal("50"), Decimal("300")]
    # il nome mostrato è quello della fattura più recente
    assert any(c.cliente == "DELTA - SOCIETA' PER AZIONI" for c in resp.clienti)

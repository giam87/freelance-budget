"""Schemi Pydantic per le response API (DTO esposti al frontend).

Distinti dai modelli di dominio in core/models.py perché:
  - hanno nomi più parlanti per la UI (camelCase implicito se serve)
  - includono meta-info di presentazione (flag di confidence, messaggi utente)
  - evolvono indipendentemente dai modelli di dominio
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class FatturatoAnno(BaseModel):
    """Totali aggregati annuali — sia lato emissione che lato cassa."""
    anno: int
    fatturato_lordo: Decimal = Field(
        description="Somma amount_gross delle fatture EMESSE nell'anno"
    )
    incassato: Decimal = Field(
        description="Somma dei payments con status in {paid, paid_partially} "
                    "raggruppati per anno di paid_date"
    )
    n_fatture: int
    n_fatture_incassate: int = Field(
        description="Numero di fatture con almeno un payment in status paid/paid_partially"
    )


class IncassatoMese(BaseModel):
    """Singolo punto del trend mensile (per grafico)."""
    anno: int
    mese: int = Field(ge=1, le=12)
    importo: Decimal


class ScadenzaDTO(BaseModel):
    tipo: str
    data: date
    anno_imposta_riferimento: int
    importo_stimato: Decimal
    descrizione: str
    stato: Literal["prevista", "pagata", "in_ritardo"] = "prevista"


class TaxBreakdownDTO(BaseModel):
    """Calcolo fiscale su una base specifica (incassato YTD o proiezione)."""
    anno_riferimento: int
    etichetta: str = Field(
        description="Descrizione breve della vista (es. 'Status ad oggi', "
                    "'Previsione fine anno', 'Anno precedente')"
    )
    base_utilizzata: Decimal = Field(
        description="Importo base (incassato o fatturato proiettato) usato per il calcolo"
    )
    origine_base: Literal["incassato", "incassato_proiettato", "fatturato_emesso"]
    coefficiente_redditivita: Decimal
    imponibile_lordo: Decimal
    aliquota_inps: Decimal
    inps_dovuta: Decimal
    aliquota_sostitutiva: Decimal
    imposta_sostitutiva_dovuta: Decimal
    totale_accantonamento: Decimal
    note: list[str] = Field(default_factory=list)


class InvoiceRow(BaseModel):
    id: int
    numero: str
    data: date
    cliente: str
    compenso_netto: Decimal = Field(description="amount_net FIC — prestazione pura")
    totale_lordo: Decimal = Field(description="amount_gross FIC — comprensivo rivalsa/bollo")
    stato_pagamento: Literal["paid", "paid_partially", "not_paid"] = "not_paid"
    importo_incassato: Decimal = Field(
        default=Decimal("0"),
        description="Somma dei payments in status paid/paid_partially",
    )


class ProiezioneAnno(BaseModel):
    """Proiezione lineare YTD → fine anno, sia fatturato che incassato."""
    anno: int
    giorno_anno_corrente: int
    giorni_totali_anno: int
    fatturato_ytd: Decimal
    fatturato_proiettato: Decimal
    incassato_ytd: Decimal
    incassato_proiettato: Decimal
    fatturato_stesso_periodo_prec: Decimal = Field(
        default=Decimal("0"),
        description="Fatturato emesso nell'anno precedente fino allo stesso giorno",
    )
    incassato_stesso_periodo_prec: Decimal = Field(
        default=Decimal("0"),
        description="Incassato nell'anno precedente fino allo stesso giorno",
    )


class ClienteStat(BaseModel):
    """Distribuzione per cliente nell'anno visualizzato."""
    cliente: str
    fatturato: Decimal = Field(description="amount_gross delle fatture emesse nell'anno")
    incassato: Decimal = Field(description="Pagamenti ricevuti nell'anno (per paid_date)")
    n_fatture: int
    quota_fatturato: Decimal = Field(description="fatturato / fatturato totale anno (0..1)")
    giorni_medi_incasso: Decimal | None = Field(
        default=None,
        description="Media giorni tra data fattura e paid_date, pagamenti dell'anno",
    )
    da_incassare: Decimal = Field(
        description="Residuo non pagato, alla data di riferimento, su fatture emesse fino ad allora"
    )


class SogliaForfettario(BaseModel):
    """Progressione verso la soglia 85.000 € che fa decadere il regime."""
    soglia: Decimal = Decimal("85000")
    # Nel forfettario si guarda il FATTURATO emesso nell'anno (non l'incassato)
    # per il limite di permanenza.
    fatturato_anno_corrente: Decimal
    percentuale_utilizzo: Decimal = Field(
        description="fatturato_anno_corrente / soglia (0.0 → 1.0+)"
    )
    fonte: str = "art. 1, comma 54, L. 190/2014 (soglia 85k dal 2023)"


class DashboardResponse(BaseModel):
    generato_il: datetime
    anno_corrente: int = Field(description="Anno visualizzato (può essere un anno passato)")
    consuntivo: bool = Field(
        default=False,
        description="True se l'anno visualizzato è chiuso: dati definitivi, niente proiezioni",
    )
    anni_disponibili: list[int] = Field(default_factory=list)
    azienda_nome: str
    azienda_id: int

    fatturato_per_anno: list[FatturatoAnno]
    proiezione_anno_corrente: ProiezioneAnno
    soglia_forfettario: SogliaForfettario

    # Breakdown multipli: status oggi (certezza) + previsione fine anno (stima)
    status_odierno: TaxBreakdownDTO = Field(
        description="Calcolo su INCASSATO YTD — quanto dovresti avere già accantonato oggi"
    )
    previsione_fine_anno: TaxBreakdownDTO = Field(
        description="Calcolo su INCASSATO proiettato a fine anno — stima totale annua"
    )
    breakdown_anno_precedente: TaxBreakdownDTO | None = Field(
        default=None,
        description="Calcolo su incassato dell'anno precedente (consuntivo)",
    )

    # Trend mensile entrate — due serie per grafico overlay
    incassato_mensile_anno_corrente: list[IncassatoMese]
    incassato_mensile_anno_precedente: list[IncassatoMese]

    scadenze_prossime: list[ScadenzaDTO] = Field(
        description="Versamenti che cadono nell'anno visualizzato (saldo anno prec. + acconti)"
    )
    scadenze_anno_successivo: list[ScadenzaDTO] = Field(
        default_factory=list,
        description="Versamenti dell'anno dopo: saldo dell'anno visualizzato + I/II acconto (stima)",
    )
    clienti: list[ClienteStat] = Field(default_factory=list)
    crediti_aperti: Decimal = Field(
        default=Decimal("0"),
        description="Totale da incassare alla data di riferimento (tutte le fatture emesse)",
    )
    ultime_fatture: list[InvoiceRow] = Field(description="Fatture emesse nell'anno visualizzato")
    warnings: list[str] = Field(
        default_factory=list,
        description="Avvisi di confidenza dati / stime / limitazioni MVP",
    )
    fonte_aliquote: str = Field(description="Fonte dichiarata delle aliquote usate")


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    hint: str | None = None

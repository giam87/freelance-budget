"""Modelli di dominio.

Tutti gli importi monetari sono rappresentati con Decimal per evitare errori
di arrotondamento sui calcoli fiscali. Le funzioni di engine restituiscono
Decimal; la UI arrotonda al centesimo solo in presentazione.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Confidence = Literal["confirmed", "inferred", "missing"]


class Previdenza(str, Enum):
    INPS_GESTIONE_SEPARATA = "inps_gestione_separata"
    # Estendere se si supporteranno casse professionali.


class RegoleFiscaliAnno(BaseModel):
    """Regole fiscali applicabili a un anno d'imposta, caricate da YAML."""
    model_config = ConfigDict(frozen=True)

    anno: int
    aliquota_sostitutiva_ordinaria: Decimal
    aliquota_sostitutiva_agevolata: Decimal
    aliquota_inps_gs_senza_altra_copertura: Decimal
    aliquota_inps_gs_con_altra_copertura: Decimal
    massimale_inps_gs: Decimal | None
    fonte: str


class UserSettings(BaseModel):
    """Profilo fiscale utente, caricato da YAML."""
    model_config = ConfigDict(frozen=True)

    anno_apertura_piva: int
    codice_ateco: str
    coefficiente_redditivita: Decimal
    previdenza: Previdenza
    ha_altra_copertura_previdenziale: bool

    def anni_attivita_al(self, anno: int) -> int:
        return anno - self.anno_apertura_piva


class Invoice(BaseModel):
    """Fattura emessa."""
    model_config = ConfigDict(frozen=True)

    id: str
    data_emissione: date
    cliente: str
    compenso: Decimal = Field(description="Importo prestazione, al netto di rivalsa e bollo")
    rivalsa_inps_4pct: Decimal = Decimal("0")
    bollo: Decimal = Decimal("0")

    @property
    def totale(self) -> Decimal:
        return self.compenso + self.rivalsa_inps_4pct + self.bollo


class Payment(BaseModel):
    """Incasso ricevuto su una fattura.

    In forfettario l'imposta si calcola sulla cassa: quello che conta è questo
    evento, non l'emissione della fattura.
    """
    model_config = ConfigDict(frozen=True)

    invoice_id: str
    data_incasso: date
    importo_incassato: Decimal
    confidence: Confidence = "confirmed"


class TipoScadenza(str, Enum):
    # Imposta sostitutiva
    ACCONTO_1_SOSTITUTIVA = "acconto_1_sostitutiva"
    ACCONTO_2_SOSTITUTIVA = "acconto_2_sostitutiva"
    ACCONTO_UNICO_SOSTITUTIVA = "acconto_unico_sostitutiva"
    SALDO_SOSTITUTIVA = "saldo_sostitutiva"
    # INPS Gestione Separata
    ACCONTO_1_INPS = "acconto_1_inps"
    ACCONTO_2_INPS = "acconto_2_inps"
    SALDO_INPS = "saldo_inps"


class StatoScadenza(str, Enum):
    PREVISTA = "prevista"
    PAGATA = "pagata"
    IN_RITARDO = "in_ritardo"


class GiornoMese(BaseModel):
    model_config = ConfigDict(frozen=True)
    mese: int = Field(ge=1, le=12)
    giorno: int = Field(ge=1, le=31)


class RegoleVersamentoTributo(BaseModel):
    """Regole per il calcolo di acconti e saldi di un singolo tributo."""
    model_config = ConfigDict(frozen=True)

    pct_acconto_totale_su_anno_precedente: Decimal
    split_acconto_1: Decimal
    split_acconto_2: Decimal
    soglia_frazionamento_euro: Decimal | None
    giorno_acconto_1: GiornoMese
    giorno_acconto_2: GiornoMese
    giorno_saldo: GiornoMese


class RegoleVersamento(BaseModel):
    """Contenitore delle regole di versamento per i tributi gestiti."""
    model_config = ConfigDict(frozen=True)

    imposta_sostitutiva: RegoleVersamentoTributo
    inps_gestione_separata: RegoleVersamentoTributo


class Scadenza(BaseModel):
    """Singola scadenza fiscale prevista o registrata."""
    model_config = ConfigDict(frozen=True)

    tipo: TipoScadenza
    data: date
    anno_imposta_riferimento: int = Field(
        description="Anno d'imposta cui la scadenza si riferisce (non sempre coincide con l'anno della data)"
    )
    importo_stimato: Decimal
    descrizione: str
    stato: StatoScadenza = StatoScadenza.PREVISTA
    note: list[str] = Field(default_factory=list)


class TaxBreakdown(BaseModel):
    """Scomposizione delle imposte per un insieme di incassi.

    Ogni campo è esposto separatamente così da essere ispezionabile/audit-friendly.
    """
    model_config = ConfigDict(frozen=True)

    anno_riferimento: int
    incassato_lordo: Decimal
    coefficiente_redditivita: Decimal
    imponibile_lordo: Decimal = Field(description="incassato × coefficiente")
    inps_dovuta: Decimal = Field(description="imponibile_lordo × aliquota GS")
    inps_dedotta_per_imposta: Decimal = Field(
        description="Contributi considerati deducibili per calcolo sostitutiva"
    )
    imponibile_netto_imposta: Decimal = Field(
        description="imponibile_lordo − inps_dedotta_per_imposta"
    )
    aliquota_sostitutiva_applicata: Decimal
    imposta_sostitutiva_dovuta: Decimal
    totale_accantonamento: Decimal = Field(
        description="inps_dovuta + imposta_sostitutiva_dovuta"
    )
    note: list[str] = Field(default_factory=list)

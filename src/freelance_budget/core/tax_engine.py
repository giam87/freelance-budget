"""Motore di calcolo fiscale per regime forfettario.

Tutte le funzioni sono pure (nessun side effect, nessun I/O).
Restituiscono Decimal al massimo della precisione: l'arrotondamento al
centesimo è responsabilità del chiamante (presentazione).

Regole applicate (regime forfettario, D.L. 190/2014 e successive):
  1. Principio di cassa: la base imponibile è l'INCASSATO nell'anno,
     non il fatturato emesso.
  2. Imponibile lordo = incassato × coefficiente_redditività (ATECO).
  3. INPS Gestione Separata: aliquota × imponibile_lordo, con eventuale
     tetto al massimale contributivo.
  4. Imposta sostitutiva: aliquota × (imponibile_lordo − contributi
     previdenziali effettivamente versati nell'anno deducibili).
  5. Aliquota sostitutiva agevolata 5% nei primi 5 anni d'attività,
     se ricorrono le condizioni (qui: solo se `anni_attivita < 5`).

IMPORTANTE — differenza competenza vs cassa dei versamenti:
  Queste funzioni calcolano il DOVUTO per l'anno (competenza fiscale).
  I versamenti effettivi in F24 sono un'altra vista (saldo anno prec +
  acconti anno corrente + eventuale acconto anno succ) gestita altrove.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from .models import Payment, RegoleFiscaliAnno, TaxBreakdown, UserSettings


CENT = Decimal("0.01")


def aliquota_sostitutiva_applicabile(
    settings: UserSettings, rules: RegoleFiscaliAnno, anno_calcolo: int
) -> Decimal:
    """Restituisce l'aliquota sostitutiva da usare in base agli anni di attività.

    Nota: l'agevolazione 5% richiede anche altri requisiti (primo anno di nuova
    attività, non mera prosecuzione, ecc.) che NON sono tracciati qui: applichiamo
    solo la regola dei 5 anni di apertura P.IVA. Per utente con P.IVA 2018,
    nel 2023+ si ricade sempre nell'aliquota ordinaria.
    """
    anni_attivita = anno_calcolo - settings.anno_apertura_piva
    if anni_attivita < 5:
        return rules.aliquota_sostitutiva_agevolata
    return rules.aliquota_sostitutiva_ordinaria


def aliquota_inps_applicabile(
    settings: UserSettings, rules: RegoleFiscaliAnno
) -> Decimal:
    if settings.previdenza.value != "inps_gestione_separata":
        raise NotImplementedError(
            f"Previdenza {settings.previdenza} non supportata in questa versione. "
            f"Solo INPS Gestione Separata è implementata."
        )
    if settings.ha_altra_copertura_previdenziale:
        return rules.aliquota_inps_gs_con_altra_copertura
    return rules.aliquota_inps_gs_senza_altra_copertura


def imponibile_lordo(incassato: Decimal, coefficiente: Decimal) -> Decimal:
    return incassato * coefficiente


def inps_dovuta(
    imponibile_lordo_: Decimal,
    aliquota: Decimal,
    massimale: Decimal | None,
) -> Decimal:
    base = imponibile_lordo_
    if massimale is not None and base > massimale:
        base = massimale
    return base * aliquota


def imposta_sostitutiva_dovuta(
    imponibile_lordo_: Decimal,
    aliquota_sostitutiva: Decimal,
    contributi_dedotti: Decimal,
) -> Decimal:
    imponibile_netto = imponibile_lordo_ - contributi_dedotti
    if imponibile_netto < 0:
        imponibile_netto = Decimal("0")
    return imponibile_netto * aliquota_sostitutiva


def breakdown_da_incassato(
    incassato_anno: Decimal,
    anno: int,
    settings: UserSettings,
    rules: RegoleFiscaliAnno,
    contributi_dedotti: Decimal | None = None,
) -> TaxBreakdown:
    """Calcola imposte dovute a fronte di un incassato aggregato per un anno.

    Se `contributi_dedotti` non è fornito, usiamo come *stima conservativa*
    l'INPS dovuta sullo stesso anno. In dichiarazione reale vanno invece dedotti
    i contributi EFFETTIVAMENTE VERSATI nell'anno (principio di cassa per la
    deduzione), che è una grandezza diversa — motivo per cui teniamo il
    parametro esplicito e distinto.
    """
    coeff = settings.coefficiente_redditivita
    imp_lordo = imponibile_lordo(incassato_anno, coeff)

    aliq_inps = aliquota_inps_applicabile(settings, rules)
    inps = inps_dovuta(imp_lordo, aliq_inps, rules.massimale_inps_gs)

    if contributi_dedotti is None:
        contributi_dedotti = inps
        note = [
            "INPS dedotta per imposta = INPS dovuta stessa annualità "
            "(stima conservativa). In dichiarazione reale dedurre i "
            "contributi effettivamente versati nell'anno."
        ]
    else:
        note = []

    aliq_sost = aliquota_sostitutiva_applicabile(settings, rules, anno)
    imp_sost = imposta_sostitutiva_dovuta(imp_lordo, aliq_sost, contributi_dedotti)

    imponibile_netto = imp_lordo - contributi_dedotti
    if imponibile_netto < 0:
        imponibile_netto = Decimal("0")

    return TaxBreakdown(
        anno_riferimento=anno,
        incassato_lordo=incassato_anno,
        coefficiente_redditivita=coeff,
        imponibile_lordo=imp_lordo,
        inps_dovuta=inps,
        inps_dedotta_per_imposta=contributi_dedotti,
        imponibile_netto_imposta=imponibile_netto,
        aliquota_sostitutiva_applicata=aliq_sost,
        imposta_sostitutiva_dovuta=imp_sost,
        totale_accantonamento=inps + imp_sost,
        note=note,
    )


def breakdown_da_pagamenti(
    pagamenti: Iterable[Payment],
    anno: int,
    settings: UserSettings,
    rules: RegoleFiscaliAnno,
    contributi_dedotti: Decimal | None = None,
) -> TaxBreakdown:
    """Versione dettagliata: parte dalla lista di incassi, somma quelli dell'anno."""
    incassato = sum(
        (p.importo_incassato for p in pagamenti if p.data_incasso.year == anno),
        start=Decimal("0"),
    )
    return breakdown_da_incassato(incassato, anno, settings, rules, contributi_dedotti)


def arrotonda_centesimi(value: Decimal) -> Decimal:
    """Arrotonda a 2 decimali; da usare SOLO in presentazione, mai nei calcoli."""
    from decimal import ROUND_HALF_UP
    return value.quantize(CENT, rounding=ROUND_HALF_UP)

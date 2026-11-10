"""Calendario fiscale: calcolo acconti e saldi per imposta sostitutiva e INPS GS.

Logica in funzioni pure, separata dall'I/O.

Convenzioni temporali fondamentali:
  - Gli ACCONTI dell'anno N si pagano nel corso dell'anno N stesso (giugno + novembre)
    e sono commisurati al DOVUTO DELL'ANNO N-1.
  - Il SALDO dell'anno N si paga a giugno dell'anno N+1, ed è pari al
    dovuto N − acconti già versati per l'anno N.
  - Se gli acconti versati superano il dovuto, c'è credito d'imposta: nessuna
    scadenza di pagamento.

Soglia frazionamento imposta sostitutiva:
  Se il dovuto anno N-1 è < €257,52, non si fraziona: unico acconto a novembre
  al 100%. Sopra soglia: 40% a giugno + 60% a novembre.

Non gestito qui (volutamente, fuori scope MVP):
  - Ravvedimento operoso, interessi di mora.
  - Opzione versamento 31 luglio con +0,40%.
  - Rateizzazione in più rate.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from .models import (
    RegoleVersamento,
    RegoleVersamentoTributo,
    Scadenza,
    StatoScadenza,
    TipoScadenza,
)


ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Imposta sostitutiva
# ---------------------------------------------------------------------------

def acconti_imposta_sostitutiva(
    dovuto_anno_precedente: Decimal,
    anno_versamento: int,
    regole: RegoleVersamentoTributo,
) -> list[Scadenza]:
    """Acconti sostitutiva per l'anno di versamento, basati sul dovuto dell'anno precedente.

    Applica la regola di frazionamento in base alla soglia configurata.
    """
    if dovuto_anno_precedente <= ZERO:
        return []

    acconto_totale = dovuto_anno_precedente * regole.pct_acconto_totale_su_anno_precedente
    soglia = regole.soglia_frazionamento_euro

    if soglia is not None and dovuto_anno_precedente < soglia:
        return [
            Scadenza(
                tipo=TipoScadenza.ACCONTO_UNICO_SOSTITUTIVA,
                data=date(
                    anno_versamento,
                    regole.giorno_acconto_2.mese,
                    regole.giorno_acconto_2.giorno,
                ),
                anno_imposta_riferimento=anno_versamento,
                importo_stimato=acconto_totale,
                descrizione=(
                    f"Acconto unico imposta sostitutiva {anno_versamento} "
                    f"(100% — sotto soglia €{soglia:.2f} → versamento non frazionato)"
                ),
                note=[
                    f"Frazionamento non applicato: dovuto anno precedente "
                    f"€{dovuto_anno_precedente:.2f} < soglia €{soglia:.2f}"
                ],
            )
        ]

    return [
        Scadenza(
            tipo=TipoScadenza.ACCONTO_1_SOSTITUTIVA,
            data=date(
                anno_versamento,
                regole.giorno_acconto_1.mese,
                regole.giorno_acconto_1.giorno,
            ),
            anno_imposta_riferimento=anno_versamento,
            importo_stimato=acconto_totale * regole.split_acconto_1,
            descrizione=(
                f"I acconto imposta sostitutiva {anno_versamento} "
                f"({int(regole.split_acconto_1 * 100)}% di €{acconto_totale:.2f})"
            ),
        ),
        Scadenza(
            tipo=TipoScadenza.ACCONTO_2_SOSTITUTIVA,
            data=date(
                anno_versamento,
                regole.giorno_acconto_2.mese,
                regole.giorno_acconto_2.giorno,
            ),
            anno_imposta_riferimento=anno_versamento,
            importo_stimato=acconto_totale * regole.split_acconto_2,
            descrizione=(
                f"II acconto imposta sostitutiva {anno_versamento} "
                f"({int(regole.split_acconto_2 * 100)}% di €{acconto_totale:.2f})"
            ),
        ),
    ]


def saldo_imposta_sostitutiva(
    dovuto_anno_imposta: Decimal,
    acconti_versati_anno_imposta: Decimal,
    anno_imposta: int,
    regole: RegoleVersamentoTributo,
) -> Scadenza | None:
    """Saldo sostitutiva per `anno_imposta`, pagato l'anno dopo.

    Restituisce None se gli acconti versati coprono o superano il dovuto
    (situazione di credito d'imposta).
    """
    saldo = dovuto_anno_imposta - acconti_versati_anno_imposta
    if saldo <= ZERO:
        return None
    return Scadenza(
        tipo=TipoScadenza.SALDO_SOSTITUTIVA,
        data=date(
            anno_imposta + 1,
            regole.giorno_saldo.mese,
            regole.giorno_saldo.giorno,
        ),
        anno_imposta_riferimento=anno_imposta,
        importo_stimato=saldo,
        descrizione=(
            f"Saldo imposta sostitutiva {anno_imposta} "
            f"(dovuto €{dovuto_anno_imposta:.2f} − acconti versati €{acconti_versati_anno_imposta:.2f})"
        ),
    )


# ---------------------------------------------------------------------------
# INPS Gestione Separata
# ---------------------------------------------------------------------------

def acconti_inps_gs(
    dovuto_anno_precedente: Decimal,
    anno_versamento: int,
    regole: RegoleVersamentoTributo,
) -> list[Scadenza]:
    if dovuto_anno_precedente <= ZERO:
        return []
    acconto_totale = dovuto_anno_precedente * regole.pct_acconto_totale_su_anno_precedente
    return [
        Scadenza(
            tipo=TipoScadenza.ACCONTO_1_INPS,
            data=date(
                anno_versamento,
                regole.giorno_acconto_1.mese,
                regole.giorno_acconto_1.giorno,
            ),
            anno_imposta_riferimento=anno_versamento,
            importo_stimato=acconto_totale * regole.split_acconto_1,
            descrizione=(
                f"I acconto INPS GS {anno_versamento} "
                f"({int(regole.split_acconto_1 * 100)}% di 80% dovuto {anno_versamento - 1})"
            ),
            note=[
                "Acconto INPS GS: 80% del dovuto anno precedente, frazionato 50/50. "
                "Verificare annualmente circolare INPS."
            ],
        ),
        Scadenza(
            tipo=TipoScadenza.ACCONTO_2_INPS,
            data=date(
                anno_versamento,
                regole.giorno_acconto_2.mese,
                regole.giorno_acconto_2.giorno,
            ),
            anno_imposta_riferimento=anno_versamento,
            importo_stimato=acconto_totale * regole.split_acconto_2,
            descrizione=(
                f"II acconto INPS GS {anno_versamento} "
                f"({int(regole.split_acconto_2 * 100)}% di 80% dovuto {anno_versamento - 1})"
            ),
        ),
    ]


def saldo_inps_gs(
    dovuto_anno_imposta: Decimal,
    acconti_versati_anno_imposta: Decimal,
    anno_imposta: int,
    regole: RegoleVersamentoTributo,
) -> Scadenza | None:
    saldo = dovuto_anno_imposta - acconti_versati_anno_imposta
    if saldo <= ZERO:
        return None
    return Scadenza(
        tipo=TipoScadenza.SALDO_INPS,
        data=date(
            anno_imposta + 1,
            regole.giorno_saldo.mese,
            regole.giorno_saldo.giorno,
        ),
        anno_imposta_riferimento=anno_imposta,
        importo_stimato=saldo,
        descrizione=(
            f"Saldo INPS GS {anno_imposta} "
            f"(dovuto €{dovuto_anno_imposta:.2f} − acconti versati €{acconti_versati_anno_imposta:.2f})"
        ),
    )


# ---------------------------------------------------------------------------
# Scadenzario orchestrato
# ---------------------------------------------------------------------------

def scadenzario_anno_versamento(
    anno_versamento: int,
    *,
    dovuto_sostitutiva_anno_precedente: Decimal,
    dovuto_inps_anno_precedente: Decimal,
    dovuto_sostitutiva_anno_precedente_meno_uno: Decimal | None = None,
    dovuto_inps_anno_precedente_meno_uno: Decimal | None = None,
    acconti_sost_versati_anno_precedente: Decimal = ZERO,
    acconti_inps_versati_anno_precedente: Decimal = ZERO,
    regole: RegoleVersamento,
) -> list[Scadenza]:
    """Tutte le scadenze che cadono nell'anno solare `anno_versamento`.

    Nell'anno N si versa:
      - 30/6: saldo anno N-1 + I acconto anno N
      - 30/11: II acconto anno N

    Parametri:
      dovuto_sostitutiva_anno_precedente: dovuto dell'anno N-1 (base per saldo e acconti N)
      dovuto_inps_anno_precedente: idem per INPS
      dovuto_*_anno_precedente_meno_uno: NON usato nell'MVP (serviva solo se
        volessimo derivare gli acconti N dai dovuti N-2; qui gli acconti N sono
        derivati dal dovuto N-1, che è lo stesso numero del saldo N-1).
      acconti_*_versati_anno_precedente: acconti per l'anno N-1 già versati
        durante il N-1; servono per calcolare il saldo N-1.
    """
    scadenze: list[Scadenza] = []

    # Saldo anno precedente (pagato in questo anno di versamento)
    anno_imposta_saldo = anno_versamento - 1
    saldo_sost = saldo_imposta_sostitutiva(
        dovuto_sostitutiva_anno_precedente,
        acconti_sost_versati_anno_precedente,
        anno_imposta_saldo,
        regole.imposta_sostitutiva,
    )
    if saldo_sost:
        scadenze.append(saldo_sost)

    saldo_inps = saldo_inps_gs(
        dovuto_inps_anno_precedente,
        acconti_inps_versati_anno_precedente,
        anno_imposta_saldo,
        regole.inps_gestione_separata,
    )
    if saldo_inps:
        scadenze.append(saldo_inps)

    # Acconti per l'anno di versamento, basati sul dovuto anno precedente
    scadenze.extend(
        acconti_imposta_sostitutiva(
            dovuto_sostitutiva_anno_precedente,
            anno_versamento,
            regole.imposta_sostitutiva,
        )
    )
    scadenze.extend(
        acconti_inps_gs(
            dovuto_inps_anno_precedente,
            anno_versamento,
            regole.inps_gestione_separata,
        )
    )

    return sorted(scadenze, key=lambda s: (s.data, s.tipo.value))

"""Orchestration layer: collega FIC client, tax_engine e calendar.

Questo file contiene la logica di aggregazione per la dashboard.
Volutamente separato da app.py (routing) per potersi testare senza FastAPI.

Principio di cassa (regime forfettario):
  - La base imponibile è l'INCASSATO (pagamenti con status paid/paid_partially).
  - Il fatturato EMESSO è tracciato ma NON entra nel calcolo delle imposte —
    serve solo per la soglia 85k € e per il confronto UI.
"""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from ..core.calendar import (
    acconti_imposta_sostitutiva,
    acconti_inps_gs,
    scadenzario_anno_versamento,
)
from ..core.config_loader import (
    ConfigError,
    load_regole_versamento,
    load_tax_rules_for_year,
    load_user_settings,
)
from ..core.models import (
    RegoleFiscaliAnno,
    RegoleVersamento,
    UserSettings,
)
from ..core.tax_engine import (
    aliquota_inps_applicabile,
    aliquota_sostitutiva_applicabile,
    breakdown_da_incassato,
)
from ..integrations.fatture_in_cloud.client import FicClient
from .dto import (
    ClienteStat,
    DashboardResponse,
    FatturatoAnno,
    IncassatoMese,
    InvoiceRow,
    ProiezioneAnno,
    ScadenzaDTO,
    SogliaForfettario,
    TaxBreakdownDTO,
)

PaymentStatusPagato = frozenset({"paid", "paid_partially"})
ZERO = Decimal("0")


class DashboardService:
    """Aggrega dati FIC + motore fiscale + scadenze → DashboardResponse."""

    def __init__(
        self,
        settings: UserSettings,
        regole_versamento: RegoleVersamento,
        rules_path: Path,
    ):
        self.settings = settings
        self.regole_versamento = regole_versamento
        self.rules_path = rules_path

    @classmethod
    def from_config(cls, project_root: Path) -> "DashboardService":
        rules_path = project_root / "config" / "tax_rules.yaml"
        settings_path = project_root / "config" / "user_settings.yaml"
        return cls(
            settings=load_user_settings(settings_path, rules_path),
            regole_versamento=load_regole_versamento(rules_path),
            rules_path=rules_path,
        )

    def build_dashboard(
        self,
        fic_client: FicClient,
        today: date | None = None,
        anno: int | None = None,
    ) -> DashboardResponse:
        """`anno` passato → consuntivo al 31/12 di quell'anno; None → anno in corso."""
        today = today or date.today()
        if anno is not None and anno > today.year:
            raise ValueError(f"Anno {anno} nel futuro: niente da analizzare")
        consuntivo = anno is not None and anno < today.year
        if consuntivo:
            today = date(anno, 12, 31)

        companies = fic_client.list_companies()
        if not companies:
            raise ValueError("Nessuna azienda accessibile con questo token FIC")
        company = companies[0]
        company_id = company["id"]
        company_name = company.get("name", "—")

        # fieldset=detailed include payments_list e altri campi annidati
        invoices_raw = list(
            fic_client.list_issued_invoices(
                company_id=company_id,
                fieldset="detailed",
            )
        )

        return self._assemble(
            invoices_raw=invoices_raw,
            company_id=company_id,
            company_name=company_name,
            today=today,
            consuntivo=consuntivo,
        )

    # ------------------------------------------------------------------
    # Assembly (public per testabilità — pytest inietta invoices_raw)
    # ------------------------------------------------------------------

    def _assemble(
        self,
        *,
        invoices_raw: list[dict],
        company_id: int,
        company_name: str,
        today: date,
        consuntivo: bool = False,
    ) -> DashboardResponse:
        anno_corrente = today.year
        anno_precedente = anno_corrente - 1

        # --- Aggregati per anno ---------------------------------------------
        fatturato_per_anno = _fatturato_per_anno(invoices_raw)
        incassato_per_anno = _incassato_per_anno(invoices_raw)
        conteggi_fatture = _conteggi_fatture_per_anno(invoices_raw)

        anni_presenti = sorted(
            set(fatturato_per_anno) | set(incassato_per_anno) | set(conteggi_fatture)
        )
        fatturato_per_anno_dto = [
            FatturatoAnno(
                anno=y,
                fatturato_lordo=fatturato_per_anno.get(y, Decimal("0")),
                incassato=incassato_per_anno.get(y, Decimal("0")),
                n_fatture=conteggi_fatture.get(y, (0, 0))[0],
                n_fatture_incassate=conteggi_fatture.get(y, (0, 0))[1],
            )
            for y in anni_presenti
        ]

        # --- Proiezioni lineari ---------------------------------------------
        fatturato_ytd = fatturato_per_anno.get(anno_corrente, Decimal("0"))
        incassato_ytd = incassato_per_anno.get(anno_corrente, Decimal("0"))
        giorni_totali = 366 if calendar.isleap(anno_corrente) else 365
        giorno_anno = (today - date(anno_corrente, 1, 1)).days + 1
        scaling = (
            Decimal(giorni_totali) / Decimal(giorno_anno)
            if giorno_anno > 0
            else Decimal("1")
        )
        fatturato_proiettato = fatturato_ytd * scaling
        incassato_proiettato = incassato_ytd * scaling
        fatturato_sp_prec, incassato_sp_prec = _stesso_periodo_anno_prima(
            invoices_raw, today
        )

        proiezione = ProiezioneAnno(
            anno=anno_corrente,
            giorno_anno_corrente=giorno_anno,
            giorni_totali_anno=giorni_totali,
            fatturato_ytd=fatturato_ytd,
            fatturato_proiettato=fatturato_proiettato,
            incassato_ytd=incassato_ytd,
            incassato_proiettato=incassato_proiettato,
            fatturato_stesso_periodo_prec=fatturato_sp_prec,
            incassato_stesso_periodo_prec=incassato_sp_prec,
        )

        # --- Soglia forfettario ---------------------------------------------
        # La soglia 85k si valuta sul FATTURATO emesso (ricavi di competenza),
        # non sull'incassato: cfr. art. 1 c. 54 L. 190/2014 come rivista dalla
        # legge di bilancio 2023.
        soglia_val = Decimal("85000")
        perc = (
            fatturato_ytd / soglia_val if soglia_val > 0 else Decimal("0")
        )
        soglia_forfettario = SogliaForfettario(
            soglia=soglia_val,
            fatturato_anno_corrente=fatturato_ytd,
            percentuale_utilizzo=perc,
        )

        # --- Breakdown fiscali: status odierno vs previsione fine anno ------
        rules_anno_corrente = load_tax_rules_for_year(anno_corrente, self.rules_path)

        status_odierno = self._build_breakdown_dto(
            base=incassato_ytd,
            anno=anno_corrente,
            rules=rules_anno_corrente,
            etichetta=f"Consuntivo {anno_corrente}" if consuntivo else "Status ad oggi",
            origine_base="incassato",
            note_extra=[
                "Calcolo su INCASSATO YTD (pagamenti con status paid/paid_partially). "
                "Questo è l'accantonamento che dovresti avere oggi da parte.",
            ],
        )

        previsione_fine_anno = self._build_breakdown_dto(
            base=incassato_proiettato,
            anno=anno_corrente,
            rules=rules_anno_corrente,
            etichetta="Previsione fine anno",
            origine_base="incassato_proiettato",
            note_extra=[
                "Proiezione lineare dell'incassato YTD a 365 giorni. "
                "La cassa effettiva dipende dai pagamenti futuri.",
            ],
        )

        breakdown_anno_precedente: TaxBreakdownDTO | None = None
        incassato_prec = incassato_per_anno.get(anno_precedente, Decimal("0"))
        if incassato_prec > 0:
            rules_anno_prec = load_tax_rules_for_year(
                anno_precedente, self.rules_path
            )
            breakdown_anno_precedente = self._build_breakdown_dto(
                base=incassato_prec,
                anno=anno_precedente,
                rules=rules_anno_prec,
                etichetta="Anno precedente (consuntivo)",
                origine_base="incassato",
                note_extra=[
                    "Consuntivo su incassato anno precedente. "
                    "Usato come base per il calcolo degli acconti dell'anno in corso.",
                ],
            )

        warnings: list[str] = []

        # --- Scadenze fiscali -----------------------------------------------
        # Nell'anno N: saldo N-1 + I acconto N (giugno), II acconto N (novembre).
        # Gli acconti già versati per N-1 si ricostruiscono dal dovuto N-2
        # (metodo storico), assumendo che siano stati pagati come da calendario.
        bap = breakdown_anno_precedente
        dovuto_prec = (
            (bap.imposta_sostitutiva_dovuta, bap.inps_dovuta) if bap else (ZERO, ZERO)
        )
        anno_prec_2 = anno_corrente - 2
        dovuto_prec_2 = self._dovuto(anno_prec_2, incassato_per_anno)
        if bap is not None and (dovuto_prec_2 is None or dovuto_prec_2 == (ZERO, ZERO)):
            warnings.append(
                f"Nessun incassato {anno_prec_2} su FIC (o regole {anno_prec_2} assenti): "
                f"il saldo {anno_precedente} è calcolato come se non avessi versato "
                f"acconti per il {anno_precedente}. Verifica con gli F24 reali."
            )
        acconti_versati_prec = self._acconti_totali(
            dovuto_prec_2 or (ZERO, ZERO), anno_precedente
        )
        scadenze = [
            _scadenza_to_dto(s)
            for s in scadenzario_anno_versamento(
                anno_versamento=anno_corrente,
                dovuto_sostitutiva_anno_precedente=dovuto_prec[0],
                dovuto_inps_anno_precedente=dovuto_prec[1],
                acconti_sost_versati_anno_precedente=acconti_versati_prec[0],
                acconti_inps_versati_anno_precedente=acconti_versati_prec[1],
                regole=self.regole_versamento,
            )
        ]

        # Anno dopo: saldo dell'anno visualizzato (stima se in corso) + acconti.
        dovuto_corr = (
            previsione_fine_anno.imposta_sostitutiva_dovuta,
            previsione_fine_anno.inps_dovuta,
        )
        acconti_versati_corr = self._acconti_totali(dovuto_prec, anno_corrente)
        scadenze_succ = [
            _scadenza_to_dto(s)
            for s in scadenzario_anno_versamento(
                anno_versamento=anno_corrente + 1,
                dovuto_sostitutiva_anno_precedente=dovuto_corr[0],
                dovuto_inps_anno_precedente=dovuto_corr[1],
                acconti_sost_versati_anno_precedente=acconti_versati_corr[0],
                acconti_inps_versati_anno_precedente=acconti_versati_corr[1],
                regole=self.regole_versamento,
            )
        ]

        for anno_imp, dovuto, versati, stima in (
            (anno_precedente, dovuto_prec, acconti_versati_prec, False),
            (anno_corrente, dovuto_corr, acconti_versati_corr, not consuntivo),
        ):
            for nome, d, v in (("imposta sostitutiva", dovuto[0], versati[0]),
                               ("INPS GS", dovuto[1], versati[1])):
                if d > 0 and v > d:
                    warnings.append(
                        f"{'Stima: ' if stima else ''}credito {nome} {anno_imp} di "
                        f"€ {v - d:.2f}: gli acconti superano il dovuto, niente saldo "
                        f"(il credito si compensa in F24)."
                    )

        clienti, crediti_aperti = _clienti(invoices_raw, anno_corrente, today)

        # --- Trend mensile incassato (anno corrente + precedente) -----------
        incassato_mensile_corr = _incassato_mensile(invoices_raw, anno_corrente)
        incassato_mensile_prec = _incassato_mensile(invoices_raw, anno_precedente)

        # --- Fatture dell'anno visualizzato (data discendente) --------------
        ultime_fatture = _ultime_fatture(
            [inv for inv in invoices_raw if _invoice_year(inv) == anno_corrente],
            limit=None,
        )

        # --- Warnings -------------------------------------------------------
        if incassato_ytd == 0 and fatturato_ytd > 0:
            warnings.append(
                "Nessun pagamento con paid_date rilevato su FIC per l'anno corrente. "
                "Verifica che le fatture abbiano la prima nota collegata."
            )
        if giorno_anno < 60:
            warnings.append(
                f"Proiezione lineare poco affidabile: siamo al giorno {giorno_anno} "
                "dell'anno. Il dato 'Previsione fine anno' si stabilizza dopo qualche mese."
            )

        return DashboardResponse(
            generato_il=datetime.now(timezone.utc),
            anno_corrente=anno_corrente,
            consuntivo=consuntivo,
            anni_disponibili=sorted(set(anni_presenti) | {anno_corrente}),
            azienda_nome=company_name,
            azienda_id=company_id,
            fatturato_per_anno=fatturato_per_anno_dto,
            proiezione_anno_corrente=proiezione,
            soglia_forfettario=soglia_forfettario,
            status_odierno=status_odierno,
            previsione_fine_anno=previsione_fine_anno,
            breakdown_anno_precedente=breakdown_anno_precedente,
            incassato_mensile_anno_corrente=incassato_mensile_corr,
            incassato_mensile_anno_precedente=incassato_mensile_prec,
            scadenze_prossime=scadenze,
            scadenze_anno_successivo=scadenze_succ,
            clienti=clienti,
            crediti_aperti=crediti_aperti,
            ultime_fatture=ultime_fatture,
            warnings=warnings,
            fonte_aliquote=rules_anno_corrente.fonte,
        )

    def _dovuto(
        self, anno: int, incassato_per_anno: dict[int, Decimal]
    ) -> tuple[Decimal, Decimal] | None:
        """(sostitutiva, INPS) dovuti per `anno`; None se mancano le regole fiscali."""
        base = incassato_per_anno.get(anno, ZERO)
        if base <= 0:
            return (ZERO, ZERO)
        try:
            rules = load_tax_rules_for_year(anno, self.rules_path)
        except ConfigError:
            return None
        tb = breakdown_da_incassato(base, anno, self.settings, rules)
        return (tb.imposta_sostitutiva_dovuta, tb.inps_dovuta)

    def _acconti_totali(
        self, dovuto_anno_prima: tuple[Decimal, Decimal], anno: int
    ) -> tuple[Decimal, Decimal]:
        """Acconti (sostitutiva, INPS) dovuti nell'anno `anno` dal dovuto dell'anno prima."""
        r = self.regole_versamento
        sost = acconti_imposta_sostitutiva(dovuto_anno_prima[0], anno, r.imposta_sostitutiva)
        inps = acconti_inps_gs(dovuto_anno_prima[1], anno, r.inps_gestione_separata)
        return (
            sum((s.importo_stimato for s in sost), ZERO),
            sum((s.importo_stimato for s in inps), ZERO),
        )

    def _build_breakdown_dto(
        self,
        *,
        base: Decimal,
        anno: int,
        rules: RegoleFiscaliAnno,
        etichetta: str,
        origine_base: str,
        note_extra: list[str],
    ) -> TaxBreakdownDTO:
        tb = breakdown_da_incassato(base, anno, self.settings, rules)
        return TaxBreakdownDTO(
            anno_riferimento=anno,
            etichetta=etichetta,
            base_utilizzata=base,
            origine_base=origine_base,  # type: ignore[arg-type]
            coefficiente_redditivita=tb.coefficiente_redditivita,
            imponibile_lordo=tb.imponibile_lordo,
            aliquota_inps=aliquota_inps_applicabile(self.settings, rules),
            inps_dovuta=tb.inps_dovuta,
            aliquota_sostitutiva=aliquota_sostitutiva_applicabile(
                self.settings, rules, anno
            ),
            imposta_sostitutiva_dovuta=tb.imposta_sostitutiva_dovuta,
            totale_accantonamento=tb.totale_accantonamento,
            note=[*tb.note, *note_extra],
        )


# ---------------------------------------------------------------------------
# Helpers puri (testabili in isolamento)
# ---------------------------------------------------------------------------

def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _parse_iso_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _invoice_date(raw: dict) -> date | None:
    return _parse_iso_date(raw.get("date"))


def _invoice_year(raw: dict) -> int | None:
    d = _invoice_date(raw)
    return d.year if d else None


def _iter_payments(invoice_raw: dict) -> Iterable[dict]:
    pl = invoice_raw.get("payments_list")
    if isinstance(pl, list):
        return pl
    return []


def _iter_pagamenti_effettuati(invoice_raw: dict) -> Iterable[tuple[date, Decimal]]:
    """Yield (paid_date, amount) per ogni payment con status 'paid' / 'paid_partially'.

    paid_partially: FIC riporta `amount` come importo effettivamente pagato,
    quindi usarlo direttamente è corretto (vedi IssuedDocumentPaymentsListItem).
    """
    for p in _iter_payments(invoice_raw):
        if p.get("status") not in PaymentStatusPagato:
            continue
        paid_date = _parse_iso_date(p.get("paid_date"))
        if paid_date is None:
            continue  # status paid ma senza paid_date → dato inconsistente, skippo
        yield paid_date, _to_decimal(p.get("amount"))


def _fatturato_per_anno(invoices_raw: Iterable[dict]) -> dict[int, Decimal]:
    """Totale lordo delle fatture EMESSE, raggruppato per anno di emissione."""
    out: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for inv in invoices_raw:
        y = _invoice_year(inv)
        if y is None:
            continue
        out[y] += _to_decimal(inv.get("amount_gross"))
    return dict(out)


def _incassato_per_anno(invoices_raw: Iterable[dict]) -> dict[int, Decimal]:
    """Somma dei payments paid/paid_partially, raggruppati per anno di paid_date.

    Una fattura emessa a dicembre 2024 ma incassata a gennaio 2025 conta nel 2025
    (principio di cassa, come richiede il regime forfettario).
    """
    out: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for inv in invoices_raw:
        for paid_date, amount in _iter_pagamenti_effettuati(inv):
            out[paid_date.year] += amount
    return dict(out)


def _incassato_mensile(invoices_raw: Iterable[dict], anno: int) -> list[IncassatoMese]:
    """Serie 12 punti (gen–dic) dell'incassato per l'anno richiesto.

    Ritorna sempre 12 record (anche mesi con 0) per rendere il grafico prevedibile.
    """
    mensile: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for inv in invoices_raw:
        for paid_date, amount in _iter_pagamenti_effettuati(inv):
            if paid_date.year == anno:
                mensile[paid_date.month] += amount
    return [
        IncassatoMese(anno=anno, mese=m, importo=mensile.get(m, Decimal("0")))
        for m in range(1, 13)
    ]


def _conteggi_fatture_per_anno(
    invoices_raw: Iterable[dict],
) -> dict[int, tuple[int, int]]:
    """Per anno di emissione: (n_fatture_totale, n_fatture_con_almeno_un_incasso)."""
    totale: dict[int, int] = defaultdict(int)
    incassate: dict[int, int] = defaultdict(int)
    for inv in invoices_raw:
        y = _invoice_year(inv)
        if y is None:
            continue
        totale[y] += 1
        if any(
            p.get("status") in PaymentStatusPagato for p in _iter_payments(inv)
        ):
            incassate[y] += 1
    anni = set(totale) | set(incassate)
    return {y: (totale.get(y, 0), incassate.get(y, 0)) for y in anni}


def _stato_pagamento_fattura(invoice_raw: dict) -> str:
    payments = list(_iter_payments(invoice_raw))
    if not payments:
        return "not_paid"
    statuses = {p.get("status") for p in payments}
    if statuses == {"paid"}:
        return "paid"
    if "paid" in statuses or "paid_partially" in statuses:
        return "paid_partially"
    return "not_paid"


def _importo_incassato_fattura(invoice_raw: dict) -> Decimal:
    return sum(
        (amount for _, amount in _iter_pagamenti_effettuati(invoice_raw)),
        Decimal("0"),
    )


def _stesso_periodo_anno_prima(
    invoices_raw: Iterable[dict], ref: date
) -> tuple[Decimal, Decimal]:
    """(fatturato, incassato) dell'anno prima dal 1/1 fino allo stesso giorno di `ref`."""
    try:
        fine = ref.replace(year=ref.year - 1)
    except ValueError:  # 29 febbraio
        fine = date(ref.year - 1, 2, 28)
    fatt, inc = ZERO, ZERO
    for inv in invoices_raw:
        d = _invoice_date(inv)
        if d and d.year == fine.year and d <= fine:
            fatt += _to_decimal(inv.get("amount_gross"))
        for paid_date, amount in _iter_pagamenti_effettuati(inv):
            if paid_date.year == fine.year and paid_date <= fine:
                inc += amount
    return fatt, inc


def _clienti(
    invoices_raw: Iterable[dict], anno: int, ref: date
) -> tuple[list[ClienteStat], Decimal]:
    """Distribuzione per cliente nell'anno + crediti aperti alla data `ref`.

    Il residuo aperto considera tutte le fatture emesse fino a `ref` (anche di
    anni prima) meno i pagamenti con paid_date entro `ref`.
    """
    agg: dict[str, dict] = defaultdict(
        lambda: {"fatt": ZERO, "inc": ZERO, "n": 0, "giorni": [], "aperto": ZERO}
    )
    for inv in invoices_raw:
        d = _invoice_date(inv)
        if d is None or d > ref:
            continue
        entity = inv.get("entity") or {}
        nome = str(entity.get("name") or "—")
        # Stesso cliente con ragione sociale scritta in modi diversi: si unisce per
        # P.IVA (o codice fiscale), e senza nessuno dei due per nome normalizzato.
        piva = re.sub(r"[^0-9A-Z]", "", str(entity.get("vat_number") or entity.get("tax_code") or "").upper())
        piva = re.sub(r"^IT(?=\d{11}$)", "", piva)  # "IT01234567890" == "01234567890"
        a = agg[f"piva:{piva}" if piva else re.sub(r"[^0-9A-Z]", "", nome.upper()) or nome]
        if d >= a.get("nome_data", date.min):  # mostra il nome della fattura più recente
            a["nome"], a["nome_data"] = nome, d
        gross = _to_decimal(inv.get("amount_gross"))
        if d.year == anno:
            a["fatt"] += gross
            a["n"] += 1
        pagato = ZERO
        for paid_date, amount in _iter_pagamenti_effettuati(inv):
            if paid_date <= ref:
                pagato += amount
            if paid_date.year == anno:
                a["inc"] += amount
                a["giorni"].append((paid_date - d).days)
        a["aperto"] += max(gross - pagato, ZERO)

    tot_fatt = sum((a["fatt"] for a in agg.values()), ZERO)
    stats = [
        ClienteStat(
            cliente=a["nome"],
            fatturato=a["fatt"],
            incassato=a["inc"],
            n_fatture=a["n"],
            quota_fatturato=a["fatt"] / tot_fatt if tot_fatt > 0 else ZERO,
            giorni_medi_incasso=(
                Decimal(sum(a["giorni"]) / len(a["giorni"])).quantize(Decimal("0.1"))
                if a["giorni"] else None
            ),
            da_incassare=a["aperto"],
        )
        for a in agg.values()
        if a["fatt"] > 0 or a["inc"] > 0 or a["aperto"] > 0
    ]
    stats.sort(key=lambda c: (c.fatturato, c.incassato), reverse=True)
    return stats, sum((c.da_incassare for c in stats), ZERO)


def _ultime_fatture(invoices_raw: list[dict], limit: int | None = 10) -> list[InvoiceRow]:
    ordered = sorted(
        invoices_raw,
        key=lambda inv: inv.get("date") or "0000-00-00",
        reverse=True,
    )
    rows: list[InvoiceRow] = []
    for inv in ordered[:limit]:
        data = _invoice_date(inv)
        if data is None:
            continue
        entity = inv.get("entity") or {}
        rows.append(
            InvoiceRow(
                id=int(inv.get("id", 0)),
                numero=str(inv.get("number") or ""),
                data=data,
                cliente=str(entity.get("name") or "—"),
                compenso_netto=_to_decimal(inv.get("amount_net")),
                totale_lordo=_to_decimal(inv.get("amount_gross")),
                stato_pagamento=_stato_pagamento_fattura(inv),  # type: ignore[arg-type]
                importo_incassato=_importo_incassato_fattura(inv),
            )
        )
    return rows


def _scadenza_to_dto(s) -> ScadenzaDTO:
    return ScadenzaDTO(
        tipo=s.tipo.value,
        data=s.data,
        anno_imposta_riferimento=s.anno_imposta_riferimento,
        importo_stimato=s.importo_stimato,
        descrizione=s.descrizione,
        stato=s.stato.value,
    )

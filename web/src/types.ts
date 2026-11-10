// DTO speculari a src/freelance_budget/api/dto.py.
// Tenuti manuali (niente codegen) per MVP: sono pochi e stabili.

export interface FatturatoAnno {
  anno: number;
  fatturato_lordo: string;
  incassato: string;
  n_fatture: number;
  n_fatture_incassate: number;
}

export interface IncassatoMese {
  anno: number;
  mese: number; // 1..12
  importo: string;
}

export interface ScadenzaDTO {
  tipo: string;
  data: string; // ISO date
  anno_imposta_riferimento: number;
  importo_stimato: string;
  descrizione: string;
  stato: "prevista" | "pagata" | "in_ritardo";
}

export interface TaxBreakdownDTO {
  anno_riferimento: number;
  etichetta: string;
  base_utilizzata: string;
  origine_base: "incassato" | "incassato_proiettato" | "fatturato_emesso";
  coefficiente_redditivita: string;
  imponibile_lordo: string;
  aliquota_inps: string;
  inps_dovuta: string;
  aliquota_sostitutiva: string;
  imposta_sostitutiva_dovuta: string;
  totale_accantonamento: string;
  note: string[];
}

export interface InvoiceRow {
  id: number;
  numero: string;
  data: string;
  cliente: string;
  compenso_netto: string;
  totale_lordo: string;
  stato_pagamento: "paid" | "paid_partially" | "not_paid";
  importo_incassato: string;
}

export interface ProiezioneAnno {
  anno: number;
  giorno_anno_corrente: number;
  giorni_totali_anno: number;
  fatturato_ytd: string;
  fatturato_proiettato: string;
  incassato_ytd: string;
  incassato_proiettato: string;
  fatturato_stesso_periodo_prec: string;
  incassato_stesso_periodo_prec: string;
}

export interface ClienteStat {
  cliente: string;
  fatturato: string;
  incassato: string;
  n_fatture: number;
  quota_fatturato: string;
  giorni_medi_incasso: string | null;
  da_incassare: string;
}

export interface SogliaForfettario {
  soglia: string;
  fatturato_anno_corrente: string;
  percentuale_utilizzo: string;
  fonte: string;
}

export interface DashboardResponse {
  generato_il: string;
  anno_corrente: number;
  consuntivo: boolean;
  anni_disponibili: number[];
  azienda_nome: string;
  azienda_id: number;
  fatturato_per_anno: FatturatoAnno[];
  proiezione_anno_corrente: ProiezioneAnno;
  soglia_forfettario: SogliaForfettario;
  status_odierno: TaxBreakdownDTO;
  previsione_fine_anno: TaxBreakdownDTO;
  breakdown_anno_precedente: TaxBreakdownDTO | null;
  incassato_mensile_anno_corrente: IncassatoMese[];
  incassato_mensile_anno_precedente: IncassatoMese[];
  scadenze_prossime: ScadenzaDTO[];
  scadenze_anno_successivo: ScadenzaDTO[];
  clienti: ClienteStat[];
  crediti_aperti: string;
  ultime_fatture: InvoiceRow[];
  warnings: string[];
  fonte_aliquote: string;
}

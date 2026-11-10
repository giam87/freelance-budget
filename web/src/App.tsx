import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { DashboardResponse, IncassatoMese, ScadenzaDTO, TaxBreakdownDTO } from "./types";
import { dataIta, euro, percento } from "./format";

type ApiError = { status: number; detail?: unknown; message: string };

async function fetchDashboard(anno: number | null, force = false): Promise<DashboardResponse> {
  const qs = new URLSearchParams();
  if (anno) qs.set("anno", String(anno));
  if (force) qs.set("force_refresh", "true");
  const res = await fetch(`${import.meta.env.BASE_URL}api/dashboard?${qs}`);
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch { /* ignore */ }
    throw { status: res.status, detail, message: `Errore ${res.status} dal backend` } as ApiError;
  }
  return (await res.json()) as DashboardResponse;
}

const MESI_BREVI = ["Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"];

export default function App() {
  const [data, setData] = useState<DashboardResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [anno, setAnno] = useState<number | null>(null);

  async function load(force = false, a = anno) {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchDashboard(a, force));
    } catch (e) {
      setError(e as ApiError);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(false); }, []);

  if (loading && !data) return <FullPage message="Carico dati da Fatture in Cloud…" />;
  if (error && !data) return <FullPage error={error} onRetry={() => load(true)} />;
  if (!data) return null;

  return (
    <div className="page">
      <Header
        data={data}
        onRefresh={() => load(true)}
        onAnno={(a) => { setAnno(a); load(false, a); }}
        loading={loading}
      />

      {data.warnings.length > 0 && (
        <section className="warnings">
          {data.warnings.map((w, i) => (
            <div key={i} className="warning">⚠ {w}</div>
          ))}
        </section>
      )}

      {/* Prima riga: stato di cassa + soglia forfettario */}
      <section className="cards">
        <CardIncassato data={data} />
        <CardFatturato data={data} />
        <CardNetto data={data} />
        <CardSoglia data={data} />
        <CardImponibile breakdown={data.status_odierno} />
      </section>

      {/* Grande highlight: accantonamento oggi vs previsione */}
      <section className="panel-accantonamento">
        <PanelAccantonamento data={data} />
      </section>

      {/* Trend mensile entrate */}
      <section>
        <Panel title="Andamento incassato mensile">
          <TrendIncassatoChart
            corrente={data.incassato_mensile_anno_corrente}
            precedente={data.incassato_mensile_anno_precedente}
            annoCorrente={data.anno_corrente}
          />
        </Panel>
      </section>

      <section className="grid-2">
        <Panel title={`Versamenti F24 ${data.anno_corrente}`}>
          <Scadenze
            scadenze={data.scadenze_prossime}
            vuoto="Nessun versamento: manca l'incassato dell'anno precedente."
          />
        </Panel>
        <Panel title={`Versamenti F24 ${data.anno_corrente + 1}${data.consuntivo ? "" : " · stima"}`}>
          <Scadenze
            scadenze={data.scadenze_anno_successivo}
            vuoto="Nessun versamento previsto."
          />
        </Panel>
      </section>

      <section>
        <Panel title={`Clienti ${data.anno_corrente}`}>
          <Clienti data={data} />
        </Panel>
      </section>

      <section>
        <Panel title={`Fatture emesse ${data.anno_corrente}`}>
          <Fatture data={data} />
        </Panel>
      </section>

      <footer className="footer">
        <div>Fonte aliquote: {data.fonte_aliquote}</div>
        <div>Generato il {dataIta(data.generato_il)}</div>
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({
  data,
  onRefresh,
  onAnno,
  loading,
}: {
  data: DashboardResponse;
  onRefresh: () => void;
  onAnno: (anno: number) => void;
  loading: boolean;
}) {
  return (
    <header className="header">
      <div>
        <h1>{data.azienda_nome}</h1>
        <div className="sub">
          Anno fiscale {data.anno_corrente} · Regime forfettario
          {data.consuntivo ? " · consuntivo al 31/12" : " · in corso"}
        </div>
      </div>
      <div className="header-actions">
        <label className="sr-only" htmlFor="anno">Anno</label>
        <select
          id="anno"
          value={data.anno_corrente}
          disabled={loading}
          onChange={(e) => onAnno(Number(e.target.value))}
        >
          {[...data.anni_disponibili].reverse().map((a) => (
            <option key={a} value={a}>{a}</option>
          ))}
        </select>
        <button onClick={onRefresh} disabled={loading}>
          {loading ? "Aggiorno…" : "Aggiorna dati"}
        </button>
      </div>
    </header>
  );
}

function Delta({ attuale, prima, anno }: { attuale: string; prima: string; anno: number }) {
  const a = parseFloat(attuale);
  const p = parseFloat(prima);
  if (!(p > 0)) return null;
  const d = (a - p) / p;
  return (
    <div className="card-sub">
      vs {anno} stesso periodo ({euro(prima, true)}):{" "}
      <strong className={d >= 0 ? "pos" : "neg"}>
        {d >= 0 ? "+" : ""}{(d * 100).toFixed(1).replace(".", ",")}%
      </strong>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Card superiori
// ---------------------------------------------------------------------------

function CardIncassato({ data }: { data: DashboardResponse }) {
  const proj = data.proiezione_anno_corrente;
  return (
    <div className="card card-primary">
      <div className="card-label">Incassato {data.anno_corrente}</div>
      <div className="card-value">{euro(proj.incassato_ytd)}</div>
      {!data.consuntivo && (
        <div className="card-sub">
          Proiezione fine anno: <strong>{euro(proj.incassato_proiettato, true)}</strong>
        </div>
      )}
      <Delta attuale={proj.incassato_ytd} prima={proj.incassato_stesso_periodo_prec} anno={data.anno_corrente - 1} />
    </div>
  );
}

function CardFatturato({ data }: { data: DashboardResponse }) {
  const proj = data.proiezione_anno_corrente;
  return (
    <div className="card">
      <div className="card-label">Fatturato emesso {data.anno_corrente}</div>
      <div className="card-value">{euro(proj.fatturato_ytd)}</div>
      <div className="card-sub">
        Crediti aperti{data.consuntivo ? " al 31/12" : ""}: <strong>{euro(data.crediti_aperti)}</strong>
      </div>
      <Delta attuale={proj.fatturato_ytd} prima={proj.fatturato_stesso_periodo_prec} anno={data.anno_corrente - 1} />
    </div>
  );
}

function CardNetto({ data }: { data: DashboardResponse }) {
  const b = data.status_odierno;
  const base = parseFloat(b.base_utilizzata);
  const acc = parseFloat(b.totale_accantonamento);
  return (
    <div className="card">
      <div className="card-label">Netto dopo tasse {data.anno_corrente}</div>
      <div className="card-value">{euro(base - acc)}</div>
      <div className="card-sub">
        Incassato − imposte e contributi · pressione{" "}
        <strong>{base > 0 ? percento(acc / base) : "—"}</strong>
      </div>
    </div>
  );
}

function CardSoglia({ data }: { data: DashboardResponse }) {
  const pct = Math.min(100, parseFloat(data.soglia_forfettario.percentuale_utilizzo) * 100);
  const pctLabel = `${pct.toFixed(1).replace(".", ",")}%`;
  const alarm = pct >= 80;
  return (
    <div className={`card${alarm ? " card-alarm" : ""}`}>
      <div className="card-label">Soglia forfettario (85.000 €)</div>
      <div className="card-value">{pctLabel}</div>
      <div className="progress-bar">
        <div
          className="progress-fill"
          style={{ width: `${pct}%`, background: alarm ? "var(--bad)" : "var(--accent)" }}
        />
      </div>
      <div className="card-sub">
        {euro(data.soglia_forfettario.fatturato_anno_corrente, true)} / {euro(data.soglia_forfettario.soglia, true)}
      </div>
    </div>
  );
}

function CardImponibile({ breakdown }: { breakdown: TaxBreakdownDTO }) {
  return (
    <div className="card">
      <div className="card-label">Utile fiscale (imponibile)</div>
      <div className="card-value">{euro(breakdown.imponibile_lordo)}</div>
      <div className="card-sub">
        Coefficiente redditività: <strong>{percento(breakdown.coefficiente_redditivita)}</strong>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pannello accantonamento (oggi vs previsione)
// ---------------------------------------------------------------------------

function PanelAccantonamento({ data }: { data: DashboardResponse }) {
  if (data.consuntivo) {
    return (
      <div className="acc-grid acc-grid-single">
        <AccantonamentoCol
          title={`DOVUTO ${data.anno_corrente} · CONSUNTIVO`}
          subtitle={`Su tutto l'incassato ${data.anno_corrente}: si versa come saldo a giugno ${data.anno_corrente + 1} (al netto degli acconti) ed è la base degli acconti ${data.anno_corrente + 1}`}
          tone="now"
          breakdown={data.status_odierno}
        />
      </div>
    );
  }
  return (
    <div className="acc-grid">
      <AccantonamentoCol
        title="DA ACCANTONARE OGGI"
        subtitle="In base all'incassato YTD — quanto dovresti avere già da parte"
        tone="now"
        breakdown={data.status_odierno}
      />
      <AccantonamentoCol
        title="PREVISIONE FINE ANNO"
        subtitle="Proiezione lineare dell'incassato a 365 giorni"
        tone="projection"
        breakdown={data.previsione_fine_anno}
      />
    </div>
  );
}

function AccantonamentoCol({
  title,
  subtitle,
  tone,
  breakdown,
}: {
  title: string;
  subtitle: string;
  tone: "now" | "projection";
  breakdown: TaxBreakdownDTO;
}) {
  return (
    <div className={`acc-col acc-col-${tone}`}>
      <div className="acc-title">{title}</div>
      <div className="acc-subtitle">{subtitle}</div>
      <div className="acc-total">{euro(breakdown.totale_accantonamento)}</div>
      <div className="acc-breakdown">
        <div className="acc-row">
          <span>Imposta sostitutiva</span>
          <span className="mono">
            {euro(breakdown.imposta_sostitutiva_dovuta)}
            <span className="acc-rate"> · {percento(breakdown.aliquota_sostitutiva)}</span>
          </span>
        </div>
        <div className="acc-row">
          <span>INPS Gestione Separata</span>
          <span className="mono">
            {euro(breakdown.inps_dovuta)}
            <span className="acc-rate"> · {percento(breakdown.aliquota_inps)}</span>
          </span>
        </div>
        <div className="acc-row acc-row-base">
          <span>Base: {baseLabel(breakdown.origine_base)}</span>
          <span className="mono">{euro(breakdown.base_utilizzata)}</span>
        </div>
      </div>
    </div>
  );
}

function baseLabel(origine: TaxBreakdownDTO["origine_base"]): string {
  switch (origine) {
    case "incassato": return "incassato";
    case "incassato_proiettato": return "incassato proiettato";
    case "fatturato_emesso": return "fatturato emesso";
  }
}

// ---------------------------------------------------------------------------
// Grafico trend mensile dual-year
// ---------------------------------------------------------------------------

function TrendIncassatoChart({
  corrente,
  precedente,
  annoCorrente,
}: {
  corrente: IncassatoMese[];
  precedente: IncassatoMese[];
  annoCorrente: number;
}) {
  const chartData = useMemo(() => {
    // Uniamo le due serie su base mese (1..12)
    return Array.from({ length: 12 }, (_, i) => {
      const m = i + 1;
      return {
        mese: MESI_BREVI[i],
        corrente: parseFloat(corrente.find(x => x.mese === m)?.importo ?? "0"),
        precedente: parseFloat(precedente.find(x => x.mese === m)?.importo ?? "0"),
      };
    });
  }, [corrente, precedente]);

  return (
    <div style={{ width: "100%", height: 300 }}>
      <ResponsiveContainer>
        <AreaChart data={chartData} margin={{ top: 12, right: 16, bottom: 4, left: 0 }}>
          <defs>
            <linearGradient id="gradCorr" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#4f9dff" stopOpacity={0.55} />
              <stop offset="100%" stopColor="#4f9dff" stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="gradPrec" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#8b93a3" stopOpacity={0.18} />
              <stop offset="100%" stopColor="#8b93a3" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#2a2f3a" />
          <XAxis dataKey="mese" stroke="#cbd1dc" />
          <YAxis
            stroke="#cbd1dc"
            tickFormatter={(v) => euro(v, true)}
            width={90}
          />
          <Tooltip
            contentStyle={{ background: "#1a1d24", border: "1px solid #2a2f3a" }}
            formatter={(v: number, name: string) => [euro(v), name]}
          />
          <Legend />
          <Area
            type="monotone"
            dataKey="precedente"
            name={String(annoCorrente - 1)}
            stroke="#8b93a3"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            fill="url(#gradPrec)"
          />
          <Area
            type="monotone"
            dataKey="corrente"
            name={String(annoCorrente)}
            stroke="#4f9dff"
            strokeWidth={2}
            fill="url(#gradCorr)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tabelle
// ---------------------------------------------------------------------------

function Scadenze({ scadenze, vuoto }: { scadenze: ScadenzaDTO[]; vuoto: string }) {
  if (scadenze.length === 0) return <div className="empty">{vuoto}</div>;
  const oggi = new Date();
  oggi.setHours(0, 0, 0, 0);
  const perData = new Map<string, ScadenzaDTO[]>();
  for (const s of scadenze) perData.set(s.data, [...(perData.get(s.data) ?? []), s]);
  const totaleAnno = scadenze.reduce((t, s) => t + parseFloat(s.importo_stimato), 0);
  return (
    <div className="scad">
      {[...perData.entries()].map(([data, items]) => {
        const giorni = Math.round((new Date(data + "T00:00:00").getTime() - oggi.getTime()) / 86400000);
        const totale = items.reduce((t, s) => t + parseFloat(s.importo_stimato), 0);
        return (
          <div key={data} className={`scad-group${giorni < 0 ? " scad-past" : ""}`}>
            <div className="scad-head">
              <div>
                <strong>{dataIta(data)}</strong>
                <span className={`badge ${giorni < 0 ? "badge-unpaid" : giorni <= 30 ? "badge-partial" : "badge-paid"}`}>
                  {giorni < 0 ? "passata" : giorni === 0 ? "oggi" : `tra ${giorni} gg`}
                </span>
              </div>
              <div className="mono scad-tot">{euro(totale)}</div>
            </div>
            {items.map((s, i) => (
              <div key={i} className="scad-row" title={s.descrizione}>
                <span>{labelTipoScadenza(s.tipo)} <span className="desc">· anno {s.anno_imposta_riferimento}</span></span>
                <span className="mono">{euro(s.importo_stimato)}</span>
              </div>
            ))}
          </div>
        );
      })}
      <div className="scad-row scad-anno">
        <span>Totale nell'anno</span>
        <span className="mono">{euro(totaleAnno)}</span>
      </div>
    </div>
  );
}

function Clienti({ data }: { data: DashboardResponse }) {
  const clienti = data.clienti;
  if (clienti.length === 0) return <div className="empty">Nessun cliente nell'anno.</div>;
  const quote = clienti.map((c) => parseFloat(c.quota_fatturato));
  const top3 = quote.slice(0, 3).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="card-sub" style={{ marginBottom: 10 }}>
        Primo cliente: <strong>{percento(quote[0])}</strong> del fatturato · primi tre:{" "}
        <strong>{percento(top3)}</strong> · clienti fatturati:{" "}
        <strong>{clienti.filter((c) => c.n_fatture > 0).length}</strong>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Cliente</th>
              <th style={{ textAlign: "right" }}>Fatture</th>
              <th style={{ textAlign: "right" }}>Fatturato</th>
              <th style={{ width: "22%" }}>Quota</th>
              <th style={{ textAlign: "right" }}>Incassato</th>
              <th style={{ textAlign: "right" }}>Gg medi incasso</th>
              <th style={{ textAlign: "right" }}>Da incassare</th>
            </tr>
          </thead>
          <tbody>
            {clienti.map((c) => (
              <tr key={c.cliente}>
                <td className="cliente">{c.cliente}</td>
                <td style={{ textAlign: "right" }}>{c.n_fatture}</td>
                <td style={{ textAlign: "right" }} className="mono">{euro(c.fatturato)}</td>
                <td>
                  <div className="quota">
                    <div className="progress-bar"><div className="progress-fill" style={{ width: `${parseFloat(c.quota_fatturato) * 100}%`, background: "var(--accent)" }} /></div>
                    <span>{percento(c.quota_fatturato)}</span>
                  </div>
                </td>
                <td style={{ textAlign: "right" }} className="mono">{euro(c.incassato)}</td>
                <td style={{ textAlign: "right" }}>{c.giorni_medi_incasso ? parseFloat(c.giorni_medi_incasso).toFixed(0) : "—"}</td>
                <td style={{ textAlign: "right" }} className={`mono${parseFloat(c.da_incassare) > 0 ? " neg" : ""}`}>{euro(c.da_incassare)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function Fatture({ data }: { data: DashboardResponse }) {
  if (data.ultime_fatture.length === 0) {
    return <div className="empty">Nessuna fattura recuperata.</div>;
  }
  return (
    <div className="table-wrap">
    <table className="table">
      <thead>
        <tr>
          <th>Data</th>
          <th>N°</th>
          <th>Cliente</th>
          <th style={{ textAlign: "right" }}>Lordo</th>
          <th>Stato</th>
        </tr>
      </thead>
      <tbody>
        {data.ultime_fatture.map((f) => (
          <tr key={f.id}>
            <td>{dataIta(f.data)}</td>
            <td>{f.numero}</td>
            <td className="cliente">{f.cliente}</td>
            <td style={{ textAlign: "right" }}>{euro(f.totale_lordo)}</td>
            <td><PaymentBadge stato={f.stato_pagamento} /></td>
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

function PaymentBadge({ stato }: { stato: "paid" | "paid_partially" | "not_paid" }) {
  const map: Record<string, { label: string; cls: string }> = {
    paid: { label: "Incassata", cls: "badge-paid" },
    paid_partially: { label: "Parziale", cls: "badge-partial" },
    not_paid: { label: "Da incassare", cls: "badge-unpaid" },
  };
  const { label, cls } = map[stato];
  return <span className={`badge ${cls}`}>{label}</span>;
}

function labelTipoScadenza(tipo: string): string {
  const map: Record<string, string> = {
    acconto_1_sostitutiva: "I acconto imposta sostitutiva",
    acconto_2_sostitutiva: "II acconto imposta sostitutiva",
    acconto_unico_sostitutiva: "Acconto unico imposta sostitutiva",
    saldo_sostitutiva: "Saldo imposta sostitutiva",
    acconto_1_inps: "I acconto INPS GS",
    acconto_2_inps: "II acconto INPS GS",
    saldo_inps: "Saldo INPS GS",
  };
  return map[tipo] ?? tipo;
}

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function FullPage({
  message,
  error,
  onRetry,
}: {
  message?: string;
  error?: ApiError;
  onRetry?: () => void;
}) {
  return (
    <div className="fullpage">
      {message && <div>{message}</div>}
      {error && (
        <div className="fullpage-error">
          <h2>Errore nel caricamento</h2>
          <div>{error.message}</div>
          <pre>{JSON.stringify(error.detail, null, 2)}</pre>
          {onRetry && <button onClick={onRetry}>Riprova</button>}
        </div>
      )}
    </div>
  );
}

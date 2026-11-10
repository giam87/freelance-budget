# freelance-budget

Dashboard fiscale per freelance in **regime forfettario** (gestione separata INPS),
alimentata dalle fatture di [Fatture in Cloud](https://www.fattureincloud.it/).

- Stima di imposta sostitutiva e contributi INPS sull'incassato (principio di cassa)
  e proiezione a fine anno.
- Scadenze F24 dell'anno in corso e del successivo, con saldo al netto degli acconti versati.
- Consuntivo degli anni chiusi e confronto con lo stesso periodo dell'anno prima.
- Distribuzione del fatturato per cliente (raggruppato per P.IVA), tempi medi di incasso, crediti aperti.
- Calendario `.ics` delle scadenze con promemoria 4 giorni prima.
- Accesso condivisibile (per esempio con il commercialista) tramite token.

> Le cifre sono stime per pianificare: non sostituiscono il calcolo del commercialista.

## Struttura

- `src/freelance_budget/core/`: regole fiscali e calendario versamenti, senza I/O.
- `src/freelance_budget/integrations/fatture_in_cloud/`: OAuth e client API.
- `src/freelance_budget/api/`: FastAPI (`/api/dashboard`, `/scadenze.ics`).
- `config/tax_rules.yaml`: aliquote e soglie per anno. `config/user_settings.yaml`: profilo fiscale.
- `web/`: frontend React + Vite, compilato in `web/dist` e servito da FastAPI.

## Avvio

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env          # credenziali app FIC, SHARE_TOKEN opzionale
.venv/bin/python scripts/authorize_fic.py   # salva il token OAuth in .fic_token.json
(cd web && npm ci && npm run build)
.venv/bin/uvicorn freelance_budget.api.app:app --port 8000
```

Test: `.venv/bin/pytest`.

## Accesso con token

Con `SHARE_TOKEN` impostato in `.env`, ogni pagina richiede il token (tranne `/api/health`):

- `https://<host>/?t=<token>` salva un cookie per 180 giorni e toglie il token dall'URL;
- `https://<host>/scadenze.ics?t=<token>` è l'indirizzo da aggiungere come calendario
  (le app calendario non conservano cookie, quindi qui il token resta nell'URL).

Cambiare `SHARE_TOKEN` e riavviare revoca tutti gli accessi.

`.env`, `.fic_token.json` e gli export delle fatture sono esclusi dal repository.

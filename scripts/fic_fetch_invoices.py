"""Primo sync esplorativo: recupera le fatture emesse da FIC e stampa un sommario.

Serve per:
  1. Validare che OAuth + client HTTP funzionino end-to-end con dati reali
  2. Vedere la forma effettiva della response per scrivere il mapping corretto

NON scrive ancora su DB. Solo stdout. Nessun calcolo fiscale qui.

Uso:
  python scripts/fic_fetch_invoices.py            # stampa sommario e 1 fattura raw
  python scripts/fic_fetch_invoices.py --year 2025
  python scripts/fic_fetch_invoices.py --dump out.json   # salva JSON raw
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

load_dotenv(ROOT / ".env")

from freelance_budget.integrations.fatture_in_cloud.client import FicClient  # noqa: E402
from freelance_budget.integrations.fatture_in_cloud.config import FicConfig  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sync esplorativo fatture emesse da FIC")
    p.add_argument(
        "--year",
        type=int,
        default=None,
        help="Filtra fatture per anno (match su campo 'date'). Default: tutte.",
    )
    p.add_argument(
        "--dump",
        type=Path,
        default=None,
        help="Scrivi la lista fatture raw (JSON) in questo path",
    )
    p.add_argument(
        "--company-id",
        type=int,
        default=None,
        help="Forza company_id, saltando la lookup",
    )
    p.add_argument(
        "--detail",
        type=int,
        default=None,
        help="Stampa il dettaglio completo (inclusi payments_list) di una fattura",
    )
    p.add_argument(
        "--with-payments",
        action="store_true",
        help="Richiede i campi payments_list nella list (se FIC lo supporta)",
    )
    return p.parse_args()


def _invoice_year(raw: dict) -> int | None:
    d = raw.get("date")
    if not d:
        return None
    try:
        return date.fromisoformat(d).year
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    args = _parse_args()
    config = FicConfig.from_env(ROOT)

    with FicClient(config=config) as client:
        if args.company_id is None:
            companies = client.list_companies()
            if not companies:
                print("❌ Nessuna company accessibile con questo token.")
                return 1
            if len(companies) > 1:
                print("Aziende disponibili:")
                for c in companies:
                    print(f"  id={c.get('id')}  name={c.get('name')}")
                print("Rilancia con --company-id=ID.")
                return 1
            company = companies[0]
            company_id = company["id"]
            print(f"Azienda: {company.get('name')} (id={company_id})")
        else:
            company_id = args.company_id

        # Modalità "dettaglio singola fattura": stampa e basta
        if args.detail is not None:
            print(f"\nRecupero dettaglio fattura id={args.detail}...")
            detail = client.get_issued_invoice(company_id=company_id, document_id=args.detail)
            print("\n--- Dettaglio completo (raw JSON) ---")
            print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
            if args.dump:
                args.dump.write_text(
                    json.dumps(detail, indent=2, ensure_ascii=False, default=str),
                    encoding="utf-8",
                )
                print(f"\nSalvato in {args.dump}")
            return 0

        print(f"\nScarico fatture emesse (company_id={company_id})...")
        fields = "payments_list" if args.with_payments else None
        all_invoices = list(
            client.list_issued_invoices(company_id=company_id, fields=fields)
        )

    print(f"Totale fatture trovate: {len(all_invoices)}")

    if args.year is not None:
        filtered = [inv for inv in all_invoices if _invoice_year(inv) == args.year]
        print(f"Filtrate per anno {args.year}: {len(filtered)}")
        all_invoices = filtered

    # Sommario per anno
    per_anno_count: dict[int | None, int] = defaultdict(int)
    per_anno_gross: dict[int | None, float] = defaultdict(float)
    for inv in all_invoices:
        y = _invoice_year(inv)
        per_anno_count[y] += 1
        per_anno_gross[y] += _to_float(inv.get("amount_gross"))

    print("\nSommario per anno (totale lordo da campo amount_gross):")
    for y in sorted((y for y in per_anno_count if y is not None)):
        print(f"  {y}: {per_anno_count[y]:3d} fatture — €{per_anno_gross[y]:>10.2f} lordo")
    if None in per_anno_count:
        print(f"  senza data: {per_anno_count[None]} fatture")

    # Dump raw
    if args.dump:
        args.dump.write_text(
            json.dumps(all_invoices, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        print(f"\nResponse raw salvata in {args.dump}")

    # Campione: prima fattura per ispezionare la forma della response
    if all_invoices:
        print("\n--- Prima fattura (raw, campi principali) ---")
        first = all_invoices[0]
        keys_of_interest = [
            "id", "number", "numeration", "date", "amount_net", "amount_vat",
            "amount_gross", "amount_rivalsa", "amount_due_discount",
            "payment_method", "payments_list", "ei_status", "entity",
        ]
        preview = {k: first.get(k) for k in keys_of_interest if k in first}
        print(json.dumps(preview, indent=2, ensure_ascii=False, default=str))
        extra_keys = sorted(set(first.keys()) - set(keys_of_interest))
        if extra_keys:
            print(f"\n(altri campi presenti: {', '.join(extra_keys)})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

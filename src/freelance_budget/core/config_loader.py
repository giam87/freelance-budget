"""Caricamento regole fiscali e settings utente dai YAML di config.

Il caricamento è separato dalla logica di calcolo: il motore (tax_engine) riceve
oggetti già validati e non conosce YAML o filesystem.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from .models import (
    GiornoMese,
    Previdenza,
    RegoleFiscaliAnno,
    RegoleVersamento,
    RegoleVersamentoTributo,
    UserSettings,
)


class ConfigError(Exception):
    """Errori di configurazione (file mancante, ATECO non mappato, anno non disponibile)."""


def _to_decimal(value) -> Decimal:
    # YAML può produrre float per i numeri: convertiamo via str per preservare
    # la rappresentazione decimale esatta (evita 0.15 → 0.14999...).
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def load_tax_rules_for_year(anno: int, rules_path: Path) -> RegoleFiscaliAnno:
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    for blocco in data.get("anni", []):
        if blocco["anno"] == anno:
            return RegoleFiscaliAnno(
                anno=anno,
                aliquota_sostitutiva_ordinaria=_to_decimal(
                    blocco["imposta_sostitutiva"]["aliquota_ordinaria"]
                ),
                aliquota_sostitutiva_agevolata=_to_decimal(
                    blocco["imposta_sostitutiva"]["aliquota_agevolata_primi_5_anni"]
                ),
                aliquota_inps_gs_senza_altra_copertura=_to_decimal(
                    blocco["inps_gestione_separata"]["aliquota_senza_altra_copertura"]
                ),
                aliquota_inps_gs_con_altra_copertura=_to_decimal(
                    blocco["inps_gestione_separata"]["aliquota_con_altra_copertura_o_pensionati"]
                ),
                massimale_inps_gs=(
                    _to_decimal(blocco["inps_gestione_separata"]["massimale_contributivo"])
                    if blocco["inps_gestione_separata"].get("massimale_contributivo") is not None
                    else None
                ),
                fonte=blocco.get("fonte", ""),
            )
    raise ConfigError(
        f"Nessuna regola fiscale definita per l'anno {anno}. "
        f"Aggiungere il blocco in {rules_path.name} sotto `anni:`."
    )


def load_user_settings(
    settings_path: Path, rules_path: Path
) -> UserSettings:
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    rules_data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))

    ateco = data["attivita"]["codice_ateco"]
    coefficienti = rules_data["regime_forfettario"]["coefficienti_redditivita"]
    if ateco not in coefficienti:
        raise ConfigError(
            f"ATECO {ateco} non presente in tax_rules.yaml → "
            f"aggiungere coefficiente di redditività in `regime_forfettario.coefficienti_redditivita`."
        )

    return UserSettings(
        anno_apertura_piva=data["anagrafica"]["anno_apertura_partita_iva"],
        codice_ateco=ateco,
        coefficiente_redditivita=_to_decimal(coefficienti[ateco]),
        previdenza=Previdenza(data["previdenza"]["tipo"]),
        ha_altra_copertura_previdenziale=bool(
            data["previdenza"]["ha_altra_copertura_previdenziale"]
        ),
    )


def _parse_regole_tributo(blocco: dict) -> RegoleVersamentoTributo:
    soglia = blocco.get("soglia_frazionamento_euro")
    return RegoleVersamentoTributo(
        pct_acconto_totale_su_anno_precedente=_to_decimal(
            blocco["pct_acconto_totale_su_anno_precedente"]
        ),
        split_acconto_1=_to_decimal(blocco["split_acconto_1"]),
        split_acconto_2=_to_decimal(blocco["split_acconto_2"]),
        soglia_frazionamento_euro=(_to_decimal(soglia) if soglia is not None else None),
        giorno_acconto_1=GiornoMese(**blocco["giorno_acconto_1"]),
        giorno_acconto_2=GiornoMese(**blocco["giorno_acconto_2"]),
        giorno_saldo=GiornoMese(**blocco["giorno_saldo"]),
    )


def load_regole_versamento(rules_path: Path) -> RegoleVersamento:
    data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
    blocco = data.get("regole_versamento")
    if not blocco:
        raise ConfigError(
            f"Sezione `regole_versamento` assente in {rules_path.name}."
        )
    return RegoleVersamento(
        imposta_sostitutiva=_parse_regole_tributo(blocco["imposta_sostitutiva"]),
        inps_gestione_separata=_parse_regole_tributo(blocco["inps_gestione_separata"]),
    )

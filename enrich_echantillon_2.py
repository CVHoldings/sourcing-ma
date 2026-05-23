"""Échantillon 2 — Enrichissement INPI des cibles retenues en échantillon 1.

Pipeline :
1. Charge les 307 cibles retenues échantillon 1 (raw_results.json filtré)
2. Pour chaque SIREN : appelle l'API INPI RNE (avec cache local)
3. Extrait dates de naissance + qualité fonction + dirigeants PM
4. Re-filtre sur âge dirigeant principal ≥ 55 (strict)
5. Génère screening_echantillon_2.xlsx
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from src.api_gouv import APIGouvClient, codes_tranche_effectif
from src.filtres import appliquer_filtres_tranche1
from src.inpi import (
    INPIClient,
    load_credentials_from_env,
    extract_dirigeants_pp,
    extract_dirigeants_pm,
    age_dirigeant_min,
)


AGE_MIN = 55
RUN_DIR = Path("runs/2026-05-22_NAF-4669B")
OUTPUT_XLSX = RUN_DIR / "screening_echantillon_2.xlsx"


def main():
    project_root = Path(__file__).parent

    # 1. Recharger échantillon 1 — on rejoue les filtres tranche 1 pour récupérer les cibles
    raw_path = RUN_DIR / "raw_results.json"
    print(f"[1/4] Chargement échantillon 1 ({raw_path})")
    with open(raw_path, encoding="utf-8") as f:
        entreprises = json.load(f)
    retenues, _ = appliquer_filtres_tranche1(
        entreprises,
        anciennete_min=40,
        exiger_dirigeant_pp=True,
        exclure_filiales_groupe=True,
        tolerer_patrimoniales=True,
    )
    sirens = [e["siren"] for e in retenues]
    print(f"      → {len(sirens)} SIREN à enrichir")

    # 2. Auth INPI
    print("[2/4] Authentification INPI...")
    user, pw = load_credentials_from_env()
    client = INPIClient(user, pw, cache_dir=project_root / "data" / "cache_inpi", pause_sec=0.3)
    client._login()
    print("      → JWT obtenu")

    # 3. Lookup batch
    print(f"[3/4] Lookup INPI sur {len(sirens)} SIREN...")
    enriched = client.enrich_batch(sirens, use_cache=True)
    print(f"      → {sum(1 for v in enriched.values() if not v.get('_error') and not v.get('_not_found'))} OK / "
          f"{sum(1 for v in enriched.values() if v.get('_not_found'))} non trouvés / "
          f"{sum(1 for v in enriched.values() if v.get('_error'))} erreurs")

    # 4. Construire le DataFrame enrichi
    print("[4/4] Construction de l'Excel échantillon 2...")
    import pandas as pd

    rows_retenues = []
    rows_rejetees_age = []
    rows_rejetees_pm = []
    rows_non_trouves = []
    rows_erreurs = []

    siren_to_meta = {e["siren"]: e for e in retenues}

    for siren in sirens:
        inpi_data = enriched.get(siren) or {}
        meta = siren_to_meta.get(siren, {})
        siege = meta.get("siege") or {}

        base_row = {
            "SIREN": siren,
            "Dénomination": meta.get("nom_complet") or meta.get("nom_raison_sociale"),
            "Forme": meta.get("nature_juridique"),
            "NAF": meta.get("activite_principale"),
            "Effectif (tranche)": meta.get("tranche_effectif_salarie"),
            "Date création": (meta.get("date_creation") or "")[:10],
            "Ancienneté (ans)": meta.get("_anciennete"),
            "Commune": siege.get("libelle_commune"),
            "Département": siege.get("departement"),
            "Région": siege.get("region"),
            "Annuaire": f"https://annuaire-entreprises.data.gouv.fr/entreprise/{siren}",
        }

        if inpi_data.get("_error"):
            rows_erreurs.append({**base_row, "Erreur INPI": inpi_data["_error"]})
            continue
        if inpi_data.get("_not_found"):
            rows_non_trouves.append(base_row)
            continue

        pp = extract_dirigeants_pp(inpi_data, exclure_cac=True)
        pm = extract_dirigeants_pm(inpi_data, exclure_cac=True)
        age, plus_jeune = age_dirigeant_min(inpi_data)

        dirigeants_str = "; ".join(
            f"{d.get('prenoms', '')} {d.get('nom', '')} ({d.get('role_libelle')}, "
            f"né {d.get('date_naissance', '?')})"
            for d in pp if d.get("is_direction")
        )
        pm_str = "; ".join(
            f"{p.get('denomination')} ({p.get('role_libelle')})"
            for p in pm if p.get("is_direction")
        )

        row = {
            **base_row,
            "Âge dirigeant min": age,
            "Plus jeune dirigeant": plus_jeune,
            "Dirigeants PP direction": dirigeants_str,
            "Personnes morales en direction": pm_str,
        }

        # Filtre final
        if pm_str:
            row["Motif rejet échantillon 2"] = "PM en direction (filiale potentielle)"
            rows_rejetees_pm.append(row)
        elif age is None:
            row["Motif rejet échantillon 2"] = "Âge dirigeant inconnu (pas de date de naissance RNE)"
            rows_rejetees_age.append(row)
        elif age < AGE_MIN:
            row["Motif rejet échantillon 2"] = f"Dirigeant trop jeune ({age} < {AGE_MIN})"
            rows_rejetees_age.append(row)
        else:
            rows_retenues.append(row)

    df_ret = pd.DataFrame(rows_retenues).sort_values("Âge dirigeant min", ascending=False)
    df_rej_age = pd.DataFrame(rows_rejetees_age)
    df_rej_pm = pd.DataFrame(rows_rejetees_pm)
    df_nf = pd.DataFrame(rows_non_trouves)
    df_err = pd.DataFrame(rows_erreurs)

    synthese = pd.DataFrame([
        ("Date échantillon 2", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Source enrichissement", "API INPI RNE (data.inpi.fr)"),
        ("SIREN échantillon 1 à enrichir", len(sirens)),
        ("Cibles retenues échantillon 2 (âge ≥ 55)", len(rows_retenues)),
        ("Rejetées — dirigeant trop jeune ou inconnu", len(rows_rejetees_age)),
        ("Rejetées — PM en direction (filiale)", len(rows_rejetees_pm)),
        ("Non trouvés INPI", len(rows_non_trouves)),
        ("Erreurs API", len(rows_erreurs)),
    ], columns=["Indicateur", "Valeur"])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as w:
        synthese.to_excel(w, sheet_name="Synthèse échantillon 2", index=False)
        df_ret.to_excel(w, sheet_name="Cibles retenues", index=False)
        df_rej_age.to_excel(w, sheet_name="Rejetées par âge", index=False)
        df_rej_pm.to_excel(w, sheet_name="Rejetées (PM dirigeante)", index=False)
        if not df_nf.empty:
            df_nf.to_excel(w, sheet_name="Non trouvés INPI", index=False)
        if not df_err.empty:
            df_err.to_excel(w, sheet_name="Erreurs", index=False)

    # Polish via export.py
    from src.export import _polish
    _polish(OUTPUT_XLSX)

    print(f"\n✓ Échantillon 2 livré : {OUTPUT_XLSX}")
    print(f"  → {len(rows_retenues)} cibles avec dirigeant ≥ {AGE_MIN} ans confirmé")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Échantillon 3 — Enrichissement financier des cibles échantillon 2.

Pipeline :
1. Charge les 118 cibles échantillon 2 (recalculées avec mapping corrigé)
2. Pour chaque SIREN : récupère /attachments + dernier bilanSaisi
3. Extrait CA, REX, EBE proxy, dette nette, capitaux propres
4. Calcule ratios + valo proxy = 4× EBE × (1 - 20% décote)
5. Filtre : EBE/CA ≥ 8 %, dette/EBE ≤ 2,5×, valo ≤ 2 M€
6. Génère screening_echantillon_3.xlsx
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from src.api_gouv import APIGouvClient
from src.filtres import appliquer_filtres_tranche1
from src.inpi import (
    INPIClient,
    load_credentials_from_env,
    extract_dirigeants_pp,
    extract_dirigeants_pm,
    age_dirigeant_min,
    get_attachments,
    latest_bilan,
    extract_bilan,
    compute_ratios,
    bilan_age_years,
)

BILAN_AGE_MAX_OK = 3   # au-delà → flag d'alerte


AGE_MIN = 55
EBE_MARGE_MIN = 0.08
GEARING_MAX = 2.5
VALO_MAX = 2_000_000
RUN_DIR = Path("runs/2026-05-22_NAF-4669B")
OUTPUT_XLSX = RUN_DIR / "screening_echantillon_3.xlsx"


def main():
    project_root = Path(__file__).parent

    # 1. Recharger les 118 cibles échantillon 2 (re-rejouer le filtre tranche 2)
    print("[1/5] Chargement échantillon 1 + filtre tranche 1 (mapping corrigé)")
    raw_path = RUN_DIR / "raw_results.json"
    entreprises = json.load(open(raw_path, encoding="utf-8"))
    sp1_retenues, _ = appliquer_filtres_tranche1(
        entreprises,
        anciennete_min=40,
        exiger_dirigeant_pp=True,
        exclure_filiales_groupe=True,
        tolerer_patrimoniales=True,
    )
    print(f"      → {len(sp1_retenues)} cibles échantillon 1")

    # 2. Re-filtrer sur âge dirigeant via INPI (mapping corrigé)
    print(f"[2/5] Lookup INPI + filtre âge ≥ {AGE_MIN}")
    user, pw = load_credentials_from_env()
    client = INPIClient(user, pw, cache_dir=project_root / "data" / "cache_inpi")
    client._login()

    sp2_cibles = []
    sirens = [e["siren"] for e in sp1_retenues]
    siren_to_meta = {e["siren"]: e for e in sp1_retenues}

    for siren in tqdm(sirens, desc="INPI age", unit="SIREN"):
        company = client.get_company(siren, use_cache=True)
        if company.get("_not_found") or company.get("_error"):
            continue
        pp = extract_dirigeants_pp(company, exclure_cac=True)
        pm = extract_dirigeants_pm(company, exclure_cac=True)
        # Rejet si PM en direction
        if any(p.get("is_direction") for p in pm):
            continue
        age, plus_jeune = age_dirigeant_min(company)
        if age is None or age < AGE_MIN:
            continue
        sp2_cibles.append({
            "siren": siren, "meta": siren_to_meta[siren],
            "age_min": age, "plus_jeune": plus_jeune,
            "dirigeants_pp": pp,
        })
    print(f"      → {len(sp2_cibles)} cibles échantillon 2 (âge ≥ {AGE_MIN})")

    # 3. Enrichissement bilans via /attachments
    print(f"[3/5] Récupération bilans saisis sur {len(sp2_cibles)} cibles")
    for cible in tqdm(sp2_cibles, desc="INPI bilans", unit="SIREN"):
        att = get_attachments(client, cible["siren"], use_cache=True)
        bilan_doc = latest_bilan(att)
        if bilan_doc is None:
            cible["bilan_status"] = "non disponible (confidentialité ou pas déposé)"
            cible["ratios"] = {}
            cible["bilan_brut"] = None
            continue
        bilan_data = extract_bilan(bilan_doc)
        cible["bilan_brut"] = bilan_data
        cible["ratios"] = compute_ratios(bilan_data)
        cible["bilan_status"] = "OK"

    # 4. Filtrage tranche 3 (finance)
    print(f"[4/5] Filtre finance — EBE/CA ≥ {EBE_MARGE_MIN:.0%}, dette/EBE ≤ {GEARING_MAX}, valo ≤ {VALO_MAX:_} €")
    rows_retenues = []
    rows_rej_finance = []
    rows_pas_de_bilan = []
    rows_finance_partielle = []

    for c in sp2_cibles:
        meta = c["meta"]
        siege = meta.get("siege") or {}
        ratios = c.get("ratios") or {}
        bilan = c.get("bilan_brut") or {}

        # Format des dirigeants pour Excel
        def _fmt_d(d):
            prenom = ((d.get('prenoms') or '').split() or [''])[0]
            return (f"{prenom} {d.get('nom', '')} ({d.get('role_libelle')}, "
                    f"né {d.get('date_naissance', '?')})").strip()
        dir_str = "; ".join(_fmt_d(d) for d in c["dirigeants_pp"] if d.get("is_direction"))

        # Ancienneté du bilan + flag obsolescence
        age_b = bilan_age_years(bilan) if bilan else None
        flag_obsolete = (age_b is not None and age_b > BILAN_AGE_MAX_OK)
        marge = ratios.get("marge_ebe")
        autonomie = ratios.get("autonomie")

        base_row = {
            "SIREN": c["siren"],
            "Dénomination": meta.get("nom_complet"),
            "Forme": meta.get("nature_juridique"),
            "NAF": meta.get("activite_principale"),
            "Effectif": meta.get("tranche_effectif_salarie"),
            "Ancienneté (ans)": meta.get("_anciennete"),
            "Commune": siege.get("libelle_commune"),
            "Département": siege.get("departement"),
            "Région": siege.get("region"),
            "Âge dirigeant min": c["age_min"],
            "Plus jeune dirigeant": c["plus_jeune"],
            "Dirigeants": dir_str,
            "Date clôture exercice": bilan.get("date_cloture"),
            "Âge du bilan (ans)": age_b,
            "Bilan obsolète (>3 ans)": "OUI" if flag_obsolete else "",
            "Type bilan": bilan.get("code_type_bilan"),
            "CA (€)": ratios.get("ca"),
            "REX (€)": ratios.get("rex"),
            "EBE proxy (€)": ratios.get("ebe_proxy"),
            "Marge EBE (%)": (marge * 100) if marge is not None else None,
            "Résultat net (€)": ratios.get("resultat_net"),
            "Capitaux propres (€)": ratios.get("capitaux_propres"),
            "Total bilan (€)": ratios.get("total_bilan"),
            "Dette nette (€)": ratios.get("dette_nette"),
            "Gearing (dette/EBE)": ratios.get("gearing"),
            "Autonomie (%)": (autonomie * 100) if autonomie is not None else None,
            "Valorisation proxy (€)": ratios.get("valo_proxy"),
            "Bilan": c.get("bilan_status"),
            "Pappers": f"https://www.pappers.fr/entreprise/{c['siren']}",
        }

        if c.get("bilan_status") != "OK":
            rows_pas_de_bilan.append(base_row)
            continue

        ebe = ratios.get("ebe_proxy")
        marge = ratios.get("marge_ebe")
        gearing = ratios.get("gearing")
        valo = ratios.get("valo_proxy")

        # Données partielles
        if ebe is None or ratios.get("ca") is None:
            base_row["Motif"] = "EBE ou CA non extractibles"
            rows_finance_partielle.append(base_row)
            continue
        if ebe <= 0:
            base_row["Motif"] = f"EBE négatif ou nul ({ebe:,.0f} €)"
            rows_rej_finance.append(base_row)
            continue

        motifs_rejet = []
        if marge is not None and marge < EBE_MARGE_MIN:
            motifs_rejet.append(f"marge EBE {marge:.1%} < {EBE_MARGE_MIN:.0%}")
        if gearing is not None and gearing > GEARING_MAX:
            motifs_rejet.append(f"gearing {gearing:.1f}× > {GEARING_MAX}×")
        if valo is not None and valo > VALO_MAX:
            motifs_rejet.append(f"valo proxy {valo/1e6:.2f} M€ > {VALO_MAX/1e6:.1f} M€")

        if motifs_rejet:
            base_row["Motif"] = " ; ".join(motifs_rejet)
            rows_rej_finance.append(base_row)
        else:
            rows_retenues.append(base_row)

    # 5. Export Excel
    print(f"[5/5] Export Excel échantillon 3")
    import pandas as pd
    from src.export import _polish

    df_ret = pd.DataFrame(rows_retenues)
    if not df_ret.empty:
        df_ret = df_ret.sort_values("Valorisation proxy (€)", ascending=False)
    df_rej = pd.DataFrame(rows_rej_finance)
    df_part = pd.DataFrame(rows_finance_partielle)
    df_pas_bilan = pd.DataFrame(rows_pas_de_bilan)

    synthese = pd.DataFrame([
        ("Date échantillon 3", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("Source données financières", "API INPI RNE — bilansSaisis (liasses 2050/2052)"),
        ("Population échantillon 1", len(sp1_retenues)),
        ("Cibles échantillon 2 (âge ≥ 55 + indépendance)", len(sp2_cibles)),
        ("Cibles échantillon 3 retenues (finance OK)", len(rows_retenues)),
        ("Rejetées sur critères financiers", len(rows_rej_finance)),
        ("Données financières partielles", len(rows_finance_partielle)),
        ("Pas de bilan disponible (confidentialité ou non déposé)", len(rows_pas_de_bilan)),
        ("", ""),
        ("Critères financiers appliqués", ""),
        ("Marge EBE minimum", f"{EBE_MARGE_MIN:.0%}"),
        ("Gearing (dette nette / EBE) maximum", f"{GEARING_MAX}×"),
        ("Valorisation proxy maximum", f"{VALO_MAX:,.0f} €"),
        ("Multiple EBE pour proxy valo", "4× — décote illiquidité 20 %"),
    ], columns=["Indicateur", "Valeur"])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as w:
        synthese.to_excel(w, sheet_name="Synthèse échantillon 3", index=False)
        df_ret.to_excel(w, sheet_name="Cibles retenues", index=False)
        df_rej.to_excel(w, sheet_name="Rejetées (critères financiers)", index=False)
        df_part.to_excel(w, sheet_name="Données partielles", index=False)
        df_pas_bilan.to_excel(w, sheet_name="Bilans non publics", index=False)

    _polish(OUTPUT_XLSX)

    print(f"\n✓ Échantillon 3 livré : {OUTPUT_XLSX}")
    print(f"  → {len(rows_retenues)} cibles avec EBE+ / marge ≥ 8% / gearing ≤ 2.5× / valo ≤ 2 M€")
    print(f"  → {len(rows_pas_de_bilan)} cibles à qualifier manuellement (bilans en confidentialité)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

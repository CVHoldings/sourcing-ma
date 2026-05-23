"""CLI principale du module Sourcing M&A."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

from src.api_gouv import APIGouvClient, codes_tranche_effectif
from src.filtres import appliquer_filtres_tranche1
from src.export import ecrire_excel


def normalize_naf(naf: str) -> str:
    """46.69B / 4669B / 46.69b → 46.69B"""
    n = naf.replace(".", "").upper()
    if len(n) == 5:
        return f"{n[:2]}.{n[2:]}"
    return naf.upper()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sourcing M&A — screening d'entreprises françaises non cotées",
    )
    p.add_argument("--naf", required=True, help="Code NAF (ex. 46.69B ou 4669B)")
    p.add_argument("--geo", default="france", help="france (seul périmètre supporté en échantillon 1)")
    p.add_argument("--effectif-min", type=int, default=10)
    p.add_argument("--effectif-max", type=int, default=49)
    p.add_argument("--anciennete-min", type=int, default=25,
                   help="Ancienneté minimale en années (proxy âge dirigeant — échantillon 1)")
    p.add_argument("--exiger-dirigeant-pp", action="store_true", default=True,
                   help="N'inclure que les entreprises dirigées par une personne physique")
    p.add_argument("--output", required=True, help="Dossier de sortie")
    p.add_argument("--config", default="config/criteres_defaut.yaml")
    p.add_argument("--no-cache", action="store_true", help="Force le re-pull API")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(__file__).parent

    with open(project_root / args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    naf = normalize_naf(args.naf)
    tranches = codes_tranche_effectif(args.effectif_min, args.effectif_max)

    print(f"[1/4] Pull API gouv — NAF {naf}, tranches effectif {','.join(tranches)}")
    cache_dir = project_root / "data" / "cache_api"
    cache_key = f"NAF-{naf.replace('.','')}_eff-{args.effectif_min}-{args.effectif_max}"
    if args.no_cache and (cache_dir / f"{cache_key}.json").exists():
        (cache_dir / f"{cache_key}.json").unlink()

    client = APIGouvClient(
        cache_dir=cache_dir,
        per_page=config["api"]["recherche_entreprises"]["per_page"],
        pause_sec=config["api"]["recherche_entreprises"]["rate_limit_pause_sec"],
    )
    params = {
        "activite_principale": naf,
        "etat_administratif": "A",
        "tranche_effectif_salarie": ",".join(tranches),
    }
    entreprises = client.search(params, cache_key=cache_key)
    print(f"      → {len(entreprises)} entreprises actives récupérées")

    print(f"[2/4] Filtres tranche 1 — ancienneté ≥ {args.anciennete_min} ans, "
          f"dirigeant PP, indépendance")
    indep = config["screening"]["independance"]
    retenues, rejetees = appliquer_filtres_tranche1(
        entreprises,
        anciennete_min=args.anciennete_min,
        exclure_filiales_groupe=indep["exclure_filiales_groupe"],
        tolerer_patrimoniales=indep["tolerer_holdings_patrimoniales"],
        exiger_dirigeant_pp=args.exiger_dirigeant_pp,
    )
    print(f"      → {len(retenues)} retenues / {len(rejetees)} rejetées "
          f"({100*len(retenues)/max(1,len(entreprises)):.1f} %)")

    print("[3/4] Export Excel")
    xlsx = output_dir / "screening_resultats.xlsx"
    parametres = {
        "naf": naf,
        "geo": args.geo,
        "effectif_min": args.effectif_min,
        "effectif_max": args.effectif_max,
        "anciennete_min": args.anciennete_min,
        "exiger_dirigeant_pp": args.exiger_dirigeant_pp,
        "exclure_filiales_groupe": indep["exclure_filiales_groupe"],
        "tolerer_patrimoniales": indep["tolerer_holdings_patrimoniales"],
        "date_run": datetime.now().isoformat(timespec="seconds"),
    }
    ecrire_excel(retenues, rejetees, parametres, xlsx)
    print(f"      → {xlsx}")

    print("[4/4] Audit trail")
    (output_dir / "parametres.yaml").write_text(
        yaml.dump(parametres, allow_unicode=True), encoding="utf-8")
    (output_dir / "raw_results.json").write_text(
        json.dumps(entreprises, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nDone — {len(retenues)} cibles dans {xlsx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

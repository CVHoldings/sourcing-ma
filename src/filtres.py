"""Filtres tranche 1 : indépendance capitalistique + ancienneté + dirigeant PP.

Note importante (cf. CLAUDE.md, section Limites) :
L'API publique `recherche-entreprises.api.gouv.fr` n'expose PAS la date de naissance
des dirigeants. Le filtre âge dirigeant ne peut donc être appliqué automatiquement
sans un token INPI RNE (échantillon 2). En échantillon 1, on utilise un PROXY :
  (ancienneté entreprise ≥ X ans) ET (dirigeant principal = personne physique)
Hypothèse : sur un négoce industriel établi depuis 25+ ans dirigé en direct par
une personne physique, la probabilité que le dirigeant soit ≥ 55 ans est forte.
La validation finale d'âge se fait manuellement via les liens annuaire-entreprises.
"""
from __future__ import annotations

from datetime import date, datetime

PM_GROUPE_HINTS = (
    "GROUPE", "HOLDING", "PARTICIPATIONS", "INVEST", "INVESTISSEMENT",
    "CAPITAL", "PARTNERS", "VENTURES", "FONDS", "FUND", "EQUITY",
    "FINANCE", "FINANCIERE",
)
PM_PATRIMONIAL_HINTS = ("SCI ", "SCEA ", "FAMILIALE", "FAMILY", "PATRIMOINE")

QUALITES_PRINCIPALES_PP = (
    "PRESIDENT", "PRÉSIDENT", "GERANT", "GÉRANT",
    "DIRECTEUR GENERAL", "DIRECTEUR GÉNÉRAL",
)
QUALITES_CAC = ("COMMISSAIRE AUX COMPTES",)


def parse_date_iso(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def anciennete_annees(date_creation: str | None, ref: date | None = None) -> int | None:
    d = parse_date_iso(date_creation)
    if d is None:
        return None
    ref = ref or date.today()
    return ref.year - d.year - (1 if (ref.month, ref.day) < (d.month, d.day) else 0)


def is_pp(d: dict) -> bool:
    return d.get("type_dirigeant") == "personne physique"


def is_pm(d: dict) -> bool:
    return d.get("type_dirigeant") == "personne morale"


def is_cac(d: dict) -> bool:
    qualite = (d.get("qualite") or "").upper()
    return any(q in qualite for q in QUALITES_CAC)


def is_dirigeant_principal(d: dict) -> bool:
    """Hors CAC, et qualité parmi les fonctions de direction effective."""
    if is_cac(d):
        return False
    qualite = (d.get("qualite") or "").upper()
    return any(q in qualite for q in QUALITES_PRINCIPALES_PP)


def dirigeants_principaux(entreprise: dict) -> list[dict]:
    return [d for d in (entreprise.get("dirigeants") or []) if is_dirigeant_principal(d)]


def a_dirigeant_pp_principal(entreprise: dict) -> bool:
    return any(is_pp(d) for d in dirigeants_principaux(entreprise))


def est_pm_patrimoniale(pm_denom: str, patronymes: set[str]) -> bool:
    denom = pm_denom.upper()
    if any(h in denom for h in PM_PATRIMONIAL_HINTS):
        return True
    for p in patronymes:
        if len(p) >= 3 and p.upper() in denom:
            return True
    return False


def est_pm_groupe(pm_denom: str) -> bool:
    return any(h in pm_denom.upper() for h in PM_GROUPE_HINTS)


def diagnostic_independance(
    entreprise: dict, tolerer_patrimoniales: bool = True
) -> tuple[bool, str]:
    """Indépendance évaluée sur les seuls dirigeants principaux (hors CAC)."""
    principaux = dirigeants_principaux(entreprise)
    pm_principaux = [d for d in principaux if is_pm(d)]
    if not pm_principaux:
        return True, "aucune PM en direction effective"

    pp_principaux = [d for d in principaux if is_pp(d)]
    patronymes = {(d.get("nom") or "").strip() for d in pp_principaux}

    groupes, patrimoniales, autres = [], [], []
    for p in pm_principaux:
        denom = p.get("denomination") or ""
        if est_pm_patrimoniale(denom, patronymes):
            patrimoniales.append(denom)
        elif est_pm_groupe(denom):
            groupes.append(denom)
        else:
            autres.append(denom)

    if groupes:
        return False, f"PM de groupe en direction: {', '.join(groupes)}"
    if autres and not tolerer_patrimoniales:
        return False, f"PM dirigeante non patrimoniale: {', '.join(autres)}"
    if autres:
        return False, f"PM dirigeante à investiguer: {', '.join(autres)}"
    return True, f"PM patrimoniale(s) tolérée(s): {', '.join(patrimoniales)}"


def appliquer_filtres_tranche1(
    entreprises: list[dict],
    anciennete_min: int = 25,
    exclure_filiales_groupe: bool = True,
    tolerer_patrimoniales: bool = True,
    exiger_dirigeant_pp: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Tranche 1 sans date de naissance (proxy : ancienneté + dirigeant PP)."""
    retenues, rejetees = [], []
    for e in entreprises:
        if e.get("etat_administratif") != "A":
            rejetees.append({**e, "_rejet_motif": "entreprise inactive"})
            continue

        anc = anciennete_annees(e.get("date_creation"))
        if anc is None:
            rejetees.append({**e, "_rejet_motif": "date de création inconnue"})
            continue
        if anc < anciennete_min:
            rejetees.append({
                **e, "_anciennete": anc,
                "_rejet_motif": f"entreprise trop jeune ({anc} ans < {anciennete_min})",
            })
            continue

        if exiger_dirigeant_pp and not a_dirigeant_pp_principal(e):
            principaux = dirigeants_principaux(e)
            qualites = "; ".join(
                f"{d.get('qualite')} ({d.get('type_dirigeant')})" for d in principaux
            ) or "aucun dirigeant principal identifié"
            rejetees.append({
                **e, "_anciennete": anc,
                "_rejet_motif": f"aucun dirigeant PP principal — {qualites}",
            })
            continue

        if exclure_filiales_groupe:
            ok, motif = diagnostic_independance(e, tolerer_patrimoniales)
            if not ok:
                rejetees.append({
                    **e, "_anciennete": anc, "_rejet_motif": motif,
                })
                continue
            retenues.append({
                **e, "_anciennete": anc,
                "_independance_motif": motif,
            })
        else:
            retenues.append({**e, "_anciennete": anc})

    return retenues, rejetees

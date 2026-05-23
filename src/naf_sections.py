"""Référentiel des 21 sections NAF rev. 2 (codes A à U) — INSEE.

Utilisé pour la recherche par activité au niveau le plus large.
L'API recherche-entreprises accepte le paramètre `section_activite_principale`.
"""
from __future__ import annotations

NAF_SECTIONS = {
    "A": "Agriculture, sylviculture et pêche",
    "B": "Industries extractives",
    "C": "Industrie manufacturière",
    "D": "Production et distribution d'électricité, de gaz, de vapeur et d'air conditionné",
    "E": "Production et distribution d'eau ; assainissement, gestion des déchets et dépollution",
    "F": "Construction",
    "G": "Commerce ; réparation d'automobiles et de motocycles",
    "H": "Transports et entreposage",
    "I": "Hébergement et restauration",
    "J": "Information et communication",
    "K": "Activités financières et d'assurance",
    "L": "Activités immobilières",
    "M": "Activités spécialisées, scientifiques et techniques",
    "N": "Activités de services administratifs et de soutien",
    "O": "Administration publique",
    "P": "Enseignement",
    "Q": "Santé humaine et action sociale",
    "R": "Arts, spectacles et activités récréatives",
    "S": "Autres activités de services",
    "T": "Activités des ménages en tant qu'employeurs",
    "U": "Activités extra-territoriales",
}


def extract_objet_social(company: dict) -> str:
    """Extrait l'objet social depuis les données INPI.

    Note : l'API INPI RNE n'expose pas systématiquement l'objet social complet sur
    son endpoint `/companies/<siren>` — il est dans les actes déposés (statuts).
    Cette fonction tente plusieurs chemins JSON connus. Renvoie chaîne vide si
    introuvable.
    """
    formality = company.get("formality") or {}
    content = formality.get("content") or {}
    for pm_key in ("personneMorale", "personnePhysique", "exploitation"):
        pm = content.get(pm_key) or {}
        # Chemins testés (variantes selon type d'entreprise)
        for path in [
            ("identite", "entreprise", "objet"),
            ("identite", "entreprise", "objetSocial"),
            ("descriptionEntreprise", "objet"),
            ("descriptionEntreprise", "descriptionObjet"),
            ("identite", "objet"),
        ]:
            obj = pm
            for key in path:
                obj = (obj or {}).get(key) if isinstance(obj, dict) else None
            if isinstance(obj, str) and len(obj) > 5:
                return obj
    return ""


def filter_par_objet_social(cibles: list[dict], mots_cles: str) -> list[dict]:
    """Filtre tolérant sur l'objet social.

    Stratégie :
    - Si tous les mots-clés sont présents dans l'objet → retenue (match)
    - Si objet social non disponible (INPI ne renvoie pas le champ) → retenue avec flag "inconnu"
    - Si objet social présent mais pas de match → exclue

    Ce comportement permissif évite d'exclure massivement les cibles dont l'API INPI
    n'expose pas l'objet (cas fréquent — il faut souvent télécharger les statuts).
    """
    if not mots_cles or not mots_cles.strip():
        return cibles
    mots = [m.lower().strip() for m in mots_cles.split() if m.strip()]
    if not mots:
        return cibles
    retenues = []
    for c in cibles:
        company = c.get("company_inpi") or {}
        objet = extract_objet_social(company).lower()
        if not objet:
            c["objet_social_match"] = "objet inconnu (INPI)"
            retenues.append(c)
            continue
        if all(m in objet for m in mots):
            c["objet_social_match"] = "match"
            retenues.append(c)
    return retenues

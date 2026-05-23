# CLAUDE.md — Projet Sourcing M&A

Module de screening d'entreprises françaises non cotées pour identifier des cibles d'acquisition (transmission / build-up). Paramétrable par code NAF + critères financiers + critères dirigeant + critères capitalistiques.

## Mission

Cas pilote : NAF 4669B (commerce de gros, équipements industriels divers), France, valorisation cible ≤ 2 M€, dirigeant ≥ 55 ans, EBE/CA ≥ 8 %, Dette nette / EBE ≤ 2,5×, indépendance capitalistique (aucun BE personne morale > 25 %).

Cas suivants : autres NAF — le module est conçu pour être réutilisé en changeant uniquement la config YAML.

## Architecture

```
Sourcing M&A/
├── CLAUDE.md                            # ce fichier
├── README.md                            # quick start utilisateur (à générer)
├── requirements.txt                     # dépendances Python
├── .venv/                               # environnement virtuel local
├── config/
│   └── criteres_defaut.yaml             # filtres par défaut, surchargeables en CLI
├── src/
│   ├── __init__.py
│   ├── api_gouv.py                      # client recherche-entreprises.api.gouv.fr (gratuit)
│   ├── inpi.py                          # client data.inpi.fr (comptes annuels — échantillon 2)
│   ├── filtres.py                       # logique de filtrage paramétrée (âge, indép., rentab., dette)
│   ├── valorisation.py                  # proxy valo (multiple × EBE) + décotes
│   ├── ranking.py                       # score composite des cibles (échantillon 2)
│   └── export.py                        # génération Excel ranked
├── data/
│   ├── cache_api/                       # cache JSON par run (évite de retaper l'API)
│   └── cache_inpi/                      # cache des comptes annuels téléchargés
├── runs/
│   └── <YYYY-MM-DD>_NAF-<XXXX>/
│       ├── parametres.yaml              # paramètres exacts du run
│       ├── log.md                       # journal exécution
│       ├── raw_results.json             # données brutes API
│       ├── filtres.json                 # audit trail des filtres appliqués
│       └── screening_resultats.xlsx     # livrable final
├── templates/                           # templates Excel/HTML
└── cli.py                               # entrée CLI principale
```

## Patterns réutilisés depuis les skills

Obligation CLAUDE.md workspace : avant tout artefact, parcours exhaustif des SKILL.md pertinents.

| Skill | Patterns réutilisés |
|---|---|
| `evaluation-entreprise` | Méthode des praticiens, multiple TPE-PME négoce industriel = 4-5× EBE, décote illiquidité 20 % par défaut, fourchette de valeur, TRI LBU 25-30 % comme référence build-up |
| `analyse-financiere` | SIG → EBE = VA - Charges Pers - IT, ratios gearing/couverture/cap. remb., scoring Conan-Holder (bonus), benchmarks Banque de France FIBEN, retraitements analytiques (crédit-bail) |
| `data-extraction-finance` | Schéma pivot avec hash SHA-256 + traçabilité, stack Python (pandas/lxml/openpyxl), pattern catégorisation hiérarchique avec niveau de confiance, dossier `templates/`, contrôle de cohérence avant export |
| `dashboard-finance` | Cadrage 5 questions, palette neutre + accent ciblé, tokens CSS finance, KPI tile avec sparkline + variance, tableaux Tufte denses pour shortlist, titres conclusifs et non thématiques |
| `gouvernance-societe` | Définition légale du bénéficiaire effectif (>25 % capital OU DV) = critère exact d'indépendance, data.inpi.fr/rbe = source RBE, ~30 % PME en confidentialité comptes = comportement attendu |

## Sources de données

| Donnée | Source | Statut |
|---|---|---|
| Liste entreprises par NAF + effectif + géo | `recherche-entreprises.api.gouv.fr` | ✅ Gratuit, sans auth, bulk |
| Dirigeants + date de naissance | Idem | ✅ Présent dans réponse JSON |
| Personnes morales associées (proxy groupe) | Idem | ✅ Présent dans dirigeants |
| Comptes annuels (CA, EBE, dette) | `data.inpi.fr` | ⏳ Échantillon 2, nécessite parsing |
| Bénéficiaires effectifs détaillés (%) | INPI authentifié / annuaire-entreprises | ⏳ Shortlist uniquement |

## Méthodologie de screening (4 tranches)

**Tranche 0 — population brute** (API gouv) : NAF + géo + tranche effectif → ~500-1500 entreprises

**Tranche 1 — filtres techniques** (sans accès aux comptes) :
- Âge dirigeant : ≥ X ans (paramétrable)
- Indépendance : aucune personne morale dans la liste des dirigeants/associés actifs
- Activité réelle : entreprise active (etat_administratif = "A")
- → réduction estimée à 25-35 % de la population brute (150-500 cibles)

**Tranche 2 — filtres financiers** (avec comptes annuels INPI) :
- EBE / CA ≥ seuil rentabilité
- Dette nette / EBE ≤ seuil endettement
- Valorisation proxy (4-5× EBE) ≤ plafond
- → réduction estimée à 30-50 cibles

**Tranche 3 — qualification manuelle** (shortlist 30 fiches) :
- Lecture des comptes complets
- Vérification BE détaillés (data.inpi.fr/rbe)
- Recherche presse + LinkedIn dirigeant
- Présence sur le terrain (site, RH, clients connus)
- → top 10-15 cibles à contacter

## Format livrable Excel

**Onglet 1 — Top 50 cibles** : ranking par score composite (rentabilité × indépendance × âge × valo)
**Onglet 2 — Détail par cible** : 1 ligne / colonne par cible avec tous les indicateurs
**Onglet 3 — Filtres appliqués** : paramètres exacts du run, traçabilité
**Onglet 4 — Cibles rejetées** : audit trail des rejets (utile pour comprendre)
**Onglet 5 — Glossaire** : définition des métriques

## Limites connues

- ~30 % des PME déposent leurs comptes en confidentialité (légal, art. L.232-25). Pour ces cibles, EBE et dette sont en "à qualifier manuellement".
- API gouv n'expose pas tous les BE (uniquement les dirigeants nommés et certaines PM associées). Indépendance fine = vérification manuelle sur les top cibles.
- Pour les holdings patrimoniales du dirigeant (cas légitime, ex. SCI familiale) : l'algo doit les distinguer des holdings de groupe. Heuristique : SCI à raison sociale incluant le patronyme du dirigeant = patrimonial, sinon = à investiguer.
- Multiple 4× EBE est un proxy de screening, pas une évaluation. Une vraie valo passe par DCF + multiples comparables sectoriels + décotes/primes (cf. skill `evaluation-entreprise`).

## Workflow type

```bash
# Activer venv
source .venv/bin/activate

# Lancer screening pilote
python cli.py \
  --naf 4669B \
  --geo france \
  --effectif-min 10 --effectif-max 49 \
  --age-dirigeant-min 55 \
  --ebe-marge-min 0.08 \
  --gearing-max 2.5 \
  --valo-max 2_000_000 \
  --output runs/2026-05-22_NAF-4669B/

# Ouvrir le livrable
open runs/2026-05-22_NAF-4669B/screening_resultats.xlsx
```

## Roadmap

- **Échantillon 1** (en cours) — API gouv + filtres tranche 1 (âge + indép.) + Excel basique → ~300 cibles short-listées sans données financières
- **Échantillon 2** — Enrichissement INPI (comptes annuels) + ranking financier complet → ~30 cibles qualifiées avec valo
- **Échantillon 3** — UI Streamlit (formulaire + lancement + visu interactive + DL Excel) → outil clé en main réutilisable
- **Échantillon 4 (optionnel)** — Enrichissement BE détaillés + intégration LinkedIn / presse (signaux faibles transmission) + cartographie géographique

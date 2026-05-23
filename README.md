# Sourcing M&A

Module de screening d'entreprises françaises non cotées pour identifier des cibles d'acquisition (transmission ou build-up). Pipeline en trois échantillons : (1) indépendance capitalistique et ancienneté, (2) âge du dirigeant via API INPI RNE, (3) qualification financière via les bilans saisis INPI.

## Sources de données

- `recherche-entreprises.api.gouv.fr` — annuaire des entreprises (gratuit, sans auth)
- API INPI RNE — `registre-national-entreprises.inpi.fr` (gratuit avec compte data.inpi.fr)
- Bilans saisis INPI — liasses fiscales 2050-2053 (régime normal) et 2033 (régime simplifié)

## Lancement local

```bash
.venv/bin/streamlit run app.py
```

Identifiants INPI dans `.env` (jamais committé).

## Déploiement Streamlit Cloud

Identifiants INPI dans `Manage app → Settings → Secrets` :

```toml
INPI_API_USER = "votre.email@example.com"
INPI_API_PASS = "votre_mot_de_passe_inpi"
```

## Architecture

- `app.py` — UI Streamlit
- `src/api_gouv.py` — client API gouv (liste brute par NAF)
- `src/inpi.py` — client INPI RNE + parsing bilans
- `src/filtres.py` — logique des trois échantillons
- `src/geo_fr.py` — référentiel régions / départements
- `src/valorisation.py` — proxy de valorisation et ratios

## Doctrine

Application des principes IBCS (Hichert), Stephen Few (Information Dashboard Design) et Edward Tufte (data-ink ratio) pour la mise en forme. Calculs financiers conformes au PCG (autonomie = CP / Total bilan, EBE = REX + DAP exploitation totales − reprises).

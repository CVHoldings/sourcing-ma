"""UI Streamlit pour Sourcing M&A — screening de cibles d'acquisition.

Design : application des principes IBCS (Hichert) / Stephen Few / Edward Tufte
extraits du skill `dashboard-finance`. Palette navy + accent rouge. Typographie
Inter. Pas d'émoticônes, hiérarchie typographique claire, tableaux denses.

Lancement : streamlit run app.py
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.api_gouv import APIGouvClient, codes_tranche_effectif
from src.filtres import appliquer_filtres_tranche1
from src.geo_fr import REGIONS_FR, DEPARTEMENTS_FR, filter_entreprises_par_geo
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
    generate_comptes_excel_bytes,
)


PROJECT_ROOT = Path(__file__).parent

# Streamlit ignore les page_icon en chemin local sur certaines versions (sert son
# propre favicon par défaut). On charge l'image en PIL et on la passe en objet —
# ça force Streamlit à inliner notre favicon.
def _load_favicon():
    try:
        from PIL import Image
        path = PROJECT_ROOT / "assets" / "favicon-v3.png"
        if path.exists():
            return Image.open(path)
    except Exception:
        pass
    return None

st.set_page_config(
    page_title="Sourcing M&A",
    page_icon=_load_favicon(),
    layout="wide",
    initial_sidebar_state="expanded",
)


# ───────────────────────── Doctrine visuelle (skill dashboard-finance) ─────────────────────────
# Palette : neutre par défaut, accent unique pour l'information critique.
# Tokens issus du skill `dashboard-finance` (Hichert/Few/Tufte).
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
  --ink-900: #0A0E1A;
  --ink-700: #2A3142;
  --ink-500: #5C6478;
  --ink-300: #9BA3B5;
  --ink-100: #E5E8EE;
  --paper:   #FAFAF7;
  --brand:   #0B2545;
  --accent:  #C8102E;
  --positive:#0F7A3A;
}

html, body, [class*="css"], [data-testid="stAppViewContainer"] *,
.stMarkdown, .stMetric, .stTabs, .stButton, .stTextInput, .stNumberInput, .stSelectbox {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
}

[data-testid="stAppViewContainer"] > .main {
  background-color: var(--paper);
}

[data-testid="stHeader"] {
  background: transparent;
}

h1, .stMarkdown h1 {
  color: var(--ink-900);
  font-weight: 700;
  letter-spacing: -0.025em;
  font-size: 2rem;
  margin-bottom: 0.25rem;
}

h2, .stMarkdown h2 {
  color: var(--ink-700);
  font-weight: 600;
  font-size: 1.25rem;
  letter-spacing: -0.01em;
  border-bottom: 1px solid var(--ink-100);
  padding-bottom: 0.4rem;
  margin-top: 2rem;
}

h3, .stMarkdown h3 {
  color: var(--ink-700);
  font-weight: 600;
  font-size: 1.05rem;
  letter-spacing: 0;
}

.stMarkdown p, .stMarkdown li {
  color: var(--ink-700);
  line-height: 1.55;
}

[data-testid="stMetric"] {
  background-color: white;
  border: 1px solid var(--ink-100);
  border-radius: 4px;
  padding: 0.9rem 1rem 0.7rem 1rem;
}

[data-testid="stMetricLabel"] p {
  color: var(--ink-500);
  font-size: 0.72rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.25rem;
}

[data-testid="stMetricValue"] {
  color: var(--brand);
  font-size: 1.55rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1;
  letter-spacing: -0.01em;
}

[data-testid="stMetricDelta"] {
  font-size: 0.72rem;
  color: var(--ink-500);
}

.stTabs [data-baseweb="tab-list"] {
  gap: 0;
  border-bottom: 1px solid var(--ink-100);
  margin-bottom: 1rem;
}

.stTabs [data-baseweb="tab"] {
  color: var(--ink-500);
  background-color: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  padding: 8px 18px;
  font-weight: 500;
  font-size: 0.9rem;
}

/* Espace au-dessus du contenu de chaque onglet pour éviter chevauchement avec l'expander */
.stTabs [data-baseweb="tab-panel"] {
  padding-top: 1rem;
}

/* L'expander de téléchargement comptes annuels — un peu plus de marge */
[data-testid="stExpander"] {
  margin: 0.6rem 0 1.2rem 0;
}

[data-testid="stExpander"] summary {
  font-weight: 500;
  color: var(--brand);
  font-size: 0.92rem;
}

.stTabs [aria-selected="true"] {
  color: var(--brand);
  border-bottom-color: var(--brand);
  font-weight: 600;
  background-color: transparent;
}

.stButton > button {
  background-color: var(--brand);
  color: white;
  border: none;
  border-radius: 3px;
  font-weight: 500;
  letter-spacing: 0.01em;
  padding: 0.55rem 1.2rem;
  transition: background-color 0.12s ease;
}

.stButton > button:hover {
  background-color: var(--ink-700);
  color: white;
}

.stButton > button[kind="primary"] {
  background-color: var(--brand);
}

.stDownloadButton > button {
  background-color: white;
  color: var(--brand);
  border: 1px solid var(--brand);
  font-weight: 500;
}

.stDownloadButton > button:hover {
  background-color: var(--brand);
  color: white;
}

[data-testid="stSidebar"] {
  background-color: #F4F4F0;
  border-right: 1px solid var(--ink-100);
}

[data-testid="stSidebar"] h1 {
  color: var(--brand);
  font-size: 1.15rem;
  font-weight: 700;
  letter-spacing: -0.01em;
  margin-bottom: 0;
}

[data-testid="stSidebar"] .stRadio label {
  font-weight: 500;
  color: var(--ink-700);
}

.stProgress > div > div > div > div {
  background-color: var(--brand);
}

.stDataFrame {
  border: 1px solid var(--ink-100);
  border-radius: 4px;
}

[data-testid="stDataFrameResizable"] {
  font-variant-numeric: tabular-nums;
  font-feature-settings: "tnum" 1;
}

.stTextInput input, .stNumberInput input, .stSelectbox > div > div {
  font-family: 'Inter', sans-serif;
  border-color: var(--ink-100);
}

div[data-baseweb="slider"] [role="slider"] {
  background-color: var(--brand);
}

hr {
  border: none;
  border-top: 1px solid var(--ink-100);
  margin: 1.5rem 0;
}

.subtitle {
  color: var(--ink-500);
  font-size: 0.95rem;
  margin-top: -0.4rem;
  margin-bottom: 1.4rem;
  font-weight: 400;
}

.caption-meta {
  color: var(--ink-500);
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-bottom: 0.25rem;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────── Helpers ───────────────────────────────
def normalize_naf(naf: str) -> str:
    n = naf.replace(".", "").upper()
    return f"{n[:2]}.{n[2:]}" if len(n) == 5 else naf.upper()


# ─────────────────────────────── Sidebar ───────────────────────────────
with st.sidebar:
    st.markdown(
        '<div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.2rem;">'
        '<div style="width:34px;height:34px;background:#0B2545;border-radius:6px;'
        'display:flex;align-items:center;justify-content:center;color:#FAFAF7;'
        'font-weight:700;font-size:0.95rem;letter-spacing:-0.02em;'
        'font-family:Inter,sans-serif;">M&A</div>'
        '<div><h1 style="margin:0;color:#0B2545;font-size:1.1rem;font-weight:700;">'
        'Sourcing M&A</h1>'
        '<div style="color:#5C6478;font-size:0.72rem;font-weight:500;letter-spacing:0.05em;'
        'text-transform:uppercase;">Screening cibles non cotées</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<hr style="margin:0.8rem 0 0.6rem 0;">', unsafe_allow_html=True)
    page = st.radio(
        "Navigation",
        ["Lancer un screening", "Consulter un run", "À propos"],
        label_visibility="collapsed",
    )
    st.markdown('<hr style="margin:1rem 0 0.6rem 0;">', unsafe_allow_html=True)
    st.markdown(
        '<div class="caption-meta">Sources de données</div>'
        '<div style="color:#5C6478;font-size:0.82rem;line-height:1.55;">'
        '· API gouv (recherche-entreprises)<br>'
        '· API INPI RNE (auth)<br>'
        '· Bilans saisis INPI (liasses 2050-2053)</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────── Page : lancer un screening ───────────────────────────
if page == "Lancer un screening":
    st.markdown("# Lancer un screening")
    st.markdown(
        '<div class="subtitle">Trois échantillons successifs : entité indépendante & ancienne, '
        'dirigeant senior, qualification financière. Renseignez les critères ci-dessous.</div>',
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("### Secteur et géographie")

        mode_recherche = st.radio(
            "Recherche par",
            ["Code NAF", "Mots-clés d'activité"],
            horizontal=True,
            help="Code NAF si vous connaissez la nomenclature INSEE. Sinon mots-clés "
                 "(comme sur Pappers) : la recherche porte sur la dénomination "
                 "et le libellé d'activité.",
        )
        naf = ""
        keywords = ""
        if mode_recherche == "Code NAF":
            naf = st.text_input(
                "Code NAF",
                value="4669B",
                help="Format 4669B ou 46.69B (exemple : 4669B = commerce de gros, "
                     "équipements industriels divers).",
            )
        else:
            keywords = st.text_input(
                "Mots-clés d'activité",
                value="",
                placeholder="ex. maintenance industrielle, transmission mécanique...",
                help="Cherche dans la dénomination des entreprises et le libellé "
                     "de leur activité. Plusieurs mots-clés possibles.",
            )

        effectif_min = st.number_input("Effectif minimum (salariés)", min_value=0, max_value=10000, value=10)
        effectif_max = st.number_input("Effectif maximum (salariés)", min_value=0, max_value=10000, value=49)

        mode_geo = st.radio(
            "Périmètre géographique",
            ["France entière", "Une ou plusieurs régions", "Un ou plusieurs départements"],
            help="Le filtre s'applique sur le département/région du siège social.",
        )
        regions_choisies: list[str] = []
        departements_choisis: list[str] = []
        if mode_geo == "Une ou plusieurs régions":
            regions_options = list(REGIONS_FR.keys())
            regions_choisies = st.multiselect(
                "Régions",
                regions_options,
                format_func=lambda c: f"{REGIONS_FR[c]} ({c})",
                default=[],
                help="Ex. Provence-Alpes-Côte d'Azur, Auvergne-Rhône-Alpes...",
            )
        elif mode_geo == "Un ou plusieurs départements":
            deps_options = list(DEPARTEMENTS_FR.keys())
            departements_choisis = st.multiselect(
                "Départements",
                deps_options,
                format_func=lambda c: DEPARTEMENTS_FR[c],
                default=[],
                help="Ex. Bouches-du-Rhône (13), Paris (75), Rhône (69)...",
            )

    with col2:
        st.markdown("### Dirigeant et indépendance")
        age_min = st.slider(
            "Âge minimum du dirigeant le plus jeune (ans)",
            40, 80, 55,
            help="Filtre sur la date de naissance officielle au RNE",
        )
        anciennete_min = st.slider(
            "Ancienneté minimum de l'entreprise (ans)",
            0, 80, 40,
            help="Pré-filtre échantillon 1 — réduit la population avant lookup INPI",
        )
        exclure_groupe = st.checkbox("Exclure les filiales de groupe", value=True)
        tolerer_patrimoniales = st.checkbox(
            "Tolérer holdings patrimoniales du dirigeant (SCI familiale, etc.)",
            value=True,
        )

    with col3:
        st.markdown("### Critères financiers")
        # FIX : valeurs en entiers (%) puis division par 100 dans le code,
        # sinon format "%.0f%%" affichait 0% sur une valeur 0.08.
        marge_min_pct = st.slider(
            "Marge EBE minimum (EBE / CA, en %)",
            0, 30, 8, 1,
            format="%d %%",
        )
        gearing_max = st.slider(
            "Gearing maximum (dette nette / EBE)",
            0.0, 10.0, 2.5, 0.5,
            format="%.1f×",
        )
        valo_max = st.number_input(
            "Valorisation maximum (€)",
            min_value=100_000, max_value=50_000_000,
            value=2_000_000, step=100_000,
        )
        multiple_ebe = st.slider(
            "Multiple EBE pour proxy valo",
            2.0, 8.0, 4.0, 0.5,
            format="%.1f×",
        )

    marge_min = marge_min_pct / 100.0  # conversion % → fraction

    st.markdown('<hr>', unsafe_allow_html=True)
    # Nom du run par défaut : varie selon mode de recherche
    if mode_recherche == "Code NAF" and naf:
        suffixe = f"NAF-{naf.replace('.', '').upper()}"
    elif keywords:
        suffixe = "kw-" + "_".join(keywords.split()[:3]).lower()[:40]
    else:
        suffixe = "screening"
    nom_run = st.text_input(
        "Nom du run (dossier de sortie)",
        value=f"{date.today().isoformat()}_{suffixe}",
    )
    lance = st.button("Lancer le screening", type="primary", use_container_width=True)

    if lance:
        # Validation : au moins un critère de recherche renseigné
        if mode_recherche == "Code NAF" and not naf.strip():
            st.error("Veuillez saisir un code NAF.")
            st.stop()
        if mode_recherche == "Mots-clés d'activité" and not keywords.strip():
            st.error("Veuillez saisir au moins un mot-clé d'activité.")
            st.stop()

        run_dir = PROJECT_ROOT / "runs" / nom_run
        run_dir.mkdir(parents=True, exist_ok=True)
        naf_norm = normalize_naf(naf) if naf else ""
        tranches = codes_tranche_effectif(int(effectif_min), int(effectif_max))

        prog = st.progress(0, "Préparation")
        log_area = st.empty()

        # Échantillon 1 — pull API gouv (NAF ou mots-clés)
        critere_label = f"NAF {naf_norm}" if naf_norm else f"mots-clés « {keywords} »"
        log_area.markdown(
            f'<div style="color:#5C6478;font-size:0.85rem;">Étape 1 sur 3 — '
            f'récupération API gouv ({critere_label}, effectif {effectif_min}-{effectif_max}).</div>',
            unsafe_allow_html=True,
        )
        client_gouv = APIGouvClient(
            cache_dir=PROJECT_ROOT / "data" / "cache_api", per_page=25, pause_sec=0.2,
        )
        params = {
            "etat_administratif": "A",
            "tranche_effectif_salarie": ",".join(tranches),
        }
        if naf_norm:
            params["activite_principale"] = naf_norm
        if keywords.strip():
            params["q"] = keywords.strip()
        # Cache_key : varie selon NAF ou mots-clés
        cache_key_base = (f"NAF-{naf_norm.replace('.','')}"
                          if naf_norm else f"kw-{keywords.strip().replace(' ','_').lower()[:40]}")
        cache_key = f"{cache_key_base}_eff-{effectif_min}-{effectif_max}"
        entreprises = client_gouv.search(params, cache_key=cache_key)

        # Filtre géographique post-API (côté Python)
        if mode_geo == "Une ou plusieurs régions" and regions_choisies:
            entreprises = filter_entreprises_par_geo(entreprises, "regions", regions=regions_choisies)
        elif mode_geo == "Un ou plusieurs départements" and departements_choisis:
            entreprises = filter_entreprises_par_geo(entreprises, "departements", departements=departements_choisis)

        prog.progress(20)

        # Filtres échantillon 1
        sp1_retenues, sp1_rejetees = appliquer_filtres_tranche1(
            entreprises, anciennete_min=int(anciennete_min),
            exclure_filiales_groupe=exclure_groupe,
            tolerer_patrimoniales=tolerer_patrimoniales,
            exiger_dirigeant_pp=True,
        )
        log_area.markdown(
            f'<div style="color:#5C6478;font-size:0.85rem;">Étape 2 sur 3 — '
            f'lookup INPI sur {len(sp1_retenues)} entités, filtre âge dirigeant ≥ {age_min} ans.</div>',
            unsafe_allow_html=True,
        )
        prog.progress(30)

        try:
            user, pw = load_credentials_from_env()
        except RuntimeError as e:
            st.error(str(e))
            st.stop()

        client_inpi = INPIClient(user, pw, cache_dir=PROJECT_ROOT / "data" / "cache_inpi")
        try:
            client_inpi._login()
        except Exception as e:
            st.error(f"Échec d'authentification INPI : {e}")
            st.stop()

        # Échantillon 2 + 3 simultanés (lookup company + attachments)
        ech3_cibles = []
        ech3_rejetees = []
        N = len(sp1_retenues)
        for i, e in enumerate(sp1_retenues):
            prog.progress(
                30 + int(60 * i / max(N, 1)),
                f"Lookup INPI {i+1}/{N} — {e.get('nom_complet', '')[:48]}",
            )
            siren = e["siren"]
            company = client_inpi.get_company(siren, use_cache=True)
            if company.get("_not_found") or company.get("_error"):
                continue
            pp = extract_dirigeants_pp(company, exclure_cac=True)
            pm = extract_dirigeants_pm(company, exclure_cac=True)
            if any(p.get("is_direction") for p in pm):
                continue
            age, plus_jeune = age_dirigeant_min(company)
            if age is None or age < age_min:
                continue
            att = get_attachments(client_inpi, siren, use_cache=True)
            bilan_doc = latest_bilan(att)
            bilan_data = extract_bilan(bilan_doc) if bilan_doc else {}
            ratios = (
                compute_ratios(bilan_data, multiple_ebe=float(multiple_ebe))
                if bilan_data else {}
            )
            ech3_cibles.append({
                "meta": e, "age": age, "plus_jeune": plus_jeune,
                "dirigeants_pp": pp, "bilan": bilan_data, "ratios": ratios,
                "bilan_disponible": bool(bilan_doc),
                "attachments": att,
            })

        prog.progress(90)
        log_area.markdown(
            f'<div style="color:#5C6478;font-size:0.85rem;">Étape 3 sur 3 — '
            f'qualification financière sur {len(ech3_cibles)} cibles.</div>',
            unsafe_allow_html=True,
        )

        # Filtre finance (échantillon 3)
        retenues, rejetees_fin, pas_bilan = [], [], []
        for c in ech3_cibles:
            r = c["ratios"]
            ebe = r.get("ebe_proxy")
            marge = r.get("marge_ebe")
            gearing = r.get("gearing")
            valo = r.get("valo_proxy")

            if not c["bilan_disponible"]:
                pas_bilan.append(c)
                continue
            if ebe is None or r.get("ca") is None:
                rejetees_fin.append({**c, "motif": "EBE ou CA non extractibles"})
                continue
            if ebe <= 0:
                rejetees_fin.append({**c, "motif": f"EBE négatif ({ebe:,.0f} €)"})
                continue

            motifs = []
            if marge is not None and marge < marge_min:
                motifs.append(f"marge {marge:.1%} < {marge_min:.0%}")
            if gearing is not None and gearing > gearing_max:
                motifs.append(f"gearing {gearing:.1f}× > {gearing_max}×")
            if valo is not None and valo > valo_max:
                motifs.append(f"valo {valo/1e6:.2f} M€ > {valo_max/1e6:.1f} M€")
            if motifs:
                rejetees_fin.append({**c, "motif": " ; ".join(motifs)})
            else:
                retenues.append(c)

        prog.progress(100)
        log_area.empty()

        # Persister en session pour les boutons de téléchargement ultérieurs
        st.session_state["last_run"] = {
            "retenues": retenues,
            "pas_bilan": pas_bilan,
            "rejetees_fin": rejetees_fin,
            "stats": {
                "brute": len(entreprises),
                "ech1": len(sp1_retenues),
                "ech2": len(ech3_cibles),
                "ech3": len(retenues),
            },
            "params": {
                "naf": naf_norm, "age_min": age_min, "anciennete_min": anciennete_min,
                "marge_min": marge_min, "gearing_max": gearing_max,
                "valo_max": valo_max, "multiple_ebe": multiple_ebe,
                "run_dir": str(run_dir),
            },
        }

    # ─── Affichage des résultats (si run en session) ───
    if "last_run" in st.session_state:
        lr = st.session_state["last_run"]
        retenues = lr["retenues"]
        pas_bilan = lr["pas_bilan"]
        rejetees_fin = lr["rejetees_fin"]
        stats = lr["stats"]

        st.markdown('<hr>', unsafe_allow_html=True)
        st.markdown("## Résultats")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Population brute", f"{stats['brute']:,}".replace(",", " "))
        c2.metric("Échantillon 1", f"{stats['ech1']:,}".replace(",", " "),
                  help="Indépendance + ancienneté + dirigeant personne physique")
        c3.metric("Échantillon 2", f"{stats['ech2']:,}".replace(",", " "),
                  help="Âge dirigeant ≥ seuil (donnée INPI RNE)")
        c4.metric("Échantillon 3", f"{stats['ech3']:,}".replace(",", " "),
                  help="Marge EBE, gearing et valorisation conformes")
        ratio_final = (100 * stats["ech3"] / max(stats["brute"], 1))
        c5.metric("Taux de retenue final", f"{ratio_final:.2f} %")

        tab1, tab2, tab3 = st.tabs([
            f"Cibles retenues ({len(retenues)})",
            f"Bilans non publics ({len(pas_bilan)})",
            f"Rejetées sur critères financiers ({len(rejetees_fin)})",
        ])

        with tab1:
            if not retenues:
                st.info("Aucune cible ne passe l'ensemble des filtres. "
                        "Élargissez les critères financiers ou l'âge minimum.")
            else:
                # Bandeau téléchargement comptes — compact, au-dessus du tableau pour bonne visibilité
                with st.expander("Télécharger les comptes annuels d'une cible (Excel détaillé)", expanded=False):
                    st.markdown(
                        '<div style="color:#5C6478;font-size:0.85rem;margin-bottom:0.6rem;">'
                        "Le fichier contient : identité, compte de résultat, bilan actif et passif, "
                        "synthèse pluri-exercices, ratios et codes liasse détaillés.</div>",
                        unsafe_allow_html=True,
                    )
                    col_sel_top, col_btn_top = st.columns([3, 1])
                    with col_sel_top:
                        options_top = {f"{c['meta'].get('nom_complet')} ({c['meta']['siren']})": c
                                       for c in retenues}
                        choix_top = st.selectbox(
                            "Cible", list(options_top.keys()),
                            label_visibility="collapsed",
                            key="dl_select_top",
                        )
                    with col_btn_top:
                        if choix_top:
                            c_dl = options_top[choix_top]
                            siren_dl = c_dl["meta"]["siren"]
                            denom_dl = c_dl["meta"].get("nom_complet", "")
                            excel_bytes_top = generate_comptes_excel_bytes(
                                siren_dl, c_dl["attachments"], denomination=denom_dl,
                            )
                            st.download_button(
                                "Télécharger Excel",
                                data=excel_bytes_top,
                                file_name=f"Comptes_{siren_dl}_{denom_dl.replace(' ', '_')[:40]}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                                key="dl_btn_top",
                            )
                st.markdown('<hr style="margin:0.8rem 0 1rem 0;">', unsafe_allow_html=True)

                rows = []
                for c in retenues:
                    meta, r = c["meta"], c["ratios"]
                    siege = meta.get("siege") or {}
                    siren = meta["siren"]
                    marge = r.get("marge_ebe")
                    rows.append({
                        "SIREN": siren,
                        "Dénomination": meta.get("nom_complet"),
                        "Commune": siege.get("libelle_commune"),
                        "Dpt": siege.get("departement"),
                        "Âge dir.": c["age"],
                        "Dirigeant principal": c["plus_jeune"],
                        "Ancien.": meta.get("_anciennete"),
                        "CA": r.get("ca"),
                        "EBE": r.get("ebe_proxy"),
                        "Marge EBE": marge * 100 if marge is not None else None,
                        "Gearing": r.get("gearing"),
                        "Valo proxy": r.get("valo_proxy"),
                        "Pappers": f"https://www.pappers.fr/entreprise/{siren}",
                    })
                df = pd.DataFrame(rows).sort_values("Valo proxy", ascending=False, na_position="last")
                st.dataframe(
                    df, hide_index=True, use_container_width=True,
                    column_config={
                        "CA": st.column_config.NumberColumn("CA (€)", format="%d"),
                        "EBE": st.column_config.NumberColumn("EBE (€)", format="%d"),
                        "Marge EBE": st.column_config.NumberColumn(format="%.1f %%"),
                        "Gearing": st.column_config.NumberColumn(format="%.2f ×"),
                        "Valo proxy": st.column_config.NumberColumn("Valo proxy (€)", format="%d"),
                        "Pappers": st.column_config.LinkColumn(display_text="Ouvrir la fiche →"),
                    },
                )

                # ─── Carte interactive en dessous du tableau ───
                st.markdown('<hr style="margin:1.2rem 0 1rem 0;">', unsafe_allow_html=True)
                st.markdown("### Localisation géographique")
                st.markdown(
                    '<div style="color:#5C6478;font-size:0.85rem;margin-bottom:0.6rem;">'
                    "Carte interactive : faites glisser pour vous déplacer, molette pour zoomer/dézoomer, "
                    "survolez un point pour les détails.</div>",
                    unsafe_allow_html=True,
                )

                # Construction des points
                points = []
                for c in retenues + pas_bilan:
                    siege = (c["meta"].get("siege") or {})
                    lat, lon = siege.get("latitude"), siege.get("longitude")
                    if not lat or not lon:
                        continue
                    try:
                        latf, lonf = float(lat), float(lon)
                    except (TypeError, ValueError):
                        continue
                    ratios = c.get("ratios", {}) or {}
                    retenue = c in retenues
                    ca_v = ratios.get("ca")
                    valo_v = ratios.get("valo_proxy")
                    points.append({
                        "lat": latf,
                        "lon": lonf,
                        "Dénomination": c["meta"].get("nom_complet") or "?",
                        "Commune": siege.get("libelle_commune") or "",
                        "Département": siege.get("departement") or "",
                        "Âge dirigeant": c.get("age", "?"),
                        "CA": f"{int(ca_v):,} €".replace(",", " ") if ca_v else "—",
                        "Valorisation proxy": f"{int(valo_v):,} €".replace(",", " ") if valo_v else "—",
                        "Statut": "Cible retenue" if retenue else "Bilan non public",
                    })

                st.caption(f"{len(points)} cibles localisées sur la carte "
                           f"({len(retenues)} retenues + {len(pas_bilan)} à bilan non public)")

                if not points:
                    st.info("Aucune coordonnée GPS disponible pour les cibles de ce screening.")
                else:
                    try:
                        import plotly.express as px
                        df_map = pd.DataFrame(points)

                        # Centre + zoom adapté au périmètre
                        lat_mean = float(df_map["lat"].mean())
                        lon_mean = float(df_map["lon"].mean())
                        if mode_geo == "France entière":
                            zoom = 4.8
                        elif mode_geo == "Une ou plusieurs régions":
                            zoom = 6.5
                        else:
                            zoom = 8

                        fig = px.scatter_mapbox(
                            df_map,
                            lat="lat", lon="lon",
                            hover_name="Dénomination",
                            hover_data={
                                "lat": False, "lon": False,
                                "Commune": True, "Département": True,
                                "Âge dirigeant": True, "CA": True,
                                "Valorisation proxy": True, "Statut": True,
                            },
                            color="Statut",
                            color_discrete_map={
                                "Cible retenue": "#C8102E",      # rouge accent — mise en avant
                                "Bilan non public": "#9CA3AF",   # gris — secondaire
                            },
                            size_max=16,
                            zoom=zoom,
                            center={"lat": lat_mean, "lon": lon_mean},
                            mapbox_style="carto-positron",
                            height=620,
                        )
                        fig.update_traces(marker=dict(size=14))
                        fig.update_layout(
                            margin=dict(l=0, r=0, t=0, b=0),
                            legend=dict(
                                orientation="h", yanchor="top", y=-0.02,
                                xanchor="center", x=0.5,
                                bgcolor="rgba(255,255,255,0.8)",
                            ),
                            font=dict(family="Inter, sans-serif", size=12),
                            dragmode="pan",  # clic-glisser = pan par défaut
                            hoverlabel=dict(
                                bgcolor="#0B2545",
                                font_size=12,
                                font_family="Inter, sans-serif",
                                font_color="white",
                            ),
                        )
                        st.plotly_chart(
                            fig,
                            use_container_width=True,
                            config={
                                "scrollZoom": True,        # zoom avec la molette
                                "displayModeBar": True,    # barre d'outils visible (zoom, dézoom, recentrer, sauvegarder)
                                "displaylogo": False,
                                "modeBarButtonsToRemove": ["lasso2d", "select2d"],
                                "doubleClick": "reset",    # double-clic = recentrer
                            },
                        )
                    except Exception as e:
                        st.error(f"Erreur rendu carte plotly : {type(e).__name__} — {e}")
                        st.info("Repli sur la carte simple Streamlit.")
                        st.map(pd.DataFrame(points), zoom=5)

        with tab2:
            st.markdown(
                '<div class="subtitle">Cibles avec dirigeant senior et indépendantes, '
                'mais comptes en confidentialité (art. L.232-25 C. com.) ou non déposés. '
                "Qualification financière à conduire manuellement.</div>",
                unsafe_allow_html=True,
            )
            if pas_bilan:
                rows = []
                for c in pas_bilan:
                    meta = c["meta"]
                    siege = meta.get("siege") or {}
                    siren = meta["siren"]
                    rows.append({
                        "SIREN": siren,
                        "Dénomination": meta.get("nom_complet"),
                        "Commune": siege.get("libelle_commune"),
                        "Dpt": siege.get("departement"),
                        "Âge dir.": c["age"],
                        "Dirigeant principal": c["plus_jeune"],
                        "Ancien.": meta.get("_anciennete"),
                        "Pappers": f"https://www.pappers.fr/entreprise/{siren}",
                    })
                st.dataframe(
                    pd.DataFrame(rows), hide_index=True, use_container_width=True,
                    column_config={
                        "Pappers": st.column_config.LinkColumn(display_text="Ouvrir la fiche →"),
                    },
                )
            else:
                st.info("Aucune cible sans bilan public.")

        with tab3:
            if rejetees_fin:
                rows = []
                for c in rejetees_fin:
                    meta, r = c["meta"], c.get("ratios", {})
                    marge = r.get("marge_ebe")
                    rows.append({
                        "SIREN": meta["siren"],
                        "Dénomination": meta.get("nom_complet"),
                        "Motif": c.get("motif"),
                        "CA": r.get("ca"),
                        "EBE": r.get("ebe_proxy"),
                        "Marge EBE": marge * 100 if marge is not None else None,
                        "Valo proxy": r.get("valo_proxy"),
                    })
                st.dataframe(
                    pd.DataFrame(rows), hide_index=True, use_container_width=True,
                    column_config={
                        "CA": st.column_config.NumberColumn("CA (€)", format="%d"),
                        "EBE": st.column_config.NumberColumn("EBE (€)", format="%d"),
                        "Marge EBE": st.column_config.NumberColumn(format="%.1f %%"),
                        "Valo proxy": st.column_config.NumberColumn("Valo proxy (€)", format="%d"),
                    },
                )
            else:
                st.info("Aucune cible rejetée sur les critères financiers.")


# ─────────────────────────── Page : consulter un run ───────────────────────────
elif page == "Consulter un run":
    st.markdown("# Consulter un screening précédent")
    st.markdown(
        '<div class="subtitle">Sélectionnez un screening précédent et un échantillon '
        "pour relire les classeurs.</div>",
        unsafe_allow_html=True,
    )
    runs_dir = PROJECT_ROOT / "runs"
    runs = sorted([d.name for d in runs_dir.glob("*") if d.is_dir()], reverse=True)
    if not runs:
        st.info("Aucun screening précédent trouvé. Lancez d'abord un screening.")
    else:
        # Libellés humains pour les classeurs
        LIBELLES_CLASSEURS = {
            "screening_echantillon_1.xlsx": "Échantillon 1 — Indépendance et ancienneté",
            "screening_echantillon_2.xlsx": "Échantillon 2 — Âge du dirigeant",
            "screening_echantillon_3.xlsx": "Échantillon 3 — Qualification financière",
            "screening_resultats.xlsx": "Résultats (format historique)",
        }

        col_run, col_classeur = st.columns([1, 2])
        with col_run:
            st.markdown('<div class="caption-meta">Screening</div>', unsafe_allow_html=True)
            run = st.selectbox("Screening", runs, label_visibility="collapsed")

        run_path = runs_dir / run
        xlsx_files = sorted(run_path.glob("*.xlsx"))

        if not xlsx_files:
            st.warning("Aucun classeur Excel dans ce screening.")
        else:
            options_classeur = {
                LIBELLES_CLASSEURS.get(f.name, f.name): f for f in xlsx_files
            }
            with col_classeur:
                st.markdown('<div class="caption-meta">Échantillon à consulter</div>',
                            unsafe_allow_html=True)
                choix = st.selectbox("Échantillon", list(options_classeur.keys()),
                                     label_visibility="collapsed")
            xlsx = options_classeur[choix]

            st.markdown('<hr style="margin:1.2rem 0;">', unsafe_allow_html=True)
            try:
                sheets = pd.ExcelFile(xlsx).sheet_names
                tab_objs = st.tabs(sheets)
                for tab, sheet in zip(tab_objs, sheets):
                    with tab:
                        df = pd.read_excel(xlsx, sheet_name=sheet)
                        st.dataframe(df, hide_index=True, use_container_width=True)
            except Exception as e:
                st.error(f"Erreur de lecture {xlsx.name} : {e}")


# ─────────────────────────── Page : à propos ───────────────────────────
else:
    st.markdown("# À propos")
    st.markdown('<div class="subtitle">Module de screening d\'entreprises françaises non '
                "cotées en vue d'identifier des cibles d'acquisition (transmission ou build-up).</div>",
                unsafe_allow_html=True)

    st.markdown("## Pipeline en trois échantillons")
    st.markdown("""
1. **Échantillon 1 — Sélection brute.** Via `recherche-entreprises.api.gouv.fr` sur le code NAF, la
   tranche d'effectif et un filtre d'indépendance capitalistique fondé sur les dirigeants déclarés.
2. **Échantillon 2 — Filtre dirigeant.** Via l'API INPI RNE pour récupérer les dates de naissance et
   ne conserver que les entités dont le dirigeant le plus jeune est au-delà du seuil retenu.
3. **Échantillon 3 — Qualification financière.** À partir des bilans saisis INPI (liasses 2050-2053),
   calcul de l'EBE proxy, du gearing et de la valorisation proxy.
""")

    st.markdown("## Doctrine appliquée")
    st.markdown("""
- Skill `evaluation-entreprise` — méthode des praticiens, multiple TPE-PME 4×, décote d'illiquidité 20 %.
- Skill `analyse-financiere` — ratios de structure (gearing, autonomie), marge EBE, retraitements PCG.
- Skill `dashboard-finance` — palette neutre + accent unique, typographie hiérarchisée, principes IBCS / Few / Tufte.
- Skill `gouvernance-societe` — définition légale du bénéficiaire effectif > 25 %.
""")

    st.markdown("## Limites connues")
    st.markdown("""
- Environ trente pour cent des PME publient leurs comptes en confidentialité
  (article L.232-25 du Code de commerce) — qualification manuelle requise pour ces cibles.
- Le proxy de valorisation (4 × EBE × 0,8) est indicatif. Une valorisation engageante demande un
  DCF, des multiples de transactions sectorielles et une due diligence complète.
- L'indépendance capitalistique est évaluée sur les dirigeants déclarés au RNE et non sur les
  pourcentages de capital fins (à compléter par les bénéficiaires effectifs détaillés).
""")

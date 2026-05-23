"""Client INPI RNE — auth SSO + lookup SIREN + comptes annuels.

Endpoints utilisés :
  POST https://registre-national-entreprises.inpi.fr/api/sso/login
       Body: {"username": ..., "password": ...}
       Réponse: {"token": "<JWT>"} ou Authorization header

  GET  https://registre-national-entreprises.inpi.fr/api/companies/<SIREN>
       Header: Authorization: Bearer <JWT>
       Réponse: identité légale + dirigeants (avec date_naissance) + BE

  GET  https://registre-national-entreprises.inpi.fr/api/companies/<SIREN>/attachments
       Liste des comptes annuels et actes déposés (avec liens téléchargement)

Note : la structure exacte de la réponse peut varier — le client est défensif et
expose les données brutes en parallèle d'extracteurs typés.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL = "https://registre-national-entreprises.inpi.fr/api"

# Cache JWT au niveau module — évite les re-logins entre sessions Streamlit
# JWT INPI valide ~1h ; on cache 50 min pour garder une marge de sécurité.
_JWT_CACHE: dict[tuple, tuple] = {}  # {(user, pw_hash): (token, timestamp)}
_JWT_TTL_SECONDS = 3000  # 50 min

# Mapping des codes RNE roleEntreprise → libellé + catégorisation.
# Mapping établi après audit empirique sur ~307 entreprises NAF 4669B :
#   - codes 71 et 72 majoritairement personnes morales (= cabinets d'audit) = CAC
#   - code 70 exclusivement personne physique = dirigeant cosignataire (pas CAC)
#   - code 73 (« Président ») majoritaire en pratique sur SAS
# Mapping empirique (audit 2026-05-22 sur 563 entreprises). La nomenclature
# officielle INPI roleEntreprise n'est pas publiée dans la doc API v4.0.
# Cas particuliers à surveiller :
# - Code 30 (285 occurrences PP) : libellé incertain, observé sur SARL alors que
#   l'« associé indéfiniment responsable » légal n'existe que pour SNC/SCS.
# - Code 99 (39 occurrences) classé "autre" (catégorie neutre) car contient des
#   cabinets MAZARS et PWC Audit (= CAC), donc pas systématiquement de direction.
ROLES_RNE = {
    "11": ("Représentant permanent", "direction"),
    "30": ("Associé / pouvoir d'engager", "direction"),
    "40": ("Liquidateur", "liquidation"),
    "50": ("Représentant légal", "direction"),
    "51": ("Gérant", "direction"),
    "52": ("Président", "direction"),
    "53": ("Directeur général", "direction"),
    "54": ("Directeur général délégué", "direction"),
    "60": ("Administrateur", "direction"),
    "61": ("Président du conseil d'administration", "direction"),
    "62": ("Membre du conseil de surveillance", "direction"),
    "63": ("Président du conseil de surveillance", "direction"),
    "64": ("Membre du conseil de surveillance (variante)", "direction"),
    "65": ("Membre du directoire", "direction"),
    "66": ("Président du directoire", "direction"),
    "67": ("Vice-président du directoire", "direction"),
    "70": ("Cosignataire / pouvoir d'engagement", "direction"),
    "71": ("Commissaire aux comptes titulaire", "cac"),
    "72": ("Commissaire aux comptes suppléant", "cac"),
    "73": ("Président", "direction"),
    "74": ("Personne ayant le pouvoir d'engager", "direction"),
    "99": ("Autre rôle non identifié", "autre"),
    "110": ("Représentant légal (PM dirigeante)", "direction"),
}


def role_libelle(code: str | int | None) -> str:
    if code is None:
        return ""
    info = ROLES_RNE.get(str(code))
    return info[0] if info else f"code RNE {code}"


def is_role_direction(code: str | int | None) -> bool:
    info = ROLES_RNE.get(str(code))
    return info is not None and info[1] == "direction"


def is_role_cac(code: str | int | None) -> bool:
    info = ROLES_RNE.get(str(code))
    return info is not None and info[1] == "cac"


class INPIClient:
    def __init__(
        self,
        username: str,
        password: str,
        cache_dir: Path,
        pause_sec: float = 0.3,
    ):
        self.username = username
        self.password = password
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pause_sec = pause_sec
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self._token: str | None = None
        self._token_obtained_at: float = 0.0

    def _login(self) -> str:
        """POST /sso/login, renvoie le JWT.

        Utilise un cache module-level (TTL 50 min) pour éviter les re-logins
        entre instanciations (utile sur Streamlit Cloud où une session peut
        reprendre rapidement après un redémarrage).
        """
        import hashlib
        cache_key = (self.username,
                     hashlib.sha256(self.password.encode()).hexdigest()[:16])
        now = time.time()
        cached = _JWT_CACHE.get(cache_key)
        if cached and (now - cached[1]) < _JWT_TTL_SECONDS:
            token = cached[0]
            self._token = token
            self._token_obtained_at = cached[1]
            self.session.headers["Authorization"] = f"Bearer {token}"
            return token

        url = f"{BASE_URL}/sso/login"
        r = self.session.post(
            url,
            json={"username": self.username, "password": self.password},
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        if not r.ok:
            raise RuntimeError(
                f"Auth INPI échouée [{r.status_code}]: {r.text[:200]}"
            )
        data = r.json()
        token = data.get("token") or r.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token:
            raise RuntimeError(f"JWT introuvable dans la réponse: {data}")
        self._token = token
        self._token_obtained_at = now
        self.session.headers["Authorization"] = f"Bearer {token}"
        _JWT_CACHE[cache_key] = (token, now)
        return token

    def ensure_token(self, max_age_sec: int = 3000) -> None:
        """Rafraîchit le token s'il a > 50 min (durée JWT typique INPI = 1h)."""
        if self._token is None or (time.time() - self._token_obtained_at) > max_age_sec:
            self._login()

    def get_company(self, siren: str, use_cache: bool = True) -> dict:
        """GET /companies/<siren> avec cache local."""
        siren = str(siren).strip()
        cache_path = self.cache_dir / f"company_{siren}.json"
        if use_cache and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        self.ensure_token()
        url = f"{BASE_URL}/companies/{siren}"
        r = self.session.get(url, timeout=30)

        if r.status_code == 401:
            self._login()
            r = self.session.get(url, timeout=30)

        if r.status_code == 404:
            data = {"_not_found": True, "siren": siren}
        elif not r.ok:
            raise RuntimeError(f"Erreur GET {siren} [{r.status_code}]: {r.text[:200]}")
        else:
            data = r.json()

        cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(self.pause_sec)
        return data

    def enrich_batch(self, sirens: list[str], use_cache: bool = True) -> dict[str, dict]:
        """Lookup séquentiel. Garde pour compatibilité ; préférer enrich_batch_parallel."""
        results: dict[str, dict] = {}
        for s in tqdm(sirens, desc="INPI lookup", unit="SIREN"):
            try:
                results[s] = self.get_company(s, use_cache=use_cache)
            except Exception as e:
                results[s] = {"_error": str(e), "siren": s}
        return results

    def enrich_batch_parallel(
        self,
        sirens: list[str],
        use_cache: bool = True,
        max_workers: int = 10,
        progress_callback=None,
    ) -> dict[str, dict]:
        """Lookup INPI parallèle via ThreadPoolExecutor.

        Performance : ~5-10× plus rapide que enrich_batch séquentiel sur des
        listes > 100 SIREN. requests.Session est thread-safe pour les GET.

        Args:
            sirens : liste de SIREN à interroger
            use_cache : utiliser le cache disque local si dispo
            max_workers : nombre de threads simultanés (10 = compromis vitesse/rate-limit)
            progress_callback : fonction (done, total, current_siren) appelée à chaque résultat

        Returns:
            dict {siren: data_or_error}
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # Authentification AVANT de paralléliser (le token est partagé entre threads)
        self.ensure_token()

        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_siren = {
                executor.submit(self.get_company, s, use_cache): s
                for s in sirens
            }
            done = 0
            for future in as_completed(future_to_siren):
                siren = future_to_siren[future]
                try:
                    results[siren] = future.result()
                except Exception as e:
                    results[siren] = {"_error": str(e), "siren": siren}
                done += 1
                if progress_callback:
                    progress_callback(done, len(sirens), siren)
        return results

    def fetch_attachments_parallel(
        self,
        sirens: list[str],
        use_cache: bool = True,
        max_workers: int = 10,
        progress_callback=None,
    ) -> dict[str, dict]:
        """Récupération parallèle des attachments (bilans saisis) pour une liste de SIREN."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        self.ensure_token()
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_siren = {
                executor.submit(get_attachments, self, s, use_cache): s
                for s in sirens
            }
            done = 0
            for future in as_completed(future_to_siren):
                siren = future_to_siren[future]
                try:
                    results[siren] = future.result()
                except Exception as e:
                    results[siren] = {"_error": str(e), "siren": siren,
                                      "actes": [], "bilans": [], "bilansSaisis": []}
                done += 1
                if progress_callback:
                    progress_callback(done, len(sirens), siren)
        return results


# Extracteurs typés (defensifs — la structure RNE peut varier selon la forme juridique)

def extract_dirigeants_pp(company: dict, exclure_cac: bool = True) -> list[dict]:
    """Liste des dirigeants personnes physiques avec date de naissance.

    Par défaut exclut les commissaires aux comptes (codes RNE 70/71).
    """
    out: list[dict] = []
    formality = company.get("formality") or {}
    content = formality.get("content") or {}
    for pm_key in ("personneMorale", "personnePhysique", "exploitation"):
        pm = content.get(pm_key) or {}
        composition = pm.get("composition") or {}
        pouvoirs = composition.get("pouvoirs") or []
        for p in pouvoirs:
            role_code = p.get("roleEntreprise")
            if exclure_cac and is_role_cac(role_code):
                continue
            individu = p.get("individu") or {}
            descr = individu.get("descriptionPersonne") or {}
            nom = descr.get("nom")
            prenoms = descr.get("prenoms")
            if not (nom or prenoms):
                continue
            out.append({
                "nom": nom,
                "prenoms": " ".join(prenoms) if isinstance(prenoms, list) else prenoms,
                "date_naissance": descr.get("dateDeNaissance"),
                "lieu_naissance": descr.get("lieuDeNaissance"),
                "nationalite": descr.get("nationalite"),
                "role_code": str(role_code) if role_code is not None else None,
                "role_libelle": role_libelle(role_code),
                "is_direction": is_role_direction(role_code),
            })
    return out


def extract_dirigeants_pm(company: dict, exclure_cac: bool = True) -> list[dict]:
    """Liste des dirigeants personnes morales (= signal de filiale potentielle)."""
    out: list[dict] = []
    formality = company.get("formality") or {}
    content = formality.get("content") or {}
    for pm_key in ("personneMorale", "personnePhysique", "exploitation"):
        pm = content.get(pm_key) or {}
        composition = pm.get("composition") or {}
        pouvoirs = composition.get("pouvoirs") or []
        for p in pouvoirs:
            role_code = p.get("roleEntreprise")
            if exclure_cac and is_role_cac(role_code):
                continue
            entreprise = p.get("entreprise") or {}
            denom = entreprise.get("denomination")
            if not denom:
                continue
            out.append({
                "denomination": denom,
                "siren": entreprise.get("siren"),
                "forme_juridique": entreprise.get("formeJuridique"),
                "role_code": str(role_code) if role_code is not None else None,
                "role_libelle": role_libelle(role_code),
                "is_direction": is_role_direction(role_code),
            })
    return out


def get_attachments(client: "INPIClient", siren: str, use_cache: bool = True) -> dict:
    """GET /companies/<siren>/attachments — listes actes/bilans/bilansSaisis."""
    siren = str(siren).strip()
    cache_path = client.cache_dir / f"attachments_{siren}.json"
    if use_cache and cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    client.ensure_token()
    url = f"{BASE_URL}/companies/{siren}/attachments"
    r = client.session.get(url, timeout=30)
    if r.status_code == 401:
        client._login()
        r = client.session.get(url, timeout=30)
    if not r.ok:
        data = {"_error": f"HTTP {r.status_code}", "siren": siren,
                "actes": [], "bilans": [], "bilansSaisis": []}
    else:
        data = r.json()

    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    time.sleep(client.pause_sec)
    return data


# Mapping codes liasse fiscale CERFA officiels (vérifié 2026-05-22).
# Sources : CERFA 2050/2051/2052/2053-SD 2025 (régime normal), CERFA 2033-A/B-SD 2025 (régime simplifié).
#
# Pour chaque page, la valeur de l'exercice N est dans une colonne différente :
#   - Régime normal (codeTypeBilan='C') :
#     - Page 1 (Bilan actif 2050)       → m3 = NET N
#     - Page 2 (Bilan passif 2051)      → m1 = N
#     - Page 3 (Compte de résultat 2052)→ m3 = N
#     - Page 4 (Affectation 2053)       → m1 = N
#   - Régime simplifié (codeTypeBilan='S' ou 'K') :
#     - Page 1 (Bilan 2033-A)           → m3 = NET N
#     - Page 2 (Compte de résultat 2033-B) → m3 = N
LIASSE_PAGE_COL_N_NORMAL = {1: "m3", 2: "m1", 3: "m3", 4: "m1", 5: "m3"}
# Régime simplifié 2033 : page 1 = bilan (m3 = Net N), page 2 = CR (m1 = N, m3 vide)
LIASSE_PAGE_COL_N_SIMPLIFIE = {1: "m3", 2: "m1"}

# Mapping pour régime normal — codes vérifiés sur CERFA 2050-2053 officiels
LIASSE_NORMAL_CODES = {
    # Bilan actif (page 1)
    "BV": ("avances_acomptes_versees", 1),
    "BX": ("creances_clients", 1),
    "BZ": ("autres_creances", 1),
    "CD": ("VMP", 1),
    "CF": ("disponibilites", 1),
    "CO": ("total_actif", 1),
    # Bilan passif (page 2)
    "DA": ("capital_social", 2),
    "DI": ("resultat_exercice_bilan", 2),
    "DL": ("capitaux_propres", 2),
    "DS": ("emprunts_obligataires_convertibles", 2),
    "DU": ("emprunts_dettes_credit", 2),
    "DV": ("emprunts_dettes_diverses", 2),
    "DX": ("dettes_fournisseurs", 2),
    "EA": ("autres_dettes", 2),
    "EE": ("total_passif", 2),
    # Compte de résultat (page 3)
    "FA": ("ventes_marchandises_total", 3),
    "FG": ("production_services_total", 3),
    "FJ": ("ca_net", 3),
    "FO": ("subventions_exploitation", 3),
    "FP": ("reprises_amort_transferts", 3),
    "FQ": ("autres_produits_exploitation", 3),
    "FR": ("total_produits_exploitation", 3),
    "FS": ("achats_marchandises", 3),
    "FU": ("achats_matieres", 3),
    "FW": ("autres_achats_charges_externes", 3),
    "FX": ("impots_taxes", 3),
    "FY": ("salaires", 3),
    "FZ": ("charges_sociales", 3),
    "GA": ("dap_amortissements", 3),
    "GB": ("dot_prov_immo", 3),
    "GC": ("dot_prov_actif_circulant", 3),
    "GD": ("dot_prov_risques_charges", 3),
    "GE": ("autres_charges_exploitation", 3),
    "GG": ("rex", 3),
    "GW": ("rcai", 3),
    # Affectation du résultat (page 4)
    "HN": ("resultat_exercice", 4),
}

# Mapping pour régime simplifié 2033 — codes vérifiés sur CERFA 2033-A/B officiels
LIASSE_SIMPLIFIE_CODES = {
    # Bilan simplifié 2033-A (page 1)
    "028": ("immo_corporelles", 1),
    "044": ("total_immo_brut", 1),
    "060": ("creances_clients", 1),
    "068": ("autres_creances", 1),
    "080": ("VMP", 1),
    "084": ("disponibilites", 1),
    "092": ("CCA", 1),
    "096": ("total_actif_circulant", 1),
    "110": ("total_actif", 1),
    "120": ("capital_social", 1),
    "126": ("reserves_legales", 1),
    "134": ("report_a_nouveau", 1),
    "136": ("resultat_exercice_bilan", 1),
    "142": ("capitaux_propres", 1),
    "156": ("emprunts_credits", 1),
    "166": ("dettes_fournisseurs", 1),
    "172": ("dettes_fiscales_sociales", 1),
    "176": ("total_dettes", 1),
    "180": ("total_passif", 1),
    # Compte de résultat simplifié 2033-B (page 2)
    "210": ("ventes_marchandises_total", 2),
    "214": ("production_vendue_biens", 2),
    "218": ("production_services_total", 2),
    "224": ("production_immobilisee", 2),
    "230": ("autres_produits_exploitation", 2),
    "232": ("ca_net", 2),
    "234": ("achats_marchandises", 2),
    "236": ("variation_stocks_marchandises", 2),
    "238": ("achats_matieres", 2),
    "242": ("autres_achats_charges_externes", 2),
    "244": ("impots_taxes", 2),
    "250": ("salaires", 2),
    "252": ("charges_sociales", 2),
    "254": ("dap_amortissements", 2),
    "256": ("dot_prov", 2),
    "262": ("autres_charges_exploitation", 2),
    "264": ("total_charges_exploitation", 2),
    "270": ("rex", 2),
    "280": ("produits_financiers", 2),
    "294": ("charges_financieres", 2),
    "306": ("impot_societes", 2),
    "310": ("resultat_exercice", 2),
}


def _str_to_int(v: str | None) -> int | None:
    """'000000000007622' → 7622. Gère aussi les signes négatifs '-0000123'."""
    if not v:
        return None
    s = str(v).strip()
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("-").lstrip("0") or "0"
    try:
        return sign * int(s)
    except (ValueError, TypeError):
        return None


def extract_bilan(bilan_saisi_doc: dict) -> dict:
    """Extrait les codes liasse avec colonne adaptée à chaque page."""
    bs = bilan_saisi_doc.get("bilanSaisi") or {}
    bilan = bs.get("bilan") or {}
    identite = bilan.get("identite") or {}
    detail = bilan.get("detail") or {}

    # Index par (page, code) → dict des 4 colonnes
    by_page_code: dict[tuple[int, str], dict] = {}
    for page in detail.get("pages") or []:
        num = page.get("numero")
        for liasse in page.get("liasses") or []:
            code = liasse.get("code")
            if code is not None and num is not None:
                by_page_code[(num, code)] = liasse

    type_bilan = identite.get("codeTypeBilan", "C")
    if type_bilan == "S":
        codes_map = LIASSE_SIMPLIFIE_CODES
        page_col = LIASSE_PAGE_COL_N_SIMPLIFIE
    else:
        codes_map = LIASSE_NORMAL_CODES
        page_col = LIASSE_PAGE_COL_N_NORMAL

    extracted = {
        "siren": identite.get("siren"),
        "date_cloture": identite.get("dateClotureExercice"),
        "duree_exercice": identite.get("dureeExerciceN"),
        "code_type_bilan": type_bilan,
        "code_activite": identite.get("codeActivite"),
    }
    for code, (libelle, page) in codes_map.items():
        col = page_col.get(page, "m1")
        liasse_dict = by_page_code.get((page, code))
        extracted[libelle] = _str_to_int(liasse_dict.get(col)) if liasse_dict else None
    return extracted


# Statuts de confidentialité considérés comme publiquement exploitables.
# "Publication simplifiee" = bilan public mais allégé (entreprises < 6 M€ CA option L.232-25).
PUBLIC_CONFIDENTIALITY_STATUSES = ("Public", "Publication simplifiee")


def latest_bilan(attachments: dict) -> dict | None:
    """Retourne le bilanSaisi avec la date de clôture la plus récente.

    Inclut les bilans en publication simplifiée (publics mais allégés).
    """
    bilans = attachments.get("bilansSaisis") or []
    bilans = [b for b in bilans
              if b.get("confidentiality") in PUBLIC_CONFIDENTIALITY_STATUSES
              and not b.get("deleted")]
    if not bilans:
        return None
    bilans.sort(key=lambda b: (b.get("bilanSaisi", {}).get("bilan", {})
                               .get("identite", {}).get("dateClotureExercice", "")),
                reverse=True)
    return bilans[0]


def bilan_age_years(bilan: dict, today=None) -> int | None:
    """Âge du bilan en années depuis sa date de clôture (proxy obsolescence)."""
    from datetime import date as _date
    today = today or _date.today()
    dc = bilan.get("date_cloture") or ""
    try:
        annee = int(dc.split("-")[0])
        return today.year - annee
    except (ValueError, IndexError):
        return None


def compute_ratios(bilan: dict, multiple_ebe: float = 4.0, decote_illiq: float = 0.20) -> dict:
    """Calcule EBE proxy, dette nette, valo proxy + ratios principaux.

    EBE = REX + dotations exploitation (GA+GB+GC+GD) − reprises (FP)
    Autonomie = CP / Total passif (standard PCG, cf. analyse-financiere skill)
    Valo proxy = multiple × EBE × (1 − décote illiquidité)
    """
    type_bilan = bilan.get("code_type_bilan", "C")

    if type_bilan == "S":
        # Régime simplifié 2033 — CA reconstitué depuis composantes (210+214+218)
        # car le code 232 sur 2033-B = "Total produits d'exploitation", PAS le CA net seul.
        ca_composantes = sum(filter(None, [
            bilan.get("ventes_marchandises_total"),       # code 210
            bilan.get("production_vendue_biens"),         # code 214
            bilan.get("production_services_total"),       # code 218
        ])) or None
        # Si aucune composante détaillée, fallback sur code 232 (total produits expl, légère surestimation)
        ca = ca_composantes or bilan.get("ca_net")
        rex = bilan.get("rex")
        dap = sum(filter(None, [
            bilan.get("dap_amortissements"),
            bilan.get("dot_prov"),
        ])) or 0
        reprises = 0  # non décomposé en 2033
        cp = bilan.get("capitaux_propres")
        total_bilan = bilan.get("total_passif") or bilan.get("total_actif")
        emprunts = bilan.get("emprunts_credits")
        dispo = bilan.get("disponibilites") or bilan.get("VMP")
    else:
        # Régime normal 2050-2053 — CA = ca_net (FJ) UNIQUEMENT.
        # Pas de fallback sur FR (total produits expl) car FR inclut subventions +
        # reprises + autres produits → gonfle artificiellement le CA et dilue la marge.
        # Si FJ est absent, on retourne None et la cible passe en "données partielles".
        ca = bilan.get("ca_net")
        rex = bilan.get("rex")
        # DAP totales = amortissements + provisions immo + ac + r&c
        dap = sum(filter(None, [
            bilan.get("dap_amortissements"),
            bilan.get("dot_prov_immo"),
            bilan.get("dot_prov_actif_circulant"),
            bilan.get("dot_prov_risques_charges"),
        ])) or 0
        reprises = bilan.get("reprises_amort_transferts") or 0
        cp = bilan.get("capitaux_propres")
        total_bilan = bilan.get("total_passif") or bilan.get("total_actif")
        emprunts = sum(filter(None, [
            bilan.get("emprunts_obligataires_convertibles"),
            bilan.get("emprunts_dettes_credit"),
            bilan.get("emprunts_dettes_diverses"),
        ])) or None
        dispo = bilan.get("disponibilites") or bilan.get("VMP")

    # EBE proxy = REX + DAP − reprises
    ebe = (rex + dap - reprises) if rex is not None else None

    dette_nette = None
    if emprunts is not None:
        dette_nette = emprunts - (dispo or 0)

    marge_ebe = (ebe / ca) if (ebe is not None and ca and ca > 0) else None
    gearing = (dette_nette / ebe) if (dette_nette is not None and ebe and ebe > 0) else None
    valo_proxy = (ebe * multiple_ebe * (1 - decote_illiq)) if (ebe and ebe > 0) else None
    # Autonomie = CP / Total bilan (passif) — standard PCG
    autonomie = (cp / total_bilan) if cp and total_bilan and total_bilan > 0 else None

    return {
        "ca": ca,
        "rex": rex,
        "ebe_proxy": ebe,
        "resultat_net": bilan.get("resultat_exercice"),
        "capitaux_propres": cp,
        "total_bilan": total_bilan,
        "emprunts_total": emprunts,
        "disponibilites": dispo,
        "dette_nette": dette_nette,
        "marge_ebe": marge_ebe,
        "gearing": gearing,
        "autonomie": autonomie,
        "valo_proxy": valo_proxy,
    }


# Structure du Compte de Résultat (CERFA 2052) — pour affichage Excel
# Tuple (libellé extracted dans le bilan, "Libellé Excel", niveau)
# niveau : "section" (en-tête de bloc), "detail", "subtotal", "total", "key" (ligne mise en avant)
COMPTE_RESULTAT_LIGNES = [
    (None, "Produits d'exploitation", "section"),
    ("ventes_marchandises_total", "Ventes de marchandises", "detail"),
    ("production_services_total", "Production vendue de services", "detail"),
    ("ca_net", "Chiffre d'affaires NET", "subtotal"),
    ("subventions_exploitation", "Subventions d'exploitation", "detail"),
    ("reprises_amort_transferts", "Reprises sur amortissements et provisions", "detail"),
    ("autres_produits_exploitation", "Autres produits", "detail"),
    ("total_produits_exploitation", "TOTAL Produits d'exploitation", "total"),
    (None, "", "blank"),
    (None, "Charges d'exploitation", "section"),
    ("achats_marchandises", "Achats de marchandises", "detail"),
    ("achats_matieres", "Achats de matières premières et approvisionnements", "detail"),
    ("autres_achats_charges_externes", "Autres achats et charges externes", "detail"),
    ("impots_taxes", "Impôts, taxes et versements assimilés", "detail"),
    ("salaires", "Salaires et traitements", "detail"),
    ("charges_sociales", "Charges sociales", "detail"),
    ("dap_amortissements", "Dotations aux amortissements sur immobilisations", "detail"),
    ("dot_prov_immo", "Dotations aux provisions sur immobilisations", "detail"),
    ("dot_prov_actif_circulant", "Dotations aux provisions sur actif circulant", "detail"),
    ("dot_prov_risques_charges", "Dotations aux provisions pour risques et charges", "detail"),
    ("autres_charges_exploitation", "Autres charges", "detail"),
    (None, "", "blank"),
    ("rex", "RÉSULTAT D'EXPLOITATION", "key"),
    (None, "", "blank"),
    ("rcai", "Résultat courant avant impôts (RCAI)", "key"),
    ("resultat_exercice", "RÉSULTAT NET DE L'EXERCICE", "key"),
]

# Structure du Bilan Actif (CERFA 2050)
BILAN_ACTIF_LIGNES = [
    (None, "Actif immobilisé", "section"),
    ("immo_corporelles", "Immobilisations corporelles", "detail"),
    (None, "", "blank"),
    (None, "Actif circulant", "section"),
    ("avances_acomptes_versees", "Avances et acomptes versés sur commandes", "detail"),
    ("creances_clients", "Créances clients et comptes rattachés", "detail"),
    ("autres_creances", "Autres créances", "detail"),
    ("VMP", "Valeurs mobilières de placement", "detail"),
    ("disponibilites", "Disponibilités", "detail"),
    (None, "", "blank"),
    ("total_actif", "TOTAL GÉNÉRAL ACTIF", "key"),
]

# Structure du Bilan Passif (CERFA 2051)
BILAN_PASSIF_LIGNES = [
    (None, "Capitaux propres", "section"),
    ("capital_social", "Capital social", "detail"),
    ("resultat_exercice_bilan", "Résultat de l'exercice", "detail"),
    ("capitaux_propres", "TOTAL Capitaux propres", "total"),
    (None, "", "blank"),
    (None, "Dettes financières", "section"),
    ("emprunts_obligataires_convertibles", "Emprunts obligataires convertibles", "detail"),
    ("emprunts_dettes_credit", "Emprunts auprès des établissements de crédit", "detail"),
    ("emprunts_dettes_diverses", "Emprunts et dettes financières diverses", "detail"),
    (None, "", "blank"),
    (None, "Dettes d'exploitation", "section"),
    ("dettes_fournisseurs", "Dettes fournisseurs et comptes rattachés", "detail"),
    ("autres_dettes", "Autres dettes", "detail"),
    (None, "", "blank"),
    ("total_passif", "TOTAL GÉNÉRAL PASSIF", "key"),
]


def _format_finance_workbook(path) -> None:
    """Mise en forme professionnelle : police, palette navy, format monétaire €,
    sections gras + fond gris, totaux en gras, lignes clés mises en avant."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    BRAND = "0B2545"
    INK_900 = "0A0E1A"
    INK_700 = "2A3142"
    INK_100 = "E5E8EE"
    PAPER = "FAFAF7"

    FORMAT_EURO = '#,##0 "€";[Red]-#,##0 "€";"—"'
    FORMAT_PCT = '0.0 "%"'
    FORMAT_RATIO = '0.00 "×"'

    wb = load_workbook(path)
    thin = Side(border_style="thin", color=INK_100)

    for ws in wb.worksheets:
        if ws.max_row < 1:
            continue
        # Header (ligne 1)
        for cell in ws[1]:
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color=BRAND, end_color=BRAND, fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin)
        ws.row_dimensions[1].height = 30

        # Largeur des colonnes
        for col_idx, col in enumerate(ws.columns, start=1):
            values = [c.value for c in col if c.value is not None]
            length = max((len(str(v)) for v in values), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(length + 3, 14), 50)

        ws.freeze_panes = "B2"

    wb.save(path)


def _apply_structure_styles(ws, lignes_struct, col_data_start: int) -> None:
    """Pour un onglet de comptes (CR, bilan), applique les styles selon le niveau."""
    from openpyxl.styles import Font, PatternFill, Alignment

    BRAND = "0B2545"
    INK_100 = "E5E8EE"
    INK_50 = "F4F4F0"
    FORMAT_EURO = '#,##0 "€";[Red]-#,##0 "€";"—"'

    # Première ligne du tableau de données = ligne 2 (après header)
    for i, (_, _, niveau) in enumerate(lignes_struct):
        row_idx = i + 2  # +1 pour 1-indexed, +1 pour header
        label_cell = ws.cell(row=row_idx, column=1)

        if niveau == "section":
            label_cell.font = Font(name="Calibri", size=11, bold=True, color="0B2545", italic=True)
            label_cell.fill = PatternFill(start_color=INK_50, end_color=INK_50, fill_type="solid")
        elif niveau == "key":
            label_cell.font = Font(name="Calibri", size=11, bold=True, color="0B2545")
            label_cell.fill = PatternFill(start_color=INK_100, end_color=INK_100, fill_type="solid")
            for c_idx in range(col_data_start, ws.max_column + 1):
                cell = ws.cell(row=row_idx, column=c_idx)
                cell.font = Font(name="Calibri", size=11, bold=True, color="0B2545")
                cell.fill = PatternFill(start_color=INK_100, end_color=INK_100, fill_type="solid")
        elif niveau == "total":
            label_cell.font = Font(name="Calibri", size=10, bold=True)
            for c_idx in range(col_data_start, ws.max_column + 1):
                cell = ws.cell(row=row_idx, column=c_idx)
                cell.font = Font(name="Calibri", size=10, bold=True)
        elif niveau == "subtotal":
            label_cell.font = Font(name="Calibri", size=10, bold=True, italic=True)
            for c_idx in range(col_data_start, ws.max_column + 1):
                ws.cell(row=row_idx, column=c_idx).font = Font(name="Calibri", size=10, bold=True, italic=True)
        elif niveau == "blank":
            pass
        else:  # detail
            label_cell.font = Font(name="Calibri", size=10)
            label_cell.alignment = Alignment(indent=1)

        # Format monétaire € sur les colonnes data (sauf section et blank)
        if niveau in ("detail", "subtotal", "total", "key"):
            for c_idx in range(col_data_start, ws.max_column + 1):
                cell = ws.cell(row=row_idx, column=c_idx)
                cell.number_format = FORMAT_EURO


def generate_comptes_excel_bytes(siren: str, attachments: dict, denomination: str = "",
                                  multiple_ebe: float = 4.0, decote_illiq: float = 0.20) -> bytes:
    """Génère un classeur Excel multi-exercices des comptes annuels d'une entreprise.

    Onglets : Identité, Compte de résultat, Bilan — Actif, Bilan — Passif,
    Synthèse pluriannuelle, Ratios, Codes liasse détaillés.

    Le multiple EBE et la décote d'illiquidité utilisés sont les mêmes que ceux
    choisis dans l'UI de screening (pas de valeur hardcodée).
    """
    import io
    import pandas as pd
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from datetime import datetime

    bilans = [b for b in (attachments.get("bilansSaisis") or [])
              if b.get("confidentiality") in PUBLIC_CONFIDENTIALITY_STATUSES
              and not b.get("deleted")]
    bilans.sort(
        key=lambda b: b.get("bilanSaisi", {}).get("bilan", {})
                       .get("identite", {}).get("dateClotureExercice", ""),
        reverse=True,
    )

    buf = io.BytesIO()

    if not bilans:
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            pd.DataFrame([
                ("SIREN", siren),
                ("Dénomination", denomination),
                ("Statut", "Aucun bilan public disponible"),
                ("Date export", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ], columns=["Champ", "Valeur"]).to_excel(w, sheet_name="Identité", index=False)
        _format_finance_workbook(buf)
        return buf.getvalue()

    # Extraction des données par exercice
    exercices = []
    for b in bilans[:6]:  # 6 derniers exercices max
        bilan = extract_bilan(b)
        ratios = compute_ratios(bilan, multiple_ebe=multiple_ebe, decote_illiq=decote_illiq)
        date_cl = bilan.get("date_cloture") or "?"
        exercices.append({
            "date": date_cl,
            "col": f"Exercice {date_cl}",
            "bilan": bilan,
            "ratios": ratios,
            "type": b.get("confidentiality", ""),
        })

    # 1. Identité
    df_identite = pd.DataFrame([
        ("SIREN", siren),
        ("Dénomination", denomination),
        ("Source", "API INPI RNE — bilans saisis (liasses 2050-2053 / 2033)"),
        ("Nombre d'exercices disponibles", len(bilans)),
        ("Dernier exercice clôturé", exercices[0]["date"] if exercices else "—"),
        ("Date d'export", datetime.now().strftime("%Y-%m-%d %H:%M")),
    ], columns=["Champ", "Valeur"])

    # 2. Compte de résultat structuré
    cr_rows = []
    for code_key, libelle, niveau in COMPTE_RESULTAT_LIGNES:
        row = {"Poste": libelle}
        for ex in exercices:
            row[ex["col"]] = ex["bilan"].get(code_key) if code_key else None
        cr_rows.append(row)
    df_cr = pd.DataFrame(cr_rows)

    # 3. Bilan Actif structuré
    actif_rows = []
    for code_key, libelle, niveau in BILAN_ACTIF_LIGNES:
        row = {"Poste": libelle}
        for ex in exercices:
            row[ex["col"]] = ex["bilan"].get(code_key) if code_key else None
        actif_rows.append(row)
    df_actif = pd.DataFrame(actif_rows)

    # 4. Bilan Passif structuré
    passif_rows = []
    for code_key, libelle, niveau in BILAN_PASSIF_LIGNES:
        row = {"Poste": libelle}
        for ex in exercices:
            row[ex["col"]] = ex["bilan"].get(code_key) if code_key else None
        passif_rows.append(row)
    df_passif = pd.DataFrame(passif_rows)

    # 5. Synthèse pluriannuelle
    synthese_rows = {}
    for ex in exercices:
        synthese_rows[ex["col"]] = {
            "Chiffre d'affaires": ex["ratios"].get("ca"),
            "Résultat d'exploitation (REX)": ex["ratios"].get("rex"),
            "EBE proxy (REX + DAP − reprises)": ex["ratios"].get("ebe_proxy"),
            "Résultat net": ex["ratios"].get("resultat_net"),
            "Capitaux propres": ex["ratios"].get("capitaux_propres"),
            "Total bilan": ex["ratios"].get("total_bilan"),
            "Emprunts totaux": ex["ratios"].get("emprunts_total"),
            "Disponibilités": ex["ratios"].get("disponibilites"),
            "Dette nette": ex["ratios"].get("dette_nette"),
        }
    df_synth = pd.DataFrame(synthese_rows)

    # 6. Ratios
    ratios_rows = {}
    for ex in exercices:
        marge = ex["ratios"].get("marge_ebe")
        autonomie = ex["ratios"].get("autonomie")
        ratios_rows[ex["col"]] = {
            "Marge EBE (%)": (marge * 100) if marge is not None else None,
            "Gearing (dette nette / EBE)": ex["ratios"].get("gearing"),
            "Autonomie financière (%)": (autonomie * 100) if autonomie is not None else None,
            f"Valorisation proxy ({multiple_ebe:g}× EBE × {(1-decote_illiq)*100:g} %)": ex["ratios"].get("valo_proxy"),
        }
    df_ratios = pd.DataFrame(ratios_rows)

    # 7. Codes liasse détaillés
    codes_rows = {}
    for ex in exercices:
        for k, v in ex["bilan"].items():
            if isinstance(v, (int, float)) and k not in ("duree_exercice",):
                codes_rows.setdefault(k, {})[ex["col"]] = v
    df_codes = pd.DataFrame(codes_rows).T

    # Écriture du classeur
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df_identite.to_excel(w, sheet_name="Identité", index=False)
        df_cr.to_excel(w, sheet_name="Compte de résultat", index=False)
        df_actif.to_excel(w, sheet_name="Bilan — Actif", index=False)
        df_passif.to_excel(w, sheet_name="Bilan — Passif", index=False)
        df_synth.to_excel(w, sheet_name="Synthèse pluriannuelle")
        df_ratios.to_excel(w, sheet_name="Ratios")
        df_codes.to_excel(w, sheet_name="Codes liasse détaillés")

    # Mise en forme
    buf.seek(0)
    wb = load_workbook(buf)
    BRAND = "0B2545"
    INK_100 = "E5E8EE"
    INK_50 = "F4F4F0"
    FORMAT_EURO = '#,##0 "€";[Red]-#,##0 "€";"—"'
    FORMAT_PCT = '0.0 "%"'
    FORMAT_RATIO = '0.00 "×"'

    # Style général (header navy, freeze panes, largeurs)
    from openpyxl.utils import get_column_letter
    from openpyxl.styles import Border, Side
    thin = Side(border_style="thin", color=INK_100)

    for ws in wb.worksheets:
        if ws.max_row < 1:
            continue
        for cell in ws[1]:
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color=BRAND, end_color=BRAND, fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(bottom=thin)
        ws.row_dimensions[1].height = 30
        for col_idx, col in enumerate(ws.columns, start=1):
            values = [c.value for c in col if c.value is not None]
            length = max((len(str(v)) for v in values), default=10)
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(length + 3, 14), 50)
        ws.freeze_panes = "B2"

    # Styles structurés CR, Actif, Passif
    if "Compte de résultat" in wb.sheetnames:
        _apply_structure_styles(wb["Compte de résultat"], COMPTE_RESULTAT_LIGNES, col_data_start=2)
    if "Bilan — Actif" in wb.sheetnames:
        _apply_structure_styles(wb["Bilan — Actif"], BILAN_ACTIF_LIGNES, col_data_start=2)
    if "Bilan — Passif" in wb.sheetnames:
        _apply_structure_styles(wb["Bilan — Passif"], BILAN_PASSIF_LIGNES, col_data_start=2)

    # Format monétaire pour Synthèse pluriannuelle (toutes les colonnes data sauf la 1ère)
    if "Synthèse pluriannuelle" in wb.sheetnames:
        ws = wb["Synthèse pluriannuelle"]
        for row in ws.iter_rows(min_row=2, min_col=2):
            for cell in row:
                cell.number_format = FORMAT_EURO

    # Format Ratios : mapping explicite poste → format pour éviter les ambiguïtés
    # (ex. "Valorisation proxy (4× EBE × 80 %)" contient "%" dans son libellé mais
    # la valeur est monétaire, pas un pourcentage).
    valo_label = f"Valorisation proxy ({multiple_ebe:g}× EBE × {(1-decote_illiq)*100:g} %)"
    RATIOS_FORMATS = {
        "Marge EBE (%)": FORMAT_PCT,
        "Gearing (dette nette / EBE)": FORMAT_RATIO,
        "Autonomie financière (%)": FORMAT_PCT,
        valo_label: FORMAT_EURO,
    }
    if "Ratios" in wb.sheetnames:
        ws = wb["Ratios"]
        for row_idx in range(2, ws.max_row + 1):
            label = ws.cell(row=row_idx, column=1).value or ""
            fmt = RATIOS_FORMATS.get(label, FORMAT_EURO)
            for c_idx in range(2, ws.max_column + 1):
                ws.cell(row=row_idx, column=c_idx).number_format = fmt

    # Format Codes liasse détaillés : format monétaire
    if "Codes liasse détaillés" in wb.sheetnames:
        ws = wb["Codes liasse détaillés"]
        for row in ws.iter_rows(min_row=2, min_col=2):
            for cell in row:
                cell.number_format = FORMAT_EURO

    out_buf = io.BytesIO()
    wb.save(out_buf)
    return out_buf.getvalue()


def age_dirigeant_min(company: dict) -> tuple[int | None, str | None]:
    """Renvoie (age_min_dirigeants_direction, nom_du_plus_jeune)."""
    from datetime import date
    today = date.today()
    pp = extract_dirigeants_pp(company)
    pp_dir = [d for d in pp if d.get("is_direction") and d.get("date_naissance")]
    if not pp_dir:
        return None, None
    ages = []
    for d in pp_dir:
        dn = d["date_naissance"]  # format "YYYY-MM" ou "YYYY-MM-DD"
        try:
            annee, mois = int(dn[:4]), int(dn[5:7])
        except (ValueError, IndexError):
            continue
        age = today.year - annee - (1 if today.month < mois else 0)
        nom = f"{d.get('prenoms', '')} {d.get('nom', '')}".strip()
        ages.append((age, nom))
    if not ages:
        return None, None
    age_min, nom = min(ages, key=lambda x: x[0])
    return age_min, nom


def extract_beneficiaires_effectifs(company: dict) -> list[dict]:
    """Liste des BE avec leur pourcentage de capital / DV."""
    formality = company.get("formality") or {}
    content = formality.get("content") or {}
    be_section = content.get("beneficiairesEffectifs") or []
    out = []
    for be in be_section:
        b = be.get("beneficiaire") or be
        out.append({
            "nom": b.get("nom"),
            "prenoms": b.get("prenoms"),
            "date_naissance": b.get("dateDeNaissance"),
            "pct_parts_directes": b.get("modalitesDeControle", {}).get("partsDirectesTotal"),
            "pct_parts_indirectes": b.get("modalitesDeControle", {}).get("partsIndirectesTotal"),
            "pct_dv_directes": b.get("modalitesDeControle", {}).get("droitsVoteDirectsTotal"),
            "pct_dv_indirectes": b.get("modalitesDeControle", {}).get("droitsVoteIndirectsTotal"),
            "modalite": b.get("modalitesDeControle", {}).get("modalite"),
        })
    return out


def load_credentials_from_env() -> tuple[str, str]:
    """Récupère les credentials API INPI.

    Ordre de priorité :
    1. Secrets Streamlit Cloud (st.secrets) — pour déploiement web
    2. Variables d'environnement / .env local — pour usage en CLI ou local
    """
    # 1. Tenter st.secrets (Streamlit Cloud)
    try:
        import streamlit as st
        if hasattr(st, "secrets"):
            user = st.secrets.get("INPI_API_USER", "")
            pw = st.secrets.get("INPI_API_PASS", "")
            if user and pw:
                return user, pw
    except Exception:
        pass

    # 2. Fallback sur .env local
    from dotenv import load_dotenv
    load_dotenv()
    user = os.environ.get("INPI_API_USER")
    pw = os.environ.get("INPI_API_PASS")
    if not user or not pw:
        raise RuntimeError(
            "INPI_API_USER et INPI_API_PASS doivent être définis. "
            "En local : dans `.env`. En cloud : dans les Secrets Streamlit. "
            "Utiliser vos identifiants personnels data.inpi.fr."
        )
    return user, pw

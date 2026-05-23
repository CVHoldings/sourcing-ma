"""Client recherche-entreprises.api.gouv.fr — bulk listing par NAF."""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

BASE_URL = "https://recherche-entreprises.api.gouv.fr"

# Mapping codes INSEE de tranche d'effectif → fourchette de salariés
TRANCHE_EFFECTIF = {
    "00": (0, 0), "01": (1, 2), "02": (3, 5), "03": (6, 9),
    "11": (10, 19), "12": (20, 49),
    "21": (50, 99), "22": (100, 199),
    "31": (200, 249), "32": (250, 499),
    "41": (500, 999), "42": (1000, 1999),
    "51": (2000, 4999), "52": (5000, 9999), "53": (10000, 99999),
}


def codes_tranche_effectif(emin: int, emax: int) -> list[str]:
    """Renvoie les codes INSEE des tranches recouvrant [emin, emax]."""
    return [code for code, (lo, hi) in TRANCHE_EFFECTIF.items()
            if not (hi < emin or lo > emax)]


class APIGouvClient:
    def __init__(self, cache_dir: Path, per_page: int = 25, pause_sec: float = 0.2):
        self.base_url = BASE_URL
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.per_page = per_page
        self.pause_sec = pause_sec
        self.session = requests.Session()
        # User-Agent réaliste : certaines APIs publiques (dont recherche-entreprises
        # depuis le datacenter Streamlit Cloud) refusent l'UA par défaut python-requests.
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36 "
                "SourcingMA/1.0 (contact: cvcapitalpartners@gmail.com)"
            ),
        })

    def count(self, params: dict) -> int:
        """Renvoie le nombre total d'entreprises correspondant aux params,
        sans paginer (1 seule requête, instantané).

        Utile pour un garde-fou volume avant un pull complet.
        """
        resp = self._get({**params, "per_page": 1, "page": 1})
        return resp.get("total_results", 0)

    def search(self, params: dict, cache_key: str | None = None,
               max_workers: int = 8, progress_callback=None) -> list[dict]:
        """Pagination parallélisée sur /search. Renvoie la liste complète des résultats.

        Args:
            params : params API (activite_principale, etc.)
            cache_key : clé de cache disque
            max_workers : nombre de pages chargées en parallèle (défaut 8, max API gouv = 10 req/s)
            progress_callback : fonction (done, total) appelée à chaque page complétée
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        cache_path = self.cache_dir / f"{cache_key}.json" if cache_key else None
        if cache_path and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        # 1) Premier appel pour connaître total_pages
        query_base = {**params, "per_page": self.per_page}
        first = self._get({**query_base, "page": 1})
        total_pages = first.get("total_pages", 1)
        all_results: list[dict] = list(first.get("results", []))

        if progress_callback:
            progress_callback(1, total_pages)

        # 2) Pages 2..N en parallèle
        if total_pages > 1:
            def _fetch_page(page_num: int):
                return self._get({**query_base, "page": page_num})

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(_fetch_page, p): p
                    for p in range(2, total_pages + 1)
                }
                done = 1
                # Pré-allouer la liste pour respecter l'ordre des pages
                pages_results = {1: first.get("results", [])}
                for future in as_completed(futures):
                    page_num = futures[future]
                    try:
                        pages_results[page_num] = future.result().get("results", [])
                    except Exception:
                        pages_results[page_num] = []
                    done += 1
                    if progress_callback:
                        progress_callback(done, total_pages)
                # Réassembler dans l'ordre
                all_results = []
                for p in sorted(pages_results.keys()):
                    all_results.extend(pages_results[p])

        if cache_path:
            cache_path.write_text(
                json.dumps(all_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return all_results

    def _get(self, params: dict) -> dict:
        """GET avec retry sur 429 (rate limit) et 5xx (erreurs serveur)."""
        url = f"{self.base_url}/search"
        last_status = None
        for attempt in range(4):
            r = self.session.get(url, params=params, timeout=30)
            last_status = r.status_code
            if r.ok:
                return r.json()
            # Retry sur 429 (rate limit) ou 5xx
            if r.status_code == 429 or 500 <= r.status_code < 600:
                wait = (2 ** attempt) * 1.5  # 1.5, 3, 6, 12 secondes
                time.sleep(wait)
                continue
            # Pour les autres erreurs (4xx hors 429), on remonte avec le détail
            try:
                body = r.text[:300]
            except Exception:
                body = ""
            raise requests.exceptions.HTTPError(
                f"API gouv HTTP {r.status_code} sur params {params}. Body: {body}"
            )
        # Tous les retries épuisés
        raise requests.exceptions.HTTPError(
            f"API gouv : {last_status} après 4 tentatives. Paramètres : {params}"
        )

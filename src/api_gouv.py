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
        self.session.headers.update({"Accept": "application/json"})

    def search(self, params: dict, cache_key: str | None = None) -> list[dict]:
        """Pagination automatique sur /search. Renvoie la liste complète des résultats."""
        cache_path = self.cache_dir / f"{cache_key}.json" if cache_key else None
        if cache_path and cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

        query = {**params, "per_page": self.per_page, "page": 1}
        first = self._get(query)
        total_pages = first.get("total_pages", 1)
        total_results = first.get("total_results", 0)
        all_results: list[dict] = list(first.get("results", []))

        if total_pages > 1:
            for page in tqdm(range(2, total_pages + 1), desc=f"NAF {params.get('activite_principale')}", unit="page"):
                time.sleep(self.pause_sec)
                query["page"] = page
                resp = self._get(query)
                all_results.extend(resp.get("results", []))

        if cache_path:
            cache_path.write_text(
                json.dumps(all_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        return all_results

    def _get(self, params: dict) -> dict:
        url = f"{self.base_url}/search"
        r = self.session.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r.json()

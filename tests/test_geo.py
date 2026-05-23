"""Tests src/geo_fr.py — régions, départements, filtre géographique."""
from __future__ import annotations

import pytest

from src.geo_fr import REGIONS_FR, DEPARTEMENTS_FR, filter_entreprises_par_geo


class TestReferentielGeo:
    def test_nombre_regions(self):
        # 13 régions métropolitaines + 5 DOM = 18 régions INSEE
        assert len(REGIONS_FR) == 18

    def test_nombre_departements(self):
        # 95 métropole (sans 20 mais avec 2A/2B) + 7 DOM/COM (971-974, 975, 976, 977, 978)
        # Total = 95 + 2A + 2B = 96, et avec 5 DOM + 3 COM = 96 + 8 = 104
        assert len(DEPARTEMENTS_FR) == 104

    def test_corse_presente(self):
        assert "2A" in DEPARTEMENTS_FR
        assert "2B" in DEPARTEMENTS_FR

    def test_com_ajoutes(self):
        """COM 975 Saint-Pierre, 977 Saint-Barthélemy, 978 Saint-Martin — bug audit v1 corrigé."""
        assert "975" in DEPARTEMENTS_FR
        assert "977" in DEPARTEMENTS_FR
        assert "978" in DEPARTEMENTS_FR

    def test_paca(self):
        # PACA = code région 93
        assert REGIONS_FR.get("93") == "Provence-Alpes-Côte d'Azur"


class TestFilterGeo:
    @pytest.fixture
    def entreprises(self):
        return [
            {"siren": "1", "siege": {"region": "93", "departement": "13"}},
            {"siren": "2", "siege": {"region": "11", "departement": "75"}},
            {"siren": "3", "siege": {"region": "84", "departement": "69"}},
        ]

    def test_filtre_france(self, entreprises):
        result = filter_entreprises_par_geo(entreprises, "france")
        assert len(result) == 3  # Tous retenus

    def test_filtre_regions(self, entreprises):
        result = filter_entreprises_par_geo(entreprises, "regions", regions=["93"])
        assert len(result) == 1
        assert result[0]["siren"] == "1"

    def test_filtre_departements(self, entreprises):
        result = filter_entreprises_par_geo(entreprises, "departements",
                                            departements=["75", "69"])
        assert len(result) == 2
        assert {r["siren"] for r in result} == {"2", "3"}

    def test_filtre_aucun_critere(self, entreprises):
        # Si mode = regions mais liste vide, on retourne tout (pas de filtre)
        result = filter_entreprises_par_geo(entreprises, "regions", regions=[])
        assert len(result) == 3

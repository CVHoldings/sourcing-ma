"""Tests src/filtres.py — filtres tranche 1, ancienneté d'entreprise."""
from __future__ import annotations

from datetime import date

import pytest

from src.filtres import (
    anciennete_annees,
    is_pp,
    is_pm,
    is_cac,
    is_dirigeant_principal,
    appliquer_filtres_tranche1,
)


class TestAnciennete:
    def test_anciennete_normale(self):
        # Date de création 1990-01-01, ref 2026-05-23 → 36 ans
        age = anciennete_annees("1990-01-01", ref=date(2026, 5, 23))
        assert age == 36

    def test_anciennete_recente(self):
        age = anciennete_annees("2020-06-15", ref=date(2026, 5, 23))
        assert age == 5  # créé après le jour anniversaire d'avant

    def test_anciennete_invalide(self):
        assert anciennete_annees(None) is None
        assert anciennete_annees("") is None
        assert anciennete_annees("abc") is None


class TestIsPPPMCac:
    def test_is_pp(self):
        assert is_pp({"type_dirigeant": "personne physique"}) is True
        assert is_pp({"type_dirigeant": "personne morale"}) is False

    def test_is_pm(self):
        assert is_pm({"type_dirigeant": "personne morale"}) is True

    def test_is_cac(self):
        assert is_cac({"qualite": "Commissaire aux comptes titulaire"}) is True
        assert is_cac({"qualite": "Président"}) is False


class TestAppliquerFiltresTranche1:
    def test_filtre_inactif(self):
        entreprises = [{
            "siren": "123",
            "etat_administratif": "C",  # cessé
            "date_creation": "1990-01-01",
        }]
        ret, rej = appliquer_filtres_tranche1(entreprises, anciennete_min=10)
        assert len(ret) == 0
        assert len(rej) == 1
        assert "inactive" in rej[0]["_rejet_motif"]

    def test_filtre_anciennete(self):
        entreprises = [{
            "siren": "123",
            "etat_administratif": "A",
            "date_creation": "2020-01-01",  # 6 ans en 2026, < seuil 10
            "dirigeants": [{
                "type_dirigeant": "personne physique",
                "qualite": "Président",
                "nom": "TEST",
                "prenoms": ["T"],
            }],
        }]
        ret, rej = appliquer_filtres_tranche1(entreprises, anciennete_min=10)
        assert len(ret) == 0
        assert any("trop jeune" in r["_rejet_motif"] for r in rej)

    def test_filtre_pp_dirigeant_requis(self):
        # Entreprise dirigée uniquement par une PM (filiale potentielle)
        entreprises = [{
            "siren": "123",
            "etat_administratif": "A",
            "date_creation": "1990-01-01",
            "dirigeants": [{
                "type_dirigeant": "personne morale",
                "qualite": "Président",
                "denomination": "HOLDING SA",
            }],
        }]
        ret, rej = appliquer_filtres_tranche1(entreprises, anciennete_min=10,
                                              exiger_dirigeant_pp=True)
        assert len(ret) == 0
        assert any("dirigeant PP" in r["_rejet_motif"] or "PM" in r["_rejet_motif"]
                   for r in rej)

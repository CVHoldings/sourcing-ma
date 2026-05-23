"""Tests src/inpi.py — extract_bilan, compute_ratios, _str_to_int, bilan_age_years."""
from __future__ import annotations

from datetime import date

import pytest

from src.inpi import (
    _str_to_int,
    extract_bilan,
    compute_ratios,
    bilan_age_years,
    LIASSE_PAGE_COL_N_NORMAL,
    LIASSE_PAGE_COL_N_SIMPLIFIE,
)


class TestStrToInt:
    def test_normal(self):
        assert _str_to_int("000000000007622") == 7622

    def test_negative(self):
        assert _str_to_int("-000000000230752") == -230752

    def test_empty(self):
        assert _str_to_int("") is None
        assert _str_to_int(None) is None

    def test_whitespace(self):
        # Comportement actuel : retourne 0 (strip+lstrip("0") sur "    " = "")
        assert _str_to_int("    ") == 0

    def test_invalid(self):
        assert _str_to_int("abc") is None


class TestExtractBilanNormal:
    """Bilan régime normal (codeTypeBilan='C') — pages 1-4, colonnes m3/m1."""

    @pytest.fixture
    def bilan_normal_minimal(self):
        return {
            "bilanSaisi": {
                "bilan": {
                    "identite": {
                        "siren": "612019505",
                        "dateClotureExercice": "2024-12-31",
                        "codeTypeBilan": "C",
                        "dureeExerciceN": "12",
                    },
                    "detail": {
                        "pages": [
                            {"numero": 1, "liasses": [
                                {"code": "CO", "m1": "10000000", "m2": "0",
                                 "m3": "10000000", "m4": "9000000"},
                            ]},
                            {"numero": 2, "liasses": [
                                {"code": "DL", "m1": "5000000", "m2": "4500000"},
                                {"code": "EE", "m1": "10000000", "m2": "9000000"},
                            ]},
                            {"numero": 3, "liasses": [
                                {"code": "FJ", "m3": "4750000", "m4": "5080000"},
                                {"code": "GG", "m3": "510000", "m4": "560000"},
                                {"code": "GA", "m3": "65000", "m4": "55000"},
                            ]},
                            {"numero": 4, "liasses": [
                                {"code": "HN", "m1": "428000", "m2": "464000"},
                            ]},
                        ],
                    },
                },
            },
        }

    def test_ca_extracted(self, bilan_normal_minimal):
        b = extract_bilan(bilan_normal_minimal)
        assert b["ca_net"] == 4750000

    def test_rex_extracted(self, bilan_normal_minimal):
        b = extract_bilan(bilan_normal_minimal)
        assert b["rex"] == 510000

    def test_total_actif_extracted(self, bilan_normal_minimal):
        b = extract_bilan(bilan_normal_minimal)
        assert b["total_actif"] == 10000000

    def test_capitaux_propres_extracted(self, bilan_normal_minimal):
        b = extract_bilan(bilan_normal_minimal)
        assert b["capitaux_propres"] == 5000000

    def test_resultat_net_extracted(self, bilan_normal_minimal):
        b = extract_bilan(bilan_normal_minimal)
        assert b["resultat_exercice"] == 428000


class TestExtractBilanSimplifie:
    """Bilan régime simplifié (codeTypeBilan='S') — pages 1/2, colonne m1 sur la 2."""

    @pytest.fixture
    def bilan_simplifie_minimal(self):
        return {
            "bilanSaisi": {
                "bilan": {
                    "identite": {
                        "siren": "302431325",
                        "dateClotureExercice": "2016-12-31",
                        "codeTypeBilan": "S",
                        "dureeExerciceN": "12",
                    },
                    "detail": {
                        "pages": [
                            {"numero": 1, "liasses": [
                                {"code": "110", "m1": "5000000", "m3": "4500000"},
                                {"code": "142", "m1": "2000000", "m3": "1800000"},
                                {"code": "180", "m1": "5000000", "m3": "4500000"},
                            ]},
                            {"numero": 2, "liasses": [
                                {"code": "210", "m1": "1000000", "m3": ""},
                                {"code": "214", "m1": "1500000", "m3": ""},
                                {"code": "218", "m1": "500000", "m3": ""},
                                {"code": "232", "m1": "3200000", "m3": ""},
                                {"code": "270", "m1": "150000", "m3": ""},
                                {"code": "310", "m1": "85000", "m3": ""},
                            ]},
                        ],
                    },
                },
            },
        }

    def test_page_2_uses_m1(self, bilan_simplifie_minimal):
        """Le bug audit P0 v2.1 : page 2 doit lire m1 (pas m3)."""
        b = extract_bilan(bilan_simplifie_minimal)
        assert b["ventes_marchandises_total"] == 1000000
        assert b["rex"] == 150000

    def test_ca_reconstitue(self, bilan_simplifie_minimal):
        """CA reconstitué depuis 210+214+218, pas via 232 (qui est total)."""
        b = extract_bilan(bilan_simplifie_minimal)
        r = compute_ratios(b)
        # CA = 1000000 + 1500000 + 500000 = 3000000
        assert r["ca"] == 3000000


class TestExtractBilanK:
    """Bilan type K — utilise les codes du régime NORMAL (pas simplifié)."""

    @pytest.fixture
    def bilan_k_minimal(self):
        return {
            "bilanSaisi": {
                "bilan": {
                    "identite": {
                        "siren": "321304016",
                        "dateClotureExercice": "2022-12-31",
                        "codeTypeBilan": "K",
                        "dureeExerciceN": "12",
                    },
                    "detail": {
                        "pages": [
                            {"numero": 3, "liasses": [
                                {"code": "FJ", "m3": "31062681", "m4": "30558086"},
                                {"code": "GG", "m3": "2294993", "m4": "3369385"},
                            ]},
                        ],
                    },
                },
            },
        }

    def test_k_routed_to_normal(self, bilan_k_minimal):
        """Bug audit v2 § 1.1 : K doit utiliser codes normaux, pas simplifiés."""
        b = extract_bilan(bilan_k_minimal)
        assert b["ca_net"] == 31062681
        assert b["rex"] == 2294993


class TestComputeRatios:
    def test_normal_no_fallback_ca(self):
        """Le fallback CA → total_produits_exploitation a été retiré.
        Si ca_net est absent en régime normal, ca doit être None."""
        bilan = {"code_type_bilan": "C", "total_produits_exploitation": 5000000}
        r = compute_ratios(bilan)
        assert r["ca"] is None

    def test_normal_ca_strict(self):
        bilan = {"code_type_bilan": "C", "ca_net": 4750000, "rex": 510000,
                 "dap_amortissements": 65000}
        r = compute_ratios(bilan)
        assert r["ca"] == 4750000
        assert r["ebe_proxy"] == 575000  # 510000 + 65000

    def test_marge_ebe(self):
        bilan = {"code_type_bilan": "C", "ca_net": 1000000, "rex": 80000,
                 "dap_amortissements": 20000}
        r = compute_ratios(bilan)
        assert r["marge_ebe"] == pytest.approx(0.10, abs=0.001)

    def test_autonomie_correcte(self):
        """Le bug audit v1 § 1.7 corrigé : autonomie = CP / Total bilan."""
        bilan = {"code_type_bilan": "C", "capitaux_propres": 5000000,
                 "total_passif": 6000000}
        r = compute_ratios(bilan)
        assert r["autonomie"] == pytest.approx(0.8333, abs=0.001)

    def test_valo_proxy_custom_multiple(self):
        bilan = {"code_type_bilan": "C", "ca_net": 1000000, "rex": 100000,
                 "dap_amortissements": 0}
        r = compute_ratios(bilan, multiple_ebe=5.0, decote_illiq=0.20)
        # EBE=100000, valo=100000*5*0.8=400000
        assert r["valo_proxy"] == 400000

    def test_ebe_negatif_ne_calcule_pas_valo(self):
        bilan = {"code_type_bilan": "C", "ca_net": 1000000, "rex": -50000,
                 "dap_amortissements": 0}
        r = compute_ratios(bilan)
        assert r["valo_proxy"] is None  # EBE négatif


class TestBilanAgeYears:
    def test_recent(self):
        # Date courante = 2026, bilan 2024 = 2 ans
        age = bilan_age_years({"date_cloture": "2024-12-31"},
                              today=date(2026, 5, 23))
        assert age == 2

    def test_obsolete(self):
        age = bilan_age_years({"date_cloture": "2014-12-31"},
                              today=date(2026, 5, 23))
        assert age == 12

    def test_none(self):
        assert bilan_age_years({}) is None
        assert bilan_age_years({"date_cloture": None}) is None
        assert bilan_age_years({"date_cloture": "invalid"}) is None


class TestPageColumnConventions:
    def test_normal_conventions(self):
        # Régime normal : page 1=m3, page 2=m1, page 3=m3, page 4=m1
        assert LIASSE_PAGE_COL_N_NORMAL[1] == "m3"
        assert LIASSE_PAGE_COL_N_NORMAL[2] == "m1"
        assert LIASSE_PAGE_COL_N_NORMAL[3] == "m3"
        assert LIASSE_PAGE_COL_N_NORMAL[4] == "m1"

    def test_simplifie_conventions(self):
        # Bug audit v2 corrigé : page 2 = m1 (pas m3 qui est vide)
        assert LIASSE_PAGE_COL_N_SIMPLIFIE[1] == "m3"
        assert LIASSE_PAGE_COL_N_SIMPLIFIE[2] == "m1"

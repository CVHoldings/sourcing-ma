"""Tests src/naf_sections.py — sections NAF, filtre objet social."""
from __future__ import annotations

import pytest

from src.naf_sections import (
    NAF_SECTIONS,
    extract_objet_social,
    filter_par_objet_social,
)


class TestNafSections:
    def test_21_sections(self):
        assert len(NAF_SECTIONS) == 21

    def test_codes_alphabet(self):
        # Les sections vont de A à U
        assert "A" in NAF_SECTIONS
        assert "U" in NAF_SECTIONS

    def test_section_g_commerce(self):
        assert "Commerce" in NAF_SECTIONS["G"]


class TestExtractObjetSocial:
    def test_path_identite_entreprise_objet(self):
        company = {
            "formality": {
                "content": {
                    "personneMorale": {
                        "identite": {
                            "entreprise": {
                                "objet": "Commerce de gros de pièces hydrauliques",
                            }
                        }
                    }
                }
            }
        }
        assert "pièces hydrauliques" in extract_objet_social(company)

    def test_path_description_objet(self):
        company = {
            "formality": {
                "content": {
                    "personneMorale": {
                        "descriptionEntreprise": {
                            "objet": "Maintenance industrielle",
                        }
                    }
                }
            }
        }
        assert "Maintenance" in extract_objet_social(company)

    def test_vide_si_absent(self):
        assert extract_objet_social({}) == ""
        assert extract_objet_social({"formality": {"content": {}}}) == ""


class TestFilterParObjetSocial:
    def test_match(self):
        cibles = [
            {"company_inpi": {
                "formality": {"content": {"personneMorale": {
                    "identite": {"entreprise": {
                        "objet": "Transmission mécanique et hydraulique"
                    }}
                }}}
            }, "siren": "1"},
        ]
        result = filter_par_objet_social(cibles, "transmission")
        assert len(result) == 1
        assert result[0].get("objet_social_match") == "match"

    def test_exclu_si_pas_de_match(self):
        cibles = [
            {"company_inpi": {
                "formality": {"content": {"personneMorale": {
                    "identite": {"entreprise": {"objet": "Restauration"}}
                }}}
            }, "siren": "1"},
        ]
        result = filter_par_objet_social(cibles, "transmission")
        assert len(result) == 0

    def test_tolerant_objet_inconnu(self):
        """Comportement tolérant : on garde les cibles à objet inconnu."""
        cibles = [{"company_inpi": {}, "siren": "1"}]
        result = filter_par_objet_social(cibles, "transmission")
        assert len(result) == 1
        assert result[0].get("objet_social_match") == "objet inconnu (INPI)"

    def test_mots_cles_vides(self):
        """Pas de filtre si pas de mots-clés."""
        cibles = [{"company_inpi": {}, "siren": "1"}]
        result = filter_par_objet_social(cibles, "")
        assert len(result) == 1

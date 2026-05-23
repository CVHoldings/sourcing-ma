"""Proxy de valorisation pour screening (pas évaluation fine).

Patterns issus du skill `evaluation-entreprise` :
- méthode des praticiens : V = (ANCC + V_rendement) / 2
- multiple TPE-PME négoce industriel ~4-5× EBE
- décote d'illiquidité PME non cotée 15-30 %
"""
from __future__ import annotations


def proxy_valorisation(
    ebe: float | None,
    multiple_ebe: float = 4.0,
    decote_illiquidite: float = 0.20,
) -> float | None:
    """Valorisation indicative = EBE × multiple × (1 - décote illiquidité)."""
    if ebe is None or ebe <= 0:
        return None
    return ebe * multiple_ebe * (1 - decote_illiquidite)


def methode_praticiens(
    ancc: float | None,
    ebe: float | None,
    multiple_ebe: float = 4.0,
) -> float | None:
    """V = (ANCC + V_rendement) / 2. Robuste pour TPE/PME."""
    if ebe is None or ebe <= 0 or ancc is None:
        return None
    v_rendement = ebe * multiple_ebe
    return (ancc + v_rendement) / 2.0


def gearing(dette_financiere_nette: float | None, ebe: float | None) -> float | None:
    if ebe is None or ebe <= 0 or dette_financiere_nette is None:
        return None
    return dette_financiere_nette / ebe


def marge_ebe(ebe: float | None, ca: float | None) -> float | None:
    if ebe is None or ca is None or ca <= 0:
        return None
    return ebe / ca

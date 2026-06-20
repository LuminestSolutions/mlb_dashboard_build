"""Placeholder scoring engine for the MLB Edge Model.

Codex/Claude should replace this stub with the full Daily MLB Handicap scoring engine.
"""

from __future__ import annotations


def conservative_recommendation(edge_pct: float, line_diff_cents: int, confidence: float, lineup_uncertain: bool) -> str:
    if edge_pct >= 4.0 and line_diff_cents >= 15 and confidence >= 7.0 and not lineup_uncertain:
        return "BET"
    return "PASS"

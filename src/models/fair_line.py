"""Fair-line and edge calculation helpers."""

from __future__ import annotations


def american_to_implied_prob(odds: int) -> float:
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def implied_prob_to_american(probability: float) -> int:
    if probability <= 0 or probability >= 1:
        raise ValueError("Probability must be between 0 and 1")
    if probability >= 0.5:
        return round(-100 * probability / (1 - probability))
    return round(100 * (1 - probability) / probability)


def edge_pct(model_prob: float, market_odds: int) -> float:
    return (model_prob - american_to_implied_prob(market_odds)) * 100

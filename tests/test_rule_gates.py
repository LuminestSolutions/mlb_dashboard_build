from __future__ import annotations

import unittest

import app


def valid_game() -> dict:
    return {
        "away_name": "Los Angeles Dodgers",
        "home_name": "New York Yankees",
        "away_probable_pitcher": "Away Starter",
        "home_probable_pitcher": "Home Starter",
        "away_lineup_status": "Confirmed",
        "home_lineup_status": "Confirmed",
        "away_injury_count": 1,
        "home_injury_count": 1,
        "away_injured_hitters": 1,
        "home_injured_hitters": 1,
        "away_bullpen_status": "Fresh",
        "home_bullpen_status": "Moderate",
        "pitcher_change_detected": 0,
        "pitcher_change_details": "",
        "weather_risk_level": "Low",
        "first_pitch_utc": "2099-07-18T23:10:00Z",
    }


def evaluate(game: dict, profile: str = "Conservative", stale: bool = False) -> dict:
    return app.evaluate_rule_gates(
        game=game,
        profile_name=profile,
        recommended_team="Los Angeles Dodgers",
        edge=7.0,
        line_diff=30,
        confidence=9.0,
        edge_threshold=4.0,
        min_line_diff=15,
        min_confidence=7.0,
        pitching_edge=5.0,
        offense_edge=3.0,
        line_movement=0,
        odds_stale=stale,
    )


class RuleGateTests(unittest.TestCase):
    def test_conservative_context_can_qualify(self) -> None:
        self.assertTrue(evaluate(valid_game())["passed"])

    def test_heavy_recommended_bullpen_blocks_conservative(self) -> None:
        game = valid_game()
        game["away_bullpen_status"] = "Heavy"
        result = evaluate(game)
        self.assertFalse(result["passed"])
        self.assertIn("Bullpen not heavily taxed", result["blockers"])

    def test_pitcher_change_blocks_aggressive(self) -> None:
        game = valid_game()
        game["pitcher_change_detected"] = 1
        game["pitcher_change_details"] = "Away starter changed"
        result = evaluate(game, profile="Aggressive")
        self.assertFalse(result["passed"])
        self.assertIn("No pitcher change", result["blockers"])

    def test_stale_odds_block_every_profile(self) -> None:
        for profile in app.RULE_PROFILES:
            with self.subTest(profile=profile):
                result = evaluate(valid_game(), profile=profile, stale=True)
                self.assertFalse(result["passed"])
                self.assertIn("Odds freshness", result["blockers"])

    def test_moderate_weather_blocks_very_conservative(self) -> None:
        game = valid_game()
        game["weather_risk_level"] = "Moderate"
        result = evaluate(game, profile="Very Conservative")
        self.assertFalse(result["passed"])
        self.assertIn("Low weather risk", result["blockers"])

    def test_due_unconfirmed_lineup_blocks_conservative(self) -> None:
        game = valid_game()
        game["first_pitch_utc"] = "2000-07-18T23:10:00Z"
        game["away_lineup_status"] = "Pending"
        result = evaluate(game)
        self.assertFalse(result["passed"])
        self.assertIn("Lineups confirmed when due", result["blockers"])

    def test_unresolved_hitter_cluster_blocks_conservative(self) -> None:
        game = valid_game()
        game["first_pitch_utc"] = "2000-07-18T23:10:00Z"
        game["away_lineup_status"] = "Pending"
        game["away_injured_hitters"] = 3
        result = evaluate(game)
        self.assertFalse(result["passed"])
        self.assertIn("No major unresolved injury cluster", result["blockers"])

    def test_high_weather_risk_blocks_aggressive(self) -> None:
        game = valid_game()
        game["weather_risk_level"] = "High"
        result = evaluate(game, profile="Aggressive")
        self.assertFalse(result["passed"])
        self.assertIn("No high weather risk", result["blockers"])


if __name__ == "__main__":
    unittest.main()

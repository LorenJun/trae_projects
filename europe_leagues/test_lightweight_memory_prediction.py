import unittest

from domain.lightweight_prediction import (
    _pick_top_scores,
    build_lightweight_prediction_result,
)


class LightweightMemoryPredictionTest(unittest.TestCase):
    def test_build_lightweight_prediction_result_from_snapshot(self):
        snapshot = {
            "match_id": "1309999",
            "欧赔": {
                "final": {"home": 1.76, "draw": 3.55, "away": 4.20},
                "initial": {"home": 1.90, "draw": 3.40, "away": 3.80},
            },
            "亚值": {
                "final": {"handicap_text": "平/半", "handicap_value": -0.25, "home_water": 1.58, "away_water": 2.53},
                "initial": {"handicap_text": "平/半", "handicap_value": -0.25, "home_water": 1.91, "away_water": 1.91},
            },
            "大小球": {
                "final": {"line": 2.75, "over": 1.95, "under": 1.85},
                "initial": {"line": 2.75, "over": 1.92, "under": 1.87},
            },
            "凯利": {"final": {"home": 0.95, "draw": 0.95, "away": 0.95}},
        }

        result = build_lightweight_prediction_result(
            snapshot=snapshot,
            league_name="英冠",
            league_code="championship",
            home_team="米尔沃尔",
            away_team="赫尔城",
            match_date="2026-05-12",
            match_time="03:00",
            match_id="1309999",
        )

        self.assertEqual(result["league_code"], "championship")
        self.assertEqual(result["league_name"], "英冠")
        self.assertEqual(result["storage_mode"], "runtime_only")
        self.assertEqual(result["prediction"], "主胜")
        self.assertTrue(result["over_under"]["available"])
        self.assertEqual(result["over_under"]["line_source"], "snapshot_final")
        self.assertEqual(result["external_match_id"], "1309999")
        self.assertIn(result["top_scores"][0][0], {"2-0", "2-1", "3-1"})
        self.assertIn("轻量模式", result["retrieved_memory_explanation"])
        self.assertIn("home_win", result["final_probabilities"])

    def test_pick_top_scores_prefers_stronger_margin_for_deep_home_favorite(self):
        scores = _pick_top_scores(
            "主胜",
            probabilities={"home_win": 0.61, "draw": 0.22, "away_win": 0.17},
            handicap_value=-1.0,
            home_water=1.82,
            away_water=2.02,
            ou_line=3.0,
            over_prob=0.57,
            under_prob=0.43,
        )
        self.assertIn(scores[0][0], {"2-0", "3-1", "3-0"})
        self.assertNotEqual([item[0] for item in scores], ["1-0", "2-0", "2-1"])

    def test_pick_top_scores_prefers_narrow_away_win_for_low_total(self):
        scores = _pick_top_scores(
            "客胜",
            probabilities={"home_win": 0.24, "draw": 0.27, "away_win": 0.49},
            handicap_value=0.5,
            home_water=2.08,
            away_water=1.84,
            ou_line=2.25,
            over_prob=0.45,
            under_prob=0.55,
        )
        self.assertIn(scores[0][0], {"0-1", "0-2"})
        self.assertIn(scores[1][0], {"0-2", "1-2", "1-1"})

    def test_pick_top_scores_prefers_draw_templates_when_prediction_is_draw(self):
        scores = _pick_top_scores(
            "平局",
            probabilities={"home_win": 0.29, "draw": 0.39, "away_win": 0.32},
            handicap_value=0.0,
            home_water=1.95,
            away_water=1.95,
            ou_line=2.0,
            over_prob=0.44,
            under_prob=0.56,
        )
        self.assertIn(scores[0][0], {"0-0", "1-1"})
        self.assertTrue(all(score in {"0-0", "1-1", "1-0", "0-1", "2-0", "0-2", "2-2"} for score, _ in scores))


if __name__ == "__main__":
    unittest.main()

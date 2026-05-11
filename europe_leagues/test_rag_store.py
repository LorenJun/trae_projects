import unittest

from runtime.rag_store import _annotate_review_dimensions, _derive_review_tags


class RagReviewTagTest(unittest.TestCase):
    def test_derive_review_tags_for_home_bias_miss(self):
        base = {
            "prediction": "主胜",
            "actual_result": "客胜",
            "actual_score": "0-1",
            "confidence": 0.67,
            "predicted_scores": ["1-0", "2-1"],
            "predicted_ou_direction": "",
            "ou_line": None,
        }
        tags = _derive_review_tags(base)
        self.assertIn("主胜高估", tags)
        self.assertIn("客胜冷门漏判", tags)
        self.assertIn("高信心误判", tags)
        self.assertIn("比分模板偏主胜", tags)
        self.assertIn("大小球盘口线缺失", tags)

    def test_annotate_review_dimensions_adds_league_level_tags(self):
        documents = [
            {
                "match_id": "m1",
                "archive_key": "m1",
                "league_code": "premier_league",
                "league_name": "英超",
                "competition_stage_name": "",
                "competition_bucket": "domestic_league",
                "home_team": "A",
                "away_team": "B",
                "match_date": "2026-05-10",
                "prediction": "主胜",
                "confidence": 0.7,
                "actual_score": "0-1",
                "actual_result": "客胜",
                "storage_mode": "archive",
                "risk_points": [],
                "market_snapshot": {},
                "market_summary": "",
                "ou_line": None,
                "predicted_ou_direction": "",
                "asian_line": None,
                "euro_home": None,
                "euro_draw": None,
                "euro_away": None,
                "actual_total_goals": 1,
                "predicted_scores": ["1-0", "2-1"],
                "completed": True,
                "archived_at": "2026-05-10T12:00:00",
                "case_type": "prediction_case",
                "text": "英超 | A vs B | 预测:主胜 | 赛果:客胜 0-1",
            },
            {
                "match_id": "m2",
                "archive_key": "m2",
                "league_code": "premier_league",
                "league_name": "英超",
                "competition_stage_name": "",
                "competition_bucket": "domestic_league",
                "home_team": "C",
                "away_team": "D",
                "match_date": "2026-05-10",
                "prediction": "主胜",
                "confidence": 0.65,
                "actual_score": "1-1",
                "actual_result": "平局",
                "storage_mode": "archive",
                "risk_points": [],
                "market_snapshot": {},
                "market_summary": "",
                "ou_line": None,
                "predicted_ou_direction": "",
                "asian_line": None,
                "euro_home": None,
                "euro_draw": None,
                "euro_away": None,
                "actual_total_goals": 2,
                "predicted_scores": ["1-0", "2-1"],
                "completed": True,
                "archived_at": "2026-05-10T12:00:00",
                "case_type": "prediction_case",
                "text": "英超 | C vs D | 预测:主胜 | 赛果:平局 1-1",
            },
        ]
        _annotate_review_dimensions(documents, recent_days=30)
        for doc in documents:
            self.assertIn("主胜高估", doc["review_tags"])
            self.assertIn("英超-主胜偏置", doc["league_review_tags"])
            self.assertIn("英超-大小球盘口线缺失", doc["league_review_tags"])
            self.assertIn("错因标签:", doc["text"])
            self.assertIn("联赛复盘:", doc["text"])


if __name__ == "__main__":
    unittest.main()

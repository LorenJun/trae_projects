import unittest
from unittest.mock import patch

from runtime.rag_store import (
    _annotate_review_dimensions,
    _build_query,
    _derive_review_tags,
    _select_top_group,
    _temporal_bonus,
    retrieve_hybrid_context,
)


class RagTemporalScoringTest(unittest.TestCase):
    def test_temporal_bonus_prefers_same_season_and_recent_cases(self):
        query = _build_query(
            league_code="la_liga",
            home_team="巴塞罗那",
            away_team="皇家马德里",
            market_snapshot={},
            match_date="2026-05-15",
            analysis_context={},
        )
        same_season_near = _temporal_bonus({"match_date": "2026-04-20"}, query)
        adjacent_season = _temporal_bonus({"match_date": "2024-10-20"}, query)
        far_history = _temporal_bonus({"match_date": "2023-04-20"}, query)

        self.assertTrue(same_season_near["season_match"])
        self.assertGreater(same_season_near["season_bonus"], adjacent_season["season_bonus"])
        self.assertGreater(same_season_near["time_decay_bonus"], adjacent_season["time_decay_bonus"])
        self.assertGreater(adjacent_season["temporal_bonus"], far_history["temporal_bonus"])

    def test_temporal_bonus_ignores_future_matches(self):
        query = _build_query(
            league_code="la_liga",
            home_team="巴塞罗那",
            away_team="皇家马德里",
            market_snapshot={},
            match_date="2026-05-15",
            analysis_context={},
        )
        future_case = _temporal_bonus({"match_date": "2026-05-18"}, query)

        self.assertEqual(future_case["temporal_bonus"], 0.0)
        self.assertEqual(future_case["season_bonus"], 0.0)
        self.assertEqual(future_case["time_decay_bonus"], 0.0)
        self.assertIsNone(future_case["month_gap"])

    def test_temporal_bonus_gracefully_degrades_for_missing_or_bad_dates(self):
        query = _build_query(
            league_code="la_liga",
            home_team="巴塞罗那",
            away_team="皇家马德里",
            market_snapshot={},
            match_date="",
            analysis_context={},
        )
        missing = _temporal_bonus({"match_date": ""}, query)
        malformed = _temporal_bonus({"match_date": "not-a-date"}, query)

        self.assertEqual(missing["temporal_bonus"], 0.0)
        self.assertEqual(missing["season_bonus"], 0.0)
        self.assertEqual(missing["time_decay_bonus"], 0.0)
        self.assertEqual(malformed["temporal_bonus"], 0.0)
        self.assertFalse(malformed["season_match"])

    def test_select_top_group_exposes_temporal_fields(self):
        doc = {
            "match_id": "hist-1",
            "league_code": "la_liga",
            "league_name": "西甲",
            "competition_stage_name": "",
            "match_date": "2026-04-20",
            "home_team": "马竞",
            "away_team": "塞维利亚",
            "prediction": "主胜",
            "confidence": 0.61,
            "actual_score": "2-1",
            "actual_result": "主胜",
            "storage_mode": "archive",
            "risk_points": [],
            "predicted_ou_direction": "大球",
            "ou_line": 2.5,
            "predicted_scores": ["2-1"],
            "case_type": "prediction_case",
            "text": "西甲 | 马竞 vs 塞维利亚",
        }
        selected = _select_top_group(
            [
                (
                    8.6,
                    3.1,
                    1.2,
                    2.4,
                    doc,
                    {
                        "season": "2025-2026",
                        "season_match": True,
                        "season_distance": 0,
                        "month_gap": 0.8,
                        "season_bonus": 0.35,
                        "time_decay_bonus": 0.234,
                        "temporal_bonus": 0.584,
                    },
                )
            ],
            "prediction_case",
            top_k=3,
            min_score=0.75,
        )

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["season"], "2025-2026")
        self.assertTrue(selected[0]["season_match"])
        self.assertAlmostEqual(selected[0]["season_bonus"], 0.35)
        self.assertAlmostEqual(selected[0]["time_decay_bonus"], 0.234)
        self.assertAlmostEqual(selected[0]["temporal_bonus"], 0.584)

    def test_retrieve_hybrid_context_uses_temporal_as_tie_break_for_prediction_cases(self):
        documents = [
            {
                "match_id": "pred-near",
                "league_code": "la_liga",
                "league_name": "西甲",
                "competition_stage_name": "",
                "competition_bucket": "domestic_league",
                "match_date": "2026-05-10",
                "home_team": "历史主队",
                "away_team": "历史客队",
                "prediction": "主胜",
                "confidence": 0.6,
                "actual_score": "1-0",
                "actual_result": "主胜",
                "storage_mode": "archive",
                "risk_points": [],
                "predicted_ou_direction": "",
                "ou_line": 2.5,
                "predicted_scores": ["1-0"],
                "case_type": "prediction_case",
                "text": "西甲 历史主队 历史客队",
                "terms": ["西甲", "历史主队", "历史客队"],
                "term_counts": {"西甲": 1, "历史主队": 1, "历史客队": 1},
                "doc_length": 3,
                "completed": True,
            },
            {
                "match_id": "pred-far",
                "league_code": "la_liga",
                "league_name": "西甲",
                "competition_stage_name": "",
                "competition_bucket": "domestic_league",
                "match_date": "2024-05-10",
                "home_team": "历史主队",
                "away_team": "历史客队",
                "prediction": "主胜",
                "confidence": 0.6,
                "actual_score": "1-0",
                "actual_result": "主胜",
                "storage_mode": "archive",
                "risk_points": [],
                "predicted_ou_direction": "",
                "ou_line": 2.5,
                "predicted_scores": ["1-0"],
                "case_type": "prediction_case",
                "text": "西甲 历史主队 历史客队",
                "terms": ["西甲", "历史主队", "历史客队"],
                "term_counts": {"西甲": 1, "历史主队": 1, "历史客队": 1},
                "doc_length": 3,
                "completed": True,
            },
            {
                "match_id": "market-near",
                "league_code": "la_liga",
                "league_name": "西甲",
                "competition_stage_name": "",
                "competition_bucket": "domestic_league",
                "match_date": "2026-05-10",
                "home_team": "历史主队",
                "away_team": "历史客队",
                "prediction": "",
                "confidence": None,
                "actual_score": "1-0",
                "actual_result": "主胜",
                "storage_mode": "archive",
                "risk_points": [],
                "predicted_ou_direction": "",
                "ou_line": 2.5,
                "predicted_scores": [],
                "case_type": "market_case",
                "text": "西甲 历史主队 历史客队",
                "terms": ["西甲", "历史主队", "历史客队"],
                "term_counts": {"西甲": 1, "历史主队": 1, "历史客队": 1},
                "doc_length": 3,
                "completed": True,
            },
        ]
        index_payload = {
            "document_count": len(documents),
            "avgdl": 3.0,
            "document_frequencies": {"西甲": 3, "历史主队": 3, "历史客队": 3},
        }
        with patch("runtime.rag_store.load_rag_cases", return_value={"cases": documents}), patch(
            "runtime.rag_store.load_rag_index", return_value=index_payload
        ):
            result = retrieve_hybrid_context(
                None,
                league_code="la_liga",
                home_team="历史主队",
                away_team="历史客队",
                market_snapshot={},
                match_date="2026-05-15",
                top_k=3,
                min_score=0.0,
            )

        self.assertEqual(result["similar_cases"][0]["match_id"], "pred-near")
        self.assertEqual(result["similar_cases"][1]["match_id"], "pred-far")
        self.assertAlmostEqual(
            result["similar_cases"][0]["bm25_score"]
            + result["similar_cases"][0]["market_bonus"]
            + result["similar_cases"][0]["structured_bonus"],
            result["similar_cases"][1]["bm25_score"]
            + result["similar_cases"][1]["market_bonus"]
            + result["similar_cases"][1]["structured_bonus"],
        )
        self.assertGreater(result["similar_cases"][0]["temporal_bonus"], result["similar_cases"][1]["temporal_bonus"])
        self.assertEqual(result["market_cases"][0]["match_id"], "market-near")
        self.assertGreater(result["market_cases"][0]["temporal_bonus"], 0.0)


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

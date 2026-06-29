"""okooo 阵容(form.php) 首发身价 + 缺阵 → λ 调整链路回归测试。

覆盖两段：
1. extract_current_odds 把快照「阵容」块归一化并透传（found→available）。
2. MatchIntelligenceEngine._derive_lineup_edge 的单调性、截断、缺失兜底。
"""
import unittest

from okooo_live_snapshot import extract_current_odds
from domain.intelligence import MatchIntelligenceEngine as E
from scripts.build_world_cup_daily_html import _validated_lineup


def _lineup(hv, av, hin=0, ain=0, hiv=0, aiv=0, found=True):
    return {
        "found": found,
        "home_starting_value_wan": hv,
        "away_starting_value_wan": av,
        "home_injury_count": hin,
        "away_injury_count": ain,
        "home_injury_value_wan": hiv,
        "away_injury_value_wan": aiv,
        "home_starting_xi": [{"number": 1, "name": "甲", "position": "门将", "value_wan": 100}],
        "away_starting_xi": [{"number": 1, "name": "乙", "position": "门将", "value_wan": 50}],
    }


class LineupTransmitTest(unittest.TestCase):
    def test_extract_current_odds_transmits_lineup(self):
        snap = {"match_id": "1", "阵容": _lineup(8937.5, 1345, ain=2, aiv=160)}
        out = extract_current_odds(snap)
        self.assertIn("阵容", out)
        ln = out["阵容"]
        self.assertTrue(ln.get("available"))
        self.assertEqual(ln.get("home_starting_value_wan"), 8937.5)
        self.assertEqual(ln.get("away_injury_count"), 2)
        self.assertEqual(len(ln.get("home_starting_xi")), 1)

    def test_extract_current_odds_skips_unfound_lineup(self):
        snap = {"match_id": "1", "阵容": {"found": False}}
        out = extract_current_odds(snap)
        self.assertEqual(out.get("阵容"), {})

    def test_extract_current_odds_missing_lineup_is_empty(self):
        out = extract_current_odds({"match_id": "1"})
        self.assertEqual(out.get("阵容"), {})

    def test_world_cup_lineup_completes_partial_starting_xi_to_eleven(self):
        partial_home = [
            {"number": 1, "name": "诺伊尔", "position": "门将", "value_wan": 400},
            {"number": 2, "name": "吕迪格", "position": "后卫", "value_wan": 900},
            {"number": 6, "name": "基米希", "position": "后卫", "value_wan": 4000},
            {"number": 7, "name": "格纳布里", "position": "前锋", "value_wan": 2000},
        ]
        partial_away = [
            {"number": 2, "name": "Gustavo Velásquez", "position": "后卫", "value_wan": 40},
            {"number": 3, "name": "奥马尔·阿尔德雷特", "position": "后卫", "value_wan": 1500},
            {"number": 4, "name": "Juan Cáceres", "position": "后卫", "value_wan": 500},
            {"number": 7, "name": "米格爾·艾馬朗", "position": "中场", "value_wan": 800},
        ]
        d = {
            "home_team": "德国",
            "away_team": "巴拉圭",
            "market_snapshot": {
                "阵容": {
                    "available": True,
                    "home_starting_xi": partial_home,
                    "away_starting_xi": partial_away,
                }
            },
        }
        lineup, meta = _validated_lineup(d)
        self.assertEqual(len(lineup.get("home_starting_xi") or []), 11)
        self.assertEqual(len(lineup.get("away_starting_xi") or []), 11)
        self.assertTrue(meta.get("home_completed"))
        self.assertTrue(meta.get("away_completed"))

    def test_world_cup_lineup_does_not_fake_missing_lineup_page(self):
        d = {"home_team": "德国", "away_team": "巴拉圭", "market_snapshot": {"阵容": {}}}
        lineup, meta = _validated_lineup(d)
        self.assertEqual(lineup, {})
        self.assertFalse(meta.get("home_fallback"))
        self.assertFalse(meta.get("away_fallback"))


class LineupEdgeTest(unittest.TestCase):
    def test_equal_value_no_edge(self):
        e = E._derive_lineup_edge({"阵容": _lineup(2000, 2000)})
        self.assertTrue(e["available"])
        self.assertEqual(e["home_edge"], 0.0)
        self.assertEqual(e["away_edge"], -0.0)

    def test_home_stronger_positive_edge(self):
        e = E._derive_lineup_edge({"阵容": _lineup(5000, 1000)})
        self.assertGreater(e["home_edge"], 0.0)
        self.assertAlmostEqual(e["home_edge"], -e["away_edge"], places=4)

    def test_away_stronger_negative_edge(self):
        e = E._derive_lineup_edge({"阵容": _lineup(1000, 5000)})
        self.assertLess(e["home_edge"], 0.0)

    def test_value_edge_monotonic_and_clipped(self):
        small = E._derive_lineup_edge({"阵容": _lineup(1200, 1000)})["value_edge"]
        big = E._derive_lineup_edge({"阵容": _lineup(5000, 1000)})["value_edge"]
        extreme = E._derive_lineup_edge({"阵容": _lineup(100000, 1)})["value_edge"]
        self.assertLess(small, big)
        self.assertLessEqual(extreme, 0.06)  # 身价分量上限
        self.assertGreaterEqual(extreme, -0.06)

    def test_injury_favors_opponent(self):
        # 客队缺阵 → 利好主队（home_edge 比无缺阵更高）
        base = E._derive_lineup_edge({"阵容": _lineup(2000, 2000)})["home_edge"]
        away_hurt = E._derive_lineup_edge({"阵容": _lineup(2000, 2000, ain=3, aiv=600)})["home_edge"]
        self.assertGreater(away_hurt, base)
        # 主队缺阵 → 利好客队
        home_hurt = E._derive_lineup_edge({"阵容": _lineup(2000, 2000, hin=3, hiv=600)})["home_edge"]
        self.assertLess(home_hurt, base)

    def test_injury_edge_clipped(self):
        e = E._derive_lineup_edge({"阵容": _lineup(2000, 2000, ain=11, aiv=99999)})
        self.assertLessEqual(e["injury_edge"], 0.05)

    def test_unavailable_lineup_no_edge(self):
        self.assertFalse(E._derive_lineup_edge({"阵容": {"available": False}})["available"])
        # found=True 的原始快照口径也应被识别为可用
        self.assertTrue(E._derive_lineup_edge({"阵容": _lineup(2000, 2000, found=True)})["available"])

    def test_missing_or_zero_values_safe(self):
        self.assertFalse(E._derive_lineup_edge({})["available"])
        self.assertFalse(E._derive_lineup_edge(None)["available"])
        self.assertFalse(E._derive_lineup_edge({"阵容": _lineup(0, 1000)})["available"])

    def test_real_czech_south_africa_edge(self):
        # 真实快照口径：捷克 8937.5万 vs 南非 1345万 + 南非缺阵2人/160万
        e = E._derive_lineup_edge({"阵容": _lineup(8937.5, 1345, ain=2, aiv=160)})
        self.assertTrue(e["available"])
        self.assertEqual(e["value_edge"], 0.06)        # 6.6x 打满上限
        self.assertGreater(e["injury_edge"], 0.0)       # 南非缺阵利好捷克
        self.assertGreater(e["home_edge"], 0.06)        # 身价 + 缺阵叠加


class FavColdDrawRiskTest(unittest.TestCase):
    """升盘升水（庄家加深让球却抬热门水位）→ 独立平局风险标志，不被净额对冲抹平。"""

    @staticmethod
    def _odds(h_i, h_f, hcp_i, hcp_f, hw_i, hw_f, aw_i, aw_f):
        return {
            "欧赔": {"initial": {"home": h_i, "draw": 4.0, "away": 5.0},
                     "final": {"home": h_f, "draw": 4.2, "away": 6.0}},
            "亚值": {"initial": {"handicap_value": hcp_i, "home_water": hw_i, "away_water": aw_i},
                     "final": {"handicap_value": hcp_f, "home_water": hw_f, "away_water": aw_f}},
        }

    def test_deepen_with_rising_fav_water_flags_cold_draw(self):
        # 比利时vs埃及形态：欧赔收紧(1.62->1.51) + 盘口加深(-1->-1.25) + 主水反升(2.00->2.24)
        odds = self._odds(1.62, 1.51, -1.0, -1.25, 2.00, 2.24, 1.83, 1.73)
        bp = E()._build_market_intelligence(odds)["bookmaker_psychology"]
        self.assertGreater(bp["fav_cold_draw_risk"], 0.0)

    def test_deepen_without_water_rise_no_flag(self):
        # 盘口加深但主水下降（庄家真信热门）→ 不应触发冷平标志
        odds = self._odds(1.62, 1.51, -1.0, -1.25, 2.10, 1.90, 1.83, 1.95)
        bp = E()._build_market_intelligence(odds)["bookmaker_psychology"]
        self.assertEqual(bp["fav_cold_draw_risk"], 0.0)

    def test_no_deepen_no_flag(self):
        # 盘口未加深 → 不触发
        odds = self._odds(1.62, 1.51, -1.0, -1.0, 2.00, 2.24, 1.83, 1.73)
        bp = E()._build_market_intelligence(odds)["bookmaker_psychology"]
        self.assertEqual(bp["fav_cold_draw_risk"], 0.0)

    def test_flag_lifts_draw_probability_without_flipping_argmax(self):
        # 软提升端到端：触发升盘升水时平局概率应高于无该信号的同强度对照，
        # 但不把热门主胜翻成平局（argmax 不变）。
        import tempfile
        from enhanced_prediction_workflow import EnhancedPredictor

        predictor = EnhancedPredictor(base_dir=tempfile.mkdtemp())
        trap = self._odds(1.62, 1.51, -1.0, -1.25, 2.00, 2.24, 1.83, 1.73)
        # 对照：同样欧赔收紧但盘口不加深、主水不升（庄家真信热门）
        calm = self._odds(1.62, 1.51, -1.0, -1.0, 2.10, 1.95, 1.83, 1.90)

        def draw_p(odds):
            r = predictor.predict_match(
                home_team="AAA", away_team="BBB", league_code="world_cup",
                match_date="2026-06-15", current_odds=odds, match_id="t",
                force_refresh_odds=False, persist=False,
            )
            return r["final_probabilities"]

        fp_trap = draw_p(trap)
        fp_calm = draw_p(calm)
        self.assertGreater(fp_trap["draw"], fp_calm["draw"])
        # 软提升不应把主胜热门翻成平局
        self.assertGreaterEqual(fp_trap["home_win"], fp_trap["draw"])


if __name__ == "__main__":
    unittest.main()

"""全角/半角标点与 alias 归一化的回归测试。

覆盖：
1. `normalize_team_name` 对 canonical / alias / 全角括号变体 / 半角括号变体的归一化行为
2. 反例：非 alias 的相似队名不误伤（如"刚果共和国" ≠ "刚果民主共和国"）
3. `_teams_names_match`（sync 队名比对）对全角/半角括号的两侧对齐
"""

import unittest

from collectors.aliasing import load_team_alias_map, normalize_team_name
from runtime.result_sync import _teams_names_match


class TeamAliasSymbolNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alias_map = load_team_alias_map()

    def test_full_width_paren_normalizes_to_canonical(self):
        self.assertEqual(
            normalize_team_name("world_cup", "刚果（金）", self.alias_map),
            "刚果民主共和国",
        )

    def test_half_width_paren_alias_normalizes(self):
        self.assertEqual(
            normalize_team_name("world_cup", "刚果(金)", self.alias_map),
            "刚果民主共和国",
        )

    def test_pure_alias_normalizes(self):
        for alias in ["民主刚果", "刚果金", "DR刚果", "刚果民主"]:
            with self.subTest(alias=alias):
                self.assertEqual(
                    normalize_team_name("world_cup", alias, self.alias_map),
                    "刚果民主共和国",
                )

    def test_canonical_stays_canonical(self):
        self.assertEqual(
            normalize_team_name("world_cup", "刚果民主共和国", self.alias_map),
            "刚果民主共和国",
        )

    def test_non_alias_similar_name_is_not_hijacked(self):
        # 不能把「刚果共和国」（另一个国家）误归到「刚果民主共和国」
        self.assertEqual(
            normalize_team_name("world_cup", "刚果共和国", self.alias_map),
            "刚果共和国",
        )

    def test_unknown_team_is_returned_as_is(self):
        for cand in ["巴西", "英格兰", "阿根廷"]:
            with self.subTest(cand=cand):
                self.assertEqual(
                    normalize_team_name("world_cup", cand, self.alias_map),
                    cand,
                )

    def test_league_alias_key_carries_over(self):
        # 「世界杯」也应能识别（LEAGUE_ALIAS_KEYS 里没世界杯，直接用 raw key）
        self.assertEqual(
            normalize_team_name("世界杯", "刚果（金）", self.alias_map),
            "刚果民主共和国",
        )


class TeamsNamesMatchSymbolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alias_map = load_team_alias_map()

    def test_full_width_vs_canonical_match(self):
        self.assertTrue(
            _teams_names_match(
                "world_cup", "刚果（金）", "刚果民主共和国", alias_map=self.alias_map
            )
        )

    def test_half_width_vs_canonical_match(self):
        self.assertTrue(
            _teams_names_match(
                "world_cup", "刚果(金)", "刚果民主共和国", alias_map=self.alias_map
            )
        )

    def test_full_width_vs_half_width_match(self):
        # 两侧一个全角一个半角，也应视为同队
        self.assertTrue(
            _teams_names_match(
                "world_cup", "刚果（金）", "刚果(金)", alias_map=self.alias_map
            )
        )

    def test_alias_vs_full_width_match(self):
        self.assertTrue(
            _teams_names_match(
                "world_cup", "民主刚果", "刚果（金）", alias_map=self.alias_map
            )
        )

    def test_different_country_does_not_match(self):
        # 刚果共和国 ≠ 刚果民主共和国
        self.assertFalse(
            _teams_names_match(
                "world_cup", "刚果共和国", "刚果民主共和国", alias_map=self.alias_map
            )
        )

    def test_unrelated_teams_do_not_match(self):
        self.assertFalse(
            _teams_names_match(
                "world_cup", "巴西", "阿根廷", alias_map=self.alias_map
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

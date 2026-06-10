import runtime.paths  # noqa: F401  确保按 app 顺序初始化，规避 storage 包循环导入
import tempfile
import unittest

from storage.ratings import RatingService


class RatingServiceTests(unittest.TestCase):
    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.svc = RatingService(self.base_dir)

    def test_empty_library_returns_none(self):
        # 空库必须返回 None，保证预测侧回退 strength 派生（SoT 零回归）。
        self.assertIsNone(self.svc.get_ratings('friendly', '中国'))

    def test_home_win_moves_ratings_in_opposite_directions(self):
        out = self.svc.update_from_result(
            'friendly', '中国', '泰国', 2, 1,
            home_seed=1490.0, away_seed=1470.0, match_date='2026-06-10',
        )
        # 主队赢：elo 上升超过播种值，客队下降。
        self.assertGreater(out['home']['elo'], 1490.0)
        self.assertLess(out['away']['elo'], 1470.0)
        self.assertEqual(out['home']['games'], 1)
        self.assertEqual(out['away']['games'], 1)

    def test_persisted_ratings_reload(self):
        self.svc.update_from_result(
            'friendly', '中国', '泰国', 2, 1,
            home_seed=1490.0, away_seed=1470.0, match_date='2026-06-10',
        )
        reloaded = RatingService(self.base_dir)
        cn = reloaded.get_ratings('friendly', '中国')
        self.assertIsNotNone(cn)
        self.assertEqual(cn['games'], 1)
        self.assertIn('glicko', cn)

    def test_draw_keeps_ratings_close_to_seed(self):
        out = self.svc.update_from_result(
            'friendly', 'A', 'B', 1, 1,
            home_seed=1500.0, away_seed=1500.0,
        )
        # 平局且实力相同：主场优势使期望>0.5，主队略降、客队略升，但幅度很小。
        self.assertLess(abs(out['home']['elo'] - 1500.0), 16)
        self.assertLess(abs(out['away']['elo'] - 1500.0), 16)

    def test_accumulated_games_increment(self):
        self.svc.update_from_result('friendly', 'A', 'B', 2, 0, 1500.0, 1500.0)
        self.svc.update_from_result('friendly', 'B', 'A', 1, 1, 1500.0, 1500.0)
        a = self.svc.get_ratings('friendly', 'A')
        self.assertEqual(a['games'], 2)

    def test_duplicate_match_id_is_idempotent(self):
        # 同一 match_id 重复回填只生效一次，避免评分/局数重复累加。
        first = self.svc.update_from_result(
            'friendly', 'A', 'B', 2, 0, 1500.0, 1500.0, match_id='m1')
        elo_after_first = first['home']['elo']
        second = self.svc.update_from_result(
            'friendly', 'A', 'B', 2, 0, 1500.0, 1500.0, match_id='m1')
        self.assertTrue(second.get('skipped'))
        a = self.svc.get_ratings('friendly', 'A')
        self.assertEqual(a['games'], 1)
        self.assertEqual(a['elo'], elo_after_first)

    def test_processed_matches_meta_not_treated_as_league(self):
        # _processed_matches 元数据键不应污染 get_ratings 的联赛查询。
        self.svc.update_from_result('friendly', 'A', 'B', 2, 0, 1500.0, 1500.0, match_id='m1')
        self.assertIsNone(self.svc.get_ratings('_processed_matches', 'm1'))

    def test_cross_instance_cache_invalidation(self):
        # 多实例共享同一文件：A 实例落盘后，B 实例（已建立缓存）应感知变更并重载。
        reader = RatingService(self.base_dir)
        self.assertIsNone(reader.get_ratings('friendly', 'A'))  # 建立空缓存
        writer = RatingService(self.base_dir)
        writer.update_from_result('friendly', 'A', 'B', 2, 0, 1500.0, 1500.0)
        a = reader.get_ratings('friendly', 'A')
        self.assertIsNotNone(a)
        self.assertEqual(a['games'], 1)


if __name__ == '__main__':
    unittest.main()

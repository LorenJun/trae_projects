"""端到端特征测试：固化 EnhancedPredictor.predict_match 主链行为。

目的：predict_match → InferencePipelineService.run() 是约 500 行的预测主链，
本测试在离线模式（force_refresh_odds=False、persist=False）下固化其关键不变量，
为后续巨石拆分/重构提供回归护栏。不断言精确数值（依赖实力派生），
只断言结构与量纲不变量，避免脆弱。
"""

import tempfile
import unittest

from enhanced_prediction_workflow import EnhancedPredictor


class PredictMatchE2ETests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.predictor = EnhancedPredictor(base_dir=tempfile.mkdtemp())

    def _predict(self, home='AAA', away='BBB', league='premier_league'):
        return self.predictor.predict_match(
            home_team=home, away_team=away, league_code=league,
            match_date='2026-06-15', current_odds=None, match_id='e2e',
            force_refresh_odds=False, persist=False,
        )

    def test_returns_normalized_final_probabilities(self):
        r = self._predict()
        fp = r['final_probabilities']
        self.assertAlmostEqual(fp['home_win'] + fp['draw'] + fp['away_win'], 1.0, places=6)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in fp.values()))

    def test_prediction_matches_argmax_probability(self):
        r = self._predict()
        fp = r['final_probabilities']
        label_map = {'home_win': '主胜', 'draw': '平局', 'away_win': '客胜'}
        argmax = label_map[max(fp, key=fp.get)]
        self.assertEqual(r['prediction'], argmax)

    def test_expected_goals_in_sane_range_after_lambda_fix(self):
        # λ 量纲修复后单场总进球应落在合理区间（约 1.5~4.5），而非翻倍虚高。
        r = self._predict()
        eg = r['expected_goals']
        self.assertGreater(eg['total'], 1.5)
        self.assertLess(eg['total'], 4.5)
        self.assertAlmostEqual(eg['home'] + eg['away'], eg['total'], places=4)

    def test_confidence_is_probability(self):
        r = self._predict()
        self.assertGreaterEqual(r['confidence'], 0.0)
        self.assertLessEqual(r['confidence'], 1.0)

    def test_top_scores_present(self):
        r = self._predict()
        self.assertIn('top_scores', r)
        self.assertTrue(r['top_scores'])


if __name__ == '__main__':
    unittest.main()

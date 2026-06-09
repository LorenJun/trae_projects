import unittest

from ml_prediction_models import MultiModelFusion


def _base_kwargs():
    return dict(
        home_team='A', away_team='B',
        home_strength=70, away_strength=60,
        home_form=3, away_form=3,
        home_injuries=0, away_injuries=0,
        h2h_home_wins=1, h2h_away_wins=1, h2h_draws=1,
        home_motivation=80, away_motivation=75,
        home_xg=1.5, away_xg=1.1,
        home_attack=1.4, home_defense=0.9,
        away_attack=1.1, away_defense=1.0,
    )


class MarketFusionTests(unittest.TestCase):
    def setUp(self):
        self.fusion = MultiModelFusion()

    def test_no_market_probs_degrades_to_model_only(self):
        res = self.fusion.predict(**_base_kwargs())
        self.assertEqual(res['final'], res['model_only'])
        self.assertFalse(res['market_fusion']['applied'])
        self.assertEqual(res['market_fusion']['alpha'], 0.0)

    def test_market_convex_combination_matches_formula(self):
        market = {'home_win': 0.47, 'draw': 0.29, 'away_win': 0.24}
        alpha = 0.4
        res = self.fusion.predict(market_probs=market, market_alpha=alpha, **_base_kwargs())
        self.assertTrue(res['market_fusion']['applied'])
        model = res['market_fusion']['model_probs']
        for key in ('home_win', 'draw', 'away_win'):
            expected = (1 - alpha) * model[key] + alpha * market[key]
            self.assertAlmostEqual(res['final'][key], expected, places=6)

    def test_alpha_zero_skips_market(self):
        market = {'home_win': 0.47, 'draw': 0.29, 'away_win': 0.24}
        res = self.fusion.predict(market_probs=market, market_alpha=0.0, **_base_kwargs())
        self.assertEqual(res['final'], res['model_only'])
        self.assertFalse(res['market_fusion']['applied'])

    def test_final_probabilities_sum_to_one(self):
        market = {'home_win': 0.5, 'draw': 0.3, 'away_win': 0.2}
        res = self.fusion.predict(market_probs=market, market_alpha=0.35, **_base_kwargs())
        self.assertAlmostEqual(sum(res['final'].values()), 1.0, places=6)

    def test_invalid_market_probs_degrades(self):
        res = self.fusion.predict(market_probs={'home_win': 0, 'draw': 0, 'away_win': 0},
                                  market_alpha=0.4, **_base_kwargs())
        self.assertFalse(res['market_fusion']['applied'])
        self.assertEqual(res['final'], res['model_only'])


class ExpertSystemTests(unittest.TestCase):
    def setUp(self):
        self.fusion = MultiModelFusion()

    def test_expert_carries_signals(self):
        res = self.fusion.predict(
            expert_signals={'home_suspensions': 1, 'away_key_available': False, 'intel_home_bias': 0.2},
            **_base_kwargs(),
        )
        expert = res['all_models']['expert']
        self.assertIn('expert_score', expert)
        self.assertIn('expert_factors', expert)
        self.assertAlmostEqual(sum(v for k, v in expert.items()
                                   if k in ('home_win', 'draw', 'away_win')), 1.0, places=6)

    def test_key_absence_shifts_probability_to_opponent(self):
        home_out = self.fusion._expert_system(
            70, 60, 3, 3, 0, 0,
            expert_signals={'home_key_available': False},
        )
        away_out = self.fusion._expert_system(
            70, 60, 3, 3, 0, 0,
            expert_signals={'away_key_available': False},
        )
        # 主队核心缺阵应较客队核心缺阵更不利于主胜
        self.assertLess(home_out['home_win'], away_out['home_win'])


class EnsembleStackingTests(unittest.TestCase):
    def setUp(self):
        self.fusion = MultiModelFusion()

    def test_stacking_flag_set_with_accuracy(self):
        res = self.fusion.predict(
            model_accuracy={'poisson': 0.55, 'elo': 0.6},
            **_base_kwargs(),
        )
        self.assertTrue(res['all_models']['ensemble'].get('stacking_weighted'))

    def test_equal_weight_without_accuracy(self):
        res = self.fusion.predict(**_base_kwargs())
        self.assertFalse(res['all_models']['ensemble'].get('stacking_weighted'))

    def test_ensemble_excludes_self_expert_market(self):
        preds = {
            'poisson': {'home_win': 0.4, 'draw': 0.3, 'away_win': 0.3},
            'expert': {'home_win': 1.0, 'draw': 0.0, 'away_win': 0.0},
            'ensemble': {'home_win': 0.0, 'draw': 0.0, 'away_win': 1.0},
            'market': {'home_win': 0.0, 'draw': 1.0, 'away_win': 0.0},
        }
        out = self.fusion._ensemble_predict(preds)
        # 仅 poisson 应参与，结果等于 poisson
        self.assertAlmostEqual(out['home_win'], 0.4, places=6)
        self.assertAlmostEqual(out['away_win'], 0.3, places=6)


if __name__ == '__main__':
    unittest.main()

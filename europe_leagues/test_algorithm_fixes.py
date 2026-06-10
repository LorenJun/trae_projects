import unittest

from ml_prediction_models import (
    DixonColesModel,
    PoissonModel,
    EloRatingSystem,
    GlickoRatingSystem,
    LogisticRegressionModel,
    MultiModelFusion,
)


class DixonColesRhoTests(unittest.TestCase):
    """#1: ρ<0 应抬高 0-0/1-1 平局、压低 1-0/0-1。"""

    def setUp(self):
        self.indep = PoissonModel(league_avg_goals=1.3)
        self.dc = DixonColesModel(league_avg_goals=1.3, rho=-0.1)

    def test_negative_rho_boosts_nil_nil(self):
        hl, al = 1.2, 1.0
        indep_00 = (self.indep.poisson_probability(hl, 0)
                    * self.indep.poisson_probability(al, 0))
        dc_00 = self.dc.dc_probability(hl, al, 0, 0)
        self.assertGreater(dc_00, indep_00)

    def test_negative_rho_boosts_one_one(self):
        hl, al = 1.2, 1.0
        indep_11 = (self.indep.poisson_probability(hl, 1)
                    * self.indep.poisson_probability(al, 1))
        dc_11 = self.dc.dc_probability(hl, al, 1, 1)
        self.assertGreater(dc_11, indep_11)

    def test_negative_rho_suppresses_one_nil(self):
        hl, al = 1.2, 1.0
        indep_10 = (self.indep.poisson_probability(hl, 1)
                    * self.indep.poisson_probability(al, 0))
        dc_10 = self.dc.dc_probability(hl, al, 1, 0)
        self.assertLess(dc_10, indep_10)

    def test_grid_is_normalized_and_nonnegative(self):
        out = self.dc.predict_with_dixon_coles(2.0, 1.6)
        self.assertAlmostEqual(
            out['home_win'] + out['draw'] + out['away_win'], 1.0, places=6)
        self.assertTrue(all(p >= 0 for p in out['score_probs'].values()))


class PoissonNormalizationTests(unittest.TestCase):
    """#3: predict_score_probability 三者之和应为 1。"""

    def test_score_probability_normalized(self):
        out = PoissonModel(league_avg_goals=1.4).predict_score_probability(1.6, 1.2)
        self.assertAlmostEqual(
            out['home_win'] + out['draw'] + out['away_win'], 1.0, places=6)


class PoissonBaselineTests(unittest.TestCase):
    """#2: 传入单队基准时融合内部 λ 不应翻倍。"""

    def test_per_team_baseline_yields_reasonable_total(self):
        # 修正后单队基准≈1.35，合计 λ 应在 2.4~3.2，而非旧口径(2.5整场当单队)的≈5.6
        pm = PoissonModel(league_avg_goals=1.35)
        hl, al = pm.calculate_expected_goals(1.0, 1.0, 1.0, 1.0, home_advantage=1.03)
        self.assertLess(hl + al, 3.2)
        self.assertGreater(hl + al, 2.4)

    def test_fusion_accepts_baseline_without_error(self):
        fusion = MultiModelFusion()
        out = fusion.predict(
            home_team='A', away_team='B',
            home_strength=55, away_strength=52,
            home_form=3, away_form=3,
            home_injuries=0, away_injuries=0,
            h2h_home_wins=1, h2h_away_wins=1, h2h_draws=1,
            home_motivation=75, away_motivation=75,
            home_xg=1.2, away_xg=1.1,
            home_attack=1.0, home_defense=1.0,
            away_attack=1.0, away_defense=1.0,
            per_team_baseline=1.35, home_advantage=1.03,
        )
        self.assertAlmostEqual(sum(out['final'].values()), 1.0, places=6)


class LogisticInjuryTests(unittest.TestCase):
    """#4: 主队伤停越多，主胜概率应下降。"""

    def test_home_injuries_reduce_home_win(self):
        lr = LogisticRegressionModel()
        base = lr.predict(70, 60, 3, 3, 0, 0, 1, 1, 1, 80, 75)
        injured = lr.predict(70, 60, 3, 3, 3, 0, 1, 1, 1, 80, 75)
        self.assertLess(injured['home_win'], base['home_win'])


class GlickoDistinctTests(unittest.TestCase):
    """#5: Glicko 应因 RD 收缩而与 Elo 输出不同（更向 0.5 回归）。"""

    def test_glicko_shrinks_toward_draw_vs_elo(self):
        elo = EloRatingSystem()
        glicko = GlickoRatingSystem(rd_constant=150)
        for sys_ in (elo, glicko):
            sys_.set_rating('H', 1700)
            sys_.set_rating('A', 1500)
        e = elo.predict_match('H', 'A')
        g = glicko.predict_match('H', 'A')
        # 同样的评级差，Glicko 的主胜概率应更保守（更接近 0.5）
        self.assertLess(g['home_win'], e['home_win'])


class QuarterLineTests(unittest.TestCase):
    """亚盘四分线（2.25/2.75）应取相邻两线的平均，而非被当成整数/半盘误算。"""

    def setUp(self):
        self.pm = PoissonModel(league_avg_goals=1.4)

    def test_quarter_line_is_average_of_adjacent_lines(self):
        hl, al = 1.5, 1.3
        q = self.pm.predict_over_under(hl, al, 2.25)
        lo = self.pm.predict_over_under(hl, al, 2.0)
        hi = self.pm.predict_over_under(hl, al, 2.5)
        self.assertAlmostEqual(q['over'], (lo['over'] + hi['over']) / 2.0, places=6)
        self.assertAlmostEqual(q['under'], (lo['under'] + hi['under']) / 2.0, places=6)

    def test_quarter_line_probs_sum_to_one(self):
        out = self.pm.predict_over_under(1.5, 1.3, 2.75)
        self.assertAlmostEqual(out['over'] + out['under'], 1.0, places=6)

    def test_quarter_line_monotonic_vs_half_lines(self):
        # 2.75 的大球概率应介于 2.5 与 3.0 之间
        hl, al = 1.6, 1.4
        p25 = self.pm.predict_over_under(hl, al, 2.5)['over']
        p275 = self.pm.predict_over_under(hl, al, 2.75)['over']
        p30 = self.pm.predict_over_under(hl, al, 3.0)['over']
        self.assertLessEqual(p275, p25)
        self.assertGreaterEqual(p275, p30)


if __name__ == '__main__':
    unittest.main()

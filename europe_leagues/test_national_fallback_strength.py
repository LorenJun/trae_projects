import os
import unittest

from domain.team_strength import TeamStrengthService


class NationalFallbackStrengthTests(unittest.TestCase):
    def setUp(self):
        self.svc = TeamStrengthService()

    def test_known_national_team_uses_fallback_not_flat_default(self):
        # 阿根廷在国家队兜底表里应得到差异化高强度，而非默认 50。
        res = self.svc.analyze_team_strength('friendly', '阿根廷')
        self.assertEqual(res['strength_source'], 'national_fallback')
        self.assertGreater(res['strength'], 80)
        self.assertGreater(res['attack'], 1.2)

    def test_weak_national_team_lower_than_strong(self):
        strong = self.svc.analyze_team_strength('friendly', '阿根廷')
        weak = self.svc.analyze_team_strength('friendly', '泰国')
        self.assertGreater(strong['strength'], weak['strength'])
        self.assertGreater(strong['attack'], weak['attack'])

    def test_two_unknown_teams_are_not_identical(self):
        # 之前所有未知球队都退回同一组默认值导致预测趋同；现在应区分开。
        a = self.svc.analyze_team_strength('friendly', '中国')
        b = self.svc.analyze_team_strength('friendly', '伊拉克')
        self.assertNotEqual(a['strength'], b['strength'])

    def test_truly_unknown_team_falls_back_to_flat_default(self):
        res = self.svc.analyze_team_strength('friendly', '不存在的队XYZ')
        self.assertEqual(res['strength_source'], 'flat_default')
        self.assertEqual(res['strength'], 50.0)

    def test_national_strength_table_exists(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'national_team_strength.json')
        self.assertTrue(os.path.exists(path))


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""
机器学习预测模型
基于多模型融合提高预测准确性
"""

import os
import json
import math
from datetime import datetime
from typing import Dict, List, Tuple, Optional
import logging

logging.basicConfig(level=logging.INFO, filename='ml_prediction.log')

# ==================== 统计概率模型 ====================

class PoissonModel:
    """泊松分布模型 - 用于大小球分析和比分预测"""

    def __init__(self, league_avg_goals: float = 2.5):
        self.league_avg_goals = league_avg_goals

    def calculate_expected_goals(
        self,
        home_attack: float,
        home_defense: float,
        away_attack: float,
        away_defense: float,
        home_advantage: float = 1.12
    ) -> Tuple[float, float]:
        """
        计算预期进球数
        :return: (主队预期进球, 客队预期进球)
        """
        home_lambda = home_attack * away_defense * self.league_avg_goals * home_advantage
        away_lambda = away_attack * home_defense * self.league_avg_goals
        return (home_lambda, away_lambda)

    def poisson_probability(self, lambda_rate: float, k: int) -> float:
        """计算泊松分布概率 P(X=k)"""
        return (lambda_rate ** k * math.exp(-lambda_rate)) / math.factorial(k)

    def predict_over_under(
        self,
        home_lambda: float,
        away_lambda: float,
        line: float = 2.5
    ) -> Dict[str, float]:
        """预测大小球"""
        total_lambda = home_lambda + away_lambda

        over_prob = sum(
            self.poisson_probability(total_lambda, k)
            for k in range(int(line) + 1, 10)
        )
        under_prob = 1 - over_prob

        return {
            'over': over_prob,
            'under': under_prob,
            'total_lambda': total_lambda,
            'line': line
        }

    def predict_score_probability(
        self,
        home_lambda: float,
        away_lambda: float,
        max_goals: int = 5
    ) -> Dict[str, float]:
        """预测比分概率"""
        score_probs = {}

        for home_goals in range(max_goals + 1):
            for away_goals in range(max_goals + 1):
                home_prob = self.poisson_probability(home_lambda, home_goals)
                away_prob = self.poisson_probability(away_lambda, away_goals)
                score_probs[f"{home_goals}-{away_goals}"] = home_prob * away_prob

        # 计算胜负平概率
        win_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) > int(score.split('-')[1])
        )
        draw_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) == int(score.split('-')[1])
        )
        lose_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) < int(score.split('-')[1])
        )

        return {
            'score_probs': score_probs,
            'home_win': win_prob,
            'draw': draw_prob,
            'away_win': lose_prob
        }


class DixonColesModel(PoissonModel):
    """Dixon-Coles模型 - 修正的泊松分布，考虑进球相关性"""

    def __init__(self, league_avg_goals: float = 2.5, rho: float = -0.1):
        super().__init__(league_avg_goals)
        self.rho = rho  # 进球相关系数（通常为负值，表示0-0和1-1等低比分关联）

    def dc_probability(
        self,
        home_lambda: float,
        away_lambda: float,
        home_goals: int,
        away_goals: int
    ) -> float:
        """计算Dixon-Coles修正后的概率"""
        # 基础泊松概率
        home_base = self.poisson_probability(home_lambda, home_goals)
        away_base = self.poisson_probability(away_lambda, away_goals)

        # 相关系数修正
        if home_goals == 0 and away_goals == 0:
            rho_adjustment = 1 + self.rho
        elif home_goals == 1 and away_goals == 1:
            rho_adjustment = 1 + self.rho * 0.5
        elif home_goals == 0 or away_goals == 0:
            rho_adjustment = 1 - self.rho * 0.5
        else:
            rho_adjustment = 1

        return home_base * away_base * rho_adjustment

    def predict_with_dixon_coles(
        self,
        home_lambda: float,
        away_lambda: float,
        max_goals: int = 5
    ) -> Dict[str, float]:
        """使用Dixon-Coles模型预测"""
        score_probs = {}

        for home_goals in range(max_goals + 1):
            for away_goals in range(max_goals + 1):
                prob = self.dc_probability(home_lambda, away_lambda, home_goals, away_goals)
                score_probs[f"{home_goals}-{away_goals}"] = prob

        # 归一化
        total_prob = sum(score_probs.values())
        for score in score_probs:
            score_probs[score] /= total_prob

        # 计算胜负平概率
        win_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) > int(score.split('-')[1])
        )
        draw_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) == int(score.split('-')[1])
        )
        lose_prob = sum(
            prob for score, prob in score_probs.items()
            if int(score.split('-')[0]) < int(score.split('-')[1])
        )

        return {
            'score_probs': score_probs,
            'home_win': win_prob,
            'draw': draw_prob,
            'away_win': lose_prob,
            'model': 'Dixon-Coles'
        }


# ==================== 评级系统模型 ====================

class EloRatingSystem:
    """Elo评级系统"""

    def __init__(self, k_factor: int = 32, home_advantage: int = 100):
        self.k_factor = k_factor
        self.home_advantage = home_advantage
        self.ratings: Dict[str, float] = {}

    def get_rating(self, team: str) -> float:
        """获取球队评级"""
        return self.ratings.get(team, 1500)  # 默认1500

    def set_rating(self, team: str, rating: float):
        """设置球队评级"""
        self.ratings[team] = rating

    def update_ratings(
        self,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int
    ):
        """更新评级"""
        home_rating = self.get_rating(home_team)
        away_rating = self.get_rating(away_team)

        # 计算预期得分
        home_expected = 1 / (1 + 10 ** ((away_rating + self.home_advantage - home_rating) / 400))
        away_expected = 1 / (1 + 10 ** ((home_rating - self.home_advantage - away_rating) / 400))

        # 确定实际得分
        if home_goals > away_goals:
            home_actual, away_actual = 1, 0
        elif home_goals < away_goals:
            home_actual, away_actual = 0, 1
        else:
            home_actual, away_actual = 0.5, 0.5

        # 更新评级
        home_new = home_rating + self.k_factor * (home_actual - home_expected)
        away_new = away_rating + self.k_factor * (away_actual - away_expected)

        self.set_rating(home_team, home_new)
        self.set_rating(away_team, away_new)

    def predict_match(self, home_team: str, away_team: str) -> Dict[str, float]:
        """预测比赛"""
        home_rating = self.get_rating(home_team) + self.home_advantage
        away_rating = self.get_rating(away_team)

        # 计算预期得分
        home_win_prob = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        away_win_prob = 1 / (1 + 10 ** ((home_rating - away_rating) / 400))
        draw_prob = 1 - home_win_prob - away_win_prob

        return {
            'home_win': home_win_prob,
            'draw': draw_prob,
            'away_win': away_win_prob,
            'home_rating': self.get_rating(home_team),
            'away_rating': self.get_rating(away_team)
        }


class GlickoRatingSystem(EloRatingSystem):
    """Glicko评级系统 - 更精准的评级"""

    def __init__(self, rd_constant: int = 150, vol_constant: float = 0.06):
        super().__init__()
        self.rd_constant = rd_constant  # 评级偏差
        self.vol_constant = vol_constant  # 波动性

    def predict_match(self, home_team: str, away_team: str) -> Dict[str, float]:
        """预测比赛（考虑评级偏差）"""
        home_rating = self.get_rating(home_team) + self.home_advantage
        away_rating = self.get_rating(away_team)

        # 计算预期得分（简化版本）
        home_win_prob = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        away_win_prob = 1 / (1 + 10 ** ((home_rating - away_rating) / 400))
        draw_prob = 1 - home_win_prob - away_win_prob

        return {
            'home_win': home_win_prob,
            'draw': draw_prob,
            'away_win': away_win_prob,
            'home_rating': self.get_rating(home_team),
            'away_rating': self.get_rating(away_team),
            'model': 'Glicko'
        }


# ==================== 机器学习模型 ====================

class LogisticRegressionModel:
    """逻辑回归模型"""

    def __init__(self, weights: Dict[str, float] = None):
        # 特征权重
        self.weights = weights or {
            'strength_diff': 0.3,
            'home_advantage': 0.15,
            'form': 0.2,
            'injuries': -0.15,
            'head_to_head': 0.1,
            'motivation': 0.1
        }

    def predict(
        self,
        home_strength: float,
        away_strength: float,
        home_form: float,
        away_form: float,
        home_injuries: int,
        away_injuries: int,
        h2h_home_wins: int,
        h2h_away_wins: int,
        h2h_draws: int,
        home_motivation: float,
        away_motivation: float
    ) -> Dict[str, float]:
        """预测比赛结果"""

        # 计算特征
        strength_diff = (home_strength - away_strength) / 100
        form_diff = (home_form - away_form) / 100
        injury_impact = -(home_injuries - away_injuries) * 0.02

        total_h2h = h2h_home_wins + h2h_away_wins + h2h_draws
        if total_h2h > 0:
            h2h_advantage = (h2h_home_wins - h2h_away_wins) / total_h2h
        else:
            h2h_advantage = 0

        motivation_diff = (home_motivation - away_motivation) / 100

        # 计算加权和
        home_score = (
            self.weights['strength_diff'] * strength_diff +
            self.weights['home_advantage'] +
            self.weights['form'] * form_diff +
            self.weights['injuries'] * injury_impact +
            self.weights['head_to_head'] * h2h_advantage +
            self.weights['motivation'] * motivation_diff
        )

        # 转换为概率
        home_win_prob = 1 / (1 + math.exp(-home_score * 5))
        away_win_prob = 1 / (1 + math.exp(home_score * 5))
        draw_prob = 1 - home_win_prob - away_win_prob

        # 归一化
        total = home_win_prob + away_win_prob + draw_prob
        home_win_prob /= total
        away_win_prob /= total
        draw_prob /= total

        return {
            'home_win': home_win_prob,
            'draw': draw_prob,
            'away_win': away_win_prob,
            'model': 'LogisticRegression'
        }


class RandomForestModel:
    """随机森林模型（简化版）"""

    def __init__(self):
        self.trees = []  # 简化的决策树

    def add_tree(self, rules: List[Dict]):
        """添加决策树"""
        self.trees.append(rules)

    def predict_single(
        self,
        home_strength: float,
        away_strength: float,
        home_form: int,
        away_form: int
    ) -> float:
        """单个决策树预测"""
        score = 0

        # 简化规则
        if home_strength > away_strength + 10:
            score += 1
        elif away_strength > home_strength + 10:
            score -= 1

        if home_form > away_form + 2:
            score += 0.5
        elif away_form > home_form + 2:
            score -= 0.5

        return score

    def predict(
        self,
        home_strength: float,
        away_strength: float,
        home_form: int,
        away_form: int
    ) -> Dict[str, float]:
        """随机森林预测"""
        scores = [self.predict_single(home_strength, away_strength, home_form, away_form)]

        # 计算平均分数
        avg_score = sum(scores) / len(scores)

        # 转换为概率
        if avg_score > 0.5:
            home_win_prob = 0.7
            away_win_prob = 0.2
            draw_prob = 0.1
        elif avg_score < -0.5:
            home_win_prob = 0.2
            away_win_prob = 0.7
            draw_prob = 0.1
        else:
            if abs(avg_score) < 0.2:
                home_win_prob = 0.35
                away_win_prob = 0.35
                draw_prob = 0.3
            else:
                home_win_prob = 0.3
                away_win_prob = 0.3
                draw_prob = 0.4

        return {
            'home_win': home_win_prob,
            'draw': draw_prob,
            'away_win': away_win_prob,
            'model': 'RandomForest'
        }


# ==================== 特殊分析模型 ====================

class XGModel:
    """预期进球(xG)模型"""

    def __init__(self):
        self.home_xg = {}
        self.away_xg = {}

    def calculate_xg(
        self,
        home_shots: int,
        home_shots_on_target: int,
        away_shots: int,
        away_shots_on_target: int,
        home_big_chances: int,
        away_big_chances: int
    ) -> Dict[str, float]:
        """计算xG"""
        # 简化xG计算
        home_xg = home_shots * 0.1 + home_shots_on_target * 0.3 + home_big_chances * 0.4
        away_xg = away_shots * 0.1 + away_shots_on_target * 0.3 + away_big_chances * 0.4

        return {
            'home_xg': home_xg,
            'away_xg': away_xg,
            'total_xg': home_xg + away_xg
        }

    def predict_from_xg(
        self,
        home_xg: float,
        away_xg: float
    ) -> Dict[str, float]:
        """基于xG预测"""
        poisson = PoissonModel()

        home_lambda = home_xg * 1.1  # 主场调整
        away_lambda = away_xg * 0.9

        score_probs = poisson.predict_score_probability(home_lambda, away_lambda)

        return {
            'home_xg': home_xg,
            'away_xg': away_xg,
            'home_win': score_probs['home_win'],
            'draw': score_probs['draw'],
            'away_win': score_probs['away_win'],
            'model': 'xG'
        }


class BayesianModel:
    """贝叶斯模型"""

    def __init__(self, prior_home_win: float = 0.45, prior_draw: float = 0.27, prior_away_win: float = 0.28):
        # 先验概率（基于联赛历史）
        self.prior_home_win = prior_home_win
        self.prior_draw = prior_draw
        self.prior_away_win = prior_away_win

    def update_with_evidence(
        self,
        home_win: float,
        draw: float,
        away_win: float,
        home_evidence_weight: float = 0.3,
        form_weight: float = 0.2,
        injury_weight: float = 0.15
    ) -> Dict[str, float]:
        """根据证据更新概率"""

        # 简化的贝叶斯更新
        posterior_home = self.prior_home_win * (1 + home_evidence_weight)
        posterior_draw = self.prior_draw * (1 + form_weight)
        posterior_away = self.prior_away_win * (1 + injury_weight)

        # 归一化
        total = posterior_home + posterior_draw + posterior_away
        posterior_home /= total
        posterior_draw /= total
        posterior_away /= total

        return {
            'home_win': posterior_home,
            'draw': posterior_draw,
            'away_win': posterior_away,
            'model': 'Bayesian'
        }


# ==================== 多模型融合系统 ====================

class MultiModelFusion:
    """多模型融合系统"""

    # 模型权重配置
    MODEL_WEIGHTS = {
        'poisson': 0.15,
        'dixon_coles': 0.10,
        'elo': 0.15,
        'glicko': 0.10,
        'logistic_regression': 0.12,
        'random_forest': 0.10,
        'xg': 0.10,
        'bayesian': 0.08,
        'expert': 0.05,
        'ensemble': 0.05
    }

    def __init__(self, model_weights: Optional[Dict[str, float]] = None):
        self.models = {
            'poisson': PoissonModel(),
            'dixon_coles': DixonColesModel(),
            'elo': EloRatingSystem(),
            'glicko': GlickoRatingSystem(),
            'logistic_regression': LogisticRegressionModel(),
            'random_forest': RandomForestModel(),
            'xg': XGModel(),
            'bayesian': BayesianModel()
        }
        self.model_weights = self._normalize_weights(model_weights or self.MODEL_WEIGHTS.copy())

    @staticmethod
    def _normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
        total_weight = sum(weights.values())
        if total_weight <= 0:
            return MultiModelFusion.MODEL_WEIGHTS.copy()
        return {
            model_name: weight / total_weight
            for model_name, weight in weights.items()
        }

    def set_model_weights(self, model_weights: Dict[str, float]):
        """更新模型融合权重。"""
        self.model_weights = self._normalize_weights(model_weights)

    def predict(
        self,
        home_team: str,
        away_team: str,
        home_strength: float,
        away_strength: float,
        home_form: int,
        away_form: int,
        home_injuries: int,
        away_injuries: int,
        h2h_home_wins: int,
        h2h_away_wins: int,
        h2h_draws: int,
        home_motivation: float,
        away_motivation: float,
        home_xg: float,
        away_xg: float,
        home_attack: float,
        home_defense: float,
        away_attack: float,
        away_defense: float
    ) -> Dict:
        """多模型融合预测"""

        all_predictions = {}

        # 1. 泊松分布模型
        poisson = self.models['poisson']
        home_lambda, away_lambda = poisson.calculate_expected_goals(
            home_attack, home_defense, away_attack, away_defense
        )
        poisson_result = poisson.predict_score_probability(home_lambda, away_lambda)
        all_predictions['poisson'] = poisson_result

        # 2. Dixon-Coles模型
        dc = self.models['dixon_coles']
        dc_result = dc.predict_with_dixon_coles(home_lambda, away_lambda)
        all_predictions['dixon_coles'] = dc_result

        # 3. Elo评级模型
        elo = self.models['elo']
        elo.set_rating(home_team, home_strength * 10 + 1500)
        elo.set_rating(away_team, away_strength * 10 + 1500)
        elo_result = elo.predict_match(home_team, away_team)
        all_predictions['elo'] = elo_result

        # 4. Glicko评级模型
        glicko = self.models['glicko']
        glicko.set_rating(home_team, home_strength * 10 + 1500)
        glicko.set_rating(away_team, away_strength * 10 + 1500)
        glicko_result = glicko.predict_match(home_team, away_team)
        all_predictions['glicko'] = glicko_result

        # 5. 逻辑回归模型
        lr = self.models['logistic_regression']
        lr_result = lr.predict(
            home_strength, away_strength,
            home_form, away_form,
            home_injuries, away_injuries,
            h2h_home_wins, h2h_away_wins, h2h_draws,
            home_motivation, away_motivation
        )
        all_predictions['logistic_regression'] = lr_result

        # 6. 随机森林模型
        rf = self.models['random_forest']
        rf_result = rf.predict(home_strength, away_strength, home_form, away_form)
        all_predictions['random_forest'] = rf_result

        # 7. xG模型
        xg = self.models['xg']
        xg_result = xg.predict_from_xg(home_xg, away_xg)
        all_predictions['xg'] = xg_result

        # 8. 贝叶斯模型
        bayesian = self.models['bayesian']
        bayesian_result = bayesian.update_with_evidence(
            home_strength / 100,
            1 - home_strength / 100 - away_strength / 100,
            away_strength / 100
        )
        all_predictions['bayesian'] = bayesian_result

        # 9. 专家系统（简化版）
        expert_result = self._expert_system(
            home_strength, away_strength,
            home_form, away_form,
            home_injuries, away_injuries
        )
        all_predictions['expert'] = expert_result

        # 10. 集成学习（加权平均）
        ensemble_result = self._ensemble_predict(all_predictions)
        all_predictions['ensemble'] = ensemble_result

        # 加权融合
        final_prediction = self._weighted_fusion(all_predictions)

        return {
            'final': final_prediction,
            'all_models': all_predictions,
            'home_lambda': home_lambda,
            'away_lambda': away_lambda
        }

    def _expert_system(
        self,
        home_strength: float,
        away_strength: float,
        home_form: int,
        away_form: int,
        home_injuries: int,
        away_injuries: int
    ) -> Dict[str, float]:
        """专家系统（简化版）"""
        score = 0

        # 实力评估
        if home_strength > away_strength + 15:
            score += 0.3
        elif home_strength > away_strength + 5:
            score += 0.15

        # 状态评估
        if home_form > away_form + 3:
            score += 0.2
        elif home_form > away_form:
            score += 0.1

        # 伤病评估
        if home_injuries > away_injuries + 2:
            score -= 0.2

        # 转换为概率
        if score > 0.2:
            return {'home_win': 0.65, 'draw': 0.25, 'away_win': 0.10, 'model': 'Expert'}
        elif score > 0:
            return {'home_win': 0.55, 'draw': 0.30, 'away_win': 0.15, 'model': 'Expert'}
        elif score > -0.2:
            return {'home_win': 0.35, 'draw': 0.35, 'away_win': 0.30, 'model': 'Expert'}
        else:
            return {'home_win': 0.25, 'draw': 0.30, 'away_win': 0.45, 'model': 'Expert'}

    def _ensemble_predict(self, all_predictions: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """集成学习预测"""
        ensemble_home = 0
        ensemble_draw = 0
        ensemble_away = 0

        for model_name, prediction in all_predictions.items():
            if model_name not in ['ensemble', 'expert']:
                ensemble_home += prediction['home_win']
                ensemble_draw += prediction['draw']
                ensemble_away += prediction['away_win']

        n_models = len(all_predictions) - 2  # 排除ensemble和expert

        return {
            'home_win': ensemble_home / n_models,
            'draw': ensemble_draw / n_models,
            'away_win': ensemble_away / n_models,
            'model': 'Ensemble'
        }

    def _weighted_fusion(self, all_predictions: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """加权融合"""
        fused_home = 0
        fused_draw = 0
        fused_away = 0

        for model_name, prediction in all_predictions.items():
            weight = self.model_weights.get(model_name, 0.05)
            fused_home += prediction['home_win'] * weight
            fused_draw += prediction['draw'] * weight
            fused_away += prediction['away_win'] * weight

        return {
            'home_win': fused_home,
            'draw': fused_draw,
            'away_win': fused_away
        }


def main():
    """测试多模型融合预测"""
    fusion = MultiModelFusion()

    # 测试比赛：切尔西 vs 曼联
    result = fusion.predict(
        home_team='切尔西',
        away_team='曼联',
        home_strength=65,  # 假设实力值 0-100
        away_strength=70,
        home_form=3,  # 最近5场胜场数
        away_form=4,
        home_injuries=2,
        away_injuries=3,
        h2h_home_wins=2,
        h2h_away_wins=3,
        h2h_draws=1,
        home_motivation=80,
        away_motivation=85,
        home_xg=1.5,
        away_xg=1.8,
        home_attack=1.2,
        home_defense=0.9,
        away_attack=1.4,
        away_defense=0.8
    )

    print("=" * 60)
    print("多模型融合预测结果")
    print("=" * 60)

    final = result['final']
    print(f"\n最终预测:")
    print(f"  主胜概率: {final['home_win']:.1%}")
    print(f"  平局概率: {final['draw']:.1%}")
    print(f"  客胜概率: {final['away_win']:.1%}")

    print(f"\n预期进球:")
    print(f"  主队: {result['home_lambda']:.2f}")
    print(f"  客队: {result['away_lambda']:.2f}")

    print("\n各模型预测详情:")
    for model_name, prediction in result['all_models'].items():
        print(f"  {model_name}: 主胜{prediction['home_win']:.1%}, 平局{prediction['draw']:.1%}, 客胜{prediction['away_win']:.1%}")


if __name__ == "__main__":
    main()

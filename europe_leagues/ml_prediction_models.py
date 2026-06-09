#!/usr/bin/env python3
"""模块说明：定义机器学习预测模型与相关训练或推理辅助逻辑。

机器学习预测模型
基于多模型融合提高预测准确性"""

import os
import json
import math
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional
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
        max_goals = 14
        goal_probs = {
            k: self.poisson_probability(total_lambda, k)
            for k in range(max_goals + 1)
        }
        residual = max(0.0, 1.0 - sum(goal_probs.values()))
        goal_probs[max_goals] += residual

        integer_line = abs(float(line) - round(float(line))) < 1e-9
        threshold = math.ceil(float(line))
        over_raw = sum(prob for goals, prob in goal_probs.items() if goals >= threshold)
        if integer_line:
            push_prob = goal_probs.get(int(round(float(line))), 0.0)
            over_raw = sum(prob for goals, prob in goal_probs.items() if goals > int(round(float(line))))
            under_raw = sum(prob for goals, prob in goal_probs.items() if goals < int(round(float(line))))
            effective_mass = max(1e-9, 1.0 - push_prob)
            over_prob = over_raw / effective_mass
            under_prob = under_raw / effective_mass
        else:
            push_prob = 0.0
            under_raw = sum(prob for goals, prob in goal_probs.items() if goals < threshold)
            over_prob = over_raw
            under_prob = under_raw

        return {
            'over': over_prob,
            'under': under_prob,
            'over_raw': over_raw,
            'under_raw': under_raw,
            'push': push_prob,
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
        """预测比赛（包含可用的平局概率近似）。

        说明：
        - 传统 Elo 二元 logistic 会导致 home_win + away_win = 1，从而 draw 恒为 0。
        - 这会显著拉低平局预测、进而拖累比分/大小球推断。
        - 这里用“实力接近则更易平”的经验项，为 draw 分配合理质量，
          再把剩余概率按 Elo 强弱分配给主/客胜。
        """
        home_rating = self.get_rating(home_team) + self.home_advantage
        away_rating = self.get_rating(away_team)

        # Elo 强弱（胜负二元）基准概率
        p_home_raw = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        p_home_raw = max(0.001, min(0.999, p_home_raw))
        p_away_raw = 1 - p_home_raw

        # 平局概率：强弱越接近越大（经验近似）
        diff = abs(home_rating - away_rating)
        draw_base = 0.28
        draw_prob = draw_base * math.exp(-diff / 250)
        draw_prob = max(0.08, min(0.32, draw_prob))

        remain = 1 - draw_prob
        home_win_prob = remain * p_home_raw
        away_win_prob = remain * p_away_raw

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
        """预测比赛（包含可用的平局概率近似）"""
        home_rating = self.get_rating(home_team) + self.home_advantage
        away_rating = self.get_rating(away_team)

        # 计算预期得分（简化版本）
        p_home_raw = 1 / (1 + 10 ** ((away_rating - home_rating) / 400))
        p_home_raw = max(0.001, min(0.999, p_home_raw))
        p_away_raw = 1 - p_home_raw

        diff = abs(home_rating - away_rating)
        draw_base = 0.28
        draw_prob = draw_base * math.exp(-diff / 250)
        draw_prob = max(0.08, min(0.32, draw_prob))

        remain = 1 - draw_prob
        home_win_prob = remain * p_home_raw
        away_win_prob = remain * p_away_raw

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
        p_home_raw = 1 / (1 + math.exp(-home_score * 5))
        p_home_raw = max(0.001, min(0.999, p_home_raw))
        p_away_raw = 1 - p_home_raw

        # 平局概率经验项：越接近越容易平（避免 draw 恒为0）
        draw_base = 0.30
        draw_prob = draw_base * math.exp(-abs(home_score) * 1.5)
        draw_prob = max(0.08, min(0.34, draw_prob))

        remain = 1 - draw_prob
        home_win_prob = remain * p_home_raw
        away_win_prob = remain * p_away_raw

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

    # 市场（赔率隐含概率）二次融合默认权重 α（你给的实战默认：常规 0.35）
    DEFAULT_MARKET_ALPHA = 0.35

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
        away_defense: float,
        market_probs: Optional[Dict[str, float]] = None,
        market_alpha: Optional[float] = None,
        expert_signals: Optional[Dict[str, Any]] = None,
        model_accuracy: Optional[Dict[str, float]] = None
    ) -> Dict:
        """多模型融合预测

        两阶段融合：
        1) 10 个模型的加权融合 -> P_model（硬数据 + 已有 expert/ensemble）
        2) 与赔率隐含的市场概率做凸组合：final = (1-α)·P_model + α·P_market
        """

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

        # 9. 专家系统（接入真实情报信号）
        expert_result = self._expert_system(
            home_strength, away_strength,
            home_form, away_form,
            home_injuries, away_injuries,
            expert_signals=expert_signals,
        )
        all_predictions['expert'] = expert_result

        # 10. 集成学习（按历史准确率带权 stacking）
        ensemble_result = self._ensemble_predict(all_predictions, model_accuracy=model_accuracy)
        all_predictions['ensemble'] = ensemble_result

        # 第一阶段：10 模型加权融合 -> P_model
        model_prediction = self._weighted_fusion(all_predictions)

        # 第二阶段：与市场（赔率隐含）概率做凸组合 final = (1-α)·P_model + α·P_market
        final_prediction, market_fusion_diag = self._fuse_with_market(
            model_prediction, market_probs, market_alpha
        )

        return {
            'final': final_prediction,
            'model_only': model_prediction,
            'all_models': all_predictions,
            'market_fusion': market_fusion_diag,
            'home_lambda': home_lambda,
            'away_lambda': away_lambda
        }

    def _fuse_with_market(
        self,
        model_prediction: Dict[str, float],
        market_probs: Optional[Dict[str, float]],
        market_alpha: Optional[float],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """市场二次融合：final = (1-α)·P_model + α·P_market。

        无可用市场概率（无真实赔率）时 α 自动置 0，等价于只用模型概率。
        """
        diag: Dict[str, Any] = {'applied': False, 'alpha': 0.0, 'reason': 'no_market_probs'}
        if not isinstance(market_probs, dict):
            return model_prediction, diag

        try:
            mh = float(market_probs.get('home_win'))
            md = float(market_probs.get('draw'))
            ma = float(market_probs.get('away_win'))
        except (TypeError, ValueError):
            return model_prediction, diag

        total = mh + md + ma
        if total <= 0 or min(mh, md, ma) < 0:
            diag['reason'] = 'invalid_market_probs'
            return model_prediction, diag
        mh, md, ma = mh / total, md / total, ma / total

        alpha = self.DEFAULT_MARKET_ALPHA if market_alpha is None else float(market_alpha)
        alpha = max(0.0, min(1.0, alpha))

        # 归一化模型概率，保证两侧均为合法分布、融合结果和为 1
        mp_total = (
            float(model_prediction.get('home_win', 0.0))
            + float(model_prediction.get('draw', 0.0))
            + float(model_prediction.get('away_win', 0.0))
        )
        if mp_total > 0:
            norm_model = {
                'home_win': float(model_prediction['home_win']) / mp_total,
                'draw': float(model_prediction['draw']) / mp_total,
                'away_win': float(model_prediction['away_win']) / mp_total,
            }
        else:
            norm_model = {'home_win': 1 / 3, 'draw': 1 / 3, 'away_win': 1 / 3}

        if alpha <= 0.0:
            diag.update({'reason': 'alpha_zero', 'alpha': 0.0,
                         'market_probs': {'home_win': round(mh, 6), 'draw': round(md, 6), 'away_win': round(ma, 6)}})
            return model_prediction, diag

        fused = {
            'home_win': (1 - alpha) * norm_model['home_win'] + alpha * mh,
            'draw': (1 - alpha) * norm_model['draw'] + alpha * md,
            'away_win': (1 - alpha) * norm_model['away_win'] + alpha * ma,
        }
        diag = {
            'applied': True,
            'alpha': round(alpha, 4),
            'reason': 'market_fused',
            'model_probs': {k: round(float(v), 6) for k, v in norm_model.items()},
            'market_probs': {'home_win': round(mh, 6), 'draw': round(md, 6), 'away_win': round(ma, 6)},
        }
        return fused, diag

    def _expert_system(
        self,
        home_strength: float,
        away_strength: float,
        home_form: int,
        away_form: int,
        home_injuries: int,
        away_injuries: int,
        expert_signals: Optional[Dict[str, Any]] = None
    ) -> Dict[str, float]:
        """专家系统：接入真实情报信号（伤停/停赛、核心可用、战意、爆冷、盘口异常）。

        产出连续评分而非固定概率桶，再用 sigmoid + 平局先验映射为概率，
        让 expert 模型携带真实信息量而非硬编码规则。
        """
        signals = expert_signals if isinstance(expert_signals, dict) else {}

        def _num(key: str, default: float = 0.0) -> float:
            try:
                value = signals.get(key)
                return default if value is None else float(value)
            except (TypeError, ValueError):
                return default

        # 主队视角的净优势评分（>0 利主，<0 利客）
        score = 0.0
        factors: List[str] = []

        # 1) 实力差
        strength_gap = home_strength - away_strength
        score += max(-0.30, min(0.30, strength_gap / 50.0))
        if abs(strength_gap) >= 10:
            factors.append(f"实力差{strength_gap:+.0f}")

        # 2) 近况状态差
        form_gap = home_form - away_form
        score += max(-0.15, min(0.15, form_gap * 0.04))
        if abs(form_gap) >= 2:
            factors.append(f"状态差{form_gap:+d}")

        # 3) 伤停 / 停赛（人数差，主队多伤停利客）
        home_out = home_injuries + int(_num('home_suspensions'))
        away_out = away_injuries + int(_num('away_suspensions'))
        out_gap = away_out - home_out
        score += max(-0.18, min(0.18, out_gap * 0.05))
        if out_gap != 0:
            factors.append(f"伤停净差{out_gap:+d}")

        # 4) 核心球员可用性（缺失利对方）
        if 'home_key_available' in signals and not bool(signals.get('home_key_available')):
            score -= 0.10
            factors.append("主队核心缺阵")
        if 'away_key_available' in signals and not bool(signals.get('away_key_available')):
            score += 0.10
            factors.append("客队核心缺阵")

        # 5) 战意 / 动机差
        motivation_gap = _num('home_motivation') - _num('away_motivation')
        score += max(-0.12, min(0.12, motivation_gap / 100.0))
        if abs(motivation_gap) >= 10:
            factors.append(f"战意差{motivation_gap:+.0f}")

        # 6) 情报净倾向（match_intelligence 已折算的主队净利好评分，-1~1）
        intel_bias = max(-1.0, min(1.0, _num('intel_home_bias')))
        if intel_bias:
            score += intel_bias * 0.10
            factors.append(f"情报倾向{intel_bias:+.2f}")

        # 7) 盘口指向的热门方向（市场强烈看好某方时给予小幅确认）
        favorite = str(signals.get('market_favorite') or '').strip().lower()
        fav_strength = max(0.0, min(1.0, _num('market_favorite_strength')))
        if favorite == 'home':
            score += 0.10 * fav_strength
        elif favorite == 'away':
            score -= 0.10 * fav_strength

        # 8) 爆冷风险（高爆冷指数压缩主队方向优势）
        upset_index = max(0.0, min(1.0, _num('upset_index')))
        if upset_index:
            score *= (1.0 - 0.35 * upset_index)
            if upset_index >= 0.4:
                factors.append(f"爆冷指数{upset_index:.2f}")

        # 评分 -> 概率：主客胜由 sigmoid 决定，平局概率随对阵均势上升
        spread = 1.0 / (1.0 + math.exp(-3.2 * score))  # 0~1，0.5 为均势
        draw_base = 0.30 - 0.18 * abs(2 * spread - 1.0)  # 越均势平局越高
        draw = max(0.16, min(0.34, draw_base))
        remaining = 1.0 - draw
        home_win = remaining * spread
        away_win = remaining * (1.0 - spread)

        return {
            'home_win': round(home_win, 6),
            'draw': round(draw, 6),
            'away_win': round(away_win, 6),
            'model': 'Expert',
            'expert_score': round(score, 4),
            'expert_factors': factors,
        }

    def _ensemble_predict(
        self,
        all_predictions: Dict[str, Dict[str, float]],
        model_accuracy: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """集成学习：按各基模型历史准确率带权 stacking。

        排除自身/expert/market 避免重复计数；无历史准确率时退化为等权平均。
        """
        accuracy = model_accuracy if isinstance(model_accuracy, dict) else {}
        excluded = {'ensemble', 'expert', 'market'}

        ensemble_home = 0.0
        ensemble_draw = 0.0
        ensemble_away = 0.0
        weight_sum = 0.0
        used_accuracy = False

        for model_name, prediction in all_predictions.items():
            if model_name in excluded:
                continue
            acc = accuracy.get(model_name)
            if isinstance(acc, (int, float)) and acc > 0:
                weight = 0.5 + float(acc) * 1.5  # 与 DynamicWeightAdjuster 一致的口径
                used_accuracy = True
            else:
                weight = 1.0
            ensemble_home += float(prediction['home_win']) * weight
            ensemble_draw += float(prediction['draw']) * weight
            ensemble_away += float(prediction['away_win']) * weight
            weight_sum += weight

        if weight_sum <= 0:
            return {'home_win': 1 / 3, 'draw': 1 / 3, 'away_win': 1 / 3, 'model': 'Ensemble'}

        return {
            'home_win': ensemble_home / weight_sum,
            'draw': ensemble_draw / weight_sum,
            'away_win': ensemble_away / weight_sum,
            'model': 'Ensemble',
            'stacking_weighted': used_accuracy,
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

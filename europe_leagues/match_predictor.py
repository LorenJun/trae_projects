#!/usr/bin/env python3
"""
足球比赛预测分析模块
基于赔率数据（欧指、亚盘、凯利指数）进行综合预测分析
"""

import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class Outcome(Enum):
    HOME_WIN = "主胜"
    DRAW = "平局"
    AWAY_WIN = "客胜"


class HandicapDirection(Enum):
    HOME = "主队"
    AWAY = "客队"
    NEUTRAL = "中性"


@dataclass
class OddsData:
    """赔率数据结构"""
    match_id: str
    home_team: str
    away_team: str
    league: str

    euro_home: float
    euro_draw: float
    euro_away: float

    euro_home_current: Optional[float] = None
    euro_draw_current: Optional[float] = None
    euro_away_current: Optional[float] = None

    asian_handicap: Optional[str] = None
    asian_home_odds: Optional[float] = None
    asian_away_odds: Optional[float] = None
    asian_home_current: Optional[float] = None
    asian_handicap_current: Optional[str] = None
    asian_away_current: Optional[float] = None

    kelly_home: Optional[float] = None
    kelly_away: Optional[float] = None


class MatchPredictor:
    """比赛预测器"""

    def __init__(self, odds_data: OddsData):
        self.odds = odds_data

    def calculate_implied_probabilities(self) -> Dict[str, float]:
        """计算欧指的隐含概率"""
        home = 1 / self.odds.euro_home
        draw = 1 / self.odds.euro_draw
        away = 1 / self.odds.euro_away
        total = home + draw + away

        return {
            Outcome.HOME_WIN.value: home / total,
            Outcome.DRAW.value: draw / total,
            Outcome.AWAY_WIN.value: away / total
        }

    def analyze_odds_movement(self) -> Dict[str, any]:
        """分析赔率变化"""
        analysis = {}

        if self.odds.euro_home_current:
            home_change = self.odds.euro_home_current - self.odds.euro_home
            away_change = self.odds.euro_away_current - self.odds.euro_away

            analysis['home_trend'] = '上升' if home_change > 0 else '下降'
            analysis['away_trend'] = '上升' if away_change > 0 else '下降'
            analysis['home_change_pct'] = round(home_change / self.odds.euro_home * 100, 2)
            analysis['away_change_pct'] = round(away_change / self.odds.euro_away * 100, 2)

            if home_change > 0 and away_change < 0:
                analysis['interpretation'] = '主队热度下降，客队热度上升'
            elif home_change < 0 and away_change > 0:
                analysis['interpretation'] = '主队热度上升，客队热度下降'
            else:
                analysis['interpretation'] = '赔率变化不明显'

        return analysis

    def analyze_asian_handicap(self) -> Dict[str, any]:
        """分析亚盘数据"""
        if not self.odds.asian_handicap:
            return {'status': '无亚盘数据'}

        handicap = self.odds.asian_handicap

        home_odds = self.odds.asian_home_odds if self.odds.asian_home_odds else 1.9
        away_odds = self.odds.asian_away_odds if self.odds.asian_away_odds else 1.9

        if '平手' in handicap:
            handicap_type = '平手'
        elif '半球' in handicap:
            handicap_type = '半球'
        elif '一球' in handicap:
            handicap_type = '一球'
        elif '球半' in handicap:
            handicap_type = '球半'
        else:
            handicap_type = handicap

        return {
            'handicap': handicap,
            'type': handicap_type,
            'home_odds': home_odds,
            'away_odds': away_odds,
            'juice': round((home_odds + away_odds - 2) * 100, 2),
            'current_handicap': self.odds.asian_handicap_current,
            'direction': self._get_handicap_direction()
        }

    def _get_handicap_direction(self) -> str:
        """判断亚盘倾向"""
        if not self.odds.asian_handicap_current:
            return '数据不足'

        current = self.odds.asian_handicap_current
        original = self.odds.asian_handicap

        if current != original:
            if '平手' in original and '半球' in current:
                return '强化主队'
            elif '半球' in original and '平手' in current:
                return '强化客队'
        return '盘口稳定'

    def analyze_kelly_index(self) -> Dict[str, any]:
        """分析凯利指数"""
        if not self.odds.kelly_home:
            return {'status': '无凯利数据'}

        home_kelly = self.odds.kelly_home
        away_kelly = self.odds.kelly_away

        if home_kelly > 0.95:
            home_signal = '主队过热'
        elif home_kelly < 0.85:
            home_signal = '主队被低估'
        else:
            home_signal = '正常'

        if away_kelly > 0.95:
            away_signal = '客队过热'
        elif away_kelly < 0.85:
            away_signal = '客队被低估'
        else:
            away_signal = '正常'

        return {
            'home_kelly': home_kelly,
            'away_kelly': away_kelly,
            'home_signal': home_signal,
            'away_signal': away_signal,
            'divergence': abs(home_kelly - away_kelly)
        }

    def poisson_probability(self, goals: float, lambda_rate: float) -> float:
        """泊松分布概率计算"""
        return (lambda_rate ** goals * math.exp(-lambda_rate)) / math.factorial(int(goals))

    def calculate_poisson_prediction(self, home_avg: float = 1.3, away_avg: float = 1.0) -> Dict[str, float]:
        """基于泊松分布预测比分概率"""
        scores = {}

        for home_goals in range(5):
            for away_goals in range(5):
                p_home = self.poisson_probability(home_goals, home_avg)
                p_away = self.poisson_probability(away_goals, away_avg)
                scores[f'{home_goals}-{away_goals}'] = p_home * p_away

        total = sum(scores.values())
        for k in scores:
            scores[k] /= total

        most_likely = max(scores, key=scores.get)
        home_win_prob = sum(v for k, v in scores.items() if int(k.split('-')[0]) > int(k.split('-')[1]))
        draw_prob = sum(v for k, v in scores.items() if int(k.split('-')[0]) == int(k.split('-')[1]))
        away_win_prob = sum(v for k, v in scores.items() if int(k.split('-')[0]) < int(k.split('-')[1]))

        return {
            'most_likely_score': most_likely,
            'home_win_prob': round(home_win_prob * 100, 1),
            'draw_prob': round(draw_prob * 100, 1),
            'away_win_prob': round(away_win_prob * 100, 1),
            'top_scores': sorted(scores.items(), key=lambda x: x[1], reverse=True)[:5]
        }

    def comprehensive_prediction(self) -> Dict[str, any]:
        """综合预测分析"""
        implied = self.calculate_implied_probabilities()
        odds_movement = self.analyze_odds_movement()
        asian = self.analyze_asian_handicap()
        kelly = self.analyze_kelly_index()

        home_prob = implied[Outcome.HOME_WIN.value] * 100
        draw_prob = implied[Outcome.DRAW.value] * 100
        away_prob = implied[Outcome.AWAY_WIN.value] * 100

        score = 0

        if home_prob > away_prob:
            primary_prediction = Outcome.HOME_WIN.value
            score += home_prob
        else:
            primary_prediction = Outcome.AWAY_WIN.value
            score += away_prob

        if odds_movement.get('home_trend') == '下降':
            score += 10
        if odds_movement.get('away_trend') == '上升':
            score += 5

        if kelly.get('home_signal') == '主队被低估':
            score += 10
        if kelly.get('away_signal') == '客队过热':
            score -= 5

        if asian.get('direction') == '强化主队':
            score += 10
        elif asian.get('direction') == '强化客队':
            score -= 10

        confidence = '中等'
        if score > 75:
            confidence = '高'
        elif score < 55:
            confidence = '低'

        return {
            'primary_prediction': primary_prediction,
            'confidence': confidence,
            'score': score,
            'implied_probabilities': {
                'home_win': round(home_prob, 1),
                'draw': round(draw_prob, 1),
                'away_win': round(away_prob, 1)
            },
            'odds_movement': odds_movement,
            'asian_handicap': asian,
            'kelly_index': kelly,
            'recommendation': self._generate_recommendation(
                primary_prediction, confidence, implied, odds_movement, kelly, asian
            )
        }

    def _generate_recommendation(self, prediction: str, confidence: str,
                                 implied: Dict, odds_mov: Dict, kelly: Dict,
                                 asian: Dict) -> Dict[str, str]:
        """生成投注建议"""
        if confidence == '低':
            return {
                'bet': '不建议投注',
                'reason': '信心不足，建议观望',
                'odds_type': '-'
            }

        bet_options = []

        if implied[Outcome.HOME_WIN.value] > 0.45 and odds_mov.get('home_trend') == '下降':
            bet_options.append(('主胜', implied[Outcome.HOME_WIN.value], '欧指'))

        if implied[Outcome.DRAW.value] > 0.30:
            bet_options.append(('平局', implied[Outcome.DRAW.value], '欧指'))

        if asian.get('direction') == '强化主队':
            bet_options.append(('主队盘口', 0.52, '亚盘'))

        if kelly.get('home_signal') == '主队被低估':
            bet_options.append(('主胜', 0.55, '凯利'))

        if not bet_options:
            return {
                'bet': '观望',
                'reason': '各项指标不一致，建议谨慎',
                'odds_type': '-'
            }

        best_bet = max(bet_options, key=lambda x: x[1])
        return {
            'bet': best_bet[0],
            'odds': f'{best_bet[1]*100:.1f}%',
            'source': best_bet[2],
            'reason': self._get_bet_reason(best_bet, odds_mov, kelly, asian)
        }

    def _get_bet_reason(self, bet: Tuple, odds_mov: Dict, kelly: Dict, asian: Dict) -> str:
        """解释投注原因"""
        reasons = []

        if bet[2] == '欧指' and odds_mov.get('home_trend') == '下降':
            reasons.append('主队赔率下降，热度上升')
        if bet[2] == '凯利' and kelly.get('home_signal') == '主队被低估':
            reasons.append('凯利指数显示主队被低估')
        if bet[2] == '亚盘' and asian.get('direction') == '强化主队':
            reasons.append('亚盘强化主队')

        return '; '.join(reasons) if reasons else '综合分析推荐'


def analyze_match(odds_data: OddsData) -> Dict[str, any]:
    """分析比赛并返回预测结果"""
    predictor = MatchPredictor(odds_data)
    return predictor.comprehensive_prediction()


def print_analysis_report(match_id: str, home_team: str, away_team: str, odds_data: OddsData):
    """打印完整的分析报告"""
    predictor = MatchPredictor(odds_data)
    result = predictor.comprehensive_prediction()
    poisson = predictor.calculate_poisson_prediction()

    print("\n" + "="*70)
    print(f"📊 比赛预测分析报告")
    print("="*70)
    print(f"🏆 对阵: {home_team} vs {away_team}")
    print(f"📍 Match ID: {match_id}")
    print("="*70)

    print("\n📈 【欧指分析 - 隐含概率】")
    print("-"*50)
    impl = result['implied_probabilities']
    print(f"  主胜: {impl['home_win']:.1f}%")
    print(f"  平局: {impl['draw']:.1f}%")
    print(f"  客胜: {impl['away_win']:.1f}%")

    print("\n📉 【赔率变化分析】")
    print("-"*50)
    mov = result['odds_movement']
    if mov:
        print(f"  主队赔率: {mov.get('home_trend', '-')} ({mov.get('home_change_pct', 0):+.2f}%)")
        print(f"  客队赔率: {mov.get('away_trend', '-')} ({mov.get('away_change_pct', 0):+.2f}%)")
        print(f"  解读: {mov.get('interpretation', '-')}")
    else:
        print("  暂无即时数据")

    print("\n🎯 【亚盘分析】")
    print("-"*50)
    asian = result['asian_handicap']
    if asian.get('status'):
        print(f"  {asian['status']}")
    else:
        print(f"  盘口: {asian.get('handicap', '-')}")
        print(f"  主队水位: {asian.get('home_odds', '-')}")
        print(f"  客队水位: {asian.get('away_odds', '-')}")
        print(f"  抽水: {asian.get('juice', '-')}%")
        print(f"  盘口变化: {asian.get('direction', '-')}")

    print("\n📊 【凯利指数】")
    print("-"*50)
    kelly = result['kelly_index']
    if kelly.get('status'):
        print(f"  {kelly['status']}")
    else:
        print(f"  主队凯利: {kelly.get('home_kelly', '-')} ({kelly.get('home_signal', '-')})")
        print(f"  客队凯利: {kelly.get('away_kelly', '-')} ({kelly.get('away_signal', '-')})")

    print("\n🔮 【泊松分布预测】")
    print("-"*50)
    print(f"  最可能比分: {poisson['most_likely_score']}")
    print(f"  主胜概率: {poisson['home_win_prob']}%")
    print(f"  平局概率: {poisson['draw_prob']}%")
    print(f"  客胜概率: {poisson['away_win_prob']}%")
    print("  热门比分:")
    for score, prob in poisson['top_scores']:
        print(f"    {score}: {prob*100:.1f}%")

    print("\n" + "="*70)
    print("🎯 【综合预测结果】")
    print("-"*70)
    print(f"  预测结果: {result['primary_prediction']}")
    print(f"  信心等级: {result['confidence']} (得分: {result['score']:.0f})")

    rec = result['recommendation']
    print(f"\n  💰 投注建议:")
    print(f"     推荐: {rec['bet']}")
    print(f"     概率: {rec.get('odds', '-')}")
    print(f"     来源: {rec.get('source', '-')}")
    print(f"     理由: {rec['reason']}")

    print("="*70)
    print("⚠️  风险提示: 投注有风险，请理性分析，量力而行")
    print("="*70 + "\n")

    return result
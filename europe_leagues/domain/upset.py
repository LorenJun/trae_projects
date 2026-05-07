"""模块说明：负责爆冷风险评估、赔率反打案例与冷门知识检索。"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from runtime.cache import PredictionCache

logger = logging.getLogger(__name__)


class UpsetAnalyzer:
    """爆冷分析器"""
    
    def __init__(self, base_dir: str = None, league_config: Optional[Dict[str, Dict[str, Any]]] = None):
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.league_config = league_config or {}
        self.upset_cases = self._load_upset_cases()
        self.cache = PredictionCache()

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _case_get(case: Dict, *keys: str) -> Any:
        for k in keys:
            if k in case:
                return case.get(k)
        return None

    @staticmethod
    def _case_int(case: Dict, *keys: str, default: int = 0) -> int:
        value = UpsetAnalyzer._case_get(case, *keys)
        try:
            if value in (None, ''):
                return default
            return int(float(value))
        except Exception:
            return default

    @staticmethod
    def _extract_case_tags(case: Dict) -> set:
        """Extract coarse tags from a knowledge-base case for similarity matching."""
        text_fields = []
        for key in ('爆冷类型', '盘口异常', '赔率变化', '凯利指数', '伤病影响', '战术变化', '心理因素', '爆冷原因分析'):
            val = UpsetAnalyzer._case_get(case, key)
            if val:
                text_fields.append(str(val))
        text = ' '.join(text_fields)

        tags = set()
        if '降盘' in text or '降' in (UpsetAnalyzer._case_get(case, '盘口异常') or ''):
            tags.add('handicap_down')
        if '升盘' in text or '升' in (UpsetAnalyzer._case_get(case, '盘口异常') or ''):
            tags.add('handicap_up')
        if '平手' in text:
            tags.add('handicap_level')
        if '偏高' in text:
            tags.add('kelly_or_water_high')
        if '伤' in text or '缺阵' in text:
            tags.add('injury_or_absence')
        if '轮换' in text or '欧战' in text or '杯' in text:
            tags.add('rotation_or_cup')
        if '战意' in text or '保级' in text or '争冠' in text:
            tags.add('motivation')
        if '主场' in text:
            tags.add('home_boost')
        if '平局' in text:
            tags.add('draw_risk')
        if '赔率' in text and '上升' in text:
            tags.add('odds_up')
        if '赔率' in text and '下降' in text:
            tags.add('odds_down')
        return tags

    @staticmethod
    def _extract_current_tags(
        strength_diff: float,
        asian_handicap: Optional[Dict],
        european_odds: Optional[Dict],
        mismatch_analysis: Optional[Dict],
    ) -> set:
        """Extract coarse tags from current match context for similarity matching."""
        tags = set()
        if abs(float(strength_diff or 0.0)) >= 20:
            tags.add('big_gap')
        if mismatch_analysis and mismatch_analysis.get('mismatch_detected'):
            tags.add('handicap_strength_mismatch')
            tags.add(f"mismatch_{mismatch_analysis.get('mismatch_level')}")

        # Draw-odds low heuristic
        if isinstance(european_odds, dict):
            final_odds = european_odds.get('final', {}) or {}
            draw_odds = UpsetAnalyzer._to_float(final_odds.get('draw'), 0.0)
            if draw_odds and draw_odds < 3.2:
                tags.add('draw_risk')

        # Handicap movement heuristic (if initial/final both present)
        if isinstance(asian_handicap, dict):
            fin = asian_handicap.get('final', {}) or {}
            ini = asian_handicap.get('initial', {}) or {}
            fin_v = str(fin.get('handicap_value') or '')
            ini_v = str(ini.get('handicap_value') or '')
            if fin_v and ini_v and fin_v != ini_v:
                tags.add('handicap_move')
        return tags

    def _find_similar_cases(
        self,
        league_name: str,
        strength_diff: float,
        asian_handicap: Optional[Dict],
        european_odds: Optional[Dict],
        mismatch_analysis: Optional[Dict],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Similarity retrieval over the upset case library (as a lightweight knowledge base)."""
        cases = self._league_cases(league_name)
        if not cases:
            return []

        cur_tags = self._extract_current_tags(strength_diff, asian_handicap, european_odds, mismatch_analysis)
        cur_gap = abs(float(strength_diff or 0.0))

        scored = []
        for case in cases:
            # Skip unverified cases to reduce noise.
            level = str(self._case_get(case, '爆冷等级') or '')
            actual = str(self._case_get(case, '实际结果') or '')
            if level == '待验证' or actual == '待验证':
                continue

            # Approximate "gap" using ranking/points differences from the case as a proxy for strength gap.
            rank_gap = abs(self._case_int(case, '排名差', default=0))
            pts_gap = abs(self._case_int(case, '积分差', default=0))
            case_gap = rank_gap * 2.0 + pts_gap * 0.8

            # Gap similarity: 1/(1+delta) in a normalized space.
            gap_delta = abs(cur_gap - case_gap)
            gap_sim = 1.0 / (1.0 + (gap_delta / 10.0))

            case_tags = self._extract_case_tags(case)
            union = cur_tags | case_tags
            tag_sim = (len(cur_tags & case_tags) / len(union)) if union else 0.0

            score = 0.6 * tag_sim + 0.4 * gap_sim
            scored.append((score, tag_sim, gap_sim, case))

        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        top = []
        for score, tag_sim, gap_sim, case in scored[:top_k]:
            top.append({
                'case_id': self._case_get(case, '案例ID') or '',
                'match_date': self._case_get(case, '比赛日期') or '',
                'home_team': self._case_get(case, '主队') or '',
                'away_team': self._case_get(case, '客队') or '',
                'upset_level': self._case_get(case, '爆冷等级') or '',
                'upset_type': self._case_get(case, '爆冷类型') or '',
                'reason': self._case_get(case, '爆冷原因分析') or '',
                'suggestion': self._case_get(case, '改进建议') or '',
                'score': round(float(score), 3),
            })
        return top
    
    def _league_cases(self, league_name: str) -> List[Dict]:
        return [
            case for case in self.upset_cases
            if self._case_get(case, 'league', '联赛') == league_name
        ]
    
    def _load_upset_cases(self) -> List[Dict]:
        """加载爆冷案例库"""
        file_path = os.path.join(self.base_dir, '爆冷案例库.json')
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载爆冷案例库失败: {e}")
        return []
    
    def analyze_handicap_vs_strength(
        self,
        home_team: str,
        away_team: str,
        strength_diff: float,
        asian_handicap: Optional[Dict] = None,
        european_odds: Optional[Dict] = None
    ) -> Dict:
        """分析强队实力指数与让步数据是否匹配 - 新增爆冷预警核心逻辑
        
        当强队实力明显占优但盘口/赔率让步不足时，触发爆冷预警
        
        Args:
            home_team: 主队名称
            away_team: 客队名称
            strength_diff: 实力差距 (正值表示主队强，负值表示客队强)
            asian_handicap: 亚盘数据 {'initial': {...}, 'final': {...}}
            european_odds: 欧赔数据 {'initial': {...}, 'final': {...}}
            
        Returns:
            {
                'mismatch_detected': bool,  # 是否检测到不匹配
                'mismatch_level': str,      # '高'/'中'/'低'
                'strong_team': str,         # 强队名称
                'weak_team': str,           # 弱队名称
                'strength_advantage': float, # 实力优势值
                'handicap_advantage': float, # 盘口优势值
                'gap': float,               # 差距值
                'warning_factors': List[str], # 预警因素
                'suggested_outcome': str    # 建议投注方向
            }
        """
        result = {
            'mismatch_detected': False,
            'mismatch_level': '低',
            'strong_team': '',
            'weak_team': '',
            'strength_advantage': 0.0,
            'handicap_advantage': 0.0,
            'gap': 0.0,
            'warning_factors': [],
            'suggested_outcome': ''
        }
        
        if not asian_handicap and not european_odds:
            return result
            
        # 确定哪方是强队
        if strength_diff > 0:
            result['strong_team'] = home_team
            result['weak_team'] = away_team
            result['strength_advantage'] = strength_diff
        else:
            result['strong_team'] = away_team
            result['weak_team'] = home_team
            result['strength_advantage'] = abs(strength_diff)
            
        # 实力优势必须足够大才进行分析
        if result['strength_advantage'] < 10:
            return result
            
        warning_factors = []
        gap = 0.0
        
        # 1. 亚盘分析 - 强队让球是否足够
        if asian_handicap:
            final_handicap = asian_handicap.get('final', {})
            handicap_value = self._to_float(final_handicap.get('handicap_value'), 0.0)
            home_water = self._to_float(final_handicap.get('home_water'), 0.0)
            away_water = self._to_float(final_handicap.get('away_water'), 0.0)
            
            # 转换盘口为数值
            handicap_num = 0.0
            if handicap_value:
                # 处理类似 "0.5", "1", "1/1.5" 等格式
                if '/' in str(handicap_value):
                    parts = str(handicap_value).split('/')
                    handicap_num = (float(parts[0]) + float(parts[1])) / 2
                else:
                    handicap_num = float(handicap_value)
                    
            # 判断强队是主队还是客队
            is_strong_home = result['strong_team'] == home_team
            
            # 强队让球不足的情况
            if is_strong_home:
                # 主队是强队，应该让球
                if handicap_num < 0.5 and result['strength_advantage'] >= 20:
                    gap = 20 - handicap_num * 10
                    warning_factors.append(f"{home_team}实力强{result['strength_advantage']:.0f}分但仅让{handicap_value}球，盘口过浅")
                elif handicap_num < 0.25 and result['strength_advantage'] >= 15:
                    gap = 15 - handicap_num * 10
                    warning_factors.append(f"{home_team}实力占优但盘口让球不足")
            else:
                # 客队是强队，应该受让或让球
                if handicap_num > -0.5 and result['strength_advantage'] >= 20:
                    gap = 20 + handicap_num * 10
                    warning_factors.append(f"{away_team}实力强{result['strength_advantage']:.0f}分但盘口{handicap_value}球，未获足够支持")
                    
            # 水位异常 - 强队水位过高
            if is_strong_home and home_water > 1.0:
                gap += 5
                warning_factors.append(f"{home_team}水位偏高({home_water})，庄家赔付压力大")
            elif not is_strong_home and away_water > 1.0:
                gap += 5
                warning_factors.append(f"{away_team}水位偏高({away_water})，庄家赔付压力大")
                
        # 2. 欧赔分析 - 强队赔率是否过高
        if european_odds:
            final_odds = european_odds.get('final', {})
            home_odds = self._to_float(final_odds.get('home'), 0.0)
            draw_odds = self._to_float(final_odds.get('draw'), 0.0)
            away_odds = self._to_float(final_odds.get('away'), 0.0)
            
            is_strong_home = result['strong_team'] == home_team
            
            # 根据实力差距计算理论赔率
            if result['strength_advantage'] >= 25:
                expected_strong_odds = 1.3
            elif result['strength_advantage'] >= 20:
                expected_strong_odds = 1.5
            elif result['strength_advantage'] >= 15:
                expected_strong_odds = 1.7
            elif result['strength_advantage'] >= 10:
                expected_strong_odds = 1.9
            else:
                expected_strong_odds = 2.1
                
            actual_strong_odds = home_odds if is_strong_home else away_odds
            
            if actual_strong_odds > 0 and actual_strong_odds > expected_strong_odds * 1.15:
                odds_gap = (actual_strong_odds - expected_strong_odds) / expected_strong_odds * 100
                gap += odds_gap
                warning_factors.append(f"{result['strong_team']}赔率{actual_strong_odds}高于理论值{expected_strong_odds:.2f}，机构不看好")
                
            # 平局赔率偏低 - 防范冷门信号
            if draw_odds > 0 and draw_odds < 3.2 and result['strength_advantage'] >= 15:
                gap += 8
                warning_factors.append(f"平局赔率{draw_odds}偏低，机构防范冷门")
                
        # 3. 综合判断
        result['gap'] = gap
        result['warning_factors'] = warning_factors
        
        if gap >= 30:
            result['mismatch_level'] = '高'
            result['mismatch_detected'] = True
        elif gap >= 15:
            result['mismatch_level'] = '中'
            result['mismatch_detected'] = True
        elif gap >= 5:
            result['mismatch_level'] = '低'
            
        # 建议投注方向
        if result['mismatch_detected']:
            if result['mismatch_level'] == '高':
                result['suggested_outcome'] = f"防范冷门 - {result['weak_team']}不败或平局"
            elif result['mismatch_level'] == '中':
                result['suggested_outcome'] = f"谨慎 - {result['weak_team']}+1球或小球"
            else:
                result['suggested_outcome'] = f"观望 - {result['strong_team']}小胜或平局"
        else:
            result['suggested_outcome'] = f"正常 - {result['strong_team']}胜"
            
        return result

    def assess_upset_potential(
        self,
        home_team: str,
        away_team: str,
        league_code: str,
        strength_diff: float,
        home_strength: Dict,
        away_strength: Dict,
        predicted_outcome: Optional[str] = None,
        confidence: Optional[float] = None,
        historical_odds_reference: Optional[Dict] = None,
        asian_handicap: Optional[Dict] = None,
        european_odds: Optional[Dict] = None
    ) -> Dict:
        """评估爆冷可能性（增强版）"""
        cache_params = {
            'home': home_team, 'away': away_team, 'league': league_code,
            'diff': strength_diff,
            'pred': predicted_outcome,
            'conf': None if confidence is None else round(float(confidence), 3),
            'odds_ref': historical_odds_reference,
            'asian_handicap': asian_handicap,
            'european_odds': european_odds,
        }
        cached = self.cache.get('assess_upset_potential', cache_params)
        if cached:
            return cached
        
        upset_index = 0.0
        factors = []
        mismatch_analysis = None
        
        # 1. 实力差距因素
        if abs(strength_diff) > 20:
            upset_index += 30
            factors.append(f"实力差距大({strength_diff:+.1f})")
        elif abs(strength_diff) > 10:
            upset_index += 15
        
        # 2. 历史爆冷案例（精确匹配 + 模式学习）
        league_name = (self.league_config.get(league_code, {}) or {}).get('name', league_code)
        league_cases = self._league_cases(league_name)
        similar_cases = []
        for case in league_cases:
            c_home = self._case_get(case, 'home_team', '主队')
            c_away = self._case_get(case, 'away_team', '客队')
            if not c_home or not c_away:
                continue
            if (
                (c_home == home_team and c_away == away_team) or
                (c_home == away_team and c_away == home_team)
            ):
                similar_cases.append(case)
        
        if similar_cases:
            upset_index += len(similar_cases) * 15
            factors.append(f"历史爆冷案例({len(similar_cases)}个)")

        # 基于案例库做“模式学习”：同一联赛中，历史上与当前预测方向相同但被反打的比例越高，爆冷指数越高。
        if predicted_outcome:
            opposite_cases = []
            for case in league_cases:
                pred = self._case_get(case, 'predicted_outcome', '预测结果')
                actual = self._case_get(case, 'actual_outcome', '实际结果')
                if pred == predicted_outcome and actual and actual != predicted_outcome:
                    opposite_cases.append(case)

            if opposite_cases:
                boost = min(20.0, len(opposite_cases) * 3.0)
                upset_index += boost
                factors.append(f"历史同向反打({len(opposite_cases)}次)")

                super_cold = 0
                for case in opposite_cases:
                    odds = self._to_float(self._case_get(case, 'upset_odds', '实际爆冷赔率'), 0.0)
                    if odds >= 5.0:
                        super_cold += 1
                if super_cold:
                    upset_index += min(10.0, super_cold * 5.0)
                    factors.append(f"历史超级冷门({super_cold}次)")

            # 经验规律：强热门且高信心时，若信息面不足（战意/轮换/临场）很容易“过热被穿”
            if confidence is not None and confidence >= 0.70 and abs(strength_diff) >= 15:
                upset_index += 5.0
                factors.append("强热门需防过热")
        
        # 3. 伤病因素
        if home_strength.get('injured_count', 0) >= 3:
            upset_index += 20
            factors.append(f"{home_team}伤病严重({home_strength['injured_count']}人)")
        elif home_strength.get('injured_count', 0) >= 2:
            upset_index += 10
        
        if away_strength.get('injured_count', 0) >= 3:
            upset_index += 20
            factors.append(f"{away_team}伤病严重({away_strength['injured_count']}人)")
        elif away_strength.get('injured_count', 0) >= 2:
            upset_index += 10
        
        # 4. 核心球员缺席
        if not home_strength.get('key_players_available', True):
            upset_index += 25
            factors.append(f"{home_team}核心球员缺席")
        
        if not away_strength.get('key_players_available', True):
            upset_index += 25
            factors.append(f"{away_team}核心球员缺席")

        # 5. 历史相似赔率参考
        if historical_odds_reference and historical_odds_reference.get('available'):
            summary = historical_odds_reference.get('summary', {})
            sample_size = summary.get('sample_size', 0)
            cold_rate = summary.get('cold_result_rate', 0.0)
            result_rates = summary.get('result_rates', {})

            if sample_size >= 3:
                upset_index += min(15.0, cold_rate * 25.0)
                if cold_rate >= 0.4:
                    factors.append(f"相似赔率冷门占比高({cold_rate:.0%})")

                if predicted_outcome:
                    reverse_rate = 1.0 - result_rates.get(predicted_outcome, 0.0)
                    if reverse_rate >= 0.6:
                        upset_index += min(10.0, reverse_rate * 10.0)
                        factors.append(f"相似赔率反向结果偏多({reverse_rate:.0%})")
        
        # 6. 【新增】强队实力指数与让步数据不匹配分析
        if asian_handicap or european_odds:
            mismatch_analysis = self.analyze_handicap_vs_strength(
                home_team=home_team,
                away_team=away_team,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                european_odds=european_odds
            )
            
            if mismatch_analysis.get('mismatch_detected'):
                gap = mismatch_analysis.get('gap', 0)
                level = mismatch_analysis.get('mismatch_level', '低')
                
                # 根据不匹配程度增加爆冷指数
                if level == '高':
                    upset_index += min(35, gap)
                elif level == '中':
                    upset_index += min(20, gap)
                else:
                    upset_index += min(10, gap)
                    
                # 添加不匹配因素到列表
                warning_factors = mismatch_analysis.get('warning_factors', [])
                for factor in warning_factors:
                    if factor not in factors:
                        factors.append(f"[实力-盘口不匹配] {factor}")

        # 7. 【新增】爆冷案例库知识检索（相似案例 Top-K），用于解释与复盘
        knowledge = {'available': False, 'top_cases': [], 'hint': ''}
        try:
            league_name = (self.league_config.get(league_code, {}) or {}).get('name', league_code)
            top_cases = self._find_similar_cases(
                league_name=league_name,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                european_odds=european_odds,
                mismatch_analysis=mismatch_analysis,
                top_k=3,
            )
            if top_cases:
                knowledge['available'] = True
                knowledge['top_cases'] = top_cases
                best = top_cases[0]
                # One-line hint for schedule write-back.
                hint_bits = []
                if best.get('upset_level'):
                    hint_bits.append(str(best['upset_level']))
                if best.get('upset_type') and best['upset_type'] != '无':
                    hint_bits.append(str(best['upset_type']))
                knowledge['hint'] = f"{best.get('home_team','')}vs{best.get('away_team','')}({','.join(hint_bits)})".strip('()')

                # Conservative boost: only when similarity is reasonably high and the case is a real upset.
                if float(best.get('score') or 0.0) >= 0.55 and best.get('upset_level') not in ('微弱爆冷', ''):
                    upset_index += min(8.0, float(best.get('score') or 0.0) * 8.0)
                    factors.append(f"[案例库] 相似案例:{knowledge['hint']} s={best.get('score')}")
        except Exception:
            # Keep prediction robust; knowledge is best-effort.
            knowledge = {'available': False, 'top_cases': [], 'hint': ''}
        
        # 确定爆冷等级
        upset_index = min(100, upset_index)
        
        if upset_index >= 70:
            upset_level = '高'
            warning_level = '🔴'
        elif upset_index >= 40:
            upset_level = '中'
            warning_level = '🟡'
        else:
            upset_level = '低'
            warning_level = '🟢'
        
        result = {
            'index': upset_index,
            'level': upset_level,
            'warning_level': warning_level,
            'similar_cases_count': len(similar_cases),
            'factors': factors,
            'historical_odds_reference': historical_odds_reference,
            'handicap_strength_mismatch': mismatch_analysis,
            'case_knowledge': knowledge,
        }
        
        self.cache.set('assess_upset_potential', cache_params, result)
        return result

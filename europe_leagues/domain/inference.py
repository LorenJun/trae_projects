"""模块说明：负责核心推理链、盘口校准、联赛学习与概率融合。"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from domain.constants import resolve_home_advantage, resolve_rho
from domain.odds import resolve_over_under_line
from models import DixonColesModel


class InferencePipelineService:
    REAL_OU_LINE_SOURCES = {'snapshot_final', 'snapshot_initial'}
    # 以下为「大小球判定策略」默认值，可被 config/prediction_policy.json 覆盖（见 _load_ou_policy）。
    # 大小球只有在「模型边际足够」时才允许提交方向；否则转中性不建议。
    # 复盘 35 场：单场大小球命中率仅 ~34%，且各置信度档位无差异，故以边际+市场同向为闸门。
    OU_MIN_CONVICTION_MARGIN = 0.10
    # 方案 A：大小球恒定中性。36 场反解诊断显示模型 total_λ 已紧贴盘口线（均差 −0.04），
    # 而模型相对盘口的偏移命中率仅 37%（负 alpha）——单边选边本质无 edge。
    # 故默认不再提交任何大小球方向，只保留盘口展示与诊断，不计入准确率。
    OU_ALWAYS_NEUTRAL = True
    # λ 市场校准的网格 cost 由三项构成（拟合 1X2 的 cost_prob 权重恒为 1.0）：
    #   cost_base 让 λ 不偏离队力基准、cost_total 让总进球贴近联赛/基准均值。
    # LAMBDA_OU_ANCHOR_COST：联合拟合「大小球盘口线反解的市场总进球」的权重。
    # 诊断显示纯拟合 1X2 会为均势高平局形态压低总进球（2022 世界杯 λ 总进球偏差 −0.075、
    # 低估 30/64）。加入此锚定项让 λ 总量同时贴合市场对总进球的定价，缓解比分系统性偏小。
    # 仅当大小球盘口可反解出 implied_total 时生效，不影响融合层 1X2（1X2 由独立 market_alpha 凸组合对齐）。
    # 默认 0.10（与历史一致，SoT 联赛零回归）；world_cup 在 BY_LEAGUE 提到 0.30：
    # 54 场世界杯已完赛回测显示，配合 cost_base/cost_total 削弱后，≥3 球场次校准 λ 被抬到 ≥2.5
    # 的命中从 7/30 升到 20/30，且 1X2 拟合误差未恶化（0.0208→0.0135）。
    # anchor 只竞争总进球量级维度、不动主客 share（由权重恒 1.0 的 cost_prob 主导），故 1X2 方向不受影响。
    LAMBDA_OU_ANCHOR_COST = 0.10
    LAMBDA_OU_ANCHOR_COST_BY_LEAGUE: Dict[str, float] = {'world_cup': 0.30}
    # cost_base 队力基准锚的权重：让校准后 λ 不过度偏离 attack×defense 派生的队力基准。
    # 世界杯队力多为 FIFA 排名兜底（不可靠且系统性偏保守），过强的基准锚会把 λ 死死按在
    # 偏低的起点上、抵消 OU 锚的上抬。故对 world_cup 调低此权重，让市场 OU 总进球更有话语权。
    # 默认 0.08（与历史一致，SoT 联赛零回归）；仅 world_cup 走 LAMBDA_BASE_ANCHOR_COST_BY_LEAGUE。
    LAMBDA_BASE_ANCHOR_COST = 0.08
    LAMBDA_BASE_ANCHOR_COST_BY_LEAGUE: Dict[str, float] = {'world_cup': 0.03}
    # cost_total 由两个独立的"总进球拉力"项构成（历史上合并为单个 0.04 权重）：
    #   ① 往联赛均值 league_avg 拉：LAMBDA_TOTAL_AVG_COST（默认 0.04）。
    #   ② 往队力基准总量 base_total 拉：LAMBDA_TOTAL_BASE_COST（默认 0.04）。
    # 拆开是为了对 world_cup 单独削弱②——FIFA 兜底队力使 base_total 系统性偏低（1.9~2.05），
    # 该项与 cost_base 冗余同向、把 λ 死死按在偏低起点上，抵消 OU 锚的上抬。
    # 默认两项各 0.04，与历史 `0.04*((tg-avg)²+(tg-base)²)` 逐字节等价，SoT 联赛零回归。
    # world_cup 把 base 项削到 0：FIFA 兜底队力 base_total 偏低且与 cost_base 冗余，去掉后
    # 让 OU 锚主导总进球量级（仍保留 avg 项防止 λ 被脏盘拉飞）。
    LAMBDA_TOTAL_AVG_COST = 0.04
    LAMBDA_TOTAL_BASE_COST = 0.04
    LAMBDA_TOTAL_BASE_COST_BY_LEAGUE: Dict[str, float] = {'world_cup': 0.0}

    def __init__(
        self,
        *,
        league_config: Dict[str, Dict[str, Any]],
        team_manager: Any,
        match_intelligence_engine: Any,
        odds_reference: Any,
        upset_analyzer: Any,
        model_fusion: Any,
        poisson_model: Any,
        weight_adjuster: Any,
        league_ou_learning: Any,
        postprocess_service: Any,
        rating_service: Any = None,
        base_dir: Optional[str] = None,
    ):
        self.league_config = league_config
        self.team_manager = team_manager
        self.match_intelligence_engine = match_intelligence_engine
        self.odds_reference = odds_reference
        self.upset_analyzer = upset_analyzer
        self.model_fusion = model_fusion
        self.poisson_model = poisson_model
        self.weight_adjuster = weight_adjuster
        self.league_ou_learning = league_ou_learning
        self.postprocess_service = postprocess_service
        self.rating_service = rating_service
        self.base_dir = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
        # 实例级大小球策略：默认取类常量，再用 config/prediction_policy.json 覆盖（若存在且合法）。
        self.ou_always_neutral = self.OU_ALWAYS_NEUTRAL
        self.ou_min_conviction_margin = self.OU_MIN_CONVICTION_MARGIN
        self._load_ou_policy()

    def _load_ou_policy(self) -> None:
        """从 config/prediction_policy.json 读取大小球策略，覆盖默认值。

        文件缺失或字段非法时静默回退到类常量默认，保证无配置也能运行。
        识别字段（over_under 块）：
          - always_neutral: bool   → 是否恒定中性（方案 A）
          - min_conviction_margin: float → 闸门最小边际（仅 always_neutral=false 时生效）
        """
        config_path = self.base_dir / "config" / "prediction_policy.json"
        if not config_path.exists():
            return
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        ou_block = payload.get("over_under")
        if not isinstance(ou_block, dict):
            return
        if isinstance(ou_block.get("always_neutral"), bool):
            self.ou_always_neutral = ou_block["always_neutral"]
        margin = ou_block.get("min_conviction_margin")
        try:
            if margin is not None:
                margin = float(margin)
                if 0.0 <= margin <= 1.0:
                    self.ou_min_conviction_margin = margin
        except (TypeError, ValueError):
            pass

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            s = str(value).strip()
            if not s:
                return None
            return float(s)
        except Exception:
            return None

    @staticmethod
    def _clamp_probability(value: float, lower: float = 0.0, upper: float = 0.99) -> float:
        return max(lower, min(upper, float(value)))

    @staticmethod
    def _normalize_prob_dict(probabilities: Dict[str, Any]) -> Dict[str, float]:
        p_h = float(probabilities.get('home_win') or 0.0)
        p_d = float(probabilities.get('draw') or 0.0)
        p_a = float(probabilities.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            return {'home_win': 0.0, 'draw': 0.0, 'away_win': 0.0}
        return {'home_win': p_h / total, 'draw': p_d / total, 'away_win': p_a / total}

    @classmethod
    def _apply_near_tie_stage_cap(
        cls,
        *,
        before_prob: Dict[str, Any],
        after_prob: Dict[str, Any],
        stage_name: str,
        gap_threshold: float = 0.02,
        keep_margin: float = 0.0008,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'stage': stage_name}
        if not isinstance(before_prob, dict) or not isinstance(after_prob, dict):
            diag['reason'] = 'invalid_inputs'
            return after_prob, diag

        before_norm = cls._normalize_prob_dict(before_prob)
        after_norm = cls._normalize_prob_dict(after_prob)
        before_ranked = sorted(before_norm.items(), key=lambda item: item[1], reverse=True)
        after_ranked = sorted(after_norm.items(), key=lambda item: item[1], reverse=True)
        if len(before_ranked) < 2 or len(after_ranked) < 2:
            diag['reason'] = 'insufficient_mass'
            return after_norm, diag

        before_top, before_second = before_ranked[0], before_ranked[1]
        after_top = after_ranked[0]
        before_gap = float(before_top[1]) - float(before_second[1])
        diag.update({
            'before_top': before_top[0],
            'after_top': after_top[0],
            'before_gap': round(float(before_gap), 4),
            'gap_threshold': gap_threshold,
        })
        if before_gap > gap_threshold + 1e-9:
            diag['reason'] = 'pre_stage_gap_too_large'
            return after_norm, diag
        if before_top[0] == after_top[0]:
            diag['reason'] = 'top_label_unchanged'
            return after_norm, diag

        donor_key = after_top[0]
        anchor_key = before_top[0]
        anchor_prob = float(after_norm.get(anchor_key) or 0.0)
        donor_prob = float(after_norm.get(donor_key) or 0.0)
        if donor_prob <= anchor_prob:
            diag['reason'] = 'after_top_not_ahead'
            return after_norm, diag
        take = (donor_prob - anchor_prob + keep_margin) / 2.0
        take = min(take, max(0.0, donor_prob - 0.02))
        if take <= 0:
            diag['reason'] = 'take_below_threshold'
            return after_norm, diag
        capped = dict(after_norm)
        capped[donor_key] = max(0.0, donor_prob - take)
        capped[anchor_key] = anchor_prob + take
        capped = cls._normalize_prob_dict(capped)
        diag.update({
            'applied': True,
            'reason': 'single_stage_flip_capped',
            'anchor_top': anchor_key,
            'attempted_top': donor_key,
            'take': round(float(take), 6),
            'capped_probabilities': {
                'home_win': round(float(capped.get('home_win') or 0.0), 6),
                'draw': round(float(capped.get('draw') or 0.0), 6),
                'away_win': round(float(capped.get('away_win') or 0.0), 6),
            },
        })
        return capped, diag

    @classmethod
    def _apply_cumulative_near_tie_cap(
        cls,
        *,
        anchor_prob: Dict[str, Any],
        current_prob: Dict[str, Any],
        gap_threshold: float = 0.02,
        keep_margin: float = 0.0012,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False}
        if not isinstance(anchor_prob, dict) or not isinstance(current_prob, dict):
            diag['reason'] = 'invalid_inputs'
            return current_prob, diag

        anchor_norm = cls._normalize_prob_dict(anchor_prob)
        current_norm = cls._normalize_prob_dict(current_prob)
        anchor_ranked = sorted(anchor_norm.items(), key=lambda item: item[1], reverse=True)
        current_ranked = sorted(current_norm.items(), key=lambda item: item[1], reverse=True)
        if len(anchor_ranked) < 2 or len(current_ranked) < 2:
            diag['reason'] = 'insufficient_mass'
            return current_norm, diag

        anchor_top, anchor_second = anchor_ranked[0], anchor_ranked[1]
        current_top = current_ranked[0]
        anchor_gap = float(anchor_top[1]) - float(anchor_second[1])
        diag.update({
            'anchor_top': anchor_top[0],
            'current_top': current_top[0],
            'anchor_gap': round(float(anchor_gap), 4),
            'gap_threshold': gap_threshold,
        })
        if anchor_gap > gap_threshold + 1e-9:
            diag['reason'] = 'anchor_gap_too_large'
            return current_norm, diag
        if anchor_top[0] == current_top[0]:
            diag['reason'] = 'top_label_unchanged'
            return current_norm, diag

        donor_key = current_top[0]
        anchor_key = anchor_top[0]
        anchor_current = float(current_norm.get(anchor_key) or 0.0)
        donor_current = float(current_norm.get(donor_key) or 0.0)
        if donor_current <= anchor_current:
            diag['reason'] = 'current_top_not_ahead'
            return current_norm, diag
        take = (donor_current - anchor_current + keep_margin) / 2.0
        take = min(take, max(0.0, donor_current - 0.02))
        if take <= 0:
            diag['reason'] = 'take_below_threshold'
            return current_norm, diag
        capped = dict(current_norm)
        capped[donor_key] = max(0.0, donor_current - take)
        capped[anchor_key] = anchor_current + take
        capped = cls._normalize_prob_dict(capped)
        diag.update({
            'applied': True,
            'reason': 'cumulative_flip_capped',
            'attempted_top': donor_key,
            'take': round(float(take), 6),
            'capped_probabilities': {
                'home_win': round(float(capped.get('home_win') or 0.0), 6),
                'draw': round(float(capped.get('draw') or 0.0), 6),
                'away_win': round(float(capped.get('away_win') or 0.0), 6),
            },
        })
        return capped, diag

    @classmethod
    def _calibrate_confidence_with_league_learning(
        cls,
        confidence: float,
        applied_weights: Optional[Dict[str, Any]],
    ) -> Tuple[float, Dict[str, Any]]:
        raw_confidence = cls._clamp_probability(cls._to_float(confidence) or 0.0)
        diag: Dict[str, Any] = {
            'applied': False,
            'reason': 'missing_applied_weights',
            'base_confidence': round(raw_confidence, 4),
            'adjusted_confidence': round(raw_confidence, 4),
            'confidence_adjustment': 0.0,
            'league_weight_factor': 1.0,
            'league_total_predictions': 0,
            'weight_reason': '',
        }
        if not isinstance(applied_weights, dict) or not applied_weights:
            return raw_confidence, diag

        league_weight_factor = cls._to_float(applied_weights.get('league_weight_factor'))
        if league_weight_factor is not None:
            diag['league_weight_factor'] = round(league_weight_factor, 4)
        diag['league_total_predictions'] = int(applied_weights.get('league_total_predictions', 0) or 0)
        diag['weight_reason'] = str(applied_weights.get('weight_reason') or '').strip()

        confidence_adjustment = cls._to_float(applied_weights.get('confidence_adjustment'))
        if confidence_adjustment is None:
            diag['reason'] = 'missing_confidence_adjustment'
            return raw_confidence, diag

        confidence_adjustment = cls._clamp_probability(confidence_adjustment, -0.08, 0.08)
        diag['confidence_adjustment'] = round(confidence_adjustment, 4)
        if abs(confidence_adjustment) < 0.0005:
            diag['reason'] = 'neutral_confidence_adjustment'
            return raw_confidence, diag

        adjusted_confidence = cls._clamp_probability(raw_confidence + confidence_adjustment)
        diag['adjusted_confidence'] = round(adjusted_confidence, 4)
        diag['applied'] = True
        diag['reason'] = 'league_confidence_boost' if confidence_adjustment > 0 else 'league_confidence_trim'
        if adjusted_confidence in (0.0, 0.99):
            diag['reason'] += '_clamped'
        return adjusted_confidence, diag

    @staticmethod
    def _mix_cross_direction_scores(
        reranked_scores: Optional[List[Tuple[str, float]]],
        score_probs: Optional[Dict[str, Any]],
        limit: int = 3,
    ) -> List[Tuple[str, float]]:
        """以预测方向最可能比分为锚，再按全局比分分布补足，给出跨方向 top-N。

        rerank_top_scores 输出严格限定在预测方向内（可能 3 个同向比分）。
        这里保留其首选作为方向锚点（赢面最大的比分），其余名额用全局
        Dixon-Coles 分布按概率补足，从而覆盖最可能出现的几个比分方向。
        """
        target = max(1, int(limit or 3))
        anchored = [
            (str(score).strip(), float(prob or 0.0))
            for score, prob in (reranked_scores or [])
            if str(score).strip()
        ]
        global_map = {
            str(score).strip(): float(prob or 0.0)
            for score, prob in (score_probs or {}).items()
            if str(score).strip()
        }
        global_ranked = sorted(global_map.items(), key=lambda item: item[1], reverse=True)
        final: List[Tuple[str, float]] = []
        seen = set()
        if anchored:
            anchor_score = anchored[0][0]
            anchor_prob = global_map.get(anchor_score, anchored[0][1])
            final.append((anchor_score, anchor_prob))
            seen.add(anchor_score)
        for score, prob in global_ranked:
            if len(final) >= target:
                break
            if score in seen:
                continue
            final.append((score, prob))
            seen.add(score)
        for score, prob in anchored:
            if len(final) >= target:
                break
            if score in seen:
                continue
            final.append((score, prob))
            seen.add(score)
        if not final:
            return list(anchored[:target])
        total = sum(prob for _score, prob in final)
        if total > 0:
            final = [(score, prob / total) for score, prob in final]
        return sorted(final, key=lambda item: item[1], reverse=True)[:target]

    @staticmethod
    def _rerank_scores_for_under_three(
        score_probs: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        limit: int = 8,
    ) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
        ranked = sorted(
            ((str(score), float(prob or 0.0)) for score, prob in (score_probs or {}).items()),
            key=lambda item: item[1],
            reverse=True,
        )
        target_limit = max(3, int(limit or 8))
        diag: Dict[str, Any] = {
            'applied': False,
            'reason': 'guard_not_triggered',
            'line': None,
            'over': None,
            'under': None,
            'penalties': {},
            'target_limit': target_limit,
        }
        if not ranked or not isinstance(over_under, dict):
            diag['reason'] = 'missing_score_probs_or_over_under'
            return ranked[: target_limit], diag

        line = InferencePipelineService._to_float(over_under.get('line'))
        over_prob = InferencePipelineService._to_float(over_under.get('over'))
        under_prob = InferencePipelineService._to_float(over_under.get('under'))
        diag['line'] = line
        diag['over'] = over_prob
        diag['under'] = under_prob
        if line is None or over_prob is None or under_prob is None:
            diag['reason'] = 'invalid_over_under_payload'
            return ranked[: target_limit], diag
        if line > 3.0 or under_prob <= over_prob:
            diag['reason'] = 'not_under_three'
            return ranked[: target_limit], diag

        league_learning = over_under.get('league_learning') if isinstance(over_under.get('league_learning'), dict) else {}
        market_learning_case = False
        market_payload = over_under.get('market') if isinstance(over_under.get('market'), dict) else {}
        market_initial = market_payload.get('initial') if isinstance(market_payload.get('initial'), dict) else {}
        market_final = market_payload.get('final') if isinstance(market_payload.get('final'), dict) else {}
        initial_line = InferencePipelineService._to_float(market_initial.get('line'))
        final_line = InferencePipelineService._to_float(market_final.get('line'))
        if (
            line <= 2.5
            and float(under_prob - over_prob) <= 0.1
            and float(league_learning.get('recent_avg_goals') or 0.0) >= 2.85
            and float(league_learning.get('over25_rate') or 0.0) >= 0.62
            and float(league_learning.get('over35_rate') or 0.0) >= 0.3
            and float(league_learning.get('btts_rate') or 0.0) >= 0.58
            and initial_line is not None
            and final_line is not None
            and initial_line >= 3.25
            and final_line <= 2.5
            and (initial_line - final_line) >= 0.5
        ):
            market_learning_case = True
            target_limit = max(target_limit, 20)
            diag['target_limit'] = target_limit
            diag['tail_retention'] = 'open_learning'
            diag['market_line_drop'] = round(initial_line - final_line, 4)

        if line <= 2.5:
            factors = {'3-1': 0.64, '2-2': 0.74}
            diag['line_bucket'] = '<=2.5'
        elif line <= 2.75:
            factors = {'3-1': 0.72, '2-2': 0.8}
            diag['line_bucket'] = '<=2.75'
        else:
            factors = {'3-1': 0.72, '2-2': 0.86}
            diag['line_bucket'] = '<=3.0'
        adjusted = dict(ranked)
        penalties: Dict[str, float] = {}
        diag['factors'] = factors
        for score, factor in factors.items():
            if score not in adjusted:
                continue
            original = float(adjusted[score])
            adjusted[score] = original * factor
            penalties[score] = round(original - adjusted[score], 6)

        reranked = sorted(adjusted.items(), key=lambda item: item[1], reverse=True)
        if penalties:
            diag['applied'] = True
            diag['reason'] = 'under_three_score_penalty'
            diag['signals'] = ['under3-score-consistency-guard']
            if diag.get('tail_retention') == 'open_learning':
                diag['signals'].append('under3-open-learning-tail-retention')
            diag['penalties'] = penalties
        else:
            diag['reason'] = 'target_scores_missing'
            if diag.get('tail_retention') == 'open_learning':
                diag['signals'] = ['under3-open-learning-tail-retention']
        return reranked[: target_limit], diag

    @staticmethod
    def _apply_draw_confirmation_guard(
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
        review_outcome_diag: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {
            'applied': False,
            'qualified': False,
            'reason': 'not_draw_top1',
            'signals': [],
            'evidence': [],
        }
        if not isinstance(final_prob, dict):
            diag['reason'] = 'invalid_final_prob'
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        top_side = max((('home_win', p_h), ('draw', p_d), ('away_win', p_a)), key=lambda item: item[1])[0]
        if top_side != 'draw':
            return final_prob, diag

        diag['reason'] = 'draw_confirmation_evaluated'
        sorted_probs = sorted((p_h, p_a), reverse=True)
        runner_up = sorted_probs[0] if sorted_probs else 0.0
        top_gap = p_d - runner_up
        diag['top_gap'] = round(top_gap, 6)
        if top_gap >= 0.045:
            diag['qualified'] = True
            diag['evidence'].append('draw_prob_clear_lead')
        elif top_gap <= 0.018:
            diag['signals'].append('draw_confirmation_gap_weak')

        euro_final = None
        if isinstance(current_odds, dict):
            euro = current_odds.get('胜平负赔率') or current_odds.get('欧赔')
            if isinstance(euro, dict) and isinstance(euro.get('final'), dict):
                euro_final = euro.get('final')
        draw_odds = home_odds = away_odds = None
        if isinstance(euro_final, dict):
            draw_odds = InferencePipelineService._to_float(euro_final.get('draw') if 'draw' in euro_final else euro_final.get('平'))
            home_odds = InferencePipelineService._to_float(euro_final.get('home') if 'home' in euro_final else euro_final.get('主'))
            away_odds = InferencePipelineService._to_float(euro_final.get('away') if 'away' in euro_final else euro_final.get('客'))
        if draw_odds is not None and home_odds is not None and away_odds is not None:
            market_min = min(home_odds, draw_odds, away_odds)
            draw_market_gap = draw_odds - market_min
            diag['draw_market_gap'] = round(draw_market_gap, 4)
            if draw_market_gap <= 0.18:
                diag['qualified'] = True
                diag['evidence'].append('draw_market_supported')
            elif draw_market_gap >= 0.38:
                diag['signals'].append('draw_market_not_confirmed')

        ou_line = InferencePipelineService._to_float(over_under.get('line')) if isinstance(over_under, dict) else None
        ou_over = InferencePipelineService._to_float(over_under.get('over')) if isinstance(over_under, dict) else None
        ou_under = InferencePipelineService._to_float(over_under.get('under')) if isinstance(over_under, dict) else None
        if ou_line is not None:
            diag['ou_line'] = ou_line
        if ou_under is not None and ou_over is not None:
            ou_under_edge = ou_under - ou_over
            diag['ou_under_edge'] = round(ou_under_edge, 4)
            if ou_line is not None and ou_line <= 2.5 and ou_under > ou_over:
                diag['qualified'] = True
                diag['evidence'].append('under_supports_draw')
            elif ou_under <= ou_over and ou_line is not None and ou_line >= 2.75:
                diag['signals'].append('open_total_not_support_draw')
            if (
                ou_line is not None
                and ou_line >= 3.0
                and ou_under_edge >= 0.45
                and 'draw_market_not_confirmed' in diag['signals']
            ):
                diag['signals'].append('extreme_under_not_draw_confirmed')

        scenario_tags = match_intelligence.get('scenario_tags', []) if isinstance(match_intelligence, dict) else []
        contextual_rules = match_intelligence.get('contextual_rules', {}) if isinstance(match_intelligence, dict) else {}
        review_diag = review_outcome_diag if isinstance(review_outcome_diag, dict) else {}
        review_signals = set(str(item).strip() for item in (review_diag.get('signals') or []) if str(item).strip())
        review_motivation = review_diag.get('motivation_risk') if isinstance(review_diag.get('motivation_risk'), dict) else {}
        review_home_bias_gate = review_diag.get('home_bias_gate') if isinstance(review_diag.get('home_bias_gate'), dict) else {}
        review_evidence = set(str(item).strip() for item in (review_home_bias_gate.get('evidence') or []) if str(item).strip())
        if 'recent_form_volatility_high' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('double_volatility_supports_draw')
        if 'la_liga_mid_table_home_flat' in scenario_tags:
            diag['qualified'] = True
            diag['evidence'].append('la_liga_mid_table_home_flat')
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags:
            diag['signals'].append('relegation_home_motivation_conflicts_draw')
        if 'review-fragile-home-favorite-correction' in review_signals:
            diag['signals'].append('fragile_home_outcome_review_present')
        if review_motivation.get('supports_upset') and str(review_motivation.get('pressure_side') or '').strip() == 'away':
            diag['signals'].append('away_pressure_review_signal')
        volatility = contextual_rules.get('volatility') if isinstance(contextual_rules, dict) else {}
        if isinstance(volatility, dict):
            diag['volatility'] = {
                'home': (volatility.get('home') or {}).get('label'),
                'away': (volatility.get('away') or {}).get('label'),
            }

        review_favored_side = str(review_motivation.get('favored_side') or '').strip()
        review_score = float(review_motivation.get('score') or 0.0) if isinstance(review_motivation, dict) else 0.0
        review_applied_shift = review_diag.get('applied_shift') if isinstance(review_diag.get('applied_shift'), dict) else {}
        away_review_shift = float(review_applied_shift.get('away_shift') or 0.0)
        review_three_layer_context = review_diag.get('three_layer_context') if isinstance(review_diag.get('three_layer_context'), dict) else {}
        review_handicap_depth_bucket = str(review_three_layer_context.get('handicap_depth_bucket') or '').strip()
        review_euro_support_bucket = str(review_three_layer_context.get('euro_support_bucket') or '').strip()
        narrow_away_bump_present = 'review-narrow-away-bump' in review_signals
        away_upset_bucket_ok = bool(
            not review_handicap_depth_bucket
            or review_handicap_depth_bucket in {'level_ball', 'level_shallow', 'level_medium', 'unknown'}
            or away_review_shift >= 0.02
            or narrow_away_bump_present
        )
        soft_draw_context_evidence = {'la_liga_mid_table_home_flat'}
        draw_support_evidence = {
            'draw_market_supported',
            'under_supports_draw',
            'double_volatility_supports_draw',
        }.union(soft_draw_context_evidence)
        home_rebound_blocking_evidence = draw_support_evidence.difference(soft_draw_context_evidence)
        soft_draw_support_evidence = {
            'under_supports_draw',
            'double_volatility_supports_draw',
        }
        extreme_under_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and ou_line is not None
            and ou_line <= 3.25
            and top_gap >= 0.06
            and top_gap <= 0.12
            and p_h >= 0.32
            and (p_h - p_a) >= 0.10
        )
        deep_soft_extreme_under_rebound = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and ou_line is not None
            and (
                ou_line <= 3.25
                or (
                    review_handicap_depth_bucket == 'level_very_deep'
                    and not bool(review_motivation.get('supports_upset'))
                    and ou_line <= 3.5
                )
            )
            and top_gap >= 0.08
            and top_gap <= 0.13
            and p_h >= 0.305
            and (p_h - p_a) >= 0.045
            and (
                not bool(review_motivation.get('supports_upset'))
                or (
                    review_handicap_depth_bucket == 'level_very_deep'
                    and ou_line >= 3.25
                    and top_gap >= 0.12
                    and review_favored_side == 'home'
                    and str(review_motivation.get('pressure_side') or '').strip() == 'away'
                    and review_score >= 14.0
                )
                or (
                    review_handicap_depth_bucket == 'level_deep'
                    and ou_line <= 3.0
                    and top_gap >= 0.12
                    and review_favored_side == 'home'
                    and str(review_motivation.get('pressure_side') or '').strip() == 'away'
                    and review_score >= 14.0
                )
            )
        )
        very_deep_extreme_under_pressure_rebound = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'away_pressure_review_signal' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket == 'level_very_deep'
            and review_euro_support_bucket == 'draw_soft'
            and ou_line is not None
            and ou_line >= 4.25
            and ou_line <= 5.0
            and ou_under_edge >= 0.44
            and top_gap >= 0.045
            and top_gap <= 0.07
            and p_h >= 0.35
            and (p_h - p_a) >= 0.12
            and bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and str(review_motivation.get('pressure_side') or '').strip() == 'away'
            and review_score >= 14.0
            and home_odds is not None
            and home_odds <= 1.5
            and away_odds is not None
            and away_odds >= 6.0
        )
        fragile_home_strong_support_rebound = bool(
            'review-fragile-home-favorite-correction' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and review_favored_side == 'home'
            and str(review_motivation.get('pressure_side') or '').strip() == 'away'
            and review_score >= 12.0
            and 'away_motivation_pressure' in review_evidence
            and 'handicap_strength_mismatch' not in review_evidence
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
            and 'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and ou_line is not None
            and ou_line <= 3.0
            and top_gap >= 0.045
            and top_gap <= 0.065
            and p_h >= 0.33
            and p_h > p_a
            and (p_h - p_a) <= 0.06
        )
        fragile_home_unconfirmed_draw = bool(
            'review-fragile-home-favorite-correction' in review_signals
            and not fragile_home_strong_support_rebound
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
        )
        away_upset_unconfirmed_draw = bool(
            bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'away'
            and review_score >= 12.0
            and not draw_support_evidence.intersection(diag['evidence'])
            and away_upset_bucket_ok
            and ('draw_market_not_confirmed' in diag['signals'] or top_gap <= 0.13)
        )
        serie_a_soft_draw_away_rescue = bool(
            away_upset_bucket_ok
            and 'review-league-serie-a-draw-to-away-relief' in review_signals
            and (
                (
                    review_favored_side == 'away'
                    and review_score >= 14.0
                )
                or (
                    review_favored_side == 'home'
                    and review_score <= 3.5
                    and away_review_shift >= 0.04
                )
            )
            and 'draw_market_not_confirmed' in diag['signals']
            and set(diag['evidence']).issubset(soft_draw_support_evidence.union({'draw_prob_clear_lead'}))
            and top_gap <= 0.09
            and p_a >= 0.24
        )
        serie_a_soft_blocked_home_rebound = bool(
            'review-league-serie-a-soft-draw-away-blocked-home-top' in review_signals
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'draw_soft'
            and 'draw_market_not_confirmed' in diag['signals']
            and set(diag['evidence']).issubset(soft_draw_support_evidence)
            and ou_line is not None
            and ou_line <= 2.5
            and top_gap <= 0.04
            and p_h >= 0.35
            and (p_h - p_a) >= 0.11
            and home_odds is not None
            and home_odds <= 1.65
            and away_odds is not None
            and away_odds >= 5.0
        )
        serie_a_home_fragility_final_follow_through = bool(
            'review-league-serie-a-home-draw-guard-entry' in review_signals
            and 'review-league-serie-a-home-draw-guard-near-tie' in review_signals
            and review_favored_side == 'away'
            and review_score >= 14.0
            and away_review_shift >= 0.03
            and 'draw_market_not_confirmed' in diag['signals']
            and 'under_supports_draw' in diag['evidence']
            and top_gap <= 0.08
            and p_a >= 0.27
        )
        ligue1_home_edge_final_follow_through = bool(
            'review-league-ligue1-home-edge-trim' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and away_review_shift >= 0.05
            and float(review_applied_shift.get('draw_to_away_trim') or 0.0) >= 0.008
            and 'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and top_gap <= 0.05
            and p_d >= p_a
            and p_a >= 0.31
            and home_odds is not None
            and home_odds <= 1.8
            and draw_odds is not None
            and draw_odds >= 4.0
            and away_odds is not None
            and away_odds >= 4.0
        )
        serie_a_upset_knowledge_draw_override = bool(
            'review-league-serie-a-upset-knowledge-retry' in review_signals
            and 'review-league-serie-a-draw-to-away-relief' in review_signals
            and 'draw_market_not_confirmed' in diag['signals']
            and 'under_supports_draw' in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket in {'strong_support', 'draw_soft'}
            and away_review_shift >= 0.04
            and top_gap <= 0.1
            and p_a >= 0.3
            and p_h <= 0.31
            and away_odds is not None
            and away_odds >= 5.0
            and home_odds is not None
            and home_odds <= 1.7
        )
        home_rebound_unconfirmed_draw = bool(
            not review_signals.intersection({'review-fragile-home-favorite-correction', 'review-narrow-away-bump'})
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'draw_market_not_confirmed' in diag['signals']
            and not home_rebound_blocking_evidence.intersection(diag['evidence'])
            and top_gap <= 0.095
            and p_h >= 0.34
            and (p_h - p_a) >= 0.09
        )
        volatility_only_home_rebound = bool(
            not bool(review_motivation.get('supports_upset'))
            and set(diag['evidence']) == {'double_volatility_supports_draw'}
            and 'draw_market_not_confirmed' in diag['signals']
            and review_handicap_depth_bucket == 'unknown'
            and review_euro_support_bucket == 'market_opposes'
            and ou_line is not None
            and ou_line >= 3.25
            and ou_under_edge >= 0.35
            and top_gap <= 0.018
            and p_h >= 0.34
            and (p_h - p_a) >= 0.05
        )
        deep_soft_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'under_supports_draw' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and top_gap <= 0.12
            and p_h >= 0.32
            and (p_h - p_a) >= 0.10
        )
        deep_soft_extreme_under_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'extreme_under_not_draw_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and 'double_volatility_supports_draw' in diag['evidence']
            and 'under_supports_draw' not in diag['evidence']
            and 'draw_market_supported' not in diag['evidence']
            and review_handicap_depth_bucket in {'level_deep', 'level_very_deep'}
            and review_euro_support_bucket == 'draw_soft'
            and top_gap >= 0.09
            and top_gap <= 0.125
            and p_h >= 0.32
            and (p_h - p_a) >= 0.07
        )
        market_opposed_home_unconfirmed_draw = bool(
            'draw_market_not_confirmed' in diag['signals']
            and 'draw_prob_clear_lead' in diag['evidence']
            and not draw_support_evidence.intersection(diag['evidence'])
            and review_euro_support_bucket == 'market_opposes'
            and top_gap >= 0.045
            and top_gap <= 0.07
            and p_h >= 0.31
            and abs(p_h - p_a) <= 0.02
        )
        near_tie_draw_away_gap_max = 0.066 if narrow_away_bump_present and away_review_shift >= 0.03 else 0.038
        near_tie_home_gap_max = 0.07 if narrow_away_bump_present and away_review_shift >= 0.03 else 0.05
        near_tie_away_promotion = bool(
            away_upset_unconfirmed_draw
            and away_review_shift >= 0.02
            and (
                'draw_market_not_confirmed' in diag['signals']
                or narrow_away_bump_present
            )
            and 'under_supports_draw' not in diag['evidence']
            and abs(p_d - p_a) <= near_tie_draw_away_gap_max
            and abs(p_d - p_h) <= near_tie_home_gap_max
        )
        narrow_away_strong_redirect = bool(
            away_upset_unconfirmed_draw
            and narrow_away_bump_present
            and away_review_shift >= 0.03
            and 'under_supports_draw' not in diag['evidence']
            and top_gap <= 0.07
        )
        if (fragile_home_strong_support_rebound or fragile_home_unconfirmed_draw or away_upset_unconfirmed_draw or home_rebound_unconfirmed_draw or volatility_only_home_rebound or deep_soft_home_unconfirmed_draw or deep_soft_extreme_under_home_unconfirmed_draw or deep_soft_extreme_under_rebound or very_deep_extreme_under_pressure_rebound or market_opposed_home_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_soft_blocked_home_rebound or serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override or extreme_under_unconfirmed_draw) and diag['qualified']:
            removable_evidence = {'draw_prob_clear_lead'}
            if serie_a_soft_draw_away_rescue or serie_a_soft_blocked_home_rebound:
                removable_evidence = removable_evidence.union(soft_draw_support_evidence)
            if serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override:
                removable_evidence = removable_evidence.union({'under_supports_draw', 'double_volatility_supports_draw'})
            if deep_soft_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if deep_soft_extreme_under_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if volatility_only_home_rebound:
                removable_evidence = removable_evidence.union({'double_volatility_supports_draw'})
            if home_rebound_unconfirmed_draw or fragile_home_strong_support_rebound or fragile_home_unconfirmed_draw:
                removable_evidence = removable_evidence.union(soft_draw_context_evidence)
            if set(diag['evidence']).issubset(removable_evidence):
                diag['qualified'] = False
                if fragile_home_strong_support_rebound:
                    diag['signals'].append('fragile_home_strong_support_rebound_override')
                if volatility_only_home_rebound:
                    diag['signals'].append('volatility_only_home_rebound_override')
                if fragile_home_unconfirmed_draw:
                    diag['signals'].append('fragile_home_draw_confirmation_override')
                if away_upset_unconfirmed_draw:
                    diag['signals'].append('away_upset_draw_confirmation_override')
                if home_rebound_unconfirmed_draw:
                    diag['signals'].append('home_rebound_draw_confirmation_override')
                if deep_soft_home_unconfirmed_draw:
                    diag['signals'].append('deep_soft_home_draw_confirmation_override')
                if deep_soft_extreme_under_home_unconfirmed_draw:
                    diag['signals'].append('deep_soft_extreme_under_draw_confirmation_override')
                if deep_soft_extreme_under_rebound:
                    diag['signals'].append('deep_soft_extreme_under_rebound_override')
                if very_deep_extreme_under_pressure_rebound:
                    diag['signals'].append('very_deep_extreme_under_pressure_rebound_override')
                if market_opposed_home_unconfirmed_draw:
                    diag['signals'].append('market_opposed_home_draw_confirmation_override')
                if serie_a_soft_draw_away_rescue:
                    diag['signals'].append('serie_a_soft_draw_confirmation_override')
                if serie_a_soft_blocked_home_rebound:
                    diag['signals'].append('serie_a_soft_blocked_home_draw_confirmation_override')
                if serie_a_home_fragility_final_follow_through:
                    diag['signals'].append('serie_a_home_fragility_draw_confirmation_override')
                if serie_a_upset_knowledge_draw_override:
                    diag['signals'].append('serie_a_upset_knowledge_draw_confirmation_override')
                if extreme_under_unconfirmed_draw:
                    diag['signals'].append('extreme_under_draw_confirmation_override')

        la_liga_weak_gap_draw_retention = bool(
            'review-bias-config-floor' in review_signals
            and review_handicap_depth_bucket == 'level_medium'
            and review_euro_support_bucket == 'strong_support'
            and not bool(review_motivation.get('supports_upset'))
            and review_favored_side == 'home'
            and review_score <= 3.5
            and 'draw_confirmation_gap_weak' in diag['signals']
            and 'draw_market_not_confirmed' in diag['signals']
            and not diag['evidence']
            and ou_line is not None
            and ou_line <= 2.75
            and ou_under_edge is not None
            and ou_under_edge <= 0.3
            and top_gap <= 0.008
            and p_h >= 0.35
            and p_d >= p_h
            and str((diag.get('volatility') or {}).get('home') or '').strip() == 'low'
            and str((diag.get('volatility') or {}).get('away') or '').strip() == 'low'
        )
        if la_liga_weak_gap_draw_retention:
            diag['reason'] = 'la_liga_weak_gap_draw_retained'
            diag['signals'].append('la_liga_weak_gap_draw_retention')
            return final_prob, diag

        if diag['qualified']:
            diag['reason'] = 'draw_confirmation_passed'
            return final_prob, diag

        draw_excess = min(0.026, max(0.0, p_d - runner_up + 0.006))
        if deep_soft_extreme_under_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.07, draw_excess + 0.04)
            diag['signals'].append('deep_soft_extreme_under_draw_excess_boost')
        elif volatility_only_home_rebound and draw_excess > 0:
            diag['signals'].append('volatility_only_home_rebound_draw_excess_base')
        elif deep_soft_extreme_under_rebound and draw_excess > 0:
            draw_excess = min(0.07, draw_excess + 0.04)
            diag['signals'].append('deep_soft_extreme_under_rebound_draw_excess_boost')
        elif very_deep_extreme_under_pressure_rebound and draw_excess > 0:
            draw_excess = min(0.076, draw_excess + 0.046)
            diag['signals'].append('very_deep_extreme_under_pressure_draw_excess_boost')
        elif fragile_home_strong_support_rebound and draw_excess > 0:
            draw_excess = min(0.05, draw_excess + 0.016)
            diag['signals'].append('fragile_home_strong_support_draw_excess_boost')
        elif deep_soft_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.072, draw_excess + 0.044)
            diag['signals'].append('deep_soft_home_draw_excess_boost')
        elif market_opposed_home_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.052, draw_excess + 0.022)
            diag['signals'].append('market_opposed_home_draw_excess_boost')
        elif home_rebound_unconfirmed_draw and draw_excess > 0:
            draw_excess = min(0.05, draw_excess + 0.022)
            diag['signals'].append('home_rebound_draw_excess_boost')
        elif serie_a_home_fragility_final_follow_through and draw_excess > 0:
            draw_excess = min(0.04, draw_excess + 0.014)
            diag['signals'].append('serie_a_home_fragility_draw_excess_boost')
        elif serie_a_upset_knowledge_draw_override and draw_excess > 0:
            draw_excess = min(0.084, draw_excess + 0.054)
            diag['signals'].append('serie_a_upset_knowledge_draw_excess_boost')
        if 'review-fragile-home-favorite-correction' in review_signals and not fragile_home_strong_support_rebound and draw_excess > 0:
            draw_excess = min(0.052, draw_excess + 0.014)
            diag['signals'].append('fragile_home_draw_excess_boost')
        elif away_upset_unconfirmed_draw and draw_excess > 0:
            boost = 0.012 if away_review_shift >= 0.02 else 0.01
            if near_tie_away_promotion:
                boost += 0.008
                diag['signals'].append('near_tie_away_promotion')
            if narrow_away_strong_redirect:
                boost += 0.01
                diag['signals'].append('narrow_away_strong_redirect_boost')
            draw_excess = min(0.052, draw_excess + boost)
            diag['signals'].append('away_upset_draw_excess_boost')
        elif serie_a_soft_draw_away_rescue and draw_excess > 0:
            if review_favored_side == 'home' and review_score <= 3.5 and away_review_shift >= 0.04:
                draw_excess = min(0.084, draw_excess + 0.054)
                diag['signals'].append('serie_a_soft_low_risk_draw_excess_boost')
            else:
                draw_excess = min(0.056, draw_excess + 0.016)
            diag['signals'].append('serie_a_soft_draw_excess_boost')
        if draw_excess <= 0.0:
            diag['reason'] = 'draw_confirmation_no_shift'
            return final_prob, diag
        favored_side = 'home_win' if p_h >= p_a else 'away_win'
        favored_ratio = 0.68 if favored_side == 'home_win' else 0.32
        if deep_soft_extreme_under_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.96
            diag['signals'].append('deep_soft_extreme_under_redirect_draw_to_home')
        elif volatility_only_home_rebound and favored_side == 'home_win':
            favored_ratio = 0.88
            diag['signals'].append('volatility_only_home_rebound_redirect_draw_to_home')
        elif deep_soft_extreme_under_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('deep_soft_extreme_under_rebound_redirect_draw_to_home')
        elif very_deep_extreme_under_pressure_rebound and favored_side == 'home_win':
            favored_ratio = 0.98
            diag['signals'].append('very_deep_extreme_under_pressure_redirect_draw_to_home')
        elif fragile_home_strong_support_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('fragile_home_strong_support_redirect_draw_to_home')
        elif deep_soft_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.98
            diag['signals'].append('deep_soft_redirect_draw_to_home')
        elif market_opposed_home_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.92
            diag['signals'].append('market_opposed_redirect_draw_to_home')
        elif home_rebound_unconfirmed_draw and favored_side == 'home_win':
            favored_ratio = 0.9
            diag['signals'].append('home_rebound_redirect_draw_to_home')
        elif serie_a_soft_blocked_home_rebound and favored_side == 'home_win':
            favored_ratio = 0.94
            diag['signals'].append('serie_a_soft_blocked_home_redirect_draw_to_home')
        if 'premier_league_relegation_home_motivation_bonus' in scenario_tags and favored_side == 'home_win':
            favored_ratio = max(favored_ratio, 0.78)
        if 'review-fragile-home-favorite-correction' in review_signals and not fragile_home_strong_support_rebound and not serie_a_upset_knowledge_draw_override:
            favored_side = 'away_win'
            favored_ratio = 0.78
            if 'away_motivation_pressure' in review_evidence or 'handicap_strength_mismatch' in review_evidence:
                favored_ratio = 0.86
            diag['signals'].append('fragile_home_redirect_draw_to_away')
        elif away_upset_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_home_fragility_final_follow_through or ligue1_home_edge_final_follow_through:
            favored_side = 'away_win'
            favored_ratio = 0.72
            if away_review_shift >= 0.02 or review_score >= 15.0:
                favored_ratio = 0.8
            if near_tie_away_promotion:
                favored_ratio = max(favored_ratio, 0.94 if away_review_shift >= 0.024 else 0.9)
                diag['signals'].append('near_tie_redirect_draw_to_away')
            if narrow_away_strong_redirect:
                favored_ratio = max(favored_ratio, 0.96)
                diag['signals'].append('narrow_away_strong_redirect')
            if serie_a_soft_draw_away_rescue:
                if review_favored_side == 'home' and review_score <= 3.5 and away_review_shift >= 0.04:
                    favored_ratio = max(favored_ratio, 1.0)
                    diag['signals'].append('serie_a_soft_low_risk_full_redirect')
                else:
                    favored_ratio = max(favored_ratio, 0.96)
                diag['signals'].append('serie_a_soft_redirect_draw_to_away')
            if serie_a_home_fragility_final_follow_through:
                favored_ratio = max(favored_ratio, 0.94)
                diag['signals'].append('serie_a_home_fragility_redirect_draw_to_away')
            if ligue1_home_edge_final_follow_through:
                favored_ratio = max(favored_ratio, 0.94)
                diag['signals'].append('ligue1_home_edge_redirect_draw_to_away')
            diag['signals'].append('away_upset_redirect_draw_to_away')
        elif serie_a_upset_knowledge_draw_override:
            favored_side = 'away_win'
            favored_ratio = 1.0
            diag['signals'].append('serie_a_upset_knowledge_redirect_draw_to_away')
        p_d -= draw_excess
        if favored_side == 'home_win':
            p_h += draw_excess * favored_ratio
            p_a += draw_excess * (1.0 - favored_ratio)
        else:
            p_a += draw_excess * favored_ratio
            p_h += draw_excess * (1.0 - favored_ratio)
        total = p_h + p_d + p_a
        if total > 0:
            p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
        if (
            (home_rebound_unconfirmed_draw or fragile_home_strong_support_rebound or volatility_only_home_rebound)
            and favored_side == 'home_win'
            and p_d >= p_h
            and (p_d - p_h) <= 0.002
        ):
            home_rebound_trim = min(0.0022, (p_d - p_h) + 0.0008, max(0.0, p_d - 0.02))
            if home_rebound_trim > 0:
                p_d -= home_rebound_trim
                p_h += home_rebound_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                if fragile_home_strong_support_rebound:
                    diag['signals'].append('fragile_home_strong_support_near_tie_trim')
                elif volatility_only_home_rebound:
                    diag['signals'].append('volatility_only_home_rebound_near_tie_trim')
                else:
                    diag['signals'].append('home_rebound_near_tie_trim')
        away_near_tie_trim_limit = 0.006 if serie_a_home_fragility_final_follow_through else (0.0045 if (narrow_away_strong_redirect or serie_a_soft_draw_away_rescue) else 0.0025)
        if (
            favored_side == 'away_win'
            and (away_upset_unconfirmed_draw or serie_a_soft_draw_away_rescue or serie_a_home_fragility_final_follow_through or serie_a_upset_knowledge_draw_override)
            and p_h >= p_a
            and (p_h - p_a) <= away_near_tie_trim_limit
        ):
            away_rebound_trim = min(0.0028, (p_h - p_a) + 0.0008, max(0.0, p_h - 0.02))
            if away_rebound_trim > 0:
                p_h -= away_rebound_trim
                p_a += away_rebound_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('away_upset_near_tie_trim')
                if serie_a_soft_draw_away_rescue:
                    diag['signals'].append('serie_a_soft_near_tie_trim')
                if serie_a_home_fragility_final_follow_through:
                    diag['signals'].append('serie_a_home_fragility_near_tie_trim')
        if (
            serie_a_soft_draw_away_rescue
            and review_favored_side == 'home'
            and review_score <= 3.5
            and away_review_shift >= 0.04
            and favored_side == 'away_win'
            and p_h >= p_a
            and (p_h - p_a) <= 0.007
        ):
            serie_a_soft_final_trim = min(0.0038, (p_h - p_a) + 0.0006, max(0.0, p_h - 0.02))
            if serie_a_soft_final_trim > 0:
                p_h -= serie_a_soft_final_trim
                p_a += serie_a_soft_final_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('serie_a_soft_final_margin_trim')
        if (
            serie_a_home_fragility_final_follow_through
            and favored_side == 'away_win'
            and p_h >= p_a
            and (p_h - p_a) <= 0.02
        ):
            serie_a_home_fragility_final_trim = min(0.0106, ((p_h - p_a) * 0.5) + 0.0009, max(0.0, p_h - 0.02))
            if serie_a_home_fragility_final_trim > 0:
                p_h -= serie_a_home_fragility_final_trim
                p_a += serie_a_home_fragility_final_trim
                total = p_h + p_d + p_a
                if total > 0:
                    p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
                diag['signals'].append('serie_a_home_fragility_final_trim')
        diag.update(
            {
                'applied': True,
                'reason': 'draw_confirmation_failed_shifted',
                'shift': round(draw_excess, 4),
                'favored_side': favored_side,
                'adjusted_probabilities': {
                    'home_win': round(p_h, 6),
                    'draw': round(p_d, 6),
                    'away_win': round(p_a, 6),
                },
            }
        )
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    @staticmethod
    def _parse_handicap_value(value: Any) -> Optional[float]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            numeric = float(value)
            return numeric if abs(numeric) > 1e-9 else 0.0
        raw = str(value).strip()
        if not raw:
            return None
        try:
            if '/' in raw and not any(ch in raw for ch in '球平受让'):
                parts = [float(item) for item in raw.split('/') if item]
                if parts:
                    return sum(parts) / len(parts)
            return float(raw)
        except Exception:
            pass
        mapping = {
            '平手': 0.0,
            '平手/半球': -0.25,
            '平/半': -0.25,
            '半球': -0.5,
            '半球/一球': -0.75,
            '半/一': -0.75,
            '一球': -1.0,
            '一球/球半': -1.25,
            '一/球半': -1.25,
            '球半': -1.5,
            '球半/两球': -1.75,
            '两球': -2.0,
            '两球/两球半': -2.25,
            '两球半': -2.5,
            '两球半/三球': -2.75,
            '三球': -3.0,
            '三球/三球半': -3.25,
            '三球半': -3.5,
            '三球半/四球': -3.75,
            '四球': -4.0,
            '四球/四球半': -4.25,
            '四球半': -4.5,
            '四球半/五球': -4.75,
            '五球': -5.0,
            '受让平手': 0.0,
            '受让平手/半球': 0.25,
            '受让平/半': 0.25,
            '受让半球': 0.5,
            '受让半球/一球': 0.75,
            '受让半/一': 0.75,
            '受让一球': 1.0,
            '受让一球/球半': 1.25,
            '受让一/球半': 1.25,
            '受让球半': 1.5,
            '受让球半/两球': 1.75,
            '受让两球': 2.0,
            '受让两球/两球半': 2.25,
            '受让两球半': 2.5,
            '受让两球半/三球': 2.75,
            '受让三球': 3.0,
            '受让三球/三球半': 3.25,
            '受让三球半': 3.5,
            '受让三球半/四球': 3.75,
            '受让四球': 4.0,
            '受平手': 0.0,
            '受平手/半球': 0.25,
            '受平/半': 0.25,
            '受半球': 0.5,
            '受半': 0.5,
            '受半球/一球': 0.75,
            '受半/一': 0.75,
            '受一球': 1.0,
            '受一球/球半': 1.25,
            '受一/球半': 1.25,
            '受球半': 1.5,
            '受球半/两球': 1.75,
            '受两球': 2.0,
            '受两球/两球半': 2.25,
            '受两球半': 2.5,
            '受两球半/三球': 2.75,
            '受三球': 3.0,
            '受三球/三球半': 3.25,
            '受三球半': 3.5,
            '受三球半/四球': 3.75,
            '受四球': 4.0,
        }
        return mapping.get(raw.replace(' ', ''))

    def _build_preliminary_upset_potential(
        self,
        *,
        home_team: str,
        away_team: str,
        league_code: str,
        strength_diff: float,
        predicted_outcome: str,
        asian_handicap: Optional[Dict[str, Any]],
        european_odds: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        preliminary: Dict[str, Any] = {
            'available': False,
            'motivation_risk': {},
            'handicap_strength_mismatch': {},
        }
        analyzer = self.upset_analyzer
        if analyzer is None:
            return preliminary
        try:
            if hasattr(analyzer, '_score_motivation_risk'):
                motivation_risk = analyzer._score_motivation_risk(
                    match_intelligence=match_intelligence,
                    strength_diff=strength_diff,
                    predicted_outcome=predicted_outcome,
                )
                if isinstance(motivation_risk, dict):
                    preliminary['motivation_risk'] = motivation_risk
        except Exception:
            preliminary['motivation_risk'] = {}
        try:
            if hasattr(analyzer, 'analyze_handicap_vs_strength'):
                mismatch = analyzer.analyze_handicap_vs_strength(
                    home_team=home_team,
                    away_team=away_team,
                    strength_diff=strength_diff,
                    asian_handicap=asian_handicap,
                    european_odds=european_odds,
                )
                if isinstance(mismatch, dict):
                    preliminary['handicap_strength_mismatch'] = mismatch
        except Exception:
            preliminary['handicap_strength_mismatch'] = {}
        preliminary['available'] = bool(
            (isinstance(preliminary.get('motivation_risk'), dict) and preliminary['motivation_risk'].get('available'))
            or (
                isinstance(preliminary.get('handicap_strength_mismatch'), dict)
                and preliminary['handicap_strength_mismatch'].get('mismatch_detected')
            )
        )
        return preliminary

    @staticmethod
    def _build_serie_a_upset_knowledge_retry_gate(
        *,
        final_prob: Dict[str, float],
        review_outcome_diag: Optional[Dict[str, Any]],
        draw_guard_diag: Optional[Dict[str, Any]],
        upset_potential: Optional[Dict[str, Any]],
        main_prediction: str,
        league_code: str,
    ) -> Dict[str, Any]:
        gate: Dict[str, Any] = {
            'eligible': False,
            'reason': 'not_serie_a',
            'knowledge_score': 0.0,
            'similar_cases_count': 0,
            'case_knowledge_available': False,
            'reverse_rate': 0.0,
            'cold_rate': 0.0,
            'draw_shift': 0.0,
            'away_shift': 0.0,
        }
        if league_code != 'serie_a':
            return gate
        if not isinstance(final_prob, dict):
            gate['reason'] = 'invalid_final_prob'
            return gate

        review_diag = review_outcome_diag if isinstance(review_outcome_diag, dict) else {}

        if main_prediction not in {'主胜', '平局'}:
            gate['reason'] = 'not_home_or_draw_top'
            return gate
        review_signals = {
            str(item).strip()
            for item in (review_diag.get('signals') or [])
            if str(item).strip()
        }
        if 'review-league-serie-a-soft-draw-away-blocked-home-top' not in review_signals:
            gate['reason'] = 'blocked_soft_draw_signal_missing'
            return gate

        draw_diag = draw_guard_diag if isinstance(draw_guard_diag, dict) else {}
        draw_guard_signals = {
            str(item).strip()
            for item in (draw_diag.get('signals') or [])
            if str(item).strip()
        }
        draw_guard_evidence = {
            str(item).strip()
            for item in (draw_diag.get('evidence') or [])
            if str(item).strip()
        }
        three_layer_context = review_diag.get('three_layer_context') if isinstance(review_diag.get('three_layer_context'), dict) else {}
        retryable_blocked_soft_draw = bool(
            'draw_market_not_confirmed' in draw_guard_signals
            and 'under_supports_draw' in draw_guard_evidence
            and str(three_layer_context.get('handicap_depth_bucket') or '').strip() == 'level_medium'
            and str(three_layer_context.get('euro_support_bucket') or '').strip() == 'draw_soft'
        )
        if (
            'serie_a_soft_blocked_home_redirect_draw_to_home' not in draw_guard_signals
            and not retryable_blocked_soft_draw
        ):
            gate['reason'] = 'blocked_home_rebound_missing'
            return gate

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        if p_d < 0.36 or p_a < 0.22 or (p_h - p_d) > 0.025:
            gate['reason'] = 'probability_shape_not_retryable'
            return gate

        knowledge = upset_potential if isinstance(upset_potential, dict) else {}
        risk_score_detail = knowledge.get('risk_score_detail') if isinstance(knowledge.get('risk_score_detail'), dict) else {}
        knowledge_score = float(risk_score_detail.get('knowledge_score') or 0.0)
        similar_cases_count = int(knowledge.get('similar_cases_count') or 0)
        case_knowledge = knowledge.get('case_knowledge') if isinstance(knowledge.get('case_knowledge'), dict) else {}
        case_knowledge_available = bool(case_knowledge.get('available'))
        historical_reference = knowledge.get('historical_odds_reference') if isinstance(knowledge.get('historical_odds_reference'), dict) else {}
        historical_summary = historical_reference.get('summary') if isinstance(historical_reference.get('summary'), dict) else {}
        result_rates = historical_summary.get('result_rates') if isinstance(historical_summary.get('result_rates'), dict) else {}
        reverse_rate = 1.0 - float(result_rates.get('主胜') or 0.0) if result_rates else 0.0
        cold_rate = float(historical_summary.get('cold_result_rate') or 0.0)
        gate.update(
            {
                'knowledge_score': round(knowledge_score, 4),
                'similar_cases_count': similar_cases_count,
                'case_knowledge_available': case_knowledge_available,
                'reverse_rate': round(reverse_rate, 4),
                'cold_rate': round(cold_rate, 4),
            }
        )
        if knowledge_score < 16.0 and not (similar_cases_count >= 1 and case_knowledge_available):
            gate['reason'] = 'knowledge_signal_weak'
            return gate
        if reverse_rate > 0 and reverse_rate < 0.55 and cold_rate < 0.35 and knowledge_score < 18.0:
            gate['reason'] = 'historical_reverse_rate_weak'
            return gate

        gate.update(
            {
                'eligible': True,
                'reason': 'eligible',
                'draw_shift': 0.012,
                'away_shift': 0.046,
            }
        )
        return gate

    def apply_dynamic_weights(self, league_code: str) -> Dict[str, Any]:
        try:
            diag = self.weight_adjuster.get_adjustment_diagnostics(league_code)
            weights = diag.get('final_weights')
            if weights and hasattr(self.model_fusion, 'set_model_weights'):
                self.model_fusion.set_model_weights(weights)
            return diag
        except Exception as exc:
            return {'league_code': league_code, 'error': str(exc)}

    def _market_implied_1x2(self, european_odds: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        """从欧赔 final 反推归一化市场隐含 1X2 概率；无有效赔率返回 None。"""
        if not isinstance(european_odds, dict):
            return None
        final = european_odds.get('final')
        if not isinstance(final, dict):
            return None
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            return None
        ph, pd, pa = 1.0 / oh, 1.0 / od, 1.0 / oa
        total = ph + pd + pa
        return {'home_win': ph / total, 'draw': pd / total, 'away_win': pa / total}

    @staticmethod
    def _favor_label_from_side(side: Optional[str]) -> str:
        if str(side or '').strip() == 'home':
            return '主胜'
        if str(side or '').strip() == 'away':
            return '客胜'
        return ''

    def _apply_outcome_stability_guard(
        self,
        *,
        ranked_probabilities: List[Tuple[str, float]],
        anchor_ranked_probabilities: Optional[List[Tuple[str, float]]],
        draw_guard_diag: Optional[Dict[str, Any]] = None,
        operation_adj_diag: Optional[Dict[str, Any]] = None,
        review_outcome_diag: Optional[Dict[str, Any]] = None,
        sentiment_adj_diag: Optional[Dict[str, Any]] = None,
        gap_threshold: float = 0.012,
    ) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'eligible': False}
        if not ranked_probabilities or len(ranked_probabilities) < 2:
            diag['reason'] = 'insufficient_ranking'
            return ranked_probabilities, diag
        if not anchor_ranked_probabilities or len(anchor_ranked_probabilities) < 1:
            diag['reason'] = 'missing_anchor_ranking'
            return ranked_probabilities, diag

        anchor_top = str(anchor_ranked_probabilities[0][0] or '').strip()
        top_label, top_prob = ranked_probabilities[0]
        second_label, second_prob = ranked_probabilities[1]
        top_label = str(top_label or '').strip()
        second_label = str(second_label or '').strip()
        gap = float(top_prob or 0.0) - float(second_prob or 0.0)
        diag.update({
            'anchor_top': anchor_top,
            'current_top': top_label,
            'runner_up': second_label,
            'gap': round(float(gap), 4),
            'gap_threshold': gap_threshold,
        })
        if not anchor_top:
            diag['reason'] = 'empty_anchor_label'
            return ranked_probabilities, diag
        if gap > gap_threshold + 1e-9:
            diag['reason'] = 'gap_too_large'
            return ranked_probabilities, diag
        if top_label == anchor_top:
            diag['reason'] = 'top_label_unchanged'
            return ranked_probabilities, diag
        if second_label != anchor_top:
            diag['reason'] = 'anchor_not_runner_up'
            return ranked_probabilities, diag

        draw_guard_diag = draw_guard_diag if isinstance(draw_guard_diag, dict) else {}
        operation_adj_diag = operation_adj_diag if isinstance(operation_adj_diag, dict) else {}
        review_outcome_diag = review_outcome_diag if isinstance(review_outcome_diag, dict) else {}
        sentiment_adj_diag = sentiment_adj_diag if isinstance(sentiment_adj_diag, dict) else {}
        favored_label = self._favor_label_from_side(operation_adj_diag.get('favored_side'))
        draw_guard_applied = bool(draw_guard_diag.get('applied'))
        draw_guard_qualified = bool(draw_guard_diag.get('qualified'))
        operation_applied = bool(operation_adj_diag.get('applied'))
        operation_direction = str(operation_adj_diag.get('direction') or '').strip()
        sentiment_applied = bool(sentiment_adj_diag.get('applied'))
        review_applied = bool(review_outcome_diag.get('applied'))
        current_supported = bool(
            (top_label == '平局' and draw_guard_applied)
            or (operation_applied and operation_direction == 'endorse' and favored_label == top_label)
        )
        anchor_supported = bool(
            (anchor_top == '平局' and draw_guard_qualified and not draw_guard_applied)
            or (operation_applied and favored_label == anchor_top and operation_direction in {'endorse', 'deceptive'})
        )
        diag.update({
            'draw_guard_applied': draw_guard_applied,
            'draw_guard_qualified': draw_guard_qualified,
            'operation_applied': operation_applied,
            'operation_direction': operation_direction,
            'favored_label': favored_label,
            'review_applied': review_applied,
            'sentiment_applied': sentiment_applied,
            'current_supported': current_supported,
            'anchor_supported': anchor_supported,
        })
        diag['eligible'] = True
        if current_supported and not anchor_supported:
            diag['reason'] = 'current_top_has_confirming_signal'
            return ranked_probabilities, diag
        if not (anchor_supported or draw_guard_qualified or sentiment_applied or review_applied or operation_applied):
            diag['reason'] = 'no_instability_evidence'
            return ranked_probabilities, diag

        promoted = [item for item in ranked_probabilities if str(item[0] or '').strip() == anchor_top]
        remainder = [item for item in ranked_probabilities if str(item[0] or '').strip() != anchor_top]
        if not promoted:
            diag['reason'] = 'anchor_label_missing_from_current_ranking'
            return ranked_probabilities, diag
        reordered = promoted + remainder
        diag.update({
            'applied': True,
            'reason': 'anchor_top_reinstated_for_near_tie',
            'preferred_direction': anchor_top,
            'preferred_outcomes': [anchor_top, top_label],
        })
        return reordered, diag

    def _apply_draw_proximity_promotion(
        self,
        *,
        ranked_probabilities: List[Tuple[str, float]],
        european_odds: Optional[Dict[str, Any]],
        home_advantage: float,
        gap_threshold: float = 0.05,
        market_draw_floor: float = 0.27,
    ) -> Tuple[List[Tuple[str, float]], Dict[str, Any]]:
        """平局接近触发：中立场地下，当平局排第 2 且与最高仅差 gap_threshold、
        且市场欧赔隐含平局相对偏高时，把平局提升为 top-1。

        仅在中立场地（home_advantage<=1.0，世界杯非东道主）启用：联赛 1.12 /
        友谊赛 1.03 / 东道主 1.08 均不触发，保证零回归。不改动概率本身，只调整
        最终选边，吃下 argmax 临门差（平局做次高态）的平局场次。
        """
        diag: Dict[str, Any] = {'applied': False}
        if not (home_advantage <= 1.0 + 1e-9):
            diag['reason'] = 'venue_not_neutral'
            return ranked_probabilities, diag
        if not ranked_probabilities or len(ranked_probabilities) < 2:
            diag['reason'] = 'insufficient_ranking'
            return ranked_probabilities, diag
        top_label, top_prob = ranked_probabilities[0]
        second_label, second_prob = ranked_probabilities[1]
        if top_label == '平局' or second_label != '平局':
            diag['reason'] = 'draw_not_runner_up'
            return ranked_probabilities, diag
        gap = float(top_prob) - float(second_prob)
        diag['model_gap'] = round(gap, 4)
        market_probs = self._market_implied_1x2(european_odds)
        market_draw = float(market_probs.get('draw')) if isinstance(market_probs, dict) else None
        diag['market_draw'] = round(market_draw, 4) if market_draw is not None else None

        # 低置信防平：世界杯/中立赛程里，首选胜负低于 40% 且平局只落后 6 个百分点内，
        # 本质是三分布均衡盘。若市场隐含平局不低（>=27%），把「防平」提升为主口径，
        # 避免 37%-32% 这类弱客胜/弱主胜被写成单选方向。
        low_conf_draw_guard = bool(
            float(top_prob or 0.0) < 0.40
            and gap <= 0.06 + 1e-9
            and market_draw is not None
            and market_draw >= market_draw_floor
        )
        if low_conf_draw_guard:
            promoted = [ranked_probabilities[1], ranked_probabilities[0]] + list(ranked_probabilities[2:])
            diag.update({
                'applied': True,
                'reason': 'low_confidence_draw_guard',
                'gap_threshold': 0.06,
                'market_draw_floor': market_draw_floor,
                'from_label': top_label,
                'to_label': '平局',
                'top_prob': round(float(top_prob), 4),
                'draw_prob': round(float(second_prob), 4),
            })
            return promoted, diag

        if gap > gap_threshold + 1e-9:
            diag['reason'] = 'gap_too_large'
            return ranked_probabilities, diag
        if market_draw is None or market_draw < market_draw_floor:
            diag['reason'] = 'market_draw_not_elevated'
            return ranked_probabilities, diag
        promoted = [ranked_probabilities[1], ranked_probabilities[0]] + list(ranked_probabilities[2:])
        diag.update({
            'applied': True,
            'reason': 'draw_proximity_promotion',
            'gap_threshold': gap_threshold,
            'market_draw_floor': market_draw_floor,
            'from_label': top_label,
            'to_label': '平局',
            'top_prob': round(float(top_prob), 4),
            'draw_prob': round(float(second_prob), 4),
        })
        return promoted, diag

    def _resolve_market_alpha(
        self,
        market_probs: Optional[Dict[str, float]],
        applied_weights: Dict[str, Any],
        expert_signals: Dict[str, Any],
        strength_quality: str = 'real',
        odds_anomaly: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """按实战口径动态选 α：常规 0.35 / 热门强队 0.25 / 小联赛-数据少 0.5 / 伤停换帅 0.65。

        无市场概率时 α=0（等价只用模型）。
        当球队强度无真实数据支撑（national_fallback / flat_default）时，模型硬数据可信度低，
        进一步提高市场权重，避免无数据场景预测趋同。

        热门强队的"防诱盘"降权只在盘口本身可疑时才触发：复用 detect_market_odds_anomaly 的
        可信度（level/trusted），区分"诱盘 vs 真实调整"——多公司一致、亚欧同向的强主/强客
        是真实信号，应继续信任市场而非压低市场权重；只有盘口异常（少公司/亚欧背离）才防诱盘。
        """
        if not isinstance(market_probs, dict):
            return {'alpha': 0.0, 'reason': 'no_market_probs', 'regime': 'model_only',
                    'strength_quality': strength_quality}

        alpha = getattr(self.model_fusion, 'DEFAULT_MARKET_ALPHA', 0.35)
        regime = 'normal'
        reason = '常规场景，模型为主市场为辅'

        # 小联赛 / 数据少：市场更可信
        has_enough = bool(applied_weights.get('has_enough_samples', False))
        if not has_enough:
            alpha = 0.5
            regime = 'data_poor'
            reason = '联赛样本不足/小联赛，提高市场权重'

        # 球队强度无真实球员数据支撑：模型硬数据不可信，市场主导
        if strength_quality in ('fallback', 'flat'):
            floor = 0.6 if strength_quality == 'flat' else 0.5
            if alpha < floor:
                alpha = floor
            if regime in ('normal', 'data_poor'):
                regime = 'no_strength_data'
                reason = '球队无真实球员数据（国家队兜底/默认值），提高市场权重防趋同'

        # 突发消息（核心伤停 / 多伤停 / 换帅）：市场资金已反映消息
        out_gap = abs(int(expert_signals.get('home_suspensions', 0)) + int(expert_signals.get('away_suspensions', 0)))
        heavy_absence = (
            expert_signals.get('home_key_available') is False
            or expert_signals.get('away_key_available') is False
            or bool(expert_signals.get('coach_change'))
            or out_gap >= 2
        )
        if heavy_absence:
            alpha = max(alpha, 0.65)
            regime = 'news_shock'
            reason = '核心伤停/停赛或换帅等突发消息，市场资金已反映'

        # 热门强队（市场强烈看好一方）：只有盘口本身可疑时才降权防诱盘。
        # fav_prob 用 max(home_win, away_win) 不分主客，强主/强客走同一规则。
        # 复用 detect_market_odds_anomaly 的可信度：多公司一致、亚欧同向（level=none/low）
        # 是真实强主/强客调整，应继续信市场；只有盘口异常（level=medium/high，少公司/亚欧
        # 背离）才认定诱盘并压低市场权重。注意 detect 里 trusted 仅在 level=='none' 时为 True，
        # 故判别只按 level（low 也属可信，不降权）。
        fav_prob = max(market_probs.get('home_win', 0.0), market_probs.get('away_win', 0.0))
        anomaly_level = (odds_anomaly or {}).get('level', 'none')
        odds_suspicious = anomaly_level in ('medium', 'high')
        if fav_prob >= 0.60 and regime in ('normal', 'data_poor'):
            if odds_suspicious:
                alpha = min(alpha, 0.25)
                regime = 'hot_favorite'
                reason = '市场强烈看好一方且盘口可疑，降低市场权重防诱盘'
            else:
                regime = 'hot_favorite_trusted'
                reason = '市场强烈看好一方但盘口可信（多公司一致），维持市场权重信任真实调整'

        return {'alpha': round(float(alpha), 4), 'reason': reason, 'regime': regime,
                'market_favorite_prob': round(float(fav_prob), 4),
                'odds_anomaly_level': anomaly_level,
                'strength_quality': strength_quality}

    def _build_expert_signals(
        self,
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
        home_motivation: float,
        away_motivation: float,
        match_intelligence: Optional[Dict[str, Any]],
        market_probs: Optional[Dict[str, float]],
    ) -> Dict[str, Any]:
        """组装 expert 模型的真实情报输入。"""
        signals: Dict[str, Any] = {
            'home_suspensions': int(home_strength.get('suspended_count', 0) or 0),
            'away_suspensions': int(away_strength.get('suspended_count', 0) or 0),
            'home_key_available': bool(home_strength.get('key_players_available', True)),
            'away_key_available': bool(away_strength.get('key_players_available', True)),
            'home_motivation': float(home_motivation),
            'away_motivation': float(away_motivation),
        }
        # 情报净倾向：用 quant_adjustment 的 λ 缩放差折算主队净利好（-1~1）
        if isinstance(match_intelligence, dict):
            quant = match_intelligence.get('quant_adjustment') or {}
            try:
                home_scale = float(quant.get('home_lambda_scale') or 1.0)
                away_scale = float(quant.get('away_lambda_scale') or 1.0)
                signals['intel_home_bias'] = max(-1.0, min(1.0, (home_scale - away_scale)))
            except (TypeError, ValueError):
                signals['intel_home_bias'] = 0.0
            if match_intelligence.get('coach_change') or match_intelligence.get('head_coach_change'):
                signals['coach_change'] = True
        # 市场热门方向
        if isinstance(market_probs, dict):
            ph = market_probs.get('home_win', 0.0)
            pa = market_probs.get('away_win', 0.0)
            if ph >= pa:
                signals['market_favorite'] = 'home'
                signals['market_favorite_strength'] = max(0.0, min(1.0, (ph - 0.4) / 0.4))
            else:
                signals['market_favorite'] = 'away'
                signals['market_favorite_strength'] = max(0.0, min(1.0, (pa - 0.4) / 0.4))
        return signals

    def detect_market_odds_anomaly(
        self,
        league_code: str,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        base_home_lambda: Optional[float] = None,
        base_away_lambda: Optional[float] = None,
    ) -> Dict[str, Any]:
        diag: Dict[str, Any] = {
            'available': False,
            'trusted': True,
            'score': 0.0,
            'level': 'none',
            'reasons': [],
            'signals': [],
            'calibration_weight': 1.0,
        }
        if not isinstance(european_odds, dict):
            diag['reason'] = 'missing_european_odds'
            return diag
        final = european_odds.get('final')
        if not isinstance(final, dict):
            diag['reason'] = 'missing_euro_final'
            return diag
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            diag['reason'] = 'invalid_euro_final'
            return diag

        diag['available'] = True
        ph = 1.0 / oh
        pd = 1.0 / od
        pa = 1.0 / oa
        total = ph + pd + pa
        ph, pd, pa = ph / total, pd / total, pa / total
        fav_side = 'home' if ph >= pa else 'away'
        fav_prob = ph if fav_side == 'home' else pa
        diag['market_implied_probs'] = {'home': round(ph, 4), 'draw': round(pd, 4), 'away': round(pa, 4)}
        diag['market_favorite'] = fav_side

        score = 0.0
        reasons: List[str] = []
        signals: List[str] = []

        company_mode = str(european_odds.get('company_mode') or '')
        consensus = european_odds.get('consensus') if isinstance(european_odds.get('consensus'), dict) else {}
        filtered_company_count = int(consensus.get('filtered_company_count') or 0)
        company_count = int(consensus.get('company_count') or 0)
        diag['company_mode'] = company_mode or 'unknown'
        diag['filtered_company_count'] = filtered_company_count
        diag['company_count'] = company_count

        if company_mode == 'average_row_fallback':
            score += 0.18
            reasons.append('欧赔仅拿到平均值回退')
            signals.append('average_row_fallback')
        elif filtered_company_count and filtered_company_count < 4:
            score += 0.12
            reasons.append(f'欧赔共识公司过少({filtered_company_count})')
            signals.append('low_company_count')

        if isinstance(asian_handicap, dict):
            fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
            ini = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}

            def _resolve_handicap(row: Dict[str, Any]) -> Optional[float]:
                # 按优先级遍历候选字段，跳过值为 None/空的键，
                # 避免快照里 handicap=null 但 handicap_text="三球半" 时漏读文字盘口。
                for key in ('handicap', 'handicap_value', '盘口值', 'handicap_text', '盘口'):
                    if key in row and row[key] not in (None, ''):
                        parsed = self._parse_handicap_value(row[key])
                        if parsed is not None:
                            return parsed
                return None

            hcp_final = _resolve_handicap(fin)
            hw_f = self._to_float(fin.get('home_water'))
            aw_f = self._to_float(fin.get('away_water'))
            hcp_initial = _resolve_handicap(ini)
            giver = None
            if hcp_final is not None:
                if hcp_final < -0.06:
                    giver = 'home'
                elif hcp_final > 0.06:
                    giver = 'away'
            diag['asian_final_handicap'] = hcp_final
            diag['asian_giver'] = giver
            strong_market = fav_prob >= 0.56
            very_strong_market = fav_prob >= 0.62
            hcp_mag = abs(hcp_final) if hcp_final is not None else None
            if strong_market and giver and giver != fav_side:
                score += 0.55
                reasons.append('欧赔强侧与亚值让步方向相反')
                signals.append('favorite_direction_mismatch')
            elif very_strong_market and (giver is None or hcp_mag is None or hcp_mag < 0.75):
                score += 0.42
                reasons.append('欧赔强侧明显但亚值让步不足')
                signals.append('favorite_depth_mismatch')
            elif strong_market and hcp_mag is not None and hcp_mag < 0.25:
                score += 0.22
                reasons.append('欧赔偏强但亚值接近平手')
                signals.append('near_level_handicap')
            if fav_side == 'home' and hw_f and hw_f >= 2.15 and very_strong_market:
                score += 0.18
                reasons.append('主强侧赔率低但主队终水偏高')
                signals.append('home_water_high_vs_low_odds')
            if fav_side == 'away' and aw_f and aw_f >= 2.15 and very_strong_market:
                score += 0.18
                reasons.append('客强侧赔率低但客队终水偏高')
                signals.append('away_water_high_vs_low_odds')
            if hcp_initial is not None and hcp_final is not None and abs(hcp_final) + 0.24 < abs(hcp_initial) and very_strong_market:
                score += 0.12
                reasons.append('亚值明显退盘但欧赔仍保持强侧')
                signals.append('retreat_vs_strong_odds')

        if base_home_lambda and base_away_lambda:
            dc = DixonColesModel(rho=resolve_rho(league_code))
            base_probs = dc.predict_with_dixon_coles(max(0.15, float(base_home_lambda)), max(0.15, float(base_away_lambda)))
            base_home = float(base_probs.get('home_win') or 0.0)
            base_away = float(base_probs.get('away_win') or 0.0)
            base_fav_side = 'home' if base_home >= base_away else 'away'
            base_fav_prob = base_home if base_fav_side == 'home' else base_away
            market_base_gap = fav_prob - base_fav_prob if fav_side == base_fav_side else fav_prob + base_fav_prob - 0.5
            diag['base_model_probs'] = {'home': round(base_home, 4), 'draw': round(float(base_probs.get('draw') or 0.0), 4), 'away': round(base_away, 4)}
            diag['base_model_favorite'] = base_fav_side
            if fav_side != base_fav_side and fav_prob >= 0.54:
                score += 0.28
                reasons.append('欧赔强侧与基础模型方向相反')
                signals.append('base_model_direction_mismatch')
            elif market_base_gap >= 0.16 and fav_prob >= 0.58:
                score += 0.22
                reasons.append('欧赔强度显著高于基础模型')
                signals.append('base_model_gap_large')

        level = 'none'
        weight = 1.0
        if score >= 0.72:
            level = 'high'
            weight = 0.0
        elif score >= 0.42:
            level = 'medium'
            weight = 0.35
        elif score >= 0.18:
            level = 'low'
            weight = 0.7
        diag['score'] = round(score, 4)
        diag['level'] = level
        diag['trusted'] = level == 'none'
        diag['reasons'] = reasons
        diag['signals'] = signals
        diag['calibration_weight'] = weight
        return diag

    def detect_market_movement_sentiment(
        self,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """识别盘口动态运动中的「虚热诱下」情绪信号（initial→final）。

        两类诱下特征：
        1) 欧赔虚热：热门侧赔率被压低 + 冷门侧赔率被明显抬高 → 公众资金被往热门方向赶。
        2) 盘水背离：亚盘不升盘（甚至退盘）却让热门侧水位大幅走低 → 庄家不愿为"赢球"
           背书、只用低水/低赔制造表面铁热；或升盘同时配高水（抬人气诱下）。

        返回 luring_side（被诱、即应警惕方向）+ strength（0~1）+ reasons/signals。
        仅做检测，不改概率（由 apply_market_sentiment_adjustment 消费）。
        """
        diag: Dict[str, Any] = {
            'available': False,
            'detected': False,
            'luring_side': None,
            'strength': 0.0,
            'reasons': [],
            'signals': [],
        }
        if not isinstance(european_odds, dict):
            diag['reason'] = 'missing_european_odds'
            return diag
        ini = european_odds.get('initial') if isinstance(european_odds.get('initial'), dict) else {}
        fin = european_odds.get('final') if isinstance(european_odds.get('final'), dict) else {}
        oh_i, od_i, oa_i = self._to_float(ini.get('home')), self._to_float(ini.get('draw')), self._to_float(ini.get('away'))
        oh_f, od_f, oa_f = self._to_float(fin.get('home')), self._to_float(fin.get('draw')), self._to_float(fin.get('away'))
        if not all(isinstance(v, float) and v > 1.01 for v in (oh_i, od_i, oa_i, oh_f, od_f, oa_f)):
            diag['reason'] = 'missing_euro_initial_or_final'
            return diag
        diag['available'] = True

        # 终盘市场强侧
        ph_f, pa_f = 1.0 / oh_f, 1.0 / oa_f
        fav_side = 'home' if ph_f >= pa_f else 'away'
        dog_side = 'away' if fav_side == 'home' else 'home'
        diag['market_favorite'] = fav_side

        # 赔率变动率（正=赔率走高/被看衰，负=赔率走低/被加注）
        fav_odds_move = ((oh_f - oh_i) / oh_i) if fav_side == 'home' else ((oa_f - oa_i) / oa_i)
        dog_odds_move = ((oa_f - oa_i) / oa_i) if fav_side == 'home' else ((oh_f - oh_i) / oh_i)
        diag['fav_odds_move'] = round(fav_odds_move, 4)
        diag['dog_odds_move'] = round(dog_odds_move, 4)

        score = 0.0
        reasons: List[str] = []
        signals: List[str] = []

        # 信号1：欧赔虚热 —— 热门赔率被压低且冷门赔率被明显抬高
        if fav_odds_move <= -0.04 and dog_odds_move >= 0.10:
            mag = min(0.45, abs(fav_odds_move) * 1.5 + dog_odds_move * 0.9)
            score += mag
            reasons.append(f'欧赔虚热：热门侧赔率降{abs(fav_odds_move)*100:.1f}%、冷门侧涨{dog_odds_move*100:.1f}%')
            signals.append('euro_favorite_pump')

        # 信号2：盘水背离（亚盘 initial→final）
        if isinstance(asian_handicap, dict):
            a_ini = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}
            a_fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
            hcp_i = self._parse_handicap_value(
                a_ini.get('handicap') if 'handicap' in a_ini else a_ini.get('handicap_value') if 'handicap_value' in a_ini else a_ini.get('handicap_text')
            )
            hcp_f = self._parse_handicap_value(
                a_fin.get('handicap') if 'handicap' in a_fin else a_fin.get('handicap_value') if 'handicap_value' in a_fin else a_fin.get('handicap_text')
            )
            # 热门侧水位（让球方）
            fav_water_i = self._to_float(a_ini.get('home_water')) if fav_side == 'home' else self._to_float(a_ini.get('away_water'))
            fav_water_f = self._to_float(a_fin.get('home_water')) if fav_side == 'home' else self._to_float(a_fin.get('away_water'))
            diag['handicap_initial'] = hcp_i
            diag['handicap_final'] = hcp_f
            if isinstance(fav_water_i, float) and isinstance(fav_water_f, float):
                water_move = fav_water_f - fav_water_i
                diag['fav_water_move'] = round(water_move, 4)
                hcp_deepened = (
                    hcp_i is not None and hcp_f is not None and abs(hcp_f) - abs(hcp_i) > 0.06
                )
                hcp_flat_or_retreat = (
                    hcp_i is not None and hcp_f is not None and abs(hcp_f) - abs(hcp_i) <= 0.06
                )
                # 2a：盘不升而热门水大降 —— 庄家不愿为赢球背书的典型诱下
                if hcp_flat_or_retreat and water_move <= -0.20:
                    mag = min(0.40, abs(water_move) * 0.9)
                    score += mag
                    reasons.append(f'盘水背离：盘口未升而热门侧主水大降{abs(water_move):.2f}（不愿为赢球背书）')
                    signals.append('handicap_flat_water_drop')
                # 2b：升盘同时配高水 —— 抬人气诱下
                elif hcp_deepened and water_move >= 0.12:
                    mag = min(0.32, water_move * 1.2)
                    score += mag
                    reasons.append(f'升盘配高水：让球加深但热门侧升水{water_move:.2f}（抬人气诱下）')
                    signals.append('handicap_up_water_up')

        if not signals:
            diag['reason'] = 'no_luring_signal'
            return diag

        strength = max(0.0, min(1.0, score))
        diag['detected'] = strength >= 0.18
        diag['strength'] = round(strength, 4)
        diag['luring_side'] = fav_side  # 被诱方向=被抬热的热门，应警惕其不胜
        diag['protect_side'] = dog_side
        diag['reasons'] = reasons
        diag['signals'] = signals
        return diag

    def apply_market_sentiment_adjustment(
        self,
        *,
        final_prob: Dict[str, float],
        sentiment: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """消费 detect_market_movement_sentiment：检测到虚热诱下时，
        从被诱（热门）方向回撤概率，补给平局 + 被看衰方向，并给出预警。

        回撤幅度 = strength * 0.10，封顶 0.07，且保留热门方向不低于 0.02。
        """
        diag: Dict[str, Any] = {'applied': False, 'source': 'market_movement_sentiment'}
        if not isinstance(final_prob, dict) or not isinstance(sentiment, dict):
            diag['reason'] = 'missing_inputs'
            return final_prob, diag
        if not sentiment.get('detected'):
            diag['reason'] = str(sentiment.get('reason') or 'not_detected')
            return final_prob, diag
        luring = sentiment.get('luring_side')
        if luring not in ('home', 'away'):
            diag['reason'] = 'invalid_luring_side'
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            diag['reason'] = 'invalid_probability_mass'
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

        strength = float(sentiment.get('strength') or 0.0)
        retreat_coef = float(getattr(self, 'sentiment_retreat_coef', 0.10))
        retreat_cap = float(getattr(self, 'sentiment_retreat_cap', 0.07))
        draw_share = float(getattr(self, 'sentiment_retreat_draw_share', 0.6))
        take = min(retreat_cap, strength * retreat_coef)
        luring_p = p_h if luring == 'home' else p_a
        actual = min(take, max(0.0, luring_p - 0.02))
        if actual < 0.004:
            diag['reason'] = 'delta_below_threshold'
            return final_prob, diag

        # 回撤分配：draw_share 给平局、其余给被看衰侧
        give_draw = actual * draw_share
        give_dog = actual * (1.0 - draw_share)
        if luring == 'home':
            p_h -= actual
            p_d += give_draw
            p_a += give_dog
        else:
            p_a -= actual
            p_d += give_draw
            p_h += give_dog

        s = p_h + p_d + p_a
        final_prob = {'home_win': p_h / s, 'draw': p_d / s, 'away_win': p_a / s}
        diag.update({
            'applied': True,
            'luring_side': luring,
            'protect_side': sentiment.get('protect_side'),
            'strength': round(strength, 4),
            'delta_taken': round(actual, 4),
            'reasons': sentiment.get('reasons'),
            'signals': sentiment.get('signals'),
            'warning': '盘口动态显示热门侧被虚抬（诱下嫌疑），已下调其胜率并补充平局/冷门方向',
        })
        return final_prob, diag

    def classify_market_operation_pattern(
        self,
        *,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        ou_signal: Optional[Dict[str, Any]] = None,
        kelly: Optional[Dict[str, Any]] = None,
        market_sentiment: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """统一识别庄家「盘口×大小球×欧赔×凯利」交叉操盘手法，判别真实/诱导并打警示标签。

        三大交叉场景：
          1) 盘动+球动   2) 盘不动+球动   3) 盘不动+球不动（结合欧赔、凯利静态读盘）

        判别三原则：
          a. 明暗一致性——盘口可见方向与水位暗钱方向相反=诱导；
          b. 背书意愿——升盘=愿为赢球背书(真)，配高水/退水=不愿背书(诱)；
          c. 欧赔↔盘口/凯利同步——欧赔虚热却不被盘口或凯利跟随=人气虚抬。

        仅做判别+给出 verdict/warning_labels/recommended_extra_retreat，
        由 apply_market_operation_adjustment 消费做智能概率调整。
        """
        diag: Dict[str, Any] = {
            'available': False,
            'pattern': None,
            'verdict': 'neutral',
            'strength': 0.0,
            'warning_labels': [],
            'deception_signals': [],
            'corroboration_signals': [],
            'luring_side': None,
            'favored_side': None,
            'recommended_extra_retreat': 0.0,
        }

        sentiment = market_sentiment if isinstance(market_sentiment, dict) else {}
        ou = ou_signal if isinstance(ou_signal, dict) else {}
        senti_signals = sentiment.get('signals') if isinstance(sentiment.get('signals'), list) else []
        ou_signals = ou.get('signals') if isinstance(ou.get('signals'), list) else []

        fav_side = sentiment.get('market_favorite')
        if fav_side not in ('home', 'away'):
            # 欧赔缺失时由亚盘让球方推断强侧
            hcp_f = None
            if isinstance(asian_handicap, dict):
                a_fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
                hcp_f = self._parse_handicap_value(
                    a_fin.get('handicap') if 'handicap' in a_fin else a_fin.get('handicap_value') if 'handicap_value' in a_fin else a_fin.get('handicap_text')
                )
            if isinstance(hcp_f, float):
                fav_side = 'home' if hcp_f < 0 else 'away' if hcp_f > 0 else None
        dog_side = 'away' if fav_side == 'home' else 'home' if fav_side == 'away' else None
        diag['favored_side'] = fav_side

        # 盘动判定：亚盘让球深度变化是否 > 0.06
        handicap_moved = None
        hcp_i = sentiment.get('handicap_initial')
        hcp_f = sentiment.get('handicap_final')
        if not isinstance(hcp_i, (int, float)) or not isinstance(hcp_f, (int, float)):
            if isinstance(asian_handicap, dict):
                a_ini = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}
                a_fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
                hcp_i = self._parse_handicap_value(
                    a_ini.get('handicap') if 'handicap' in a_ini else a_ini.get('handicap_value') if 'handicap_value' in a_ini else a_ini.get('handicap_text')
                )
                hcp_f = self._parse_handicap_value(
                    a_fin.get('handicap') if 'handicap' in a_fin else a_fin.get('handicap_value') if 'handicap_value' in a_fin else a_fin.get('handicap_text')
                )
        if isinstance(hcp_i, (int, float)) and isinstance(hcp_f, (int, float)):
            handicap_moved = abs(abs(float(hcp_f)) - abs(float(hcp_i))) > 0.06

        # 球动判定：大小球升降盘，或单侧水位移动
        ou_moved = None
        if ou.get('available'):
            ou_moved = bool(
                ('ou_line_up' in ou_signals)
                or ('ou_line_down' in ou_signals)
                or ('over_water_drop' in ou_signals)
                or ('under_water_drop' in ou_signals)
            )

        if handicap_moved is None and ou_moved is None and not senti_signals:
            diag['reason'] = 'insufficient_market_dynamics'
            return diag
        diag['available'] = True

        # 场景标签
        hm = bool(handicap_moved)
        om = bool(ou_moved)
        if handicap_moved is None and ou_moved is None:
            pattern = 'static_static'
        elif hm and om:
            pattern = 'handicap_move_ou_move'
        elif (not hm) and om:
            pattern = 'handicap_static_ou_move'
        elif hm and (not om):
            pattern = 'handicap_move_ou_static'
        else:
            pattern = 'static_static'
        diag['pattern'] = pattern

        # 方向轴与进球轴分离：方向诱导只由「欧赔深压/亚盘硬背离」驱动，
        # 大小球暗钱仅作进球轴标签（pace_shift 已在 postprocess 生效），不翻转方向判定。
        deception = 0.0
        corroboration = 0.0
        dsig: List[str] = []
        csig: List[str] = []
        labels: List[str] = []

        fav_move = sentiment.get('fav_odds_move')
        dog_move = sentiment.get('dog_odds_move')
        # 欧赔深压：热门侧赔率被压低 ≥7%（普通强队走热通常 <7%，避免误报）
        deep_euro_pump = (
            'euro_favorite_pump' in senti_signals
            and isinstance(fav_move, (int, float))
            and float(fav_move) <= -0.07
        )
        light_euro_pump = ('euro_favorite_pump' in senti_signals) and not deep_euro_pump
        handicap_deepened = (
            hm and isinstance(hcp_i, (int, float)) and isinstance(hcp_f, (int, float))
            and abs(float(hcp_f)) - abs(float(hcp_i)) > 0.06
        )

        # ——欧赔轴——
        if deep_euro_pump:
            mag = min(0.45, abs(float(fav_move)) * 1.5 + abs(float(dog_move or 0.0)) * 0.9)
            deception += mag
            dsig.append('euro_deep_favorite_pump')
            labels.append('⚠️欧赔深压·热门被人气虚抬')
        elif light_euro_pump:
            # 轻度走热=正常强队被看好，不计方向诱导，仅提示
            labels.append('◽欧赔小幅走热·属强队正常被看好')
        elif isinstance(fav_move, (int, float)) and isinstance(dog_move, (int, float)) and fav_move <= -0.02 and dog_move <= 0.02:
            corroboration += min(0.18, abs(float(fav_move)) * 1.2)
            csig.append('euro_fav_compressed_consensus')

        # ——亚盘轴——
        if 'handicap_flat_water_drop' in senti_signals:
            deception += min(0.40, abs(float(sentiment.get('fav_water_move') or 0.0)) * 0.9)
            dsig.append('handicap_flat_water_drop')
            labels.append('⚠️盘不升·热门水大降（不愿为赢球背书）')
        if handicap_deepened:
            fav_water_move = sentiment.get('fav_water_move')
            # 升盘=愿为赢球背书。仅当「欧赔深压」同现，升盘配高水才成立诱下；
            # 否则升盘默认背书豁免（升盘配底水更强），避免把正常升盘误判为诱导。
            if 'handicap_up_water_up' in senti_signals and deep_euro_pump:
                deception += min(0.20, abs(float(fav_water_move or 0.0)) * 0.8)
                dsig.append('handicap_up_water_up_with_deep_pump')
                labels.append('⚠️升盘配高水叠加欧赔深压·抬人气诱下')
            elif not deep_euro_pump:
                # 欧赔深压时不给背书印证，避免与诱导判定自相矛盾
                corroboration += 0.16
                csig.append('handicap_up_endorsed')
                labels.append('✅升盘背书·庄家愿为赢球加深让步')

        # ——亚盘升降盘 × 水位档位 × 欧赔方向：让球方向操盘手法矩阵——
        # 用户规则（世界杯无伤病数据，用欧赔主队升/降代理「有无利空」）：
        #   升盘+上盘高水+欧赔主降 → 阻上(主真赢)；+欧赔主升 → 诱上(主难赢)
        #   升盘+上盘低水 → 绝大多数诱上(大热必死)
        #   降盘+下盘低水+欧赔客降 → 防客(客拿分)；+欧赔客升/急降盘 → 诱客(客无分)
        #   降盘+下盘高水 → 阻下(客难打出)
        # 仅当亚盘动(hm)且能取到让球方终盘水位时启用；输出方向标签 + 软性诱/印证分。
        dir_matrix: Optional[Dict[str, Any]] = None
        if hm and fav_side in ('home', 'away') and isinstance(asian_handicap, dict):
            a_fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
            fav_water_f = (
                self._to_float(a_fin.get('home_water')) if fav_side == 'home'
                else self._to_float(a_fin.get('away_water'))
            )
            dog_water_f = (
                self._to_float(a_fin.get('away_water')) if fav_side == 'home'
                else self._to_float(a_fin.get('home_water'))
            )
            line_up = handicap_deepened  # 让球加深=升盘(抬门槛)
            line_down = (
                isinstance(hcp_i, (int, float)) and isinstance(hcp_f, (int, float))
                and abs(float(hcp_i)) - abs(float(hcp_f)) > 0.06
            )
            # 欧赔方向代理利空：热门赔率降=被加注(无利空/真热)，升=被看衰(有利空嫌疑)
            fav_odds_down = isinstance(fav_move, (int, float)) and float(fav_move) <= -0.02
            fav_odds_up = isinstance(fav_move, (int, float)) and float(fav_move) >= 0.02
            dog_odds_down = isinstance(dog_move, (int, float)) and float(dog_move) <= -0.02
            sharp_line_move = (
                isinstance(hcp_i, (int, float)) and isinstance(hcp_f, (int, float))
                and abs(abs(float(hcp_i)) - abs(float(hcp_f))) >= 0.20
            )
            verdict_dir = None
            water_used = None
            water_tier = None
            # 升盘看上盘(热门)水位。水位字段存小数赔率全值，需转港水(=赔率-1)再判档。
            if line_up and isinstance(fav_water_f, float):
                hk = round(fav_water_f - 1.0, 4)
                water_used = hk
                fav_low = hk <= 0.85
                fav_high = hk >= 0.95
                water_tier = 'low' if fav_low else 'high' if fav_high else 'mid'
                if fav_high:
                    if fav_odds_down:
                        verdict_dir = 'block_up_home_genuine'
                        corroboration += 0.16
                        csig.append('line_up_high_water_euro_down_block_up')
                        labels.append('✅升盘配高水+欧赔主降·阻上(主真赢)')
                    elif fav_odds_up:
                        verdict_dir = 'lure_up_home_fade'
                        deception += 0.22
                        dsig.append('line_up_high_water_euro_up_lure_up')
                        labels.append('⚠️升盘配高水+欧赔主升·诱上(主难赢)')
                elif fav_low and not fav_odds_down:
                    # 升盘配低水默认诱上(大热必死)；若欧赔同时深压主队(被加注)
                    # 属 smart money 背书，不判诱，让位给既有升盘背书印证逻辑。
                    verdict_dir = 'lure_up_hot_death'
                    deception += 0.26
                    dsig.append('line_up_low_water_lure_up_hot_death')
                    labels.append('⚠️升盘配低水·诱上(大热必死)')
            # 降盘看下盘(客/受让方)水位。水位字段存小数赔率全值，需转港水(=赔率-1)再判档。
            elif line_down and isinstance(dog_water_f, float):
                hk = round(dog_water_f - 1.0, 4)
                water_used = hk
                dog_low = hk <= 0.85
                dog_high = hk >= 0.95
                water_tier = 'low' if dog_low else 'high' if dog_high else 'mid'
                if dog_low:
                    if fav_odds_up or dog_odds_down:
                        # 主队被看衰(利空) / 客队被加注 → 防客(客拿分)。利空优先于急降盘。
                        verdict_dir = 'protect_dog_genuine'
                        deception += 0.20
                        dsig.append('line_down_low_water_protect_dog')
                        labels.append('⚠️降盘配下盘低水+主看衰/客加注·防客(客拿分)')
                    elif fav_odds_down or sharp_line_move:
                        # 主队被加注/无利空 + 临场急降盘 → 诱客(客无分,主稳)
                        verdict_dir = 'lure_dog_no_point'
                        corroboration += 0.16
                        csig.append('line_down_low_water_lure_dog')
                        labels.append('✅降盘配下盘低水+主加注/急降·诱客(客无分,主稳)')
                elif dog_high:
                    verdict_dir = 'block_down_dog_hard'
                    corroboration += 0.14
                    csig.append('line_down_high_water_block_down')
                    labels.append('✅降盘配下盘高水·阻下(客难打出,主稳)')
            if verdict_dir is not None:
                dir_matrix = {
                    'verdict_dir': verdict_dir,
                    'fav_side': fav_side,
                    'line_move': 'up' if line_up else 'down' if line_down else 'flat',
                    'water_side': 'favorite' if line_up else 'underdog',
                    'water_final': round(water_used, 4) if isinstance(water_used, float) else None,
                    'water_tier': water_tier,
                    'fav_odds_move': round(float(fav_move), 4) if isinstance(fav_move, (int, float)) else None,
                    'dog_odds_move': round(float(dog_move), 4) if isinstance(dog_move, (int, float)) else None,
                }

        # ——大小球轴（进球轴，独立于方向判定）——
        goal_labels: List[str] = []
        div = ou.get('ou_line_water_divergence') if isinstance(ou.get('ou_line_water_divergence'), dict) else None
        if div:
            side = div.get('side')
            if side == 'over':
                goal_labels.append('⚠️降盘底水给大·暗钱买大球')
            else:
                goal_labels.append('⚠️升盘压小水·暗钱买小球')
        nudge = ou.get('ou_flat_water_nudge') if isinstance(ou.get('ou_flat_water_nudge'), dict) else None
        if nudge:
            side = nudge.get('side')
            goal_labels.append(f'✅盘锁·{"大" if side == "over" else "小"}球底水真买（弱）')
        labels.extend(goal_labels)

        # ——凯利轴（仅放大方向诱导，不单独触发）——
        kd = None
        if isinstance(kelly, dict):
            kfin = kelly.get('final') if isinstance(kelly.get('final'), dict) else kelly
            kd = self._to_float(kfin.get('draw') if isinstance(kfin, dict) else None)
        if isinstance(kd, float):
            if kd <= 0.95 and dsig:
                deception += 0.06
                dsig.append('kelly_draw_low_amplifier')
                labels.append('⚠️凯利平局偏低·放大诱下嫌疑')
            elif 0.97 <= kd <= 1.03 and not dsig:
                corroboration += 0.10
                csig.append('kelly_consensus_stable')

        deception = max(0.0, min(1.0, deception))
        corroboration = max(0.0, min(1.0, corroboration))
        net = deception - corroboration
        if net >= 0.18:
            verdict = 'deceptive'
            strength = deception
        elif net <= -0.12 and not dsig:
            verdict = 'genuine'
            strength = corroboration
        else:
            verdict = 'neutral'
            strength = max(deception, corroboration)

        # 警示标签兜底（全场景打标）
        pattern_cn = {
            'handicap_move_ou_move': '盘动+球动',
            'handicap_static_ou_move': '盘不动+球动',
            'handicap_move_ou_static': '盘动+球不动',
            'static_static': '盘不动+球不动',
        }.get(pattern, pattern)
        if verdict == 'genuine' and not any(lbl.startswith('✅') for lbl in labels):
            labels.append(f'✅{pattern_cn}·盘水欧凯同向（真实）')
        elif verdict == 'neutral':
            labels.append(f'◽{pattern_cn}·信号不足/中性')

        # 标准力度：诱导额外回撤最多 +0.05
        extra_retreat = 0.0
        if verdict == 'deceptive' and dog_side is not None:
            extra_retreat = round(min(0.05, strength * 0.07), 4)

        diag.update({
            'pattern_cn': pattern_cn,
            'handicap_moved': handicap_moved,
            'ou_moved': ou_moved,
            'verdict': verdict,
            'strength': round(strength, 4),
            'deception_score': round(deception, 4),
            'corroboration_score': round(corroboration, 4),
            'deception_signals': dsig,
            'corroboration_signals': csig,
            'warning_labels': labels,
            'luring_side': fav_side if verdict == 'deceptive' else None,
            'recommended_extra_retreat': extra_retreat,
            'direction_handicap_matrix': dir_matrix,
        })
        return diag

    def _load_operation_weights(self) -> Dict[str, Any]:
        """惰性加载已学习的操盘信号可靠度系数（runtime/operation_weights.json）。

        文件不存在或样本不足时返回 {}，调权自动退化为「仅提示不改概率」。
        系数由 tools/learn_operation_weights.py 离线从归档学习并写入，绑定留出表现。
        """
        cached = getattr(self, '_operation_weights_cache', None)
        if cached is not None:
            return cached
        weights: Dict[str, Any] = {}
        try:
            import json
            from runtime.paths import get_default_paths
            path = get_default_paths().runtime_file('operation_weights.json')
            if path.exists():
                payload = json.loads(path.read_text(encoding='utf-8'))
                if isinstance(payload, dict):
                    w = payload.get('weights')
                    weights = w if isinstance(w, dict) else {}
        except Exception:
            weights = {}
        self._operation_weights_cache = weights
        return weights

    # 让球方向矩阵 6 口诀 → 强侧视角有符号偏置（正=印证强侧，负=诱导回撤）。
    # 幅度沿用 classify_market_operation_pattern 中各口诀的软分量级，确保口诀间相对强弱一致。
    _DIRECTION_MATRIX_BIAS: Dict[str, float] = {
        'block_up_home_genuine': 0.16,    # 升盘配高水+欧赔主降·阻上(主真赢) → 印证
        'lure_up_home_fade': -0.22,       # 升盘配高水+欧赔主升·诱上(主难赢) → 诱导
        'lure_up_hot_death': -0.26,       # 升盘配低水·诱上(大热必死) → 诱导
        'protect_dog_genuine': -0.20,     # 降盘配下盘低水·防客(客拿分) → 诱导(强侧回撤)
        'lure_dog_no_point': 0.16,        # 降盘配下盘低水·诱客(客无分,主稳) → 印证
        'block_down_dog_hard': 0.14,      # 降盘配下盘高水·阻下(客难打出,主稳) → 印证
    }

    @classmethod
    def _direction_matrix_bias(cls, pattern_diag: Optional[Dict[str, Any]]) -> Tuple[float, Optional[str]]:
        """从让球方向矩阵口诀导出常开方向偏置（不依赖历史样本量）。

        返回 (bias, verdict_dir)；bias 为强侧视角有符号量，无口诀时返回 (0.0, None)。
        """
        if not isinstance(pattern_diag, dict):
            return 0.0, None
        dh = pattern_diag.get('direction_handicap_matrix')
        if not isinstance(dh, dict):
            return 0.0, None
        verdict_dir = dh.get('verdict_dir')
        return cls._DIRECTION_MATRIX_BIAS.get(verdict_dir, 0.0), verdict_dir

    def apply_market_operation_adjustment(
        self,
        *,
        final_prob: Dict[str, float],
        pattern_diag: Optional[Dict[str, Any]],
        delta_vector: Optional[Dict[str, Any]] = None,
        weights: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """两层动态调权：调整量 = 逐场Δ向量 · 各信号历史可靠度系数。

        - 第一层 delta_vector：该场实时盘口位移（每场不同，贴合当场数据）；
        - 第二层 weights：每个信号的可靠度(reliability)与方向(sign)，从历史赛果学出，
          绑定留出表现；样本/显著性不足则 reliability=0，对应信号自动失效。
        合成 score（标准化后加权和）：>0=印证强侧(真实看好)，<0=诱导嫌疑。
        所有系数为 0（如当前样本不足）时 score=0，恒返回 applied=False、概率不变，
        等价「仅透传警示标签」。无写死方向阈值，随样本增长自动生效/退化。
        警示标签始终透传，不受 applied 影响。
        """
        from domain.operation_weight_learner import score_delta

        diag: Dict[str, Any] = {'applied': False, 'source': 'market_operation_pattern'}
        if not isinstance(final_prob, dict) or not isinstance(pattern_diag, dict):
            diag['reason'] = 'missing_inputs'
            return final_prob, diag
        diag['verdict'] = pattern_diag.get('verdict')
        diag['pattern'] = pattern_diag.get('pattern')
        diag['warning_labels'] = pattern_diag.get('warning_labels')

        if weights is None:
            weights = self._load_operation_weights()
        fav_side = pattern_diag.get('favored_side')
        if fav_side not in ('home', 'away'):
            diag['reason'] = 'missing_favorite'
            return final_prob, diag

        # 第二层：历史学习权重路（样本足够才生效，否则 score=0、active=[]）
        score, active = score_delta(delta_vector, weights) if isinstance(delta_vector, dict) else (0.0, [])
        diag['score'] = round(score, 4)
        diag['active_signals'] = active

        # 常开：让球方向矩阵 6 口诀偏置（不依赖历史样本量，世界杯等冷启动场景也生效）
        matrix_bias, matrix_verdict_dir = self._direction_matrix_bias(pattern_diag)
        diag['matrix_verdict_dir'] = matrix_verdict_dir

        # 两路方向偏置（强侧视角：正=印证强侧 / 负=诱导回撤）各自压幅后求和。
        delta_learned = round(math.tanh(score) * 0.05, 4) if (active and abs(score) >= 1e-9) else 0.0
        delta_matrix = round(max(-0.04, min(0.04, matrix_bias * 0.18)), 4) if matrix_bias else 0.0
        diag['delta_learned'] = delta_learned
        diag['delta_matrix'] = delta_matrix
        if abs(delta_learned) < 1e-9 and abs(delta_matrix) < 1e-9:
            # 学习权重与口诀均无信号 → 不改概率，仅透传标签。
            diag['reason'] = 'no_signal_label_only'
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            diag['reason'] = 'invalid_probability_mass'
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

        # 合成方向力度，封顶 ±0.06（两路同向时略放宽，仍受控）。逐场连续、无写死方向阈值。
        delta = round(max(-0.06, min(0.06, delta_learned + delta_matrix)), 4)
        fav_p = p_h if fav_side == 'home' else p_a

        if delta < 0:  # 诱导嫌疑：从强侧回撤，60%给平局、40%给冷门
            take = min(abs(delta), max(0.0, fav_p - 0.02))
            if take < 0.004:
                diag['reason'] = 'delta_below_threshold'
                return final_prob, diag
            give_draw, give_dog = take * 0.6, take * 0.4
            if fav_side == 'home':
                p_h -= take; p_d += give_draw; p_a += give_dog
            else:
                p_a -= take; p_d += give_draw; p_h += give_dog
            diag['delta_taken'] = round(take, 4)
        else:  # 印证强侧：从平局让给强侧
            give = min(delta, max(0.0, p_d - 0.02))
            if give < 0.004:
                diag['reason'] = 'delta_below_threshold'
                return final_prob, diag
            p_d -= give
            if fav_side == 'home':
                p_h += give
            else:
                p_a += give
            diag['delta_given'] = round(give, 4)

        s = p_h + p_d + p_a
        final_prob = {'home_win': p_h / s, 'draw': p_d / s, 'away_win': p_a / s}
        diag.update({
            'applied': True,
            'favored_side': fav_side,
            'direction': 'deceptive' if delta < 0 else 'endorse',
            'delta_total': delta,
        })
        return final_prob, diag

    def _resolve_home_advantage(self, league_code: str, home_team: str = "") -> float:
        """主场优势系数：联赛主客场制用 1.12；中立/弱主场赛事（友谊赛、世界杯、洲际杯赛）降低。

        友谊赛/国家队比赛常在中立或弱主场环境进行，固定 1.12 会让主队 λ 恒定虚高、
        比分恒为 X-1，因此这类赛事用接近中立的系数。世界杯为合办赛事，仅东道主
        （美/加/墨）作主队时享有弱主场优势，其余对阵按中立场地处理。
        """
        return resolve_home_advantage(league_code, home_team)

    def _resolve_historical_ratings(
        self,
        league_code: str,
        home_team: str,
        away_team: str,
    ) -> Optional[Dict[str, Dict[str, float]]]:
        """组装持久化的历史 ELO/Glicko 评分供模型融合使用。

        - 无 rating_service 或某队无历史记录时返回 None，让模型融合回退到 strength 派生值（SoT 零回归）；
        - 仅当两队都有历史评分时才下发，避免一队历史/一队派生的混合口径。
        """
        if self.rating_service is None:
            return None
        try:
            home_hist = self.rating_service.get_ratings(league_code, home_team)
            away_hist = self.rating_service.get_ratings(league_code, away_team)
        except Exception:
            return None
        if not home_hist or not away_hist:
            return None
        return {'home': home_hist, 'away': away_hist}

    @staticmethod
    def compute_tri_axis_consistency(
        final_prob: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
        operation_pattern: Optional[Dict[str, Any]],
        european_odds: Optional[Dict[str, Any]] = None,
        asian_handicap: Optional[Dict[str, Any]] = None,
        kelly: Optional[Dict[str, Any]] = None,
        over_under_odds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """影子层：汇总方向轴/大小球轴/操盘轴的方向，做一致性与背离检测。

        纯诊断：不修改任何概率。三轴来自同一市场，高度相关，故此处只做
        「同向→标注加强、背离→标注矛盾」的门控，不做加权叠加（避免重复计价）。
        """
        out: Dict[str, Any] = {
            'available': False,
            'direction_axis': None,
            'ou_axis': None,
            'operation_axis': None,
            'market_drift': None,
            'agreement': None,
            'divergence_flags': [],
            'note': None,
        }

        # 方向轴：模型最终 1X2 的领先方向 + 强度（领先优势）
        if isinstance(final_prob, dict):
            probs = {k: float(final_prob.get(k) or 0.0) for k in ('home_win', 'draw', 'away_win')}
            ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
            if ranked and ranked[0][1] > 0:
                top, second = ranked[0], ranked[1]
                out['direction_axis'] = {
                    'lean': top[0],
                    'prob': round(top[1], 4),
                    'margin': round(top[1] - second[1], 4),
                    'decisive': bool(top[0] != 'draw' and (top[1] - second[1]) >= 0.10),
                }

        # 大小球轴：over/under 谁占优 + 强度。
        # 方案A中性场（ou_neutral / stakes_neutral）大小球无 edge、不下注，
        # 这里标记为中性 lean，使其不参与「大胜低进球」背离与「共振看好进攻」判定。
        if isinstance(over_under, dict) and over_under.get('available'):
            ov = over_under.get('over')
            un = over_under.get('under')
            ou_neutral = bool(over_under.get('ou_neutral') or over_under.get('stakes_neutral'))
            if isinstance(ov, (int, float)) and isinstance(un, (int, float)):
                lean = 'neutral' if ou_neutral else ('over' if ov > un else 'under')
                out['ou_axis'] = {
                    'lean': lean,
                    'neutral': ou_neutral,
                    'over': round(float(ov), 4),
                    'under': round(float(un), 4),
                    'margin': round(abs(float(ov) - float(un)), 4),
                    'line': over_under.get('line'),
                }

        # 操盘轴：庄家形态判定（真实/诱导/中性）+ 诱导方向
        if isinstance(operation_pattern, dict):
            verdict = operation_pattern.get('verdict')
            if verdict:
                out['operation_axis'] = {
                    'verdict': verdict,
                    'luring_side': operation_pattern.get('luring_side'),
                    'strength': operation_pattern.get('strength'),
                    'pattern_cn': operation_pattern.get('pattern_cn'),
                }
            # 升降盘×水位×欧赔方向 让球方向口诀（透传到研判文本，供临场提示）
            dh = operation_pattern.get('direction_handicap_matrix')
            if isinstance(dh, dict) and dh.get('verdict_dir'):
                out['direction_handicap'] = dh

        # 临场资金轴：封盘热门(最低赔率方)相对开盘的赔率漂移。
        # 经验观测（世界杯 16 场回测）：封盘热门赔率走高(资金离场) → 热门不兑现/爆冷概率显著上升。
        # 纯诊断标记，不改方向/概率；阈值仅作监控基线，样本不足以调参。
        out['market_drift'] = InferencePipelineService._compute_market_drift(
            european_odds, asian_handicap=asian_handicap, kelly=kelly, over_under_odds=over_under_odds
        )

        dir_ax = out['direction_axis']
        ou_ax = out['ou_axis']
        op_ax = out['operation_axis']
        md_ax = out['market_drift']
        flags: List[str] = []

        # 背离检测 1：操盘判定为「诱导」，且诱导方向正是模型的方向倾向 → 模型可能在跟庄入坑
        if dir_ax and op_ax and op_ax.get('verdict') == 'deceptive':
            luring = op_ax.get('luring_side')
            if luring and luring == dir_ax.get('lean'):
                flags.append('direction_follows_luring_side')

        # 背离检测 2：方向轴判定决定性大胜（非平局且领先显著），但大小球轴偏小球
        #            → 「大胜却低进球」的内在矛盾（强队小胜 or 模型高估某方）
        if dir_ax and ou_ax and dir_ax.get('decisive') and ou_ax.get('lean') == 'under':
            flags.append('decisive_winner_but_low_scoring')

        # 背离检测 3：封盘热门赔率走高（资金临场离场），且模型方向与该热门方向一致
        #            → 我们押的方向正被市场资金抛弃，平局/爆冷风险上升。
        #            质检门控：仅当亚值/凯利共振印证(drift_confidence=high)才触发，
        #            欧赔孤证(low)疑似抓取噪声，降级不报背离。
        if md_ax and md_ax.get('favorite_drifting_out') and dir_ax:
            if (
                md_ax.get('favorite_side') == dir_ax.get('lean')
                and md_ax.get('drift_confidence') == 'high'
            ):
                flags.append('favorite_drifting_out_against_direction')

        # 一致性正向：方向决定性 + 大小球偏大 + 操盘真实 → 三轴共振看好进攻方
        agreement = None
        if dir_ax and ou_ax and op_ax:
            if (
                dir_ax.get('decisive')
                and ou_ax.get('lean') == 'over'
                and op_ax.get('verdict') == 'genuine'
            ):
                agreement = 'aligned_attacking'
            elif flags:
                agreement = 'divergent'
            else:
                agreement = 'mixed'

        out['divergence_flags'] = flags
        out['agreement'] = agreement
        out['available'] = bool(dir_ax or ou_ax or op_ax)
        if flags:
            out['note'] = '；'.join({
                'direction_follows_luring_side': '方向轴与庄家诱导方向一致，警惕跟庄入坑',
                'decisive_winner_but_low_scoring': '方向看决定性胜负但大小球偏小，存在“大胜低进球”矛盾',
                'favorite_drifting_out_against_direction': '封盘热门赔率走高、资金临场离场，且与模型方向同向，平局/爆冷风险上升',
            }.get(f, f) for f in flags)
        elif agreement == 'aligned_attacking':
            out['note'] = '三轴共振：方向、大小球、操盘一致看好进攻方'

        # 综合研判结论：把三轴揉成一句人类可读判断（纯文本输出，不改方向/概率）
        out['verdict_summary'] = InferencePipelineService._compose_tri_axis_verdict(
            dir_ax, ou_ax, op_ax, agreement, flags, md_ax, out.get('direction_handicap')
        )
        return out

    @staticmethod
    def _compute_market_drift(
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        kelly: Optional[Dict[str, Any]] = None,
        over_under_odds: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """计算封盘热门(最低赔率方)相对开盘的赔率漂移，并用亚值/凯利做共振质检。纯诊断，不改概率。

        favorite_drifting_out=True 表示封盘热门赔率较开盘上行（资金离场、热门走冷），
        经验上与平局/爆冷正相关；阈值 +0.05 仅作监控基线。

        质检层：欧赔/亚值/凯利数学同源，同向移动是「真实资金流」的相互印证，可滤掉单家
        欧赔抓取噪声。drift_confidence:
          - high  : 欧赔走冷 + 亚值或凯利同向印证（≥1 个）
          - low   : 欧赔孤证（亚值、凯利均不印证或缺失）—— 疑似抓取噪声，不应触发背离标记
          - n/a   : 欧赔未达阈值，无需质检
        大小球漂移(line/水位)作为独立维度记录，不并入胜负资金共振。
        """
        if not isinstance(european_odds, dict):
            return None
        ini = european_odds.get('initial')
        fin = european_odds.get('final')
        if not isinstance(ini, dict) or not isinstance(fin, dict):
            return None

        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        sides = {}
        for key, lean in (('home', 'home_win'), ('draw', 'draw'), ('away', 'away_win')):
            oi, ofn = _f(ini.get(key)), _f(fin.get(key))
            if oi and ofn and min(oi, ofn) > 1.01:
                sides[lean] = (oi, ofn)
        if not sides:
            return None

        # 封盘热门 = 封盘最低赔率方
        fav_lean = min(sides, key=lambda k: sides[k][1])
        fav_open, fav_close = sides[fav_lean]
        drift = round(fav_close - fav_open, 4)
        threshold = 0.05
        drifting_out = drift > threshold
        steaming_in = drift < -threshold

        out: Dict[str, Any] = {
            'favorite_side': fav_lean,
            'favorite_open': round(fav_open, 4),
            'favorite_close': round(fav_close, 4),
            'drift': drift,
            'threshold': threshold,
            'favorite_drifting_out': drifting_out,
            'favorite_steaming_in': steaming_in,
        }

        # ——共振质检：仅在欧赔达阈值（走冷或进场）时才做交叉印证——
        confirmations: Dict[str, Any] = {}
        if drifting_out or steaming_in:
            expect_out = drifting_out  # True=资金离热门，False=资金进热门

            # 亚值印证：热门方水位变化。水位升=该侧资金离场。
            ya_conf = None
            if isinstance(asian_handicap, dict):
                yi = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}
                yf = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
                water_key = 'home_water' if fav_lean == 'home_win' else ('away_water' if fav_lean == 'away_win' else None)
                if water_key:
                    wi, wf = _f(yi.get(water_key)), _f(yf.get(water_key))
                    if wi is not None and wf is not None:
                        w_drift = round(wf - wi, 4)
                        # 水位升(>0)=热门资金离场，与“走冷”同向
                        ya_conf = {
                            'fav_water_drift': w_drift,
                            'agree': (w_drift > 0) == expect_out and abs(w_drift) >= 0.02,
                        }
            if ya_conf is not None:
                confirmations['asian'] = ya_conf

            # 凯利印证：热门方凯利指数变化。凯利升=庄家让利/资金离场。
            ke_conf = None
            if isinstance(kelly, dict):
                ki = kelly.get('initial') if isinstance(kelly.get('initial'), dict) else {}
                kf = kelly.get('final') if isinstance(kelly.get('final'), dict) else {}
                kkey = {'home_win': 'home', 'draw': 'draw', 'away_win': 'away'}[fav_lean]
                kvi, kvf = _f(ki.get(kkey)), _f(kf.get(kkey))
                if kvi is not None and kvf is not None:
                    k_drift = round(kvf - kvi, 4)
                    ke_conf = {
                        'fav_kelly_drift': k_drift,
                        'agree': (k_drift > 0) == expect_out and abs(k_drift) >= 0.005,
                    }
            if ke_conf is not None:
                confirmations['kelly'] = ke_conf

            agree_count = sum(1 for c in confirmations.values() if c.get('agree'))
            out['confirmations'] = confirmations
            out['confirm_count'] = agree_count
            out['drift_confidence'] = 'high' if agree_count >= 1 else 'low'
        else:
            out['drift_confidence'] = 'n/a'

        # ——大小球漂移：独立维度，仅记录不并入胜负共振——
        if isinstance(over_under_odds, dict):
            oi = over_under_odds.get('initial') if isinstance(over_under_odds.get('initial'), dict) else {}
            ofn = over_under_odds.get('final') if isinstance(over_under_odds.get('final'), dict) else {}
            li, lf = _f(oi.get('line')), _f(ofn.get('line'))
            if li is not None and lf is not None:
                out['ou_line_drift'] = {
                    'initial_line': li,
                    'final_line': lf,
                    'line_drift': round(lf - li, 4),  # 负=降盘(看小)，正=升盘(看大)
                }

        return out

    @staticmethod
    def _compose_tri_axis_verdict(
        dir_ax: Optional[Dict[str, Any]],
        ou_ax: Optional[Dict[str, Any]],
        op_ax: Optional[Dict[str, Any]],
        agreement: Optional[str],
        flags: List[str],
        md_ax: Optional[Dict[str, Any]] = None,
        dir_handicap: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """将方向支持率/大小球支持率/操盘手法融合为一句研判结论。仅供人工参考。"""
        lean_cn = {'home_win': '主胜', 'draw': '平局', 'away_win': '客胜', 'over': '大球', 'under': '小球'}
        verdict_cn = {'deceptive': '诱导盘', 'genuine': '真实盘', 'neutral': '中性盘'}
        dir_handicap_cn = {
            'block_up_home_genuine': '升盘配高水+欧赔主降·阻上(主真赢)',
            'lure_up_home_fade': '升盘配高水+欧赔主升·诱上(主难赢)',
            'lure_up_hot_death': '升盘配低水·诱上(大热必死)',
            'protect_dog_genuine': '降盘配下盘低水+主看衰/客加注·防客(客拿分)',
            'lure_dog_no_point': '降盘配下盘低水+主加注/急降·诱客(客无分,主稳)',
            'block_down_dog_hard': '降盘配下盘高水·阻下(客难打出,主稳)',
        }

        seg: List[str] = []
        if dir_ax:
            d_lean = lean_cn.get(dir_ax.get('lean'), dir_ax.get('lean'))
            d_prob = float(dir_ax.get('prob') or 0.0)
            d_str = '强' if dir_ax.get('decisive') else ('中' if d_prob >= 0.45 else '弱')
            seg.append(f"方向{d_str}（{d_lean} {d_prob:.0%}）")
        if ou_ax:
            if ou_ax.get('neutral') or ou_ax.get('lean') == 'neutral':
                seg.append("大小球中性（不下注）")
            else:
                o_lean = lean_cn.get(ou_ax.get('lean'), ou_ax.get('lean'))
                o_prob = float(ou_ax.get('over') if ou_ax.get('lean') == 'over' else ou_ax.get('under') or 0.0)
                seg.append(f"大小球倾向{o_lean}（{o_prob:.0%}）")
        if op_ax and op_ax.get('verdict'):
            seg.append(f"操盘{verdict_cn.get(op_ax.get('verdict'), op_ax.get('verdict'))}")
        if isinstance(dir_handicap, dict) and dir_handicap.get('verdict_dir'):
            dh_cn = dir_handicap_cn.get(dir_handicap.get('verdict_dir'))
            if dh_cn:
                seg.append(f"让球口诀[{dh_cn}]")
        if md_ax and md_ax.get('favorite_drifting_out'):
            fav = lean_cn.get(md_ax.get('favorite_side'), md_ax.get('favorite_side'))
            seg.append(f"临场资金离场（{fav}封盘走冷 {md_ax.get('drift'):+.3f}）")
        if not seg:
            return None

        if 'favorite_drifting_out_against_direction' in flags:
            conclusion = '模型方向正被市场资金临场抛弃，热门走冷——平局/爆冷风险上升，方向需打折'
        elif 'direction_follows_luring_side' in flags:
            conclusion = '方向与庄家诱导同向，方向判断需打折，警惕反向爆冷'
        elif 'decisive_winner_but_low_scoring' in flags:
            conclusion = '方向可信，但“强势胜方+偏小球”自相矛盾——强队大胜多伴随多入球，重点警惕小球误判'
        elif agreement == 'aligned_attacking':
            conclusion = '三轴共振看好进攻方，方向与进球预期一致，综合可信度高'
        elif agreement == 'mixed':
            conclusion = '三轴无明显矛盾，可按模型方向参考'
        else:
            conclusion = '信号不足，按模型方向谨慎参考'

        return '；'.join(seg) + ' ⇒ ' + conclusion

    def calibrate_lambdas_from_market(
        self,
        league_code: str,
        base_home_lambda: float,
        base_away_lambda: float,
        european_odds: Optional[Dict[str, Any]],
        asian_handicap: Optional[Dict[str, Any]] = None,
        market_total_anchor: Optional[float] = None,
    ) -> Tuple[float, float, Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False}
        if not isinstance(european_odds, dict):
            return base_home_lambda, base_away_lambda, diag
        final = european_odds.get('final')
        if not isinstance(final, dict):
            return base_home_lambda, base_away_lambda, diag
        oh = self._to_float(final.get('home'))
        od = self._to_float(final.get('draw'))
        oa = self._to_float(final.get('away'))
        if not oh or not od or not oa or min(oh, od, oa) <= 1.01:
            return base_home_lambda, base_away_lambda, diag

        anomaly_diag = self.detect_market_odds_anomaly(
            league_code=league_code,
            european_odds=european_odds,
            asian_handicap=asian_handicap,
            base_home_lambda=base_home_lambda,
            base_away_lambda=base_away_lambda,
        )
        diag['odds_anomaly'] = anomaly_diag
        weight = float(anomaly_diag.get('calibration_weight') or 1.0)
        if anomaly_diag.get('level') == 'high':
            diag.update({'applied': False, 'reason': 'market_odds_anomaly_high', 'kept_base_lambda': {'home': round(float(base_home_lambda), 3), 'away': round(float(base_away_lambda), 3), 'total': round(float(base_home_lambda + base_away_lambda), 3)}})
            return base_home_lambda, base_away_lambda, diag

        ph = 1.0 / oh
        pd = 1.0 / od
        pa = 1.0 / oa
        total = ph + pd + pa
        ph, pd, pa = ph / total, pd / total, pa / total

        dc = DixonColesModel(rho=resolve_rho(league_code))
        league_avg = float(self.league_config.get(league_code, {}).get('avg_goals') or 2.6)
        base_total = max(0.8, float(base_home_lambda) + float(base_away_lambda))
        # 队力基准锚权重：默认 0.08，world_cup 等 FIFA 兜底联赛可在 BY_LEAGUE 调低，
        # 让市场 OU 总进球锚更有话语权（不过度被偏低的兜底队力拽回）。
        base_anchor_w = float(self.LAMBDA_BASE_ANCHOR_COST_BY_LEAGUE.get(league_code, self.LAMBDA_BASE_ANCHOR_COST))
        # cost_total 的两项拉力权重：往联赛均值拉(avg) + 往队力基准总量拉(base)。
        # world_cup 等兜底队力联赛可在 BY_LEAGUE 削弱 base 项，避免偏低 base_total 按住 λ。
        total_avg_w = float(self.LAMBDA_TOTAL_AVG_COST)
        total_base_w = float(self.LAMBDA_TOTAL_BASE_COST_BY_LEAGUE.get(league_code, self.LAMBDA_TOTAL_BASE_COST))
        # OU 锚权重：默认 0.10，world_cup 在 BY_LEAGUE 提到 0.30（市场大小球是最可信的进球量级信号）。
        anchor_w = float(self.LAMBDA_OU_ANCHOR_COST_BY_LEAGUE.get(league_code, self.LAMBDA_OU_ANCHOR_COST))
        # 大小球盘口反解的市场总进球锚：仅在落入合理量级时启用，避免脏盘把 λ 拉飞。
        anchor_total: Optional[float] = None
        if market_total_anchor is not None:
            try:
                cand = float(market_total_anchor)
                if 0.8 <= cand <= 6.0:
                    anchor_total = cand
            except (TypeError, ValueError):
                anchor_total = None
        best = None
        best_cost = 1e9
        total_min = max(1.2, league_avg - 0.8)
        total_max = min(5.2, league_avg + 1.6)
        # 锚定的市场总进球可能落在默认网格之外（如盘口 3.5 而联赛均值 2.6），
        # 适度放宽上下界以容纳锚点，否则会被网格边界截断、锚定失效。
        if anchor_total is not None:
            total_min = min(total_min, max(0.8, anchor_total - 0.4))
            total_max = max(total_max, min(6.0, anchor_total + 0.4))
        for total_tick in range(int(total_min * 20), int(total_max * 20) + 1):
            total_goals = total_tick / 20.0
            for share_tick in range(20, 81, 2):
                share = share_tick / 100.0
                hl = max(0.15, total_goals * share)
                al = max(0.15, total_goals * (1 - share))
                probs = dc.predict_with_dixon_coles(hl, al)
                cost_prob = (probs['home_win'] - ph) ** 2 + (probs['draw'] - pd) ** 2 + (probs['away_win'] - pa) ** 2
                cost_base = base_anchor_w * ((hl - base_home_lambda) ** 2 + (al - base_away_lambda) ** 2)
                cost_total = total_avg_w * (total_goals - league_avg) ** 2 + total_base_w * (total_goals - base_total) ** 2
                cost_anchor = 0.0
                if anchor_total is not None:
                    cost_anchor = anchor_w * (total_goals - anchor_total) ** 2
                cost = cost_prob + cost_base + cost_total + cost_anchor
                if cost < best_cost:
                    best_cost = cost
                    best = (hl, al, probs)
        if not best:
            return base_home_lambda, base_away_lambda, diag

        hl, al, probs = best
        if weight < 0.999:
            hl = float(base_home_lambda) * (1.0 - weight) + float(hl) * weight
            al = float(base_away_lambda) * (1.0 - weight) + float(al) * weight
            probs = dc.predict_with_dixon_coles(hl, al)
        diag = {
            'applied': True,
            'source': 'euro_final_1x2',
            'odds_final': {'home': oh, 'draw': od, 'away': oa},
            'implied_probs': {'home': round(ph, 4), 'draw': round(pd, 4), 'away': round(pa, 4)},
            'model_probs': {'home': round(float(probs['home_win']), 4), 'draw': round(float(probs['draw']), 4), 'away': round(float(probs['away_win']), 4)},
            'lambda_base': {'home': round(float(base_home_lambda), 3), 'away': round(float(base_away_lambda), 3), 'total': round(float(base_total), 3)},
            'lambda_calibrated': {'home': round(float(hl), 3), 'away': round(float(al), 3), 'total': round(float(hl + al), 3)},
            'cost': round(float(best_cost), 6),
            'odds_anomaly': anomaly_diag,
            'blend_weight': round(weight, 4),
            'market_total_anchor': round(float(anchor_total), 4) if anchor_total is not None else None,
        }
        return float(hl), float(al), diag

    def apply_league_ou_learning(
        self,
        league_code: str,
        match_date: str,
        home_team: str,
        away_team: str,
        home_lambda: float,
        away_lambda: float,
        strength_diff: float,
        suppress_over_uplift: bool = False,
    ) -> Tuple[float, float, Dict[str, Any]]:
        learning = self.league_ou_learning.get_recent_learning(league_code=league_code, match_date=match_date)
        diag: Dict[str, Any] = {'applied': False, 'league_code': league_code, 'home_team': home_team, 'away_team': away_team, 'learning': learning}
        if not learning.get('available'):
            diag['reason'] = learning.get('reason', 'unavailable')
            return home_lambda, away_lambda, diag
        sample_size = int(learning.get('sample_size', 0) or 0)
        if sample_size < self.league_ou_learning.MIN_SAMPLE_SIZE:
            diag['reason'] = f'sample_size<{self.league_ou_learning.MIN_SAMPLE_SIZE}'
            return home_lambda, away_lambda, diag
        base_total = max(0.6, float(home_lambda) + float(away_lambda))
        league_avg = float(self.league_config.get(league_code, {}).get('avg_goals') or 2.6)
        recent_avg = float(learning.get('avg_goals') or league_avg)
        over25_rate = float(learning.get('over25_rate') or 0.0)
        over35_rate = float(learning.get('over35_rate') or 0.0)
        btts_rate = float(learning.get('btts_rate') or 0.0)
        clean_sheet_rate = float(learning.get('clean_sheet_rate') or 0.0)
        sample_weight = min(0.22, 0.08 + sample_size * 0.008)
        target_total = base_total * (1.0 - sample_weight) + recent_avg * sample_weight
        # 小球侧底水否决：市场已明确「钱压小球」时，不允许联赛学习层把 λ 往上 blend，
        # 也禁用 over 系的 total_scale 加成，只保留向下修正。
        if suppress_over_uplift and recent_avg > base_total:
            target_total = base_total
        total_scale = 1.0
        signals: List[str] = []
        if over25_rate >= 0.60:
            total_scale += 0.04
            signals.append('high_over25')
        elif over25_rate <= 0.35:
            total_scale -= 0.04
            signals.append('low_over25')
        if over35_rate >= 0.30:
            total_scale += 0.02
            signals.append('high_over35')
        elif over35_rate <= 0.12:
            total_scale -= 0.02
            signals.append('low_over35')
        if suppress_over_uplift:
            total_scale = min(total_scale, 1.0)
            signals.append('under_low_water_veto')
        target_total *= total_scale
        target_total = max(0.8, min(4.2, target_total))
        current_share = float(home_lambda) / base_total if base_total > 0 else 0.5
        adjusted_share = current_share
        if btts_rate >= 0.65 and abs(strength_diff) <= 18:
            adjusted_share = 0.5 + (current_share - 0.5) * 0.88
            signals.append('high_btts_balance')
        elif clean_sheet_rate >= 0.55 and abs(strength_diff) >= 12:
            adjusted_share = 0.5 + (current_share - 0.5) * 1.08
            signals.append('high_clean_sheet_skew')
        adjusted_share = max(0.18, min(0.82, adjusted_share))
        new_home_lambda = max(0.15, target_total * adjusted_share)
        new_away_lambda = max(0.15, target_total * (1.0 - adjusted_share))
        diag.update({
            'applied': True,
            'signals': signals,
            'sample_size': sample_size,
            'base_lambda': {'home': round(float(home_lambda), 3), 'away': round(float(away_lambda), 3), 'total': round(base_total, 3)},
            'adjusted_lambda': {'home': round(float(new_home_lambda), 3), 'away': round(float(new_away_lambda), 3), 'total': round(float(new_home_lambda + new_away_lambda), 3)},
            'league_avg_goals': round(league_avg, 3),
            'recent_avg_goals': round(recent_avg, 3),
            'over25_rate': round(over25_rate, 4),
            'over35_rate': round(over35_rate, 4),
            'btts_rate': round(btts_rate, 4),
            'clean_sheet_rate': round(clean_sheet_rate, 4),
            'sample_weight': round(sample_weight, 4),
            'target_total_scale': round(total_scale, 4),
        })
        return new_home_lambda, new_away_lambda, diag

    def apply_live_outcome_adjustment(
        self,
        league_code: str,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'signals': [], 'delta': {}}
        if not isinstance(final_prob, dict) or not isinstance(current_odds, dict):
            return final_prob, diag

        def _boost_side(
            home_prob: float,
            draw_prob: float,
            away_prob: float,
            *,
            side: str,
            amount: float,
        ) -> Tuple[float, float, float, float]:
            if amount <= 0:
                return home_prob, draw_prob, away_prob, 0.0
            pools = {
                'home': max(0.0, home_prob - 0.05),
                'draw': max(0.0, draw_prob - 0.05),
                'away': max(0.0, away_prob - 0.05),
            }
            donors = [name for name in ('home', 'draw', 'away') if name != side and pools[name] > 0]
            available = sum(pools[name] for name in donors)
            take = min(amount, available)
            if take <= 0:
                return home_prob, draw_prob, away_prob, 0.0
            probs = {'home': home_prob, 'draw': draw_prob, 'away': away_prob}
            for donor in donors:
                share = take * (pools[donor] / available) if available > 0 else 0.0
                probs[donor] -= share
            probs[side] += take
            return probs['home'], probs['draw'], probs['away'], take

        def _pick(d: Dict[str, Any], *keys: str) -> Any:
            current: Any = d
            for key in keys:
                if not isinstance(current, dict):
                    return None
                current = current.get(key)
            return current

        def _parse_euro_final(eu: Any) -> Tuple[Optional[float], Optional[float], Optional[float]]:
            if not isinstance(eu, dict):
                return None, None, None
            final = eu.get('final')
            if isinstance(final, dict):
                return self._to_float(final.get('home')), self._to_float(final.get('draw')), self._to_float(final.get('away'))
            final = eu.get('最新指数')
            if isinstance(final, dict):
                return self._to_float(final.get('主')), self._to_float(final.get('平')), self._to_float(final.get('客'))
            return None, None, None

        def _parse_asian(asian: Any) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
            if not isinstance(asian, dict):
                return None, None, None, None, None, None
            initial = asian.get('initial')
            final = asian.get('final')
            if isinstance(initial, dict) and isinstance(final, dict):
                hcp_i = self._to_float(initial.get('handicap') if 'handicap' in initial else initial.get('盘口值'))
                hcp_f = self._to_float(final.get('handicap') if 'handicap' in final else final.get('盘口值'))
                hw_i = self._to_float(initial.get('home_water') if 'home_water' in initial else initial.get('主水'))
                aw_i = self._to_float(initial.get('away_water') if 'away_water' in initial else initial.get('客水'))
                hw_f = self._to_float(final.get('home_water') if 'home_water' in final else final.get('主水'))
                aw_f = self._to_float(final.get('away_water') if 'away_water' in final else final.get('客水'))
                return hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f
            return None, None, None, None, None, None

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total
        original_probs = {'home_win': p_h, 'draw': p_d, 'away_win': p_a}

        euro = current_odds.get('欧赔')
        asian = current_odds.get('亚值')
        kelly = current_odds.get('凯利')
        oh, od, oa = _parse_euro_final(euro)
        hcp_i, hcp_f, hw_i, aw_i, hw_f, aw_f = _parse_asian(asian)
        kd = self._to_float(_pick(kelly, 'final', 'draw')) if isinstance(kelly, dict) else None

        fav_side = None
        fav_odds = None
        if isinstance(oh, float) and isinstance(oa, float):
            if oh <= oa:
                fav_side, fav_odds = 'home', oh
            else:
                fav_side, fav_odds = 'away', oa
        _ = od
        _ = league_code
        deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 0.75
        very_deep_handicap = isinstance(hcp_f, float) and abs(hcp_f) >= 1.0

        giver = None
        if isinstance(hcp_f, float):
            if hcp_f < -0.06:
                giver = 'home'
            elif hcp_f > 0.06:
                giver = 'away'
        retreat = isinstance(hcp_i, float) and isinstance(hcp_f, float) and abs(hcp_f) + 0.12 < abs(hcp_i)
        water_drift = False
        if fav_side == 'home' and isinstance(hw_i, float) and isinstance(hw_f, float) and (hw_f - hw_i) >= 0.04:
            water_drift = True
        if fav_side == 'away' and isinstance(aw_i, float) and isinstance(aw_f, float) and (aw_f - aw_i) >= 0.04:
            water_drift = True

        draw_boost = 0.0
        if isinstance(fav_odds, float) and fav_odds <= 1.60:
            draw_boost += 0.04
            diag['signals'].append('低赔强侧(<=1.60)')
        if deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag['signals'].append('深让>=0.75')
        if very_deep_handicap and giver == fav_side:
            draw_boost += 0.03
            diag['signals'].append('强让>=1.0')
        if retreat and giver == fav_side and very_deep_handicap:
            draw_boost += 0.03
            diag['signals'].append('强让退盘')
        if water_drift and giver == fav_side and very_deep_handicap:
            draw_boost += 0.02
            diag['signals'].append('强侧水位走高')
        if isinstance(kd, float) and kd <= 0.95:
            draw_boost += 0.01
            diag['signals'].append('平局凯利偏低')
        try:
            if isinstance(historical_odds_reference, dict):
                summary = historical_odds_reference.get('summary') or {}
                rates = summary.get('result_rates') or {}
                draw_rate = rates.get('平局')
                if isinstance(draw_rate, (int, float)) and draw_rate >= 0.33:
                    draw_boost += 0.02
                    diag['signals'].append('相似盘路平局率偏高')
        except Exception:
            pass

        market_alignment_diag: Dict[str, Any] = {}
        try:
            if isinstance(historical_odds_reference, dict):
                market_alignment = historical_odds_reference.get('market_alignment') or {}
                aligned_count = int(market_alignment.get('aligned_count') or 0)
                avg_alignment_score = float(market_alignment.get('avg_alignment_score') or 0.0)
                dominant_direction = str(market_alignment.get('dominant_direction') or 'balanced')
                same_psychology_count = int(market_alignment.get('same_psychology_count') or 0)
                same_capital_flow_count = int(market_alignment.get('same_capital_flow_count') or 0)
                same_totals_direction_count = int(market_alignment.get('same_totals_direction_count') or 0)
                side_boost = 0.0
                if dominant_direction in ('home', 'draw', 'away') and aligned_count >= 2 and avg_alignment_score >= 0.45:
                    side_boost += min(0.024, 0.008 + avg_alignment_score * 0.02)
                    if same_psychology_count >= 2:
                        side_boost += 0.006
                        diag['signals'].append('历史盘口操盘手法一致')
                    if same_capital_flow_count >= 2:
                        side_boost += 0.008
                        diag['signals'].append('历史盘口资金走向一致')
                    if same_totals_direction_count >= 2:
                        side_boost += 0.004
                        diag['signals'].append('历史盘口节奏方向一致')
                    if aligned_count >= 3:
                        side_boost += 0.004
                    side_boost = min(0.05, side_boost)
                    p_h, p_d, p_a, applied_take = _boost_side(
                        p_h,
                        p_d,
                        p_a,
                        side=dominant_direction,
                        amount=side_boost,
                    )
                    if applied_take > 0:
                        diag['signals'].append('历史盘口轨迹同向加权')
                        market_alignment_diag = {
                            'applied': True,
                            'dominant_direction': dominant_direction,
                            'aligned_count': aligned_count,
                            'avg_alignment_score': round(avg_alignment_score, 4),
                            'same_psychology_count': same_psychology_count,
                            'same_capital_flow_count': same_capital_flow_count,
                            'same_totals_direction_count': same_totals_direction_count,
                            'applied_boost': round(applied_take, 6),
                            'aligned_match_ids': market_alignment.get('aligned_match_ids') or [],
                        }
        except Exception:
            market_alignment_diag = {}

        draw_boost = min(0.10, max(0.0, draw_boost))
        if draw_boost <= 0:
            if market_alignment_diag.get('applied'):
                total2 = p_h + p_d + p_a
                if total2 > 0:
                    p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2
                diag['applied'] = True
                diag['delta'] = {
                    'home_win': round(p_h - original_probs['home_win'], 6),
                    'draw': round(p_d - original_probs['draw'], 6),
                    'away_win': round(p_a - original_probs['away_win'], 6),
                }
                diag['fav'] = {'side': fav_side, 'odds': fav_odds}
                diag['asian'] = {'handicap_initial': hcp_i, 'handicap_final': hcp_f, 'giver': giver, 'retreat': retreat, 'water_drift': water_drift}
                diag['historical_market_alignment'] = market_alignment_diag
                return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag
            return final_prob, diag
        if fav_side == 'home':
            take = min(draw_boost, max(0.0, p_h - 0.05))
            p_h -= take
            p_d += take
        elif fav_side == 'away':
            take = min(draw_boost, max(0.0, p_a - 0.05))
            p_a -= take
            p_d += take
        elif p_h >= p_a:
            take = min(draw_boost, max(0.0, p_h - 0.05))
            p_h -= take
            p_d += take
        else:
            take = min(draw_boost, max(0.0, p_a - 0.05))
            p_a -= take
            p_d += take

        total2 = p_h + p_d + p_a
        if total2 > 0:
            p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2

        diag['applied'] = True
        diag['delta'] = {
            'home_win': round(p_h - original_probs['home_win'], 6),
            'draw': round(p_d - original_probs['draw'], 6),
            'away_win': round(p_a - original_probs['away_win'], 6),
        }
        diag['fav'] = {'side': fav_side, 'odds': fav_odds}
        diag['asian'] = {'handicap_initial': hcp_i, 'handicap_final': hcp_f, 'giver': giver, 'retreat': retreat, 'water_drift': water_drift}
        if market_alignment_diag.get('applied'):
            diag['historical_market_alignment'] = market_alignment_diag
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    @staticmethod
    def _implied_total_from_ou_line(line: float, over_prob: float, under_prob: float) -> Optional[float]:
        # 反解出与"真实大小球盘口线 + 去水大小球概率"一致的总进球 λ：
        # 在泊松总进球分布下二分搜索 λ，使 P(总进球 > line) 的去水占比逼近 over 概率。
        # 用于球队无攻防数据（占位强度）时，用市场盘口锚定总进球量级，避免坍缩成 0-0。
        if line is None or line <= 0:
            return None
        op = float(over_prob or 0.0)
        up = float(under_prob or 0.0)
        if op <= 0 or up <= 0:
            return None
        over_share = op / (op + up)
        threshold = math.floor(line) if abs(line - round(line)) > 1e-9 else int(round(line))

        def over_share_for(total_lambda: float) -> float:
            # P(总进球 > line)，整数盘口的平局球数计入推球、从分母中剔除。
            p_over = 0.0
            p_under = 0.0
            pmf = math.exp(-total_lambda)
            for k in range(0, 16):
                if k > 0:
                    pmf *= total_lambda / k
                if abs(line - round(line)) <= 1e-9 and k == threshold:
                    continue
                if k > line:
                    p_over += pmf
                else:
                    p_under += pmf
            denom = p_over + p_under
            return p_over / denom if denom > 0 else 0.0

        lo, hi = 0.3, 6.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if over_share_for(mid) < over_share:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    def apply_market_ou_calibration(
        self,
        *,
        home_lambda: float,
        away_lambda: float,
        current_odds: Optional[Dict[str, Any]],
        strength_quality: str = 'real',
        asian_handicap: Optional[Dict[str, Any]] = None,
    ) -> Tuple[float, float, Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'market_pressure_source': 'market_signal', 'strength_quality': strength_quality}
        if not self.postprocess_service:
            diag['reason'] = 'missing_postprocess_service'
            return home_lambda, away_lambda, diag
        market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
        diag['market_signal'] = market_signal
        if not isinstance(market_signal, dict) or not market_signal.get('available'):
            diag['reason'] = 'market_signal_unavailable'
            return home_lambda, away_lambda, diag
        current_total = max(0.6, float(home_lambda) + float(away_lambda))
        share = float(home_lambda) / current_total if current_total > 0 else 0.5

        # 退化（占位/兜底强度）场景下，模型 λ 的主客拆分同样不可信，
        # 用真实亚盘让球数反推主客净胜球，得到有真实盘口背书的 share。
        ah_share = None
        ah_diag: Dict[str, Any] = {'applied': False}
        if strength_quality in ('flat', 'fallback') and isinstance(asian_handicap, dict):
            ah_fin = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}
            hcp = None
            for key in ('handicap', 'handicap_value', '盘口值', 'handicap_text', '盘口'):
                hcp = self._parse_handicap_value(ah_fin.get(key))
                if hcp is not None:
                    break
            if hcp is not None:
                # _parse_handicap_value：让球为负、受让为正（主队视角）。
                # 主队预期净胜球 supremacy = -hcp（让球越深，主队越强）。
                ah_diag = {'applied': True, 'handicap_home_view': round(float(hcp), 4), 'supremacy': round(float(-hcp), 4)}
        diag['asian_share_signal'] = ah_diag

        # 球队攻防数据缺失（占位/兜底强度，如世界杯无 teams 数据）时，模型 λ 不可信，
        # 用真实大小球盘口线的绝对量级锚定总进球，避免低 λ 把概率全堆到 0-0。
        degraded = strength_quality in ('flat', 'fallback')
        final_prices = market_signal.get('final_prices') if isinstance(market_signal.get('final_prices'), dict) else {}
        final_line = self._to_float(market_signal.get('final_line'))
        implied_total = None
        if degraded and final_line and final_prices.get('available'):
            implied_total = self._implied_total_from_ou_line(
                final_line,
                self._to_float(final_prices.get('over_prob')) or 0.0,
                self._to_float(final_prices.get('under_prob')) or 0.0,
            )
        if implied_total is not None:
            anchor_weight = 0.85 if strength_quality == 'flat' else 0.6
            target_total = current_total * (1.0 - anchor_weight) + implied_total * anchor_weight
            # pace_shift 也需在锚定分支生效：去水会把「小球/大球侧水位档位差」洗成近中性，
            # implied_total 因此丢失「钱压某一侧」的静态水位信号（如 6-19 墨西哥小球底水被去水抹平）。
            # postprocess 的 pace_shift 显式保留了该信号，这里叠加进锚定后的总进球。
            pace_shift_anchor = float(market_signal.get('pace_shift') or 0.0)
            if abs(pace_shift_anchor) >= 1e-9:
                target_total += pace_shift_anchor * 3.6
                diag['anchor_pace_shift'] = round(pace_shift_anchor, 6)
            target_total = max(0.8, min(4.5, target_total))
            effective_share = share
            if ah_diag.get('applied'):
                # 在锚定后的总进球下，用让球数推主客 share：home_λ-away_λ≈supremacy。
                # share=(total+supremacy)/(2*total)，并与模型 share 取 0.7 权重融合后夹紧。
                supremacy = float(ah_diag['supremacy'])
                ah_share = (target_total + supremacy) / (2.0 * target_total)
                ah_share = max(0.18, min(0.82, ah_share))
                effective_share = max(0.18, min(0.82, share * 0.3 + ah_share * 0.7))
                ah_diag['share_from_handicap'] = round(float(ah_share), 4)
                ah_diag['model_share'] = round(float(share), 4)
                ah_diag['effective_share'] = round(float(effective_share), 4)
            new_home = max(0.15, target_total * effective_share)
            new_away = max(0.15, target_total * (1.0 - effective_share))
            diag.update(
                {
                    'applied': True,
                    'market_pressure_source': 'absolute_ou_line',
                    'implied_total_from_line': round(float(implied_total), 6),
                    'anchor_weight': anchor_weight,
                    'final_line': round(float(final_line), 4),
                    'target_total': round(float(target_total), 6),
                    'base_total': round(float(current_total), 6),
                    'share_used': round(float(effective_share), 6),
                    'adjusted_lambda': {
                        'home': round(float(new_home), 6),
                        'away': round(float(new_away), 6),
                        'total': round(float(new_home + new_away), 6),
                    },
                }
            )
            return new_home, new_away, diag

        pace_shift = float(market_signal.get('pace_shift') or 0.0)
        if abs(pace_shift) < 1e-9:
            diag['reason'] = 'market_signal_flat'
            diag['market_pace_shift'] = 0.0
            return home_lambda, away_lambda, diag
        target_total = max(0.8, min(4.2, current_total + pace_shift * 3.6))
        new_home = max(0.15, target_total * share)
        new_away = max(0.15, target_total * (1.0 - share))
        diag.update(
            {
                'applied': True,
                'market_pace_shift': round(pace_shift, 6),
                'target_total': round(float(target_total), 6),
                'base_total': round(float(current_total), 6),
                'adjusted_lambda': {
                    'home': round(float(new_home), 6),
                    'away': round(float(new_away), 6),
                    'total': round(float(new_home + new_away), 6),
                },
            }
        )
        return new_home, new_away, diag

    def build_real_market_over_under(
        self,
        *,
        home_lambda: float,
        away_lambda: float,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        match_intelligence: Optional[Dict[str, Any]],
        realtime_context_applied: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str] = None,
        strength_diff: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        _ = (predicted_outcome, strength_diff)
        diag: Dict[str, Any] = {
            'available': False,
            'requires_real_market_line': True,
            'refined': {
                'market_lambda_applied': bool(isinstance(realtime_context_applied, dict) and isinstance(realtime_context_applied.get('market_ou_lambda_calibration'), dict) and realtime_context_applied.get('market_ou_lambda_calibration', {}).get('applied')),
                'reused_market_signal': False,
                'market_signal_shift': None,
            },
        }
        ou_line, ou_line_source = resolve_over_under_line(
            current_odds=current_odds,
            analysis_context=analysis_context,
            to_float=self._to_float,
        )
        diag['line_source'] = ou_line_source
        if ou_line_source not in self.REAL_OU_LINE_SOURCES or not isinstance(ou_line, float):
            diag['reason'] = 'missing_real_market_line'
            return {
                'available': False,
                'requires_real_market_line': True,
                'line_source': ou_line_source,
                'reason': 'missing_real_market_line',
            }, diag

        over_under = self.poisson_model.predict_over_under(home_lambda, away_lambda, line=ou_line)
        if not isinstance(over_under, dict):
            diag['reason'] = 'poisson_predict_over_under_failed'
            return {
                'available': False,
                'requires_real_market_line': True,
                'line_source': ou_line_source,
                'reason': 'poisson_predict_over_under_failed',
            }, diag

        if diag['refined']['market_lambda_applied'] and self.postprocess_service:
            market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
            diag['refined']['reused_market_signal'] = bool(market_signal.get('available'))
            diag['refined']['market_signal_shift'] = 0.0 if market_signal.get('available') else None
            if market_signal.get('available'):
                final_bias = float(market_signal.get('bias_final') or 0.0)
                if final_bias >= 0.03:
                    over_under['over'] = max(0.0, float(over_under.get('over') or 0.0) - min(0.05, final_bias * 0.18))
                    over_under['under'] = min(1.0, float(over_under.get('under') or 0.0) + min(0.05, final_bias * 0.18))
                elif final_bias <= -0.03:
                    shift = min(0.05, abs(final_bias) * 0.18)
                    over_under['over'] = min(1.0, float(over_under.get('over') or 0.0) + shift)
                    over_under['under'] = max(0.0, float(over_under.get('under') or 0.0) - shift)

        over_under, ou_resonance_diag = self.match_intelligence_engine._apply_market_resonance_to_over_under(
            over_under=over_under,
            match_intelligence=match_intelligence,
        )
        if isinstance(realtime_context_applied, dict):
            realtime_context_applied['market_resonance_over_under_adjustment'] = ou_resonance_diag
            learning_diag = realtime_context_applied.get('league_over_under_learning', {})
        else:
            learning_diag = {}

        over_under = self.postprocess_service.attach_over_under_context(
            over_under=over_under,
            current_odds=current_odds,
            learning_diag=learning_diag,
            ou_line_source=ou_line_source,
            realtime_context_applied=realtime_context_applied,
        )
        over_under['available'] = True
        over_under['requires_real_market_line'] = True
        over_under['used_real_market_line'] = True
        conviction_diag = self._assess_over_under_conviction(over_under, current_odds)
        if not conviction_diag.get('commit'):
            over_under['ou_neutral'] = True
            over_under['neutral_reason'] = conviction_diag.get('reason') or 'low_ou_conviction'
        over_under['conviction'] = conviction_diag
        if conviction_diag.get('line_up_over_reversal'):
            over_under['line_up_over_reversal'] = True
        diag['conviction'] = conviction_diag
        diag.update(
            {
                'available': True,
                'line': round(float(ou_line), 3),
                'line_source': ou_line_source,
            }
        )
        return over_under, diag

    def _assess_over_under_conviction(
        self,
        over_under: Dict[str, Any],
        current_odds: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """大小球方向可信度闸门。

        复盘结论：单场大小球命中率仅 ~34%，且与模型置信度无相关——盘口微动噪声被当成了信号。
        因此默认不提交方向（转中性），仅当「模型边际足够」且「市场资金真实同向」时才落方向。
        关键：反向资金诱导（暗钱背离 ou_line_water_divergence）不计入同向证据，
        即便它把 over/under 推到某一侧，也不允许据此提交方向——只保留为展示标签。

        2026-07-07 世界杯 90 场复盘增强：**中性盘退让阈值**。盘口线在 2.25/2.5/2.75
        这一"进球均值附近的等价档"上，样本命中率仅 40%~65%，远低于极端档（≥3.5 或 ≤2.0）。
        对中性盘施加更高的 margin 门槛（0.15），把"预大边际 0.05"这种噪声一律转 push。

        2026-07-07 世界杯 90 场复盘增强：**高盘反打爆冷预警**。当盘口线 ≥ 3.25 且模型
        反打小球（`model_side='under'`），实测命中率仅 20%（5 场里错 4 场），
        属于「机构做高盘诱下」的经典陷阱。此时强制转 push，并在 reason 中标注
        `high_line_under_guard` 供网页渲染 ⚠️「高盘预小需谨慎」爆冷提示。

        2026-07-07 世界杯 91 场复盘增强：**升盘反打大球预警**。当盘口线上升 ≥ 0.25
        且模型预 under，实测 14 场里大球出现 9 场（64.3%），模型 under 命中率仅 35.7%。
        属于「机构升盘明面示强大球、暗面吸小球诱导」的经典诱盘手法。此时不改模型
        方向（仍走 always_neutral / high_line_under_guard 通道），仅在 conviction 中
        暴露 `line_up_over_reversal=True` 供网页渲染 ⚠️「升盘反打大球」爆冷提示。
        """
        over = self._to_float(over_under.get('over'))
        under = self._to_float(over_under.get('under'))
        if over is None or under is None:
            return {'commit': False, 'reason': 'invalid_ou_probs', 'margin': None}
        margin = abs(over - under)
        model_side = 'over' if over >= under else 'under'
        market_signal = {}
        if self.postprocess_service:
            market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds) or {}
        goal_pressure = str(market_signal.get('goal_pressure') or 'balanced')
        market_side = 'over' if goal_pressure == 'over' else 'under' if goal_pressure == 'under' else None
        reverse_money = bool(market_signal.get('ou_line_water_divergence'))
        market_agrees = (market_side == model_side)
        genuine_agree = market_agrees and not reverse_money
        # 中性盘退让：line ∈ {2.25, 2.5, 2.75} 需要更高的模型边际才能提交方向。
        line_val = self._to_float(over_under.get('line'))
        neutral_line = line_val is not None and abs(line_val - 2.5) <= 0.25
        effective_min_margin = (
            max(self.ou_min_conviction_margin, 0.15)
            if neutral_line
            else self.ou_min_conviction_margin
        )
        # 高盘反打爆冷闸门：line ≥ 3.25 且模型预小，视为机构做高盘诱下陷阱。
        high_line_under_guard = bool(
            line_val is not None and line_val >= 3.25 and model_side == 'under'
        )
        # 升盘反打大球预警：盘口线上升 ≥ 0.25 且模型预 under → 91 场样本实际大球率 64.3%。
        # 此处仅识别不改方向（可能已被 always_neutral / high_line_under_guard 提前 push），
        # 但在网页渲染醒目预警 tag。
        initial_line = None
        market_block = over_under.get('market') if isinstance(over_under.get('market'), dict) else {}
        initial_block = market_block.get('initial') if isinstance(market_block.get('initial'), dict) else {}
        initial_line = self._to_float(initial_block.get('line'))
        line_delta = None
        if line_val is not None and initial_line is not None:
            line_delta = round(float(line_val) - float(initial_line), 3)
        line_up_over_reversal = bool(
            line_delta is not None and line_delta >= 0.25 and model_side == 'under'
        )
        if self.ou_always_neutral:
            commit = False
            reason = 'ou_neutral_policy'
        elif high_line_under_guard:
            commit = False
            reason = 'high_line_under_guard'
        else:
            commit = bool(margin >= effective_min_margin and genuine_agree)
            if commit:
                reason = 'committed'
            elif reverse_money and market_agrees:
                reason = 'reverse_money_only'
            elif margin < effective_min_margin:
                reason = 'neutral_line_low_margin' if neutral_line else 'low_margin'
            else:
                reason = 'no_market_corroboration'
        return {
            'commit': commit,
            'reason': reason,
            'margin': round(float(margin), 4),
            'model_side': model_side,
            'market_side': market_side,
            'reverse_money': reverse_money,
            'min_margin': effective_min_margin,
            'neutral_line': neutral_line,
            'high_line_under_guard': high_line_under_guard,
            'line_up_over_reversal': line_up_over_reversal,
            'line_delta': line_delta,
            'initial_line': initial_line,
        }

    def apply_real_totals_outcome_adjustment(
        self,
        *,
        final_prob: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {'applied': False, 'source': 'real_market_over_under'}
        if not isinstance(final_prob, dict) or not isinstance(over_under, dict):
            diag['reason'] = 'missing_inputs'
            return final_prob, diag
        if not over_under.get('available'):
            diag['reason'] = str(over_under.get('reason') or 'over_under_unavailable')
            return final_prob, diag
        line_source = str(over_under.get('line_source') or '').strip()
        if line_source not in self.REAL_OU_LINE_SOURCES:
            diag['reason'] = f'unsupported_line_source:{line_source or "unknown"}'
            return final_prob, diag

        line = self._to_float(over_under.get('line'))
        over_prob = self._to_float(over_under.get('over'))
        under_prob = self._to_float(over_under.get('under'))
        if line is None or over_prob is None or under_prob is None:
            diag['reason'] = 'invalid_over_under_payload'
            return final_prob, diag

        final_totals = {}
        if isinstance(current_odds, dict):
            totals = current_odds.get('大小球')
            if isinstance(totals, dict) and isinstance(totals.get('final'), dict):
                final_totals = totals.get('final') or {}
        market_over_odds = self._to_float(final_totals.get('over'))
        market_under_odds = self._to_float(final_totals.get('under'))
        market_bias = 0.0
        if (
            isinstance(market_over_odds, float)
            and isinstance(market_under_odds, float)
            and market_over_odds > 1.01
            and market_under_odds > 1.01
        ):
            implied_over = 1.0 / market_over_odds
            implied_under = 1.0 / market_under_odds
            total_implied = implied_over + implied_under
            if total_implied > 0:
                market_bias = implied_under / total_implied - implied_over / total_implied

        model_bias = float(under_prob) - float(over_prob)
        combined_bias = model_bias * 0.65 + market_bias * 0.35
        if line <= 2.25:
            combined_bias += 0.025
        elif line <= 2.5 and combined_bias > 0:
            combined_bias += 0.01
        elif line >= 3.25 and combined_bias < 0:
            combined_bias -= 0.025
        elif line >= 3.0 and combined_bias < 0:
            combined_bias -= 0.01
        combined_bias = max(-0.18, min(0.18, combined_bias))

        draw_delta = max(-0.02, min(0.03, combined_bias * 0.18))
        if abs(draw_delta) < 0.004:
            diag['reason'] = 'bias_below_threshold'
            diag['line'] = round(float(line), 3)
            diag['combined_bias'] = round(float(combined_bias), 4)
            return final_prob, diag

        p_h = float(final_prob.get('home_win') or 0.0)
        p_d = float(final_prob.get('draw') or 0.0)
        p_a = float(final_prob.get('away_win') or 0.0)
        total = p_h + p_d + p_a
        if total <= 0:
            diag['reason'] = 'invalid_probability_mass'
            return final_prob, diag
        p_h, p_d, p_a = p_h / total, p_d / total, p_a / total

        side_mass = max(1e-9, p_h + p_a)
        home_share = p_h / side_mass
        away_share = p_a / side_mass

        if draw_delta > 0:
            available_take = max(0.0, p_h - 0.02) + max(0.0, p_a - 0.02)
            actual_delta = min(draw_delta, available_take)
            take_home = min(max(0.0, p_h - 0.02), actual_delta * home_share)
            take_away = min(max(0.0, p_a - 0.02), actual_delta - take_home)
            remainder = actual_delta - take_home - take_away
            if remainder > 1e-9:
                extra_home = min(max(0.0, p_h - 0.02) - take_home, remainder)
                take_home += extra_home
                remainder -= extra_home
            if remainder > 1e-9:
                extra_away = min(max(0.0, p_a - 0.02) - take_away, remainder)
                take_away += extra_away
            p_h -= take_home
            p_a -= take_away
            p_d += take_home + take_away
        else:
            actual_delta = min(-draw_delta, max(0.0, p_d - 0.02))
            p_d -= actual_delta
            p_h += actual_delta * home_share
            p_a += actual_delta * away_share
            actual_delta = -actual_delta

        total2 = p_h + p_d + p_a
        if total2 > 0:
            p_h, p_d, p_a = p_h / total2, p_d / total2, p_a / total2

        diag.update(
            {
                'applied': True,
                'line': round(float(line), 3),
                'line_source': line_source,
                'model_bias': round(float(model_bias), 4),
                'market_bias': round(float(market_bias), 4),
                'combined_bias': round(float(combined_bias), 4),
                'draw_delta': round(float(actual_delta), 4),
                'effect': 'under_to_draw' if actual_delta > 0 else 'over_reduce_draw',
                'adjusted_probabilities': {
                    'home_win': round(float(p_h), 6),
                    'draw': round(float(p_d), 6),
                    'away_win': round(float(p_a), 6),
                },
            }
        )
        return {'home_win': p_h, 'draw': p_d, 'away_win': p_a}, diag

    def run(
        self,
        *,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str,
        current_odds: Optional[Dict[str, Any]],
        analysis_context: Dict[str, Any],
        realtime: Dict[str, Any],
        review_learning: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        applied_weights = self.apply_dynamic_weights(league_code)
        home_strength = self.team_manager.analyze_team_strength(league_code, home_team)
        away_strength = self.team_manager.analyze_team_strength(league_code, away_team)
        match_intelligence = self.match_intelligence_engine._build_match_intelligence(
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            match_date=match_date,
            analysis_context=analysis_context,
            current_odds=current_odds,
            home_strength=home_strength,
            away_strength=away_strength,
        )
        realtime['context_applied']['match_intelligence'] = {
            'available': bool(match_intelligence.get('available')),
            'signals': match_intelligence.get('signals', []),
            'market_signals': (match_intelligence.get('market', {}) or {}).get('signals', []),
        }
        try:
            motivation = match_intelligence.get('motivation') if isinstance(match_intelligence, dict) else {}
            if 'home_motivation' not in analysis_context and isinstance(motivation, dict):
                suggested_scores = motivation.get('suggested_scores') or {}
                if suggested_scores.get('home') is not None:
                    analysis_context['home_motivation'] = float(suggested_scores['home'])
                if suggested_scores.get('away') is not None:
                    analysis_context['away_motivation'] = float(suggested_scores['away'])
            h2h_context = match_intelligence.get('head_to_head') if isinstance(match_intelligence, dict) else {}
            if isinstance(h2h_context, dict) and h2h_context.get('available'):
                analysis_context.setdefault('h2h_home_wins', int(h2h_context.get('home_wins', 0)))
                analysis_context.setdefault('h2h_away_wins', int(h2h_context.get('away_wins', 0)))
                analysis_context.setdefault('h2h_draws', int(h2h_context.get('draws', 0)))
        except Exception as exc:
            realtime['context_applied']['match_intelligence_bind_error'] = str(exc)

        strength_diff = home_strength['strength'] - away_strength['strength']
        # 强度数据质量：双方都有真实球员数据=real；任一退回兜底/默认=fallback/flat。
        sources = {home_strength.get('strength_source', 'real'), away_strength.get('strength_source', 'real')}
        if 'flat_default' in sources:
            strength_quality = 'flat'
        elif 'national_fallback' in sources:
            strength_quality = 'fallback'
        else:
            strength_quality = 'real'
        realtime['context_applied']['strength_quality'] = strength_quality
        asian_handicap = current_odds.get('亚值') if isinstance(current_odds, dict) else None
        european_odds = current_odds.get('欧赔') if isinstance(current_odds, dict) else None
        league_avg_goals = self.league_config[league_code]['avg_goals']
        home_form = int(analysis_context.get('home_form', 3))
        away_form = int(analysis_context.get('away_form', 3))
        home_motivation = float(analysis_context.get('home_motivation', 75))
        away_motivation = float(analysis_context.get('away_motivation', 75))
        realtime['context_applied'].update({'home_form': home_form, 'away_form': away_form, 'home_motivation': home_motivation, 'away_motivation': away_motivation})

        # league_avg_goals 是“全场总进球”均值，需除以 2 才是单队进球基准，
        # 否则两队 λ 各自≈整场总进球、合计翻倍，导致预期进球与大球概率系统性偏高。
        per_team_baseline = league_avg_goals / 2.0
        home_advantage = self._resolve_home_advantage(league_code, home_team)
        base_home_lambda = home_strength['attack'] * away_strength['defense'] * per_team_baseline * home_advantage
        base_away_lambda = away_strength['attack'] * home_strength['defense'] * per_team_baseline
        realtime['context_applied']['home_advantage_factor'] = round(float(home_advantage), 4)
        home_lambda = base_home_lambda
        away_lambda = base_away_lambda
        # 大小球盘口反解的市场总进球锚：用真实大小球终盘线 + 去水概率反推 implied_total，
        # 传入 λ 校准联合拟合，让 λ 总量同时贴合 1X2 与市场对总进球的定价（治本：缓解为
        # 拟合均势 1X2 而压低总进球）。仅在大小球盘口可用时生效，否则锚为 None（行为不变）。
        market_total_anchor = None
        try:
            ou_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
            if isinstance(ou_signal, dict) and ou_signal.get('available'):
                final_prices = ou_signal.get('final_prices') if isinstance(ou_signal.get('final_prices'), dict) else {}
                final_line = self._to_float(ou_signal.get('final_line'))
                if final_line and final_prices.get('available'):
                    market_total_anchor = self._implied_total_from_ou_line(
                        final_line,
                        self._to_float(final_prices.get('over_prob')) or 0.0,
                        self._to_float(final_prices.get('under_prob')) or 0.0,
                    )
        except Exception:
            market_total_anchor = None
        try:
            home_lambda, away_lambda, cal_diag = self.calibrate_lambdas_from_market(
                league_code=league_code,
                base_home_lambda=base_home_lambda,
                base_away_lambda=base_away_lambda,
                european_odds=european_odds,
                asian_handicap=asian_handicap,
                market_total_anchor=market_total_anchor,
            )
            realtime['context_applied']['lambda_calibration'] = cal_diag
        except Exception as exc:
            realtime['context_applied']['lambda_calibration'] = {'applied': False, 'error': str(exc)}
        try:
            home_lambda, away_lambda, market_ou_lambda_diag = self.apply_market_ou_calibration(
                home_lambda=home_lambda,
                away_lambda=away_lambda,
                current_odds=current_odds,
                strength_quality=strength_quality,
                asian_handicap=asian_handicap,
            )
            realtime['context_applied']['market_ou_lambda_calibration'] = market_ou_lambda_diag
        except Exception as exc:
            realtime['context_applied']['market_ou_lambda_calibration'] = {'applied': False, 'error': str(exc)}
        # 小球侧否决标志（两级解耦）：
        # ① under_low_water_veto（窄）：仅小球侧底水加成（under_low_water_nudge，最强「钱买小」信号）。
        #    抑制后续 λ 上抬环节（联赛学习上调 + 世界杯尾部上抬），让市场底水定调。
        #    保持窄口径：普通偏小盘口不应连 world_cup 尾部补偿一起关掉（fallback λ 本就低估进球）。
        # ② suppress_review_over_push（宽）：底水信号 或 大小球盘口显式偏小（goal_pressure == 'under'）。
        #    仅否决「复盘补大球」这条后置 over-push，避免把已定价小球的盘口（如 2.75 葡萄牙vs刚果、
        #    2.25 墨西哥vs南非）重新顶回大球；λ 上抬环节不受此标志影响。
        under_low_water_veto = False
        suppress_review_over_push = False
        try:
            _msig = market_ou_lambda_diag.get('market_signal') if isinstance(market_ou_lambda_diag, dict) else None
            if isinstance(_msig, dict):
                if _msig.get('under_low_water_nudge'):
                    under_low_water_veto = True
                if _msig.get('under_low_water_nudge') or _msig.get('goal_pressure') == 'under':
                    suppress_review_over_push = True
        except Exception:
            under_low_water_veto = False
            suppress_review_over_push = False
        try:
            home_lambda, away_lambda, ou_learning_diag = self.apply_league_ou_learning(
                league_code=league_code,
                match_date=match_date,
                home_team=home_team,
                away_team=away_team,
                home_lambda=home_lambda,
                away_lambda=away_lambda,
                strength_diff=strength_diff,
                suppress_over_uplift=under_low_water_veto,
            )
            realtime['context_applied']['league_over_under_learning'] = ou_learning_diag
        except Exception as exc:
            realtime['context_applied']['league_over_under_learning'] = {'applied': False, 'error': str(exc)}
        try:
            quant_adjustment = match_intelligence.get('quant_adjustment') if isinstance(match_intelligence, dict) else {}
            if isinstance(quant_adjustment, dict):
                home_scale = float(quant_adjustment.get('home_lambda_scale') or 1.0)
                away_scale = float(quant_adjustment.get('away_lambda_scale') or 1.0)
                home_lambda = max(0.15, home_lambda * home_scale)
                away_lambda = max(0.15, away_lambda * away_scale)
                realtime['context_applied']['match_intelligence_lambda_scale'] = {'home_lambda_scale': home_scale, 'away_lambda_scale': away_scale}
        except Exception as exc:
            realtime['context_applied']['match_intelligence_lambda_scale'] = {'error': str(exc)}

        # 世界杯 FIFA 兜底队力下，λ 被市场校准压低，高进球尾部（≥4 球）被系统性低估，
        # 复盘 18 场实测总进球均值 3.06 而模型最可能仅 2 球。对 world_cup 做尾部上抬：
        # 仅当合计 λ 低于联赛进球基准时按缺口补足，保持主客 λ 占比不变，
        # 真实球员数据缺失（fallback）时补偿更强。中位场次不翻盘，只拉起爆大球尾部质量。
        if league_code == 'world_cup':
            total_lambda_pre = home_lambda + away_lambda
            target_total_lambda = float(league_avg_goals)
            if under_low_water_veto:
                # 小球侧底水否决：市场已明确「钱压小球」，不再把 λ 往联赛基准上抬。
                realtime['context_applied']['world_cup_tail_uplift'] = {
                    'applied': False,
                    'reason': 'under_low_water_veto',
                    'total_lambda_before': round(total_lambda_pre, 4),
                }
            elif total_lambda_pre > 0 and total_lambda_pre < target_total_lambda:
                gap_ratio = (target_total_lambda - total_lambda_pre) / target_total_lambda
                close_rate = 0.42 if strength_quality != 'real' else 0.22
                scale = 1.0 + gap_ratio * close_rate
                score_ceiling_boost = bool(
                    total_lambda_pre >= 2.85
                    and total_lambda_pre <= 3.35
                    and home_lambda >= 1.7
                    and (home_lambda - away_lambda) >= 0.45
                )
                if score_ceiling_boost:
                    scale += 0.03
                scale = min(scale, 1.16 if score_ceiling_boost else 1.12)
                home_lambda *= scale
                away_lambda *= scale
                realtime['context_applied']['world_cup_tail_uplift'] = {
                    'applied': True,
                    'strength_quality': strength_quality,
                    'total_lambda_before': round(total_lambda_pre, 4),
                    'total_lambda_after': round(home_lambda + away_lambda, 4),
                    'scale': round(scale, 4),
                    'score_ceiling_boost': score_ceiling_boost,
                }
            else:
                realtime['context_applied']['world_cup_tail_uplift'] = {
                    'applied': False,
                    'reason': 'lambda_already_at_or_above_baseline',
                    'total_lambda_before': round(total_lambda_pre, 4),
                }

        home_xg = home_lambda * 0.8
        away_xg = away_lambda * 0.8
        h2h_home_wins = int(analysis_context.get('h2h_home_wins', 0))
        h2h_away_wins = int(analysis_context.get('h2h_away_wins', 0))
        h2h_draws = int(analysis_context.get('h2h_draws', 0))
        realtime['context_applied'].update({'h2h_home_wins': h2h_home_wins, 'h2h_away_wins': h2h_away_wins, 'h2h_draws': h2h_draws})

        # 市场（赔率隐含）概率 + 动态 α + 专家情报信号 + 各模型历史准确率
        market_probs = self._market_implied_1x2(european_odds)
        expert_signals = self._build_expert_signals(
            home_strength=home_strength,
            away_strength=away_strength,
            home_motivation=home_motivation,
            away_motivation=away_motivation,
            match_intelligence=match_intelligence,
            market_probs=market_probs,
        )
        odds_anomaly_diag = (realtime.get('context_applied', {})
                             .get('lambda_calibration', {})
                             .get('odds_anomaly'))
        alpha_diag = self._resolve_market_alpha(market_probs, applied_weights, expert_signals, strength_quality, odds_anomaly_diag)
        model_accuracy = applied_weights.get('model_accuracy') if isinstance(applied_weights, dict) else None

        historical_ratings = self._resolve_historical_ratings(
            league_code, home_team, away_team
        )

        fusion_result = self.model_fusion.predict(
            home_team=home_team,
            away_team=away_team,
            home_strength=home_strength['strength'],
            away_strength=away_strength['strength'],
            home_form=home_form,
            away_form=away_form,
            home_injuries=home_strength['injured_count'],
            away_injuries=away_strength['injured_count'],
            h2h_home_wins=h2h_home_wins,
            h2h_away_wins=h2h_away_wins,
            h2h_draws=h2h_draws,
            home_motivation=home_motivation,
            away_motivation=away_motivation,
            home_xg=home_xg,
            away_xg=away_xg,
            home_attack=home_strength['attack'],
            home_defense=home_strength['defense'],
            away_attack=away_strength['attack'],
            away_defense=away_strength['defense'],
            market_probs=market_probs,
            market_alpha=alpha_diag.get('alpha'),
            expert_signals=expert_signals,
            model_accuracy=model_accuracy,
            historical_ratings=historical_ratings,
            per_team_baseline=per_team_baseline,
            home_advantage=home_advantage,
        )
        realtime['context_applied']['market_fusion'] = fusion_result.get('market_fusion')
        realtime['context_applied']['market_alpha'] = alpha_diag
        realtime['context_applied']['expert_signals'] = expert_signals
        final_prob = fusion_result['final']
        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)

        if isinstance(current_odds, dict) and current_odds:
            exclude_match_id = current_odds.get('match_id')
            historical_odds_reference = self.odds_reference.find_similar_matches(
                league_code=league_code,
                current_odds=current_odds,
                top_k=5,
                exclude_match_id=exclude_match_id,
            )
        else:
            historical_odds_reference = {
                'available': False,
                'league_history_count': self.odds_reference.get_league_record_count(league_code),
                'matched_feature_count': 0,
                'similar_matches': [],
                'summary': {'sample_size': 0, 'result_counts': {'主胜': 0, '平局': 0, '客胜': 0}, 'result_rates': {'主胜': 0.0, '平局': 0.0, '客胜': 0.0}, 'cold_result_count': 0, 'cold_result_rate': 0.0},
                'insights': ['当前未传入赔率快照，历史赔率参考已就绪但未参与匹配'],
            }

        adjusted_prob, live_adj_diag = self.apply_live_outcome_adjustment(
            league_code=league_code,
            final_prob=final_prob,
            current_odds=current_odds,
            historical_odds_reference=historical_odds_reference,
        )
        if isinstance(live_adj_diag, dict) and live_adj_diag.get('applied'):
            final_prob = adjusted_prob
            ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        realtime['context_applied']['live_outcome_adjustment'] = live_adj_diag
        anchor_ranked_probabilities = list(ranked_probabilities)
        preliminary_upset_potential = self._build_preliminary_upset_potential(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            strength_diff=strength_diff,
            predicted_outcome=ranked_probabilities[0][0] if ranked_probabilities else '平局',
            asian_handicap=asian_handicap,
            european_odds=european_odds,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['preliminary_upset_signal'] = {
            'available': bool(preliminary_upset_potential.get('available')),
            'motivation_risk': preliminary_upset_potential.get('motivation_risk') or {},
            'handicap_strength_mismatch': preliminary_upset_potential.get('handicap_strength_mismatch') or {},
        }
        review_stage_input = dict(final_prob)
        final_prob, review_outcome_diag = self.postprocess_service.apply_review_outcome_adjustment(
            final_probabilities=final_prob,
            league_code=league_code,
            strength_diff=strength_diff,
            asian_handicap=asian_handicap,
            current_odds=current_odds,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
            upset_potential=preliminary_upset_potential,
        )
        review_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'review_outcome_adjustment'}
        if isinstance(review_outcome_diag, dict) and review_outcome_diag.get('applied'):
            final_prob, review_stage_cap_diag = self._apply_near_tie_stage_cap(
                before_prob=review_stage_input,
                after_prob=final_prob,
                stage_name='review_outcome_adjustment',
            )
            ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        realtime['context_applied']['review_outcome_adjustment'] = review_outcome_diag
        realtime['context_applied']['review_outcome_stage_cap'] = review_stage_cap_diag
        match_intel_stage_input = dict(final_prob)
        final_prob, match_intel_diag = self.match_intelligence_engine._apply_match_intelligence_adjustment(final_prob=final_prob, match_intelligence=match_intelligence)
        match_intel_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'match_intelligence_adjustment'}
        if isinstance(match_intel_diag, dict):
            if match_intel_diag.get('applied'):
                final_prob, match_intel_stage_cap_diag = self._apply_near_tie_stage_cap(
                    before_prob=match_intel_stage_input,
                    after_prob=final_prob,
                    stage_name='match_intelligence_adjustment',
                )
            realtime['context_applied']['match_intelligence_adjustment'] = match_intel_diag
            realtime['context_applied']['match_intelligence_stage_cap'] = match_intel_stage_cap_diag
            retry_motivation = preliminary_upset_potential.get('motivation_risk') if isinstance(preliminary_upset_potential, dict) and isinstance(preliminary_upset_potential.get('motivation_risk'), dict) else {}
            retry_ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
            retry_top_label = retry_ranked_probabilities[0][0] if retry_ranked_probabilities else ''
            retry_home = float(final_prob.get('home_win', 0.0))
            retry_draw = float(final_prob.get('draw', 0.0))
            retry_away = float(final_prob.get('away_win', 0.0))
            retry_top_lead = retry_home - max(retry_draw, retry_away) if retry_top_label == '主胜' else 0.0
            retry_signals = {
                str(item).strip()
                for item in ((review_outcome_diag or {}).get('signals') or [])
                if str(item).strip()
            }
            retry_has_premier_away_support = bool(
                retry_signals.intersection({
                    'review-league-premier-away-upset-support',
                    'review-league-premier-balanced-away-floor',
                    'review-league-premier-away-follow-through',
                })
            )
            retry_strong_away_motivation = bool(
                bool(retry_motivation.get('supports_upset'))
                and str(retry_motivation.get('favored_side') or '').strip() == 'away'
                and str(retry_motivation.get('pressure_side') or '').strip() == 'home'
                and float(retry_motivation.get('score') or 0.0) >= 12.0
            )
            retry_balanced_home_top_case = bool(
                retry_top_label == '主胜'
                and retry_home <= 0.392
                and retry_draw >= 0.318
                and retry_away >= 0.272
                and retry_top_lead <= 0.07
            )
            should_retry_review_after_match_intelligence = bool(
                league_code == 'premier_league'
                and retry_top_label == '主胜'
                and not retry_has_premier_away_support
                and (retry_strong_away_motivation or retry_balanced_home_top_case)
            )
            realtime['context_applied']['review_outcome_retry_gate'] = {
                'eligible': should_retry_review_after_match_intelligence,
                'league_code': league_code,
                'top_label': retry_top_label,
                'motivation_risk': retry_motivation,
                'strong_away_motivation': retry_strong_away_motivation,
                'balanced_home_top_case': retry_balanced_home_top_case,
                'top_lead': round(float(retry_top_lead), 4),
                'has_premier_away_support': retry_has_premier_away_support,
            }
            if should_retry_review_after_match_intelligence:
                review_retry_upset_signal = dict(preliminary_upset_potential) if isinstance(preliminary_upset_potential, dict) else {}
                if retry_balanced_home_top_case and not retry_strong_away_motivation:
                    review_retry_upset_signal['motivation_risk'] = {
                        'available': True,
                        'supports_upset': True,
                        'favored_side': 'away',
                        'pressure_side': 'home',
                        'score': 12.0,
                    }
                review_retry_prob, review_retry_diag = self.postprocess_service.apply_review_outcome_adjustment(
                    final_probabilities=final_prob,
                    league_code=league_code,
                    strength_diff=strength_diff,
                    asian_handicap=asian_handicap,
                    current_odds=current_odds,
                    review_learning=review_learning,
                    match_intelligence=match_intelligence,
                    upset_potential=review_retry_upset_signal,
                )
                if isinstance(review_retry_diag, dict):
                    review_retry_diag = dict(review_retry_diag)
                    review_retry_diag['retry_after_match_intelligence'] = True
                    realtime['context_applied']['review_outcome_adjustment_retry'] = review_retry_diag
                    if review_retry_diag.get('applied'):
                        final_prob = review_retry_prob
                        review_outcome_diag = review_retry_diag
                        realtime['context_applied']['review_outcome_adjustment'] = review_retry_diag
                        ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        over_under, over_under_diag = self.build_real_market_over_under(
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            current_odds=current_odds,
            analysis_context=analysis_context,
            match_intelligence=match_intelligence,
            realtime_context_applied=realtime['context_applied'],
        )
        realtime['context_applied']['over_under_guard'] = over_under_diag
        over_under, review_ou_diag = self.postprocess_service.apply_review_over_under_adjustment(
            over_under=over_under,
            league_code=league_code,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
            suppress_over_shift=suppress_review_over_push,
        )
        realtime['context_applied']['review_over_under_adjustment'] = review_ou_diag
        real_ou_stage_input = dict(final_prob)
        final_prob, real_ou_outcome_diag = self.apply_real_totals_outcome_adjustment(
            final_prob=final_prob,
            current_odds=current_odds,
            over_under=over_under,
        )
        real_ou_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'real_market_over_under_outcome_adjustment'}
        if isinstance(real_ou_outcome_diag, dict) and real_ou_outcome_diag.get('applied'):
            final_prob, real_ou_stage_cap_diag = self._apply_near_tie_stage_cap(
                before_prob=real_ou_stage_input,
                after_prob=final_prob,
                stage_name='real_market_over_under_outcome_adjustment',
            )
        realtime['context_applied']['real_market_over_under_outcome_adjustment'] = real_ou_outcome_diag
        realtime['context_applied']['real_market_over_under_stage_cap'] = real_ou_stage_cap_diag
        draw_guard_stage_input = dict(final_prob)
        final_prob, draw_guard_diag = self._apply_draw_confirmation_guard(
            final_prob=final_prob,
            current_odds=current_odds,
            over_under=over_under,
            match_intelligence=match_intelligence,
            review_outcome_diag=review_outcome_diag,
        )
        draw_guard_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'draw_confirmation_guard'}
        if isinstance(draw_guard_diag, dict) and draw_guard_diag.get('applied'):
            final_prob, draw_guard_stage_cap_diag = self._apply_near_tie_stage_cap(
                before_prob=draw_guard_stage_input,
                after_prob=final_prob,
                stage_name='draw_confirmation_guard',
            )
        realtime['context_applied']['draw_confirmation_guard'] = draw_guard_diag
        realtime['context_applied']['draw_confirmation_stage_cap'] = draw_guard_stage_cap_diag
        sentiment_adj_diag: Dict[str, Any] = {'applied': False}
        try:
            market_sentiment = self.detect_market_movement_sentiment(
                european_odds=european_odds,
                asian_handicap=asian_handicap,
            )
            realtime['context_applied']['market_movement_sentiment'] = market_sentiment
            sentiment_stage_input = dict(final_prob)
            final_prob, sentiment_adj_diag = self.apply_market_sentiment_adjustment(
                final_prob=final_prob,
                sentiment=market_sentiment,
            )
            sentiment_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'market_sentiment_adjustment'}
            if isinstance(sentiment_adj_diag, dict) and sentiment_adj_diag.get('applied'):
                final_prob, sentiment_stage_cap_diag = self._apply_near_tie_stage_cap(
                    before_prob=sentiment_stage_input,
                    after_prob=final_prob,
                    stage_name='market_sentiment_adjustment',
                )
            realtime['context_applied']['market_sentiment_adjustment'] = sentiment_adj_diag
            realtime['context_applied']['market_sentiment_stage_cap'] = sentiment_stage_cap_diag
        except Exception as exc:
            realtime['context_applied']['market_sentiment_adjustment'] = {'applied': False, 'error': str(exc)}
        operation_adj_diag: Dict[str, Any] = {'applied': False}
        try:
            ou_market_signal = self.postprocess_service.extract_over_under_market_signal(current_odds)
            operation_pattern = self.classify_market_operation_pattern(
                european_odds=european_odds,
                asian_handicap=asian_handicap,
                ou_signal=ou_market_signal,
                kelly=current_odds.get('凯利') if isinstance(current_odds, dict) else None,
                market_sentiment=realtime['context_applied'].get('market_movement_sentiment'),
            )
            realtime['context_applied']['market_operation_pattern'] = operation_pattern
            from domain.operation_weight_learner import extract_delta_vector
            operation_delta_vector = extract_delta_vector(
                european_odds,
                asian_handicap,
                current_odds.get('凯利') if isinstance(current_odds, dict) else None,
                self._parse_handicap_value,
            )
            realtime['context_applied']['market_operation_delta_vector'] = operation_delta_vector
            operation_stage_input = dict(final_prob)
            final_prob, operation_adj_diag = self.apply_market_operation_adjustment(
                final_prob=final_prob,
                pattern_diag=operation_pattern,
                delta_vector=operation_delta_vector,
            )
            operation_stage_cap_diag: Dict[str, Any] = {'applied': False, 'stage': 'market_operation_adjustment'}
            if isinstance(operation_adj_diag, dict) and operation_adj_diag.get('applied'):
                final_prob, operation_stage_cap_diag = self._apply_near_tie_stage_cap(
                    before_prob=operation_stage_input,
                    after_prob=final_prob,
                    stage_name='market_operation_adjustment',
                )
            realtime['context_applied']['market_operation_adjustment'] = operation_adj_diag
            realtime['context_applied']['market_operation_stage_cap'] = operation_stage_cap_diag
        except Exception as exc:
            realtime['context_applied']['market_operation_adjustment'] = {'applied': False, 'error': str(exc)}
        operation_adj_diag = realtime['context_applied'].get('market_operation_adjustment') if isinstance(realtime['context_applied'].get('market_operation_adjustment'), dict) else operation_adj_diag
        anchor_ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
        ranked_probabilities = list(anchor_ranked_probabilities)
        main_prediction = ranked_probabilities[0][0]
        confidence = ranked_probabilities[0][1]

        lightweight_rag_decision = {}
        upset_potential = self.upset_analyzer.assess_upset_potential(
            home_team=home_team,
            away_team=away_team,
            league_code=league_code,
            strength_diff=strength_diff,
            home_strength=home_strength,
            away_strength=away_strength,
            predicted_outcome=main_prediction,
            confidence=confidence,
            historical_odds_reference=historical_odds_reference,
            asian_handicap=asian_handicap,
            european_odds=european_odds,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['full_upset_signal'] = {
            'available': bool(isinstance(upset_potential, dict) and upset_potential),
            'level': upset_potential.get('level') if isinstance(upset_potential, dict) else None,
            'similar_cases_count': int(upset_potential.get('similar_cases_count') or 0) if isinstance(upset_potential, dict) else 0,
            'risk_score_detail': upset_potential.get('risk_score_detail') if isinstance(upset_potential, dict) else {},
            'case_knowledge': upset_potential.get('case_knowledge') if isinstance(upset_potential, dict) else {},
        }
        serie_a_upset_knowledge_retry_gate = self._build_serie_a_upset_knowledge_retry_gate(
            final_prob=final_prob,
            review_outcome_diag=review_outcome_diag,
            draw_guard_diag=draw_guard_diag,
            upset_potential=upset_potential,
            main_prediction=main_prediction,
            league_code=league_code,
        )
        realtime['context_applied']['serie_a_upset_knowledge_retry_gate'] = serie_a_upset_knowledge_retry_gate
        if serie_a_upset_knowledge_retry_gate.get('eligible'):
            review_retry_signal = {
                'available': True,
                'motivation_risk': review_outcome_diag.get('motivation_risk') if isinstance(review_outcome_diag, dict) and isinstance(review_outcome_diag.get('motivation_risk'), dict) else {},
                'handicap_strength_mismatch': review_outcome_diag.get('handicap_strength_mismatch') if isinstance(review_outcome_diag, dict) and isinstance(review_outcome_diag.get('handicap_strength_mismatch'), dict) else {},
                'risk_score_detail': upset_potential.get('risk_score_detail') if isinstance(upset_potential, dict) else {},
                'historical_odds_reference': upset_potential.get('historical_odds_reference') if isinstance(upset_potential, dict) else {},
                'case_knowledge': upset_potential.get('case_knowledge') if isinstance(upset_potential, dict) else {},
                'similar_cases_count': int(upset_potential.get('similar_cases_count') or 0) if isinstance(upset_potential, dict) else 0,
            }
            review_retry_prob, review_retry_diag = self.postprocess_service.apply_review_outcome_adjustment(
                final_probabilities=final_prob,
                league_code=league_code,
                strength_diff=strength_diff,
                asian_handicap=asian_handicap,
                current_odds=current_odds,
                review_learning=review_learning,
                match_intelligence=match_intelligence,
                upset_potential=review_retry_signal,
            )
            if isinstance(review_retry_diag, dict):
                review_retry_diag = dict(review_retry_diag)
                review_retry_diag['retry_from_full_upset_knowledge'] = True
                retry_shift = review_retry_diag.get('applied_shift') if isinstance(review_retry_diag.get('applied_shift'), dict) else {}
                retry_shift['draw_shift'] = max(
                    float(retry_shift.get('draw_shift') or 0.0),
                    float(serie_a_upset_knowledge_retry_gate.get('draw_shift') or 0.0),
                )
                retry_shift['away_shift'] = max(
                    float(retry_shift.get('away_shift') or 0.0),
                    float(serie_a_upset_knowledge_retry_gate.get('away_shift') or 0.0),
                )
                review_retry_diag['applied_shift'] = retry_shift
                retry_signals = [str(item).strip() for item in (review_retry_diag.get('signals') or []) if str(item).strip()]
                for signal_name in (
                    'review-league-serie-a-draw-to-away-relief',
                    'review-league-serie-a-soft-draw-away-entry',
                    'review-league-serie-a-upset-knowledge-retry',
                ):
                    if signal_name not in retry_signals:
                        retry_signals.append(signal_name)
                review_retry_diag['signals'] = retry_signals
                realtime['context_applied']['review_outcome_adjustment_full_upset_retry'] = review_retry_diag
                knowledge_draw_shift = float(serie_a_upset_knowledge_retry_gate.get('draw_shift') or 0.0)
                knowledge_away_shift = float(serie_a_upset_knowledge_retry_gate.get('away_shift') or 0.0)
                final_prob = self.postprocess_service._shift_from_side_to_targets(
                    review_retry_prob,
                    from_key='home_win',
                    draw_shift=knowledge_draw_shift,
                    away_shift=knowledge_away_shift,
                )
                final_prob, draw_guard_diag = self._apply_draw_confirmation_guard(
                    final_prob=final_prob,
                    current_odds=current_odds,
                    over_under=over_under,
                    match_intelligence=match_intelligence,
                    review_outcome_diag=review_retry_diag,
                )
                realtime['context_applied']['draw_confirmation_guard'] = draw_guard_diag
                review_outcome_diag = review_retry_diag
                realtime['context_applied']['review_outcome_adjustment'] = review_retry_diag
                ranked_probabilities = self.postprocess_service.rank_outcomes(final_prob)
                main_prediction = ranked_probabilities[0][0]
                confidence = ranked_probabilities[0][1]
        ranked_probabilities, draw_proximity_diag = self._apply_draw_proximity_promotion(
            ranked_probabilities=ranked_probabilities,
            european_odds=european_odds,
            home_advantage=home_advantage,
        )
        realtime['context_applied']['draw_proximity_promotion'] = draw_proximity_diag
        cumulative_stage_cap_diag: Dict[str, Any] = {'applied': False}
        current_prob_map = {
            'home_win': next((float(prob) for label, prob in ranked_probabilities if label == '主胜'), 0.0),
            'draw': next((float(prob) for label, prob in ranked_probabilities if label == '平局'), 0.0),
            'away_win': next((float(prob) for label, prob in ranked_probabilities if label == '客胜'), 0.0),
        }
        current_prob_map = self._normalize_prob_dict(current_prob_map)
        current_before_cumulative = dict(current_prob_map)
        current_prob_map, cumulative_stage_cap_diag = self._apply_cumulative_near_tie_cap(
            anchor_prob={
                'home_win': next((float(prob) for label, prob in anchor_ranked_probabilities if label == '主胜'), 0.0),
                'draw': next((float(prob) for label, prob in anchor_ranked_probabilities if label == '平局'), 0.0),
                'away_win': next((float(prob) for label, prob in anchor_ranked_probabilities if label == '客胜'), 0.0),
            },
            current_prob=current_prob_map,
        )
        if cumulative_stage_cap_diag.get('applied'):
            ranked_probabilities = self.postprocess_service.rank_outcomes(current_prob_map)
            final_prob = dict(current_prob_map)
        realtime['context_applied']['cumulative_outcome_stage_cap'] = cumulative_stage_cap_diag
        ranked_probabilities, outcome_stability_diag = self._apply_outcome_stability_guard(
            ranked_probabilities=ranked_probabilities,
            anchor_ranked_probabilities=anchor_ranked_probabilities,
            draw_guard_diag=draw_guard_diag,
            operation_adj_diag=operation_adj_diag,
            review_outcome_diag=review_outcome_diag,
            sentiment_adj_diag=sentiment_adj_diag,
        )
        realtime['context_applied']['outcome_stability_guard'] = outcome_stability_diag
        if draw_proximity_diag.get('applied') or cumulative_stage_cap_diag.get('applied') or outcome_stability_diag.get('applied'):
            main_prediction = ranked_probabilities[0][0]
            confidence = ranked_probabilities[0][1]
        confidence, confidence_diag = self._calibrate_confidence_with_league_learning(
            confidence=confidence,
            applied_weights=applied_weights,
        )
        realtime['context_applied']['league_confidence_adjustment'] = confidence_diag

        match_intelligence = self.match_intelligence_engine._finalize_match_intelligence(
            match_intelligence=match_intelligence,
            historical_odds_reference=historical_odds_reference,
            upset_potential=upset_potential,
        )

        dc_model = DixonColesModel(rho=resolve_rho(league_code))
        score_result = dc_model.predict_with_dixon_coles(home_lambda, away_lambda)
        score_probs = score_result.get('score_probs')
        if isinstance(sentiment_adj_diag, dict) and sentiment_adj_diag.get('applied'):
            score_probs = self.postprocess_service.rescale_score_probs_to_outcome(
                score_probs, final_prob
            )
            realtime['context_applied']['market_sentiment_score_rescale'] = {
                'applied': True,
                'reason': 'align_scores_to_sentiment_adjusted_1x2',
            }
        top_scores, score_guard_diag = self._rerank_scores_for_under_three(
            score_probs,
            over_under,
            limit=12,
        )
        realtime['context_applied']['score_rerank_guard'] = score_guard_diag
        top_scores, review_score_diag = self.postprocess_service.rerank_top_scores(
            top_scores,
            main_prediction,
            league_code=league_code,
            ranked_probabilities=ranked_probabilities,
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            over_under=over_under,
            strength_diff=strength_diff,
            confidence=confidence,
            current_odds=current_odds,
            review_learning=review_learning,
            match_intelligence=match_intelligence,
            upset_potential=upset_potential,
            return_diag=True,
            limit=3,
        )
        realtime['context_applied']['review_score_rerank'] = review_score_diag
        top_scores = self._mix_cross_direction_scores(
            top_scores,
            score_probs,
            limit=3,
        )
        realtime['context_applied']['score_cross_direction_mix'] = {
            'applied': True,
            'reason': 'anchor_predicted_direction_then_global_distribution',
        }
        total_goals = self.postprocess_service.compute_total_goals_distribution(score_result.get('score_probs', {}), max_bucket=7)
        total_goals, review_total_goals_diag = self.postprocess_service.apply_three_layer_total_goals_adjustment(
            total_goals,
            league_code=league_code,
            predicted_outcome_label=main_prediction,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
            total_lambda=home_lambda + away_lambda,
            over_under=over_under,
            match_intelligence=match_intelligence,
        )
        realtime['context_applied']['review_total_goals_adjustment'] = review_total_goals_diag

        realtime['context_applied']['tri_axis_consistency'] = self.compute_tri_axis_consistency(
            final_prob=final_prob,
            over_under=over_under,
            operation_pattern=realtime['context_applied'].get('market_operation_pattern'),
            european_odds=european_odds,
            asian_handicap=current_odds.get('亚值') if isinstance(current_odds, dict) else None,
            kelly=current_odds.get('凯利') if isinstance(current_odds, dict) else None,
            over_under_odds=current_odds.get('大小球') if isinstance(current_odds, dict) else None,
        )

        return {
            'applied_weights': applied_weights,
            'home_strength': home_strength,
            'away_strength': away_strength,
            'match_intelligence': match_intelligence,
            'strength_diff': strength_diff,
            'asian_handicap': asian_handicap,
            'european_odds': european_odds,
            'fusion_result': fusion_result,
            'final_probabilities': final_prob,
            'ranked_probabilities': ranked_probabilities,
            'main_prediction': main_prediction,
            'confidence': confidence,
            'historical_odds_reference': historical_odds_reference,
            'upset_potential': upset_potential,
            'top_scores': top_scores,
            'score_probs': score_probs,
            'total_goals': total_goals,
            'home_lambda': home_lambda,
            'lightweight_rag_decision': lightweight_rag_decision,
            'away_lambda': away_lambda,
            'over_under': over_under,
            'tri_axis_consistency': realtime['context_applied'].get('tri_axis_consistency'),
        }

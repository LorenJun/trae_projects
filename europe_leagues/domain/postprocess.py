"""模块说明：负责胜平负概率后处理、比分分布、大小球和凯利结果装配。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from runtime.debug_events import emit_local_debug_event


OUTCOME_LABELS = (
    ('主胜', 'home_win', 'home'),
    ('平局', 'draw', 'draw'),
    ('客胜', 'away_win', 'away'),
)


class PredictionPostprocessService:
    def __init__(self, league_config: Dict[str, Dict[str, Any]]):
        self.league_config = league_config

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _safe_float_or_none(value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _parse_score(score_text: str) -> Optional[Tuple[int, int]]:
        match = re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', str(score_text or ''))
        if not match:
            return None
        return int(match.group(1)), int(match.group(2))

    @classmethod
    def _score_matches_outcome(cls, score_text: str, outcome_label: str) -> bool:
        parsed = cls._parse_score(score_text)
        if not parsed:
            return False
        home_score, away_score = parsed
        if outcome_label == '主胜':
            return home_score > away_score
        if outcome_label == '客胜':
            return home_score < away_score
        if outcome_label == '平局':
            return home_score == away_score
        return True

    @classmethod
    def filter_top_scores_by_outcome(
        cls,
        top_scores: List[Tuple[str, float]],
        outcome_label: str,
        limit: int = 3,
    ) -> List[Tuple[str, float]]:
        if not isinstance(top_scores, list):
            return []
        normalized = str(outcome_label or '').strip()
        filtered: List[Tuple[str, float]] = []
        fallback: List[Tuple[str, float]] = []
        for item in top_scores:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            score_text = str(item[0]).strip()
            try:
                probability = float(item[1])
            except Exception:
                continue
            candidate = (score_text, probability)
            fallback.append(candidate)
            if cls._score_matches_outcome(score_text, normalized):
                filtered.append(candidate)
        return (filtered or fallback)[: max(1, int(limit or 3))]

    @classmethod
    def rerank_top_scores(
        cls,
        top_scores: List[Tuple[str, float]],
        outcome_label: str,
        *,
        home_lambda: float,
        away_lambda: float,
        over_under: Optional[Dict[str, Any]],
        strength_diff: float,
        confidence: float,
        review_learning: Optional[Dict[str, Any]] = None,
        current_odds: Optional[Dict[str, Any]] = None,
        return_diag: bool = False,
        limit: int = 3,
    ) -> Any:
        candidate_pool = list(top_scores[: max(5, int(limit or 3) + 2)])
        filtered = cls.filter_top_scores_by_outcome(candidate_pool, outcome_label, limit=max(limit, 5))
        if confidence < 0.43 and len(filtered) < max(2, int(limit or 3)):
            filtered = candidate_pool[: max(limit, 5)]
        diag: Dict[str, Any] = {"applied": False, "signals": [], "three_layer_context": {}}
        if not filtered:
            return ([], diag) if return_diag else []

        over_prob = float((over_under or {}).get('over') or 0.0)
        under_prob = float((over_under or {}).get('under') or 0.0)
        line = (over_under or {}).get('line')
        total_lambda = max(0.3, float(home_lambda) + float(away_lambda))
        review_score_bias = review_learning.get('score_bias') if isinstance(review_learning, dict) and isinstance(review_learning.get('score_bias'), dict) else {}
        three_layer_context = cls.build_three_layer_runtime_context(
            predicted_outcome_label=outcome_label,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        diag["three_layer_context"] = {
            "scenario_name": three_layer_context.get("scenario_name"),
            "history_available": three_layer_context.get("history_available"),
            "handicap_depth_bucket": three_layer_context.get("handicap_depth_bucket"),
            "strength_gap_bucket": three_layer_context.get("strength_gap_bucket"),
            "euro_support_bucket": three_layer_context.get("euro_support_bucket"),
        }
        high_scoring_bias = 0.0
        if isinstance(line, (int, float)):
            high_scoring_bias += max(-0.08, min(0.08, (total_lambda - float(line)) * 0.12))
            if float(line) >= 3.0 and total_lambda >= float(line) - 0.05:
                high_scoring_bias += 0.02
            if float(line) <= 2.5 and total_lambda <= float(line) - 0.15:
                high_scoring_bias -= 0.02
        high_scoring_bias += max(-0.08, min(0.08, (over_prob - under_prob) * 0.18))

        reranked: List[Tuple[Tuple[float, float, float], Tuple[str, float]]] = []
        for score_text, base_prob in filtered:
            parsed = cls._parse_score(score_text)
            if not parsed:
                reranked.append(((float(base_prob), 0.0, 0.0), (score_text, float(base_prob))))
                continue
            home_goals, away_goals = parsed
            total_goals = home_goals + away_goals
            margin = home_goals - away_goals
            score_bonus = 0.0
            if confidence < 0.43 and not cls._score_matches_outcome(score_text, outcome_label):
                score_bonus -= 0.012

            if outcome_label == '主胜':
                if total_goals >= 3:
                    score_bonus += max(0.0, high_scoring_bias) * 0.9
                if total_goals <= 1:
                    score_bonus -= max(0.0, high_scoring_bias) * 0.7
                if total_goals <= 2:
                    score_bonus += max(0.0, under_prob - over_prob) * 0.06
                if margin >= 2 and (confidence >= 0.42 or strength_diff >= 14):
                    score_bonus += 0.018
                if home_lambda >= 2.0 and home_goals >= 3:
                    score_bonus += 0.02
                if away_lambda <= 0.9 and away_goals == 0:
                    score_bonus += 0.012
                if away_lambda >= 1.0 and away_goals >= 1:
                    score_bonus += 0.01
                if isinstance(review_score_bias, dict) and review_score_bias.get('available'):
                    conservative_rate = float(review_score_bias.get('conservative_home_win_rate') or 0.0)
                    total_under_rate = float(review_score_bias.get('low_total_underestimate_rate') or 0.0)
                    home_ceiling_rate = float(review_score_bias.get('home_goal_ceiling_underestimate_rate') or 0.0)
                    low_total_penalty = float(review_score_bias.get('recommended_low_total_penalty') or 0.0)
                    if total_goals >= 3 and home_goals >= 2:
                        score_bonus += total_under_rate * 0.03 + home_ceiling_rate * 0.02
                    if total_goals <= 1 and home_lambda >= 1.45:
                        score_bonus -= low_total_penalty + conservative_rate * 0.01
                    if score_text in ('1-0', '0-0') and home_lambda >= 1.55 and confidence >= 0.36:
                        score_bonus -= low_total_penalty
                    if home_goals >= 3:
                        score_bonus += home_ceiling_rate * 0.018
            elif outcome_label == '客胜':
                if total_goals >= 3:
                    score_bonus += max(0.0, high_scoring_bias) * 0.9
                if total_goals <= 1:
                    score_bonus -= max(0.0, high_scoring_bias) * 0.7
                if total_goals <= 2:
                    score_bonus += max(0.0, under_prob - over_prob) * 0.06
                if abs(margin) >= 2 and (confidence >= 0.42 or strength_diff <= -14):
                    score_bonus += 0.018
                if away_lambda >= 2.0 and away_goals >= 3:
                    score_bonus += 0.02
                if home_lambda <= 0.9 and home_goals == 0:
                    score_bonus += 0.012
                if home_lambda >= 1.0 and home_goals >= 1:
                    score_bonus += 0.01
            else:
                if total_goals == 2:
                    score_bonus += 0.02
                if total_goals == 0 and over_prob >= 0.56:
                    score_bonus -= 0.03
                if total_goals >= 4 and under_prob >= 0.56:
                    score_bonus -= 0.02
                if 1.9 <= total_lambda <= 2.7 and score_text == '1-1':
                    score_bonus += 0.015
                if total_lambda < 2.0 and score_text == '0-0':
                    score_bonus += 0.012

            scenario_bonus, scenario_signal = cls._compute_three_layer_score_bonus(
                score_text=score_text,
                outcome_label=outcome_label,
                total_goals=total_goals,
                margin=margin,
                home_goals=home_goals,
                away_goals=away_goals,
                total_lambda=total_lambda,
                confidence=confidence,
                three_layer_context=three_layer_context,
            )
            score_bonus += scenario_bonus
            if scenario_signal and scenario_signal not in diag["signals"]:
                diag["signals"].append(scenario_signal)
                diag["applied"] = True

            reranked.append(
                (
                    (
                        round(float(base_prob + score_bonus), 8),
                        round(float(score_bonus), 8),
                        float(base_prob),
                    ),
                    (score_text, float(base_prob)),
                )
            )

        reranked.sort(key=lambda item: item[0], reverse=True)
        final_scores = [item[1] for item in reranked[: max(1, int(limit or 3))]]
        return (final_scores, diag) if return_diag else final_scores

    @classmethod
    def _compute_three_layer_score_bonus(
        cls,
        *,
        score_text: str,
        outcome_label: str,
        total_goals: int,
        margin: int,
        home_goals: int,
        away_goals: int,
        total_lambda: float,
        confidence: float,
        three_layer_context: Dict[str, Any],
    ) -> Tuple[float, str]:
        scenario_name = str(three_layer_context.get("scenario_name") or "")
        history_available = bool(three_layer_context.get("history_available"))
        euro_support_bucket = str(three_layer_context.get("euro_support_bucket") or "")
        if not scenario_name:
            return 0.0, ""

        bonus = 0.0
        if scenario_name == "strong_home_shallow_line" and outcome_label == "主胜":
            if away_goals >= 1 and margin == 1:
                bonus += 0.022 if history_available else 0.014
            if score_text in {"1-0", "2-1"}:
                bonus += 0.014 if history_available else 0.01
            if score_text == "3-0":
                bonus -= 0.024 if history_available else 0.016
            if home_goals >= 3 and away_goals == 0:
                bonus -= 0.02 if history_available else 0.012
            if total_goals >= 4 and confidence < 0.62:
                bonus -= 0.008
        elif scenario_name == "away_shallow_market_doubt" and outcome_label == "客胜":
            if score_text in {"0-1", "1-2"}:
                bonus += 0.014 if history_available else 0.008
            if away_goals >= 3 or abs(margin) >= 2:
                bonus -= 0.015 if history_available else 0.009
            if euro_support_bucket == "market_opposes" and home_goals >= 1:
                bonus += 0.006
        elif scenario_name in {"balanced_draw_guard", "draw_market_balance"}:
            if score_text == "1-1":
                bonus += 0.02 if history_available else 0.012
            if score_text == "0-0" and total_lambda <= 2.3:
                bonus += 0.016 if history_available else 0.01
            if score_text == "2-2" and total_lambda >= 2.7:
                bonus += 0.01 if history_available else 0.006
            if total_goals >= 4 and confidence < 0.58:
                bonus -= 0.01
        return bonus, f"score-three-layer-{scenario_name}"

    @staticmethod
    def build_market_snapshot(current_odds: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(current_odds, dict):
            return {}
        snapshot: Dict[str, Any] = {}
        for key in ('欧赔', '亚值', '大小球', '凯利'):
            value = current_odds.get(key)
            if isinstance(value, dict):
                snapshot[key] = value
        return snapshot

    @staticmethod
    def normalize_probs(p: Dict[str, float]) -> Dict[str, float]:
        h = float(p.get('home_win') or 0.0)
        d = float(p.get('draw') or 0.0)
        a = float(p.get('away_win') or 0.0)
        total = h + d + a
        if total <= 0:
            return {'home_win': 0.0, 'draw': 0.0, 'away_win': 0.0}
        return {'home_win': h / total, 'draw': d / total, 'away_win': a / total}

    @staticmethod
    def rank_outcomes(final_probabilities: Dict[str, float]) -> List[Tuple[str, float]]:
        ranked = [
            ('主胜', float(final_probabilities.get('home_win') or 0.0)),
            ('平局', float(final_probabilities.get('draw') or 0.0)),
            ('客胜', float(final_probabilities.get('away_win') or 0.0)),
        ]
        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked

    @classmethod
    def _extract_asian_line_from_current_odds(cls, current_odds: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(current_odds, dict):
            return None
        asian_block = current_odds.get("亚值")
        if not isinstance(asian_block, dict):
            return None
        return cls._extract_asian_handicap_line(asian_block)

    @classmethod
    def build_three_layer_runtime_context(
        cls,
        *,
        predicted_outcome_label: str,
        strength_diff: float,
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        outcome_to_key = {"主胜": "home", "平局": "draw", "客胜": "away"}
        predicted_winner_key = outcome_to_key.get(str(predicted_outcome_label or "").strip(), "draw")
        review = review_learning if isinstance(review_learning, dict) else {}
        stratified_review = review.get("outcome_stratified_review") if isinstance(review.get("outcome_stratified_review"), dict) else {}
        three_layer_review = review.get("three_layer_outcome_review") if isinstance(review.get("three_layer_outcome_review"), dict) else {}
        current_asian_line = cls._extract_asian_line_from_current_odds(current_odds)
        handicap_depth_bucket = cls._classify_handicap_depth_bucket(current_asian_line)
        parsed_strength_diff = cls._safe_float_or_none(strength_diff)
        strength_gap_bucket = cls._classify_strength_gap_bucket(parsed_strength_diff)
        structure_mismatch = cls._classify_structure_mismatch(
            predicted_winner=predicted_winner_key,
            strength_diff=parsed_strength_diff,
            asian_line=current_asian_line,
        )
        euro_odds = cls.extract_decimal_odds_1x2(current_odds)
        euro_support_bucket = cls._classify_euro_support_bucket(
            predicted_winner=predicted_winner_key,
            euro_home=euro_odds.get("home"),
            euro_draw=euro_odds.get("draw"),
            euro_away=euro_odds.get("away"),
        )
        stratified_bucket_key = f"{predicted_winner_key}:{handicap_depth_bucket}"
        three_layer_bucket_key = f"{predicted_winner_key}:{handicap_depth_bucket}:{euro_support_bucket}"
        stratified_bucket = stratified_review.get(stratified_bucket_key) if isinstance(stratified_review.get(stratified_bucket_key), dict) else {}
        three_layer_bucket = three_layer_review.get(three_layer_bucket_key) if isinstance(three_layer_review.get(three_layer_bucket_key), dict) else {}
        stratified_sample_count = int(stratified_bucket.get("sample_count") or 0)
        three_layer_sample_count = int(three_layer_bucket.get("sample_count") or 0)
        base_draw_signal = max(
            float(stratified_bucket.get("recommended_draw_shift") or 0.0),
            float(three_layer_bucket.get("recommended_draw_shift") or 0.0),
        )
        base_upset_signal = max(
            float(stratified_bucket.get("recommended_upset_shift") or 0.0),
            float(three_layer_bucket.get("recommended_upset_shift") or 0.0),
        )
        history_available = stratified_sample_count >= 3 or three_layer_sample_count >= 2
        scenario_name = ""
        if predicted_winner_key == "home" and structure_mismatch == "strong_home_shallow_line":
            scenario_name = "strong_home_shallow_line"
        elif predicted_winner_key == "away" and handicap_depth_bucket in {"level_ball", "level_shallow"} and euro_support_bucket in {"draw_guarded", "market_opposes", "soft_support"}:
            scenario_name = "away_shallow_market_doubt"
        elif (
            predicted_winner_key in {"home", "away"}
            and parsed_strength_diff is not None
            and handicap_depth_bucket in {"level_ball", "level_shallow"}
            and strength_gap_bucket == "balanced"
        ):
            scenario_name = "balanced_draw_guard"
        elif predicted_winner_key == "draw" and (
            handicap_depth_bucket in {"level_ball", "level_shallow"} or euro_support_bucket in {"draw_supported", "draw_live"}
        ):
            scenario_name = "draw_market_balance"
        return {
            "predicted_winner_key": predicted_winner_key,
            "handicap_depth_bucket": handicap_depth_bucket,
            "strength_gap_bucket": strength_gap_bucket,
            "structure_mismatch": structure_mismatch,
            "euro_support_bucket": euro_support_bucket,
            "stratified_bucket": stratified_bucket,
            "three_layer_bucket": three_layer_bucket,
            "history_available": history_available,
            "base_draw_signal": base_draw_signal,
            "base_upset_signal": base_upset_signal,
            "scenario_name": scenario_name,
            "asian_line": current_asian_line,
            "odds_source": euro_odds.get("source"),
        }

    @classmethod
    def apply_three_layer_total_goals_adjustment(
        cls,
        total_goals: Dict[str, Any],
        *,
        predicted_outcome_label: str,
        strength_diff: float,
        current_odds: Optional[Dict[str, Any]],
        review_learning: Optional[Dict[str, Any]] = None,
        total_lambda: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        diag: Dict[str, Any] = {"applied": False, "signals": [], "three_layer_context": {}}
        if not isinstance(total_goals, dict) or not total_goals.get("available"):
            diag["reason"] = "missing_total_goals"
            return total_goals, diag
        buckets = total_goals.get("buckets")
        if not isinstance(buckets, dict):
            diag["reason"] = "missing_buckets"
            return total_goals, diag
        context = cls.build_three_layer_runtime_context(
            predicted_outcome_label=predicted_outcome_label,
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=review_learning,
        )
        diag["three_layer_context"] = {
            "scenario_name": context.get("scenario_name"),
            "history_available": context.get("history_available"),
            "handicap_depth_bucket": context.get("handicap_depth_bucket"),
            "euro_support_bucket": context.get("euro_support_bucket"),
        }
        scenario_name = str(context.get("scenario_name") or "")
        if not scenario_name:
            diag["reason"] = "no_three_layer_scenario"
            return total_goals, diag

        adjusted = {str(key): float(value or 0.0) for key, value in buckets.items()}
        history_available = bool(context.get("history_available"))
        total_lambda_value = float(total_lambda or 0.0)
        take_from = ["4", "5", "6", "7"]
        give_to = ["1", "2"]
        shift = 0.0
        if scenario_name == "strong_home_shallow_line":
            shift = 0.026 if history_available else 0.016
            if total_lambda_value >= 2.9:
                take_from = ["5", "6", "7"]
                give_to = ["2", "3"]
        elif scenario_name == "away_shallow_market_doubt":
            shift = 0.024 if history_available else 0.014
        elif scenario_name in {"balanced_draw_guard", "draw_market_balance"}:
            shift = 0.03 if history_available else 0.018
            take_from = ["3", "4", "5", "6", "7"]
            give_to = ["0", "1", "2"] if total_lambda_value <= 2.35 else ["1", "2", "3"]

        movable = {key: max(0.0, adjusted.get(key, 0.0) - 0.02) for key in take_from}
        total_movable = sum(movable.values())
        moved = min(shift, total_movable)
        if total_movable > 0 and moved > 0:
            for key in take_from:
                portion = movable.get(key, 0.0) / total_movable if total_movable > 0 else 0.0
                take = moved * portion
                adjusted[key] = max(0.0, adjusted.get(key, 0.0) - take)
        if moved <= 0:
            diag["reason"] = "insufficient_bucket_mass"
            return total_goals, diag
        share = moved / max(1, len(give_to))
        for key in give_to:
            adjusted[key] = adjusted.get(key, 0.0) + share
        norm = sum(max(0.0, value) for value in adjusted.values())
        if norm <= 0:
            diag["reason"] = "invalid_total_bucket_norm"
            return total_goals, diag
        adjusted = {key: max(0.0, value) / norm for key, value in adjusted.items()}
        top = sorted(adjusted.items(), key=lambda item: item[1], reverse=True)[:3]
        out = dict(total_goals)
        out["buckets"] = {key: round(float(value), 6) for key, value in adjusted.items()}
        out["top_totals"] = [{"total": key, "prob": round(float(value), 4)} for key, value in top]
        out["three_layer_adjustment"] = {
            "applied": True,
            "scenario_name": scenario_name,
            "history_available": history_available,
            "shift": round(moved, 4),
        }
        diag["applied"] = True
        diag["signals"].append(f"total-goals-three-layer-{scenario_name}")
        diag["delta_top_totals"] = out["top_totals"]
        return out, diag

    def apply_review_outcome_adjustment(
        self,
        *,
        final_probabilities: Dict[str, float],
        strength_diff: float,
        asian_handicap: Optional[Dict[str, Any]] = None,
        current_odds: Optional[Dict[str, Any]] = None,
        review_learning: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {
            "applied": False,
            "signals": [],
            "delta": {},
            "league_review": {},
            "stratified_review": {},
            "three_layer_review": {},
            "three_layer_evaluated": True,
        }
        if not isinstance(final_probabilities, dict):
            diag["reason"] = "missing_final_probabilities"
            return final_probabilities, diag

        probabilities = self.normalize_probs(final_probabilities)
        p_h = float(probabilities.get("home_win") or 0.0)
        p_d = float(probabilities.get("draw") or 0.0)
        p_a = float(probabilities.get("away_win") or 0.0)

        review = review_learning if isinstance(review_learning, dict) else {}
        league_review = review.get("league_review") if isinstance(review.get("league_review"), dict) else {}
        stratified_review = review.get("outcome_stratified_review") if isinstance(review.get("outcome_stratified_review"), dict) else {}
        three_layer_review = review.get("three_layer_outcome_review") if isinstance(review.get("three_layer_outcome_review"), dict) else {}
        tags = [str(tag) for tag in (league_review.get("league_tags") or []) if str(tag).strip()]
        coverage_rate = float(league_review.get("prediction_coverage_rate") or 0.0)
        unpredicted_completed_count = int(league_review.get("unpredicted_completed_count") or 0)
        diag["league_review"] = {
            "league_tags": tags,
            "prediction_coverage_rate": coverage_rate,
            "unpredicted_completed_count": unpredicted_completed_count,
        }

        ranked_before = self.rank_outcomes(probabilities)
        top_outcome = ranked_before[0][0] if ranked_before else "平局"
        outcome_to_key = {"主胜": "home", "平局": "draw", "客胜": "away"}
        predicted_winner_key = outcome_to_key.get(top_outcome, "draw")
        current_asian_line = self._extract_asian_handicap_line(asian_handicap)
        handicap_depth_bucket = self._classify_handicap_depth_bucket(current_asian_line)
        strength_gap_bucket = self._classify_strength_gap_bucket(strength_diff)
        structure_mismatch = self._classify_structure_mismatch(
            predicted_winner=predicted_winner_key,
            strength_diff=strength_diff,
            asian_line=current_asian_line,
        )
        euro_odds = self.extract_decimal_odds_1x2(current_odds)
        euro_support_bucket = self._classify_euro_support_bucket(
            predicted_winner=predicted_winner_key,
            euro_home=euro_odds.get("home"),
            euro_draw=euro_odds.get("draw"),
            euro_away=euro_odds.get("away"),
        )
        stratified_bucket_key = f"{predicted_winner_key}:{handicap_depth_bucket}"
        stratified_bucket = stratified_review.get(stratified_bucket_key) if isinstance(stratified_review.get(stratified_bucket_key), dict) else {}
        three_layer_bucket_key = f"{predicted_winner_key}:{handicap_depth_bucket}:{euro_support_bucket}"
        three_layer_bucket = three_layer_review.get(three_layer_bucket_key) if isinstance(three_layer_review.get(three_layer_bucket_key), dict) else {}
        diag["stratified_review"] = {
            "bucket_key": stratified_bucket_key,
            "handicap_depth_bucket": handicap_depth_bucket,
            "strength_gap_bucket": strength_gap_bucket,
            "asian_line": current_asian_line,
            "structure_mismatch": structure_mismatch,
            "matched_bucket": stratified_bucket,
        }
        diag["three_layer_review"] = {
            "bucket_key": three_layer_bucket_key,
            "euro_support_bucket": euro_support_bucket,
            "odds_source": euro_odds.get("source"),
            "odds": {key: euro_odds.get(key) for key in ("home", "draw", "away")},
            "matched_bucket": three_layer_bucket,
            "history_available": bool(stratified_review or three_layer_review),
        }
        max_take_floor = 0.05

        def _take_from(key: str, amount: float) -> float:
            nonlocal p_h, p_d, p_a
            amount = max(0.0, float(amount))
            if key == "home_win":
                taken = min(amount, max(0.0, p_h - max_take_floor))
                p_h -= taken
                return taken
            if key == "draw":
                taken = min(amount, max(0.0, p_d - max_take_floor))
                p_d -= taken
                return taken
            taken = min(amount, max(0.0, p_a - max_take_floor))
            p_a -= taken
            return taken

        joined_tags = " ".join(tags)
        if "主胜偏置" in joined_tags and top_outcome == "主胜":
            shift = 0.008
            if p_h >= 0.48:
                shift += 0.008
            if abs(float(strength_diff or 0.0)) <= 16:
                shift += 0.004
            taken = _take_from("home_win", min(0.024, shift))
            if taken > 0:
                diag["signals"].append("review-home-bias-correction")
                diag["applied"] = True
                draw_share = 0.6 if "平局防守不足" in joined_tags else 0.5
                p_d += taken * draw_share
                p_a += taken * (1.0 - draw_share)

        if "平局防守不足" in joined_tags and abs(float(strength_diff or 0.0)) <= 16 and p_d <= 0.32:
            draw_boost = min(0.022, 0.008 + max(0.0, (0.32 - p_d)) * 0.06)
            preferred_from = "home_win" if p_h >= p_a else "away_win"
            taken = _take_from(preferred_from, draw_boost)
            if taken < draw_boost:
                fallback_from = "away_win" if preferred_from == "home_win" else "home_win"
                taken += _take_from(fallback_from, draw_boost - taken)
            if taken > 0:
                diag["signals"].append("review-draw-gap-correction")
                diag["applied"] = True
                p_d += taken

        if "客胜冷门敏感度不足" in joined_tags and top_outcome == "主胜" and float(strength_diff or 0.0) <= 18:
            away_boost = 0.006
            if p_h >= 0.46:
                away_boost += 0.006
            if p_a <= 0.24:
                away_boost += 0.004
            taken = _take_from("home_win", min(0.018, away_boost))
            if taken > 0:
                diag["signals"].append("review-away-upset-correction")
                diag["applied"] = True
                p_a += taken

        if coverage_rate and (coverage_rate < 0.6 or unpredicted_completed_count >= 3):
            coverage_penalty = min(
                0.018,
                max(0.0, (0.6 - coverage_rate)) * 0.04 + min(0.008, unpredicted_completed_count * 0.002),
            )
            if coverage_penalty > 0:
                top_key = "home_win" if p_h >= max(p_d, p_a) else ("away_win" if p_a >= max(p_h, p_d) else "draw")
                taken = _take_from(top_key, coverage_penalty)
                if taken > 0:
                    diag["signals"].append("review-coverage-confidence-shrink")
                    diag["applied"] = True
                    remainder = taken / 2.0
                    if top_key == "home_win":
                        p_d += remainder
                        p_a += taken - remainder
                    elif top_key == "away_win":
                        p_h += remainder
                        p_d += taken - remainder
                    else:
                        p_h += remainder
                        p_a += taken - remainder

        stratified_sample_count = int(stratified_bucket.get("sample_count") or 0)
        stratified_miss_rate = float(stratified_bucket.get("miss_rate") or 0.0)
        stratified_draw_shift = float(stratified_bucket.get("recommended_draw_shift") or 0.0)
        stratified_upset_shift = float(stratified_bucket.get("recommended_upset_shift") or 0.0)
        if predicted_winner_key in {"home", "away"} and stratified_sample_count >= 3 and stratified_miss_rate >= 0.34:
            depth_bonus = 0.004 if handicap_depth_bucket in {"level_ball", "level_shallow"} else 0.0
            strength_bonus = 0.0
            if strength_gap_bucket == "balanced":
                strength_bonus = 0.004
            elif strength_gap_bucket == "edge":
                strength_bonus = 0.002
            elif structure_mismatch:
                strength_bonus = 0.004
            draw_boost = min(0.024, stratified_draw_shift + depth_bonus + strength_bonus)
            upset_boost = min(0.018, stratified_upset_shift + (0.004 if structure_mismatch else 0.0))
            total_shift = min(0.03, draw_boost + upset_boost)
            if total_shift > 0:
                top_key = "home_win" if predicted_winner_key == "home" else "away_win"
                taken = _take_from(top_key, total_shift)
                if taken > 0:
                    diag["signals"].append("review-stratified-handicap-strength-correction")
                    diag["applied"] = True
                    draw_share = draw_boost / total_shift if total_shift > 0 else 0.5
                    upset_share = 1.0 - draw_share
                    if predicted_winner_key == "home":
                        p_d += taken * draw_share
                        p_a += taken * upset_share
                    else:
                        p_d += taken * draw_share
                        p_h += taken * upset_share

        three_layer_sample_count = int(three_layer_bucket.get("sample_count") or 0)
        three_layer_draw_shift = float(three_layer_bucket.get("recommended_draw_shift") or 0.0)
        three_layer_upset_shift = float(three_layer_bucket.get("recommended_upset_shift") or 0.0)
        history_available = stratified_sample_count >= 3 or three_layer_sample_count >= 2
        base_draw_signal = max(stratified_draw_shift, three_layer_draw_shift)
        base_upset_signal = max(stratified_upset_shift, three_layer_upset_shift)
        if predicted_winner_key in {"home", "away"}:
            scenario_name = ""
            scenario_draw_shift = 0.0
            scenario_upset_shift = 0.0
            if predicted_winner_key == "home" and structure_mismatch == "strong_home_shallow_line":
                scenario_name = "strong_home_shallow_line"
                if history_available:
                    scenario_draw_shift = max(base_draw_signal, 0.01)
                    scenario_upset_shift = max(base_upset_signal, 0.008)
                else:
                    scenario_draw_shift = 0.006
                    scenario_upset_shift = 0.004
                    if euro_support_bucket in {"draw_guarded", "soft_support"}:
                        scenario_draw_shift += 0.003
                    if euro_support_bucket == "market_opposes":
                        scenario_upset_shift += 0.004
            elif predicted_winner_key == "away" and handicap_depth_bucket in {"level_ball", "level_shallow"} and euro_support_bucket in {"draw_guarded", "market_opposes", "soft_support"}:
                scenario_name = "away_shallow_market_doubt"
                if history_available:
                    scenario_draw_shift = max(base_draw_signal, 0.012)
                    scenario_upset_shift = max(base_upset_signal, 0.006 if euro_support_bucket == "draw_guarded" else 0.01)
                else:
                    scenario_draw_shift = 0.008 if euro_support_bucket == "draw_guarded" else 0.006
                    scenario_upset_shift = 0.004 if euro_support_bucket == "draw_guarded" else 0.007
            elif handicap_depth_bucket in {"level_ball", "level_shallow"} and strength_gap_bucket == "balanced":
                scenario_name = "balanced_draw_guard"
                if history_available and base_draw_signal > 0:
                    scenario_draw_shift = max(base_draw_signal, 0.012)
                else:
                    scenario_draw_shift = 0.006
                    if euro_support_bucket in {"draw_guarded", "draw_live", "soft_support"}:
                        scenario_draw_shift += 0.003

            total_shift = min(0.032, scenario_draw_shift + scenario_upset_shift)
            if scenario_name and total_shift > 0:
                top_key = "home_win" if predicted_winner_key == "home" else "away_win"
                taken = _take_from(top_key, total_shift)
                if taken > 0:
                    diag["signals"].append(f"review-scenario-{scenario_name}")
                    diag["signals"].append(
                        "review-three-layer-historical"
                        if history_available
                        else "review-three-layer-heuristic"
                    )
                    diag["applied"] = True
                    if scenario_upset_shift <= 0:
                        p_d += taken
                    else:
                        draw_share = scenario_draw_shift / total_shift if total_shift > 0 else 0.5
                        other_share = 1.0 - draw_share
                        if predicted_winner_key == "home":
                            p_d += taken * draw_share
                            p_a += taken * other_share
                        else:
                            p_d += taken * draw_share
                            p_h += taken * other_share

        if not diag["applied"] and predicted_winner_key == "draw":
            draw_guard_shift = 0.0
            if handicap_depth_bucket in {"level_ball", "level_shallow"}:
                draw_guard_shift += 0.004
            if euro_support_bucket in {"draw_supported", "draw_live"}:
                draw_guard_shift += 0.004
            if history_available:
                draw_guard_shift = max(draw_guard_shift, min(0.012, base_draw_signal))
            if draw_guard_shift > 0 and p_d < 0.42:
                preferred_from = "home_win" if p_h >= p_a else "away_win"
                taken = _take_from(preferred_from, min(0.014, draw_guard_shift))
                if taken < draw_guard_shift:
                    fallback_from = "away_win" if preferred_from == "home_win" else "home_win"
                    taken += _take_from(fallback_from, min(0.014, draw_guard_shift) - taken)
                if taken > 0:
                    diag["signals"].append("review-scenario-draw-market-balance")
                    diag["signals"].append(
                        "review-three-layer-historical"
                        if history_available
                        else "review-three-layer-heuristic"
                    )
                    diag["applied"] = True
                    p_d += taken

        total = p_h + p_d + p_a
        if total <= 0:
            diag["reason"] = "invalid_adjusted_total"
            return final_probabilities, diag
        adjusted = {
            "home_win": p_h / total,
            "draw": p_d / total,
            "away_win": p_a / total,
        }
        if not diag["applied"]:
            diag["reason"] = "three_layer_evaluated_no_adjustment"
            return final_probabilities, diag

        diag["delta"] = {
            "home_win": round(adjusted["home_win"] - float(probabilities.get("home_win") or 0.0), 6),
            "draw": round(adjusted["draw"] - float(probabilities.get("draw") or 0.0), 6),
            "away_win": round(adjusted["away_win"] - float(probabilities.get("away_win") or 0.0), 6),
        }
        diag["before"] = {key: round(float(value), 6) for key, value in probabilities.items()}
        diag["after"] = {key: round(float(value), 6) for key, value in adjusted.items()}
        return adjusted, diag

    @staticmethod
    def _extract_asian_handicap_line(asian_handicap: Optional[Dict[str, Any]]) -> Optional[float]:
        if not isinstance(asian_handicap, dict):
            return None
        for block_name in ("final", "initial"):
            block = asian_handicap.get(block_name)
            if not isinstance(block, dict):
                continue
            for key in ("handicap_value", "handicap"):
                try:
                    value = block.get(key)
                    if value not in (None, ""):
                        return float(value)
                except Exception:
                    continue
        return None

    @staticmethod
    def _classify_handicap_depth_bucket(asian_line: Optional[float]) -> str:
        if asian_line is None:
            return "unknown"
        depth = abs(float(asian_line))
        if depth < 0.125:
            return "level_ball"
        if depth <= 0.25:
            return "level_shallow"
        if depth <= 0.75:
            return "level_medium"
        if depth <= 1.25:
            return "level_deep"
        return "level_very_deep"

    @staticmethod
    def _classify_strength_gap_bucket(strength_diff: float) -> str:
        parsed = PredictionPostprocessService._safe_float_or_none(strength_diff)
        if parsed is None:
            return "unknown"
        gap = abs(parsed)
        if gap <= 8:
            return "balanced"
        if gap <= 16:
            return "edge"
        if gap <= 24:
            return "clear"
        return "huge"

    @staticmethod
    def _classify_structure_mismatch(predicted_winner: str, strength_diff: float, asian_line: Optional[float]) -> str:
        depth = abs(PredictionPostprocessService._safe_float(asian_line, 0.0))
        gap = PredictionPostprocessService._safe_float_or_none(strength_diff)
        if gap is None:
            return ""
        if predicted_winner == "home":
            if gap >= 18 and depth <= 0.5:
                return "strong_home_shallow_line"
            if gap <= 8 and depth >= 1.0:
                return "balanced_match_deep_home_line"
        if predicted_winner == "away":
            if gap <= -18 and depth <= 0.5:
                return "strong_away_shallow_line"
            if gap >= -8 and depth >= 1.0:
                return "balanced_match_deep_away_line"
        return ""

    @staticmethod
    def _classify_euro_support_bucket(
        *,
        predicted_winner: str,
        euro_home: Optional[float],
        euro_draw: Optional[float],
        euro_away: Optional[float],
    ) -> str:
        try:
            if not euro_home or not euro_draw or not euro_away:
                return "unknown"
            raw_home = 1.0 / float(euro_home)
            raw_draw = 1.0 / float(euro_draw)
            raw_away = 1.0 / float(euro_away)
            total = raw_home + raw_draw + raw_away
            if total <= 0:
                return "unknown"
            ph = raw_home / total
            pd = raw_draw / total
            pa = raw_away / total
        except Exception:
            return "unknown"

        if predicted_winner == "draw":
            if pd >= max(ph, pa) + 0.02:
                return "draw_supported"
            if pd >= max(ph, pa) - 0.01:
                return "draw_live"
            return "draw_soft"

        pred_prob = ph if predicted_winner == "home" else pa
        opp_prob = pa if predicted_winner == "home" else ph
        if opp_prob >= pred_prob + 0.03:
            return "market_opposes"
        if pd >= pred_prob - 0.01:
            return "draw_guarded"
        if pred_prob >= max(pd, opp_prob) + 0.08:
            return "strong_support"
        if pred_prob >= max(pd, opp_prob) + 0.03:
            return "support"
        return "soft_support"

    @staticmethod
    def compute_total_goals_distribution(score_probs: Dict[str, float], max_bucket: int = 7) -> Dict[str, Any]:
        if not isinstance(score_probs, dict) or not score_probs:
            return {'available': False, 'reason': 'no_score_probs'}
        buckets = {str(i): 0.0 for i in range(max_bucket + 1)}
        buckets[f'{max_bucket}+'] = 0.0
        for score, prob in score_probs.items():
            if not isinstance(prob, (int, float)):
                continue
            match = re.match(r'^(\d+)\s*-\s*(\d+)$', str(score).strip())
            if not match:
                continue
            total_goals = int(match.group(1)) + int(match.group(2))
            if total_goals >= max_bucket:
                buckets[f'{max_bucket}+'] += float(prob)
            else:
                buckets[str(total_goals)] += float(prob)

        total_prob = sum(buckets.values())
        if total_prob > 0:
            for key in list(buckets.keys()):
                buckets[key] = buckets[key] / total_prob

        top = sorted(((key, buckets[key]) for key in buckets.keys()), key=lambda item: item[1], reverse=True)[:3]
        return {
            'available': True,
            'buckets': {key: round(float(value), 6) for key, value in buckets.items()},
            'top_totals': [{'total': key, 'prob': round(float(value), 4)} for key, value in top],
            'tail_bucket': f'{max_bucket}+',
        }

    @staticmethod
    def extract_decimal_odds_1x2(current_odds: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        if not isinstance(current_odds, dict):
            return {'home': None, 'draw': None, 'away': None, 'source': 'none'}

        def pick_final(block_name: str) -> Optional[Dict[str, Any]]:
            block = current_odds.get(block_name)
            if isinstance(block, dict):
                final = block.get('final')
                if isinstance(final, dict):
                    return final
            return None

        def parse(value: Any) -> Optional[float]:
            try:
                if value is None:
                    return None
                numeric = float(str(value).strip())
                return numeric if numeric > 1.01 else None
            except Exception:
                return None

        final = pick_final('胜平负赔率')
        if final:
            return {
                'home': parse(final.get('home') if 'home' in final else final.get('主')),
                'draw': parse(final.get('draw') if 'draw' in final else final.get('平')),
                'away': parse(final.get('away') if 'away' in final else final.get('客')),
                'source': '胜平负赔率.final',
            }

        final = pick_final('欧赔')
        if final:
            return {
                'home': parse(final.get('home') if 'home' in final else final.get('主')),
                'draw': parse(final.get('draw') if 'draw' in final else final.get('平')),
                'away': parse(final.get('away') if 'away' in final else final.get('客')),
                'source': '欧赔.final',
            }

        return {'home': None, 'draw': None, 'away': None, 'source': 'none'}

    @staticmethod
    def kelly_fraction(probability: float, odds: float) -> Optional[float]:
        try:
            probability = float(probability)
            odds = float(odds)
            if odds <= 1.01:
                return None
            b = odds - 1.0
            q = 1.0 - probability
            fraction = (b * probability - q) / b
            return max(0.0, min(1.0, float(fraction)))
        except Exception:
            return None

    def build_kelly_staking(
        self,
        final_probabilities: Dict[str, float],
        current_odds: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str] = None,
        cap_half: float = 0.05,
        cap_quarter: float = 0.03,
    ) -> Dict[str, Any]:
        probabilities = self.normalize_probs(final_probabilities or {})
        odds = self.extract_decimal_odds_1x2(current_odds)
        result: Dict[str, Any] = {
            'available': False,
            'odds_source': odds.get('source'),
            'odds': {'home': odds.get('home'), 'draw': odds.get('draw'), 'away': odds.get('away')},
            'probabilities': probabilities,
            'by_outcome': {},
            'recommended': {},
        }
        if not (odds.get('home') and odds.get('draw') and odds.get('away')):
            result['reason'] = 'missing_odds'
            return result

        by_outcome: Dict[str, Dict[str, Optional[float]]] = {}
        for label, prob_key, odds_key in OUTCOME_LABELS:
            full = self.kelly_fraction(probabilities.get(prob_key, 0.0), odds.get(odds_key) or 0.0)
            if full is None:
                by_outcome[label] = {'full': None, 'half_cap5': None, 'quarter_cap3': None}
                continue
            half = min(float(cap_half), 0.5 * full)
            quarter = min(float(cap_quarter), 0.25 * full)
            by_outcome[label] = {
                'full': round(float(full), 6),
                'half_cap5': round(float(half), 6),
                'quarter_cap3': round(float(quarter), 6),
            }

        result['by_outcome'] = by_outcome
        result['available'] = True
        if predicted_outcome and predicted_outcome in by_outcome:
            slot = by_outcome.get(predicted_outcome) or {}
            candidates = []
            if isinstance(slot.get('half_cap5'), (int, float)):
                candidates.append(('half_cap5', float(slot['half_cap5'])))
            if isinstance(slot.get('quarter_cap3'), (int, float)):
                candidates.append(('quarter_cap3', float(slot['quarter_cap3'])))
            if candidates:
                method, fraction = sorted(candidates, key=lambda item: item[1])[0]
                result['recommended'] = {
                    'outcome': predicted_outcome,
                    'method': method,
                    'fraction': round(float(fraction), 6),
                }
        return result

    @staticmethod
    def attach_over_under_context(
        over_under: Dict[str, Any],
        current_odds: Optional[Dict[str, Any]],
        learning_diag: Optional[Dict[str, Any]],
        ou_line_source: str,
        realtime_context_applied: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not isinstance(over_under, dict):
            return over_under
        over_under['line_source'] = ou_line_source
        if isinstance(learning_diag, dict) and learning_diag.get('applied'):
            over_under['league_learning'] = {
                'sample_size': learning_diag.get('sample_size'),
                'signals': learning_diag.get('signals', []),
                'recent_avg_goals': learning_diag.get('recent_avg_goals'),
                'over25_rate': learning_diag.get('over25_rate'),
                'over35_rate': learning_diag.get('over35_rate'),
                'btts_rate': learning_diag.get('btts_rate'),
                'clean_sheet_rate': learning_diag.get('clean_sheet_rate'),
            }
        try:
            market_ou = None
            if isinstance(current_odds, dict):
                block = current_odds.get('大小球')
                if isinstance(block, dict):
                    final = block.get('final') if isinstance(block.get('final'), dict) else {}
                    initial = block.get('initial') if isinstance(block.get('initial'), dict) else {}
                    market_ou = {'final': final or {}, 'initial': initial or {}}
                    if isinstance(block.get('consensus'), dict):
                        market_ou['consensus'] = block.get('consensus') or {}
                    if isinstance(block.get('companies'), list):
                        market_ou['companies'] = block.get('companies') or []
                    if block.get('company_mode'):
                        market_ou['company_mode'] = block.get('company_mode')
            if market_ou:
                over_under['market'] = market_ou
                if isinstance(realtime_context_applied, dict):
                    realtime_context_applied['ou_market'] = market_ou
        except Exception:
            pass
        return over_under

    @staticmethod
    def build_retrieved_memory_explanation(retrieved_memory: Optional[Dict[str, Any]]) -> str:
        memory = retrieved_memory if isinstance(retrieved_memory, dict) else {}
        summary = memory.get('summary') if isinstance(memory.get('summary'), dict) else {}
        similar_cases = memory.get('similar_cases') if isinstance(memory.get('similar_cases'), list) else []
        market_cases = memory.get('market_cases') if isinstance(memory.get('market_cases'), list) else []
        upset_cases = memory.get('upset_cases') if isinstance(memory.get('upset_cases'), list) else []
        if not similar_cases and not market_cases and not upset_cases:
            return 'RAG记忆暂未召回足够高质量样本，当前结论主要依赖模型推理、实时盘口与球队上下文。'

        def normalize_risk_label(text: str) -> str:
            risk = str(text or '').strip()
            if not risk:
                return ''
            if risk.startswith('控球倾向 '):
                return '控球倾向差异'
            if risk.startswith('资金流向代理偏'):
                return '资金流向偏移'
            if risk.startswith('三盘口整体偏'):
                return '三盘口共振'
            if risk.startswith('三盘口画像:'):
                return '三盘口画像共振'
            if '平局凯利偏低' in risk:
                return '平局凯利偏低'
            if '诱盘风险' in risk:
                return '诱盘风险'
            if '历史同向反打' in risk:
                return '历史同向反打'
            if '历史超级冷门' in risk:
                return '历史超级冷门'
            if '伤病严重' in risk:
                return '伤病风险'
            if '深让>=' in risk or risk.startswith('深让'):
                return '深盘风险'
            return risk

        def summarize_upset_risks(items: List[Dict[str, Any]]) -> List[str]:
            counts: Dict[str, int] = {}
            order: List[str] = []
            for item in items:
                for raw in item.get('risk_points') or []:
                    label = normalize_risk_label(str(raw or ''))
                    if not label:
                        continue
                    counts[label] = counts.get(label, 0) + 1
                    if label not in order:
                        order.append(label)
            return sorted(order, key=lambda key: (-counts.get(key, 0), order.index(key)))[:2]

        fragments: List[str] = []
        completed_count = summary.get('completed_similar_case_count')
        if similar_cases:
            fragment = f"RAG召回{len(similar_cases)}场相似比赛"
            if isinstance(completed_count, int) and completed_count > 0:
                rates = []
                for label, key in (('主胜', 'home_win_rate'), ('平局', 'draw_rate'), ('客胜', 'away_win_rate')):
                    value = summary.get(key)
                    if isinstance(value, (int, float)):
                        rates.append(f"{label}{float(value):.0%}")
                if rates:
                    fragment += f"，其中已完赛{completed_count}场，结果分布为{'/'.join(rates)}"
            fragments.append(fragment)
        if market_cases:
            avg_total = summary.get('avg_market_total_goals')
            fragment = f"盘口相似案例{len(market_cases)}场"
            if isinstance(avg_total, (int, float)):
                fragment += f"，历史平均总进球约{float(avg_total):.2f}"
            top_market = market_cases[0]
            if top_market.get('actual_score'):
                fragment += f"，最相近盘口样本赛果为{top_market.get('actual_result') or ''} {top_market.get('actual_score')}".strip()
            fragments.append(fragment)
        if upset_cases:
            fragment = f"爆冷风险参考{len(upset_cases)}场"
            risk_bits = summarize_upset_risks(upset_cases)
            if risk_bits:
                fragment += f"，高频风险包括{'、'.join(risk_bits[:2])}"
            fragments.append(fragment)
        return '；'.join(fragments) + '。'

    def build_prediction_result(
        self,
        *,
        match_id: str,
        home_team: str,
        away_team: str,
        league_code: str,
        match_date: str,
        match_time: str,
        ranked_probabilities: List[Tuple[str, float]],
        confidence: float,
        top_scores: List[Tuple[str, float]],
        total_goals: Dict[str, Any],
        home_lambda: float,
        away_lambda: float,
        over_under: Dict[str, Any],
        staking: Dict[str, Any],
        strength_diff: float,
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
        upset_potential: Dict[str, Any],
        match_intelligence: Dict[str, Any],
        historical_odds_reference: Dict[str, Any],
        fusion_result: Dict[str, Any],
        final_probabilities: Dict[str, float],
        applied_model_weights: Dict[str, Any],
        realtime: Dict[str, Any],
        analysis_context: Dict[str, Any],
        runtime_profile: Dict[str, Any],
        retrieved_memory: Optional[Dict[str, Any]] = None,
        current_odds: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        league_name = self.league_config[league_code]['name']
        memory_explanation = self.build_retrieved_memory_explanation(retrieved_memory)
        final_top_scores, score_three_layer_diag = self.rerank_top_scores(
            top_scores,
            ranked_probabilities[0][0] if ranked_probabilities else '平局',
            home_lambda=home_lambda,
            away_lambda=away_lambda,
            over_under=over_under,
            strength_diff=strength_diff,
            confidence=confidence,
            review_learning=analysis_context.get('review_learning') if isinstance(analysis_context, dict) else None,
            current_odds=current_odds,
            return_diag=True,
            limit=3,
        )
        adjusted_total_goals, total_goals_three_layer_diag = self.apply_three_layer_total_goals_adjustment(
            total_goals,
            predicted_outcome_label=ranked_probabilities[0][0] if ranked_probabilities else '平局',
            strength_diff=strength_diff,
            current_odds=current_odds,
            review_learning=analysis_context.get('review_learning') if isinstance(analysis_context, dict) else None,
            total_lambda=home_lambda + away_lambda,
        )
        if isinstance(realtime, dict):
            context_applied = realtime.setdefault('context_applied', {})
            if isinstance(context_applied, dict):
                context_applied['score_three_layer_adjustment'] = score_three_layer_diag
                context_applied['total_goals_three_layer_adjustment'] = total_goals_three_layer_diag
        # #region debug-point A:score-rerank
        emit_local_debug_event({"sessionId":"uniform-prediction-bias","runId":"pre-fix","hypothesisId":"A","location":"domain/postprocess.py:508","msg":"[DEBUG] score rerank output","data":{"league_code":league_code,"home_team":home_team,"away_team":away_team,"ranked_probabilities":ranked_probabilities,"raw_top_scores":top_scores[:5] if isinstance(top_scores, list) else top_scores,"final_top_scores":final_top_scores,"over_under":over_under,"confidence":confidence,"review_learning_score_bias":((analysis_context or {}).get("review_learning") or {}).get("score_bias") if isinstance(analysis_context, dict) else None,"score_three_layer_diag":score_three_layer_diag,"total_goals_three_layer_diag":total_goals_three_layer_diag}})
        # #endregion
        return {
            'match_id': str(match_id or ''),
            'home_team': home_team,
            'away_team': away_team,
            'league_code': league_code,
            'league_name': league_name,
            'match_date': match_date,
            'match_time': match_time,
            'prediction': ranked_probabilities[0][0] if ranked_probabilities else '平局',
            'confidence': confidence,
            'all_probabilities': dict(ranked_probabilities),
            'top_scores': final_top_scores,
            'total_goals': adjusted_total_goals,
            'expected_goals': {
                'home': home_lambda,
                'away': away_lambda,
                'total': home_lambda + away_lambda,
            },
            'over_under': over_under,
            'score_three_layer_adjustment': score_three_layer_diag,
            'total_goals_three_layer_adjustment': total_goals_three_layer_diag,
            'staking': staking,
            'league_over_under_learning': realtime.get('context_applied', {}).get('league_over_under_learning'),
            'strength_diff': strength_diff,
            'home_strength': home_strength,
            'away_strength': away_strength,
            'upset_potential': upset_potential,
            'match_intelligence': match_intelligence,
            'historical_odds_reference': historical_odds_reference,
            'model_predictions': fusion_result['all_models'],
            'final_probabilities': final_probabilities,
            'applied_model_weights': applied_model_weights,
            'realtime': realtime,
            'analysis_context': analysis_context,
            'retrieved_memory': retrieved_memory or {},
            'retrieved_memory_explanation': memory_explanation,
            'market_snapshot': self.build_market_snapshot(current_odds),
            'runtime_profile': runtime_profile,
            'timestamp': datetime.now().isoformat(),
        }

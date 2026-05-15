"""模块说明：提供基于现有归档、记忆样本与赛果标签的混合检索 RAG 服务。"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

from runtime.rag_store import (
    build_hybrid_rag_index,
    load_rag_index,
    retrieve_hybrid_context,
    retrieve_structured_cases,
)


class HybridRAGService:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _normalize_top_scores(top_scores: Optional[List[Any]]) -> List[str]:
        results: List[str] = []
        for item in top_scores or []:
            if isinstance(item, (list, tuple)) and item:
                results.append(str(item[0]))
            elif item:
                results.append(str(item))
        return results

    @staticmethod
    def _infer_current_ou_direction(
        current_over_under: Optional[Dict[str, Any]],
        market_snapshot: Optional[Dict[str, Any]],
    ) -> str:
        over_under = current_over_under if isinstance(current_over_under, dict) else {}
        over_prob = HybridRAGService._safe_float(over_under.get("over"))
        under_prob = HybridRAGService._safe_float(over_under.get("under"))
        if over_prob is not None and under_prob is not None and abs(over_prob - under_prob) > 1e-9:
            return "大球" if over_prob > under_prob else "小球"

        snapshot = market_snapshot if isinstance(market_snapshot, dict) else {}
        totals = snapshot.get("大小球") if isinstance(snapshot.get("大小球"), dict) else {}
        final = totals.get("final") if isinstance(totals.get("final"), dict) else {}
        initial = totals.get("initial") if isinstance(totals.get("initial"), dict) else {}
        over_water = HybridRAGService._safe_float(final.get("over"))
        under_water = HybridRAGService._safe_float(final.get("under"))
        if over_water is None or under_water is None:
            over_water = HybridRAGService._safe_float(initial.get("over"))
            under_water = HybridRAGService._safe_float(initial.get("under"))
        if over_water is None or under_water is None or abs(over_water - under_water) <= 1e-9:
            return ""
        return "大球" if over_water < under_water else "小球"

    @staticmethod
    def _infer_case_actual_ou_direction(case: Dict[str, Any]) -> str:
        actual_score = str(case.get("actual_score") or "").strip()
        match = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", actual_score)
        if not match:
            return str(case.get("predicted_ou_direction") or "").strip()
        total_goals = int(match.group(1)) + int(match.group(2))
        ou_line = HybridRAGService._safe_float(case.get("ou_line"))
        if ou_line is None:
            return str(case.get("predicted_ou_direction") or "").strip()
        if total_goals > ou_line:
            return "大球"
        if total_goals < ou_line:
            return "小球"
        return "走水"

    @staticmethod
    def _extract_1x2_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        source = snapshot if isinstance(snapshot, dict) else {}
        for key in ("胜平负赔率", "欧赔"):
            block = source.get(key)
            if not isinstance(block, dict):
                continue
            final = block.get("final") if isinstance(block.get("final"), dict) else {}
            initial = block.get("initial") if isinstance(block.get("initial"), dict) else {}
            selected = final or initial
            if not selected:
                continue
            return {
                "home": HybridRAGService._safe_float(selected.get("home")),
                "draw": HybridRAGService._safe_float(selected.get("draw")),
                "away": HybridRAGService._safe_float(selected.get("away")),
            }
        return {"home": None, "draw": None, "away": None}

    @staticmethod
    def _extract_totals_profile(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        source = snapshot if isinstance(snapshot, dict) else {}
        totals = source.get("大小球") if isinstance(source.get("大小球"), dict) else {}
        initial = totals.get("initial") if isinstance(totals.get("initial"), dict) else {}
        final = totals.get("final") if isinstance(totals.get("final"), dict) else {}
        initial_line = HybridRAGService._safe_float(initial.get("line"))
        final_line = HybridRAGService._safe_float(final.get("line"))
        initial_over = HybridRAGService._safe_float(initial.get("over"))
        final_over = HybridRAGService._safe_float(final.get("over"))
        initial_under = HybridRAGService._safe_float(initial.get("under"))
        final_under = HybridRAGService._safe_float(final.get("under"))
        line_direction = "flat"
        if initial_line is not None and final_line is not None:
            if final_line - initial_line >= 0.24:
                line_direction = "up"
            elif initial_line - final_line >= 0.24:
                line_direction = "down"
        favored_direction = ""
        if final_over is not None and final_under is not None and abs(final_over - final_under) > 1e-9:
            favored_direction = "大球" if final_over < final_under else "小球"
        return {
            "available": bool(initial or final),
            "initial_line": initial_line,
            "final_line": final_line,
            "line_delta": round((final_line - initial_line), 4) if initial_line is not None and final_line is not None else None,
            "line_direction": line_direction,
            "initial_over": initial_over,
            "final_over": final_over,
            "over_delta": round((final_over - initial_over), 4) if initial_over is not None and final_over is not None else None,
            "initial_under": initial_under,
            "final_under": final_under,
            "under_delta": round((final_under - initial_under), 4) if initial_under is not None and final_under is not None else None,
            "favored_direction": favored_direction,
        }

    @staticmethod
    def _compare_1x2_closeness(current_1x2: Dict[str, Optional[float]], historical_1x2: Dict[str, Optional[float]]) -> Dict[str, Any]:
        diffs: Dict[str, float] = {}
        for key in ("home", "draw", "away"):
            current_value = HybridRAGService._safe_float(current_1x2.get(key))
            historical_value = HybridRAGService._safe_float(historical_1x2.get(key))
            if current_value is None or historical_value is None:
                continue
            diffs[key] = abs(current_value - historical_value)
        if len(diffs) < 3:
            return {"close": False, "avg_diff": None, "max_diff": None}
        avg_diff = sum(diffs.values()) / len(diffs)
        max_diff = max(diffs.values()) if diffs else None
        return {
            "close": avg_diff <= 0.35 and (max_diff or 0.0) <= 0.55,
            "avg_diff": round(avg_diff, 4),
            "max_diff": round(max_diff or 0.0, 4),
            "diffs": {key: round(val, 4) for key, val in diffs.items()},
        }

    @staticmethod
    def _compare_totals_change(current_totals: Dict[str, Any], historical_totals: Dict[str, Any]) -> Dict[str, Any]:
        if not current_totals.get("available") or not historical_totals.get("available"):
            return {"consistent": False, "reason": "missing_totals"}
        favored_current = str(current_totals.get("favored_direction") or "")
        favored_historical = str(historical_totals.get("favored_direction") or "")
        line_dir_current = str(current_totals.get("line_direction") or "flat")
        line_dir_historical = str(historical_totals.get("line_direction") or "flat")
        same_favored = bool(favored_current and favored_current == favored_historical)
        same_line_direction = line_dir_current == line_dir_historical or "flat" in (line_dir_current, line_dir_historical)
        current_line_delta = HybridRAGService._safe_float(current_totals.get("line_delta"))
        historical_line_delta = HybridRAGService._safe_float(historical_totals.get("line_delta"))
        current_over_delta = HybridRAGService._safe_float(current_totals.get("over_delta"))
        historical_over_delta = HybridRAGService._safe_float(historical_totals.get("over_delta"))
        current_under_delta = HybridRAGService._safe_float(current_totals.get("under_delta"))
        historical_under_delta = HybridRAGService._safe_float(historical_totals.get("under_delta"))
        line_gap = (
            abs(current_line_delta - historical_line_delta)
            if current_line_delta is not None and historical_line_delta is not None
            else None
        )
        over_gap = (
            abs(current_over_delta - historical_over_delta)
            if current_over_delta is not None and historical_over_delta is not None
            else None
        )
        under_gap = (
            abs(current_under_delta - historical_under_delta)
            if current_under_delta is not None and historical_under_delta is not None
            else None
        )
        water_gaps = [gap for gap in (over_gap, under_gap) if gap is not None]
        avg_water_gap = (sum(water_gaps) / len(water_gaps)) if water_gaps else None
        line_close = line_gap is not None and line_gap <= 0.30
        water_close = avg_water_gap is not None and avg_water_gap <= 0.22
        favored_close = same_favored or not favored_current or not favored_historical or (line_close and water_close)
        movement_close = (
            (line_close and water_close)
            or (same_line_direction and water_close)
            or (same_favored and line_close)
        )
        consistent = bool(favored_close and movement_close)
        return {
            "consistent": consistent,
            "same_favored_direction": same_favored,
            "same_line_direction": same_line_direction,
            "line_close": line_close,
            "water_close": water_close,
            "current_favored_direction": favored_current,
            "historical_favored_direction": favored_historical,
            "current_line_direction": line_dir_current,
            "historical_line_direction": line_dir_historical,
            "line_gap": round(line_gap, 4) if line_gap is not None else None,
            "avg_water_gap": round(avg_water_gap, 4) if avg_water_gap is not None else None,
        }

    @staticmethod
    def _build_live_market_followup(
        *,
        market_snapshot: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]],
        predicted_outcome: Optional[str],
        current_ou_direction: str,
    ) -> Dict[str, Any]:
        odds_ref = historical_odds_reference if isinstance(historical_odds_reference, dict) else {}
        similar_matches = odds_ref.get("similar_matches") if isinstance(odds_ref.get("similar_matches"), list) else []
        current_1x2 = HybridRAGService._extract_1x2_snapshot(market_snapshot)
        current_totals = HybridRAGService._extract_totals_profile(market_snapshot)
        eligible: List[Dict[str, Any]] = []
        for item in similar_matches:
            if not isinstance(item, dict):
                continue
            historical_snapshot = {
                "胜平负赔率": item.get("胜平负赔率"),
                "欧赔": item.get("欧赔"),
                "大小球": item.get("大小球"),
            }
            closeness = HybridRAGService._compare_1x2_closeness(
                current_1x2,
                HybridRAGService._extract_1x2_snapshot(historical_snapshot),
            )
            totals_consistency = HybridRAGService._compare_totals_change(
                current_totals,
                HybridRAGService._extract_totals_profile(historical_snapshot),
            )
            if not closeness.get("close") or not totals_consistency.get("consistent"):
                continue
            path_score = min(
                1.0,
                0.55
                + max(0.0, 0.35 - float(closeness.get("avg_diff") or 0.35)) * 0.7
                + (0.10 if totals_consistency.get("same_favored_direction") else 0.0)
                + (0.05 if totals_consistency.get("same_line_direction") else 0.0),
            )
            eligible.append(
                {
                    "match_id": str(item.get("match_id") or ""),
                    "match_date": item.get("match_date"),
                    "home_team": item.get("home_team"),
                    "away_team": item.get("away_team"),
                    "actual_result": item.get("actual_result"),
                    "actual_score": item.get("actual_score"),
                    "similarity": float(item.get("similarity") or 0.0),
                    "path_score": round(path_score, 4),
                    "weight_bonus": round(min(0.08, 0.02 + path_score * 0.04), 4),
                    "one_x_two_closeness": closeness,
                    "totals_consistency": totals_consistency,
                }
            )
        eligible.sort(key=lambda item: (float(item.get("path_score") or 0.0), float(item.get("similarity") or 0.0)), reverse=True)
        if not eligible:
            return {
                "applied": False,
                "reason": "no_eligible_live_market_matches",
                "eligible_match_ids": [],
                "comparisons": [],
                "advice": "",
                "recommended_action": "observe",
            }
        dominant_result = Counter(str(item.get("actual_result") or "").strip() for item in eligible if str(item.get("actual_result") or "").strip()).most_common(1)
        dominant_outcome = dominant_result[0][0] if dominant_result else ""
        support_count = sum(1 for item in eligible if predicted_outcome and str(item.get("actual_result") or "").strip() == predicted_outcome)
        support_rate = support_count / len(eligible) if eligible else 0.0
        avg_bonus = sum(float(item.get("weight_bonus") or 0.0) for item in eligible) / len(eligible)
        avg_path_score = sum(float(item.get("path_score") or 0.0) for item in eligible) / len(eligible)
        recommended_action = "observe"
        advice = "临场建议: 历史盘口轨迹样本已命中，但支持方向仍不够集中，建议继续观察实时盘口变化。"
        if predicted_outcome and support_rate >= 0.6:
            recommended_action = "follow"
            ou_suffix = f"，大小球可优先关注{current_ou_direction}方向" if current_ou_direction else ""
            advice = f"临场建议: 胜平负赔率接近且大小球变化同向的历史样本命中{len(eligible)}场，历史更支持{predicted_outcome}，可顺当前{predicted_outcome}方向轻中仓跟进{ou_suffix}。"
        elif predicted_outcome and support_rate <= 0.34 and dominant_outcome and dominant_outcome != predicted_outcome:
            recommended_action = "hedge"
            advice = f"临场建议: 虽然盘口轨迹样本已命中{len(eligible)}场，但历史实际赛果更偏{dominant_outcome}，建议降低{predicted_outcome}仓位并防范反向结果。"
        return {
            "applied": True,
            "eligible_count": len(eligible),
            "eligible_match_ids": [str(item.get("match_id") or "") for item in eligible if str(item.get("match_id") or "").strip()],
            "avg_path_score": round(avg_path_score, 4),
            "avg_weight_bonus": round(avg_bonus, 4),
            "supported_outcome_rate": round(support_rate, 4),
            "dominant_historical_outcome": dominant_outcome,
            "current_ou_direction": current_ou_direction,
            "recommended_action": recommended_action,
            "advice": advice,
            "comparisons": eligible[:3],
        }

    @staticmethod
    def _sort_cases_by_direction(
        cases: List[Dict[str, Any]],
        *,
        predicted_outcome: Optional[str],
        current_ou_direction: str,
        current_scores: List[str],
    ) -> List[Dict[str, Any]]:
        def priority(item: Dict[str, Any]) -> tuple:
            actual_result = str(item.get("actual_result") or "").strip()
            actual_ou_direction = HybridRAGService._infer_case_actual_ou_direction(item)
            predicted_ou_direction = str(item.get("predicted_ou_direction") or "").strip()
            predicted_scores = [str(score) for score in (item.get("predicted_scores") or []) if str(score).strip()]
            actual_score = str(item.get("actual_score") or "").strip()
            outcome_match = 1 if predicted_outcome and actual_result == predicted_outcome else 0
            ou_match = 1 if current_ou_direction and (
                actual_ou_direction == current_ou_direction or predicted_ou_direction == current_ou_direction
            ) else 0
            score_overlap = len(set(current_scores) & set(predicted_scores))
            actual_score_match = 1 if actual_score and actual_score in current_scores else 0
            return (
                outcome_match,
                ou_match,
                score_overlap,
                actual_score_match,
                float(item.get("similarity_score") or 0.0),
                float(item.get("market_bonus") or 0.0),
            )

        return sorted(cases or [], key=priority, reverse=True)

    @staticmethod
    def build_lightweight_decision(
        *,
        summary: Dict[str, Any],
        similar_cases: List[Dict[str, Any]],
        market_cases: List[Dict[str, Any]],
        upset_cases: List[Dict[str, Any]],
        predicted_outcome: Optional[str],
    ) -> Dict[str, Any]:
        decision = {
            'available': False,
            'risk_bonus': 0,
            'confidence_penalty': 0.0,
            'scenario_tags': [],
        }
        tags: List[str] = []
        risk_bonus = 0
        confidence_penalty = 0.0
        completed_count = int(summary.get('completed_similar_case_count') or 0)
        if completed_count > 0 and predicted_outcome:
            outcome_key = {
                '主胜': 'home_win_rate',
                '平局': 'draw_rate',
                '客胜': 'away_win_rate',
            }.get(str(predicted_outcome or '').strip(), '')
            hit_rate = float(summary.get(outcome_key) or 0.0) if outcome_key else 0.0
            if hit_rate <= 0.4:
                tags.append('similar_cases_low_hit_rate')
                risk_bonus += 4
                confidence_penalty += 0.02
        if isinstance(upset_cases, list) and upset_cases:
            tags.append('upset_case_cluster')
            risk_bonus += min(4, 2 + len(upset_cases))
            confidence_penalty += 0.015
        if predicted_outcome and isinstance(market_cases, list):
            opposing = [item for item in market_cases if str(item.get('actual_result') or '').strip() and str(item.get('actual_result') or '').strip() != str(predicted_outcome or '').strip()]
            if opposing:
                tags.append('market_case_opposes_pick')
                risk_bonus += 3
                confidence_penalty += 0.012
        avg_market_total_goals = summary.get('avg_market_total_goals')
        if isinstance(avg_market_total_goals, (int, float)) and float(avg_market_total_goals) >= 3.2:
            tags.append('high_total_market_cluster')
            risk_bonus += 2
        decision.update(
            {
                'available': bool(tags),
                'risk_bonus': risk_bonus,
                'confidence_penalty': round(confidence_penalty, 4),
                'scenario_tags': tags,
            }
        )
        return decision

    @staticmethod
    def _build_directional_summary(
        *,
        summary: Dict[str, Any],
        similar_cases: List[Dict[str, Any]],
        market_cases: List[Dict[str, Any]],
        predicted_outcome: Optional[str],
        current_ou_direction: str,
        current_scores: List[str],
    ) -> Dict[str, Any]:
        combined = list(similar_cases or []) + list(market_cases or [])
        direction_cases = [
            item for item in combined
            if predicted_outcome and str(item.get("actual_result") or "").strip() == predicted_outcome
        ]
        ou_cases = [
            item for item in combined
            if current_ou_direction and (
                HybridRAGService._infer_case_actual_ou_direction(item) == current_ou_direction
                or str(item.get("predicted_ou_direction") or "").strip() == current_ou_direction
            )
        ]
        directional_ou_cases = [
            item for item in direction_cases
            if current_ou_direction and (
                HybridRAGService._infer_case_actual_ou_direction(item) == current_ou_direction
                or str(item.get("predicted_ou_direction") or "").strip() == current_ou_direction
            )
        ]
        score_counter: Counter[str] = Counter()
        score_source = directional_ou_cases or direction_cases or ou_cases
        for item in score_source:
            actual_score = str(item.get("actual_score") or "").strip()
            if actual_score:
                score_counter[actual_score] += 2
            for score in item.get("predicted_scores") or []:
                text = str(score or "").strip()
                if text:
                    score_counter[text] += 1
        preferred_scores = [score for score, _ in score_counter.most_common(3)]
        current_score_overlap = [score for score in preferred_scores if score in current_scores]
        return {
            **summary,
            "direction_priority": {
                "predicted_outcome": predicted_outcome or "",
                "matched_case_count": len(direction_cases),
            },
            "ou_priority": {
                "current_ou_direction": current_ou_direction,
                "matched_case_count": len(ou_cases),
            },
            "direction_ou_priority": {
                "matched_case_count": len(directional_ou_cases),
                "preferred_scores": preferred_scores,
                "current_score_overlap": current_score_overlap,
            },
        }

    def refresh(self, limit: int = 200) -> Dict[str, Any]:
        return build_hybrid_rag_index(self.base_dir, limit=limit)

    def diagnose(self, limit: int = 200) -> Dict[str, Any]:
        index = load_rag_index(self.base_dir, limit=limit)
        return {
            "available": True,
            "mode": index.get("rag_mode"),
            "document_count": index.get("document_count"),
            "avgdl": index.get("avgdl"),
            "case_type_counts": index.get("case_type_counts", {}),
        }

    @staticmethod
    def _merge_market_cases(
        existing: List[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged = list(existing or [])
        seen = {
            (
                str(item.get("match_id") or ""),
                str(item.get("match_date") or ""),
                str(item.get("home_team") or ""),
                str(item.get("away_team") or ""),
            )
            for item in merged
            if isinstance(item, dict)
        }
        odds_ref = historical_odds_reference if isinstance(historical_odds_reference, dict) else {}
        followup = odds_ref.get("live_market_followup") if isinstance(odds_ref.get("live_market_followup"), dict) else {}
        bonus_map = {
            str(item.get("match_id") or ""): float(item.get("weight_bonus") or 0.0)
            for item in (followup.get("comparisons") or [])
            if isinstance(item, dict) and str(item.get("match_id") or "").strip()
        }
        for similar in odds_ref.get("similar_matches") or []:
            if not isinstance(similar, dict):
                continue
            key = (
                str(similar.get("match_id") or ""),
                str(similar.get("match_date") or ""),
                str(similar.get("home_team") or ""),
                str(similar.get("away_team") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(
                {
                    "match_id": similar.get("match_id"),
                    "league_code": None,
                    "league_name": None,
                    "competition_stage_name": None,
                    "match_date": similar.get("match_date"),
                    "home_team": similar.get("home_team"),
                    "away_team": similar.get("away_team"),
                    "prediction": None,
                    "confidence": None,
                    "actual_score": similar.get("actual_score"),
                    "actual_result": similar.get("actual_result"),
                    "storage_mode": similar.get("source", "historical_odds_reference"),
                    "risk_points": [],
                    "predicted_ou_direction": None,
                    "ou_line": None,
                    "case_type": "market_case",
                    "similarity_score": round(float(similar.get("similarity") or 0.0) + bonus_map.get(str(similar.get("match_id") or ""), 0.0), 4),
                    "bm25_score": 0.0,
                    "market_bonus": round(float(similar.get("similarity") or 0.0) + bonus_map.get(str(similar.get("match_id") or ""), 0.0), 4),
                    "structured_bonus": 0.0,
                    "weight_bonus": round(bonus_map.get(str(similar.get("match_id") or ""), 0.0), 4),
                    "text": f"{similar.get('home_team')} vs {similar.get('away_team')} | 赛果:{similar.get('actual_result')} {similar.get('actual_score')}",
                }
            )
        merged.sort(key=lambda item: float(item.get("similarity_score") or 0.0), reverse=True)
        return merged[:5]

    def retrieve_match_memory(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        market_snapshot: Optional[Dict[str, Any]],
        match_id: str = "",
        analysis_context: Optional[Dict[str, Any]] = None,
        historical_odds_reference: Optional[Dict[str, Any]] = None,
        predicted_outcome: Optional[str] = None,
        current_over_under: Optional[Dict[str, Any]] = None,
        top_scores: Optional[List[Any]] = None,
        top_k: int = 5,
    ) -> Dict[str, Any]:
        result = retrieve_hybrid_context(
            self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            market_snapshot=market_snapshot,
            match_id=match_id,
            analysis_context=analysis_context,
            top_k=top_k,
        )
        summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
        similar_cases = result.get("similar_cases") if isinstance(result.get("similar_cases"), list) else []
        current_ou_direction = self._infer_current_ou_direction(current_over_under, market_snapshot)
        current_scores = self._normalize_top_scores(top_scores)
        historical_odds_reference = historical_odds_reference if isinstance(historical_odds_reference, dict) else {}
        live_market_followup = self._build_live_market_followup(
            market_snapshot=market_snapshot,
            historical_odds_reference=historical_odds_reference,
            predicted_outcome=predicted_outcome,
            current_ou_direction=current_ou_direction,
        )
        historical_odds_reference = {
            **historical_odds_reference,
            "live_market_followup": live_market_followup,
        }
        market_cases = result.get("market_cases") if isinstance(result.get("market_cases"), list) else []
        market_cases = self._merge_market_cases(market_cases, historical_odds_reference)
        similar_cases = self._sort_cases_by_direction(
            similar_cases,
            predicted_outcome=predicted_outcome,
            current_ou_direction=current_ou_direction,
            current_scores=current_scores,
        )
        market_cases = self._sort_cases_by_direction(
            market_cases,
            predicted_outcome=predicted_outcome,
            current_ou_direction=current_ou_direction,
            current_scores=current_scores,
        )
        upset_cases = result.get("upset_cases") if isinstance(result.get("upset_cases"), list) else []
        summary = {
            **summary,
            "retrieved_count": len(similar_cases) + len(market_cases) + len(upset_cases),
            "market_case_count": len(market_cases),
            "live_market_followup": live_market_followup,
        }
        summary = self._build_directional_summary(
            summary=summary,
            similar_cases=similar_cases,
            market_cases=market_cases,
            predicted_outcome=predicted_outcome,
            current_ou_direction=current_ou_direction,
            current_scores=current_scores,
        )
        return {
            "available": True,
            "mode": result.get("mode"),
            "summary": summary,
            "similar_cases": similar_cases,
            "market_cases": market_cases,
            "upset_cases": upset_cases,
        }

    def query_cases(
        self,
        *,
        league_code: str,
        home_team: str,
        away_team: str,
        market_snapshot: Optional[Dict[str, Any]],
        match_id: str = "",
        top_k: int = 5,
    ) -> Dict[str, Any]:
        return retrieve_structured_cases(
            self.base_dir,
            league_code=league_code,
            home_team=home_team,
            away_team=away_team,
            market_snapshot=market_snapshot,
            match_id=match_id,
            top_k=top_k,
        )


LightweightRAGService = HybridRAGService

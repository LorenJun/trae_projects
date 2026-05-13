"""模块说明：从近期已完赛样本中提炼比分/大小球失真复盘，并输出可供增强预测读取的学习摘要。"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional

from runtime.paths import get_default_paths


class PredictionReviewLearningService:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir
        self.paths = get_default_paths(base_dir)

    def summary_path(self):
        return self.paths.runtime_file("prediction_review_learning.json")

    @staticmethod
    def _parse_score(score_text: str) -> Optional[tuple[int, int]]:
        raw = str(score_text or "").strip()
        if "-" not in raw:
            return None
        try:
            home, away = [int(part.strip()) for part in raw.split("-", 1)]
        except Exception:
            return None
        return home, away

    @staticmethod
    def _winner_label(winner_key: str) -> str:
        mapping = {"home": "主胜", "draw": "平局", "away": "客胜"}
        return mapping.get(str(winner_key or "").strip(), str(winner_key or "").strip())

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    def _load_completed_samples(self, days: int) -> List[Dict[str, Any]]:
        from result_manager import ResultManager

        manager = ResultManager(self.base_dir)
        payload = manager._build_unified_prediction_samples(days=days)
        rag_market_lookup = self._load_rag_market_lookup()
        completed: List[Dict[str, Any]] = []
        for sample in payload.values():
            actual_pair = self._parse_score(str(sample.get("actual_score") or ""))
            predicted_winner = str(sample.get("predicted_winner") or "").strip()
            if not actual_pair or predicted_winner not in {"home", "draw", "away"}:
                continue
            market_meta = self._match_market_meta(sample, rag_market_lookup)
            predicted_scores = [
                str(item).strip()
                for item in (sample.get("predicted_scores") or [])
                if self._parse_score(str(item or ""))
            ]
            predicted_ou = sample.get("predicted_ou") if isinstance(sample.get("predicted_ou"), dict) else None
            completed.append(
                {
                    "match_id": str(sample.get("match_id") or "").strip(),
                    "league": str(sample.get("league") or "").strip(),
                    "league_name": str(sample.get("league_name") or "").strip(),
                    "match_date": str(sample.get("match_date") or "").strip(),
                    "match_time": str(sample.get("match_time") or "").strip(),
                    "home_team": str(sample.get("home_team") or "").strip(),
                    "away_team": str(sample.get("away_team") or "").strip(),
                    "actual_score": str(sample.get("actual_score") or "").strip(),
                    "actual_winner": str(sample.get("actual_winner") or "").strip(),
                    "predicted_winner": predicted_winner,
                    "predicted_scores": predicted_scores,
                    "predicted_ou": predicted_ou,
                    "asian_line": market_meta.get("asian_line"),
                    "euro_home": market_meta.get("euro_home"),
                    "euro_draw": market_meta.get("euro_draw"),
                    "euro_away": market_meta.get("euro_away"),
                    "line_source": str(sample.get("line_source") or "unknown").strip() or "unknown",
                    "storage_mode": str(sample.get("storage_mode") or "unknown").strip() or "unknown",
                    "source_presence": list(sample.get("source_presence") or []),
                }
            )
        completed.sort(key=lambda item: (item.get("match_date") or "", item.get("match_id") or ""), reverse=True)
        return completed

    @staticmethod
    def _match_lookup_keys(sample: Dict[str, Any]) -> List[str]:
        match_id = str(sample.get("match_id") or "").strip()
        league = str(sample.get("league") or sample.get("league_code") or "").strip()
        match_date = str(sample.get("match_date") or "").strip()
        home_team = str(sample.get("home_team") or "").strip()
        away_team = str(sample.get("away_team") or "").strip()
        keys = []
        if match_id:
            keys.append(f"id:{match_id}")
        if league and match_date and home_team and away_team:
            keys.append(f"meta:{league}|{match_date}|{home_team}|{away_team}")
        return keys

    def _load_rag_market_lookup(self) -> Dict[str, Dict[str, Any]]:
        rag_path = self.paths.runtime_dir / "rag_cases.json"
        if not rag_path.exists():
            return {}
        try:
            payload = json.loads(rag_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        cases = payload.get("cases") if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            return {}

        lookup: Dict[str, Dict[str, Any]] = {}
        for case in cases:
            if not isinstance(case, dict):
                continue
            if str(case.get("case_type") or "") != "prediction_case":
                continue
            market_meta = {
                "asian_line": self._safe_float(case.get("asian_line")),
                "euro_home": self._safe_float(case.get("euro_home")),
                "euro_draw": self._safe_float(case.get("euro_draw")),
                "euro_away": self._safe_float(case.get("euro_away")),
            }
            for key in self._match_lookup_keys(case):
                lookup[key] = market_meta
        return lookup

    def _match_market_meta(self, sample: Dict[str, Any], market_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        for key in self._match_lookup_keys(sample):
            matched = market_lookup.get(key)
            if isinstance(matched, dict):
                return matched
        return {"asian_line": None, "euro_home": None, "euro_draw": None, "euro_away": None}

    @staticmethod
    def _implied_probs_from_euro(home_odds: Any, draw_odds: Any, away_odds: Any) -> Dict[str, Optional[float]]:
        oh = PredictionReviewLearningService._safe_float(home_odds)
        od = PredictionReviewLearningService._safe_float(draw_odds)
        oa = PredictionReviewLearningService._safe_float(away_odds)
        if not oh or not od or not oa or oh <= 1.01 or od <= 1.01 or oa <= 1.01:
            return {"home": None, "draw": None, "away": None}
        raw_home = 1.0 / oh
        raw_draw = 1.0 / od
        raw_away = 1.0 / oa
        total = raw_home + raw_draw + raw_away
        if total <= 0:
            return {"home": None, "draw": None, "away": None}
        return {
            "home": raw_home / total,
            "draw": raw_draw / total,
            "away": raw_away / total,
        }

    @classmethod
    def _classify_euro_support_bucket(
        cls,
        *,
        predicted_winner: str,
        euro_home: Any,
        euro_draw: Any,
        euro_away: Any,
    ) -> str:
        implied = cls._implied_probs_from_euro(euro_home, euro_draw, euro_away)
        ph = implied.get("home")
        pd = implied.get("draw")
        pa = implied.get("away")
        if ph is None or pd is None or pa is None:
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
    def _classify_handicap_depth(asian_line: Any) -> str:
        line = PredictionReviewLearningService._safe_float(asian_line)
        if line is None:
            return "unknown"
        depth = abs(float(line))
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
    def _classify_strength_gap_bucket(strength_diff: Any) -> str:
        gap = abs(float(strength_diff or 0.0))
        if gap <= 8:
            return "balanced"
        if gap <= 16:
            return "edge"
        if gap <= 24:
            return "clear"
        return "huge"

    @staticmethod
    def _strength_vs_handicap_mismatch(strength_diff: Any, asian_line: Any, predicted_winner: str) -> str:
        gap = float(strength_diff or 0.0)
        depth = abs(float(PredictionReviewLearningService._safe_float(asian_line) or 0.0))
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

    def _build_outcome_stratified_review(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        depth_names = {
            "level_ball": "平手盘",
            "level_shallow": "平半/半球浅盘",
            "level_medium": "半球至半一",
            "level_deep": "一球至球半",
            "level_very_deep": "球半及以上",
            "unknown": "缺盘口",
        }
        winner_names = {"home": "主胜", "draw": "平局", "away": "客胜"}
        grouped: Dict[str, Dict[str, Any]] = {}
        for sample in samples:
            predicted_winner = str(sample.get("predicted_winner") or "").strip()
            actual_winner = str(sample.get("actual_winner") or "").strip()
            if predicted_winner not in {"home", "away", "draw"} or actual_winner not in {"home", "away", "draw"}:
                continue
            depth_bucket = self._classify_handicap_depth(sample.get("asian_line"))
            key = f"{predicted_winner}:{depth_bucket}"
            bucket = grouped.setdefault(
                key,
                {
                    "predicted_winner": predicted_winner,
                    "predicted_winner_label": winner_names.get(predicted_winner, predicted_winner),
                    "handicap_bucket": depth_bucket,
                    "handicap_bucket_label": depth_names.get(depth_bucket, depth_bucket),
                    "sample_count": 0,
                    "hit_count": 0,
                    "miss_count": 0,
                    "draw_miss_count": 0,
                    "opposite_miss_count": 0,
                    "examples": [],
                },
            )
            bucket["sample_count"] += 1
            if predicted_winner == actual_winner:
                bucket["hit_count"] += 1
            else:
                bucket["miss_count"] += 1
                if actual_winner == "draw":
                    bucket["draw_miss_count"] += 1
                elif actual_winner in {"home", "away"} and actual_winner != predicted_winner:
                    bucket["opposite_miss_count"] += 1
                if len(bucket["examples"]) < 4:
                    bucket["examples"].append(
                        {
                            "teams": f"{sample.get('home_team')} vs {sample.get('away_team')}",
                            "actual_score": sample.get("actual_score"),
                            "actual_winner": winner_names.get(actual_winner, actual_winner),
                        }
                    )

        result: Dict[str, Any] = {}
        for key, bucket in grouped.items():
            sample_count = int(bucket["sample_count"])
            miss_count = int(bucket["miss_count"])
            bucket["hit_rate"] = round(int(bucket["hit_count"]) / sample_count, 4) if sample_count else 0.0
            bucket["miss_rate"] = round(miss_count / sample_count, 4) if sample_count else 0.0
            bucket["draw_miss_rate"] = round(int(bucket["draw_miss_count"]) / sample_count, 4) if sample_count else 0.0
            bucket["opposite_miss_rate"] = round(int(bucket["opposite_miss_count"]) / sample_count, 4) if sample_count else 0.0
            if bucket["predicted_winner"] in {"home", "away"} and sample_count >= 3:
                draw_shift = min(0.02, max(0.0, bucket["draw_miss_rate"] - 0.16) * 0.08)
                upset_shift = min(0.016, max(0.0, bucket["opposite_miss_rate"] - 0.1) * 0.08)
            else:
                draw_shift = 0.0
                upset_shift = 0.0
            bucket["recommended_draw_shift"] = round(draw_shift, 4)
            bucket["recommended_upset_shift"] = round(upset_shift, 4)
            result[key] = bucket
        return result

    def _build_three_layer_outcome_review(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        grouped: Dict[str, Dict[str, Any]] = {}
        euro_support_labels = {
            "strong_support": "欧赔强支持",
            "support": "欧赔支持",
            "soft_support": "欧赔弱支持",
            "draw_guarded": "平局防守强",
            "market_opposes": "市场反向",
            "draw_supported": "欧赔主打平局",
            "draw_live": "欧赔平局活跃",
            "draw_soft": "欧赔平局弱支持",
            "unknown": "缺欧赔",
        }
        for sample in samples:
            predicted_winner = str(sample.get("predicted_winner") or "").strip()
            actual_winner = str(sample.get("actual_winner") or "").strip()
            if predicted_winner not in {"home", "away", "draw"} or actual_winner not in {"home", "away", "draw"}:
                continue
            depth_bucket = self._classify_handicap_depth(sample.get("asian_line"))
            euro_support_bucket = self._classify_euro_support_bucket(
                predicted_winner=predicted_winner,
                euro_home=sample.get("euro_home"),
                euro_draw=sample.get("euro_draw"),
                euro_away=sample.get("euro_away"),
            )
            key = f"{predicted_winner}:{depth_bucket}:{euro_support_bucket}"
            bucket = grouped.setdefault(
                key,
                {
                    "predicted_winner": predicted_winner,
                    "handicap_bucket": depth_bucket,
                    "euro_support_bucket": euro_support_bucket,
                    "euro_support_label": euro_support_labels.get(euro_support_bucket, euro_support_bucket),
                    "sample_count": 0,
                    "hit_count": 0,
                    "miss_count": 0,
                    "draw_miss_count": 0,
                    "opposite_miss_count": 0,
                },
            )
            bucket["sample_count"] += 1
            if predicted_winner == actual_winner:
                bucket["hit_count"] += 1
            else:
                bucket["miss_count"] += 1
                if actual_winner == "draw":
                    bucket["draw_miss_count"] += 1
                elif actual_winner in {"home", "away"} and actual_winner != predicted_winner:
                    bucket["opposite_miss_count"] += 1

        result: Dict[str, Any] = {}
        for key, bucket in grouped.items():
            sample_count = int(bucket["sample_count"])
            bucket["hit_rate"] = round(int(bucket["hit_count"]) / sample_count, 4) if sample_count else 0.0
            bucket["miss_rate"] = round(int(bucket["miss_count"]) / sample_count, 4) if sample_count else 0.0
            bucket["draw_miss_rate"] = round(int(bucket["draw_miss_count"]) / sample_count, 4) if sample_count else 0.0
            bucket["opposite_miss_rate"] = round(int(bucket["opposite_miss_count"]) / sample_count, 4) if sample_count else 0.0
            draw_shift = 0.0
            upset_shift = 0.0
            if sample_count >= 2 and bucket["predicted_winner"] in {"home", "away"}:
                draw_shift = min(0.024, max(0.0, bucket["draw_miss_rate"] - 0.15) * 0.09)
                upset_shift = min(0.02, max(0.0, bucket["opposite_miss_rate"] - 0.1) * 0.09)
                if bucket["euro_support_bucket"] in {"draw_guarded", "soft_support"}:
                    draw_shift = min(0.026, draw_shift + 0.004)
                if bucket["euro_support_bucket"] == "market_opposes":
                    upset_shift = min(0.022, upset_shift + 0.006)
            bucket["recommended_draw_shift"] = round(draw_shift, 4)
            bucket["recommended_upset_shift"] = round(upset_shift, 4)
            result[key] = bucket
        return result

    def _load_recent_league_review(self, recent_days: int) -> Dict[str, Dict[str, Any]]:
        try:
            from scripts.build_recent_five_leagues_review import summarize_recent_five_leagues_cases

            summary = summarize_recent_five_leagues_cases(
                self.base_dir or str(self.paths.base_dir),
                recent_days=recent_days,
            )
        except Exception:
            return {}

        league_sections = summary.get("league_sections") if isinstance(summary, dict) else {}
        if not isinstance(league_sections, dict):
            return {}
        return league_sections

    def _classify_score_miss(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        predicted_scores = list(sample.get("predicted_scores") or [])
        actual_score = str(sample.get("actual_score") or "").strip()
        if not predicted_scores or actual_score in predicted_scores:
            return None

        actual_pair = self._parse_score(actual_score)
        if not actual_pair:
            return None
        actual_home, actual_away = actual_pair
        actual_total = actual_home + actual_away

        predicted_pairs = [self._parse_score(item) for item in predicted_scores]
        predicted_pairs = [pair for pair in predicted_pairs if pair]
        if not predicted_pairs:
            return None

        predicted_totals = [home + away for home, away in predicted_pairs]
        avg_pred_total = statistics.mean(predicted_totals) if predicted_totals else 0.0
        max_pred_home = max(home for home, _away in predicted_pairs)
        max_pred_away = max(away for _home, away in predicted_pairs)
        all_drawish = all(home == away for home, away in predicted_pairs)
        all_one_goal_or_lower = all((home + away) <= 1 for home, away in predicted_pairs)
        all_btts = all(home >= 1 and away >= 1 for home, away in predicted_pairs)

        tags: List[str] = []
        if sample.get("predicted_winner") != sample.get("actual_winner"):
            tags.append("赛果方向判断偏差")
        if (
            sample.get("predicted_winner") == "home"
            and sample.get("actual_winner") == "home"
            and actual_total >= 3
            and avg_pred_total <= actual_total - 1
        ):
            tags.append("主胜方向命中但比分保守")
        if (
            sample.get("predicted_winner") == "away"
            and sample.get("actual_winner") == "away"
            and actual_total >= 3
            and avg_pred_total <= actual_total - 1
        ):
            tags.append("客胜方向命中但比分保守")
        if avg_pred_total <= actual_total - 1:
            tags.append("总进球低估")
        if avg_pred_total >= actual_total + 1 and actual_total <= 1:
            tags.append("高比分预期过热")
        if max_pred_home < actual_home:
            tags.append("主队进球上沿低估")
        if max_pred_away < actual_away:
            tags.append("客队进球上沿低估")
        if actual_away == 0 and all_btts:
            tags.append("零封场景覆盖不足")
        if sample.get("actual_winner") == "home" and (all_drawish or all_one_goal_or_lower):
            tags.append("主队强势取胜覆盖不足")
        if not tags:
            tags.append("比分候选覆盖不足")

        priority = [
            "赛果方向判断偏差",
            "主胜方向命中但比分保守",
            "客胜方向命中但比分保守",
            "总进球低估",
            "高比分预期过热",
            "主队进球上沿低估",
            "客队进球上沿低估",
            "零封场景覆盖不足",
            "主队强势取胜覆盖不足",
            "比分候选覆盖不足",
        ]
        primary_reason = next((item for item in priority if item in tags), tags[0])
        return {
            "match_id": sample.get("match_id"),
            "match_date": sample.get("match_date"),
            "league": sample.get("league"),
            "league_name": sample.get("league_name"),
            "teams": f"{sample.get('home_team')} vs {sample.get('away_team')}",
            "predicted_winner": self._winner_label(str(sample.get("predicted_winner") or "")),
            "actual_winner": self._winner_label(str(sample.get("actual_winner") or "")),
            "actual_score": actual_score,
            "predicted_scores": predicted_scores,
            "actual_total_goals": actual_total,
            "avg_pred_total_goals": round(float(avg_pred_total), 3),
            "primary_reason": primary_reason,
            "reason_tags": tags,
        }

    def _classify_ou_miss(self, sample: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        predicted_ou = sample.get("predicted_ou") if isinstance(sample.get("predicted_ou"), dict) else None
        actual_pair = self._parse_score(str(sample.get("actual_score") or ""))
        if not predicted_ou or not actual_pair:
            return None

        line = self._safe_float(predicted_ou.get("line"))
        side = str(predicted_ou.get("side") or "").strip()
        if side not in {"大", "小"} or line is None:
            return None

        actual_home, actual_away = actual_pair
        actual_total = actual_home + actual_away
        if abs(actual_total - line) < 1e-9:
            return None
        actual_side = "大" if actual_total > line else "小"
        if actual_side == side:
            return None

        tags: List[str] = []
        if side == "小" and actual_side == "大":
            tags.append("小球倾向过重")
            if sample.get("actual_winner") == "home" and actual_home >= 2:
                tags.append("主队强势赢球时低估进球")
            if actual_total - line >= 1.0:
                tags.append("大比分上沿释放不足")
        if side == "大" and actual_side == "小":
            tags.append("大球倾向过热")
            if actual_total <= 1:
                tags.append("低转化比赛识别不足")
            if actual_home == 0 or actual_away == 0:
                tags.append("单边哑火风险漏判")
        if not tags:
            tags.append("大小球方向失真")

        priority = [
            "小球倾向过重",
            "大球倾向过热",
            "主队强势赢球时低估进球",
            "大比分上沿释放不足",
            "低转化比赛识别不足",
            "单边哑火风险漏判",
            "大小球方向失真",
        ]
        primary_reason = next((item for item in priority if item in tags), tags[0])
        return {
            "match_id": sample.get("match_id"),
            "match_date": sample.get("match_date"),
            "league": sample.get("league"),
            "league_name": sample.get("league_name"),
            "teams": f"{sample.get('home_team')} vs {sample.get('away_team')}",
            "actual_score": str(sample.get("actual_score") or "").strip(),
            "predicted_side": side,
            "actual_side": actual_side,
            "line": line,
            "primary_reason": primary_reason,
            "reason_tags": tags,
            "line_source": sample.get("line_source"),
        }

    @staticmethod
    def _count_tags(items: List[Dict[str, Any]]) -> Dict[str, int]:
        counter: Counter[str] = Counter()
        for item in items:
            for tag in item.get("reason_tags") or []:
                counter[str(tag)] += 1
        return dict(counter.most_common())

    @staticmethod
    def _preview_items(items: List[Dict[str, Any]], limit: int = 6) -> List[Dict[str, Any]]:
        return items[: max(0, int(limit))]

    def _build_learning_context_bundle(self, reviewed: List[Dict[str, Any]]) -> Dict[str, Any]:
        score_candidates = [item for item in reviewed if item.get("predicted_scores")]
        score_hits = [item for item in score_candidates if str(item.get("actual_score") or "") in list(item.get("predicted_scores") or [])]
        score_misses = [item for item in (self._classify_score_miss(sample) for sample in reviewed) if item]

        ou_candidates: List[Dict[str, Any]] = []
        ou_hits = 0
        ou_misses: List[Dict[str, Any]] = []
        for sample in reviewed:
            predicted_ou = sample.get("predicted_ou") if isinstance(sample.get("predicted_ou"), dict) else None
            actual_pair = self._parse_score(str(sample.get("actual_score") or ""))
            if not predicted_ou or not actual_pair:
                continue
            line = self._safe_float(predicted_ou.get("line"))
            side = str(predicted_ou.get("side") or "").strip()
            if side not in {"大", "小"} or line is None:
                continue
            total_goals = actual_pair[0] + actual_pair[1]
            if abs(total_goals - line) < 1e-9:
                continue
            ou_candidates.append(sample)
            actual_side = "大" if total_goals > line else "小"
            if actual_side == side:
                ou_hits += 1
            else:
                miss = self._classify_ou_miss(sample)
                if miss:
                    ou_misses.append(miss)

        score_reason_counts = self._count_tags(score_misses)
        ou_reason_counts = self._count_tags(ou_misses)
        score_miss_count = len(score_misses)
        ou_miss_count = len(ou_misses)
        score_sample_count = len(score_candidates)
        ou_sample_count = len(ou_candidates)

        score_bias = {
            "available": score_miss_count > 0,
            "sample_count": score_sample_count,
            "hit_count": len(score_hits),
            "miss_count": score_miss_count,
            "miss_rate": round(score_miss_count / score_sample_count, 4) if score_sample_count else 0.0,
            "conservative_home_win_rate": round(score_reason_counts.get("主胜方向命中但比分保守", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "conservative_away_win_rate": round(score_reason_counts.get("客胜方向命中但比分保守", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "low_total_underestimate_rate": round(score_reason_counts.get("总进球低估", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "home_goal_ceiling_underestimate_rate": round(score_reason_counts.get("主队进球上沿低估", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "away_goal_ceiling_underestimate_rate": round(score_reason_counts.get("客队进球上沿低估", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "clean_sheet_coverage_gap_rate": round(score_reason_counts.get("零封场景覆盖不足", 0) / score_miss_count, 4) if score_miss_count else 0.0,
            "recommended_home_goal_boost": round(min(0.08, 0.01 + score_reason_counts.get("主队进球上沿低估", 0) * 0.01), 4) if score_miss_count else 0.0,
            "recommended_low_total_penalty": round(min(0.05, 0.01 + score_reason_counts.get("总进球低估", 0) * 0.005), 4) if score_miss_count else 0.0,
        }
        ou_bias = {
            "available": ou_miss_count > 0,
            "sample_count": ou_sample_count,
            "hit_count": ou_hits,
            "miss_count": ou_miss_count,
            "miss_rate": round(ou_miss_count / ou_sample_count, 4) if ou_sample_count else 0.0,
            "under_to_over_rate": round(ou_reason_counts.get("小球倾向过重", 0) / ou_miss_count, 4) if ou_miss_count else 0.0,
            "over_to_under_rate": round(ou_reason_counts.get("大球倾向过热", 0) / ou_miss_count, 4) if ou_miss_count else 0.0,
            "home_win_under_to_over_rate": round(ou_reason_counts.get("主队强势赢球时低估进球", 0) / ou_miss_count, 4) if ou_miss_count else 0.0,
            "low_conversion_overheat_rate": round(ou_reason_counts.get("低转化比赛识别不足", 0) / ou_miss_count, 4) if ou_miss_count else 0.0,
            "recommended_over_shift": round(min(0.04, 0.01 + ou_reason_counts.get("小球倾向过重", 0) * 0.006), 4) if ou_miss_count else 0.0,
            "recommended_under_shift": round(min(0.025, 0.006 + ou_reason_counts.get("大球倾向过热", 0) * 0.005), 4) if ou_miss_count else 0.0,
        }

        recommendations: List[str] = []
        if score_bias["conservative_home_win_rate"] >= 0.4 or score_bias["home_goal_ceiling_underestimate_rate"] >= 0.4:
            recommendations.append("主胜场景下减少 0-0/1-0 低比分拥挤，适度抬升 2-1/3-0/3-1 候选权重。")
        if score_bias["low_total_underestimate_rate"] >= 0.4:
            recommendations.append("当总进球分布集中在 1-2 球但主队优势明确时，放宽 3 球以上比分候选。")
        if ou_bias["under_to_over_rate"] >= 0.5:
            recommendations.append("大小球对 2.5/3.5 附近盘口减少机械压小，强势主队场景增加向上偏置。")
        if ou_bias["over_to_under_rate"] >= 0.25:
            recommendations.append("对杯赛或单边哑火风险场景保留 1-0/0-0/0-1 等低转化分支。")

        return {
            "score_bias": score_bias,
            "over_under_bias": ou_bias,
            "recommendations": recommendations,
            "score_review": {
                "sample_count": score_sample_count,
                "hit_count": len(score_hits),
                "miss_count": score_miss_count,
                "miss_rate": round(score_miss_count / score_sample_count, 4) if score_sample_count else 0.0,
                "reason_counts": score_reason_counts,
                "miss_examples": self._preview_items(score_misses),
            },
            "over_under_review": {
                "sample_count": ou_sample_count,
                "hit_count": ou_hits,
                "miss_count": ou_miss_count,
                "miss_rate": round(ou_miss_count / ou_sample_count, 4) if ou_sample_count else 0.0,
                "reason_counts": ou_reason_counts,
                "miss_examples": self._preview_items(ou_misses),
            },
        }

    def build_summary(self, *, days: int = 30, sample_limit: int = 12) -> Dict[str, Any]:
        completed = self._load_completed_samples(days=days)
        reviewed = completed[: max(1, int(sample_limit or 12))]
        learning_bundle = self._build_learning_context_bundle(reviewed)
        score_bias = learning_bundle["score_bias"]
        ou_bias = learning_bundle["over_under_bias"]
        recommendations = list(learning_bundle["recommendations"])

        outcome_stratified_review = self._build_outcome_stratified_review(completed)
        three_layer_outcome_review = self._build_three_layer_outcome_review(completed)
        league_outcome_stratified_review: Dict[str, Dict[str, Any]] = {}
        league_three_layer_outcome_review: Dict[str, Dict[str, Any]] = {}
        for league_code in sorted({str(sample.get("league") or "").strip() for sample in completed if str(sample.get("league") or "").strip()}):
            league_outcome_stratified_review[league_code] = self._build_outcome_stratified_review(
                [sample for sample in completed if str(sample.get("league") or "").strip() == league_code]
            )
            league_three_layer_outcome_review[league_code] = self._build_three_layer_outcome_review(
                [sample for sample in completed if str(sample.get("league") or "").strip() == league_code]
            )

        league_overview: Dict[str, Dict[str, Any]] = {}
        for sample in reviewed:
            league_code = str(sample.get("league") or "").strip()
            if not league_code:
                continue
            bucket = league_overview.setdefault(
                league_code,
                {
                    "league_name": sample.get("league_name"),
                    "completed_count": 0,
                    "score_sample_count": 0,
                    "score_miss_count": 0,
                    "ou_sample_count": 0,
                    "ou_miss_count": 0,
                },
            )
            bucket["completed_count"] += 1
            if sample.get("predicted_scores"):
                bucket["score_sample_count"] += 1
                if str(sample.get("actual_score") or "") not in list(sample.get("predicted_scores") or []):
                    bucket["score_miss_count"] += 1
            predicted_ou = sample.get("predicted_ou") if isinstance(sample.get("predicted_ou"), dict) else None
            actual_pair = self._parse_score(str(sample.get("actual_score") or ""))
            if predicted_ou and actual_pair:
                line = self._safe_float(predicted_ou.get("line"))
                side = str(predicted_ou.get("side") or "").strip()
                if side in {"大", "小"} and line is not None:
                    total_goals = actual_pair[0] + actual_pair[1]
                    if abs(total_goals - line) >= 1e-9:
                        bucket["ou_sample_count"] += 1
                        actual_side = "大" if total_goals > line else "小"
                        if actual_side != side:
                            bucket["ou_miss_count"] += 1

        learning_context_by_league: Dict[str, Dict[str, Any]] = {}
        for league_code in sorted({str(sample.get("league") or "").strip() for sample in reviewed if str(sample.get("league") or "").strip()}):
            league_reviewed = [sample for sample in reviewed if str(sample.get("league") or "").strip() == league_code]
            league_bundle = self._build_learning_context_bundle(league_reviewed)
            learning_context_by_league[league_code] = {
                "reviewed_sample_count": len(league_reviewed),
                "score_bias": league_bundle["score_bias"],
                "over_under_bias": league_bundle["over_under_bias"],
                "recommendations": list(league_bundle["recommendations"]),
            }

        reviewed_preview = [
            {
                "match_id": sample.get("match_id"),
                "match_date": sample.get("match_date"),
                "league": sample.get("league"),
                "teams": f"{sample.get('home_team')} vs {sample.get('away_team')}",
                "actual_score": sample.get("actual_score"),
                "predicted_winner": self._winner_label(str(sample.get("predicted_winner") or "")),
                "actual_winner": self._winner_label(str(sample.get("actual_winner") or "")),
                "predicted_scores": sample.get("predicted_scores"),
                "predicted_ou": sample.get("predicted_ou"),
            }
            for sample in reviewed
        ]

        return {
            "updated_at": datetime.now().isoformat(),
            "days": int(days),
            "reviewed_sample_limit": int(sample_limit),
            "completed_sample_count": len(completed),
            "reviewed_sample_count": len(reviewed),
            "reviewed_matches": reviewed_preview,
            "score_review": learning_bundle["score_review"],
            "over_under_review": learning_bundle["over_under_review"],
            "learning_context": {
                "score_bias": score_bias,
                "over_under_bias": ou_bias,
                "recommendations": recommendations,
                "by_league": learning_context_by_league,
            },
            "league_overview": league_overview,
            "outcome_stratified_review": {
                "overall": outcome_stratified_review,
                "by_league": league_outcome_stratified_review,
            },
            "three_layer_outcome_review": {
                "overall": three_layer_outcome_review,
                "by_league": league_three_layer_outcome_review,
            },
        }

    def refresh_summary(self, *, days: int = 30, sample_limit: int = 12) -> Dict[str, Any]:
        payload = self.build_summary(days=days, sample_limit=sample_limit)
        self.summary_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def load_summary(self, *, days: int = 30, sample_limit: int = 12) -> Dict[str, Any]:
        path = self.summary_path()
        if not path.exists():
            return self.refresh_summary(days=days, sample_limit=sample_limit)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return self.refresh_summary(days=days, sample_limit=sample_limit)
        if not isinstance(payload, dict) or "learning_context" not in payload:
            return self.refresh_summary(days=days, sample_limit=sample_limit)
        if not isinstance(payload.get("outcome_stratified_review"), dict):
            return self.refresh_summary(days=days, sample_limit=sample_limit)
        if not isinstance(payload.get("three_layer_outcome_review"), dict):
            return self.refresh_summary(days=days, sample_limit=sample_limit)
        return payload

    def build_prediction_context(self, *, league_code: str, days: int = 30, sample_limit: int = 12) -> Dict[str, Any]:
        payload = self.load_summary(days=days, sample_limit=sample_limit)
        learning_context = payload.get("learning_context") if isinstance(payload.get("learning_context"), dict) else {}
        league_overview = payload.get("league_overview") if isinstance(payload.get("league_overview"), dict) else {}
        league_bucket = league_overview.get(league_code) if isinstance(league_overview.get(league_code), dict) else {}
        recent_league_review = self._load_recent_league_review(recent_days=days)
        league_review_bucket = recent_league_review.get(league_code) if isinstance(recent_league_review.get(league_code), dict) else {}
        outcome_stratified_review = payload.get("outcome_stratified_review") if isinstance(payload.get("outcome_stratified_review"), dict) else {}
        outcome_by_league = outcome_stratified_review.get("by_league") if isinstance(outcome_stratified_review.get("by_league"), dict) else {}
        stratified_league = outcome_by_league.get(league_code) if isinstance(outcome_by_league.get(league_code), dict) else {}
        stratified_overall = outcome_stratified_review.get("overall") if isinstance(outcome_stratified_review.get("overall"), dict) else {}
        selected_stratified_review = stratified_league or stratified_overall
        three_layer_outcome_review = payload.get("three_layer_outcome_review") if isinstance(payload.get("three_layer_outcome_review"), dict) else {}
        three_layer_by_league = three_layer_outcome_review.get("by_league") if isinstance(three_layer_outcome_review.get("by_league"), dict) else {}
        three_layer_league = three_layer_by_league.get(league_code) if isinstance(three_layer_by_league.get(league_code), dict) else {}
        three_layer_overall = three_layer_outcome_review.get("overall") if isinstance(three_layer_outcome_review.get("overall"), dict) else {}
        selected_three_layer_review = three_layer_league or three_layer_overall
        learning_by_league = learning_context.get("by_league") if isinstance(learning_context.get("by_league"), dict) else {}
        league_learning_bundle = learning_by_league.get(league_code) if isinstance(learning_by_league.get(league_code), dict) else {}
        recommendations = list(league_learning_bundle.get("recommendations") or []) + list(learning_context.get("recommendations") or [])
        overall_score_bias = learning_context.get("score_bias") if isinstance(learning_context.get("score_bias"), dict) else {}
        overall_ou_bias = learning_context.get("over_under_bias") if isinstance(learning_context.get("over_under_bias"), dict) else {}
        league_score_bias = league_learning_bundle.get("score_bias") if isinstance(league_learning_bundle.get("score_bias"), dict) else {}
        league_ou_bias = league_learning_bundle.get("over_under_bias") if isinstance(league_learning_bundle.get("over_under_bias"), dict) else {}
        use_league_score_bias = bool(league_score_bias.get("available"))
        use_league_ou_bias = bool(league_ou_bias.get("available"))
        score_bias = league_score_bias if use_league_score_bias else overall_score_bias
        ou_bias = league_ou_bias if use_league_ou_bias else overall_ou_bias
        completed_count = int(league_review_bucket.get("completed_count") or 0)
        prediction_count = int(league_review_bucket.get("prediction_count") or 0)
        unpredicted_completed_count = int(league_review_bucket.get("unpredicted_completed_count") or 0)
        prediction_coverage_rate = round(prediction_count / completed_count, 4) if completed_count else 0.0
        top_problem_group_text = str(league_review_bucket.get("top_problem_group_text") or "").strip()
        league_review = {
            "league_tags": list(league_review_bucket.get("league_tags") or []),
            "completed_count": completed_count,
            "prediction_count": prediction_count,
            "unpredicted_completed_count": unpredicted_completed_count,
            "prediction_coverage_rate": prediction_coverage_rate,
            "top_case_tags": list(league_review_bucket.get("top_case_tags") or []),
            "top_problem_group_text": top_problem_group_text,
            "top_problem_groups": list(league_review_bucket.get("top_problem_groups") or []),
        }
        if "主胜偏置" in " ".join(league_review["league_tags"]):
            recommendations.append("联赛近期存在主胜偏置，1X2 主胜结论需额外防平/防客。")
        if "平局防守不足" in " ".join(league_review["league_tags"]):
            recommendations.append("联赛近期平局低估较明显，均势盘需提高平局分支权重。")
        if "客胜冷门敏感度不足" in " ".join(league_review["league_tags"]):
            recommendations.append("联赛近期对客胜冷门识别不足，强侧浅让场景需保留客胜分支。")
        if prediction_coverage_rate < 0.6:
            recommendations.append("联赛近期预测覆盖率偏低，需下调主方向置信度并扩大风险提示。")
        for bucket in selected_stratified_review.values():
            if not isinstance(bucket, dict):
                continue
            if float(bucket.get("recommended_draw_shift") or 0.0) >= 0.01:
                recommendations.append(
                    f"{bucket.get('predicted_winner_label', '')}{bucket.get('handicap_bucket_label', '')}样本平局漏防偏多，浅深盘交界场景应提高平局分支。"
                )
                break
        for bucket in selected_stratified_review.values():
            if not isinstance(bucket, dict):
                continue
            if float(bucket.get("recommended_upset_shift") or 0.0) >= 0.008:
                recommendations.append(
                    f"{bucket.get('predicted_winner_label', '')}{bucket.get('handicap_bucket_label', '')}样本存在反向赛果漏判，强侧结论需保留冷门分支。"
                )
                break
        for bucket in selected_three_layer_review.values():
            if not isinstance(bucket, dict):
                continue
            euro_support_bucket = str(bucket.get("euro_support_bucket") or "")
            if euro_support_bucket in {"draw_guarded", "market_opposes"} and float(bucket.get("recommended_draw_shift") or 0.0) >= 0.01:
                recommendations.append("欧赔对强侧支持不足且平局参与度偏高时，应提前扩展平局保护。")
                break
        deduped_recommendations = list(dict.fromkeys(recommendations))
        if not score_bias and not ou_bias and not league_review["league_tags"] and completed_count <= 0:
            return {"available": False}
        return {
            "available": True,
            "source": "prediction_review_learning",
            "updated_at": payload.get("updated_at"),
            "reviewed_sample_count": int(payload.get("reviewed_sample_count") or 0),
            "days": int(payload.get("days") or days),
            "league_focus": {
                "league_code": league_code,
                "completed_count": int(league_bucket.get("completed_count") or 0),
                "score_sample_count": int(league_bucket.get("score_sample_count") or 0),
                "score_miss_count": int(league_bucket.get("score_miss_count") or 0),
                "ou_sample_count": int(league_bucket.get("ou_sample_count") or 0),
                "ou_miss_count": int(league_bucket.get("ou_miss_count") or 0),
            },
            "league_review": league_review,
            "outcome_stratified_review": selected_stratified_review,
            "three_layer_outcome_review": selected_three_layer_review,
            "score_bias": score_bias,
            "over_under_bias": ou_bias,
            "score_bias_scope": "league" if use_league_score_bias else "overall",
            "over_under_bias_scope": "league" if use_league_ou_bias else "overall",
            "recommendations": deduped_recommendations[:5],
        }

"""Review bias configuration and reusable adjustment helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ReviewBiasService:
    DEFAULT_REVIEW_BIAS_CONFIG: Dict[str, Any] = {
        "outcome": {
            "context_floors": {
                "home:unknown": {"draw_shift": 0.011, "upset_shift": 0.0158},
                "home:level_medium": {"draw_shift": 0.02, "upset_shift": 0.016},
                "home:unknown:market_opposes": {"draw_shift": 0.012, "upset_shift": 0.022},
                "home:level_medium:strong_support": {"draw_shift": 0.012, "upset_shift": 0.01},
                "away:level_shallow:draw_guarded": {"draw_shift": 0.014, "upset_shift": 0.008},
            },
            "motivation_risk": {
                "enabled": True,
                "min_score": 8.0,
                "base_draw_shift": 0.006,
                "base_side_shift": 0.008,
                "max_draw_shift": 0.02,
                "max_side_shift": 0.024,
            },
            "league_tag_bias": {
                "home_bias": {"draw_shift": 0.01, "upset_shift": 0.008},
                "draw_bias": {"draw_shift": 0.01},
                "away_upset_bias": {"upset_shift": 0.008},
            },
            "scenario_shifts": {
                "strong_home_shallow_line": {"draw_shift": 0.012, "upset_shift": 0.008},
                "away_shallow_market_doubt": {"draw_shift": 0.014, "home_shift": 0.008},
                "balanced_draw_guard": {"draw_shift": 0.014},
            },
            "learning_multipliers": {
                "draw_multiplier": 1.0,
                "upset_multiplier": 1.0,
                "stratified_max_draw_shift": 0.02,
                "stratified_max_upset_shift": 0.016,
                "three_layer_max_draw_shift": 0.024,
                "three_layer_max_upset_shift": 0.02
            },
        },
        "score": {
            "open_match_total_threshold": 2.8,
            "strong_open_match_total_threshold": 3.1,
            "motivation_risk_min_score": 8.0,
            "home_open_template": {
                "1-0": 0.84,
                "2-0": 0.91,
                "2-1": 1.1,
                "3-0": 1.02,
                "3-1": 1.12,
            },
            "away_open_template": {
                "0-1": 0.84,
                "0-2": 0.98,
                "1-2": 1.1,
                "0-3": 1.04,
                "1-3": 1.12,
            },
            "draw_open_template": {
                "0-0": 0.8,
                "1-1": 0.88,
                "2-2": 1.16,
                "3-3": 1.06,
            },
            "bundesliga": {
                "home_open_template": {
                    "1-0": 0.8,
                    "2-0": 0.9,
                    "2-1": 1.12,
                    "3-0": 1.08,
                    "3-1": 1.14,
                },
                "away_open_template": {
                    "0-1": 0.82,
                    "0-2": 1.0,
                    "1-2": 1.12,
                    "0-3": 1.06,
                    "1-3": 1.14,
                },
            },
        },
        "over_under": {
            "base_over_shift": 0.04,
            "base_under_shift": 0.006,
            "line_window": [2.5, 3.0, 3.5],
            "bundesliga": {
                "base_over_shift": 0.04,
                "base_under_shift": 0.006,
            },
        },
        "total_goals": {
            "line_window": [2.5, 3.0, 3.5],
            "base_over_shift": 0.035,
            "base_under_shift": 0.01,
            "max_total_shift": 0.06,
            "low_bucket_keys": ["0", "1", "2"],
            "high_bucket_targets": {
                "3": 0.45,
                "4": 0.3,
                "5": 0.17,
                "6": 0.06,
                "7+": 0.02
            },
            "bundesliga": {
                "base_over_shift": 0.04,
                "max_total_shift": 0.065
            }
        },
    }

    def __init__(self, league_config: Dict[str, Dict[str, Any]], base_dir: Optional[str] = None):
        self.league_config = league_config or {}
        self.base_dir = Path(base_dir).resolve() if base_dir else Path(__file__).resolve().parent.parent
        self.config = self._load_review_bias_config()

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except Exception:
            return None

    @classmethod
    def _deep_merge_dict(cls, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = cls._deep_merge_dict(merged.get(key) or {}, value)
            else:
                merged[key] = value
        return merged

    def _load_review_bias_config(self) -> Dict[str, Any]:
        config = dict(self.DEFAULT_REVIEW_BIAS_CONFIG)
        config_path = self.base_dir / "config" / "review_bias_config.json"
        if not config_path.exists():
            return config
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            return config
        if not isinstance(payload, dict):
            return config
        return self._deep_merge_dict(config, payload)

    def section(self, name: str, league_code: Optional[str]) -> Dict[str, Any]:
        block = self.config.get(name)
        if not isinstance(block, dict):
            return {}
        merged = {k: v for k, v in block.items() if k not in self.league_config}
        league_block = block.get(str(league_code or "").strip())
        if isinstance(league_block, dict):
            merged = self._deep_merge_dict(merged, league_block)
        return merged

    @staticmethod
    def extract_motivation_risk(
        *,
        upset_potential: Optional[Dict[str, Any]] = None,
        match_intelligence: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if isinstance(upset_potential, dict) and isinstance(upset_potential.get("motivation_risk"), dict):
            return upset_potential.get("motivation_risk") or {}
        if isinstance(match_intelligence, dict):
            motivation = match_intelligence.get("motivation") if isinstance(match_intelligence.get("motivation"), dict) else {}
            if isinstance(motivation.get("risk_signal"), dict):
                return motivation.get("risk_signal") or {}
        return {}

    def apply_review_over_under_adjustment(
        self,
        *,
        over_under: Dict[str, Any],
        league_code: Optional[str],
        review_learning: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        diag: Dict[str, Any] = {"applied": False, "signals": []}
        if not isinstance(over_under, dict) or not over_under.get("available"):
            diag["reason"] = "over_under_unavailable"
            return over_under, diag
        line = self._safe_float(over_under.get("line"))
        over_prob = self._safe_float(over_under.get("over"))
        under_prob = self._safe_float(over_under.get("under"))
        if line is None or over_prob is None or under_prob is None:
            diag["reason"] = "invalid_over_under_payload"
            return over_under, diag
        learning = review_learning if isinstance(review_learning, dict) else {}
        ou_bias = learning.get("over_under_bias") if isinstance(learning.get("over_under_bias"), dict) else {}
        ou_cfg = self.section("over_under", league_code)
        line_window = [float(item) for item in (ou_cfg.get("line_window") or [2.5, 3.0, 3.5]) if isinstance(item, (int, float))]
        near_focus_line = any(abs(line - focus_line) <= 0.26 for focus_line in line_window) if line_window else True
        recommended_over_shift = float(ou_bias.get("recommended_over_shift") or 0.0)
        recommended_under_shift = float(ou_bias.get("recommended_under_shift") or 0.0)
        base_over_shift = max(recommended_over_shift, float(ou_cfg.get("base_over_shift") or 0.0))
        base_under_shift = max(recommended_under_shift, float(ou_cfg.get("base_under_shift") or 0.0))
        motivation_risk = self.extract_motivation_risk(match_intelligence=match_intelligence)
        motivation_bonus = 0.0
        if bool(motivation_risk.get("supports_upset")) and float(motivation_risk.get("score") or 0.0) >= 0.18:
            motivation_bonus = 0.006
        adjusted = dict(over_under)
        effect = ""
        shift = 0.0
        if near_focus_line and under_prob >= over_prob:
            shift = min(0.06, base_over_shift + motivation_bonus)
            adjusted["over"] = min(1.0, over_prob + shift)
            adjusted["under"] = max(0.0, under_prob - shift)
            effect = "review-bias-over"
            diag["signals"].append("review-ou-reduce-under-bias")
        elif near_focus_line and over_prob > under_prob and base_under_shift > 0:
            shift = min(0.03, base_under_shift)
            adjusted["over"] = max(0.0, over_prob - shift)
            adjusted["under"] = min(1.0, under_prob + shift)
            effect = "review-bias-under"
            diag["signals"].append("review-ou-under-protection")
        else:
            diag["reason"] = "review_bias_not_triggered"
            return adjusted, diag
        total = float(adjusted.get("over") or 0.0) + float(adjusted.get("under") or 0.0)
        if total > 0:
            adjusted["over"] = float(adjusted.get("over") or 0.0) / total
            adjusted["under"] = float(adjusted.get("under") or 0.0) / total
        diag.update(
            {
                "applied": True,
                "reason": effect,
                "line": round(line, 3),
                "shift": round(shift, 4),
                "near_focus_line": near_focus_line,
                "bias_scope": learning.get("over_under_bias_scope") if isinstance(learning, dict) else "",
                "adjusted": {
                    "over": round(float(adjusted.get("over") or 0.0), 6),
                    "under": round(float(adjusted.get("under") or 0.0), 6),
                },
            }
        )
        return adjusted, diag

    def apply_review_total_goals_adjustment(
        self,
        *,
        total_goals: Dict[str, Any],
        league_code: Optional[str],
        review_learning: Optional[Dict[str, Any]],
        over_under: Optional[Dict[str, Any]] = None,
        match_intelligence: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        diag: Dict[str, Any] = {"applied": False, "signals": []}
        if not isinstance(total_goals, dict) or not total_goals.get("available"):
            diag["reason"] = "unavailable_total_goals"
            return total_goals, diag
        buckets = {str(key): float(value or 0.0) for key, value in (total_goals.get("buckets") or {}).items()}
        if not buckets:
            diag["reason"] = "missing_buckets"
            return total_goals, diag
        learning = review_learning if isinstance(review_learning, dict) else {}
        tg_cfg = self.section("total_goals", league_code)
        ou_bias = learning.get("over_under_bias") if isinstance(learning.get("over_under_bias"), dict) else {}
        line = self._safe_float((over_under or {}).get("line")) if isinstance(over_under, dict) else None
        over_prob = self._safe_float((over_under or {}).get("over")) if isinstance(over_under, dict) else None
        under_prob = self._safe_float((over_under or {}).get("under")) if isinstance(over_under, dict) else None
        line_window = [float(item) for item in (tg_cfg.get("line_window") or [2.5, 3.0, 3.5]) if isinstance(item, (int, float))]
        near_focus_line = bool(line is not None and any(abs(line - focus_line) <= 0.26 for focus_line in line_window))
        base_over_shift = max(float(ou_bias.get("recommended_over_shift") or 0.0), float(tg_cfg.get("base_over_shift") or 0.0))
        base_under_shift = max(float(ou_bias.get("recommended_under_shift") or 0.0), float(tg_cfg.get("base_under_shift") or 0.0))
        motivation_risk = self.extract_motivation_risk(match_intelligence=match_intelligence)
        motivation_bonus = 0.0
        if bool(motivation_risk.get("supports_upset")) and float(motivation_risk.get("score") or 0.0) >= 12.0:
            motivation_bonus = 0.006
        low_bucket_keys = [str(item) for item in (tg_cfg.get("low_bucket_keys") or ["0", "1", "2"])]
        high_bucket_targets = {
            str(key): float(value or 0.0)
            for key, value in (tg_cfg.get("high_bucket_targets") or {"3": 0.5, "4": 0.3, "5": 0.2}).items()
        }
        max_total_shift = float(tg_cfg.get("max_total_shift") or 0.06)
        direction = ""
        shift = 0.0
        if near_focus_line and under_prob is not None and over_prob is not None and under_prob >= over_prob:
            direction = "towards_high"
            shift = min(max_total_shift, base_over_shift + motivation_bonus)
        elif near_focus_line and under_prob is not None and over_prob is not None and over_prob > under_prob and base_under_shift > 0:
            direction = "towards_low"
            shift = min(max_total_shift * 0.6, base_under_shift)
        else:
            diag["reason"] = "review_total_goals_not_triggered"
            return total_goals, diag
        adjusted = dict(buckets)
        if direction == "towards_high":
            source_total = sum(max(0.0, adjusted.get(key, 0.0)) for key in low_bucket_keys)
            actual_shift = min(shift, source_total * 0.35)
            if actual_shift <= 0:
                diag["reason"] = "no_low_bucket_mass"
                return total_goals, diag
            for key in low_bucket_keys:
                source_value = max(0.0, adjusted.get(key, 0.0))
                take = actual_shift * (source_value / source_total) if source_total > 0 else 0.0
                adjusted[key] = max(0.0, source_value - take)
            target_weight_total = sum(max(0.0, value) for value in high_bucket_targets.values()) or 1.0
            for key, weight in high_bucket_targets.items():
                adjusted[key] = max(0.0, adjusted.get(key, 0.0)) + actual_shift * (max(0.0, weight) / target_weight_total)
            diag["signals"].append("review-total-goals-reduce-low-bias")
        else:
            high_keys = [key for key in high_bucket_targets.keys() if key in adjusted]
            source_total = sum(max(0.0, adjusted.get(key, 0.0)) for key in high_keys)
            actual_shift = min(shift, source_total * 0.3)
            if actual_shift <= 0:
                diag["reason"] = "no_high_bucket_mass"
                return total_goals, diag
            for key in high_keys:
                source_value = max(0.0, adjusted.get(key, 0.0))
                take = actual_shift * (source_value / source_total) if source_total > 0 else 0.0
                adjusted[key] = max(0.0, source_value - take)
            low_targets = {"1": 0.5, "2": 0.35, "0": 0.15}
            for key, weight in low_targets.items():
                adjusted[key] = max(0.0, adjusted.get(key, 0.0)) + actual_shift * weight
            diag["signals"].append("review-total-goals-under-protection")
        total_mass = sum(adjusted.values())
        if total_mass > 0:
            for key in list(adjusted.keys()):
                adjusted[key] = adjusted[key] / total_mass
        top_totals = sorted(((key, value) for key, value in adjusted.items()), key=lambda item: item[1], reverse=True)[:3]
        result = dict(total_goals)
        result["buckets"] = {key: round(float(value), 6) for key, value in adjusted.items()}
        result["top_totals"] = [{"total": key, "prob": round(float(value), 4)} for key, value in top_totals]
        diag.update(
            {
                "applied": True,
                "reason": direction,
                "line": round(line, 3) if isinstance(line, float) else None,
                "shift": round(shift, 4),
                "near_focus_line": near_focus_line,
            }
        )
        return result, diag

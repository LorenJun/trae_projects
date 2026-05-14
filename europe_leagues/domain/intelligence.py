"""模块说明：负责比赛画像、战意、风格与市场共振等情报分析。"""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from domain.odds import parse_handicap_value
from domain.writeback import TeamsWritebackGateway
from runtime.cache import PredictionCache


class MatchIntelligenceEngine:
    def __init__(self, base_dir: Optional[str] = None, writeback: Optional[TeamsWritebackGateway] = None, team_ewma_learning: Any = None):
        self.base_dir = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.writeback = writeback or TeamsWritebackGateway(self.base_dir)
        self.team_ewma_learning = team_ewma_learning
        self.cache = PredictionCache()

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
    def _parse_handicap_value(value: Any) -> Optional[float]:
        return parse_handicap_value(value)

    def _safe_match_date(self, date_str: str) -> Optional[datetime]:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d")
        except Exception:
            return None

    def _load_league_table(self, league_code: str) -> Dict[str, Dict[str, Any]]:
        cache_params = {"league_code": league_code}
        cached = self.cache.get("league_table_rows", cache_params, ttl_hours=12)
        if cached:
            return cached
        path = self.writeback.teams_file_path(league_code)
        table: Dict[str, Dict[str, Any]] = {}
        if not os.path.exists(path):
            return table
        try:
            lines = open(path, "r", encoding="utf-8").read().splitlines()
        except Exception:
            return table
        in_table = False
        for line in lines:
            if line.startswith("|") and "排名" in line and "球队" in line and "积分" in line:
                in_table = True
                continue
            if not in_table:
                continue
            if not line.startswith("|"):
                break
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) < 10 or cols[0] == "-----":
                continue
            try:
                rank = int(cols[0])
                team = cols[1]
                table[team] = {
                    "rank": rank,
                    "team": team,
                    "played": int(cols[2]),
                    "wins": int(cols[3]),
                    "draws": int(cols[4]),
                    "losses": int(cols[5]),
                    "gf": int(cols[6]),
                    "ga": int(cols[7]),
                    "gd": int(str(cols[8]).replace("+", "")),
                    "points": int(cols[9]),
                }
            except Exception:
                continue
        self.cache.set("league_table_rows", cache_params, table)
        return table

    def _derive_motivation_profile(self, league_code: str, team_name: str) -> Dict[str, Any]:
        table = self._load_league_table(league_code)
        row = table.get(team_name)
        if not isinstance(row, dict):
            return {
                "available": False,
                "score": 75.0,
                "urgency": 0.45,
                "tier": "unknown",
                "objective": "信息不足",
                "target_zone": "unknown",
                "points_gap_to_objective": None,
                "is_must_take_points": False,
                "reason": "table_unavailable",
                "tags": [],
                "table_row": {},
            }
        total_teams = len(table) or 20
        top4_pts = None
        top1_pts = None
        relegation_cut_pts = None
        for team_row in table.values():
            if not isinstance(team_row, dict):
                continue
            rank = team_row.get("rank")
            pts = team_row.get("points")
            if rank == 1:
                top1_pts = pts
            if rank == 4:
                top4_pts = pts
            if rank == max(1, total_teams - 2):
                relegation_cut_pts = pts

        rank = int(row.get("rank") or total_teams)
        pts = int(row.get("points") or 0)
        score = 72.0
        urgency = 0.48
        tags: List[str] = []
        tier = "balanced"
        objective = "常规拿分"
        target_zone = "mid_table"
        points_gap_to_objective: Optional[int] = None
        is_must_take_points = False

        if top1_pts is not None and rank <= 2 and abs(top1_pts - pts) <= 6:
            urgency += 0.08
            tags.append("争冠窗口")
        if rank <= 4:
            score += 12.0
            urgency += 0.18
            tier = "europe_guard"
            objective = "欧战席位保护"
            target_zone = "top4"
            points_gap_to_objective = pts - int(top4_pts if top4_pts is not None else pts)
            tags.append("欧战席位保护")
        elif top4_pts is not None and abs(top4_pts - pts) <= 6:
            score += 9.0
            urgency += 0.16
            tier = "europe_chase"
            objective = "冲击欧战区"
            target_zone = "top4"
            points_gap_to_objective = pts - int(top4_pts)
            tags.append("冲击欧战区")
        elif rank <= 8:
            score += 4.0
            urgency += 0.05
            tier = "upper_mid"
            objective = "稳固上半区"
            target_zone = "upper_mid"
            tags.append("上半区竞争")

        if relegation_cut_pts is not None:
            relegation_gap = pts - int(relegation_cut_pts)
            if rank >= max(1, total_teams - 2) or relegation_gap <= 0:
                score += 14.0
                urgency += 0.28
                tier = "relegation_battle"
                objective = "保级抢分"
                target_zone = "safety"
                points_gap_to_objective = relegation_gap
                is_must_take_points = True
                tags.append("降级区求分")
            elif relegation_gap <= 6:
                score += 10.0
                urgency += 0.18
                if tier not in {"europe_guard", "europe_chase"}:
                    tier = "relegation_pressure"
                    objective = "保级缓冲"
                    target_zone = "safety"
                    points_gap_to_objective = relegation_gap
                tags.append("保级压力")
        elif rank >= max(1, total_teams - 2):
            score += 14.0
            urgency += 0.28
            tier = "relegation_battle"
            objective = "保级抢分"
            target_zone = "safety"
            is_must_take_points = True
            tags.append("降级区求分")
        if 8 < rank < max(10, total_teams - 4):
            score -= 4.0
            urgency -= 0.12
            if target_zone == "mid_table":
                tier = "mid_table_flat"
                objective = "中游拿分"
            tags.append("中游战意一般")
        if (
            not is_must_take_points
            and tier in {"europe_chase", "relegation_battle", "relegation_pressure"}
            and isinstance(points_gap_to_objective, int)
            and abs(points_gap_to_objective) <= 3
        ):
            is_must_take_points = True
            urgency += 0.06
            tags.append("关键分争夺")
        return {
            "available": True,
            "score": round(max(55.0, min(92.0, score)), 2),
            "urgency": round(max(0.25, min(1.0, urgency)), 4),
            "rank": rank,
            "points": pts,
            "tier": tier,
            "objective": objective,
            "target_zone": target_zone,
            "points_gap_to_objective": points_gap_to_objective,
            "is_must_take_points": is_must_take_points,
            "tags": tags,
            "table_row": row,
        }

    @staticmethod
    def _build_motivation_risk_signal(
        home_motivation: Dict[str, Any],
        away_motivation: Dict[str, Any],
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "available": False,
            "supports_upset": False,
            "score": 0.0,
            "favored_side": "balanced",
            "pressure_side": "balanced",
            "urgency_edge": 0.0,
            "score_edge": 0.0,
            "flags": [],
            "summary": "",
        }
        if not (
            isinstance(home_motivation, dict)
            and isinstance(away_motivation, dict)
            and home_motivation.get("available")
            and away_motivation.get("available")
        ):
            return out

        strength_diff = float(home_strength.get("strength", 0.0)) - float(away_strength.get("strength", 0.0))
        home_rank = int(home_motivation.get("rank") or 99)
        away_rank = int(away_motivation.get("rank") or 99)
        home_score = float(home_motivation.get("score") or 75.0)
        away_score = float(away_motivation.get("score") or 75.0)
        home_tier = str(home_motivation.get("tier") or "").strip()
        away_tier = str(away_motivation.get("tier") or "").strip()
        home_target_zone = str(home_motivation.get("target_zone") or "").strip()
        away_target_zone = str(away_motivation.get("target_zone") or "").strip()
        home_flat = home_tier in {"mid_table_flat", "upper_mid"} or home_target_zone == "mid_table"
        away_flat = away_tier in {"mid_table_flat", "upper_mid"} or away_target_zone == "mid_table"
        home_relegation = home_target_zone == "safety" or home_tier in {"relegation_battle", "relegation_pressure"}
        away_relegation = away_target_zone == "safety" or away_tier in {"relegation_battle", "relegation_pressure"}

        favored_side = "balanced"
        pressure_side = "balanced"
        side_confidence = 0.0
        if abs(strength_diff) >= 4.0:
            favored_side = "home" if strength_diff > 0 else "away"
            pressure_side = "away" if favored_side == "home" else "home"
            side_confidence = min(1.0, abs(strength_diff) / 12.0)
        elif home_relegation and away_flat:
            favored_side = "away"
            pressure_side = "home"
            side_confidence = 0.72
        elif away_relegation and home_flat:
            favored_side = "home"
            pressure_side = "away"
            side_confidence = 0.72
        elif home_motivation.get("is_must_take_points") and not away_motivation.get("is_must_take_points") and away_flat:
            favored_side = "away"
            pressure_side = "home"
            side_confidence = 0.62
        elif away_motivation.get("is_must_take_points") and not home_motivation.get("is_must_take_points") and home_flat:
            favored_side = "home"
            pressure_side = "away"
            side_confidence = 0.62
        elif abs(home_rank - away_rank) >= 5:
            favored_side = "home" if home_rank < away_rank else "away"
            pressure_side = "away" if favored_side == "home" else "home"
            side_confidence = 0.45

        if favored_side not in {"home", "away"} or pressure_side not in {"home", "away"}:
            out["available"] = True
            return out

        favored_profile = home_motivation if favored_side == "home" else away_motivation
        pressure_profile = away_motivation if favored_side == "home" else home_motivation

        favored_urgency = float(favored_profile.get("urgency") or 0.45)
        pressure_urgency = float(pressure_profile.get("urgency") or 0.45)
        urgency_edge = pressure_urgency - favored_urgency
        score_edge = float(pressure_profile.get("score") or 75.0) - float(favored_profile.get("score") or 75.0)
        flags: List[str] = []
        risk_score = 0.0

        if pressure_profile.get("is_must_take_points"):
            risk_score += 0.32
            flags.append("underdog_must_take_points")
        if str(pressure_profile.get("tier") or "").strip() in {"relegation_battle", "relegation_pressure"}:
            risk_score += 0.16
            flags.append("pressure_side_relegation")
        if favored_profile.get("tier") in {"mid_table_flat", "upper_mid"}:
            risk_score += 0.18
            flags.append("favorite_flat_motivation")
            if str(favored_profile.get("tier") or "").strip() == "mid_table_flat":
                risk_score += 0.06
                flags.append("favorite_mid_table_flat")
        if pressure_profile.get("target_zone") == "safety" and favored_profile.get("target_zone") == "mid_table":
            risk_score += 0.12
            flags.append("underdog_survival_vs_favorite_flat")
        if (
            pressure_profile.get("target_zone") == "safety"
            and str(favored_profile.get("tier") or "").strip() in {"mid_table_flat", "upper_mid"}
        ):
            risk_score += 0.14
            flags.append("relegation_vs_mid_table")
        if urgency_edge >= 0.05:
            risk_score += min(0.34, urgency_edge * 1.35)
            flags.append("underdog_high_urgency")
        if score_edge >= 5.0:
            risk_score += min(0.16, score_edge / 60.0)
            flags.append("underdog_motivation_score_advantage")
        if side_confidence < 0.7 and home_relegation != away_relegation:
            risk_score += 0.06
            flags.append("table_context_side_inferred")

        risk_score = round(max(0.0, min(1.0, risk_score)), 4)
        supports_upset = pressure_side != favored_side and risk_score >= 0.16 and (
            len(flags) >= 2 or urgency_edge >= 0.05 or pressure_profile.get("is_must_take_points")
        )
        side_label = {"home": "主队", "away": "客队", "balanced": "两队"}
        summary = ""
        if supports_upset or len(flags) >= 2:
            summary = (
                f"{side_label.get(pressure_side, '弱势方')}抢分战意强于"
                f"{side_label.get(favored_side, '热门方')}，需防热门方兑现不足"
            )

        out.update(
            {
                "available": True,
                "supports_upset": supports_upset,
                "score": risk_score,
                "favored_side": favored_side,
                "pressure_side": pressure_side,
                "urgency_edge": round(urgency_edge, 4),
                "score_edge": round(score_edge, 2),
                "flags": flags,
                "summary": summary,
            }
        )
        return out

    @staticmethod
    def _derive_recent_form_volatility(ewma_features: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        rows = ewma_features.get("recent_matches") if isinstance(ewma_features, dict) else None
        if not isinstance(rows, list) or len(rows) < 3:
            return {"available": False, "score": 0.0, "label": "unknown", "reason": "insufficient_recent_matches"}

        points: List[float] = []
        goal_diffs: List[float] = []
        results: List[str] = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            team_goals = float(row.get("team_goals") or 0.0)
            opp_goals = float(row.get("opp_goals") or 0.0)
            point = float(row.get("points") or 0.0)
            points.append(point)
            goal_diffs.append(team_goals - opp_goals)
            if point >= 2.99:
                results.append("W")
            elif point <= 0.01:
                results.append("L")
            else:
                results.append("D")
        if len(points) < 3:
            return {"available": False, "score": 0.0, "label": "unknown", "reason": "invalid_recent_matches"}

        point_swings = [abs(points[idx] - points[idx + 1]) for idx in range(len(points) - 1)]
        goal_swings = [abs(goal_diffs[idx] - goal_diffs[idx + 1]) for idx in range(len(goal_diffs) - 1)]
        result_changes = sum(1 for idx in range(len(results) - 1) if results[idx] != results[idx + 1])
        point_swing = (sum(point_swings) / len(point_swings)) / 3.0 if point_swings else 0.0
        goal_swing = (sum(goal_swings) / len(goal_swings)) / 3.0 if goal_swings else 0.0
        result_change_rate = result_changes / max(1, len(results) - 1)
        score = max(0.0, min(1.0, point_swing * 0.48 + goal_swing * 0.27 + result_change_rate * 0.25))
        if score >= 0.52:
            label = "high"
        elif score >= 0.32:
            label = "medium"
        else:
            label = "low"
        return {
            "available": True,
            "score": round(score, 4),
            "label": label,
            "point_swing": round(point_swing, 4),
            "goal_swing": round(goal_swing, 4),
            "result_change_rate": round(result_change_rate, 4),
        }

    @staticmethod
    def _derive_contextual_rule_adjustments(
        league_code: str,
        home_motivation: Dict[str, Any],
        away_motivation: Dict[str, Any],
        home_volatility: Dict[str, Any],
        away_volatility: Dict[str, Any],
        total_teams: int,
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "home_delta": 0.0,
            "away_delta": 0.0,
            "draw_delta": 0.0,
            "signals": [],
            "scenario_tags": [],
        }
        total_teams = max(18, int(total_teams or 20))
        home_row = home_motivation.get("table_row") if isinstance(home_motivation.get("table_row"), dict) else {}
        away_row = away_motivation.get("table_row") if isinstance(away_motivation.get("table_row"), dict) else {}
        home_rank = int(home_row.get("rank") or total_teams)
        away_rank = int(away_row.get("rank") or total_teams)
        home_score = float(home_motivation.get("score") or 75.0)
        away_score = float(away_motivation.get("score") or 75.0)
        motivation_edge = home_score - away_score
        home_tags = set(home_motivation.get("tags") or [])
        away_tags = set(away_motivation.get("tags") or [])

        if league_code == "la_liga" and 8 <= home_rank <= 14 and 7 <= away_rank <= 15 and abs(motivation_edge) <= 8.0:
            out["home_delta"] -= 0.028
            out["draw_delta"] += 0.012
            out["scenario_tags"].append("la_liga_mid_table_home_flat")
            out["signals"].append("西甲中游球队主场优势不明显，压缩主场偏置")

        if league_code == "ligue_1" and home_rank >= max(12, total_teams - 8) and away_rank <= 6:
            out["scenario_tags"].append("ligue_1_home_vs_strong_motivation_check")
            if motivation_edge >= 6.0 or "保级压力" in home_tags or "降级区求分" in home_tags:
                out["home_delta"] += 0.01
                out["draw_delta"] += 0.008
                out["signals"].append("法甲中下游主场对强队需结合战意，保留主队抢分弹性")
            else:
                out["home_delta"] -= 0.018
                out["away_delta"] += 0.006
                out["draw_delta"] += 0.01
                out["signals"].append("法甲中下游主场对强队战意不足，压缩主场偏置")

        if league_code == "premier_league" and (
            home_rank >= max(18, total_teams - 2)
            or "保级压力" in home_tags
            or "降级区求分" in home_tags
        ) and 9 <= away_rank <= 15:
            out["home_delta"] += 0.024
            out["draw_delta"] -= 0.006
            out["scenario_tags"].append("premier_league_relegation_home_motivation_bonus")
            out["signals"].append("英超保级队主场战意优势加权")

        if league_code in {"la_liga", "ligue_1"} and motivation_edge <= 4.0 and away_rank < home_rank:
            out["home_delta"] -= 0.01
            out["draw_delta"] += 0.004
            out["signals"].append("主场优势统一先验偏高，按联赛层级收缩")

        for side, volatility, current_score in (
            ("home", home_volatility, home_score),
            ("away", away_volatility, away_score),
        ):
            if not isinstance(volatility, dict) or not volatility.get("available"):
                continue
            if float(volatility.get("score") or 0.0) < 0.52:
                continue
            penalty = min(0.018, 0.01 + (float(volatility.get("score") or 0.0) - 0.52) * 0.04)
            key = f"{side}_delta"
            out[key] -= penalty
            out["draw_delta"] += min(0.01, penalty * 0.55)
            out["scenario_tags"].append(f"recent_form_{side}_volatility_high")
            if current_score >= 73.0:
                out["signals"].append(f"{'主队' if side == 'home' else '客队'}近期状态波动较大，削弱单边兑现预期")

        if (
            isinstance(home_volatility, dict)
            and isinstance(away_volatility, dict)
            and home_volatility.get("available")
            and away_volatility.get("available")
            and float(home_volatility.get("score") or 0.0) >= 0.45
            and float(away_volatility.get("score") or 0.0) >= 0.45
        ):
            out["draw_delta"] += 0.006
            out["scenario_tags"].append("recent_form_volatility_high")
            out["signals"].append("双方近期状态波动较大，提升平局与冷门容错")

        out["home_delta"] = round(max(-0.06, min(0.04, out["home_delta"])), 4)
        out["away_delta"] = round(max(-0.04, min(0.04, out["away_delta"])), 4)
        out["draw_delta"] = round(max(-0.02, min(0.03, out["draw_delta"])), 4)
        return out

    def _derive_h2h_context(self, league_code: str, home_team: str, away_team: str, match_date: str, limit: int = 6) -> Dict[str, Any]:
        cache_params = {
            "league_code": league_code,
            "home_team": home_team,
            "away_team": away_team,
            "match_date": match_date,
            "limit": limit,
        }
        cached = self.cache.get("match_h2h_context", cache_params, ttl_hours=12)
        if cached:
            return cached
        path = self.writeback.teams_file_path(league_code)
        cutoff = self._safe_match_date(match_date)
        out = {"available": False, "home_wins": 0, "away_wins": 0, "draws": 0, "matches": []}
        if not os.path.exists(path) or not cutoff:
            return out
        try:
            lines = open(path, "r", encoding="utf-8").read().splitlines()
        except Exception:
            return out
        rows: List[Dict[str, Any]] = []
        for line in lines:
            if not line.startswith("| 20"):
                continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) < 6:
                continue
            date_str, time_str, home, score, away, remark = cols[:6]
            dt = self._safe_match_date(date_str)
            if not dt or dt >= cutoff or score == "-" or "进行中" in remark:
                continue
            if not ((home == home_team and away == away_team) or (home == away_team and away == home_team)):
                continue
            m = re.match(r"^(\d+)-(\d+)$", score)
            if not m:
                continue
            hg, ag = int(m.group(1)), int(m.group(2))
            winner = "draw"
            if hg > ag:
                winner = "home" if home == home_team else "away"
            elif hg < ag:
                winner = "away" if home == home_team else "home"
            rows.append({"date": date_str, "time": time_str, "home": home, "away": away, "score": score, "winner": winner})
        rows.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
        rows = rows[:limit]
        for row in rows:
            if row["winner"] == "home":
                out["home_wins"] += 1
            elif row["winner"] == "away":
                out["away_wins"] += 1
            else:
                out["draws"] += 1
        out["available"] = bool(rows)
        out["matches"] = rows
        self.cache.set("match_h2h_context", cache_params, out)
        return out

    @staticmethod
    def _formation_style(formation: str) -> Dict[str, Any]:
        form = str(formation or "").strip()
        if not form:
            return {"formation": "", "style": "unknown", "score": 0.0}
        nums = [int(x) for x in re.findall(r"\d+", form)]
        if not nums:
            return {"formation": form, "style": "unknown", "score": 0.0}
        defenders = nums[0]
        attackers = nums[-1]
        midfielders = sum(nums[1:-1]) if len(nums) > 2 else 0
        score = (attackers - defenders) * 0.18 + (midfielders - 3) * 0.06
        if defenders >= 5:
            style = "defensive"
        elif attackers >= 3 and defenders <= 4:
            style = "attacking"
        else:
            style = "balanced"
        return {"formation": form, "style": style, "score": round(score, 3)}

    @staticmethod
    def _avg_key_player_rating(side_ctx: Dict[str, Any]) -> Optional[float]:
        if not isinstance(side_ctx, dict):
            return None
        players = side_ctx.get("key_players")
        if not isinstance(players, list) or not players:
            return None
        vals = []
        for p in players[:3]:
            if not isinstance(p, dict):
                continue
            try:
                vals.append(float(p.get("avg_rating")))
            except Exception:
                continue
        return (sum(vals) / len(vals)) if vals else None

    def _build_market_intelligence(self, current_odds: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "available": False,
            "signals": [],
            "bookmaker_psychology": {"label": "unknown", "reason": "", "trap_side": "none", "strength": 0.0},
            "capital_flow": {"source": "odds_water_kelly_proxy", "home": 0.0, "draw": 0.0, "away": 0.0, "direction": "balanced", "strength": 0.0},
            "market_resonance": {
                "available": False,
                "label": "unknown",
                "summary": "",
                "side_direction": "balanced",
                "side_strength": 0.0,
                "tempo_direction": "balanced",
                "tempo_strength": 0.0,
                "resonance_score": 0.0,
                "flags": [],
                "components": {},
            },
        }
        if not isinstance(current_odds, dict):
            return out
        euro = current_odds.get("欧赔") or {}
        asian = current_odds.get("亚值") or {}
        kelly = current_odds.get("凯利") or {}
        totals = current_odds.get("大小球") or {}

        def pick(block: Any, stage: str, key: str) -> Optional[float]:
            if not isinstance(block, dict):
                return None
            stage_block = block.get(stage)
            if not isinstance(stage_block, dict):
                return None
            return self._to_float(stage_block.get(key))

        def move_score(initial: Optional[float], final: Optional[float]) -> float:
            if not initial or not final or initial <= 0:
                return 0.0
            return max(-0.18, min(0.18, (initial - final) / initial))

        def direction_from_scores(score_map: Dict[str, float], min_top: float = 0.015, min_gap: float = 0.012) -> tuple[str, float, Dict[str, float]]:
            ranked_local = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
            top_name_local, top_val_local = ranked_local[0]
            second_val_local = ranked_local[1][1]
            direction_local = top_name_local if top_val_local > min_top and (top_val_local - second_val_local) > min_gap else "balanced"
            strength_local = round(max(0.0, top_val_local - second_val_local), 4)
            return direction_local, strength_local, {k: round(float(v), 4) for k, v in score_map.items()}

        home_score = move_score(pick(euro, "initial", "home"), pick(euro, "final", "home"))
        draw_score = move_score(pick(euro, "initial", "draw"), pick(euro, "final", "draw"))
        away_score = move_score(pick(euro, "initial", "away"), pick(euro, "final", "away"))

        h_water_i = pick(asian, "initial", "home_water")
        h_water_f = pick(asian, "final", "home_water")
        a_water_i = pick(asian, "initial", "away_water")
        a_water_f = pick(asian, "final", "away_water")
        if h_water_i is not None and h_water_f is not None:
            home_score += max(-0.08, min(0.08, (h_water_i - h_water_f) * 0.25))
        if a_water_i is not None and a_water_f is not None:
            away_score += max(-0.08, min(0.08, (a_water_i - a_water_f) * 0.25))

        k_h = pick(kelly, "final", "home")
        k_d = pick(kelly, "final", "draw")
        k_a = pick(kelly, "final", "away")
        kelly_vals = [x for x in [k_h, k_d, k_a] if isinstance(x, float)]
        if len(kelly_vals) == 3:
            avg_k = sum(kelly_vals) / 3.0
            home_score += max(-0.05, min(0.05, (avg_k - k_h) * 0.35))
            draw_score += max(-0.05, min(0.05, (avg_k - k_d) * 0.35))
            away_score += max(-0.05, min(0.05, (avg_k - k_a) * 0.35))

        signals: List[str] = []
        trap_side = "none"
        trap_strength = 0.0
        if home_score >= 0.05:
            signals.append("主胜赔率/水位走强")
        if away_score >= 0.05:
            signals.append("客胜赔率/水位走强")
        if draw_score >= 0.045:
            signals.append("平局赔率受压")
        if home_score > 0.03 and h_water_f is not None and h_water_i is not None and h_water_f > h_water_i:
            signals.append("主队赔率下降但主水升高，疑似诱主")
            trap_side = "home"
            trap_strength = max(trap_strength, min(0.12, 0.045 + max(0.0, h_water_f - h_water_i) * 0.18))
        if away_score > 0.03 and a_water_f is not None and a_water_i is not None and a_water_f > a_water_i:
            signals.append("客队赔率下降但客水升高，疑似诱客")
            trap_side = "away"
            trap_strength = max(trap_strength, min(0.12, 0.045 + max(0.0, a_water_f - a_water_i) * 0.18))
        if draw_score > max(home_score, away_score) and draw_score >= 0.04:
            signals.append("庄家有防平倾向")

        scores = {"home": round(home_score, 4), "draw": round(draw_score, 4), "away": round(away_score, 4)}
        direction, strength, _ = direction_from_scores(scores)
        label = "balanced"
        reason = "盘口与水位分歧不大"

        # 进一步做三盘口共振检测：欧赔(1X2) + 亚值(方向/深度/水位) + 大小球(节奏)
        euro_direction = direction
        euro_strength = strength

        asian_direction = "balanced"
        asian_strength = 0.0
        asian_scores = {"home": 0.0, "draw": 0.0, "away": 0.0}
        hcp_i_raw = None
        hcp_f_raw = None
        if isinstance(asian, dict):
            ini_as = asian.get("initial") if isinstance(asian.get("initial"), dict) else {}
            fin_as = asian.get("final") if isinstance(asian.get("final"), dict) else {}
            hcp_i_raw = self._parse_handicap_value(
                ini_as.get("handicap")
                if "handicap" in ini_as
                else ini_as.get("handicap_value")
                if "handicap_value" in ini_as
                else ini_as.get("盘口值")
                if "盘口值" in ini_as
                else ini_as.get("handicap_text")
            )
            hcp_f_raw = self._parse_handicap_value(
                fin_as.get("handicap")
                if "handicap" in fin_as
                else fin_as.get("handicap_value")
                if "handicap_value" in fin_as
                else fin_as.get("盘口值")
                if "盘口值" in fin_as
                else fin_as.get("handicap_text")
            )
            h_mag = abs(hcp_f_raw) if hcp_f_raw is not None else 0.0
            if hcp_f_raw is not None:
                if hcp_f_raw < -0.06:
                    asian_scores["home"] += min(0.22, 0.06 + h_mag * 0.13)
                elif hcp_f_raw > 0.06:
                    asian_scores["away"] += min(0.22, 0.06 + h_mag * 0.13)
                else:
                    asian_scores["draw"] += 0.06
            if h_water_i is not None and h_water_f is not None:
                asian_scores["home"] += max(-0.08, min(0.08, (h_water_i - h_water_f) * 0.28))
            if a_water_i is not None and a_water_f is not None:
                asian_scores["away"] += max(-0.08, min(0.08, (a_water_i - a_water_f) * 0.28))
            if hcp_i_raw is not None and hcp_f_raw is not None:
                # abs smaller => retreat; abs larger => stronger support
                delta_hcp = abs(hcp_i_raw) - abs(hcp_f_raw)
                if delta_hcp >= 0.24:
                    asian_scores["draw"] += 0.05
                elif delta_hcp <= -0.24:
                    if hcp_f_raw < -0.06:
                        asian_scores["home"] += 0.04
                    elif hcp_f_raw > 0.06:
                        asian_scores["away"] += 0.04
                if hcp_f_raw < -0.06 and home_score > 0.04 and delta_hcp >= 0.24:
                    signals.append("主队热度升高但亚盘退让，疑似诱主")
                    trap_side = "home"
                    trap_strength = max(trap_strength, min(0.18, 0.06 + delta_hcp * 0.18))
                if hcp_f_raw > 0.06 and away_score > 0.04 and delta_hcp >= 0.24:
                    signals.append("客队热度升高但亚盘退让，疑似诱客")
                    trap_side = "away"
                    trap_strength = max(trap_strength, min(0.18, 0.06 + delta_hcp * 0.18))
            if hcp_f_raw is not None and hcp_f_raw < -0.06 and h_water_i is not None and h_water_f is not None and home_score > 0.04 and (h_water_f - h_water_i) >= 0.05:
                signals.append("主队热门拉低但主水同步抬升，诱主风险增强")
                trap_side = "home"
                trap_strength = max(trap_strength, min(0.16, 0.055 + (h_water_f - h_water_i) * 0.22))
            if hcp_f_raw is not None and hcp_f_raw > 0.06 and a_water_i is not None and a_water_f is not None and away_score > 0.04 and (a_water_f - a_water_i) >= 0.05:
                signals.append("客队热门拉低但客水同步抬升，诱客风险增强")
                trap_side = "away"
                trap_strength = max(trap_strength, min(0.16, 0.055 + (a_water_f - a_water_i) * 0.22))
            asian_direction, asian_strength, _ = direction_from_scores(asian_scores, min_top=0.03, min_gap=0.018)

        if "庄家有防平倾向" in signals:
            label = "guard_draw"
            reason = "平赔压力和凯利/水位组合显示机构防平"
        elif trap_side == "home" and trap_strength >= 0.05:
            label = "tempt_home"
            reason = "主队热度上升与亚盘/水位结构不一致，存在诱主可能"
        elif trap_side == "away" and trap_strength >= 0.05:
            label = "tempt_away"
            reason = "客队热度上升与亚盘/水位结构不一致，存在诱客可能"
        elif direction == "home":
            label = "support_home"
            reason = "欧赔、亚水和凯利更偏向主队"
        elif direction == "away":
            label = "support_away"
            reason = "欧赔、亚水和凯利更偏向客队"

        totals_direction = "balanced"
        totals_strength = 0.0
        totals_scores = {"over": 0.0, "under": 0.0}
        if isinstance(totals, dict):
            ini_ou = totals.get("initial") if isinstance(totals.get("initial"), dict) else {}
            fin_ou = totals.get("final") if isinstance(totals.get("final"), dict) else {}
            ou_i = self._to_float(ini_ou.get("line"))
            ou_f = self._to_float(fin_ou.get("line"))
            over_i = self._to_float(ini_ou.get("over"))
            over_f = self._to_float(fin_ou.get("over"))
            under_i = self._to_float(ini_ou.get("under"))
            under_f = self._to_float(fin_ou.get("under"))
            if ou_i is not None and ou_f is not None:
                if ou_f - ou_i >= 0.24:
                    totals_scores["over"] += 0.14
                elif ou_i - ou_f >= 0.24:
                    totals_scores["under"] += 0.14
            if over_i is not None and over_f is not None:
                totals_scores["over"] += max(-0.08, min(0.08, (over_i - over_f) * 0.30))
            if under_i is not None and under_f is not None:
                totals_scores["under"] += max(-0.08, min(0.08, (under_i - under_f) * 0.30))
            ranked_ou = sorted(totals_scores.items(), key=lambda x: x[1], reverse=True)
            top_ou, top_ou_val = ranked_ou[0]
            sec_ou_val = ranked_ou[1][1]
            totals_direction = top_ou if top_ou_val > 0.03 and (top_ou_val - sec_ou_val) > 0.015 else "balanced"
            totals_strength = round(max(0.0, top_ou_val - sec_ou_val), 4)

        side_votes = [d for d in [euro_direction, asian_direction, direction] if d in {"home", "away", "draw"}]
        resonance_flags: List[str] = []
        side_direction = "balanced"
        side_strength = 0.0
        if side_votes:
            home_votes = side_votes.count("home")
            away_votes = side_votes.count("away")
            draw_votes = side_votes.count("draw")
            if home_votes >= 2 and home_votes > max(away_votes, draw_votes):
                side_direction = "home"
                side_strength = round(0.12 + euro_strength + asian_strength + strength, 4)
                resonance_flags.append("home_side_resonance")
            elif away_votes >= 2 and away_votes > max(home_votes, draw_votes):
                side_direction = "away"
                side_strength = round(0.12 + euro_strength + asian_strength + strength, 4)
                resonance_flags.append("away_side_resonance")
            elif draw_votes >= 2 and draw_votes >= max(home_votes, away_votes):
                side_direction = "draw"
                side_strength = round(0.10 + draw_score, 4)
                resonance_flags.append("draw_guard_resonance")

        if euro_direction in {"home", "away"} and asian_direction in {"home", "away"} and euro_direction != asian_direction:
            resonance_flags.append("euro_asian_divergence")
        if side_direction == "home" and totals_direction == "over":
            resonance_flags.append("home_over_resonance")
        elif side_direction == "home" and totals_direction == "under":
            resonance_flags.append("home_under_resonance")
        elif side_direction == "away" and totals_direction == "over":
            resonance_flags.append("away_over_resonance")
        elif side_direction == "away" and totals_direction == "under":
            resonance_flags.append("away_under_resonance")
        elif side_direction == "draw" and totals_direction == "under":
            resonance_flags.append("draw_under_resonance")

        resonance_label = "balanced"
        resonance_summary = "三盘口暂未形成清晰共振"
        resonance_score = round(max(0.0, side_strength + totals_strength), 4)
        if "euro_asian_divergence" in resonance_flags:
            resonance_label = "divergence"
            resonance_summary = "欧赔与亚值方向不完全一致，市场存在背离"
        elif side_direction == "home" and totals_direction == "over":
            resonance_label = "home_over_resonance"
            resonance_summary = "欧赔、亚值与大小球共同偏向主队主动进攻路径"
        elif side_direction == "home" and totals_direction == "under":
            resonance_label = "home_under_resonance"
            resonance_summary = "欧赔、亚值支持主队，但总进球预期偏谨慎"
        elif side_direction == "away" and totals_direction == "over":
            resonance_label = "away_over_resonance"
            resonance_summary = "市场共振更偏向客队主动拿结果且比赛节奏偏开放"
        elif side_direction == "away" and totals_direction == "under":
            resonance_label = "away_under_resonance"
            resonance_summary = "市场更偏客队不败或偷走结果，且比赛节奏偏谨慎"
        elif side_direction == "draw":
            resonance_label = "draw_guard"
            resonance_summary = "盘口结构更偏防平，主客胜并未形成单边共振"
        elif side_direction == "home":
            resonance_label = "home_side_resonance"
            resonance_summary = "欧赔与亚值整体更偏主队一侧"
        elif side_direction == "away":
            resonance_label = "away_side_resonance"
            resonance_summary = "欧赔与亚值整体更偏客队一侧"

        if label == "tempt_home" and side_direction == "home":
            resonance_flags.append("home_hot_trap_risk")
            resonance_label = "tempt_home_resonance"
            resonance_summary = "主队方向虽有共振，但赔付结构提示诱主风险"
        elif label == "tempt_away" and side_direction == "away":
            resonance_flags.append("away_hot_trap_risk")
            resonance_label = "tempt_away_resonance"
            resonance_summary = "客队方向虽有共振，但赔付结构提示诱客风险"
        if label == "guard_draw":
            resonance_flags.append("draw_trap_protection")
        if resonance_label != "balanced":
            signals.append(f"三盘口画像:{resonance_label}")

        out.update(
            {
                "available": bool(signals or any(abs(v) > 0.0 for v in scores.values())),
                "signals": signals,
                "bookmaker_psychology": {
                    "label": label,
                    "reason": reason,
                    "trap_side": trap_side,
                    "strength": round(trap_strength, 4),
                },
                "capital_flow": {
                    "source": "odds_water_kelly_proxy",
                    "home": scores["home"],
                    "draw": scores["draw"],
                    "away": scores["away"],
                    "direction": direction,
                    "strength": strength,
                },
                "raw": {"euro": euro, "asian": asian, "totals": totals, "kelly": kelly},
                "market_resonance": {
                    "available": True,
                    "label": resonance_label,
                    "summary": resonance_summary,
                    "side_direction": side_direction,
                    "side_strength": round(side_strength, 4),
                    "tempo_direction": totals_direction,
                    "tempo_strength": round(totals_strength, 4),
                    "resonance_score": resonance_score,
                    "flags": resonance_flags,
                    "components": {
                        "euro": {"direction": euro_direction, "strength": round(euro_strength, 4), "scores": scores},
                        "asian": {
                            "direction": asian_direction,
                            "strength": round(asian_strength, 4),
                            "scores": {k: round(float(v), 4) for k, v in asian_scores.items()},
                            "initial_handicap": hcp_i_raw,
                            "final_handicap": hcp_f_raw,
                        },
                        "totals": {
                            "direction": totals_direction,
                            "strength": round(totals_strength, 4),
                            "scores": {k: round(float(v), 4) for k, v in totals_scores.items()},
                        },
                    },
                },
            }
        )
        return out


    def _build_match_intelligence(
        self,
        league_code: str,
        home_team: str,
        away_team: str,
        match_date: str,
        analysis_context: Dict[str, Any],
        current_odds: Optional[Dict[str, Any]],
        home_strength: Dict[str, Any],
        away_strength: Dict[str, Any],
    ) -> Dict[str, Any]:
        league_table = self._load_league_table(league_code)
        team_ctx = analysis_context.get("team_context") if isinstance(analysis_context.get("team_context"), dict) else {}
        home_ctx = team_ctx.get("home") if isinstance(team_ctx.get("home"), dict) else {}
        away_ctx = team_ctx.get("away") if isinstance(team_ctx.get("away"), dict) else {}
        home_ewma = self.team_ewma_learning.get_team_ewma_features(league_code, home_team, match_date)
        away_ewma = self.team_ewma_learning.get_team_ewma_features(league_code, away_team, match_date)
        h2h = self._derive_h2h_context(league_code, home_team, away_team, match_date)
        home_mot = self._derive_motivation_profile(league_code, home_team)
        away_mot = self._derive_motivation_profile(league_code, away_team)
        market = self._build_market_intelligence(current_odds)

        home_formations = home_ctx.get("formations") if isinstance(home_ctx.get("formations"), list) else []
        away_formations = away_ctx.get("formations") if isinstance(away_ctx.get("formations"), list) else []
        home_shape = self._formation_style(home_formations[0].get("formation") if home_formations else "")
        away_shape = self._formation_style(away_formations[0].get("formation") if away_formations else "")

        home_poss = self._to_float(home_ctx.get("avg_possession"))
        away_poss = self._to_float(away_ctx.get("avg_possession"))
        home_rating = self._avg_key_player_rating(home_ctx)
        away_rating = self._avg_key_player_rating(away_ctx)
        recent_home = home_ctx.get("recent") if isinstance(home_ctx.get("recent"), dict) else {}
        recent_away = away_ctx.get("recent") if isinstance(away_ctx.get("recent"), dict) else {}
        home_ppg = (float(recent_home.get("points") or 0.0) / max(1.0, float(recent_home.get("matches") or 1.0))) if recent_home else 0.0
        away_ppg = (float(recent_away.get("points") or 0.0) / max(1.0, float(recent_away.get("matches") or 1.0))) if recent_away else 0.0

        home_adv = 0.0
        away_adv = 0.0
        draw_bias = 0.0
        signals: List[str] = []
        home_volatility = self._derive_recent_form_volatility(home_ewma)
        away_volatility = self._derive_recent_form_volatility(away_ewma)
        contextual_rules = self._derive_contextual_rule_adjustments(
            league_code=league_code,
            home_motivation=home_mot,
            away_motivation=away_mot,
            home_volatility=home_volatility,
            away_volatility=away_volatility,
            total_teams=len(league_table) or 20,
        )
        scenario_tags = list(contextual_rules.get("scenario_tags") or [])
        motivation_risk = self._build_motivation_risk_signal(
            home_motivation=home_mot,
            away_motivation=away_mot,
            home_strength=home_strength,
            away_strength=away_strength,
        )
        motivation_context_flags: List[str] = []
        if home_mot.get("is_must_take_points"):
            motivation_context_flags.append("home_must_take_points")
        if away_mot.get("is_must_take_points"):
            motivation_context_flags.append("away_must_take_points")
        for flag in motivation_risk.get("flags") or []:
            if flag not in motivation_context_flags:
                motivation_context_flags.append(str(flag))
        if contextual_rules.get("signals"):
            signals.extend(contextual_rules["signals"])
        if home_poss is not None and away_poss is not None:
            poss_edge = (home_poss - away_poss) / 100.0
            home_adv += poss_edge * 0.30
            away_adv -= poss_edge * 0.30
            if abs(home_poss - away_poss) >= 4.5:
                signals.append(f"控球倾向 {home_team}{home_poss:.1f}% vs {away_team}{away_poss:.1f}%")
        if home_rating is not None and away_rating is not None:
            rating_edge = home_rating - away_rating
            home_adv += rating_edge * 0.08
            away_adv -= rating_edge * 0.08
        if home_ppg or away_ppg:
            form_edge = home_ppg - away_ppg
            home_adv += form_edge * 0.06
            away_adv -= form_edge * 0.06
        injury_edge = (float(away_strength.get("injured_count", 0)) - float(home_strength.get("injured_count", 0))) / 10.0
        home_adv += injury_edge * 0.12
        away_adv -= injury_edge * 0.12
        mot_edge = (home_mot.get("score", 75.0) - away_mot.get("score", 75.0)) / 100.0
        home_adv += mot_edge * 0.20
        away_adv -= mot_edge * 0.20
        if motivation_risk.get("supports_upset"):
            bias = min(0.018, float(motivation_risk.get("score") or 0.0) * 0.045)
            favored_side = str(motivation_risk.get("favored_side") or "balanced")
            if favored_side == "home":
                home_adv -= bias
                away_adv += bias * 0.55
                draw_bias += bias * 0.45
            elif favored_side == "away":
                away_adv -= bias
                home_adv += bias * 0.55
                draw_bias += bias * 0.45
            scenario_tag = f"motivation_upset_risk_{motivation_risk.get('pressure_side')}"
            if scenario_tag not in scenario_tags:
                scenario_tags.append(scenario_tag)
            summary = str(motivation_risk.get("summary") or "").strip()
            if summary:
                signals.append(f"战意差异: {summary}")
        home_adv += float(contextual_rules.get("home_delta") or 0.0)
        away_adv += float(contextual_rules.get("away_delta") or 0.0)
        draw_bias += float(contextual_rules.get("draw_delta") or 0.0)
        if h2h.get("available"):
            total_h2h = max(1, int(h2h.get("home_wins", 0)) + int(h2h.get("away_wins", 0)) + int(h2h.get("draws", 0)))
            h2h_edge = (int(h2h.get("home_wins", 0)) - int(h2h.get("away_wins", 0))) / total_h2h
            home_adv += h2h_edge * 0.06
            away_adv -= h2h_edge * 0.06
        if home_shape.get("style") == "attacking" and away_shape.get("style") == "defensive":
            signals.append(f"{home_team}阵型更主动，{away_team}偏收缩")
        if away_shape.get("style") == "attacking" and home_shape.get("style") == "defensive":
            signals.append(f"{away_team}阵型更主动，{home_team}偏收缩")

        capital_flow = market.get("capital_flow", {})
        flow_dir = capital_flow.get("direction")
        flow_strength = float(capital_flow.get("strength") or 0.0)
        psychology = market.get("bookmaker_psychology", {}) if isinstance(market.get("bookmaker_psychology"), dict) else {}
        psychology_label = str(psychology.get("label") or "unknown")
        trap_side = str(psychology.get("trap_side") or "none")
        trap_strength = float(psychology.get("strength") or 0.0)
        flow_scale = 1.0
        if trap_side == flow_dir and trap_strength > 0:
            flow_scale = max(0.35, 1.0 - trap_strength * 3.2)
        if flow_dir == "home":
            home_adv += min(0.06, flow_strength * 1.2 * flow_scale)
            away_adv -= min(0.04, flow_strength * 0.8 * flow_scale)
            if flow_scale < 0.95:
                signals.append("主队资金热度疑似受诱盘放大，已降权处理")
            else:
                signals.append("资金流向代理偏主队")
        elif flow_dir == "away":
            away_adv += min(0.06, flow_strength * 1.2 * flow_scale)
            home_adv -= min(0.04, flow_strength * 0.8 * flow_scale)
            if flow_scale < 0.95:
                signals.append("客队资金热度疑似受诱盘放大，已降权处理")
            else:
                signals.append("资金流向代理偏客队")
        elif flow_dir == "draw":
            signals.append("资金流向代理偏平局")

        home_adv = max(-0.12, min(0.12, home_adv))
        away_adv = max(-0.12, min(0.12, away_adv))
        if psychology_label == "guard_draw":
            draw_bias += 0.018
        if flow_dir == "draw":
            draw_bias += min(0.024, flow_strength * 1.5)
        if psychology_label == "tempt_home":
            home_adv -= min(0.03, 0.01 + trap_strength * 0.22)
            away_adv += min(0.016, 0.004 + trap_strength * 0.10)
            draw_bias += min(0.02, 0.006 + trap_strength * 0.12)
            signals.append("庄家心理识别为诱主，抑制主队方向过热")
        elif psychology_label == "tempt_away":
            away_adv -= min(0.03, 0.01 + trap_strength * 0.22)
            home_adv += min(0.016, 0.004 + trap_strength * 0.10)
            draw_bias += min(0.02, 0.006 + trap_strength * 0.12)
            signals.append("庄家心理识别为诱客，抑制客队方向过热")
        resonance = market.get("market_resonance") if isinstance(market.get("market_resonance"), dict) else {}
        resonance_label = str(resonance.get("label") or "balanced")
        resonance_score = float(resonance.get("resonance_score") or 0.0)
        resonance_prob_delta_home = 0.0
        resonance_prob_delta_away = 0.0
        resonance_prob_delta_draw = 0.0
        resonance_ou_delta = 0.0
        resonance_signals: List[str] = []
        if resonance_label == "home_over_resonance":
            home_adv += 0.018
            away_adv -= 0.006
            draw_bias -= 0.006
            resonance_prob_delta_home += 0.012
            resonance_prob_delta_draw -= 0.006
            resonance_ou_delta += 0.032
            resonance_signals.append("三盘口共振支持主队开放局")
        elif resonance_label == "home_under_resonance":
            home_adv += 0.014
            away_adv -= 0.004
            draw_bias += 0.008
            resonance_prob_delta_home += 0.008
            resonance_prob_delta_draw += 0.006
            resonance_ou_delta -= 0.03
            resonance_signals.append("三盘口共振支持主队谨慎兑现")
        elif resonance_label == "away_over_resonance":
            away_adv += 0.018
            home_adv -= 0.006
            draw_bias -= 0.006
            resonance_prob_delta_away += 0.012
            resonance_prob_delta_draw -= 0.006
            resonance_ou_delta += 0.032
            resonance_signals.append("三盘口共振支持客队开放局")
        elif resonance_label == "away_under_resonance":
            away_adv += 0.014
            home_adv -= 0.004
            draw_bias += 0.008
            resonance_prob_delta_away += 0.008
            resonance_prob_delta_draw += 0.006
            resonance_ou_delta -= 0.03
            resonance_signals.append("三盘口共振支持客队谨慎兑现")
        elif resonance_label == "draw_guard":
            home_adv *= 0.95
            away_adv *= 0.95
            draw_bias += 0.012
            resonance_prob_delta_draw += 0.012
            resonance_ou_delta -= 0.012
            resonance_signals.append("三盘口共振偏防平")
        elif resonance_label == "divergence":
            home_adv *= 0.92
            away_adv *= 0.92
            draw_bias += 0.01
            resonance_prob_delta_draw += 0.01
            resonance_signals.append("三盘口背离，提升平局与冷门容错")
        elif resonance_label == "tempt_home_resonance":
            home_adv -= 0.012
            draw_bias += 0.01
            resonance_prob_delta_home -= 0.008
            resonance_prob_delta_draw += 0.01
            resonance_ou_delta -= 0.008
            resonance_signals.append("主队方向共振但存在诱盘风险")
        elif resonance_label == "tempt_away_resonance":
            away_adv -= 0.012
            draw_bias += 0.01
            resonance_prob_delta_away -= 0.008
            resonance_prob_delta_draw += 0.01
            resonance_ou_delta -= 0.008
            resonance_signals.append("客队方向共振但存在诱盘风险")
        elif resonance_label == "home_side_resonance":
            home_adv += 0.01
            away_adv -= 0.004
            resonance_prob_delta_home += 0.006
            resonance_signals.append("三盘口整体偏主队")
        elif resonance_label == "away_side_resonance":
            away_adv += 0.01
            home_adv -= 0.004
            resonance_prob_delta_away += 0.006
            resonance_signals.append("三盘口整体偏客队")

        if resonance_signals:
            signals.extend(resonance_signals)

        home_adv = max(-0.12, min(0.12, home_adv))
        away_adv = max(-0.12, min(0.12, away_adv))
        draw_bias = max(-0.015, min(0.05, draw_bias))

        return {
            "available": True,
            "market": market,
            "table": {"home": home_mot.get("table_row"), "away": away_mot.get("table_row")},
            "motivation": {
                "home": home_mot,
                "away": away_mot,
                "edge": {
                    "home_minus_away": round(float(home_mot.get("score", 75.0)) - float(away_mot.get("score", 75.0)), 2),
                    "urgency_home_minus_away": round(float(home_mot.get("urgency", 0.45)) - float(away_mot.get("urgency", 0.45)), 4),
                    "dominant_side": "home"
                    if float(home_mot.get("score", 75.0)) > float(away_mot.get("score", 75.0))
                    else "away"
                    if float(home_mot.get("score", 75.0)) < float(away_mot.get("score", 75.0))
                    else "balanced",
                    "dominant_reason": str(motivation_risk.get("summary") or ""),
                },
                "context_flags": motivation_context_flags,
                "risk_signal": motivation_risk,
                "suggested_scores": {
                    "home": round(float(analysis_context.get("home_motivation", home_mot.get("score", 75.0))), 2),
                    "away": round(float(analysis_context.get("away_motivation", away_mot.get("score", 75.0))), 2),
                },
            },
            "team_state": {
                "home": {
                    "recent": recent_home,
                    "ewma": home_ewma,
                    "avg_possession": home_poss,
                    "formation": home_shape,
                    "last_lineup": home_ctx.get("last_lineup"),
                    "key_players": home_ctx.get("key_players", []),
                    "injuries": {"injured_count": home_strength.get("injured_count", 0), "suspended_count": home_strength.get("suspended_count", 0)},
                },
                "away": {
                    "recent": recent_away,
                    "ewma": away_ewma,
                    "avg_possession": away_poss,
                    "formation": away_shape,
                    "last_lineup": away_ctx.get("last_lineup"),
                    "key_players": away_ctx.get("key_players", []),
                    "injuries": {"injured_count": away_strength.get("injured_count", 0), "suspended_count": away_strength.get("suspended_count", 0)},
                },
            },
            "head_to_head": h2h,
            "signals": signals,
            "scenario_tags": scenario_tags,
            "contextual_rules": {
                "signals": contextual_rules.get("signals", []),
                "scenario_tags": scenario_tags,
                "volatility": {"home": home_volatility, "away": away_volatility},
            },
            "quant_adjustment": {
                "home_delta": round(home_adv, 4),
                "away_delta": round(away_adv, 4),
                "draw_delta": round(draw_bias, 4),
                "home_lambda_scale": round(max(0.90, min(1.10, 1.0 + home_adv * 0.6)), 4),
                "away_lambda_scale": round(max(0.90, min(1.10, 1.0 + away_adv * 0.6)), 4),
                "scenario_home_delta": contextual_rules.get("home_delta", 0.0),
                "scenario_away_delta": contextual_rules.get("away_delta", 0.0),
                "scenario_draw_delta": contextual_rules.get("draw_delta", 0.0),
                "resonance_label": resonance_label,
                "resonance_score": round(resonance_score, 4),
                "resonance_prob_delta_home": round(resonance_prob_delta_home, 4),
                "resonance_prob_delta_away": round(resonance_prob_delta_away, 4),
                "resonance_prob_delta_draw": round(resonance_prob_delta_draw, 4),
                "resonance_ou_delta": round(max(-0.05, min(0.05, resonance_ou_delta)), 4),
            },
        }

    def _apply_match_intelligence_adjustment(
        self,
        final_prob: Dict[str, float],
        match_intelligence: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, float], Dict[str, Any]]:
        diag: Dict[str, Any] = {"applied": False, "source": "match_intelligence", "signals": [], "delta": {}}
        if not isinstance(final_prob, dict) or not isinstance(match_intelligence, dict):
            return final_prob, diag
        qa = match_intelligence.get("quant_adjustment")
        if not isinstance(qa, dict):
            return final_prob, diag
        p_h = float(final_prob.get("home_win", 0.0))
        p_d = float(final_prob.get("draw", 0.0))
        p_a = float(final_prob.get("away_win", 0.0))
        delta_h = max(
            -0.035,
            min(
                0.035,
                float(qa.get("home_delta") or 0.0) * 0.22 + float(qa.get("resonance_prob_delta_home") or 0.0),
            ),
        )
        delta_a = max(
            -0.035,
            min(
                0.035,
                float(qa.get("away_delta") or 0.0) * 0.22 + float(qa.get("resonance_prob_delta_away") or 0.0),
            ),
        )
        delta_d = max(
            -0.015,
            min(
                0.025,
                float(qa.get("draw_delta") or 0.0) + float(qa.get("resonance_prob_delta_draw") or 0.0),
            ),
        )
        if abs(delta_h) < 0.001 and abs(delta_a) < 0.001 and abs(delta_d) < 0.001:
            return final_prob, diag
        p_h = max(0.01, p_h + delta_h)
        p_a = max(0.01, p_a + delta_a)
        p_d = max(0.01, p_d + delta_d)
        s = p_h + p_d + p_a
        p_h, p_d, p_a = p_h / s, p_d / s, p_a / s
        diag.update(
            {
                "applied": True,
                "signals": match_intelligence.get("signals", []) + (match_intelligence.get("market", {}).get("signals", []) or []),
                "delta": {"home": round(delta_h, 4), "draw": round(delta_d, 4), "away": round(delta_a, 4)},
                "resonance_label": qa.get("resonance_label"),
                "scenario_tags": match_intelligence.get("scenario_tags", []),
            }
        )
        return {"home_win": p_h, "draw": p_d, "away_win": p_a}, diag

    def _apply_market_resonance_to_over_under(
        self,
        over_under: Optional[Dict[str, Any]],
        match_intelligence: Optional[Dict[str, Any]],
    ) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        diag: Dict[str, Any] = {"applied": False, "source": "market_resonance", "delta": 0.0}
        if not isinstance(over_under, dict) or not isinstance(match_intelligence, dict):
            return over_under, diag
        qa = match_intelligence.get("quant_adjustment")
        if not isinstance(qa, dict):
            return over_under, diag
        over = self._to_float(over_under.get("over"))
        under = self._to_float(over_under.get("under"))
        if over is None or under is None:
            return over_under, diag
        delta = max(-0.05, min(0.05, float(qa.get("resonance_ou_delta") or 0.0)))
        if abs(delta) < 0.003:
            return over_under, diag
        over = max(0.01, min(0.99, over + delta))
        under = max(0.01, min(0.99, 1.0 - over))
        out = dict(over_under)
        out["over"] = over
        out["under"] = under
        out["resonance_adjustment"] = {
            "applied": True,
            "delta": round(delta, 4),
            "label": qa.get("resonance_label"),
        }
        diag.update(
            {
                "applied": True,
                "delta": round(delta, 4),
                "label": qa.get("resonance_label"),
                "over": round(over, 4),
                "under": round(under, 4),
            }
        )
        return out, diag

    def _finalize_match_intelligence(
        self,
        match_intelligence: Optional[Dict[str, Any]],
        historical_odds_reference: Optional[Dict[str, Any]],
        upset_potential: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        out = dict(match_intelligence or {})
        if isinstance(historical_odds_reference, dict):
            out["historical_odds_reference"] = {
                "available": historical_odds_reference.get("available"),
                "summary": historical_odds_reference.get("summary"),
                "insights": historical_odds_reference.get("insights"),
            }
        if isinstance(upset_potential, dict):
            out["upset_case"] = {
                "level": upset_potential.get("level"),
                "index": upset_potential.get("index"),
                "factors": upset_potential.get("factors"),
                "case_knowledge": upset_potential.get("case_knowledge"),
            }
        return out

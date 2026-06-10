"""模块说明：集中收口预测系统的魔法数字与派生公式（单一事实来源）。

历史上这些常量散落在 domain/inference.py、result_manager.py、ml_prediction_models.py、
storage/ratings.py 等处，多处硬编码同一份 rho_map / 主场系数 / strength→ELO 派生公式，
极易在调参时漏改某一处导致行为不一致。此模块作为唯一来源，供各调用方引用。

注意：常量值与各历史调用点保持完全一致，确保 SoT 联赛零回归。
"""

from __future__ import annotations

from typing import Dict

# ---------------------------------------------------------------------------
# Dixon-Coles τ 修正的 ρ（rho）系数：按联赛刻画低比分相关性（ρ<0 抬高 0-0/1-1 平局）
# ---------------------------------------------------------------------------
DEFAULT_RHO = -0.10

RHO_MAP: Dict[str, float] = {
    'premier_league': -0.08,
    'la_liga': -0.10,
    'serie_a': -0.12,
    'bundesliga': -0.06,
    'ligue_1': -0.10,
    'friendly': -0.04,
    'world_cup': -0.07,
}


def resolve_rho(league_code: str) -> float:
    """返回联赛的 Dixon-Coles ρ；未知联赛回退默认值。"""
    return RHO_MAP.get(league_code, DEFAULT_RHO)


# ---------------------------------------------------------------------------
# 主场优势系数：联赛主客场制 1.12；中立/弱主场赛事（友谊赛、世界杯、洲际杯赛）降低
# ---------------------------------------------------------------------------
DEFAULT_HOME_ADVANTAGE = 1.12

HOME_ADVANTAGE_MAP: Dict[str, float] = {
    'friendly': 1.03,
    'world_cup': 1.02,
    'champions_league': 1.06,
    'europa_league': 1.06,
    'conference_league': 1.06,
}


def resolve_home_advantage(league_code: str) -> float:
    """返回联赛的主场优势系数；未知联赛回退 1.12。"""
    return HOME_ADVANTAGE_MAP.get(league_code, DEFAULT_HOME_ADVANTAGE)


# ---------------------------------------------------------------------------
# strength → ELO/Glicko 初始播种公式：读取侧回退与赛果回填播种须保持同一口径
# ---------------------------------------------------------------------------
ELO_BASELINE = 1500.0
ELO_STRENGTH_SCALE = 10.0


def strength_to_seed_rating(strength: float) -> float:
    """由球队当前实力派生 ELO/Glicko 初始播种值：strength*10 + 1500。"""
    return strength * ELO_STRENGTH_SCALE + ELO_BASELINE

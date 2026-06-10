#!/usr/bin/env python3
"""赛程备注（teams_2025-26.md 备注列）与比分文本的纯解析函数。

从 result_manager.ResultManager 抽出的无状态解析逻辑，集中收口以便复用与测试。
这些函数不依赖任何实例状态或 I/O，ResultManager 上的同名方法委托到此处。
"""

import re
from typing import Dict, List, Optional


def parse_score_to_winner(score_text: str) -> Optional[str]:
    """从 `2-1` 形式的比分文本判定胜负方（home/draw/away）。"""
    if not isinstance(score_text, str) or '-' not in score_text:
        return None
    parts = score_text.split('-')
    if len(parts) != 2:
        return None
    try:
        hs = int(parts[0].strip())
        as_ = int(parts[1].strip())
    except Exception:
        return None
    if hs > as_:
        return 'home'
    if hs < as_:
        return 'away'
    return 'draw'


def parse_predicted_winner(note: str) -> Optional[str]:
    """从备注解析预测胜负方，兼容 `预测:主胜` 与 `预测主胜✅` 等写法。"""
    if not isinstance(note, str):
        return None
    # Support both:
    # - enhanced writeback: `预测:主胜 ...`
    # - legacy fragments: `预测主胜✅` / `已结束/预测平局✅`
    match = re.search(r'(?:预测\s*[:：]?\s*)(主胜|平局|客胜)', note)
    if not match:
        return None
    return {'主胜': 'home', '平局': 'draw', '客胜': 'away'}.get(match.group(1))


def parse_prediction_confidence(note: str) -> Optional[float]:
    """从备注解析信心值，归一化到 (0, 1]。"""
    if not isinstance(note, str):
        return None
    match = re.search(r'信心[:：]?\s*([0-9]+(?:\.[0-9]+)?)', note)
    if not match:
        return None
    try:
        value = float(match.group(1))
    except Exception:
        return None
    if value > 1:
        value = value / 100.0
    if 0 < value <= 1:
        return value
    return None


def parse_predicted_scores(note: str) -> List[str]:
    """解析备注中 `比分:1-1/1-0` 形式的预测比分片段。"""
    if not isinstance(note, str):
        return []
    m = re.search(r'比分[:\s]*([0-9\-\/]+)', note)
    if not m:
        return []
    raw = (m.group(1) or '').strip()
    scores = []
    for part in raw.split('/'):
        s = part.strip()
        if re.match(r'^\d+\s*-\s*\d+$', s):
            scores.append(re.sub(r'\s+', '', s))
    return scores


def parse_predicted_ou(note: str) -> Optional[Dict[str, object]]:
    """解析备注中 `大小:小2.5(0.58)` 形式的大小球预测片段。"""
    if not isinstance(note, str):
        return None
    m = re.search(r'大小[:\s]*([大小])\s*([0-9]+(?:\.[0-9]+)?)', note)
    if not m:
        return None
    side = m.group(1)
    try:
        line = float(m.group(2))
    except Exception:
        return None
    return {'side': side, 'line': line}

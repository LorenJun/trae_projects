"""模块说明：操盘信号「自验证·动态调权」学习器。

两层调权架构的第二层：把每个操盘信号(欧赔走热/让球加深/水位/凯利/跨轴背离)的
调权力度，从历史归档赛果里**学**出来，而不是手写阈值。

设计原则（对应需求）：
  1. 逐场数据动态——extract_delta_vector 对每场实时算出该场自己的 Δ 向量(第一层)；
  2. 真实/诱导由数据定——每个信号的方向(sign)与可靠度(reliability)由历史命中率(AUC)决定；
  3. 不写死阈值——可靠度连续输出；样本/显著性不足时自动归零(仅提示不调权)，
     样本攒够、信号显出 edge 后自动放大，无需手动开关；
  4. 兼容任意联赛——只依赖盘口结构本身(欧赔/亚盘/凯利)，与具体联赛无关。

权重恒绑定留出表现：跑不赢基线就权重=0，结构上无法过拟合。
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Optional, Tuple

# 以「强侧」为参照系，符号统一为：正=利好强侧（强侧更可能赢）。
FEATURE_NAMES: Tuple[str, ...] = (
    'euro_fav_imp_move',      # 强侧隐含概率位移：走热=正
    'euro_spread_widen',      # 强弱隐含概率差扩大：走热=正
    'hcp_deepen',             # 让球加深：庄家为强侧背书=正
    'fav_water_drop',         # 强侧水位下降(取负号)：底水真买=正
    'kelly_fav_move',         # 强侧凯利位移：资金背书=正
    'div_euro_hot_hcp_flat',  # 跨轴背离：欧赔热但盘不升=诱导嫌疑(利空强侧)
)

# 安全闸（保守：宁可不调权也不乱调）。这两个不是预测方向阈值，只是激活门槛。
MIN_SAMPLES = 80     # 某信号有效样本不足 → 可靠度=0
MIN_AUC_GAP = 0.07   # |AUC-0.5| 不显著 → 可靠度=0（视为噪声）
GAP_SCALE = 0.20     # 可靠度饱和尺度：gap 超过门槛后线性增长到 1.0


def _f(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def extract_delta_vector(
    european_odds: Optional[Dict[str, Any]],
    asian_handicap: Optional[Dict[str, Any]] = None,
    kelly: Optional[Dict[str, Any]] = None,
    parse_hcp: Optional[Callable[[Any], Optional[float]]] = None,
) -> Optional[Dict[str, Any]]:
    """第一层：抽取该场比赛自己的盘口位移向量(initial→final)。

    返回 dict（含 'fav' 与各特征，特征缺失时为 None）；欧赔不可用时返回 None。
    符号统一：正=利好强侧。完全逐场，不含任何写死阈值。
    """
    if not isinstance(european_odds, dict):
        return None
    ei = european_odds.get('initial') if isinstance(european_odds.get('initial'), dict) else {}
    ef = european_odds.get('final') if isinstance(european_odds.get('final'), dict) else {}
    oh_i, oa_i = _f(ei.get('home')), _f(ei.get('away'))
    oh_f, oa_f = _f(ef.get('home')), _f(ef.get('away'))
    od_f = _f(ef.get('draw'))
    if None in (oh_i, oa_i, oh_f, oa_f) or not od_f:
        return None

    fav = 'home' if (1.0 / oh_f) >= (1.0 / oa_f) else 'away'

    def imp(o: Optional[float]) -> Optional[float]:
        return (1.0 / o) if o else None

    p_fav_i = imp(oh_i if fav == 'home' else oa_i)
    p_fav_f = imp(oh_f if fav == 'home' else oa_f)
    p_dog_i = imp(oa_i if fav == 'home' else oh_i)
    p_dog_f = imp(oa_f if fav == 'home' else oh_f)

    feat: Dict[str, Any] = {'fav': fav}
    feat['euro_fav_imp_move'] = (p_fav_f - p_fav_i)
    feat['euro_spread_widen'] = (p_fav_f - p_dog_f) - (p_fav_i - p_dog_i)

    hcp_i = hcp_f = None
    if isinstance(asian_handicap, dict):
        ai = asian_handicap.get('initial') if isinstance(asian_handicap.get('initial'), dict) else {}
        af = asian_handicap.get('final') if isinstance(asian_handicap.get('final'), dict) else {}

        def _hcp(d: Dict[str, Any]) -> Optional[float]:
            raw = d.get('handicap') if 'handicap' in d else d.get('handicap_value') if 'handicap_value' in d else d.get('handicap_text')
            if parse_hcp is not None:
                return parse_hcp(raw)
            return _f(raw)

        hcp_i, hcp_f = _hcp(ai), _hcp(af)
        feat['hcp_deepen'] = (abs(hcp_f) - abs(hcp_i)) if (hcp_i is not None and hcp_f is not None) else None
        fav_w_i = _f(ai.get('home_water')) if fav == 'home' else _f(ai.get('away_water'))
        fav_w_f = _f(af.get('home_water')) if fav == 'home' else _f(af.get('away_water'))
        feat['fav_water_drop'] = (-(fav_w_f - fav_w_i)) if (fav_w_i is not None and fav_w_f is not None) else None
    else:
        feat['hcp_deepen'] = None
        feat['fav_water_drop'] = None

    if isinstance(kelly, dict):
        ki = kelly.get('initial') if isinstance(kelly.get('initial'), dict) else {}
        kf = kelly.get('final') if isinstance(kelly.get('final'), dict) else {}
        kfav_i, kfav_f = _f(ki.get(fav)), _f(kf.get(fav))
        feat['kelly_fav_move'] = (kfav_f - kfav_i) if (kfav_i is not None and kfav_f is not None) else None
    else:
        feat['kelly_fav_move'] = None

    eh = feat.get('euro_spread_widen')
    hd = feat.get('hcp_deepen')
    if eh is not None and hd is not None:
        feat['div_euro_hot_hcp_flat'] = max(0.0, eh) * (1.0 if hd <= 0.001 else 0.0)
    else:
        feat['div_euro_hot_hcp_flat'] = None

    return feat


def auc(pairs: List[Tuple[float, int]]) -> Optional[float]:
    """单变量 AUC（Mann-Whitney）。pairs: [(特征值, 强侧赢=1/没赢=0)]。"""
    pos = [v for v, y in pairs if y == 1]
    neg = [v for v, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def _mean_std(values: List[float]) -> Tuple[float, float]:
    n = len(values)
    if n == 0:
        return 0.0, 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, math.sqrt(var)


class OperationWeightLearner:
    """从历史样本学每个操盘信号的可靠度系数（第二层）。"""

    def __init__(
        self,
        min_samples: int = MIN_SAMPLES,
        min_auc_gap: float = MIN_AUC_GAP,
        gap_scale: float = GAP_SCALE,
    ):
        self.min_samples = int(min_samples)
        self.min_auc_gap = float(min_auc_gap)
        self.gap_scale = float(gap_scale)

    def _reliability(self, n: int, gap: float) -> float:
        if n < self.min_samples or gap < self.min_auc_gap:
            return 0.0
        return max(0.0, min(1.0, (gap - self.min_auc_gap) / self.gap_scale))

    def learn(self, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
        """samples: 每条含各特征值 + '_fav_won'(1/0)。返回 {feature: {...}}。"""
        weights: Dict[str, Any] = {}
        for name in FEATURE_NAMES:
            pairs = [
                (float(s[name]), int(s['_fav_won']))
                for s in samples
                if s.get(name) is not None and s.get('_fav_won') in (0, 1)
            ]
            if not pairs:
                continue
            a = auc(pairs)
            if a is None:
                continue
            values = [v for v, _ in pairs]
            mean, std = _mean_std(values)
            gap = abs(a - 0.5)
            n = len(pairs)
            weights[name] = {
                'reliability': round(self._reliability(n, gap), 4),
                'sign': 1 if a >= 0.5 else -1,
                'auc': round(a, 4),
                'n': n,
                'mean': round(mean, 6),
                'std': round(std, 6),
            }
        return weights

    def learn_from_archive(
        self,
        archive: Dict[str, Any],
        parse_hcp: Optional[Callable[[Any], Optional[float]]] = None,
    ) -> Dict[str, Any]:
        samples: List[Dict[str, Any]] = []
        for _key, entry in (archive or {}).items():
            if not isinstance(entry, dict):
                continue
            ms = entry.get('market_snapshot') or {}
            if not (isinstance(ms, dict) and ms.get('欧赔')):
                continue
            score = _parse_score(entry.get('actual_score'))
            if score is None:
                continue
            feat = extract_delta_vector(ms.get('欧赔'), ms.get('亚值'), ms.get('凯利'), parse_hcp)
            if feat is None:
                continue
            h, a = score
            actual = 'home' if h > a else 'away' if h < a else 'draw'
            feat['_fav_won'] = 1 if feat['fav'] == actual else 0
            samples.append(feat)
        weights = self.learn(samples)
        return {
            'weights': weights,
            'sample_count': len(samples),
            'min_samples': self.min_samples,
            'min_auc_gap': self.min_auc_gap,
        }


def _parse_score(text: Any) -> Optional[Tuple[int, int]]:
    s = str(text or '').strip()
    if '-' not in s:
        return None
    try:
        h, a = s.split('-')[:2]
        return int(h.strip()), int(a.strip())
    except Exception:
        return None


def score_delta(delta_vector: Optional[Dict[str, Any]], weights: Optional[Dict[str, Any]]) -> Tuple[float, List[str]]:
    """两层合成：score = Σ z(Δ_f) · sign_f · reliability_f。

    score>0=印证强侧(真实看好)，score<0=诱导嫌疑(强侧被高估)。
    无可靠信号(系数全0)时返回 (0.0, [])，调用方据此不调概率。
    """
    if not isinstance(delta_vector, dict) or not isinstance(weights, dict):
        return 0.0, []
    score = 0.0
    active: List[str] = []
    for name, w in weights.items():
        if not isinstance(w, dict):
            continue
        rel = float(w.get('reliability') or 0.0)
        if rel <= 0.0:
            continue
        val = delta_vector.get(name)
        if not isinstance(val, (int, float)):
            continue
        std = float(w.get('std') or 0.0)
        if std <= 1e-9:
            continue
        mean = float(w.get('mean') or 0.0)
        z = (float(val) - mean) / std
        sign = 1.0 if int(w.get('sign') or (1 if float(w.get('auc') or 0.5) >= 0.5 else -1)) >= 0 else -1.0
        score += z * sign * rel
        active.append(name)
    return score, active

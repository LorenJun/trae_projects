#!/usr/bin/env python3
"""跨轴特征·信号诊断（建判定逻辑之前的实证步骤）。

对 42 场留出样本，逐场抽取「欧赔/亚盘/凯利/大小球」各轴的连续特征，
再按真实赛果(强侧赢=1 / 没赢=0)分组，看每个特征在两组间是否有分离度。
只有先证明某特征有区分力，才有资格进 net 分；否则就是噪声。

输出每个特征：
  - mean(强侧赢) vs mean(强侧没赢) 的均值差
  - 简单单变量 AUC（按特征排序能否分出赢/没赢）
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.inference import InferencePipelineService

ARCHIVE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '.okooo-scraper', 'runtime', 'prediction_archive.json',
)


def f(v):
    try:
        return float(v)
    except Exception:
        return None


def parse_score(text):
    text = str(text or '').strip()
    if '-' not in text:
        return None
    try:
        h, a = text.split('-')[:2]
        return int(h.strip()), int(a.strip())
    except Exception:
        return None


def parse_hcp(svc, d):
    if not isinstance(d, dict):
        return None
    return svc._parse_handicap_value(
        d.get('handicap') if 'handicap' in d else d.get('handicap_value') if 'handicap_value' in d else d.get('handicap_text')
    )


def extract_features(svc, ms):
    """返回该场所有候选跨轴特征 dict（以「强侧」为参照系，符号统一为：正=利好强侧）。"""
    eu = ms.get('欧赔') or {}
    ah = ms.get('亚值') or {}
    ke = ms.get('凯利') or {}
    ou = ms.get('大小球') or {}
    ei, ef = eu.get('initial') or {}, eu.get('final') or {}
    oh_i, oa_i = f(ei.get('home')), f(ei.get('away'))
    oh_f, oa_f = f(ef.get('home')), f(ef.get('away'))
    od_f = f(ef.get('draw'))
    if None in (oh_i, oa_i, oh_f, oa_f) or not od_f:
        return None
    # 终盘强侧
    fav = 'home' if (1.0 / oh_f) >= (1.0 / oa_f) else 'away'

    def imp(o):
        return 1.0 / o if o else None
    # 隐含概率位移（已归一去抽水近似：直接用 1/odds 差，方向上够用）
    p_fav_i = imp(oh_i if fav == 'home' else oa_i)
    p_fav_f = imp(oh_f if fav == 'home' else oa_f)
    p_dog_i = imp(oa_i if fav == 'home' else oh_i)
    p_dog_f = imp(oa_f if fav == 'home' else oh_f)
    feat = {}
    feat['fav'] = fav
    # 欧赔轴：强侧隐含概率上升=走热(利好强侧)
    feat['euro_fav_imp_move'] = (p_fav_f - p_fav_i)
    feat['euro_dog_imp_move'] = (p_dog_f - p_dog_i)
    feat['euro_spread_widen'] = (p_fav_f - p_dog_f) - (p_fav_i - p_dog_i)  # 强弱差扩大=走热

    # 亚盘轴：让球加深=庄家为强侧背书(利好强侧)
    ai, af = ah.get('initial') or {}, ah.get('final') or {}
    hcp_i, hcp_f = parse_hcp(svc, ai), parse_hcp(svc, af)
    feat['hcp_deepen'] = (abs(hcp_f) - abs(hcp_i)) if (hcp_i is not None and hcp_f is not None) else None
    # 强侧水位下降=底水真买(利好强侧)，故取负号让"正=利好强侧"
    fav_w_i = f(ai.get('home_water')) if fav == 'home' else f(ai.get('away_water'))
    fav_w_f = f(af.get('home_water')) if fav == 'home' else f(af.get('away_water'))
    feat['fav_water_drop'] = (-(fav_w_f - fav_w_i)) if (fav_w_i is not None and fav_w_f is not None) else None

    # 凯利轴：强侧凯利上升/接近1=资金背书(利好强侧)
    ki, kf = ke.get('initial') or {}, ke.get('final') or {}
    kfav_i = f(ki.get(fav)); kfav_f = f(kf.get(fav)); kdraw_f = f(kf.get('draw'))
    feat['kelly_fav_move'] = (kfav_f - kfav_i) if (kfav_i is not None and kfav_f is not None) else None
    feat['kelly_fav_level'] = kfav_f
    feat['kelly_draw_level'] = kdraw_f  # 高=平局被低估？低=平局被加注(利空强侧)

    # —— 跨轴背离派生 ——
    # 欧赔走热 但 亚盘没背书：euro_heat 正、hcp_deepen<=0 → 诱导嫌疑(利空强侧)
    eh = feat['euro_spread_widen']
    hd = feat['hcp_deepen']
    if eh is not None and hd is not None:
        feat['div_euro_hot_hcp_flat'] = max(0.0, eh) * (1.0 if hd <= 0.001 else 0.0)
        # 同向背书度：欧赔热 且 盘也升 → 利好强侧
        feat['concord_euro_hcp'] = (1.0 if (eh > 0 and hd > 0.001) else 0.0)
    return feat


def auc(pairs):
    """pairs: list of (feat_value, label1/0). 单变量 AUC（Mann-Whitney）。"""
    pos = [v for v, y in pairs if y == 1]
    neg = [v for v, y in pairs if y == 0]
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                wins += 0.5
    return wins / (len(pos) * len(neg))


def main():
    archive = json.load(open(ARCHIVE, encoding='utf-8'))
    svc = object.__new__(InferencePipelineService)
    samples = []
    for key, entry in archive.items():
        ms = entry.get('market_snapshot') or {}
        if not (isinstance(ms, dict) and ms.get('欧赔')):
            continue
        score = parse_score(entry.get('actual_score'))
        if score is None:
            continue
        feat = extract_features(svc, ms)
        if feat is None:
            continue
        fav = feat['fav']
        actual = 'home' if score[0] > score[1] else 'away' if score[0] < score[1] else 'draw'
        feat['_fav_won'] = 1 if fav == actual else 0
        samples.append(feat)

    print(f'样本: {len(samples)} 场   强侧赢: {sum(s["_fav_won"] for s in samples)}   强侧没赢: {sum(1-s["_fav_won"] for s in samples)}')
    print('=' * 90)
    feat_names = [
        'euro_fav_imp_move', 'euro_dog_imp_move', 'euro_spread_widen',
        'hcp_deepen', 'fav_water_drop',
        'kelly_fav_move', 'kelly_fav_level', 'kelly_draw_level',
        'div_euro_hot_hcp_flat', 'concord_euro_hcp',
    ]
    print(f'{"feature":24s} {"mean(赢)":>10s} {"mean(没赢)":>10s} {"差":>8s} {"AUC":>7s} {"n":>4s}')
    print('-' * 90)
    for name in feat_names:
        pairs = [(s[name], s['_fav_won']) for s in samples if s.get(name) is not None]
        if not pairs:
            print(f'{name:24s}  (无数据)')
            continue
        pos = [v for v, y in pairs if y == 1]
        neg = [v for v, y in pairs if y == 0]
        mp = sum(pos) / len(pos) if pos else 0.0
        mn = sum(neg) / len(neg) if neg else 0.0
        a = auc(pairs)
        # AUC<0.5 表示该特征「越大越不利强侧」，对诱导判定同样有用，看 |AUC-0.5|
        print(f'{name:24s} {mp:10.4f} {mn:10.4f} {mp-mn:8.4f} {a:7.3f} {len(pairs):4d}')
    print('=' * 90)
    print('解读：AUC 越偏离 0.50 越有信号；>0.5=越大越利好强侧，<0.5=越大越利空强侧(诱导)。')
    print('     |AUC-0.5|<0.07 基本是噪声。')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""庄家操盘判别器·方向轴回测。

拿归档里所有「有 market_snapshot(欧赔) + 有真实赛果」的已完赛比赛，
按生产管线同样的方式构造输入、调用 classify_market_operation_pattern，
统计方向轴判定(deceptive/genuine/neutral)与真实赛果的吻合度，量化是否过拟合。

判定语义对照赛果：
  - genuine(背书强侧)  → 强侧赢 视为命中
  - deceptive(诱下强侧) → 强侧没赢(平/负) 视为命中
  - neutral            → 不计入命中率分母(无方向断言)，仅观察分布
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.inference import InferencePipelineService
from domain.postprocess import PredictionPostprocessService

ARCHIVE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '.okooo-scraper', 'runtime', 'prediction_archive.json',
)


def parse_score(text):
    text = str(text or '').strip()
    if '-' not in text:
        return None
    try:
        h, a = text.split('-')[:2]
        return int(h.strip()), int(a.strip())
    except Exception:
        return None


def outcome_from_score(h, a):
    if h > a:
        return 'home'
    if h < a:
        return 'away'
    return 'draw'


def main():
    archive = json.load(open(ARCHIVE, encoding='utf-8'))
    svc = object.__new__(InferencePipelineService)
    post = PredictionPostprocessService(league_config={})

    rows = []
    for key, entry in archive.items():
        ms = entry.get('market_snapshot') or {}
        if not (isinstance(ms, dict) and ms.get('欧赔')):
            continue
        score = parse_score(entry.get('actual_score'))
        if score is None:
            continue
        european_odds = ms.get('欧赔')
        asian_handicap = ms.get('亚值')
        kelly = ms.get('凯利')
        current_odds = {'欧赔': european_odds, '亚值': asian_handicap, '大小球': ms.get('大小球'), '凯利': kelly}

        try:
            sentiment = svc.detect_market_movement_sentiment(
                european_odds=european_odds, asian_handicap=asian_handicap,
            )
            ou_signal = post.extract_over_under_market_signal(current_odds)
            pattern = svc.classify_market_operation_pattern(
                european_odds=european_odds,
                asian_handicap=asian_handicap,
                ou_signal=ou_signal,
                kelly=kelly,
                market_sentiment=sentiment,
            )
        except Exception as exc:
            rows.append({'key': key, 'error': str(exc)})
            continue

        verdict = pattern.get('verdict')
        fav = pattern.get('favored_side')
        actual = outcome_from_score(*score)
        fav_won = (fav == actual)

        hit = None
        if verdict == 'genuine':
            hit = fav_won
        elif verdict == 'deceptive':
            hit = not fav_won
        rows.append({
            'key': key,
            'verdict': verdict,
            'fav': fav,
            'fav_move': round(float(sentiment.get('fav_odds_move') or 0.0), 4),
            'actual': actual,
            'score': f'{score[0]}-{score[1]}',
            'fav_won': fav_won,
            'hit': hit,
        })

    # 统计
    by_verdict = {'deceptive': [], 'genuine': [], 'neutral': [], 'error': []}
    for r in rows:
        if 'error' in r:
            by_verdict['error'].append(r)
        else:
            by_verdict[r['verdict']].append(r)

    print(f'样本总数(有欧赔+赛果): {len([r for r in rows if "error" not in r])}  错误: {len(by_verdict["error"])}')
    print('=' * 72)
    directional = []
    for v in ('deceptive', 'genuine'):
        items = by_verdict[v]
        hits = sum(1 for r in items if r['hit'])
        directional.extend(items)
        rate = (hits / len(items) * 100) if items else 0.0
        print(f'{v:10s} n={len(items):2d}  命中={hits:2d}  命中率={rate:5.1f}%')
    print(f'{"neutral":10s} n={len(by_verdict["neutral"]):2d}  (无方向断言，不计命中)')
    print('=' * 72)
    dh = sum(1 for r in directional if r['hit'])
    drate = (dh / len(directional) * 100) if directional else 0.0
    print(f'方向轴总命中率(deceptive+genuine): {dh}/{len(directional)} = {drate:.1f}%')
    # 基线：若全部猜 fav 赢
    fav_base = sum(1 for r in rows if 'error' not in r and r['fav_won'])
    n_all = len([r for r in rows if 'error' not in r])
    print(f'基线·全押强侧赢: {fav_base}/{n_all} = {fav_base/n_all*100:.1f}%')
    print('=' * 72)
    print('逐场明细(仅方向判定):')
    for r in sorted(directional, key=lambda x: x['verdict']):
        mark = '✅' if r['hit'] else '❌'
        print(f"  {mark} [{r['verdict']:9s}] {r['key'][:42]:42s} fav={r['fav']:>4s} move={r['fav_move']:+.3f} 赛果={r['score']}({r['actual']})")


if __name__ == '__main__':
    main()

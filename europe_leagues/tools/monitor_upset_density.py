#!/usr/bin/env python3
"""冷门密度监控：用模型预测概率做泊松-二项方差检验，判断方向命中是否系统性偏离。

原理：每场预测方向的命中概率 = 该场预测胜方的模型概率(confidence)。
N 场的命中数服从泊松-二项分布，期望 = Σp_i，方差 = Σp_i(1-p_i)。
z = (实际命中 - 期望) / 标准差。|z| 越大越异常：
  z < -2  → 命中显著低于模型预期（模型高估自己/存在系统性误差/极端冷门密集）
  -2..-1  → 略低，正常偏冷波动
  -1..1   → 正常
仅当 |z| 超过阈值才告警，避免被短期噪声带偏。结果落 runtime/upset_density_monitor.json 作基线。

用法： /usr/bin/python3 tools/monitor_upset_density.py [--days N] [--z-threshold 2.0] [--dry-run]
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.paths import get_default_paths

_WINNER_MAP = {
    '主胜': 'home', '客胜': 'away', '平局': 'draw',
    'home': 'home', 'away': 'away', 'draw': 'draw',
    'home_win': 'home', 'away_win': 'away',
}


def _winner_from_score(score):
    try:
        h, a = [int(x) for x in str(score).split('-')]
    except Exception:
        return None
    return 'home' if h > a else ('away' if h < a else 'draw')


def _collect_rows(archive, since_date):
    rows = []
    entries = archive if isinstance(archive, list) else list(archive.values())
    for e in entries:
        if not isinstance(e, dict):
            continue
        md = str(e.get('match_date', ''))
        if not md or md < since_date:
            continue
        score = e.get('actual_score')
        if not score:
            continue
        actual = _winner_from_score(score)
        if actual is None:
            continue
        pred = _WINNER_MAP.get(e.get('predicted_winner'), e.get('predicted_winner'))
        conf = float(e.get('confidence') or 0.0)
        if not pred or conf <= 0:
            continue
        rows.append({
            'date': md,
            'home': e.get('home_team'),
            'away': e.get('away_team'),
            'pred': pred,
            'p': conf,
            'score': score,
            'actual': actual,
            'hit': pred == actual,
        })
    rows.sort(key=lambda r: (r['date'], str(r['home'])))
    return rows


def analyze(rows):
    n = len(rows)
    hits = sum(1 for r in rows if r['hit'])
    exp = sum(r['p'] for r in rows)
    var = sum(r['p'] * (1 - r['p']) for r in rows)
    sd = math.sqrt(var) if var > 0 else 0.0
    z = (hits - exp) / sd if sd > 0 else 0.0
    return {
        'n': n,
        'hits': hits,
        'expected_hits': round(exp, 3),
        'std': round(sd, 3),
        'z': round(z, 3),
        'ci68': [round(exp - sd, 2), round(exp + sd, 2)],
        'ci95': [round(exp - 2 * sd, 2), round(exp + 2 * sd, 2)],
    }


def run_monitor(days=14, z_threshold=2.0, write=True):
    """计算冷门密度 z 值并（可选）落基线。返回 record（无样本时返回 None）。

    供 CLI / auto-sync-results 等以编程方式调用，不打印、不退出进程。
    """
    paths = get_default_paths()
    archive_path = paths.runtime_file('prediction_archive.json')
    if not archive_path.exists():
        return None
    archive = json.loads(archive_path.read_text(encoding='utf-8'))

    since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = _collect_rows(archive, since_date)
    if not rows:
        return None

    stat = analyze(rows)
    alert = abs(stat['z']) >= z_threshold
    record = {
        'checked_at': datetime.now().isoformat(),
        'window_days': days,
        'since_date': since_date,
        'z_threshold': z_threshold,
        'alert': alert,
        'stat': stat,
        'matches': rows,
    }
    if write:
        _persist(paths, record)
    return record


def _persist(paths, record):
    out_path = paths.runtime_file('upset_density_monitor.json')
    history = []
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding='utf-8'))
            history = prev.get('history', []) if isinstance(prev, dict) else []
        except Exception:
            history = []
    history.append({k: record[k] for k in ('checked_at', 'window_days', 'alert', 'stat')})
    history = history[-90:]
    out_path.write_text(
        json.dumps({**record, 'history': history}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    return out_path


def main() -> int:
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    days = 14
    z_threshold = 2.0
    if '--days' in args:
        days = int(args[args.index('--days') + 1])
    if '--z-threshold' in args:
        z_threshold = float(args[args.index('--z-threshold') + 1])

    paths = get_default_paths()
    archive_path = paths.runtime_file('prediction_archive.json')
    if not archive_path.exists():
        print(f'归档不存在: {archive_path}')
        return 1
    archive = json.loads(archive_path.read_text(encoding='utf-8'))

    since_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    rows = _collect_rows(archive, since_date)
    if not rows:
        print(f'近 {days} 天无已结算样本（since {since_date}）')
        return 0

    stat = analyze(rows)

    print(f'冷门密度监控  窗口: 近 {days} 天 (since {since_date})  z 阈值: ±{z_threshold}')
    print('=' * 72)
    print(f'{"date":11s} {"home":12s} {"pred":5s} {"p%":>5s} {"score":6s} {"act":5s} hit')
    print('-' * 72)
    for r in rows:
        print(f'{r["date"]:11s} {str(r["home"])[:12]:12s} {r["pred"]:5s} '
              f'{r["p"]*100:5.1f} {r["score"]:6s} {r["actual"]:5s} {"Y" if r["hit"] else "-"}')
    print('=' * 72)
    print(f'样本 n={stat["n"]}  实际命中={stat["hits"]}  模型期望={stat["expected_hits"]}  '
          f'sd={stat["std"]}')
    print(f'z = {stat["z"]}   68%区间 {stat["ci68"]}   95%区间 {stat["ci95"]}')

    alert = abs(stat['z']) >= z_threshold
    if alert:
        direction = '低于' if stat['z'] < 0 else '高于'
        print(f'\n⚠ 告警：z={stat["z"]} 超过阈值 ±{z_threshold}，方向命中系统性{direction}模型预期。')
        print('  → 建议排查：模型是否高估信心 / 是否存在系统性偏差 / 冷门是否异常密集。')
    else:
        print(f'\n✓ 正常：|z|={abs(stat["z"])} 未超过阈值 ±{z_threshold}，冷门密度在正常波动区间内。')

    record = {
        'checked_at': datetime.now().isoformat(),
        'window_days': days,
        'since_date': since_date,
        'z_threshold': z_threshold,
        'alert': alert,
        'stat': stat,
        'matches': rows,
    }

    if dry_run:
        print('\n[dry-run] 未写入文件。')
        return 0

    out_path = _persist(paths, record)
    print(f'\n已写入基线: {out_path}')
    return 1 if alert else 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""离线学习操盘信号可靠度系数 → runtime/operation_weights.json。

从归档(prediction_archive.json)所有「有欧赔+真实赛果」的已完赛比赛，
对每个跨轴信号算单变量 AUC，推导可靠度系数(reliability/sign/mean/std)，
写入 runtime 供生产管线的 apply_market_operation_adjustment 消费。

样本/显著性不足的信号 reliability 自动=0（调权退化为仅提示），随归档增长重跑即可。
用法： /usr/bin/python3 tools/learn_operation_weights.py [--dry-run]
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.inference import InferencePipelineService
from domain.operation_weight_learner import OperationWeightLearner
from runtime.paths import get_default_paths


def main() -> int:
    dry_run = '--dry-run' in sys.argv
    paths = get_default_paths()
    archive_path = paths.runtime_file('prediction_archive.json')
    if not archive_path.exists():
        print(f'归档不存在: {archive_path}')
        return 1
    archive = json.loads(archive_path.read_text(encoding='utf-8'))

    learner = OperationWeightLearner()
    result = learner.learn_from_archive(archive, InferencePipelineService._parse_handicap_value)

    n = result['sample_count']
    print(f'样本: {n} 场   样本门槛: {result["min_samples"]}   AUC门槛: {result["min_auc_gap"]}')
    print('=' * 78)
    print(f'{"feature":24s} {"AUC":>7s} {"sign":>5s} {"reliability":>12s} {"n":>4s}')
    print('-' * 78)
    active = 0
    for name, w in result['weights'].items():
        rel = w['reliability']
        if rel > 0:
            active += 1
        print(f'{name:24s} {w["auc"]:7.3f} {w["sign"]:5d} {rel:12.4f} {w["n"]:4d}')
    print('=' * 78)
    print(f'生效信号(reliability>0): {active} / {len(result["weights"])}')
    if active == 0:
        print('→ 当前无信号达到激活门槛，调权将退化为「仅提示不改概率」（防过拟合预期行为）。')

    # 大小球操盘轴
    ou_result = learner.learn_ou_from_archive(archive)
    print('\n' + '#' * 78)
    print(f'大小球操盘轴  样本: {ou_result["sample_count"]} 场（标签=实际总进球是否打穿终盘线，走盘剔除）')
    print('=' * 78)
    print(f'{"feature":24s} {"AUC":>7s} {"sign":>5s} {"reliability":>12s} {"n":>4s}')
    print('-' * 78)
    ou_active = 0
    for name, w in ou_result['weights'].items():
        rel = w['reliability']
        if rel > 0:
            ou_active += 1
        print(f'{name:24s} {w["auc"]:7.3f} {w["sign"]:5d} {rel:12.4f} {w["n"]:4d}')
    print('=' * 78)
    print(f'生效信号(reliability>0): {ou_active} / {len(ou_result["weights"])}')

    if dry_run:
        print('\n[dry-run] 未写入文件。')
        return 0

    out_path = paths.runtime_file('operation_weights.json')
    payload = dict(result)
    payload['ou_axis'] = ou_result
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n已写入: {out_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
更新4-18和4-19五大联赛预测数据
自动更新实际结果和准确率，并将爆冷案例添加到爆冷案例库
"""

import os
import re
import json
from datetime import datetime
from pathlib import Path

# 定义联赛信息
LEAGUES = {
    'premier_league': {
        'name': '英超',
        'file': 'premier_league/analysis/predictions/2026-04-18_predictions.md',
        'matches': [
            {'date': '2026-04-19', 'home': '诺丁汉', 'away': '伯恩利', 'result': '主胜', 'score': '2-0', 'predicted': '主胜'},
            {'date': '2026-04-19', 'home': '维拉', 'away': '桑德兰', 'result': '主胜', 'score': '3-1', 'predicted': '主胜'},
            {'date': '2026-04-19', 'home': '埃弗顿', 'away': '利物浦', 'result': '客胜', 'score': '0-2', 'predicted': '客胜'},
            {'date': '2026-04-19', 'home': '曼城', 'away': '阿森纳', 'result': '主胜', 'score': '2-1', 'predicted': '主胜'}
        ]
    },
    'serie_a': {
        'name': '意甲',
        'file': 'serie_a/analysis/predictions/2026-04-18_predictions.md',
        'matches': [
            {'date': '2026-04-19', 'home': '克雷莫纳', 'away': '都灵', 'result': '客胜', 'score': '0-1', 'predicted': '平局/客胜'},
            {'date': '2026-04-19', 'home': '维罗纳', 'away': 'AC米兰', 'result': '客胜', 'score': '0-3', 'predicted': '客胜'}
        ]
    },
    'bundesliga': {
        'name': '德甲',
        'file': 'bundesliga/analysis/predictions/2026-04-18_predictions.md',
        'matches': [
            {'date': '2026-04-19', 'home': '斯图加特', 'away': '法兰克福', 'result': '主胜', 'score': '2-0', 'predicted': '主胜/平局'},
            {'date': '2026-04-19', 'home': '美因茨', 'away': '门兴', 'result': '主胜', 'score': '2-1', 'predicted': '主胜'}
        ]
    },
    'ligue_1': {
        'name': '法甲',
        'file': 'ligue_1/analysis/predictions/2026-04-18_predictions.md',
        'matches': [
            {'date': '2026-04-19', 'home': '摩纳哥', 'away': '欧塞尔', 'result': '主胜', 'score': '4-0', 'predicted': '主胜'},
            {'date': '2026-04-19', 'home': '斯特拉斯堡', 'away': '雷恩', 'result': '平局', 'score': '1-1', 'predicted': '主不败'}
        ]
    }
}

# 爆冷案例定义
UPSET_CASES = [
    {
        "联赛": "英超",
        "比赛时间": "2026-04-19 03:00",
        "主队": "切尔西",
        "客队": "曼联",
        "主队排名": 6,
        "客队排名": 3,
        "主队积分": 48,
        "客队积分": 55,
        "积分差": -7,
        "预测结果": "切尔西胜",
        "实际结果": "曼联胜",
        "比分": "0-1",
        "爆冷类型": "主胜被爆冷",
        "爆冷指数": "高",
        "主要因素": ["切尔西近期状态极差（近6场5负1平）", "曼联反击犀利", "切尔西核心缺阵"],
        "赔率变化": "主胜赔率从1.85升至2.25，客胜赔率从3.20降至2.80",
        "是否验证": "是",
        "验证时间": "2026-04-19",
        "记录时间": "2026-04-19"
    },
    {
        "联赛": "德甲",
        "比赛时间": "2026-04-18 21:30",
        "主队": "霍芬海姆",
        "客队": "多特蒙德",
        "主队排名": 6,
        "客队排名": 3,
        "主队积分": 51,
        "客队积分": 62,
        "积分差": -11,
        "预测结果": "多特蒙德胜",
        "实际结果": "霍芬海姆胜",
        "比分": "2-1",
        "爆冷类型": "客胜被爆冷",
        "爆冷指数": "高",
        "主要因素": ["多特蒙德核心埃姆雷·詹+吉拉西缺阵", "霍芬海姆主场优势", "多特蒙德进攻火力下降"],
        "赔率变化": "客胜赔率从1.80升至2.10，主胜赔率从3.50降至3.20",
        "是否验证": "是",
        "验证时间": "2026-04-18",
        "记录时间": "2026-04-19"
    },
    {
        "联赛": "法甲",
        "比赛时间": "2026-04-18 23:00",
        "主队": "洛里昂",
        "客队": "马赛",
        "主队排名": 9,
        "客队排名": 4,
        "主队积分": 49,
        "客队积分": 68,
        "积分差": -19,
        "预测结果": "马赛胜",
        "实际结果": "洛里昂胜",
        "比分": "2-0",
        "爆冷类型": "客胜被爆冷",
        "爆冷指数": "高",
        "主要因素": ["洛里昂主场92%不败率", "马赛客场防守差", "马赛核心缺阵"],
        "赔率变化": "客胜赔率从1.70升至1.90，主胜赔率从4.00降至3.50",
        "是否验证": "是",
        "验证时间": "2026-04-18",
        "记录时间": "2026-04-19"
    },
    {
        "联赛": "意甲",
        "比赛时间": "2026-04-19 00:00",
        "主队": "那不勒斯",
        "客队": "拉齐奥",
        "主队排名": 2,
        "客队排名": 9,
        "主队积分": 73,
        "客队积分": 56,
        "积分差": -17,
        "预测结果": "那不勒斯胜",
        "实际结果": "拉齐奥胜",
        "比分": "0-2",
        "爆冷类型": "主胜被爆冷",
        "爆冷指数": "高",
        "主要因素": ["那不勒斯核心卢卡库+内雷斯缺阵", "拉齐奥意大利杯留力", "那不勒斯进攻受损"],
        "赔率变化": "主胜赔率从1.40升至1.60，客胜赔率从6.00降至5.00",
        "是否验证": "是",
        "验证时间": "2026-04-19",
        "记录时间": "2026-04-19"
    }
]

def update_prediction_file(file_path, matches):
    """更新预测文件"""
    if not os.path.exists(file_path):
        print(f"文件不存在: {file_path}")
        return
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 更新比赛结果
    for match in matches:
        # 构建搜索模式
        pattern = rf"\| {match['date']} \|.*?{match['home']} \| {match['away']} \|.*?\| 待更新 \| 待更新 \| 待更新 \|"
        replacement = f"| {match['date']} | {match['home']} | {match['away']} | {match['predicted']} | | | | {match['result']} | {match['score']} | {'✅' if match['result'] in match['predicted'] else '❌'} |"
        content = re.sub(pattern, replacement, content, flags=re.DOTALL)
    
    # 更新统计信息
    # 计算已完成比赛数和正确数
    completed_matches = re.findall(r'\|.*?\|.*?\|.*?\|.*?\|.*?\|.*?\|.*?\|.*?\|.*?\|.*?\| (✅|❌) \|', content)
    total_completed = len(completed_matches)
    correct_count = completed_matches.count('✅')
    accuracy = (correct_count / total_completed * 100) if total_completed > 0 else 0
    
    # 更新统计字段
    content = re.sub(r'- \*\*已完成比赛\*\*：\d+', f'- **已完成比赛**：{total_completed}', content)
    content = re.sub(r'- \*\*预测正确数\*\*：\d+', f'- **预测正确数**：{correct_count}', content)
    content = re.sub(r'- \*\*准确率\*\*：.*?%', f'- **准确率**：{accuracy:.1f}%', content)
    
    # 写回文件
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"更新完成: {file_path}")
    print(f"已完成比赛: {total_completed}, 正确数: {correct_count}, 准确率: {accuracy:.1f}%")

def update_upset_case_library():
    """更新爆冷案例库"""
    library_path = '爆冷案例库.json'
    
    # 读取现有案例库
    if os.path.exists(library_path):
        with open(library_path, 'r', encoding='utf-8') as f:
            try:
                existing_cases = json.load(f)
            except json.JSONDecodeError:
                existing_cases = []
    else:
        existing_cases = []
    
    # 添加新的爆冷案例
    new_cases_added = 0
    for new_case in UPSET_CASES:
        # 检查是否已存在
        case_exists = False
        for case in existing_cases:
            # 检查必要字段是否存在
            if '联赛' in case and '主队' in case and '客队' in case:
                if (case['联赛'] == new_case['联赛'] and 
                    case['主队'] == new_case['主队'] and 
                    case['客队'] == new_case['客队']):
                    # 如果有比赛时间字段，也进行比较
                    if '比赛时间' in case and '比赛时间' in new_case:
                        if case['比赛时间'] == new_case['比赛时间']:
                            case_exists = True
                            break
                    else:
                        # 如果没有比赛时间字段，仅通过联赛、主队、客队判断
                        case_exists = True
                        break
        
        if not case_exists:
            existing_cases.append(new_case)
            new_cases_added += 1
    
    # 写回案例库
    with open(library_path, 'w', encoding='utf-8') as f:
        json.dump(existing_cases, f, ensure_ascii=False, indent=2)
    
    print(f"\n爆冷案例库更新完成")
    print(f"新增爆冷案例: {new_cases_added}")
    print(f"总案例数: {len(existing_cases)}")

def main():
    """Deprecated: legacy script targeting analysis/predictions paths and hardcoded home directories."""
    print("=" * 80)
    print("该脚本已废弃：旧版预测文件与路径（analysis/predictions + 硬编码 /Users/lin）不再是主流程。")
    print()
    print("请改用当前正式入口：")
    print("  - 预测写回:  cd europe_leagues && python3 prediction_system.py predict-schedule --league <league> --date <YYYY-MM-DD> --days 1 --json")
    print("  - 赛果回填:  cd europe_leagues && python3 prediction_system.py save-result --match-id <match_id> --home-score <n> --away-score <n> --json")
    print("  - 更新统计:  cd europe_leagues && python3 prediction_system.py accuracy --refresh --json")
    print("=" * 80)
    return

if __name__ == "__main__":
    main()

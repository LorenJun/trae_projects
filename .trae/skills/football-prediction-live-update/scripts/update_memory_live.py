#!/usr/bin/env python3
"""
足球预测临场数据更新脚本
用于自动更新MEMORY.md中的滚动记忆，整合临场数据（首发、伤停、赔率变化）

使用方法:
    python3 update_memory_live.py --match-id la_liga_20260515_赫罗纳_皇家社会 \
        --lineups "赫罗纳:球员A,球员B...|皇家社会:球员C,球员D..." \
        --odds-change "2.13->1.99" \
        --confidence-boost 12 \
        --reasoning "临场降强信号，亚盘升盘"
"""

import re
import argparse
from datetime import datetime
from typing import Dict, List, Optional


def parse_args():
    parser = argparse.ArgumentParser(description='更新MEMORY.md临场数据')
    parser.add_argument('--match-id', required=True, help='比赛ID')
    parser.add_argument('--lineups', help='首发阵容，格式: "主队:球员列表|客队:球员列表"')
    parser.add_argument('--injuries', help='伤停更新')
    parser.add_argument('--odds-change', help='赔率变化，格式: "初盘->临盘"')
    parser.add_argument('--asian-change', help='亚盘变化')
    parser.add_argument('--confidence-boost', type=int, default=0, help='信心度提升值')
    parser.add_argument('--direction-change', help='方向变化，格式: "原预测->新预测"')
    parser.add_argument('--score-change', help='比分变化，格式: "原比分->新比分"')
    parser.add_argument('--reasoning', required=True, help='调整说明/理由')
    parser.add_argument('--memory-file', default='MEMORY.md', help='MEMORY.md文件路径')
    return parser.parse_args()


def build_live_analysis_section(args) -> str:
    """构建临场分析依据部分"""
    sections = []
    
    if args.lineups:
        home, away = args.lineups.split('|')
        sections.append(f"    - 首发阵容: [{home}] [{away}]")
    
    if args.injuries:
        sections.append(f"    - 伤停更新: {args.injuries}")
    
    if args.odds_change:
        sections.append(f"    - 赔率变化: 主胜{args.odds_change}({'↓' if '->' in args.odds_change and float(args.odds_change.split('->')[0]) > float(args.odds_change.split('->')[1]) else '↑'})")
    
    if args.asian_change:
        sections.append(f"    - 亚盘变化: {args.asian_change}")
    
    sections.append(f"    - 调整说明: {args.reasoning}")
    
    return '\n'.join(sections)


def update_prediction_line(original_line: str, args) -> str:
    """更新预测行，添加【临场更新】标记"""
    # 提取原预测信息
    match = re.search(r'预测:\s*(.+?)\s*\|', original_line)
    if match:
        original_pred = match.group(1).strip()
        # 添加【临场更新】标记
        new_line = original_line.replace('预测:', '【临场更新】预测:')
        
        # 如果有方向变化，更新概率
        if args.direction_change:
            old, new = args.direction_change.split('->')
            new_line = re.sub(r'\(\d+\.\d+%\)', f'({new}%)', new_line)
        
        # 如果有比分变化，更新比分
        if args.score_change:
            old, new = args.score_change.split('->')
            new_line = new_line.replace(old, new)
        
        return new_line
    
    return original_line


def update_memory_file(args):
    """更新MEMORY.md文件"""
    
    # 读取文件
    with open(args.memory_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 查找比赛记录
    match_pattern = rf"(- \[.*?{args.match_id}.*?\n)(.*?)(?=\n- \[|\Z)"
    
    match_obj = re.search(match_pattern, content, re.DOTALL)
    if not match_obj:
        print(f"错误: 未找到比赛记录 {args.match_id}")
        return False
    
    original_record = match_obj.group(0)
    
    # 更新预测行
    updated_record = update_prediction_line(original_record, args)
    
    # 更新或添加临场分析
    if '◦ 临场分析依据:' in updated_record:
        # 替换现有临场分析
        updated_record = re.sub(
            r'◦ 临场分析依据:.*?(?=\n  ·|\Z)',
            f'◦ 临场分析依据:\n{build_live_analysis_section(args)}',
            updated_record,
            flags=re.DOTALL
        )
    else:
        # 添加新的临场分析
        # 找到更新时间行之前
        updated_record = re.sub(
            r'(· MatchID:.*?\n)',
            f'◦ 临场分析依据:\n{build_live_analysis_section(args)}\n\1',
            updated_record
        )
    
    # 更新时间戳
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    updated_record = re.sub(
        r'更新时间: \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',
        f'更新时间: {current_time}',
        updated_record
    )
    
    # 替换原内容
    updated_content = content.replace(original_record, updated_record)
    
    # 写回文件
    with open(args.memory_file, 'w', encoding='utf-8') as f:
        f.write(updated_content)
    
    print(f"✅ 已更新 {args.match_id}")
    print(f"   更新时间: {current_time}")
    return True


def main():
    args = parse_args()
    
    try:
        success = update_memory_file(args)
        if success:
            print("\n更新内容预览:")
            print(f"  - 比赛ID: {args.match_id}")
            if args.direction_change:
                print(f"  - 方向变化: {args.direction_change}")
            if args.confidence_boost:
                print(f"  - 信心提升: +{args.confidence_boost}%")
            print(f"  - 调整理由: {args.reasoning}")
    except Exception as e:
        print(f"❌ 更新失败: {e}")
        raise


if __name__ == '__main__':
    main()

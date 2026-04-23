#!/usr/bin/env python3
"""
数据导入脚本
将项目中的所有预测数据、球员数据和爆冷案例库数据导入到预测历史数据库
"""

import os
import json
import re
from datetime import datetime


def parse_teams_schedule_file(file_path: str) -> tuple:
    """从 teams_2025-26.md 的赛程表解析预测与赛果。

    约定：
    - 表头格式：| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |
    - 预测写在备注列，形如：`进行中；预测:主胜 信心:0.48 爆冷:低`
    - 已结束比赛的比分列为 `x-y`
    """
    predictions = []
    results = []

    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    league = os.path.basename(os.path.dirname(file_path))
    league_name_map = {
        'premier_league': '英超联赛',
        'serie_a': '意甲联赛',
        'bundesliga': '德甲联赛',
        'ligue_1': '法甲联赛',
        'la_liga': '西甲联赛',
    }
    league_name = league_name_map.get(league, league)

    for line in lines:
        line = line.strip()
        if not line.startswith('|'):
            continue
        cols = [col.strip() for col in line.split('|') if col.strip()]
        if len(cols) != 6:
            continue

        date, match_time, home_team, score_text, away_team, note = cols
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
            continue
        if not home_team or not away_team:
            continue

        match_id = f"{league}_{date.replace('-', '')}_{home_team}_{away_team}"
        match_id = re.sub(r'\s+', '_', match_id)

        # Parse prediction from note
        # Supported note examples:
        # - 进行中；预测:主胜 信心:0.48 爆冷:低
        # - 已结束/预测主胜✅比分完全正确
        predicted = None
        prob = ''
        m_pred = re.search(r'预测:\s*(主胜|平局|客胜)', note)
        if not m_pred:
            m_pred = re.search(r'预测\s*(主胜|平局|客胜)', note)
        if m_pred:
            predicted = m_pred.group(1)
            m_prob = re.search(r'信心:\s*([0-9.]+)', note)
            if m_prob:
                prob = m_prob.group(1)

        predicted_winner = None
        if predicted == '主胜':
            predicted_winner = 'home'
        elif predicted == '客胜':
            predicted_winner = 'away'
        elif predicted == '平局':
            predicted_winner = 'draw'

        if predicted_winner:
            predictions.append({
                'match_id': match_id,
                'league': league,
                'league_name': league_name,
                'home_team': home_team,
                'away_team': away_team,
                'match_date': date,
                'match_time': match_time,
                'predicted_winner': predicted_winner,
                'predicted_score': '',
                'predicted_probability': prob,
                'over_under': '',
                'handicap': '',
                'correct': False,
                'model_predictions': {},
                'saved_at': datetime.now().isoformat()
            })

        # Parse actual result from score
        if re.match(r'^\d+\s*-\s*\d+$', score_text):
            hs, as_ = [int(x.strip()) for x in score_text.split('-')]
            if hs > as_:
                actual_winner = 'home'
            elif hs < as_:
                actual_winner = 'away'
            else:
                actual_winner = 'draw'
            results.append({
                'match_id': match_id,
                'league': league,
                'home_team': home_team,
                'away_team': away_team,
                'match_date': date,
                'match_time': match_time,
                'actual_winner': actual_winner,
                'actual_score': f'{hs}-{as_}',
                'home_score': hs,
                'away_score': as_,
                'result_status': 'completed'
            })

    return predictions, results


def parse_prediction_file(file_path: str) -> tuple:
    """解析预测文件，返回预测和结果"""
    predictions = []
    results = []
    
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"\n=== 解析文件: {file_path} ===")
    
    # 提取联赛信息
    league_name = '未知联赛'
    league = 'unknown'
    
    for line in lines:
        if line.startswith('# '):
            # 尝试提取联赛名称
            if '2025-26' in line:
                league_name = line.split('2025-26')[0].replace('# ', '').strip()
            elif '联赛' in line:
                league_name = line.split('联赛')[0].replace('# ', '').strip()
            break
    
    print(f"联赛: {league_name}")
    
    # 映射联赛名称到英文代码
    league_mapping = {
        '英超联赛': 'premier_league',
        '意甲联赛': 'serie_a',
        '德甲联赛': 'bundesliga',
        '法甲联赛': 'ligue_1',
        '西甲联赛': 'la_liga',
        '英超': 'premier_league',
        '意甲': 'serie_a',
        '德甲': 'bundesliga',
        '法甲': 'ligue_1',
        '西甲': 'la_liga'
    }
    league = league_mapping.get(league_name, 'unknown')
    print(f"联赛代码: {league}")
    
    # 找到预测表格的开始和结束
    table_start = -1
    table_end = -1
    
    for i, line in enumerate(lines):
        if '| 比赛日期 |' in line and '| 预测是否正确 |' in line:
            table_start = i
        elif table_start != -1 and line.strip() == '' and table_end == -1:
            table_end = i
            break
    
    if table_start == -1:
        print("未找到预测表格")
        return predictions, results
    
    if table_end == -1:
        table_end = len(lines)
    
    print(f"表格位置: 第{table_start+1}行到第{table_end}行")
    
    # 解析表格数据
    table_lines = lines[table_start+1:table_end]
    print(f"表格行数: {len(table_lines)}")
    
    for i, line in enumerate(table_lines):
        line = line.strip()
        if not line or not line.startswith('|'):
            continue
        
        print(f"\n处理第{i+2}行: {line}")
        
        # 解析表格行
        columns = [col.strip() for col in line.split('|') if col.strip()]
        print(f"解析到 {len(columns)} 列: {columns}")
        
        # 跳过表头分隔线
        if all(col == '-----' or col == '------' or col == '-----------' or col == '-------------' for col in columns):
            print("跳过表头分隔线")
            continue
        
        if len(columns) < 8:
            print(f"列数不足，跳过")
            continue
        
        # 提取数据
        date = ''
        time = ''
        home_team = ''
        away_team = ''
        predicted = ''
        predicted_score = ''
        probability = ''
        over_under = ''
        handicap = ''
        actual = ''
        actual_score = ''
        correct = ''
        
        # 处理不同的表格格式
        if len(columns) == 11:
            # 格式1: 标准格式（无时间列）
            date = columns[0]
            home_team = columns[1]
            away_team = columns[2]
            predicted = columns[3]
            predicted_score = columns[4]
            probability = columns[5]
            over_under = columns[6]
            handicap = columns[7]
            actual = columns[8]
            actual_score = columns[9]
            correct = columns[10]
        elif len(columns) == 12:
            # 格式2: 包含时间列
            date = columns[0]
            time = columns[1]
            home_team = columns[2]
            away_team = columns[3]
            predicted = columns[4]
            predicted_score = columns[5]
            probability = columns[6]
            over_under = columns[7]
            handicap = columns[8]
            actual = columns[9]
            actual_score = columns[10]
            correct = columns[11]
        else:
            # 尝试最佳匹配
            if len(columns) >= 9:
                date = columns[0]
                if len(columns) > 1 and ':' in columns[1]:
                    # 包含时间列
                    time = columns[1]
                    home_team = columns[2]
                    away_team = columns[3]
                    predicted = columns[4]
                    if len(columns) > 5:
                        predicted_score = columns[5]
                    if len(columns) > 6:
                        probability = columns[6]
                    if len(columns) > 7:
                        over_under = columns[7]
                    if len(columns) > 8:
                        actual = columns[8]
                    if len(columns) > 9:
                        actual_score = columns[9]
                    if len(columns) > 10:
                        correct = columns[10]
                else:
                    # 不包含时间列
                    home_team = columns[1]
                    away_team = columns[2]
                    predicted = columns[3]
                    if len(columns) > 4:
                        predicted_score = columns[4]
                    if len(columns) > 5:
                        probability = columns[5]
                    if len(columns) > 6:
                        over_under = columns[6]
                    if len(columns) > 7:
                        actual = columns[7]
                    if len(columns) > 8:
                        actual_score = columns[8]
                    if len(columns) > 9:
                        correct = columns[9]
            else:
                print(f"不支持的列数: {len(columns)}")
                continue
        
        print(f"解析结果:")
        print(f"  日期: {date}")
        print(f"  时间: {time}")
        print(f"  主队: {home_team}")
        print(f"  客队: {away_team}")
        print(f"  预测: {predicted}")
        print(f"  预测比分: {predicted_score}")
        print(f"  概率: {probability}")
        print(f"  大小球: {over_under}")
        print(f"  实际结果: {actual}")
        print(f"  实际比分: {actual_score}")
        print(f"  是否正确: {correct}")
        
        # 跳过无效数据
        if not home_team or not away_team or not date or home_team == '------' or away_team == '------':
            print(f"数据不完整，跳过")
            continue
        
        # 提取实际结果
        if actual == '主胜':
            actual_winner = 'home'
        elif actual == '客胜':
            actual_winner = 'away'
        elif actual == '平局':
            actual_winner = 'draw'
        else:
            actual_winner = None
        
        # 提取预测结果
        if '主胜' in predicted:
            predicted_winner = 'home'
        elif '客胜' in predicted:
            predicted_winner = 'away'
        elif '平局' in predicted:
            predicted_winner = 'draw'
        else:
            predicted_winner = None
        
        # 生成match_id
        match_id = f"{league}_{date.replace('-', '')}_{home_team}_{away_team}"
        match_id = re.sub(r'\s+', '_', match_id)
        
        # 保存预测
        prediction = {
            'match_id': match_id,
            'league': league,
            'league_name': league_name,
            'home_team': home_team,
            'away_team': away_team,
            'match_date': date,
            'match_time': time,
            'predicted_winner': predicted_winner,
            'predicted_score': predicted_score,
            'predicted_probability': probability,
            'over_under': over_under,
            'handicap': handicap,
            'correct': correct == '✅' or correct == '是',
            'model_predictions': {
                'expert': {
                    'home_win': 0.5 if predicted_winner == 'home' else 0.2,
                    'draw': 0.5 if predicted_winner == 'draw' else 0.3,
                    'away_win': 0.5 if predicted_winner == 'away' else 0.2
                }
            },
            'saved_at': datetime.now().isoformat()
        }
        predictions.append(prediction)
        print(f"  保存预测: {match_id}")
        
        # 保存结果（如果有实际结果）
        if actual and actual != '待更新' and actual != '待进行' and actual_score and actual_score != '待更新' and actual_score != '-':
            # 解析实际比分
            score_parts = actual_score.split('-')
            if len(score_parts) == 2:
                try:
                    home_score = int(score_parts[0])
                    away_score = int(score_parts[1])
                except ValueError:
                    home_score = 0
                    away_score = 0
            else:
                home_score = 0
                away_score = 0
            
            result = {
                'match_id': match_id,
                'league': league,
                'home_team': home_team,
                'away_team': away_team,
                'match_date': date,
                'match_time': time,
                'actual_winner': actual_winner,
                'actual_score': actual_score,
                'home_score': home_score,
                'away_score': away_score,
                'result_status': 'completed'
            }
            results.append(result)
            print(f"  保存结果: {match_id} - {actual_score}")
    
    print(f"\n解析完成: {len(predictions)} 个预测, {len(results)} 个结果")
    return predictions, results


def import_prediction_files():
    """已废弃：prediction_history/ 不再作为数据源或存储。

    teams_2025-26.md 是唯一数据源；准确率由 result_manager.py 直接解析计算。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    leagues = ['premier_league', 'serie_a', 'bundesliga', 'ligue_1', 'la_liga']
    
    total_preds = 0
    total_results = 0
    for league in leagues:
        teams_file = os.path.join(base_dir, league, "teams_2025-26.md")
        if not os.path.exists(teams_file):
            continue

        print(f"处理文件: {teams_file}")
        predictions, results = parse_teams_schedule_file(teams_file)
        total_preds += len(predictions)
        total_results += len(results)
        print(f"  解析预测: {len(predictions)} 条，解析赛果: {len(results)} 条")

    print(f"\n汇总：预测 {total_preds} 条，赛果 {total_results} 条（未写入 prediction_history/）")


def import_player_data():
    """导入球员数据"""
    player_data = {}
    
    leagues = ['premier_league', 'serie_a', 'bundesliga', 'ligue_1', 'la_liga']
    
    for league in leagues:
        players_dir = f"{league}/players"
        if not os.path.exists(players_dir):
            continue
        
        files = os.listdir(players_dir)
        player_files = [f for f in files if f.endswith('.json')]
        
        player_data[league] = {}
        for file_name in player_files:
            team_name = os.path.splitext(file_name)[0]
            file_path = os.path.join(players_dir, file_name)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                player_data[league][team_name] = data
                print(f"导入球员数据: {league}/{team_name}")
            except Exception as e:
                print(f"导入球员数据失败: {file_path}, 错误: {e}")
    
    # 保存球员数据
    with open('player_data.json', 'w', encoding='utf-8') as f:
        json.dump(player_data, f, ensure_ascii=False, indent=2)


def import_upset_cases():
    """导入爆冷案例库"""
    if not os.path.exists('爆冷案例库.json'):
        print("爆冷案例库文件不存在")
        return
    
    with open('爆冷案例库.json', 'r', encoding='utf-8') as f:
        cases = json.load(f)
    
    print(f"导入爆冷案例: {len(cases)} 个")
    
    # 保存为标准格式
    upset_data = {
        'cases': cases,
        'imported_at': datetime.now().isoformat()
    }
    
    with open('upset_cases.json', 'w', encoding='utf-8') as f:
        json.dump(upset_data, f, ensure_ascii=False, indent=2)


def update_web_data():
    """更新Web界面数据"""
    # prediction_history/ 已废弃；这里仅更新运行时准确率统计文件
    from result_manager import ResultManager, print_accuracy_report
    manager = ResultManager()
    stats = manager.update_accuracy_stats()
    print_accuracy_report(stats)
    print("\n准确率统计已更新（来源：teams_2025-26.md）")


def main():
    """主函数"""
    print("开始导入数据...")
    
    # 1. 导入预测文件
    print("\n1. 导入预测文件")
    import_prediction_files()
    
    # 2. 导入球员数据
    print("\n2. 导入球员数据")
    import_player_data()
    
    # 3. 导入爆冷案例库
    print("\n3. 导入爆冷案例库")
    import_upset_cases()
    
    # 4. 更新准确率统计（基于 teams_2025-26.md）
    print("\n4. 更新准确率统计")
    update_web_data()
    
    print("\n数据导入完成！")


if __name__ == "__main__":
    main()

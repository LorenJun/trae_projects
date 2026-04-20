#!/usr/bin/env python3
"""
优化的预测比赛流程
整合球员数据、多数据源收集、爆冷分析和预测生成
"""

import os
import json
from datetime import datetime, timedelta
import logging
from glob import glob

# 优先复用增强版预测器（包含：动态调权 + 历史相似盘路 + 更完整的爆冷分析）
try:
    from enhanced_prediction_workflow import EnhancedPredictor
except Exception:
    EnhancedPredictor = None

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='prediction_workflow.log'
)

# 定义联赛信息
LEAGUES = {
    'premier_league': {
        'name': '英超',
        'code': 'premier_league',
        'teams': ['切尔西', '曼联', '利物浦', '阿森纳', '曼城', '阿斯顿维拉', '诺丁汉森林', '布莱顿', '布伦特福德', '富勒姆', '伯恩茅斯', '水晶宫', '埃弗顿', '狼队', '西汉姆联', '热刺', '利兹联', '伯恩利', '桑德兰']
    },
    'serie_a': {
        'name': '意甲',
        'code': 'serie_a',
        'teams': ['国际米兰', 'AC米兰', '尤文图斯', '那不勒斯', '罗马', '亚特兰大', '拉齐奥', '佛罗伦萨', '博洛尼亚', '乌迪内斯']
    },
    'bundesliga': {
        'name': '德甲',
        'code': 'bundesliga',
        'teams': ['拜仁慕尼黑', '多特蒙德', '勒沃库森', '斯图加特', '柏林联合', '霍芬海姆', '法兰克福', '门兴格拉德巴赫', '沃尔夫斯堡', '美因茨']
    },
    'ligue_1': {
        'name': '法甲',
        'code': 'ligue_1',
        'teams': ['巴黎圣日耳曼', '马赛', '摩纳哥', '里尔', '里昂', '朗斯', '雷恩', '尼斯', '洛里昂', '斯特拉斯堡']
    },
    'la_liga': {
        'name': '西甲',
        'code': 'la_liga',
        'teams': ['巴塞罗那', '皇家马德里', '马德里竞技', '塞维利亚', '皇家社会', '比利亚雷亚尔', '贝蒂斯', '瓦伦西亚', '毕尔巴鄂竞技', '奥萨苏纳']
    }
}

def load_player_data(league_code, team_name):
    """加载球队球员数据"""
    file_path = f"{league_code}/players/{team_name}.json"
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def load_match_data(league_code, date):
    """加载比赛数据"""
    # 优先从联赛目录的赔率落盘文件读取，避免抓取依赖导致的不可用
    odds_matches = load_match_data_from_odds(league_code, date)
    if odds_matches:
        return odds_matches

    # 兜底：集成 data_collector.py 的功能（若可用）
    from data_collector import DataCollector
    collector = DataCollector()
    import asyncio
    matches = asyncio.run(collector.collect_league_data(league_code, date))
    return matches

def load_match_data_from_odds(league_code, date):
    """从联赛目录 analysis/odds/*_odds.json 读取真实赛程与赔率快照。"""
    # 未来赛程优先读取 odds_snapshots（即时赔率快照）
    snapshot_dir = os.path.join(league_code, 'analysis', 'odds_snapshots')
    if os.path.isdir(snapshot_dir):
        for file_path in sorted(glob(os.path.join(snapshot_dir, '*_odds_snapshot.json'))):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    payload = json.load(f)
            except Exception:
                continue
            matches = []
            for record in payload.get('matches', []):
                if record.get('match_date') != date:
                    continue
                home = record.get('home_team')
                away = record.get('away_team')
                if not home or not away:
                    continue
                current_odds = {
                    'match_id': record.get('match_id'),
                    '胜平负赔率': record.get('胜平负赔率', {}),
                    '欧赔': record.get('欧赔', {}),
                    '亚值': record.get('亚值', {}),
                    '凯利': record.get('凯利', {}),
                    '离散率': record.get('离散率', {}),
                }
                matches.append({'home_team': home, 'away_team': away, 'current_odds': current_odds, 'source': file_path})
            if matches:
                return matches

    odds_dir = os.path.join(league_code, 'analysis', 'odds')
    if not os.path.isdir(odds_dir):
        return []

    matches = []
    for file_path in sorted(glob(os.path.join(odds_dir, '*_odds.json'))):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception:
            continue
        for record in payload.get('matches', []):
            if record.get('match_date') != date:
                continue
            home = record.get('home_team')
            away = record.get('away_team')
            if not home or not away:
                continue
            current_odds = {
                'match_id': record.get('match_id'),
                '胜平负赔率': record.get('胜平负赔率', {}),
                '欧赔': record.get('欧赔', {}),
                '亚值': record.get('亚值', {}),
                '凯利': record.get('凯利', {}),
                '离散率': record.get('离散率', {}),
            }
            matches.append({
                'home_team': home,
                'away_team': away,
                'current_odds': current_odds,
                'source': file_path
            })
    return matches

def load_upset_cases():
    """加载爆冷案例库"""
    file_path = '爆冷案例库.json'
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def analyze_team_strength(team_name, league_code):
    """分析球队实力"""
    team_data = load_player_data(league_code, team_name)
    if not team_data:
        return {
            'team': team_name,
            'strength': 50,  # 默认实力值
            'injured_count': 0,
            'suspended_count': 0,
            'key_players_available': True
        }
    
    # 统计球员状态
    total_players = len(team_data['players'])
    injured_players = [p for p in team_data['players'] if p.get('transfer_status') == 'injured']
    suspended_players = [p for p in team_data['players'] if p.get('transfer_status') == 'suspended']
    
    # 计算实力值（基于球员市场价值和状态）
    total_value = sum(p.get('market_value', 0) for p in team_data['players'])
    available_value = sum(p.get('market_value', 0) for p in team_data['players'] if p.get('transfer_status') == 'current')
    
    # 基础实力值
    base_strength = 50
    
    # 根据市场价值调整
    if total_value > 0:
        value_ratio = available_value / total_value
        strength_adjustment = (value_ratio - 0.5) * 50  # -25 到 +25 的调整
        base_strength += strength_adjustment
    
    # 确保实力值在合理范围内
    strength = max(10, min(90, base_strength))
    
    # 检查核心球员是否可用
    key_positions = ['前锋', '中场', '后卫', '门将']
    key_players_available = True
    
    for position in key_positions:
        position_players = [p for p in team_data['players'] if p.get('position') == position and p.get('transfer_status') == 'current']
        if not position_players:
            key_players_available = False
            break
    
    return {
        'team': team_name,
        'strength': strength,
        'injured_count': len(injured_players),
        'suspended_count': len(suspended_players),
        'key_players_available': key_players_available,
        'total_players': total_players,
        'available_players': total_players - len(injured_players) - len(suspended_players)
    }

def predict_match(home_team, away_team, league_code, match_date, current_odds=None):
    """预测比赛结果"""
    # 主流程：走增强版预测器（如果可用）
    if EnhancedPredictor is not None:
        try:
            predictor = EnhancedPredictor()
            enhanced = predictor.predict_match(
                home_team=home_team,
                away_team=away_team,
                league_code=league_code,
                match_date=match_date,
                current_odds=current_odds
            )
            # 兼容旧字段结构（供本文件报告使用）
            return {
                'home_team': home_team,
                'away_team': away_team,
                'prediction': enhanced.get('prediction'),
                'confidence': float(enhanced.get('confidence', 0.0) or 0.0),
                'strength_diff': enhanced.get('strength_diff'),
                'home_strength': enhanced.get('home_strength', {}),
                'away_strength': enhanced.get('away_strength', {}),
                'upset_potential': enhanced.get('upset_potential', {}),
                'match_date': match_date,
                # 额外字段：方便你核对“动态调权是否进入主流程”
                'applied_model_weights': enhanced.get('applied_model_weights'),
                'historical_odds_reference': enhanced.get('historical_odds_reference'),
            }
        except Exception as e:
            logging.warning(f"增强版预测失败，回退旧逻辑: {e}")

    # 分析双方实力
    home_strength = analyze_team_strength(home_team, league_code)
    away_strength = analyze_team_strength(away_team, league_code)
    
    # 计算实力差
    strength_diff = home_strength['strength'] - away_strength['strength']
    
    # 考虑主场优势（+10%）
    home_advantage = 5
    adjusted_diff = strength_diff + home_advantage
    
    # 基于实力差预测结果
    if adjusted_diff > 20:
        prediction = '主胜'
        confidence = 0.85
    elif adjusted_diff > 10:
        prediction = '主胜'
        confidence = 0.75
    elif adjusted_diff > 0:
        prediction = '主胜/平局'
        confidence = 0.65
    elif adjusted_diff > -10:
        prediction = '平局/客胜'
        confidence = 0.65
    elif adjusted_diff > -20:
        prediction = '客胜'
        confidence = 0.75
    else:
        prediction = '客胜'
        confidence = 0.85
    
    # 检查爆冷可能性
    upset_potential = assess_upset_potential(home_team, away_team, league_code, strength_diff)
    
    return {
        'home_team': home_team,
        'away_team': away_team,
        'prediction': prediction,
        'confidence': confidence,
        'strength_diff': strength_diff,
        'home_strength': home_strength,
        'away_strength': away_strength,
        'upset_potential': upset_potential,
        'match_date': match_date
    }

def assess_upset_potential(home_team, away_team, league_code, strength_diff):
    """评估爆冷可能性"""
    upset_cases = load_upset_cases()
    
    # 查找类似的爆冷案例
    similar_cases = []
    for case in upset_cases:
        if case.get('联赛') == LEAGUES[league_code]['name']:
            # 检查是否涉及相同的球队
            if (case.get('主队') == home_team and case.get('客队') == away_team) or \
               (case.get('主队') == away_team and case.get('客队') == home_team):
                similar_cases.append(case)
    
    # 计算爆冷指数
    upset_index = 0
    
    # 基于实力差
    if abs(strength_diff) > 15:
        upset_index += 30  # 实力差距越大，爆冷可能性越高
    
    # 基于历史爆冷案例
    if similar_cases:
        upset_index += len(similar_cases) * 20
    
    # 基于球员状态
    home_strength = analyze_team_strength(home_team, league_code)
    away_strength = analyze_team_strength(away_team, league_code)
    
    if home_strength['injured_count'] >= 3 or not home_strength['key_players_available']:
        upset_index += 25
    
    if away_strength['injured_count'] >= 3 or not away_strength['key_players_available']:
        upset_index += 25
    
    # 确保爆冷指数在合理范围内
    upset_index = min(100, upset_index)
    
    # 爆冷等级
    if upset_index >= 70:
        upset_level = '高'
    elif upset_index >= 40:
        upset_level = '中'
    else:
        upset_level = '低'
    
    return {
        'index': upset_index,
        'level': upset_level,
        'similar_cases_count': len(similar_cases)
    }

def generate_prediction_report(league_code, date):
    """生成预测报告"""
    # 加载比赛数据
    matches = load_match_data(league_code, date)
    
    if not matches:
        logging.warning(f"没有找到 {LEAGUES[league_code]['name']} {date} 的比赛数据")
        return None
    
    # 生成预测
    predictions = []
    for match in matches:
        # 兼容两类结构：
        # - odds落盘读取：dict {'home_team','away_team','current_odds'}
        # - data_collector：MatchData对象（可能带 odds_data）
        if isinstance(match, dict):
            home_team = match.get('home_team')
            away_team = match.get('away_team')
            current_odds = match.get('current_odds')
        else:
            home_team = getattr(match, 'home_team', None)
            away_team = getattr(match, 'away_team', None)
            current_odds = getattr(match, 'odds_data', None)

        if not home_team or not away_team:
            continue
        prediction = predict_match(
            home_team,
            away_team,
            league_code,
            date,
            current_odds=current_odds
        )
        predictions.append(prediction)
    
    # 生成报告内容
    report_content = f"# {LEAGUES[league_code]['name']} 联赛 2025-26 赛季 预测分析\n\n"
    report_content += f"## 预测日期: {date}\n\n"
    report_content += "## 比赛预测\n\n"
    report_content += "| 日期 | 主队 | 客队 | 预测结果 | 信心指数 | 爆冷可能性 | 备注 |\n"
    report_content += "|------|------|------|----------|----------|------------|------|\n"
    
    for pred in predictions:
        report_content += f"| {date} | {pred['home_team']} | {pred['away_team']} | {pred['prediction']} | {pred['confidence']:.2f} | {pred['upset_potential']['level']} | "
        
        # 添加备注
        notes = []
        # 动态调权诊断（增强版才有）
        diag = pred.get('applied_model_weights')
        if isinstance(diag, dict) and 'has_enough_samples' in diag:
            if diag.get('has_enough_samples'):
                notes.append("动态调权:已生效")
            else:
                notes.append("动态调权:样本不足")
        if pred['home_strength']['injured_count'] > 0:
            notes.append(f"{pred['home_team']}伤病{pred['home_strength']['injured_count']}人")
        if pred['away_strength']['injured_count'] > 0:
            notes.append(f"{pred['away_team']}伤病{pred['away_strength']['injured_count']}人")
        if pred['upset_potential']['level'] == '高':
            notes.append("爆冷警告")
        # 历史相似盘路参考（增强版才有）
        odds_ref = pred.get('historical_odds_reference')
        if isinstance(odds_ref, dict) and odds_ref.get('available'):
            summary = odds_ref.get('summary', {})
            notes.append(f"相似盘{summary.get('sample_size', 0)}场")
        
        note_str = '; '.join(notes) if notes else '无'
        report_content += f"{note_str} |\n"
    
    # 添加统计信息
    high_confidence = [p for p in predictions if p['confidence'] >= 0.8]
    medium_confidence = [p for p in predictions if 0.6 <= p['confidence'] < 0.8]
    low_confidence = [p for p in predictions if p['confidence'] < 0.6]
    
    report_content += f"\n## 预测统计\n\n"
    report_content += f"- **总比赛数**: {len(predictions)}\n"
    report_content += f"- **高信心预测** (≥80%): {len(high_confidence)}\n"
    report_content += f"- **中等信心预测** (60%-80%): {len(medium_confidence)}\n"
    report_content += f"- **低信心预测** (<60%): {len(low_confidence)}\n"
    
    # 爆冷警告
    upset_warnings = [p for p in predictions if p['upset_potential']['level'] == '高']
    if upset_warnings:
        report_content += f"\n## 爆冷警告\n\n"
        for warning in upset_warnings:
            report_content += f"- {warning['home_team']} vs {warning['away_team']}: 爆冷可能性高\n"
    
    # 保存报告
    report_dir = f"{league_code}/analysis/predictions"
    os.makedirs(report_dir, exist_ok=True)
    report_file = f"{report_dir}/{date}_predictions.md"
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write(report_content)
    
    logging.info(f"生成 {LEAGUES[league_code]['name']} {date} 的预测报告: {report_file}")
    return report_file

def main():
    """主函数"""
    print("=" * 60)
    print("优化的预测比赛流程")
    print("=" * 60)
    
    # 切换到项目根目录（使用相对路径）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)
    
    # 预测未来3天的比赛（减少到3天提高效率）
    today = datetime.now()
    for i in range(3):
        target_date = (today + timedelta(days=i)).strftime('%Y-%m-%d')
        
        for league_code in LEAGUES:
            print(f"\n生成 {LEAGUES[league_code]['name']} {target_date} 的预测...")
            report_file = generate_prediction_report(league_code, target_date)
            if report_file:
                print(f"  生成成功: {report_file}")
            else:
                print(f"  没有找到比赛数据")
    
    print("\n" + "=" * 60)
    print("预测流程执行完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()

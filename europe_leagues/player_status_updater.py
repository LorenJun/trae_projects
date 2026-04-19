#!/usr/bin/env python3
"""
球员状态定期更新脚本
用于每周更新球员的伤病和停赛情况
"""

import os
import json
from datetime import datetime, timedelta
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='player_status_update.log'
)

# 定义联赛和球队信息
LEAGUES = {
    'premier_league': {
        'name': '英超',
        'teams': ['切尔西', '曼联', '利物浦', '阿森纳', '曼城']
    },
    'serie_a': {
        'name': '意甲',
        'teams': ['那不勒斯', '罗马']
    },
    'bundesliga': {
        'name': '德甲',
        'teams': ['多特蒙德', '拜仁慕尼黑']
    },
    'ligue_1': {
        'name': '法甲',
        'teams': ['马赛', '巴黎圣日耳曼']
    },
    'la_liga': {
        'name': '西甲',
        'teams': ['巴塞罗那', '皇家马德里']
    }
}

def load_player_data(league_code, team_name):
    """加载球队球员数据"""
    file_path = f"{league_code}/players/{team_name}.json"
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None

def save_player_data(league_code, team_name, data):
    """保存球队球员数据"""
    file_path = f"{league_code}/players/{team_name}.json"
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def update_player_status():
    """更新球员状态"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    logging.info("开始更新球员状态")
    
    # 模拟伤病和停赛数据（实际应用中应从API获取）
    # 这里使用示例数据，实际应用中应替换为真实数据源
    status_updates = {
        'premier_league': {
            '切尔西': [
                {'name': '恩昆库', 'status': 'injured', 'reason': '膝盖伤势', 'expected_return': '2026-05-15'},
                {'name': '福法纳', 'status': 'injured', 'reason': ' ankle injury', 'expected_return': '2026-05-10'}
            ],
            '曼联': [
                {'name': '马奎尔', 'status': 'suspended', 'reason': '累积黄牌停赛', 'expected_return': '2026-04-22'},
                {'name': '霍伊伦', 'status': 'injured', 'reason': '肌肉拉伤', 'expected_return': '2026-04-25'},
                {'name': '万·比萨卡', 'status': 'injured', 'reason': '小腿伤势', 'expected_return': '2026-04-30'}
            ],
            '利物浦': [
                {'name': '萨拉赫', 'status': 'current', 'reason': '恢复训练'},
                {'name': '迪亚斯', 'status': 'current', 'reason': '状态良好'}
            ],
            '阿森纳': [
                {'name': '热苏斯', 'status': 'current', 'reason': '状态良好'},
                {'name': '托马斯', 'status': 'current', 'reason': '恢复训练'}
            ],
            '曼城': [
                {'name': '德布劳内', 'status': 'current', 'reason': '状态良好'},
                {'name': '哈兰德', 'status': 'current', 'reason': '状态良好'}
            ]
        },
        'serie_a': {
            '那不勒斯': [
                {'name': '卢卡库', 'status': 'injured', 'reason': '大腿伤势', 'expected_return': '2026-05-01'},
                {'name': '内雷斯', 'status': 'injured', 'reason': '肌肉拉伤', 'expected_return': '2026-04-28'},
                {'name': '迪洛伦佐', 'status': 'injured', 'reason': '膝盖伤势', 'expected_return': '2026-05-10'},
                {'name': '拉赫马尼', 'status': 'injured', 'reason': '脚踝伤势', 'expected_return': '2026-04-25'}
            ],
            '罗马': [
                {'name': '迪巴拉', 'status': 'injured', 'reason': '肌肉伤势', 'expected_return': '2026-04-28'},
                {'name': '佩莱格里尼', 'status': 'injured', 'reason': '膝盖伤势', 'expected_return': '2026-05-05'},
                {'name': '科内', 'status': 'injured', 'reason': '脚踝伤势', 'expected_return': '2026-04-30'}
            ]
        },
        'bundesliga': {
            '多特蒙德': [
                {'name': '埃姆雷·詹', 'status': 'injured', 'reason': '赛季报销', 'expected_return': '2026-08-01'},
                {'name': '吉拉西', 'status': 'injured', 'reason': '膝盖伤势', 'expected_return': '2026-05-15'}
            ],
            '拜仁慕尼黑': [
                {'name': '凯恩', 'status': 'current', 'reason': '状态良好'},
                {'name': '穆西亚拉', 'status': 'current', 'reason': '状态良好'}
            ]
        },
        'ligue_1': {
            '马赛': [
                {'name': '孔多比亚', 'status': 'injured', 'reason': '肌肉伤势', 'expected_return': '2026-04-28'},
                {'name': '梅迪纳', 'status': 'injured', 'reason': '脚踝伤势', 'expected_return': '2026-04-30'},
                {'name': '康拉德', 'status': 'injured', 'reason': '膝盖伤势', 'expected_return': '2026-05-10'}
            ],
            '巴黎圣日耳曼': [
                {'name': '姆巴佩', 'status': 'current', 'reason': '状态良好'},
                {'name': '梅西', 'status': 'current', 'reason': '状态良好'}
            ]
        },
        'la_liga': {
            '巴塞罗那': [
                {'name': '莱万多夫斯基', 'status': 'current', 'reason': '状态良好'},
                {'name': '佩德里', 'status': 'current', 'reason': '状态良好'}
            ],
            '皇家马德里': [
                {'name': '维尼修斯', 'status': 'current', 'reason': '状态良好'},
                {'name': '本泽马', 'status': 'current', 'reason': '状态良好'}
            ]
        }
    }
    
    update_count = 0
    
    for league_code, league_info in LEAGUES.items():
        for team_name in league_info['teams']:
            # 加载球队数据
            team_data = load_player_data(league_code, team_name)
            if not team_data:
                logging.warning(f"无法加载 {league_code}/{team_name} 的数据")
                continue
            
            # 获取状态更新
            team_updates = status_updates.get(league_code, {}).get(team_name, [])
            
            for update in team_updates:
                player_name = update['name']
                new_status = update['status']
                
                # 查找并更新球员
                for player in team_data['players']:
                    if player['name'] == player_name:
                        old_status = player.get('transfer_status', 'current')
                        if old_status != new_status:
                            player['transfer_status'] = new_status
                            player['last_updated'] = datetime.now().isoformat()
                            if 'expected_return' in update:
                                player['expected_return'] = update['expected_return']
                            if 'reason' in update:
                                player['injury_reason'] = update['reason']
                            update_count += 1
                            logging.info(f"更新 {team_name} 的 {player_name} 状态: {old_status} → {new_status}")
                        break
            
            # 更新球队最后更新时间
            team_data['last_updated'] = datetime.now().isoformat()
            
            # 保存更新后的数据
            save_player_data(league_code, team_name, team_data)
    
    logging.info(f"球员状态更新完成，共更新 {update_count} 名球员")
    return update_count

def generate_weekly_report():
    """生成每周球员状态报告"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    report = {
        'report_date': datetime.now().isoformat(),
        'leagues': []
    }
    
    for league_code, league_info in LEAGUES.items():
        league_report = {
            'league': league_info['name'],
            'teams': []
        }
        
        for team_name in league_info['teams']:
            team_data = load_player_data(league_code, team_name)
            if not team_data:
                continue
            
            # 统计球员状态
            total_players = len(team_data['players'])
            injured_players = [p for p in team_data['players'] if p.get('transfer_status') == 'injured']
            suspended_players = [p for p in team_data['players'] if p.get('transfer_status') == 'suspended']
            current_players = [p for p in team_data['players'] if p.get('transfer_status') == 'current']
            
            # 详细的伤病和停赛信息
            injury_details = []
            for player in injured_players:
                injury_details.append({
                    'name': player['name'],
                    'position': player['position'],
                    'reason': player.get('injury_reason', '未知'),
                    'expected_return': player.get('expected_return', '未知')
                })
            
            suspension_details = []
            for player in suspended_players:
                suspension_details.append({
                    'name': player['name'],
                    'position': player['position'],
                    'reason': player.get('injury_reason', '未知'),
                    'expected_return': player.get('expected_return', '未知')
                })
            
            team_info = {
                'name': team_name,
                'total_players': total_players,
                'current_players': len(current_players),
                'injured_players': len(injured_players),
                'suspended_players': len(suspended_players),
                'injury_details': injury_details,
                'suspension_details': suspension_details,
                'last_updated': team_data.get('last_updated', '未知')
            }
            
            league_report['teams'].append(team_info)
        
        report['leagues'].append(league_report)
    
    # 生成报告文件
    report_file = f'weekly_player_report_{datetime.now().strftime("%Y-%m-%d")}.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    logging.info(f"生成每周球员状态报告: {report_file}")
    return report_file

def main():
    """主函数"""
    print("=" * 60)
    print("球员状态定期更新")
    print("=" * 60)
    
    # 更新球员状态
    update_count = update_player_status()
    print(f"更新了 {update_count} 名球员的状态")
    
    # 生成每周报告
    report_file = generate_weekly_report()
    print(f"生成了每周报告: {report_file}")
    
    print("\n" + "=" * 60)
    print("球员状态更新完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
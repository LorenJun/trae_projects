#!/usr/bin/env python3
"""
球员数据管理系统
用于更新五大联赛球队的球员数据，处理转会信息
"""

import os
import json
from datetime import datetime
from pathlib import Path

# 定义联赛信息
LEAGUES = {
    'premier_league': {
        'name': '英超',
        'teams': [
            '利物浦', '阿森纳', '曼城', '纽卡斯尔联', '切尔西',
            '阿斯顿维拉', '诺丁汉森林', '布莱顿', '布伦特福德', '富勒姆',
            '伯恩茅斯', '水晶宫', '埃弗顿', '狼队', '西汉姆联',
            '曼联', '热刺', '利兹联', '伯恩利', '桑德兰'
        ]
    },
    'serie_a': {
        'name': '意甲',
        'teams': [
            '国际米兰', 'AC米兰', '尤文图斯', '那不勒斯', '罗马',
            '亚特兰大', '拉齐奥', '佛罗伦萨', '博洛尼亚', '乌迪内斯',
            '萨索洛', '恩波利', '莱切', '维罗纳', '都灵',
            '克雷莫纳', '帕尔马', '比萨', '卡利亚里', '科莫'
        ]
    },
    'bundesliga': {
        'name': '德甲',
        'teams': [
            '拜仁慕尼黑', '多特蒙德', '勒沃库森', '斯图加特', '柏林联合',
            '霍芬海姆', '法兰克福', '门兴格拉德巴赫', '沃尔夫斯堡', '美因茨',
            '奥格斯堡', '弗莱堡', '云达不莱梅', '汉堡', '圣保利',
            '科隆', '波鸿', '沙尔克04'
        ]
    },
    'ligue_1': {
        'name': '法甲',
        'teams': [
            '巴黎圣日耳曼', '马赛', '摩纳哥', '里尔', '里昂',
            '朗斯', '雷恩', '尼斯', '洛里昂', '斯特拉斯堡',
            '布雷斯特', '蒙彼利埃', '欧塞尔', '克莱蒙', '勒阿弗尔',
            '图卢兹', '南特', '特鲁瓦'
        ]
    },
    'la_liga': {
        'name': '西甲',
        'teams': [
            '巴塞罗那', '皇家马德里', '马德里竞技', '塞维利亚', '皇家社会',
            '比利亚雷亚尔', '贝蒂斯', '瓦伦西亚', '毕尔巴鄂竞技', '奥萨苏纳',
            '赫塔菲', '塞尔塔', '阿拉维斯', '莱万特', '马略卡',
            '加的斯', '埃尔切', '巴列卡诺', '格拉纳达', '赫罗纳'
        ]
    }
}

# 球员数据结构模板
PLAYER_TEMPLATE = {
    'name': '',           # 球员姓名
    'position': '',        # 位置
    'age': 0,              # 年龄
    'nationality': '',     # 国籍
    'transfer_status': 'current',  # current, transferred, injured
    'join_date': '',       # 加盟日期
    'contract_until': '',  # 合同到期
    'market_value': 0,     # 市场价值（欧元）
    'stats': {             # 赛季数据
        'appearances': 0,  # 出场次数
        'goals': 0,        # 进球数
        'assists': 0,      # 助攻数
        'yellow_cards': 0,  # 黄牌数
        'red_cards': 0      # 红牌数
    },
    'last_updated': ''     # 最后更新时间
}

# 球队数据结构模板
TEAM_TEMPLATE = {
    'name': '',
    'league': '',
    'players': [],
    'last_updated': ''
}

def create_player_data_structure():
    """创建球员数据结构"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    for league_code, league_info in LEAGUES.items():
        print(f"\n创建 {league_info['name']} 球员数据结构...")
        
        # 创建球员数据目录
        players_dir = f"{league_code}/players"
        os.makedirs(players_dir, exist_ok=True)
        
        for team_name in league_info['teams']:
            # 创建球队球员数据文件
            team_file = f"{players_dir}/{team_name}.json"
            
            if not os.path.exists(team_file):
                # 创建新的球队数据文件
                team_data = TEAM_TEMPLATE.copy()
                team_data['name'] = team_name
                team_data['league'] = league_info['name']
                team_data['last_updated'] = datetime.now().isoformat()
                
                with open(team_file, 'w', encoding='utf-8') as f:
                    json.dump(team_data, f, ensure_ascii=False, indent=2)
                
                print(f"  创建 {team_name} 球员数据文件")
            else:
                print(f"  {team_name} 球员数据文件已存在")

def update_player_transfers():
    """更新球员转会信息"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    # 示例转会信息（实际应用中可以从外部数据源获取）
    transfers = {
        'premier_league': {
            '切尔西': [
                {'name': '恩昆库', 'status': 'injured', 'reason': '长期伤病'},
                {'name': '福法纳', 'status': 'injured', 'reason': '长期伤病'}
            ],
            '曼联': [
                {'name': '马奎尔', 'status': 'suspended', 'reason': '停赛'},
                {'name': '霍伊伦', 'status': 'injured', 'reason': '伤病'},
                {'name': '万·比萨卡', 'status': 'injured', 'reason': '伤病'}
            ]
        },
        'serie_a': {
            '那不勒斯': [
                {'name': '卢卡库', 'status': 'injured', 'reason': '伤病'},
                {'name': '内雷斯', 'status': 'injured', 'reason': '伤病'},
                {'name': '迪洛伦佐', 'status': 'injured', 'reason': '伤病'},
                {'name': '拉赫马尼', 'status': 'injured', 'reason': '伤病'}
            ],
            '罗马': [
                {'name': '迪巴拉', 'status': 'injured', 'reason': '伤病'},
                {'name': '佩莱格里尼', 'status': 'injured', 'reason': '伤病'},
                {'name': '科内', 'status': 'injured', 'reason': '伤病'}
            ]
        },
        'bundesliga': {
            '多特蒙德': [
                {'name': '埃姆雷·詹', 'status': 'injured', 'reason': '赛季报销'},
                {'name': '吉拉西', 'status': 'injured', 'reason': '主力射手伤病'}
            ]
        },
        'ligue_1': {
            '马赛': [
                {'name': '孔多比亚', 'status': 'injured', 'reason': '伤病'},
                {'name': '梅迪纳', 'status': 'injured', 'reason': '伤病'},
                {'name': '康拉德', 'status': 'injured', 'reason': '伤病'}
            ]
        }
    }
    
    for league_code, league_transfers in transfers.items():
        for team_name, player_transfers in league_transfers.items():
            team_file = f"{league_code}/players/{team_name}.json"
            
            if os.path.exists(team_file):
                with open(team_file, 'r', encoding='utf-8') as f:
                    team_data = json.load(f)
                
                # 更新球员状态
                for transfer in player_transfers:
                    # 检查球员是否存在
                    player_exists = False
                    for player in team_data['players']:
                        if player['name'] == transfer['name']:
                            player['transfer_status'] = transfer['status']
                            player_exists = True
                            break
                    
                    if not player_exists:
                        # 添加新球员
                        new_player = PLAYER_TEMPLATE.copy()
                        new_player['name'] = transfer['name']
                        new_player['transfer_status'] = transfer['status']
                        new_player['last_updated'] = datetime.now().isoformat()
                        team_data['players'].append(new_player)
                
                # 更新最后更新时间
                team_data['last_updated'] = datetime.now().isoformat()
                
                # 写回文件
                with open(team_file, 'w', encoding='utf-8') as f:
                    json.dump(team_data, f, ensure_ascii=False, indent=2)
                
                print(f"更新 {league_code}/{team_name} 的球员转会信息")

def generate_player_status_report():
    """生成球员状态报告"""
    # 切换到项目根目录
    os.chdir('/Users/lin/trae_projects/europe_leagues')
    
    report = []
    
    for league_code, league_info in LEAGUES.items():
        league_report = {
            'league': league_info['name'],
            'teams': []
        }
        
        for team_name in league_info['teams']:
            team_file = f"{league_code}/players/{team_name}.json"
            
            if os.path.exists(team_file):
                with open(team_file, 'r', encoding='utf-8') as f:
                    team_data = json.load(f)
                
                # 统计球员状态
                injured_players = [p for p in team_data['players'] if p.get('transfer_status') == 'injured']
                suspended_players = [p for p in team_data['players'] if p.get('transfer_status') == 'suspended']
                transferred_players = [p for p in team_data['players'] if p.get('transfer_status') == 'transferred']
                
                team_info = {
                    'name': team_name,
                    'total_players': len(team_data['players']),
                    'injured': len(injured_players),
                    'suspended': len(suspended_players),
                    'transferred': len(transferred_players),
                    'available': len(team_data['players']) - len(injured_players) - len(suspended_players),
                    'last_updated': team_data.get('last_updated', 'N/A')
                }
                
                league_report['teams'].append(team_info)
        
        report.append(league_report)
    
    # 生成报告文件
    report_file = 'player_status_report.json'
    with open(report_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n生成球员状态报告: {report_file}")
    
    # 打印摘要
    print("\n球员状态摘要:")
    for league in report:
        print(f"\n{league['league']}:")
        for team in league['teams']:
            if team['injured'] > 0 or team['suspended'] > 0:
                print(f"  {team['name']}: 伤病 {team['injured']}, 停赛 {team['suspended']}")

def main():
    """主函数"""
    print("=" * 60)
    print("球员数据管理系统")
    print("=" * 60)
    
    # 创建球员数据结构
    create_player_data_structure()
    
    # 更新球员转会信息
    update_player_transfers()
    
    # 生成球员状态报告
    generate_player_status_report()
    
    print("\n" + "=" * 60)
    print("球员数据更新完成！")
    print("=" * 60)

if __name__ == "__main__":
    main()
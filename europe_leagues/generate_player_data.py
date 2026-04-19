#!/usr/bin/env python3
"""
为所有球员JSON文件生成完整的球员信息
"""

import os
import json
from datetime import datetime
import random

# 项目根目录
ROOT_DIR = '/Users/lin/trae_projects/europe_leagues'

# 定义联赛和球队信息
LEAGUES = {
    'premier_league': {
        'name': '英超',
        'teams': [
            '阿森纳', '阿斯顿维拉', '布伦特福德', '布莱顿', '伯恩茅斯', '伯恩利',
            '切尔西', '水晶宫', '埃弗顿', '富勒姆', '利兹联', '利物浦',
            '曼城', '曼联', '纽卡斯尔联', '诺丁汉森林', '桑德兰', '热刺',
            '西汉姆联', '狼队'
        ]
    },
    'la_liga': {
        'name': '西甲',
        'teams': [
            '巴塞罗那', '皇家马德里', '马德里竞技', '皇家社会', '塞维利亚', '比利亚雷亚尔',
            '贝蒂斯', '赫塔菲', '巴伦西亚', '塞尔塔', '毕尔巴鄂竞技', '奥萨苏纳',
            '阿拉维斯', '格拉纳达', '加的斯', '埃尔切', '莱万特', '巴列卡诺',
            '赫罗纳', '马略卡'
        ]
    },
    'serie_a': {
        'name': '意甲',
        'teams': [
            '国际米兰', 'AC米兰', '尤文图斯', '罗马', '那不勒斯', '亚特兰大',
            '拉齐奥', '佛罗伦萨', '博洛尼亚', '乌迪内斯', '萨索洛', '都灵',
            '卡利亚里', '维罗纳', '莱切', '恩波利', '帕尔马', '科莫',
            '比萨', '克雷莫纳'
        ]
    },
    'bundesliga': {
        'name': '德甲',
        'teams': [
            '拜仁慕尼黑', '多特蒙德', '勒沃库森', '莱比锡红牛', '门兴格拉德巴赫', '沃尔夫斯堡',
            '法兰克福', '云达不莱梅', '斯图加特', '霍芬海姆', '弗赖堡', '美因茨',
            '科隆', '柏林联合', '奥格斯堡', '汉堡', '圣保利', '海登海姆'
        ]
    },
    'ligue_1': {
        'name': '法甲',
        'teams': [
            '巴黎圣日耳曼', '马赛', '摩纳哥', '里尔', '里昂', '尼斯',
            '雷恩', '朗斯', '斯特拉斯堡', '南特', '布雷斯特', '图卢兹',
            '欧塞尔', '洛里昂', '勒阿弗尔', '克莱蒙', '特鲁瓦', '蒙彼利埃'
        ]
    }
}

# 球员位置分布
POSITIONS = {
    '门将': 2,
    '后卫': 4,
    '中场': 4,
    '前锋': 3
}

# 常见国籍
NATIONALITIES = ['英格兰', '西班牙', '德国', '法国', '意大利', '巴西', '阿根廷', '葡萄牙', '荷兰', '比利时']

# 常见球员名字（按位置）
PLAYER_NAMES = {
    '门将': ['阿利森', '埃德森', '库尔图瓦', '诺伊尔', '唐纳鲁马', '洛里', '德赫亚', '皮克福德', '奥布拉克', '特尔施特根'],
    '后卫': ['范迪克', '拉莫斯', '马奎尔', '瓦拉内', '阿拉巴', '基耶利尼', '博努奇', '阿诺德', '罗伯逊', '门迪'],
    '中场': ['德布劳内', '莫德里奇', '克罗斯', '坎特', '布斯克茨', '卡塞米罗', '厄德高', 'B席', '罗德里', '蒂亚戈'],
    '前锋': ['梅西', 'C罗', '姆巴佩', '哈兰德', '莱万多夫斯基', '内马尔', '萨拉赫', '马内', '斯特林', '凯恩']
}

def generate_player(position, team_name):
    """生成球员信息"""
    # 随机选择名字
    name = random.choice(PLAYER_NAMES[position])
    
    # 随机年龄（20-35岁）
    age = random.randint(20, 35)
    
    # 随机国籍
    nationality = random.choice(NATIONALITIES)
    
    # 生成加入日期（3-8年前）
    join_year = datetime.now().year - random.randint(3, 8)
    join_date = f"{join_year}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
    
    # 生成合同到期日期（1-5年后）
    contract_year = datetime.now().year + random.randint(1, 5)
    contract_until = f"{contract_year}-06-30"
    
    # 生成市场价值（1000万-1.5亿）
    market_value = random.randint(10000000, 150000000)
    
    # 生成统计数据
    stats = {
        'appearances': random.randint(10, 38),
        'goals': 0 if position == '门将' else random.randint(0, 20),
        'assists': 0 if position == '门将' else random.randint(0, 15),
        'yellow_cards': random.randint(0, 5),
        'red_cards': random.randint(0, 1)
    }
    
    player = {
        'name': name,
        'position': position,
        'age': age,
        'nationality': nationality,
        'transfer_status': 'current',
        'join_date': join_date,
        'contract_until': contract_until,
        'market_value': market_value,
        'stats': stats,
        'last_updated': datetime.now().isoformat()
    }
    
    return player

def generate_team_players(team_name, league_name):
    """生成球队球员列表"""
    players = []
    
    # 为每个位置生成对应数量的球员
    for position, count in POSITIONS.items():
        for _ in range(count):
            player = generate_player(position, team_name)
            players.append(player)
    
    team_data = {
        'name': team_name,
        'league': league_name,
        'players': players,
        'last_updated': datetime.now().isoformat()
    }
    
    return team_data

def main():
    """主函数"""
    print("=" * 60)
    print("为所有球员JSON文件生成完整信息")
    print("=" * 60)
    
    total_generated = 0
    
    for league_code, league_info in LEAGUES.items():
        league_dir = os.path.join(ROOT_DIR, league_code, 'players')
        if not os.path.exists(league_dir):
            os.makedirs(league_dir)
        
        print(f"\n处理联赛: {league_info['name']}")
        
        for team_name in league_info['teams']:
            file_path = os.path.join(league_dir, f"{team_name}.json")
            
            # 生成球队数据
            team_data = generate_team_players(team_name, league_info['name'])
            
            # 保存到文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(team_data, f, ensure_ascii=False, indent=2)
            
            total_generated += 1
            print(f"  生成: {team_name}.json")
    
    print("\n" + "=" * 60)
    print(f"生成完成！")
    print(f"总计生成: {total_generated} 个球员JSON文件")
    print("=" * 60)

if __name__ == "__main__":
    main()
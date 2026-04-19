#!/usr/bin/env python3
"""
批量更新球员JSON文件的最后更新时间
确保所有五大联赛的球员数据文件都有最新的更新时间
"""

import os
import json
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='batch_update_players.log'
)

# 定义联赛目录
LEAGUES = [
    'premier_league',
    'la_liga',
    'serie_a',
    'bundesliga',
    'ligue_1'
]

# 项目根目录
ROOT_DIR = '/Users/lin/trae_projects/europe_leagues'

def update_player_file(file_path):
    """更新单个球员文件的最后更新时间"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 更新最后更新时间
        current_time = datetime.now().isoformat()
        data['last_updated'] = current_time
        
        # 更新每个球员的最后更新时间
        for player in data.get('players', []):
            player['last_updated'] = current_time
        
        # 保存更新后的数据
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logging.info(f"更新了文件: {file_path}")
        return True
    except Exception as e:
        logging.error(f"更新文件失败 {file_path}: {e}")
        return False

def main():
    """主函数"""
    print("=" * 60)
    print("批量更新球员JSON文件")
    print("=" * 60)
    
    total_updated = 0
    total_files = 0
    
    for league in LEAGUES:
        league_dir = os.path.join(ROOT_DIR, league, 'players')
        if not os.path.exists(league_dir):
            logging.warning(f"联赛目录不存在: {league_dir}")
            continue
        
        print(f"\n处理联赛: {league}")
        
        # 获取所有JSON文件
        json_files = [f for f in os.listdir(league_dir) if f.endswith('.json')]
        league_files = len(json_files)
        league_updated = 0
        
        for json_file in json_files:
            file_path = os.path.join(league_dir, json_file)
            total_files += 1
            if update_player_file(file_path):
                league_updated += 1
                total_updated += 1
        
        print(f"  处理文件: {league_files}, 更新成功: {league_updated}")
    
    print("\n" + "=" * 60)
    print(f"更新完成！")
    print(f"总计处理: {total_files} 个文件")
    print(f"更新成功: {total_updated} 个文件")
    print("=" * 60)

if __name__ == "__main__":
    main()
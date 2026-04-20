#!/usr/bin/env python3
"""
澳客网(okooo.com) 赔率数据采集脚本
使用 Playwright 绕过反爬虫机制
支持自动查找比赛ID
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from okooo_helper_v2 import get_okooo_odds, OkoooScraper
from okooo_match_finder import OkoooMatchFinder, find_and_fetch_odds


# 联赛配置
LEAGUE_CONFIG = {
    "premier_league": {
        "name": "英超",
        "okooo_league_id": "17",
    },
    "la_liga": {
        "name": "西甲",
        "okooo_league_id": "8",
    },
    "serie_a": {
        "name": "意甲",
        "okooo_league_id": "23",
    },
    "bundesliga": {
        "name": "德甲",
        "okooo_league_id": "35",
    },
    "ligue_1": {
        "name": "法甲",
        "okooo_league_id": "34",
    },
    "afc_champions_league": {
        "name": "亚冠",
        "okooo_league_id": "167",
    },
}


class OddsSnapshotBackfill:
    """赔率快照回填器"""
    
    def __init__(self, league: str, headless: bool = True):
        self.league = league
        self.league_config = LEAGUE_CONFIG.get(league)
        if not self.league_config:
            raise ValueError(f"未知的联赛: {league}")
        
        self.headless = headless
        self.scraper = None
        self.finder = OkoooMatchFinder()
        
    def start(self):
        """启动浏览器"""
        self.scraper = OkoooScraper(headless=self.headless)
        self.scraper.start()
        return self
    
    def close(self):
        """关闭浏览器"""
        if self.scraper:
            self.scraper.close()
            self.scraper = None
    
    def find_match_id(self, team1: str, team2: str) -> Optional[str]:
        """
        根据球队名称查找比赛ID
        
        Args:
            team1: 主队名称
            team2: 客队名称
            
        Returns:
            比赛ID或None
        """
        league_hint = self.league_config['name']
        return self.finder.find_match_id(team1, team2, league_hint)
    
    def verify_match_id(self, match_id: str, teams: List[str]) -> bool:
        """
        验证比赛ID是否正确
        
        Args:
            match_id: 比赛ID
            teams: 期望的球队名称列表
            
        Returns:
            验证是否通过
        """
        return self.finder.verify_match_id(match_id, teams)
    
    def get_match_odds(self, match_id: str) -> Optional[Dict]:
        """
        获取单场比赛的赔率数据
        
        Args:
            match_id: 澳客网比赛ID
            
        Returns:
            赔率数据字典或None
        """
        if not self.scraper:
            raise RuntimeError("浏览器未启动，请先调用start()")
        
        return self.scraper.extract_odds(match_id)
    
    def get_odds_by_teams(self, team1: str, team2: str) -> Optional[Dict]:
        """
        根据球队名称获取赔率数据（自动查找比赛ID）
        
        Args:
            team1: 主队名称
            team2: 客队名称
            
        Returns:
            包含match_id和odds_data的字典，或None
        """
        print(f"\n🔍 查找比赛: {team1} vs {team2}")
        
        # 1. 查找比赛ID
        match_id = self.find_match_id(team1, team2)
        if not match_id:
            print("✗ 未找到比赛ID")
            return None
        
        print(f"✓ 找到比赛ID: {match_id}")
        
        # 2. 验证比赛ID
        if not self.verify_match_id(match_id, [team1, team2]):
            print("✗ 比赛ID验证失败")
            return None
        
        # 3. 获取赔率数据
        print(f"📊 获取赔率数据...")
        odds_data = self.get_match_odds(match_id)
        
        if odds_data:
            print(f"✓ 成功获取 {len(odds_data.get('bookmakers', {}))} 家博彩公司数据")
            return {
                'match_id': match_id,
                'odds_data': odds_data
            }
        else:
            print("✗ 获取赔率失败")
            return None
    
    def save_odds_snapshot(self, odds_data: Dict, output_dir: str):
        """
        保存赔率快照到文件
        
        Args:
            odds_data: 赔率数据
            output_dir: 输出目录
        """
        os.makedirs(output_dir, exist_ok=True)
        
        match_id = odds_data.get("match_id", "unknown")
        fetched_at = odds_data.get("fetched_at", datetime.now().strftime("%Y-%m-%d"))
        
        filename = f"{match_id}_odds_{fetched_at.replace(' ', '_').replace(':', '-')}.json"
        filepath = os.path.join(output_dir, filename)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(odds_data, f, ensure_ascii=False, indent=2)
        
        print(f"  已保存: {filepath}")
        return filepath
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def display_odds_summary(odds_data: Dict):
    """显示赔率摘要"""
    bookmakers = odds_data.get('bookmakers', {})
    
    # 主要博彩公司
    main_companies = ['威廉.希尔', '立博', 'Bet365', '澳门彩票', '竞彩官方', 'bwin', '香港马会', '99家平均']
    
    print("\n" + "=" * 70)
    print("主要博彩公司赔率:")
    print("=" * 70)
    print(f"{'公司':<15s} {'初赔(主/平/客)':<20s} {'即时(主/平/客)':<20s}")
    print("-" * 70)
    
    for company in main_companies:
        if company in bookmakers:
            data = bookmakers[company]
            init = data.get('initial', {})
            curr = data.get('current', {})
            
            init_home = init.get('home', 0) or 0
            init_draw = init.get('draw', 0) or 0
            init_away = init.get('away', 0) or 0
            curr_home = curr.get('home', 0) or 0
            curr_draw = curr.get('draw', 0) or 0
            curr_away = curr.get('away', 0) or 0
            
            init_str = f'{init_home:.2f}/{init_draw:.2f}/{init_away:.2f}' if init_home else 'N/A'
            curr_str = f'{curr_home:.2f}/{curr_draw:.2f}/{curr_away:.2f}' if curr_home else 'N/A'
            
            print(f'{company:<15s} {init_str:<20s} {curr_str:<20s}')
    
    # 赔率分析
    avg_data = bookmakers.get('99家平均', {})
    if avg_data:
        init = avg_data.get('initial', {})
        curr = avg_data.get('current', {})
        
        print("\n" + "=" * 70)
        print("📈 赔率分析:")
        print("=" * 70)
        
        print(f"\n【99家平均初赔】")
        print(f"  主胜: {init.get('home', 'N/A')} | 平局: {init.get('draw', 'N/A')} | 客胜: {init.get('away', 'N/A')}")
        
        curr_home = curr.get('home')
        curr_away = curr.get('away')
        
        if curr_home and curr_away:
            print(f"\n【99家平均即时】")
            print(f"  主胜: {curr_home} | 平局: {curr.get('draw', 'N/A')} | 客胜: {curr_away}")
            
            # 赔率变化
            init_home = init.get('home', 0)
            init_away = init.get('away', 0)
            
            if init_home and init_away:
                home_change = ((curr_home - init_home) / init_home * 100)
                away_change = ((curr_away - init_away) / init_away * 100)
                
                print(f"\n【赔率变化】")
                print(f"  主胜: {home_change:+.1f}% ({'看好主队' if home_change < 0 else '不看好主队'})")
                print(f"  客胜: {away_change:+.1f}% ({'看好客队' if away_change < 0 else '不看好客队'})")
    
    # 竞彩官方
    jc_data = bookmakers.get('竞彩官方', {})
    if jc_data:
        init = jc_data.get('initial', {})
        print(f"\n【竞彩官方初赔】")
        print(f"  主胜: {init.get('home', 'N/A')} | 平局: {init.get('draw', 'N/A')} | 客胜: {init.get('away', 'N/A')}")
        
        home = init.get('home', 0)
        draw = init.get('draw', 0)
        away = init.get('away', 0)
        if home and draw and away:
            return_rate = (1 / home + 1 / draw + 1 / away) ** -1 * 100
            print(f"  返还率: {return_rate:.2f}%")


def main():
    parser = argparse.ArgumentParser(
        description='澳客网赔率数据采集工具 - 支持自动查找比赛ID',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 通过比赛ID获取
  python backfill_odds_snapshots.py --match-id 1324404 --league afc_champions_league
  
  # 通过球队名称自动查找（推荐）
  python backfill_odds_snapshots.py --teams "神户胜利,吉达国民" --league afc_champions_league
  
  # 显示浏览器窗口（调试用）
  python backfill_odds_snapshots.py --teams "神户胜利,吉达国民" --league afc_champions_league --no-headless
        """
    )
    
    # 两种获取方式
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--match-id', type=str, help='澳客网比赛ID')
    group.add_argument('--teams', type=str, help='球队名称，格式: "主队,客队"')
    
    parser.add_argument('--league', type=str, default='premier_league',
                        choices=list(LEAGUE_CONFIG.keys()),
                        help='联赛名称（默认: premier_league）')
    parser.add_argument('--output-dir', type=str, help='输出目录')
    parser.add_argument('--headless', action='store_true', default=True,
                        help='使用无头模式（默认）')
    parser.add_argument('--no-headless', dest='headless', action='store_false',
                        help='显示浏览器窗口')
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("澳客网赔率数据采集工具")
    print("=" * 70)
    
    # 设置输出目录
    if not args.output_dir:
        args.output_dir = os.path.join(
            os.path.dirname(__file__),
            args.league,
            "analysis",
            "odds_snapshots"
        )
    
    with OddsSnapshotBackfill(args.league, headless=args.headless) as backfill:
        
        # 方式1: 通过比赛ID获取
        if args.match_id:
            print(f"\n通过比赛ID获取: {args.match_id}")
            
            # 验证ID
            print("\n验证比赛ID...")
            if not backfill.verify_match_id(args.match_id, []):
                print("✗ 比赛ID验证失败")
                sys.exit(1)
            
            odds_data = backfill.get_match_odds(args.match_id)
            
            if odds_data:
                display_odds_summary(odds_data)
                backfill.save_odds_snapshot(odds_data, args.output_dir)
            else:
                print("\n✗ 获取赔率失败")
                sys.exit(1)
        
        # 方式2: 通过球队名称自动查找（推荐）
        elif args.teams:
            teams = [t.strip() for t in args.teams.split(',')]
            if len(teams) != 2:
                print("✗ 球队名称格式错误，请使用: \"主队,客队\"")
                sys.exit(1)
            
            team1, team2 = teams
            result = backfill.get_odds_by_teams(team1, team2)
            
            if result:
                odds_data = result['odds_data']
                display_odds_summary(odds_data)
                backfill.save_odds_snapshot(odds_data, args.output_dir)
                
                print("\n" + "=" * 70)
                print("✓ 完成!")
                print(f"比赛ID: {result['match_id']}")
                print(f"数据文件: {args.output_dir}")
            else:
                print("\n✗ 获取失败")
                sys.exit(1)


if __name__ == "__main__":
    main()

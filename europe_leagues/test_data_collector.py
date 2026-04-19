#!/usr/bin/env python3
"""
测试数据收集器的功能
"""

import asyncio
from data_collector import DataCollector
from datetime import datetime, timedelta

async def test_data_collection():
    """测试数据收集功能"""
    print("=" * 60)
    print("测试数据收集器")
    print("=" * 60)
    
    collector = DataCollector()
    
    # 测试今天的比赛数据
    today = datetime.now().strftime('%Y-%m-%d')
    print(f"测试日期: {today}")
    
    # 测试五大联赛
    leagues = [
        {'code': 'premier_league', 'name': '英超'},
        {'code': 'serie_a', 'name': '意甲'},
        {'code': 'bundesliga', 'name': '德甲'},
        {'code': 'ligue_1', 'name': '法甲'},
        {'code': 'la_liga', 'name': '西甲'}
    ]
    
    for league in leagues:
        print(f"\n测试 {league['name']}...")
        
        # 测试带缓存的抓取
        print("1. 测试带缓存的抓取:")
        matches_with_cache = await collector.collect_league_data(league['code'], today, use_cache=True)
        
        # 测试不带缓存的抓取
        print("2. 测试不带缓存的抓取:")
        matches_without_cache = await collector.collect_league_data(league['code'], today, use_cache=False)
        
        # 生成报告
        report_with_cache = collector.get_accuracy_report(matches_with_cache)
        report_without_cache = collector.get_accuracy_report(matches_without_cache)
        
        print(f"\n{league['name']} 数据报告:")
        print(f"带缓存 - 比赛数: {report_with_cache['total']}, 准确率: {report_with_cache['accuracy']:.2f}%")
        print(f"无缓存 - 比赛数: {report_without_cache['total']}, 准确率: {report_without_cache['accuracy']:.2f}%")
        
        # 显示比赛数据
        if matches_with_cache:
            print("\n比赛数据:")
            for match in matches_with_cache[:5]:  # 只显示前5场
                score_str = f" {match.score[0]}-{match.score[1]}" if match.score else ""
                print(f"{match.match_time} {match.home_team} vs {match.away_team}{score_str} [{match.status}] 来源: {', '.join(match.sources)}")
        else:
            print("\n没有找到比赛数据")
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_data_collection())
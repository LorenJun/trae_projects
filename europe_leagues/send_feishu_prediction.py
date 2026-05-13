#!/usr/bin/env python3
"""
发送预测结果到飞书的命令行工具
支持从prediction_system.py的JSON输出直接发送

用法:
    python3 send_feishu_prediction.py --match-date 2026-05-13 --league la_liga
    python3 send_feishu_prediction.py --from-json prediction_result.json
    python3 send_feishu_prediction.py --match-id 1302903 --match-id 1302902
"""

import argparse
import json
import os
import sys
from typing import List, Dict, Any, Optional
from datetime import datetime

# 导入增强版卡片构建器
from feishu_enhanced_cards import FeishuEnhancedSender, FeishuEnhancedCardBuilder


def load_prediction_from_json(json_path: str) -> Dict[str, Any]:
    """从JSON文件加载预测结果"""
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_matches_from_prediction_result(result: Dict) -> List[Dict]:
    """从prediction_system.py的输出中提取比赛数据"""
    if not result.get('success'):
        print(f"❌ 预测结果失败: {result.get('error', '未知错误')}")
        return []
    
    data = result.get('data', {})
    
    # 单场比赛预测
    if 'match_id' in data:
        return [data]
    
    # 多场比赛预测
    if 'matches' in data:
        return data['matches']
    
    return [data]


def run_prediction(match_id: str, home_team: str, away_team: str, 
                   league: str, match_date: str) -> Optional[Dict]:
    """运行预测并返回结果"""
    import subprocess
    
    cmd = [
        'python3', 'prediction_system.py', 'predict-match',
        '--home-team', home_team,
        '--away-team', away_team,
        '--league', league,
        '--date', match_date,
        '--match-id', match_id,
        '--no-refresh-odds',
        '--json'
    ]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            cwd='/Users/bytedance/trae_projects/europe_leagues'
        )
        
        if result.returncode != 0:
            print(f"❌ 预测命令失败: {result.stderr}")
            return None
        
        # 解析JSON输出
        output = result.stdout.strip()
        # 找到JSON开始的位置
        json_start = output.find('{')
        if json_start == -1:
            print(f"❌ 无法找到JSON输出")
            return None
        
        json_str = output[json_start:]
        return json.loads(json_str)
        
    except subprocess.TimeoutExpired:
        print(f"❌ 预测命令超时")
        return None
    except Exception as e:
        print(f"❌ 预测异常: {e}")
        return None


def get_la_liga_matches_2026_05_13() -> List[Dict]:
    """获取2026-05-13西甲比赛列表"""
    return [
        {
            'match_id': '1302903',
            'home_team': '塞尔塔',
            'away_team': '莱万特',
            'league': 'la_liga',
            'match_date': '2026-05-13'
        },
        {
            'match_id': '1302902',
            'home_team': '皇家贝蒂斯',
            'away_team': '埃尔切',
            'league': 'la_liga',
            'match_date': '2026-05-13'
        },
        {
            'match_id': '1302907',
            'home_team': '奥萨苏纳',
            'away_team': '马德里竞技',
            'league': 'la_liga',
            'match_date': '2026-05-13'
        }
    ]


def send_predictions_to_feishu(matches: List[Dict], 
                                match_date: str,
                                league_name: str,
                                webhook_url: Optional[str] = None) -> bool:
    """发送预测结果到飞书"""
    
    if not matches:
        print("❌ 没有比赛数据可发送")
        return False
    
    # 构建增强版卡片
    card = FeishuEnhancedCardBuilder.build_enhanced_prediction_card(
        match_date=match_date,
        league_name=league_name,
        matches=matches
    )
    
    # 发送
    sender = FeishuEnhancedSender(webhook_url)
    return sender.send_card(card)


def main():
    parser = argparse.ArgumentParser(
        description='发送足球预测结果到飞书',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 发送今日西甲所有比赛预测
  python3 send_feishu_prediction.py --match-date 2026-05-13 --league la_liga
  
  # 从JSON文件发送
  python3 send_feishu_prediction.py --from-json result.json
  
  # 发送指定比赛
  python3 send_feishu_prediction.py --match-id 1302903 --match-id 1302902
  
  # 指定Webhook URL
  python3 send_feishu_prediction.py --webhook https://open.feishu.cn/...
        """
    )
    
    parser.add_argument('--match-date', type=str, 
                        default='2026-05-13',
                        help='比赛日期 (默认: 2026-05-13)')
    parser.add_argument('--league', type=str, 
                        default='la_liga',
                        help='联赛代码 (默认: la_liga)')
    parser.add_argument('--league-name', type=str, 
                        default='西甲',
                        help='联赛显示名称 (默认: 西甲)')
    parser.add_argument('--from-json', type=str,
                        help='从JSON文件加载预测结果')
    parser.add_argument('--match-id', type=str, action='append',
                        help='指定比赛ID (可多次使用)')
    parser.add_argument('--webhook', type=str,
                        help='飞书Webhook URL')
    
    args = parser.parse_args()
    
    # 获取webhook
    webhook_url = args.webhook or os.environ.get('FEISHU_WEBHOOK_URL')
    if not webhook_url:
        print("❌ 错误: 请提供--webhook参数或设置FEISHU_WEBHOOK_URL环境变量")
        sys.exit(1)
    
    matches = []
    
    # 方式1: 从JSON文件加载
    if args.from_json:
        print(f"📂 从JSON文件加载: {args.from_json}")
        if not os.path.exists(args.from_json):
            print(f"❌ 文件不存在: {args.from_json}")
            sys.exit(1)
        
        result = load_prediction_from_json(args.from_json)
        matches = extract_matches_from_prediction_result(result)
    
    # 方式2: 指定比赛ID
    elif args.match_id:
        print(f"🔍 获取指定比赛预测: {args.match_id}")
        all_matches = get_la_liga_matches_2026_05_13()
        for mid in args.match_id:
            match_info = next((m for m in all_matches if m['match_id'] == mid), None)
            if match_info:
                print(f"  📊 预测比赛: {match_info['home_team']} vs {match_info['away_team']}")
                result = run_prediction(
                    match_info['match_id'],
                    match_info['home_team'],
                    match_info['away_team'],
                    match_info['league'],
                    match_info['match_date']
                )
                if result:
                    data = result.get('data', {})
                    if data:
                        matches.append(data)
            else:
                print(f"  ⚠️ 未找到比赛ID: {mid}")
    
    # 方式3: 默认发送所有西甲比赛
    else:
        print(f"📅 获取 {args.match_date} {args.league_name} 所有比赛预测...")
        all_matches = get_la_liga_matches_2026_05_13()
        
        for match_info in all_matches:
            print(f"  📊 预测比赛: {match_info['home_team']} vs {match_info['away_team']}")
            result = run_prediction(
                match_info['match_id'],
                match_info['home_team'],
                match_info['away_team'],
                match_info['league'],
                match_info['match_date']
            )
            if result:
                data = result.get('data', {})
                if data:
                    matches.append(data)
        
        if not matches:
            print("❌ 没有获取到任何预测结果")
            sys.exit(1)
    
    # 发送飞书卡片
    print(f"\n📤 发送 {len(matches)} 场比赛的预测结果到飞书...")
    success = send_predictions_to_feishu(
        matches=matches,
        match_date=args.match_date,
        league_name=args.league_name,
        webhook_url=webhook_url
    )
    
    if success:
        print("✅ 发送成功!")
        sys.exit(0)
    else:
        print("❌ 发送失败")
        sys.exit(1)


if __name__ == '__main__':
    main()

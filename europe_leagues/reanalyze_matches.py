#!/usr/bin/env python3
"""基于最新实时数据重新分析滚动记忆中的比赛"""

import sys
sys.path.insert(0, '/Users/bytedance/trae_projects/europe_leagues')

from domain.lightweight_prediction import predict_lightweight_match, build_lightweight_prediction_result
from okooo_live_snapshot import find_snapshot_for_match, extract_current_odds
import json

# 定义5场比赛
matches = [
    ('championship', '英冠', '南安普敦', '米堡', '2026-05-13', '1327389'),
    ('la_liga', '西甲', '奥萨苏纳', '马德里竞技', '2026-05-13', '1302907'),
    ('la_liga', '西甲', '皇家贝蒂斯', '埃尔切', '2026-05-13', '1302902'),
    ('la_liga', '西甲', '塞尔塔', '莱万特', '2026-05-13', '1302903'),
    ('premier_league', '英超', '曼城', '水晶宫', '2026-05-13', '1326947'),
]

base_dir = '/Users/bytedance/trae_projects/europe_leagues'

print('=' * 80)
print('基于最新实时数据的重新分析')
print('=' * 80)

results = []
for league_code, league_name, home, away, date, mid in matches:
    print(f"\n{'='*60}")
    print(f"【{home} vs {away}】({league_name})")
    print(f"{'='*60}")
    try:
        # 先查找已存在的快照
        snapshot = find_snapshot_for_match(
            base_dir=base_dir,
            league_code=league_code,
            match_id=mid,
            home_team=home,
            away_team=away,
            match_date=date,
        )
        
        if snapshot:
            path, payload = snapshot
            print(f"找到快照: {path}")
            
            # 使用现有快照构建预测
            result = build_lightweight_prediction_result(
                snapshot=payload,
                league_name=league_name,
                league_code=league_code,
                home_team=home,
                away_team=away,
                match_date=date,
                match_time="",
                match_id=mid,
            )
            results.append({
                'match': f'{home} vs {away}',
                'result': result
            })
            print(f"预测结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"未找到快照，尝试获取新数据...")
            result = predict_lightweight_match(
                base_dir=base_dir,
                league_name=league_name,
                league_code=league_code,
                home_team=home,
                away_team=away,
                match_date=date,
                match_id=mid,
            )
            results.append({
                'match': f'{home} vs {away}',
                'result': result
            })
            print(f"预测结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"预测失败: {e}")
        import traceback
        traceback.print_exc()
        results.append({
            'match': f'{home} vs {away}',
            'error': str(e)
        })

print(f"\n{'='*80}")
print("分析完成")
print(f"{'='*80}")

# 保存结果
with open('/Users/bytedance/trae_projects/europe_leagues/reanalysis_results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2, default=str)
print("结果已保存到 reanalysis_results.json")

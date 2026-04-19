#!/usr/bin/env python3
"""
导出爆冷案例分析报告
"""

import sys
sys.path.append('/Users/lin/trae_projects/europe_leagues')

from upset_case_library import 爆冷案例库

# 加载案例库
案例库 = 爆冷案例库()

# 导出分析报告
案例库.导出Markdown()

print("爆冷案例分析报告已导出")
print(f"当前案例库共有 {len(案例库.案例列表)} 个案例")

# 查看统计信息
统计 = 案例库.统计爆冷规律()
print("\n统计信息:")
for key, value in 统计.items():
    if key not in ["高风险场景", "改进建议汇总"]:
        print(f"  {key}: {value}")

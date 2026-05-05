# 法甲联赛旧预测模板

> 当前说明：本文件是历史模板，不再作为正式主流程输出。  
> 当前正式流程统一写回 `europe_leagues/ligue_1/teams_2025-26.md` 的赛程表备注列。  
> 赛后结果通过 `save-result` 或 `bulk_fetch_and_update.py` 回填，统计通过 `prediction_system.py accuracy --refresh --json` 刷新。

## 当前正式记录方式

赛程表统一表头：

| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |
|------|------|------|------|------|------|
| YYYY-MM-DD | HH:MM | 球队名 | - | 球队名 | 预测:主胜；比分:1-0/1-1；大小:小2.5(0.58) |

## 备注列建议格式

- `预测:主胜`
- `比分:1-0/1-1`
- `大小:小2.5(0.58)`
- 赛后可追加 `✅/❌`

## 当前统计口径

- 胜平负命中率
- 比分 Top 命中率
- 大小球命中率

## 推荐入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league ligue_1 --date 2026-05-04 --json
python3 prediction_system.py predict-schedule --league ligue_1 --date 2026-05-04 --days 1 --json
python3 prediction_system.py accuracy --refresh --json
```

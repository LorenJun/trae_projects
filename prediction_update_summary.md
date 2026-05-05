# 足球联赛预测更新总结

> 当前说明：本文记录的是早期独立预测文件更新情况。  
> 当前正式流程已切换为写回 `europe_leagues/<league>/teams_2025-26.md` 备注列，不再以 `analysis/predictions/*.md` 作为主流程产物。  
> 因此下列路径与统计描述仅作历史参考。

## 更新时间：2026-04-18

## 各联赛预测文件更新情况

### 1. 英超联赛
**历史文件路径**：/Users/bytedance/trae_projects/europe_leagues/premier_league/analysis/predictions/2026-04-18_predictions.md

**更新内容**：
- 包含8场比赛预测
- 新增比赛：诺丁汉 vs 伯恩利、维拉 vs 桑德兰、埃弗顿 vs 利物浦、曼城 vs 阿森纳、切尔西 vs 曼联
- 详细的预测概率、置信度排序和冷门风险提示
- 实时更新的统计字段

### 2. 德甲联赛
**历史文件路径**：/Users/bytedance/trae_projects/europe_leagues/bundesliga/analysis/predictions/2026-04-18_predictions.md

**更新内容**：
- 包含8场比赛预测
- 新增比赛：斯图加特 vs 法兰克福、美因茨 vs 门兴、弗莱堡 vs 拜仁
- 更新了圣保利 vs 科隆的实际结果（1-1平局）
- 修正了霍芬海姆 vs 多特蒙德的预测概率（从0.75调整为0.60）
- 详细的置信度排序和冷门风险提示

### 3. 意甲联赛
**历史文件路径**：/Users/bytedance/trae_projects/europe_leagues/serie_a/analysis/predictions/2026-04-18_predictions.md

**更新内容**：
- 包含9场比赛预测
- 修正了乌迪内斯 vs 帕尔马的数据错误（从平局调整为主胜）
- 更新了国际米兰 vs 卡利亚里的实际结果（3-0胜利）
- 更新了萨索洛 vs 科莫的实际结果（2-1失败）
- 详细的多维度分析和风险评估

### 4. 法甲联赛
**历史文件路径**：/Users/bytedance/trae_projects/europe_leagues/ligue_1/analysis/predictions/2026-04-18_predictions.md

**更新内容**：
- 包含8场比赛预测
- 新增比赛：勒阿弗尔 vs 蒙彼利埃、克莱蒙 vs 布雷斯特
- 详细的球队基本面分析、战术打法分析和战意评估
- 完整的置信度排序和冷门风险提示

## 预测分析框架

早期独立预测文件采用的分析框架如下，当前正式链路已不再直接使用：

1. **预测数据总览**：包含比赛基本信息、预测结果、预测概率等
2. **置信度排序**：按预测概率从高到低排序，提供推荐理由
3. **冷门风险提示**：分析可能出现的冷门情况及风险因素
4. **统计字段**：早期报告内嵌的完成情况与预测准确率
5. **重点比赛详细分析**：
   - 关键分析要点（联赛排名、近期状态、攻防能力等）
   - 战意评估
   - 历史交锋
   - 战术打法分析
   - 多维度预测
   - 推荐投注

## 数据来源

- 球队基本面数据
- 历史交锋记录
- 球员状态和伤病情况
- 赔率数据和资金流向分析
- 公开媒体报道

## 风险提示

当前正式建议：

1. 先执行 `prediction_system.py collect-data --json`
2. 再执行 `prediction_system.py predict-match` 或 `predict-schedule`
3. 将预测写回 `teams_2025-26.md`
4. 赛后执行 `save-result` 或 `bulk_fetch_and_update.py`
5. 最后执行 `prediction_system.py accuracy --refresh --json`

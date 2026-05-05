# 足球比赛预测分析系统

> 当前说明：本文件已按现行正式链路更新。  
> 正式入口优先使用 `prediction_system.py collect-data / predict-match / predict-schedule / save-result / accuracy --refresh / harness-run`。  
> 正式写回只认 `europe_leagues/<league>/teams_2025-26.md`，旧 `analysis/predictions/` 与 `analysis/results/` 不再作为主流程输出。
>
> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测  
> 3. 结果写回 `europe_leagues/<league>/teams_2025-26.md`  
> 4. 赛后用 `prediction_system.py save-result` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`

## 项目结构

```
europe_leagues/
├── premier_league/          # 英超联赛
│   ├── teams_2025-26.md    # 球队信息
│   └── analysis/           # 分析数据
│       ├── odds/           # 赔率数据落盘（可选）
│       └── odds_snapshots/ # 赔率快照落盘（可选）
├── la_liga/                 # 西甲联赛
│   ├── teams_2025-26.md    # 球队信息
│   └── analysis/           # 分析数据
├── serie_a/                 # 意甲联赛
│   ├── teams_2025-26.md    # 球队信息
│   └── analysis/           # 分析数据
├── bundesliga/             # 德甲联赛
│   ├── teams_2025-26.md    # 球队信息
│   └── analysis/           # 分析数据
└── ligue_1/                # 法甲联赛
    ├── teams_2025-26.md    # 球队信息
    └── analysis/           # 分析数据
```

## 使用方法

### 1. 预测写回（赛程表备注列）

当前项目以 `teams_2025-26.md` 为单一事实来源：
- 赛程表比分列：真实赛果（已完赛为 `x-y`，未完赛为 `-`）
- 赛程表备注列：预测信息（例如 `预测:主胜；比分:1-0/1-1；大小:小2.5(0.58)`），赛后可追加 `✅/❌`
- 如需复盘说明，可补充在同一联赛文件中，但正式主流程不依赖单独预测报告

### 2. 统计预测准确率

准确率统计直接解析各联赛 `teams_2025-26.md`：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py accuracy --refresh --json
```

## 统计指标

脚本会生成以下统计指标：

### 1. 各联赛准确率
- 比赛总数
- 正确预测数
- 准确率
- 主胜预测准确率
- 平局预测准确率
- 客胜预测准确率
- 大小球预测准确率
- 比分 Top 命中率

### 2. 结果闭环
- 单场回填：`prediction_system.py save-result --match-id ... --home-score ... --away-score ... --json`
- 批量回填：`bulk_fetch_and_update.py --start ... --end ... --yes`
- 刷新统计：`prediction_system.py accuracy --refresh --json`

### 3. 改进建议
- 各联赛需要改进的方面
- 总体建议

## 示例使用流程

1. **赛前**：先执行 `collect-data` 或抓赛程拿到 `match_id`
2. **预测**：运行 `predict-match` / `predict-schedule`，检查 `line_source` 与 `over_under.market.final`
3. **写回**：将预测结果写入 `teams_2025-26.md` 备注列
4. **赛后**：用 `save-result` 或 `bulk_fetch_and_update.py` 回填实际比分
5. **统计**：运行 `prediction_system.py accuracy --refresh --json`

## 注意事项

1. **数据格式**：保持表格格式一致，确保数据能被脚本正确读取
2. **数据完整性**：确保需要统计的比赛行同时具备 `比分(x-y)` 与 `备注预测(预测...)`
3. **正式入口**：优先使用 `prediction_system.py` 的非交互子命令
4. **及时更新**：比赛结束后及时回填实际结果，保证统计的准确性

## 扩展功能

- **自动数据采集**：可以添加脚本自动从网站获取比赛结果
- **可视化分析**：可以添加数据可视化功能，直观展示预测表现
- **模型优化**：根据统计结果优化预测模型参数
- **多赛季对比**：可以扩展支持多个赛季的对比分析

## 技术要求

- Python 3.6+
- pandas 库
- 基本的Markdown编辑能力

## 联系方式

如有问题或建议，请联系项目维护者。

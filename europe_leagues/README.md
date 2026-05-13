# 足球比赛预测分析系统

> 当前说明：本文件已按现行正式链路更新。  
> 正式入口优先使用 `prediction_system.py collect-data / predict-match / predict-schedule / save-result / accuracy --refresh / harness-run`。  
> 正式写回遵守“双路径”：五大联赛写 `europe_leagues/<league>/teams_2025-26.md`，欧战/杯赛写 `MEMORY.md` 与 runtime-only 归档；旧 `analysis/predictions/` 与 `analysis/results/` 不再作为主流程输出。
> 
> 项目架构文档：`docs/architecture/europe_leagues_architecture.md` - 包含完整的技术分析、分层架构、模块划分与端到端流程图
>
> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层、历史盘口一致性与临场建议层  
> 3. 五大联赛 SoT 写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写入 `MEMORY.md` 与 runtime-only 归档  
> 4. 赛后用 `prediction_system.py save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`、`retrieved_memory_explanation`、`realtime.context_applied.live_outcome_adjustment.historical_market_alignment`、`retrieved_memory.summary.live_market_followup`、`live_betting_advice`  
> 欧战正式 competition config：`europa_league`、`champions_league`、`conference_league` 已进入主链，可直接走 `predict-match` / `harness-run`，但写回仍保持 `runtime_only`  
> 当前实现说明：`prediction_system.py` 为兼容入口，真实 CLI 路由在 `app/cli.py`

## 项目结构

```
europe_leagues/
├── app/                     # CLI 主实现与 JSON 路由
│   └── cli.py
├── harness/                 # Harness 编排层
│   ├── core.py
│   └── football.py
├── domain/                  # 领域服务、RAG 与预测外壳
│   ├── predictor.py
│   ├── features.py
│   ├── odds.py
│   ├── rag.py
│   ├── intelligence.py
│   ├── upset.py
│   ├── live.py
│   ├── inference.py
│   ├── postprocess.py
│   ├── persistence.py
│   ├── reporting.py
│   ├── writeback.py
│   └── team_strength.py
├── collectors/              # 赛程/快照/上下文采集
├── models/                  # Poisson / Dixon-Coles / Fusion
├── storage/                 # SoT / 归档 / 准确率
├── runtime/                 # 缓存、赛果同步、记忆样本与 RAG 索引
├── prediction_system.py     # 兼容 CLI 入口
├── enhanced_prediction_workflow.py # 预测主编排
├── result_manager.py        # 赛果回填与归档兼容管理器
├── premier_league/          # 英超联赛
│   ├── teams_2025-26.md    # 五大联赛 SoT
│   └── analysis/           # 分析数据
├── la_liga/                 # 西甲联赛
├── serie_a/                 # 意甲联赛
├── bundesliga/             # 德甲联赛
└── ligue_1/                # 法甲联赛
```

## 当前分层说明

- 接口层：`prediction_system.py`、`app/cli.py`
- 编排层：`harness/*`、`domain/predictor.py`、`enhanced_prediction_workflow.py`
- 领域层：`domain/features.py`、`odds.py`、`rag.py`、`intelligence.py`、`inference.py`、`postprocess.py` 等
- 采集层：`collectors/*` 与保留的 `okooo_*`、`data_collector.py`
- 存储层：`storage/*`、`runtime/*`、`result_manager.py`
- 数据层：各联赛目录下的 `teams_2025-26.md`、根目录 `MEMORY.md`、`analysis/*`、`players/*.json`

## 使用方法

### 1. 预测写回（赛程表备注列）

当前项目采用双路径写回：
- 五大联赛：`teams_2025-26.md` 负责赛程表比分列、备注列与 SoT 写回
- 欧战/杯赛：`MEMORY.md`、`prediction_archive.json`、`prediction_memory_odds_samples.json` 负责 runtime-only 记忆与归档
- 新预测会原生写入 `RAG记忆:`，历史数据可用 `sync-memory-rag` 回填
- 预测备注与 `MEMORY.md` 现会显式展示 `MatchID`，便于后续按 `match_id` 回溯实时盘口和历史赔率轨迹
- RAG 已升级为“方向优先 + 大小球二次筛选 + 高频比分”，并在满足 `1X2` 赔率接近、大小球变化接近时生成 `live_betting_advice`
- `europa_league`、`champions_league`、`conference_league` 都属于正式可预测 competition config，会优先按 `match_id` 命中欧战快照目录别名并复用真实盘口
- 历史欧战归档会通过迁移统一回填 `league_code`、`league_name`、`snapshot_dir`、`snapshot_dir_aliases`、`snapshot_path`、`line_source`
- 缺历史快照的旧记录只补 canonical 字段，不伪造 `line_source`

### 2. 统计预测准确率

准确率统计会综合联赛 SoT 与滚动记忆：

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
3. **RAG / 历史盘口增强**：检查 `retrieved_memory_explanation`、`retrieved_memory.summary.live_market_followup`、`live_betting_advice`
4. **诊断**：检查 `realtime.context_applied.live_outcome_adjustment.historical_market_alignment` 是否命中历史盘口同向样本
5. **写回**：按比赛类型写入 `teams_2025-26.md` 或 `MEMORY.md` / runtime archive，并确认 `MatchID` 已落盘
6. **赛后**：用 `save-result`、`auto-sync-results` 或 `bulk_fetch_and_update.py` 回填实际比分
7. **统计**：运行 `prediction_system.py accuracy --refresh --json`

欧战示例：

```bash
python3 prediction_system.py predict-match \
  --league champions_league \
  --home-team 阿森纳 \
  --away-team 马竞 \
  --date 2026-05-08 \
  --match-id 1324472 \
  --no-refresh-odds \
  --json
```

## 注意事项

1. **数据格式**：保持表格格式一致，确保数据能被脚本正确读取
2. **数据完整性**：确保需要统计的比赛行同时具备 `比分(x-y)` 与 `备注预测(预测...)`
3. **正式入口**：优先使用 `prediction_system.py` 的非交互子命令
4. **及时更新**：比赛结束后及时回填实际结果，保证统计与 RAG 样本的准确性

## 扩展功能

- **自动数据采集**：可以添加脚本自动从网站获取比赛结果
- **可视化分析**：可以添加数据可视化功能，直观展示预测表现
- **模型优化**：根据统计结果优化预测模型参数
- **多赛季对比**：可以扩展支持多个赛季的对比分析

## 技术要求

- Python 3.9+
- pandas 库
- 基本的Markdown编辑能力

## 联系方式

如有问题或建议，请联系项目维护者。

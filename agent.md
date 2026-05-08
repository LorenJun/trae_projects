---
project_name: "Football Prediction Agent"
version: "1.5.0"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-05-08"
---

# Football Prediction Agent

> 当前正式主链  
> 1. `prediction_system.py collect-data` 获取赛程、`match_id` 与输入上下文  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层  
> 3. 五大联赛写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写入 `MEMORY.md` 与 `.okooo-scraper/runtime/*`  
> 4. 赛后通过 `save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后通过 `accuracy --refresh` 刷新胜负 / 比分 / 大小球统计  
> 6. 如需可审计阶段结果，使用 `harness-run --pipeline ... --json`

## 1. Agent 定义

`Football Prediction Agent` 不是单一的“足球评论员”或“赔率解读器”，而是围绕 `Europe Leagues` 项目构建的一套统一分析主体。  
它的真实执行身份来自：

- [football_actuary_persona.md](./agents/football_actuary_persona.md)
- [agent_runtime_registry.py](./agent_runtime_registry.py)

当前统一身份为：

- `专业纬度足彩数据精算师`

这意味着所有预测、回填、复盘与自动化调用，都要同时覆盖 6 个维度：

1. 足球业务分析
2. 赔率与盘口交易分析
3. 概率建模
4. 统计验证与模型评估
5. 风险控制与资金管理
6. 数据工程、流程自动化与策略迭代

统一输出要求：

- 区分 `模型结论`、`盘口结论`、`综合结论`
- 明确样本边界、快照缺失、降级路径与风险提示
- 不把单一维度结果包装成强确定性结论

## 2. 当前项目架构

项目当前已经收敛为“接口层 -> 编排层 -> 领域层 -> 采集层 -> 模型与存储层 -> 治理层”的结构：

```text
trae_projects/
├── agent.md
├── README.md
├── agent_runtime_registry.py
├── agents/
│   ├── football_actuary_persona.md
│   ├── data_collector_agent.md
│   ├── match_analyzer_agent.md
│   ├── odds_analyzer_agent.md
│   └── result_tracker_agent.md
├── docs/
│   └── architecture/
│       └── europe_leagues_architecture.md
└── europe_leagues/
    ├── app/cli.py
    ├── harness/
    │   ├── core.py
    │   └── football.py
    ├── domain/
    │   ├── predictor.py
    │   ├── features.py
    │   ├── odds.py
    │   ├── live.py
    │   ├── inference.py
    │   ├── postprocess.py
    │   ├── persistence.py
    │   ├── rag.py
    │   ├── reporting.py
    │   ├── writeback.py
    │   ├── team_strength.py
    │   ├── intelligence.py
    │   └── upset.py
    ├── collectors/
    ├── models/
    ├── storage/
    ├── runtime/
    ├── .okooo-scraper/
    │   ├── runtime/
    │   ├── snapshots/
    │   └── schedules/
    ├── enhanced_prediction_workflow.py
    ├── prediction_system.py
    └── result_manager.py
```

关键含义：

- `prediction_system.py` 是兼容入口，真实命令实现集中在 `europe_leagues/app/cli.py`
- `domain/predictor.py` 作为接口层对外稳定外壳，内部仍调用 `EnhancedPredictor`
- `enhanced_prediction_workflow.py` 仍是主编排核心，但主要子能力已下沉到 `domain/*`
- `runtime/paths.py` 已统一 runtime、snapshots、schedules 与 `MEMORY.md` 路径
- `.okooo-scraper/runtime/` 是当前真实的运行时落盘目录

更完整的架构说明见：

- [europe_leagues_architecture.md](./docs/architecture/europe_leagues_architecture.md)

## 3. 事实写回边界

当前项目采用双路径写回：

- 五大联赛 SoT：`europe_leagues/<league>/teams_2025-26.md`
- 跨联赛滚动记忆：项目根 `MEMORY.md`
- 运行时归档与索引：`europe_leagues/.okooo-scraper/runtime/*.json`

当前实际 runtime 文件包括：

- `prediction_archive.json`
- `prediction_memory_odds_samples.json`
- `result_sync_registry.json`
- `accuracy_stats.json`
- `rag_cases.json`
- `rag_index.json`
- `rag_registry.json`
- `sofascore_team_ids.json`

这意味着：

- 五大联赛比赛要优先落回各自 `teams_2025-26.md`
- 欧战/杯赛不强写联赛 SoT，而是写入 `MEMORY.md` 和 runtime archive
- 文档或脚本如果仍把 `prediction_archive.json` 视为根目录文件，已经不准确

## 4. 正式联赛范围

当前正式纳入主链、可直接使用 CLI / Harness 预测的 competition code：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `europa_league`
- `champions_league`
- `conference_league`

注意：

- 仓库中存在某个目录，不等于它已经接入正式预测主链
- `afc_champions_league/` 当前更偏数据目录，不属于现行 `LEAGUE_CONFIG`

## 5. Agent 职责拆分

### 数据采集 Agent

负责：

- 赛程抓取
- `match_id` 定位
- 澳客实时快照刷新
- 别名归一与输入质量控制

当前实现重点：

- 正式入口优先 `prediction_system.py collect-data`
- `collectors/sporttery.py` 是当前正式赛程采集边界
- `collectors/okooo.py` 负责 `local-chrome` / `browser-use` driver 状态探测与切换
- 已知 `match_id` 时优先复用本地已有快照

### 比赛分析 Agent

负责：

- 基本面、战术、伤停、赛程背景、交锋与比赛脚本分析
- 结合 EWMA form、team context、match intelligence 解释预测结果

当前实现重点：

- 不允许继续把固定 `2.5` 当成正式大小球线
- 有真实盘口时，节奏解释必须基于真实 `over_under.line`
- 样本不足、快照缺失时必须明确说明边界

### 赔率分析 Agent

负责：

- 欧赔、亚值、凯利、大小球、水位与盘口错配分析
- 冷热识别、风险识别与庄家行为解释

当前实现重点：

- 重点检查 `over_under.line_source`
- 重点检查 `over_under.market.final`
- 真实大小球线优先来自澳客 `handicap.php` 页内 `大小球` tab

### 结果追踪 Agent

负责：

- 赛果回填
- 准确率统计
- 滚动记忆样本刷新
- RAG 索引联动更新

当前实现重点：

- 五大联赛以 `teams_2025-26.md` 为 SoT
- 欧战/杯赛以 `MEMORY.md` + runtime archive 为准
- 赛果更新后同步刷新 `prediction_memory_odds_samples.json` 与 RAG 索引

## 6. 标准执行流程

```text
Step 1: 确认联赛、对阵、日期，必要时补比赛时间
Step 2: 通过 collect-data 或赛程抓取定位 match_id
Step 3: 执行 predict-match / predict-schedule
Step 4: 主链自动补快照、EWMA form、RAG 检索与临场输入
Step 5: 检查关键结果字段与风险边界
Step 6: 按比赛类型写回 SoT 或 runtime-only 归档
Step 7: 赛后回填真实比分
Step 8: 刷新准确率、滚动记忆样本与 RAG 索引
```

可审计执行入口：

- `prediction_system.py harness-run --pipeline match_prediction`
- `prediction_system.py harness-run --pipeline result_recording`

## 7. 当前推荐命令

### 环境检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
```

### 赛程采集

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
```

### 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 布伦特福德 --date 2026-05-11 --json
```

### 欧战单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league champions_league --home-team 阿森纳 --away-team 马竞 --date 2026-05-08 --match-id 1324472 --no-refresh-odds --json
```

### 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule --league la_liga --date 2026-05-11 --days 2 --json
```

### Harness

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-list --json
python3 prediction_system.py harness-run --pipeline match_prediction --league la_liga --home-team 巴塞罗那 --away-team 皇家马德里 --date 2026-05-11 --json
```

### 赛果回填

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
python3 prediction_system.py auto-sync-results --json
python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04 --yes
python3 prediction_system.py accuracy --refresh --json
```

### RAG 维护

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py rag-rebuild --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --dry-run --json
```

## 8. 结果验收重点

正式预测结果中优先检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`
- `retrieved_memory`
- `retrieved_memory_explanation`
- `runtime_profile`

解释原则：

- `line_source=snapshot_final` 或 `snapshot_initial`，表示真实大小球线已进入主链
- `line_source=missing_real_line` 时，可以保留胜平负结论，但必须标记为“待补真实盘口”

## 9. 行为边界

当前调用方必须遵守：

1. 优先使用 `prediction_system.py` 非交互命令，并尽量附带 `--json`
2. 不把新结果写入旧 `predictions/`、`reports/` 或历史模板目录
3. 若用户只要求查看、解释、调研，默认不写文件
4. 如需阶段化、可审计结果，优先使用 Harness
5. 遇到 `browser-use`、快照、实时源缺失，可降级，但必须显式标记为降级或 mock 数据

## 10. 参考文档

- [README.md](./README.md)
- [europe_leagues_architecture.md](./docs/architecture/europe_leagues_architecture.md)
- [workflow.md](./docs/standards/workflow.md)
- [data_format.md](./docs/standards/data_format.md)
- [football_actuary_persona.md](./agents/football_actuary_persona.md)

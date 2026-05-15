---
project_name: "Football Prediction Agent"
version: "1.5.0"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-05-08"
---

# Football Prediction Agent

> 当前正式链路  
> 1. `prediction_system.py collect-data` 获取赛程、`match_id` 与上下文  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层  
> 3. 五大联赛写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写回 `MEMORY.md` 与 `.okooo-scraper/runtime/*`  
> 4. 赛后用 `save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填；结果闭环会统一刷新 archive / MEMORY / RAG / review-learning  
> 5. `accuracy --refresh` 仍可作为显式重建入口，但正常赛果闭环后准确率会自动同步刷新  
> 6. 如需阶段化、可审计结果，优先使用 `harness-run --pipeline ... --json`

## 项目简介

`Football Prediction Agent` 是围绕 `Europe Leagues` 项目构建的足球预测分析与赛果闭环系统。  
当前实现已经从“脚本集合”收敛为一条正式主链：

- `prediction_system.py` 兼容入口
- `app/cli.py` 真实 CLI 路由与 JSON 输出
- `DomainPredictor` / `EnhancedPredictor` 主预测编排
- `PredictionPersistenceService` 统一预测持久化 side effects
- `HybridRAGService` + review-learning + postprocess 主链增强
- SoT / runtime-only 双路径写回
- 赛果同步、准确率统计与 RAG 联动更新

当前系统重点能力：

- 五大联赛 + 欧战 competition config 统一预测入口
- 欧赔、亚值、凯利、大小球真实盘口链路
- EWMA 近况、临场刷新、已有快照复用
- RAG 相似案例、盘口案例与爆冷案例召回
- Harness 阶段化编排
- 赛果回填、滚动记忆样本、准确率统计

## 当前真实架构

当前项目可按 6 层理解：

1. 接口层：`prediction_system.py`、`app/cli.py`、`harness/*`
2. 编排层：`domain/predictor.py`、`enhanced_prediction_workflow.py`
3. 领域层：`domain/*`
4. 采集层：`collectors/*` 与保留的 `okooo_*`、`data_collector.py`
5. 模型与存储层：`models/*`、`storage/*`、`runtime/*`、`.okooo-scraper/*`
6. 治理层：`agents/*.md`、`agent_runtime_registry.py`

当前关键变化：

- `prediction_system.py` 已收缩为兼容壳
- 真实命令逻辑集中在 `europe_leagues/app/cli.py`
- `EnhancedPredictor` 负责预测主编排，`PredictionPersistenceService` 负责预测持久化编排
- `domain/inference.py`、`domain/postprocess.py`、`domain/rag.py` 与 review-learning 已接入正式预测主链
- runtime 物理落盘已统一到 `europe_leagues/.okooo-scraper/runtime/`
- `health-check`、`setup-openclaw` 已成为正式环境入口
- Hermes 或其他接入方应固定按“先发现 `prediction_system.py`，再下钻到 `app/cli.py` 真正执行”的规则识别

详细架构见：

- [agent.md](./agent.md)
- [europe_leagues_architecture.md](./docs/architecture/europe_leagues_architecture.md)
- [skill_lifecycle.md](./docs/standards/skill_lifecycle.md)
- [claude_sessionstart_hooks.json](./docs/examples/claude_sessionstart_hooks.json)

## 正式联赛范围

当前正式纳入 `LEAGUE_CONFIG` 的 competition code：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `europa_league`
- `champions_league`
- `conference_league`

注意：

- 目录存在不等于已经进入正式主链
- 如 `afc_champions_league/` 当前更像数据目录，而不是现行 CLI 正式联赛

## 事实写回与 runtime 边界

项目当前采用双路径写回：

- 五大联赛 SoT：`europe_leagues/<league>/teams_2025-26.md`
- 跨联赛滚动记忆：项目根 `MEMORY.md`
- 运行时归档与索引：`europe_leagues/.okooo-scraper/runtime/*.json`

当前 `.okooo-scraper/runtime/` 中的关键文件：

- `prediction_archive.json`
- `prediction_memory_odds_samples.json`
- `result_sync_registry.json`
- `accuracy_stats.json`
- `rag_cases.json`
- `rag_index.json`
- `rag_registry.json`
- `sofascore_team_ids.json`

同时存在两类配套目录：

- `.okooo-scraper/snapshots/`
- `.okooo-scraper/schedules/`

## 统一职业身份

项目统一身份不是单一的“看球专家”，而是：

- [专业纬度足彩数据精算师](./agents/football_actuary_persona.md)

这会直接影响：

- CLI 输出中的 `runtime_profile`
- Harness 返回结果中的 `runtime_profile`
- 预测结果、归档与执行口径

统一要求：

1. 区分 `模型结论`、`盘口结论`、`综合结论`
2. 显式说明样本边界、风险和降级路径
3. 不把缺失真实盘口的数据包装成正式大小球结论

## 快速开始

### 1. 阅读入口文档

- [agent.md](./agent.md)
- [europe_leagues_architecture.md](./docs/architecture/europe_leagues_architecture.md)
- [workflow.md](./docs/standards/workflow.md)
- [skill_lifecycle.md](./docs/standards/skill_lifecycle.md)

## Skill 生命周期治理

当前仓库采用“Skill 正文与生命周期治理分层”的约定：

- `SKILL.md` 只负责描述能力、入口、执行步骤与输出要求
- 安装、更新、同步、版本检查不写进 `SKILL.md`
- 生命周期治理应放在 CLI、Hook、启动脚本或 Harness 层
- 仓库内文档与 Skill 内容同步属于维护脚本职责，不属于 Agent 运行时推理职责

这条规则的目的，是避免把横切治理逻辑污染进能力正文，减少 Token 噪音，并保证 Agent 在开始任务前就已经看到最新 Skill 副本。

### 2. 环境准备

```bash
cd /Users/bytedance/trae_projects
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-openclaw.txt
python3 -m playwright install chromium
```

如果希望一次性初始化 `openclaw` 所需环境：

```bash
bash scripts/setup_openclaw_env.sh
```

### 3. 检查运行环境

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

## 当前标准流程

```text
1. 确认比赛信息
2. collect-data / 赛程抓取定位 match_id
3. predict-match / predict-schedule
4. 检查 over_under.line / line_source / over_under.market.final
5. 检查 retrieved_memory / retrieved_memory_explanation
6. 按比赛类型写回 SoT 或 runtime-only 归档
7. save-result / bulk_fetch_and_update.py / auto-sync-results / result-sync-daemon 回填赛果
8. 结果闭环自动刷新胜负 / 比分 / 大小球统计、记忆样本、RAG 与 review-learning；需要显式重建时再执行 accuracy --refresh
```

关键原则：

- 真实大小球优先，不再把 `default_2.5` 作为正式结论
- 若 `line_source=missing_real_line`，允许保留胜平负，但必须标记为待补真实盘口
- 新预测会原生写入 `RAG记忆:`
- `sync-memory-rag` 主要用于历史条目回填
- 旧 `predictions/`、`analysis/predictions/*.md`、`reports/` 不再作为正式主流程输出

## 常用命令

### 环境检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
```

### 赛程采集

```bash
python3 prediction_system.py collect-data --league la_liga --date 2026-05-11 --json
```

### 单场预测

```bash
python3 prediction_system.py predict-match \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 皇家马德里 \
  --date 2026-05-11 \
  --json
```

### 欧战预测

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

### 批量预测

```bash
python3 prediction_system.py predict-schedule \
  --league la_liga \
  --date 2026-05-11 \
  --days 2 \
  --json
```

### Harness

```bash
python3 prediction_system.py harness-list --json
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league la_liga \
  --home-team 巴塞罗那 \
  --away-team 皇家马德里 \
  --date 2026-05-11 \
  --json
```

### 赛果回填与统计

```bash
python3 prediction_system.py save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py result-sync-daemon --json
python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04 --yes
python3 prediction_system.py accuracy --refresh --json
```

### RAG 维护

```bash
python3 prediction_system.py rag-rebuild --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --dry-run --json
```

## 结果验收

预测结果中优先检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`
- `retrieved_memory`
- `retrieved_memory_explanation`
- `runtime_profile`

判定重点：

- `snapshot_final` / `snapshot_initial`：真实大小球线已进入主链
- `missing_real_line`：允许保留胜平负，不输出正式大小球结论

## 文档导航

### 入口文档

- [agent.md](./agent.md)：Agent 身份、职责、主链边界
- [europe_leagues_architecture.md](./docs/architecture/europe_leagues_architecture.md)：当前真实架构分析

### 标准规范

- [workflow.md](./docs/standards/workflow.md)
- [data_format.md](./docs/standards/data_format.md)
- [coding_standards.md](./docs/standards/coding_standards.md)

### Agent 文档

- [football_actuary_persona.md](./agents/football_actuary_persona.md)
- [data_collector_agent.md](./agents/data_collector_agent.md)
- [match_analyzer_agent.md](./agents/match_analyzer_agent.md)
- [odds_analyzer_agent.md](./agents/odds_analyzer_agent.md)
- [result_tracker_agent.md](./agents/result_tracker_agent.md)

## 联系方式

- 维护者：LorenJun
- 邮箱：jl.07221@qq.com

## 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 1.0.0 | 2026-04-18 | 初始版本 |
| 1.1.0 | 2026-05-04 | 同步正式预测链路与 Harness 口径 |
| 1.2.0 | 2026-05-07 | 同步模块化整改后的目录结构与领域服务 |
| 1.3.0 | 2026-05-08 | 补充 RAG 记忆层与历史回填说明 |
| 1.4.0 | 2026-05-08 | 补充欧战 competition config 与 runtime-only 链路 |
| 1.5.0 | 2026-05-08 | 按当前真实架构重写 README，统一 CLI、runtime 路径、Agent 身份与文档导航口径 |

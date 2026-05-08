---
project_name: "Football Prediction Agent"
version: "1.4.0"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-05-08"
---

# 足球比赛预测分析Agent

> 当前正式流程  
> 1. `prediction_system.py collect-data` 或赛程抓取定位 `match_id`  
> 2. `prediction_system.py predict-match / predict-schedule` 执行增强预测，并自动接入 RAG 记忆层  
> 3. 五大联赛 SoT 写回 `europe_leagues/<league>/teams_2025-26.md`；欧战/杯赛写入 `MEMORY.md` 与 runtime-only 归档  
> 4. 赛后用 `prediction_system.py save-result`、`auto-sync-results`、`result-sync-daemon` 或 `bulk_fetch_and_update.py` 回填  
> 5. 最后用 `prediction_system.py accuracy --refresh --json` 刷新胜负 / 比分 / 大小球统计  
> 可审计编排入口：`prediction_system.py harness-run --pipeline ... --json`  
> 关键检查项：`over_under.line`、`line_source`、`over_under.market.final`、`retrieved_memory_explanation`
> 欧战正式 competition config：`europa_league`、`champions_league`、`conference_league` 已进入主链，但写回仍保持 `runtime_only`

## 项目概述

本项目是一个以欧洲联赛为核心的足球预测系统，当前已完成“赛程/MatchID -> 实时赔率快照 -> `app/cli.py` -> `DomainPredictor` / `EnhancedPredictor` -> `HybridRAGService` -> SoT/runtime-only 双路径写回 -> 准确率统计”的主流程收敛，并支持可选的 Sofascore 球队状态增强（team_context）与 Harness 编排入口。

### 当前核心功能

- 多联赛比赛分析与预测
- 实时欧赔、亚值、凯利抓取
- 从 `亚值` 页面内 `大小球` tab 抓取真实盘口线与水位
- 可选：SofaScore 近况增强（阵型/控球/上一场首发/球员评分趋势）注入 `analysis_context['team_context']`
- 多模型融合、Dixon-Coles、动态调权、临场修正
- EWMA 近况自动回填、缺失大小球自动补抓
- 混合检索 RAG 记忆层、盘口相似案例与爆冷案例召回
- 新预测原生写入 `RAG记忆:`，历史记录可通过 `sync-memory-rag` 回填
- 预测结果按“双路径写回”落到 `teams_2025-26.md` 或 `MEMORY.md` / runtime archive
- `europa_league`、`champions_league`、`conference_league` 作为正式 competition config 直接复用欧战快照与真实盘口链路
- 赛果回填与准确率统计（胜负 / 比分 / 大小球）
- Harness pipeline：`match_prediction` / `result_recording`

## 当前架构

```text
trae_projects/
├── agent.md
├── agents/
│   ├── data_collector_agent.md
│   ├── football_actuary_persona.md
│   ├── match_analyzer_agent.md
│   ├── odds_analyzer_agent.md
│   └── result_tracker_agent.md
├── .trae/skills/
│   ├── football-match-analysis/
│   ├── okooo-match-finder/
│   ├── browser-use/
│   ├── chrome-mcp/
│   └── playwright-cli/
└── europe_leagues/
    ├── app/cli.py
    ├── harness/
    │   ├── core.py
    │   └── football.py
    ├── domain/
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
    ├── collectors/
    ├── models/
    ├── storage/
    ├── runtime/
    │   ├── memory_samples.py
    │   ├── result_sync.py
    │   └── rag_store.py
    ├── prediction_system.py
    ├── enhanced_prediction_workflow.py
    ├── result_manager.py
    ├── <league>/teams_2025-26.md
    └── .okooo-scraper/
```

当前含义：

- `prediction_system.py` 仅作为兼容入口，实际命令实现已迁移到 `app/cli.py`
- `DomainPredictor` 是接口层与预测核心之间的稳定外壳
- `EnhancedPredictor` 仍保留主流程编排职责，但大部分子能力已拆到 `domain/`、`collectors/`、`storage/`、`runtime/`
- `agents/*.md` 与 `agent_runtime_registry.py` 共同决定运行时输出中的 `runtime_profile`

## 事实与写回边界

当前项目采用双路径写回：

- 五大联赛 SoT：`europe_leagues/<league>/teams_2025-26.md`
- 欧战/杯赛 runtime-only：`MEMORY.md`、`prediction_archive.json`、`prediction_memory_odds_samples.json`、`result_sync_registry.json`
- RAG 索引：`rag_cases.json`、`rag_index.json`、`rag_registry.json`
- 欧战 canonical 归档字段：`league_code`、`league_name`、`snapshot_dir`、`snapshot_dir_aliases`、`snapshot_path`、`line_source`

运行时抓取与缓存目录：

- `europe_leagues/.okooo-scraper/snapshots/`
- `europe_leagues/.okooo-scraper/schedules/`
- `europe_leagues/.okooo-scraper/runtime/`

## 统一职业身份

当前项目所有 Agent、主流程与复盘链路，统一采用：

- [专业纬度足彩数据精算师](./agents/football_actuary_persona.md)

作为上位职业身份约束。

其核心含义是：

- 不只看基本面
- 不只看盘口
- 不只跑模型
- 不只统计结果

而是同时从以下专业纬度工作：

1. 足球业务分析
2. 赔率与盘口交易分析
3. 概率建模
4. 统计验证与模型评估
5. 风险控制与资金管理
6. 数据工程、流程自动化与策略迭代

统一要求：

- 输出中区分 `模型结论`、`盘口结论`、`综合结论`
- 明确样本边界、降级情况与风险提示
- 正式流程服从 `prediction_system.py` -> `app/cli.py`、`DomainPredictor` / `EnhancedPredictor`、`bulk_fetch_and_update.py` 与 `harness`
- 正式写回必须遵守“双路径写回”，并避免把结果写回旧 `predictions/`、`reports/` 目录

## Agent 分工

### 数据采集Agent

负责：

- 获取赛程
- 定位 `match_id`
- 刷新实时快照
- 维护球队简称映射
- 为批量结果回填准备当天已结束比赛

当前要求：

- 优先使用 `okooo_fetch_daily_schedule.py`
- 自动化场景优先使用 `prediction_system.py collect-data`
- 已知 `match_id` 时直接抓快照
- 大小球优先走 `handicap.php` 页面内 `大小球` tab
- 在精算师职业画像中，主要承担“第 6 维数据工程、流程自动化与策略迭代中的输入准备与质量控制”职责

### 比赛分析Agent

负责：

- 基本面、战术、伤停、战意、历史交锋
- 结合赔率上下文解释比分与节奏

当前要求：

- 若真实大小球线已存在，不再沿用固定 `2.5` 解释比赛节奏
- 可结合 `analysis_context['team_context']`、EWMA form 与 `match_intelligence`
- 在精算师职业画像中，主要承担“第 1 维足球业务分析 + 第 5 维风险控制支持中的比赛脚本解释”职责

### 赔率分析Agent

负责：

- 欧赔、亚值、凯利、大小球、水位
- 庄家心理、盘口错配、冷热识别

当前要求：

- 最终报告里要明确输出真实大小球盘口线与大/小水位
- 重点检查 `over_under.line_source` 与 `over_under.market.final`
- 在精算师职业画像中，主要承担“第 2 维赔率与盘口交易分析 + 第 5 维风险控制支持中的盘口风险识别”职责

### 结果追踪Agent

负责：

- 赛果回填
- 准确率统计
- 历史学习数据更新

当前要求：

- 五大联赛以 `teams_2025-26.md` 为 SoT，欧战/杯赛以 `MEMORY.md` + runtime archive 为准
- 支持 `bulk_fetch_and_update.py` 批量回填
- 支持 `auto-sync-results` 与 `result-sync-daemon`
- 统计胜负、比分、大小球准确率
- 赛果更新后同步刷新 `prediction_memory_odds_samples.json` 与 RAG 索引
- 在精算师职业画像中，主要承担“第 4 维统计验证与模型评估 + 第 6 维策略迭代支持”职责

## 技能说明

### `football-match-analysis`

用于执行完整的单场足球预测，要求：

- 优先走 `prediction_system.py predict-match`
- 由 `app/cli.py` -> `DomainPredictor` -> `EnhancedPredictor` 调度真实主链
- 自动拉实时快照并处理大小球补抓
- 自动检索 RAG 相似记忆并生成 `retrieved_memory_explanation`
- 使用真实大小球盘口线
- 输出终版结论、必要诊断字段和 `runtime_profile`

### `okooo-match-finder`

用于：

- 定位 `match_id`
- 输出可抓取入口与赛程上下文
- 为实时快照、批量采集、Harness 预测和批量结果更新提供稳定前置输入

## 当前标准流程

```text
Step 1: 确认联赛、对阵、日期、必要时比赛时间
Step 2: 抓赛程并获取 MatchID / kickoff_time
Step 3: 通过 `collect-data` 复用赛程与已有快照
Step 4: 刷新实时快照（欧赔/亚值/大小球/凯利）
Step 4.5: 自动补 EWMA form、缺失大小球，按需注入 `team_context`
Step 5: 运行 `DomainPredictor` / `EnhancedPredictor` + `HybridRAGService` 编排流程
Step 6: 输出最终结论与 `retrieved_memory_explanation`
Step 7: 按比赛类型写回 teams_2025-26.md 或 MEMORY.md / runtime archive
Step 8: 赛后回填真实比分并更新胜负/比分/大小球准确率
```

## 推荐命令

### 抓赛程

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 抓实时快照

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py --driver local-chrome --league 英超 --team1 曼联 --team2 布伦特福德 --date 2026-04-28 --time 03:00 --match-id 1296070 --overwrite
```

### 跑单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 布伦特福德 --date 2026-04-28 --json
```

### 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule --league premier_league --date 2026-04-28 --days 1 --json
```

### Harness 入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --date 2026-04-28 --home-team 曼联 --away-team 布伦特福德 --json
```

### 批量结果回填

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04 --yes
```

## 结果验收标准

预测结果中应优先检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`
- `retrieved_memory_explanation`

若 `line_source=snapshot_final`，说明真实大小球数据已成功进入预测流程。

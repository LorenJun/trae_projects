---
project_name: "Football Prediction Agent"
version: "1.2.0"
author: "LorenJun"
email: "jl.07221@qq.com"
created_date: "2026-04-18"
last_updated_date: "2026-04-27"
---

# 足球比赛预测分析Agent

## 项目概述

本项目是一个以欧洲联赛为核心的足球预测系统，当前已完成“实时赔率快照 + 亚值页内大小球抓取 + 综合预测输出”的主流程收敛，并支持可选的 Sofascore 球队状态增强（team_context）。

### 当前核心功能

- 多联赛比赛分析与预测
- 实时欧赔、亚值、凯利抓取
- 从 `亚值` 页面内 `大小球` tab 抓取真实盘口线与水位
- 可选：SofaScore 近况增强（阵型/控球/上一场首发/球员评分趋势）注入 `analysis_context['team_context']`
- 多模型融合、Dixon-Coles、动态调权、临场修正
- 预测结果写回 `teams_2025-26.md`
- 赛果回填与准确率统计

## 当前架构

```text
trae_projects/
├── agent.md
├── agents/
│   ├── data_collector_agent.md
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
    ├── enhanced_prediction_workflow.py
    ├── okooo_save_snapshot.py
    ├── okooo_fetch_daily_schedule.py
    ├── okooo_live_snapshot.py
    ├── okooo_team_aliases.json
    ├── result_manager.py
    ├── <league>/teams_2025-26.md
    └── .okooo-scraper/
```

## 单一事实来源

正式预测与赛果学习统一以：

- `europe_leagues/<league>/teams_2025-26.md`

为准。

运行时抓取与缓存目录：

- `europe_leagues/.okooo-scraper/snapshots/`
- `europe_leagues/.okooo-scraper/schedules/`
- `europe_leagues/.okooo-scraper/runtime/`

## Agent 分工

### 数据采集Agent

负责：

- 获取赛程
- 定位 `match_id`
- 刷新实时快照
- 维护球队简称映射

当前要求：

- 优先使用 `okooo_fetch_daily_schedule.py`
- 已知 `match_id` 时直接抓快照
- 大小球优先走 `handicap.php` 页面内 `大小球` tab

### 比赛分析Agent

负责：

- 基本面、战术、伤停、战意、历史交锋
- 结合赔率上下文解释比分与节奏

当前要求：

- 若真实大小球线已存在，不再沿用固定 `2.5` 解释比赛节奏

### 赔率分析Agent

负责：

- 欧赔、亚值、凯利、大小球、水位
- 庄家心理、盘口错配、冷热识别

当前要求：

- 最终报告里要明确输出真实大小球盘口线与大/小水位

### 结果追踪Agent

负责：

- 赛果回填
- 准确率统计
- 历史学习数据更新

## 技能说明

### `football-match-analysis`

用于执行完整的单场足球预测，要求：

- 优先拉实时快照
- 使用真实大小球盘口线
- 输出终版结论

### `okooo-match-finder`

用于：

- 定位 `match_id`
- 输出可抓取入口
- 为实时快照与预测流程提供稳定前置输入

## 当前标准流程

```text
Step 1: 确认联赛、对阵、日期、必要时比赛时间
Step 2: 抓赛程并获取 MatchID
Step 3: 刷新实时快照（欧赔/亚值/大小球/凯利）
Step 3.5 (可选): 注入球队状态增强（默认开启，可用 ENABLE_TEAM_CONTEXT=0 关闭）
Step 4: 运行增强预测流程
Step 5: 输出最终结论
Step 6: 需要落盘时写回 teams_2025-26.md
Step 7: 赛后回填真实比分并更新准确率
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

## 结果验收标准

预测结果中应优先检查：

- `final_probabilities`
- `top_scores`
- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`

若 `line_source=snapshot_final`，说明真实大小球数据已成功进入预测流程。

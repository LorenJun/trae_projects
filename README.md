---
project_name: "Football Prediction Agent"
version: "1.6.0"
author: "LorenJun"
created_date: "2026-04-18"
last_updated_date: "2026-05-19"
---

# Football Prediction Agent

本仓库当前最核心、最活跃的正式应用位于 `europe_leagues/`。

## 仓库级入口说明

对于 `europe_leagues/` 子项目，入口分两层：

- `europe_leagues/prediction_system.py`：兼容 / 发现入口
- `europe_leagues/app/cli.py`：真实 CLI 路由、命令实现与 JSON 输出入口

不要把 `prediction_system.py` 误当成业务主逻辑。

## 当前正式应用范围

当前正式能力主要围绕 `europe_leagues/` 展开，包括：

- 赛程采集与 `match_id` 定位
- 单场 / 批量足球预测
- SoT-backed 与 runtime-only 双路径持久化
- 赛果同步、结果闭环与准确率统计
- RAG 记忆、样本索引与赛后复盘
- Harness 阶段化编排与审计输出

## 当前正式命令面（europe_leagues）

主要正式命令包括：

- `list-leagues`
- `predict-match`
- `predict-match-lite`
- `predict-schedule`
- `collect-data`
- `pending-results`
- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `accuracy`
- `sync-pending-results-review`
- `build-season-master-review`
- `refresh-repo-docs`
- `purge-nonreal-data`
- `rag-rebuild`
- `rag-diagnose`
- `sync-memory-rag`
- `health-check`
- `migrate-archive`
- `setup-openclaw`
- `harness-list`
- `harness-run`

## 仓库结构（高层）

```text
/Users/bytedance/trae_projects/
├── europe_leagues/          # 当前正式足球预测应用
├── .trae/skills/            # 仓库根 skill 定义
├── docs/                    # 仓库根通用文档（若存在）
└── README.md
```

## europe_leagues 的关键分层

- 入口层：`europe_leagues/prediction_system.py`、`europe_leagues/app/cli.py`
- 编排层：`europe_leagues/domain/predictor.py`、`europe_leagues/enhanced_prediction_workflow.py`
- 领域层：`europe_leagues/domain/*`
- 结果闭环：`europe_leagues/domain/persistence.py`、`europe_leagues/runtime/result_sync.py`、`europe_leagues/result_manager.py`
- 存储层：`europe_leagues/storage/*`、`europe_leagues/.okooo-scraper/*`

## SoT / runtime-only 边界

### SoT-backed competitions

以下 competition 以 markdown SoT 为主：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

### runtime-only competitions

以下 competition 主要写入运行时归档与滚动记忆：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

## 仓库根 skills

当前 skills 位于：

- `/Users/bytedance/trae_projects/.trae/skills/`

与 `europe_leagues/` 直接相关的高价值 skills 包括：

- `football-match-analysis`
- `okooo-match-finder`
- `sync-pending-results-review`
- `update-five-leagues-schedules`
- `update-five-leagues-players`

这些 skill 的说明也应与 `europe_leagues/` 当前 CLI-first 架构保持一致。

## 快速开始

### 环境准备

```bash
cd /Users/bytedance/trae_projects
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
python3 -m pip install -r requirements-openclaw.txt
python3 -m playwright install chromium
```

### 基础健康检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

### 预测与回填示例

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py accuracy --refresh --json
```

## 相关文档

优先阅读：

- `europe_leagues/README.md`
- `europe_leagues/README_使用指南.md`
- `europe_leagues/docs/INDEX.md`
- `europe_leagues/docs/PRD_足球预测系统_2026.md`
- `europe_leagues/ODDS_FETCH_GUIDE.md`
- `europe_leagues/docs/upset_warning_guide.md`
- `debug-local-odds-access.md`

当前与澳客实时赔率链路最相关的补充结论：

- 正式快照链默认走 `local-chrome`
- 默认请求口径已统一为 `iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池由 `europe_leagues/okooo_mobile_access.py` 统一维护，当前为 `100` 组随机 profile
- 已验证正式 `predict-match` 可稳定拿到真实欧赔、亚值、大小球、凯利数据

如仓库级说明与代码冲突，以这些实现为准：

- `europe_leagues/app/cli.py`
- `europe_leagues/domain/persistence.py`
- `europe_leagues/runtime/result_sync.py`
- `europe_leagues/result_manager.py`

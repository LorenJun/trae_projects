# 足球预测系统（Europe Leagues）

本目录是当前仓库里真正运行的足球预测应用。

## 入口与权威链

当前正式入口分两层：

- `prediction_system.py`：兼容 / 发现入口
- `app/cli.py`：真实 CLI 路由、JSON 输出与命令实现

不要把 `prediction_system.py` 误当成业务主逻辑；真正的命令面、参数和输出约定以 `app/cli.py` 为准。

## 当前正式能力

当前正式 CLI 子命令包括：

- 基础：`list-leagues`、`health-check`、`setup-openclaw`
- 采集与预测：`collect-data`、`predict-match`、`predict-match-lite`、`predict-schedule`
- 结果同步：`pending-results`、`save-result`、`auto-sync-results`、`result-sync-daemon`
- 复盘与治理：`accuracy`、`sync-pending-results-review`、`build-season-master-review`
- 文档与清理：`refresh-repo-docs`、`purge-nonreal-data`
- RAG：`rag-rebuild`、`rag-diagnose`、`sync-memory-rag`
- 归档与运行：`migrate-archive`、`harness-list`、`harness-run`

## 当前架构

核心运行链路：

1. `prediction_system.py` 接收命令并转发到 `app/cli.py`
2. `domain/predictor.py` 暴露稳定预测外壳
3. `enhanced_prediction_workflow.py` 负责主编排
4. `domain/*` 提供 inference / postprocess / live / rag / persistence 等领域服务
5. `domain/persistence.py` 负责预测落盘 side effects
6. `runtime/result_sync.py` 与 `result_manager.py` 负责赛果同步、结果闭环与衍生数据更新

关键文件：

- `app/cli.py`
- `prediction_system.py`
- `domain/predictor.py`
- `enhanced_prediction_workflow.py`
- `domain/persistence.py`
- `runtime/result_sync.py`
- `result_manager.py`

## SoT 与 runtime 边界

当前系统有两类持久化路径：

### 1. league-backed / SoT-backed competitions

以下比赛类型以 markdown SoT 为主：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

对应主事实源：

- 五大联赛：`<league>/teams_2025-26.md`
- 世界杯：`world_cup/teams_2026.md`

### 2. runtime-only competitions

以下比赛类型以运行时归档与滚动记忆为主：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战类扩展 competition

对应主持久化路径：

- 项目根 `MEMORY.md`
- `.okooo-scraper/runtime/*.json`

## 预测与结果闭环

预测 side effects 由 `domain/persistence.py` 统一编排，通常会联动：

- SoT 写回或 runtime-only 归档
- `MEMORY.md` 滚动记忆更新
- prediction archive 更新
- RAG 样本 / 索引同步
- result sync registry 登记

赛后结果闭环由下列入口负责：

- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `sync-pending-results-review`

结果命中后，`runtime/result_sync.py` 与 `result_manager.py` 会协同刷新：

- SoT 比分/备注
- `MEMORY.md`
- `prediction_archive.json`
- 准确率统计
- RAG / 记忆样本 / review-learning 相关衍生数据

`accuracy --refresh` 仍然可用，但更多是显式重建入口，不是唯一的日常闭环方式。

## 常用命令

### 环境检查

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py setup-openclaw --json
python3 prediction_system.py health-check --json
python3 prediction_system.py list-leagues --json
```

### 采集与预测

```bash
python3 prediction_system.py collect-data --league premier_league --date 2026-05-11 --json
python3 prediction_system.py predict-match --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
python3 prediction_system.py predict-schedule --league premier_league --date 2026-05-11 --days 1 --json
python3 prediction_system.py harness-run --pipeline match_prediction --league premier_league --home-team 曼联 --away-team 切尔西 --date 2026-05-11 --json
```

### 结果同步与复盘

```bash
python3 prediction_system.py pending-results --days-back 14 --json
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py save-result --match-id premier_league_20260511_曼联_切尔西 --home-score 2 --away-score 1 --json
python3 prediction_system.py result-sync-daemon --json
python3 prediction_system.py sync-pending-results-review --days-back 30 --limit 20 --json
python3 prediction_system.py accuracy --refresh --json
```

### RAG 与仓库治理

```bash
python3 prediction_system.py rag-rebuild --json
python3 prediction_system.py rag-diagnose --json
python3 prediction_system.py sync-memory-rag --json
python3 prediction_system.py purge-nonreal-data --json
python3 prediction_system.py refresh-repo-docs --json
```

## 目录概览

```text
europe_leagues/
├── app/cli.py
├── prediction_system.py
├── enhanced_prediction_workflow.py
├── domain/
├── runtime/
├── storage/
├── harness/
├── collectors/
├── .okooo-scraper/
├── premier_league/
├── la_liga/
├── serie_a/
├── bundesliga/
├── ligue_1/
└── world_cup/
```

## 相关文档

本子目录当前仍有效的高价值文档：

- `README_使用指南.md`
- `ODDS_FETCH_GUIDE.md`
- `docs/INDEX.md`
- `docs/PRD_足球预测系统_2026.md`
- `docs/upset_warning_guide.md`
- `../debug-local-odds-access.md`

其中与澳客访问和实时赔率最相关的当前结论是：

- 正式快照链默认走 `local-chrome`
- 默认访问口径是 `iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池由 `okooo_mobile_access.py` 统一维护，当前为 `100` 组随机 profile
- 正式 `predict-match` 已验证可稳定拿到真实欧赔、亚值、大小球、凯利数据

仓库根下的 skills 位于：

- `/Users/bytedance/trae_projects/.trae/skills/`

如果文档、skill 与代码冲突，以当前代码实现为准，优先看：

- `app/cli.py`
- `domain/persistence.py`
- `runtime/result_sync.py`
- `result_manager.py`

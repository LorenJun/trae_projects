#!/usr/bin/env markdown
# europe_leagues 文档导航路由

> 本页是 **纯导航页**：只告诉你"哪个场景该读哪个文件、读哪一节"，不展开具体内容。
> 弱模型/新 agent 请 **先读本页(<120 行)**，再按指向精确跳转目标文件，不要全文吞长文档。

---

## 一、我想做什么 → 该读哪个文件

| 你的目标 | 去读 | 关键章节 |
|---|---|---|
| 了解系统总览 / 入口 / 架构边界 | [`../README.md`](../README.md) | 顶部「速读摘要」+「入口与权威链」 |
| 学怎么跑命令 / 日常工作流 | [`../README_使用指南.md`](../README_使用指南.md) | 「默认工作流」 |
| 理解产品边界 / SoT 治理语义 | [`PRD_足球预测系统_2026.md`](./PRD_足球预测系统_2026.md) | 顶部「速读摘要」+ 第 2、5 节 |
| 抓澳客盘口 / 欧赔凯利解析 / 排障 | [`../ODDS_FETCH_GUIDE.md`](../ODDS_FETCH_GUIDE.md) | 「盘口抓取链路」「解析规则」 |
| 抓盘口反复撞验证墙 / 固定 IP 单机抗封 | [`../ODDS_FETCH_GUIDE.md`](../ODDS_FETCH_GUIDE.md) | 「固定 IP 单机抗封三道防线」 |
| 本机 odds.php 被拦截排障 | [`../../debug-local-odds-access.md`](../../debug-local-odds-access.md) | 全文 |
| 爆冷预警怎么用 | [`upset_warning_guide.md`](./upset_warning_guide.md) | 全文 |
| 看历史爆冷案例 | [`../爆冷案例库.md`](../爆冷案例库.md) | 按联赛/类型检索 |

---

## 二、权威入口（代码真相源）

文档可能滞后，**实现以代码为准**：

- 兼容 / 发现入口：[`../prediction_system.py`](../prediction_system.py)
- 真实 CLI 实现：[`../app/cli.py`](../app/cli.py)
- 预测持久化编排：[`../domain/persistence.py`](../domain/persistence.py)
- 结果同步与轮询：[`../runtime/result_sync.py`](../runtime/result_sync.py)
- 结果归档与准确率：[`../result_manager.py`](../result_manager.py)

---

## 三、正式命令面（速查）

完整参数见 [`../app/cli.py`](../app/cli.py)。当前正式子命令：

`list-leagues` · `predict-match` · `predict-match-lite` · `predict-schedule` · `collect-data` · `pending-results` · `save-result` · `auto-sync-results` · `result-sync-daemon` · `accuracy` · `apply-reanalysis` · `sync-pending-results-review` · `build-season-master-review` · `refresh-repo-docs` · `purge-nonreal-data` · `rag-rebuild` · `rag-diagnose` · `sync-memory-rag` · `health-check` · `migrate-archive` · `setup-openclaw` · `harness-list` · `harness-run`

---

## 四、持久化边界（速查，细节见 PRD 第 5 节）

| 类别 | competition | 是否写滚动记忆/归档 |
|---|---|---|
| **SoT-backed** | premier_league / la_liga / serie_a / bundesliga / ligue_1 / world_cup | 写回 archive + 滚动记忆 + `<league>/teams_*.md`（五大联赛 `teams_2025-26.md`，世界杯 `teams_2026.md`）；`predict-match` 与 `predict-match-lite` 均进正式归档 |
| **runtime-only** | europa_league / champions_league / conference_league / 其他杯赛 | 运行时归档 + 滚动记忆 |
| **reference-only** | 友谊赛（仅 `friendly`） | **不写滚动记忆、不进归档**（`predict-match` 与 `predict-match-lite` 均强制跳过，标记 `reference_only_league_not_persisted`） |

---

## 五、技能位置

仓库根 skills：[`/Users/bytedance/trae_projects/.trae/skills/`](/Users/bytedance/trae_projects/.trae/skills/)

与本项目高相关：`football-match-analysis` · `okooo-match-finder` · `sync-pending-results-review` · `update-five-leagues-schedules` · `update-five-leagues-players`

---

## 索引维护原则

本文件**只做路由、不展开内容**。新增/删除文档时只更新本页的「目标→文件」映射；具体技术口径一律写进目标文件自身的「速读摘要」头部，不要回流到本页，避免索引膨胀成第二份正文。

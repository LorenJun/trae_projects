#!/usr/bin/env markdown
# europe_leagues Docs Index

本目录只收录当前 `europe_leagues/` 子项目里真实存在、且仍有维护价值的文档。

## 权威入口

- 兼容 / 发现入口：`../prediction_system.py`
- 真实 CLI 实现：`../app/cli.py`
- 预测持久化编排：`../domain/persistence.py`
- 结果同步与轮询：`../runtime/result_sync.py`
- 结果归档与准确率：`../result_manager.py`

## 当前正式命令面

当前正式 CLI 子命令包括：

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

## 文档目录

### 核心说明

- `../README.md`：应用级总览、入口说明、SoT/runtime 边界、核心命令
- `../README_使用指南.md`：CLI-first 执行手册与常见工作流
- `PRD_足球预测系统_2026.md`：产品视角 PRD 与当前架构假设

### 专项指南

- `../ODDS_FETCH_GUIDE.md`：澳客赛程、快照、大小球与预测衔接说明
- `../../debug-local-odds-access.md`：本机访问 `odds.php` 被拦截时的请求头排障结论
- `upset_warning_guide.md`：爆冷预警相关的当前使用说明

## 澳客访问现状

当前正式链路关于 `m.okooo.com` 的有效口径是：

- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 默认移动 profile 池：`../okooo_mobile_access.py`，当前为 `100` 组随机 profile
- 欧赔解析：优先 `multi_company_consensus`，`99家平均` 仅作为 fallback
- 已验证样例：`la_liga / 埃尔切 vs 赫塔费 / MatchID=1302914` 可稳定拿到真实欧赔、亚值、大小球、凯利

## 当前持久化边界

### SoT-backed

以下 competition 以 markdown SoT 为主：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

### runtime-only

以下 competition 以运行时归档与滚动记忆为主：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

## 技能位置

仓库根的 skills 位于：

- `/Users/bytedance/trae_projects/.trae/skills/`

与本子项目直接相关的高价值 skill 包括：

- `football-match-analysis`
- `okooo-match-finder`
- `sync-pending-results-review`
- `update-five-leagues-schedules`
- `update-five-leagues-players`

## 索引维护原则

本文件只保留：

- 当前真实存在的文件
- 当前正式链路仍然使用的说明
- 不会误导读者进入失效路径的链接

如果某份外层仓库文档不是当前子项目运行所必需，就不要在这里把它当作本目录的默认依赖。

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
- `apply-reanalysis`
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

- `../README.md`：应用级总览、入口说明、SoT/runtime 边界、核心命令、官方 vs replay 准确率口径
- `../README_使用指南.md`：CLI-first 执行手册、默认工作流、高级 replay/apply 维护流
- `PRD_足球预测系统_2026.md`：产品视角 PRD、当前架构假设与 replay/apply 治理语义

### 专项指南

- `../ODDS_FETCH_GUIDE.md`：澳客赛程、快照、大小球与预测衔接说明
- `../../debug-local-odds-access.md`：本机访问 `odds.php` 被拦截时的请求头排障结论
- `upset_warning_guide.md`：爆冷预警相关的当前使用说明

## 澳客访问现状

当前正式链路关于 `m.okooo.com` 的有效口径是：

- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 默认移动 profile 池：`../okooo_mobile_access.py`，当前为 `500` 组 `iPhone Safari` profile，分布在多个 iPhone device pool 上
- 正式主动访问的移动端 URL 统一由 `runtime.match_ids.build_okooo_match_url()` 构造，只接受纯数字 `external_match_id`
- `internal_match_id / teams_match_id` 只允许在项目内部使用，不能再拼进澳客 `MatchID`
- 盘口抓取统一走单会话 hub 真实导航：暖首页 → `history.php` → 点 `亚指`/`欧指` 整页跳转 → 页内点 `大小球`/`凯利` tab，一次会话拿回四盘；已移除所有深链回退路径
- 阻断检测现在同时覆盖文字风控页和滑块/图形验证页
- 命中验证页后，当前入口路径会快速返回 `verification_required` 并停止本路径重试；同时会打开基于 `match_id + market_family` 的 TTL breaker，并在市场页访问前执行最小间隔节流，避免持续撞验证页
- hub 链路在每次整页跳转后及解析完四盘后都做强阻断判定，任意盘口命中验证墙都会上抛顶层 `blocked` 触发熔断与换池重入，避免中途撞墙静默丢数据
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

对 SoT-backed 联赛，`apply-reanalysis` 会把选中的 replay 预测同时写回 prediction archive 与对应 `<league>/teams_2025-26.md` 的预测备注片段。

### runtime-only

以下 competition 以运行时归档与滚动记忆为主：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

## 结果统计口径

- `accuracy` 主统计中的 `overall` / `by_league` 代表正式记录口径
- `reanalysis_report` 代表当前模型 replay 口径
- 两者并存是设计行为，不会自动互相覆盖
- `apply-reanalysis` 是把选中的 replay 结果显式提升为正式记录的维护动作

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

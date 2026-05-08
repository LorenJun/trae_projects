---
name: "update-five-leagues-schedules"
description: "用 500 轮次接口更新五大联赛 `teams_2025-26.md` 赛程段落，并与整改后的 SoT、存储边界和主预测链保持一致。需要批量刷新赛程/比分（并避免未完赛 0-0 误导）时调用。"
---

# Update Five Leagues Schedules

本 Skill 用于把“五大联赛赛程更新”封装为一套可重复执行的流程，目标是统一刷新各联赛 `teams_2025-26.md` 中的“赛程信息”段落。

整改后口径下，它属于 SoT 维护能力，服务于：

- `europe_leagues/<league>/teams_2025-26.md`
- `europe_leagues/storage/teams_md.py`
- `prediction_system.py` / `app/cli.py` / `bulk_fetch_and_update.py` 的下游读取链

## 更新原则

- 数据源：`liansai.500.com` 轮次接口（稳定、可按轮次抓取）
- 更新范围：从各联赛文件中当前已列出的“首个轮次”开始，一直更新到收官轮
- 修改范围：仅修改 `teams_2025-26.md` 的 `## 赛程信息` 段落，其它章节不动
- 防误导规则：只有已结束比赛（`status==5`）才写真实比分，未结束/未开赛一律写 `比分 = '-'`

## 适用场景

- 赛程或比分需要批量刷新、对齐最新官方赛程时
- 生成预测报告前希望先确保赛程段落正确
- 你发现某个联赛赛程不对，需要一键重刷并修正

## 与整改后架构的关系

- 数据归属：对五大联赛而言，`teams_2025-26.md` 是联赛赛程/赛果/预测备注 SoT；欧战/杯赛不属于本 Skill 的写回目标
- 下游消费方：CLI、Harness、批量回填、准确率统计
- 正确边界：这个 Skill 只维护赛程 SoT，不负责预测推理、赔率快照或 archive 迁移
- 风险控制：不要把更新结果写到 `analysis/predictions/*.md` 或其他旧模板目录

## 一键执行

当前仓库内尚未落地 `update_schedules_from_500.py`（历史文档残留的命令）。

在补齐该脚本前：
- 请优先使用现有方式维护 `teams_2025-26.md` 的赛程比分列（已完赛填 `x-y`，未完赛填 `-`）
- 或新增/接入可用的外部赛程数据源后再实现一键更新脚本
- 如需验证更新结果，优先通过 `prediction_system.py accuracy --refresh --json`、`pending-results --json` 或正式预测命令读取 SoT，而不是人工依赖旧分析模板

## 输出文件

脚本会更新以下 5 个文件的赛程段落（五大联赛）：

- `premier_league/teams_2025-26.md`
- `la_liga/teams_2025-26.md`
- `bundesliga/teams_2025-26.md`
- `serie_a/teams_2025-26.md`
- `ligue_1/teams_2025-26.md`

## 与主流程的衔接

- 赛程更新后，`collect-data` / `predict-schedule` / `save-result` / `accuracy --refresh` 会继续以这些文件为准
- 这类更新不会直接改 `app/cli.py`、`domain/*`、`prediction_archive.json` 或 `MEMORY.md`
- 若用户真正想做的是“赛后批量回填”，应优先考虑 `bulk_fetch_and_update.py`，而不是把它误判成纯赛程维护

## 常见问题

- 如果某个联赛更新失败：通常是 stageId 推导失败或 500 端数据临时异常；可重跑一次，或先检查该联赛是否已有历史 odds 推导 sid/stageId 的数据。
- 如果你希望补全“更早轮次”：当前策略是“从文件已有轮次开始到赛季末”，不补历史轮次；如需全赛季补齐，需要在脚本里扩展起始轮次。

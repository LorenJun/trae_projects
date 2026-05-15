---
name: "five-leagues-season-review-rag"
description: "Builds a unified five-league season review markdown and syncs replay tags into RAG. Invoke when user asks to aggregate finished rounds, maintain season review docs, or compare predictions vs results."
---

# Five Leagues Season Review RAG

本 Skill 用于把“五大联赛已完赛数据梳理、联赛拆分复盘、错因标签回写 RAG、整赛季统一 Markdown 汇总”固化成一套标准流程。

它适用于当前 `Europe Leagues` 项目，目标是把每轮联赛数据统一沉淀到一个总文档中，并把复盘标签同步进 RAG 记忆库，后续所有赛后分析都优先基于这一套统一数据源。

## 何时使用

在以下场景应优先调用本 Skill：

- 用户要求“统计最近几轮/整个赛季的五大联赛已完赛数据”
- 用户要求“把每轮比赛集中到一个 md 文档中统一复盘”
- 用户要求“把复盘错因标签写回 RAG 记忆库”
- 用户要求“按联赛拆分整理赛季复盘”
- 用户要求“基于统一数据源做预测 vs 实际赛果分析”

如果用户只是想更新某一场比赛结果，优先使用 `sync-pending-results-review`，不要误用本 Skill。

## 统一数据源

本 Skill 必须遵循以下数据源优先级：

1. 五大联赛 `teams_2025-26.md`
2. `result_manager.py` 解析出的正式已完赛结果
3. `prediction_archive.json` 与 `prediction_memory_odds_samples.json`
4. `.okooo-scraper/runtime/rag_cases.json`

统一输出文件：

- 单次批量复盘：
  - `/Users/bytedance/trae_projects/europe_leagues/runtime/recent_five_leagues_review_<date>.md`
- 赛季总汇总文档：
  - `/Users/bytedance/trae_projects/europe_leagues/runtime/season_reviews/2025-26_five_leagues_master_review.md`
- RAG 记忆库：
  - `/Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/runtime/rag_cases.json`
  - `/Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/runtime/rag_index.json`
  - `/Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/runtime/rag_registry.json`

## 正式执行链路

### 1. 优先使用统一命令

完成整条链路时，优先执行：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py build-season-master-review \
  --season 2025-26 \
  --recent-days 7 \
  --days-back 30 \
  --limit 50 \
  --rag-limit 300 \
  --json
```

该命令会自动串联：

- `sync-pending-results-review`
- `rag-rebuild`
- `scripts/build_recent_five_leagues_review.py`
- `scripts/build_season_master_review.py`

并默认产出：

- 三层校准规则复盘
- 未命中原因分解
- 自动整改建议清单

如果只是分步排查，再按下面的拆分步骤执行。

### 2. 先同步已完赛结果

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py sync-pending-results-review \
  --days-back 30 \
  --limit 50 \
  --review-sample-limit 8 \
  --json
```

要求：

- 确保待回填结果先进入 `teams_2025-26.md`
- 确保 `MEMORY.md`、准确率和爆冷案例库已同步

### 3. 重建 RAG

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py rag-rebuild --limit 300 --json
```

要求：

- `rag_cases.json` 中必须包含已完赛案例
- 单场错因标签与联赛级复盘标签必须写回 RAG 文档

### 4. 生成最近窗口复盘

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 scripts/build_recent_five_leagues_review.py
```

要求：

- 产出最近窗口批量复盘文档
- 按联赛拆分统计命中率、高频错因、联赛级标签
- 必须生成“三层校准规则复盘”
- 必须生成“未命中原因分解”
- 必须生成“自动整改建议”章节，明确下一步优先优化方向

### 5. 更新赛季总文档

将本轮复盘的核心内容追加或合并进：

- `/Users/bytedance/trae_projects/europe_leagues/runtime/season_reviews/2025-26_five_leagues_master_review.md`

更新原则：

- 一轮一节，不覆盖历史轮次
- 每个轮次下按联赛拆分
- 必须记录：
  - 本轮已完赛场次
  - 胜平负/比分/大小球命中率
  - 高频错因标签
  - 联赛级标签
  - 校准规则复盘
  - 校准未命中原因
  - 自动整改建议
  - 代表性误判比赛

## 推荐文档结构

赛季总文档建议保持以下结构：

```md
# 2025-26 五大联赛赛季统一复盘数据源

## 赛季总览

## 最近窗口基线

## 最新批次

### 第 X 轮 / 第 X 批次（YYYY-MM-DD 至 YYYY-MM-DD）

#### 英超

#### 西甲

#### 意甲

#### 德甲

#### 法甲

## 跨联赛整理结论
```

最近窗口复盘文档默认建议保持以下结构：

```md
# 五大联赛近7天已完赛复盘

## 总体快照

## 全局 Top 问题

## 联赛 Top 问题一览

## 三层校准观察

## 统一整改优先级
```

其中 `Top 问题` 必须按固定四组展示，便于跨联赛/跨窗口对比：

- `覆盖`：无预测已完赛、归档覆盖不足
- `方向`：主胜/平局/客胜方向偏差与冷门漏判
- `比分`：比分未命中、比分模板偏置
- `盘口`：亚盘/欧赔/大小球/实力差等市场与数据缺口，以及三层规则未命中原因

最近窗口复盘文档和赛季总文档中，默认都应包含以下附加章节或字段（可精简展示，但不可缺失口径）：

- `三层校准规则复盘`
- `未命中原因分解`
- `自动整改建议`

其中“自动整改建议”必须根据最近窗口统计自动生成，不能手写拍脑袋总结。

## 错因标签规范

### 单场标签

- `主胜高估`
- `平局低估`
- `客胜冷门漏判`
- `比分未命中`
- `比分模板偏主胜`
- `比分模板偏平局`
- `比分模板偏客胜`
- `进球弹性低估`
- `进球弹性高估`
- `大小球盘口线缺失`
- `大小球方向错误`
- `高信心误判`

### 联赛标签

- `英超-主胜偏置`
- `英超-平局防守不足`
- `英超-客胜冷门敏感度不足`
- `西甲-大小球方向偏差`
- `法甲-预测归档覆盖不足`

要求：

- 联赛标签必须由窗口样本自动聚合，不手写臆断
- 单场标签优先从预测方向、比分模板、大小球和信心偏差四个维度生成

## 整改建议规范

“自动整改建议”必须基于真实统计结果自动生成，至少覆盖以下两类：

- 数据整改建议：
  - `优先补齐 strength_diff 留存`
  - `优先补齐亚盘快照留存`
  - `优先补齐欧赔终盘留存`
- 规则整改建议：
  - `浅盘/均势盘覆盖面偏窄，考虑扩展到半球至半一`
  - `预测为平局场景尚未建模，需补平局专用规则`
  - `均势阈值或市场疑虑阈值偏严，需评估放宽`
  - `某条规则近期失效或效果分化，需调整校准幅度`

要求：

- 优先根据“未命中原因分解”生成数据整改建议
- 若某条规则近期 `失效` 或 `效果分化`，必须给出对应规则整改建议
- 建议内容要指向可执行动作，避免空泛结论
- 默认输出 3 到 5 条最重要建议，按优先级排序

## 输出要求

完成后至少向用户报告：

- 最近窗口已完赛场次数
- 哪些数据已写入 RAG
- 赛季总文档是否已更新
- 每个联赛的联赛级标签
- 每个联赛的 Top 问题（按 `覆盖/方向/比分/盘口` 四组固定分组）
- 当前三层校准规则哪些有效、哪些失效
- 当前未命中原因主要是缺数据还是规则未触发
- 自动整改建议清单的前几项重点
- 若某联赛预测归档覆盖不足，要明确指出

## 禁止事项

- 不要绕过 `teams_2025-26.md` 直接手工编造赛果
- 不要只更新 RAG 而不更新赛季总文档
- 不要在没有说明统计窗口的情况下输出赛季结论
- 不要把“最近几天复盘”和“全赛季复盘”混成同一个统计口径

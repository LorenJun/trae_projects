---
name: "sync-pending-results-review"
description: "赛果回填与复盘总结技能，按 `prediction_system.py` 发现入口并下钻到 `europe_leagues/app/cli.py` 执行，统一 pending-results、auto-sync-results、save-result、MEMORY、SoT、archive 与 review-learning 的结果闭环。Invoke when user asks to update pending results, backfill scores, or produce post-match review summaries."
---

# Sync Pending Results And Review

本 Skill 用于把“未完赛比赛更新 + 赛后复盘总结”封装成一套正式结果闭环流程。

## 入口规则

始终按以下两层理解入口：

- 发现 / 兼容入口：`europe_leagues/prediction_system.py`
- 真实命令实现：`europe_leagues/app/cli.py`

## 它负责什么

- 查找待回填比赛
- 自动或手工补录真实赛果
- 协调 `result_sync_registry.json`、`prediction_archive.json`、`MEMORY.md` 与联赛 SoT
- 刷新准确率、review-learning 与相关衍生产物
- 生成批次复盘总结或单场复盘备注

## 正式主链

1. `prediction_system.py pending-results --days-back <N> --json`
2. `prediction_system.py auto-sync-results --limit <N> --json`
3. 自动未命中时使用 `prediction_system.py save-result`
4. 需要一体化回填 + 复盘时使用 `prediction_system.py sync-pending-results-review`
5. 需要显式重建时再执行 `prediction_system.py accuracy --refresh --json`

## 更新原则

- 优先自动同步，减少人工写错比分
- 自动同步未命中时，先核赛果，再手工补录
- SoT-backed 比赛优先使用 canonical `teams_match_id`
- 五大联赛 / 世界杯以 markdown SoT 为准
- 欧战 / 杯赛以 `MEMORY.md` 与 runtime archive 为准
- 不允许把当前比赛赛果扩散写入历史相似样本
- 正常结果闭环命中后，系统会继续同步多项衍生产物；`accuracy --refresh` 不是唯一常规路径

## 与当前预测链的衔接

- 上游正式 `predict-match` 已可稳定拿到真实：
  - 欧赔 `multi_company_consensus`
  - 亚值
  - 大小球
  - 凯利
- 结果闭环阶段不应回退或覆盖这些真实盘口快照，只应补齐赛果与复盘产物

## 推荐命令

### 一键回填 + 复盘

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py sync-pending-results-review \
  --days-back 30 \
  --limit 20 \
  --review-sample-limit 8 \
  --json
```

### 单独查看待回填比赛

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py pending-results --days-back 30 --json
```

### 单独尝试自动同步

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py auto-sync-results --limit 20 --json
```

### 手工补录

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py save-result --match-id '<主队> vs <客队>' --home-score <x> --away-score <y> --json
```

## 结果闭环后应看到什么

- SoT-backed 比赛更新 `teams_2025-26.md` / `teams_2026.md`
- runtime-only 比赛更新 `MEMORY.md` 与 `.okooo-scraper/runtime/*.json`
- `prediction_archive.json` 实际赛果字段补齐
- 准确率与 review-learning 相关输出刷新
- 必要时滚动记忆从“未完赛”迁移到“已完赛”

## 常见问题

- `auto-sync-results` 返回 0，不代表比赛一定没结束；还要核外部赛果
- `save-result` 找不到比赛，常见原因是外部 `MatchID` 与项目内部 canonical `match_id` 不一致
- 如果联赛 md 已有比分但备注仍不对，说明历史写回不完整，需要按正式闭环补齐

## 最终规则

如果 skill 说明与 `europe_leagues/README.md`、`README_使用指南.md`、`runtime/result_sync.py`、`result_manager.py` 的当前实现冲突，以当前代码实现为准。

---
name: "sync-pending-results-review"
description: "Syncs unfinished match results, updates MEMORY.md and league SoT, then generates review summaries. Invoke when user asks to update pending results, backfill scores, or produce post-match review summaries."
---

# Sync Pending Results And Review

本 Skill 用于把“未完赛比赛更新 + 赛后复盘总结”封装成一套可重复执行的标准流程，适用于当前 `Football Prediction Agent` / `Europe Leagues` 项目。

它的目标是统一完成以下事项：

- 查找仍处于待回填状态的预测样本
- 自动或手工补录真实赛果
- 同步更新 `MEMORY.md`
- 同步更新五大联赛 `teams_2025-26.md`
- 刷新准确率统计
- 生成批次复盘总结或单场复盘备注

## 何时使用

在以下场景应优先调用本 Skill：

- 用户要求“更新未完赛比赛结果”
- 用户要求“把最新赛果同步到预测记忆中”
- 用户要求“刷新 pending-results / save-result / accuracy”
- 用户要求“生成最近几场已完赛比赛的复盘总结”
- 用户要求“把赛果回填和复盘做成一套统一流程”

如果用户只是要预测单场比赛，优先使用预测主技能，不要误用本 Skill。

## 正式主链

本 Skill 必须遵循当前项目的正式结果回填链路：

1. `prediction_system.py pending-results --days-back <N> --json`
2. `prediction_system.py auto-sync-results --limit <N> --json`
3. 若自动同步未命中：
   - 核对外部赛果来源
   - 使用 `prediction_system.py save-result` 手工补录
4. `prediction_system.py accuracy --refresh --json`
5. 复查 `MEMORY.md` 与联赛 `teams_2025-26.md`
6. 如用户要求，生成或追加复盘总结

## 更新原则

- 优先自动同步，避免人工写错比分
- 自动同步没命中时，先核赛果，再手工补录
- `save-result` 优先用项目内部比赛标识；若外部 `MatchID` 无法直接命中 SoT，则改用 `主队 vs 客队`
- 五大联赛 SoT 以 `europe_leagues/<league>/teams_2025-26.md` 为准
- 欧战/杯赛类 runtime-only 结果以 `MEMORY.md` 和 runtime archive 为主
- 更新后必须刷新 `accuracy`
- 若发现文档比分与外部赛果不一致，需要纠正错误落盘，而不是只改汇总结论

## 标准执行步骤

### 一键执行命令

推荐直接使用下面这个固定命令：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py sync-pending-results-review \
  --days-back 30 \
  --limit 20 \
  --review-sample-limit 8 \
  --json
```

这个命令会自动串联：

- `pending-results`
- `auto-sync-results`
- `accuracy --refresh`
- `MEMORY.md` 复盘总结更新

如果你只想预览，不想写回 `MEMORY.md`，加上：

```bash
--no-write-review
```

### 1. 检查待回填比赛

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py pending-results --days-back 30 --json
```

检查输出中的：

- `match_id`
- `league`
- `home_team`
- `away_team`
- `match_date`
- `match_time`

### 2. 尝试自动同步

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py auto-sync-results --limit 20 --json
```

若 `updated_count > 0`，说明已有比赛自动完成回填。

若 `updated_count == 0`，不要假设比赛未结束，要继续核查外部赛果。

### 3. 外部核赛果

优先使用澳客移动端历史页：

- `https://m.okooo.com/match/history.php?MatchID=<id>`

核对：

- 最终比分
- 半场比分
- 主客队顺序
- 是否存在日期偏移或赛程行错位

### 4. 手工补录赛果

优先尝试：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py save-result --match-id '<主队> vs <客队>' --home-score <x> --away-score <y> --json
```

说明：

- 当外部 `MatchID` 不是项目内部 `match_id` 时，直接用球队名更稳
- 若同名比赛可能跨日期冲突，需先核对 `teams_2025-26.md` 中对应联赛行
- 若联赛文件里已存在比分但备注仍是 `进行中`，需要人工修正备注为赛后格式

### 5. 刷新准确率

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py accuracy --refresh --json
```

至少记录：

- `total_predictions`
- `correct_predictions`
- `win_accuracy`
- `score_accuracy`
- `ou_accuracy`

### 6. 更新滚动记忆

核查 `MEMORY.md` 的 `<!-- prediction-memory:start -->` 区块：

- 未完赛是否迁移到已完赛
- `■ 赛果:` 是否正确
- `更新时间` 是否更新
- 顶部滚动准确率是否与当前样本一致

### 7. 生成复盘总结

若用户要求复盘，需要按当前 `MEMORY.md` 中的标准模板生成：

- 总体复盘
- 单场复盘
- 优化方向

应优先复盘：

- 胜平负是否命中
- 比分是否命中
- 大小球是否命中
- 盘口与赛果是否一致
- 当前偏差是方向判断问题，还是比分/进球弹性问题

## 推荐复盘格式

### 单场复盘

```md
- `{home_team} {actual_score} {away_team}`
  赛前预测为 `{prediction}`，Top 比分为 `{top_scores}`，大小球倾向为 `{ou_pick}`；最终赛果为 `{actual_result}`。复盘结论：{review_conclusion}。
```

### 批次复盘

```md
### 滚动记忆复盘总结（最近 {sample_count} 场已完赛）

> 基于滚动记忆区块中的 {sample_count} 场已完赛样本生成。当前结论：{overall_summary}

#### 总体复盘

- 已完赛 `{sample_count}` 场，胜平负命中 `{win_hits}/{sample_count}`
- 比分命中 `{score_hits}/{sample_count}`
- 大小球命中 `{ou_hits}/{ou_sample_count}`
- 样本分布：{sample_distribution_summary}

#### 单场复盘

- `{match_1}`
  {match_1_review}

#### 优化方向

- 胜平负：{win_action}
- 比分：{score_action}
- 大小球：{ou_action}
- 风险提示：{risk_action}
```

## 常见问题

- `auto-sync-results` 返回 `due_count=0`：
  - 说明自动队列未命中，不代表没有已完赛比赛，需要继续核外部赛果
- `save-result` 提示 `找不到比赛`：
  - 往往是外部 `MatchID` 与项目内部 `match_id` 不一致，改用 `主队 vs 客队`
- 联赛 `md` 已有比分，但备注还是 `进行中`：
  - 说明历史写回不完整，需要手工修成赛后格式
- `MEMORY.md` 和联赛 SoT 不一致：
  - 先以真实赛果为准修正文档，再刷新 `accuracy`
- 准确率总表没变化：
  - 说明该样本可能还没完整进入统一统计来源，需要继续检查 SoT / archive / memory 三者是否一致

## 必查文件

- `/Users/bytedance/trae_projects/MEMORY.md`
- `/Users/bytedance/trae_projects/europe_leagues/<league>/teams_2025-26.md`
- `/Users/bytedance/trae_projects/europe_leagues/result_manager.py`
- `/Users/bytedance/trae_projects/europe_leagues/domain/persistence.py`
- `/Users/bytedance/trae_projects/europe_leagues/domain/writeback.py`

## 最终输出要求

完成后给用户的结果至少应包括：

- 本次更新了哪些比赛
- 哪些比赛是自动同步，哪些是手工补录
- `MEMORY.md` 是否已切到已完赛
- 联赛 `teams_2025-26.md` 是否已同步
- 刷新后的准确率结果
- 若有残留未回填样本，要明确列出

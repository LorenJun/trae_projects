---
name: "okooo-match-finder"
description: "从澳客网移动端定位足球 MatchID，并衔接赛程抓取、实时快照、大小球补抓与下游预测入口。Invoke when user needs match_id, schedule lookup, or okooo realtime snapshot URLs."
---

# 澳客比赛定位与快照技能

本 Skill 用于把“比赛信息”稳定转成 `MatchID`、赛程记录与可抓取赔率入口，是实时预测链路的上游。

## 主目标

- 从 `热门赛事` 或联赛赛程页定位目标比赛
- 解决球队简称、日期歧义、时间歧义
- 获取 `match_id`
- 为后续实时快照抓取、批量采集与预测流程提供稳定输入

## 当前推荐流程

1. 先使用 `okooo_fetch_daily_schedule.py` 抓当天赛程
2. 从赛程 JSON 中确认 `home_team`、`away_team`、`kickoff_time`、`match_id`
3. 若要进入正式预测链路，优先调用 `prediction_system.py collect-data` 或 `predict-match`
4. 若需要显式快照，再调用 `okooo_save_snapshot.py --match-id ...`
5. 若赛程匹配失败，再退回 remen 模糊匹配

## 重要事实

- 大小球真实入口优先级：
  1. `handicap.php` 页面内 `大小球` tab
  2. `overunder.php` / `daxiao.php`
  3. 桌面 `/ou/` 页面（仅 fallback）
- 球队可能用简称展示，例如：`布伦特`、`毕尔巴鄂`
- 因此必须结合：
  - `--date`
  - `--time`
  - `okooo_team_aliases.json`
- `okooo_fetch_daily_schedule.py` 不只服务赛前，它还能识别 `已结束` 比赛并输出比分，供 `bulk_fetch_and_update.py` 使用
- `DataCollector` 会优先复用当天赛程里的 `match_id`，并自动挂接本地已有快照到 `odds_data`

## 推荐命令

### 1. 抓某天联赛赛程

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 2. 先走正式采集入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-04-28 --json
```

### 3. 用 MatchID 直接抓快照

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py \
  --driver local-chrome \
  --league 英超 \
  --team1 曼联 \
  --team2 布伦特福德 \
  --date 2026-04-28 \
  --time 03:00 \
  --match-id 1296070 \
  --overwrite
```

## 输出要点

标准输出文件应包含：

- `match_id`
- `schedule`
- `history_url`
- `欧赔`
- `亚值`
- `大小球`
- `凯利`

其中 `大小球._flow` 理想值应为：

- `asian_inner_tab`

下游常见消费方包括：

- `prediction_system.py predict-match`
- `prediction_system.py predict-schedule`
- `prediction_system.py harness-run --pipeline match_prediction`
- `bulk_fetch_and_update.py`

## 失败排查顺序

1. 是否拿到正确 `match_id`
2. 是否日期不对
3. 是否球队简称未收录到 `okooo_team_aliases.json`
4. 是否忘记传 `match_time`
5. 是否被页面风控拦截
6. 是否已经存在同 `match_id` 快照但内容过旧，需要 `--overwrite` 或重新刷新

## 与预测系统的衔接

`EnhancedPredictor.predict_match()` 会自动尝试：

- 刷新实时快照
- 缺失大小球时再次补抓
- 将 `大小球.final.line/over/under` 注入 `over_under.market`
- 在缺少上下文时补 EWMA form，并可注入 `team_context`

因此，本 Skill 的目标不是只给一个 `MatchID`，而是尽量把后续预测、批量处理、赛果回填所需的上游数据入口全部打通。

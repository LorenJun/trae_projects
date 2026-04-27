---
name: "okooo-match-finder"
description: "从澳客网移动端定位足球 MatchID，并衔接欧赔、亚值、亚值页内大小球、凯利抓取。Invoke when user needs match_id, schedule lookup, or okooo realtime snapshot URLs."
---

# 澳客比赛定位与快照技能

本 Skill 用于把“比赛信息”稳定转成 `MatchID` 与可抓取的赔率入口，是实时预测链路的上游。

## 主目标

- 从 `热门赛事` 或联赛赛程页定位目标比赛
- 解决球队简称、日期歧义、时间歧义
- 获取 `match_id`
- 为后续实时快照抓取提供稳定输入

## 当前推荐流程

1. 先使用 `okooo_fetch_daily_schedule.py` 抓当天赛程
2. 从赛程 JSON 中取目标比赛的 `match_id`
3. 再调用 `okooo_save_snapshot.py --match-id ...`
4. 若赛程匹配失败，再退回 remen 模糊匹配

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

## 推荐命令

### 1. 抓某天联赛赛程

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 2. 用 MatchID 直接抓快照

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
- `欧赔`
- `亚值`
- `大小球`
- `凯利`

其中 `大小球._flow` 理想值应为：

- `asian_inner_tab`

## 失败排查顺序

1. 是否拿到正确 `match_id`
2. 是否日期不对
3. 是否球队简称未收录到 `okooo_team_aliases.json`
4. 是否忘记传 `match_time`
5. 是否被页面风控拦截

## 与预测系统的衔接

`EnhancedPredictor.predict_match()` 会自动尝试：

- 刷新实时快照
- 缺失大小球时再次补抓
- 将 `大小球.final.line/over/under` 注入 `over_under.market`

因此，本 Skill 的目标不是只给一个 MatchID，而是尽量把后续预测所需的实时数据入口全部打通。

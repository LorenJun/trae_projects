---
name: "okooo-match-finder"
description: "澳客 MatchID 定位与预测入口衔接技能，按 `prediction_system.py` 发现入口并在需要预测时下钻到 `europe_leagues/app/cli.py` 执行，衔接赛程抓取、实时快照、大小球补抓与正式预测链路。Invoke when user needs match_id, schedule lookup, or okooo realtime snapshot URLs."
---

# 澳客比赛定位与快照技能

本 Skill 用于把比赛信息稳定转成 `match_id`、赛程记录与可抓取赔率入口，是正式预测链的上游技能。

## 入口规则

始终按以下两层理解入口：

- 发现 / 兼容入口：`europe_leagues/prediction_system.py`
- 真实命令实现：`europe_leagues/app/cli.py`

当目标是“找 `match_id` 后进入正式预测链”时，应把执行口径落到 CLI，而不是绕过命令层直接拼底层模块。

## 主目标

- 从热门赛事或联赛赛程页定位目标比赛
- 解决球队简称、日期歧义、时间歧义
- 获取 `match_id`
- 为实时快照抓取、批量采集与正式预测流程提供稳定输入

## 当前推荐流程

1. 先使用 `okooo_fetch_daily_schedule.py` 抓当天赛程
2. 从赛程 JSON 确认 `home_team`、`away_team`、`kickoff_time`、`match_id`
3. 若要进入正式预测链，优先调用 `prediction_system.py collect-data` 或 `predict-match`
4. 若需要显式快照，再调用 `okooo_save_snapshot.py --match-id ...`
5. 若赛程匹配失败，再退回 remen 模糊匹配

如果出现“别的设备能打开，本机抓不到 `odds.php`”：

- 不要先默认判断为网络不通
- 先确认是否走了公共移动访问策略
- 关键放行条件是：`移动端 UA + Referer`

## 关键事实

- 大小球真实入口优先级：
  1. `handicap.php` 页面内 `大小球` tab
  2. `overunder.php` / `daxiao.php`
  3. 其他旧入口作为 fallback
- 欧赔真实入口：`https://m.okooo.com/match/odds.php?MatchID=<MatchID>`
- 当前正式快照 driver：`local-chrome`
- 当前正式访问口径：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 当前公共设备池：`100` 组随机 `iPhone Safari` profile
- 欧赔解析优先级：多公司明细 -> `multi_company_consensus` -> `99家平均` fallback
- 球队可能用简称展示，例如 `布伦特`、`毕尔巴鄂`
- 因此必须结合：
  - `--date`
  - `--time`
  - `okooo_team_aliases.json`
- `okooo_fetch_daily_schedule.py` 还能识别 `已结束` 比赛并输出比分，供赛后批量回填流程使用

## 与正式主链的衔接

本 Skill 不只是返回一个 `match_id`，还要为后续这些正式命令铺路：

- `collect-data`
- `predict-match`
- `predict-schedule`
- `harness-run`
- `auto-sync-results`
- `bulk_fetch_and_update.py`

## 当前模块边界

- 兼容采集脚本：`okooo_fetch_daily_schedule.py`、`okooo_save_snapshot.py`
- 采集相关代码：`collectors/*`
- 下游 CLI 主链：`europe_leagues/app/cli.py`
- 下游预测外壳：`domain/predictor.py`
- 下游主编排：`enhanced_prediction_workflow.py`

## 推荐命令

### 抓某天联赛赛程

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 先走正式采集入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-04-28 --json
```

### 用 MatchID 直接抓快照

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

## 失败排查顺序

1. 是否拿到正确 `match_id`
2. 是否日期不对
3. 是否球队简称未收录到 `okooo_team_aliases.json`
4. 是否忘记传 `match_time`
5. 是否被页面风控拦截
6. 是否已有旧快照需要 `--overwrite`

## 边界说明

- 本 Skill 是正式预测链的上游，不负责最终结果闭环
- 如果用户要的是赛果回填或复盘，应切换到 `sync-pending-results-review` 相关技能 / 命令
- 如果 skill 说明与 `europe_leagues/README.md`、`README_使用指南.md`、`app/cli.py` 冲突，以当前代码实现为准

---
name: "okooo-match-finder"
description: "澳客 MatchID 定位与预测入口衔接技能，按 `prediction_system.py` 发现入口并在需要预测时下钻到 `app/cli.py` 执行，衔接赛程抓取、实时快照、大小球补抓与正式预测链路。Invoke when user needs match_id, schedule lookup, or okooo realtime snapshot URLs."
---

# 澳客比赛定位与快照技能

本 Skill 用于把“比赛信息”稳定转成 `MatchID`、赛程记录与可抓取赔率入口，是实时预测链路的上游。整改后它同时要为 `collectors/`、`Harness` 和 CLI 主链提供统一前置输入。

## 主目标

- 从 `热门赛事` 或联赛赛程页定位目标比赛
- 解决球队简称、日期歧义、时间歧义
- 获取 `match_id`
- 为后续实时快照抓取、批量采集与预测流程提供稳定输入

## 当前推荐流程

1. 先使用 `okooo_fetch_daily_schedule.py` 抓当天赛程
2. 从赛程 JSON 中确认 `home_team`、`away_team`、`kickoff_time`、`match_id`
3. 若要进入正式预测链路，优先调用 `prediction_system.py collect-data` 或 `predict-match`，由兼容入口转发到 `app/cli.py`
4. 若需要显式快照，再调用 `okooo_save_snapshot.py --match-id ...`
5. 若赛程匹配失败，再退回 remen 模糊匹配

## Hermes 识别规则

- Hermes 或其他接入方可以从 `prediction_system.py` 发现项目入口
- 但必须继续下钻到 `app/cli.py` 识别并执行真实命令
- 不要把 `prediction_system.py` 当作业务主逻辑实现层
- 当本 Skill 的目标是“找 `match_id` 后进入正式预测链路”时，应把执行识别落到 `app/cli.py` 的 `collect-data` / `predict-match` 路由
- 当本 Skill 的目标只是“定位比赛并生成快照入口”时，可以停留在 `okooo_fetch_daily_schedule.py` / `okooo_save_snapshot.py`
- 若后续进入阶段化预测，应识别为 `prediction_system.py harness-run --pipeline match_prediction`，不要绕开 CLI 直接调用底层模块

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

## 当前模块边界

- 兼容脚本入口：`okooo_fetch_daily_schedule.py`、`okooo_save_snapshot.py`
- 整改后采集包：`collectors/okooo.py`、`collectors/aliasing.py`、`collectors/odds_snapshots.py`
- 下游 CLI 主链：`app/cli.py`
- 下游预测外壳：`domain/predictor.py`
- 下游主编排：`enhanced_prediction_workflow.py`

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
- `app/cli.py` 的 `collect-data` / `predict-match` 路由
- `bulk_fetch_and_update.py`

## 失败排查顺序

1. 是否拿到正确 `match_id`
2. 是否日期不对
3. 是否球队简称未收录到 `okooo_team_aliases.json`
4. 是否忘记传 `match_time`
5. 是否被页面风控拦截
6. 是否已经存在同 `match_id` 快照但内容过旧，需要 `--overwrite` 或重新刷新

## 与预测系统的衔接

`DomainPredictor` / `EnhancedPredictor` 预测链会自动尝试：

- 刷新实时快照
- 缺失大小球时再次补抓
- 将 `大小球.final.line/over/under` 注入 `over_under.market`
- 在缺少上下文时补 EWMA form，并可注入 `team_context`

因此，本 Skill 的目标不是只给一个 `MatchID`，而是尽量把后续预测、批量处理、赛果回填所需的上游数据入口全部打通。

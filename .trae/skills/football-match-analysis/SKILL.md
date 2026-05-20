---
name: "football-match-analysis"
description: "足球比赛预测主技能，按 `prediction_system.py` 发现入口并下钻到 `europe_leagues/app/cli.py` 执行，衔接 collect-data、实时快照、DomainPredictor、EnhancedPredictor、RAG、persistence 与 result sync 正式链路。Invoke when user needs match prediction, betting analysis, or match odds evaluation."
---

# Football Match Analysis

本 Skill 是当前仓库里与 `europe_leagues/` 正式应用对应的预测主技能。

## 入口规则

始终按以下两层理解入口：

- 发现 / 兼容入口：`europe_leagues/prediction_system.py`
- 真实命令实现：`europe_leagues/app/cli.py`

不要把 `prediction_system.py` 误判成业务主逻辑实现层。

## 正式主流程

1. 确认 `league`、主客队、比赛日期，必要时补 `match_time`
2. 优先通过 `prediction_system.py collect-data` 或 `okooo_fetch_daily_schedule.py` 获取 `match_id`
3. 调用 `prediction_system.py predict-match` 或 `predict-schedule`
4. 命令会经 `app/cli.py` 进入 `DomainPredictor` / `EnhancedPredictor`
5. 预测链会联动：
   - 实时快照刷新
   - 缺失大小球补抓
   - EWMA 近况补齐
   - RAG 相似比赛 / 盘口样本 / 爆冷案例检索
   - `domain/persistence.py` 负责 side effects
   - `runtime/result_sync.py` / `result_manager.py` 负责赛后闭环

## 当前澳客访问口径

- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动 profile 池：`europe_leagues/okooo_mobile_access.py`
- 当前设备池规模：`100` 组随机 `iPhone Safari` profile
- 欧赔解析优先 `multi_company_consensus`；`99家平均` 仅作为 fallback

## 当前正式命令面

本 Skill 相关的高频正式命令包括：

- `collect-data`
- `predict-match`
- `predict-match-lite`
- `predict-schedule`
- `pending-results`
- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `accuracy`
- `sync-pending-results-review`
- `harness-run`

## SoT / runtime 边界

### SoT-backed

以下 competition 以 markdown SoT 为主：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

### runtime-only

以下 competition 以 `MEMORY.md` 与 runtime archive 为主：

- `europa_league`
- `champions_league`
- `conference_league`
- 其他杯赛 / 欧战扩展比赛

## 关键规则

- 大小球不要默认按 `2.5` 解读；真实盘口缺失时必须明确落为缺失态
- 若已知 `match_id`，优先直连抓取，避免重复赛程模糊匹配
- 若球队在赛程里显示简称，需结合 `okooo_team_aliases.json`
- 新预测会联动 SoT 或 runtime-only 写回、archive、MEMORY、RAG 与 result sync registry
- 赛后回填应优先走 `save-result` / `auto-sync-results` / `result-sync-daemon` / `sync-pending-results-review`
- 默认使用 CLI-first，不要把底层 Python import 当成标准用户流程

## 推荐命令

### 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match \
  --league premier_league \
  --home-team 曼联 \
  --away-team 布伦特福德 \
  --date 2026-04-28 \
  --time 03:00 \
  --match-id 1296070 \
  --json
```

### 批量预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-schedule \
  --league premier_league \
  --date 2026-04-28 \
  --days 1 \
  --json
```

### Harness 编排入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league premier_league \
  --date 2026-04-28 \
  --home-team 曼联 \
  --away-team 布伦特福德 \
  --time 03:00 \
  --json
```

### 结果回填

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py auto-sync-results --json
python3 prediction_system.py save-result --match-id premier_league_20260428_曼联_布伦特福德 --home-score 2 --away-score 1 --json
```

## 输出最低标准

最终回答至少应覆盖：

- 胜平负方向与概率
- Top 比分
- 真实欧赔初赔 / 即赔，优先输出 `multi_company_consensus`
- 真实大小球盘口线与大/小水位
- 真实亚值盘口与水位
- 凯利初始 / 即时值
- 风险提示
- `retrieved_memory_explanation`
- `live_betting_advice`
- 必要时补充 `runtime_profile`

## 已验证样例

当前链路已用以下样例做过正式 `predict-match` 验证：

- `la_liga / 埃尔切 vs 赫塔费 / MatchID=1302914`
- 可稳定拿到真实：
  - 欧赔 `multi_company_consensus`
  - 亚值
  - 大小球
  - 凯利

## 调试边界

允许开发时临时直接调用底层 Python 类做排查，但那不是标准用户入口。

如果 skill 说明与 `europe_leagues/README.md`、`README_使用指南.md`、`europe_leagues/app/cli.py` 冲突，以当前代码实现为准。

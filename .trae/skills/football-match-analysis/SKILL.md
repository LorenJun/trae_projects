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
3. 调用 `prediction_system.py predict-match`（足彩 14 场用 `predict-fourteen-issue`）
4. 命令会经 `app/cli.py` 进入 `DomainPredictor` / `EnhancedPredictor`
5. 预测链会联动：
   - 实时快照刷新
   - 预测前快照注水与 `match_id` 修正
   - 缺失大小球补抓
   - EWMA 近况补齐
   - RAG 相似比赛 / 盘口样本 / 爆冷案例检索
   - `domain/persistence.py` 负责 side effects（**仅 SoT 联赛才写回**）
   - `runtime/result_sync.py` / `result_manager.py` 负责赛后闭环

其中澳客相关链路当前已经支持：

- 自动翻到目标年月
- 按当天日期分组抽取整天比赛
- 按 `日期 + 主客队 + 时间` 精确锁定目标比赛行
- 快照身份校验，避免错误 `match_id` 或旧文件串场
- 将真实 `欧赔 / 亚值 / 大小球 / 凯利` 回流到正式 `predict-match`

## 当前澳客访问口径

- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动 profile 池：`europe_leagues/okooo_mobile_access.py`
- 当前设备池规模：`500` 组 `iPhone Safari` profile，分布在多个 iPhone device pool 上
- 欧赔解析优先 `multi_company_consensus`；`99家平均` 仅作为 fallback

## 当前正式命令面

本 Skill 相关的高频正式命令包括：

- `collect-data`
- `predict-match`
- `predict-fourteen-issue`
- `pending-results`
- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `accuracy`
- `sync-pending-results-review`
- `harness-run`

## SoT 写回边界（二元）

写回边界已收敛为二元：**只有 SoT-backed 正式联赛走完整写回；其余赛事走 `archive_only`（仅归档 + 赛果同步 + 准确率，不写 MEMORY/RAG/teams md）。**

### SoT-backed（完整写回）

以下且仅以下 competition 允许写回 teams md / `MEMORY.md` 滚动记忆 / RAG / 赛果同步登记：

- `premier_league`
- `la_liga`
- `serie_a`
- `bundesliga`
- `ligue_1`
- `world_cup`

判定逻辑见 `app/cli.py` 的 `SOT_BACKED_LEAGUE_CODES` 与 `is_sot_backed_league()`。

### 其余一切赛事（archive_only，仅归档不写 MEMORY/RAG/teams md）

欧战（`europa_league` / `champions_league` / `conference_league`）、其他杯赛、友谊赛（`friendly`）以及任何非上述六个联赛的 competition：`predict-match` 走 `archive_only` 路径（`persist=True, archive_only=True`），**只归档预测 + 登记赛果同步 + 刷新准确率，不写 teams md、不写 `MEMORY.md`、不进 RAG**，结果标 `persisted.archive_only=True`、`memory_updated=False`、`archived=True`。

## 关键规则

- 大小球不要默认按 `2.5` 解读；真实盘口缺失时必须明确落为缺失态
- 若已知 `match_id`，优先直连抓取，避免重复赛程模糊匹配
- 若球队在赛程里显示简称，需结合 `okooo_team_aliases.json`
- 若赛程页当前停在错误月份，应优先依赖脚本自动翻月，而不是手工假设日期标签可直接点击
- 若 `collect-data` 已有真实快照，`predict-match` 应优先复用并注入，而不是重新走弱兜底
- 新预测只有 SoT 联赛（五大联赛 + 世界杯）才完整写回 teams md / MEMORY / RAG / result sync registry；其余赛事走 `archive_only`，仅归档 + 赛果同步，不写 MEMORY/RAG/teams md
- 赛后回填应优先走 `save-result` / `auto-sync-results` / `result-sync-daemon` / `sync-pending-results-review`
- 默认使用 CLI-first，不要把底层 Python import 当成标准用户流程

## 推荐命令

### 单场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-match \
  --league premier_league \
  --home-team 伯恩利 \
  --away-team 狼队 \
  --date 2026-05-24 \
  --time 23:00 \
  --json
```

### 足彩 14 场预测

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py predict-fourteen-issue \
  --issue 26082 \
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
- `premier_league / 伯恩利 vs 狼队 / MatchID=1296105`
- 可稳定拿到真实：
  - 欧赔 `multi_company_consensus`
  - 亚值
  - 大小球
  - 凯利
- 其中 `伯恩利 vs 狼队` 已验证：
  - 先由赛程页自动翻月到 `2026-05`
  - 从当天 10 场比赛中精确定位目标行
  - 真实盘口回流后，正式预测从偏 `客胜` 修正为偏 `平局`

## 调试边界

允许开发时临时直接调用底层 Python 类做排查，但那不是标准用户入口。

如果 skill 说明与 `europe_leagues/README.md`、`README_使用指南.md`、`europe_leagues/app/cli.py` 冲突，以当前代码实现为准。

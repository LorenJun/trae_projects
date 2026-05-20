# 澳客实时赔率与大小球抓取指南

本指南只描述当前正式采集链与预测主链如何衔接，默认采用 CLI-first 方式。

## 入口与目标

当前正式入口分两层：

- `prediction_system.py`：兼容 / 发现入口
- `app/cli.py`：真实 CLI 路由与 JSON 输出实现

本指南的目标是把：

- 赛程定位
- `match_id` 获取
- 实时快照抓取
- 大小球真实盘口定位
- 正式预测链路接入

统一成一条稳定流程。

## 当前主流程

1. 先定位比赛：联赛、主客队、日期，必要时补 `match_time`
2. 优先调用 `prediction_system.py collect-data`，或抓当天赛程获取 `match_id`
3. 必要时用 `okooo_save_snapshot.py` 显式生成实时快照 JSON
4. 再通过 `prediction_system.py predict-match` 或 `harness-run --pipeline match_prediction` 进入正式预测链
5. 预测 side effects 由 `domain/persistence.py` 统一处理；赛果闭环由 `runtime/result_sync.py` 与 `result_manager.py` 统一处理

## 关键事实

- 欧赔入口：`https://m.okooo.com/match/odds.php?MatchID=<MatchID>`
- 亚值入口：`https://m.okooo.com/match/handicap.php?MatchID=<MatchID>`
- 大小球真实位置：`handicap.php` 页面内的 `大小球` tab
- 快照目录：`.okooo-scraper/snapshots/<league>/`
- 赛程缓存目录：`.okooo-scraper/schedules/<league>/`
- 默认快照 driver：`local-chrome`
- 默认访问策略：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池：`okooo_mobile_access.py` 统一维护，当前为 `100` 组随机 `iPhone Safari` profile，统一 `viewport={"width": 1080, "height": 720}`

## 当前访问策略

`m.okooo.com` 对默认桌面请求、无 `Referer` 请求较敏感，常见失败形态是 `403/405` 或阿里云风控页。

当前仓库里的正式访问口径已经统一为：

- `local-chrome` 优先
- `iPhone Safari` 风格 UA
- `Referer: https://m.okooo.com/`
- no-cache 头
- 每次请求从 `100` 组移动 profile 池中随机取一个

如果本机浏览器能打开、脚本却访问失败，优先排查是否绕过了这套公共策略，而不是先怀疑 DNS 或系统代理。

## 推荐命令

### 1. 先抓当天赛程并拿到 MatchID

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-04-28
```

### 2. 走正式采集入口

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
  --out-dir /Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/snapshots \
  --overwrite
```

### 4. 用正式 CLI 跑最终预测

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

### 5. 需要阶段化审计时走 Harness

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

## 预测输出检查点

执行预测后，至少检查：

- `over_under.line`
- `over_under.line_source`
- `over_under.market.final`
- `realtime.okooo`
- `retrieved_memory_explanation`

理想情况下：

- `line_source` 指向真实盘口来源（例如 `snapshot_final`）
- `market.final` 中存在真实 `over / line / under`
- `market_snapshot.欧赔.company_mode` 优先为 `multi_company_consensus`
- `market_snapshot.欧赔.companies` 不应为空
- 若缺少真实盘口，应明确落为 `missing_real_line` 或等价缺失状态

当前已在正式 `predict-match` 链上验证过样例：

- 比赛：`la_liga / 埃尔切 vs 赫塔费 / MatchID=1302914`
- 欧赔：可稳定解析为 `multi_company_consensus`
- 亚值 / 大小球 / 凯利：可稳定落入 `market_snapshot`
- 大小球：`over_under.available=true`，`line_source=snapshot_final`

## 稳定性策略

1. 已知 `match_id` 时优先直连抓取，不要重复模糊匹配
2. 未知 `match_id` 时优先用 `collect-data` 或 `okooo_fetch_daily_schedule.py` 落赛程 JSON
3. 球队简称差异统一依赖 `okooo_team_aliases.json`
4. 大小球优先走 `handicap.php -> 大小球 tab`
5. `/ou/`、`overunder.php`、`daxiao.php` 只作为 fallback
6. 最终进入正式预测流程时，优先使用 CLI，而不是直接 import 底层预测类

## 当前闭环位置

本指南关注的是赛前采集与预测衔接，但需要知道后续正式闭环入口：

- `save-result`
- `auto-sync-results`
- `result-sync-daemon`
- `sync-pending-results-review`

这些命令会驱动：

- SoT-backed 比赛更新 `teams_2025-26.md` / `teams_2026.md`
- runtime-only 比赛更新 `MEMORY.md` 与 `.okooo-scraper/runtime/*.json`
- 准确率、RAG、记忆样本和相关衍生结果同步刷新

## 调试说明

默认不建议把下面方式当作正式流程：

- 直接 import `EnhancedPredictor`
- 直接 import `DomainPredictor`

这些方式只适合开发排查或内部调试；正式对外与仓库级说明仍以 CLI 为准。

## 常见问题

### 1. 为什么抓到了欧赔和亚值，但大小球还是默认值？

优先排查：

1. 快照里是否真的有 `大小球`
2. `over_under.line_source` 是否指向真实快照来源
3. `over_under.market.final` 是否有真实字段
4. 预测流程是否成功读到最新快照

### 2. 为什么本机直接打开 `odds.php` 会返回 `403/405`？

优先排查：

1. 是否缺少移动端 UA
2. 是否缺少 `Referer: https://m.okooo.com/`
3. 是否没有走仓库里的公共移动 profile 策略
4. 是否命中了浏览器扩展、隐私防护或旧缓存

排障结论见仓库根文档：`debug-local-odds-access.md`

### 3. 为什么赛程里找不到球队？

常见原因：

- 澳客使用简称
- 日期不对
- 忘记传 `match_time`

处理方式：

- 更新 `okooo_team_aliases.json`
- 先跑 `okooo_fetch_daily_schedule.py`
- 已知 MatchID 后直接传 `--match-id`

### 4. 哪个入口才算正式流程？

以这些命令为准：

- `prediction_system.py collect-data`
- `prediction_system.py predict-match`
- `prediction_system.py predict-schedule`
- `prediction_system.py harness-run`
- 必要时配合 `okooo_save_snapshot.py`

如文档与代码冲突，以 `app/cli.py`、`domain/persistence.py`、`runtime/result_sync.py`、`result_manager.py` 的当前实现为准。

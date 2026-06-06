# 澳客实时赔率与大小球抓取指南

本指南只描述当前正式采集链与预测主链如何衔接，默认采用 CLI-first 方式。

## 入口与目标

当前正式入口分两层：

- `prediction_system.py`：兼容 / 发现入口
- `app/cli.py`：真实 CLI 路由与 JSON 输出实现

本指南的目标是把：

- 赛程定位
- `external_match_id` 获取
- 实时快照抓取
- 大小球真实盘口定位
- 正式预测链路接入

统一成一条稳定流程。

## 当前主流程

1. 先定位比赛：联赛、主客队、日期，必要时补 `match_time`
2. 优先调用 `prediction_system.py collect-data`，或抓当天赛程获取纯数字 `external_match_id`
3. 必要时用 `okooo_save_snapshot.py` 显式生成实时快照 JSON
4. 预测链会优先注入已有快照；若真实盘口线缺失，会按严格身份补抓快照
5. 再通过 `prediction_system.py predict-match` 或 `harness-run --pipeline match_prediction` 进入正式预测链
5. 预测 side effects 由 `domain/persistence.py` 统一处理；赛果闭环由 `runtime/result_sync.py` 与 `result_manager.py` 统一处理

## 关键事实

- 欧赔入口：`https://m.okooo.com/match/odds.php?MatchID=<external_match_id>`
- 亚值入口：`https://m.okooo.com/match/handicap.php?MatchID=<external_match_id>`
- 大小球真实位置：`handicap.php` 页面内的 `大小球` tab
- 历史/赛果入口：`https://m.okooo.com/match/history.php?MatchID=<external_match_id>`
- 快照目录：`.okooo-scraper/snapshots/<league>/`
- 赛程缓存目录：`.okooo-scraper/schedules/<league>/`
- 默认快照 driver：`local-chrome`
- 默认访问策略：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池：`okooo_mobile_access.py` 统一维护，当前为 `500` 组 `iPhone Safari` profile，分布在多个 iPhone device pool 上，`viewport` 与 `device_scale_factor` 会随设备池变化
- 日赛程脚本已支持自动翻月到目标年月、按日期分组抽取整天赛程并清洗操作按钮噪声
- 快照脚本已支持按 `日期 + 主客队 + 时间` 精确锁定目标比赛行，避免同日多场 `23:00` 误命中
- 快照读取链路新增身份校验：只有 `match_id + 主客队 + match_date` 一致时才复用旧快照
- 预测链已支持预测前快照注水与 `missing_real_line` 场景下的正式补抓
- 直连移动端页面时，正式链的主动访问入口统一通过 `runtime.match_ids.build_okooo_match_url()` 构造 URL；非纯数字 ID 会被直接拦截
- 阻断页识别除了 `403/405` 文本页，也覆盖 `请进行验证 / 滑动到最右边 / 拖动滑块 / 验证码` 与 `canvas / verify iframe / 大图验证` 等图形验证页
- 命中验证页时，当前入口路径会尽快返回 `verification_required` 并停止本路径连续重试；随后会触发 1 次 fresh mobile pool 重入，若仍命中验证则停止，不会无限打转

## 当前访问策略

`m.okooo.com` 对默认桌面请求、无 `Referer` 请求较敏感，常见失败形态是 `403/405` 或阿里云风控页。

当前仓库里的正式访问口径已经统一为：

- `local-chrome` 优先
- `iPhone Safari` 风格 UA
- `Referer: https://m.okooo.com/`
- no-cache 头
- 每次请求都会从 `500` 组移动 profile 池中选择 profile，并优先在 fresh-pool 恢复时切到不同 `device_pool_id`

如果本机浏览器能打开、脚本却访问失败，优先排查是否绕过了这套公共策略，而不是先怀疑 DNS 或系统代理。

## 推荐命令

### 1. 先抓当天赛程并拿到 external_match_id

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 英超 --date 2026-05-24
```

### 2. 走正式采集入口

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py collect-data --league premier_league --date 2026-05-24 --json
```

### 3. 用 external_match_id 直接抓快照

```bash
cd /Users/bytedance/trae_projects
python3 europe_leagues/okooo_save_snapshot.py \
  --driver local-chrome \
  --league 英超 \
  --team1 伯恩利 \
  --team2 狼队 \
  --date 2026-05-24 \
  --time 23:00 \
  --match-id 1296105 \
  --out-dir /Users/bytedance/trae_projects/europe_leagues/.okooo-scraper/snapshots \
  --overwrite
```

### 4. 用正式 CLI 跑最终预测

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

### 5. 需要阶段化审计时走 Harness

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 prediction_system.py harness-run \
  --pipeline match_prediction \
  --league premier_league \
  --date 2026-05-24 \
  --home-team 伯恩利 \
  --away-team 狼队 \
  --time 23:00 \
  --json
```

### 6. 运行完整 okooo 自动化测试

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 -m unittest test_okooo_save_snapshot test_okooo_mobile_access test_okooo_fetch_daily_schedule test_okooo_browser
```

如需显式运行 Playwright 烟雾测试：

```bash
cd /Users/bytedance/trae_projects/europe_leagues
OKOOO_BROWSER_E2E=1 python3 -m unittest test_okooo_browser
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

- `la_liga / 埃尔切 vs 赫塔费 / MatchID=1302914`
- `premier_league / 伯恩利 vs 狼队 / MatchID=1296105`
- 已验证结果：
  - 欧赔可稳定解析为 `multi_company_consensus`
  - 亚值 / 大小球 / 凯利可稳定落入 `market_snapshot`
  - 大小球可写入 `over_under.line`，来源为 `snapshot_final`
  - 真实盘口回流后，预测结果可以从原始模型方向被正式修正

## 稳定性策略

1. 已知 `match_id` 时优先直连抓取，不要重复模糊匹配
2. 未知 `match_id` 时优先用 `collect-data` 或 `okooo_fetch_daily_schedule.py` 落赛程 JSON
3. 球队简称差异统一依赖 `okooo_team_aliases.json`
4. 若联赛页停在错误月份，优先依赖脚本自动翻月，不要手工假设日期标签可直接点击
5. 大小球优先走 `handicap.php -> 大小球 tab`
6. `/ou/`、`overunder.php`、`daxiao.php` 只作为 fallback
7. 最终进入正式预测流程时，优先使用 CLI，而不是直接 import 底层预测类
8. 当 `line_source=missing_real_line` 时，应先排查错误 `match_id`、坏赛程缓存或串场快照，而不是直接回退默认盘口
9. `internal_match_id / teams_match_id` 只能用于项目内部定位或写回，不能直接拿去访问任何 `m.okooo.com/match/*.php?MatchID=...` 页面

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
4. 是否已经返回 `verification_required` 且 fresh mobile pool 重入也失败
5. 是否命中了浏览器扩展、隐私防护或旧缓存

排障结论见仓库根文档：`debug-local-odds-access.md`

### 3. 为什么赛程里找不到球队？

常见原因：

- 澳客使用简称
- 日期不对
- 忘记传 `match_time`

处理方式：

- 更新 `okooo_team_aliases.json`
- 先跑 `okooo_fetch_daily_schedule.py`
- 已知纯数字 `external_match_id` 后直接传 `--match-id`

### 5. 为什么抓到了快照但预测里还是 `missing_real_line`？

优先排查：

1. 快照是否命中了错误比赛，主客队或日期与请求不一致
2. 旧快照文件是否被错误复用
3. `collect-data` 是否已经把 `odds_data` 写回目标比赛
4. `predict-match` 是否读到了最新快照路径

### 4. 哪个入口才算正式流程？

以这些命令为准：

- `prediction_system.py collect-data`
- `prediction_system.py predict-match`
- `prediction_system.py predict-schedule`
- `prediction_system.py harness-run`
- 必要时配合 `okooo_save_snapshot.py`

如文档与代码冲突，以 `app/cli.py`、`domain/persistence.py`、`runtime/result_sync.py`、`result_manager.py` 的当前实现为准。

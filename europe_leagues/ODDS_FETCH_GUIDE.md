# 澳客实时赔率与大小球抓取指南

> ## 速读摘要（TL;DR）
> 读不全也能抓盘——看完这段即可掌握抓取链路与解析关键点。
>
> - **定位**：本文讲 **怎么抓澳客四盘 + 解析规则 + 撞墙排障**（命令工作流看 [`README_使用指南.md`](README_使用指南.md)）。
> - **四盘**：欧赔 / 凯利 / 亚盘 / 大小球，走 **单会话 hub 真实导航** 一次拿回：暖首页 → `history.php` → 点 `亚指`/`欧指` 整页跳转 → 页内点 `大小球`/`凯利` tab。已移除所有深链回退。
> - **抓取入口**：`okooo_save_snapshot.py`；只补欧赔用 `--odds-only`；可调 `--market-dwell`/`--ouzhi-retry-waits`/`--no-odds-fresh-session`。
> - **阻断判定**：`_page_blocked_now` 带「赔率数字逃生阀」——页面已渲染 ≥6 个 `x.xx` 赔率时一律判正常、绝不判墙（曾因过宽 canvas 弱规则误判 odds.php，已删除）。
> - **欧赔撞墙处理**：仅标记欧赔/凯利 `blocked`、保留已拿到的亚值/大小球；按 `OUZHI_RETRY_WAITS`(默认 3,5,10) 阶梯重试，仍失败则换新设备指纹 odds-only 会话单独重抓。
> - **固定 IP 抗封（单机无代理）**：三道代码级防线默认生效——① 进程级全局频控闸（每次深链导航前强制最小间隔，默认 2.5s，`--min-request-interval`/`OKOOO_MIN_REQUEST_INTERVAL` 可调）；② 所有等待/退避带 ±35% 随机抖动；③ 注入 stealth 脚本屏蔽 `navigator.webdriver` 等自动化指纹。节奏由代码恒定约束，与调用方是哪个模型无关。
> - **解析关键不变量**：
>   - 欧赔优先 `multi_company_consensus`，`99家平均` 仅作 fallback。
>   - 凯利与欧赔同构表，逐公司行抽 `[初始 主/平/客][最新 主/平/客][返还率]` 取共识；**主/平/客三路应彼此不同，坍缩成同一返还率即为列映射 bug（已修复）**。
> - **找其他文档**：先看导航路由页 [`docs/INDEX.md`](docs/INDEX.md)。
>
> 详细内容见下文分节。

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

- 唯一盘口抓取链路：单会话 hub 真实导航。先暖一次 `m.okooo.com` 首页养 cookie，再落地该场 hub 页 `history.php`，在同一会话里按真人路径点击导航：
  - 点 `亚指` 链接整页跳转到 `handicap.php`，解析亚盘后，在同页点内嵌 `大小球` tab 解析大小球
  - 回到 hub，点 `欧指` 链接整页跳转到 `odds.php`，解析欧赔后，在同页点 `凯利` tab 解析凯利
  - 一次会话拿回欧赔 / 亚值 / 大小球 / 凯利四盘，不再为每个盘口冷启动新浏览器或直接深链跳深页
- hub 入口：`https://m.okooo.com/match/history.php?MatchID=<external_match_id>`
- 各盘口落地页（由 hub 内点击导航到达，不直接深链）：
  - 欧赔 / 凯利：`odds.php`
  - 亚值 / 大小球：`handicap.php`
- 大小球真实位置：`handicap.php` 页面内的 `大小球` tab；不存在独立可用的 `overunder.php` / `daxiao.php`（空页面）
- 已移除所有深链回退 extractor 与回退编排（`_extract_odds_bundle` / `_extract_*_with_fallback` / `_extract_*_mobile_alt` / history-tab 流等），编排层 `_extract_all_markets_with_fallback` 现在只跑一次 hub，hub 没解析到的盘口按原样（`blocked` / `found:false`）返回
- 快照目录：`.okooo-scraper/snapshots/<league>/`
- 赛程缓存目录：`.okooo-scraper/schedules/<league>/`
- 默认快照 driver：`local-chrome`
- 默认访问策略：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 公共移动设备池：`okooo_mobile_access.py` 统一维护，当前为 `500` 组 `iPhone Safari` profile，分布在多个 iPhone device pool 上，`viewport` 与 `device_scale_factor` 会随设备池变化
- 日赛程脚本已支持自动翻月到目标年月、按日期分组抽取整天赛程并清洗操作按钮噪声
- 快照脚本已支持按 `日期 + 主客队 + 时间` 精确锁定目标比赛行，避免同日多场 `23:00` 误命中
- 快照读取链路新增身份校验：只有 `match_id + 主客队 + match_date` 一致时才复用旧快照
- 预测链已支持预测前快照注水与 `missing_real_line` 场景下的正式补抓
- 阻断页识别除了 `403/405` 文本页，也覆盖 `请进行验证 / 滑动到最右边 / 拖动滑块 / 验证码` 与 `verify iframe / 大图验证`（验证特征图，长宽 ≥200px）等图形验证页
- **强阻断判定 `_page_blocked_now` 带「赔率数字逃生阀」**：页面若已渲染出 ≥6 个 `x.xx` 形态赔率数字，则一律判为正常页、绝不判墙。真实滑块/验证码墙不可能渲染出完整赔率表（`odds.php` 实测有 ~270 个赔率数字），这一票否决保证「解析得到的真实数据」不被误判吞掉。曾因一条过宽的 `canvas + slider/verify` 弱规则把正常 `odds.php` 误判成墙，导致欧赔/凯利长期拿不到数据，该弱规则已删除
- **凯利解析（`_parse_kelly_on_current_page`）逐公司行抽取**：凯利与欧赔在澳客是同构表（每家公司行布局 `[初始 主/平/客][最新 主/平/客][返还率]`），解析复用欧赔同款「逐公司行 + 多公司共识平均」逻辑——每行抽 6 个凯利值（凯利区间 0.3–2.5 校验）、中位数去离群后加权出三路共识，第 7 个数（0.8–1.2）单独识别为返还率 `payout_rate`。早期错误地只读单个 `99家平均` 聚合行的单 `td`，导致主/平/客三路被同一个返还率填充（已修复）。三路相等是列映射错误的强信号
- hub 链路在每次整页跳转后（→`handicap.php`、→`odds.php`）以及解析完四盘后都做强阻断判定（`_page_blocked_now`），任意盘口命中验证墙都会把 `blocked` 上抛到顶层，确保熔断与换池重入正常触发，避免中途撞墙被当成「没开盘」而静默丢数据
- 欧值(`odds.php`)若真撞墙，先做阶梯重试（`OUZHI_RETRY_WAITS`，默认 `3s → 5s → 10s`：等待后回 hub 重新点欧值再判墙）；阶梯耗尽仍撞墙且亚值/大小球已成功时，触发独立的换新设备指纹 odds-only 会话单独重抓欧赔/凯利（`--no-odds-fresh-session` 可关闭），恢复成功打 `_recovered_via: odds_fresh_session` 标记。亚值/大小球来自第一段会话，恢复段永不影响它们
- odds.php 撞墙时只把欧赔/凯利标记 `blocked`，**保留同会话已拿到的真实亚值/大小球**，不再整轮丢弃；仅当亚值/大小球也全部失败时才把 `blocked` 上抛顶层触发熔断+换池重入
- 命中验证页时，当前入口路径会尽快返回 `verification_required` 并停止本路径连续重试；同时会打开基于 `match_id + market_family` 的 TTL breaker，并在市场页访问前执行最小间隔节流，避免持续撞验证页
- **固定 IP 单机抗封三道防线（默认全开，无需代理）**：
  - **进程级全局频控闸 `_global_pace_gate`**：挂在 `_open_ready` 每次深链导航前，强制两次深链之间的最小间隔（默认 `2.5s`，带抖动）。固定 IP 下「请求节奏」是被风控识别的首要信号——这道闸让下游访问频率恒定温柔，**无论上游是哪个模型/agent、并发多猛都一视同仁**，从根上消除「某些模型每次都撞墙」的节奏差异。可用 `--min-request-interval <秒>` 或 `OKOOO_MIN_REQUEST_INTERVAL` 配置
  - **随机抖动 `_jittered`（±35%）**：所有等待（频控闸、OUZHI 阶梯重试、文件级 throttle）都加随机抖动。固定节奏本身就是机器特征，抖动让重试节奏不可预测、更像真人
  - **stealth 指纹屏蔽 `_install_stealth_script`**：连接后用 CDP `Page.addScriptToEvaluateOnNewDocument` 在文档启动前注入脚本，屏蔽 `navigator.webdriver`、补 `window.chrome`/`navigator.languages`/permissions 等 CDP 自动化最明显的破绽，让页面读起来像正常移动 Safari。best-effort，注入失败绝不阻断抓取
  - 物理上限说明：以上是「行为+指纹」层优化，若澳客已对该 IP 做硬性日配额，只能延缓不能突破（单机无代理无法绕过 IP 级封禁）
- 设备指纹池（`okooo_mobile_access.py`）已锁定每个 iPhone 设备的 `viewport + device_scale_factor` 一致性、仅轮换 UA 版本，`fresh_mobile_profile` 跨设备池干净轮换，避免拼出「不存在的设备组合」被识别

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

快照脚本的盘口抓取相关可选参数：

- `--market-dwell <秒>`：每盘解析前的额外停留秒数（默认 `5.0`，等价环境变量 `OKOOO_MARKET_DWELL`），用于确保盘口数据渲染完整再读取
- `--ouzhi-retry-waits <逗号秒列表>`：欧值(`odds.php`)撞墙后的阶梯重试等待（默认 `3,5,10`，等价环境变量 `OKOOO_OUZHI_RETRY_WAITS`）
- `--no-odds-fresh-session`：关闭「odds.php 撞墙后换新设备指纹 odds-only 会话单独重抓欧赔/凯利」的恢复（默认开启）
- `--odds-only`：独立冷会话只抓欧赔/凯利（`hub → 欧值 → odds.php → 凯利 tab`，全程不碰 `handicap.php`），用于隔离排查 odds.php 单端点问题
- `--min-request-interval <秒>`：深链导航之间的进程级最小间隔（默认 `2.5`，带 ±35% 抖动，等价环境变量 `OKOOO_MIN_REQUEST_INTERVAL`）。固定 IP 单机抗封的核心旋钮——撞墙频繁就调大、嫌慢可调小

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
5. 四盘盘口统一走单会话 hub 真实导航（`history.php` → 点 `亚指`/`欧指` 跳转 → 页内点 `大小球`/`凯利` tab）；已无深链回退路径，hub 失败的盘口保持 `blocked`/`found:false` 原样
6. 大小球只存在于 `handicap.php` 内的 `大小球` tab；`overunder.php`、`daxiao.php` 是空页面，不作为来源
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
4. 是否已经返回 `verification_required` 且同一 `match_id + market_family` 的 TTL breaker 仍处于打开状态
5. 是否因为最小间隔节流导致访问被主动延后
6. 是否命中了浏览器扩展、隐私防护或旧缓存

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
- `prediction_system.py predict-fourteen-issue`
- `prediction_system.py harness-run`
- 必要时配合 `okooo_save_snapshot.py`

如文档与代码冲突，以 `app/cli.py`、`domain/persistence.py`、`runtime/result_sync.py`、`result_manager.py` 的当前实现为准。

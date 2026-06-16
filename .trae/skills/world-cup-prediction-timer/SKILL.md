---
name: "world-cup-prediction-timer"
description: "世界杯预测定时刷新器 / 赛程驱动监控 + 可被其他 agent 调用的刷新接口。赛程唯一来源为 world_cup/teams_2026.md：自动监控下一场未回填赛果的真实球队比赛，比分回填后自动切到下一场，依次类推。可传入「比赛日期 + 球队名称（+开赛时间）」去澳客网拉取最新欧值/亚值/大小球/概率，重跑正式 predict-match 预测链（自动落库 teams md / MEMORY / archive）并重生成对应日期网页。每天 21:00 全量刷新未开赛比赛、每场赛前 1 小时自动再刷一次；监控异常会弹 macOS 通知提示重启定时器；支持 launchd 开机自启。Invoke when another agent needs to refresh a World Cup match's latest odds/prediction and update its MD + web page, or to run/monitor the recurring schedule-driven prediction timer."
---

# World Cup Prediction Timer

世界杯预测定时刷新器，**赛程驱动**。直接读取 `world_cup/teams_2026.md` 的赛程表，监控「下一场未回填赛果的真实球队比赛」；当该场比分从 `-` 变为真实比分（赛果回填）后，自动切换到下一场继续监控，依次到赛事结束。其他 agent 可直接调用 `refresh` 子命令完成单场/单日刷新。

## 入口规则
- 发现入口：`prediction_system.py`（正式 CLI 总入口）。
- 定时器脚本：`scripts/world_cup_prediction_timer.py`（本 skill 的核心）。
- 赛程来源（SoT）：`world_cup/teams_2026.md` 的「赛程信息」表（`| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |`，比分 `-` = 未完赛，真实比分 = 已完赛）。淘汰赛占位队（`A2`/`胜者73`/`组第三` 等）自动跳过。
- 底层复用：`scripts/build_world_cup_daily_html.py`（逐场调用 `predict-match` 抓盘口+落库+渲染网页）。
- 实际预测/落库：`app/cli.py` 的 `predict-match`（默认 `OKOOO_REFRESH_LIVE=1` 抓澳客最新盘口；world_cup 为 SoT 联赛，会写回 `teams_2025-26.md` / MEMORY / archive）。
- 运行状态：`world_cup/analysis/predictions/timer_state.json`（监控对象、触发标记、失败计数、告警冷却）。
- 日志：`world_cup/analysis/predictions/timer.log`；launchd 输出 `launchd.out.log` / `launchd.err.log`。
- 网页产物：`world_cup/analysis/predictions/<date>_predictions.html`。

## 何时触发
- 其他 agent 需要刷新某场世界杯比赛的最新欧值/亚值/大小球/概率并更新网页。
- 需要启动赛程驱动的常驻监控（自动盯下一场、完赛自动切换）。
- 需要配置开机自启，让重启电脑后定时器自动运行。
- 需要查询当前监控的是哪场比赛 / 监控是否健康。

## 触发口径（定时器）
1. **每天 21:00**：刷新所有「尚未开赛」比赛所在日期的网页（按日期分组，当天仅一次）。
2. **每场赛前 1 小时**：自动触发该场所在日期的刷新（每场仅一次）。
3. **赛程监控**：始终盯住第一场未回填赛果的真实球队比赛；回填后自动切到下一场。

## 健康监控（异常即提示重启）
满足以下任一条件，会写日志 + 弹 macOS 通知（`osascript`）提示「请重启定时器」，并带 30 分钟同类去重冷却：
- **赛果久未回填**：当前监控比赛开赛超过 `STALE_AFTER_HOURS`（默认 3）小时仍为 `-`。
- **刷新连续失败**：预测刷新连续失败达 `MAX_FAILS`（默认 3）次。
- **赛程解析失败 / 为空**：`teams_2026.md` 读取异常或解析不到任何真实球队比赛。
- **轮询异常崩溃**：单轮出现未预期异常。

## 标准流程

### A. 其他 agent 单场刷新（最常用）
传入日期 + 球队（+ 开赛时间），立刻抓最新数据、重跑预测、更新 MD 与网页。会自动补全该日期赛程内的其他比赛，保证网页完整：

```bash
cd europe_leagues && python3 scripts/world_cup_prediction_timer.py refresh \
    --date 2026-06-17 --home 阿根廷 --away 阿尔及利亚 --time 09:00
```

### B. 刷新某日期全部比赛
直接按赛程刷新当天全部真实球队比赛（无需手动逐场注册）：

```bash
cd europe_leagues && python3 scripts/world_cup_prediction_timer.py refresh-date --date 2026-06-17
```

### C. 启动赛程驱动常驻监控
```bash
cd europe_leagues && python3 scripts/world_cup_prediction_timer.py daemon --interval-minutes 10
```
每 10 分钟轮询：盯当前比赛、命中 21:00 / 赛前 1 小时即刷新、完赛自动切换、异常弹通知。失败不退出。

### D. 开机自启（macOS launchd，推荐）
```bash
cd europe_leagues && python3 scripts/world_cup_prediction_timer.py install-launchd --interval-minutes 10
# 卸载：
python3 scripts/world_cup_prediction_timer.py uninstall-launchd
```
生成 `~/Library/LaunchAgents/com.europeleagues.worldcuptimer.plist`（`RunAtLoad` + `KeepAlive`）并 `launchctl load`。重启电脑/重新登录后 daemon 会自动运行；崩溃会被 launchd 自动拉起。
查看状态：`launchctl list | grep com.europeleagues.worldcuptimer`。

### E. 查看赛程 / 当前监控
```bash
python3 scripts/world_cup_prediction_timer.py schedule    # 列出全部真实球队比赛 + 标注监控中/已完赛
python3 scripts/world_cup_prediction_timer.py current     # 当前监控比赛 + 健康状态
```

### F. 单次调度（供外部 cron / 测试）
```bash
python3 scripts/world_cup_prediction_timer.py run-once                       # 当前时间判定触发
python3 scripts/world_cup_prediction_timer.py run-once --now "2026-06-17 02:15" --dry-run  # 模拟时间、只判不刷
```

## 子命令速查
| 子命令 | 作用 | 关键参数 |
| --- | --- | --- |
| `schedule` | 打印解析出的真实球队赛程 | — |
| `current` | 当前监控比赛 + 健康状态 | `[--now]` |
| `refresh` | 刷新某场所在日期网页（自动补全当天）| `--date [--home --away --time]` |
| `refresh-date` | 刷新某日期全部真实球队比赛 | `--date` |
| `run-once` | 按赛程检查一次触发/监控/健康 | `[--now] [--dry-run]` |
| `daemon` | 赛程驱动常驻守护进程 | `[--interval-minutes] [--max-cycles]` |
| `install-launchd` | 注册 macOS 开机自启 | `[--interval-minutes]` |
| `uninstall-launchd` | 注销开机自启 | — |

## 关键规则
- **赛程驱动**：以 `teams_2026.md` 为唯一赛程来源，监控顺序即赛程开赛时间顺序；不再需要手工注册比赛。
- **完赛判定**：比分单元格匹配 `^\d+-\d+$` 即视为已完赛并自动切换到下一场；`-` 视为未完赛。
- **仅世界杯**：固定 `--league world_cup`，不处理其他联赛；淘汰赛占位队自动跳过。
- **不重复造轮子**：抓盘口/预测/落库/渲染网页全部由 `build_world_cup_daily_html.py` → `predict-match` 完成，本脚本只做赛程解析 + 触发调度 + 监控 + 健康告警。
- **真实数据**：默认走澳客实时盘口（`OKOOO_REFRESH_LIVE=1`）。测试时可设 `OKOOO_REFRESH_LIVE=0` 复用本地快照、不发起网络请求。
- **异常即提示重启**：监控卡住/连续失败/赛程异常会弹 macOS 通知并写 `timer.log`，按提示重启定时器即可（launchd 模式下 `KeepAlive` 也会自动拉起）。

## 已验证样例
- 赛程解析：从 `teams_2026.md` 解析出 72 场真实球队比赛，淘汰赛占位队全部跳过，监控自动定位到第一场未完赛比赛。
- 监控切换：模拟当前场回填比分后，监控自动切到下一场并写日志。
- 触发判定：赛前 1 小时触发该日期刷新；开赛超 3 小时未回填弹「赛果未回填」告警。（`run-once --now --dry-run` 验证通过）
- 开机自启：`install-launchd` 成功写入 plist 并 `launchctl load`，daemon 随登录运行。

## 冲突处理
若脚本行为与本文档不一致，以 `scripts/world_cup_prediction_timer.py` 与 `app/cli.py predict-match` 的实际代码为准。

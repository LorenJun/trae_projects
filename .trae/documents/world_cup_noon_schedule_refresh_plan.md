# 世界杯赛程 12:00 自动重拉覆盖 SoT 改造计划

> **实施状态（2026-07-06）**：主体已完成，抓取通道最终版**未走月份页 `saishi/16-YYYY-MM/`**，改为**按日抓取** `okooo_fetch_daily_schedule.py --date {YYYY-MM-DD} --driver local-chrome`，日期窗口 `[today, today+1, today+2]`。原月份页 browser-use 通道被 405 反复阻挡，subprocess + LocalChromeSession CDP 后可稳定拿到目标日的 `{MatchID, home_team, away_team, kickoff_time, status, score}`。因此下面「关键文件」章节里的 `fetch_world_cup_schedule(months=...)` 签名已保留但参数被忽略，实际以 `_default_date_window()` 为准。
>
> 同期废弃的旧代码：`domain/world_cup_bracket.py` + `collectors/okooo_match_result.py` + `test_world_cup_bracket_draw.py` 已删除；`world_cup/teams_2026.md` 末尾「淘汰赛晋级映射」整段章节已清除，改由新章节「淘汰赛赛程自愈说明」承接。
>
> 开机自启已经生效：`~/Library/LaunchAgents/com.europeleagues.worldcuptimer.plist`（`RunAtLoad=true` + `KeepAlive=true`，30 分钟对齐轮询），最新 daemon 已加载 subprocess `sys.executable` 修复版。

## Context

**问题**：目前 `world_cup/teams_2026.md` 的淘汰赛赛程依赖「淘汰赛晋级映射（复盘自动推进 SoT）」章节把 `W## / L##` 占位符级联替换为真实球队。链路上任何一环（如 07-02 90 分钟平局未确认加时/点球胜者、澳客风控 405 拿不到详情页）失败，都会阻塞下游几天，07-07 08:00 W81 vs 西班牙这种场次就无法预测。

**目标**：新增「每天中午 12:00 自动从澳客 m 站月份页 `saishi/16-YYYY-MM/` 拉最新完整世界杯赛程，覆盖式写回 `teams_2026.md` 的赛程行」。写回只更新赛程侧字段（日期/时间/主队/客队/MatchID/备注前缀段），保留预测系统已写入的备注尾段（`预测:/信心:/比分:/大小:/爆冷:/解读:/复盘:` 等）。这样即便淘汰赛占位符级联失败，中午一次拉取就能把 W81/W85/W86 → 真实球队名。

**预期收益**：07-07/07-08/07-12 等淘汰赛场次不再依赖手工确认平局胜者；daemon 每天 12:00 自愈一次 SoT。

---

## 现有可复用组件

- **拉取样板**：[collect_wc2022_manifest.py](file:///Users/bytedance/trae_projects/europe_leagues/collect_wc2022_manifest.py) 已经实现「打开 `https://m.okooo.com/saishi/16-YYYY-MM/` → 滚动到底 → 用 `EXTRACT_JS` 抽 MatchID/日期/文本 → `_parse_row` 解析 stage/比分/主客队」的完整链路，直接复用其 EXTRACT_JS 和 `_parse_row`。
- **浏览器基础设施**：[okooo_save_snapshot.BrowserUse](file:///Users/bytedance/trae_projects/europe_leagues/okooo_save_snapshot.py#L1215-L1276) + [_open_ready](file:///Users/bytedance/trae_projects/europe_leagues/okooo_save_snapshot.py#L1778-L1833) + `_eval_scroll_to_bottom / _eval_scroll_to_top`。
- **别名归一化**：[collectors/aliasing.py](file:///Users/bytedance/trae_projects/europe_leagues/collectors/aliasing.py) 的 `normalize_team_name('world_cup', name)` 用于把 "刚果（金）" 归一到 SoT canonical name。
- **行内 note 局部拼接样板**：[domain/writeback.py `update_teams_md_prediction_notes`](file:///Users/bytedance/trae_projects/europe_leagues/domain/writeback.py) 与 [domain/world_cup_bracket.apply_placeholder_replacements](file:///Users/bytedance/trae_projects/europe_leagues/domain/world_cup_bracket.py#L236-L266)。新赛程覆盖参照它们「只改指定列，其余保留」的写法。
- **daemon 定时钩子样板**：[world_cup_prediction_timer._default_state](file:///Users/bytedance/trae_projects/europe_leagues/scripts/world_cup_prediction_timer.py#L198-L206) + `evaluate_triggers` 的 `state["last_evening_refresh"] != today` 每日一次判定。
- **告警通道**：[alert(state, kind, title, message)](file:///Users/bytedance/trae_projects/europe_leagues/scripts/world_cup_prediction_timer.py#L180-L193) 带冷却，走 macOS 通知。

## 关键文件

### 1. 新建 `domain/world_cup_schedule_fetch.py`
职责：一次性拉某月 saishi/16-YYYY-MM/ 页面，返回结构化赛程记录。

- `fetch_world_cup_schedule(months: list[str]) -> list[MatchRecord]`
  - 复用 `collect_wc2022_manifest.EXTRACT_JS / _parse_row` 的正则实现（**同一份代码抽公共 helper**，不复制）。
  - 内部用 `okooo_save_snapshot.BrowserUse(session=f"wc_sched_{month}")` + `_open_ready` + `_eval_scroll_to_bottom`。
  - 抛 405 / blocked 时抛 `ScheduleFetchBlocked` 异常，让上层 daemon 走 alert。
- `MatchRecord` = `{match_id, date, time, home, away, home_score, away_score, stage, section, raw_text, history_url}`。
- 别名归一化在这一步做（`normalize_team_name('world_cup', name)`）。

### 2. 新建 `domain/world_cup_schedule_writeback.py`
职责：把拉到的 `list[MatchRecord]` 覆盖式 upsert 进 `teams_2026.md` 的 `## 赛程信息` 区块。

- `apply_schedule_updates(teams_file, records) -> {updated_lines, added_lines, warnings}`
- 匹配键：`MatchID`（首选，从备注里正则抽出）→ 其次 `(date, home_canonical, away_canonical)`。
- 每场只 upsert 五段：`date / time / home / away / MatchID`。
- `比分` 只在原值为 `-` 或空、且澳客返回 finished 时覆盖；否则不动。
- `备注` 分号切分：只重写「前缀非预测段」（如 `待开赛；北京时间...；1/16决赛 W80；MatchID:1277378`），保留分号后带 `预测:/信心:/比分:/大小:/进球数:/爆冷:/仓位:/案例:/情景:/动态调权:/解读:/复盘:` 的每一段。
- 停止条件：不新增行；只更新既有行；找不到匹配的赛程行落 warning 交人工。
- 淘汰赛主客队名如果拉到真实球队（比如「塞内加尔」而 md 现有「W81」），直接替换 → 与 `apply_placeholder_replacements` 效果一致。

### 3. 修改 `scripts/world_cup_prediction_timer.py`
- 加常量 `NOON_HOUR = 12`、`NOON_MINUTE = 0`。
- `_default_state` 加 `"last_noon_schedule_pull": ""`；`load_state` 里 setdefault 兼容旧状态。
- 新增函数 `_maybe_refresh_schedule_from_okooo(state, now) -> bool`：
  - 满足 `now >= 今日 12:00` 且 `state["last_noon_schedule_pull"] != today` 时执行；
  - 调 `fetch_world_cup_schedule(months=[当前月, 下月])` → `apply_schedule_updates`；
  - 成功后写 `state["last_noon_schedule_pull"] = today`；失败调 `alert(state, "schedule_pull_fail", ...)`；
  - `finally` 里 `save_state(state)`。
- `run_cycle` 里在 `parse_schedule()` **之前**插入这一步，然后照常 `parse_schedule()`。

### 4. 单元测试
- 新建 `test_world_cup_schedule_writeback.py`：喂固定 `records` 与最小 md，断言：
  - `W81 → 真实球队` 覆盖生效；
  - 预测尾段（`预测:主胜 信心:0.44 ... 解读:...`）保留原样；
  - `比分 0-1` 已回填的行不会被 `- 未开赛` 覆盖；
  - MatchID 变更 / 时间调整 / 别名归一化（`刚果（金）` → `刚果民主共和国` canonical）；
  - 找不到匹配行 → warning。
- 已有 `test_world_cup_bracket_draw.py` 保持不动（`advance_world_cup_bracket` 依然是次级链路）。

### 5. 主链保留双通道
- `sync-pending-results-review` 仍调 `advance_world_cup_bracket`（次级）；日常 daemon 12:00 拉取是主通道。两条通道都基于最终写入 md 的行，天然幂等，不冲突。

---

## 与用户当前需求（预测 07-07 两场）的关系

- 本改造上线后 daemon 中午自动跑，07-07 08:00 W81 vs 西班牙这类场次的主队会被自动补上。
- **手工触发一次**：加一个 `python3 scripts/world_cup_prediction_timer.py refresh-schedule` 子命令做一次性重拉（无需等到 12:00），落地就能预测 07-07 两场。
- 澳客当前对本机整站 405 —— 拉取失败会走 `alert` + `schedule_pull_fail` 通知，SoT 保持原样，不破坏现状。恢复访问后下次 12:00 自动生效。

---

## Verification

1. **单元测试**：
   ```bash
   cd europe_leagues
   python3 -m unittest test_world_cup_schedule_writeback -v
   python3 -m unittest test_world_cup_bracket_draw test_team_alias_symbol_normalization test_result_sync test_result_manager
   ```
2. **本地 dry-run 覆盖**（不落盘，用 tempfile）：喂假 records 验证 update/preserve 分支。
3. **一次性重拉子命令**（澳客可访问时）：
   ```bash
   python3 scripts/world_cup_prediction_timer.py refresh-schedule
   ```
   跑完看 `git diff europe_leagues/world_cup/teams_2026.md`：应只有赛程行主客队 / MatchID / 备注前缀段变化，预测尾段与已回填比分不动。
4. **daemon 冒烟**：把 `NOON_HOUR/NOON_MINUTE` 暂时改成 `now + 1min` 手工触发一轮 `run_cycle`，观察 `timer_state.json` 里 `last_noon_schedule_pull` 是否变成今日。回滚常量。
5. **端到端**：拉取通道打通后，`teams_2026.md` 里 07-07 08:00 的主队字段从 `W81` 变成真实球队名，跑 `python3 scripts/build_world_cup_daily_html.py --date 2026-07-07` 应能生成两场预测卡片。

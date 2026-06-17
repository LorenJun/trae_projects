#!/usr/bin/env python3
"""世界杯预测定时器 / 赛程驱动监控 + 可调用刷新接口。

赛程唯一来源（SoT）：`world_cup/teams_2026.md`。定时器直接读取其中的赛程表，
监控「下一场未开赛/进行中的真实球队比赛」，当该场比分从 `-` 变成真实比分
（赛果回填）后，自动切换到下一场继续监控，依次类推到赛事结束。

定时触发口径（仍保留）：
  1) 每天 21:00 触发一次：刷新「当天这批」即将开赛的比赛所在日期的网页。
  2) 每场比赛开赛前 1 小时自动触发一次：刷新该场所在日期的网页。

抓盘口 / 预测 / 落库 / 渲染网页全部复用
`scripts/build_world_cup_daily_html.py`（逐场调用 `predict-match`，
predict-match 默认 OKOOO_REFRESH_LIVE=1 会抓澳客最新盘口并落库）。

健康监控：若当前监控的比赛在开赛 STALE_AFTER_HOURS 小时后仍未回填赛果、
或预测刷新连续失败、或赛程文件解析失败，会通过 macOS 通知 + 日志提示
「请重启定时器」。

开机自启：`install-launchd` 子命令生成 LaunchAgent plist 并加载，
重启电脑后 daemon 会随登录自动运行（RunAtLoad + KeepAlive）。

子命令：
  schedule         打印解析出的真实球队赛程（调试）
  current          打印当前监控比赛 + 健康状态
  refresh          立刻刷新某场/某日并重生成网页（其他 agent 主调用入口）
  refresh-date     立刻刷新某日期全部真实球队比赛并重生成网页
  run-once         按赛程检查一次触发/监控/健康（--now 可模拟时间）
  daemon           常驻守护进程，按 interval 轮询 run-once
  install-launchd  注册 macOS 开机自启（LaunchAgent）
  uninstall-launchd 注销开机自启

用法示例：
  # 其他 agent 调用：刷新阿根廷这场的最新数据 + 网页
  python3 scripts/world_cup_prediction_timer.py refresh \
      --date 2026-06-17 --home 阿根廷 --away 阿尔及利亚 --time 09:00

  # 启动赛程驱动守护进程（每 10 分钟轮询）
  python3 scripts/world_cup_prediction_timer.py daemon --interval-minutes 10

  # 配置开机自启
  python3 scripts/world_cup_prediction_timer.py install-launchd --interval-minutes 10
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORLD_CUP_DIR = PROJECT_ROOT / "world_cup"
TEAMS_MD = WORLD_CUP_DIR / "teams_2026.md"
PRED_DIR = WORLD_CUP_DIR / "analysis" / "predictions"
STATE_PATH = PRED_DIR / "timer_state.json"
LOG_PATH = PRED_DIR / "timer.log"
GENERATOR = PROJECT_ROOT / "scripts" / "build_world_cup_daily_html.py"

EVENING_HOUR = 21          # 每天晚间触发（时）
EVENING_MINUTE = 0         # 每天晚间触发（分）=> 21:00
EVENING_WINDOW_HOURS = 18  # 晚间触发只刷「当天这批」：未来 N 小时内开赛的比赛
PRE_MATCH_MINUTES = 60     # 赛前 1 小时触发
STALE_AFTER_HOURS = 3      # 开赛 N 小时后仍无赛果 => 监控疑似卡住
MAX_FAILS = 3              # 刷新连续失败次数阈值 => 提示重启
ALERT_COOLDOWN_MINUTES = 30  # 同类告警最短间隔，避免刷屏
SYNC_LIMIT = 10            # 每轮自动回填赛果的最大尝试场数

LAUNCHD_LABEL = "com.europeleagues.worldcuptimer"
SCORE_RE = re.compile(r"^\d+-\d+$")


# ----------------------------- 日志 / 告警 -----------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def notify(title: str, message: str) -> None:
    """macOS 非阻塞通知；失败静默（非 macOS 环境也能跑）。

    文本经 argv 传入 AppleScript，避免引号/特殊字符注入或语法错误。
    """
    try:
        script = (
            'on run argv\n'
            '  display notification (item 1 of argv) with title (item 2 of argv)\n'
            'end run'
        )
        subprocess.run(
            ["osascript", "-e", script, message, title],
            check=False, timeout=10, capture_output=True,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def alert(state: dict, kind: str, title: str, message: str) -> None:
    """记录日志 + 弹通知，带去重冷却。"""
    now = datetime.now()
    cooldown = state.setdefault("alert_cooldown", {})
    last = cooldown.get(kind)
    if last:
        try:
            if now - datetime.fromisoformat(last) < timedelta(minutes=ALERT_COOLDOWN_MINUTES):
                return
        except ValueError:
            pass
    cooldown[kind] = now.isoformat()
    log(f"⚠ 告警[{kind}] {title}：{message}")
    notify(f"⚠ {title}", message)


# ----------------------------- 状态读写 -----------------------------

def _default_state() -> dict:
    return {
        "last_evening_refresh": "",
        "pre_match_done": [],
        "monitor": {"current": None, "since": ""},
        "fail_count": 0,
        "alert_cooldown": {},
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()
    base = _default_state()
    base.update(data)
    base.setdefault("monitor", {"current": None, "since": ""})
    base.setdefault("alert_cooldown", {})
    return base


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ----------------------------- 赛程解析 -----------------------------

def _parse_valid_teams(text: str) -> set[str]:
    """从「小组信息」区块解析 48 支真实球队名。"""
    teams: set[str] = set()
    in_groups = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## 小组信息"):
            in_groups = True
            continue
        if in_groups and s.startswith("## "):
            break
        if not in_groups or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) != 2:
            continue
        idx, name = cells
        if idx.isdigit() and name and name != "球队":
            teams.add(name)
    return teams


def parse_schedule(text: str | None = None) -> list[dict]:
    """解析赛程表，仅保留双方均为真实球队的比赛（跳过 A2/胜者73/组第三 等占位）。

    返回按开赛时间排序的 list[dict]：
      {date, time, home, away, score, finished, section}
    """
    if text is None:
        text = TEAMS_MD.read_text(encoding="utf-8")
    valid = _parse_valid_teams(text)
    matches: list[dict] = []
    section = ""
    in_schedule = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## 赛程信息"):
            in_schedule = True
            continue
        if not in_schedule:
            continue
        if s.startswith("### "):
            section = s[4:].strip()
            continue
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 5:
            continue
        date, tm, home, score, away = cells[0], cells[1], cells[2], cells[3], cells[4]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue  # 跳过表头/分隔行
        if home not in valid or away not in valid:
            continue  # 跳过淘汰赛占位球队
        finished = bool(SCORE_RE.match(score))
        matches.append({
            "date": date,
            "time": tm,
            "home": home,
            "away": away,
            "score": score,
            "finished": finished,
            "section": section,
        })
    matches.sort(key=lambda m: (_kickoff_dt(m) or datetime.max))
    return matches


def _match_key(m: dict) -> str:
    return f"{m['date']}|{m['home']}|{m['away']}"


def _kickoff_dt(m: dict) -> datetime | None:
    date = (m.get("date") or "").strip()
    tm = (m.get("time") or "").strip()
    if not date:
        return None
    fmt = "%Y-%m-%d %H:%M" if tm else "%Y-%m-%d"
    try:
        return datetime.strptime(f"{date} {tm}".strip(), fmt)
    except ValueError:
        return None


def find_current_match(schedule: list[dict]) -> dict | None:
    """当前监控对象 = 第一场未回填赛果的真实球队比赛。"""
    for m in schedule:  # schedule 已按开赛时间排序
        if not m["finished"]:
            return m
    return None


# ----------------------------- 核心刷新 -----------------------------

def _matches_on_date(schedule: list[dict], date: str) -> list[dict]:
    return [m for m in schedule if m["date"] == date.strip()]


def _run_generator(date: str, matches: list[dict]) -> None:
    """复用网页生成器：逐场重跑 predict-match（抓澳客最新盘口+落库）+ 重渲染网页。"""
    cmd = [sys.executable, str(GENERATOR), "--date", date]
    for m in matches:
        spec = f"{m['home']},{m['away']}"
        if m.get("time"):
            spec += f",{m['time']}"
        cmd += ["--match", spec]
    log(f"刷新 {date} 共 {len(matches)} 场："
        + " / ".join(f"{m['home']}vs{m['away']}" for m in matches))
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if proc.returncode != 0:
        raise RuntimeError(f"网页生成器失败（{date}）returncode={proc.returncode}")


def refresh_date_from_schedule(date: str, schedule: list[dict] | None = None) -> int:
    """刷新某日期全部真实球队比赛并重生成网页。返回刷新场数。"""
    schedule = schedule if schedule is not None else parse_schedule()
    todays = _matches_on_date(schedule, date)
    if not todays:
        log(f"跳过 {date}：赛程中无真实球队比赛")
        return 0
    todays.sort(key=lambda m: (m.get("time") or ""))
    _run_generator(date, todays)
    return len(todays)


def auto_sync_results() -> bool:
    """调用正式结果闭环 auto-sync-results 自动回填已完赛赛果。
    返回是否有比赛被新回填（用于决定是否触发网页重生成）。
    """
    cmd = [sys.executable, "prediction_system.py", "auto-sync-results",
           "--limit", str(SYNC_LIMIT), "--json"]
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"自动回填赛果调用失败：{exc}")
        return False
    if proc.returncode != 0:
        log(f"自动回填赛果返回非零（returncode={proc.returncode}）")
        return False
    out = proc.stdout or ""
    idx = out.find("{")
    if idx < 0:
        return False
    try:
        payload = json.loads(out[idx:])
    except json.JSONDecodeError:
        return False
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    synced_n = data.get("updated_count")
    if synced_n is None:
        updates = data.get("updates") or []
        synced_n = sum(1 for u in updates if isinstance(u, dict) and u.get("updated"))
    try:
        synced_n = int(synced_n)
    except (TypeError, ValueError):
        synced_n = 0
    if synced_n > 0:
        log(f"✓ 自动回填赛果 {synced_n} 场")
    return synced_n > 0


# ----------------------------- 触发 / 监控 / 健康 -----------------------------

def evaluate_triggers(schedule: list[dict], state: dict, now: datetime) -> list[str]:
    """返回需要重生成网页的日期列表（去重），并就地更新触发标记。"""
    due: set[str] = set()

    today = now.strftime("%Y-%m-%d")
    evening_at = now.replace(hour=EVENING_HOUR, minute=EVENING_MINUTE,
                             second=0, microsecond=0)
    if now >= evening_at and state.get("last_evening_refresh") != today:
        window_end = now + timedelta(hours=EVENING_WINDOW_HOURS)
        for m in schedule:
            if m["finished"]:
                continue
            ko = _kickoff_dt(m)
            # 只验证「当天这批」：未来窗口内即将开赛的比赛
            if ko is not None and now <= ko <= window_end:
                due.add(m["date"])
        state["last_evening_refresh"] = today

    done = set(state.get("pre_match_done", []))
    for m in schedule:
        if m["finished"]:
            continue
        key = _match_key(m)
        if key in done:
            continue
        ko = _kickoff_dt(m)
        if ko is None:
            continue
        if ko - timedelta(minutes=PRE_MATCH_MINUTES) <= now < ko:
            due.add(m["date"])
            done.add(key)
    state["pre_match_done"] = sorted(done)
    return sorted(due)


def update_monitor(schedule: list[dict], state: dict, now: datetime) -> dict | None:
    """更新当前监控对象；若上一场已结束则自动切换并记录日志。返回当前监控比赛。"""
    cur = find_current_match(schedule)
    cur_key = _match_key(cur) if cur else None
    prev_key = state["monitor"].get("current")
    if cur_key != prev_key:
        if prev_key:
            prev = next((m for m in schedule if _match_key(m) == prev_key), None)
            if prev and prev["finished"]:
                log(f"✓ {prev['home']} vs {prev['away']} 已结束（{prev['score']}），"
                    f"监控自动切换到 "
                    + (f"{cur['home']} vs {cur['away']}" if cur else "（无后续比赛）"))
        state["monitor"]["current"] = cur_key
        state["monitor"]["since"] = now.isoformat()
        state["fail_count"] = 0
    return cur


def check_health(schedule: list[dict], state: dict, now: datetime, cur: dict | None) -> None:
    """健康检查：赛果久未回填 / 刷新连续失败 => 通知提示重启。"""
    if cur is not None:
        ko = _kickoff_dt(cur)
        if ko is not None and now > ko + timedelta(hours=STALE_AFTER_HOURS):
            alert(
                state, "stale_result", "赛果未回填，监控可能卡住",
                f"{cur['home']} vs {cur['away']}（{cur['date']} {cur['time']}）"
                f"已开赛超过 {STALE_AFTER_HOURS} 小时但比分仍为 '-'。"
                f"请回填赛果或重启定时器。",
            )
    if state.get("fail_count", 0) >= MAX_FAILS:
        alert(
            state, "refresh_fail", "定时器刷新连续失败",
            f"预测刷新已连续失败 {state['fail_count']} 次，监控可能异常，请重启定时器。",
        )


def run_cycle(now: datetime, *, do_refresh: bool = True) -> tuple[list[str], dict | None]:
    """跑一轮：解析赛程 -> 触发 -> 监控切换 -> 刷新 -> 健康检查。"""
    state = load_state()
    try:
        schedule = parse_schedule()
    except (OSError, ValueError) as exc:
        alert(state, "schedule_parse", "赛程读取失败",
              f"无法解析 {TEAMS_MD.name}：{exc}。请检查赛程文件或重启定时器。")
        save_state(state)
        return [], None

    if not schedule:
        alert(state, "schedule_empty", "赛程为空",
              "未从赛程表解析到任何真实球队比赛，请检查 teams_2026.md。")
        save_state(state)
        return [], None

    # 已开赛但未回填的比赛 => 自动尝试回填赛果，回填成功后把对应日期并入刷新集合，
    # 使「赛果 + 终场比分 + 最新大小球/欧赔/亚盘水位」自动刷上网页。
    newly_finished_dates: set[str] = set()
    if do_refresh:
        kicked_unfinished = [
            m for m in schedule
            if not m["finished"] and (_kickoff_dt(m) is not None) and now >= _kickoff_dt(m)
        ]
        if kicked_unfinished:
            finished_before = {_match_key(m) for m in schedule if m["finished"]}
            try:
                if auto_sync_results():
                    schedule = parse_schedule()
                    for m in schedule:
                        if m["finished"] and _match_key(m) not in finished_before:
                            newly_finished_dates.add(m["date"])
            except (OSError, subprocess.SubprocessError) as exc:
                log(f"自动回填赛果异常：{exc}")

    # 自动剔除：pre_match_done 中已完赛的比赛键移除，保持其仅含「未完赛」的赛前去重项。
    finished_keys = {_match_key(m) for m in schedule if m["finished"]}
    prev_done = state.get("pre_match_done", [])
    pruned = [k for k in prev_done if k not in finished_keys]
    if len(pruned) != len(prev_done):
        log(f"✓ 从 pre_match_done 自动剔除 {len(prev_done) - len(pruned)} 场已完赛比赛")
        state["pre_match_done"] = sorted(pruned)

    cur = update_monitor(schedule, state, now)
    due = set(evaluate_triggers(schedule, state, now))
    due |= newly_finished_dates
    due = sorted(due)

    if do_refresh:
        for date in due:
            try:
                refresh_date_from_schedule(date, schedule)
                state["fail_count"] = 0
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                state["fail_count"] = state.get("fail_count", 0) + 1
                log(f"错误：{date} 刷新失败（连续 {state['fail_count']} 次）：{exc}")

    check_health(schedule, state, now, cur)
    save_state(state)
    return due, cur


# ----------------------------- 子命令 -----------------------------

def cmd_schedule(args) -> None:
    schedule = parse_schedule()
    cur = find_current_match(schedule)
    cur_key = _match_key(cur) if cur else None
    print(f"真实球队比赛共 {len(schedule)} 场：")
    for m in schedule:
        mark = "▶ 监控中" if _match_key(m) == cur_key else (
            "✓" if m["finished"] else " ")
        print(f"  [{m['section']}] {m['date']} {m['time']}  "
              f"{m['home']} {m['score']} {m['away']}  {mark}")


def cmd_current(args) -> None:
    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now()
    schedule = parse_schedule()
    cur = find_current_match(schedule)
    if not cur:
        print("当前无待监控比赛（所有真实球队比赛已结束或无赛程）。")
        return
    ko = _kickoff_dt(cur)
    print(f"当前监控：{cur['home']} vs {cur['away']}  "
          f"（{cur['section']} {cur['date']} {cur['time']}）")
    if ko:
        if now < ko:
            print(f"  距开赛 {str(ko - now).split('.')[0]}")
        elif now > ko + timedelta(hours=STALE_AFTER_HOURS):
            print(f"  ⚠ 已开赛超 {STALE_AFTER_HOURS} 小时仍无赛果，疑似卡住，建议重启定时器")
        else:
            print("  进行中 / 待回填赛果")


def cmd_refresh(args) -> None:
    """其他 agent 主调用入口：刷新某场所在日期的网页（保证页面完整）。"""
    schedule = parse_schedule()
    matches = _matches_on_date(schedule, args.date)
    if args.home and args.away:
        key = f"{args.date.strip()}|{args.home.strip()}|{args.away.strip()}"
        if not any(_match_key(m) == key for m in matches):
            matches.append({
                "date": args.date.strip(),
                "time": (args.time or "").strip(),
                "home": args.home.strip(),
                "away": args.away.strip(),
            })
    if not matches:
        log(f"跳过 {args.date}：赛程与入参均无可刷新比赛")
        return
    matches.sort(key=lambda m: (m.get("time") or ""))
    _run_generator(args.date, matches)


def cmd_refresh_date(args) -> None:
    refresh_date_from_schedule(args.date)


def cmd_run_once(args) -> None:
    now = datetime.strptime(args.now, "%Y-%m-%d %H:%M") if args.now else datetime.now()
    due, cur = run_cycle(now, do_refresh=not args.dry_run)
    if cur:
        log(f"当前监控：{cur['home']} vs {cur['away']}（{cur['date']} {cur['time']}）")
    if not due:
        log(f"[{now:%Y-%m-%d %H:%M}] 无到点刷新")
    elif args.dry_run:
        log(f"[dry-run] 到点日期：{', '.join(due)}")


def cmd_daemon(args) -> None:
    interval = max(1, int(args.interval_minutes))
    cycles = 0
    log(f"守护启动：每 {interval} 分钟轮询；赛程驱动监控 + 21:00 / 赛前1小时刷新")
    while True:
        try:
            run_cycle(datetime.now(), do_refresh=True)
        except Exception as exc:  # 守护进程不因单次异常退出
            log(f"错误：本轮异常：{exc}")
            try:
                st = load_state()
                alert(st, "cycle_crash", "定时器轮询异常",
                      f"本轮监控异常：{exc}。请重启定时器。")
                save_state(st)
            except Exception:
                pass
        cycles += 1
        if args.max_cycles and cycles >= args.max_cycles:
            log(f"守护达到 max_cycles={args.max_cycles}，退出")
            break
        time.sleep(interval * 60)


# ----------------------------- launchd 开机自启 -----------------------------

def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _build_plist(interval_minutes: int) -> str:
    out_log = PRED_DIR / "launchd.out.log"
    err_log = PRED_DIR / "launchd.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{Path(__file__).resolve()}</string>
        <string>daemon</string>
        <string>--interval-minutes</string>
        <string>{interval_minutes}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{PROJECT_ROOT}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{out_log}</string>
    <key>StandardErrorPath</key>
    <string>{err_log}</string>
</dict>
</plist>
"""


def cmd_install_launchd(args) -> None:
    interval = max(1, int(args.interval_minutes))
    plist_path = _launchd_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(_build_plist(interval), encoding="utf-8")
    log(f"已写入 LaunchAgent：{plist_path}")
    # 先卸载旧的（忽略不存在的报错），再加载
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   check=False, capture_output=True)
    proc = subprocess.run(["launchctl", "load", str(plist_path)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        log(f"⚠ launchctl load 失败：{proc.stderr.strip()}")
        print("请手动执行： launchctl load " + str(plist_path))
        return
    log(f"✓ 开机自启已启用（{LAUNCHD_LABEL}），重启后 daemon 会随登录自动运行。")
    print("查看状态： launchctl list | grep " + LAUNCHD_LABEL)


def cmd_uninstall_launchd(args) -> None:
    plist_path = _launchd_plist_path()
    if not plist_path.exists():
        print("未安装开机自启（plist 不存在）。")
        return
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   check=False, capture_output=True)
    plist_path.unlink()
    log(f"✓ 已注销开机自启并删除 {plist_path}")


# ----------------------------- 入口 -----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="世界杯预测定时器 / 赛程驱动监控")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sch = sub.add_parser("schedule", help="打印解析出的真实球队赛程")
    p_sch.set_defaults(func=cmd_schedule)

    p_cur = sub.add_parser("current", help="打印当前监控比赛 + 健康状态")
    p_cur.add_argument("--now", help="模拟当前时间 'YYYY-MM-DD HH:MM'（测试用）")
    p_cur.set_defaults(func=cmd_current)

    p_ref = sub.add_parser("refresh", help="刷新某场所在日期网页（自动补全完整页面）")
    p_ref.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    p_ref.add_argument("--home", help="主队名称（可选，赛程外比赛时提供）")
    p_ref.add_argument("--away", help="客队名称（可选）")
    p_ref.add_argument("--time", help="开赛时间 HH:MM（可选）")
    p_ref.set_defaults(func=cmd_refresh)

    p_refd = sub.add_parser("refresh-date", help="刷新某日期全部真实球队比赛")
    p_refd.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    p_refd.set_defaults(func=cmd_refresh_date)

    p_run = sub.add_parser("run-once", help="按赛程检查一次触发/监控/健康")
    p_run.add_argument("--now", help="模拟当前时间 'YYYY-MM-DD HH:MM'（测试用）")
    p_run.add_argument("--dry-run", action="store_true", help="只判断不实际刷新")
    p_run.set_defaults(func=cmd_run_once)

    p_dae = sub.add_parser("daemon", help="常驻守护进程")
    p_dae.add_argument("--interval-minutes", type=int, default=10, help="轮询间隔分钟，默认 10")
    p_dae.add_argument("--max-cycles", type=int, default=0, help="最大轮询次数，0=永久")
    p_dae.set_defaults(func=cmd_daemon)

    p_ins = sub.add_parser("install-launchd", help="注册 macOS 开机自启")
    p_ins.add_argument("--interval-minutes", type=int, default=10, help="守护轮询间隔分钟")
    p_ins.set_defaults(func=cmd_install_launchd)

    p_uns = sub.add_parser("uninstall-launchd", help="注销开机自启")
    p_uns.set_defaults(func=cmd_uninstall_launchd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

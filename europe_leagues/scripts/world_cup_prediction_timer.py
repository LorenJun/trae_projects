#!/usr/bin/env python3
"""世界杯预测定时器 / 赛程驱动监控 + 可调用刷新接口。

赛程唯一来源（SoT）：`world_cup/teams_2026.md`。定时器直接读取其中的赛程表，
监控「下一场未开赛/进行中的真实球队比赛」，当该场比分从 `-` 变成真实比分
（赛果回填）后，自动切换到下一场继续监控，依次类推到赛事结束。

定时触发口径：
  1) 每天 21:00 触发一次：预测「隔天」全部世界杯比赛，重生成整页 HTML 并推送截图。
  2) 每场比赛开赛前 1 小时自动触发一次：捞最新盘口数据、重跑预测，重生成整页 HTML 并推送截图。
  3) 赛后自动回填只更新赛果/网页，不推送飞书截图；其他时间点不推送。

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

飞书图片推送说明：webhook 图片消息只能发送 image_key；脚本会先把截图上传到
飞书开放平台换取 image_key，再调用 webhook。请在运行环境中设置
LARK_TENANT_ACCESS_TOKEN，或设置 LARK_APP_ID/LARK_APP_SECRET 让脚本自动换 token。

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
import os
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
PRE_MATCH_MINUTES = 60     # 赛前 1 小时触发
STALE_AFTER_HOURS = 3      # 开赛 N 小时后仍无赛果 => 监控疑似卡住
MAX_FAILS = 3              # 刷新连续失败次数阈值 => 提示重启
ALERT_COOLDOWN_MINUTES = 30  # 同类告警最短间隔，避免刷屏
SYNC_LIMIT = 10            # 每轮自动回填赛果的最大尝试场数
SCREENSHOT_WIDTH = 1100    # 预测页截图宽度，和网页 max-width 保持匹配
SCREENSHOT_HEIGHT = 900
PUSH_DEDUPE_MINUTES = 20   # 同一日期短时间重复刷新时，只推送一次截图

DEFAULT_LARK_WEBHOOK_URL = (
    "https://open.larkoffice.com/open-apis/bot/v2/hook/"
    "225e2409-8f3b-42b2-83c8-b3ea20ac3077"
)
LARK_TENANT_TOKEN_URL = "https://open.larkoffice.com/open-apis/auth/v3/tenant_access_token/internal"
LARK_IMAGE_UPLOAD_URL = "https://open.larkoffice.com/open-apis/im/v1/images"

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


def _json_http_post(url: str, payload: dict, *, headers: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    """stdlib 版 JSON POST，避免给守护进程额外引入 requests 运行时依赖。"""
    from urllib import error, request

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


def _multipart_http_post(url: str, fields: dict[str, str], files: dict[str, Path], *, headers: dict | None = None, timeout: int = 60) -> tuple[int, str]:
    """stdlib 版 multipart/form-data POST，用于飞书图片上传。"""
    from urllib import error, request
    import mimetypes
    import uuid

    boundary = f"----wc-timer-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for name, path in files.items():
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {ctype}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    req = request.Request(
        url,
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", **(headers or {})},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", errors="replace")


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
        "pre_match_done_schema": 2,
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
    if int(data.get("pre_match_done_schema") or 0) < 2:
        # 旧版曾可能把未来比赛过早写入 pre_match_done。升级后清空，按新逻辑重新布防。
        base["pre_match_done"] = []
        base["pre_match_done_schema"] = 2
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


def _html_path(date: str) -> Path:
    return PRED_DIR / f"{date}_predictions.html"


def _screenshot_path(date: str) -> Path:
    return PRED_DIR / f"{date}_predictions.png"


def _push_lock_path(date: str) -> Path:
    return PRED_DIR / f".{date}.push.lock"


def _push_marker_path(date: str) -> Path:
    return PRED_DIR / f".{date}.last_push.json"


def _recently_pushed(date: str, now: datetime) -> bool:
    """判断同一日期是否刚刚推送过，避免手工补跑与 daemon 同时触发导致重复飞书图片。"""
    marker = _push_marker_path(date)
    if not marker.exists():
        return False
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        last = datetime.fromisoformat(str(data.get("pushed_at") or ""))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return now - last < timedelta(minutes=PUSH_DEDUPE_MINUTES)


def _mark_pushed(date: str, now: datetime) -> None:
    marker = _push_marker_path(date)
    marker.write_text(
        json.dumps({"date": date, "pushed_at": now.isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def capture_html_screenshot(date: str) -> Path:
    """把每日预测 HTML 截成整页 PNG。"""
    html_path = _html_path(date)
    if not html_path.exists():
        raise FileNotFoundError(f"预测 HTML 不存在：{html_path}")
    screenshot_path = _screenshot_path(date)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 playwright，无法截图；请先执行 `pip install -r requirements.txt` "
            "和 `python3 -m playwright install chromium`。"
        ) from exc

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": SCREENSHOT_WIDTH, "height": SCREENSHOT_HEIGHT}, device_scale_factor=2)
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.screenshot(path=str(screenshot_path), full_page=True)
        finally:
            browser.close()
    log(f"✓ 已生成整页截图：{screenshot_path}")
    return screenshot_path


def _get_lark_tenant_access_token() -> str | None:
    """读取或用 app_id/app_secret 换取 tenant_access_token，用于上传图片取得 image_key。"""
    token = os.environ.get("LARK_TENANT_ACCESS_TOKEN") or os.environ.get("FEISHU_TENANT_ACCESS_TOKEN")
    if token:
        return token
    app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        return None
    status, text = _json_http_post(
        LARK_TENANT_TOKEN_URL,
        {"app_id": app_id, "app_secret": app_secret},
        timeout=30,
    )
    if status >= 300:
        raise RuntimeError(f"飞书 tenant_access_token 获取失败 HTTP {status}: {text[:500]}")
    data = json.loads(text or "{}")
    if data.get("code") != 0 or not data.get("tenant_access_token"):
        raise RuntimeError(f"飞书 tenant_access_token 获取失败：{text[:500]}")
    return str(data["tenant_access_token"])


def _upload_lark_image(image_path: Path) -> str:
    """上传截图到飞书开放平台，返回 webhook 图片消息需要的 image_key。"""
    token = _get_lark_tenant_access_token()
    if not token:
        raise RuntimeError(
            "飞书 webhook 发送图片需要 image_key；请设置 LARK_TENANT_ACCESS_TOKEN，"
            "或设置 LARK_APP_ID/LARK_APP_SECRET 供脚本自动上传图片换取 image_key。"
        )
    status, text = _multipart_http_post(
        LARK_IMAGE_UPLOAD_URL,
        {"image_type": "message"},
        {"image": image_path},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if status >= 300:
        raise RuntimeError(f"飞书图片上传失败 HTTP {status}: {text[:500]}")
    data = json.loads(text or "{}")
    image_key = ((data.get("data") or {}).get("image_key")) if isinstance(data, dict) else None
    if data.get("code") != 0 or not image_key:
        raise RuntimeError(f"飞书图片上传失败：{text[:500]}")
    return str(image_key)


def send_lark_screenshot(date: str, screenshot_path: Path, *, webhook_url: str | None = None) -> bool:
    """通过飞书 / Lark webhook 推送整页 HTML 截图。"""
    webhook = webhook_url or os.environ.get("WORLD_CUP_LARK_WEBHOOK_URL") or os.environ.get("LARK_WEBHOOK_URL") or DEFAULT_LARK_WEBHOOK_URL
    image_key = _upload_lark_image(screenshot_path)
    payload = {"msg_type": "image", "content": {"image_key": image_key}}
    status, text = _json_http_post(webhook, payload, timeout=30)
    if status >= 300:
        raise RuntimeError(f"飞书 webhook 推送失败 HTTP {status}: {text[:500]}")
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        data = {}
    if data and data.get("code") not in (0, None):
        raise RuntimeError(f"飞书 webhook 推送失败：{text[:500]}")
    log(f"✓ 已通过飞书 webhook 推送 {date} 整页截图")
    return True


def push_prediction_page(date: str) -> None:
    """刷新后统一执行：HTML -> 整页截图 -> webhook 推送。"""
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _push_lock_path(date)
    with lock_path.open("w", encoding="utf-8") as lock_fh:
        try:
            import fcntl

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except (ImportError, OSError):
            # macOS 正常有 fcntl；若在其他平台不可用，仍继续执行，至少保留时间去重。
            pass
        now = datetime.now()
        if _recently_pushed(date, now):
            log(f"跳过 {date} 飞书截图推送：{PUSH_DEDUPE_MINUTES} 分钟内已推送过")
            return
        screenshot = capture_html_screenshot(date)
        send_lark_screenshot(date, screenshot)
        _mark_pushed(date, now)


def refresh_date_from_schedule(date: str, schedule: list[dict] | None = None, *, push: bool = True) -> int:
    """刷新某日期全部真实球队比赛并重生成网页。返回刷新场数。

    push=True 仅用于 21:00 隔天预测、赛前 1 小时临场刷新、以及人工 refresh/refresh-date。
    daemon 里的赛后自动回填刷新必须传 push=False，避免比赛结束后仍向飞书发截图。
    """
    schedule = schedule if schedule is not None else parse_schedule()
    todays = _matches_on_date(schedule, date)
    if not todays:
        log(f"跳过 {date}：赛程中无真实球队比赛")
        return 0
    todays.sort(key=lambda m: (m.get("time") or ""))
    _run_generator(date, todays)
    if push:
        push_prediction_page(date)
    else:
        log(f"✓ {date} 已刷新网页；本次为赛后自动回填，不推送飞书截图")
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
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    evening_at = now.replace(hour=EVENING_HOUR, minute=EVENING_MINUTE,
                             second=0, microsecond=0)
    if now >= evening_at and state.get("last_evening_refresh") != today:
        # 用户口径：每天晚上 21:00 预测「隔天」的世界杯比赛。
        if any((not m["finished"]) and m["date"] == tomorrow for m in schedule):
            due.add(tomorrow)
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


def sanitize_pre_match_done(schedule: list[dict], state: dict, now: datetime) -> None:
    """清理历史状态中被过早写入的赛前刷新标记。

    只保留：
    - 未完赛且已进入「赛前 1 小时」窗口/已开赛的比赛；
    - 这样既避免重复推送，也能把历史误写入的未来比赛重新布防。
    """
    by_key = {_match_key(m): m for m in schedule}
    prev = list(state.get("pre_match_done", []))
    kept: list[str] = []
    removed = 0
    for key in prev:
        m = by_key.get(key)
        if not m or m.get("finished"):
            removed += 1
            continue
        ko = _kickoff_dt(m)
        if ko is not None and now < ko - timedelta(minutes=PRE_MATCH_MINUTES):
            # 历史状态里过早写入的未来比赛，删除后让它到点重新触发。
            removed += 1
            continue
        kept.append(key)
    if removed:
        log(f"✓ 清理 pre_match_done 过期/过早标记 {removed} 条")
    state["pre_match_done"] = sorted(set(kept))


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

    # 已开赛但未回填的比赛 => 自动尝试回填赛果，回填成功后只把对应日期并入刷新集合，
    # 使「赛果 + 终场比分」刷上网页；这类赛后刷新不进入 push_due，不推送飞书截图。
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

    sanitize_pre_match_done(schedule, state, now)

    cur = update_monitor(schedule, state, now)
    push_due = set(evaluate_triggers(schedule, state, now))
    refresh_due = sorted(push_due | newly_finished_dates)

    if do_refresh:
        for date in refresh_due:
            try:
                refresh_date_from_schedule(date, schedule, push=date in push_due)
                state["fail_count"] = 0
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                state["fail_count"] = state.get("fail_count", 0) + 1
                log(f"错误：{date} 刷新失败（连续 {state['fail_count']} 次）：{exc}")

    if do_refresh:
        check_health(schedule, state, now, cur)
        save_state(state)
    return refresh_due, cur


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
    push_prediction_page(args.date)


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
    log(f"守护启动：每 {interval} 分钟轮询；赛程驱动监控 + 21:00预测隔天 / 赛前1小时刷新 + 截图推送")
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
        sleep_seconds = _seconds_until_next_aligned_tick(datetime.now(), interval)
        log(f"下次轮询将在 {sleep_seconds // 60}分{sleep_seconds % 60}秒后触发（对齐整点/{interval}分钟边界）")
        time.sleep(sleep_seconds)


def _seconds_until_next_aligned_tick(now: datetime, interval_minutes: int) -> int:
    """返回距离下一个对齐轮询点的秒数。

    例如 interval=30 时，轮询点固定为 xx:00 / xx:30，而不是按守护进程启动时间滚动。
    这样每天 21:00 能准点检查，不会因为 17:15 启动而变成 21:15 才触发。
    """
    interval_minutes = max(1, min(60, int(interval_minutes)))
    base = now.replace(second=0, microsecond=0)
    minute = base.minute
    next_minute = ((minute // interval_minutes) + 1) * interval_minutes
    if next_minute >= 60:
        target = (base.replace(minute=0) + timedelta(hours=1))
    else:
        target = base.replace(minute=next_minute)
    return max(1, int((target - now).total_seconds()))


# ----------------------------- launchd 开机自启 -----------------------------

def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _xml_escape(value: str) -> str:
    return (value.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;")
                 .replace("'", "&apos;"))


def _launchd_environment_xml() -> str:
    """把当前 shell 中与飞书推送相关的环境变量写入 launchd，避免开机自启后丢失凭据。"""
    keys = [
        "WORLD_CUP_LARK_WEBHOOK_URL",
        "LARK_WEBHOOK_URL",
        "LARK_TENANT_ACCESS_TOKEN",
        "FEISHU_TENANT_ACCESS_TOKEN",
        "LARK_APP_ID",
        "FEISHU_APP_ID",
        "LARK_APP_SECRET",
        "FEISHU_APP_SECRET",
    ]
    pairs = [(k, os.environ.get(k, "")) for k in keys if os.environ.get(k)]
    if not pairs:
        return ""
    lines = ["    <key>EnvironmentVariables</key>", "    <dict>"]
    for key, value in pairs:
        lines.append(f"        <key>{_xml_escape(key)}</key>")
        lines.append(f"        <string>{_xml_escape(value)}</string>")
    lines.append("    </dict>")
    return "\n".join(lines) + "\n"


def _build_plist(interval_minutes: int) -> str:
    out_log = PRED_DIR / "launchd.out.log"
    err_log = PRED_DIR / "launchd.err.log"
    env_xml = _launchd_environment_xml()
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
{env_xml}    <key>StandardOutPath</key>
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

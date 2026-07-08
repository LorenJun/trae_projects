"""模块说明：走"按日期"通道刷新世界杯赛程。

正式通道调 `okooo_fetch_daily_schedule.py`（本机 Chrome CDP + 移动端画像 +
`m.okooo.com/saishi/16/` 每日切换），一次一天，每天落一份
`.okooo-scraper/schedules/world_cup/{date}.json`，包含
`{match_id, kickoff_time, home_team, away_team, status, score}`。

对比之前的"占位符级联映射"方案：
- 不再依赖 SoT 里预写的 W##/L## 占位符 + 90 分钟/加时/点球胜者裁决；
- 直接以澳客当天赛程为准，MatchID + 真实主客队一次落地，SoT 只做上层持久化；
- 已完赛日抓到会顺带带回比分，`apply_schedule_updates` 按既有规则安全回填。

入口：
  - `fetch_world_cup_schedule_for_dates(dates)`：按传入日期列表跑，逐日 subprocess，返回 MatchRecord[]。
  - `fetch_world_cup_schedule(months)`：向后兼容旧 daemon 签名，忽略 months，
    默认抓 `[today, today+1, today+2]`。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


class ScheduleFetchBlocked(RuntimeError):
    """澳客风控 / 网络失败导致全部日期抓取失败。"""


@dataclass
class MatchRecord:
    match_id: str
    date: str
    time: str
    home: str
    away: str
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    stage: str = ""
    section: str = ""
    raw_text: str = ""
    history_url: str = ""
    finished: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "date": self.date,
            "time": self.time,
            "home": self.home,
            "away": self.away,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "stage": self.stage,
            "section": self.section,
            "raw_text": self.raw_text,
            "history_url": self.history_url,
            "finished": self.finished,
        }


@dataclass
class ScheduleFetchReport:
    records: List[MatchRecord] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _schedules_dir() -> Path:
    return _project_root() / ".okooo-scraper" / "schedules" / "world_cup"


def _detect_stage(raw_text: str) -> str:
    if not raw_text:
        return ""
    for stage in ("1/16决赛", "1/8决赛", "1/4决赛", "半决赛", "季军赛", "决赛"):
        if stage in raw_text:
            return stage
    return ""


def _record_from_row(date_str: str, row: Dict[str, Any]) -> Optional[MatchRecord]:
    mid = str(row.get("match_id") or "").strip()
    home = str(row.get("home_team") or "").strip()
    away = str(row.get("away_team") or "").strip()
    if not mid or not home or not away:
        return None
    tm = str(row.get("kickoff_time") or "").strip()
    stage = _detect_stage(str(row.get("raw_text") or ""))
    finished = str(row.get("status") or "") == "已结束"
    hs = row.get("home_score")
    as_ = row.get("away_score")
    return MatchRecord(
        match_id=mid,
        date=date_str,
        time=tm,
        home=home,
        away=away,
        home_score=int(hs) if isinstance(hs, int) else None,
        away_score=int(as_) if isinstance(as_, int) else None,
        stage=stage,
        section=stage,
        raw_text=str(row.get("raw_text") or ""),
        history_url=str(row.get("history_url") or ""),
        finished=finished,
    )


def _fetch_one_date(date_str: str, *, timeout_seconds: int = 180) -> Tuple[List[MatchRecord], Optional[str]]:
    """跑 okooo_fetch_daily_schedule.py subprocess 抓一天赛程；返回 (records, warning)。"""
    script = _project_root() / "okooo_fetch_daily_schedule.py"
    if not script.exists():
        return [], f"抓取脚本缺失：{script}"

    cmd = [
        sys.executable,
        "-u",
        str(script),
        "--league",
        "世界杯",
        "--date",
        date_str,
        "--driver",
        "local-chrome",
    ]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(_project_root()),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return [], f"date={date_str} subprocess 超时 {timeout_seconds}s"
    except OSError as exc:
        return [], f"date={date_str} subprocess 启动失败：{exc}"

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[-400:]
        return [], f"date={date_str} exit={completed.returncode} err={stderr}"

    out_path = _schedules_dir() / f"{date_str}.json"
    if not out_path.exists():
        return [], f"date={date_str} 未生成 {out_path.name}"

    try:
        payload = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"date={date_str} JSON 解析失败：{exc}"

    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not matches:
        return [], f"date={date_str} 抓到 0 条比赛"

    records: List[MatchRecord] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        rec = _record_from_row(date_str, row)
        if rec is not None:
            records.append(rec)
    return records, None


def fetch_world_cup_schedule_for_dates(
    dates: Sequence[str],
    *,
    sleep_between: float = 1.2,
) -> ScheduleFetchReport:
    """按日期列表逐日抓取，返回 MatchRecord + 每日 warning。"""
    report = ScheduleFetchReport()
    normalized: List[str] = []
    for d in dates:
        s = str(d or "").strip()
        if not _DATE_RE.match(s):
            report.warnings.append(f"skip invalid date: {d!r}")
            continue
        normalized.append(s)

    for idx, d in enumerate(normalized):
        recs, warn = _fetch_one_date(d)
        report.records.extend(recs)
        if warn:
            report.warnings.append(warn)
        if idx < len(normalized) - 1 and sleep_between > 0:
            time.sleep(sleep_between)

    if normalized and not report.records:
        raise ScheduleFetchBlocked(
            f"全部 {len(normalized)} 天抓取失败；首条 warning：{report.warnings[0] if report.warnings else 'unknown'}"
        )
    return report


def _default_date_window(reference: Optional[datetime] = None) -> List[str]:
    """默认抓 [today .. today+13]（14 天）。北京时间下 today 由 daemon 侧传入。

    覆盖范围要够到淘汰赛全轮：1/8→1/4→半决→季军→决赛间隔最长约 13 天，
    只抓 3 天窗口会漏掉 1/4 决赛及之后新亮出的对阵（澳客定档后 SoT 永远补不上）。
    """
    ref = reference or datetime.now()
    base: date = ref.date()
    return [(base + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(14)]


def fetch_world_cup_schedule(months: Iterable[Any] | None = None) -> List[MatchRecord]:
    """向后兼容旧 daemon 签名：忽略 months，抓 [today .. today+13]（14 天）。"""
    _ = months  # 保留位置参数，实际使用日期窗口
    report = fetch_world_cup_schedule_for_dates(_default_date_window())
    return report.records


__all__ = [
    "MatchRecord",
    "ScheduleFetchBlocked",
    "ScheduleFetchReport",
    "fetch_world_cup_schedule",
    "fetch_world_cup_schedule_for_dates",
]

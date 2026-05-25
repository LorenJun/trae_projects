#!/usr/bin/env python3
"""模块说明：抓取澳客指定日期赛程并生成可复用的日程数据文件。

Fetch a single day's schedule from okooo mobile league page and save to JSON.

This is designed for the "daily schedule only" use case:
  - Find all matches on a given date (e.g. 2026-04-22) for a league (e.g. 西甲)
  - Extract MatchID + teams + kickoff time + history url
  - Save under europe_leagues/.okooo-scraper/schedules/<league_code>/YYYY-MM-DD.json

It reuses local-chrome CDP utilities from okooo_save_snapshot.py."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List


def _league_code(league_cn: str) -> str:
    mapping = {
        "英超": "premier_league",
        "西甲": "la_liga",
        "意甲": "serie_a",
        "德甲": "bundesliga",
        "法甲": "ligue_1",
    }
    return mapping.get((league_cn or "").strip(), "other")


def _date_tokens(date_yyyy_mm_dd: str) -> List[str]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", (date_yyyy_mm_dd or "").strip())
    if not m:
        return []
    _y, mm, dd = m.group(1), m.group(2), m.group(3)
    return [f"{mm}-{dd}", f"{int(mm)}-{int(dd)}"]


def _parse_row_text(text: str) -> Dict[str, Any]:
    """Best-effort parse from a schedule row like: '第33轮 毕尔巴鄂 01:00 奥萨苏纳' or '完 毕尔巴鄂 2-1 奥萨苏纳'."""
    s = (text or "").strip()
    s = re.sub(r"\s+", " ", s)
    result = {"raw_text": s}
    
    # Check if status is "完"
    has_finish_marker = "完" in s
    # 只有明确带"完"的行才尝试解析比分，避免把 20:00 / 21:30 / 22:15 误当成赛果。
    score_match = re.search(r"(\d{1,2})\s*[-:]\s*(\d{1,2})", s) if has_finish_marker else None
    if score_match:
        home_score = int(score_match.group(1))
        away_score = int(score_match.group(2))
        result["home_score"] = home_score
        result["away_score"] = away_score
        result["score"] = f"{home_score}-{away_score}"
        result["status"] = "已结束"
        # Remove score and finish marker for cleaner team/kickoff extraction
        s_clean = re.sub(r"\b\d{1,2}\s*[-:]\s*\d{1,2}\b", "", s)
        s_clean = re.sub(r"\b完\b", "", s_clean)
    else:
        result["status"] = "已结束" if has_finish_marker else "待进行"
        s_clean = re.sub(r"\b完\b", "", s) if has_finish_marker else s
    
    # Try to find kickoff time
    kickoff_match = re.search(r"(\d{1,2}:\d{2})", s_clean)
    if kickoff_match:
        result["kickoff_time"] = kickoff_match.group(1)
        s_clean = re.sub(r"\b\d{1,2}:\d{2}\b", "", s_clean)
    
    # Remove round marker, 完/进行中 markers, extra spaces, date tokens
    s_clean = re.sub(r"\b\d{4}-\d{1,2}-\d{1,2}\b", "", s_clean)
    s_clean = re.sub(r"^第\d+轮", "", s_clean)
    s_clean = re.sub(r"\b第\d+轮\b", "", s_clean)
    s_clean = re.sub(r"\b(?:完|进行中|未开始)\b", "", s_clean, flags=re.IGNORECASE)
    s_clean = re.sub(r"\b\d{1,2}-\d{1,2}\b", "", s_clean)
    s_clean = re.sub(
        r"\b(?:盈亏|亚指|欧指|分析|预测|AI|积分|阵容|情报|独家|直播|动画|会员)\b",
        "",
        s_clean,
        flags=re.IGNORECASE,
    )
    s_clean = re.sub(r"\s+", " ", s_clean).strip()
    
    # Split into home and away
    # If we have two teams, split on space and take first and last non-empty
    parts = [p for p in s_clean.split() if p.strip()]
    if len(parts) >= 2:
        result["home_team"] = parts[0]
        result["away_team"] = parts[-1]
        # If more than 2 parts, combine middle parts with either first or last as needed
        if len(parts) > 2:
            # Heuristic: if we have a Chinese team name vs another, try to find the split
            # For simplicity, take first and last as the team names for now
            pass
    elif len(parts) == 1:
        # Only one team found, maybe the other is missing or messed up
        result["home_team"] = parts[0]
    
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="联赛中文名（例如：西甲）")
    parser.add_argument("--date", required=True, help="日期 YYYY-MM-DD（例如：2026-04-22）")
    parser.add_argument("--driver", choices=["local-chrome"], default="local-chrome")
    parser.add_argument("--chrome-port", type=int, default=9222, help="CDP 端口（默认 9222）")
    parser.add_argument(
        "--chrome-path",
        default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        help="Google Chrome 可执行文件路径",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default=str(Path(__file__).resolve().parent / ".okooo-scraper" / "chrome_profile"),
        help="local-chrome 模式启动的独立用户目录（项目内，已 gitignore）",
    )
    args = parser.parse_args()

    # Import from the snapshot script to reuse CDP/session + league url mapping.
    from okooo_save_snapshot import (
        LocalChromeSession,
        REMEN_URL,
        _extract_schedule_rows_for_date_on_current_page,
        _ensure_local_chrome,
        _mobile_league_url,
        _navigate_schedule_to_month,
    )

    league_cn = (args.league or "").strip()
    league_code = _league_code(league_cn)
    league_url = _mobile_league_url(league_cn)
    if not league_url:
        raise SystemExit(f"不支持的联赛: {league_cn}（缺少 mobile 赛程 URL 映射）")

    _ensure_local_chrome(args.chrome_port, args.chrome_path, args.chrome_user_data_dir)
    s = LocalChromeSession(args.chrome_port, session_name=f"schedule_{league_code}")
    try:
        # Prefer human-like navigation: remen -> click league
        s.open(REMEN_URL)
        click_league_js = r"""
(() => {
  const name = %s;
  const els = Array.from(document.querySelectorAll('a,div,span,button,li'));
  const el = els.find(e => ((e.innerText || '').trim() === name));
  if (!el) return JSON.stringify({clicked:false});
  el.click();
  return JSON.stringify({clicked:true});
})()
""" % json.dumps(league_cn, ensure_ascii=False)
        s.eval_json(click_league_js)
        time.sleep(2.0)

        # Fallback: open league schedule URL directly if needed.
        s.open(league_url)

        # Wait for match anchors to appear (mobile schedule sometimes lazy-loads).
        for _ in range(12):
            cnt = s.eval_json(
                "(() => JSON.stringify({n: document.querySelectorAll(\"[href*='MatchID='],[href*='matchid='],[onclick*='MatchID='],[onclick*='matchid='],a[href*='history.php']\").length, blocked: (document.body?.innerText||'').includes('访问被阻断')}))()"
            )
            if isinstance(cnt, dict) and cnt.get("blocked"):
                raise SystemExit("访问被阻断：请确认本地 Chrome 能正常打开澳客页面后再重试")
            if isinstance(cnt, dict) and (cnt.get("n") or 0) > 0:
                break
            # Nudge the page to trigger loading.
            s.eval_json("(() => { window.scrollTo(0, document.body.scrollHeight); return JSON.stringify({ok:true}); })()")
            s.eval_json("(() => { window.scrollTo(0, 0); return JSON.stringify({ok:true}); })()")
            time.sleep(1.0)

        month_nav = {"attempted": False, "matched": False, "reason": "missing_date"}
        if args.date:
            month_nav = _navigate_schedule_to_month(s, args.date, max_steps=18)

        dtoks = _date_tokens(args.date)
        click_result = {
            "clicked": True,
            "mode": "month_section_scan",
            "token": args.date,
        }
        # Allow time for scroll/navigation/render.
        s.eval_json("(() => { return JSON.stringify({href: location.href, scrollY: window.scrollY}); })()")

        raw = _extract_schedule_rows_for_date_on_current_page(s, date_hint=args.date, limit=64)
    finally:
        s.close()

    rows = (raw or {}).get("rows") if isinstance(raw, dict) else None
    if not rows:
        raise SystemExit(f"未抓到赛程行：league={league_cn}, date={args.date}, month_nav={month_nav}, click={click_result}")

    if not isinstance(click_result, dict) or click_result.get("clicked") is not True:
        raise SystemExit(f"日期切换失败：league={league_cn}, date={args.date}, month_nav={month_nav}, click={click_result}")

    matches: List[Dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        item = {"match_id": r.get("mid"), "history_url": r.get("href")}
        item.update(_parse_row_text(r.get("text", "")))
        # Keep all rows, both completed and upcoming (prioritize completed ones for our use case)
        if not item.get("home_team") or not item.get("away_team"):
            continue
        # Filter out obvious other-date rows that embed a date token not matching args.date
        toks = set(_date_tokens(args.date))
        embedded = re.findall(r"\b\d{1,2}-\d{1,2}\b", item.get("raw_text", "") or "")
        if embedded:
            # if a row embeds a date token and it's not our requested date, skip
            if not any(e in toks for e in embedded):
                continue
        matches.append(item)

    out_dir = Path(__file__).resolve().parent / ".okooo-scraper" / "schedules" / league_code
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.date}.json"
    payload = {
        "league": league_cn,
        "league_code": league_code,
        "date": args.date,
        "source_url": league_url,
        "month_navigation": month_nav,
        "date_click": click_result,
        "matches": matches,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()

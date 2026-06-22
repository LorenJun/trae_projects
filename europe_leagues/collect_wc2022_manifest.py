"""Collect the 2022 Qatar World Cup match manifest (out-of-sample).

Reads okooo mobile month pages (https://m.okooo.com/saishi/16-YYYY-MM/) via the
project's local-chrome session and writes a manifest of every match:
MatchID, date, round, home/away team, final score, and history_url.

This is the sample-collection step; per-match odds are pulled separately by
collect_wc2022_odds.py keyed off this manifest.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

import okooo_save_snapshot as oss

MONTHS = ["2022-11", "2022-12"]
OUT_DIR = oss._default_data_root() / "out_of_sample" / "world_cup_2022"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

EXTRACT_JS = r"""
(() => {
  const norm = (s) => String(s||'').replace(/\s+/g,' ').trim();
  const anchors = Array.from(document.querySelectorAll("a[href*='MatchID='],a[href*='matchid=']"));
  const seen = new Set();
  const rows = [];
  for (const el of anchors) {
    const href = el.getAttribute('href')||'';
    const m = href.match(/MatchID=(\d+)/i);
    if (!m) continue;
    const mid = m[1];
    if (seen.has(mid)) continue;
    seen.add(mid);
    const item = el.closest('.item')||el.closest('li')||el.closest('tr')||el.parentElement;
    let dateText = '';
    let node = item;
    for (let hop=0; hop<40 && node; hop++) {
      let p = node.previousElementSibling;
      while (p) {
        const t = norm(p.innerText);
        if (/^\d{4}-\d{1,2}-\d{1,2}/.test(t) && t.length <= 40) { dateText = t; break; }
        p = p.previousElementSibling;
      }
      if (dateText) break;
      node = node.parentElement;
    }
    let abs = href;
    if (abs.startsWith('/')) abs = location.origin + abs;
    rows.push({mid, date_header: dateText, text: norm(item ? item.innerText : el.innerText).slice(0,120), href: abs});
  }
  return JSON.stringify({url: location.href, count: rows.length, rows: rows});
})()
"""


# Most specific stage labels first so "决赛" never matches inside "1/4决赛".
_STAGES = ["1/8决赛", "1/4决赛", "半决赛", "季军赛", "决赛", "小组赛", "淘汰赛"]


def _parse_row(text: str) -> Dict[str, Any]:
    s = re.sub(r"\s+", " ", (text or "").strip())
    out: Dict[str, Any] = {"raw_text": s}
    rm = re.search(r"第(\d+)轮", s)
    if rm:
        out["round"] = f"第{rm.group(1)}轮"
    for kw in _STAGES:
        if kw in s:
            out["stage"] = kw
            break
    # score like "完 0:2" or "0-2"
    sm = re.search(r"(\d{1,2})\s*[:：-]\s*(\d{1,2})", s)
    if sm:
        out["home_score"] = int(sm.group(1))
        out["away_score"] = int(sm.group(2))
        out["score"] = f"{int(sm.group(1))}-{int(sm.group(2))}"
        out["status"] = "已结束"
    # Teams: remove only structural markers (round/stage/status/score/time/group),
    # never single characters that occur inside team names (加/亚/欧/点...).
    c = s
    c = re.sub(r"第\d+轮", " ", c)
    for kw in _STAGES:
        c = c.replace(kw, " ")
    c = re.sub(r"\d{1,2}\s*[:：-]\s*\d{1,2}", " ", c)
    c = re.sub(r"\b\d{1,2}:\d{2}\b", " ", c)
    c = re.sub(r"\b[A-L]组\b", " ", c)
    c = re.sub(r"(完|未开始|进行中|未)", " ", c)
    parts = [p for p in c.split() if p.strip()]
    if len(parts) >= 2:
        out["home_team"] = parts[0]
        out["away_team"] = parts[-1]
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    user_data_dir = str(oss._default_data_root() / "chrome_profile")
    meta = oss._ensure_local_chrome(9222, CHROME_PATH, user_data_dir)
    port = int(meta.get("port") or 9222)

    all_matches: Dict[str, Dict[str, Any]] = {}
    per_month: Dict[str, int] = {}
    for month in MONTHS:
        url = f"https://m.okooo.com/saishi/16-{month}/"
        bu = oss.LocalChromeSession(port=port, session_name=f"wc2022_manifest_{month}")
        try:
            state = oss._open_ready(bu, url, settle_seconds=4.0)
            if oss._is_blocked_text(state):
                raise SystemExit(f"BLOCKED on {url}: {state[:200]}")
            for _ in range(8):
                oss._eval_scroll_to_bottom(bu)
                time.sleep(0.6)
            oss._eval_scroll_to_top(bu)
            time.sleep(0.5)
            res = bu.eval_json(EXTRACT_JS)
        finally:
            bu.close()
        rows = (res or {}).get("rows") or []
        per_month[month] = len(rows)
        for r in rows:
            mid = str(r.get("mid"))
            if not mid:
                continue
            rec: Dict[str, Any] = {
                "match_id": mid,
                "date": r.get("date_header") or "",
                "month": month,
                "history_url": r.get("href") or f"https://m.okooo.com/match/history.php?MatchID={mid}",
            }
            rec.update(_parse_row(r.get("text", "")))
            all_matches[mid] = rec
        print(f"[{month}] rows={len(rows)}")
        time.sleep(1.0)

    matches = sorted(all_matches.values(), key=lambda x: (x.get("date") or "", x.get("match_id")))
    payload = {
        "league": "世界杯",
        "season": "2022",
        "host": "卡塔尔",
        "source": "okooo m.okooo.com/saishi/16-YYYY-MM/",
        "purpose": "out_of_sample",
        "months": MONTHS,
        "per_month_rows": per_month,
        "total_matches": len(matches),
        "matches": matches,
    }
    out_path = OUT_DIR / "manifest.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"TOTAL={len(matches)} -> {out_path}")


if __name__ == "__main__":
    main()

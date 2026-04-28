#!/usr/bin/env python3
"""
Fetch an okooo match snapshot via browser-use (user-like browsing) and save JSON to disk.

Output naming rule:
  赛事名称_时间.json
Example:
  巴黎圣曼vs南特_2026-04-21_23-10-05.json

Notes:
- This script intentionally depends only on `browser-use` CLI (not Playwright) because direct HTTP
  requests are often blocked (405) and some environments don't have Playwright installed.
"""

from __future__ import annotations

import atexit
import argparse
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import requests
from websocket import create_connection

REMEN_URL = "https://m.okooo.com/saishi/remen/"
RETRY_DELAYS = [2.0, 4.0, 6.0]
CLICK_SETTLE_SECONDS = 2.5


def _default_data_root() -> Path:
    """Return a project-relative data directory for scraper artifacts.

    This avoids hardcoding user home paths and keeps paths consistent with
    prediction workflows. The directory is expected to be gitignored.
    """
    return Path(__file__).resolve().parent / ".okooo-scraper"


def _league_slug(league: str) -> str:
    """Normalize league name to a directory-friendly slug.

    For five major leagues we use stable english slugs for easier management.
    """
    name = (league or "").strip()
    mapping = {
        "英超": "premier_league",
        "意甲": "serie_a",
        "西甲": "la_liga",
        "德甲": "bundesliga",
        "法甲": "ligue_1",
    }
    if name in mapping:
        return mapping[name]
    # Fallback for other leagues: keep it readable but safe for filesystem
    return _safe_filename(name).lower() or "other"


def _now_stamp() -> str:
    # Avoid ":" for cross-platform filenames.
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _safe_filename(s: str) -> str:
    s = (s or "").strip()
    s = s.replace(" ", "")
    # Windows/macOS reserved characters
    s = re.sub(r'[<>:"/\\\\|?*]+', "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "snapshot"

def _norm_team_tokens(name: str) -> list[str]:
    """Generate fuzzy-match tokens for a team name as shown on okooo schedule pages."""
    n = (name or "").strip().replace(" ", "")
    if not n:
        return []
    tokens = {n}
    # Common suffixes/words that may be omitted on schedule pages.
    for suf in ["足球俱乐部", "俱乐部", "足球", "队", "FC", "fc", "竞技", "竞技队"]:
        if n.endswith(suf) and len(n) > len(suf):
            tokens.add(n[: -len(suf)])
    # Some teams are commonly abbreviated by dropping the last 1-2 chars.
    if len(n) >= 4:
        tokens.add(n[:-1])
    if len(n) >= 5:
        tokens.add(n[:-2])
    # Remove very short tokens to reduce false positives.
    return sorted([t for t in tokens if len(t) >= 2], key=len, reverse=True)


def _date_tokens(date_yyyy_mm_dd: str) -> list[str]:
    """Convert YYYY-MM-DD to likely fragments shown in schedule rows, e.g. '04-22'."""
    if not date_yyyy_mm_dd:
        return []
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", date_yyyy_mm_dd.strip())
    if not m:
        return []
    _y, mm, dd = m.group(1), m.group(2), m.group(3)
    return [f"{mm}-{dd}", f"{int(mm)}-{int(dd)}"]


def _time_tokens(hh_mm: str) -> list[str]:
    """Convert HH:MM to likely fragments shown in schedule rows, e.g. '03:00' / '3:00'."""
    if not hh_mm:
        return []
    m = re.match(r"^(\d{1,2}):(\d{2})$", hh_mm.strip())
    if not m:
        return []
    hh = int(m.group(1))
    mm = m.group(2)
    return [f"{hh:02d}:{mm}", f"{hh}:{mm}"]


def _alias_table_path() -> str:
    return str(Path(__file__).resolve().parent / "okooo_team_aliases.json")


def _load_alias_table() -> Dict[str, Any]:
    """Load team alias table for fuzzy matching schedule rows (best-effort)."""
    path = _alias_table_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _team_aliases(alias_table: Dict[str, Any], league: str, team_name: str) -> list[str]:
    if not team_name:
        return []
    league_key = _league_slug(league)
    league_map = alias_table.get(league_key, {}) if isinstance(alias_table, dict) else {}
    aliases = league_map.get(team_name, []) if isinstance(league_map, dict) else []
    out = [team_name]
    for a in aliases or []:
        if a and isinstance(a, str):
            out.append(a)
    # De-dup while preserving order.
    seen = set()
    uniq = []
    for x in out:
        x = x.strip()
        if not x or x in seen:
            continue
        seen.add(x)
        uniq.append(x)
    return uniq


def _norm_team_tokens_multi(names: list[str]) -> list[str]:
    """Generate fuzzy-match tokens for multiple aliases."""
    tokens: set[str] = set()
    for name in names or []:
        for tok in _norm_team_tokens(name):
            tokens.add(tok)
    # Prefer longer tokens first to reduce false positives.
    return sorted([t for t in tokens if len(t) >= 2], key=len, reverse=True)


def _eval_scroll_to_bottom(bu: Any) -> None:
    bu.eval_json("(() => { window.scrollTo(0, document.body.scrollHeight); return JSON.stringify({ok:true}); })()")
    time.sleep(1.2)


def _eval_scroll_to_top(bu: Any) -> None:
    bu.eval_json("(() => { window.scrollTo(0, 0); return JSON.stringify({ok:true}); })()")
    time.sleep(0.8)


def _find_rows_fuzzy(
    bu: Any,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    league: str = "",
    alias_table: Dict[str, Any] | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    d_tokens = _date_tokens(date_hint)
    tm_tokens = _time_tokens(time_hint)
    js = r"""
(() => {
  const t1 = %s;
  const t2 = %s;
  const ds = %s;
  const ts = %s;
  const requireDate = %s;
  const requireTime = %s;
  const rows = Array.from(document.querySelectorAll("a[href*='history.php?MatchID=']"))
    .map(a => {
      const m = a.href.match(/MatchID=(\d+)/);
      const mid = m ? m[1] : null;
      const row = a.closest('li') || a.closest('tr') || a.closest('div');
      const text = row ? (row.innerText || '').replace(/\s+/g,' ').trim() : '';
      if (!mid) return null;
      const compact = text.replace(/\s+/g,'');
      const hasAny = (tokens) => tokens.some(tok => tok && compact.includes(tok));
      const hasDate = (ds.length ? hasAny(ds) : true);
      const hasTime = (ts.length ? hasAny(ts) : true);
      if (requireDate && !hasDate) return null;
      if (requireTime && !hasTime) return null;
      const score =
        (hasAny(t1) ? 10 : 0) +
        (hasAny(t2) ? 10 : 0) +
        (ds.length ? (hasDate ? 6 : 0) : 0) +
        (ts.length ? (hasTime ? 8 : 0) : 0) +
        Math.min(compact.length, 100) / 100.0;
      return { mid, href: a.href, text, score };
    })
    .filter(x => x && x.score >= 20)  // require both teams present; date/time handled above if required
    .sort((a,b) => b.score - a.score);
  return JSON.stringify({count: rows.length, rows: rows.slice(0, %d)});
})()
""" % (
        json.dumps(t1_tokens, ensure_ascii=False),
        json.dumps(t2_tokens, ensure_ascii=False),
        json.dumps(d_tokens, ensure_ascii=False),
        json.dumps(tm_tokens, ensure_ascii=False),
        "true" if bool(d_tokens) else "false",
        "true" if bool(tm_tokens) else "false",
        limit,
    )
    return bu.eval_json(js)


def _find_rows_anywhere_on_current_page(
    bu: Any,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    league: str = "",
    alias_table: Dict[str, Any] | None = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Search the current page for candidate matches and MatchID.

    Unlike `_find_rows_fuzzy`, this scans:
    - anchors with `history.php?MatchID=...`
    - any element containing `MatchID=...` in href/onclick/data-href
    - nearby container text

    This is more robust on cup/continental competition pages where the mobile
    schedule markup differs from domestic league pages.
    """
    alias_table = alias_table or {}
    t1_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team1))
    t2_tokens = _norm_team_tokens_multi(_team_aliases(alias_table, league, team2))
    d_tokens = _date_tokens(date_hint)
    tm_tokens = _time_tokens(time_hint)
    js = r"""
(() => {
  const t1 = %s;
  const t2 = %s;
  const ds = %s;
  const ts = %s;
  const requireDate = %s;
  const requireTime = %s;

  const norm = (s) => String(s || '').replace(/\s+/g, '').trim();
  const hasAny = (compact, tokens) => (tokens || []).some(tok => tok && compact.includes(tok));

  const nodes = Array.from(document.querySelectorAll('a,div,span,button,li,tr,td'));
  const seen = new Set();
  const rows = [];

  const extractMid = (el) => {
    const attrs = [];
    const collectAttrs = (node) => {
      if (!node || !node.getAttribute) return;
      attrs.push(node.getAttribute('href') || '');
      attrs.push(node.getAttribute('onclick') || '');
      attrs.push(node.getAttribute('data-href') || '');
      attrs.push(node.getAttribute('data-url') || '');
    };
    collectAttrs(el);
    if (el && el.querySelectorAll) {
      const inner = Array.from(el.querySelectorAll('a,[onclick],[data-href],[data-url]')).slice(0, 8);
      for (const node of inner) collectAttrs(node);
    }
    const combined = attrs.join(' ');
    const m = combined.match(/MatchID=(\d+)/i) || combined.match(/matchid=(\d+)/i);
    return m ? m[1] : null;
  };

  for (const el of nodes) {
    const mid = extractMid(el);
    if (!mid || seen.has(mid)) continue;
    const row = el.closest('li') || el.closest('tr') || el.closest('div') || el.parentElement;
    const text = (row?.innerText || el.innerText || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const compact = norm(text);
    const hasT1 = hasAny(compact, t1);
    const hasT2 = hasAny(compact, t2);
    const hasDate = ds.length ? hasAny(compact, ds) : true;
    const hasTime = ts.length ? hasAny(compact, ts) : true;
    if (!hasT1 || !hasT2) continue;
    if (requireDate && !hasDate) continue;
    if (requireTime && !hasTime) continue;

    let href = '';
    if (el && el.getAttribute) href = el.getAttribute('href') || '';
    if ((!href || !/MatchID=/i.test(href)) && row && row.querySelector) {
      const a = row.querySelector("a[href*='MatchID='], a[href*='matchid=']");
      if (a && a.getAttribute) href = a.getAttribute('href') || href;
    }
    if (!href || !/MatchID=/i.test(href)) {
      href = `https://m.okooo.com/match/history.php?MatchID=${mid}`;
    } else if (href.startsWith('/')) {
      href = location.origin + href;
    }

    const score =
      10 +
      10 +
      (ds.length ? (hasDate ? 6 : 0) : 0) +
      (ts.length ? (hasTime ? 8 : 0) : 0) +
      Math.min(compact.length, 100) / 100.0;
    rows.push({ mid, href, text, score });
    seen.add(mid);
  }

  rows.sort((a, b) => b.score - a.score);
  return JSON.stringify({count: rows.length, rows: rows.slice(0, %d)});
})()
""" % (
        json.dumps(t1_tokens, ensure_ascii=False),
        json.dumps(t2_tokens, ensure_ascii=False),
        json.dumps(d_tokens, ensure_ascii=False),
        json.dumps(tm_tokens, ensure_ascii=False),
        "true" if bool(d_tokens) else "false",
        "true" if bool(tm_tokens) else "false",
        limit,
    )
    return bu.eval_json(js)


def _find_existing_snapshot_by_match_id(out_dir: Path, match_id: str) -> Optional[Path]:
    """Return an existing snapshot JSON path in out_dir that matches match_id, else None."""
    if not match_id:
        return None
    try:
        candidates = sorted(out_dir.glob("*.json"))
    except Exception:
        return None

    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and str(data.get("match_id") or "") == str(match_id):
            return p
    return None


@dataclass
class BrowserUse:
    session: str
    headed: bool = False

    def _run_once(self, cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _close_session_best_effort(self) -> None:
        try:
            cp = self._run_once(["browser-use", "--session", self.session, "close"], timeout=30)
            _ = cp.returncode  # best-effort
        except Exception:
            pass

    def run(self, *args: str, timeout: int = 60, use_headed: bool = False) -> str:
        cmd = ["browser-use", "--session", self.session]
        if use_headed:
            cmd.append("--headed")
        cmd.extend(args)
        cp = self._run_once(cmd, timeout=timeout)
        out = (cp.stdout or "").strip()
        err = (cp.stderr or "").strip()
        if cp.returncode == 0:
            return out

        combined = (out + "\n" + err).strip()
        if "already running with different config" in combined:
            # Auto-recover: close the stale session and retry once.
            self._close_session_best_effort()
            cp2 = self._run_once(cmd, timeout=timeout)
            out2 = (cp2.stdout or "").strip()
            err2 = (cp2.stderr or "").strip()
            if cp2.returncode == 0:
                return out2
            combined = (out2 + "\n" + err2).strip()

        tail = "\n".join([x for x in combined.splitlines() if x][-8:])
        raise RuntimeError(f"browser-use failed: {' '.join(cmd)}\n{tail}")

    def open(self, url: str) -> None:
        self.run("open", url, timeout=90, use_headed=self.headed)

    def state(self) -> str:
        return self.run("state", timeout=60)

    def eval_json(self, js_expr: str) -> Dict[str, Any]:
        # Expect the command prints a JSON string (or "result: ...") on stdout.
        out = self.run("eval", js_expr, timeout=90)
        # Try to parse the last JSON-looking line.
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        payload = lines[-1] if lines else ""
        payload = payload.removeprefix("result:").strip()
        try:
            return json.loads(payload)
        except Exception:
            return {"_raw": out}

    def close(self) -> None:
        try:
            self.run("close", timeout=30)
        except Exception:
            # best-effort
            pass


class LocalChromeSession:
    def __init__(self, port: int, session_name: str = "") -> None:
        self.port = port
        self.session_name = session_name
        self.target_id: str | None = None
        self.ws = None
        self._msg_id = 0

    def _browser_version(self) -> Dict[str, Any]:
        r = requests.get(f"http://127.0.0.1:{self.port}/json/version", timeout=10)
        r.raise_for_status()
        return r.json()

    def _new_target(self, url: str) -> Dict[str, Any]:
        endpoint = f"http://127.0.0.1:{self.port}/json/new?{url}"
        r = requests.put(endpoint, timeout=15)
        if r.status_code >= 400:
            r = requests.get(endpoint, timeout=15)
        r.raise_for_status()
        return r.json()

    def _connect_if_needed(self, url: str = "about:blank") -> None:
        if self.ws:
            return
        target = self._new_target(url)
        self.target_id = target.get("id")
        ws_url = target.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("CDP page target missing webSocketDebuggerUrl")
        self.ws = create_connection(ws_url, timeout=20, suppress_origin=True)
        self._cdp("Page.enable")
        self._cdp("Runtime.enable")

    def _cdp(self, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not self.ws:
            self._connect_if_needed()
        self._msg_id += 1
        payload = {"id": self._msg_id, "method": method, "params": params or {}}
        assert self.ws is not None
        self.ws.send(json.dumps(payload))
        while True:
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == self._msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} failed: {msg['error']}")
                return msg.get("result", {})

    def open(self, url: str) -> None:
        if not self.ws:
            self._connect_if_needed(url)
            time.sleep(3.5)
            return
        self._cdp("Page.navigate", {"url": url})
        time.sleep(3.5)

    def state(self) -> str:
        result = self._cdp(
            "Runtime.evaluate",
            {
                "expression": """(() => {
                  const body = document.body ? document.body.innerText : '';
                  return `viewport: ${window.innerWidth}x${window.innerHeight}\\npage: ${window.innerWidth}x${window.innerHeight}\\nscroll: (${window.scrollX}, ${window.scrollY})\\n${body}`;
                })()""",
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return result.get("result", {}).get("value", "") or ""

    def eval_json(self, js_expr: str) -> Dict[str, Any]:
        result = self._cdp(
            "Runtime.evaluate",
            {"expression": js_expr, "returnByValue": True, "awaitPromise": True},
        )
        value = result.get("result", {}).get("value")
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"_raw": value}
        if isinstance(value, dict):
            return value
        return {"_raw": value}

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        finally:
            self.ws = None
        if self.target_id:
            try:
                requests.get(f"http://127.0.0.1:{self.port}/json/close/{self.target_id}", timeout=5)
            except Exception:
                pass
            self.target_id = None


def _wait_for_chrome_debug_port(port: int, timeout_seconds: float = 15.0) -> bool:
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
            if r.ok:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _ensure_local_chrome(port: int, chrome_path: str, user_data_dir: str) -> Dict[str, Any]:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
        if r.ok:
            return {"port": port, "started_by_script": False}
    except Exception:
        pass

    # If the desired port is unavailable/flaky, try a few adjacent ports.
    port_candidates = [port] + [p for p in range(port + 1, port + 6)]
    last_err: str | None = None
    for cand in port_candidates:
        profile_dir = (Path(user_data_dir).resolve() / f"port_{cand}")
        profile_dir.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [
                chrome_path,
                f"--remote-debugging-port={cand}",
                "--remote-allow-origins=*",
                f"--user-data-dir={profile_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=AutomationControlled",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if _wait_for_chrome_debug_port(cand, timeout_seconds=20.0):
            def _cleanup() -> None:
                try:
                    proc.terminate()
                except Exception:
                    pass

            atexit.register(_cleanup)
            return {"port": cand, "started_by_script": True, "profile_dir": str(profile_dir)}
        # failed; kill and try next port
        try:
            proc.terminate()
        except Exception:
            pass
        last_err = f"port {cand} did not respond"

    raise RuntimeError(f"本地 Chrome 远程调试端口启动失败（尝试 {port_candidates}）: {last_err or ''}".strip())


def _is_blocked_text(text: str) -> bool:
    text = text or ""
    return (
        "访问被阻断" in text
        or "安全威胁" in text
        or "您的访问被阻断" in text
        or "Sorry, your request has been blocked" in text
        or "<title>405</title>" in text
    )


def _is_success_payload(data: Dict[str, Any]) -> bool:
    if data.get("blocked"):
        return False
    if data.get("found") is False:
        return False
    if data.get("parsed") is False:
        return False
    return True


def _annotate_attempts(data: Dict[str, Any], attempts: list[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(data)
    out["_attempts"] = attempts
    return out


def _open_ready(bu: BrowserUse, url: str, settle_seconds: float = 2.5) -> str:
    bu.open(url)
    time.sleep(settle_seconds)
    try:
        state_text = bu.state()
    except Exception:
        state_text = ""
    return state_text


def _mobile_league_url(league: str) -> str | None:
    mapping = {
        "英超": "https://m.okooo.com/saishi/17/",
        "意甲": "https://m.okooo.com/saishi/23/",
        "西甲": "https://m.okooo.com/saishi/8/",
        "德甲": "https://m.okooo.com/saishi/35/",
        "法甲": "https://m.okooo.com/saishi/34/",
        "欧冠": "https://m.okooo.com/saishi/7/",
        "中超": "https://m.okooo.com/saishi/649/",
        "英冠": "https://m.okooo.com/saishi/133/",
    }
    return mapping.get(league)


def _click_visible_text(bu: BrowserUse, labels: list[str], settle_seconds: float = CLICK_SETTLE_SECONDS) -> Dict[str, Any]:
    js = r"""
(() => {
  const labels = %s;
  const els = [...document.querySelectorAll('a,div,span,button,li')];
  for (const label of labels) {
    const el = els.find(e => ((e.innerText || '').replace(/\s+/g,'').trim() === label));
    if (el) {
      el.click();
      return JSON.stringify({clicked:true, label, tag:el.tagName});
    }
  }
  return JSON.stringify({clicked:false, labels});
})()
""" % json.dumps(labels, ensure_ascii=False)
    result = bu.eval_json(js)
    time.sleep(settle_seconds)
    return result


def _parse_europe_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});
  const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('99家平均'));
  if(!row) return JSON.stringify({found:false});
  const num=(sel)=>{const el=row.querySelector(sel); if(!el) return null; const v=parseFloat((el.textContent||'').trim()); return Number.isFinite(v)?v:null;};
  const initial={home:num('span[type=sheng]'),draw:num('span[type=ping]'),away:num('span[type=fu]')};
  const final={home:num('span[type=xinsheng]'),draw:num('span[type=xinping]'),away:num('span[type=xinfu]')};
  const delta=(a,b)=> (a==null||b==null) ? null : +(a-b).toFixed(4);
  return JSON.stringify({found:true, initial, final, delta:{home:delta(final.home,initial.home), draw:delta(final.draw,initial.draw), away:delta(final.away,initial.away)}});
})()
"""
    return bu.eval_json(js)


def _parse_asian_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});
  const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('平均指数'));
  if(!row) return JSON.stringify({found:false});
  const s=(row.innerText||'').replace(/\s+/g,' ').trim();
  const m=s.match(/平均指数\s*(\d+\.\d+)\s*([\u4e00-\u9fff/]+)\s*(\d+\.\d+)\s*(\d+\.\d+)\s*([\u4e00-\u9fff/]+)\s*(\d+\.\d+)/);
  if(!m) return JSON.stringify({found:true, parsed:false, text:s});
  const initial={home_water:parseFloat(m[1]), handicap_text:m[2], away_water:parseFloat(m[3])};
  const final={home_water:parseFloat(m[4]), handicap_text:m[5], away_water:parseFloat(m[6])};
  const delta=(a,b)=> (a==null||b==null) ? null : +(a-b).toFixed(4);
  return JSON.stringify({found:true, parsed:true, initial, final, delta:{home_water:delta(final.home_water, initial.home_water), away_water:delta(final.away_water, initial.away_water)}});
})()
"""
    return bu.eval_json(js)


def _parse_totals_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    """Parse Over/Under (大小球) average row on current page.

    Expected mobile row pattern:
      澳门  1.82  3.0  1.90   1.77  2.75  1.95
    Where:
      initial: over, line, under
      final:   over, line, under
    """
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const rows = [...document.querySelectorAll('tr')];
  const avgRow = rows.find(tr => {
    const txt = (tr.innerText || '').replace(/\s+/g, ' ').trim();
    return txt.includes('平均指数') || txt.includes('澳门');
  });
  if (!avgRow) return JSON.stringify({found:false});

  const tds = [...avgRow.querySelectorAll('td')].map(td => (td.innerText||'').replace(/\s+/g,' ').trim());
  const nums = [];
  for (const td of tds) {
    const m = td.match(/\d+(?:\.\d+)?/g) || [];
    for (const x of m) nums.push(parseFloat(x));
  }
  // Need at least 6 numbers: init(over,line,under), final(over,line,under)
  if (nums.length < 6) {
    return JSON.stringify({found:true, parsed:false, text:(avgRow.innerText||'').replace(/\s+/g,' ').trim(), tds});
  }

  const initial = { over: nums[0], line: nums[1], under: nums[2] };
  const final = { over: nums[3], line: nums[4], under: nums[5] };
  const delta = {
    over: +(final.over - initial.over).toFixed(4),
    line: +(final.line - initial.line).toFixed(4),
    under: +(final.under - initial.under).toFixed(4),
  };

  return JSON.stringify({found:true, parsed:true, initial, final, delta});
})()
"""
    data = bu.eval_json(js)
    if isinstance(data, dict) and data.get("found") and data.get("parsed"):
        return data

    # Fallback for the actual mobile UX: O/U is nested under 亚值 page and is often
    # rendered as free text instead of a regular table row.
    fallback_js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const body = document.body?.innerText || '';
  const lines = body.split(/\n+/).map(x => x.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const compact = body.replace(/\s+/g, ' ').trim();

  const companyAliases = [
    '澳门彩票', '澳门', 'Bet365', 'bet365', '皇冠', 'Pinnacle', '威廉.希尔', '立博'
  ];
  const numRe = /\d+(?:\.\d+)?/g;

  const parseLine = (line, company) => {
    const idx = line.indexOf(company);
    if (idx < 0) return null;
    const tail = line.slice(idx + company.length).trim();
    const nums = (tail.match(numRe) || []).map(x => parseFloat(x));
    if (nums.length < 6) return null;
    return {
      found: true,
      parsed: true,
      company,
      initial: { over: nums[0], line: nums[1], under: nums[2] },
      final: { over: nums[3], line: nums[4], under: nums[5] },
      delta: {
        over: +(nums[3] - nums[0]).toFixed(4),
        line: +(nums[4] - nums[1]).toFixed(4),
        under: +(nums[5] - nums[2]).toFixed(4),
      },
      _source: 'body_text_line',
      _matched_line: line,
    };
  };

  for (const company of companyAliases) {
    for (const line of lines) {
      const parsed = parseLine(line, company);
      if (parsed) return JSON.stringify(parsed);
    }
  }

  for (const company of companyAliases) {
    const idx = compact.indexOf(company);
    if (idx < 0) continue;
    const windowText = compact.slice(idx, idx + 120);
    const parsed = parseLine(windowText, company);
    if (parsed) {
      parsed._source = 'body_text_window';
      parsed._matched_line = windowText;
      return JSON.stringify(parsed);
    }
  }

  return JSON.stringify({found:false, _source:'body_text_fallback'});
})()
"""
    return bu.eval_json(fallback_js)


def _parse_kelly_on_current_page(bu: BrowserUse) -> Dict[str, Any]:
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});
  const tbl=[...document.querySelectorAll('table')].find(t=>t.innerText.includes('初始凯利') && t.innerText.includes('最新凯利') && t.innerText.includes('99家平均'));
  if(!tbl) return JSON.stringify({found:false});
  const row=[...tbl.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('99家平均'));
  if(!row) return JSON.stringify({found:false});
  const tds=[...row.querySelectorAll('td')].map(td=>(td.innerText||'').trim());
  const nums=(s)=>((s.match(/\d+\.\d{2}/g)||[]).map(parseFloat));
  const init=nums(tds[1]||'');
  const fin=nums(tds[2]||'');
  const payout=nums(tds[3]||'');
  const initial=init.length>=3?{home:init[0],draw:init[1],away:init[2]}:null;
  const final=fin.length>=3?{home:fin[0],draw:fin[1],away:fin[2]}:null;
  const delta=(a,b)=> (a==null||b==null) ? null : +(a-b).toFixed(4);
  return JSON.stringify({
    found:true,
    initial, final,
    delta: initial && final ? {home:delta(final.home, initial.home), draw:delta(final.draw, initial.draw), away:delta(final.away, initial.away)} : null,
    payout_rate: (payout[0] ?? null)
  });
})()
"""
    return bu.eval_json(js)


def _parse_kelly_anywhere_on_page(bu: BrowserUse) -> Dict[str, Any]:
    """Fallback: search any table row containing '99家平均' and try to infer initial/final kelly."""
    js = r"""
(() => {
  const blocked = (document.body?.innerText||'').includes('访问被阻断') || (document.title||'').includes('405');
  if (blocked) return JSON.stringify({blocked:true});

  const rows = [...document.querySelectorAll('tr')].filter(tr => (tr.innerText||'').includes('99家平均'));
  const toNums = (s) => (String(s||'').match(/\d+\.\d{2}/g)||[]).map(parseFloat);

  // Prefer a row whose ancestor table contains both labels.
  const preferred = rows.find(r => {
    const t = r.closest('table');
    const it = t ? (t.innerText||'') : '';
    return it.includes('初始凯利') && it.includes('最新凯利');
  }) || null;

  const candidates = preferred ? [preferred] : rows;

  for (const row of candidates) {
    const tds = [...row.querySelectorAll('td')].map(td => (td.innerText||'').trim());
    // Pattern A: dedicated kelly table where tds[1], tds[2], tds[3] map to initial/final/payout.
    if (tds.length >= 4) {
      const init = toNums(tds[1]);
      const fin = toNums(tds[2]);
      const payout = toNums(tds[3]);
      const okTriplet = (arr) => arr.length >= 3 && arr.slice(0,3).every(v => v >= 0.3 && v <= 2.5);
      const okPayout = (arr) => arr.length >= 1 && arr[0] >= 0.8 && arr[0] <= 1.2;
      if (okTriplet(init) && okTriplet(fin)) {
        const initial = {home:init[0], draw:init[1], away:init[2]};
        const final = {home:fin[0], draw:fin[1], away:fin[2]};
        const delta = {home:+(final.home-initial.home).toFixed(4), draw:+(final.draw-initial.draw).toFixed(4), away:+(final.away-initial.away).toFixed(4)};
        return JSON.stringify({found:true, initial, final, delta, payout_rate: okPayout(payout) ? payout[0] : null, _source:'row_tds'});
      }
    }

    // Pattern B: one-line numbers where first 3 are initial, next 3 are final, last is payout.
    const allNums = toNums(row.innerText);
    if (allNums.length >= 7) {
      const init = allNums.slice(0, 3);
      const fin = allNums.slice(3, 6);
      const payout = allNums[6];
      const okTriplet = (arr) => arr.every(v => v >= 0.3 && v <= 2.5);
      if (okTriplet(init) && okTriplet(fin) && payout >= 0.8 && payout <= 1.2) {
        const initial = {home:init[0], draw:init[1], away:init[2]};
        const final = {home:fin[0], draw:fin[1], away:fin[2]};
        const delta = {home:+(final.home-initial.home).toFixed(4), draw:+(final.draw-initial.draw).toFixed(4), away:+(final.away-initial.away).toFixed(4)};
        return JSON.stringify({found:true, initial, final, delta, payout_rate:payout, _source:'row_innerText'});
      }
    }
  }

  return JSON.stringify({found:false});
})()
"""
    return bu.eval_json(js)


def _parse_desktop_avg_row(row_cells: list[str]) -> Dict[str, Any]:
    def pick_float(idx: int) -> float | None:
        if idx >= len(row_cells):
            return None
        match = re.search(r"\d+\.\d+", row_cells[idx] or "")
        if not match:
            return None
        return float(match.group(0))

    # Desktop okooo odds row layout observed:
    # [0]序号 [1]公司 [2:5]初赔 [5:8]即时 [8]变化图 [9:12]概率 [12:15]凯利 [15]赔付率
    initial = {"home": pick_float(2), "draw": pick_float(3), "away": pick_float(4)}
    final = {"home": pick_float(5), "draw": pick_float(6), "away": pick_float(7)}
    kelly = {"home": pick_float(12), "draw": pick_float(13), "away": pick_float(14)}
    payout_rate = pick_float(15)
    delta = {
        "home": None if initial["home"] is None or final["home"] is None else round(final["home"] - initial["home"], 4),
        "draw": None if initial["draw"] is None or final["draw"] is None else round(final["draw"] - initial["draw"], 4),
        "away": None if initial["away"] is None or final["away"] is None else round(final["away"] - initial["away"], 4),
    }
    return {
        "found": True,
        "initial": initial,
        "final": final,
        "delta": delta,
        "kelly": kelly,
        "payout_rate": payout_rate,
        "_row_cells": row_cells,
    }


def _find_match_id(
    bu: Any,
    league: str,
    team1: str,
    team2: str,
    date_hint: str = "",
    time_hint: str = "",
    alias_table: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    def find_rows() -> Dict[str, Any]:
        return _find_rows_fuzzy(
            bu,
            team1,
            team2,
            date_hint=date_hint,
            time_hint=time_hint,
            league=league,
            alias_table=alias_table or {},
            limit=5,
        )

    def find_rows_anywhere() -> Dict[str, Any]:
        return _find_rows_anywhere_on_current_page(
            bu,
            team1,
            team2,
            date_hint=date_hint,
            time_hint=time_hint,
            league=league,
            alias_table=alias_table or {},
            limit=5,
        )

    bu.open(REMEN_URL)

    # Click the league entry by visible text.
    click_league = r"""
(() => {
  const name = %s;
  const els = Array.from(document.querySelectorAll('a,div,span,button'));
  const el = els.find(e => ((e.innerText || '').trim() === name));
  if (!el) return JSON.stringify({clicked:false, reason:'league not found'});
  el.click();
  return JSON.stringify({clicked:true, tag:el.tagName});
})()
""" % json.dumps(league, ensure_ascii=False)
    bu.eval_json(click_league)
    time.sleep(2.0)

    found = find_rows()
    if not isinstance(found, dict) or not found.get("rows"):
        found = find_rows_anywhere()
    if not isinstance(found, dict) or not found.get("rows"):
        league_url = _mobile_league_url(league)
        if league_url:
            bu.open(league_url)
            time.sleep(2.0)
            found = find_rows()
            if not isinstance(found, dict) or not found.get("rows"):
                found = find_rows_anywhere()

    # If still not found, try scrolling to load more schedule blocks.
    if not isinstance(found, dict) or not found.get("rows"):
        for _ in range(10):
            _eval_scroll_to_bottom(bu)
            found = find_rows()
            if not isinstance(found, dict) or not found.get("rows"):
                found = find_rows_anywhere()
            if isinstance(found, dict) and found.get("rows"):
                break
        # One more try from top (some pages lazy-load above fold).
        if not isinstance(found, dict) or not found.get("rows"):
            _eval_scroll_to_top(bu)
            found = find_rows()
            if not isinstance(found, dict) or not found.get("rows"):
                found = find_rows_anywhere()
    if not isinstance(found, dict) or not found.get("rows"):
        raise RuntimeError(f"未在联赛赛程中找到包含 {team1} 和 {team2} 的比赛行(可尝试补充别名/时间)")

    first = found["rows"][0]
    return {"match_id": first["mid"], "schedule_row": first}


def _extract_europe(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://m.okooo.com/match/odds.php?MatchID={match_id}"
    state_text = _open_ready(bu, url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    data = _parse_europe_on_current_page(bu)
    data["url"] = url
    return data


def _extract_asian(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://m.okooo.com/match/handicap.php?MatchID={match_id}"
    state_text = _open_ready(bu, url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    data = _parse_asian_on_current_page(bu)
    data["url"] = url
    return data


def _extract_totals_from_asian_page(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    """Extract totals from the mobile asian-handicap page's inner '大小球' tab.

    User-provided evidence shows O/U is nested under the 亚值 page instead of
    always being exposed as a dedicated mobile path.
    """
    url = f"https://m.okooo.com/match/handicap.php?MatchID={match_id}"
    state_text = _open_ready(bu, url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}

    # First click the nested tab on the asian page.
    click_result = _click_visible_text(bu, ["大小球", "大/小", "总进球"], settle_seconds=CLICK_SETTLE_SECONDS)
    data = _parse_totals_on_current_page(bu)
    data["url"] = url
    data["_flow"] = "asian_inner_tab"
    data["_click"] = click_result
    return data


def _extract_totals(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    # Prefer the mobile asian page's inner "大小球" tab. This matches the actual
    # UI observed in recent screenshots and is more reliable than guessing a
    # standalone totals route.
    data0 = _extract_totals_from_asian_page(bu, match_id)
    if data0.get("found"):
        return data0

    # Fallback: dedicated totals page if available on mobile.
    # Some setups route to /overunder.php, others to /daxiao.php; try first path.
    primary = f"https://m.okooo.com/match/overunder.php?MatchID={match_id}"
    state_text = _open_ready(bu, primary, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": primary, "_state_excerpt": state_text[:500]}
    data = _parse_totals_on_current_page(bu)
    if data.get("found"):
        data["url"] = primary
        return data

    alt = f"https://m.okooo.com/match/daxiao.php?MatchID={match_id}"
    state_text2 = _open_ready(bu, alt, settle_seconds=3.0)
    if _is_blocked_text(state_text2):
        return {"blocked": True, "url": alt, "_state_excerpt": state_text2[:500]}
    data2 = _parse_totals_on_current_page(bu)
    data2["url"] = alt
    return data2


def _extract_kelly_from_odds_tab(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    # Prefer tab navigation because direct kelly.php is sometimes blocked.
    url = f"https://m.okooo.com/match/odds.php?MatchID={match_id}"
    state_text = _open_ready(bu, url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}

    click_result = _click_visible_text(bu, ["凯利", "凯利指数"], settle_seconds=CLICK_SETTLE_SECONDS)

    # Give the tab content a moment to render (especially on local-chrome/CDP).
    time.sleep(2.0)
    data = _parse_kelly_on_current_page(bu)
    if isinstance(data, dict) and data.get("found") is False:
        data = _parse_kelly_anywhere_on_page(bu)
    data["url"] = url
    data["_click"] = click_result
    return data


def _extract_kelly_direct(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://m.okooo.com/match/kelly.php?MatchID={match_id}"
    state_text = _open_ready(bu, url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    data = _parse_kelly_on_current_page(bu)
    if isinstance(data, dict) and data.get("found") is False:
        data = _parse_kelly_anywhere_on_page(bu)
    data["url"] = url
    return data


def _extract_europe_from_history_flow(bu: BrowserUse, history_url: str) -> Dict[str, Any]:
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}
    click_result = _click_visible_text(bu, ["欧赔", "欧指", "赔率"])
    data = _parse_europe_on_current_page(bu)
    data["url"] = history_url
    data["_flow"] = "history_tab"
    data["_click"] = click_result
    return data


def _extract_asian_from_history_flow(bu: BrowserUse, history_url: str) -> Dict[str, Any]:
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}
    click_result = _click_visible_text(bu, ["亚盘", "亚值", "让球"])
    data = _parse_asian_on_current_page(bu)
    data["url"] = history_url
    data["_flow"] = "history_tab"
    data["_click"] = click_result
    return data


def _extract_totals_from_history_flow(bu: BrowserUse, history_url: str) -> Dict[str, Any]:
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}
    click_result = _click_visible_text(bu, ["大小球", "大小", "大/小", "总进球"])
    data = _parse_totals_on_current_page(bu)
    data["url"] = history_url
    data["_flow"] = "history_tab"
    data["_click"] = click_result
    return data


def _extract_kelly_from_history_flow(bu: BrowserUse, history_url: str) -> Dict[str, Any]:
    state_text = _open_ready(bu, history_url, settle_seconds=3.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": history_url, "_state_excerpt": state_text[:500]}
    click_result = _click_visible_text(bu, ["凯利"])
    data = _parse_kelly_on_current_page(bu)
    data["url"] = history_url
    data["_flow"] = "history_tab"
    data["_click"] = click_result
    return data


def _extract_europe_desktop(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
    state_text = _open_ready(bu, url, settle_seconds=4.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    row_info = bu.eval_json(
        r"""
(() => {
  const rows=[...document.querySelectorAll('tr')];
  const row=rows.find(r=>(r.innerText||'').includes('99家平均'));
  if(!row) return JSON.stringify({found:false});
  const tds=[...row.querySelectorAll('td')].map(td=>(td.innerText||'').replace(/\s+/g,' ').trim());
  return JSON.stringify({found:true, tds});
})()
"""
    )
    if row_info.get("found") and row_info.get("tds"):
        data = _parse_desktop_avg_row(row_info["tds"])
    else:
        data = _parse_europe_on_current_page(bu)
    data["url"] = url
    data["_flow"] = "desktop_direct"
    return data


def _extract_asian_desktop(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://www.okooo.com/soccer/match/{match_id}/ah/"
    state_text = _open_ready(bu, url, settle_seconds=4.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    data = _parse_asian_on_current_page(bu)
    data["url"] = url
    data["_flow"] = "desktop_direct"
    return data


def _extract_totals_desktop(bu: BrowserUse, match_id: str) -> Dict[str, Any]:
    url = f"https://www.okooo.com/soccer/match/{match_id}/ou/"
    state_text = _open_ready(bu, url, settle_seconds=4.0)
    if _is_blocked_text(state_text):
        return {"blocked": True, "url": url, "_state_excerpt": state_text[:500]}
    data = _parse_totals_on_current_page(bu)
    data["url"] = url
    data["_flow"] = "desktop_direct"
    return data


def _run_with_retries(
    _label: str,
    session_prefix: str,
    client_factory: Callable[[str], Any],
    extractor,
    *extractor_args: str,
) -> Dict[str, Any]:
    attempts: list[Dict[str, Any]] = []
    last_data: Dict[str, Any] = {"found": False}

    # Always retry with fresh sessions. Headed mode is preferred because it is
    # empirically less likely to be blocked on okooo mobile pages.
    for index, delay in enumerate(RETRY_DELAYS, start=1):
        session = f"{session_prefix}_{datetime.now().strftime('%H%M%S')}_{index}"
        bu = client_factory(session)
        try:
            data = extractor(bu, *extractor_args)
            last_data = data
            success = _is_success_payload(data)
            attempts.append(
                {
                    "attempt": index,
                    "client": bu.__class__.__name__,
                    "success": success,
                    "blocked": bool(isinstance(data, dict) and data.get("blocked")),
                    "found": None if not isinstance(data, dict) else data.get("found"),
                    "parsed": None if not isinstance(data, dict) else data.get("parsed"),
                }
            )
            if success:
                return _annotate_attempts(data, attempts)
        except Exception as exc:
            last_data = {"error": str(exc)}
            attempts.append({"attempt": index, "client": bu.__class__.__name__, "success": False, "error": str(exc)})
        finally:
            bu.close()

        time.sleep(delay)

    return _annotate_attempts(last_data, attempts)


def _extract_kelly_with_fallback(match_id: str, client_factory: Callable[[str], Any], session_prefix: str) -> Dict[str, Any]:
    first = _run_with_retries(
        "kelly_tab",
        f"{session_prefix}_tab",
        client_factory,
        _extract_kelly_from_odds_tab,
        match_id,
    )
    if _is_success_payload(first):
        return first

    second = _run_with_retries(
        "kelly_direct",
        f"{session_prefix}_direct",
        client_factory,
        _extract_kelly_direct,
        match_id,
    )
    if _is_success_payload(second):
        second["_fallback_from"] = "odds_tab"
        return second

    # keep more informative result
    return second if second.get("_attempts") else first


def _extract_europe_with_fallback(match_id: str, history_url: str, client_factory: Callable[[str], Any], session_prefix: str) -> Dict[str, Any]:
    first = _run_with_retries("europe_mobile", f"{session_prefix}_mobile", client_factory, _extract_europe, match_id)
    if _is_success_payload(first):
        return first
    second = _run_with_retries("europe_history", f"{session_prefix}_history", client_factory, _extract_europe_from_history_flow, history_url)
    if _is_success_payload(second):
        second["_fallback_from"] = "mobile_direct"
        return second
    third = _run_with_retries("europe_desktop", f"{session_prefix}_desktop", client_factory, _extract_europe_desktop, match_id)
    if _is_success_payload(third):
        third["_fallback_from"] = "history_tab"
        return third
    return third if third.get("_attempts") else (second if second.get("_attempts") else first)


def _extract_asian_with_fallback(match_id: str, history_url: str, client_factory: Callable[[str], Any], session_prefix: str) -> Dict[str, Any]:
    first = _run_with_retries("asian_mobile", f"{session_prefix}_mobile", client_factory, _extract_asian, match_id)
    if _is_success_payload(first):
        return first
    second = _run_with_retries("asian_history", f"{session_prefix}_history", client_factory, _extract_asian_from_history_flow, history_url)
    if _is_success_payload(second):
        second["_fallback_from"] = "mobile_direct"
        return second
    third = _run_with_retries("asian_desktop", f"{session_prefix}_desktop", client_factory, _extract_asian_desktop, match_id)
    if _is_success_payload(third):
        third["_fallback_from"] = "history_tab"
        return third
    return third if third.get("_attempts") else (second if second.get("_attempts") else first)


def _extract_totals_with_fallback(match_id: str, history_url: str, client_factory: Callable[[str], Any], session_prefix: str) -> Dict[str, Any]:
    first = _run_with_retries("totals_mobile", f"{session_prefix}_mobile", client_factory, _extract_totals, match_id)
    if _is_success_payload(first):
        return first
    second = _run_with_retries("totals_history", f"{session_prefix}_history", client_factory, _extract_totals_from_history_flow, history_url)
    if _is_success_payload(second):
        second["_fallback_from"] = "mobile_direct"
        return second
    third = _run_with_retries("totals_desktop", f"{session_prefix}_desktop", client_factory, _extract_totals_desktop, match_id)
    if _is_success_payload(third):
        third["_fallback_from"] = "history_tab"
        return third
    return third if third.get("_attempts") else (second if second.get("_attempts") else first)


def _extract_kelly_full_fallback(match_id: str, history_url: str, client_factory: Callable[[str], Any], session_prefix: str) -> Dict[str, Any]:
    first = _extract_kelly_with_fallback(match_id, client_factory, session_prefix)
    if _is_success_payload(first):
        return first
    second = _run_with_retries("kelly_history", f"{session_prefix}_history", client_factory, _extract_kelly_from_history_flow, history_url)
    if _is_success_payload(second):
        second["_fallback_from"] = "mobile_direct"
        return second
    third = _run_with_retries("kelly_desktop", f"{session_prefix}_desktop", client_factory, _extract_europe_desktop, match_id)
    if _is_success_payload(third) and third.get("kelly"):
        return {
            "found": True,
            "initial": None,
            "final": third.get("kelly"),
            "delta": None,
            "payout_rate": third.get("payout_rate"),
            "url": third.get("url"),
            "_flow": "desktop_odds_row",
            "_fallback_from": "history_tab",
            "_attempts": third.get("_attempts", []),
        }
    return third if third.get("_attempts") else (second if second.get("_attempts") else first)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--league", required=True, help="联赛名称（需与热门赛事页展示文本一致，如：法甲/英超/意甲...）")
    parser.add_argument("--team1", required=True, help="主队名称（用于赛程行匹配）")
    parser.add_argument("--team2", required=True, help="客队名称（用于赛程行匹配）")
    parser.add_argument("--match-id", default="", help="可选：直接指定 MatchID，跳过赛程匹配。")
    parser.add_argument(
        "--driver",
        choices=["browser-use", "local-chrome"],
        default="browser-use",
        help="抓取驱动：browser-use 或 local-chrome（通过本地 Chrome CDP 抓取）",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_default_data_root() / "snapshots"),
        help="输出目录（默认写入用户目录下的 okooo-scraper/snapshots，避免污染仓库）。",
    )
    parser.add_argument(
        "--no-league-subdir",
        action="store_true",
        help="不按联赛分目录（默认会在 out-dir 下按联赛 slug 建子目录保存快照）。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="同一场比赛固定文件名并覆盖已有 JSON（默认开启时间戳命名）。",
    )
    parser.add_argument(
        "--no-matchid-dedupe",
        action="store_true",
        help="禁用按 match_id 自动覆盖（默认同 match_id 会覆盖已有 JSON）。",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="以有头浏览器运行 browser-use。部分页面在无头模式下更容易被拦截，抓不到赛程时建议开启。",
    )
    parser.add_argument("--chrome-port", type=int, default=9222, help="local-chrome 模式使用的 CDP 端口")
    parser.add_argument(
        "--chrome-path",
        default="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        help="Google Chrome 可执行文件路径",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default=str(_default_data_root() / "chrome_profile"),
        help="local-chrome 模式启动的独立用户目录（默认写入用户目录下，避免污染仓库）。",
    )
    parser.add_argument(
        "--date",
        default="",
        help="可选：比赛日期 YYYY-MM-DD，用于在赛程中更准确定位（例如 2026-04-22）。",
    )
    parser.add_argument(
        "--time",
        default="",
        help="可选：比赛时间 HH:MM，用于在赛程中更精准定位（例如 03:00）。",
    )
    args = parser.parse_args()

    event_name = f"{args.team1}vs{args.team2}"
    out_dir = Path(args.out_dir).resolve()
    if not args.no_league_subdir:
        out_dir = out_dir / _league_slug(args.league)
    out_dir.mkdir(parents=True, exist_ok=True)

    # NOTE: some browser-use implementations may truncate/normalize session names.
    # Keep the random token at the beginning to reduce collision probability after truncation.
    token = uuid.uuid4().hex[:8]
    session_prefix = f"ok_{token}"

    chrome_meta: Dict[str, Any] = {}
    if args.driver == "local-chrome":
        chrome_meta = _ensure_local_chrome(args.chrome_port, args.chrome_path, args.chrome_user_data_dir)

        def client_factory(session_name: str) -> Any:
            return LocalChromeSession(port=args.chrome_port, session_name=session_name)

    else:

        def client_factory(session_name: str) -> Any:
            return BrowserUse(session=session_name, headed=args.headed)

    if args.match_id:
        match_id = str(args.match_id)
        found = {
            "match_id": match_id,
            "schedule_row": {
                "mid": match_id,
                "href": f"https://m.okooo.com/match/history.php?MatchID={match_id}",
                "text": "",
                "score": None,
            },
        }
    else:
        alias_table = _load_alias_table()
        schedule_bu = client_factory(f"{session_prefix}_sched")
        try:
            found = _find_match_id(
                schedule_bu,
                args.league,
                args.team1,
                args.team2,
                date_hint=args.date,
                time_hint=args.time,
                alias_table=alias_table,
            )
        finally:
            schedule_bu.close()
        match_id = found["match_id"]

    # Output path policy:
    # - If a snapshot with the same match_id already exists in out_dir, overwrite it.
    # - Else: if --overwrite is set, use a stable event filename.
    # - Else: create a timestamped filename.
    out_path: Path
    existing = None if args.no_matchid_dedupe else _find_existing_snapshot_by_match_id(out_dir, str(match_id))
    if existing:
        out_path = existing
    else:
        if args.overwrite:
            filename = f"{_safe_filename(event_name)}.json"
        else:
            filename = f"{_safe_filename(event_name)}_{_now_stamp()}.json"
        out_path = out_dir / filename

    payload: Dict[str, Any] = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "driver": args.driver,
        "chrome": chrome_meta if chrome_meta else None,
        "league": args.league,
        "event": event_name,
        "match_id": match_id,
        "match_date": args.date or "",
        "home_team": args.team1,
        "away_team": args.team2,
        "match_time": found["schedule_row"].get("text", ""),
        "schedule": found["schedule_row"],
        "欧赔": _extract_europe_with_fallback(match_id, found["schedule_row"]["href"], client_factory, f"{session_prefix}_eu"),
        "亚值": _extract_asian_with_fallback(match_id, found["schedule_row"]["href"], client_factory, f"{session_prefix}_as"),
        "大小球": _extract_totals_with_fallback(match_id, found["schedule_row"]["href"], client_factory, f"{session_prefix}_ou"),
        "凯利": _extract_kelly_full_fallback(match_id, found["schedule_row"]["href"], client_factory, f"{session_prefix}_ke"),
    }
    if args.overwrite:
        payload["_note"] = "overwrite=true: same event writes to a stable filename"
    if existing:
        payload["_note_match_id_overwrite"] = f"match_id={match_id}: overwrote existing snapshot file"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()

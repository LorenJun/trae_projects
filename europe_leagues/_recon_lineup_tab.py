"""One-off read-only recon: find okooo lineup/首发 tab for a given MatchID.

Reuses the project's local-chrome session (stealth + warm-up + mobile profile)
to land on the match hub, dump all nav links + visible text, then probe a few
candidate lineup page names. Read-only: writes nothing to runtime/snapshots.
"""
import json
import sys
import time

import okooo_save_snapshot as oss

MATCH_ID = sys.argv[1] if len(sys.argv) > 1 else "1315854"


def dump_links_and_text(bu, tag):
    js = r"""
(() => {
  const links = Array.from(document.querySelectorAll('a')).map(a => ({
    t: (a.innerText || '').replace(/\s+/g,' ').trim(),
    h: a.getAttribute('href') || ''
  })).filter(x => x.t && x.t.length <= 10);
  const body = document.body ? document.body.innerText.replace(/\s+/g,' ').trim() : '';
  const lineupHit = /\u9635\u5bb9|\u9996\u53d1|\u9635\u578b|\u62a5\u51fa|\u51fa\u573a\u540d\u5355|\u53d1\u5e03/.test(body);
  return JSON.stringify({url: location.href, title: document.title, bodyLen: body.length,
    links: links, lineupHit: lineupHit, bodySample: body.slice(0, 600)});
})()
"""
    res = bu.eval_json(js)
    print(f"\n===== [{tag}] {res.get('url')} =====")
    print(f"title={res.get('title')!r} bodyLen={res.get('bodyLen')} lineupHit={res.get('lineupHit')}")
    links = res.get("links") or []
    navish = [x for x in links if any(k in x["t"] for k in ("阵容", "首发", "阵型", "情报", "亚指", "欧指", "大小", "凯利", "分析", "资料", "对阵", "数据", "前瞻", "推介"))]
    print("nav-ish links:")
    for x in navish:
        print("   ", x["t"], "->", x["h"])
    print("bodySample:", res.get("bodySample"))
    return res


def main():
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    user_data_dir = str(oss._default_data_root() / "chrome_profile")
    meta = oss._ensure_local_chrome(9222, chrome_path, user_data_dir)
    port = int(meta.get("port") or 9222)
    bu = oss.LocalChromeSession(port=port, session_name="recon_lineup")
    try:
        hub = f"https://m.okooo.com/match/history.php?MatchID={MATCH_ID}"
        state = oss._open_ready(bu, hub, settle_seconds=3.5)
        if oss._is_blocked_text(state):
            print("BLOCKED on hub:", state[:300])
            return
        dump_links_and_text(bu, "hub")

        # Try clicking a 阵容/首发/情报 tab if present
        click = oss._click_visible_text(bu, ["阵容", "首发", "首发阵容", "情报", "前瞻"], settle_seconds=3.5)
        print("\nclick result:", json.dumps(click, ensure_ascii=False)[:300])
        time.sleep(3.0)

        # form.php is lazy-loaded; scroll to bottom in steps to render the full XI.
        for _ in range(8):
            bu.eval_json("(() => { window.scrollBy(0, document.body.scrollHeight); return JSON.stringify({y:window.scrollY}); })()")
            time.sleep(1.2)
        bu.eval_json("(() => { window.scrollTo(0, 0); return '{}'; })()")
        time.sleep(0.8)

        full = bu.eval_json(r"(() => JSON.stringify({u:location.href, len:(document.body?document.body.innerText.length:0), text:(document.body?document.body.innerText.replace(/\s+/g,' ').trim():'')}))()")
        print(f"\n===== [form.php FULL after scroll] {full.get('u')} len={full.get('len')} =====")
        print(full.get("text"))
        return  # recon done; skip blind page-name probing
    finally:
        bu.close()


if __name__ == "__main__":
    main()

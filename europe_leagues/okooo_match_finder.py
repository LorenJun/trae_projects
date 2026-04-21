#!/usr/bin/env python3
"""
澳客网比赛查找工具
根据球队名称自动搜索并验证正确的比赛ID
"""

import re
from playwright.sync_api import sync_playwright


class OkoooMatchFinder:
    """澳客网比赛ID查找器 - 纯在线搜索方式"""

    def find_match_id(self, team1, team2, league_hint=None):
        """
        查找比赛ID - 纯在线搜索方式，无缓存文件

        Args:
            team1: 主队名称（中文或简称）
            team2: 客队名称（中文或简称）
            league_hint: 联赛提示（如"英超"、"意甲"等）

        Returns:
            str: 比赛ID，如果未找到返回None
        """
        print(f"正在搜索: {team1} vs {team2}")
        return self._search_online(team1, team2, league_hint)

    def _normalize_team(self, name: str) -> str:
        """Normalize team name for fuzzy matching (no external deps)."""
        if not name:
            return ""
        text = name.strip().replace(" ", "")
        # Common suffixes/keywords
        for suffix in ["足球俱乐部", "俱乐部", "队", "FC", "fc"]:
            if text.endswith(suffix):
                text = text[: -len(suffix)]
        return text

    def _text_has_teams(self, text: str, team1: str, team2: str) -> bool:
        """Best-effort check whether page text mentions the matchup."""
        if not text:
            return False
        t = text.replace(" ", "")
        a = self._normalize_team(team1)
        b = self._normalize_team(team2)
        if not a or not b:
            return False
        # When abbreviations exist, allow partial containment.
        return (a in t and b in t) or (b in t and a in t)

    def _extract_match_ids(self, html: str):
        """Extract possible match ids from HTML/url patterns on mobile/desktop."""
        if not html:
            return []
        ids = set()
        # Mobile odds pages
        for mid in re.findall(r"[?&]MatchID=(\d+)", html, flags=re.IGNORECASE):
            ids.add(mid)
        # Desktop match pages
        for mid in re.findall(r"/soccer/match/(\d+)/", html):
            ids.add(mid)
        # Some pages may omit trailing slash
        for mid in re.findall(r"/soccer/match/(\d+)(?:/|\\b)", html):
            ids.add(mid)
        return list(ids)

    def _is_blocked(self, text: str) -> bool:
        if not text:
            return False
        return ("访问被阻断" in text) or ("安全威胁" in text) or ("<title>405</title>" in text) or ("Sorry, your request has been blocked" in text)

    def _search_online(self, team1, team2, league_hint=None):
        """在线搜索比赛ID（从移动端热门赛事入口查找 MatchID）"""

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )

            page = context.new_page()

            try:
                # Mobile "hot competitions" entry. This is the only fixed entry point;
                # we then follow its links to locate specific match pages that contain MatchID.
                start_url = "https://m.okooo.com/saishi/remen/"
                print(f"  从热门赛事页查找: {start_url}")
                page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)

                html = page.content()
                if self._is_blocked(html):
                    print("  ✗ 热门赛事页被阻断(405/安全威胁)")
                    browser.close()
                    return None

                # Collect candidate urls from anchors; some are relative.
                js_collect = """
                    () => {
                        const out = [];
                        const as = Array.from(document.querySelectorAll('a[href]'));
                        for (const a of as) {
                            const href = a.getAttribute('href') || '';
                            const text = (a.textContent || '').trim();
                            if (!href) continue;
                            out.push({ href, text });
                        }
                        return out;
                    }
                """
                raw_links = page.evaluate(js_collect) or []

                def abs_url(href: str) -> str:
                    if not href:
                        return ""
                    if href.startswith("http://") or href.startswith("https://"):
                        return href
                    if href.startswith("//"):
                        return "https:" + href
                    if href.startswith("/"):
                        return "https://m.okooo.com" + href
                    return "https://m.okooo.com/" + href

                # Prefer links likely to lead to match lists/details.
                candidates = []
                for item in raw_links:
                    href = abs_url(item.get("href", ""))
                    text = item.get("text", "")
                    if not href:
                        continue
                    if "saishi" in href or "match" in href or "odds.php" in href or "MatchID=" in href:
                        candidates.append((href, text))

                # Also include any match-id patterns found directly in the HTML (rare).
                for mid in self._extract_match_ids(html):
                    candidates.append((f"https://m.okooo.com/match/odds.php?MatchID={mid}", ""))

                # Dedupe while keeping order.
                seen = set()
                ordered = []
                for href, text in candidates:
                    key = href
                    if key in seen:
                        continue
                    seen.add(key)
                    ordered.append((href, text))

                # If league hint is provided, prioritize links whose text includes it.
                if league_hint:
                    ordered.sort(key=lambda x: 0 if league_hint in (x[1] or "") else 1)

                print(f"  候选入口链接数: {len(ordered)}")

                # Traverse a limited number of pages to avoid hammering the site.
                max_pages = 25 if league_hint else 15
                for href, text in ordered[:max_pages]:
                    try:
                        page.goto(href, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(2500)
                        sub_html = page.content()
                        if self._is_blocked(sub_html):
                            continue

                        # If this URL already includes MatchID, validate quickly.
                        m = re.search(r"[?&]MatchID=(\d+)", href, flags=re.IGNORECASE)
                        if m:
                            match_id = m.group(1)
                            if self._quick_verify(page, match_id, team1, team2):
                                print(f"  ✓ 命中 MatchID: {match_id} (from url)")
                                browser.close()
                                return match_id

                        # Otherwise, attempt to extract MatchID from the page.
                        for match_id in self._extract_match_ids(sub_html):
                            if self._quick_verify(page, match_id, team1, team2):
                                print(f"  ✓ 命中 MatchID: {match_id}")
                                browser.close()
                                return match_id

                        # As fallback, check if the page text mentions both teams,
                        # then extract ids (helps when ids appear in JS snippets).
                        try:
                            inner_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
                        except Exception:
                            inner_text = ""
                        if self._text_has_teams(inner_text, team1, team2):
                            for match_id in self._extract_match_ids(inner_text):
                                if self._quick_verify(page, match_id, team1, team2):
                                    print(f"  ✓ 命中 MatchID: {match_id}")
                                    browser.close()
                                    return match_id

                    except Exception:
                        # Keep best-effort behavior; continue to next candidate.
                        continue

                browser.close()
                return None

            except Exception as e:
                print(f"  搜索出错: {e}")
                browser.close()
                return None

    def _quick_verify(self, page, match_id, team1, team2):
        """快速验证比赛ID"""
        try:
            # Prefer mobile odds page because desktop match pages are more likely blocked.
            url = f"https://m.okooo.com/match/odds.php?MatchID={match_id}"
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)
            html = page.content()
            if self._is_blocked(html):
                return False

            try:
                inner_text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                inner_text = ""
            # Require both teams to reduce false positives.
            if self._text_has_teams(inner_text, team1, team2):
                return True

            # Fallback to title check (best-effort).
            title = page.title() or ""
            if "405" in title:
                return False
            return (team1 in title and team2 in title) or (team1 in title or team2 in title)

        except Exception:
            return False

    def verify_match_id(self, match_id, expected_teams):
        """验证比赛ID是否正确"""
        print(f"  验证比赛ID: {match_id}")

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                )

                page = context.new_page()
                result = self._do_verify(page, match_id, expected_teams)

                browser.close()
                return result
        except Exception as e:
            print(f"    ⚠ 验证跳过: {e}")
            return True

    def _do_verify(self, page, match_id, expected_teams):
        """执行实际的验证逻辑"""
        try:
            url = f'https://www.okooo.com/soccer/match/{match_id}/'
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)

            title = page.title()
            print(f"    页面标题: {title}")

            if '405' in title:
                print("    ✗ 页面被阻断(405)")
                return False

            found_teams = []
            for team in expected_teams:
                if team in title:
                    found_teams.append(team)
                    print(f"    ✓ 包含球队: {team}")

            if len(found_teams) >= 2:
                print("    ✓ 验证通过")
                return True
            elif len(found_teams) == 1:
                print("    ⚠ 只找到一个球队")
                return True
            else:
                print("    ✗ 未找到期望的球队")
                return False

        except Exception as e:
            print(f"    ✗ 验证出错: {e}")
            return False

    def _get_league_url(self, league_hint):
        """根据联赛提示获取联赛页面URL"""
        league_urls = []

        if '亚冠' in league_hint or '亚' in league_hint:
            league_urls.extend([
                'https://www.okooo.com/soccer/league/167/schedule/',
                'https://www.okooo.com/soccer/league/167/',
            ])

        if '英超' in league_hint or ('超' in league_hint and '英' in league_hint):
            league_urls.extend([
                'https://www.okooo.com/soccer/league/17/schedule/',
                'https://www.okooo.com/soccer/league/17/',
            ])

        if '意甲' in league_hint or ('甲' in league_hint and '意' in league_hint):
            league_urls.extend([
                'https://www.okooo.com/soccer/league/23/schedule/',
                'https://www.okooo.com/soccer/league/23/',
            ])

        if '西甲' in league_hint or ('甲' in league_hint and '西' in league_hint):
            league_urls.extend([
                'https://www.okooo.com/soccer/league/8/schedule/',
                'https://www.okooo.com/soccer/league/8/',
            ])

        if '德甲' in league_hint or ('甲' in league_hint and '德' in league_hint):
            league_urls.extend([
                'https://www.okooo.com/soccer/league/35/schedule/',
                'https://www.okooo.com/soccer/league/35/',
            ])

        if '法甲' in league_hint or ('甲' in league_hint and '法' in league_hint):
            league_urls.extend([
                'https://www.okooo.com/soccer/league/34/schedule/',
                'https://www.okooo.com/soccer/league/34/',
            ])

        if '法乙' in league_hint or '乙' in league_hint:
            league_urls.extend([
                'https://www.okooo.com/soccer/league/182/schedule/110445/',
                'https://www.okooo.com/soccer/league/182/',
            ])

        if '中超' in league_hint:
            league_urls.extend([
                'https://www.okooo.com/soccer/league/649/schedule/',
                'https://www.okooo.com/soccer/league/649/',
            ])

        if not league_urls:
            league_urls = ['https://www.okooo.com/soccer/']

        return league_urls


def find_and_fetch_odds(team1, team2, league_hint=None, headless=True):
    """一站式查找比赛并获取赔率"""
    finder = OkoooMatchFinder()

    print(f"🔍 查找比赛: {team1} vs {team2}")
    match_id = finder.find_match_id(team1, team2, league_hint)

    if not match_id:
        print("✗ 未找到比赛ID")
        return None

    print(f"✓ 找到比赛ID: {match_id}")

    odds_url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
    print(f"📊 赔率链接: {odds_url}")

    print("📥 获取赔率数据...")
    from okooo_helper_v2 import OkoooScraper

    scraper = OkoooScraper(headless=headless)
    scraper.start()

    try:
        odds_data = scraper.extract_odds(match_id)

        if odds_data:
            print(f"✓ 成功获取 {len(odds_data.get('bookmakers', {}))} 家博彩公司数据")
            return {
                'match_id': match_id,
                'odds_url': odds_url,
                'odds_data': odds_data
            }
        else:
            print("✗ 获取赔率失败")
            return None
    finally:
        scraper.close()


if __name__ == '__main__':
    import sys

    if len(sys.argv) >= 3:
        team1 = sys.argv[1]
        team2 = sys.argv[2]
        league_hint = sys.argv[3] if len(sys.argv) > 3 else None

        finder = OkoooMatchFinder()
        match_id = finder.find_match_id(team1, team2, league_hint)

        if match_id:
            print(f"\n✅ 比赛ID: {match_id}")
            print(f"   欧指: https://www.okooo.com/soccer/match/{match_id}/odds/")
            print(f"   亚盘: https://www.okooo.com/soccer/match/{match_id}/ah/")
        else:
            print("\n❌ 未找到比赛")
    else:
        print("用法: python okooo_match_finder.py 球队1 球队2 [联赛提示]")
        print("示例: python okooo_match_finder.py 水晶宫 西汉孤 英超")

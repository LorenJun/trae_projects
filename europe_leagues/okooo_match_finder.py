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

    def _search_online(self, team1, team2, league_hint=None):
        """在线搜索比赛ID"""

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )

            page = context.new_page()

            try:
                print("  尝试从首页获取...")
                page.goto('https://www.okooo.com/soccer/',
                         wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(3000)

                js_code = f'''
                    () => {{
                        const results = [];
                        const links = document.querySelectorAll('a[href*="/soccer/match/"]');

                        for (const link of links) {{
                            const text = link.textContent || '';
                            const href = link.href || '';

                            const hasTeam1 = text.includes("{team1}") || text.includes("{team2}");
                            const hasTeam2 = text.includes("{team2}") || text.includes("{team1}");

                            if (hasTeam1) {{
                                const match = href.match(/soccer\\/match\\/(\\d+)/);
                                if (match) {{
                                    results.push({{
                                        match_id: match[1],
                                        text: text.trim(),
                                        href: href
                                    }});
                                }}
                            }}
                        }}

                        return results;
                    }}
                '''

                match_links = page.evaluate(js_code)

                if match_links:
                    print(f"  ✓ 找到 {len(match_links)} 个可能结果")
                    for result in match_links[:3]:
                        match_id = result['match_id']
                        print(f"    候选: ID={match_id}, 内容={result['text'][:50]}")

                        if self._quick_verify(page, match_id, team1, team2):
                            print(f"  ✓ 验证通过: {match_id}")
                            browser.close()
                            return match_id

                if league_hint:
                    league_urls = self._get_league_url(league_hint)
                    for league_url in league_urls:
                        print(f"  尝试联赛页面: {league_url}")
                        try:
                            page.goto(league_url, wait_until='domcontentloaded', timeout=30000)
                            page.wait_for_timeout(5000)

                            js_code2 = f'''
                                () => {{
                                    const results = [];
                                    const links = document.querySelectorAll('a[href*="/soccer/match/"]');

                                    for (const link of links) {{
                                        const text = link.textContent || '';
                                        const href = link.href || '';

                                        if (text.includes("{team1}") || text.includes("{team2}")) {{
                                            const match = href.match(/soccer\\/match\\/(\\d+)/);
                                            if (match) {{
                                                results.push({{
                                                    match_id: match[1],
                                                    text: text.trim()
                                                }});
                                            }}
                                        }}
                                    }}

                                    return results;
                                }}
                            '''

                            match_links = page.evaluate(js_code2)

                            if match_links:
                                print(f"    找到 {len(match_links)} 个结果")
                                for result in match_links[:2]:
                                    match_id = result['match_id']
                                    if self._quick_verify(page, match_id, team1, team2):
                                        print(f"    ✓ 验证通过: {match_id}")
                                        browser.close()
                                        return match_id

                            print("    尝试从HTML源码提取...")
                            html = page.content()
                            all_match_ids = re.findall(r'/soccer/match/(\d+)/', html)
                            unique_ids = list(set(all_match_ids))
                            print(f"    页面中找到 {len(unique_ids)} 个 match_id")

                            for match_id in unique_ids:
                                if self._quick_verify(page, match_id, team1, team2):
                                    print(f"    ✓ 验证通过: {match_id}")
                                    browser.close()
                                    return match_id

                        except Exception as e:
                            print(f"    访问失败: {e}")
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
            url = f'https://www.okooo.com/soccer/match/{match_id}/'
            page.goto(url, wait_until='domcontentloaded', timeout=15000)
            page.wait_for_timeout(2000)

            title = page.title()

            if '405' in title:
                return False

            has_team1 = team1 in title
            has_team2 = team2 in title

            return has_team1 or has_team2

        except:
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
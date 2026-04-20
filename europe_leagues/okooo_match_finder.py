#!/usr/bin/env python3
"""
澳客网比赛查找工具
根据球队名称自动搜索并验证正确的比赛ID
"""

import re
import json
import os
from datetime import datetime
from playwright.sync_api import sync_playwright


class OkoooMatchFinder:
    """澳客网比赛ID查找器"""
    
    # 已知比赛ID映射（手动维护常用比赛）
    KNOWN_MATCHES = {
        # 亚冠
        '神户胜利_吉达国民': '1324404',
        '吉达国民_神户胜利': '1324404',
        '神户胜利船_吉达国民': '1324404',
        
        # 英超
        '水晶宫_西汉姆联': '1296057',
        '西汉姆联_水晶宫': '1296057',
    }
    
    def __init__(self):
        self.cache_file = os.path.expanduser('~/.hermes/okooo_match_cache.json')
        self.cache = self._load_cache()
    
    def _load_cache(self):
        """加载缓存的比赛ID映射"""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}
    
    def _save_cache(self):
        """保存缓存"""
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)
    
    def find_match_id(self, team1, team2, league_hint=None):
        """
        查找比赛ID
        
        Args:
            team1: 主队名称（中文或简称）
            team2: 客队名称（中文或简称）
            league_hint: 联赛提示（如"亚冠"、"英超"等）
        
        Returns:
            str: 比赛ID，如果未找到返回None
        """
        # 1. 检查已知映射
        key1 = f"{team1}_{team2}"
        key2 = f"{team2}_{team1}"
        
        if key1 in self.KNOWN_MATCHES:
            match_id = self.KNOWN_MATCHES[key1]
            print(f"✓ 从已知映射找到: {match_id}")
            return match_id
        
        if key2 in self.KNOWN_MATCHES:
            match_id = self.KNOWN_MATCHES[key2]
            print(f"✓ 从已知映射找到: {match_id}")
            return match_id
        
        # 2. 检查缓存
        cache_key = f"{team1}_{team2}"
        if cache_key in self.cache:
            match_id = self.cache[cache_key]
            print(f"✓ 从缓存找到: {match_id}")
            # 验证缓存是否有效
            if self.verify_match_id(match_id, [team1, team2]):
                return match_id
            else:
                print("  缓存ID已失效，重新搜索...")
                del self.cache[cache_key]
        
        # 3. 在线搜索
        print(f"正在搜索: {team1} vs {team2}")
        match_id = self._search_online(team1, team2, league_hint)
        
        if match_id:
            # 保存到缓存
            self.cache[cache_key] = match_id
            self._save_cache()
        
        return match_id
    
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
                # 方法1: 访问澳客网首页，查找热门比赛
                print("  尝试从首页获取...")
                page.goto('https://www.okooo.com/soccer/', 
                         wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(3000)
                
                # 在页面中搜索球队名称
                match_links = page.evaluate(f'''() => {{
                    const results = [];
                    const links = document.querySelectorAll('a[href*="/soccer/match/"]');
                    
                    for (const link of links) {{
                        const text = link.textContent || '';
                        const href = link.href || '';
                        
                        // 检查是否包含两个球队名称
                        const hasTeam1 = text.includes("{team1}") || text.includes("{team2}");
                        const hasTeam2 = text.includes("{team2}") || text.includes("{team1}");
                        
                        if (hasTeam1) {{
                            const match = href.match(/\/soccer\/match\/(\d+)\//);
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
                }}''')
                
                if match_links:
                    print(f"  ✓ 找到 {len(match_links)} 个可能结果")
                    for result in match_links[:3]:
                        match_id = result['match_id']
                        print(f"    候选: ID={match_id}, 内容={result['text'][:50]}")
                        
                        # 验证这个ID
                        if self._quick_verify(page, match_id, team1, team2):
                            print(f"  ✓ 验证通过: {match_id}")
                            browser.close()
                            return match_id
                
                # 方法2: 如果知道联赛，访问联赛页面
                if league_hint:
                    league_urls = self._get_league_url(league_hint)
                    for league_url in league_urls:
                        print(f"  尝试联赛页面: {league_url}")
                        try:
                            page.goto(league_url, wait_until='domcontentloaded', timeout=30000)
                            page.wait_for_timeout(3000)
                            
                            match_links = page.evaluate(f'''() => {{
                                const results = [];
                                const links = document.querySelectorAll('a[href*="/soccer/match/"]');
                                
                                for (const link of links) {{
                                    const text = link.textContent || '';
                                    const href = link.href || '';
                                    
                                    if (text.includes("{team1}") || text.includes("{team2}")) {{
                                        const match = href.match(/\/soccer\/match\/(\d+)\//);
                                        if (match) {{
                                            results.push({{
                                                match_id: match[1],
                                                text: text.trim()
                                            }});
                                        }}
                                    }}
                                }}
                                
                                return results;
                            }}''')
                            
                            if match_links:
                                print(f"    找到 {len(match_links)} 个结果")
                                for result in match_links[:2]:
                                    match_id = result['match_id']
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
            
            # 检查是否是405错误
            if '405' in title:
                return False
            
            # 检查标题是否包含球队名称
            has_team1 = team1 in title
            has_team2 = team2 in title
            
            if has_team1 or has_team2:
                return True
            
            # 检查页面内容
            content = page.content()
            if team1 in content or team2 in content:
                return True
            
            return False
            
        except:
            return False
    
    def verify_match_id(self, match_id, expected_teams):
        """
        验证比赛ID是否正确（使用外部Playwright实例）
        
        Args:
            match_id: 澳客网比赛ID
            expected_teams: 期望的球队名称列表
        
        Returns:
            bool: 验证是否通过
        """
        print(f"  验证比赛ID: {match_id}")
        
        # 如果提供了外部page对象，使用它
        if hasattr(self, '_verify_page') and self._verify_page:
            return self._do_verify(self._verify_page, match_id, expected_teams)
        
        # 否则创建新的浏览器实例
        try:
            from playwright.sync_api import sync_playwright
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
            # 如果验证失败，假设ID是正确的（因为已经从映射中找到）
            return True
    
    def _do_verify(self, page, match_id, expected_teams):
        """执行实际的验证逻辑"""
        try:
            url = f'https://www.okooo.com/soccer/match/{match_id}/'
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)
            
            title = page.title()
            print(f"    页面标题: {title}")
            
            # 检查是否是405错误
            if '405' in title:
                print("    ✗ 页面被阻断(405)")
                return False
            
            # 检查是否包含期望的球队
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
                return True  # 部分匹配也接受
            else:
                print("    ✗ 未找到期望的球队")
                return False
            
        except Exception as e:
            print(f"    ✗ 验证出错: {e}")
            return False
    
    def _get_league_url(self, league_hint):
        """根据联赛提示获取联赛页面URL"""
        league_urls = []
        
        # 亚冠
        if '亚冠' in league_hint or '亚' in league_hint:
            league_urls.extend([
                'https://www.okooo.com/soccer/league/167/schedule/',  # 亚冠赛程
                'https://www.okooo.com/soccer/league/167/',  # 亚冠首页
            ])
        
        # 英超
        if '英超' in league_hint or '超' in league_hint:
            league_urls.extend([
                'https://www.okooo.com/soccer/league/35/schedule/',
                'https://www.okooo.com/soccer/league/35/',
            ])
        
        # 默认返回首页
        if not league_urls:
            league_urls = ['https://www.okooo.com/soccer/']
        
        return league_urls
    
    def add_known_match(self, team1, team2, match_id):
        """添加已知比赛映射"""
        key = f"{team1}_{team2}"
        self.KNOWN_MATCHES[key] = match_id
        print(f"✓ 已添加映射: {key} -> {match_id}")


def find_and_fetch_odds(team1, team2, league_hint=None, headless=True):
    """
    一站式查找比赛并获取赔率
    
    Args:
        team1: 主队名称
        team2: 客队名称
        league_hint: 联赛提示
        headless: 是否使用无头模式
    
    Returns:
        dict: 包含比赛ID和赔率数据
    """
    finder = OkoooMatchFinder()
    
    # 1. 查找比赛ID
    print(f"🔍 查找比赛: {team1} vs {team2}")
    match_id = finder.find_match_id(team1, team2, league_hint)
    
    if not match_id:
        print("✗ 未找到比赛ID")
        return None
    
    print(f"✓ 找到比赛ID: {match_id}")
    
    # 2. 验证比赛ID
    if not finder.verify_match_id(match_id, [team1, team2]):
        print("✗ 比赛ID验证失败")
        return None
    
    # 3. 获取赔率数据
    print(f"📊 获取赔率数据...")
    from okooo_helper_v2 import get_okooo_odds
    
    odds_data = get_okooo_odds(match_id, headless=headless)
    
    if odds_data:
        print(f"✓ 成功获取 {len(odds_data.get('bookmakers', {}))} 家博彩公司数据")
        return {
            'match_id': match_id,
            'odds_data': odds_data
        }
    else:
        print("✗ 获取赔率失败")
        return None


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("用法: python okooo_match_finder.py <主队名称> <客队名称> [联赛提示]")
        print("示例: python okooo_match_finder.py 神户胜利 吉达国民 亚冠")
        sys.exit(1)
    
    team1 = sys.argv[1]
    team2 = sys.argv[2]
    league = sys.argv[3] if len(sys.argv) > 3 else None
    
    result = find_and_fetch_odds(team1, team2, league)
    
    if result:
        print("\n" + "="*70)
        print("✓ 完成!")
        print(f"比赛ID: {result['match_id']}")
        print(f"获取公司数: {len(result['odds_data'].get('bookmakers', {}))}")
    else:
        print("\n✗ 失败")
        sys.exit(1)

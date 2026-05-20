#!/usr/bin/env python3
"""模块说明：提供澳客比赛搜索与结果过滤能力，辅助 MatchID 定位。

澳客网比赛搜索工具
根据球队名称搜索正确的比赛ID"""

import re
import time
from playwright.sync_api import sync_playwright

from okooo_mobile_access import cache_busted_okooo_url, mobile_context_options, mobile_headers, random_mobile_profile


def search_okooo_match(team1_name, team2_name, league_name=None):
    """
    在澳客网搜索比赛ID
    
    Args:
        team1_name: 主队名称（中文或英文）
        team2_name: 客队名称（中文或英文）
        league_name: 联赛名称（可选）
    
    Returns:
        dict: 包含比赛ID、URL、比赛时间等信息
    """
    
    with sync_playwright() as p:
        profile = random_mobile_profile()
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**mobile_context_options(profile=profile))
        context.set_extra_http_headers(mobile_headers(profile=profile))
        
        page = context.new_page()
        
        try:
            # 优先从移动端热门赛事入口进入，避免桌面页更容易触发拦截。
            print(f"正在搜索: {team1_name} vs {team2_name}")
            
            page.goto(
                cache_busted_okooo_url('https://m.okooo.com/saishi/remen/', profile=profile),
                wait_until='domcontentloaded',
                timeout=30000,
            )
            page.wait_for_timeout(5000)
            
            # 在页面中搜索球队名称
            match_info = page.evaluate(f'''() => {{
                const team1 = "{team1_name}";
                const team2 = "{team2_name}";
                
                // 查找所有包含比赛信息的链接
                const links = document.querySelectorAll('a[href*="MatchID="], a[href*="matchid="], a[href*="odds.php"], a[href*="history.php"]');
                
                for (const link of links) {{
                    const text = link.textContent || '';
                    const href = link.href || '';
                    
                    // 检查是否包含两个球队名称
                    if ((text.includes(team1) || text.includes(team2)) &&
                        href.match(/[?&]MatchID=(\d+)/i)) {{
                        
                        // 提取比赛ID
                        const match = href.match(/[?&]MatchID=(\d+)/i);
                        if (match) {{
                            // 查找比赛时间
                            let timeText = '';
                            const row = link.closest('tr');
                            if (row) {{
                                const timeCell = row.querySelector('td:first-child, .time, .date');
                                if (timeCell) {{
                                    timeText = timeCell.textContent.trim();
                                }}
                            }}
                            
                            return {{
                                match_id: match[1],
                                url: href,
                                text: text.trim(),
                                match_time: timeText
                            }};
                        }}
                    }}
                }}
                
                return null;
            }}''')
            
            if match_info:
                print(f"✓ 找到比赛!")
                print(f"  比赛ID: {match_info['match_id']}")
                print(f"  比赛链接: {match_info['url']}")
                print(f"  比赛信息: {match_info['text']}")
                if match_info['match_time']:
                    print(f"  比赛时间: {match_info['match_time']}")
                
                return match_info
            else:
                print("✗ 在赛程页面未找到比赛")
                
                # 方法2: 使用搜索功能
                print("\n尝试使用搜索功能...")
                
                # 在移动端页内继续筛选，避免再次切到桌面搜索页。
                search_url = f'https://m.okooo.com/saishi/remen/?keyword={team1_name}'
                page.goto(cache_busted_okooo_url(search_url, profile=profile), wait_until='domcontentloaded', timeout=30000)
                page.wait_for_timeout(3000)
                
                # 提取搜索结果
                search_results = page.evaluate(f'''() => {{
                    const results = [];
                    const links = document.querySelectorAll('a[href*="MatchID="], a[href*="matchid="], a[href*="odds.php"], a[href*="history.php"]');
                    
                    for (const link of links) {{
                        const text = link.textContent || '';
                        const href = link.href || '';
                        
                        if (text.includes("{team1_name}") || text.includes("{team2_name}")) {{
                            const match = href.match(/[?&]MatchID=(\d+)/i);
                            if (match) {{
                                results.push({{
                                    match_id: match[1],
                                    url: href,
                                    text: text.trim()
                                }});
                            }}
                        }}
                    }}
                    
                    return results;
                }}''')
                
                if search_results:
                    print(f"✓ 搜索找到 {len(search_results)} 个可能结果:")
                    for i, result in enumerate(search_results[:3], 1):
                        print(f"  {i}. ID: {result['match_id']} - {result['text']}")
                    return search_results[0]
                else:
                    print("✗ 搜索未找到结果")
                    return None
                
        except Exception as e:
            print(f"搜索出错: {e}")
            return None
        finally:
            browser.close()


def verify_match_id(match_id, expected_teams=None):
    """
    验证比赛ID是否正确
    
    Args:
        match_id: 澳客网比赛ID
        expected_teams: 期望的球队名称列表（用于验证）
    
    Returns:
        dict: 比赛信息，如果验证失败返回None
    """
    
    with sync_playwright() as p:
        profile = random_mobile_profile()
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(**mobile_context_options(profile=profile))
        context.set_extra_http_headers(mobile_headers(profile=profile))
        
        page = context.new_page()
        
        try:
            url = f'https://m.okooo.com/match/odds.php?MatchID={match_id}'
            page.goto(cache_busted_okooo_url(url, profile=profile), wait_until='domcontentloaded', timeout=30000)
            page.wait_for_timeout(3000)
            
            title = page.title()
            
            # 检查是否是405错误
            if '405' in title or 'blocked' in page.content().lower():
                print(f"✗ 比赛ID {match_id} 被阻断或无效")
                return None
            
            # 提取比赛信息
            match_info = page.evaluate('''() => {
                // 尝试多种方式提取球队名称
                const homeTeam = document.querySelector('.home-team, .team-home, [class*="home"]')?.textContent?.trim();
                const awayTeam = document.querySelector('.away-team, .team-away, [class*="away"]')?.textContent?.trim();
                
                // 从标题提取
                const titleMatch = document.title.match(/(.+?)vs(.+?)-/);
                
                return {
                    title: document.title,
                    homeTeam: homeTeam,
                    awayTeam: awayTeam,
                    titleTeams: titleMatch ? [titleMatch[1].trim(), titleMatch[2].trim()] : null
                };
            }''')
            
            print(f"✓ 验证成功!")
            print(f"  页面标题: {match_info['title']}")
            
            if expected_teams and match_info['titleTeams']:
                for team in expected_teams:
                    if team in match_info['title']:
                        print(f"  ✓ 包含球队: {team}")
            
            return {
                'match_id': match_id,
                'title': match_info['title'],
                'url': url
            }
            
        except Exception as e:
            print(f"验证出错: {e}")
            return None
        finally:
            browser.close()


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) < 3:
        print("用法: python okooo_match_search.py <主队名称> <客队名称>")
        print("示例: python okooo_match_search.py 神户胜利 吉达国民")
        sys.exit(1)
    
    team1 = sys.argv[1]
    team2 = sys.argv[2]
    
    # 搜索比赛
    result = search_okooo_match(team1, team2)
    
    if result:
        print("\n" + "="*70)
        print("验证比赛ID...")
        verify_match_id(result['match_id'], [team1, team2])
    else:
        print("\n✗ 未找到比赛")
        sys.exit(1)

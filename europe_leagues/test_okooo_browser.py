#!/usr/bin/env python3
"""
测试使用 Playwright 获取澳客网赔率数据
"""

import re
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent / 'venv_browser' / 'lib' / 'python3.12' / 'site-packages'))

from playwright.sync_api import sync_playwright

def parse_handicap(handicap_text: str):
    """解析盘口文本为数值"""
    if not handicap_text:
        return None
    
    try:
        text = handicap_text.strip()
        
        # 数字直接解析
        if re.match(r'^-?\d+\.?\d*$', text):
            return float(text)
        
        # 中文盘口映射
        handicap_map = {
            "平手": 0,
            "平手/半球": 0.25,
            "半球": 0.5,
            "半球/一球": 0.75,
            "一球": 1,
            "一球/球半": 1.25,
            "球半": 1.5,
            "球半/两球": 1.75,
            "两球": 2,
            "两球/两球半": 2.25,
            "两球半": 2.5,
            "受让平手": 0,
            "受让平手/半球": -0.25,
            "受让半球": -0.5,
            "受让半球/一球": -0.75,
            "受让一球": -1,
            "受让一球/球半": -1.25,
            "受让球半": -1.5,
        }
        
        if text in handicap_map:
            return handicap_map[text]
        
        # 尝试提取数字
        numbers = re.findall(r'-?\d+\.?\d*', text)
        if numbers:
            return float(numbers[0])
            
    except Exception:
        pass
    
    return None


def fetch_okooo_odds(match_id: str):
    """使用 Playwright 获取澳客网赔率数据"""
    odds_data = {
        "home_win_initial": None,
        "draw_initial": None,
        "away_win_initial": None,
        "home_win_final": None,
        "draw_final": None,
        "away_win_final": None,
        "asian_handicap_initial": None,
        "asian_home_water_initial": None,
        "asian_away_water_initial": None,
        "asian_handicap_final": None,
        "asian_home_water_final": None,
        "asian_away_water_final": None,
    }
    
    # 欧赔页面 - 使用移动端
    ouzhi_url = f"https://m.okooo.com/match/odds.php?MatchID={match_id}"
    
    with sync_playwright() as p:
        # 启动浏览器
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
            ]
        )
        
        # 创建上下文 - 使用移动端 User-Agent
        context = browser.new_context(
            viewport={'width': 390, 'height': 844},
            user_agent='Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )
        
        # 添加反检测脚本
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            window.chrome = { runtime: {} };
        """)
        
        page = context.new_page()
        
        try:
            print(f"[INFO] 访问欧赔页面: {ouzhi_url}")
            
            # 访问欧赔页面
            response = page.goto(ouzhi_url, wait_until='networkidle', timeout=30000)
            print(f"[INFO] 页面状态: {response.status if response else 'Unknown'}")
            
            # 等待页面加载
            page.wait_for_timeout(5000)
            
            # 获取页面内容
            page_content = page.content()
            
            # 检查是否被阻断
            if "访问被阻断" in page_content or "安全威胁" in page_content or "阻断" in page_content:
                print("[WARN] 澳客网访问被阻断")
                browser.close()
                return None
            
            # 检查是否需要验证码
            if "验证码" in page_content or "captcha" in page_content.lower():
                print("[WARN] 需要验证码")
                browser.close()
                return None
            
            print("[INFO] 页面加载成功，开始解析数据...")
            
            # 尝试多种方式提取欧赔数据
            # 方式1: 使用选择器查找表格
            try:
                tables = page.query_selector_all('table')
                print(f"[INFO] 找到 {len(tables)} 个表格")
                
                for table_idx, table in enumerate(tables):
                    rows = table.query_selector_all('tr')
                    print(f"[INFO] 表格 {table_idx}: {len(rows)} 行")
                    
                    for row in rows:
                        cells = row.query_selector_all('td, th')
                        if len(cells) >= 6:
                            cell_texts = [c.inner_text().strip() for c in cells]
                            print(f"  行数据: {cell_texts}")
                            
                            # 查找包含"平均"的行
                            if any('平均' in text for text in cell_texts):
                                print(f"  [FOUND] 平均赔率行: {cell_texts}")
                                # 提取数字
                                numbers = []
                                for text in cell_texts:
                                    matches = re.findall(r'\d+\.\d+', text)
                                    numbers.extend(matches)
                                
                                if len(numbers) >= 6:
                                    odds_data["home_win_initial"] = float(numbers[0])
                                    odds_data["draw_initial"] = float(numbers[1])
                                    odds_data["away_win_initial"] = float(numbers[2])
                                    odds_data["home_win_final"] = float(numbers[3])
                                    odds_data["draw_final"] = float(numbers[4])
                                    odds_data["away_win_final"] = float(numbers[5])
                                    print(f"  [EXTRACTED] 欧赔: 初始 {numbers[0]}/{numbers[1]}/{numbers[2]}, 即时 {numbers[3]}/{numbers[4]}/{numbers[5]}")
                                    break
            except Exception as e:
                print(f"[WARN] 表格解析失败: {e}")
            
            # 方式2: 使用正则表达式
            if odds_data["home_win_final"] is None:
                print("[INFO] 尝试正则表达式解析...")
                avg_lines = re.findall(r'平均\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)', page_content)
                if avg_lines:
                    print(f"[FOUND] 正则匹配到 {len(avg_lines)} 行")
                    if len(avg_lines) >= 1:
                        odds_data["home_win_final"] = float(avg_lines[0][0])
                        odds_data["draw_final"] = float(avg_lines[0][1])
                        odds_data["away_win_final"] = float(avg_lines[0][2])
                    if len(avg_lines) >= 2:
                        odds_data["home_win_initial"] = float(avg_lines[1][0])
                        odds_data["draw_initial"] = float(avg_lines[1][1])
                        odds_data["away_win_initial"] = float(avg_lines[1][2])
            
            # 访问亚盘页面
            if odds_data["home_win_final"] is not None:
                yazhi_url = f"https://m.okooo.com/match/handicap.php?MatchID={match_id}"
                print(f"\n[INFO] 访问亚盘页面: {yazhi_url}")
                
                page.goto(yazhi_url, wait_until='networkidle', timeout=30000)
                page.wait_for_timeout(5000)
                
                page_content = page.content()
                
                # 检查是否被阻断
                if "访问被阻断" in page_content or "安全威胁" in page_content:
                    print("[WARN] 亚盘页面访问被阻断")
                else:
                    # 解析亚盘数据
                    try:
                        tables = page.query_selector_all('table')
                        for table in tables:
                            rows = table.query_selector_all('tr')
                            for row in rows:
                                cells = row.query_selector_all('td, th')
                                if len(cells) >= 6:
                                    cell_texts = [c.inner_text().strip() for c in cells]
                                    if any('平均' in text for text in cell_texts):
                                        print(f"  [FOUND] 亚盘平均行: {cell_texts}")
                                        numbers = []
                                        handicap_text = None
                                        for text in cell_texts:
                                            matches = re.findall(r'\d+\.\d+', text)
                                            numbers.extend(matches)
                                            if not handicap_text and any(kw in text for kw in ['球', '手', '/']):
                                                handicap_text = text
                                        
                                        if len(numbers) >= 6:
                                            odds_data["asian_home_water_initial"] = float(numbers[0])
                                            odds_data["asian_away_water_initial"] = float(numbers[2])
                                            odds_data["asian_home_water_final"] = float(numbers[3])
                                            odds_data["asian_away_water_final"] = float(numbers[5])
                                            
                                            if handicap_text:
                                                odds_data["asian_handicap_initial"] = parse_handicap(handicap_text)
                                                odds_data["asian_handicap_final"] = parse_handicap(handicap_text)
                                            print(f"  [EXTRACTED] 亚盘: 主水 {numbers[0]}/{numbers[3]}, 客水 {numbers[2]}/{numbers[5]}")
                                            break
                    except Exception as e:
                        print(f"[WARN] 亚盘解析失败: {e}")
            
            browser.close()
            
        except Exception as e:
            print(f"[ERROR] 访问页面失败: {e}")
            browser.close()
            return None
    
    return odds_data


if __name__ == "__main__":
    # 测试水晶宫 vs 西汉姆联的比赛 (MatchID: 1260336)
    match_id = "1260336"
    
    print("=" * 60)
    print("测试获取澳客网赔率数据")
    print("=" * 60)
    print(f"比赛ID: {match_id}")
    print(f"比赛: 水晶宫 vs 西汉姆联")
    print()
    
    result = fetch_okooo_odds(match_id)
    
    print("\n" + "=" * 60)
    print("结果:")
    print("=" * 60)
    
    if result:
        print(f"\n欧赔数据:")
        print(f"  初始: 主 {result['home_win_initial']} / 平 {result['draw_initial']} / 客 {result['away_win_initial']}")
        print(f"  即时: 主 {result['home_win_final']} / 平 {result['draw_final']} / 客 {result['away_win_final']}")
        
        print(f"\n亚盘数据:")
        print(f"  初始: 主水 {result['asian_home_water_initial']} / 盘口 {result['asian_handicap_initial']} / 客水 {result['asian_away_water_initial']}")
        print(f"  即时: 主水 {result['asian_home_water_final']} / 盘口 {result['asian_handicap_final']} / 客水 {result['asian_away_water_final']}")
    else:
        print("获取数据失败")

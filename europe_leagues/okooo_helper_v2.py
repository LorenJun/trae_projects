#!/usr/bin/env python3
"""
澳客网(okooo.com) Playwright 辅助模块 v2
针对澳客网反爬虫机制优化的数据提取模块
"""

import random
import re
import time
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright


class OkoooScraper:
    """澳客网赔率数据提取器"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        
    def _get_stealth_args(self) -> List[str]:
        """获取反检测浏览器参数"""
        return [
            '--disable-blink-features=AutomationControlled',
            '--disable-web-security',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-site-isolation-trials',
            '--disable-dev-shm-usage',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
            '--window-size=1920,1080',
        ]
    
    def _get_random_user_agent(self) -> str:
        """获取随机用户代理"""
        agents = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        ]
        return random.choice(agents)
    
    def start(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=self._get_stealth_args()
        )
        
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self._get_random_user_agent(),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        
        # 添加反检测脚本
        self.context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            window.chrome = {
                runtime: {
                    OnInstalledReason: {
                        CHROME_UPDATE: "chrome_update",
                        INSTALL: "install",
                        SHARED_MODULE_UPDATE: "shared_module_update",
                        UPDATE: "update"
                    }
                }
            };
        """)
        
        self.page = self.context.new_page()
        return self
    
    def extract_odds(self, match_id: str) -> Optional[Dict]:
        """
        提取比赛赔率数据
        
        Args:
            match_id: 澳客网比赛ID
            
        Returns:
            包含赔率数据的字典
        """
        url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
        
        try:
            # 访问页面
            print(f"  正在访问: {url}")
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            if response.status == 405:
                print("  [WARN] 访问被阻断，尝试刷新...")
                time.sleep(2)
                self.page.reload(wait_until="domcontentloaded", timeout=60000)
            
            # 等待网络空闲
            self.page.wait_for_load_state("networkidle", timeout=30000)
            
            # 等待AJAX数据加载
            print("  等待数据加载...")
            time.sleep(8)
            
            # 使用JavaScript提取数据
            odds_data = self.page.evaluate("""
                () => {
                    const results = [];
                    
                    // 查找所有表格
                    const tables = document.querySelectorAll('table');
                    
                    for (const table of tables) {
                        const rows = table.querySelectorAll('tr');
                        
                        for (const row of rows) {
                            const cells = row.querySelectorAll('td');
                            if (cells.length >= 10) {
                                // 检查是否是赔率数据行
                                const firstCell = cells[0]?.textContent?.trim();
                                const secondCell = cells[1]?.textContent?.trim();
                                
                                // 赔率数据行的特征：第一列是序号，第二列是公司名称
                                if (/^\\d+$/.test(firstCell) && secondCell && secondCell.length > 1) {
                                    // 清理公司名称中的特殊字符
                                    let company = secondCell.split(/[\\s\\n]+/)[0];
                                    company = company.replace(/[!#@$%^\u0026*()]/g, '').trim();
                                    
                                    // 排除非公司行
                                    if (['公司', '均值', '最大', '最小', '序', '总', '主', '客'].includes(company)) {
                                        continue;
                                    }
                                    
                                    // 提取赔率数值
                                    const getOdds = (idx) => {
                                        const val = cells[idx]?.textContent?.trim();
                                        if (val && /^\\d+\\.\\d+$/.test(val)) {
                                            return parseFloat(val);
                                        }
                                        return null;
                                    };
                                    
                                    const initialHome = getOdds(2);
                                    const initialDraw = getOdds(3);
                                    const initialAway = getOdds(4);
                                    const currentHome = getOdds(5);
                                    const currentDraw = getOdds(6);
                                    const currentAway = getOdds(7);
                                    
                                    // 验证数据有效性（赔率通常在1-50之间）
                                    if (initialHome && initialHome > 1 && initialHome < 50) {
                                        results.push({
                                            company: company,
                                            initial: {
                                                home: initialHome,
                                                draw: initialDraw,
                                                away: initialAway
                                            },
                                            current: {
                                                home: currentHome,
                                                draw: currentDraw,
                                                away: currentAway
                                            }
                                        });
                                    }
                                }
                            }
                        }
                    }
                    
                    return results;
                }
            """)
            
            # 转换为字典格式
            bookmakers = {}
            for item in odds_data:
                company = item["company"]
                if company not in bookmakers:
                    bookmakers[company] = item
            
            return {
                "match_id": match_id,
                "source_url": url,
                "bookmakers": bookmakers,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            
        except Exception as e:
            print(f"  [ERROR] 提取数据失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def close(self):
        """关闭浏览器"""
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


def get_okooo_odds(match_id: str, headless: bool = True) -> Optional[Dict]:
    """
    便捷函数：获取澳客网赔率数据
    
    Args:
        match_id: 澳客网比赛ID
        headless: 是否使用无头模式
        
    Returns:
        赔率数据字典或None
    """
    with OkoooScraper(headless=headless) as scraper:
        return scraper.extract_odds(match_id)


if __name__ == "__main__":
    # 测试
    print("测试澳客网赔率提取 v2")
    print("=" * 60)
    
    match_id = "1296057"
    result = get_okooo_odds(match_id, headless=True)
    
    if result:
        print(f"\n✓ 成功获取 {len(result['bookmakers'])} 家博彩公司数据")
        
        # 显示所有公司
        print("\n提取的博彩公司:")
        for i, (name, data) in enumerate(list(result['bookmakers'].items())[:15]):
            init = data.get('initial', {})
            curr = data.get('current', {})
            print(f"  {i+1:2d}. {name:15s} "
                  f"初赔: {init.get('home', 'N/A'):5.2f} {init.get('draw', 'N/A'):5.2f} {init.get('away', 'N/A'):5.2f}  |  "
                  f"即时: {curr.get('home', 'N/A'):5.2f} {curr.get('draw', 'N/A'):5.2f} {curr.get('away', 'N/A'):5.2f}")
        
        # 检查主要公司
        main_companies = ['威廉希尔', '立博', 'Bet365', '澳门彩票', '竞彩官方', 'bwin']
        found_main = [c for c in main_companies if c in result['bookmakers']]
        print(f"\n主要博彩公司: {found_main if found_main else '未找到主要公司'}")
    else:
        print("\n✗ 获取赔率失败")

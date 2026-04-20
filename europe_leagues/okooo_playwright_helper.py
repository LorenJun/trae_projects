#!/usr/bin/env python3
"""
澳客网(okooo.com) Playwright 辅助模块
提供增强的反爬虫功能和数据提取功能
"""

import random
import time
from typing import Dict, List, Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


# 用户代理列表
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# 视口大小列表
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1680, "height": 1050},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1366, "height": 768},
]

# 博彩公司ID映射
BOOKMAKER_MAP = {
    "24": "威廉希尔",
    "25": "立博", 
    "26": "Bet365",
    "27": "bwin",
    "28": "澳门彩票",
    "29": "竞彩官方",
    "30": "香港马会",
    "31": "必发",
}


class OkoooPlaywrightHelper:
    """澳客网Playwright辅助类"""
    
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.playwright = None
        
    def _get_random_user_agent(self) -> str:
        """获取随机用户代理"""
        return random.choice(USER_AGENTS)
    
    def _get_random_viewport(self) -> Dict:
        """获取随机视口大小"""
        return random.choice(VIEWPORTS)
    
    def _add_stealth_scripts(self, page: Page):
        """添加反检测脚本"""
        # 隐藏webdriver属性
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // 覆盖permissions API
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // 添加Chrome运行时属性
            window.chrome = {
                runtime: {
                    OnInstalledReason: {
                        CHROME_UPDATE: "chrome_update",
                        INSTALL: "install",
                        SHARED_MODULE_UPDATE: "shared_module_update",
                        UPDATE: "update"
                    },
                    OnRestartRequiredReason: {
                        APP_UPDATE: "app_update",
                        OS_UPDATE: "os_update",
                        PERIODIC: "periodic"
                    },
                    PlatformArch: {
                        ARM: "arm",
                        ARM64: "arm64",
                        MIPS: "mips",
                        MIPS64: "mips64",
                        MIPS64EL: "mips64el",
                        MIPSel: "mipsel",
                        X86_32: "x86-32",
                        X86_64: "x86-64"
                    },
                    PlatformNaclArch: {
                        ARM: "arm",
                        MIPS: "mips",
                        MIPS64: "mips64",
                        MIPS64EL: "mips64el",
                        MIPSel: "mipsel",
                        MIPSel64: "mipsel64",
                        X86_32: "x86-32",
                        X86_64: "x86-64"
                    },
                    PlatformOs: {
                        ANDROID: "android",
                        CROS: "cros",
                        LINUX: "linux",
                        MAC: "mac",
                        OPENBSD: "openbsd",
                        WIN: "win"
                    },
                    RequestUpdateCheckStatus: {
                        NO_UPDATE: "no_update",
                        THROTTLED: "throttled",
                        UPDATE_AVAILABLE: "update_available"
                    }
                }
            };
            
            // 修改plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    {
                        0: {
                            type: "application/x-google-chrome-pdf",
                            suffixes: "pdf",
                            description: "Portable Document Format",
                            enabledPlugin: Plugin
                        },
                        description: "Portable Document Format",
                        filename: "internal-pdf-viewer",
                        length: 1,
                        name: "Chrome PDF Plugin"
                    },
                    {
                        0: {
                            type: "application/pdf",
                            suffixes: "pdf",
                            description: "",
                            enabledPlugin: Plugin
                        },
                        description: "",
                        filename: "mhjfbmdgcfjbbpaeojofohoefgiehjai",
                        length: 1,
                        name: "Chrome PDF Viewer"
                    }
                ]
            });
            
            // 修改languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
        """)
    
    def start(self):
        """启动浏览器"""
        self.playwright = sync_playwright().start()
        
        # 启动浏览器
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-web-security',
                '--disable-features=IsolateOrigins,site-per-process',
                '--disable-site-isolation-trials',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ]
        )
        
        # 创建上下文
        self.context = self.browser.new_context(
            viewport=self._get_random_viewport(),
            user_agent=self._get_random_user_agent(),
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            geolocation={'latitude': 31.2304, 'longitude': 121.4737},  # 上海
            permissions=['geolocation'],
            color_scheme='light',
            java_script_enabled=True,
            bypass_csp=True,
        )
        
        # 设置额外HTTP头
        self.context.set_extra_http_headers({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        })
        
        # 创建页面
        self.page = self.context.new_page()
        
        # 添加反检测脚本
        self._add_stealth_scripts(self.page)
        
        return self
    
    def navigate(self, url: str, wait_time: int = 5) -> bool:
        """导航到指定URL"""
        if not self.page:
            raise RuntimeError("浏览器未启动，请先调用start()")
        
        try:
            # 随机延迟
            time.sleep(random.uniform(0.5, 1.5))
            
            # 访问页面
            self.page.goto(url, wait_until='networkidle', timeout=60000)
            
            # 等待页面加载
            time.sleep(wait_time)
            
            # 检查是否被阻断
            content = self.page.content()
            if "访问被阻断" in content or "安全威胁" in content or "405" in content:
                print("  [WARN] 页面访问被阻断，尝试刷新...")
                time.sleep(2)
                self.page.reload(wait_until='networkidle', timeout=60000)
                time.sleep(wait_time)
            
            return True
        except Exception as e:
            print(f"  [ERROR] 导航失败: {e}")
            return False
    
    def extract_odds_data(self, match_id: str) -> Optional[Dict]:
        """从澳客网提取赔率数据"""
        url = f"https://www.okooo.com/soccer/match/{match_id}/odds/"
        
        if not self.navigate(url, wait_time=6):
            return None
        
        try:
            # 等待表格数据加载
            self.page.wait_for_selector('table tbody tr', timeout=15000)
            
            # 额外等待AJAX数据
            time.sleep(3)
            
            # 提取数据
            bookmaker_odds = {}
            rows = self.page.query_selector_all('table tbody tr')
            
            for row in rows:
                try:
                    cells = row.query_selector_all('td')
                    if len(cells) < 8:
                        continue
                    
                    # 获取公司名称
                    company_name = cells[1].inner_text().strip().split('\n')[0].strip()
                    if not company_name or company_name in ['公司', '均值', '最大', '最小', '序']:
                        continue
                    
                    # 提取赔率
                    odds_data = {"bookmaker": company_name, "initial": {}, "current": {}}
                    
                    # 初赔
                    try:
                        home_initial = cells[2].inner_text().strip()
                        draw_initial = cells[3].inner_text().strip()
                        away_initial = cells[4].inner_text().strip()
                        
                        if home_initial and home_initial != '主':
                            odds_data["initial"] = {
                                "home": float(home_initial),
                                "draw": float(draw_initial),
                                "away": float(away_initial)
                            }
                    except:
                        pass
                    
                    # 即时赔
                    try:
                        home_current = cells[5].inner_text().strip()
                        draw_current = cells[6].inner_text().strip()
                        away_current = cells[7].inner_text().strip()
                        
                        if home_current and home_current != '主':
                            odds_data["current"] = {
                                "home": float(home_current),
                                "draw": float(draw_current),
                                "away": float(away_current)
                            }
                    except:
                        pass
                    
                    if odds_data["initial"] or odds_data["current"]:
                        bookmaker_odds[company_name] = odds_data
                        
                except Exception as e:
                    continue
            
            return {
                "match_id": match_id,
                "source_url": url,
                "bookmakers": bookmaker_odds,
                "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            
        except Exception as e:
            print(f"  [ERROR] 提取数据失败: {e}")
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


def extract_okooo_odds(match_id: str, headless: bool = True) -> Optional[Dict]:
    """
    便捷函数：从澳客网提取赔率数据
    
    Args:
        match_id: 澳客网比赛ID
        headless: 是否使用无头模式
    
    Returns:
        赔率数据字典或None
    """
    with OkoooPlaywrightHelper(headless=headless) as helper:
        return helper.extract_odds_data(match_id)


if __name__ == "__main__":
    # 测试
    import json
    
    print("测试澳客网赔率提取...")
    print("=" * 60)
    
    # 测试比赛ID: 1296057 (水晶宫 vs 西汉姆联)
    match_id = "1296057"
    result = extract_okooo_odds(match_id, headless=False)
    
    if result:
        print(f"\n✓ 成功获取 {len(result['bookmakers'])} 家博彩公司数据")
        print("\n主要博彩公司赔率:")
        
        main_companies = ['威廉希尔', '立博', 'Bet365', '澳门彩票', '竞彩官方']
        for company in main_companies:
            if company in result['bookmakers']:
                data = result['bookmakers'][company]
                init = data.get('initial', {})
                curr = data.get('current', {})
                print(f"\n{company}:")
                print(f"  初赔: 主{init.get('home', 'N/A')} 平{init.get('draw', 'N/A')} 客{init.get('away', 'N/A')}")
                print(f"  即时: 主{curr.get('home', 'N/A')} 平{curr.get('draw', 'N/A')} 客{curr.get('away', 'N/A')}")
    else:
        print("✗ 获取赔率失败")

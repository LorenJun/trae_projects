---
name: "browser-use"
description: "Browser automation for AI agents. Invoke when user needs AI-driven browser tasks, web scraping, or form filling."
---

# Browser Use

Browser Use 是一个为 AI Agent 优化的浏览器自动化工具，底层基于 CDP 直连，最近推出了 2.0 版本 CLI，号称成本减半，提供了强大的 AI 驱动浏览器能力。

## 核心功能

- **AI 驱动**：专为 AI Agent 设计的浏览器自动化
- **成本优化**：2.0 版本成本减半
- **云浏览器**：提供可扩展的云端浏览器基础设施
- **隐身模式**：内置代理轮换和验证码解决
- **持久文件系统**：支持持久化存储和内存
- **丰富的集成**：支持 Gmail、Slack、Notion 等 1000+ 集成
- **表单填充**：智能填充表单和上传文件
- **购物自动化**：支持 Instacart 等购物平台
- **个人助手**：帮助寻找商品和信息

## 安装和配置

1. **使用 uv 安装（推荐，Python>=3.11）**
   ```bash
   uv init && uv add browser-use && uv sync
   # 如需安装 Chromium
   uvx browser-use install
   ```

2. **环境变量配置**
   ```bash
   # .env 文件
   BROWSER_USE_API_KEY=your-key
   # 可选：其他 LLM API 密钥
   # GOOGLE_API_KEY=your-key
   # ANTHROPIC_API_KEY=your-key
   ```

## 基本用法

### Python 库使用
```python
from browser_use import Agent, Browser, ChatBrowserUse
import asyncio

async def main():
    browser = Browser(
        # use_cloud=True,  # 使用云端隐身浏览器
    )

    agent = Agent(
        task="Find the number of stars of the browser-use repo",
        llm=ChatBrowserUse(),
        # llm=ChatGoogle(model='gemini-3-flash-preview'),
        # llm=ChatAnthropic(model='claude-sonnet-4-6'),
        browser=browser,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### CLI 使用
- 打开网页：`browser-use open https://example.com`
- 查看可点击元素：`browser-use state`
- 点击元素：`browser-use click 5`
- 输入文本：`browser-use type "Hello"`
- 截图：`browser-use screenshot page.png`
- 关闭浏览器：`browser-use close`

## 示例

### 示例 1：表单填充
```python
from browser_use import Agent, Browser, ChatBrowserUse
import asyncio

async def main():
    browser = Browser()
    agent = Agent(
        task="Fill in this job application with my resume and information.",
        llm=ChatBrowserUse(),
        browser=browser,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### 示例 2：购物自动化
```python
from browser_use import Agent, Browser, ChatBrowserUse
import asyncio

async def main():
    browser = Browser()
    agent = Agent(
        task="Put this list of items into my instacart.",
        llm=ChatBrowserUse(),
        browser=browser,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
```

### 示例 3：个人助手
```python
from browser_use import Agent, Browser, ChatBrowserUse
import asyncio

async def main():
    browser = Browser()
    agent = Agent(
        task="Help me find parts for a custom PC.",
        llm=ChatBrowserUse(),
        browser=browser,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
```

## 高级功能

### 自定义工具
```python
from browser_use import Tools

tools = Tools()

@tools.action(description='Description of what this tool does.')
def custom_tool(param: str) -> str:
    return f"Result: {param}"

agent = Agent(
    task="Your task",
    llm=llm,
    browser=browser,
    tools=tools,
)
```

### 认证管理
- **使用真实浏览器配置文件**：复用已保存登录信息的 Chrome 配置文件
- **临时账户**：使用 AgentMail 处理临时邮箱
- **同步认证配置文件**：使用 `curl -fsSL https://browser-use.com/profile.sh | BROWSER_USE_API_KEY=XXXX sh`

### 云端浏览器
- **可扩展的浏览器基础设施**
- **内存管理**
- **代理轮换**
- **隐身浏览器指纹**
- **高性能并行执行**

## 注意事项

- **模型选择**：推荐使用 `ChatBrowserUse()`，专为浏览器自动化优化
- **CAPTCHA 处理**：使用 Browser Use Cloud 提供的隐身浏览器
- **生产环境**：对于生产用例，推荐使用 Browser Use Cloud API
- **详细文档**：请参考 https://github.com/browser-use/browser-use

## 故障排除

- **浏览器安装失败**：运行 `uvx browser-use install` 重新安装
- **认证问题**：参考认证管理部分的示例
- **CAPTCHA 问题**：使用 Browser Use Cloud
- **网络问题**：检查网络连接和目标网站的可访问性

## 使用建议

**优先级**：★★★☆☆（第三选择）

**适用场景**：
- AI 驱动的复杂浏览器任务
- 表单填充和提交
- 购物自动化（如 Instacart）
- 个人助手任务（如寻找商品信息）
- 需要云端浏览器基础设施的场景

**推荐理由**：
- 专为 AI Agent 设计
- 2.0 版本成本优化（成本减半）
- 云端浏览器支持，可扩展性强
- 内置代理轮换和验证码解决
- 丰富的集成生态（1000+ 集成）

**使用示例**：
```python
# AI 驱动的浏览器任务
from browser_use import Agent, Browser, ChatBrowserUse
import asyncio

async def main():
    browser = Browser()
    agent = Agent(
        task="Find the number of stars of the browser-use repo",
        llm=ChatBrowserUse(),
        browser=browser,
    )
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
```
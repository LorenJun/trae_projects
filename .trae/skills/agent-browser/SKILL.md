---
name: "agent-browser"
description: "Browser automation CLI for AI agents. Invoke when user needs headless browser automation, web scraping, or AI-driven browser tasks."
---

# Agent Browser

Agent Browser 是 Vercel 出品的为 AI Agent 优化的浏览器自动化 CLI，底层基于 Playwright，提供了丰富的命令集和智能的元素定位方式，是无头浏览器自动化的首选工具。

## 核心功能

- **Rust 原生实现**：高性能的 Rust 命令行工具
- **智能元素定位**：基于引用（refs）的确定性元素选择
- **丰富的命令集**：支持点击、填写、滚动、截图等多种操作
- **会话管理**：支持多个隔离的浏览器会话
- **认证管理**：支持多种认证方式，包括 Chrome 配置文件复用
- **安全特性**：支持内容边界标记、域名白名单等安全功能
- **可视化仪表板**：实时监控浏览器会话
- **AI 聊天**：支持自然语言控制浏览器

## 安装和配置

1. **全局安装（推荐）**
   ```bash
   npm install -g agent-browser
   agent-browser install  # 首次运行时下载 Chrome
   ```

2. **项目安装**
   ```bash
   npm install agent-browser
   agent-browser install
   ```

3. **Homebrew 安装（macOS）**
   ```bash
   brew install agent-browser
   agent-browser install
   ```

## 基本用法

### 核心命令
- 打开网页：`agent-browser open https://example.com`
- 获取快照：`agent-browser snapshot`  # 获取带引用的可访问性树
- 点击元素：`agent-browser click @e1`  # 使用引用点击
- 填写文本：`agent-browser fill @e2 "test@example.com"`
- 按键：`agent-browser press Enter`
- 截图：`agent-browser screenshot page.png`
- 关闭浏览器：`agent-browser close`

### 选择器类型
- **引用选择器**（推荐）：`@e1`, `@e2`（从快照中获取）
- **CSS 选择器**：`#id`, `.class`, `div > button`
- **文本选择器**：`text=Submit`
- **XPath 选择器**：`xpath=//button`
- **语义选择器**：`agent-browser find role button click --name "Submit"`

## 示例

### 示例 1：登录流程
```bash
# 1. 打开登录页面并获取快照
agent-browser open https://example.com/login
agent-browser snapshot -i

# 2. 使用引用填写表单
agent-browser fill @e1 "username"
agent-browser fill @e2 "password"
agent-browser click @e3

# 3. 等待登录成功并截图
agent-browser wait --text "Welcome"
agent-browser screenshot login-success.png
```

### 示例 2：网页导航和数据提取
```bash
agent-browser open https://example.com
agent-browser snapshot -i
agent-browser click @e1  # 点击链接
agent-browser wait --load networkidle
agent-browser get text @e2  # 获取文本内容
agent-browser screenshot page.png
```

## 高级功能

### 会话管理
- 创建新标签页：`agent-browser tab new https://example.com`
- 切换标签页：`agent-browser tab t1`
- 关闭标签页：`agent-browser tab close t1`

### 网络控制
- 拦截请求：`agent-browser network route "**/*.js" --abort`
- 查看请求：`agent-browser network requests`
- 开始 HAR 录制：`agent-browser network har start`

### 存储管理
- 保存认证状态：`agent-browser state save auth.json`
- 加载认证状态：`agent-browser state load auth.json`
- 管理 cookies：`agent-browser cookies set name value`

### 安全特性
- 内容边界标记：`agent-browser --content-boundaries snapshot`
- 域名白名单：`agent-browser --allowed-domains "example.com,*.example.com" open https://example.com`
- 动作确认：`agent-browser --confirm-actions eval,download`

## 注意事项

- 使用 `--headed` 选项可以显示浏览器窗口，便于调试
- 使用 `agent-browser dashboard start` 可以启动可视化仪表板
- 详细文档请参考：https://github.com/vercel-labs/agent-browser

## 故障排除

- **Chrome 安装失败**：运行 `agent-browser install` 重新安装
- **权限错误**：确保有足够的权限安装和运行浏览器
- **会话问题**：运行 `agent-browser doctor` 诊断安装问题
- **网络问题**：检查网络连接和目标网站的可访问性

## 使用建议

**优先级**：★★★★★（首选）

**适用场景**：
- 无头浏览器自动化任务
- AI 驱动的浏览器操作
- 网页抓取和数据提取
- 表单自动填充
- 登录流程自动化

**推荐理由**：
- 为 AI Agent 专门优化
- Rust 原生实现，性能优异
- 智能元素定位系统（基于引用）
- 丰富的命令集和功能
- 强大的会话管理和认证支持

**使用示例**：
```bash
# 基本工作流
agent-browser open https://example.com
agent-browser snapshot -i  # 获取可交互元素
agent-browser click @e1     # 使用引用点击元素
agent-browser fill @e2 "text"  # 填写表单
agent-browser screenshot result.png  # 截图
```
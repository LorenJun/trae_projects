---
name: "playwright-cli"
description: "Playwright CLI for browser automation and testing. Invoke when user needs to automate web tasks, test websites, or perform browser-based operations."
---

# Playwright CLI

Playwright CLI 是微软出品的浏览器自动化和测试工具，支持 Chromium、Firefox 和 WebKit 三大浏览器引擎，提供简洁的命令行接口用于 AI 代理的浏览器操作。

## 核心功能

- **跨浏览器支持**：同时支持 Chromium、Firefox 和 WebKit
- **自动等待**：智能等待元素可操作，无需人工设置超时
- **强大的定位器**：基于用户视角的元素定位方法
- **测试隔离**：每个测试都在独立的浏览器上下文中运行
- **追踪功能**：捕获执行轨迹、截图和视频
- **并行执行**：默认在所有配置的浏览器中并行运行测试

## 安装和配置

1. **安装 Playwright CLI**
   ```bash
   npm install -g @playwright/cli@latest
   ```

2. **安装浏览器**
   ```bash
   npx playwright install
   ```

3. **可选：安装技能**
   ```bash
   playwright-cli install --skills
   ```

## 基本用法

### 浏览器操作
- 打开网页：`playwright-cli open https://example.com --headed`
- 点击元素：`playwright-cli click "button:has-text('Submit')"`
- 输入文本：`playwright-cli type "input[name='email']" "test@example.com"`
- 按键：`playwright-cli press Enter`
- 截图：`playwright-cli screenshot`

### 会话监控
- 查看所有运行的浏览器会话：`playwright-cli show`

## 示例

### 示例 1：测试登录流程
```bash
playwright-cli open https://example.com/login
playwright-cli fill "input[name='username']" "testuser"
playwright-cli fill "input[name='password']" "password123"
playwright-cli click "button:has-text('Login')"
playwright-cli wait "text=Welcome"
playwright-cli screenshot login-success.png
```

### 示例 2：测试表单提交
```bash
playwright-cli open https://example.com/form
playwright-cli fill "input[name='name']" "John Doe"
playwright-cli fill "input[name='email']" "john@example.com"
playwright-cli select "select[name='country']" "United States"
playwright-cli click "input[type='checkbox']"
playwright-cli click "button:has-text('Submit')"
playwright-cli wait "text=Form submitted successfully"
playwright-cli screenshot form-success.png
```

## 高级功能

### 网络请求拦截
- 拦截和修改网络请求
- 模拟响应
- 监控网络流量

### 设备模拟
- 模拟移动设备
- 模拟不同的屏幕尺寸
- 模拟地理位置

### 存储管理
- 保存和加载认证状态
- 管理 cookies
- 管理本地存储

## 注意事项

- 使用 `--headed` 选项可以显示浏览器窗口，便于调试
- 使用 `playwright-cli show` 可以查看实时会话
- 详细文档请参考：https://github.com/microsoft/playwright

## 故障排除

- **浏览器安装失败**：运行 `npx playwright install` 重新安装
- **权限错误**：确保有足够的权限安装和运行浏览器
- **网络问题**：检查网络连接和目标网站的可访问性

## 使用建议

**优先级**：★★★★☆（第二选择）

**适用场景**：
- 跨浏览器测试（需要支持 Chromium、Firefox、WebKit）
- 端到端测试
- 需要自动等待和智能定位的任务
- 企业级测试自动化
- 需要详细追踪和报告的场景

**推荐理由**：
- 微软官方支持，稳定性高
- 跨浏览器支持，确保兼容性
- 自动等待机制，减少人工干预
- 强大的定位器系统
- 完善的测试隔离和并行执行

**使用示例**：
```bash
# 基本测试工作流
playwright-cli open https://example.com
playwright-cli fill "input[name='username']" "testuser"
playwright-cli fill "input[name='password']" "password123"
playwright-cli click "button:has-text('Login')"
playwright-cli wait "text=Welcome"
playwright-cli screenshot login-success.png
```
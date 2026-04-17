---
name: "chrome-mcp"
description: "Chrome DevTools MCP Server for AI agents. Invoke when user needs reliable browser automation, deep debugging, and performance analysis."
---

# Chrome MCP

Chrome MCP 是 Chrome 官方在 M144 版本新增的功能，通过 Chrome DevTools Protocol (CDP) 为 AI Agent 提供完整的浏览器控制能力，包括可靠的自动化、深度调试和性能分析。

## 核心功能

- **完整的 Chrome DevTools 能力**：访问所有 DevTools 功能
- **可靠的自动化**：基于官方 CDP 协议的稳定自动化
- **深度调试**：支持断点、网络分析、性能分析等
- **性能分析**：详细的性能监控和分析工具
- **远程调试**：通过网络连接控制浏览器
- **实时会话**：支持实时浏览器会话管理

## 配置和设置

1. **启用远程调试**
   - 打开 Chrome 浏览器
   - 访问 `chrome://inspect/#remote-debugging`
   - 在 "Discover network targets" 部分点击 "Configure..."
   - 添加本地或远程设备的 IP 地址和端口

2. **启动 Chrome 远程调试**
   ```bash
   # 本地启动 Chrome 并启用远程调试
   "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
   
   # macOS
   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
   
   # Linux
   google-chrome --remote-debugging-port=9222
   ```

3. **连接到远程调试端口**
   - 访问 `http://localhost:9222` 查看可用的浏览器会话
   - 使用 WebSocket URL 连接到特定会话

## 基本用法

### 使用 CDP 协议
```python
import json
import websocket

# 连接到 Chrome DevTools
ws_url = "ws://localhost:9222/devtools/browser/<session-id>"
ws = websocket.create_connection(ws_url)

# 发送命令
def send_command(method, params=None):
    command = {
        "id": 1,
        "method": method,
        "params": params or {}
    }
    ws.send(json.dumps(command))
    return json.loads(ws.recv())

# 打开新标签页
send_command("Target.createTarget", {"url": "https://example.com"})

# 获取页面内容
send_command("Page.navigate", {"url": "https://example.com"})
send_command("Page.getDocument")

# 关闭连接
ws.close()
```

### 与 AI Agent 集成
1. **配置 MCP 服务器**
   - 在 MCP 客户端配置中添加 Chrome MCP 服务器
   - 使用 `chrome://inspect/#remote-debugging` 页面获取连接信息

2. **Agent 工作流**
   - 连接到 Chrome 远程调试端口
   - 发送 CDP 命令执行浏览器操作
   - 接收和处理响应
   - 分析结果并执行下一步操作

## 高级功能

### 网络分析
- **请求拦截**：拦截和修改网络请求
- **响应模拟**：模拟网络响应
- **流量监控**：监控和分析网络流量
- **HAR 录制**：录制和分析 HAR 文件

### 性能分析
- **性能监控**：监控页面加载和运行时性能
- **内存分析**：分析内存使用情况
- **CPU 分析**：分析 CPU 使用情况
- **渲染分析**：分析页面渲染性能

### 调试功能
- **断点设置**：设置 JavaScript 断点
- **变量检查**：检查和修改变量值
- **调用栈分析**：分析函数调用栈
- **DOM 检查**：检查和修改 DOM 结构

## 示例

### 示例 1：网页截图
```python
import json
import websocket
import base64

# 连接到 Chrome DevTools
ws_url = "ws://localhost:9222/devtools/browser/<session-id>"
ws = websocket.create_connection(ws_url)

def send_command(method, params=None):
    command = {"id": 1, "method": method, "params": params or {}}
    ws.send(json.dumps(command))
    return json.loads(ws.recv())

# 创建目标
target = send_command("Target.createTarget", {"url": "https://example.com"})
target_id = target["result"]["targetId"]

# 附加到目标
session = send_command("Target.attachToTarget", {"targetId": target_id, "flatten": True})
session_id = session["result"]["sessionId"]

# 导航到页面
send_command("Page.navigate", {"url": "https://example.com"}, session_id)

# 等待页面加载
send_command("Page.loadEventFired", {}, session_id)

# 截图
screenshot = send_command("Page.captureScreenshot", {"format": "png"}, session_id)
with open("screenshot.png", "wb") as f:
    f.write(base64.b64decode(screenshot["result"]["data"]))

# 关闭连接
ws.close()
```

### 示例 2：表单自动填充
```python
import json
import websocket

# 连接到 Chrome DevTools
ws_url = "ws://localhost:9222/devtools/browser/<session-id>"
ws = websocket.create_connection(ws_url)

def send_command(method, params=None, session_id=None):
    command = {"id": 1, "method": method, "params": params or {}}
    if session_id:
        command["sessionId"] = session_id
    ws.send(json.dumps(command))
    return json.loads(ws.recv())

# 创建目标并导航到登录页面
target = send_command("Target.createTarget", {"url": "https://example.com/login"})
target_id = target["result"]["targetId"]
session = send_command("Target.attachToTarget", {"targetId": target_id, "flatten": True})
session_id = session["result"]["sessionId"]

# 等待页面加载
send_command("Page.loadEventFired", {}, session_id)

# 填充表单
send_command("Runtime.evaluate", {
    "expression": "document.querySelector('input[name=username]').value = 'testuser';"
}, session_id)
send_command("Runtime.evaluate", {
    "expression": "document.querySelector('input[name=password]').value = 'password123';"
}, session_id)

# 提交表单
send_command("Runtime.evaluate", {
    "expression": "document.querySelector('button[type=submit]').click();"
}, session_id)

# 关闭连接
ws.close()
```

## 注意事项

- **安全考虑**：`--remote-debugging-port` 会在本地暴露完整的浏览器控制能力，仅在受信任的环境中使用
- **版本要求**：需要 Chrome M144 或更高版本
- **性能影响**：启用远程调试可能会对浏览器性能产生一定影响
- **详细文档**：请参考 https://developer.chrome.com/blog/chrome-devtools-mcp-debug-your-browser-session?hl=zh-cn

## 故障排除

- **连接失败**：检查 Chrome 是否已启动并启用了远程调试
- **权限错误**：确保以正确的用户权限运行 Chrome
- **端口被占用**：尝试使用不同的端口号
- **网络问题**：检查网络连接和防火墙设置

## 使用建议

**优先级**：★★★☆☆（第四选择）

**适用场景**：
- 需要深度调试的浏览器任务
- 性能分析和优化
- Chrome 特定功能测试
- 需要完整 DevTools 能力的场景
- 远程浏览器调试

**推荐理由**：
- Chrome 官方功能，可靠性高
- 完整的 DevTools 能力
- 支持深度调试和性能分析
- 实时会话管理
- 官方 CDP 协议支持

**使用示例**：
```python
# 连接到 Chrome DevTools 并截图
import json
import websocket
import base64

# 连接到 Chrome DevTools
ws_url = "ws://localhost:9222/devtools/browser/<session-id>"
ws = websocket.create_connection(ws_url)

def send_command(method, params=None):
    command = {"id": 1, "method": method, "params": params or {}}
    ws.send(json.dumps(command))
    return json.loads(ws.recv())

# 创建目标并导航
target = send_command("Target.createTarget", {"url": "https://example.com"})
target_id = target["result"]["targetId"]
session = send_command("Target.attachToTarget", {"targetId": target_id, "flatten": True})
session_id = session["result"]["sessionId"]

# 截图
screenshot = send_command("Page.captureScreenshot", {"format": "png"}, session_id)
with open("screenshot.png", "wb") as f:
    f.write(base64.b64decode(screenshot["result"]["data"]))

# 关闭连接
ws.close()
```
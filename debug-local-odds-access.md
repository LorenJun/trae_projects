# [OPEN] local-odds-access

## 背景
- 现象：其他设备可正常访问，唯独当前本机访问 `odds.php` 异常。
- 范围：优先怀疑本机网络环境、DNS、TLS、系统代理、证书链、UA/请求方式差异，而不是目标站点整体故障。

## 假设
1. 本机存在系统代理或环境变量代理，导致请求被错误转发或污染。
2. 本机 DNS 解析结果异常，命中了错误 IP 或被本地网络设备劫持。
3. 本机 TLS / 证书链 / 中间人代理导致 HTTPS 请求失败或内容异常。
4. 本机请求头、UA、编码、缓存参数与其他设备差异较大，触发了站点的设备级风控。
5. 当前“访问不了”并非完全不可达，而是返回了异常页面、乱码页、重定向页或被拦截页。

## 当前计划
- 先收集本机运行时证据：代理环境、DNS、直连请求、curl 响应头、TLS 握手结果。
- 对比“浏览器表现”和“脚本表现”是否一致。
- 基于证据缩小到网络层、系统层或请求层原因。

## 证据记录
- 环境变量代理为空：未发现 `HTTP_PROXY / HTTPS_PROXY / ALL_PROXY`。
- macOS 系统网络代理为空：`Wi-Fi / LAN / iPhone USB` 的 Web / Secure Web / SOCKS 均为 `Enabled: No`。
- DNS 解析正常：
  - `m.okooo.com -> 211.95.142.138, 116.163.31.218`
  - `okooo.com -> 47.95.120.211, 47.95.113.74`
- 默认 `curl` 访问 `GET https://m.okooo.com/match/odds.php?MatchID=1302914` 返回 `HTTP/2 405`，服务端为 `Tengine`，返回的是阿里云风控/拦截页。
- `curl -I` 的 `HEAD` 请求同样返回 `HTTP/2 405`。
- 带移动端 UA、`Referer: https://m.okooo.com/`、no-cache 头后，`curl` 返回 `HTTP/2 200`，服务端为 `Lego Server`，正文大小约 `119712`。
- Python `requests` 结合仓库中的 `okooo_mobile_access` 配置也稳定返回 `200`，可重复复现 6/6 成功。

## 当前判断
- “本机完全访问不了”不成立；本机在合适请求特征下可稳定访问。
- 更可能的根因是：本机默认请求特征（桌面浏览器/默认 curl/某些扩展或缓存状态）触发了站点/WAF 风控。
- 下一步若继续排查，应聚焦浏览器侧：UA、扩展、缓存、Cookie、Referer、隐私防护、请求方法，而非 DNS/系统代理。

## Chrome DevTools 进一步证据
- 使用 Chrome CDP / DevTools 远程调试对主文档请求做了矩阵测试。
- `desktop-default`：`403`，`server=SLT`，桌面 UA，无 `Referer`，被拦截。
- `mobile-only`：`405`，`server=Tengine`，移动端 UA，但无 `Referer`，仍被拦截。
- `mobile-referrer`：`200`，`server=Lego Server`，移动端 UA + `Referer: https://m.okooo.com/`，成功进入真实赔率页。
- `desktop + referer`：仍为 `403`，说明不是 `Referer` 单独放行。

## 现阶段结论
- 决定性差异不是单一桌面/移动切换，也不是单一 `Referer`。
- 当前证据支持：`移动端 UA + Referer` 组合是通过风控的关键条件。
- `cache-bust` 参数可继续保留，但不是从拦截变成功的必要条件；`mobile-referrer` 不带 cache-bust 已可 `200`。

## 当前仓库口径
- 正式快照链默认使用 `local-chrome`，不再依赖裸桌面请求路径。
- `m.okooo.com` 的公共访问策略已统一收敛到 `okooo_mobile_access.py`：
  - `iPhone Safari UA`
  - `Referer: https://m.okooo.com/`
  - no-cache 头
  - cache-bust 参数
  - 当前 `100` 组随机移动 profile 池
- `okooo_save_snapshot.py` 的首次打开路径已调整为：
  - 先开 `about:blank`
  - 再通过 `Page.navigate(..., referrer=...)` 进入目标页

## 当前验证结果
- 正式 `predict-match` 链已使用这套访问策略重跑验证。
- 已验证样例：
  - `la_liga`
  - `埃尔切 vs 赫塔费`
  - `MatchID=1302914`
- 当前可稳定拿到：
  - 欧赔 `multi_company_consensus`
  - 亚值真实盘口
  - 大小球真实盘口与水位
  - 凯利初始 / 即时值

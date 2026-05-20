---
title: 仓库变更日志
owner: trae_projects
version: v1
last_updated: 2026-05-20
---

# CHANGELOG

本文记录当前仓库最近一轮与 `europe_leagues` 澳客赔率链、正式预测链、访问策略和文档口径同步相关的重要变更。

范围：
- 代码：`/Users/bytedance/trae_projects/europe_leagues`
- 技能：`/Users/bytedance/trae_projects/.trae/skills`
- 文档：仓库根与 `europe_leagues/` 下相关 `md`

---

## 2026-05-20

### 1. 澳客访问策略收敛

本轮变更把 `m.okooo.com` 的正式访问口径统一收敛到同一套策略，避免“浏览器能打开、脚本抓不到”或“桌面请求被拦截”的分裂行为。

当前固定口径：
- 默认快照 driver：`local-chrome`
- 默认请求特征：`iPhone Safari UA + Referer: https://m.okooo.com/`
- 默认 no-cache 头
- 默认 cache-bust 参数
- 公共移动 profile 入口：`europe_leagues/okooo_mobile_access.py`

已验证结论：
- 单独移动端 UA 不足以放行
- 单独 `Referer` 不足以放行
- `移动端 UA + Referer` 组合是当前通过风控的关键条件

关联文件：
- `europe_leagues/okooo_mobile_access.py`
- `europe_leagues/okooo_save_snapshot.py`
- `debug-local-odds-access.md`

### 2. 本机访问排障结论固化

已完成对“其他设备正常、唯独本机访问不了 `odds.php`”问题的排障，并将结论转化为正式链路策略。

确认结果：
- 不是系统代理问题
- 不是 DNS 解析异常问题
- 不是整站不可达问题
- 更像是默认请求特征触发了站点/WAF 风控

典型现象：
- 默认桌面请求常见 `403/405`
- 带移动端 UA + `Referer` 后可稳定 `200`

关联文档：
- `debug-local-odds-access.md`
- `europe_leagues/ODDS_FETCH_GUIDE.md`

### 3. 快照链首次导航修复

`okooo_save_snapshot.py` 中的 `LocalChromeSession` 已修正首次打开页面的方式：

旧行为：
- 冷启动直接打开 `odds.php`

新行为：
- 先开 `about:blank`
- 应用移动 profile
- 再通过 `Page.navigate(..., referrer=...)` 进入目标页

这项变更直接解决了“快照层看起来刷新成功，但实际没拿到真实赔率表”的问题。

关联文件：
- `europe_leagues/okooo_save_snapshot.py`

### 4. 欧赔解析器修复

欧赔解析链已补齐两类关键问题：

1. 紧凑赔率串解析
- 支持将 `2.243.093.27` 这类压缩格式拆成 `2.24 / 3.09 / 3.27`

2. 干扰字符公司名解析
- 支持处理 `威!廉#希!尔`、`b!e#t365` 这类带扰动字符的公司名

新增能力：
- `99家平均` 紧凑 fallback 可完整拆出 `home/draw/away` 的初赔与即赔
- 页面正文中的多公司欧赔明细可被正确识别

关联文件：
- `europe_leagues/okooo_save_snapshot.py`
- `europe_leagues/test_okooo_save_snapshot.py`

### 5. 欧赔升级为多公司共识优先

欧赔链路不再优先停留在 `99家平均` fallback，而是升级为“多公司共识优先”方案。

当前行为：
- 优先解析多家公司欧赔明细
- 优先生成 `multi_company_consensus`
- `99家平均` 仅作为保底 fallback

当前已验证到的公司样本包括：
- `Bet365`
- `皇冠`
- `Pinnacle`
- `澳门彩票`
- `威廉希尔`
- `易胜博`
- `立博`
- `12BET`
- `Bwin`
- `Interwetten`
- `利记`
- `伟德`
- `香港马会`

关联文件：
- `europe_leagues/okooo_save_snapshot.py`
- `europe_leagues/.okooo-scraper/snapshots/la_liga/埃尔切vs赫塔费.json`

### 6. 正式 predict-match 已验证打通

已使用正式 CLI 对当前链路完成验证，确认不是“局部抓取脚本成功”，而是“正式预测链已打通真实赔率数据”。

验证样例：
- `league`: `la_liga`
- 比赛：`埃尔切 vs 赫塔费`
- `MatchID=1302914`

正式链路验证结果：
- 欧赔：`multi_company_consensus`
- 亚值：真实盘口可用
- 大小球：真实盘口线与水位可用
- 凯利：初始 / 即时可用

已做稳定性检查：
- 在随机设备池模式下连续运行 `predict-match`，样例可稳定拿到真实数据

关联入口：
- `europe_leagues/prediction_system.py`
- `europe_leagues/app/cli.py`

### 7. 公共移动设备池扩容

`okooo_mobile_access.py` 中的公共移动设备池已连续扩容：

阶段变化：
- 先从混合池收敛为纯 `iPhone Safari`
- 再扩到 `20` 组
- 再扩到 `50` 组
- 当前扩到 `100` 组

当前设备池特征：
- 全部为 `iPhone Safari` 风格 UA
- 全部统一 `viewport={"width": 1080, "height": 720}`
- `device_scale_factor=3`
- 每次访问随机选择一个 profile

当前影响范围：
- 所有通过公共层 `random_mobile_profile()` 访问 `m.okooo.com` 的正式脚本
- 浏览器链、requests 链、快照链、批量回填链、正式预测链

关联文件：
- `europe_leagues/okooo_mobile_access.py`
- `europe_leagues/test_okooo_mobile_access.py`

### 8. 代理验证脚本与正式链对齐

`scripts/validate_okooo_proxies.py` 已从“按索引轮转 profile”改为“每次请求随机选一个公共 profile”，与正式预测链保持一致。

当前行为：
- 直连模式和代理模式都会随机取公共移动设备 profile
- 访问头、缓存参数、`Referer` 和快照链保持统一口径

关联文件：
- `europe_leagues/scripts/validate_okooo_proxies.py`

### 9. 文档同步

以下文档已同步本轮最新结论：

- `README.md`
- `debug-local-odds-access.md`
- `europe_leagues/README.md`
- `europe_leagues/README_使用指南.md`
- `europe_leagues/ODDS_FETCH_GUIDE.md`
- `europe_leagues/docs/INDEX.md`

同步内容包括：
- `local-chrome` 默认链路
- `iPhone Safari + Referer` 放行条件
- `100` 设备池
- 欧赔 `multi_company_consensus`
- 正式 `predict-match` 已验证可拿到真实数据
- `build-season-master-review` 示例命令与当前 CLI 参数对齐

### 10. Skill 同步

以下技能文件已同步更新：

- `.trae/skills/football-match-analysis/SKILL.md`
- `.trae/skills/okooo-match-finder/SKILL.md`
- `.trae/skills/football-prediction-live-update/SKILL.md`
- `.trae/skills/sync-pending-results-review/SKILL.md`

同步内容包括：
- 当前澳客访问口径
- 公共移动设备池规模
- 欧赔解析优先级
- 正式预测链已验证的真实数据能力

---

## 当前稳定口径

截至本次更新，仓库关于澳客赔率链与正式预测链的稳定口径如下：

- `prediction_system.py` 是兼容 / 发现入口
- `app/cli.py` 是真实命令实现入口
- `local-chrome` 是当前默认快照 driver
- `iPhone Safari UA + Referer` 是当前默认访问特征
- `100` 组随机移动 profile 是当前公共访问池
- 欧赔优先输出 `multi_company_consensus`
- 正式 `predict-match` 已验证可稳定拿到真实欧赔、亚值、大小球、凯利

如果后续代码与本文冲突，以当前代码实现为准，优先参考：
- `europe_leagues/app/cli.py`
- `europe_leagues/okooo_mobile_access.py`
- `europe_leagues/okooo_save_snapshot.py`
- `europe_leagues/domain/persistence.py`
- `europe_leagues/runtime/result_sync.py`

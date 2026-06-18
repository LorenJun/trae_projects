---
title: 仓库变更日志
owner: trae_projects
version: v1
last_updated: 2026-06-18
---

# CHANGELOG

本文记录当前仓库最近一轮与 `europe_leagues` 澳客赔率链、正式预测链、访问策略和文档口径同步相关的重要变更。

范围：
- 代码：`/Users/bytedance/trae_projects/europe_leagues`
- 技能：`/Users/bytedance/trae_projects/.trae/skills`
- 文档：仓库根与 `europe_leagues/` 下相关 `md`

---

## 2026-06-18

### 0. 比分方向改由亚值让球口诀驱动 + OU 6 规则定大小球（`domain/score_projection.py`）

此前比分方向 `direction_of` 取模型 1X2 概率最高方向。按用户要求改为：**亚值让球口诀(verdict_dir)定比分方向，OU 操盘 6 规则定大球/小球**，再在交集内按方向概率选比分。

- 新增 `_VERDICT_DIR_INTENT` 映射：印证类（`block_up_home_genuine`/`lure_dog_no_point`/`block_down_dog_hard`）→ `fav`（让球方获胜方向）；诱导类（`lure_up_home_fade`/`lure_up_hot_death`/`protect_dog_genuine`）→ `fade`（强侧不赢）。
- 新增 `_direction_handicap`：优先 `tri_axis_consistency.direction_handicap`，回退 `realtime.context_applied.market_operation_pattern.direction_handicap_matrix`。
- `direction_of`：口诀触发时——印证类取强侧获胜；诱导类在 平局/弱侧 中取模型概率更高者（用户选「平局+弱侧都放行」）。`allowed_outcomes`：印证类仅放行强侧获胜，诱导类放行 平局+弱侧（爆冷中/高时再全放行）。
- **未触发口诀时回退模型 1X2**（用户选择），保留原有平局达标/平局方向扩张/爆冷放行逻辑。
- `project_scores_for_side` 尾部 top3 补足改为仅在 `allowed_outcomes` 内取，不再无视方向从全 1X2 拉取，避免泄漏越界比分。
- OU 侧维持既有 `_build` 大小球同侧硬约束（6 规则经 `over_under` 概率体现）；候选不足 3 个时尾部允许放宽 OU 凑满 top3（既有契约）。

新增 `test_score_projection_direction.py`（8 例）覆盖印证/诱导/无口诀回退/OU 约束/来源回退。全量 456 测试通过。

### 1. 让球方向 6 口诀常开偏置：冷启动也能驱动胜平负概率（`domain/inference.py`）

此前 `apply_market_operation_adjustment` 只消费第二层历史学习权重（`operation_weight_learner`），而世界杯样本量 < `MIN_SAMPLES=80` → 各信号 `reliability=0` → 让球方向矩阵 6 口诀（`direction_handicap_matrix.verdict_dir`）只作为标签透传，**从不改胜平负概率**。

按「始终叠加」改造，新增常开矩阵偏置路（不依赖历史样本量）：
- 新增类常量 `_DIRECTION_MATRIX_BIAS`：6 口诀 → 强侧视角有符号偏置（`block_up_home_genuine`+0.16 / `lure_up_home_fade`−0.22 / `lure_up_hot_death`−0.26 / `protect_dog_genuine`−0.20 / `lure_dog_no_point`+0.16 / `block_down_dog_hard`+0.14），幅度沿用 classifier 内各口诀软分量级；
- 新增 `_direction_matrix_bias` classmethod 从 `pattern_diag` 取出口诀偏置；
- 重写 `apply_market_operation_adjustment`：`delta_learned`（历史权重路，样本足才非 0）+ `delta_matrix`（口诀路，常开，`±0.04` 封顶 = bias×0.18）两路同向求和，合成 `delta` 再 `±0.06` 封顶；正=印证强侧（从平局让给强侧），负=诱导（从强侧回撤，60% 给平局、40% 给冷门）。两路皆 0 时早退 `no_signal_label_only`（原 `no_reliable_signal_label_only`，因含义已扩为「学习权重与口诀均无信号」）。

OU 操盘 6 规则本就经 `pace_shift → target_total → λ`（`inference.py`）驱动比分与大小球概率，无需改动；本次只补齐让球 6 口诀对胜平负概率的缺失链路。新增 2 个冷启动单测（`weights={}` 下 `lure_up_hot_death` 压低强侧、`block_up_home_genuine` 抬升强侧）。全量 448 测试通过。

### 1. 比分口径单一数据源：网页 / MEMORY / teams_2026.md 统一（`domain/score_projection.py`）

此前网页卡片用 `_scores_for_side` 按「方向 + 大小球」从泊松网格重算比分，而 MEMORY.md 与 teams_2026.md 的 `比分:` 直接取模型原始 `top_scores`，导致同一场比赛三处比分对不上（如加拿大 vs 卡塔尔：网页 3-0/2-1/3-1，记忆 1-1/2-0/2-1）。

将重算逻辑（`direction_of` / `allowed_outcomes` / `project_scores_for_side`）抽到新模块 `domain/score_projection.py` 作为唯一数据源：
- 网页 `scripts/build_world_cup_daily_html.py` 删除本地重复实现，改为 import；
- 写回侧 `domain/persistence.py`（MEMORY 条目 `score_summary` + 持久化 payload `predicted_scores`）与 `domain/writeback.py`（teams_2026.md `format_score_ou_note`）均改用 `project_scores_for_side`，模型原始 `top_scores` 作为兜底。

至此三处比分完全一致，复盘/护栏修正也能体现在网页与记忆中。446 测试通过。

> 补充：方向判定为「平局」且大小球判「大球」时，平局比分（对角线 0-0/1-1…）与「大球同侧（总进球>盘线）」硬约束求交集会塌缩到 2-2/3-3/4-4 等大比分。`allowed_outcomes` 增加：方向为平局时额外并入胜平负**第二高方向**的比分（如第二高为主胜则补 2-1/3-0/3-1），避免失真。

### 2. 大小球低线均势偏小修正（`ou_low_line_balanced_under_nudge`）

复盘 24 场已完赛世界杯小组赛大小球（命中 11/24=45.8%，押大押小各约 46%，接近抛硬币）。结构化水位特征显示：

- 降盘 + over 侧低水（暗钱买大）→ 实际走大 4/4=100%（引擎既有 `ou_line_down_low_water_over_trap` 已正确捕捉）；
- 关键盲点：低盘线（≤2.5，小组赛常见）+ 中性水位（大小赔率差 < 0.04，庄家无倾向）+ 盘口未升、无暗钱买大信号 → 实际几乎一边倒走小（3/3），但模型仅命中 1/3。

`domain/postprocess.py` 的 `extract_over_under_market_signal` 新增与既有 `balanced_high_line_over_nudge`（高线均势诱大）对称的规则：`goal_pressure == balanced` 且终盘线 ≤2.5、`|bias_final| ≤ 0.02`、未升盘、无任何 over 暗钱/背离/诱多加成时，给一个小幅负 `pace_shift`（−0.008 ～ −0.016）把比分往小格局收，追加信号 `ou_low_line_balanced_under_nudge`。回测在 24 场上触发 1 场（巴西vs摩洛哥），方向正确，零误报。

---

## 2026-06-17

### 1. 让球方向操盘手法矩阵（升降盘 × 水位 × 欧赔方向）

`domain/inference.py` 的 `classify_market_operation_pattern` 新增「亚盘升降盘 × 让球方水位档位 × 欧赔方向」方向手法矩阵，6 条规则映射阻上/诱上/防客/诱客/阻下，输出 `direction_handicap_matrix`。世界杯无伤病数据，用欧赔热门方升/降代理「有无利空」。水位字段存小数赔率全值，判档前转港水（`赔率-1`）。矩阵只产软性 deception/corroboration 分 + 方向标签，不单独加权改概率。

口诀标签经 `compute_tri_axis_consistency` 透传到 `tri_axis_consistency.direction_handicap`，由 `_compose_tri_axis_verdict` 以「让球口诀[…]」并入研判文本，并在世界杯日报网页「临场资金」研判行展示。

### 2. 临场压盘诱多识别（`ou_line_down_low_water_trap`）

`domain/postprocess.py` 的 `extract_over_under_market_signal` 新增：赛前格局被压（降盘）但 over 侧静态处中低水位时，判「压盘诱小、暗里防大」，给 over 侧 `pace_shift` 加成（上限 0.10，按水位档位缩放），把比分推向大格局，追加信号 `ou_line_down_low_water_over_trap`。与盘水背离加成互斥。

### 3. 定时器完赛自动回填 + 刷新

`scripts/world_cup_prediction_timer.py` 新增 `auto_sync_results()`，`run_cycle` 每轮对「已开赛未回填」的比赛调用正式结果闭环 `auto-sync-results`，回填成功后把对应日期并入刷新集合，自动把终场比分 + 最新大小球/欧赔/亚盘水位刷上网页。

### 4. 世界杯日报比分/总进球渲染口径收口

`scripts/build_world_cup_daily_html.py`：
- `_scores_for_side`：方向硬约束（绝不出反向爆冷比分）+ 大小球同侧 + 爆冷/平局放行（`_allowed_outcomes`）+ 候选集内条件归一化；不足 3 个时按胜平负第二高方向补足，仍空才回退到概率最高一侧。
- `_total_goals_row`：「最可能总进球」只列与大小球判定同侧的总进球档并重新归一化，消除「3 球众数 vs 小球累积」的展示口径矛盾。

### 5. 澳客队名别名补齐

`okooo_team_aliases.json` 的 `world_cup` / `世界杯` 区块补「刚果民主共和国」（民主刚果/刚果(金)/刚果金/DR刚果…）与「乌兹别克斯坦」别名，修复长队名前缀截断导致赛事匹配失败、抓不到真实盘口（`missing_real_market_line`）的问题。

关联文件：
- `europe_leagues/domain/inference.py`、`europe_leagues/domain/postprocess.py`
- `europe_leagues/scripts/world_cup_prediction_timer.py`、`europe_leagues/scripts/build_world_cup_daily_html.py`
- `europe_leagues/okooo_team_aliases.json`、`europe_leagues/test_review_learning_adjustment.py`
- `docs/architecture/europe_leagues_architecture.md` 3.7 节
- `.trae/skills/world-cup-daily-predictions-page/SKILL.md`、`.trae/skills/world-cup-prediction-timer/SKILL.md`

---

## 2026-06-06

### 1. 正式 URL 构造收口到纯数字 external_match_id

本轮补齐了澳客移动端 URL 的统一构造入口，避免内部比赛键被误拼进 `MatchID`。

当前固定口径：
- 正式主动访问的移动端 URL 统一走 `runtime.match_ids.build_okooo_match_url()`
- 仅允许纯数字 `external_match_id`
- `internal_match_id / teams_match_id` 仅保留给项目内部定位、归档和写回，不再允许直接访问澳客页面

当前正式页面形态：
- 欧赔：`https://m.okooo.com/match/odds.php?MatchID=<external_match_id>`
- 亚值：`https://m.okooo.com/match/handicap.php?MatchID=<external_match_id>`
- 历史：`https://m.okooo.com/match/history.php?MatchID=<external_match_id>`
- `overunder.php` / `daxiao.php` 只保留为大小球 fallback

关联文件：
- `europe_leagues/runtime/match_ids.py`
- `europe_leagues/okooo_save_snapshot.py`
- `europe_leagues/runtime/result_sync.py`
- `europe_leagues/okooo_playwright_helper.py`

### 2. 澳客验证页识别扩展

本轮把澳客风控识别从“只看文字阻断页”扩展到“同时识别图形验证页”。

新增识别范围：
- `请进行验证`
- `滑动到最右边`
- `拖动滑块`
- `请按住滑块`
- `验证码`
- `canvas / verify iframe / 大图验证` 等 DOM 特征

并修正导航行为：
- `navigate()` 命中疑似验证页后会刷新一次
- 刷新后仍是验证页则直接返回失败，不再误当作成功页面继续解析

关联文件：
- `europe_leagues/runtime/okooo_access.py`
- `europe_leagues/okooo_playwright_helper.py`
- `europe_leagues/okooo_match_finder.py`
- `europe_leagues/test_okooo_save_snapshot.py`
- `europe_leagues/test_result_sync.py`

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
- 再扩到 `100` 组
- 当前扩到 `500` 组（`5` 个 iPhone device pool × `100` 个版本组合）

当前设备池特征：
- 全部为 `iPhone Safari` 风格 UA
- 分布在 `5` 个真实 iPhone device pool（iPhone SE / 12 / 13 mini / 14 Plus / 15 Pro）
- 每个 device pool 使用各自真实的 `viewport` 与 `device_scale_factor`（如 iPhone SE 为 `375x667` / DPR `2`，iPhone 15 Pro 为 `393x852` / DPR `3`），不再统一成单一 viewport
- 每次访问通过 `random_mobile_profile()` 随机选择一个 profile

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
- `500` 设备池
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
- `500` 组随机移动 profile 是当前公共访问池
- 欧赔优先输出 `multi_company_consensus`
- 正式 `predict-match` 已验证可稳定拿到真实欧赔、亚值、大小球、凯利

如果后续代码与本文冲突，以当前代码实现为准，优先参考：
- `europe_leagues/app/cli.py`
- `europe_leagues/okooo_mobile_access.py`
- `europe_leagues/okooo_save_snapshot.py`
- `europe_leagues/domain/persistence.py`
- `europe_leagues/runtime/result_sync.py`

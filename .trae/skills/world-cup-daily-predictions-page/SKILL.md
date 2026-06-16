---
name: "world-cup-daily-predictions-page"
description: "世界杯每日比赛预测网页自动生成技能。给定某天的世界杯比赛清单，逐场调用正式 predict-match 取最新盘口与预测，自动生成/更新暗色卡片 HTML（胜平负概率 + 比分 + 大小球 + 真实欧赔/亚盘/凯利原始盘口 + 三轴研判 + 临场资金质检 + 爆冷预警）。Invoke when user asks to predict a day's World Cup matches and produce or refresh the predictions web page."
---

# World Cup Daily Predictions Page

把"当天世界杯比赛全部预测 + 生成/更新网页"封装成一条可复用流程。每天预测时自动产出
`world_cup/analysis/predictions/<date>_predictions.html`。

## 入口规则

- 发现 / 兼容入口：`europe_leagues/prediction_system.py`
- 真实命令实现：`europe_leagues/app/cli.py`
- 网页生成器：`europe_leagues/scripts/build_world_cup_daily_html.py`

生成器内部对每场调用正式 `predict-match --json`（CLI-first，与 football-match-analysis 同一条链），
不另起弱兜底，盘口缺失时如实标注。

## 何时触发

用户表达「预测某天的世界杯比赛并生成/更新网页」「把今天/明天的世界杯比赛做成网页」
「刷新世界杯预测页」等意图时触发。

## 标准流程

1. **确定当天比赛清单**（显式口径）：和用户确认当天有哪些场次（主队、客队、开赛时间）。
   - 若用户只给日期没给清单，先用 `okooo_fetch_daily_schedule.py` 或 `collect-data` 拉当天世界杯赛程，
     再把识别到的场次回报用户确认，避免漏场/串场。
2. **生成网页（核心一步，自动逐场预测）**：

   ```bash
   cd /Users/bytedance/trae_projects/europe_leagues
   python3 scripts/build_world_cup_daily_html.py \
     --date 2026-06-17 \
     --match 法国,塞内加尔,03:00 \
     --match 伊拉克,挪威,06:00 \
     --match 阿根廷,阿尔及利亚,09:00 \
     --match 奥地利,约旦,12:00
   ```

   - `--match` 格式为 `主队,客队[,时间]`，可重复传多场。
   - 也可用 `--matches-file matches.json`，文件为 `[{"home":"法国","away":"塞内加尔","time":"03:00"}, ...]`。
   - 默认输出到 `world_cup/analysis/predictions/<date>_predictions.html`，可用 `--output` 覆盖。
   - 生成器会对每场重跑 `predict-match`，因此预测结果会同步归档（正式链 side effects 照常生效）。

3. **校验渲染**：用本地 http server 在浏览器打开核对（`file://` 被禁），看四要素是否齐全：
   胜平负条、Top 比分、真实盘口区块（欧赔/亚盘/凯利/大小，含来源标注）、三轴 + 临场资金研判、必要时爆冷预警。

   ```bash
   cd /Users/bytedance/trae_projects/europe_leagues/world_cup/analysis/predictions
   python3 -m http.server 8799   # 浏览器开 http://127.0.0.1:8799/<date>_predictions.html
   ```

## 网页内容口径（与现有页面一致）

每张比赛卡片自动渲染：

- 胜平负方向 + 信心、三色概率条
- 最可能总进球分布、Top 3 比分
- 大小球方向 + 盘口线 + 盘口漂移
- **真实盘口原始数据区块**：欧赔（开→终 + 来源如「13家共识」）、亚盘（开→终 + 水位 + 单家/均盘兜底）、
  凯利（主/平/客）、大小（大/小水位 @ 线）——用于直观证明使用真实数据
- 三轴综合研判（背离/共振/部分一致）+ 临场资金质检（drift_confidence：high 共振 / low 孤证 / n/a）
- 爆冷预警（仅中/高时显示，🟡/🔴）

口径详见 `docs/architecture/europe_leagues_architecture.md` 3.7 节。

## 关键规则

- **数据真实性**：盘口全部走澳客实时采集；队力数据世界杯多为 FIFA 排名兜底（fallback），网页 meta 已注明。
- 大小球不要默认按 2.5；缺失时如实落缺失态（生成器对空 final 自动跳过该行）。
- 亚盘让球符号约定：主让为负（如 -1.25 = 主队让 1.25 球，是深让，不是浅盘）。
  爆冷"盘口过浅"判断已修为按"强队实际让球数"，不要再用裸符号判断。
- 世界杯是 SoT-backed 联赛：predict-match 会完整写回（归档 + 赛果同步登记 + 准确率），无需额外操作。
- 默认 CLI-first，不要把底层 Python import 当成标准用户入口（排查时可临时直调）。

## 已验证样例

- `2026-06-17`：法国/塞内加尔、伊拉克/挪威、阿根廷/阿尔及利亚、奥地利/约旦 四场，
  生成器一次产出完整页面并通过浏览器核对，与手工版逐项一致。

## 冲突处理

如本说明与 `europe_leagues/app/cli.py`、`scripts/build_world_cup_daily_html.py`、
`europe_leagues/README.md` 实际实现冲突，以当前代码实现为准。

---
name: "okooo-match-finder"
description: "从澳客网(okooo.com)自动搜索获取足球比赛ID，支持英超、西甲等联赛。Invoke when user needs match_id for specific football match or wants to get okooo match URLs."
---

# 澳客网比赛查找工具

通过模拟用户访问移动端澳客站点，从「热门赛事」进入具体联赛赛程，定位到目标比赛并提取 `MatchID`，并可进一步抓取欧赔/亚盘/凯利的初始与最新数据用于对比。

## 核心功能

- 从 `https://m.okooo.com/saishi/remen/` 进入联赛（如法甲）赛程页
- 在赛程页通过球队名定位比赛行，提取 `MatchID`
- 生成相关页面 URL：
  - 欧赔（欧指）：`https://m.okooo.com/match/odds.php?MatchID=...`
  - 亚盘（亚指/让球盘）：`https://m.okooo.com/match/handicap.php?MatchID=...`
  - 凯利：欧赔页内的「凯利」tab（或独立 `kelly.php`，视站点策略）
- 可选：提取 99 家平均/平均指数的“初始 vs 最新”并计算变化

## 使用方式

### 命令行使用

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 okooo_match_finder.py <主队> <客队> [联赛]
```

### 代码调用

```python
from okooo_match_finder import OkoooMatchFinder

finder = OkoooMatchFinder()
match_id = finder.find_match_id("水晶宫", "西汉孤", "英超")

if match_id:
    print(f"欧赔: https://m.okooo.com/match/odds.php?MatchID={match_id}")
    print(f"亚值: https://m.okooo.com/match/handicap.php?MatchID={match_id}")
```

## 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| team1 | str | 是 | 主队名称（中文或简称） |
| team2 | str | 是 | 客队名称（中文或简称） |
| league_hint | str | 否 | 联赛提示（如"英超"、"意甲"、"西甲"、"德甲"、"法甲"） |

## 输出结果

- `match_id`（即 `MatchID`）
- 欧赔 URL：`https://m.okooo.com/match/odds.php?MatchID={id}`
- 亚值 URL：`https://m.okooo.com/match/handicap.php?MatchID={id}`
- 凯利入口：优先从欧赔页点击「凯利」tab；若可直达则 `https://m.okooo.com/match/kelly.php?MatchID={id}`
- 可选：将抓取到的“赛程+欧赔/亚值/凯利初始&最新&变化”保存到本地项目（命名规则：`赛事名称_时间.json`）

## 保存到本地（推荐）

项目已提供脚本 [okooo_save_snapshot.py](file:///Users/bytedance/trae_projects/europe_leagues/okooo_save_snapshot.py)，会按“热门赛事 → 联赛赛程 → MatchID → 欧赔/亚值/凯利”抓取并保存为 JSON。

默认输出会写到项目相对目录（并已通过 `.gitignore` 忽略，避免污染提交）：
- `europe_leagues/.okooo-scraper/snapshots/<league>/`

五大联赛目录示例（固定 slug，便于分批管理）：
- `premier_league`（英超）
- `la_liga`（西甲）
- `serie_a`（意甲）
- `bundesliga`（德甲）
- `ligue_1`（法甲）

### 推荐：本地 Chrome 实时抓取（更稳）

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 okooo_save_snapshot.py --driver local-chrome --league 法甲 --team1 '巴黎圣曼' --team2 '南特'
```

### 按日期辅助定位（推荐）
当球队在赛程中显示为简称（如“毕尔巴鄂”而不是“毕尔巴鄂竞技”）时，用 `--date` 提高命中率：

```bash
python3 okooo_save_snapshot.py --driver local-chrome --league 西甲 --team1 '毕尔巴鄂竞技' --team2 '奥萨苏纳' --date 2026-04-22
```

### 同 match_id 覆盖写入
脚本默认会在输出目录中查找同 `match_id` 的 JSON 并覆盖，避免重复生成文件（可用 `--no-matchid-dedupe` 关闭）。
若想强制“固定赛事名文件”，可加 `--overwrite`（文件名为 `主队vs客队.json`）。
如需关闭“按联赛分目录”，可加 `--no-league-subdir`。

## 最新预测流程（总结）
目标：把“实时快照 JSON”直接交给预测系统分析，无需手工搬文件/转格式。

### 1) 生成实时快照（推荐 local-chrome）

```bash
cd /Users/bytedance/trae_projects/europe_leagues
python3 okooo_save_snapshot.py --driver local-chrome --league 西甲 --team1 '毕尔巴鄂竞技' --team2 '奥萨苏纳' --date 2026-04-22
```

输出位置（按联赛分目录）：
- `europe_leagues/.okooo-scraper/snapshots/la_liga/*.json`

### 2) 预测系统读取顺序（自动）
预测流程会按优先级读取赔率数据：
1. `europe_leagues/<league>/analysis/odds_snapshots/*_odds_snapshot.json`（批量赔率落盘）
2. `europe_leagues/.okooo-scraper/snapshots/<league>/*.json`（实时快照：由 `okooo_save_snapshot.py` 生成）
3. `europe_leagues/okooo_snapshots/*.json`（旧目录兼容）

### 3) 运行预测（写回 teams_2025-26.md）
入口脚本：
- [optimized_prediction_workflow.py](file:///Users/bytedance/trae_projects/europe_leagues/optimized_prediction_workflow.py)
- [enhanced_prediction_workflow.py](file:///Users/bytedance/trae_projects/europe_leagues/enhanced_prediction_workflow.py)

预测结果写入位置（每个联赛）：
- `europe_leagues/<league>/teams_2025-26.md`

写入方式：
- 更新对应日期赛程表行的“备注”列
- 以可重复覆盖的格式追加，例如：`进行中；预测:主胜 信心:0.48 爆冷:低 动态调权:样本不足`

不再生成的文件：
- `europe_leagues/<league>/analysis/predictions/*_predictions*.md`

运行日志输出（已收敛到运行时目录，避免污染仓库根目录）：
- `europe_leagues/.okooo-scraper/runtime/enhanced_prediction.log`
- `europe_leagues/.okooo-scraper/runtime/prediction_workflow.log`

### 4) 预测前自动刷新赔率（默认开启）
预测时会对“已进入预测列表的比赛”先刷新一次最新赔率快照，再用最新赔率进行预测。
- 默认开启：`OKOOO_REFRESH_LIVE=1`
- 临时关闭：`OKOOO_REFRESH_LIVE=0`

说明：
- 刷新优先使用 `match_id`（若可用），从而跳过赛程匹配，稳定更新欧赔/亚值/凯利。

## 当日赛程抓取（仅获取 MatchID 列表）
如果你只需要某一天的赛程（例如 2026-04-22 西甲），可以用：
- [okooo_fetch_daily_schedule.py](file:///Users/bytedance/trae_projects/europe_leagues/okooo_fetch_daily_schedule.py)

```bash
python3 europe_leagues/okooo_fetch_daily_schedule.py --league 西甲 --date 2026-04-22
```

输出示例：
- `europe_leagues/.okooo-scraper/schedules/la_liga/2026-04-22.json`

## 常见问题
### 为什么只更新了某一场比赛？
原因通常是“预测列表里只有这一场”：
- `analysis/odds_snapshots/*_odds_snapshot.json` 不存在或不包含该日期
- `.okooo-scraper/snapshots/<league>/` 下只有部分比赛的实时快照

预测流程不会主动“发现当天所有比赛”，它只会对已进入预测列表的比赛逐场刷新赔率并写回 `teams_2025-26.md`。

## 最新流程（推荐）
此流程适合“需要从热门赛事页出发，找到赛程，并对比欧赔/亚值/凯利初始与最新变化”的需求。

### 1) 从热门赛事进入联赛赛程并定位 MatchID

```bash
# 1. 打开热门赛事页
browser-use open 'https://m.okooo.com/saishi/remen/'

# 2. 点击联赛（示例：法甲）
# 提示：先用 state 找到 “法甲” 对应的元素索引，再 click
browser-use --json state | head -n 60
browser-use click <法甲的索引>

# 3. 在法甲赛程页里，查找包含“巴黎”“南特”的比赛行，并提取 MatchID
browser-use eval "JSON.stringify(Array.from(document.querySelectorAll(\"a[href*='history.php?MatchID=']\")).map(a=>{const m=a.href.match(/MatchID=(\\d+)/);const mid=m?m[1]:null;const row=a.closest('li')||a.closest('tr')||a.closest('div');const text=row?(row.innerText||'').replace(/\\s+/g,' ').trim():'';return {mid,href:a.href,text};}).filter(x=>x.mid&&x.text.includes('巴黎')&&x.text.includes('南特')).slice(0,5))"
```

### 2) 欧赔：抓“99家平均 初始/最新”并计算变化

```bash
browser-use open 'https://m.okooo.com/match/odds.php?MatchID=<MatchID>'

# 直接从 99家平均 行提取初赔/即时
browser-use eval "(() => { const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('99家平均')); if(!row) return JSON.stringify({found:false}); const num=(sel)=>{const el=row.querySelector(sel); if(!el) return null; const v=parseFloat((el.textContent||'').trim()); return Number.isFinite(v)?v:null;}; const initial={home:num('span[type=sheng]'),draw:num('span[type=ping]'),away:num('span[type=fu]')}; const final={home:num('span[type=xinsheng]'),draw:num('span[type=xinping]'),away:num('span[type=xinfu]')}; return JSON.stringify({found:true, initial, final, delta:{home:final.home-initial.home, draw:final.draw-initial.draw, away:final.away-initial.away}}); })()"
```

### 3) 亚值：抓“平均指数 初盘/最新水位”

```bash
browser-use open 'https://m.okooo.com/match/handicap.php?MatchID=<MatchID>'

# 平均指数行格式通常类似：平均指数 1.87 两球 1.99  1.95 两球/两球半 1.89
browser-use eval "(() => { const row=[...document.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('平均指数')); if(!row) return JSON.stringify({found:false}); const s=(row.innerText||'').replace(/\\s+/g,' ').trim(); const m=s.match(/平均指数\\s*(\\d+\\.\\d+)\\s*([\\u4e00-\\u9fff/]+)\\s*(\\d+\\.\\d+)\\s*(\\d+\\.\\d+)\\s*([\\u4e00-\\u9fff/]+)\\s*(\\d+\\.\\d+)/); if(!m) return JSON.stringify({found:true, parsed:false, text:s}); const initial={home_water:parseFloat(m[1]), handicap_text:m[2], away_water:parseFloat(m[3])}; const final={home_water:parseFloat(m[4]), handicap_text:m[5], away_water:parseFloat(m[6])}; return JSON.stringify({found:true, parsed:true, initial, final, delta:{home_water:final.home_water-initial.home_water, away_water:final.away_water-initial.away_water}}); })()"
```

### 4) 凯利：从欧赔页切换到「凯利」tab，抓“99家平均 初始/最新凯利”

说明：`kelly.php` 有时会被拦截，但欧赔页内切换 tab 往往可用。

```bash
browser-use open 'https://m.okooo.com/match/odds.php?MatchID=<MatchID>'

# 点击“凯利”tab（若找不到可先 state 查看对应元素索引）
browser-use eval "(() => { const els=[...document.querySelectorAll('a,div,span,button')]; const el=els.find(e=>((e.innerText||'').trim()==='凯利')); if(!el) return JSON.stringify({clicked:false}); el.click(); return JSON.stringify({clicked:true, tag:el.tagName}); })()"

# 在凯利表格里，选取包含“初始凯利/最新凯利”的 table，然后抓 99家平均 行
browser-use eval "(() => { const tbl=[...document.querySelectorAll('table')].find(t=>t.innerText.includes('初始凯利') && t.innerText.includes('最新凯利') && t.innerText.includes('99家平均')); if(!tbl) return JSON.stringify({found:false}); const row=[...tbl.querySelectorAll('tr')].find(tr=>(tr.innerText||'').includes('99家平均')); if(!row) return JSON.stringify({found:false}); const tds=[...row.querySelectorAll('td')].map(td=>(td.innerText||'').trim()); const nums=(s)=>((s.match(/\\d+\\.\\d{2}/g)||[]).map(parseFloat)); const init=nums(tds[1]||''); const fin=nums(tds[2]||''); const payout=nums(tds[3]||''); const initial=init.length>=3?{home:init[0],draw:init[1],away:init[2]}:null; const final=fin.length>=3?{home:fin[0],draw:fin[1],away:fin[2]}:null; return JSON.stringify({found:true, initial, final, payout_rate:(payout[0]??null)}); })()"
```

## 示例（法甲：巴黎圣曼 vs 南特）
使用上面的流程可在法甲赛程中定位到 `MatchID=1301465`，并抓到：
- 欧赔（99家平均）初赔 vs 最新
- 亚值（平均指数）初盘 vs 最新水位
- 凯利（99家平均）初始 vs 最新

## 支持联赛

| 联赛 | league_hint |
|------|-------------|
| 英超 | 英超 / premier |
| 西甲 | 西甲 / la_liga |
| 意甲 | 意甲 / serie_a |
| 德甲 | 德甲 / bundesliga |
| 法甲 | 法甲 / ligue_1 |

## 注意事项

- `curl` 直接访问 `m.okooo.com` 经常返回 405，建议使用 `browser-use` 做浏览器模拟访问。
- 若遇到偶发 405，可尝试：
  - `browser-use close` 后重新 `open`
  - 加 `--headed` 重新打开
  - 从赛程页重新定位 `MatchID` 再进入赔率页
- 本地 Chrome 模式会使用 CDP（远程调试端口），如遇端口占用脚本会自动尝试相邻端口。

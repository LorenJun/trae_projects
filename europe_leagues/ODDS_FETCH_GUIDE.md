# 澳客网赔率数据获取指南

## 概述

本项目使用 Playwright 自动化工具从澳客网(okooo.com)获取实时赔率数据，支持自动查找比赛ID，避免手动查找的麻烦。

## 核心文件

| 文件 | 功能 |
|------|------|
| `backfill_odds_snapshots.py` | 主脚本，支持通过比赛ID或球队名称获取赔率 |
| `okooo_helper_v2.py` | Playwright赔率提取核心模块 |
| `okooo_match_finder.py` | 比赛ID自动查找工具 |

## 使用方法

### 方法1: 通过球队名称自动查找（推荐）

```bash
python backfill_odds_snapshots.py --teams "主队名称,客队名称" --league 联赛名称
```

**示例:**

```bash
# 获取亚冠神户胜利vs吉达国民的赔率
python backfill_odds_snapshots.py --teams "神户胜利,吉达国民" --league afc_champions_league

# 获取英超比赛赔率
python backfill_odds_snapshots.py --teams "水晶宫,西汉姆联" --league premier_league
```

### 方法2: 通过已知比赛ID获取

```bash
python backfill_odds_snapshots.py --match-id 比赛ID --league 联赛名称
```

**示例:**

```bash
# 使用已知的比赛ID
python backfill_odds_snapshots.py --match-id 1324404 --league afc_champions_league
```

### 支持的联赛

- `premier_league` - 英超
- `la_liga` - 西甲
- `serie_a` - 意甲
- `bundesliga` - 德甲
- `ligue_1` - 法甲
- `afc_champions_league` - 亚冠

### 调试模式

如果需要查看浏览器窗口进行调试，添加 `--no-headless` 参数:

```bash
python backfill_odds_snapshots.py --teams "神户胜利,吉达国民" --league afc_champions_league --no-headless
```

## 数据输出

数据会自动保存到对应联赛的目录:
```
{联赛名称}/analysis/odds_snapshots/{比赛ID}_odds_{时间戳}.json
```

**示例输出文件:**
```json
{
  "match_id": "1324404",
  "source_url": "https://www.okooo.com/soccer/match/1324404/odds/",
  "fetched_at": "2026-04-21 00:16:49",
  "bookmakers": {
    "威廉.希尔": {
      "company": "威廉.希尔",
      "initial": {"home": 4.33, "draw": 3.80, "away": 1.67},
      "current": {"home": 4.40, "draw": null, "away": null}
    },
    "竞彩官方": {
      "company": "竞彩官方",
      "initial": {"home": 5.15, "draw": 3.85, "away": 1.48},
      "current": {"home": null, "draw": null, "away": null}
    }
  }
}
```

## 自动查找机制

当使用 `--teams` 参数时，系统会:

1. **检查已知映射** - 首先查找内置的比赛ID映射表
2. **检查本地缓存** - 查找之前搜索过的比赛ID
3. **在线搜索** - 访问澳客网搜索比赛
4. **验证ID** - 访问比赛页面验证ID是否正确
5. **获取数据** - 使用验证后的ID获取赔率数据

### 添加已知比赛映射

如果某个比赛经常被查询，可以在 `okooo_match_finder.py` 中添加映射:

```python
KNOWN_MATCHES = {
    '神户胜利_吉达国民': '1324404',
    '吉达国民_神户胜利': '1324404',
    # 添加更多映射...
}
```

## 反爬虫机制

本项目使用以下技术绕过澳客网的反爬虫机制:

1. **浏览器指纹伪装** - 随机User-Agent、视口大小
2. **禁用自动化标记** - 移除 `navigator.webdriver` 属性
3. **JavaScript执行** - 在页面上下文中提取数据
4. **智能等待** - 等待页面完全加载后再提取数据
5. **自动重试** - 访问被阻断时自动刷新重试

## 常见问题

### Q: 为什么有时找不到比赛？
A: 请确保:
- 球队名称正确（可以使用简称）
- 选择了正确的联赛
- 比赛确实存在于澳客网

### Q: 如何获取比赛ID？
A: 访问澳客网比赛页面，URL中的数字就是比赛ID:
```
https://www.okooo.com/soccer/match/1324404/odds/
# 比赛ID: 1324404
```

### Q: 数据更新频率是多少？
A: 每次运行脚本都会获取最新的实时数据。澳客网的赔率数据会实时更新。

### Q: 为什么有些公司的即时赔率为null？
A: 表示该公司尚未更新即时赔率，只有初赔数据。

## 技术实现

### 核心流程

```
用户输入球队名称
    ↓
查找比赛ID (okooo_match_finder.py)
    ├── 检查已知映射
    ├── 检查本地缓存
    └── 在线搜索验证
    ↓
获取赔率数据 (okooo_helper_v2.py)
    ├── 启动Playwright浏览器
    ├── 访问赔率页面
    ├── 等待数据加载
    └── JavaScript提取数据
    ↓
保存数据到JSON文件
```

### 关键类和方法

- `OkoooMatchFinder` - 比赛ID查找器
  - `find_match_id(team1, team2)` - 查找比赛ID
  - `verify_match_id(match_id, teams)` - 验证ID正确性

- `OkoooScraper` - 赔率提取器
  - `extract_odds(match_id)` - 提取指定比赛的赔率

- `OddsSnapshotBackfill` - 主控制类
  - `get_odds_by_teams(team1, team2)` - 一站式获取赔率

## 更新日志

### 2026-04-21
- ✨ 新增自动查找比赛ID功能
- ✨ 添加球队名称到ID的映射机制
- ✨ 支持通过 `--teams` 参数直接查询
- 🔧 修复Playwright异步问题
- 📝 添加详细的使用文档

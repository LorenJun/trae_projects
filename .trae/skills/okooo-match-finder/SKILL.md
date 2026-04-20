---
name: "okooo-match-finder"
description: "从澳客网(okooo.com)自动搜索获取足球比赛ID，支持英超、西甲等联赛。Invoke when user needs match_id for specific football match or wants to get okooo match URLs."
---

# 澳客网比赛查找工具

自动从澳客网搜索足球比赛ID，返回欧指/亚盘/凯利赔率URL。

## 核心功能

- 根据球队名称自动搜索并验证比赛ID
- 支持联赛提示（如"英超"、"意甲"）缩小范围
- 无缓存文件，直接在线获取
- 自动验证比赛ID正确性

## 使用方式

### 命令行使用

```bash
cd /Users/lin/trae_projects/europe_leagues
python3 okooo_match_finder.py <主队> <客队> [联赛]
```

### 代码调用

```python
from okooo_match_finder import OkoooMatchFinder

finder = OkoooMatchFinder()
match_id = finder.find_match_id("水晶宫", "西汉孤", "英超")

if match_id:
    print(f"欧指: https://www.okooo.com/soccer/match/{match_id}/odds/")
    print(f"亚盘: https://www.okooo.com/soccer/match/{match_id}/ah/")
```

## 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| team1 | str | 是 | 主队名称（中文或简称） |
| team2 | str | 是 | 客队名称（中文或简称） |
| league_hint | str | 否 | 联赛提示（如"英超"、"意甲"、"西甲"、"德甲"、"法甲"） |

## 输出结果

- 比赛ID (match_id)
- 欧指赔率URL: `https://www.okooo.com/soccer/match/{id}/odds/`
- 亚盘赔率URL: `https://www.okooo.com/soccer/match/{id}/ah/`

## 示例

**搜索水晶宫 vs 西汉孤（英超）:**

```bash
python3 okooo_match_finder.py 水晶宫 西汉孤 英超
```

**输出:**

```
正在搜索: 水晶宫 vs 西汉孤
  尝试从首页获取...
  尝试联赛页面: https://www.okooo.com/soccer/league/17/schedule/
    尝试从HTML源码提取...
    页面中找到 10 个 match_id
    ✓ 验证通过: 1296057

✅ 比赛ID: 1296057
   欧指: https://www.okooo.com/soccer/match/1296057/odds/
   亚盘: https://www.okooo.com/soccer/match/1296057/ah/
```

## 支持联赛

| 联赛 | league_hint |
|------|-------------|
| 英超 | 英超 / premier |
| 西甲 | 西甲 / la_liga |
| 意甲 | 意甲 / serie_a |
| 德甲 | 德甲 / bundesliga |
| 法甲 | 法甲 / ligue_1 |

## 注意事项

- 使用Playwright自动化浏览器
- 无需登录即可获取公开数据
- 搜索结果会经过验证确保正确
- 搜索失败时返回None
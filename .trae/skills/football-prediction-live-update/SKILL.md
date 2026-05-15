---
title: 足球预测临场数据更新与滚动记忆管理
description: 获取临场数据（首发、伤停、赔率变化）并更新MEMORY.md滚动记忆的标准流程
triggers:
  - 临场更新
  - 首发阵容
  - 赔率变化
  - 更新滚动记忆
  - live-update
  - 临场分析
name: football-prediction-live-update
---

# 足球预测临场数据更新与滚动记忆管理

## 概述

本技能定义了如何将临场数据（首发阵容、伤停更新、赔率变化）整合到当前正式预测链路中，并在需要时更新滚动记忆与预测备注。

## 核心原则

1. **覆盖更新**：临场分析后，用新预测**覆盖**原预测行，而非重复添加
2. **标记区分**：使用【临场更新】标记区分初始预测 vs 临场更新
3. **变化说明**：必须包含"调整说明"解释预测变化的逻辑
4. **单一来源**：每场比赛在滚动记忆中只有**一个最终预测**

## 标准格式

### 更新前（初始预测）
```markdown
- [la_liga|2026-05-15|赫罗纳|皇家社会] 2026-05-15 西甲 赫罗纳 vs 皇家社会
  预测: 主胜 (39.7%) | 比分: 1-0 > 1-1 > 0-1 | 大小球: 小球 2.75 (64.9%)
  · MatchID: ... | 更新时间: 2026-05-15 00:07:51
```

### 更新后（临场更新）
```markdown
- [la_liga|2026-05-15|赫罗纳|皇家社会] 2026-05-15 西甲 赫罗纳 vs 皇家社会
  【临场更新】预测: 主胜 (52.0%) | 比分: 2-1 > 1-0 > 1-1 | 大小球: 小球 2.75 | 亚盘: 赫罗纳-0.5 | 信心: ★★★★☆
  ◦ 欧赔: 2.13/3.57/3.06->1.99/3.68/3.37
  ◦ 亚盘: 平/半 1.96/1.86->半球 2.01/1.90
  ◦ 大小: 2.75 1.91/1.87->2.75 1.90/1.90
  ◦ 凯利: 0.93/0.93/0.93->0.94/0.94/0.94
  ▲ 风险: 低(31) 压力方目标: 保级抢分; 历史同向反打(9次)
  ◆ RAG记忆: ...
  · MatchID: ... | 更新时间: 2026-05-15 10:30:00
  ◦ 临场分析依据:
    - 首发阵容: [赫罗纳] ... [皇家社会] ...
    - 伤停更新: 赫罗纳主力中场伤愈复出，皇家社会边锋停赛
    - 赔率变化: 主胜2.13→1.99(↓0.14)，11/12家公司下调；亚盘平/半→半球(升盘)
    - 战意评估: 赫罗纳保级关键战，主场必须抢分；皇家社会欧战资格无望，动力不足
    - 机构信号: 符合"临场降强"信号——机构真实看好赫罗纳
    - 调整说明: 原预测主胜(39.7%)→临场提升为主胜(52%)，从双选31升级为单选3，赔率走势+亚盘升盘确认信心
```

## 更新流程

### 步骤1: 获取临场数据

```python
# 从多个数据源获取临场信息
live_data = {
    'lineups': fetch_lineups(match_id),      # 首发阵容
    'injuries': fetch_injuries(match_id),    # 伤停更新
    'odds_changes': fetch_odds_changes(match_id),  # 赔率变化
    'weather': fetch_weather(venue),         # 天气（可选）
    'news': fetch_team_news(match_id)        # 球队新闻
}
```

### 步骤2: 分析影响

```python
def analyze_live_impact(original_prediction, live_data):
    """分析临场数据对原预测的影响"""
    impact = {
        'direction_change': False,  # 方向是否改变
        'confidence_change': 0,     # 信心度变化
        'score_adjustment': [],     # 比分调整
        'reasoning': []             # 调整理由
    }
    
    # 分析赔率变化
    if live_data['odds_changes']['home_win'] < original_prediction['odds']['home_win'] - 0.1:
        impact['confidence_change'] += 10
        impact['reasoning'].append("主胜赔率大幅下调，机构看好主队")
    
    # 分析首发阵容
    key_players_out = live_data['lineups'].get('key_absences', [])
    if key_players_out:
        impact['reasoning'].append(f"关键球员缺阵: {', '.join(key_players_out)}")
    
    # 分析战意
    if live_data['team_news'].get('motivation') == 'high':
        impact['confidence_change'] += 5
        impact['reasoning'].append("保级/争冠战意强烈")
    
    return impact
```

### 步骤3: 生成更新后预测

```python
def generate_updated_prediction(original, impact):
    """基于影响分析生成更新后的预测"""
    updated = original.copy()
    
    # 更新概率
    updated['probabilities']['home_win'] += impact['confidence_change']
    
    # 更新比分排序（如有必要）
    if impact.get('score_adjustment'):
        updated['scores'] = impact['score_adjustment']
    
    # 生成调整说明
    updated['adjustment_note'] = f"原预测{original['prediction']}→临场更新为{updated['prediction']}，{'；'.join(impact['reasoning'])}"
    
    return updated
```

### 步骤4: 更新 MEMORY.md

```python
def update_memory_md(match_id, updated_prediction, live_data):
    """更新 MEMORY.md 文件"""
    
    # 读取现有内容
    with open('MEMORY.md', 'r') as f:
        content = f.read()
    
    # 查找并替换该比赛的记录
    match_pattern = rf"(- \[.*?{match_id}.*?\n)(  预测:.*?\n)(.*?)(?=\n- \[|$)"
    
    # 构建新记录
    new_record = build_memory_record(updated_prediction, live_data)
    
    # 替换（覆盖更新）
    updated_content = re.sub(match_pattern, new_record, content, flags=re.DOTALL)
    
    # 写回文件
    with open('MEMORY.md', 'w') as f:
        f.write(updated_content)
```

## 关键字段说明

### 预测行字段
| 字段 | 格式 | 示例 |
|------|------|------|
| 预测方向 | 主胜/平局/客胜/双选XX (概率%) | 主胜 (52.0%) |
| 比分 | 首选 > 次选 > 第三 | 2-1 > 1-0 > 1-1 |
| 大小球 | 大球/小球 X.X (概率%) | 小球 2.75 (64.9%) |
| 亚盘 | 让球方±盘口 | 赫罗纳-0.5 |
| 信心 | ★数量 | ★★★★☆ |

### 临场分析依据字段
| 字段 | 内容 |
|------|------|
| 首发阵容 | 双方首发11人名单 |
| 伤停更新 | 新增伤停或复出球员 |
| 赔率变化 | 初盘→临盘变化，标注↑↓ |
| 战意评估 | 保级/争冠/欧战资格等动机分析 |
| 机构信号 | 凯利指数、盘口异动等 |
| 调整说明 | 原预测→新预测的变化逻辑 |

## 常见调整场景

### 场景1: 赔率大幅下调（临场降强）
```markdown
调整说明: 原预测主胜(39.7%)→临场提升为主胜(52%)，主胜赔率2.13→1.99(↓0.14)，
         11/12家公司下调，亚盘平/半→半球升盘，符合"临场降强"信号
```

### 场景2: 关键球员伤停
```markdown
调整说明: 原预测主胜(45%)→临场调整为平局(40%)，主队核心前锋伤缺，
         进攻火力大减，历史同阵容下胜率仅30%
```

### 场景3: 战意差异明显
```markdown
调整说明: 原预测客胜(35%)→临场调整为双选30(客胜/平局50%)，
         客队已夺冠无欲无求，主队保级生死战战意极强，需防冷门
```

## 自动化脚本

使用 `scripts/update_memory_live.py` 自动生成临场更新内容：

```bash
python3 .trae/skills/football-prediction-live-update/scripts/update_memory_live.py \
    --match-id la_liga_20260515_赫罗纳_皇家社会 \
    --odds-change "2.13->1.99" \
    --confidence-boost 12 \
    --reasoning "临场降强信号，亚盘升盘"
```

## 注意事项

1. **更新时间**：必须在比赛开始前30分钟内完成更新
2. **数据来源**：优先使用澳客网、官方首发名单等可靠来源
3. **版本控制**：每次更新修改`更新时间`字段
4. **避免重复**：确保是"覆盖"而非"追加"新记录
5. **AI记忆同步**：更新MEMORY.md后，同步更新AI上下文记忆

## 故障排除

| 问题 | 解决方案 |
|------|----------|
| MEMORY.md中找不到比赛记录 | 检查MatchID是否正确，或比赛尚未初始预测 |
| 更新后格式错乱 | 使用统一的`build_memory_record()`函数生成记录 |
| 原预测被意外删除 | 更新前先备份，或从git历史恢复 |
| 临场数据获取失败 | 使用备用数据源，或标记为"数据待补" |

## 相关文档

- `../../docs/standards/workflow.md` - 正式预测、写回与回填流程
- `../../docs/standards/skill_lifecycle.md` - Skill 正文与维护治理边界
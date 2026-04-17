---
document_title: "编码规范"
version: "1.0.0"
last_updated: "2026-04-18"
---

# 💻 编码规范

本规范定义项目中所有代码的编写标准，确保代码的一致性、可读性和可维护性。

---

## Python 代码规范

### 文件编码
- 所有Python文件使用 **UTF-8** 编码
- 文件开头添加编码声明：
  ```python
  # -*- coding: utf-8 -*-
  ```

---

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 模块/包名 | 小写 + 下划线 | `match_analyzer.py`, `odds_utils` |
| 类名 | 大驼峰 (PascalCase) | `MatchAnalyzer`, `OddsCalculator` |
| 函数/方法 | 小写 + 下划线 | `calculate_kelly_index()`, `get_match_data()` |
| 变量 | 小写 + 下划线 | `home_team`, `match_odds` |
| 常量 | 全大写 + 下划线 | `MAX_ODDS`, `DEFAULT_LEAGUE` |
| 私有方法 | 单下划线前缀 | `_internal_calc()`, `_helper_func()` |

---

### 代码格式化

#### 缩进
- 使用 **4个空格** 缩进
- 不使用Tab

#### 行长度
- 每行不超过 **100个字符**
- 长字符串使用括号换行：
  ```python
  long_message = (
      "This is a very long message that needs "
      "to be split across multiple lines "
      "for readability."
  )
  ```

#### 空行
- 顶层函数和类之间用 **2个空行**
- 方法之间用 **1个空行**
- 逻辑块之间用 **1个空行**

---

### 导入规范

#### 导入顺序
```python
# 1. 标准库
import os
import sys
import json

# 2. 第三方库
import requests
import pandas as pd
import numpy as np

# 3. 本地模块
from . import utils
from .match_analyzer import MatchAnalyzer
```

#### 导入规则
- 每个导入单独一行
- 避免使用 `from module import *`
- 相对导入使用点号

---

### 类型提示

```python
from typing import List, Dict, Optional, Tuple

def calculate_kelly_index(
    odds: float,
    implied_prob: float
) -> float:
    """计算凯利指数"""
    return odds * implied_prob

def get_match_data(
    match_id: str,
    league: Optional[str] = None
) -> Dict[str, any]:
    """获取比赛数据"""
    pass

def parse_odds(
    odds_text: str
) -> Tuple[float, float, float]:
    """解析赔率数据"""
    pass
```

---

### 文档字符串

#### 函数文档
```python
def calculate_kelly_index(odds: float, implied_prob: float) -> float:
    """
    计算凯利指数。

    凯利指数 = 赔率 × 市场隐含概率，
    用于衡量博彩公司对某一赛果的赔付风险。

    Args:
        odds: 赔率值，如 2.65
        implied_prob: 市场隐含概率，0.0-1.0

    Returns:
        float: 凯利指数

    Raises:
        ValueError: 当赔率或概率无效时

    Examples:
        >>> calculate_kelly_index(2.65, 0.377)
        1.00
    """
    if odds <= 1.0:
        raise ValueError("赔率必须大于1.0")
    if not (0.0 <= implied_prob <= 1.0):
        raise ValueError("概率必须在0.0-1.0之间")

    return odds * implied_prob
```

#### 类文档
```python
class MatchAnalyzer:
    """
    比赛分析器。

    负责对比赛双方进行多维度分析，包括：
    - 球队基本面分析
    - 近期状态评估
    - 历史交锋记录
    - 战术打法对比

    Attributes:
        home_team: 主队名称
        away_team: 客队名称
        league: 联赛名称
    """

    def __init__(self, home_team: str, away_team: str, league: str):
        self.home_team = home_team
        self.away_team = away_team
        self.league = league
```

---

### 错误处理

```python
# 使用try-except
try:
    data = fetch_odds_data(url)
except requests.exceptions.RequestException as e:
    logger.error(f"获取数据失败: {e}")
    raise DataFetchError(f"无法访问 {url}") from e

# 验证输入
def validate_odds(odds: float) -> None:
    if not isinstance(odds, (int, float)):
        raise TypeError("赔率必须是数字")
    if odds <= 1.0:
        raise ValueError(f"赔率必须大于1.0，当前值: {odds}")
```

---

### 日志规范

```python
import logging

logger = logging.getLogger(__name__)

# 日志级别
logger.debug("详细调试信息")
logger.info("一般信息")
logger.warning("警告信息")
logger.error("错误信息")
logger.critical("严重错误")

# 格式化日志
logger.info(
    "分析比赛: %s vs %s, 联赛: %s",
    home_team, away_team, league
)
```

---

## Markdown 文档规范

### 标题层级

```markdown
# 一级标题 (H1)
## 二级标题 (H2)
### 三级标题 (H3)
#### 四级标题 (H4)
```

### 列表

```markdown
- 无序列表项1
- 无序列表项2
  - 嵌套列表项

1. 有序列表项1
2. 有序列表项2
```

### 代码块

````markdown
```python
# Python 代码
print("Hello World")
```

```json
{
  "key": "value"
}
```
````

### 链接

```markdown
[显示文本](URL)
<https://example.com>
```

### 表格

```markdown
| 左对齐 | 右对齐 | 居中 |
|--------|-------:|:----:|
| 内容1  |    10 |  ✅  |
| 内容2  |    20 |  ❌  |
```

---

## Git 提交规范

### 提交信息格式

```
<类型>: <简短描述>

<详细描述>
```

### 提交类型

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复bug |
| `docs` | 文档更新 |
| `style` | 代码格式调整 |
| `refactor` | 重构 |
| `perf` | 性能优化 |
| `test` | 测试相关 |
| `chore` | 构建/工具链 |

### 示例

```
feat: 添加凯利指数计算功能

- 实现 calculate_kelly_index() 函数
- 添加完整的类型提示和文档
- 包含单元测试
```

---

## 测试规范

### 测试文件命名
- 测试文件: `test_*.py`
- 测试函数: `test_*`

### 测试示例

```python
import pytest
from odds_utils import calculate_kelly_index

def test_kelly_index_normal():
    """测试正常情况下的凯利指数计算"""
    assert calculate_kelly_index(2.65, 0.377) == pytest.approx(1.00, 0.01)

def test_kelly_index_invalid_odds():
    """测试无效赔率"""
    with pytest.raises(ValueError):
        calculate_kelly_index(0.5, 0.5)
```

---

## 项目结构规范

```
trae_projects/
├── agents/              # Agent文档
├── .trae/               # Trae技能
├── docs/                # 文档
│   ├── standards/       # 标准规范
│   └── tutorials/       # 教程
├── europe_leagues/      # 联赛数据
├── scripts/             # Python脚本
├── data/                # 数据目录（按需创建）
│   ├── odds/            # 赔率数据
│   └── teams/           # 球队数据
├── analysis/            # 分析目录（按需创建）
│   ├── match/           # 比赛分析
│   └── odds/            # 赔率分析
├── reports/             # 报告目录（按需创建）
│   ├── match/           # 比赛报告
│   └── monthly/         # 月度报告
└── logs/                # 日志目录（按需创建）
```

---

## 最佳实践

1. **先写文档，后写代码**: 先完善设计文档
2. **单一职责**: 每个函数只做一件事
3. **DRY原则**: 避免重复代码
4. **KISS原则**: 保持简单
5. **YAGNI原则**: 不要过度设计
6. **测试驱动**: 先写测试，后写实现
7. **持续重构**: 定期清理和优化代码

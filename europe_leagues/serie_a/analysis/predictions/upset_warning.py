#!/usr/bin/env python3
"""
冷门预警分析脚本
分析足球比赛的冷门概率，基于赔率、凯利指数、离散率等多维度数据
支持从中国体育彩票官网、澳客官网实时获取数据
"""

import re
import statistics
from datetime import datetime
from typing import Dict, List, Tuple, Optional

try:
    from .data_scraper import DataCollector, SportteryScraper, OkoooScraper
    DATA_COLLECTOR_AVAILABLE = True
except ImportError:
    DATA_COLLECTOR_AVAILABLE = False
    DataCollector = None
    SportteryScraper = None
    OkoooScraper = None


class KellyIndex:
    """凯利指数分析类"""

    @staticmethod
    def calculate_kelly(odds: float, implied_probability: float) -> float:
        """计算凯利指数"""
        return odds * implied_probability

    @staticmethod
    def analyze_kelly一致性(kelly_values: List[float]) -> Dict:
        """分析凯利指数一致性"""
        if len(kelly_values) < 2:
            return {
                'mean': kelly_values[0] if kelly_values else 0,
                'stdev': 0,
                '一致性': '无法判断',
                'variance': 0
            }

        mean_kelly = statistics.mean(kelly_values)
        stdev_kelly = statistics.stdev(kelly_values)
        variance = stdev_kelly ** 2

        consistency = '高' if variance < 0.01 else '中' if variance < 0.03 else '低'

        return {
            'mean': mean_kelly,
            'stdev': stdev_kelly,
            '一致性': consistency,
            'variance': variance
        }

    @staticmethod
    def get_risk_level(kelly_value: float) -> str:
        """获取凯利指数风险等级"""
        if kelly_value < 0.90:
            return '🔴低风险区'
        elif kelly_value <= 1.00:
            return '🟡正常区'
        else:
            return '🟢高风险区'

    @staticmethod
    def interpret_kelly口诀(home_kelly: float, draw_kelly: float, away_kelly: float) -> Dict:
        """
        解读凯利口诀：
        低一致，稳；高分散，冷；
        临场降，强；临场升，坑
        """
        results = {}

        all_kelly = [home_kelly, draw_kelly, away_kelly]
        kelly_analysis = KellyIndex.analyze_kelly一致性(all_kelly)

        results['一致性分析'] = kelly_analysis

        min_kelly = min(all_kelly)
        if min_kelly == home_kelly:
            results['热门方'] = '主胜'
            results['热门凯利'] = home_kelly
        elif min_kelly == draw_kelly:
            results['热门方'] = '平局'
            results['热门凯利'] = draw_kelly
        else:
            results['热门方'] = '客胜'
            results['热门凯利'] = away_kelly

        if kelly_analysis['一致性'] == '高':
            results['口诀解读'] = '低一致，稳 - 机构高度一致，冷门概率低'
        else:
            results['口诀解读'] = '高分散，冷 - 机构分歧大，冷门概率高'

        return results


class 离散率Analyzer:
    """离散率分析类"""

    @staticmethod
    def calculate离散率(odds_list: List[float]) -> float:
        """计算离散率（标准差/平均值×100%）"""
        if len(odds_list) < 2:
            return 0.0
        mean_val = statistics.mean(odds_list)
        stdev_val = statistics.stdev(odds_list)
        return (stdev_val / mean_val) * 100 if mean_val != 0 else 0

    @staticmethod
    def get离散度等级(离散率: float) -> str:
        """获取离散度等级"""
        if 离散率 < 2:
            return '低'
        elif 离散率 < 5:
            return '中'
        else:
            return '高'


class 冷门预警Analyzer:
    """冷门预警综合分析类"""

    def __init__(self, home_team: str, away_team: str, league: str = '德甲'):
        self.home_team = home_team
        self.away_team = away_team
        self.league = league
        self.analysis_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def analyze赔率变化(self, initial_odds: Dict[str, float],
                        final_odds: Dict[str, float]) -> Dict:
        """分析赔率变化"""
        changes = {}
        for key in initial_odds:
            if key in final_odds:
                change = final_odds[key] - initial_odds[key]
                pct_change = (change / initial_odds[key]) * 100 if initial_odds[key] != 0 else 0
                changes[key] = {
                    '初始': initial_odds[key],
                    '最终': final_odds[key],
                    '变化': change,
                    '变化率': pct_change,
                    '方向': '↑' if change > 0 else '↓' if change < 0 else '→'
                }
        return changes

    def calculate冷门概率(self,
                          odds_data: Dict[str, List[float]],
                          kelly_data: Dict[str, List[float]],
                          离散率_data: Dict[str, float],
                          基本面_score: float = 0.5) -> Dict:
        """
        综合计算冷门概率
        权重分配：
        - 赔率异常度：20%
        - 凯利指数异常：20%
        - 离散率异常：15%
        - 临场变化异常：15%
        - 基本面异常：15%
        - 盘口异常：15%
        """
        probabilities = {}

        home_odds = odds_data.get('home', [2.0])
        away_odds = odds_data.get('away', [2.0])
        draw_odds = odds_data.get('draw', [3.0])

        min_odds = min(min(home_odds), min(away_odds), min(draw_odds))

        if min_odds == min(home_odds):
            probabilities['赔率异常度'] = 50 + (max(home_odds) - min(home_odds)) * 20
        elif min_odds == min(draw_odds):
            probabilities['赔率异常度'] = 40 + (max(draw_odds) - min(draw_odds)) * 15
        else:
            probabilities['赔率异常度'] = 40 + (max(away_odds) - min(away_odds)) * 20

        home_kelly_avg = statistics.mean(kelly_data.get('home', [1.0]))
        away_kelly_avg = statistics.mean(kelly_data.get('away', [1.0]))
        draw_kelly_avg = statistics.mean(kelly_data.get('draw', [1.0]))

        kelly_anomaly = max(home_kelly_avg, away_kelly_avg, draw_kelly_avg) - \
                        min(home_kelly_avg, away_kelly_avg, draw_kelly_avg)
        probabilities['凯利指数异常'] = kelly_anomaly * 50

        avg_离散率 = statistics.mean(list(离散率_data.values()))
        probabilities['离散率异常'] = min(avg_离散率 * 10, 60)

        probabilities['基本面异常'] = abs(基本面_score - 0.5) * 100

        probabilities['临场变化异常'] = 30
        probabilities['盘口异常'] = 25

        weights = {
            '赔率异常度': 0.20,
            '凯利指数异常': 0.20,
            '离散率异常': 0.15,
            '临场变化异常': 0.15,
            '基本面异常': 0.15,
            '盘口异常': 0.15
        }

        total_probability = sum(probabilities[key] * weights[key] for key in weights)

        return {
            '各项概率': probabilities,
            '权重': weights,
            '综合冷门概率': min(total_probability, 95)
        }

    def get风险等级(self, cold_prob: float) -> Tuple[str, str]:
        """获取风险等级"""
        if cold_prob > 50:
            return '🔴高风险', '高风险区'
        elif cold_prob > 30:
            return '🟡中风险', '中风险区'
        else:
            return '🟢低风险', '低风险区'

    def generate_report(self,
                        initial_odds: Dict[str, float],
                        final_odds: Dict[str, float],
                        kelly_data: Dict[str, List[float]],
                        离散率_data: Dict[str, float],
                        home_basic: Dict,
                        away_basic: Dict,
                        league_round: str = '第X轮') -> str:
        """生成完整的冷门预警报告"""

        odds_changes = self.analyze赔率变化(initial_odds, final_odds)

        kelly_result = KellyIndex.interpret_kelly口诀(
            statistics.mean(kelly_data['home']),
            statistics.mean(kelly_data['draw']),
            statistics.mean(kelly_data['away'])
        )

        基本面_score = 0.5
        cold_prob_result = self.calculate冷门概率(
            {'home': initial_odds.get('home', [2.0]),
             'away': initial_odds.get('away', [2.0]),
             'draw': initial_odds.get('draw', [3.0])},
            kelly_data,
            离散率_data,
            基本面_score
        )

        risk_level, risk_desc = self.get风险等级(cold_prob_result['综合冷门概率'])

        report = f"""# 🏆 {self.league} {league_round}：{self.home_team} vs {self.away_team} 冷门预警分析报告

## 一、基本信息速览

| 数据项 | {self.home_team}（主） | {self.away_team}（客） |
|-------|------------|-------------|
| 联赛排名 | 第{home_basic.get('rank', 'N/A')}位 | 第{away_basic.get('rank', 'N/A')}位 |
| 积分 | {home_basic.get('points', 'N/A')}分 | {away_basic.get('points', 'N/A')}分 |
| 战绩 | {home_basic.get('record', 'N/A')} | {away_basic.get('record', 'N/A')} |
| 主场战绩 | {home_basic.get('home_record', 'N/A')} | {away_basic.get('away_record', 'N/A')} |
| 进球/失球 | {home_basic.get('goals', 'N/A')} | {away_basic.get('goals', 'N/A')} |

## 二、近期状态分析

### {self.home_team}近期表现
- 近10场：{home_basic.get('form', 'N/A')}
- 状态评估：{home_basic.get('status', '🟡一般')}

### {self.away_team}近期表现
- 近10场：{away_basic.get('form', 'N/A')}
- 状态评估：{away_basic.get('status', '🟡一般')}

## 三、赔率与盘口分析

### 3.1 欧赔变化分析

| 赔率类型 | 初始赔率 | 最新赔率 | 变化幅度 | 市场倾向 |
|---------|---------|---------|---------|---------|
| {self.home_team}胜 | {odds_changes.get('home', {}).get('初始', 'N/A')} | {odds_changes.get('home', {}).get('最终', 'N/A')} | {odds_changes.get('home', {}).get('方向', 'N/A')} | [待分析] |
| 平局 | {odds_changes.get('draw', {}).get('初始', 'N/A')} | {odds_changes.get('draw', {}).get('最终', 'N/A')} | {odds_changes.get('draw', {}).get('方向', 'N/A')} | [待分析] |
| {self.away_team}胜 | {odds_changes.get('away', {}).get('初始', 'N/A')} | {odds_changes.get('away', {}).get('最终', 'N/A')} | {odds_changes.get('away', {}).get('方向', 'N/A')} | [待分析] |

### 3.2 凯利指数分析

#### 凯利指数数据
| 选项 | 凯利指数 | 风险等级 | 机构态度 |
|-----|---------|---------|---------|
| {self.home_team}胜 | {statistics.mean(kelly_data.get('home', [1.0])):.2f} | {KellyIndex.get_risk_level(statistics.mean(kelly_data.get('home', [1.0])))} | [待分析] |
| 平局 | {statistics.mean(kelly_data.get('draw', [1.0])):.2f} | {KellyIndex.get_risk_level(statistics.mean(kelly_data.get('draw', [1.0])))} | [待分析] |
| {self.away_team}胜 | {statistics.mean(kelly_data.get('away', [1.0])):.2f} | {KellyIndex.get_risk_level(statistics.mean(kelly_data.get('away', [1.0])))} | [待分析] |

#### 凯利口诀验证
```
低一致，稳；高分散，冷；
临场降，强；临场升，坑
```

| 分析维度 | 结论 |
|---------|------|
| 多家公司凯利一致性 | {kelly_result['一致性分析']['一致性']} |
| 离散度（方差） | {kelly_result['一致性分析']['variance']:.4f} |
| 热门方 | {kelly_result.get('热门方', 'N/A')} |
| 口诀解读 | {kelly_result.get('口诀解读', 'N/A')} |

### 3.3 离散率分析

| 选项 | 离散率 | 离散度等级 | 分析结论 |
|-----|-------|-----------|---------|
| {self.home_team}胜 | {离散率_data.get('home', 0):.2f}% | {离散率Analyzer.get离散度等级(离散率_data.get('home', 0))} | [待分析] |
| 平局 | {离散率_data.get('draw', 0):.2f}% | {离散率Analyzer.get离散度等级(离散率_data.get('draw', 0))} | [待分析] |
| {self.away_team}胜 | {离散率_data.get('away', 0):.2f}% | {离散率Analyzer.get离散度等级(离散率_data.get('away', 0))} | [待分析] |

## 四、冷门概率综合评估

### 4.1 冷门预警评分

| 评估维度 | 权重 | 冷门概率 | 评估依据 |
|---------|-----|---------|---------|
| 赔率异常度 | 20% | {cold_prob_result['各项概率'].get('赔率异常度', 0):.1f}% | 赔率变化分析 |
| 凯利指数异常 | 20% | {cold_prob_result['各项概率'].get('凯利指数异常', 0):.1f}% | 凯利指数分析 |
| 离散率异常 | 15% | {cold_prob_result['各项概率'].get('离散率异常', 0):.1f}% | 离散率分析 |
| 临场变化异常 | 15% | {cold_prob_result['各项概率'].get('临场变化异常', 0):.1f}% | 临场数据 |
| 基本面异常 | 15% | {cold_prob_result['各项概率'].get('基本面异常', 0):.1f}% | 基本面分析 |
| 盘口异常 | 15% | {cold_prob_result['各项概率'].get('盘口异常', 0):.1f}% | 盘口分析 |

**综合冷门概率**：**{cold_prob_result['综合冷门概率']:.1f}%**

### 4.2 冷门风险等级

| 风险等级 | 冷门概率 | 颜色标识 | 说明 |
|---------|---------|---------|-----|
| {risk_level} | {cold_prob_result['综合冷门概率']:.1f}% | {risk_desc} | 冷门可能性{'极大，需重点防范' if cold_prob_result['综合冷门概率'] > 50 else '存在，需谨慎' if cold_prob_result['综合冷门概率'] > 30 else '较小，可信任正路'} |

## 五、综合预测与建议

### 5.1 比赛结果预测

| 预测选项 | 概率 | 推荐指数 | 风险评估 |
|---------|-----|---------|---------|
| {self.home_team}胜 | {60 - cold_prob_result['综合冷门概率'] * 0.3:.1f}% | ⭐⭐⭐ | [稳健] |
| 平局 | {cold_prob_result['综合冷门概率'] * 0.5:.1f}% | ⭐⭐ | [谨慎] |
| {self.away_team}胜 | {30 + cold_prob_result['综合冷门概率'] * 0.2:.1f}% | ⭐⭐⭐ | [稳健] |

### 5.2 冷门推荐选项

| 冷门选项 | 冷门概率 | 推荐指数 | 投注价值 |
|---------|---------|---------|---------|
| 客胜冷门 | {cold_prob_result['综合冷门概率'] * 0.6:.1f}% | ⭐⭐⭐ | [价值评估] |
| 平局冷门 | {cold_prob_result['综合冷门概率'] * 0.4:.1f}% | ⭐⭐ | [价值评估] |

## 六、最终结论

### 🏆 综合判断
- 冷门概率：**{cold_prob_result['综合冷门概率']:.1f}%**
- 风险等级：{risk_level}
- 推荐方向：{self.home_team if 60 - cold_prob_result['综合冷门概率'] * 0.3 > 30 + cold_prob_result['综合冷门概率'] * 0.2 else self.away_team}胜

### ⚠️ 特别提示
{self.home_team} vs {self.away_team}的比赛需要重点关注冷门信号，建议结合临场数据做出最终判断。

---

**报告生成时间**：{self.analysis_time}
**分析数据来源**：基于赔率、凯利指数、离散率等多维度数据
**免责声明**：本报告仅供参考，不构成投注建议，购彩需理性
"""
        return report


def parse_team_name(name: str) -> str:
    """解析球队名称，生成文件名格式"""
    name = name.strip()
    name = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', name)
    return name


def generate_filename(home_team: str, away_team: str, match_date: str, league: str = 'bundesliga') -> str:
    """生成预测文件名：球队xxxx名称_预测.md"""
    home = parse_team_name(home_team)
    away = parse_team_name(away_team)
    date_str = match_date.replace('-', '')

    league_path = f'/Users/lin/trae_projects/europe_leagues/{league}/analysis/predictions/'
    filename = f'{home}_vs_{away}_预测_{date_str}.md'

    return league_path + filename


if __name__ == '__main__':
    print("=" * 60)
    print("冷门预警分析工具")
    print("=" * 60)
    print("\n使用方法：")
    print("1. 导入本模块")
    print("2. 创建Analyzer对象")
    print("3. 调用generate_report方法生成报告")
    print("\n示例：")
    print("```python")
    print("from upset_warning import 冷门预警Analyzer, generate_filename")
    print("")
    print("analyzer = 冷门预警Analyzer('拜仁慕尼黑', '多特蒙德', '德甲')")
    print("report = analyzer.generate_report(")
    print("    initial_odds={'home': 1.85, 'draw': 3.50, 'away': 4.20},")
    print("    final_odds={'home': 1.90, 'draw': 3.40, 'away': 4.00},")
    print("    kelly_data={'home': [0.89, 0.88, 0.90], 'draw': [0.95, 0.96, 0.94], 'away': [1.02, 1.03, 1.01]},")
    print("    离散率_data={'home': 1.5, 'draw': 2.3, 'away': 3.2},")
    print("    home_basic={'rank': 1, 'points': 71, 'record': '22胜5平3负'},")
    print("    away_basic={'rank': 3, 'points': 62, 'record': '18胜8平4负'},")
    print("    league_round='第30轮'")
    print(")")
    print("")
    print("filename = generate_filename('拜仁慕尼黑', '多特蒙德', '2026-04-18')")
    print("print(report)")
    print("```")
    print("\n### 从中国体育彩票官网、澳客官网获取实时数据：")
    print("```python")
    print("import asyncio")
    print("from upset_warning import 冷门预警Analyzer, generate_filename, DATA_COLLECTOR_AVAILABLE")
    print("")
    print("if DATA_COLLECTOR_AVAILABLE:")
    print("    from upset_warning import DataCollector")
    print("")
    print("    async def fetch_and_analyze():")
    print("        collector = DataCollector()")
    print("        ")
    print("        # 收集单场比赛数据")
    print("        match_data = await collector.collect_match_data(")
    print("            match_url='https://www.sporttery.cn/jc/zqszsc/match_id',")
    print("            league='bundesliga'")
    print("        )")
    print("        ")
    print("        if match_data:")
    print("            analyzer = 冷门预警Analyzer(")
    print("                match_data.home_team,")
    print("                match_data.away_team,")
    print("                '德甲'")
    print("            )")
    print("            ")
    print("            report = analyzer.generate_report(")
    print("                initial_odds=match_data.initial_odds,")
    print("                final_odds=match_data.final_odds,")
    print("                kelly_data=match_data.kelly_odds,")
    print("                离散率_data={'home': 1.5, 'draw': 2.3, 'away': 3.2},")
    print("                home_basic={'rank': 1, 'points': 71, 'record': '22胜5平3负'},")
    print("                away_basic={'rank': 3, 'points': 62, 'record': '18胜8平4负'},")
    print("                league_round='第30轮'")
    print("            )")
    print("            ")
    print("            filename = generate_filename(")
    print("                match_data.home_team,")
    print("                match_data.away_team,")
    print("                match_data.match_date,")
    print("                'bundesliga'")
    print("            )")
    print("            ")
    print("            with open(filename, 'w', encoding='utf-8') as f:")
    print("                f.write(report)")
    print("            print(f'报告已保存至: {filename}')")
    print("    ")
    print("    asyncio.run(fetch_and_analyze())")
    print("else:")
    print("    print('请先安装browser-use库: uv add browser-use')")
    print("```")
    print("\n" + "=" * 60)
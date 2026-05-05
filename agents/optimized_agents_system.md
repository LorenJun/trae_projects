# 足球预测多Agent系统优化方案

> 当前说明：本文是多 Agent 优化方案稿。当前仓库的正式执行链路已收敛为 `prediction_system.py`、`EnhancedPredictor`、`okooo_*`、`bulk_fetch_and_update.py` 和 `harness/football.py`。  
> 阅读本文时，请以 `teams_2025-26.md` 为单一事实来源，并以 `collect-data -> predict-match/predict-schedule -> save-result/accuracy` 为正式流程口径。

## 系统概览

本系统包含4个核心Agent，每个Agent负责特定的足球预测分析任务，通过协作完成完整的比赛预测分析流程。

## 当前正式入口映射

- 数据采集：`prediction_system.py collect-data --json`
- 单场预测：`prediction_system.py predict-match --json`
- 批量预测：`prediction_system.py predict-schedule --json`
- Harness 编排：`prediction_system.py harness-run --pipeline match_prediction --json`
- 单场回填：`prediction_system.py save-result --json`
- 批量回填：`bulk_fetch_and_update.py`
- 准确率统计：`prediction_system.py accuracy --refresh --json`

---

## Agent 1: 数据采集Agent（优化版）

### 优化内容
- ✅ 完善错误处理机制
- ✅ 多源数据验证
- ✅ 自动降级策略
- ✅ 实时监控与告警

### 错误处理机制

#### 错误分类体系

```python
# 错误代码规范
ERROR_CODES = {
    # 网络错误 (NETxxx)
    'NET001': {'name': '连接超时', 'retry': True, 'max_retries': 3},
    'NET002': {'name': '连接拒绝', 'retry': True, 'max_retries': 2},
    'NET003': {'name': 'DNS解析失败', 'retry': True, 'max_retries': 3},
    'NET004': {'name': 'SSL证书错误', 'retry': False, 'fallback': True},
    'NET005': {'name': '代理连接失败', 'retry': True, 'max_retries': 2},
    'NET006': {'name': '带宽不足', 'retry': True, 'max_retries': 2},

    # 数据解析错误 (PARxxx)
    'PAR001': {'name': '页面结构变化', 'retry': False, 'alert': True},
    'PAR002': {'name': '数据格式异常', 'retry': True, 'fallback_parser': True},
    'PAR003': {'name': '编码错误', 'retry': True, 'encoding_fallback': True},
    'PAR004': {'name': '缺失字段', 'retry': False, 'mark_optional': True},
    'PAR005': {'name': '数据类型错误', 'retry': False, 'transform': True},

    # 数据验证错误 (VALxxx)
    'VAL001': {'name': '赔率超出范围', 'retry': False, 'mark_anomaly': True},
    'VAL002': {'name': '凯利指数异常', 'retry': False, 'multisource_verify': True},
    'VAL003': {'name': '盘口数据矛盾', 'retry': False, 'use_consensus': True},
    'VAL004': {'name': '数据缺失', 'retry': False, 'mark_optional': True},
    'VAL005': {'name': '数据不一致', 'retry': True, 'verify_all_sources': True},

    # 业务逻辑错误 (BIZxxx)
    'BIZ001': {'name': '比赛已取消', 'retry': False},
    'BIZ002': {'name': '比赛已推迟', 'retry': False},
    'BIZ003': {'name': '数据过期', 'retry': True, 'refresh': True},
    'BIZ004': {'name': '无权限访问', 'retry': False, 'alert': True}
}
```

#### 错误处理策略

```python
class ErrorHandler:
    """错误处理器"""

    def __init__(self):
        self.error_handlers = {
            'NET': self.handle_network_error,
            'PAR': self.handle_parse_error,
            'VAL': self.handle_validation_error,
            'BIZ': self.handle_business_error
        }
        self.fallback_strategies = {
            'primary': self.get_primary_data,
            'secondary': self.get_secondary_data,
            'cache': self.get_cached_data,
            'default': self.get_default_data
        }

    def handle_error(self, error_code, context):
        """统一错误处理入口"""
        error_category = error_code[:3]
        handler = self.error_handlers.get(error_category, self.handle_unknown_error)

        return handler(error_code, context)

    def handle_network_error(self, error_code, context):
        """网络错误处理"""
        config = ERROR_CODES.get(error_code, {})

        if config.get('retry') and context.get('retry_count', 0) < config.get('max_retries', 3):
            # 执行重试
            wait_time = 2 ** context.get('retry_count', 0)
            time.sleep(min(wait_time, 30))  # 最大等待30秒
            return {'action': 'RETRY', 'wait_time': wait_time}

        if config.get('fallback'):
            # 执行降级
            return {'action': 'FALLBACK', 'strategy': 'secondary'}

        return {'action': 'SKIP', 'reason': '不可恢复的网络错误'}

    def handle_parse_error(self, error_code, context):
        """解析错误处理"""
        config = ERROR_CODES.get(error_code, {})

        if config.get('alert'):
            # 发送告警通知管理员
            send_critical_alert({
                'type': 'PARSER_UPDATE_REQUIRED',
                'error_code': error_code,
                'context': context
            })

        if config.get('fallback_parser'):
            return {'action': 'USE_BACKUP_PARSER'}

        if config.get('encoding_fallback'):
            return {'action': 'TRY_ENCODING', 'encodings': ['utf-8', 'gbk', 'gb2312']}

        return {'action': 'SKIP', 'reason': '解析错误'}

    def handle_validation_error(self, error_code, context):
        """验证错误处理"""
        config = ERROR_CODES.get(error_code, {})

        if config.get('multisource_verify'):
            # 多源验证
            all_sources = get_all_source_data(context['data_type'])
            return {'action': 'VERIFY_MULTISOURCE', 'data': all_sources}

        if config.get('use_consensus'):
            # 使用共识数据
            return {'action': 'USE_CONSENSUS'}

        if config.get('mark_anomaly'):
            # 标记为异常数据
            return {'action': 'MARK_ANOMALY', 'data': context['data']}

        return {'action': 'SKIP', 'reason': '验证错误'}

    def handle_business_error(self, error_code, context):
        """业务错误处理"""
        if error_code in ['BIZ001', 'BIZ002']:
            # 比赛取消或推迟
            update_match_status(error_code, context['match_id'])
            return {'action': 'SKIP', 'reason': '比赛已取消/推迟'}

        if error_code == 'BIZ003':
            # 数据过期，刷新
            return {'action': 'REFRESH'}

        return {'action': 'SKIP', 'reason': '业务错误'}
```

#### 重试策略

```python
class RetryStrategy:
    """重试策略"""

    def __init__(self):
        self.config = {
            'max_retries': 3,
            'initial_wait': 1,
            'max_wait': 30,
            'backoff_factor': 2,
            'jitter': True
        }

    def should_retry(self, error_code, attempt):
        """判断是否应该重试"""
        error_config = ERROR_CODES.get(error_code, {})
        return (
            error_config.get('retry', False) and
            attempt < error_config.get('max_retries', self.config['max_retries'])
        )

    def calculate_wait_time(self, attempt):
        """计算等待时间"""
        wait = self.config['initial_wait'] * (self.config['backoff_factor'] ** attempt)
        wait = min(wait, self.config['max_wait'])

        if self.config['jitter']:
            wait *= (0.5 + random.random())  # 添加随机抖动

        return wait

    def execute_with_retry(self, func, *args, **kwargs):
        """带重试执行"""
        attempt = 0
        last_error = None

        while attempt < self.config['max_retries']:
            try:
                return func(*args, **kwargs)

            except Exception as e:
                error_code = extract_error_code(e)
                last_error = e

                if not self.should_retry(error_code, attempt):
                    raise

                wait_time = self.calculate_wait_time(attempt)
                log(f"重试 {attempt + 1}/{self.config['max_retries']}，等待 {wait_time:.1f}秒")
                time.sleep(wait_time)
                attempt += 1

        raise last_error
```

#### 降级策略

```python
class DegradationManager:
    """降级管理器"""

    def __init__(self):
        self.degradation_levels = {
            'FULL': {
                'description': '完全降级',
                'sources': ['primary', 'secondary', 'tertiary', 'cache'],
                'timeout': 5
            },
            'PARTIAL': {
                'description': '部分降级',
                'sources': ['primary', 'secondary', 'cache'],
                'timeout': 10
            },
            'MINIMAL': {
                'description': '最小降级',
                'sources': ['primary', 'cache'],
                'timeout': 15
            },
            'EMERGENCY': {
                'description': '紧急降级',
                'sources': ['cache', 'default'],
                'timeout': 30
            }
        }
        self.current_level = 'FULL'
        self.success_rates = {}

    def execute_with_degradation(self, data_type, match_id):
        """降级执行"""
        level_config = self.degradation_levels[self.current_level]

        for source in level_config['sources']:
            try:
                with timeout(level_config['timeout']):
                    data = self.get_data_from_source(source, data_type, match_id)

                    if self.validate_data(data):
                        self.update_success_rate(source, True)
                        return data

            except Exception as e:
                self.update_success_rate(source, False)
                continue

        # 所有源都失败
        return self.handle_all_sources_failed(data_type, match_id)

    def adjust_degradation_level(self):
        """动态调整降级级别"""
        for source, rate in self.success_rates.items():
            if rate < 0.5:
                self.current_level = 'EMERGENCY'
                break
            elif rate < 0.7:
                self.current_level = 'MINIMAL'
                break
            elif rate < 0.9:
                self.current_level = 'PARTIAL'
            else:
                self.current_level = 'FULL'
```

#### 监控指标

```python
# 监控指标定义
MONITORING_METRICS = {
    # 性能指标
    'response_time': {'threshold': 3000, 'unit': 'ms', 'alert': 'WARNING'},
    'error_rate': {'threshold': 0.05, 'unit': 'ratio', 'alert': 'WARNING'},
    'error_rate_critical': {'threshold': 0.10, 'unit': 'ratio', 'alert': 'ERROR'},

    # 数据质量指标
    'data_completeness': {'threshold': 0.90, 'unit': 'ratio', 'alert': 'WARNING'},
    'data_accuracy': {'threshold': 0.95, 'unit': 'ratio', 'alert': 'WARNING'},

    # 可用性指标
    'availability': {'threshold': 0.99, 'unit': 'ratio', 'alert': 'WARNING'},
    'retry_rate': {'threshold': 0.20, 'unit': 'ratio', 'alert': 'INFO'}
}

# 实时监控
class DataCollectionMonitor:
    """数据采集监控"""

    def __init__(self):
        self.metrics = defaultdict(list)
        self.alert_thresholds = MONITORING_METRICS

    def record_metric(self, metric_name, value):
        """记录指标"""
        self.metrics[metric_name].append({
            'value': value,
            'timestamp': datetime.now().timestamp()
        })

    def check_alerts(self):
        """检查告警"""
        alerts = []

        for metric_name, config in self.alert_thresholds.items():
            recent_values = [m['value'] for m in self.metrics[metric_name][-10:]]

            if not recent_values:
                continue

            avg_value = sum(recent_values) / len(recent_values)

            if metric_name == 'error_rate_critical' and avg_value > config['threshold']:
                alerts.append({
                    'level': 'ERROR',
                    'metric': metric_name,
                    'value': avg_value,
                    'threshold': config['threshold']
                })
            elif avg_value > config['threshold']:
                alerts.append({
                    'level': config['alert'],
                    'metric': metric_name,
                    'value': avg_value,
                    'threshold': config['threshold']
                })

        return alerts
```

---

## Agent 2: 赔率分析Agent（优化版）

### 优化内容
- ✅ 优化凯利指数算法
- ✅ 增加多维度分析
- ✅ 智能赔率异常检测
- ✅ 实时赔率变化追踪

### 优化的凯利指数算法

```python
class OptimizedKellyAnalyzer:
    """优化后的凯利指数分析器"""

    def __init__(self):
        self.kelly_weights = {
            ' pinnacle': 0.20,
            'bet365': 0.15,
            'williamhill': 0.12,
            'interwetten': 0.10,
            'bwin': 0.08,
            'unibet': 0.08,
            'other': 0.27
        }

        self.risk_thresholds = {
            'low_risk': 0.90,
            'normal': 1.00,
            'high_risk': 1.05
        }

    def calculate_weighted_kelly(self, odds_data):
        """计算加权凯利指数"""
        kelly_values = {}

        for company, odds in odds_data.items():
            implied_prob = 1 / odds if odds > 0 else 0
            market_avg_prob = self.estimate_market_prob(odds, company)
            kelly = odds * market_avg_prob

            weight = self.kelly_weights.get(company.lower(), 0.05)
            kelly_values[company] = {
                'kelly': kelly,
                'weight': weight,
                'odds': odds,
                'implied_prob': implied_prob
            }

        # 加权平均凯利
        weighted_kelly = sum(
            v['kelly'] * v['weight']
            for v in kelly_values.values()
        )

        return {
            'weighted_kelly': weighted_kelly,
            'company_values': kelly_values,
            'variance': self.calculate_kelly_variance(kelly_values),
            'std_dev': self.calculate_kelly_std_dev(kelly_values)
        }

    def analyze_kelly_patterns(self, kelly_data):
        """分析凯利模式"""
        weighted_kelly = kelly_data['weighted_kelly']
        variance = kelly_data['variance']

        # 风险评估
        if weighted_kelly < self.risk_thresholds['low_risk']:
            risk_level = 'LOW'
            signal_strength = 'STRONG'
            interpretation = '机构高度警惕，热门方向稳定'
        elif weighted_kelly < self.risk_thresholds['normal']:
            risk_level = 'NORMAL'
            signal_strength = 'MODERATE'
            interpretation = '无明显倾向，常规分布'
        elif weighted_kelly < self.risk_thresholds['high_risk']:
            risk_level = 'HIGH'
            signal_strength = 'WEAK'
            interpretation = '机构不惧赔付，谨慎对待'
        else:
            risk_level = 'CRITICAL'
            signal_strength = 'VERY_WEAK'
            interpretation = '高风险区域，可能为冷门方向'

        # 方差分析
        if variance < 0.0001:
            variance_signal = '机构高度一致'
        elif variance < 0.001:
            variance_signal = '有一定分歧，但方向明确'
        elif variance < 0.01:
            variance_signal = '分歧较大，需谨慎'
        else:
            variance_signal = '机构分歧严重，冷门可能'

        return {
            'risk_level': risk_level,
            'signal_strength': signal_strength,
            'interpretation': interpretation,
            'variance_signal': variance_signal,
            'recommendation': self.generate_recommendation(risk_level, variance)
        }

    def calculate_kelly_variance(self, kelly_values):
        """计算凯利方差"""
        kelly_list = [v['kelly'] for v in kelly_values.values()]
        mean = sum(kelly_list) / len(kelly_list)
        variance = sum((k - mean) ** 2 for k in kelly_list) / len(kelly_list)
        return variance

    def calculate_kelly_std_dev(self, kelly_values):
        """计算凯利标准差"""
        variance = self.calculate_kelly_variance(kelly_values)
        return variance ** 0.5

    def generate_recommendation(self, risk_level, variance):
        """生成推荐"""
        if risk_level == 'LOW' and variance < 0.001:
            return {
                'action': 'CONFIDENT_BET',
                'confidence': 'HIGH',
                'stake_recommendation': '正常投注'
            }
        elif risk_level == 'NORMAL' and variance < 0.001:
            return {
                'action': 'STANDARD_BET',
                'confidence': 'MEDIUM',
                'stake_recommendation': '轻仓试探'
            }
        elif variance > 0.01:
            return {
                'action': 'AVOID_BET',
                'confidence': 'LOW',
                'stake_recommendation': '建议观望'
            }
        else:
            return {
                'action': 'CAREFUL_BET',
                'confidence': 'MEDIUM',
                'stake_recommendation': '谨慎投注'
            }
```

### 赔率变化追踪

```python
class OddsChangeTracker:
    """赔率变化追踪器"""

    def __init__(self):
        self.history = defaultdict(list)
        self.change_thresholds = {
            'significant': 0.05,  # 5%以上为显著变化
            'major': 0.10,       # 10%以上为重大变化
            'critical': 0.20      # 20%以上为剧烈变化
        }

    def track_odds_change(self, match_id, company, odds_data):
        """追踪赔率变化"""
        timestamp = datetime.now().timestamp()

        record = {
            'timestamp': timestamp,
            'company': company,
            'home_win': odds_data.get('home_win'),
            'draw': odds_data.get('draw'),
            'away_win': odds_data.get('away_win')
        }

        self.history[match_id].append(record)

        # 分析变化趋势
        if len(self.history[match_id]) >= 2:
            return self.analyze_trend(match_id)

        return None

    def analyze_trend(self, match_id):
        """分析变化趋势"""
        records = self.history[match_id]

        if len(records) < 2:
            return None

        latest = records[-1]
        initial = records[0]

        # 计算变化幅度
        changes = {}
        for outcome in ['home_win', 'draw', 'away_win']:
            if initial.get(outcome) and latest.get(outcome):
                change_pct = (latest[outcome] - initial[outcome]) / initial[outcome]
                changes[outcome] = {
                    'initial': initial[outcome],
                    'current': latest[outcome],
                    'change_pct': change_pct,
                    'change_direction': 'up' if change_pct > 0 else 'down'
                }

        # 识别显著变化
        significant_changes = {
            k: v for k, v in changes.items()
            if abs(v['change_pct']) > self.change_thresholds['significant']
        }

        # 生成趋势分析
        return {
            'match_id': match_id,
            'changes': changes,
            'significant_changes': significant_changes,
            'trend_summary': self.summarize_trend(changes),
            'recommendation': self.generate_trend_recommendation(significant_changes)
        }

    def summarize_trend(self, changes):
        """总结趋势"""
        directions = [c['change_direction'] for c in changes.values()]

        if all(d == 'down' for d in directions):
            return '赔率全面下调，主队被市场看好'
        elif all(d == 'up' for d in directions):
            return '赔率全面上调，主队被市场看衰'
        else:
            home_change = changes.get('home_win', {}).get('change_pct', 0)
            if home_change < -0.05:
                return '主队赔率下调，市场倾向主队'
            elif home_change > 0.05:
                return '主队赔率上调，可能存在诱主'
            else:
                return '赔率变化平稳，无明显倾向'
```

### 凯利-赔率组合分析

```python
class KellyOddsCombinationAnalyzer:
    """凯利-赔率组合分析器"""

    def __init__(self):
        self.combination_rules = self.load_combination_rules()

    def analyze_combination(self, odds, kelly):
        """分析赔率与凯利组合"""
        recommendations = []

        for outcome in ['home_win', 'draw', 'away_win']:
            odds_val = odds.get(outcome)
            kelly_val = kelly.get(outcome)

            if not odds_val or not kelly_val:
                continue

            # 计算隐含概率
            implied_prob = 1 / odds_val

            # 组合分析
            if kelly_val < 0.90 and odds_val < 2.5:
                # 低赔 + 低凯利 = 强强联合
                recommendations.append({
                    'outcome': outcome,
                    'pattern': 'LOW_ODDS_LOW_KELLY',
                    'signal': 'STRONG',
                    'confidence': 0.85,
                    'interpretation': '强强联合，方向稳定',
                    'action': 'CONFIDENT_BET'
                })

            elif kelly_val > 1.05 and odds_val < 2.0:
                # 低赔 + 高凯利 = 矛盾信号
                recommendations.append({
                    'outcome': outcome,
                    'pattern': 'LOW_ODDS_HIGH_KELLY',
                    'signal': 'WEAK',
                    'confidence': 0.40,
                    'interpretation': '矛盾信号，可能存在诱上',
                    'action': 'AVOID'
                })

            elif kelly_val < 0.90 and odds_val > 3.5:
                # 高赔 + 低凯利 = 冷门保护
                recommendations.append({
                    'outcome': outcome,
                    'pattern': 'HIGH_ODDS_LOW_KELLY',
                    'signal': 'MODERATE',
                    'confidence': 0.65,
                    'interpretation': '冷门保护，机构防范',
                    'action': 'VALUE_BET'
                })

            elif kelly_val > 1.10 and odds_val > 4.0:
                # 高赔 + 高凯利 = 冷门确认
                recommendations.append({
                    'outcome': outcome,
                    'pattern': 'HIGH_ODDS_HIGH_KELLY',
                    'signal': 'STRONG',
                    'confidence': 0.75,
                    'interpretation': '冷门确认，可以博冷',
                    'action': 'UPSET_BET'
                })

        return recommendations
```

---

## Agent 3: 结果追踪Agent（优化版）

### 优化内容
- ✅ 实现可视化报告
- ✅ 实时数据更新
- ✅ 历史趋势分析
- ✅ 性能仪表盘

### 可视化报告系统

```python
class VisualizationReporter:
    """可视化报告生成器"""

    def __init__(self):
        self.report_templates = {
            'daily': self.generate_daily_report,
            'weekly': self.generate_weekly_report,
            'monthly': self.generate_monthly_report,
            'match': self.generate_match_report
        }

    def generate_match_report(self, match_id, prediction, result):
        """生成单场比赛可视化报告"""
        # 创建雷达图数据
        radar_data = {
            'categories': ['预测准确度', '赔率分析准确度', '状态分析准确度', '战术分析准确度'],
            'values': [
                1.0 if prediction['result'] == result['actual_result'] else 0.0,
                self.calculate_odds_accuracy(prediction, result),
                self.calculate_form_accuracy(prediction, result),
                self.calculate_tactics_accuracy(prediction, result)
            ]
        }

        # 创建时间线图数据
        timeline_data = {
            'events': [
                {'time': '比赛前24h', 'action': '发布预测', 'data': prediction},
                {'time': '比赛前1h', 'action': '更新赔率', 'data': 'latest_odds'},
                {'time': '比赛开始', 'action': '比赛开始', 'data': None},
                {'time': '比赛结束', 'action': '比赛结束', 'data': result}
            ]
        }

        # 创建结果对比图
        comparison_data = {
            'prediction': {
                'result': prediction['predicted_result'],
                'score': prediction['predicted_score'],
                'probabilities': prediction['probabilities']
            },
            'actual': {
                'result': result['actual_result'],
                'score': result['actual_score']
            }
        }

        return {
            'report_type': 'match',
            'match_id': match_id,
            'radar_chart': radar_data,
            'timeline': timeline_data,
            'comparison': comparison_data,
            'summary': self.generate_match_summary(prediction, result)
        }

    def generate_daily_report(self, date):
        """生成每日可视化报告"""
        predictions = self.get_daily_predictions(date)
        results = self.get_daily_results(date)

        # 准确率统计
        accuracy_stats = self.calculate_accuracy_stats(predictions, results)

        # 时间分布图
        time_distribution = self.calculate_time_distribution(predictions)

        # 联赛分布图
        league_distribution = self.calculate_league_distribution(predictions)

        # 赔率分析准确度
        odds_accuracy = self.calculate_odds_accuracy_by_league(predictions, results)

        return {
            'report_type': 'daily',
            'date': date,
            'accuracy_stats': accuracy_stats,
            'time_distribution': time_distribution,
            'league_distribution': league_distribution,
            'odds_accuracy': odds_accuracy,
            'charts': {
                'accuracy_gauge': self.create_accuracy_gauge(accuracy_stats['overall']),
                'distribution_pie': self.create_pie_chart(league_distribution),
                'timeline_bar': self.create_bar_chart(time_distribution)
            }
        }

    def generate_weekly_report(self, start_date, end_date):
        """生成周报"""
        daily_reports = []
        current_date = start_date

        while current_date <= end_date:
            daily_reports.append(self.generate_daily_report(current_date))
            current_date += timedelta(days=1)

        # 周趋势分析
        trend_analysis = self.analyze_weekly_trend(daily_reports)

        # 最佳/最差预测
        best_predictions = self.get_best_predictions(daily_reports)
        worst_predictions = self.get_worst_predictions(daily_reports)

        return {
            'report_type': 'weekly',
            'period': f'{start_date} to {end_date}',
            'daily_reports': daily_reports,
            'trend_analysis': trend_analysis,
            'best_predictions': best_predictions,
            'worst_predictions': worst_predictions,
            'recommendations': self.generate_weekly_recommendations(trend_analysis)
        }
```

### 实时性能仪表盘

```python
class PerformanceDashboard:
    """性能仪表盘"""

    def __init__(self):
        self.widgets = {
            'accuracy_gauge': AccuracyGaugeWidget(),
            'trend_chart': TrendChartWidget(),
            'recent_predictions': RecentPredictionsWidget(),
            'model_performance': ModelPerformanceWidget(),
            'alerts': AlertsWidget()
        }

    def render_dashboard(self):
        """渲染仪表盘"""
        dashboard_data = {
            'title': '足球预测分析仪表盘',
            'last_updated': datetime.now().isoformat(),
            'widgets': {}
        }

        for widget_name, widget in self.widgets.items():
            dashboard_data['widgets'][widget_name] = widget.render()

        return dashboard_data

    def create_real_time_updates(self):
        """创建实时更新"""
        return {
            'accuracy': self.get_current_accuracy(),
            'predictions_today': self.get_today_prediction_count(),
            'pending_results': self.get_pending_result_count(),
            'active_matches': self.get_active_match_count(),
            'recent_alerts': self.get_recent_alerts()
        }


class AccuracyGaugeWidget:
    """准确率仪表盘组件"""

    def render(self):
        """渲染准确率仪表盘"""
        current_accuracy = self.calculate_current_accuracy()

        return {
            'type': 'gauge',
            'title': '预测准确率',
            'value': current_accuracy,
            'min': 0,
            'max': 100,
            'thresholds': {
                'green': {'min': 70, 'max': 100},
                'yellow': {'min': 50, 'max': 70},
                'red': {'min': 0, 'max': 50}
            },
            'unit': '%'
        }


class TrendChartWidget:
    """趋势图组件"""

    def render(self):
        """渲染趋势图"""
        historical_data = self.get_historical_accuracy(days=30)

        return {
            'type': 'line_chart',
            'title': '准确率趋势（近30天）',
            'data': historical_data,
            'x_axis': 'date',
            'y_axis': 'accuracy',
            'annotations': self.get_trend_annotations(historical_data)
        }
```

### 数据追踪规范

```python
class ResultTracker:
    """结果追踪器"""

    def __init__(self):
        self.tracking_rules = {
            'prediction': {
                'required_fields': [
                    'prediction_id',
                    'match_id',
                    'prediction_date',
                    'match_date',
                    'home_team',
                    'away_team',
                    'league',
                    'predicted_result',
                    'predicted_score',
                    'confidence',
                    'analysis_link'
                ],
                'optional_fields': [
                    'odds_used',
                    'kelly_values',
                    'model_outputs'
                ]
            },
            'result': {
                'required_fields': [
                    'prediction_id',
                    'match_id',
                    'actual_result',
                    'actual_score',
                    'home_goals',
                    'away_goals',
                    'total_goals',
                    'match_status'
                ]
            }
        }

    def record_prediction(self, prediction_data):
        """记录预测"""
        # 验证必填字段
        self.validate_fields(prediction_data, self.tracking_rules['prediction'])

        # 生成唯一ID
        prediction_id = self.generate_prediction_id(prediction_data)

        # 保存预测
        record = {
            'prediction_id': prediction_id,
            **prediction_data,
            'recorded_at': datetime.now().isoformat()
        }

        self.save_to_database('predictions', record)

        return prediction_id

    def record_result(self, result_data):
        """记录结果"""
        # 验证必填字段
        self.validate_fields(result_data, self.tracking_rules['result'])

        # 关联预测ID
        result_data['prediction_id'] = self.find_prediction_id(result_data)

        # 保存结果
        record = {
            **result_data,
            'recorded_at': datetime.now().isoformat()
        }

        self.save_to_database('results', record)

        # 更新预测准确度
        self.update_prediction_accuracy(result_data['prediction_id'])

        return result_data

    def calculate_accuracy(self, prediction_id):
        """计算准确度"""
        prediction = self.get_prediction(prediction_id)
        result = self.get_result(prediction_id)

        if not result:
            return None

        # 主预测准确度
        main_accuracy = 1.0 if prediction['predicted_result'] == result['actual_result'] else 0.0

        # 比分准确度
        score_accuracy = 1.0 if prediction['predicted_score'] == result['actual_score'] else 0.0

        # 进球数准确度
        predicted_total = prediction['predicted_score'].split('-')[0] + prediction['predicted_score'].split('-')[1]
        actual_total = result['total_goals']
        goals_accuracy = 1.0 if abs(int(predicted_total) - actual_total) <= 1 else 0.0

        return {
            'main_accuracy': main_accuracy,
            'score_accuracy': score_accuracy,
            'goals_accuracy': goals_accuracy,
            'overall': (main_accuracy * 0.5 + score_accuracy * 0.3 + goals_accuracy * 0.2)
        }
```

---

## Agent 4: 预测模型评估Agent（优化版）

### 优化内容
- ✅ 建立自动评估系统
- ✅ 实时性能监控
- ✅ 模型对比分析
- ✅ 动态参数调优

### 自动评估系统

```python
class AutoEvaluationSystem:
    """自动评估系统"""

    def __init__(self):
        self.evaluation_metrics = {
            'accuracy': self.evaluate_accuracy,
            'precision': self.evaluate_precision,
            'recall': self.evaluate_recall,
            'f1_score': self.evaluate_f1_score,
            'roc_auc': self.evaluate_roc_auc,
            'calibration': self.evaluate_calibration
        }

        self.model_registry = {}
        self.performance_history = defaultdict(list)

    def evaluate_model(self, model_name, test_data):
        """评估模型"""
        predictions = self.model_registry[model_name].predict(test_data)
        actuals = test_data['actual_results']

        results = {}

        for metric_name, metric_func in self.evaluation_metrics.items():
            try:
                results[metric_name] = metric_func(predictions, actuals)
            except Exception as e:
                results[metric_name] = None
                log_error(f"评估{model_name}的{metric_name}失败", str(e))

        # 记录评估历史
        self.performance_history[model_name].append({
            'timestamp': datetime.now().timestamp(),
            'metrics': results,
            'sample_size': len(test_data)
        })

        # 检查是否需要告警
        self.check_performance_alerts(model_name, results)

        return results

    def evaluate_accuracy(self, predictions, actuals):
        """评估准确率"""
        correct = sum(1 for p, a in zip(predictions, actuals) if p == a)
        return correct / len(predictions) if predictions else 0

    def evaluate_precision(self, predictions, actuals):
        """评估精确率"""
        from sklearn.metrics import precision_score
        return precision_score(actuals, predictions, average='weighted', zero_division=0)

    def evaluate_recall(self, predictions, actuals):
        """评估召回率"""
        from sklearn.metrics import recall_score
        return recall_score(actuals, predictions, average='weighted', zero_division=0)

    def evaluate_f1_score(self, predictions, actuals):
        """评估F1分数"""
        from sklearn.metrics import f1_score
        return f1_score(actuals, predictions, average='weighted', zero_division=0)

    def evaluate_roc_auc(self, predictions, actuals):
        """评估ROC AUC"""
        from sklearn.metrics import roc_auc_score
        try:
            return roc_auc_score(actuals, predictions)
        except:
            return None

    def evaluate_calibration(self, predictions, actuals):
        """评估校准度"""
        # 计算预测概率与实际概率的差异
        calibration_errors = []

        for pred_prob, actual in zip(predictions, actuals):
            # pred_prob 是概率，actual 是实际结果
            if actual == 1:
                error = 1 - pred_prob
            else:
                error = pred_prob
            calibration_errors.append(error)

        return 1 - (sum(calibration_errors) / len(calibration_errors))

    def compare_models(self):
        """模型对比分析"""
        model_comparisons = {}

        model_names = list(self.performance_history.keys())

        for i, model1 in enumerate(model_names):
            for model2 in model_names[i+1:]:
                comparison = self.compare_two_models(model1, model2)
                model_comparisons[f'{model1}_vs_{model2}'] = comparison

        return model_comparisons

    def compare_two_models(self, model1_name, model2_name):
        """对比两个模型"""
        metrics1 = self.get_latest_metrics(model1_name)
        metrics2 = self.get_latest_metrics(model2_name)

        comparison = {}

        for metric in ['accuracy', 'precision', 'recall', 'f1_score']:
            val1 = metrics1.get(metric, 0)
            val2 = metrics2.get(metric, 0)

            if val1 and val2:
                comparison[metric] = {
                    'model1': val1,
                    'model2': val2,
                    'difference': val1 - val2,
                    'winner': model1_name if val1 > val2 else model2_name
                }

        # 综合评分
        comparison['overall_winner'] = self.determine_overall_winner(comparison)

        return comparison

    def determine_overall_winner(self, comparison):
        """确定综合获胜者"""
        wins = {comparison['overall_winner']: 0}

        for metric, data in comparison.items():
            if metric == 'overall_winner':
                continue
            winner = data['winner']
            wins[winner] = wins.get(winner, 0) + 1

        return max(wins, key=wins.get)
```

### 实时性能监控

```python
class RealTimePerformanceMonitor:
    """实时性能监控"""

    def __init__(self):
        self.monitoring_config = {
            'check_interval': 60,  # 每60秒检查一次
            'window_size': 100,    # 窗口大小
            'alert_thresholds': {
                'accuracy_drop': 0.05,
                'latency_increase': 0.5,
                'error_rate_increase': 0.02
            }
        }

        self.current_metrics = {}
        self.alert_history = []

    def start_monitoring(self):
        """启动监控"""
        while True:
            try:
                self.check_performance()
                time.sleep(self.monitoring_config['check_interval'])
            except Exception as e:
                log_error("监控检查失败", str(e))

    def check_performance(self):
        """检查性能"""
        # 获取最新指标
        latest_metrics = self.collect_latest_metrics()

        # 对比历史数据
        if self.current_metrics:
            alerts = self.detect_performance_issues(latest_metrics)

            if alerts:
                self.handle_alerts(alerts)

        # 更新当前指标
        self.current_metrics = latest_metrics

    def detect_performance_issues(self, latest):
        """检测性能问题"""
        alerts = []

        if not self.current_metrics:
            return alerts

        for metric_name in ['accuracy', 'latency', 'error_rate']:
            if metric_name in latest and metric_name in self.current_metrics:
                current_val = latest[metric_name]
                previous_val = self.current_metrics[metric_name]

                threshold_key = f'{metric_name}_drop' if 'accuracy' in metric_name else f'{metric_name}_increase'
                threshold = self.monitoring_config['alert_thresholds'].get(threshold_key, 0)

                if abs(current_val - previous_val) > threshold:
                    alerts.append({
                        'metric': metric_name,
                        'current': current_val,
                        'previous': previous_val,
                        'change': current_val - previous_val,
                        'threshold': threshold,
                        'severity': 'HIGH' if abs(current_val - previous_val) > threshold * 2 else 'MEDIUM'
                    })

        return alerts

    def handle_alerts(self, alerts):
        """处理告警"""
        for alert in alerts:
            # 记录告警
            self.alert_history.append({
                **alert,
                'timestamp': datetime.now().timestamp()
            })

            # 发送通知
            self.send_alert_notification(alert)

            # 自动调整
            if alert['severity'] == 'HIGH':
                self.trigger_auto_adjustment(alert)
```

### 动态参数调优

```python
class DynamicParameterTuner:
    """动态参数调优器"""

    def __init__(self):
        self.tuning_strategies = {
            'grid_search': self.grid_search_tuning,
            'random_search': self.random_search_tuning,
            'bayesian_optimization': self.bayesian_tuning
        }

        self.parameter_bounds = {
            'poisson_weight': (0.05, 0.25),
            'elo_weight': (0.05, 0.25),
            'ml_weight': (0.05, 0.25),
            'xg_weight': (0.05, 0.20),
            'bayesian_weight': (0.02, 0.15),
            'home_advantage': (0.05, 0.20),
            'fatigue_factor': (0.90, 1.10)
        }

    def auto_tune_parameters(self, model_name, historical_data):
        """自动调优参数"""
        # 选择调优策略
        strategy = self.tuning_strategies['bayesian_optimization']

        # 执行调优
        best_params = strategy(historical_data)

        # 应用新参数
        self.apply_parameters(model_name, best_params)

        # 记录调优历史
        self.record_tuning_history(model_name, best_params)

        return best_params

    def bayesian_tuning(self, historical_data):
        """贝叶斯优化调参"""
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, RBF

        # 准备参数空间
        param_names = list(self.parameter_bounds.keys())
        param_ranges = [self.parameter_bounds[name] for name in param_names]

        # 使用高斯过程进行贝叶斯优化
        kernel = ConstantKernel(1.0) * RBF(length_scale=1.0)
        gp = GaussianProcessRegressor(kernel=kernel, n_restarts_optimizer=5)

        best_score = 0
        best_params = {}

        # 迭代优化
        for iteration in range(50):
            # 采样新参数
            params = self.sample_parameters(param_ranges)

            # 评估参数
            score = self.evaluate_parameters(params, historical_data)

            # 更新高斯过程
            X = [[p for p in params.values()]]
            y = [score]
            gp.fit(X, y)

            # 更新最佳参数
            if score > best_score:
                best_score = score
                best_params = params

        return best_params

    def evaluate_parameters(self, params, historical_data):
        """评估参数组合"""
        # 模拟使用参数进行预测
        predictions = self.simulate_predictions(params, historical_data)

        # 计算准确率
        accuracy = self.calculate_accuracy(predictions, historical_data['actual_results'])

        return accuracy

    def simulate_predictions(self, params, data):
        """模拟预测"""
        # 使用给定参数模拟预测过程
        predictions = []

        for record in data['features']:
            # 加权融合各模型输出
            fused_prob = (
                record['poisson_prob'] * params['poisson_weight'] +
                record['elo_prob'] * params['elo_weight'] +
                record['ml_prob'] * params['ml_weight'] +
                record['xg_prob'] * params['xg_weight'] +
                record['bayesian_prob'] * params['bayesian_weight']
            )

            # 考虑主场优势
            home_advantage = params['home_advantage']
            fused_prob *= (1 + home_advantage if record['is_home'] else 1)

            # 预测结果
            pred = 'home_win' if fused_prob > 0.5 else ('away_win' if fused_prob < 0.5 else 'draw')
            predictions.append(pred)

        return predictions
```

---

## 系统集成

### Agent协作流程

```
┌─────────────────────────────────────────────────────────────────┐
│                        数据采集Agent                              │
│  - 多源数据采集                                                  │
│  - 错误自动处理                                                  │
│  - 数据验证                                                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        赔率分析Agent                              │
│  - 凯利指数计算                                                  │
│  - 赔率变化追踪                                                  │
│  - 组合分析                                                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        比赛分析Agent                              │
│  - 球队基本面                                                    │
│  - 战术分析                                                      │
│  - 历史交锋                                                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                        预测模型Agent                              │
│  - 多模型融合                                                    │
│  - 自动评估                                                      │
│  - 动态调优                                                      │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                       结果追踪Agent                               │
│  - 结果记录                                                      │
│  - 可视化报告                                                    │
│  - 性能监控                                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 统一配置管理

```python
# 统一配置
UNIFIED_CONFIG = {
    'data_collector': {
        'max_retries': 3,
        'timeout': 30,
        'degradation_strategy': 'PARTIAL'
    },

    'odds_analyzer': {
        'kelly_weights': {
            'pinnacle': 0.20,
            'bet365': 0.15,
            'williamhill': 0.12
        },
        'risk_thresholds': {
            'low_risk': 0.90,
            'normal': 1.00,
            'high_risk': 1.05
        }
    },

    'match_analyzer': {
        'feature_weights': {
            'form': 0.25,
            'tactics': 0.20,
            'head_to_head': 0.15,
            'injuries': 0.15,
            'motivation': 0.15,
            'home_advantage': 0.10
        }
    },

    'prediction_model': {
        'fusion_weights': {
            'statistical': 0.25,
            'rating': 0.25,
            'machine_learning': 0.22,
            'special': 0.23
        }
    },

    'result_tracker': {
        'accuracy_thresholds': {
            'excellent': 0.75,
            'good': 0.65,
            'fair': 0.55,
            'poor': 0.45
        }
    }
}
```

---

## 实施计划

| 阶段 | 任务 | 时间 | 优先级 |
|------|------|------|--------|
| 1 | 数据采集Agent错误处理完善 | 1周 | HIGH |
| 2 | 赔率分析Agent凯利算法优化 | 1周 | HIGH |
| 3 | 结果追踪Agent可视化报告 | 1周 | MEDIUM |
| 4 | 预测模型评估系统建立 | 2周 | HIGH |
| 5 | 系统集成测试 | 1周 | MEDIUM |
| 6 | 部署与监控 | 1周 | MEDIUM |

---

## 预期效果

| 指标 | 优化前 | 优化后 | 改进 |
|------|-------|-------|------|
| 数据采集成功率 | 85% | 98% | +13% |
| 赔率分析准确率 | 70% | 80% | +10% |
| 预测准确率 | 72% | 78% | +6% |
| 错误响应时间 | 5分钟 | 30秒 | -90% |
| 报告生成时间 | 10分钟 | 1分钟 | -90% |

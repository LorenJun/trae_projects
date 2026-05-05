# 实时赔率监控系统（优化版）

> 当前说明：本文是扩展性方案稿，不是当前仓库的正式生产链路。  
> 当前正式赔率链路以 `prediction_system.py collect-data / predict-match / predict-schedule`、`okooo_fetch_daily_schedule.py`、`okooo_save_snapshot.py`、`EnhancedPredictor.predict_match()` 为准。  
> 真实大小球口径以澳客移动端 `handicap.php` 页内 `大小球` tab 和最终 `over_under.line_source` / `over_under.market.final` 为准。

## 系统概述

本系统实现了对足球比赛赔率的实时监控，具有以下特点：

- ✅ 1小时检查间隔
- ✅ 多源数据对比
- ✅ 智能告警规则
- ✅ 实时通知
- ✅ 历史数据存储

## 与当前正式流程的关系

- 正式生产流程优先使用澳客赛程与实时快照，而不是本文中的多博彩商融合示例
- 若需要进入现行预测链路，先走 `collect-data` / `predict-match`，不要把本文示例直接视作主流程实现
- 若后续将本文方案落地，输出仍应回到 `teams_2025-26.md` 与 `.okooo-scraper/runtime/`

---

## 核心实现

```python
class RealTimeOddsMonitor:
    """实时赔率监控系统"""

    def __init__(self):
        self.odds_data = {}  # 存储赔率历史数据
        self.alert_rules = self.load_alert_rules()  # 加载告警规则
        self.monitoring_config = {
            'check_interval': 3600,  # 1小时（3600秒）
            'history_size': 100,  # 每个比赛保存100条历史记录
            'max_alerts_per_match': 5,  # 每个比赛最多5条告警
            'min_sources': 2,  # 至少需要2个数据源
            'consensus_threshold': 0.8,  # 一致性阈值
            'change_threshold': 0.05  # 变化阈值（5%）
        }
        self.alert_history = defaultdict(list)  # 告警历史
        self.sources = {
            'pinnacle': PinnacleDataSource(),
            'bet365': Bet365DataSource(),
            'williamhill': WilliamHillDataSource(),
            'sporttery': SportteryDataSource()
        }

    def start_monitoring(self):
        """开始监控"""
        log("实时赔率监控系统启动，检查间隔：1小时")
        
        while True:
            try:
                self.check_odds()
                log(f"完成一次赔率检查，等待 {self.monitoring_config['check_interval']//3600} 小时后再次检查")
                time.sleep(self.monitoring_config['check_interval'])
            except Exception as e:
                log_error("监控检查失败", str(e))
                # 发生错误后等待10分钟再重试
                time.sleep(600)

    def check_odds(self):
        """检查赔率"""
        # 获取所有需要监控的比赛
        matches = self.get_monitored_matches()
        log(f"开始检查 {len(matches)} 场比赛的赔率")

        for match in matches:
            try:
                # 从多个数据源获取赔率
                odds_data = self.get_multi_source_odds(match['id'])
                
                if not odds_data['sources']:
                    log(f"比赛 {match['id']} 无可用数据源，跳过")
                    continue

                # 多源数据对比和融合
                fused_odds = self.fuse_odds_data(odds_data)
                
                # 保存历史数据
                self.update_odds_history(match['id'], fused_odds)
                
                # 分析变化
                alerts = self.analyze_odds_changes(match['id'])
                
                # 发送告警
                for alert in alerts:
                    self.send_alert(alert, match)
                    
            except Exception as e:
                log_error(f"检查比赛 {match['id']} 赔率失败", str(e))
                continue

    def get_multi_source_odds(self, match_id):
        """从多个数据源获取赔率"""
        sources_data = {}
        valid_sources = []

        for source_name, source in self.sources.items():
            try:
                odds = source.get_odds(match_id)
                if odds:
                    sources_data[source_name] = {
                        'odds': odds,
                        'timestamp': datetime.now().isoformat()
                    }
                    valid_sources.append(source_name)
                    log(f"从 {source_name} 获取到比赛 {match_id} 的赔率")
            except Exception as e:
                log_error(f"从 {source_name} 获取赔率失败", str(e))

        return {
            'sources': valid_sources,
            'data': sources_data,
            'timestamp': datetime.now().isoformat()
        }

    def fuse_odds_data(self, odds_data):
        """融合多个数据源的赔率"""
        if not odds_data['sources']:
            return None

        # 计算加权平均值
        weights = {
            'pinnacle': 0.3,      # Pinnacle 权重最高
            'bet365': 0.25,       # Bet365 权重次之
            'williamhill': 0.2,    # William Hill 权重中等
            'sporttery': 0.25      # 竞彩网 权重中等
        }

        home_win = 0
        draw = 0
        away_win = 0
        total_weight = 0

        for source in odds_data['sources']:
            weight = weights.get(source, 0.1)
            odds = odds_data['data'][source]['odds']
            
            home_win += odds.get('home_win', 0) * weight
            draw += odds.get('draw', 0) * weight
            away_win += odds.get('away_win', 0) * weight
            total_weight += weight

        if total_weight > 0:
            fused = {
                'home_win': home_win / total_weight,
                'draw': draw / total_weight,
                'away_win': away_win / total_weight,
                'sources': odds_data['sources'],
                'source_count': len(odds_data['sources']),
                'timestamp': odds_data['timestamp']
            }

            # 计算数据源一致性
            fused['consistency'] = self.calculate_consistency(odds_data['data'])
            
            return fused
        else:
            return None

    def calculate_consistency(self, sources_data):
        """计算数据源一致性"""
        if len(sources_data) < 2:
            return 1.0

        # 计算各赔率的标准差
        home_win_values = []
        draw_values = []
        away_win_values = []

        for source_data in sources_data.values():
            odds = source_data['odds']
            home_win_values.append(odds.get('home_win', 0))
            draw_values.append(odds.get('draw', 0))
            away_win_values.append(odds.get('away_win', 0))

        # 计算标准差
        def calculate_std(values):
            if len(values) < 2:
                return 0
            mean = sum(values) / len(values)
            variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
            return variance ** 0.5

        home_win_std = calculate_std(home_win_values)
        draw_std = calculate_std(draw_values)
        away_win_std = calculate_std(away_win_values)

        # 计算一致性得分（标准差越小，一致性越高）
        max_std = 5.0  # 最大可能标准差
        consistency = 1.0 - (home_win_std + draw_std + away_win_std) / (3 * max_std)
        consistency = max(0, min(1, consistency))  # 确保在0-1之间

        return consistency

    def update_odds_history(self, match_id, fused_odds):
        """更新赔率历史"""
        if match_id not in self.odds_data:
            self.odds_data[match_id] = []

        # 添加新数据
        self.odds_data[match_id].append(fused_odds)

        # 保持历史数据大小
        if len(self.odds_data[match_id]) > self.monitoring_config['history_size']:
            self.odds_data[match_id] = self.odds_data[match_id][-self.monitoring_config['history_size']:]

    def analyze_odds_changes(self, match_id):
        """分析赔率变化"""
        history = self.odds_data.get(match_id, [])

        if len(history) < 2:
            return []

        alerts = []
        current = history[-1]
        previous = history[-2]

        # 检查数据源数量
        if len(current['sources']) < self.monitoring_config['min_sources']:
            alerts.append({
                'type': 'insufficient_sources',
                'message': f'数据源不足，当前只有 {len(current["sources"])} 个数据源',
                'severity': 'LOW'
            })

        # 检查一致性
        if current['consistency'] < self.monitoring_config['consensus_threshold']:
            alerts.append({
                'type': 'low_consistency',
                'message': f'数据源一致性低 ({current["consistency"]:.2f})，请谨慎参考',
                'severity': 'MEDIUM'
            })

        # 分析主胜赔率变化
        home_win_change = (current['home_win'] - previous['home_win']) / previous['home_win'] if previous['home_win'] != 0 else 0
        draw_change = (current['draw'] - previous['draw']) / previous['draw'] if previous['draw'] != 0 else 0
        away_win_change = (current['away_win'] - previous['away_win']) / previous['away_win'] if previous['away_win'] != 0 else 0

        # 检查快速变化
        change_threshold = self.monitoring_config['change_threshold']
        if abs(home_win_change) > change_threshold:
            alerts.append({
                'type': 'rapid_change',
                'outcome': 'home_win',
                'change': home_win_change,
                'severity': self.calculate_severity(home_win_change)
            })

        if abs(draw_change) > change_threshold:
            alerts.append({
                'type': 'rapid_change',
                'outcome': 'draw',
                'change': draw_change,
                'severity': self.calculate_severity(draw_change)
            })

        if abs(away_win_change) > change_threshold:
            alerts.append({
                'type': 'rapid_change',
                'outcome': 'away_win',
                'change': away_win_change,
                'severity': self.calculate_severity(away_win_change)
            })

        # 检查趋势变化
        if len(history) >= 3:
            trend_alerts = self.analyze_trend(history)
            alerts.extend(trend_alerts)

        return alerts

    def analyze_trend(self, history):
        """分析赔率趋势"""
        alerts = []

        # 分析最近3次变化
        changes = []
        for i in range(len(history)-1, len(history)-4, -1):
            if i > 0:
                current = history[i]
                previous = history[i-1]
                
                home_win_change = (current['home_win'] - previous['home_win']) / previous['home_win'] if previous['home_win'] != 0 else 0
                changes.append(home_win_change)

        # 检查连续变化
        if len(changes) >= 2:
            if all(c > 0.03 for c in changes):
                alerts.append({
                    'type': 'upward_trend',
                    'outcome': 'home_win',
                    'message': '主胜赔率持续上升，可能不被看好',
                    'severity': 'MEDIUM'
                })
            elif all(c < -0.03 for c in changes):
                alerts.append({
                    'type': 'downward_trend',
                    'outcome': 'home_win',
                    'message': '主胜赔率持续下降，被市场看好',
                    'severity': 'MEDIUM'
                })

        return alerts

    def calculate_severity(self, change):
        """计算严重程度"""
        abs_change = abs(change)
        if abs_change > 0.15:
            return 'CRITICAL'
        elif abs_change > 0.10:
            return 'HIGH'
        elif abs_change > 0.05:
            return 'MEDIUM'
        else:
            return 'LOW'

    def send_alert(self, alert, match):
        """发送告警"""
        # 检查是否超过最大告警数
        if len(self.alert_history[match['id']]) >= self.monitoring_config['max_alerts_per_match']:
            return

        # 构建告警消息
        alert_message = self.build_alert_message(alert, match)

        # 记录告警
        self.alert_history[match['id']].append({
            'timestamp': datetime.now().isoformat(),
            'alert': alert,
            'message': alert_message
        })

        # 通知用户
        self.notify_user(alert_message, alert.get('severity', 'MEDIUM'))

    def build_alert_message(self, alert, match):
        """构建告警消息"""
        if alert['type'] == 'rapid_change':
            direction = '上升' if alert['change'] > 0 else '下降'
            outcome_name = self.get_outcome_name(alert['outcome'])
            return f"【赔率预警】{match['home_team']} vs {match['away_team']} 的{outcome_name}赔率快速{direction}{abs(alert['change']*100):.1f}%"

        elif alert['type'] == 'insufficient_sources':
            return f"【赔率预警】{match['home_team']} vs {match['away_team']} {alert['message']}"

        elif alert['type'] == 'low_consistency':
            return f"【赔率预警】{match['home_team']} vs {match['away_team']} {alert['message']}"

        elif alert['type'] == 'upward_trend':
            return f"【赔率预警】{match['home_team']} vs {match['away_team']} {alert['message']}"

        elif alert['type'] == 'downward_trend':
            return f"【赔率预警】{match['home_team']} vs {match['away_team']} {alert['message']}"

        return f"【赔率预警】{match['home_team']} vs {match['away_team']} 未知告警类型"

    def get_outcome_name(self, outcome):
        """获取赛果名称"""
        names = {
            'home_win': '主胜',
            'draw': '平局',
            'away_win': '客胜'
        }
        return names.get(outcome, outcome)

    def load_alert_rules(self):
        """加载告警规则"""
        return [
            {
                'type': 'rapid_change',
                'threshold': 0.05,
                'description': '赔率快速变化超过5%'
            },
            {
                'type': 'consensus_change',
                'threshold': 0.8,
                'description': '多家机构赔率方向一致变化'
            },
            {
                'type': 'insufficient_sources',
                'threshold': 2,
                'description': '数据源不足'
            }
        ]

    def get_monitored_matches(self):
        """获取需要监控的比赛"""
        # 这里应该从数据库或配置中获取需要监控的比赛
        # 暂时返回模拟数据
        return [
            {
                'id': '123456',
                'home_team': '曼联',
                'away_team': '利物浦',
                'league': '英超',
                'date': '2026-04-20',
                'time': '20:30'
            },
            {
                'id': '123457',
                'home_team': '拜仁',
                'away_team': '多特蒙德',
                'league': '德甲',
                'date': '2026-04-21',
                'time': '21:30'
            }
        ]

    def notify_user(self, message, severity):
        """通知用户"""
        # 这里可以实现各种通知方式，如邮件、短信、WebSocket等
        log(f"[告警] [{severity}] {message}")
        
        # 示例：发送邮件通知
        # send_email('user@example.com', '赔率预警', message)
        
        # 示例：通过WebSocket通知前端
        # websocket_manager.broadcast({'type': 'alert', 'message': message, 'severity': severity})


# 数据源实现
class PinnacleDataSource:
    """Pinnacle数据源"""
    def get_odds(self, match_id):
        # 实现Pinnacle赔率获取
        # 模拟返回数据
        return {
            'home_win': 2.10,
            'draw': 3.40,
            'away_win': 3.20
        }


class Bet365DataSource:
    """Bet365数据源"""
    def get_odds(self, match_id):
        # 实现Bet365赔率获取
        # 模拟返回数据
        return {
            'home_win': 2.15,
            'draw': 3.50,
            'away_win': 3.10
        }


class WilliamHillDataSource:
    """William Hill数据源"""
    def get_odds(self, match_id):
        # 实现William Hill赔率获取
        # 模拟返回数据
        return {
            'home_win': 2.20,
            'draw': 3.40,
            'away_win': 3.00
        }


class SportteryDataSource:
    """竞彩网数据源"""
    def get_odds(self, match_id):
        # 实现竞彩网赔率获取
        # 模拟返回数据
        return {
            'home_win': 2.05,
            'draw': 3.30,
            'away_win': 3.30
        }


# 辅助函数
def log(message):
    """日志函数"""
    print(f"[{datetime.now().isoformat()}] {message}")


def log_error(message, error):
    """错误日志函数"""
    print(f"[{datetime.now().isoformat()}] [ERROR] {message}: {error}")


def send_email(to, subject, message):
    """发送邮件"""
    # 实现邮件发送逻辑
    pass


class WebSocketManager:
    """WebSocket管理器"""
    def broadcast(self, message):
        # 实现WebSocket广播逻辑
        pass


# 全局WebSocket管理器
websocket_manager = WebSocketManager()
```

---

## 系统配置

### 配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| check_interval | 3600 | 检查间隔（秒），设置为1小时 |
| history_size | 100 | 每个比赛保存的历史记录数 |
| max_alerts_per_match | 5 | 每个比赛最多发送的告警数 |
| min_sources | 2 | 至少需要的数据源数量 |
| consensus_threshold | 0.8 | 数据源一致性阈值 |
| change_threshold | 0.05 | 赔率变化阈值（5%） |

### 数据源配置

| 数据源 | 权重 | 可靠性 | 特点 |
|--------|------|--------|------|
| Pinnacle | 0.30 | 高 | 专业博彩公司，赔率精准 |
| Bet365 | 0.25 | 高 | 全球最大博彩公司之一 |
| William Hill | 0.20 | 中高 | 传统老牌博彩公司 |
| 竞彩网 | 0.25 | 中 | 官方数据，稳定性高 |

---

## 告警规则

### 告警类型

| 告警类型 | 描述 | 严重程度 |
|---------|------|----------|
| rapid_change | 赔率快速变化 | 中高 |
| low_consistency | 数据源一致性低 | 中 |
| insufficient_sources | 数据源不足 | 低 |
| upward_trend | 赔率持续上升 | 中 |
| downward_trend | 赔率持续下降 | 中 |

### 严重程度等级

| 等级 | 说明 | 处理方式 |
|------|------|----------|
| CRITICAL | 严重变化（>15%） | 立即通知 |
| HIGH | 较大变化（10-15%） | 优先通知 |
| MEDIUM | 中等变化（5-10%） | 常规通知 |
| LOW | 轻微变化（<5%） | 记录不通知 |

---

## 数据融合算法

### 加权融合

1. **数据源权重分配**：根据数据源的可靠性和专业性分配不同权重
2. **加权平均计算**：对各数据源的赔率进行加权平均
3. **一致性计算**：计算各数据源之间的一致性程度
4. **异常检测**：识别和排除异常数据源

### 一致性计算

```python
def calculate_consistency(sources_data):
    """计算数据源一致性"""
    # 1. 收集所有数据源的赔率
    # 2. 计算每个结果的标准差
    # 3. 标准差越小，一致性越高
    # 4. 将一致性归一化到0-1之间
```

---

## 使用示例

### 启动监控

```python
# 创建监控实例
monitor = RealTimeOddsMonitor()

# 启动监控线程
import threading
monitor_thread = threading.Thread(target=monitor.start_monitoring, daemon=True)
monitor_thread.start()

# 主线程继续执行其他任务
while True:
    time.sleep(1)
```

### 手动检查

```python
# 手动检查特定比赛的赔率
match_id = '123456'
odds_data = monitor.get_multi_source_odds(match_id)
fused_odds = monitor.fuse_odds_data(odds_data)

print(f"融合后的赔率: {fused_odds}")
print(f"数据源一致性: {fused_odds['consistency']:.2f}")
print(f"使用的数据源: {fused_odds['sources']}")
```

---

## 性能优化

### 时间复杂度

- **每次检查**：O(N × S)，其中N是比赛数量，S是数据源数量
- **数据融合**：O(S)，其中S是数据源数量
- **历史存储**：O(H)，其中H是历史记录数量

### 内存使用

- **每个比赛**：约1KB × 历史记录数
- **1000场比赛**：约1MB（假设每个比赛100条记录）

### 扩展性

- **支持动态添加数据源**
- **支持分布式部署**
- **支持水平扩展**

---

## 故障处理

### 数据源故障

- **自动降级**：当某个数据源失败时，自动使用其他数据源
- **重试机制**：对临时故障进行自动重试
- **告警通知**：当多个数据源同时失败时发送告警

### 系统故障

- **异常捕获**：捕获并记录所有异常
- **自动恢复**：故障后自动恢复监控
- **状态监控**：监控系统自身的健康状态

---

## 总结

本实时赔率监控系统具有以下特点：

1. **1小时检查间隔**：平衡了实时性和系统负载
2. **多源数据对比**：融合多个权威数据源，提高数据可靠性
3. **智能告警**：基于规则和趋势分析，及时发现异常
4. **历史存储**：保存完整的赔率变化历史，支持趋势分析
5. **可扩展性**：支持动态添加数据源和功能扩展

系统采用模块化设计，易于集成到现有足球预测系统中，为用户提供及时、准确的赔率变化信息，帮助用户做出更明智的投注决策。

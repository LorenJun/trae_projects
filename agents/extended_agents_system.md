# 足球预测多Agent系统扩展方案

## 系统扩展概览

本扩展方案基于之前的优化版多Agent系统，新增核心功能：

1. ✅ 引入机器学习模型提升预测准确率

---

## 功能1: 机器学习模型集成

### 模型架构设计

```python
class MLModelIntegration:
    """机器学习模型集成"""

    def __init__(self):
        self.models = {
            'logistic_regression': LogisticRegressionModel(),
            'random_forest': RandomForestModel(),
            'xgboost': XGBoostModel(),
            'neural_network': NeuralNetworkModel(),
            'ensemble': EnsembleModel()
        }
        self.feature_engineering = FeatureEngineering()
        self.model_selector = ModelSelector()

    def train_models(self, training_data):
        """训练模型"""
        # 特征工程
        features = self.feature_engineering.process(training_data)
        X, y = features['X'], features['y']

        # 训练所有模型
        for model_name, model in self.models.items():
            try:
                model.train(X, y)
                model.save(f'models/{model_name}.pkl')
                log(f"模型 {model_name} 训练完成")
            except Exception as e:
                log_error(f"模型 {model_name} 训练失败", str(e))

    def predict(self, match_data):
        """预测比赛结果"""
        # 提取特征
        features = self.feature_engineering.extract_features(match_data)

        # 模型选择
        selected_model = self.model_selector.select_model(match_data)

        # 预测
        prediction = self.models[selected_model].predict(features)

        # 集成预测
        if selected_model != 'ensemble':
            ensemble_prediction = self.models['ensemble'].predict(features)
            prediction = self.combine_predictions(prediction, ensemble_prediction)

        return prediction

    def combine_predictions(self, single_prediction, ensemble_prediction):
        """组合预测结果"""
        # 权重融合
        weights = {'single': 0.7, 'ensemble': 0.3}

        combined = {
            'home_win': single_prediction['home_win'] * weights['single'] + ensemble_prediction['home_win'] * weights['ensemble'],
            'draw': single_prediction['draw'] * weights['single'] + ensemble_prediction['draw'] * weights['ensemble'],
            'away_win': single_prediction['away_win'] * weights['single'] + ensemble_prediction['away_win'] * weights['ensemble']
        }

        return combined
```

### 特征工程

```python
class FeatureEngineering:
    """特征工程"""

    def process(self, data):
        """处理训练数据"""
        features = []
        labels = []

        for record in data:
            record_features = self.extract_features(record)
            features.append(list(record_features.values()))
            labels.append(self.encode_label(record['result']))

        return {
            'X': np.array(features),
            'y': np.array(labels)
        }

    def extract_features(self, match_data):
        """提取特征"""
        features = {
            # 球队基本特征
            'home_rank': match_data.get('home_rank', 10),
            'away_rank': match_data.get('away_rank', 10),
            'home_pts': match_data.get('home_points', 30),
            'away_pts': match_data.get('away_points', 30),

            # 近期状态特征
            'home_form': self.calculate_form_score(match_data.get('home_recent_matches', [])),
            'away_form': self.calculate_form_score(match_data.get('away_recent_matches', [])),
            'home_goals': self.calculate_goal_average(match_data.get('home_recent_matches', [])),
            'away_goals': self.calculate_goal_average(match_data.get('away_recent_matches', [])),

            # 历史交锋特征
            'head_to_head_home_wins': match_data.get('h2h_home_wins', 0),
            'head_to_head_away_wins': match_data.get('h2h_away_wins', 0),
            'head_to_head_draws': match_data.get('h2h_draws', 0),

            # 赔率特征
            'home_odds': match_data.get('home_odds', 2.0),
            'draw_odds': match_data.get('draw_odds', 3.0),
            'away_odds': match_data.get('away_odds', 2.0),

            # 凯利指数特征
            'home_kelly': match_data.get('home_kelly', 1.0),
            'draw_kelly': match_data.get('draw_kelly', 1.0),
            'away_kelly': match_data.get('away_kelly', 1.0),

            # 主客场特征
            'is_home': 1 if match_data.get('is_home', True) else 0,

            # 联赛特征
            'league_id': self.encode_league(match_data.get('league', 'premier_league')),

            # 时间特征
            'match_hour': int(match_data.get('match_time', '15:00').split(':')[0]),
            'is_weekend': 1 if self.is_weekend(match_data.get('match_date', '2026-01-01')) else 0
        }

        return features

    def calculate_form_score(self, recent_matches):
        """计算状态得分"""
        if not recent_matches:
            return 0

        score = 0
        for i, match in enumerate(recent_matches):
            if match['result'] == 'win':
                score += 3 * (0.8 ** i)  # 最近比赛权重更高
            elif match['result'] == 'draw':
                score += 1 * (0.8 ** i)

        return score

    def calculate_goal_average(self, recent_matches):
        """计算进球平均值"""
        if not recent_matches:
            return 1.5

        total_goals = sum(match.get('goals', 0) for match in recent_matches)
        return total_goals / len(recent_matches)

    def encode_label(self, result):
        """编码标签"""
        if result == 'home_win':
            return 0
        elif result == 'draw':
            return 1
        else:  # away_win
            return 2

    def encode_league(self, league):
        """编码联赛"""
        league_map = {
            'premier_league': 0,
            'bundesliga': 1,
            'serie_a': 2,
            'ligue_1': 3,
            'la_liga': 4
        }
        return league_map.get(league, 0)

    def is_weekend(self, date_str):
        """判断是否周末"""
        date = datetime.strptime(date_str, '%Y-%m-%d')
        return date.weekday() >= 5
```

### 模型实现

```python
class LogisticRegressionModel:
    """逻辑回归模型"""

    def __init__(self):
        from sklearn.linear_model import LogisticRegression
        self.model = LogisticRegression(max_iter=1000, C=1.0)

    def train(self, X, y):
        """训练模型"""
        self.model.fit(X, y)

    def predict(self, features):
        """预测"""
        X = np.array([list(features.values())])
        proba = self.model.predict_proba(X)[0]

        return {
            'home_win': proba[0],
            'draw': proba[1],
            'away_win': proba[2]
        }

    def save(self, path):
        """保存模型"""
        import pickle
        with open(path, 'wb') as f:
            pickle.dump(self.model, f)

    def load(self, path):
        """加载模型"""
        import pickle
        with open(path, 'rb') as f:
            self.model = pickle.load(f)


class RandomForestModel:
    """随机森林模型"""

    def __init__(self):
        from sklearn.ensemble import RandomForestClassifier
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            random_state=42
        )

    def train(self, X, y):
        """训练模型"""
        self.model.fit(X, y)

    def predict(self, features):
        """预测"""
        X = np.array([list(features.values())])
        proba = self.model.predict_proba(X)[0]

        return {
            'home_win': proba[0],
            'draw': proba[1],
            'away_win': proba[2]
        }


class XGBoostModel:
    """XGBoost模型"""

    def __init__(self):
        from xgboost import XGBClassifier
        self.model = XGBClassifier(
            n_estimators=100,
            max_depth=6,
            learning_rate=0.1,
            objective='multi:softprob'
        )

    def train(self, X, y):
        """训练模型"""
        self.model.fit(X, y)

    def predict(self, features):
        """预测"""
        X = np.array([list(features.values())])
        proba = self.model.predict_proba(X)[0]

        return {
            'home_win': proba[0],
            'draw': proba[1],
            'away_win': proba[2]
        }


class NeuralNetworkModel:
    """神经网络模型"""

    def __init__(self):
        from tensorflow.keras.models import Sequential
        from tensorflow.keras.layers import Dense, Dropout

        self.model = Sequential([
            Dense(64, activation='relu', input_shape=(20,)),
            Dropout(0.2),
            Dense(32, activation='relu'),
            Dropout(0.2),
            Dense(3, activation='softmax')
        ])

        self.model.compile(
            optimizer='adam',
            loss='sparse_categorical_crossentropy',
            metrics=['accuracy']
        )

    def train(self, X, y):
        """训练模型"""
        self.model.fit(X, y, epochs=50, batch_size=32, validation_split=0.2)

    def predict(self, features):
        """预测"""
        X = np.array([list(features.values())])
        proba = self.model.predict(X)[0]

        return {
            'home_win': proba[0],
            'draw': proba[1],
            'away_win': proba[2]
        }


class EnsembleModel:
    """集成模型"""

    def __init__(self):
        self.base_models = {
            'logistic_regression': LogisticRegressionModel(),
            'random_forest': RandomForestModel(),
            'xgboost': XGBoostModel()
        }

    def train(self, X, y):
        """训练所有基础模型"""
        for model_name, model in self.base_models.items():
            model.train(X, y)

    def predict(self, features):
        """集成预测"""
        predictions = []

        for model_name, model in self.base_models.items():
            pred = model.predict(features)
            predictions.append(pred)

        # 平均融合
        home_win = sum(p['home_win'] for p in predictions) / len(predictions)
        draw = sum(p['draw'] for p in predictions) / len(predictions)
        away_win = sum(p['away_win'] for p in predictions) / len(predictions)

        return {
            'home_win': home_win,
            'draw': draw,
            'away_win': away_win
        }
```

### 模型选择器

```python
class ModelSelector:
    """模型选择器"""

    def __init__(self):
        self.model_performance = {
            'logistic_regression': {'accuracy': 0.72, 'speed': 0.95},
            'random_forest': {'accuracy': 0.75, 'speed': 0.75},
            'xgboost': {'accuracy': 0.78, 'speed': 0.80},
            'neural_network': {'accuracy': 0.76, 'speed': 0.70},
            'ensemble': {'accuracy': 0.80, 'speed': 0.60}
        }

    def select_model(self, match_data):
        """选择最佳模型"""
        league = match_data.get('league', 'premier_league')
        urgency = match_data.get('urgency', 'normal')  # normal, high, critical

        # 根据联赛选择模型
        league_models = {
            'premier_league': 'xgboost',
            'bundesliga': 'random_forest',
            'serie_a': 'logistic_regression',
            'ligue_1': 'xgboost',
            'la_liga': 'neural_network'
        }

        # 根据紧急程度调整
        if urgency == 'high':
            # 快速模型
            fast_models = ['logistic_regression', 'xgboost']
            if league_models[league] not in fast_models:
                return 'logistic_regression'
        elif urgency == 'critical':
            # 最高准确率
            return 'ensemble'

        return league_models.get(league, 'xgboost')
```
#!/usr/bin/env python3
"""
历史预测准确性数据库
记录和追踪预测的准确性
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, filename='prediction_history.log')


class PredictionHistoryDB:
    """历史预测数据库"""

    def __init__(self, db_path: str = "prediction_history"):
        self.db_path = db_path
        self.predictions_file = f"{db_path}/predictions.json"
        self.results_file = f"{db_path}/results.json"
        self.accuracy_file = f"{db_path}/accuracy_stats.json"
        self._init_db()

    def _init_db(self):
        """初始化数据库"""
        os.makedirs(self.db_path, exist_ok=True)

        # 初始化文件
        if not os.path.exists(self.predictions_file):
            with open(self.predictions_file, 'w', encoding='utf-8') as f:
                json.dump([], f)

        if not os.path.exists(self.results_file):
            with open(self.results_file, 'w', encoding='utf-8') as f:
                json.dump([], f)

        if not os.path.exists(self.accuracy_file):
            with open(self.accuracy_file, 'w', encoding='utf-8') as f:
                json.dump({}, f)

    def save_prediction(self, prediction: Dict):
        """保存预测"""
        with open(self.predictions_file, 'r', encoding='utf-8') as f:
            predictions = json.load(f)

        prediction['saved_at'] = datetime.now().isoformat()
        predictions.append(prediction)

        with open(self.predictions_file, 'w', encoding='utf-8') as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)

        logging.info(f"保存预测: {prediction.get('match_id')}")

    def save_result(self, match_id: str, result: Dict):
        """保存比赛结果"""
        with open(self.results_file, 'r', encoding='utf-8') as f:
            results = json.load(f)

        result['match_id'] = match_id
        result['saved_at'] = datetime.now().isoformat()
        results.append(result)

        with open(self.results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        logging.info(f"保存结果: {match_id}")

    def get_prediction(self, match_id: str) -> Optional[Dict]:
        """获取预测"""
        with open(self.predictions_file, 'r', encoding='utf-8') as f:
            predictions = json.load(f)

        for pred in predictions:
            if pred.get('match_id') == match_id:
                return pred
        return None

    def get_result(self, match_id: str) -> Optional[Dict]:
        """获取结果"""
        with open(self.results_file, 'r', encoding='utf-8') as f:
            results = json.load(f)

        for result in results:
            if result.get('match_id') == match_id:
                return result
        return None

    def calculate_accuracy(self, league: Optional[str] = None, days: int = 30) -> Dict:
        """计算准确率"""
        with open(self.predictions_file, 'r', encoding='utf-8') as f:
            predictions = json.load(f)

        with open(self.results_file, 'r', encoding='utf-8') as f:
            results = json.load(f)

        # 创建结果字典
        results_dict = {r['match_id']: r for r in results}

        # 筛选预测
        cutoff_date = datetime.now().timestamp() - (days * 86400)
        recent_predictions = [
            p for p in predictions
            if datetime.fromisoformat(p['saved_at']).timestamp() > cutoff_date
        ]

        if league:
            recent_predictions = [
                p for p in recent_predictions
                if p.get('league') == league
            ]

        # 计算准确率
        total = 0
        correct = 0
        correct_scores = 0
        half_correct = 0

        for pred in recent_predictions:
            match_id = pred.get('match_id')
            if match_id not in results_dict:
                continue

            result = results_dict[match_id]
            total += 1

            # 胜负预测
            if pred.get('predicted_winner') == result.get('actual_winner'):
                correct += 1

            # 比分预测
            if pred.get('predicted_score') == result.get('actual_score'):
                correct_scores += 1

            # 半准确（胜负正确但比分错误）
            if (pred.get('predicted_winner') == result.get('actual_winner') and
                pred.get('predicted_score') != result.get('actual_score')):
                half_correct += 1

        accuracy = (correct / total * 100) if total > 0 else 0
        score_accuracy = (correct_scores / total * 100) if total > 0 else 0
        half_accuracy = (half_correct / total * 100) if total > 0 else 0

        return {
            'total_predictions': total,
            'correct_predictions': correct,
            'correct_scores': correct_scores,
            'half_correct': half_correct,
            'win_accuracy': accuracy,
            'score_accuracy': score_accuracy,
            'half_accuracy': half_accuracy,
            'league': league,
            'days': days,
            'calculated_at': datetime.now().isoformat()
        }

    def update_accuracy_stats(self):
        """更新准确率统计"""
        stats = {
            'overall': self.calculate_accuracy(),
            'by_league': {},
            'by_model': {},
            'trend': []
        }

        # 按联赛统计
        leagues = ['premier_league', 'serie_a', 'bundesliga', 'ligue_1', 'la_liga']
        for league in leagues:
            stats['by_league'][league] = self.calculate_accuracy(league=league)

        # 按模型统计
        models = ['poisson', 'dixon_coles', 'elo', 'glicko', 'logistic_regression', 'random_forest', 'xg', 'bayesian']
        for model in models:
            stats['by_model'][model] = self._calculate_model_accuracy(model)

        # 计算趋势（最近7天）
        for i in range(7):
            day = (datetime.now().timestamp() - (i * 86400)) / 86400
            day_accuracy = self.calculate_accuracy(days=1)
            stats['trend'].append({
                'date': datetime.fromtimestamp(day * 86400).strftime('%Y-%m-%d'),
                'accuracy': day_accuracy['win_accuracy']
            })

        stats['trend'].reverse()

        with open(self.accuracy_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)

        logging.info("准确率统计已更新")
        return stats

    def _calculate_model_accuracy(self, model_name: str) -> Dict:
        """计算特定模型的准确率"""
        with open(self.predictions_file, 'r', encoding='utf-8') as f:
            predictions = json.load(f)

        with open(self.results_file, 'r', encoding='utf-8') as f:
            results = json.load(f)

        results_dict = {r['match_id']: r for r in results}

        total = 0
        correct = 0

        for pred in predictions:
            if model_name not in pred.get('model_predictions', {}):
                continue

            match_id = pred.get('match_id')
            if match_id not in results_dict:
                continue

            model_pred = pred['model_predictions'][model_name]
            actual = results_dict[match_id]['actual_winner']

            # 确定模型预测
            if model_pred['home_win'] > model_pred['away_win'] and model_pred['home_win'] > model_pred.get('draw', 0):
                predicted = 'home'
            elif model_pred['away_win'] > model_pred['home_win']:
                predicted = 'away'
            else:
                predicted = 'draw'

            total += 1
            if predicted == actual:
                correct += 1

        accuracy = (correct / total * 100) if total > 0 else 0

        return {
            'total': total,
            'correct': correct,
            'accuracy': accuracy
        }

    def generate_accuracy_report(self) -> str:
        """生成准确率报告"""
        stats = self.update_accuracy_stats()

        report = []
        report.append("=" * 60)
        report.append("📊 预测准确率报告")
        report.append("=" * 60)

        # 总体准确率
        overall = stats['overall']
        report.append(f"\n【总体准确率】")
        report.append(f"  总预测数: {overall['total_predictions']}")
        report.append(f"  胜负准确率: {overall['win_accuracy']:.1f}%")
        report.append(f"  比分准确率: {overall['score_accuracy']:.1f}%")
        report.append(f"  半准确率: {overall['half_accuracy']:.1f}%")

        # 各联赛准确率
        report.append(f"\n【各联赛准确率】")
        for league, league_stats in stats['by_league'].items():
            if league_stats['total_predictions'] > 0:
                league_name = {
                    'premier_league': '英超',
                    'serie_a': '意甲',
                    'bundesliga': '德甲',
                    'ligue_1': '法甲',
                    'la_liga': '西甲'
                }.get(league, league)
                report.append(f"  {league_name}: {league_stats['win_accuracy']:.1f}% ({league_stats['total_predictions']}场)")

        # 各模型准确率
        report.append(f"\n【各模型准确率】")
        for model, model_stats in stats['by_model'].items():
            if model_stats['total'] > 0:
                report.append(f"  {model}: {model_stats['accuracy']:.1f}% ({model_stats['total']}场)")

        # 趋势
        report.append(f"\n【最近7天趋势】")
        for trend in stats['trend'][-7:]:
            report.append(f"  {trend['date']}: {trend['accuracy']:.1f}%")

        report.append("\n" + "=" * 60)

        return "\n".join(report)


def main():
    """测试历史数据库"""
    db = PredictionHistoryDB()

    # 保存一些测试预测
    test_predictions = [
        {
            'match_id': 'test_001',
            'league': 'premier_league',
            'home_team': '切尔西',
            'away_team': '曼联',
            'predicted_winner': 'away',
            'predicted_score': '1-2',
            'model_predictions': {
                'elo': {'home_win': 0.3, 'draw': 0.3, 'away_win': 0.4},
                'poisson': {'home_win': 0.35, 'draw': 0.25, 'away_win': 0.4}
            },
            'saved_at': datetime.now().isoformat()
        },
        {
            'match_id': 'test_002',
            'league': 'serie_a',
            'home_team': '那不勒斯',
            'away_team': '拉齐奥',
            'predicted_winner': 'home',
            'predicted_score': '2-1',
            'model_predictions': {
                'elo': {'home_win': 0.6, 'draw': 0.2, 'away_win': 0.2}
            },
            'saved_at': datetime.now().isoformat()
        }
    ]

    print("保存测试预测...")
    for pred in test_predictions:
        db.save_prediction(pred)

    # 保存测试结果
    test_results = [
        {
            'match_id': 'test_001',
            'actual_winner': 'away',
            'actual_score': '0-1',
            'home_score': 0,
            'away_score': 1
        },
        {
            'match_id': 'test_002',
            'actual_winner': 'away',
            'actual_score': '0-2',
            'home_score': 0,
            'away_score': 2
        }
    ]

    print("保存测试结果...")
    for result in test_results:
        db.save_result(result['match_id'], result)

    # 生成准确率报告
    print("\n" + db.generate_accuracy_report())


if __name__ == "__main__":
    main()
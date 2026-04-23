#!/usr/bin/env python3
"""
比赛结果管理和准确率更新系统
"""

import os
import sys
import json
import re
from datetime import datetime
from typing import Dict, List, Optional
import logging

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 联赛名称映射
LEAGUE_NAMES = {
    'premier_league': '英超联赛',
    'serie_a': '意甲联赛',
    'bundesliga': '德甲联赛',
    'ligue_1': '法甲联赛',
    'la_liga': '西甲联赛'
}


class ResultManager:
    """结果管理器"""
    
    def __init__(self):
        # prediction_history/ is deprecated; teams_2025-26.md is the single source of truth.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        runtime_dir = os.path.join(base_dir, '.okooo-scraper', 'runtime')
        os.makedirs(runtime_dir, exist_ok=True)
        self.accuracy_file = os.path.join(runtime_dir, 'accuracy_stats.json')

    def load_predictions(self) -> List[Dict]:
        """Derive predictions from teams_2025-26.md note columns (no prediction_history/)."""
        preds: List[Dict] = []
        for league_code, match_date, match_time, home_team, score_text, away_team, note in self._iter_teams_rows():
            predicted_winner = self._parse_predicted_winner(note)
            if predicted_winner not in ('home', 'draw', 'away'):
                continue
            preds.append({
                'match_id': self._match_id_for_teams_row(league_code, match_date, home_team, away_team),
                'league': league_code,
                'league_name': LEAGUE_NAMES.get(league_code, league_code),
                'home_team': home_team,
                'away_team': away_team,
                'match_date': match_date,
                'match_time': match_time,
                'predicted_winner': predicted_winner,
                'saved_at': match_date,
            })
        return preds

    def load_results(self) -> List[Dict]:
        """Derive completed results from teams_2025-26.md score columns (no prediction_history/)."""
        results: List[Dict] = []
        for league_code, match_date, match_time, home_team, score_text, away_team, note in self._iter_teams_rows():
            if not re.match(r'^\d+\s*-\s*\d+$', score_text or ''):
                continue
            actual_winner = self._parse_score_to_winner(score_text)
            if actual_winner not in ('home', 'draw', 'away'):
                continue
            hs, as_ = [int(x.strip()) for x in score_text.split('-')]
            results.append({
                'match_id': self._match_id_for_teams_row(league_code, match_date, home_team, away_team),
                'league': league_code,
                'home_team': home_team,
                'away_team': away_team,
                'match_date': match_date,
                'match_time': match_time,
                'actual_winner': actual_winner,
                'actual_score': f"{hs}-{as_}",
                'home_score': hs,
                'away_score': as_,
                'result_status': 'completed',
                'saved_at': match_date,
            })
        return results

    def _teams_md_path(self, league_code: str) -> str:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, league_code, 'teams_2025-26.md')

    def _match_id_for_teams_row(self, league_code: str, match_date: str, home_team: str, away_team: str) -> str:
        return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

    def _parse_score_to_winner(self, score_text: str) -> Optional[str]:
        if not isinstance(score_text, str) or '-' not in score_text:
            return None
        parts = score_text.split('-')
        if len(parts) != 2:
            return None
        try:
            hs = int(parts[0].strip())
            as_ = int(parts[1].strip())
        except Exception:
            return None
        if hs > as_:
            return 'home'
        if hs < as_:
            return 'away'
        return 'draw'

    def _parse_predicted_winner(self, note: str) -> Optional[str]:
        """Parse predicted winner from note column."""
        if not isinstance(note, str):
            return None
        m = re.search(r'预测[:\s]*(主胜|平局|客胜)', note)
        if not m:
            return None
        val = m.group(1)
        return {'主胜': 'home', '平局': 'draw', '客胜': 'away'}.get(val)

    def _iter_teams_rows(self):
        """Yield (league, date, time, home, score, away, note)."""
        for league_code in LEAGUE_NAMES.keys():
            path = self._teams_md_path(league_code)
            if not os.path.exists(path):
                continue
            try:
                lines = open(path, 'r', encoding='utf-8').read().splitlines()
            except Exception:
                continue
            for line in lines:
                if not line.strip().startswith('|'):
                    continue
                cols = [c.strip() for c in line.strip().strip('|').split('|')]
                if len(cols) != 6:
                    continue
                match_date, match_time, home_team, score_text, away_team, note = cols
                if not re.match(r'^\d{4}-\d{2}-\d{2}$', match_date):
                    continue
                yield league_code, match_date, match_time, home_team, score_text, away_team, note
    
    def load_predictions(self) -> List[Dict]:
        """加载所有预测"""
        with open(self.predictions_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def load_results(self) -> List[Dict]:
        """加载所有结果"""
        with open(self.results_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def save_result(self, match_id: str, home_score: int, away_score: int):
        """保存比赛结果"""
        results = self.load_results()
        
        # 检查是否已存在
        existing_idx = None
        for idx, result in enumerate(results):
            if result.get('match_id') == match_id:
                existing_idx = idx
                break
        
        # 确定实际胜者
        if home_score > away_score:
            actual_winner = 'home'
        elif home_score < away_score:
            actual_winner = 'away'
        else:
            actual_winner = 'draw'
        
        result_data = {
            'match_id': match_id,
            'actual_winner': actual_winner,
            'actual_score': f"{home_score}-{away_score}",
            'home_score': home_score,
            'away_score': away_score,
            'saved_at': datetime.now().isoformat()
        }
        
        if existing_idx is not None:
            results[existing_idx] = result_data
            logger.info(f"更新比赛结果: {match_id}")
        else:
            results.append(result_data)
            logger.info(f"保存比赛结果: {match_id}")
        
        with open(self.results_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        # 更新预测的正确性
        self._update_prediction_correctness(match_id, actual_winner)
        
        return result_data
    
    def _update_prediction_correctness(self, match_id: str, actual_winner: str):
        """更新预测的正确性标记"""
        predictions = self.load_predictions()
        
        for pred in predictions:
            if pred.get('match_id') == match_id:
                predicted_winner = pred.get('predicted_winner')
                pred['correct'] = (predicted_winner == actual_winner)
                break
        
        with open(self.predictions_file, 'w', encoding='utf-8') as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
    
    def get_pending_matches(self, days_back: int = 7) -> List[Dict]:
        """获取待更新结果的比赛"""
        predictions = self.load_predictions()
        results = self.load_results()
        
        result_ids = set(r['match_id'] for r in results)
        
        pending = []
        cutoff = datetime.now().timestamp() - (days_back * 86400)
        
        for pred in predictions:
            match_id = pred.get('match_id', '')
            
            # 跳过占位符
            if '------' in match_id:
                continue
            
            # 检查是否已有结果
            if match_id in result_ids:
                continue
            
            # 检查时间范围
            saved_at = pred.get('saved_at', '')
            if saved_at:
                try:
                    saved_time = datetime.fromisoformat(saved_at).timestamp()
                    if saved_time < cutoff:
                        continue
                except:
                    pass
            
            pending.append(pred)
        
        return pending
    
    def calculate_accuracy(self, league: Optional[str] = None, days: int = 30) -> Dict:
        """计算准确率"""
        # Source of truth: teams_2025-26.md
        total = 0
        correct = 0
        total_score_pred = 0
        correct_score = 0
        
        cutoff = datetime.now().timestamp() - (days * 86400)

        valid_winners = {'home', 'draw', 'away'}

        for league_code, match_date, _match_time, home_team, score_text, away_team, note in self._iter_teams_rows():
            if league and league_code != league:
                continue

            # Time window filter based on match_date
            try:
                saved_time = datetime.fromisoformat(match_date).timestamp()
                if saved_time < cutoff:
                    continue
            except Exception:
                pass

            predicted_winner = self._parse_predicted_winner(note)
            actual_winner = self._parse_score_to_winner(score_text) if re.match(r'^\d+\s*-\s*\d+$', score_text or '') else None
            if predicted_winner not in valid_winners or actual_winner not in valid_winners:
                continue
            total += 1
            if predicted_winner == actual_winner:
                correct += 1
        
        accuracy = (correct / total * 100) if total > 0 else 0
        score_accuracy = (correct_score / total_score_pred * 100) if total_score_pred > 0 else 0
        
        return {
            'league': league,
            'total_predictions': total,
            'correct_predictions': correct,
            'win_accuracy': round(accuracy, 2),
            'total_score_predictions': total_score_pred,
            'correct_score_predictions': correct_score,
            'score_accuracy': round(score_accuracy, 2),
            'model_accuracy': {},
            'calculated_at': datetime.now().isoformat(),
            'days': days
        }
    
    def update_accuracy_stats(self):
        """更新准确率统计"""
        stats = {
            'overall': self.calculate_accuracy(),
            'by_league': {},
            'last_updated': datetime.now().isoformat()
        }
        
        for league_code in LEAGUE_NAMES.keys():
            stats['by_league'][league_code] = self.calculate_accuracy(league=league_code)
        
        with open(self.accuracy_file, 'w', encoding='utf-8') as f:
            json.dump(stats, f, ensure_ascii=False, indent=2)
        
        logger.info("准确率统计已更新")
        return stats
    
    def save_prediction_from_enhanced(self, enhanced_pred: Dict, league_code: str):
        """从增强预测系统保存预测"""
        predictions = self.load_predictions()
        
        match_date = enhanced_pred.get('match_date', datetime.now().strftime('%Y-%m-%d'))
        home_team = enhanced_pred.get('home_team', '')
        away_team = enhanced_pred.get('away_team', '')
        
        match_id = f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"
        
        # 转换预测结果
        prediction_result = enhanced_pred.get('prediction', '')
        if prediction_result == '主胜':
            predicted_winner = 'home'
        elif prediction_result == '客胜':
            predicted_winner = 'away'
        elif prediction_result == '平局':
            predicted_winner = 'draw'
        else:
            predicted_winner = None
        
        # 处理比分预测
        top_scores = enhanced_pred.get('top_scores', [])
        predicted_score = '/'.join([s for s, _ in top_scores[:2]]) if top_scores else ''
        
        prediction_data = {
            'match_id': match_id,
            'league': league_code,
            'league_name': LEAGUE_NAMES.get(league_code, league_code),
            'home_team': home_team,
            'away_team': away_team,
            'match_date': match_date,
            'match_time': '',
            'predicted_winner': predicted_winner,
            'predicted_score': predicted_score,
            'predicted_probability': str(enhanced_pred.get('confidence', '')),
            'over_under': '大球' if enhanced_pred.get('over_under', {}).get('over', 0) > 0.5 else '小球',
            'correct': False,
            'model_predictions': enhanced_pred.get('model_predictions', {}),
            'full_prediction': enhanced_pred,
            'saved_at': datetime.now().isoformat()
        }
        
        # 检查是否已存在
        existing_idx = None
        for idx, pred in enumerate(predictions):
            if pred.get('match_id') == match_id:
                existing_idx = idx
                break
        
        if existing_idx is not None:
            predictions[existing_idx] = prediction_data
        else:
            predictions.append(prediction_data)
        
        with open(self.predictions_file, 'w', encoding='utf-8') as f:
            json.dump(predictions, f, ensure_ascii=False, indent=2)
        
        logger.info(f"保存预测: {match_id}")
        return prediction_data


def print_accuracy_report(stats: Dict):
    """打印准确率报告"""
    print("\n" + "=" * 80)
    print("📊 预测准确率统计报告")
    print("=" * 80)
    
    overall = stats['overall']
    print(f"\n【总体统计】")
    print(f"  总预测数: {overall['total_predictions']}")
    print(f"  正确预测: {overall['correct_predictions']}")
    print(f"  胜负准确率: {overall['win_accuracy']}%")
    if overall.get('total_score_predictions', 0) > 0:
        print(f"  比分准确率: {overall['score_accuracy']}% ({overall['correct_score_predictions']}/{overall['total_score_predictions']})")
    
    print(f"\n【各联赛统计】")
    for league_code, league_stats in stats['by_league'].items():
        league_name = LEAGUE_NAMES.get(league_code, league_code)
        if league_stats['total_predictions'] > 0:
            print(f"  {league_name}: {league_stats['win_accuracy']}% ({league_stats['correct_predictions']}/{league_stats['total_predictions']})")
        else:
            print(f"  {league_name}: 暂无数据")
    
    print(f"\n【最后更新】: {stats.get('last_updated', '未知')}")
    print("=" * 80 + "\n")


def interactive_update():
    """交互式更新比赛结果"""
    manager = ResultManager()
    
    print("=" * 80)
    print("🏆 比赛结果更新系统")
    print("=" * 80)
    
    while True:
        print("\n请选择操作:")
        print("1. 查看待更新的比赛")
        print("2. 输入比赛结果")
        print("3. 查看准确率统计")
        print("4. 重新计算准确率")
        print("0. 退出")
        
        choice = input("\n请输入选项: ").strip()
        
        if choice == '0':
            print("👋 再见!")
            break
        
        elif choice == '1':
            pending = manager.get_pending_matches()
            print(f"\n📋 待更新结果的比赛 (共{len(pending)}场):")
            if not pending:
                print("  暂无待更新的比赛")
            else:
                for idx, pred in enumerate(pending[:20], 1):
                    print(f"\n  {idx}. {pred['league_name']} - {pred['match_date']}")
                    print(f"     {pred['home_team']} vs {pred['away_team']}")
                    pred_result = pred.get('predicted_winner', '')
                    result_text = {'home': '主胜', 'away': '客胜', 'draw': '平局'}.get(pred_result, pred_result)
                    print(f"     预测: {result_text} (ID: {pred['match_id']})")
                if len(pending) > 20:
                    print(f"\n  ... 还有 {len(pending) - 20} 场")
        
        elif choice == '2':
            pending = manager.get_pending_matches()
            
            if not pending:
                print("⚠️  暂无待更新的比赛")
                continue
            
            print("\n📋 请选择要更新的比赛:")
            for idx, pred in enumerate(pending[:20], 1):
                print(f"  {idx}. {pred['home_team']} vs {pred['away_team']} ({pred['league_name']})")
            
            match_idx = input("\n请输入序号 (或直接输入比赛ID): ").strip()
            
            match_id = None
            if match_idx.isdigit():
                idx = int(match_idx) - 1
                if 0 <= idx < len(pending):
                    match_id = pending[idx]['match_id']
            else:
                match_id = match_idx
            
            if not match_id:
                print("❌ 无效输入")
                continue
            
            # 查找比赛信息
            pred_info = None
            for pred in pending:
                if pred['match_id'] == match_id:
                    pred_info = pred
                    break
            
            if not pred_info:
                # 从所有预测中查找
                all_preds = manager.load_predictions()
                for pred in all_preds:
                    if pred['match_id'] == match_id:
                        pred_info = pred
                        break
            
            if pred_info:
                print(f"\n更新比赛: {pred_info['home_team']} vs {pred_info['away_team']}")
                
                home_score = input("主队进球: ").strip()
                away_score = input("客队进球: ").strip()
                
                if home_score.isdigit() and away_score.isdigit():
                    manager.save_result(match_id, int(home_score), int(away_score))
                    manager.update_accuracy_stats()
                    print("✅ 结果已保存!")
                else:
                    print("❌ 无效的比分输入")
            else:
                print(f"❌ 找不到比赛: {match_id}")
        
        elif choice == '3':
            try:
                with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                    stats = json.load(f)
                print_accuracy_report(stats)
            except Exception as e:
                print(f"❌ 读取统计失败: {e}")
        
        elif choice == '4':
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
        
        else:
            print("❌ 无效选项")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='比赛结果管理和准确率更新')
    parser.add_argument('--interactive', '-i', action='store_true', help='进入交互模式')
    parser.add_argument('--update-accuracy', '-u', action='store_true', help='更新准确率统计')
    parser.add_argument('--show-accuracy', '-s', action='store_true', help='显示准确率统计')
    
    args = parser.parse_args()
    
    manager = ResultManager()
    
    if args.interactive:
        interactive_update()
    elif args.update_accuracy:
        stats = manager.update_accuracy_stats()
        print_accuracy_report(stats)
    elif args.show_accuracy:
        try:
            with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                stats = json.load(f)
            print_accuracy_report(stats)
        except Exception as e:
            print(f"❌ 读取统计失败: {e}")
    else:
        interactive_update()


if __name__ == '__main__':
    main()

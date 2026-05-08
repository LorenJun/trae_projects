"""模块说明：负责样本比赛挑选与预测报告格式化输出。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


class PredictionReportService:
    def __init__(self, league_config: Dict[str, Dict[str, Any]]):
        self.league_config = league_config

    def get_sample_matches(self, league_code: str) -> List[Dict[str, Any]]:
        teams = self.league_config[league_code]['teams']
        matches: List[Dict[str, Any]] = []
        for i in range(0, min(len(teams), 10), 2):
            if i + 1 < len(teams):
                matches.append({'home_team': teams[i], 'away_team': teams[i + 1]})
        return matches

    def format_report(self, league_code: str, match_date: str, predictions: List[Dict[str, Any]]) -> str:
        league_name = self.league_config[league_code]['name']
        report = f"# {league_name} 联赛预测分析报告\n"
        report += f"\n**预测日期**: {match_date}\n"
        report += f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report += f"**预测场次**: {len(predictions)} 场\n"
        report += "\n" + "=" * 80 + "\n"
        report += "## 比赛预测详情\n"

        for pred in predictions:
            home = pred['home_team']
            away = pred['away_team']
            report += f"\n### {home} vs {away}\n"
            report += f"\n**预测结果**: {pred['prediction']} (信心: {pred['confidence']:.1%})\n"
            report += "\n**概率分布**:\n"
            for outcome in ('主胜', '平局', '客胜'):
                prob = pred['all_probabilities'].get(outcome, 0.0)
                bar = '#' * int(prob * 50)
                report += f"  {outcome}: {prob:6.1%} {bar}\n"

            report += "\n**最可能比分**:\n"
            for score, prob in pred['top_scores']:
                report += f"  {score}: {prob:.1%}\n"

            ou = pred['over_under']
            if isinstance(ou, dict) and ou.get('available'):
                ou_line = ou.get('line')
                line_label = f"{ou_line:g}" if isinstance(ou_line, (int, float)) else '?'
                report += f"\n**大小球分析** ({line_label}球):\n"
                report += f"  大球: {ou['over']:.1%} | 小球: {ou['under']:.1%}\n"
                report += f"  预期总进球: {ou['total_lambda']:.2f}\n"
                report += f"  盘口来源: {ou.get('line_source', 'unknown')}\n"
            else:
                report += "\n**大小球分析**:\n"
                report += "  待补真实盘口，当前不输出正式大小球结论。\n"

            hs = pred['home_strength']
            aws = pred['away_strength']
            report += "\n**实力对比**:\n"
            report += f"  {home}: 实力={hs['strength']:.1f} 进攻={hs['attack']:.2f} 防守={hs['defense']:.2f} "
            if hs['injured_count'] > 0:
                report += f"伤病={hs['injured_count']}人"
            report += "\n"
            report += f"  {away}: 实力={aws['strength']:.1f} 进攻={aws['attack']:.2f} 防守={aws['defense']:.2f} "
            if aws['injured_count'] > 0:
                report += f"伤病={aws['injured_count']}人"
            report += "\n"

            upset = pred['upset_potential']
            report += f"\n**爆冷分析**: {upset['warning_level']} {upset['level']} (指数: {upset['index']:.0f})\n"
            if upset['factors']:
                report += f"  风险因素: {', '.join(upset['factors'])}\n"

            odds_ref = pred.get('historical_odds_reference', {})
            if odds_ref.get('available'):
                summary = odds_ref.get('summary', {})
                report += "\n**历史赔率参考**:\n"
                report += (
                    f"  相似样本: {summary.get('sample_size', 0)} 场 | "
                    f"主胜: {summary.get('result_rates', {}).get('主胜', 0.0):.1%} | "
                    f"平局: {summary.get('result_rates', {}).get('平局', 0.0):.1%} | "
                    f"客胜: {summary.get('result_rates', {}).get('客胜', 0.0):.1%}\n"
                )
                report += f"  高赔赛果占比: {summary.get('cold_result_rate', 0.0):.1%}\n"
                if odds_ref.get('insights'):
                    report += f"  参考结论: {'；'.join(odds_ref['insights'])}\n"
                for similar in odds_ref.get('similar_matches', [])[:3]:
                    report += (
                        f"  - {similar['match_date']} {similar['home_team']} vs {similar['away_team']} "
                        f"=> {similar['actual_result']} ({similar['actual_score']}) [相似度 {similar['similarity']:.2f}]\n"
                    )

            rag_explanation = str(pred.get('retrieved_memory_explanation') or '').strip()
            rag_memory = pred.get('retrieved_memory', {}) if isinstance(pred.get('retrieved_memory'), dict) else {}
            if rag_explanation:
                report += f"\n**RAG记忆解释**:\n  {rag_explanation}\n"
            if rag_memory.get('similar_cases'):
                report += "  相似记忆样本:\n"
                for similar in rag_memory.get('similar_cases', [])[:3]:
                    report += (
                        f"  - {similar.get('match_date', '-') } {similar.get('home_team', '-')} vs {similar.get('away_team', '-')} "
                        f"=> {similar.get('actual_result', '-') } ({similar.get('actual_score', '-')}) [分数 {float(similar.get('similarity_score') or 0.0):.2f}]\n"
                    )
            if rag_memory.get('market_cases'):
                report += "  盘口记忆样本:\n"
                for similar in rag_memory.get('market_cases', [])[:2]:
                    report += (
                        f"  - {similar.get('match_date', '-') } {similar.get('home_team', '-')} vs {similar.get('away_team', '-')} "
                        f"=> {similar.get('actual_result', '-') } ({similar.get('actual_score', '-')}) [盘口分 {float(similar.get('similarity_score') or 0.0):.2f}]\n"
                    )

            report += "\n" + "-" * 60 + "\n"

        report += "\n" + "=" * 80 + "\n"
        report += "## 预测统计摘要\n"
        high_conf = [p for p in predictions if p['confidence'] >= 0.7]
        med_conf = [p for p in predictions if 0.5 <= p['confidence'] < 0.7]
        low_conf = [p for p in predictions if p['confidence'] < 0.5]
        report += f"\n- **高信心预测** (≥70%): {len(high_conf)} 场\n"
        report += f"- **中等信心预测** (50%-70%): {len(med_conf)} 场\n"
        report += f"- **低信心预测** (<50%): {len(low_conf)} 场\n"

        upset_warnings = [p for p in predictions if p['upset_potential']['level'] == '高']
        if upset_warnings:
            report += "\n## 爆冷警告\n"
            for pred in upset_warnings:
                report += f"\n- {pred['home_team']} vs {pred['away_team']}\n"
                report += f"  风险因素: {', '.join(pred['upset_potential']['factors'])}\n"

        report += "\n" + "=" * 80 + "\n"
        report += "## 使用说明\n\n"
        report += "1. 本报告基于多模型融合预测，仅供参考\n"
        report += "2. 高信心预测（≥70%）可靠性较高\n"
        report += "3. 爆冷警告需特别关注\n"
        report += "4. 预测结果会根据实际比赛结果持续优化\n"
        return report

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
足球比赛预测准确率统计脚本

该脚本用于统计整个赛季的比赛预测准确率，包括：
1. 胜平负预测准确率
2. 大小球预测准确率
3. 让球盘预测准确率
4. 按联赛分类统计
5. 按比赛类型统计
6. 生成详细的统计报告
"""

import os
import re
import pandas as pd
from datetime import datetime

class FootballPredictionAnalyzer:
    """足球预测分析器"""
    
    def __init__(self, base_dir):
        """
        初始化分析器
        :param base_dir: 基础目录路径
        """
        self.base_dir = base_dir
        self.leagues = [
            'premier_league',  # 英超
            'la_liga',          # 西甲
            'serie_a',          # 意甲
            'bundesliga',       # 德甲
            'ligue_1'           # 法甲
        ]
        self.league_names = {
            'premier_league': '英超',
            'la_liga': '西甲',
            'serie_a': '意甲',
            'bundesliga': '德甲',
            'ligue_1': '法甲'
        }
    
    def load_prediction_data(self, league):
        """
        加载预测数据
        :param league: 联赛名称
        :return: 预测数据DataFrame
        """
        prediction_path = os.path.join(
            self.base_dir, league, 'analysis', 'predictions', 'predictions_template.md'
        )
        
        if not os.path.exists(prediction_path):
            print(f"警告：{prediction_path} 文件不存在")
            return pd.DataFrame()
        
        # 读取Markdown文件
        with open(prediction_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取表格数据
        table_match = re.search(r'\|.*?\|(.*?)\n\n', content, re.DOTALL)
        if not table_match:
            print(f"警告：{prediction_path} 中未找到表格数据")
            return pd.DataFrame()
        
        table_content = table_match.group(1)
        lines = table_content.strip().split('\n')
        
        # 解析表头
        header = [col.strip() for col in lines[0].split('|') if col.strip()]
        
        # 解析数据行
        data = []
        for line in lines[1:]:
            if line.strip() and not line.strip().startswith('|'):
                continue
            row = [col.strip() for col in line.split('|') if col.strip()]
            if len(row) == len(header):
                data.append(row)
        
        df = pd.DataFrame(data, columns=header)
        return df
    
    def load_result_data(self, league):
        """
        加载实际结果数据
        :param league: 联赛名称
        :return: 实际结果数据DataFrame
        """
        result_path = os.path.join(
            self.base_dir, league, 'analysis', 'results', 'results_template.md'
        )
        
        if not os.path.exists(result_path):
            print(f"警告：{result_path} 文件不存在")
            return pd.DataFrame()
        
        # 读取Markdown文件
        with open(result_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 提取表格数据
        table_match = re.search(r'\|.*?\|(.*?)\n\n', content, re.DOTALL)
        if not table_match:
            print(f"警告：{result_path} 中未找到表格数据")
            return pd.DataFrame()
        
        table_content = table_match.group(1)
        lines = table_content.strip().split('\n')
        
        # 解析表头
        header = [col.strip() for col in lines[0].split('|') if col.strip()]
        
        # 解析数据行
        data = []
        for line in lines[1:]:
            if line.strip() and not line.strip().startswith('|'):
                continue
            row = [col.strip() for col in line.split('|') if col.strip()]
            if len(row) == len(header):
                data.append(row)
        
        df = pd.DataFrame(data, columns=header)
        return df
    
    def analyze_accuracy(self, league):
        """
        分析预测准确率
        :param league: 联赛名称
        :return: 准确率统计结果
        """
        prediction_df = self.load_prediction_data(league)
        result_df = self.load_result_data(league)
        
        if prediction_df.empty or result_df.empty:
            return {
                'league': league,
                'total_matches': 0,
                'correct_predictions': 0,
                'accuracy': 0.0,
                'home_win_accuracy': 0.0,
                'draw_accuracy': 0.0,
                'away_win_accuracy': 0.0,
                'over_under_accuracy': 0.0,
                'handicap_accuracy': 0.0
            }
        
        # 合并数据
        merged_df = pd.merge(
            prediction_df, 
            result_df, 
            on=['比赛日期', '主队', '客队'], 
            how='inner'
        )
        
        total_matches = len(merged_df)
        if total_matches == 0:
            return {
                'league': league,
                'total_matches': 0,
                'correct_predictions': 0,
                'accuracy': 0.0,
                'home_win_accuracy': 0.0,
                'draw_accuracy': 0.0,
                'away_win_accuracy': 0.0,
                'over_under_accuracy': 0.0,
                'handicap_accuracy': 0.0
            }
        
        # 胜平负预测准确率
        correct_predictions = sum(merged_df['预测结果'] == merged_df['实际结果'])
        accuracy = (correct_predictions / total_matches) * 100
        
        # 主胜预测准确率
        home_win_predictions = merged_df[merged_df['预测结果'] == '主胜']
        if len(home_win_predictions) > 0:
            home_win_correct = sum(home_win_predictions['预测结果'] == home_win_predictions['实际结果'])
            home_win_accuracy = (home_win_correct / len(home_win_predictions)) * 100
        else:
            home_win_accuracy = 0.0
        
        # 平局预测准确率
        draw_predictions = merged_df[merged_df['预测结果'] == '平局']
        if len(draw_predictions) > 0:
            draw_correct = sum(draw_predictions['预测结果'] == draw_predictions['实际结果'])
            draw_accuracy = (draw_correct / len(draw_predictions)) * 100
        else:
            draw_accuracy = 0.0
        
        # 客胜预测准确率
        away_win_predictions = merged_df[merged_df['预测结果'] == '客胜']
        if len(away_win_predictions) > 0:
            away_win_correct = sum(away_win_predictions['预测结果'] == away_win_predictions['实际结果'])
            away_win_accuracy = (away_win_correct / len(away_win_predictions)) * 100
        else:
            away_win_accuracy = 0.0
        
        # 大小球预测准确率
        if '大小球预测' in merged_df.columns and '实际比分' in merged_df.columns:
            def get_over_under_result(row):
                if pd.isna(row['实际比分']):
                    return None
                try:
                    score = row['实际比分'].split('-')
                    if len(score) == 2:
                        total_goals = int(score[0]) + int(score[1])
                        return '大球' if total_goals >= 3 else '小球'
                except:
                    pass
                return None
            
            merged_df['实际大小球'] = merged_df.apply(get_over_under_result, axis=1)
            over_under_df = merged_df[~merged_df['实际大小球'].isna() & ~merged_df['大小球预测'].isna()]
            if len(over_under_df) > 0:
                over_under_correct = sum(over_under_df['大小球预测'] == over_under_df['实际大小球'])
                over_under_accuracy = (over_under_correct / len(over_under_df)) * 100
            else:
                over_under_accuracy = 0.0
        else:
            over_under_accuracy = 0.0
        
        # 让球盘预测准确率（简化计算）
        handicap_accuracy = 0.0  # 这里需要根据具体的让球盘规则进行计算
        
        return {
            'league': league,
            'total_matches': total_matches,
            'correct_predictions': correct_predictions,
            'accuracy': accuracy,
            'home_win_accuracy': home_win_accuracy,
            'draw_accuracy': draw_accuracy,
            'away_win_accuracy': away_win_accuracy,
            'over_under_accuracy': over_under_accuracy,
            'handicap_accuracy': handicap_accuracy
        }
    
    def generate_report(self):
        """
        生成完整的统计报告
        :return: 统计报告
        """
        report = f"# 足球比赛预测准确率统计报告\n\n"
        report += f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        
        # 按联赛统计
        league_stats = []
        for league in self.leagues:
            stats = self.analyze_accuracy(league)
            league_stats.append(stats)
        
        # 生成联赛统计表格
        report += "## 各联赛预测准确率统计\n\n"
        report += "| 联赛 | 比赛总数 | 正确预测 | 准确率 | 主胜准确率 | 平局准确率 | 客胜准确率 | 大小球准确率 | 让球盘准确率 |\n"
        report += "|------|---------|---------|--------|------------|------------|------------|--------------|--------------|\n"
        
        total_matches = 0
        total_correct = 0
        
        for stats in league_stats:
            total_matches += stats['total_matches']
            total_correct += stats['correct_predictions']
            
            report += f"| {self.league_names.get(stats['league'], stats['league'])} | {stats['total_matches']} | {stats['correct_predictions']} | {stats['accuracy']:.2f}% | {stats['home_win_accuracy']:.2f}% | {stats['draw_accuracy']:.2f}% | {stats['away_win_accuracy']:.2f}% | {stats['over_under_accuracy']:.2f}% | {stats['handicap_accuracy']:.2f}% |\n"
        
        # 计算总体准确率
        if total_matches > 0:
            overall_accuracy = (total_correct / total_matches) * 100
        else:
            overall_accuracy = 0.0
        
        report += f"| **总计** | **{total_matches}** | **{total_correct}** | **{overall_accuracy:.2f}%** | - | - | - | - | - |\n\n"
        
        # 生成详细分析
        report += "## 详细分析\n\n"
        
        # 按预测类型分析
        report += "### 预测类型准确率分析\n\n"
        
        # 计算各预测类型的总体准确率
        home_win_total = 0
        home_win_correct = 0
        draw_total = 0
        draw_correct = 0
        away_win_total = 0
        away_win_correct = 0
        over_under_total = 0
        over_under_correct = 0
        
        for league in self.leagues:
            prediction_df = self.load_prediction_data(league)
            result_df = self.load_result_data(league)
            
            if not prediction_df.empty and not result_df.empty:
                merged_df = pd.merge(
                    prediction_df, 
                    result_df, 
                    on=['比赛日期', '主队', '客队'], 
                    how='inner'
                )
                
                # 主胜预测
                home_win_predictions = merged_df[merged_df['预测结果'] == '主胜']
                home_win_total += len(home_win_predictions)
                home_win_correct += sum(home_win_predictions['预测结果'] == home_win_predictions['实际结果'])
                
                # 平局预测
                draw_predictions = merged_df[merged_df['预测结果'] == '平局']
                draw_total += len(draw_predictions)
                draw_correct += sum(draw_predictions['预测结果'] == draw_predictions['实际结果'])
                
                # 客胜预测
                away_win_predictions = merged_df[merged_df['预测结果'] == '客胜']
                away_win_total += len(away_win_predictions)
                away_win_correct += sum(away_win_predictions['预测结果'] == away_win_predictions['实际结果'])
                
                # 大小球预测
                if '大小球预测' in merged_df.columns and '实际比分' in merged_df.columns:
                    def get_over_under_result(row):
                        if pd.isna(row['实际比分']):
                            return None
                        try:
                            score = row['实际比分'].split('-')
                            if len(score) == 2:
                                total_goals = int(score[0]) + int(score[1])
                                return '大球' if total_goals >= 3 else '小球'
                        except:
                            pass
                        return None
                    
                    merged_df['实际大小球'] = merged_df.apply(get_over_under_result, axis=1)
                    over_under_df = merged_df[~merged_df['实际大小球'].isna() & ~merged_df['大小球预测'].isna()]
                    over_under_total += len(over_under_df)
                    over_under_correct += sum(over_under_df['大小球预测'] == over_under_df['实际大小球'])
        
        # 计算各类型准确率
        home_win_acc = (home_win_correct / home_win_total * 100) if home_win_total > 0 else 0
        draw_acc = (draw_correct / draw_total * 100) if draw_total > 0 else 0
        away_win_acc = (away_win_correct / away_win_total * 100) if away_win_total > 0 else 0
        over_under_acc = (over_under_correct / over_under_total * 100) if over_under_total > 0 else 0
        
        report += "| 预测类型 | 预测次数 | 正确次数 | 准确率 |\n"
        report += "|---------|---------|---------|--------|\n"
        report += f"| 主胜预测 | {home_win_total} | {home_win_correct} | {home_win_acc:.2f}% |\n"
        report += f"| 平局预测 | {draw_total} | {draw_correct} | {draw_acc:.2f}% |\n"
        report += f"| 客胜预测 | {away_win_total} | {away_win_correct} | {away_win_acc:.2f}% |\n"
        report += f"| 大小球预测 | {over_under_total} | {over_under_correct} | {over_under_acc:.2f}% |\n\n"
        
        # 生成改进建议
        report += "## 改进建议\n\n"
        
        # 分析各联赛的表现
        for stats in league_stats:
            if stats['total_matches'] > 0:
                report += f"### {self.league_names.get(stats['league'], stats['league'])}\n"
                report += f"- 准确率：{stats['accuracy']:.2f}%\n"
                
                # 分析薄弱环节
                weak_points = []
                if stats['home_win_accuracy'] < 60:
                    weak_points.append('主胜预测')
                if stats['draw_accuracy'] < 50:
                    weak_points.append('平局预测')
                if stats['away_win_accuracy'] < 60:
                    weak_points.append('客胜预测')
                if stats['over_under_accuracy'] < 60:
                    weak_points.append('大小球预测')
                
                if weak_points:
                    report += f"- 需要改进的方面：{', '.join(weak_points)}\n"
                else:
                    report += "- 各方面表现均衡，继续保持\n"
                report += "\n"
        
        # 总体建议
        report += "### 总体建议\n"
        if overall_accuracy < 60:
            report += "- 建议加强数据收集和分析，特别是球队状态和伤病信息\n"
            report += "- 考虑调整模型权重，优化预测算法\n"
        elif overall_accuracy < 70:
            report += "- 建议进一步优化模型参数，提高预测准确性\n"
            report += "- 加强对特殊比赛（如德比战、保级战）的分析\n"
        else:
            report += "- 预测表现良好，建议继续保持并尝试进一步优化\n"
        
        return report
    
    def save_report(self, report):
        """
        保存统计报告
        :param report: 统计报告内容
        """
        report_path = os.path.join(self.base_dir, 'prediction_accuracy_report.md')
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"报告已保存到：{report_path}")

if __name__ == '__main__':
    # 初始化分析器
    base_dir = 'europe_leagues'
    analyzer = FootballPredictionAnalyzer(base_dir)
    
    # 生成报告
    report = analyzer.generate_report()
    
    # 保存报告
    analyzer.save_report(report)
    
    # 打印报告
    print(report)
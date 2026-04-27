#!/usr/bin/env python3
"""
预测流程演示脚本
展示完整的预测流程
"""

from enhanced_prediction_workflow import EnhancedPredictor, LEAGUE_CONFIG

def main():
    print("="*80)
    print("🏆 足球预测系统 - 完整流程演示")
    print("="*80)
    print()
    
    # 初始化预测器
    print("📦 步骤1: 初始化预测器...")
    predictor = EnhancedPredictor()
    print("✅ 初始化完成！")
    print()
    
    # 示例1: 单场预测
    print("🎯 步骤2: 单场比赛预测（切尔西 vs 曼联）...")
    print()
    
    match_result = predictor.predict_match(
        home_team='切尔西',
        away_team='曼联',
        league_code='premier_league',
        match_date='2026-04-20'
    )
    
    print("="*80)
    print("📊 预测结果详情")
    print("="*80)
    
    print()
    print("【基本信息】")
    print(f"  主队: {match_result['home_team']}")
    print(f"  客队: {match_result['away_team']}")
    print(f"  联赛: {match_result['league_name']}")
    print()
    
    print("【核心预测】")
    print(f"  预测结果: {match_result['prediction']}")
    print(f"  信心指数: {match_result['confidence']:.1%}")
    print()
    
    print("【概率分布】")
    probs = match_result.get('all_probabilities')
    if not isinstance(probs, dict):
        fp = match_result.get('final_probabilities') or {}
        probs = {
            '主胜': float(fp.get('home_win', 0) or 0),
            '平局': float(fp.get('draw', 0) or 0),
            '客胜': float(fp.get('away_win', 0) or 0),
        }
    for outcome, prob in probs.items():
        try:
            p = float(prob)
        except Exception:
            p = 0.0
        bar = "█" * int(p * 50)
        print(f"  {outcome}: {p:6.1%} {bar}")
    print()
    
    print("【最可能比分】")
    for i, (score, prob) in enumerate(match_result['top_scores'][:3], 1):
        print(f"  {i}. {score} - {prob:.1%}")
    print()
    
    print("【大小球分析】")
    ou = match_result['over_under']
    print(f"  大球概率: {ou['over']:.1%}")
    print(f"  小球概率: {ou['under']:.1%}")
    print(f"  预期总进球: {ou['total_lambda']:.2f}")
    if isinstance(ou.get('line'), (int, float)):
        print(f"  盘口线: {ou['line']:g} (source={ou.get('line_source', '-')})")
    market = ou.get('market') if isinstance(ou, dict) else None
    if isinstance(market, dict) and isinstance(market.get('final'), dict) and market.get('final'):
        mf = market['final']
        print(f"  水位(最终): 大 {mf.get('over')} / 盘 {mf.get('line')} / 小 {mf.get('under')}")
    print()
    
    print("【实力对比】")
    hs = match_result['home_strength']
    aws = match_result['away_strength']
    print(f"  {match_result['home_team']}:")
    print(f"    实力值: {hs['strength']:.1f}")
    print(f"    进攻力: {hs['attack']:.2f}")
    print(f"    防守力: {hs['defense']:.2f}")
    print(f"    伤病数: {hs['injured_count']}人")
    print(f"  {match_result['away_team']}:")
    print(f"    实力值: {aws['strength']:.1f}")
    print(f"    进攻力: {aws['attack']:.2f}")
    print(f"    防守力: {aws['defense']:.2f}")
    print(f"    伤病数: {aws['injured_count']}人")
    print()
    
    print("【爆冷分析】")
    upset = match_result['upset_potential']
    print(f"  风险等级: {upset['warning_level']} {upset['level']}")
    print(f"  风险指数: {upset['index']:.0f}")
    print(f"  影响因素: {', '.join(upset['factors'])}")
    print()
    
    print("【模型预测详情】")
    models = match_result.get('model_predictions', {})
    for model_name, pred in list(models.items())[:5]:
        if isinstance(pred, dict):
            home_prob = pred.get('home_win', 0)
            away_prob = pred.get('away_win', 0)
            print(f"  {model_name}: ")
            print(f"    主胜: {home_prob:.1%}, 客胜: {away_prob:.1%}")
    print("  ...（还有5个模型预测）")
    print()
    
    print("="*80)
    print("🔄 完整预测流程图")
    print("="*80)
    print()
    print("  1️⃣  输入比赛信息 → 2️⃣  10种模型同时预测 → 3️⃣  多模型融合 → ")
    print("  4️⃣  生成预测结果 → 5️⃣  实力分析 → 6️⃣  爆冷分析 → ")
    print("  7️⃣  生成报告 → 8️⃣  保存到历史数据库 → 9️⃣  更新准确率统计")
    print()
    
    print("="*80)
    print("🚀 下一步操作")
    print("="*80)
    print()
    print("  1. 运行单场预测:  cd europe_leagues && python3 prediction_system.py predict-match --league premier_league --home-team 切尔西 --away-team 曼联 --date 2026-04-20 --json")
    print("  2. 查看写回结果:  打开 europe_leagues/premier_league/teams_2025-26.md 的赛程表备注列")
    print("  3. 回填赛果/更新准确率: cd europe_leagues && python3 prediction_system.py save-result ... --json && python3 prediction_system.py accuracy --refresh --json")
    print()
    print("="*80)
    print("✅ 演示完成！")
    print("="*80)

if __name__ == '__main__':
    main()

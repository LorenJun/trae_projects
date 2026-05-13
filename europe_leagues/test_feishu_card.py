#!/usr/bin/env python3
"""
测试飞书卡片消息
演示精美的预测结果卡片效果
"""

import json
from feishu_enhanced_cards import FeishuEnhancedCardBuilder, FeishuEnhancedSender

# 测试数据 - 2026-05-13 西甲三场比赛
test_matches = [
    {
        'match_id': '1302903',
        'home_team': '塞尔塔',
        'away_team': '莱万特',
        'prediction': '主胜',
        'confidence': 0.3825,
        'all_probabilities': {'主胜': 0.3825, '平局': 0.3371, '客胜': 0.2804},
        'expected_goals': {'home': 1.50, 'away': 0.86, 'total': 2.36},
        'top_scores': [['1-0', 0.148], ['2-0', 0.111], ['2-1', 0.091]],
        'over_under': {'line': 2.75, 'under': 0.6063, 'over': 0.3937},
        'upset_potential': {'level': '低', 'index': 26.0, 'factors': ['历史同向反打(6次)', '相似赔率反向结果偏多(80%)']},
        'market_snapshot': {
            '欧赔': {'final': {'home': 1.80, 'draw': 3.71, 'away': 4.11}},
            '亚值': {'final': {'handicap_text': '半球', 'home_water': 2.01, 'away_water': 2.09}}
        }
    },
    {
        'match_id': '1302902',
        'home_team': '皇家贝蒂斯',
        'away_team': '埃尔切',
        'prediction': '主胜',
        'confidence': 0.3899,
        'all_probabilities': {'主胜': 0.3899, '平局': 0.3504, '客胜': 0.2597},
        'expected_goals': {'home': 1.72, 'away': 0.85, 'total': 2.56},
        'top_scores': [['1-0', 0.139], ['2-0', 0.119], ['2-1', 0.096]],
        'over_under': {'line': 3.0, 'under': 0.6866, 'over': 0.3134},
        'upset_potential': {'level': '低', 'index': 26.0, 'factors': ['历史同向反打(6次)', '相似赔率反向结果偏多(80%)']},
        'market_snapshot': {
            '欧赔': {'final': {'home': 1.59, 'draw': 4.18, 'away': 5.12}},
            '亚值': {'final': {'handicap_text': '一球', 'home_water': 2.02, 'away_water': 1.90}}
        }
    },
    {
        'match_id': '1302907',
        'home_team': '奥萨苏纳',
        'away_team': '马德里竞技',
        'prediction': '主胜',
        'confidence': 0.3571,
        'all_probabilities': {'主胜': 0.3571, '平局': 0.3504, '客胜': 0.2924},
        'expected_goals': {'home': 1.19, 'away': 1.06, 'total': 2.25},
        'top_scores': [['1-1', 0.126], ['1-0', 0.132], ['0-1', 0.116]],
        'over_under': {'line': 2.5, 'under': 0.6027, 'over': 0.3973},
        'upset_potential': {'level': '低', 'index': 26.0, 'factors': ['历史同向反打(6次)', '相似赔率反向结果偏多(80%)']},
        'market_snapshot': {
            '欧赔': {'final': {'home': 2.52, 'draw': 3.43, 'away': 2.63}},
            '亚值': {'final': {'handicap_text': '平手', 'home_water': 1.92, 'away_water': 2.01}}
        }
    }
]

def main():
    """主函数"""
    print("=" * 60)
    print("🏆 飞书精美预测卡片生成器")
    print("=" * 60)
    
    # 生成卡片
    print("\n📋 正在生成预测卡片...")
    card = FeishuEnhancedCardBuilder.build_enhanced_prediction_card(
        match_date='2026-05-13',
        league_name='西甲',
        matches=test_matches
    )
    
    # 保存到文件
    output_file = '/tmp/feishu_prediction_card.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    
    print(f"✅ 卡片已保存到: {output_file}")
    
    # 打印卡片结构概览
    print("\n📊 卡片结构概览:")
    print(f"  - 标题: {card['header']['title']['content']}")
    print(f"  - 副标题: {card['header']['subtitle']['content']}")
    print(f"  - 元素数量: {len(card['elements'])}")
    print(f"  - 宽屏模式: {card['config']['wide_screen_mode']}")
    
    # 统计信息
    print("\n📈 预测统计:")
    print(f"  - 比赛数量: {len(test_matches)}")
    print(f"  - 主胜预测: {sum(1 for m in test_matches if m['prediction'] == '主胜')} 场")
    print(f"  - 平局预测: {sum(1 for m in test_matches if m['prediction'] == '平局')} 场")
    print(f"  - 客胜预测: {sum(1 for m in test_matches if m['prediction'] == '客胜')} 场")
    
    avg_confidence = sum(m['confidence'] for m in test_matches) / len(test_matches) * 100
    print(f"  - 平均信心度: {avg_confidence:.1f}%")
    
    # 打印JSON预览
    print("\n📝 卡片JSON预览 (前2000字符):")
    card_json = json.dumps(card, ensure_ascii=False, indent=2)
    print(card_json[:2000])
    print("\n... (truncated)")
    
    # 尝试发送（如果有webhook）
    import os
    webhook = os.environ.get('FEISHU_WEBHOOK_URL')
    if webhook:
        print(f"\n📤 检测到Webhook，正在发送...")
        sender = FeishuEnhancedSender(webhook)
        success = sender.send_card(card)
        if success:
            print("✅ 卡片发送成功！")
        else:
            print("❌ 卡片发送失败")
    else:
        print("\n⚠️ 未设置FEISHU_WEBHOOK_URL环境变量")
        print("💡 如需发送卡片，请设置环境变量:")
        print("   export FEISHU_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'")
    
    print("\n" + "=" * 60)
    print("✨ 卡片特性:")
    print("  ✅ 使用note卡片高亮预测结果")
    print("  ✅ emoji图标增强可读性")
    print("  ✅ 进度条样式显示概率分布")
    print("  ✅ 双列布局展示关键数据")
    print("  ✅ 颜色编码区分不同结果")
    print("  ✅ 支持宽屏模式")
    print("  ✅ 头部统计概览")
    print("  ✅ 风险提示高亮")
    print("=" * 60)

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
足球预测系统 - 统一入口
提供多个预测系统的选择和执行
"""

import os
import sys
from datetime import datetime, timedelta
import argparse

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

def print_header():
    """打印系统标题"""
    print("="*80)
    print("⚽ 足球比赛预测系统 ⚽")
    print("="*80)
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

def list_leagues():
    """列出可用联赛"""
    from enhanced_prediction_workflow import LEAGUE_CONFIG
    print("📋 可用联赛:")
    for code, config in LEAGUE_CONFIG.items():
        print(f"  - {config['name']} ({code})")
    print()

def run_enhanced_system(league_code=None, days=3):
    """运行增强版预测系统"""
    print("🚀 启动增强版预测系统...")
    print()
    
    try:
        from enhanced_prediction_workflow import EnhancedPredictor, LEAGUE_CONFIG
        
        predictor = EnhancedPredictor()
        
        if league_code:
            leagues = [league_code] if league_code in LEAGUE_CONFIG else []
        else:
            leagues = list(LEAGUE_CONFIG.keys())
        
        if not leagues:
            print(f"❌ 无效的联赛代码: {league_code}")
            return
        
        base_date = datetime.now()
        updated_files = []
        
        for l_code in leagues:
            print(f"📊 处理 {LEAGUE_CONFIG[l_code]['name']}...")
            
            for day_offset in range(days):
                match_date = (base_date + timedelta(days=day_offset)).strftime('%Y-%m-%d')
                print(f"  📅 生成 {match_date} 的预测...")
                
                teams_file = predictor.generate_prediction_report(l_code, match_date)
                if teams_file:
                    print(f"  ✅ 已更新 {os.path.basename(teams_file)}")
                    updated_files.append(teams_file)
        
        print()
        print("="*80)
        print(f"🎉 完成！共更新 {len(updated_files)} 个 teams_2025-26.md 文件")
        print("="*80)
        
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()

def run_original_system(league_code=None):
    """运行原始版预测系统"""
    print("📦 启动原始版预测系统...")
    print()
    
    try:
        from optimized_prediction_workflow import main as original_main
        original_main()
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()

def run_ml_test():
    """测试机器学习模型"""
    print("🤖 测试多模型融合系统...")
    print()
    
    try:
        from ml_prediction_models import MultiModelFusion
        
        fusion = MultiModelFusion()
        
        # 测试比赛
        result = fusion.predict(
            home_team='切尔西',
            away_team='曼联',
            home_strength=65,
            away_strength=70,
            home_form=3,
            away_form=4,
            home_injuries=2,
            away_injuries=3,
            h2h_home_wins=2,
            h2h_away_wins=3,
            h2h_draws=1,
            home_motivation=80,
            away_motivation=85,
            home_xg=1.5,
            away_xg=1.8,
            home_attack=1.2,
            home_defense=0.9,
            away_attack=1.4,
            away_defense=0.8
        )
        
        print("="*60)
        print("多模型融合预测结果")
        print("="*60)
        
        final = result['final']
        print(f"\n最终预测:")
        print(f"  主胜概率: {final['home_win']:.1%}")
        print(f"  平局概率: {final['draw']:.1%}")
        print(f"  客胜概率: {final['away_win']:.1%}")
        
        print(f"\n预期进球:")
        print(f"  主队: {result['home_lambda']:.2f}")
        print(f"  客队: {result['away_lambda']:.2f}")
        
        print("\n各模型预测详情:")
        for model_name, prediction in result['all_models'].items():
            print(f"  {model_name}: 主胜{prediction['home_win']:.1%}, "
                  f"平局{prediction['draw']:.1%}, 客胜{prediction['away_win']:.1%}")
        
        print()
        print("="*60)
        print("✅ 测试完成！")
        print("="*60)
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()

def show_interactive_menu():
    """显示交互式菜单"""
    while True:
        print()
        print("╔" + "═"*50 + "╗")
        print("║" + " "*10 + "🏆 足球预测系统主菜单" + " "*17 + "║")
        print("╚" + "═"*50 + "╝")
        print()
        print("【预测功能】")
        print("  1. 运行增强版预测系统（推荐）")
        print("  2. 运行原始版预测系统")
        print("  3. 测试机器学习模型")
        print()
        print("【结果管理】")
        print("  4. 结果管理（录入比赛结果）")
        print("  5. 查看准确率统计")
        print("  6. 更新准确率统计")
        print()
        print("【其他功能】")
        print("  7. 列出可用联赛")
        print()
        print("  0. 退出")
        print()

        choice = input("请输入选项 (0-7): ").strip()

        if choice == '1':
            list_leagues()
            league_input = input("请输入联赛代码（留空则处理所有联赛）: ").strip()
            league_code = league_input if league_input else None

            days_input = input("请输入预测天数（默认3天）: ").strip()
            days = int(days_input) if days_input.isdigit() else 3

            run_enhanced_system(league_code, days)

        elif choice == '2':
            run_original_system()

        elif choice == '3':
            run_ml_test()

        elif choice == '4':
            from result_manager import interactive_update
            interactive_update()

        elif choice == '5':
            from result_manager import ResultManager, print_accuracy_report
            manager = ResultManager()
            try:
                with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                    import json
                    stats = json.load(f)
                    print_accuracy_report(stats)
            except Exception as e:
                stats = manager.update_accuracy_stats()
                print_accuracy_report(stats)

        elif choice == '6':
            from result_manager import ResultManager, print_accuracy_report
            manager = ResultManager()
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)

        elif choice == '7':
            list_leagues()

        elif choice == '0':
            print("👋 再见！")
            break

        else:
            print("❌ 无效选项，请重试")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='足球比赛预测系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用示例:
  %(prog)s                    # 交互式菜单
  %(prog)s enhanced           # 运行增强版系统
  %(prog)s enhanced -l premier_league -d 7  # 指定联赛和天数
  %(prog)s original           # 运行原始版系统
  %(prog)s ml-test            # 测试机器学习模型
  %(prog)s results            # 结果管理交互模式
  %(prog)s show-accuracy      # 显示准确率统计
  %(prog)s update-accuracy    # 更新准确率统计
        '''
    )

    subparsers = parser.add_subparsers(title='子命令', dest='command')

    # 增强版系统
    parser_enhanced = subparsers.add_parser('enhanced', help='运行增强版预测系统')
    parser_enhanced.add_argument('-l', '--league', help='指定联赛代码')
    parser_enhanced.add_argument('-d', '--days', type=int, default=3, help='预测天数（默认3）')

    # 原始版系统
    subparsers.add_parser('original', help='运行原始版预测系统')

    # ML测试
    subparsers.add_parser('ml-test', help='测试机器学习模型')
    
    # 结果管理
    subparsers.add_parser('results', help='结果管理交互式菜单')
    subparsers.add_parser('show-accuracy', help='显示准确率统计')
    subparsers.add_parser('update-accuracy', help='更新准确率统计')

    # 解析参数
    args = parser.parse_args()

    # 切换工作目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    print_header()

    if args.command == 'enhanced':
        run_enhanced_system(args.league, args.days)
    elif args.command == 'original':
        run_original_system()
    elif args.command == 'ml-test':
        run_ml_test()
    elif args.command == 'results':
        from result_manager import interactive_update
        interactive_update()
    elif args.command == 'show-accuracy':
        from result_manager import ResultManager, print_accuracy_report
        manager = ResultManager()
        try:
            with open(manager.accuracy_file, 'r', encoding='utf-8') as f:
                import json
                stats = json.load(f)
                print_accuracy_report(stats)
        except Exception as e:
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
    elif args.command == 'update-accuracy':
        from result_manager import ResultManager, print_accuracy_report
        manager = ResultManager()
        stats = manager.update_accuracy_stats()
        print_accuracy_report(stats)
    else:
        show_interactive_menu()

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
足球预测系统 - 统一入口
提供交互模式和面向自动化编排的 CLI 子命令。
"""

import argparse
import asyncio
import contextlib
import io
import importlib.util
import json
import os
import shutil
import sys
from datetime import datetime, timedelta

# 添加项目路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def print_header():
    """打印系统标题"""
    print("=" * 80)
    print("⚽ 足球比赛预测系统 ⚽")
    print("=" * 80)
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()


def add_json_flag(parser):
    parser.add_argument("--json", action="store_true", help="输出 JSON，便于 openclaw/脚本调用")


def emit_response(payload, as_json=False):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if isinstance(payload, str):
        print(payload)
        return

    print(json.dumps(payload, ensure_ascii=False, indent=2))


def run_quietly(func):
    """捕获 stdout/stderr，避免 JSON 输出被模块日志污染。"""
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
        result = func()
    return result, stdout_buffer.getvalue(), stderr_buffer.getvalue()


def build_json_result(command, data=None, captured_stdout="", captured_stderr="", success=True):
    payload = {
        "success": success,
        "command": command,
        "generated_at": datetime.now().isoformat(),
    }
    if data is not None:
        payload["data"] = data
    if captured_stdout.strip():
        payload["captured_stdout"] = captured_stdout.strip()
    if captured_stderr.strip():
        payload["captured_stderr"] = captured_stderr.strip()
    return payload


def serialize_match_data(match):
    return {
        "match_id": getattr(match, "match_id", "") or "",
        "home_team": match.home_team,
        "away_team": match.away_team,
        "league": match.league,
        "match_date": match.match_date,
        "match_time": match.match_time,
        "status": match.status,
        "score": list(match.score) if match.score else None,
        "odds_data": match.odds_data if getattr(match, "odds_data", None) else None,
        "sources": match.sources or [],
        "update_time": match.update_time,
    }


def get_openclaw_dependency_report():
    repo_root = PROJECT_ROOT
    setup_script = os.path.join(repo_root, "scripts", "setup_openclaw_env.sh")
    requirements_file = os.path.join(repo_root, "requirements.txt")
    openclaw_requirements_file = os.path.join(repo_root, "requirements-openclaw.txt")

    python_version = sys.version_info
    python_ok = python_version >= (3, 9)
    dependency_specs = [
        {"name": "requests", "module": "requests", "required_for": "HTTP 数据抓取"},
        {"name": "playwright", "module": "playwright", "required_for": "浏览器自动化抓取"},
        {"name": "websocket-client", "module": "websocket", "required_for": "实时快照脚本"},
        {"name": "browser-use", "module": "browser_use", "required_for": "真实 user-like 浏览器采集"},
    ]

    dependencies = []
    missing_dependencies = []
    for item in dependency_specs:
        available = importlib.util.find_spec(item["module"]) is not None
        dependencies.append(
            {
                "name": item["name"],
                "module": item["module"],
                "available": available,
                "required_for": item["required_for"],
            }
        )
        if not available:
            missing_dependencies.append(item["name"])

    npm_available = shutil.which("npm") is not None
    install_commands = [
        f"bash {setup_script}",
        f"python3 -m pip install -r {openclaw_requirements_file}",
        "python3 -m playwright install chromium",
    ]

    return {
        "python_version": sys.version.split()[0],
        "python_supported": python_ok,
        "dependencies": dependencies,
        "missing_dependencies": missing_dependencies,
        "requirements_file": requirements_file,
        "openclaw_requirements_file": openclaw_requirements_file,
        "setup_script": setup_script,
        "package_json_exists": os.path.exists(os.path.join(repo_root, "package.json")),
        "npm_setup_optional": npm_available,
        "openclaw_full_ready": python_ok and not missing_dependencies,
        "bootstrap_commands": install_commands,
    }


def validate_leagues(league_code=None):
    from enhanced_prediction_workflow import LEAGUE_CONFIG

    if league_code:
        if league_code not in LEAGUE_CONFIG:
            raise ValueError(f"无效的联赛代码: {league_code}")
        return [league_code], LEAGUE_CONFIG

    return list(LEAGUE_CONFIG.keys()), LEAGUE_CONFIG


def list_leagues_data():
    from enhanced_prediction_workflow import LEAGUE_CONFIG

    return [
        {
            "code": code,
            "name": config["name"],
            "team_count": len(config.get("teams", [])),
            "avg_goals": config.get("avg_goals"),
        }
        for code, config in LEAGUE_CONFIG.items()
    ]


def list_leagues(json_output=False):
    leagues = list_leagues_data()
    if json_output:
        emit_response(build_json_result("list-leagues", leagues), as_json=True)
        return

    print("📋 可用联赛:")
    for league in leagues:
        print(f"  - {league['name']} ({league['code']})")
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
                match_date = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                print(f"  📅 生成 {match_date} 的预测...")
                teams_file = predictor.generate_prediction_report(l_code, match_date)
                if teams_file:
                    print(f"  ✅ 已更新 {os.path.basename(teams_file)}")
                    updated_files.append(teams_file)

        print()
        print("=" * 80)
        print(f"🎉 完成！共更新 {len(updated_files)} 个 teams_2025-26.md 文件")
        print("=" * 80)

    except Exception as e:
        print(f"❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()


def run_original_system():
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
        result = fusion.predict(
            home_team="切尔西",
            away_team="曼联",
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
            away_defense=0.8,
        )

        print("=" * 60)
        print("多模型融合预测结果")
        print("=" * 60)

        final = result["final"]
        print("\n最终预测:")
        print(f"  主胜概率: {final['home_win']:.1%}")
        print(f"  平局概率: {final['draw']:.1%}")
        print(f"  客胜概率: {final['away_win']:.1%}")

        print("\n预期进球:")
        print(f"  主队: {result['home_lambda']:.2f}")
        print(f"  客队: {result['away_lambda']:.2f}")

        print("\n各模型预测详情:")
        for model_name, prediction in result["all_models"].items():
            print(
                f"  {model_name}: 主胜{prediction['home_win']:.1%}, "
                f"平局{prediction['draw']:.1%}, 客胜{prediction['away_win']:.1%}"
            )

        print()
        print("=" * 60)
        print("✅ 测试完成！")
        print("=" * 60)

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


def run_openclaw_list_leagues(json_output):
    if json_output:
        result, captured_stdout, captured_stderr = run_quietly(list_leagues_data)
        emit_response(
            build_json_result("list-leagues", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return
    list_leagues()


def run_openclaw_predict_match(args):
    def _execute():
        from enhanced_prediction_workflow import EnhancedPredictor

        predictor = EnhancedPredictor()
        ctx = None
        if getattr(args, "context_file", ""):
            with open(args.context_file, "r", encoding="utf-8") as f:
                ctx = json.load(f)
        return predictor.predict_match(
            home_team=args.home_team,
            away_team=args.away_team,
            league_code=args.league,
            match_date=args.date,
            match_id=getattr(args, "match_id", "") or "",
            force_refresh_odds=not getattr(args, "no_refresh_odds", False),
            okooo_driver=getattr(args, "okooo_driver", "local-chrome"),
            okooo_headed=bool(getattr(args, "okooo_headed", False)),
            match_time=getattr(args, "match_time", "") or "",
            league_hint=getattr(args, "league_hint", None),
            analysis_context=ctx,
        )

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("predict-match", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"比赛: {result['home_team']} vs {result['away_team']}")
    print(f"联赛: {result['league_name']}  日期: {result['match_date']}")
    print(f"预测: {result['prediction']}  信心: {result['confidence']:.2%}")
    print(f"主胜/平局/客胜: {result['final_probabilities']}")


def run_openclaw_predict_schedule(args):
    def _execute():
        from enhanced_prediction_workflow import EnhancedPredictor

        leagues, league_config = validate_leagues(args.league)
        predictor = EnhancedPredictor()
        base_date = datetime.strptime(args.date, "%Y-%m-%d")
        updates = []

        for league_code in leagues:
            for day_offset in range(args.days):
                match_date = (base_date + timedelta(days=day_offset)).strftime("%Y-%m-%d")
                teams_file = predictor.generate_prediction_report(league_code, match_date)
                updates.append(
                    {
                        "league": league_code,
                        "league_name": league_config[league_code]["name"],
                        "match_date": match_date,
                        "teams_file": teams_file,
                        "updated": bool(teams_file),
                    }
                )
        return updates

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("predict-schedule", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    updates = _execute()
    print("📅 预测写回结果:")
    for item in updates:
        status = "已更新" if item["updated"] else "无数据"
        print(f"- {item['league_name']} {item['match_date']}: {status}")


def run_openclaw_collect_data(args):
    def _execute():
        from data_collector import DataCollector

        collector = DataCollector()
        matches = asyncio.run(collector.collect_league_data(args.league, args.date, use_cache=not args.no_cache))
        return {
            "league": args.league,
            "date": args.date,
            "count": len(matches),
            "matches": [serialize_match_data(match) for match in matches],
            "use_cache": not args.no_cache,
        }

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("collect-data", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"已收集 {result['league']} {result['date']} 比赛 {result['count']} 场")
    for match in result["matches"]:
        print(f"- {match['match_time']} {match['home_team']} vs {match['away_team']} [{match['status']}]")


def run_openclaw_pending_results(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        return manager.get_pending_matches(days_back=args.days_back)

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("pending-results", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    pending = _execute()
    print(f"待更新结果比赛: {len(pending)} 场")
    for item in pending:
        print(f"- {item['match_date']} {item['home_team']} vs {item['away_team']}")


def run_openclaw_save_result(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        return manager.save_result(args.match_id, args.home_score, args.away_score)

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("save-result", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    result = _execute()
    print(f"已保存比分: {result['home_team']} {result['actual_score']} {result['away_team']}")


def run_openclaw_accuracy(args):
    def _execute():
        from result_manager import ResultManager

        manager = ResultManager()
        if args.refresh:
            return manager.update_accuracy_stats()
        with open(manager.accuracy_file, "r", encoding="utf-8") as f:
            return json.load(f)

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("accuracy", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    stats = _execute()
    overall = stats["overall"]
    print(f"总体准确率: {overall['win_accuracy']}% ({overall['correct_predictions']}/{overall['total_predictions']})")


def run_openclaw_health_check(args):
    def _execute():
        report = {
            "cwd": os.getcwd(),
            "python": sys.version.split()[0],
            "timestamp": datetime.now().isoformat(),
        }

        from result_manager import ResultManager
        from data_collector import DataCollector
        from enhanced_prediction_workflow import LEAGUE_CONFIG

        manager = ResultManager()
        collector = DataCollector()
        report["accuracy_file_exists"] = os.path.exists(manager.accuracy_file)
        report["browser_use_available"] = collector.browser_use_available
        report["league_codes"] = list(LEAGUE_CONFIG.keys())
        report["scrapers"] = [scraper.name for scraper in collector.scrapers]
        report["openclaw_dependency_report"] = get_openclaw_dependency_report()
        if not collector.browser_use_available:
            report["recommended_action"] = {
                "owner": "openclaw",
                "reason": "未安装 browser-use，真实浏览器采集链路不可用",
                "install_command": f"bash {os.path.join(PROJECT_ROOT, 'scripts', 'setup_openclaw_env.sh')}",
            }
        return report

    if args.json:
        result, captured_stdout, captured_stderr = run_quietly(_execute)
        emit_response(
            build_json_result("health-check", result, captured_stdout, captured_stderr),
            as_json=True,
        )
        return

    report = _execute()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def run_openclaw_setup_guide(args):
    payload = {
        "project_root": PROJECT_ROOT,
        "venv_path": os.path.join(PROJECT_ROOT, ".venv"),
        "dependency_report": get_openclaw_dependency_report(),
        "next_command": "cd /Users/bytedance/trae_projects/europe_leagues && python3 prediction_system.py health-check --json",
    }
    emit_response(build_json_result("setup-openclaw", payload), as_json=args.json)


def run_harness_list(args):
    from harness import list_pipelines

    payload = list_pipelines()
    if args.json:
        emit_response(build_json_result("harness-list", payload), as_json=True)
        return

    print("可用 Harness Pipelines:")
    for item in payload:
        print(f"- {item['name']}: {item['description']}")


def run_harness_pipeline(args):
    from harness import build_pipeline

    analysis_context = None
    if getattr(args, "context_file", ""):
        with open(args.context_file, "r", encoding="utf-8") as f:
            analysis_context = json.load(f)

    inputs = {
        "league": getattr(args, "league", ""),
        "date": getattr(args, "date", ""),
        "home_team": getattr(args, "home_team", ""),
        "away_team": getattr(args, "away_team", ""),
        "match_id": getattr(args, "match_id", ""),
        "match_time": getattr(args, "match_time", ""),
        "league_hint": getattr(args, "league_hint", ""),
        "okooo_driver": getattr(args, "okooo_driver", "browser-use"),
        "okooo_headed": bool(getattr(args, "okooo_headed", False)),
        "no_refresh_odds": bool(getattr(args, "no_refresh_odds", False)),
        "no_cache": bool(getattr(args, "no_cache", False)),
        "analysis_context": analysis_context,
        "home_score": getattr(args, "home_score", None),
        "away_score": getattr(args, "away_score", None),
        "refresh": bool(getattr(args, "refresh", False)),
    }
    pipeline = build_pipeline(args.pipeline)
    result = pipeline.execute(inputs)

    if args.json:
        emit_response(build_json_result("harness-run", result), as_json=True)
        return

    print(f"Pipeline: {result['pipeline']}")
    print(f"状态: {result['status']}")
    if result["error"]:
        print(f"错误: {result['error']}")
    for stage in result["stages"]:
        print(f"- {stage['stage']}: {stage['status']}")


def show_interactive_menu():
    """显示交互式菜单"""
    while True:
        print()
        print("╔" + "═" * 50 + "╗")
        print("║" + " " * 10 + "🏆 足球预测系统主菜单" + " " * 17 + "║")
        print("╚" + "═" * 50 + "╝")
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
        if choice == "1":
            list_leagues()
            league_input = input("请输入联赛代码（留空则处理所有联赛）: ").strip()
            league_code = league_input if league_input else None
            days_input = input("请输入预测天数（默认3天）: ").strip()
            days = int(days_input) if days_input.isdigit() else 3
            run_enhanced_system(league_code, days)
        elif choice == "2":
            run_original_system()
        elif choice == "3":
            run_ml_test()
        elif choice == "4":
            from result_manager import interactive_update
            interactive_update()
        elif choice == "5":
            from result_manager import ResultManager, print_accuracy_report

            manager = ResultManager()
            try:
                with open(manager.accuracy_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
                    print_accuracy_report(stats)
            except Exception:
                stats = manager.update_accuracy_stats()
                print_accuracy_report(stats)
        elif choice == "6":
            from result_manager import ResultManager, print_accuracy_report

            manager = ResultManager()
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
        elif choice == "7":
            list_leagues()
        elif choice == "0":
            print("👋 再见！")
            break
        else:
            print("❌ 无效选项，请重试")


def build_parser():
    parser = argparse.ArgumentParser(
        description="足球比赛预测系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
常用示例:
  %(prog)s predict-match --league la_liga --home-team 巴塞罗那 --away-team 皇家马德里 --date 2026-05-11 --json
  %(prog)s predict-schedule --league la_liga --date 2026-05-11 --days 2 --json
  %(prog)s collect-data --league la_liga --date 2026-05-11 --json
  %(prog)s pending-results --days-back 14 --json
  %(prog)s save-result --match-id la_liga_20260511_巴塞罗那_皇家马德里 --home-score 2 --away-score 1 --json
  %(prog)s accuracy --refresh --json
  %(prog)s setup-openclaw --json
        """,
    )

    subparsers = parser.add_subparsers(title="子命令", dest="command")

    parser_enhanced = subparsers.add_parser("enhanced", help="运行增强版预测系统")
    parser_enhanced.add_argument("-l", "--league", help="指定联赛代码")
    parser_enhanced.add_argument("-d", "--days", type=int, default=3, help="预测天数（默认3）")

    subparsers.add_parser("original", help="运行原始版预测系统")
    subparsers.add_parser("ml-test", help="测试机器学习模型")
    subparsers.add_parser("results", help="结果管理交互式菜单")
    subparsers.add_parser("show-accuracy", help="显示准确率统计")
    subparsers.add_parser("update-accuracy", help="更新准确率统计")

    parser_list = subparsers.add_parser("list-leagues", help="列出联赛，适合自动化调用")
    add_json_flag(parser_list)

    parser_predict_match = subparsers.add_parser("predict-match", help="预测单场比赛")
    parser_predict_match.add_argument("--league", required=True, help="联赛代码")
    parser_predict_match.add_argument("--home-team", required=True, help="主队名称")
    parser_predict_match.add_argument("--away-team", required=True, help="客队名称")
    parser_predict_match.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    parser_predict_match.add_argument("--match-id", default="", help="澳客 MatchID（可选；不传则自动搜索）")
    parser_predict_match.add_argument("--league-hint", default="", help="澳客联赛提示（可选，例如 西甲/英超）")
    parser_predict_match.add_argument("--okooo-driver", default="browser-use", help="刷新澳客快照的 driver（默认 browser-use，更稳）")
    parser_predict_match.add_argument("--okooo-headed", action="store_true", help="browser-use 以有头模式运行（更稳但更慢）")
    parser_predict_match.add_argument("--time", dest="match_time", default="", help="比赛时间 HH:MM（用于赛程精准定位）")
    parser_predict_match.add_argument("--no-refresh-odds", action="store_true", help="不刷新澳客实时赔率（仅使用本地/空赔率）")
    parser_predict_match.add_argument("--context-file", default="", help="补充信息 JSON 文件（战术/战意/首发/临场等）")
    add_json_flag(parser_predict_match)

    parser_predict_schedule = subparsers.add_parser("predict-schedule", help="按日期批量生成预测并写回")
    parser_predict_schedule.add_argument("--league", help="联赛代码；留空表示全部联赛")
    parser_predict_schedule.add_argument("--date", required=True, help="开始日期 YYYY-MM-DD")
    parser_predict_schedule.add_argument("--days", type=int, default=1, help="连续处理天数")
    add_json_flag(parser_predict_schedule)

    parser_collect = subparsers.add_parser("collect-data", help="抓取或降级采集比赛数据")
    parser_collect.add_argument("--league", required=True, help="联赛代码")
    parser_collect.add_argument("--date", required=True, help="比赛日期 YYYY-MM-DD")
    parser_collect.add_argument("--no-cache", action="store_true", help="跳过缓存")
    add_json_flag(parser_collect)

    parser_pending = subparsers.add_parser("pending-results", help="列出待更新结果的比赛")
    parser_pending.add_argument("--days-back", type=int, default=7, help="向前查询的天数")
    add_json_flag(parser_pending)

    parser_save = subparsers.add_parser("save-result", help="写入单场比赛结果")
    parser_save.add_argument("--match-id", required=True, help="比赛 ID")
    parser_save.add_argument("--home-score", required=True, type=int, help="主队进球")
    parser_save.add_argument("--away-score", required=True, type=int, help="客队进球")
    add_json_flag(parser_save)

    parser_accuracy = subparsers.add_parser("accuracy", help="获取准确率统计")
    parser_accuracy.add_argument("--refresh", action="store_true", help="重新计算准确率")
    add_json_flag(parser_accuracy)

    parser_health = subparsers.add_parser("health-check", help="检查核心依赖与运行状态")
    add_json_flag(parser_health)

    parser_setup = subparsers.add_parser("setup-openclaw", help="输出 openclaw 初始化安装指引")
    add_json_flag(parser_setup)

    parser_harness_list = subparsers.add_parser("harness-list", help="列出 Harness 风格的可编排 pipeline")
    add_json_flag(parser_harness_list)

    parser_harness_run = subparsers.add_parser("harness-run", help="通过 Harness pipeline 执行任务")
    parser_harness_run.add_argument("--pipeline", required=True, help="pipeline 名称")
    parser_harness_run.add_argument("--league", default="", help="联赛代码")
    parser_harness_run.add_argument("--date", default="", help="比赛日期 YYYY-MM-DD")
    parser_harness_run.add_argument("--home-team", default="", help="主队名称")
    parser_harness_run.add_argument("--away-team", default="", help="客队名称")
    parser_harness_run.add_argument("--match-id", default="", help="比赛 ID")
    parser_harness_run.add_argument("--league-hint", default="", help="联赛提示")
    parser_harness_run.add_argument("--time", dest="match_time", default="", help="比赛时间 HH:MM")
    parser_harness_run.add_argument("--okooo-driver", default="browser-use", help="赔率刷新 driver")
    parser_harness_run.add_argument("--okooo-headed", action="store_true", help="是否有头运行")
    parser_harness_run.add_argument("--no-refresh-odds", action="store_true", help="不刷新赔率")
    parser_harness_run.add_argument("--no-cache", action="store_true", help="跳过采集缓存")
    parser_harness_run.add_argument("--context-file", default="", help="补充信息 JSON 文件")
    parser_harness_run.add_argument("--home-score", type=int, help="主队进球")
    parser_harness_run.add_argument("--away-score", type=int, help="客队进球")
    parser_harness_run.add_argument("--refresh", action="store_true", help="result_recording 后刷新准确率")
    add_json_flag(parser_harness_run)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    if args.command is None:
        print_header()
        show_interactive_menu()
        return

    if args.command == "enhanced":
        print_header()
        run_enhanced_system(args.league, args.days)
    elif args.command == "original":
        print_header()
        run_original_system()
    elif args.command == "ml-test":
        print_header()
        run_ml_test()
    elif args.command == "results":
        print_header()
        from result_manager import interactive_update

        interactive_update()
    elif args.command == "show-accuracy":
        print_header()
        from result_manager import ResultManager, print_accuracy_report

        manager = ResultManager()
        try:
            with open(manager.accuracy_file, "r", encoding="utf-8") as f:
                stats = json.load(f)
                print_accuracy_report(stats)
        except Exception:
            stats = manager.update_accuracy_stats()
            print_accuracy_report(stats)
    elif args.command == "update-accuracy":
        print_header()
        from result_manager import ResultManager, print_accuracy_report

        manager = ResultManager()
        stats = manager.update_accuracy_stats()
        print_accuracy_report(stats)
    elif args.command == "list-leagues":
        run_openclaw_list_leagues(args.json)
    elif args.command == "predict-match":
        run_openclaw_predict_match(args)
    elif args.command == "predict-schedule":
        run_openclaw_predict_schedule(args)
    elif args.command == "collect-data":
        run_openclaw_collect_data(args)
    elif args.command == "pending-results":
        run_openclaw_pending_results(args)
    elif args.command == "save-result":
        run_openclaw_save_result(args)
    elif args.command == "accuracy":
        run_openclaw_accuracy(args)
    elif args.command == "health-check":
        run_openclaw_health_check(args)
    elif args.command == "setup-openclaw":
        run_openclaw_setup_guide(args)
    elif args.command == "harness-list":
        run_harness_list(args)
    elif args.command == "harness-run":
        run_harness_pipeline(args)


if __name__ == "__main__":
    main()

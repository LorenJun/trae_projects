#!/usr/bin/env python3
"""
Bulk fetch recent results from okooo and update teams_2025-26.md files.

Usage:
  python3 bulk_fetch_and_update.py --start 2026-05-01 --end 2026-05-04
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _date_range(start_str, end_str):
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d")
    out = []
    curr = start
    while curr <= end:
        out.append(curr.strftime("%Y-%m-%d"))
        curr += timedelta(days=1)
    return out


def _safe_date_value(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return datetime.min


def _match_identity(item):
    """Build a stable identity so the same finished match collected on multiple days can be merged."""
    league_code = str(item.get("league_code") or "").strip()
    match_id = str(item.get("match_id") or "").strip()
    if match_id:
        return ("match_id", league_code, match_id)

    history_url = str(item.get("history_url") or "").strip()
    if history_url:
        return ("history_url", league_code, history_url)

    return (
        "teams_score",
        league_code,
        str(item.get("home_team") or "").strip(),
        str(item.get("away_team") or "").strip(),
        str(item.get("home_score")),
        str(item.get("away_score")),
    )


def _prefer_item(current, candidate):
    """Prefer the more complete item; if tied, prefer the later request date."""
    def rank(item):
        return (
            1 if item.get("match_id") else 0,
            1 if item.get("history_url") else 0,
            1 if item.get("score") else 0,
            _safe_date_value(item.get("date", "")),
        )

    return candidate if rank(candidate) >= rank(current) else current


def fetch_day(league_name, league_code, date):
    original_argv = sys.argv.copy()
    original_stdout = sys.stdout
    try:
        import io

        # Redirect stdout to capture the output path
        captured_output = io.StringIO()
        sys.stdout = captured_output

        # Temporarily change sys.argv
        sys.argv = [
            "okooo_fetch_daily_schedule.py",
            "--league", league_name,
            "--date", date,
            "--chrome-port", "9222",
        ]

        # Import and run the script's main function
        import runpy
        import okooo_fetch_daily_schedule

        # Monkey patch the print statements temporarily to see what's happening
        # Run the script
        runpy.run_path(str(Path(__file__).resolve().parent / "okooo_fetch_daily_schedule.py"), run_name="__main__")

        out_path_str = captured_output.getvalue().strip()
        out_path = Path(out_path_str) if out_path_str else None

        if out_path and out_path.exists() and out_path.is_file():
            print(f"✅ 抓取成功: {league_name} {date} -> {out_path.name}")
            return json.loads(out_path.read_text(encoding="utf-8"))
        print(f"⚠️ 未找到输出文件: {league_name} {date}")
        return None
    except Exception as e:
        import traceback
        print(f"⚠️ 异常: {league_name} {date} -> {e}")
        print(traceback.format_exc())
        return None
    finally:
        sys.argv = original_argv
        sys.stdout = original_stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--yes", "-y", action="store_true", help="跳过确认，直接更新")
    args = parser.parse_args()

    leagues = [
        ("英超", "premier_league"),
        ("西甲", "la_liga"),
        ("意甲", "serie_a"),
        ("德甲", "bundesliga"),
        ("法甲", "ligue_1"),
    ]

    dates = _date_range(args.start, args.end)

    print(f"📅 抓取范围: {args.start} 至 {args.end}")
    print(f"🏟️  联赛列表: {[name for name,code in leagues]}")
    print()

    all_finished = []
    unique_finished = {}
    raw_finished_count = 0
    duplicate_finished_count = 0

    for league_name, league_code in leagues:
        for date in dates:
            data = fetch_day(league_name, league_code, date)
            if not data:
                continue
            matches = data.get("matches", [])
            finished = [
                m for m in matches
                if isinstance(m, dict)
                and m.get("status") == "已结束"
                and m.get("home_score") is not None
                and m.get("away_score") is not None
            ]
            if finished:
                print(f"  🎯 找到 {len(finished)} 场已结束:")
                for m in finished:
                    print(f"    {m.get('home_team')} {m.get('score')} {m.get('away_team')}")
                    raw_finished_count += 1
                    item = {
                        "league_name": league_name,
                        "league_code": league_code,
                        "date": date,
                        "home_team": m.get("home_team"),
                        "away_team": m.get("away_team"),
                        "home_score": m.get("home_score"),
                        "away_score": m.get("away_score"),
                        "score": m.get("score"),
                        "match_id": m.get("match_id"),
                        "history_url": m.get("history_url"),
                    }
                    identity = _match_identity(item)
                    existing = unique_finished.get(identity)
                    if existing is None:
                        unique_finished[identity] = item
                    else:
                        duplicate_finished_count += 1
                        unique_finished[identity] = _prefer_item(existing, item)

    all_finished = list(unique_finished.values())

    print()
    print("=" * 80)
    print(f"共收集到 {len(all_finished)} 场待更新的已结束比赛")
    if duplicate_finished_count:
        print(
            f"去重前共抓到 {raw_finished_count} 场，已合并重复比赛 {duplicate_finished_count} 场"
        )
    print("=" * 80)

    if not all_finished:
        print("未找到任何已结束的比赛")
        return

    # Preview step
    print()
    print("=" * 80)
    print("⚠️  即将更新以下比赛（预览模式）")
    print("=" * 80)
    for i, item in enumerate(all_finished, 1):
        print(f"  {i}. {item['league_name']} {item['home_team']} vs {item['away_team']}: {item['score']}")

    print()
    if args.yes:
        print("✅ 使用 --yes 参数，跳过确认，直接更新")
    else:
        response = input("⚠️  确认要更新这些比赛吗？[y/N] ").strip().lower()
        if response not in ('y', 'yes'):
            print("取消更新")
            return

    # Skip backup
    print()
    print("📋 跳过备份（按要求不创建备份）")

    # Now we need to update teams_2025-26.md
    from result_manager import ResultManager
    manager = ResultManager()
    
    # Load existing results to skip already updated
    existing_result_count = len(manager.load_results())
    print(f"\n📊 已检测到 {existing_result_count} 场已有比分的比赛")
    
    updated = []
    skipped = []
    processed_result_ids = set()
    for item in all_finished:
        try:
            # Quick check: first try to get match_id
            # We need to match to teams_2025-26.md rows
            row = manager.save_result(
                f"{item['home_team']} vs {item['away_team']}",
                item["home_score"],
                item["away_score"],
                league=item["league_code"],
                date_override=item["date"],
            )
            if row:
                result_id = row.get("match_id") or _match_identity(item)
                if result_id in processed_result_ids:
                    continue
                processed_result_ids.add(result_id)

                if row.get("already_exists"):
                    skipped.append(row)
                    continue

                print(f"✅ 更新成功: {row['league_name']} {row['home_team']} {row['actual_score']} {row['away_team']}")
                updated.append(row)
        except Exception as e:
            print(f"⚠️ 更新失败: {item['league_code']} {item['home_team']} vs {item['away_team']}: {e}")

    print()
    print("=" * 80)
    print(f"成功更新 {len(updated)} 场比赛到 teams_2025-26.md")
    if skipped:
        print(f"已有比分跳过 {len(skipped)} 场")
    print("=" * 80)

    # Update accuracy stats
    stats = manager.update_accuracy_stats()
    print()
    print("📊 最新准确率统计:")
    print(f"总体胜平负: {stats['overall']['win_accuracy']}%")
    print(f"总体比分: {stats['overall']['score_accuracy']}%")
    print(f"总体大小球: {stats['overall']['ou_accuracy']}%")


if __name__ == "__main__":
    main()

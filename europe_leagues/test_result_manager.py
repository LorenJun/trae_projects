#!/usr/bin/env python3
"""模块说明：验证 ResultManager 赛果写回与准确率统计逻辑的测试脚本。

ResultManager 和 DataCollector 的回归测试。"""

import asyncio
import contextlib
import io
import json
import logging
import os
import tempfile
import unittest
from datetime import datetime as _real_datetime
from pathlib import Path
from unittest.mock import patch

from runtime.result_sync import _load_registry, register_prediction_result_sync

from app.cli import _parse_memory_completed_entry
from data_collector import DataCollector
from result_manager import ResultManager
from upset_case_library import 创建爆冷案例, 爆冷案例库
from domain.persistence import PredictionPersistenceService


@contextlib.contextmanager
def quiet_test_output():
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            yield
    finally:
        logging.disable(previous_disable)


# 固定夹具中的“今天”，避免随真实时钟前移导致按 days_back 窗口过滤的用例逐渐失效。
# 夹具比赛日期为 2026-05-10/11，这里冻结在其后数日，确保始终落在 30 天窗口内。
_FROZEN_NOW = _real_datetime(2026, 5, 15, 12, 0, 0)


class _FrozenDatetime(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return _FROZEN_NOW
        return _FROZEN_NOW.astimezone(tz)


class ResultManagerTest(unittest.TestCase):
    def setUp(self):
        self._frozen_time = patch("result_manager.datetime", _FrozenDatetime)
        self._frozen_time.start()
        self.addCleanup(self._frozen_time.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        league_dir = self.base_dir / "la_liga"
        league_dir.mkdir(parents=True)
        (league_dir / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | - | 皇家马德里 | 预测:主胜 信心:0.61 爆冷:低 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        self.memory_path = self.base_dir.parent / "MEMORY.md"
        self.memory_path.write_text(
            "\n".join(
                [
                    "### 预测结果滚动记忆",
                    "",
                    "<!-- prediction-memory:start -->",
                    "> 滚动预测准确率： 暂无已完赛样本",
                    "",
                    "#### 未完赛",
                    "",
                    "- [la_liga|2026-05-10|巴塞罗那|皇家马德里] 2026-05-10 西甲 巴塞罗那 vs 皇家马德里",
                    "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  ▲ 风险: 低(10) 测试样本",
                    "  · 记忆ID: la_liga|2026-05-10|巴塞罗那|皇家马德里 | 更新时间: 2026-05-10 01:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "prediction_review_learning.json").write_text(
            json.dumps({"updated_at": "stale"}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (runtime_dir / "prediction_archive.json").write_text(
            json.dumps(
                {
                    "la_liga_20260511_巴塞罗那_皇家马德里": {
                        "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                        "league": "la_liga",
                        "league_name": "西甲联赛",
                        "match_date": "2026-05-11",
                        "home_team": "巴塞罗那",
                        "away_team": "皇家马德里",
                        "prediction": "主胜",
                        "predicted_winner": "home",
                        "predicted_scores": ["2-1", "1-0"],
                        "top_scores": [["2-1", 0.2], ["1-0", 0.1]],
                        "over_under": {"available": False, "reason": "missing_real_market_line"},
                        "market_snapshot": {},
                        "confidence": 0.61,
                        "upset_potential": {"level": "低", "index": 10, "factors": ["测试样本"]},
                    }
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.manager = ResultManager(base_dir=str(self.base_dir))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_get_pending_matches_reads_from_teams_file(self):
        with quiet_test_output():
            pending = self.manager.get_pending_matches(days_back=30)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(pending[0]["predicted_winner"], "home")

    def test_save_result_updates_score_and_correctness_marker(self):
        with quiet_test_output():
            result = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
        self.assertEqual(result["actual_score"], "2-1")
        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 |", content)
        self.assertIn("预测:主胜 信心:0.61 爆冷:低 ✅", content)
        self.assertEqual(result["predicted_winner"], "home")
        self.assertEqual(result["note"], "预测:主胜 信心:0.61 爆冷:低 ✅")

    def test_save_result_without_force_keeps_existing_score(self):
        with quiet_test_output():
            self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
            updated = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 3, 2)
        self.assertEqual(updated["actual_score"], "2-1")
        self.assertTrue(updated.get("already_exists"))

        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 |", content)
        self.assertNotIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 3-2 | 皇家马德里 |", content)

    def test_save_result_force_overwrites_existing_score_with_latest_value(self):
        with quiet_test_output():
            self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)
            updated = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 3, 2, force=True)
        self.assertEqual(updated["actual_score"], "3-2")
        self.assertTrue(updated.get("forced"))

        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-05-11 | 03:00 | 巴塞罗那 | 3-2 | 皇家马德里 |", content)
        self.assertIn("预测:主胜 信心:0.61 爆冷:低 ✅", content)

    def test_save_result_does_not_auto_reconcile_stale_memory_pending_entry(self):
        with quiet_test_output():
            self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)

        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("#### 未完赛", memory_text)
        self.assertNotIn("#### 已完赛", memory_text)
        self.assertNotIn("■ 赛果: 主胜 2-1", memory_text)
        self.assertIn("- [la_liga|2026-05-10|巴塞罗那|皇家马德里] 2026-05-10 西甲 巴塞罗那 vs 皇家马德里", memory_text)

    def test_sync_prediction_archive_result_does_not_mutate_similar_matches(self):
        archive = self.manager.prediction_archive_store.load()
        archive["la_liga_20260511_巴塞罗那_皇家马德里"]["upset_potential"]["similar_matches"] = [
            {"match_id": "hist-1", "actual_result": "客胜", "actual_score": "0-2"},
            {"match_id": "hist-2", "actual_result": "平局", "actual_score": "1-1"},
        ]
        self.manager.prediction_archive_store.save(archive)

        with quiet_test_output():
            sync_result = self.manager._sync_prediction_archive_result(
                {
                    "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                    "home_team": "巴塞罗那",
                    "away_team": "皇家马德里",
                    "match_date": "2026-05-11",
                    "actual_score": "2-1",
                    "actual_winner": "home",
                }
            )

        self.assertEqual(sync_result["status"], "success")
        updated_archive = self.manager.prediction_archive_store.load()
        similar_matches = updated_archive["la_liga_20260511_巴塞罗那_皇家马德里"]["upset_potential"]["similar_matches"]
        self.assertEqual(similar_matches[0]["actual_result"], "客胜")
        self.assertEqual(similar_matches[0]["actual_score"], "0-2")
        self.assertEqual(similar_matches[1]["actual_result"], "平局")
        self.assertEqual(similar_matches[1]["actual_score"], "1-1")

    def test_save_result_refreshes_closeout_derivatives_and_registry(self):
        register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                "internal_match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                "teams_match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                "league_code": "la_liga",
                "league_name": "西甲",
                "match_date": "2026-05-11",
                "match_time": "03:00",
                "home_team": "巴塞罗那",
                "away_team": "皇家马德里",
                "prediction": "主胜",
                "confidence": 0.61,
            },
        )

        with quiet_test_output():
            result = self.manager.save_result("la_liga_20260511_巴塞罗那_皇家马德里", 2, 1)

        self.assertTrue(result["refresh"]["accuracy_refreshed"])
        self.assertTrue(result["refresh"]["memory_samples_synced"])
        self.assertTrue(result["refresh"]["rag_index_synced"])
        self.assertTrue(result["refresh"]["review_learning_refreshed"])
        self.assertEqual(result["archive_sync"]["status"], "success")
        self.assertTrue(result["registry_sync"]["completed"])
        self.assertEqual(result["upset_sync"]["status"], "skipped")

        accuracy_payload = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "accuracy_stats.json").read_text(encoding="utf-8")
        )
        self.assertEqual(accuracy_payload["overall"]["total_predictions"], 1)
        self.assertEqual(accuracy_payload["overall"]["correct_predictions"], 1)
        self.assertEqual(accuracy_payload["overall"]["total_score_predictions"], 1)
        self.assertEqual(accuracy_payload["overall"]["correct_score_predictions"], 1)
        self.assertEqual(accuracy_payload["overall"]["win_scope"], "unified_prediction_sources")
        self.assertEqual(accuracy_payload["overall"]["score_scope"], "unified_prediction_sources")

        review_payload = json.loads(
            (self.base_dir / ".okooo-scraper" / "runtime" / "prediction_review_learning.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(review_payload.get("updated_at"), "stale")

        registry = _load_registry(str(self.base_dir))
        self.assertEqual(registry["la_liga_20260511_巴塞罗那_皇家马德里"]["status"], "completed")
        self.assertEqual(registry["la_liga_20260511_巴塞罗那_皇家马德里"]["actual_score"], "2-1")

    def test_calculate_accuracy_uses_archive_backfill_when_teams_note_lacks_prediction(self):
        (self.base_dir / "la_liga" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 | 无预测记录 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        stats = self.manager.calculate_accuracy(league="la_liga", days=30)

        self.assertEqual(stats["total_predictions"], 1)
        self.assertEqual(stats["correct_predictions"], 1)
        self.assertEqual(stats["total_score_predictions"], 1)
        self.assertEqual(stats["correct_score_predictions"], 1)
        self.assertEqual(stats["win_scope"], "unified_prediction_sources")
        self.assertEqual(stats["score_scope"], "unified_prediction_sources")

    def test_calculate_accuracy_prefers_sot_note_over_stale_archive_prediction(self):
        archive = self.manager.prediction_archive_store.load()
        archived = archive["la_liga_20260511_巴塞罗那_皇家马德里"]
        archived["prediction"] = "平局"
        archived["predicted_winner"] = "draw"
        archived["predicted_scores"] = ["1-1", "0-0"]
        archived["predicted_ou"] = {"side": "大", "line": 2.5}
        self.manager.prediction_archive_store.save(archive)
        (self.base_dir / "la_liga" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 | 预测:主胜 信心:0.61 比分:2-1/1-0 大小:小2.5 爆冷:低 ✅ |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        samples = self.manager._build_unified_prediction_samples(days=30)
        sample = samples["la_liga_20260511_巴塞罗那_皇家马德里"]
        stats = self.manager.calculate_accuracy(league="la_liga", days=30)

        self.assertEqual(sample["predicted_winner"], "home")
        self.assertEqual(sample["predicted_scores"], ["2-1", "1-0"])
        self.assertEqual(sample["predicted_ou"], {"side": "小", "line": 2.5})
        self.assertEqual(stats["correct_predictions"], 1)
        self.assertEqual(stats["correct_score_predictions"], 1)

    def test_update_accuracy_stats_outputs_league_weight_profile(self):
        stats = self.manager.update_accuracy_stats()
        overall = stats["overall"]
        league_stats = stats["by_league"]["la_liga"]

        self.assertIn("league_weight_factor", overall)
        self.assertIn("confidence_adjustment", overall)
        self.assertIn("weight_reason", overall)
        self.assertIn("top_weight_drivers", overall)
        self.assertIn("poisson", overall["model_accuracy"])
        self.assertIn("elo", overall["model_accuracy"])
        self.assertGreaterEqual(float(league_stats["league_weight_factor"]), 0.92)

    def test_update_accuracy_stats_keeps_friendly_bucket_separate_from_world_cup(self):
        archive = self.manager.prediction_archive_store.load()
        archive["friendly_20260606_巴西_日本"] = {
            "match_id": "friendly_20260606_巴西_日本",
            "league": "friendly",
            "league_code": "friendly",
            "league_name": "友谊赛",
            "match_date": "2026-06-06",
            "home_team": "巴西",
            "away_team": "日本",
            "prediction": "主胜",
            "predicted_winner": "home",
            "predicted_scores": ["2-1", "1-0"],
            "top_scores": [["2-1", 0.2], ["1-0", 0.1]],
            "predicted_ou": {"side": "大", "line": 2.5},
            "actual_score": "2-1",
            "actual_winner": "home",
            "storage_mode": "runtime_only",
            "source_presence": ["archive"],
            "confidence": 0.58,
            "over_under": {"available": True, "line": 2.5, "over": 0.57, "under": 0.43},
            "market_snapshot": {},
            "upset_potential": {"level": "低", "index": 8, "factors": ["测试样本"]},
        }
        self.manager.prediction_archive_store.save(archive)

        stats = self.manager.update_accuracy_stats()

        self.assertIn("friendly", stats["by_league"])
        self.assertEqual(stats["by_league"]["friendly"]["total_predictions"], 1)
        self.assertEqual(stats["by_league"]["friendly"]["correct_predictions"], 1)
        self.assertEqual(stats["by_league"]["world_cup"]["total_predictions"], 0)

    def test_calculate_accuracy_restores_completed_status_note_from_archive_prediction(self):
        (self.base_dir / "la_liga" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | 2-1 | 皇家马德里 | 已完赛；赛果:主胜 2-1；复盘:胜平负缺失 比分缺失 大小球缺失 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        samples = self.manager._build_unified_prediction_samples(days=30)
        sample = samples["la_liga_20260511_巴塞罗那_皇家马德里"]

        self.assertEqual(sample["predicted_winner"], "home")
        self.assertEqual(sample["predicted_scores"], ["2-1", "1-0"])

    def test_reconcile_memory_pending_entries_updates_memory_from_archive_result(self):
        archive = self.manager.prediction_archive_store.load()
        archive["la_liga_20260511_巴塞罗那_皇家马德里"]["actual_score"] = "2-1"
        archive["la_liga_20260511_巴塞罗那_皇家马德里"]["actual_winner"] = "home"
        self.manager.prediction_archive_store.save(archive)
        self.memory_path.write_text(
            "\n".join(
                [
                    "### 预测结果滚动记忆",
                    "",
                    "<!-- prediction-memory:start -->",
                    "> 滚动预测准确率： 暂无已完赛样本",
                    "",
                    "#### 未完赛",
                    "",
                    "- [la_liga|2026-05-11|巴塞罗那|皇家马德里] 2026-05-11 西甲 巴塞罗那 vs 皇家马德里",
                    "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  ▲ 风险: 低(10) 测试样本",
                    "  · 记忆ID: la_liga|2026-05-11|巴塞罗那|皇家马德里 | 更新时间: 2026-05-11 01:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with quiet_test_output():
            report = self.manager.reconcile_memory_pending_entries(days_back=30)

        self.assertEqual(report["reconciled_count"], 1)
        self.assertEqual(report["reconciled"][0]["status"], "memory_updated_from_archive")
        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("#### 已完赛", memory_text)
        self.assertIn("■ 赛果: 主胜 2-1", memory_text)

    def test_reconcile_memory_pending_entries_rejects_nearest_date_archive_match(self):
        archive = self.manager.prediction_archive_store.load()
        archive["la_liga_20260511_巴塞罗那_皇家马德里"]["actual_score"] = "2-1"
        archive["la_liga_20260511_巴塞罗那_皇家马德里"]["actual_winner"] = "home"
        self.manager.prediction_archive_store.save(archive)
        self.memory_path.write_text(
            "\n".join(
                [
                    "### 预测结果滚动记忆",
                    "",
                    "<!-- prediction-memory:start -->",
                    "> 滚动预测准确率： 暂无已完赛样本",
                    "",
                    "#### 未完赛",
                    "",
                    "- [la_liga|2026-05-10|巴塞罗那|皇家马德里] 2026-05-10 西甲 巴塞罗那 vs 皇家马德里",
                    "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 待补真实盘口",
                    "  ▲ 风险: 低(10) 测试样本",
                    "  · 记忆ID: la_liga|2026-05-10|巴塞罗那|皇家马德里 | 更新时间: 2026-05-10 01:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        with quiet_test_output():
            report = self.manager.reconcile_memory_pending_entries(days_back=30)

        self.assertEqual(report["reconciled_count"], 0)
        self.assertEqual(report["failed_count"], 1)
        self.assertEqual(report["failed"][0]["status"], "not_in_archive")
        memory_text = self.memory_path.read_text(encoding="utf-8")
        self.assertIn("#### 未完赛", memory_text)
        self.assertNotIn("■ 赛果: 主胜 2-1", memory_text)

    def test_update_accuracy_stats_includes_reanalysis_report(self):
        reanalysis_payload = {
            "generated_at": "2026-05-24T14:37:48.555309",
            "scope": {"league": "la_liga", "days": 30},
            "summary": {
                "replayed_matches": 4,
                "baseline_win_hits": 1,
                "replay_win_hits": 3,
                "baseline_score_hits": 1,
                "replay_score_hits": 2,
                "baseline_away_predictions": 0,
                "replay_away_predictions": 1,
                "baseline_home_to_away_errors": 1,
                "replay_home_to_away_errors": 0,
                "baseline_home_to_draw_errors": 2,
                "replay_home_to_draw_errors": 1,
                "improved_matches": 2,
                "regressed_matches": 0,
                "blocked_predictions": 0,
                "snapshot_backed_matches": 2,
                "baseline_win_accuracy": 25.0,
                "replay_win_accuracy": 75.0,
                "baseline_score_accuracy": 25.0,
                "replay_score_accuracy": 50.0,
            },
        }
        (self.base_dir / "reanalysis_results_la_liga.json").write_text(
            json.dumps(reanalysis_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        stats = self.manager.update_accuracy_stats()
        reanalysis = stats["reanalysis_report"]

        self.assertTrue(reanalysis["available"])
        self.assertEqual(reanalysis["scope"], "current_model_replay")
        self.assertEqual(reanalysis["overall"]["win_scope"], "current_model_replay")
        self.assertEqual(reanalysis["overall"]["win_accuracy"], 75.0)
        self.assertEqual(reanalysis["overall"]["correct_predictions"], 3)
        self.assertEqual(reanalysis["overall"]["total_predictions"], 4)
        self.assertEqual(reanalysis["by_league"]["la_liga"]["win_accuracy"], 75.0)
        self.assertEqual(reanalysis["by_league"]["la_liga"]["source_file"], "reanalysis_results_la_liga.json")
        self.assertEqual(reanalysis["delta_summary"]["baseline_win_accuracy"], 25.0)
        self.assertEqual(reanalysis["delta_summary"]["replay_win_accuracy"], 75.0)
        self.assertEqual(reanalysis["delta_summary"]["win_accuracy_delta"], 50.0)
        self.assertEqual(reanalysis["delta_summary"]["away_prediction_delta"], 1)
        self.assertEqual(reanalysis["delta_summary"]["top_win_accuracy_improvements"][0]["league"], "la_liga")

    def test_update_accuracy_stats_marks_reanalysis_unavailable_without_files(self):
        stats = self.manager.update_accuracy_stats()
        reanalysis = stats["reanalysis_report"]

        self.assertFalse(reanalysis["available"])
        self.assertEqual(reanalysis["scope"], "current_model_replay")
        self.assertEqual(reanalysis["overall"], {})
        self.assertEqual(reanalysis["by_league"], {})
        self.assertEqual(reanalysis["delta_summary"], {})
        self.assertEqual(reanalysis["generated_from"]["files"], [])

    def test_build_rag_replay_samples_filters_missing_fields_and_duplicates(self):
        archive = self.manager.prediction_archive_store.load()
        archive["la_liga_20260511_巴塞罗那_皇家马德里"].update(
            {
                "actual_score": "2-1",
                "actual_winner": "home",
                "market_snapshot": {"欧赔": {"final": {"home": 2.1, "draw": 3.2, "away": 3.5}}},
                "full_prediction": {
                    "league_code": "la_liga",
                    "home_team": "巴塞罗那",
                    "away_team": "皇家马德里",
                    "retrieved_memory_explanation": "baseline summary",
                    "rag_decision": {"available": True, "risk_bonus": 3, "confidence_penalty": 0.01, "scenario_tags": ["market_case_opposes_pick"]},
                },
            }
        )
        archive["dup-entry"] = {
            "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
            "league": "la_liga",
            "match_date": "2026-05-11",
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "actual_score": "2-1",
            "actual_winner": "home",
            "prediction": "主胜",
            "market_snapshot": {"欧赔": {"final": {"home": 2.2, "draw": 3.1, "away": 3.4}}},
            "full_prediction": {
                "league_code": "la_liga",
                "home_team": "巴塞罗那",
                "away_team": "皇家马德里",
                "retrieved_memory_explanation": "dup baseline",
                "rag_decision": {"available": True},
            },
        }
        archive["missing-result"] = {
            "match_id": "missing-result",
            "league": "la_liga",
            "match_date": "2026-05-12",
            "home_team": "奥萨苏纳",
            "away_team": "马竞",
            "prediction": "平局",
            "market_snapshot": {"欧赔": {"final": {"home": 2.8, "draw": 2.9, "away": 2.6}}},
            "full_prediction": {"retrieved_memory_explanation": "baseline", "rag_decision": {"available": True}},
        }
        archive["missing-market"] = {
            "match_id": "missing-market",
            "league": "la_liga",
            "match_date": "2026-05-13",
            "home_team": "赫塔费",
            "away_team": "马洛卡",
            "prediction": "主胜",
            "actual_score": "1-0",
            "actual_winner": "home",
            "full_prediction": {"retrieved_memory_explanation": "baseline", "rag_decision": {"available": True}},
        }
        archive["missing-baseline"] = {
            "match_id": "missing-baseline",
            "league": "la_liga",
            "match_date": "2026-05-14",
            "home_team": "瓦伦西亚",
            "away_team": "塞维利亚",
            "prediction": "客胜",
            "actual_score": "0-2",
            "actual_winner": "away",
            "market_snapshot": {"欧赔": {"final": {"home": 2.4, "draw": 3.0, "away": 2.9}}},
        }
        self.manager.prediction_archive_store.save(archive)

        report = self.manager.build_rag_replay_samples(league="la_liga")

        self.assertEqual(report["sample_source"], "archive")
        self.assertEqual(report["replayable_count"], 1)
        self.assertEqual(len(report["samples"]), 1)
        self.assertEqual(report["samples"][0]["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        skip_reasons = {item["match_id"]: item["skip_reason"] for item in report["skipped"]}
        self.assertEqual(skip_reasons["missing-result"], "missing_actual_result")
        self.assertEqual(skip_reasons["missing-market"], "missing_market_snapshot")
        self.assertEqual(skip_reasons["missing-baseline"], "missing_baseline_rag_fields")
        self.assertEqual(skip_reasons["la_liga_20260511_巴塞罗那_皇家马德里"], "duplicate_match")

    def test_evaluate_rag_replay_is_read_only_and_aggregates(self):
        archive = self.manager.prediction_archive_store.load()
        archive["la_liga_20260511_巴塞罗那_皇家马德里"].update(
            {
                "actual_score": "2-1",
                "actual_winner": "home",
                "market_snapshot": {"欧赔": {"final": {"home": 2.1, "draw": 3.2, "away": 3.5}}},
                "full_prediction": {
                    "league_code": "la_liga",
                    "home_team": "巴塞罗那",
                    "away_team": "皇家马德里",
                    "retrieved_memory_explanation": "baseline summary",
                    "rag_decision": {"available": True, "risk_bonus": 3, "confidence_penalty": 0.01, "scenario_tags": ["market_case_opposes_pick"]},
                },
            }
        )
        self.manager.prediction_archive_store.save(archive)
        archive_before = Path(self.manager.prediction_archive_file).read_text(encoding="utf-8")
        memory_before = self.memory_path.read_text(encoding="utf-8")

        class DummyRagService:
            def __init__(self, base_dir=None):
                self.base_dir = base_dir

            def retrieve_match_memory(self, **kwargs):
                return {
                    "available": True,
                    "mode": "hybrid-structured-bm25-v2",
                    "summary": {
                        "retrieved_count": 3,
                        "completed_similar_case_count": 2,
                        "market_case_count": 1,
                        "home_win_rate": 1.0,
                        "draw_rate": 0.0,
                        "away_win_rate": 0.0,
                        "avg_market_total_goals": 3.4,
                        "direction_ou_priority": {
                            "preferred_scores": ["2-1", "1-0"],
                            "current_score_overlap": ["2-1"],
                        },
                        "live_market_followup": {"applied": False, "reason": "unit-test"},
                    },
                    "similar_cases": [{"match_id": "hist-1"}, {"match_id": "hist-2"}],
                    "market_cases": [{"match_id": "market-1"}],
                    "upset_cases": [{"match_id": "upset-1"}],
                }

            def build_lightweight_decision(self, **kwargs):
                return {
                    "available": True,
                    "risk_bonus": 5,
                    "confidence_penalty": 0.03,
                    "scenario_tags": ["upset_case_cluster", "similar_cases_low_hit_rate"],
                }

        with patch("domain.rag.HybridRAGService", DummyRagService):
            report = self.manager.evaluate_rag_replay(league="la_liga", include_matches=True)

        self.assertTrue(report["read_only"])
        self.assertEqual(report["sample_source"], "archive")
        self.assertEqual(report["overall"]["sample_count"], 1)
        self.assertEqual(report["overall"]["replayed_count"], 1)
        self.assertEqual(report["overall"]["skipped_count"], 0)
        self.assertEqual(report["overall"]["decision_changed_count"], 1)
        self.assertEqual(report["overall"]["baseline_market_opposes_pick_count"], 1)
        self.assertEqual(report["overall"]["replay_upset_case_cluster_count"], 1)
        self.assertEqual(report["overall"]["baseline_win_hit_rate"], 100.0)
        self.assertEqual(report["overall"]["replay_win_hit_rate"], 100.0)
        self.assertEqual(len(report["matches"]), 1)
        self.assertEqual(report["matches"][0]["replay"]["retrieved_memory"]["similar_case_temporal"][0]["match_id"], "hist-1")
        self.assertIn("temporal_bonus", report["matches"][0]["replay"]["retrieved_memory"]["similar_case_temporal"][0])
        match_payload = report["matches"][0]
        self.assertEqual(match_payload["status"], "replayed")
        self.assertIn("rag_decision", match_payload["changed_fields"])
        self.assertEqual(match_payload["replay"]["supported_winner_key"], "home")
        self.assertEqual(match_payload["replay"]["preferred_scores"], ["2-1", "1-0"])
        self.assertEqual(match_payload["replay"]["retrieved_memory"]["similar_case_ids"], ["hist-1", "hist-2"])
        self.assertEqual(report["by_league"]["la_liga"]["replayed_count"], 1)
        self.assertEqual(Path(self.manager.prediction_archive_file).read_text(encoding="utf-8"), archive_before)
        self.assertEqual(self.memory_path.read_text(encoding="utf-8"), memory_before)

    def test_apply_reanalysis_predictions_updates_completed_note_and_archive(self):
        (self.base_dir / "la_liga" / "teams_2025-26.md").write_text(
            "\n".join(
                [
                    "# 测试联赛",
                    "",
                    "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |",
                    "|-----|------|-----|------|-----|------|",
                    "| 2026-05-11 | 03:00 | 巴塞罗那 | 1-2 | 皇家马德里 | 已完赛；预测:主胜 信心:0.61 比分:2-1/1-0；复盘:旧结果 |",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        reanalysis_payload = {
            "generated_at": "2026-05-24T18:28:30.180407",
            "matches": [
                {
                    "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                    "league": "la_liga",
                    "league_name": "西甲联赛",
                    "match_date": "2026-05-11",
                    "match_time": "03:00",
                    "home_team": "巴塞罗那",
                    "away_team": "皇家马德里",
                    "storage_mode": "league_sot",
                    "snapshot": {"match_id": "ext-1"},
                    "baseline": {
                        "predicted_winner": "主胜",
                        "predicted_winner_key": "home",
                        "win_hit": False,
                    },
                    "replay": {
                        "predicted_winner": "客胜",
                        "predicted_winner_key": "away",
                        "predicted_scores": ["1-2", "0-1"],
                        "final_probabilities": {"home_win": 0.31, "draw": 0.29, "away_win": 0.40},
                        "all_probabilities": {"主胜": 0.31, "平局": 0.29, "客胜": 0.40},
                        "confidence": 0.40,
                        "win_hit": True,
                    },
                }
            ],
        }
        reanalysis_path = self.base_dir / "reanalysis_results_la_liga.json"
        reanalysis_path.write_text(json.dumps(reanalysis_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        report = self.manager.apply_reanalysis_predictions(
            league="la_liga",
            input_path=str(reanalysis_path),
            only_improved=True,
            refresh_accuracy=True,
        )

        self.assertEqual(report["applied_count"], 1)
        content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        self.assertIn("预测:客胜", content)
        self.assertIn("复盘:旧结果", content)
        archive = self.manager.prediction_archive_store.load()
        archived = archive["la_liga_20260511_巴塞罗那_皇家马德里"]
        self.assertEqual(archived["predicted_winner"], "away")
        self.assertTrue(archived["applied_from_reanalysis"])
        self.assertEqual(archived["reanalysis_source_file"], "reanalysis_results_la_liga.json")
        refreshed = report["accuracy_refresh"]
        self.assertEqual(refreshed["overall"]["correct_predictions"], 1)
        self.assertEqual(refreshed["by_league"]["la_liga"]["correct_predictions"], 1)

    def test_apply_reanalysis_predictions_dry_run_leaves_completed_note_unchanged(self):
        original_content = (self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8")
        reanalysis_payload = {
            "generated_at": "2026-05-24T18:28:30.180407",
            "matches": [
                {
                    "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
                    "league": "la_liga",
                    "league_name": "西甲联赛",
                    "match_date": "2026-05-11",
                    "match_time": "03:00",
                    "home_team": "巴塞罗那",
                    "away_team": "皇家马德里",
                    "storage_mode": "league_sot",
                    "baseline": {
                        "predicted_winner": "主胜",
                        "predicted_winner_key": "home",
                        "win_hit": False,
                    },
                    "replay": {
                        "predicted_winner": "客胜",
                        "predicted_winner_key": "away",
                        "predicted_scores": ["1-2"],
                        "final_probabilities": {"home_win": 0.31, "draw": 0.29, "away_win": 0.40},
                        "all_probabilities": {"主胜": 0.31, "平局": 0.29, "客胜": 0.40},
                        "confidence": 0.40,
                        "win_hit": True,
                    },
                }
            ],
        }
        reanalysis_path = self.base_dir / "reanalysis_results_la_liga.json"
        reanalysis_path.write_text(json.dumps(reanalysis_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        report = self.manager.apply_reanalysis_predictions(
            league="la_liga",
            input_path=str(reanalysis_path),
            dry_run=True,
            only_improved=True,
        )

        self.assertEqual(report["applied"][0]["status"], "dry_run")
        self.assertEqual((self.base_dir / "la_liga" / "teams_2025-26.md").read_text(encoding="utf-8"), original_content)
        archive = self.manager.prediction_archive_store.load()
        self.assertEqual(archive["la_liga_20260511_巴塞罗那_皇家马德里"]["predicted_winner"], "home")


class PredictionPersistenceServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        memory_path = self.base_dir.parent / "MEMORY.md"
        memory_path.write_text(
            "\n".join(
                [
                    "### 预测结果滚动记忆",
                    "",
                    "<!-- prediction-memory:start -->",
                    "> 滚动预测准确率： 暂无已完赛样本",
                    "",
                    "#### 未完赛",
                    "",
                    "- [la_liga|2026-05-10|赫塔费|马略卡] 2026-05-10 西甲 赫塔费 vs 马略卡",
                    "  预测: 主胜 (40.0%) | 比分: 1-0 > 1-1 | 大小球: 待补真实盘口",
                    "  ▲ 风险: 低(10) 样本1",
                    "  · 记忆ID: la_liga|2026-05-10|赫塔费|马略卡 | 更新时间: 2026-05-10 01:00:00",
                    "<!-- prediction-memory:end -->",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_dir = self.base_dir / ".okooo-scraper" / "runtime"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        self.service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=None,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_update_prediction_memory_keeps_same_teams_different_dates(self):
        result = {
            "league_code": "la_liga",
            "league_name": "西甲",
            "match_date": "2026-05-17",
            "home_team": "赫塔费",
            "away_team": "马洛卡",
            "prediction": "平局",
            "confidence": 0.41,
            "top_scores": [("1-1", 0.2), ("0-0", 0.1)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"):
            self.service.update_prediction_memory(result)

        memory_text = (self.base_dir.parent / "MEMORY.md").read_text(encoding="utf-8")
        self.assertIn("2026-05-10 西甲 赫塔费 vs 马略卡", memory_text)
        self.assertIn("2026-05-17 西甲 赫塔费 vs 马洛卡", memory_text)

    def test_render_prediction_memory_block_keeps_pending_section_above_completed(self):
        pending_entry = "\n".join(
            [
                "- [la_liga|2026-05-17|赫塔费|马洛卡] 2026-05-17 西甲 赫塔费 vs 马洛卡 | MatchID: future-1",
                "  预测: 平局 (41.0%) | 比分: 1-1 > 0-0 | 大小球: 待补真实盘口",
                "  ▲ 风险: 低(10) 样本2",
                "  · MatchID: future-1 | 记忆ID: la_liga|2026-05-17|赫塔费|马洛卡 | 更新时间: 2026-05-17 01:00:00",
            ]
        )
        completed_entry = "\n".join(
            [
                "- [la_liga|2026-05-10|赫塔费|马略卡] 2026-05-10 西甲 赫塔费 vs 马略卡 | MatchID: done-1",
                "  预测: 主胜 (40.0%) | 比分: 1-0 > 1-1 | 大小球: 待补真实盘口",
                "  ▲ 风险: 低(10) 样本1",
                "  ■ 赛果: 主胜 1-0",
                "  · MatchID: done-1 | 记忆ID: la_liga|2026-05-10|赫塔费|马略卡 | 更新时间: 2026-05-10 01:00:00",
            ]
        )

        block = PredictionPersistenceService.render_prediction_memory_block(
            [completed_entry, pending_entry],
            "<!-- prediction-memory:start -->",
            "<!-- prediction-memory:end -->",
        )

        pending_pos = block.index("#### 未完赛")
        completed_pos = block.index("#### 已完赛")
        self.assertLess(pending_pos, completed_pos)
        self.assertLess(block.index("future-1"), block.index("done-1"))

    def test_render_prediction_memory_block_auto_reorders_fields(self):
        raw_entry = "\n".join(
            [
                "- [la_liga|2026-05-17|赫塔费|马洛卡] 2026-05-17 西甲 赫塔费 vs 马洛卡 | MatchID: future-2",
                "  · 更新时间: 2026-05-17 01:00:00 | 记忆ID: la_liga|2026-05-17|赫塔费|马洛卡 | MatchID: future-2",
                "  ■ 状态: 待开赛",
                "  ◆ RAG记忆: 测试RAG",
                "  ▲ 风险: 低(10) 样本3",
                "  ◦ 欧赔: 2.10/3.20/3.60->2.05/3.25/3.70",
                "  预测: 平局 (41.0%) | 比分: 1-1 > 0-0 | 大小球: 待补真实盘口",
            ]
        )

        block = PredictionPersistenceService.render_prediction_memory_block(
            [raw_entry],
            "<!-- prediction-memory:start -->",
            "<!-- prediction-memory:end -->",
        )

        prediction_pos = block.index("  预测:")
        market_pos = block.index("  ◦ 欧赔:")
        risk_pos = block.index("  ▲ 风险:")
        rag_pos = block.index("  ◆ RAG记忆:")
        status_pos = block.index("  ■ 状态:")
        meta_pos = block.index("  · MatchID: future-2 | 记忆ID: la_liga|2026-05-17|赫塔费|马洛卡 | 更新时间: 2026-05-17 01:00:00")
        self.assertLess(prediction_pos, market_pos)
        self.assertLess(market_pos, risk_pos)
        self.assertLess(risk_pos, rag_pos)
        self.assertLess(rag_pos, status_pos)
        self.assertLess(status_pos, meta_pos)

    def test_persist_prediction_registers_result_sync_and_sets_canonical_ids(self):
        saved_payloads = []

        class DummyCache:
            def set(self, cache_name, cache_params, result):
                saved_payloads.append((cache_name, cache_params, result["home_team"], result["away_team"]))

        class DummyResultManager:
            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                self.saved = (league_code, result.get("internal_match_id"), result.get("teams_match_id"), result.get("storage_mode"))
                return {}

            def update_accuracy_stats(self):
                return {"overall": {}}

        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=DummyCache(),
            result_manager=DummyResultManager(),
        )
        result = {
            "match_id": "predict_cache_999",
            "external_match_id": "999001",
            "league_code": "la_liga",
            "league_name": "西甲",
            "match_date": "2026-05-11",
            "match_time": "03:00",
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "prediction": "主胜",
            "confidence": 0.61,
            "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
            "over_under": {"available": True, "line": 2.75, "over": 0.44, "under": 0.56},
            "runtime_profile": {"mode": "unit-test"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"), patch("domain.persistence.register_prediction_result_sync") as mock_register:
            persisted = service.persist_prediction("predict_match", {"key": 1}, result, "la_liga")

        self.assertEqual(saved_payloads[0][0], "predict_match")
        self.assertEqual(persisted["internal_match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(persisted["teams_match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(persisted["storage_mode"], "league_sot")
        self.assertTrue(persisted["persisted"]["archived"])
        self.assertTrue(persisted["persisted"]["memory_updated"])
        self.assertTrue(persisted["persisted"]["result_sync_registered"])
        mock_register.assert_called_once()

    def test_prepare_cached_prediction_registers_result_sync_and_persisted_status(self):
        class DummyResultManager:
            def __init__(self):
                self.saved = []

            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                self.saved.append((league_code, result.get("internal_match_id"), result.get("storage_mode")))
                return {}

            def update_accuracy_stats(self):
                return {"overall": {}}

        manager = DummyResultManager()
        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=manager,
        )
        cached = {
            "match_id": "cache_ref_321",
            "external_match_id": "321001",
            "league_code": "europa_league",
            "league_name": "欧联",
            "match_date": "2026-05-17",
            "home_team": "罗马",
            "away_team": "塞维利亚",
            "prediction": "平局",
            "confidence": 0.44,
            "top_scores": [("1-1", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"), patch("domain.persistence.register_prediction_result_sync") as mock_register:
            persisted = service.prepare_cached_prediction(cached, {"mode": "cache"}, "europa_league")

        self.assertEqual(persisted["internal_match_id"], "cache_ref_321")
        self.assertEqual(persisted["storage_mode"], "runtime_only")
        self.assertTrue(persisted["persisted"]["archived"])
        self.assertTrue(persisted["persisted"]["memory_updated"])
        self.assertTrue(persisted["persisted"]["result_sync_registered"])
        self.assertEqual(manager.saved, [("europa_league", "cache_ref_321", "runtime_only")])
        mock_register.assert_called_once_with(str(self.base_dir), persisted)

    def test_prepare_cached_prediction_runtime_only_archive_failure_keeps_memory_and_sync(self):
        class DummyResultManager:
            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                raise RuntimeError("archive failed")

            def update_accuracy_stats(self):
                return {"overall": {}}

        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=DummyResultManager(),
        )
        cached = {
            "match_id": "cache_ref_654",
            "external_match_id": "654001",
            "league_code": "europa_league",
            "league_name": "欧联",
            "match_date": "2026-05-18",
            "home_team": "勒沃库森",
            "away_team": "罗马",
            "prediction": "主胜",
            "confidence": 0.51,
            "top_scores": [("2-1", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"), patch("domain.persistence.register_prediction_result_sync") as mock_register:
            persisted = service.prepare_cached_prediction(cached, {"mode": "cache"}, "europa_league")

        self.assertEqual(persisted["internal_match_id"], "cache_ref_654")
        self.assertEqual(persisted["storage_mode"], "runtime_only")
        self.assertFalse(persisted["persisted"]["archived"])
        self.assertTrue(persisted["persisted"]["memory_updated"])
        self.assertTrue(persisted["persisted"]["result_sync_registered"])
        self.assertEqual(persisted["persisted"]["error"], "archive failed")
        mock_register.assert_called_once_with(str(self.base_dir), persisted)

    def test_prepare_cached_prediction_does_not_promote_internal_match_id_to_external(self):
        class DummyResultManager:
            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                return {}

            def update_accuracy_stats(self):
                return {"overall": {}}

        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=DummyResultManager(),
        )
        cached = {
            "match_id": "world_cup_20260612_墨西哥_南非",
            "league_code": "world_cup",
            "league_name": "世界杯",
            "match_date": "2026-06-12",
            "home_team": "墨西哥",
            "away_team": "南非",
            "prediction": "主胜",
            "confidence": 0.51,
            "top_scores": [("1-0", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"), patch(
            "domain.persistence.register_prediction_result_sync"
        ):
            persisted = service.prepare_cached_prediction(cached, {"mode": "cache"}, "world_cup")

        self.assertEqual(persisted["match_id"], "world_cup_20260612_墨西哥_南非")
        self.assertEqual(persisted["internal_match_id"], "world_cup_20260612_墨西哥_南非")
        self.assertEqual(persisted["external_match_id"], "")

    def test_prepare_cached_prediction_normalizes_friendly_display_name_for_persistence_and_sync(self):
        class DummyResultManager:
            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def save_prediction_from_enhanced(self, result, league_code):
                return {}

            def update_accuracy_stats(self):
                return {"overall": {}}

        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=DummyResultManager(),
        )
        cached = {
            "match_id": "friendly_20260612_巴西_日本",
            "league_code": "friendly",
            "league_name": "世界杯",
            "match_date": "2026-06-12",
            "home_team": "巴西",
            "away_team": "日本",
            "prediction": "主胜",
            "confidence": 0.51,
            "top_scores": [("2-1", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"), patch(
            "domain.persistence.register_prediction_result_sync"
        ) as mock_register:
            persisted = service.prepare_cached_prediction(cached, {"mode": "cache"}, "friendly")

        self.assertEqual(persisted["league_code"], "friendly")
        self.assertEqual(persisted["league"], "friendly")
        self.assertEqual(persisted["league_name"], "友谊赛")
        mock_register.assert_called_once_with(str(self.base_dir), persisted)

    def test_register_prediction_result_sync_uses_friendly_display_name(self):
        entry = register_prediction_result_sync(
            str(self.base_dir),
            {
                "match_id": "friendly_20260612_巴西_日本",
                "league_code": "friendly",
                "league_name": "",
                "match_date": "2026-06-12",
                "home_team": "巴西",
                "away_team": "日本",
                "prediction": "主胜",
                "confidence": 0.54,
            },
        )

        registry = _load_registry(str(self.base_dir))
        self.assertEqual(entry["league_code"], "friendly")
        self.assertEqual(entry["league_name"], "友谊赛")
        self.assertEqual(registry["friendly_20260612_巴西_日本"]["league_name"], "友谊赛")

    def test_persist_memory_only_prediction_sets_runtime_only_identity_without_archive(self):
        class DummyResultManager:
            def _find_existing_teams_match_id(self, league_code, match_date, home_team, away_team):
                return ""

            def _runtime_only_match_id(self, external_match_id, league_code, match_date, home_team, away_team):
                return external_match_id or f"{league_code}_{match_date.replace('-', '')}_{home_team}_{away_team}"

            def update_accuracy_stats(self):
                return {"overall": {}}

        service = PredictionPersistenceService(
            base_dir=str(self.base_dir),
            cache=None,
            result_manager=DummyResultManager(),
        )
        result = {
            "match_id": "lite-111",
            "league_code": "championship",
            "league_name": "英冠",
            "match_date": "2026-05-20",
            "home_team": "米尔沃尔",
            "away_team": "赫尔城",
            "prediction": "主胜",
            "confidence": 0.55,
            "top_scores": [("2-1", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
        }

        with patch("domain.persistence.sync_prediction_memory_samples"), patch("domain.persistence.sync_rag_index"):
            persisted = service.persist_memory_only_prediction(result, "championship")

        self.assertEqual(persisted["internal_match_id"], "lite-111")
        self.assertEqual(persisted["storage_mode"], "runtime_only")
        self.assertFalse(persisted["persisted"]["archived"])
        self.assertTrue(persisted["persisted"]["memory_updated"])
        self.assertNotIn("result_sync_registered", persisted["persisted"])


class ResultManagerArchiveTest(ResultManagerTest):

    def test_save_prediction_from_enhanced_archives_review_and_market_fields(self):
        enhanced_pred = {
            "match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
            "external_match_id": "123001",
            "match_date": "2026-05-11",
            "match_time": "03:00",
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "prediction": "主胜",
            "confidence": 0.61,
            "final_probabilities": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
            "top_scores": [("2-1", 0.2), ("1-0", 0.1)],
            "over_under": {"available": True, "line": 2.75, "over": 0.44, "under": 0.56},
            "market_snapshot": {
                "欧赔": {
                    "initial": {"home": 2.12, "draw": 3.3, "away": 3.45},
                    "final": {"home": 2.25, "draw": 3.12, "away": 3.18},
                },
                "亚值": {
                    "initial": {"handicap_value": -0.5},
                    "final": {"handicap_value": -0.25},
                },
                "大小球": {
                    "initial": {"line": 2.5},
                    "final": {"line": 2.75},
                },
            },
            "strength_diff": 14.5,
            "rag_decision": {
                "available": True,
                "risk_bonus": 7,
                "confidence_penalty": 0.018,
                "scenario_tags": ["upset_case_cluster", "market_case_opposes_pick"],
            },
            "runtime_profile": {"mode": "unit-test"},
            "realtime": {
                "context_applied": {
                    "posterior_outcome_pipeline": {
                        "baseline": {"home_win": 0.55, "draw": 0.24, "away_win": 0.21},
                        "final": {"home_win": 0.52, "draw": 0.27, "away_win": 0.21},
                        "stages_applied": ["review_outcome_adjustment"],
                    },
                    "posterior_outcome_guard": {"applied": False, "reason": "within_limit"},
                    "review_outcome_adjustment": {
                        "applied": True,
                        "stratified_review": {"bucket_key": "home:level_shallow"},
                        "three_layer_review": {"bucket_key": "home:level_shallow:draw_guarded"},
                    },
                }
            },
        }
        with quiet_test_output():
            self.manager.save_prediction_from_enhanced(enhanced_pred, "la_liga")

        archive = self.manager.prediction_archive_store.load()
        archived = archive["la_liga_20260511_巴塞罗那_皇家马德里"]
        self.assertEqual(archived["predicted_winner"], "home")
        self.assertEqual(archived["predicted_scores"], ["2-1", "1-0"])
        self.assertEqual(archived["predicted_ou"], {"side": "小", "line": 2.75})
        self.assertEqual(archived["market_snapshot"]["亚值"]["final"]["handicap_value"], -0.25)
        self.assertEqual(archived["market_snapshot"]["亚值"]["initial"]["handicap_value"], -0.5)
        self.assertEqual(archived["market_snapshot"]["大小球"]["final"]["line"], 2.75)
        self.assertEqual(archived["market_snapshot"]["欧赔"]["final"]["home"], 2.25)
        self.assertEqual(archived["full_prediction"]["strength_diff"], 14.5)
        self.assertEqual(
            archived["full_prediction"]["realtime"]["context_applied"]["review_outcome_adjustment"]["stratified_review"]["bucket_key"],
            "home:level_shallow",
        )
        self.assertEqual(
            archived["full_prediction"]["realtime"]["context_applied"]["review_outcome_adjustment"]["three_layer_review"]["bucket_key"],
            "home:level_shallow:draw_guarded",
        )
        self.assertEqual(archived["full_prediction"]["rag_decision"]["risk_bonus"], 7)
        self.assertEqual(archived["full_prediction"]["rag_decision"]["confidence_penalty"], 0.018)
        self.assertEqual(
            archived["full_prediction"]["rag_decision"]["scenario_tags"],
            ["upset_case_cluster", "market_case_opposes_pick"],
        )
        self.assertEqual(
            archived["full_prediction"]["realtime"]["context_applied"]["posterior_outcome_pipeline"]["stages_applied"],
            ["review_outcome_adjustment"],
        )
        self.assertNotIn("analysis_context", archived["full_prediction"])
        self.assertNotIn("retrieved_memory", archived["full_prediction"])
        self.assertNotIn("model_predictions", archived["full_prediction"])
        self.assertNotIn("home_strength", archived["full_prediction"])
        self.assertNotIn("away_strength", archived["full_prediction"])

    def test_save_prediction_from_enhanced_preserves_zero_final_market_values(self):
        enhanced_pred = {
            "match_id": "la_liga_20260512_奥萨苏纳_马德里竞技",
            "external_match_id": "456001",
            "match_date": "2026-05-12",
            "match_time": "03:00",
            "home_team": "奥萨苏纳",
            "away_team": "马德里竞技",
            "prediction": "平局",
            "confidence": 0.52,
            "final_probabilities": {"home_win": 0.3, "draw": 0.4, "away_win": 0.3},
            "top_scores": [("1-1", 0.18), ("0-0", 0.12)],
            "over_under": {"available": True, "line": 0.0, "over": 0.48, "under": 0.52},
            "market_snapshot": {
                "欧赔": {
                    "initial": {"home": 2.35, "draw": 3.1, "away": 2.88},
                    "final": {"home": 0.0, "draw": 3.0, "away": 2.9},
                },
                "亚值": {
                    "initial": {"handicap_value": -0.25},
                    "final": {"handicap_value": 0.0},
                },
                "大小球": {
                    "initial": {"line": 2.5},
                    "final": {"line": 0.0},
                },
            },
            "runtime_profile": {"mode": "unit-test"},
            "realtime": {"context_applied": {}},
        }
        with quiet_test_output():
            self.manager.save_prediction_from_enhanced(enhanced_pred, "la_liga")

        archive = self.manager.prediction_archive_store.load()
        archived = next(item for item in archive.values() if item.get("external_match_id") == "456001")
        self.assertEqual(archived["predicted_ou"], {"side": "小", "line": 0.0})
        self.assertEqual(archived["market_snapshot"]["亚值"]["final"]["handicap_value"], 0.0)
        self.assertEqual(archived["market_snapshot"]["亚值"]["initial"]["handicap_value"], -0.25)
        self.assertEqual(archived["market_snapshot"]["大小球"]["final"]["line"], 0.0)
        self.assertEqual(archived["market_snapshot"]["大小球"]["initial"]["line"], 2.5)
        self.assertEqual(archived["market_snapshot"]["欧赔"]["final"]["home"], 0.0)
        self.assertEqual(archived["market_snapshot"]["欧赔"]["initial"]["home"], 2.35)

    def test_save_prediction_from_enhanced_prefers_precomputed_identity_fields(self):
        enhanced_pred = {
            "match_id": "custom_runtime_match_777",
            "external_match_id": "777001",
            "internal_match_id": "custom_internal_id",
            "teams_match_id": "la_liga_20260511_巴塞罗那_皇家马德里",
            "storage_mode": "league_sot",
            "match_date": "2026-05-11",
            "match_time": "03:00",
            "home_team": "巴塞罗那",
            "away_team": "皇家马德里",
            "prediction": "主胜",
            "confidence": 0.61,
            "top_scores": [("2-1", 0.2)],
            "over_under": {"available": False, "reason": "missing_real_market_line"},
            "runtime_profile": {"mode": "unit-test"},
            "realtime": {"context_applied": {}},
        }

        with quiet_test_output():
            saved = self.manager.save_prediction_from_enhanced(enhanced_pred, "la_liga")

        archive = self.manager.prediction_archive_store.load()
        archived = next(item for item in archive.values() if item.get("match_id") == "custom_internal_id")
        self.assertEqual(saved["match_id"], "custom_internal_id")
        self.assertEqual(saved["teams_match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(archived["match_id"], "custom_internal_id")
        self.assertEqual(archived["teams_match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(archived["storage_mode"], "league_sot")

    def test_review_entry_upset_sync_uses_review_sample_fields(self):
        entry_text = "\n".join(
            [
                "- [la_liga|2026-05-11|巴塞罗那|皇家马德里] 2026-05-11 西甲联赛 巴塞罗那 vs 皇家马德里",
                "  预测: 主胜 (61.0%) | 比分: 2-1 > 1-0 | 大小球: 小球 2.75 (60.0%)",
                "  ■ 赛果: 客胜 1-2",
                "  · 记忆ID: la_liga_20260511_巴塞罗那_皇家马德里 | 更新时间: 2026-05-11 12:00:00",
            ]
        )
        parsed_entry = _parse_memory_completed_entry(entry_text)
        self.assertEqual(parsed_entry["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(parsed_entry["league"], "la_liga")
        self.assertEqual(parsed_entry["confidence_pct"], 61.0)

        with patch.object(self.manager, "_auto_sync_upset_case", return_value={"status": "added", "case_id": "case-1"}) as mocked:
            result = self.manager.sync_upset_cases_from_review_entries([parsed_entry])

        self.assertEqual(result["total_entries"], 1)
        self.assertEqual(result["processed_count"], 1)
        self.assertEqual(result["added_count"], 1)
        payload = mocked.call_args.args[0]
        self.assertEqual(payload["match_id"], "la_liga_20260511_巴塞罗那_皇家马德里")
        self.assertEqual(payload["predicted_winner"], "主胜")
        self.assertEqual(payload["actual_winner"], "客胜")
        self.assertEqual(payload["actual_score"], "1-2")
        self.assertEqual(payload["note"], "信心:0.61")

    def test_parse_memory_completed_entry_keeps_pipe_style_memory_id(self):
        entry_text = "\n".join(
            [
                "- [bundesliga|2026-05-09|多特蒙德|法兰克福] 2026-05-09 德甲 多特蒙德 vs 法兰克福",
                "  预测: 主胜 (37.1%) | 比分: 2-1 > 2-0 > 1-0 | 大小球: 小球 3.5 (77.5%)",
                "  ■ 赛果: 主胜 3-2",
                "  · 记忆ID: bundesliga|2026-05-09|多特蒙德|法兰克福 | 更新时间: 2026-05-11 11:48:42",
            ]
        )
        parsed_entry = _parse_memory_completed_entry(entry_text)
        self.assertEqual(parsed_entry["memory_id"], "bundesliga|2026-05-09|多特蒙德|法兰克福")
        self.assertEqual(parsed_entry["match_id"], "bundesliga|2026-05-09|多特蒙德|法兰克福")


class DataCollectorTest(unittest.TestCase):
    def test_degrades_to_mock_when_browser_use_missing(self):
        with patch.dict(
            os.environ,
            {"ENABLE_MOCK_SCRAPER": "1", "OKOOO_COLLECT_SCHEDULE": "0"},
            clear=False,
        ), patch("importlib.util.find_spec", return_value=None):
            with quiet_test_output():
                collector = DataCollector()

        self.assertEqual([scraper.name for scraper in collector.scrapers], ["mock"])
        with quiet_test_output():
            matches = asyncio.run(collector.collect_league_data("la_liga", "2026-05-11", use_cache=False))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].sources, ["mock"])


class UpsetCaseLibraryTest(unittest.TestCase):
    def test_duplicate_case_is_silent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            library_path = Path(temp_dir) / "爆冷案例库.json"
            library = 爆冷案例库(str(library_path))
            case = 创建爆冷案例(
                比赛日期="2026-05-11",
                联赛="西甲",
                轮次="自动同步",
                主队="巴塞罗那",
                客队="皇家马德里",
                预测结果="主胜",
                实际结果="客胜",
                预测比分="2-1/1-0",
                实际比分="1-2",
                预测概率=61.0,
                主队排名=1,
                客队排名=2,
                主队积分=80,
                客队积分=78,
                伤病影响="自动同步，待人工补充",
                战术变化="测试",
                心理因素="测试",
                赔率变化="测试",
                凯利指数="测试",
                盘口异常="测试",
                爆冷原因分析="测试",
                改进建议="测试",
            )
            self.assertTrue(library.添加案例(case))
            stdout_buffer = io.StringIO()
            with contextlib.redirect_stdout(stdout_buffer):
                added = library.添加案例(case)
            self.assertFalse(added)
            self.assertEqual(stdout_buffer.getvalue(), "")


class CompletedMemorySyncTest(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()

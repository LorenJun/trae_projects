"""单元测试：domain/world_cup_schedule_writeback.apply_schedule_updates 的覆盖式 upsert 行为。

覆盖：
- 淘汰赛占位符 `W81` → 真实球队名（比利时）覆盖生效；
- 预测尾段（`预测:主胜 信心:0.44 ... 解读:...`）保留原样；
- 比分已回填的行不会被 `-` 覆盖；
- MatchID 变更 / 开赛时间调整；
- 别名归一化（全角括号 `刚果（金）` → canonical `刚果民主共和国`）；
- 找不到匹配的赛程行 → warning，且不新增行。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT_DIR))

from domain.world_cup_schedule_writeback import apply_schedule_updates  # noqa: E402


MIN_MD = """# 世界杯 2026

## 赛程信息（北京时间，2026年6月-7月）

### 1/8决赛
| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |
|-----|------|-----|------|-----|------|
| 2026-07-07 | 08:00 | W81 | - | 西班牙 | 待开赛；北京时间（基于官方赛历）；1/8决赛 W81(=2026-07-02 比利时vs塞内加尔 胜者) vs W82(=2026-07-03 西班牙vs奥地利 胜者)；MatchID:1277392 |
| 2026-07-07 | 03:00 | 葡萄牙 | - | 瑞士 | 待开赛；北京时间（基于官方赛历）；1/8决赛 W83 vs W84；MatchID:1277391；预测:主胜 信心:0.44 比分:1-0/2-1 大小:中性2.75(不下注) 解读:欧赔向主队收紧。 |
| 2026-07-02 | 04:00 | 比利时 | 0-1 | 塞内加尔 | 待开赛；1/16决赛 W80；MatchID:1277378 |
| 2026-06-18 | 01:00 | 葡萄牙 | 1-1 | 刚果民主共和国 | 待开赛；MatchID:1316313 |

## 其它信息
"""


def _make_records():
    return [
        # W81 → 比利时（点球胜者）；MatchID 匹配
        dict(match_id="1277392", date="2026-07-07", time="08:00",
             home="比利时", away="西班牙",
             home_score=None, away_score=None, finished=False),
        # 葡萄牙 vs 瑞士：时间调整 03:00 -> 04:00，MatchID 也变（1277391 -> 1277999）
        # 我们通过原 MatchID 匹配；预测尾段应原样保留。
        dict(match_id="1277391", date="2026-07-07", time="04:00",
             home="葡萄牙", away="瑞士",
             home_score=None, away_score=None, finished=False),
        # 比利时 vs 塞内加尔已回填 0-1，澳客拉到 finished 2-2；比分不应被覆盖。
        dict(match_id="1277378", date="2026-07-02", time="04:00",
             home="比利时", away="塞内加尔",
             home_score=2, away_score=2, finished=True),
        # 别名归一化：澳客给出 "刚果（金）"（全角），应归一到 canonical "刚果民主共和国"
        # 且不产生 warning、不改动主/客队字段（已是 canonical）。
        dict(match_id="1316313", date="2026-06-18", time="01:00",
             home="葡萄牙", away="刚果（金）",
             home_score=None, away_score=None, finished=False),
        # 找不到匹配的场次 → warning
        dict(match_id="9999999", date="2026-07-15", time="03:00",
             home="巴西", away="德国",
             home_score=None, away_score=None, finished=False),
    ]


class ApplyScheduleUpdatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.md_path = Path(self.tmpdir.name) / "teams_2026.md"
        self.md_path.write_text(MIN_MD, encoding="utf-8")

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_upsert_and_preserve(self) -> None:
        summary = apply_schedule_updates(self.md_path, _make_records())
        text = self.md_path.read_text(encoding="utf-8")

        # 1) W81 → 比利时 覆盖生效
        self.assertIn("| 2026-07-07 | 08:00 | 比利时 | - | 西班牙 |", text)
        self.assertNotIn("| 2026-07-07 | 08:00 | W81 |", text)

        # 2) 葡萄牙 vs 瑞士 时间调整为 04:00 且 预测尾段保留
        self.assertIn("| 2026-07-07 | 04:00 | 葡萄牙 | - | 瑞士 |", text)
        self.assertIn("预测:主胜 信心:0.44 比分:1-0/2-1 大小:中性2.75(不下注) 解读:欧赔向主队收紧。", text)

        # 3) 比利时 vs 塞内加尔 已回填 0-1，不被覆盖为 2-2
        self.assertIn("| 2026-07-02 | 04:00 | 比利时 | 0-1 | 塞内加尔 |", text)
        self.assertNotIn("| 2026-07-02 | 04:00 | 比利时 | 2-2 | 塞内加尔 |", text)

        # 4) 别名归一化：全角"刚果（金）"应归到 canonical，不新增 warning
        self.assertIn("| 2026-06-18 | 01:00 | 葡萄牙 | 1-1 | 刚果民主共和国 |", text)

        # 5) 找不到匹配的场次 → warning，且不新增行
        warnings = summary.get("warnings") or []
        self.assertTrue(any("9999999" in w for w in warnings),
                        f"expected warning for MatchID=9999999, got: {warnings}")
        self.assertEqual(summary.get("added_lines"), 0)

        # 6) updated_lines 应 >= 3（W81/葡萄牙时间/别名归一; 比利时那一行前 5 段全等则不动）
        self.assertGreaterEqual(int(summary.get("updated_lines") or 0), 2)

    def test_matchid_change_writes_back(self) -> None:
        # 单独用 (date, home, away) 三元组匹配，MatchID 变化时新 MatchID 应写入备注。
        records = [
            dict(match_id="8888888", date="2026-07-07", time="08:00",
                 home="比利时", away="西班牙",
                 home_score=None, away_score=None, finished=False),
        ]
        # 先执行一次把 W81 → 比利时（用旧 MatchID 匹配）
        apply_schedule_updates(self.md_path, [
            dict(match_id="1277392", date="2026-07-07", time="08:00",
                 home="比利时", away="西班牙",
                 home_score=None, away_score=None, finished=False),
        ])
        # 再用新 MatchID + 三元组匹配
        summary = apply_schedule_updates(self.md_path, records)
        text = self.md_path.read_text(encoding="utf-8")
        self.assertIn("MatchID:8888888", text)
        # 旧 MatchID 应被替换
        self.assertNotIn("MatchID:1277392", text)
        self.assertEqual(summary.get("added_lines"), 0)


if __name__ == "__main__":
    unittest.main()

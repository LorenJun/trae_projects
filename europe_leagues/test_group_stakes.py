import unittest

from domain.group_stakes import (
    TeamStat,
    assess_stakes_scenario,
    classify_stakes_scenario,
    compute_group_standings,
    compute_qualification,
    parse_group_map,
)


def _group_info(groups):
    """groups: {组字母: [球队...]} -> `## 小组信息` markdown 段。"""
    lines = ["## 小组信息", ""]
    for letter, teams in groups.items():
        lines.append(f"### {letter}组")
        lines.append("| 序号 | 球队 |")
        lines.append("| - | - |")
        for i, t in enumerate(teams, start=1):
            lines.append(f"| {i} | {t} |")
        lines.append("")
    return "\n".join(lines)


def _schedule(rows):
    """rows: [(home, score, away)] -> `### 小组赛` markdown 段。"""
    lines = ["### 小组赛", "| 日期 | 时间 | 主队 | 比分 | 客队 | 备注 |", "| - | - | - | - | - | - |"]
    for home, score, away in rows:
        lines.append(f"| 2026-06-11 | 03:00 | {home} | {score} | {away} | x |")
    return "\n".join(lines)


# A 组完整两轮（墨/韩各 2 胜锁定前二，捷/南非两负）
_A_OPEN = [
    ("墨西哥", "2-0", "捷克"),
    ("韩国", "3-0", "南非"),
    ("墨西哥", "1-0", "南非"),
    ("韩国", "2-1", "捷克"),
]


class ParseAndStandingsTest(unittest.TestCase):
    def test_parse_group_map(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]})
        gm = parse_group_map(md)
        self.assertEqual(gm.get("墨西哥"), "A")
        self.assertEqual(gm.get("南非"), "A")
        self.assertEqual(len(gm), 4)

    def test_standings_aggregation(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]}) + "\n" + _schedule(_A_OPEN)
        groups = compute_group_standings(md)
        a = {s.team: s for s in groups["A"]}
        self.assertEqual(a["墨西哥"].pts, 6)
        self.assertEqual(a["韩国"].pts, 6)
        self.assertEqual(a["捷克"].pts, 0)
        self.assertEqual(a["南非"].pts, 0)
        self.assertEqual(a["墨西哥"].played, 2)
        self.assertEqual(groups["A"][0].rank, 1)

    def test_team_alias_normalized(self):
        md = _group_info({"H": ["葡萄牙", "刚果（金）", "乌兹别克斯坦", "哥伦比亚"]})
        gm = parse_group_map(md)
        self.assertIn("刚果民主共和国", gm)
        self.assertNotIn("刚果（金）", gm)

    def test_exclude_pair_removes_self_match(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]}) + "\n" + _schedule(_A_OPEN)
        groups = compute_group_standings(md, exclude_pair=("墨西哥", "捷克"))
        a = {s.team: s for s in groups["A"]}
        self.assertEqual(a["墨西哥"].played, 1)
        self.assertEqual(a["捷克"].played, 1)
        self.assertEqual(a["韩国"].played, 2)


def _full_12_group_md():
    """12 组 × 4 队、全部踢满 3 场，用于最佳第三名判定。"""
    letters = list("ABCDEFGHIJKL")
    groups = {}
    rows = []
    for letter in letters:
        t1, t2, t3, t4 = (f"{letter}1", f"{letter}2", f"{letter}3", f"{letter}4")
        groups[letter] = [t1, t2, t3, t4]
        rows += [
            (t1, "1-0", t2), (t1, "1-0", t3), (t1, "1-0", t4),
            (t2, "1-0", t3), (t2, "1-0", t4),
            (t3, "1-0", t4),
        ]
    return _group_info(groups) + "\n" + _schedule(rows), groups


class QualificationTest(unittest.TestCase):
    def test_best_third_full_complete(self):
        md, _ = _full_12_group_md()
        groups = compute_group_standings(md)
        status = compute_qualification(groups)
        self.assertEqual(status["A1"], "已出线")
        self.assertEqual(status["A2"], "已出线")
        thirds = [status[f"{l}3"] for l in "ABCDEFGHIJKL"]
        self.assertEqual(thirds.count("已出线"), 8)
        self.assertEqual(thirds.count("已出局"), 4)
        self.assertEqual(status["A4"], "已出局")

    def test_provisional_status_when_incomplete(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]}) + "\n" + _schedule(_A_OPEN)
        groups = compute_group_standings(md)
        status = compute_qualification(groups)
        self.assertEqual(status["墨西哥"], "已出线")
        self.assertEqual(status["韩国"], "已出线")


def _mk_group(letter, specs):
    """specs: [(team, won, draw, lost, gf, ga)]，played=won+draw+lost。返回排序并标 rank 的行。"""
    rows = []
    for team, w, d, l, gf, ga in specs:
        rows.append(TeamStat(team=team, group=letter, played=w + d + l, won=w, draw=d, lost=l, gf=gf, ga=ga))
    rows.sort(key=lambda s: (-s.pts, -s.gd, -s.gf, s.team))
    for i, s in enumerate(rows, start=1):
        s.rank = i
    return rows


class ScenarioClassificationTest(unittest.TestCase):
    """直接喂入 groups + status，精确覆盖每种情景类型。"""

    def _classify(self, rows, status, home, away):
        groups = {rows[0].group: rows}
        return classify_stakes_scenario(home, away, groups, status)

    def test_dead_rubber_both_qualified(self):
        rows = _mk_group("A", [("A1", 2, 0, 0, 4, 0), ("A2", 2, 0, 0, 4, 1), ("A3", 0, 0, 2, 0, 4), ("A4", 0, 0, 2, 1, 4)])
        status = {"A1": "已出线", "A2": "已出线", "A3": "已出局", "A4": "已出局"}
        sc = self._classify(rows, status, "A1", "A2")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "dead_rubber_both")
        self.assertTrue(sc.ou_neutral)
        self.assertEqual(sc.stake_scale, 0.0)
        self.assertLess(sc.confidence_penalty, 0.0)

    def test_dead_rubber_both_eliminated(self):
        rows = _mk_group("A", [("A1", 2, 0, 0, 4, 0), ("A2", 2, 0, 0, 4, 1), ("A3", 0, 0, 2, 0, 4), ("A4", 0, 0, 2, 1, 4)])
        status = {"A1": "已出线", "A2": "已出线", "A3": "已出局", "A4": "已出局"}
        sc = self._classify(rows, status, "A3", "A4")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "dead_rubber_both")

    def test_qualified_vs_eliminated(self):
        rows = _mk_group("A", [("A1", 2, 0, 0, 4, 0), ("A2", 2, 0, 0, 4, 1), ("A3", 0, 0, 2, 0, 4), ("A4", 0, 0, 2, 1, 4)])
        status = {"A1": "已出线", "A2": "已出线", "A3": "已出局", "A4": "已出局"}
        sc = self._classify(rows, status, "A1", "A3")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "qualified_vs_eliminated")
        self.assertTrue(sc.ou_neutral)

    def test_one_side_secured(self):
        rows = _mk_group("B", [("B1", 2, 0, 0, 4, 0), ("B2", 1, 0, 1, 2, 2), ("B3", 1, 0, 1, 2, 2), ("B4", 0, 0, 2, 0, 4)])
        status = {"B1": "已出线", "B2": "争夺中", "B3": "争夺中", "B4": "争夺中"}
        sc = self._classify(rows, status, "B1", "B2")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "one_side_secured")
        self.assertTrue(sc.ou_neutral)

    def test_one_eliminated(self):
        rows = _mk_group("B", [("B1", 2, 0, 0, 4, 0), ("B2", 1, 0, 1, 2, 2), ("B3", 1, 0, 1, 2, 2), ("B4", 0, 0, 2, 0, 4)])
        status = {"B1": "争夺中", "B2": "争夺中", "B3": "争夺中", "B4": "已出局"}
        sc = self._classify(rows, status, "B2", "B4")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "one_eliminated")
        self.assertFalse(sc.ou_neutral)  # 一方仍有动机，大小球不强制中性

    def test_mutual_draw_advances(self):
        # 两队各 4 分（打平后各 5 分），另两队理论最高 3 分 -> 默契球风险
        rows = _mk_group("C", [("C1", 1, 1, 0, 3, 1), ("C2", 1, 1, 0, 3, 1), ("C3", 0, 0, 2, 0, 3), ("C4", 0, 0, 2, 1, 3)])
        status = {"C1": "争夺中", "C2": "争夺中", "C3": "争夺中", "C4": "争夺中"}
        sc = self._classify(rows, status, "C1", "C2")
        self.assertTrue(sc.distortion)
        self.assertEqual(sc.type, "mutual_draw_advances")
        self.assertTrue(sc.ou_neutral)

    def test_final_round_live_both_motivated_normal(self):
        # 末轮但四队同分、无人锁定、打平不双双晋级 -> 生死战，不降级
        rows = _mk_group("D", [("D1", 1, 0, 1, 1, 1), ("D2", 1, 0, 1, 1, 1), ("D3", 1, 0, 1, 1, 1), ("D4", 1, 0, 1, 1, 1)])
        status = {"D1": "争夺中", "D2": "争夺中", "D3": "争夺中", "D4": "争夺中"}
        sc = self._classify(rows, status, "D1", "D2")
        self.assertFalse(sc.distortion)
        self.assertEqual(sc.type, "normal")

    def test_opener_not_misjudged(self):
        rows = _mk_group("E", [("E1", 0, 0, 0, 0, 0), ("E2", 0, 0, 0, 0, 0), ("E3", 0, 0, 0, 0, 0), ("E4", 0, 0, 0, 0, 0)])
        status = {t.team: "争夺中" for t in rows}
        sc = self._classify(rows, status, "E1", "E2")
        self.assertFalse(sc.distortion)

    def test_second_round_not_misjudged(self):
        rows = _mk_group("F", [("F1", 1, 0, 0, 1, 0), ("F2", 1, 0, 0, 1, 0), ("F3", 0, 0, 1, 0, 1), ("F4", 0, 0, 1, 0, 1)])
        status = {"F1": "争夺中", "F2": "争夺中", "F3": "争夺中", "F4": "争夺中"}
        # 双方各踢 1 场 -> 非末轮
        sc = self._classify(rows, status, "F1", "F2")
        self.assertFalse(sc.distortion)

    def test_cross_group_pair_normal(self):
        rows = _mk_group("G", [("G1", 2, 0, 0, 4, 0), ("G2", 0, 0, 2, 0, 4), ("G3", 1, 0, 1, 1, 1), ("G4", 1, 0, 1, 1, 1)])
        groups = {"G": rows, "H": _mk_group("H", [("H1", 2, 0, 0, 4, 0), ("H2", 0, 0, 2, 0, 4), ("H3", 1, 0, 1, 1, 1), ("H4", 1, 0, 1, 1, 1)])}
        status = {s.team: "争夺中" for r in groups.values() for s in r}
        sc = classify_stakes_scenario("G1", "H1", groups, status)
        self.assertFalse(sc.distortion)


class AssessEntryTest(unittest.TestCase):
    def test_assess_via_md_text_dead_rubber(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]}) + "\n" + _schedule(_A_OPEN)
        sc = assess_stakes_scenario("墨西哥", "韩国", md_text=md)
        self.assertIsInstance(sc, dict)
        self.assertTrue(sc["distortion"])
        self.assertEqual(sc["type"], "dead_rubber_both")

    def test_assess_opener_via_md_normal(self):
        md = _group_info({"A": ["墨西哥", "韩国", "捷克", "南非"]}) + "\n" + _schedule([])
        sc = assess_stakes_scenario("墨西哥", "韩国", md_text=md)
        self.assertFalse(sc["distortion"])
        self.assertEqual(sc["type"], "normal")

    def test_assess_missing_md_safe_normal(self):
        sc = assess_stakes_scenario("墨西哥", "韩国", teams_md_path="/nonexistent/path.md")
        self.assertFalse(sc["distortion"])
        self.assertEqual(sc["type"], "normal")

    def test_assess_bad_md_safe_normal(self):
        sc = assess_stakes_scenario("X", "Y", md_text="garbage not markdown")
        self.assertFalse(sc["distortion"])


if __name__ == "__main__":
    unittest.main()

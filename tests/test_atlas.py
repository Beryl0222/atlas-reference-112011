"""编校链接工具的规则测试。

测试只使用 data/ 里的虚构交接资料；需要模拟资料变动的场景在临时目录建副本，
绝不修改原始 JSON。断言按需求条目组织，方便编校逐条复核。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from atlas.gazetteer import (
    aliases_of,
    all_tokens,
    branch_units,
    continuation_of,
    resolve_name,
    split_successors,
)
from atlas.model import _cjk_number, load_workspace
from atlas.scan import (
    analyze_edition,
    analyze_paragraph,
    cjk_year,
    downstream_expand,
    explicit_year,
    resolve_year,
    scan_mentions,
)
from atlas.sheets import cover_groups
from atlas.workflow import (
    BLOCKER_NAME,
    BLOCKER_PRESS,
    BLOCKER_SHEET,
    Ledger,
    blocked_links,
    build_links,
    press_gate,
    record_decision,
    recheck,
    reverse_index,
    sheet_fact_hash,
    text_hash,
    web_export,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def workspace():
    return load_workspace(DATA)


def link_by(links, paragraph_id, interpretation, edition=None):
    out = [
        link for link in links
        if link.paragraph_id == paragraph_id and link.interpretation == interpretation
        and (edition is None or link.edition == edition)
    ]
    assert len(out) == 1, f"候选不唯一：{[(l.paragraph_id, l.interpretation) for l in out]}"
    return out[0]


class GazetteerTests(unittest.TestCase):
    def setUp(self):
        self.ws = workspace()

    def test_alias_index_keeps_both_forms(self):
        tokens = all_tokens(self.ws)
        self.assertEqual(set(tokens["上渡口"]), {"N1"})
        self.assertEqual(set(tokens["下渡口"]), {"N2"})
        self.assertIn("双湾西缘", tokens)
        n1 = next(row for row in self.ws.names if row.id == "N1")
        self.assertEqual(aliases_of(n1), ("上渡口",))

    def test_same_name_two_places_need_context(self):
        # 不加支系约束：同名异地两个候选都在。
        both = {c.record_id for c in resolve_name(self.ws, "旧渡口", 1980)}
        self.assertEqual(both, {"N1", "N2"})
        # 北支约束只留 A 单元的 N1，南支约束只留 S 单元的 N2。
        north, north_units = {"A", "B", "C"}, frozenset({"A", "B", "C"})
        self.assertEqual(
            [c.record_id for c in resolve_name(self.ws, "旧渡口", 1980, "北支", north_units)],
            ["N1"],
        )
        south, south_units = {"S", "C", "D"}, frozenset({"S", "C", "D"})
        self.assertEqual(
            [c.record_id for c in resolve_name(self.ws, "旧渡口", 1980, "南支", south_units)],
            ["N2"],
        )

    def test_split_distinguished_from_continuation(self):
        n3 = next(row for row in self.ws.names if row.id == "N3")
        n4 = next(row for row in self.ws.names if row.id == "N4")
        # N3 旧双湾在 2000 年拆成西湾 N5、东湾 N6 两条独立地物。
        successors = {row.id for row in split_successors(self.ws, n3)}
        self.assertEqual(successors, {"N5", "N6"})
        # N4 是同名续称但无统一边界主体，不能当成同一地物的简单改名。
        self.assertIsNone(continuation_of(self.ws, n3))
        self.assertIn("无统一边界主体", n4.history)

    def test_year_is_a_hard_constraint(self):
        # 西湾 2000 年才设立：1980 年没有在效记录，2020 年才有 N5。
        self.assertEqual(resolve_name(self.ws, "西湾", 1980), [])
        self.assertEqual([c.record_id for c in resolve_name(self.ws, "西湾", 2020)], ["N5"])

    def test_branch_unit_sets_exclude_confluence_from_source(self):
        source, whole = branch_units(list(self.ws.reaches), "南支")
        self.assertEqual(source, {"S"})
        self.assertEqual(whole, {"S", "C", "D"})


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.ws = workspace()

    def test_cjk_numbers_and_years(self):
        self.assertEqual(_cjk_number("七"), 7)
        self.assertEqual(_cjk_number("十一"), 11)
        self.assertEqual(cjk_year("一九八〇"), 1980)
        self.assertEqual(explicit_year("图示取一九八〇年。"), 1980)

    def test_section_pages_cover_multi_page_titles(self):
        j1 = next(row for row in self.ws.sections if row.id == "J1")
        self.assertEqual(j1.pages, (7, 8))
        j3 = next(row for row in self.ws.sections if row.id == "J3")
        self.assertEqual(j3.pages, (11, 12))

    def test_caption_year_overrides_section_default(self):
        p1 = self.ws.paragraph("P1", "春刊")
        year, basis = resolve_year(self.ws, p1)
        self.assertEqual(year, 1980)
        self.assertIn("题注", basis)
        # 夏刊同节备注声明保留年款，同样回到 1980。
        p1x = self.ws.paragraph("P1", "夏刊")
        self.assertEqual(resolve_year(self.ws, p1x)[0], 1980)

    def test_present_tense_word_beats_section_caption_year(self):
        # P3 与题注同章节但“现今”自述现状，不能套用 1980 年款。
        p3 = self.ws.paragraph("P3", "春刊")
        self.assertEqual(resolve_year(self.ws, p3)[0], 2020)

    def test_old_atlas_quote_uses_catalog_year_and_range(self):
        p4 = self.ws.paragraph("P4", "春刊")
        refs = analyze_paragraph(self.ws, p4)
        ref = refs[0]
        self.assertEqual(ref.year, 1980)
        self.assertTrue(ref.quoted)
        self.assertEqual(
            "".join(ref.interpretations[0].units), "AB",
        )
        # 夏刊改写为阿拉伯数字年款，仍是引文。
        p4x = self.ws.paragraph("P4", "夏刊")
        self.assertTrue(analyze_paragraph(self.ws, p4x)[0].quoted)

    def test_range_expands_along_reaches_not_admin_bounds(self):
        self.assertEqual("".join(downstream_expand(self.ws, {"A"}, {"B", "C"})), "ABC")
        self.assertEqual("".join(downstream_expand(self.ws, {"S"}, {"C"})), "CS")

    def test_p1_north_branch_inherits_full_range_and_disambiguates(self):
        refs = analyze_edition(self.ws, "春刊")
        range_ref = next(r for r in refs["P1"] if r.kind == "范围指代")
        anaphora = next(r for r in refs["P1"] if r.kind == "承接语")
        self.assertEqual(range_ref.branch, "北支")
        self.assertEqual("".join(range_ref.interpretations[0].units), "ABC")
        # 同名的南支 N2 被同段北支承接语排除。
        candidate_ids = {c.record_id for c in range_ref.interpretations[0].candidates}
        self.assertIn("N1", candidate_ids)
        self.assertNotIn("N2", candidate_ids)
        # 承接语回到上文 ABC，而不是孤立地只给源段 A。
        self.assertIn("承接上文范围", anaphora.interpretations[0].label)
        self.assertEqual("".join(anaphora.interpretations[0].units), "ABC")

    def test_old_boundary_reading_goes_back_to_predecessor(self):
        p2 = self.ws.paragraph("P2", "春刊")
        ref = analyze_paragraph(self.ws, p2)[0]
        labels = [(item.label, "".join(item.units)) for item in ref.interpretations]
        self.assertIn(("西湾采用旧界（N3）", "BC"), labels)
        self.assertIn(("西湾按N5解释", "B"), labels)

    def test_south_branch_keeps_multiple_interpretations(self):
        p5 = self.ws.paragraph("P5", "春刊")
        ref = analyze_paragraph(self.ws, p5)[0]
        labels = {item.label: "".join(item.units) for item in ref.interpretations}
        self.assertEqual(labels["南支源段"], "S")
        self.assertEqual(labels["南支全支系（含汇入点）"], "CDS")
        self.assertTrue(any("承接语" in c for c in ref.cautions))

    def test_trimmed_sentence_leaves_contested_name(self):
        # 夏刊 P3 删掉“东湾”限定语，只剩无统一边界主体的“双湾”。
        refs = analyze_edition(self.ws, "夏刊")
        ref = refs["P3"][0]
        self.assertEqual(ref.interpretations[0].candidates[0].relation, "无统一边界主体")

    def test_mentions_are_longest_match(self):
        mentions = [m.token for m in scan_mentions(self.ws, "上渡口至双湾西缘设砂坝")]
        self.assertIn("双湾西缘", mentions)
        self.assertNotIn("双湾", mentions)


class SheetTests(unittest.TestCase):
    def setUp(self):
        self.ws = workspace()

    def test_full_coverage_group_no_single_thematic_substitute(self):
        groups = cover_groups(self.ws, "春刊", 1980, ("A", "B", "C"))
        combos = {tuple(group.sheet_ids) for group in groups}
        self.assertEqual(combos, {("F1", "F2")})
        # 单张 F1 只覆盖 AB，不允许顶替整段 ABC。
        self.assertNotIn(("F1",), combos)

    def test_extra_coverage_allowed_and_minimal_sets_returned(self):
        groups = cover_groups(self.ws, "春刊", 1980, ("A", "B"))
        self.assertEqual({tuple(g.sheet_ids) for g in groups}, {("F1",)})

    def test_no_group_when_coverage_incomplete(self):
        self.assertEqual(cover_groups(self.ws, "春刊", 2020, ("C", "D", "S")), [])

    def test_edition_and_year_are_strict(self):
        # 夏刊没有 2020 年覆盖 B 的图，不能借春刊 F1。
        self.assertEqual(cover_groups(self.ws, "夏刊", 2020, ("B",)), [])

    def test_replacement_and_move_flags(self):
        groups = cover_groups(self.ws, "夏刊", 1980, ("A", "B", "C"))
        group = next(g for g in groups if set(g.sheet_ids) == {"F1", "F2", "F5"})
        self.assertEqual(group.status, "印前定位未定")
        joined = " ".join(group.flags)
        self.assertIn("E4", joined)
        self.assertIn("E8", joined)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.ws = workspace()
        self.tmp = tempfile.mkdtemp()
        self.ledger = Ledger(Path(self.tmp) / "ledger.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spring_p1_link_is_complete_group(self):
        links = build_links(self.ws, "春刊")
        link = link_by(links, "P1", "旧渡口→双湾")
        self.assertEqual(link.blockers, [])
        self.assertEqual(
            [tuple(g.sheet_ids) for g in link.groups], [("F1", "F2")],
        )

    def test_blocker_categories(self):
        summer = build_links(self.ws, "夏刊")
        spring = build_links(self.ws, "春刊")
        self.assertIn(
            BLOCKER_PRESS, link_by(summer, "P1", "旧渡口→双湾").blockers,
        )
        self.assertIn(
            BLOCKER_NAME, link_by(summer, "P2", "西湾按N5解释").blockers,
        )
        self.assertIn(
            BLOCKER_SHEET,
            link_by(spring, "P5", "南支全支系（含汇入点）").blockers,
        )
        self.assertIn(
            BLOCKER_SHEET, link_by(spring, "P3", "双湾按N4解释").blockers,
        )

    def test_web_keeps_text_with_pending_marker(self):
        export = web_export(self.ws, "春刊", self.ledger)
        pending = next(row for row in export if row["段落"] == "P5" and "全支系" in row["采用解释"])
        self.assertEqual(pending["发布方式"], "仅刊文字，图幅待考")
        ready = next(row for row in export if row["段落"] == "P4")
        self.assertEqual(ready["发布方式"], "文字与组图链接同时上线")

    def test_press_gate_requires_fixed_number_page_edition(self):
        gate = press_gate(self.ws, "春刊", self.ledger)
        self.assertFalse(gate["放行"])
        links = build_links(self.ws, "春刊")
        link = link_by(links, "P4", "上渡口→双湾西缘")
        record_decision(
            self.ws, self.ledger, link, link.groups[0],
            "编辑甲", "2026-06-02", "纸刊", "旧版图说1980，F1覆盖AB",
        )
        # 只确认一处，闸门仍不放行，但已确认计数增加。
        gate = press_gate(self.ws, "春刊", self.ledger)
        self.assertFalse(gate["放行"])
        self.assertEqual(gate["已定稿链接"], 1)

    def test_press_decision_rejected_while_press_position_open(self):
        links = build_links(self.ws, "夏刊")
        link = link_by(links, "P1", "旧渡口→双湾")
        group = link.groups[0]
        self.assertEqual(group.status, "印前定位未定")
        with self.assertRaises(ValueError):
            record_decision(
                self.ws, self.ledger, link, group,
                "编辑乙", "2026-08-05", "纸刊", "不应通过",
            )
        # 网页渠道可以先确认。
        decision = record_decision(
            self.ws, self.ledger, link, group,
            "编辑乙", "2026-08-05", "网页", "E5先上线正文",
        )
        self.assertEqual(decision.numbers, ["11-甲", "11-乙", "11-丙"])

    def test_recheck_text_change_invalidates_decision(self):
        links = build_links(self.ws, "春刊")
        link = link_by(links, "P1", "旧渡口→双湾")
        record_decision(
            self.ws, self.ledger, link, link.groups[0],
            "编辑甲", "2026-06-02", "纸刊", "test",
        )
        edited = self._mutate_paragraph("P1", "春刊", text="旧渡口以下至双湾。")
        result = recheck(edited, self.ledger, "春刊")[0]
        self.assertEqual(result.state, "正文已变")

    def test_recheck_sheet_change_invalidates_decision(self):
        links = build_links(self.ws, "春刊")
        link = link_by(links, "P1", "旧渡口→双湾")
        record_decision(
            self.ws, self.ledger, link, link.groups[0],
            "编辑甲", "2026-06-02", "纸刊", "test",
        )
        changed = self._mutate_coverage("F1", "春刊", units=["B"])
        result = recheck(changed, self.ledger, "春刊")[0]
        self.assertEqual(result.state, "图幅事实已变")
        self.assertTrue(any("图号/页码/年代/覆盖变动" in d for d in result.details))

    def test_recheck_repagination_keeps_interpretation(self):
        links = build_links(self.ws, "春刊")
        link = link_by(links, "P1", "旧渡口→双湾")
        record_decision(
            self.ws, self.ledger, link, link.groups[0],
            "编辑甲", "2026-06-02", "纸刊", "test",
        )
        moved = self._mutate_paragraph("P1", "春刊", position="十五页第一栏")
        result = recheck(moved, self.ledger, "春刊")[0]
        self.assertEqual(result.state, "仍然有效")
        self.assertTrue(any("版面挪行" in d for d in result.details))

    def test_reverse_index_is_edition_scoped(self):
        spring = reverse_index(self.ws, "春刊")["F1"]
        summer = reverse_index(self.ws, "夏刊")["F1"]
        self.assertEqual(spring["图号"], "7-甲")
        self.assertEqual(summer["图号"], "11-甲")
        paragraphs = {row["段落"] for row in spring["引用段落"]}
        self.assertIn("P4", paragraphs)
        # 旧版图号不会出现在新版反查里。
        self.assertTrue(all(row["段落"] in {"P1", "P2", "P3", "P4"} for row in summer["引用段落"]))

    def test_blocked_list_filters_by_category(self):
        all_blocked = blocked_links(self.ws, "夏刊")
        name_only = blocked_links(self.ws, "夏刊", BLOCKER_NAME)
        press_only = blocked_links(self.ws, "夏刊", BLOCKER_PRESS)
        self.assertTrue(len(all_blocked) > len(name_only))
        self.assertTrue(all(BLOCKER_NAME in row["阻塞分类"] for row in name_only))
        self.assertTrue(all(BLOCKER_PRESS in row["阻塞分类"] for row in press_only))

    def test_hashes_are_stable_and_distinct(self):
        p1 = self.ws.paragraph("P1", "春刊")
        self.assertEqual(text_hash(p1), text_hash(p1))
        sheets = [self.ws.sheet("F1", "春刊"), self.ws.sheet("F2", "春刊")]
        changed_sheets = [self.ws.sheet("F1", "夏刊"), self.ws.sheet("F2", "夏刊")]
        self.assertNotEqual(sheet_fact_hash(sheets), sheet_fact_hash(changed_sheets))

    def _mutate_paragraph(self, paragraph_id, edition, text=None, position=None):
        mirror = Path(self.tmp) / f"p_{paragraph_id}_{edition}"
        if mirror.exists():
            shutil.rmtree(mirror)
        shutil.copytree(DATA, mirror)
        path = mirror / "paragraphs.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            if row["id"] == paragraph_id and row["版次"] == edition:
                if text is not None:
                    row["正文"] = text
                if position is not None:
                    row["位置"] = position
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return load_workspace(mirror)

    def _mutate_coverage(self, sheet_id, edition, units):
        mirror = Path(self.tmp) / f"f_{sheet_id}_{edition}"
        if mirror.exists():
            shutil.rmtree(mirror)
        shutil.copytree(DATA, mirror)
        path = mirror / "coverage.json"
        rows = json.loads(path.read_text(encoding="utf-8"))
        for row in rows:
            if row["id"] == sheet_id and row["版次"] == edition:
                row["区域"] = units
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return load_workspace(mirror)


if __name__ == "__main__":
    unittest.main()

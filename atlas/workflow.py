"""编辑确认台账、复核、渠道闸门、反查与阻塞分类。

台账把“编辑确认过的解释”和“资料事实”分开存：

- 确认只对所依据的正文哈希与图幅事实哈希有效；
- 版面挪行（位置变化、正文不变）不动解释；
- 正文删减限定语、图幅换稿改覆盖或分图编号，都让原确认失效并要求重新决定；
- 线上允许带“图幅待考”标记发布文字；纸刊要求图号、页码、版次全部定死。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from atlas.model import Paragraph, Sheet, Workspace
from atlas.scan import Reference, analyze_edition
from atlas.sheets import CoverGroup, cover_groups, note_flags

BLOCKER_NAME = "地名解释"
BLOCKER_SHEET = "图幅条件"
BLOCKER_PRESS = "印前定位"


def text_hash(paragraph: Paragraph) -> str:
    return hashlib.sha256(paragraph.text.encode("utf-8")).hexdigest()[:12]


def sheet_fact_hash(sheets: list[Sheet]) -> str:
    payload = [
        (sheet.id, sheet.edition, sheet.number, sheet.page, sheet.year, tuple(sheet.units))
        for sheet in sorted(sheets, key=lambda item: item.id)
    ]
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


@dataclass
class Link:
    """一个指代在一个解释下形成的出版链接。"""

    paragraph_id: str
    edition: str
    clause: str
    kind: str
    year: int
    year_basis: str
    quoted: bool
    branch: str | None
    interpretation: str
    units: tuple[str, ...]
    groups: list[CoverGroup] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    decision_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "段落": self.paragraph_id,
            "版次": self.edition,
            "原文": self.clause,
            "类型": self.kind,
            "年代": self.year,
            "年代依据": self.year_basis,
            "引自旧版": self.quoted,
            "支系": self.branch,
            "采用解释": self.interpretation,
            "覆盖单元": list(self.units),
            "候选图幅组": [group.to_dict() for group in self.groups],
            "待考提示": list(self.cautions),
            "阻塞分类": list(self.blockers),
            "确认台账号": self.decision_id,
        }


def build_links(workspace: Workspace, edition: str) -> list[Link]:
    """每个解释各生成一条链接，多解释并存，供编校取舍与内部复核。"""
    analysis = analyze_edition(workspace, edition)
    links: list[Link] = []
    for paragraph in workspace.paragraphs_of(edition):
        for reference in analysis.get(paragraph.id, []):
            for interpretation in reference.interpretations:
                groups = cover_groups(workspace, edition, reference.year, interpretation.units)
                blockers, cautions = _classify(workspace, reference, groups, interpretation)
                links.append(
                    Link(
                        paragraph.id, edition, reference.text, reference.kind,
                        reference.year, reference.year_basis, reference.quoted,
                        reference.branch, interpretation.label, interpretation.units,
                        groups, reference.cautions + cautions, blockers,
                    )
                )
    return links


def _classify(
    workspace: Workspace,
    reference: Reference,
    groups: list[CoverGroup],
    interpretation,
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    cautions: list[str] = []
    if any("在效记录" in item for item in reference.cautions):
        blockers.append(BLOCKER_NAME)
    if any(
        candidate.relation == "无统一边界主体"
        for item in reference.interpretations
        for candidate in item.candidates
    ):
        blockers.append(BLOCKER_NAME)
    if len(reference.interpretations) > 1:
        cautions.append("该指代存在多种解释，需编校确认")
    if not groups:
        blockers.append(BLOCKER_SHEET)
        cautions.append(f"{reference.year}年没有完整覆盖{''.join(interpretation.units)}的在版图幅")
    for group in groups:
        if group.status == "图幅待考":
            blockers.append(BLOCKER_SHEET)
        if group.status == "印前定位未定":
            blockers.append(BLOCKER_PRESS)
    if reference.paragraph_id and reference.edition:
        _, _, _, unpublish = note_flags(
            workspace.notes_for(reference.paragraph_id, reference.edition)
        )
        if unpublish:
            blockers.append(BLOCKER_PRESS)
    return sorted(set(blockers)), cautions


# ---------------------------------------------------------------- 确认台账


@dataclass
class Decision:
    decision_id: str
    edition: str
    paragraph_id: str
    clause: str
    interpretation: str
    year: int
    units: list[str]
    sheet_ids: list[str]
    numbers: list[str]
    pages: list[int]
    text_hash: str
    sheet_fact_hash: str
    decided_by: str
    date: str
    basis: str
    channel: str  # 网页 / 纸刊
    position: str

    def to_dict(self) -> dict:
        return asdict(self)


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.decisions: list[Decision] = []
        if self.path.exists():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.decisions = [Decision(**row) for row in payload]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([item.to_dict() for item in self.decisions], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def next_id(self) -> str:
        return f"D{len(self.decisions) + 1:03d}"

    def add(self, decision: Decision) -> None:
        self.decisions.append(decision)
        self.save()

    def for_paragraph(self, paragraph_id: str, edition: str) -> list[Decision]:
        return [
            row for row in self.decisions
            if row.paragraph_id == paragraph_id and row.edition == edition
        ]

    def matching(self, link: Link) -> list[Decision]:
        return [
            row for row in self.decisions
            if row.edition == link.edition
            and row.paragraph_id == link.paragraph_id
            and row.clause == link.clause
            and row.interpretation == link.interpretation
        ]


def record_decision(
    workspace: Workspace,
    ledger: Ledger,
    link: Link,
    group: CoverGroup,
    decided_by: str,
    date: str,
    channel: str,
    basis: str,
) -> Decision:
    if channel == "纸刊" and group.status != "可链接":
        raise ValueError(f"纸刊确认要求印前已定，当前状态：{group.status}（{group.flags}）")
    paragraph = workspace.paragraph(link.paragraph_id, link.edition)
    sheets = [workspace.sheet(sheet_id, link.edition) for sheet_id in group.sheet_ids]
    decision = Decision(
        ledger.next_id(), link.edition, link.paragraph_id, link.clause,
        link.interpretation, link.year, list(link.units),
        list(group.sheet_ids), list(group.numbers), list(group.pages),
        text_hash(paragraph), sheet_fact_hash(sheets),
        decided_by, date, basis, channel, paragraph.position,
    )
    ledger.add(decision)
    return decision


# ---------------------------------------------------------------- 复核


@dataclass
class RecheckResult:
    decision_id: str
    state: str  # 仍然有效 / 正文已变 / 图幅事实已变 / 段落已撤
    details: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def recheck(workspace: Workspace, ledger: Ledger, edition: str | None = None) -> list[RecheckResult]:
    results: list[RecheckResult] = []
    for decision in ledger.decisions:
        if edition and decision.edition != edition:
            continue
        details: list[str] = []
        try:
            paragraph = workspace.paragraph(decision.paragraph_id, decision.edition)
        except KeyError:
            results.append(RecheckResult(decision.decision_id, "段落已撤", ["该版次中找不到原段落"]))
            continue
        state = "仍然有效"
        if text_hash(paragraph) != decision.text_hash:
            state = "正文已变"
            details.append("正文哈希不一致：限定语可能被删减，须重新决定解释")
        current_sheets: list[Sheet] = []
        for sheet_id in decision.sheet_ids:
            try:
                current_sheets.append(workspace.sheet(sheet_id, decision.edition))
            except KeyError:
                state = "图幅事实已变"
                details.append(f"图幅{sheet_id}在{decision.edition}已不存在")
        if current_sheets and sheet_fact_hash(current_sheets) != decision.sheet_fact_hash:
            state = "图幅事实已变"
            before = ", ".join(decision.numbers)
            after = ", ".join(sheet.number for sheet in current_sheets)
            details.append(f"图号/页码/年代/覆盖变动（确认时：{before}；现行：{after}），须复核各处引用")
        if state == "仍然有效" and paragraph.position != decision.position:
            details.append(
                f"版面挪行（{decision.position}→{paragraph.position}），正文与图幅事实未变，解释保留"
            )
        results.append(RecheckResult(decision.decision_id, state, details))
    return results


# ---------------------------------------------------------------- 渠道闸门


def web_export(workspace: Workspace, edition: str, ledger: Ledger) -> list[dict]:
    """线上版：链接可点；未决处保留文字并明确提示图幅待考。"""
    out = []
    for link in build_links(workspace, edition):
        decisions = ledger.matching(link)
        payload = link.to_dict()
        if decisions:
            payload["确认台账号"] = decisions[-1].decision_id
        if link.blockers:
            payload["发布方式"] = "仅刊文字，图幅待考"
        else:
            payload["发布方式"] = "文字与组图链接同时上线"
        out.append(payload)
    return out


def press_gate(workspace: Workspace, edition: str, ledger: Ledger) -> dict:
    """纸刊送印前：每处链接必须有图号、页码、版次定死的确认。"""
    violations = []
    decided = 0
    for link in build_links(workspace, edition):
        match = [
            row for row in ledger.matching(link)
            if row.channel == "纸刊" and row.numbers and row.pages
        ]
        if not match:
            violations.append({
                "段落": link.paragraph_id,
                "原文": link.clause,
                "解释": link.interpretation,
                "阻塞分类": link.blockers or ["未确认"],
            })
        else:
            decided += 1
    return {
        "版次": edition,
        "已定稿链接": decided,
        "放行": not violations,
        "未通过项": violations,
    }


# ---------------------------------------------------------------- 反查与阻塞清单


def reverse_index(workspace: Workspace, edition: str) -> dict[str, dict]:
    """读者从一张图回查该版图集里引用它的段落；旧版图号只在旧版下命中。"""
    index: dict[str, dict] = {}
    for link in build_links(workspace, edition):
        for group in link.groups:
            for sheet_id, number in zip(group.sheet_ids, group.numbers):
                entry = index.setdefault(sheet_id, {
                    "图幅": sheet_id,
                    "版次": edition,
                    "图号": number,
                    "引用段落": [],
                })
                entry["引用段落"].append({
                    "段落": link.paragraph_id,
                    "原文": link.clause,
                    "解释": link.interpretation,
                    "覆盖单元": list(link.units),
                })
    return index


def blocked_links(workspace: Workspace, edition: str, blocker: str | None = None) -> list[dict]:
    links = [link for link in build_links(workspace, edition) if link.blockers]
    if blocker:
        links = [link for link in links if blocker in link.blockers]
    return [link.to_dict() for link in links]

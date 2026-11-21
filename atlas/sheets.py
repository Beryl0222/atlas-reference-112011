"""按年代与覆盖匹配图幅组，并从 desk 记录换稿/印前状态。

匹配规则：

- 只在同一版次、同一观测年内选图；旧版图号只能回指旧版记录，不跨版沿用；
- 一组图必须完整覆盖指代单元（允许多覆盖），返回全部极小覆盖组，
  不允许为省版面只挑一张同名专题图；
- desk 里的换稿、移页、待校、撤签分别标记，coverage 未跟上的移页记录
  会让图号/页码处于“印前未定”状态。
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from atlas.model import DeskNote, Sheet, Workspace

# desk 关键词只在这里集中维护，避免散落各处各自猜词。
_REPLACE_MARKERS = ("改为", "新补", "撤去", "撤")
_MOVE_MARKERS = ("移至", "新图号", "挪页", "移页")
_PENDING_MARKERS = ("待校", "待考", "暂撤", "核照")
_BLOCKED_PUBLISH_MARKERS = ("未将其付印",)


@dataclass(frozen=True)
class CoverGroup:
    sheet_ids: tuple[str, ...]
    numbers: tuple[str, ...]
    pages: tuple[int, ...]
    covers: tuple[str, ...]
    status: str  # 可链接 / 图幅待考 / 印前定位未定
    flags: tuple[str, ...]
    note_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "图幅": list(self.sheet_ids),
            "图号": list(self.numbers),
            "页码": list(self.pages),
            "实际覆盖": list(self.covers),
            "状态": self.status,
            "图务提示": list(self.flags),
            "依据desk": list(self.note_ids),
        }


def note_flags(notes: list[DeskNote]) -> tuple[set[str], set[str], set[str], set[str]]:
    replaced, moved, pending, unpublish = set(), set(), set(), set()
    for note in notes:
        if any(marker in note.content for marker in _REPLACE_MARKERS):
            replaced.add(note.id)
        if any(marker in note.content for marker in _MOVE_MARKERS):
            moved.add(note.id)
        if any(marker in note.content for marker in _PENDING_MARKERS):
            pending.add(note.id)
        if any(marker in note.content for marker in _BLOCKED_PUBLISH_MARKERS):
            unpublish.add(note.id)
    return replaced, moved, pending, unpublish


def sheet_notes(workspace: Workspace, sheet: Sheet) -> list[DeskNote]:
    return workspace.notes_for(sheet.id, sheet.edition)


def cover_groups(
    workspace: Workspace,
    edition: str,
    year: int,
    units: tuple[str, ...],
) -> list[CoverGroup]:
    target = set(units)
    pool = [
        sheet for sheet in workspace.sheets_of(edition)
        if sheet.year == year and set(sheet.units) & target
    ]
    if not pool:
        return []
    minimal: list[tuple[Sheet, ...]] = []
    for size in range(1, len(pool) + 1):
        for combo in combinations(pool, size):
            covered = set().union(*(set(sheet.units) for sheet in combo))
            if not target.issubset(covered):
                continue
            if any(
                set(combo) > set(other)
                and target.issubset(set().union(*(set(s.units) for s in other)))
                for other in minimal
            ):
                continue
            minimal.append(combo)
    # 去重（不同顺序的同一组合）
    unique: dict[tuple[str, ...], tuple[Sheet, ...]] = {}
    for combo in minimal:
        unique[tuple(sorted(sheet.id for sheet in combo))] = combo
    return [_build_group(workspace, combo, target) for combo in unique.values()]


def _build_group(workspace: Workspace, combo: tuple[Sheet, ...], target: set[str]) -> CoverGroup:
    flags: list[str] = []
    note_ids: set[str] = set()
    replaced, moved, pending, unpublish = set(), set(), set(), set()
    for sheet in combo:
        notes = sheet_notes(workspace, sheet)
        r, m, p, u = note_flags(notes)
        replaced |= r
        moved |= m
        pending |= p
        unpublish |= u
        for note in notes:
            note_ids.add(note.id)
            if set(sheet.units) - target:
                flags.append(f"{sheet.id}另覆盖{''.join(sorted(set(sheet.units) - target))}（允许多覆盖）")
    if replaced:
        flags.append(f"图幅换稿：{''.join(sorted(replaced))}，引用须按新覆盖复核")
    if pending:
        flags.append(f"图幅待考/撤签：{''.join(sorted(pending))}")
    if moved:
        flags.append(f"移页改号、coverage 未更新：{''.join(sorted(moved))}")
    if unpublish:
        flags.append(f"旧校样未付印：{''.join(sorted(unpublish))}")
    if pending:
        status = "图幅待考"
    elif moved:
        status = "印前定位未定"
    else:
        status = "可链接"
    covers = tuple(sorted(set().union(*(set(sheet.units) for sheet in combo))))
    return CoverGroup(
        tuple(sheet.id for sheet in combo),
        tuple(sheet.number for sheet in combo),
        tuple(sheet.page for sheet in combo),
        covers,
        status,
        tuple(flags),
        tuple(sorted(note_ids)),
    )


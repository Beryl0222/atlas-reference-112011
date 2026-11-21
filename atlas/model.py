"""加载交接资料并建立按版次的索引。

编号的作用范围是各自资料集，同号记录按版次区分，因此所有索引键都带版次。
空字符串、空数组只表示“未填写”，加载时原样保留，不拿它当反面事实。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corpus import read_collection


def _as_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_collection(path)
    if not isinstance(rows, list):
        raise ValueError(f"{path.name} 应为记录数组")
    return rows


@dataclass(frozen=True)
class Paragraph:
    id: str
    edition: str
    position: str
    text: str

    @property
    def page(self) -> int | None:
        return _leading_page(self.position)


@dataclass(frozen=True)
class NameRecord:
    id: str
    name: str
    start: int
    end: int | None
    units: tuple[str, ...]
    history: str

    @property
    def current(self) -> bool:
        return self.end is None


@dataclass(frozen=True)
class Sheet:
    id: str
    edition: str
    number: str
    page: int
    year: int
    units: tuple[str, ...]


@dataclass(frozen=True)
class DeskNote:
    id: str
    edition: str
    channel: str
    target: str
    date: str
    content: str


@dataclass(frozen=True)
class Reach:
    id: str
    branch: str
    upper: str
    lower: str
    kind: str


@dataclass(frozen=True)
class Section:
    id: str
    edition: str
    title: str
    time_scope: str
    note: str

    @property
    def pages(self) -> tuple[int, int]:
        tokens = re.findall(r"[一二三四五六七八九十百零〇\d]+", self.title)
        nums = [n for n in (_cjk_number(token) for token in tokens) if n]
        if len(nums) >= 2:
            return nums[0], nums[1]
        if nums:
            return nums[0], nums[0]
        return 0, 0


@dataclass(frozen=True)
class Workspace:
    paragraphs: tuple[Paragraph, ...]
    names: tuple[NameRecord, ...]
    sheets: tuple[Sheet, ...]
    notes: tuple[DeskNote, ...]
    reaches: tuple[Reach, ...]
    sections: tuple[Section, ...]
    editions: tuple[str, ...]
    _paragraph_index: dict[tuple[str, str], Paragraph] = field(compare=False, repr=False)
    _sheet_index: dict[tuple[str, str], Sheet] = field(compare=False, repr=False)

    def paragraph(self, paragraph_id: str, edition: str) -> Paragraph:
        return self._paragraph_index[(paragraph_id, edition)]

    def sheet(self, sheet_id: str, edition: str) -> Sheet:
        return self._sheet_index[(sheet_id, edition)]

    def paragraphs_of(self, edition: str) -> list[Paragraph]:
        return [row for row in self.paragraphs if row.edition == edition]

    def sheets_of(self, edition: str) -> list[Sheet]:
        return [row for row in self.sheets if row.edition == edition]

    def notes_for(self, target: str, edition: str) -> list[DeskNote]:
        return [row for row in self.notes if row.target == target and row.edition == edition]

    def section_for_page(self, edition: str, page: int | None) -> Section | None:
        for section in self.sections:
            if section.edition != edition:
                continue
            low, high = section.pages
            if page is not None and low <= page <= high:
                return section
        return None

    def observation_years(self, edition: str) -> tuple[int, ...]:
        return tuple(sorted({sheet.year for sheet in self.sheets_of(edition)}))


def load_workspace(data_dir: str | Path) -> Workspace:
    data_dir = Path(data_dir)
    paragraphs = tuple(
        Paragraph(row["id"], row["版次"], row["位置"], row["正文"])
        for row in _as_rows(data_dir / "paragraphs.json")
    )
    names = tuple(
        NameRecord(
            row["id"], row["名称"], row["生效"], row["止于"],
            tuple(row["范围"]), row["沿革"],
        )
        for row in _as_rows(data_dir / "gazetteer.json")
    )
    sheets = tuple(
        Sheet(row["id"], row["版次"], row["图号"], row["页"], row["年"], tuple(row["区域"]))
        for row in _as_rows(data_dir / "coverage.json")
    )
    notes = tuple(
        DeskNote(row["id"], row["版次"], row["入口"], row["对象"], row["时间"], row["内容"])
        for row in _as_rows(data_dir / "desk.json")
    )
    reaches = tuple(
        Reach(row["id"], row["支系"], row["上单元"], row["下单元"], row["类型"])
        for row in _as_rows(data_dir / "reaches.json")
    )
    sections = tuple(
        Section(row["id"], row["版次"], row["章节"], row["时间口径"], row["编校备注"])
        for row in _as_rows(data_dir / "context.json")
    )
    editions = tuple(dict.fromkeys(row.edition for row in paragraphs))
    paragraph_index = {(row.id, row.edition): row for row in paragraphs}
    sheet_index = {(row.id, row.edition): row for row in sheets}
    return Workspace(
        paragraphs, names, sheets, notes, reaches, sections, editions,
        paragraph_index, sheet_index,
    )


def _leading_page(position: str) -> int | None:
    match = re.match(r"([一二三四五六七八九十百零〇\d]+)页", position)
    if not match:
        return None
    return _cjk_number(match.group(1))


def _cjk_number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    digits = "零一二三四五六七八九"
    total = 0
    section = 0
    for char in token:
        if char == "〇":
            section = 0
        elif char in digits:
            section = digits.index(char)
        elif char == "十":
            total += (section or 1) * 10
            section = 0
        elif char == "百":
            total += (section or 1) * 100
            section = 0
    return total + section
"""地名候选、别名与改名/拆分关系。

编辑部要区分的三类关系在这里分开表达：

1. 续称/改名：同名记录首尾相接、覆盖一致，仍是同一地物；
2. 拆分：一条旧名在其后被多条新名分割覆盖；
3. 同名异地：同名称在不同支系/单元各有独立记录，需要年代与上下文共同约束。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from atlas.model import NameRecord, Reach, Workspace

_ALIAS_RE = re.compile(r"(?:又名|亦称|又称|同时称|别称)([一-鿿][一-鿿\d]{1,7})")
_OLD_BOUNDARY_RE = re.compile(r"采用?旧界|依旧界|按旧界")


@dataclass(frozen=True)
class NameCandidate:
    """一个可落到图幅上的地名解释。"""

    record_id: str
    name: str
    year: int
    units: tuple[str, ...]
    aliases: tuple[str, ...]
    relation: str  # 现用 / 历史 / 拆分后继 / 无统一边界主体
    basis: str


def aliases_of(record: NameRecord) -> tuple[str, ...]:
    seen: list[str] = []
    for match in _ALIAS_RE.finditer(record.history):
        alias = match.group(1)
        if alias not in seen:
            seen.append(alias)
    return tuple(seen)


def all_tokens(workspace: Workspace) -> dict[str, tuple[str, ...]]:
    """名称/别名 -> 地名记录 id 列表，按词长降序供最长匹配。"""
    tokens: dict[str, set[str]] = {}
    for record in workspace.names:
        for token in (record.name, *aliases_of(record)):
            tokens.setdefault(token, set()).add(record.id)
    ordered = sorted(tokens, key=len, reverse=True)
    return {token: tuple(sorted(tokens[token])) for token in ordered}


def active_at(record: NameRecord, year: int) -> bool:
    if year < record.start:
        return False
    return record.end is None or year <= record.end


def has_unified_boundary(record: NameRecord) -> bool:
    return "无统一边界主体" not in record.history


def resolve_name(
    workspace: Workspace,
    token: str,
    year: int,
    branch: str | None = None,
    branch_units: frozenset[str] | None = None,
) -> list[NameCandidate]:
    """按名称/别名找候选，年代为硬约束；支系只作收窄，不作猜测。"""
    index = all_tokens(workspace)
    if token not in index:
        return []
    candidates: list[NameCandidate] = []
    for record_id in index[token]:
        record = next(row for row in workspace.names if row.id == record_id)
        if not active_at(record, year):
            continue
        if branch and branch_units is not None:
            if not any(unit in branch_units for unit in record.units):
                continue
        relation = _relation(record)
        basis = f"{record.name}{record.start}–{record.end or '今'}覆盖{''.join(record.units)}"
        if aliases_of(record):
            basis += f"；别名{'、'.join(aliases_of(record))}"
        candidates.append(
            NameCandidate(
                record.id, record.name, year, record.units,
                aliases_of(record), relation, basis,
            )
        )
    return candidates


def _relation(record: NameRecord) -> str:
    if not has_unified_boundary(record):
        return "无统一边界主体"
    if record.current:
        return "现用"
    return "历史"


def split_successors(workspace: Workspace, record: NameRecord) -> list[NameRecord]:
    """旧名覆盖在其终止后被哪些现用记录分占。"""
    if record.end is None:
        return []
    old_units = set(record.units)
    out: list[NameRecord] = []
    for later in workspace.names:
        if later is record or later.start < record.end:
            continue
        if not later.current:
            continue
        if set(later.units).issubset(old_units) and later.id != record.id:
            if later.name != record.name and has_unified_boundary(later):
                out.append(later)
    return out


def continuation_of(workspace: Workspace, record: NameRecord) -> NameRecord | None:
    """同名、覆盖一致、首尾相接且仍有统一边界的续称记录；找不到返回 None。"""
    if record.end is None:
        return None
    for later in workspace.names:
        if later is record or later.name != record.name:
            continue
        if later.start != record.end + 1:
            continue
        if set(later.units) != set(record.units):
            continue
        if not has_unified_boundary(later):
            continue
        return later
    return None


def old_boundary_reading(workspace: Workspace, record: NameRecord) -> NameCandidate | None:
    """“采用旧界”：沿沿革回到旧名所给年代与范围，而不是拿现用范围顶替。"""
    predecessors = [
        row for row in workspace.names
        if set(record.units).issubset(set(row.units))
        and row.end is not None and row.end < record.start
    ]
    if not predecessors:
        return None
    old = min(predecessors, key=lambda row: row.end)
    return NameCandidate(
        old.id, old.name, old.end, old.units, aliases_of(old),
        "旧界", f"题注声明采用旧界，回到{old.name}{old.start}–{old.end}的范围{''.join(old.units)}",
    )


def mentions_old_boundary(text: str) -> bool:
    return bool(_OLD_BOUNDARY_RE.search(text))


def branch_units(reaches: list[Reach], branch: str) -> tuple[set[str], set[str]]:
    """返回（源段单元，支系全部单元）。

    源段只取该支系的上单元；汇入点同时属于别的支系，不计入“源段”解释。
    """
    own = [row for row in reaches if row.branch == branch]
    upper = {row.upper for row in own}
    lower = {row.lower for row in own}
    whole = upper | lower
    source = {unit for unit in upper if not any(unit == row.lower for row in own)}
    return source, whole

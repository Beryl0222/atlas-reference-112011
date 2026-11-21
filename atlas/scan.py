"""从正文、题注、表头切出指代，并解析年代、支系与覆盖单元。

解析只依据资料里写明的事实：

- 章节的时间口径只是默认值，题注年款或明确引文（含“旧版图说”）优先；
- “X 至 Y”按 reaches 的沿河相邻关系展开，不按行政区界猜测；
- 承接语先回到上文给出的范围，再用支系约束地名候选；
- 一个指代有多种合理解释时全部保留，由编校在台账里取舍。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from atlas.gazetteer import (
    NameCandidate,
    all_tokens,
    branch_units,
    mentions_old_boundary,
    old_boundary_reading,
    resolve_name,
)
from atlas.model import Paragraph, Workspace

_QUOTE_RE = re.compile(r"[“「]([^”」]*)[”」]")
_YEAR_RE = re.compile(r"(\d{4})年")
_OLD_ATLAS_RE = re.compile(r"旧版图说|旧版图")
_CURRENT_RE = re.compile(r"现今|现在|现状|近况")
_RANGE_LINK_RE = re.compile(r"至|到")
_ANAPHORA_RE = re.compile(r"也|同样|这个范围|该范围|一带")


_CJK_VALUE = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def cjk_year(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if len(token) == 4 and all(char in _CJK_VALUE for char in token):
        return int("".join(str(_CJK_VALUE[char]) for char in token))
    return None


def explicit_year(text: str) -> int | None:
    match = _YEAR_RE.search(text)
    if match:
        return int(match.group(1))
    for token in re.findall(r"[〇零一二三四五六七八九]{4}年", text):
        year = cjk_year(token[:-1])
        if year:
            return year
    return None

@dataclass
class Mention:
    token: str
    span: tuple[int, int]
    kind: str  # 名称 / 支系
    record_ids: tuple[str, ...] = ()


@dataclass
class Interpretation:
    label: str
    units: tuple[str, ...]
    basis: str
    candidates: list[NameCandidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "解释": self.label,
            "覆盖单元": list(self.units),
            "依据": self.basis,
            "候选": [candidate_to_dict(item) for item in self.candidates],
        }


@dataclass
class Reference:
    text: str
    kind: str  # 范围指代 / 专名 / 承接语 / 支系
    year: int
    year_basis: str
    quoted: bool
    branch: str | None
    interpretations: list[Interpretation]
    cautions: list[str] = field(default_factory=list)
    paragraph_id: str | None = None
    edition: str | None = None

    def to_dict(self) -> dict:
        return {
            "段落": self.paragraph_id,
            "版次": self.edition,
            "原文": self.text,
            "类型": self.kind,
            "年代": self.year,
            "年代依据": self.year_basis,
            "引自旧版": self.quoted,
            "支系": self.branch,
            "解释": [item.to_dict() for item in self.interpretations],
            "待考提示": list(self.cautions),
        }


def candidate_to_dict(candidate: NameCandidate) -> dict:
    return {
        "地名记录": candidate.record_id,
        "名称": candidate.name,
        "年代": candidate.year,
        "覆盖单元": list(candidate.units),
        "别名": list(candidate.aliases),
        "关系": candidate.relation,
        "依据": candidate.basis,
    }


# ---------------------------------------------------------------- 年代口径


def resolve_year(workspace: Workspace, paragraph: Paragraph) -> tuple[int, str]:
    text = paragraph.text
    explicit = explicit_year(text)
    if explicit is not None:
        return explicit, "段落自身年款（题注或明确年份）"
    section = workspace.section_for_page(paragraph.edition, paragraph.page)
    if _OLD_ATLAS_RE.search(text) and section:
        found = _YEAR_RE.search(section.note)
        if found:
            return int(found.group(1)), f"旧版图说按目录备注回到{found.group(1)}年水系章"
    # 段落自身的现状词（“现今/现在/近况”）优先于章节备注里只针对题注的年款。
    if _CURRENT_RE.search(text):
        current = max(workspace.observation_years(paragraph.edition))
        return current, "现状词取本版最新观测年份"
    # 章节备注里的年款约束图面叙述（范围指代、承接语、题注/图说）；
    # 普通专名的现状叙述不套用历史年款。是否图面叙述由引用类型在调用处标记。
    if section:
        digits = _YEAR_RE.search(section.time_scope)
        if digits:
            return int(digits.group(1)), f"章节{section.id}默认时间口径"
        figure_text = _is_figure_text(workspace, paragraph)
        if "现状" in section.time_scope and not figure_text:
            current = max(workspace.observation_years(paragraph.edition))
            return current, f"章节{section.id}默认现状口径，取本版最新观测年份"
        year, basis = _section_note_year(workspace, section)
        if year is not None:
            return year, basis
        if "现状" in section.time_scope:
            current = max(workspace.observation_years(paragraph.edition))
            return current, f"章节{section.id}默认现状口径，取本版最新观测年份"
    current = max(workspace.observation_years(paragraph.edition))
    return current, "无明确口径，回退为本版最新观测年份"


def _is_figure_text(workspace: Workspace, paragraph: Paragraph) -> bool:
    if "题注" in paragraph.position or bool(_OLD_ATLAS_RE.search(paragraph.text)):
        return True
    # 含沿河范围词或支系承接语的段落属于图面叙述，沿用章节备注的图内年款。
    return bool(_RANGE_LINK_RE.search(paragraph.text)) or any(
        mention.kind == "支系"
        for mention in scan_mentions(workspace, paragraph.text)
    )


def _section_note_year(workspace: Workspace, section) -> tuple[int | None, str | None]:
    found = _YEAR_RE.search(section.note)
    if found:
        return int(found.group(1)), f"章节{section.id}备注的题注/引文年款"
    # “保留同样的年款”“同一节”等措辞本身不带数字，回到同书其他章节的明示年款。
    if "年款" in section.note or "题注" in section.note:
        for other in workspace.sections:
            if other is section:
                continue
            match = _YEAR_RE.search(other.note)
            if match:
                return int(match.group(1)), (
                    f"章节{section.id}备注声明保留年款，沿{other.id}题注取{match.group(1)}年"
                )
    return None, None


# ---------------------------------------------------------------- 文本切分


def scan_mentions(workspace: Workspace, text: str) -> list[Mention]:
    name_tokens = all_tokens(workspace)
    branches = tuple(sorted({reach.branch for reach in workspace.reaches}, key=len, reverse=True))
    used: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return not any(span[0] < end and span[1] > start for start, end in used)

    found: list[Mention] = []
    for token, record_ids in name_tokens.items():
        for match in re.finditer(re.escape(token), text):
            span = match.span()
            if free(span):
                used.append(span)
                found.append(Mention(token, span, "名称", record_ids))
    for branch in branches:
        for match in re.finditer(re.escape(branch), text):
            span = match.span()
            if free(span):
                used.append(span)
                found.append(Mention(branch, span, "支系"))
    return sorted(found, key=lambda item: item.span)


def _clauses(text: str) -> list[tuple[str, bool, int]]:
    """按标点切小句，保留引号内文字并标记该小句是否处在引号中。"""
    quoted_spans = [match.span(1) for match in _QUOTE_RE.finditer(text)]
    pieces: list[tuple[str, bool, int]] = []
    for match in re.finditer(r"[^，。；：、\s]+", text):
        piece, start = match.group(), match.start()
        end = match.end()
        # 小句可能连引号一起匹配，故用“与引号区间有重叠”判断，而不是严格落在引号内。
        in_quote = any(start < hi and end > lo for lo, hi in quoted_spans)
        pieces.append((piece, in_quote, start))
    return pieces


# ---------------------------------------------------------------- 沿河展开


def downstream_expand(workspace: Workspace, start: set[str], stop: set[str]) -> tuple[str, ...]:
    """沿阅读方向从 start 展开到 stop（含两端）；汇入点不作为继续下行的出口。

    到达终点集合中的任一单元时该路线停止；若终点本身还有河段通向其余终点，
    那些河段仍从其下单元一侧继续，保证整段河道（如 A→B→C）完整覆盖。
    """
    reaches = workspace.reaches
    visited: set[str] = set(start)
    frontier = list(start)
    while frontier:
        unit = frontier.pop(0)
        outgoing = [reach for reach in reaches if reach.upper == unit]
        confluence = [reach for reach in outgoing if reach.kind == "汇入"]
        for reach in confluence:
            # 汇入点登记后该路线终止，不能沿汇入点所在的别的支系继续外推。
            visited.add(reach.lower)
        # 单元若以汇入收尾（如 S→C），其普通河段不再向同一终点展开，
        # 否则会把支系另一侧的单元（S→D）误并入本段。
        if confluence and any(reach.lower in stop for reach in confluence):
            continue
        for reach in outgoing:
            if reach.kind == "汇入":
                continue
            if reach.lower in visited:
                continue
            visited.add(reach.lower)
            if reach.lower not in stop or any(
                further.upper == reach.lower and further.lower in stop
                and further.lower not in visited
                for further in reaches
            ):
                # 非终点继续下行；终点若仍通向未纳入的其他终点（B→C）也继续走完。
                frontier.append(reach.lower)
    return tuple(sorted(visited))


def branch_of_unit(workspace: Workspace, unit: str) -> str | None:
    for reach in workspace.reaches:
        if unit in (reach.upper, reach.lower):
            return reach.branch
    return None


def _branch_constraint(
    workspace: Workspace, branch: str | None
) -> tuple[str | None, frozenset[str] | None]:
    if not branch:
        return None, None
    _, whole = branch_units(list(workspace.reaches), branch)
    return branch, frozenset(whole)


# ---------------------------------------------------------------- 段落解析


def analyze_paragraph(
    workspace: Workspace,
    paragraph: Paragraph,
    prior_ranges: list[tuple[str, tuple[str, ...]]] | None = None,
) -> list[Reference]:
    prior_ranges = list(prior_ranges or [])
    year, year_basis = resolve_year(workspace, paragraph)
    references: list[Reference] = []
    # 同段内的支系承接语先登记支系约束，使同段地名候选（如同名的北/南支旧渡口）被收窄。
    paragraph_branches = [
        mention.token
        for clause, _, _ in _clauses(paragraph.text)
        for mention in scan_mentions(workspace, clause)
        if mention.kind == "支系"
    ]

    def tag(reference: Reference) -> Reference:
        reference.paragraph_id = paragraph.id
        reference.edition = paragraph.edition
        return reference

    for clause, quoted, _ in _clauses(paragraph.text):
        mentions = scan_mentions(workspace, clause)
        if not mentions:
            continue
        link = _RANGE_LINK_RE.search(clause)
        if link and len([m for m in mentions if m.kind == "名称"]) >= 2:
            reference = tag(_range_reference(
                workspace, clause, year, year_basis, quoted, mentions,
                paragraph_branches[0] if paragraph_branches else None,
            ))
            references.append(reference)
            for interpretation in reference.interpretations:
                prior_ranges.append((clause, interpretation.units))
            continue
        branches = [m for m in mentions if m.kind == "支系"]
        if branches:
            references.append(tag(_anaphora_reference(
                workspace, clause, year, year_basis, quoted,
                branches[0].token, prior_ranges,
            )))
            name_mentions = [m for m in mentions if m.kind == "名称"]
            for mention in name_mentions:
                references.append(tag(_name_reference(
                    workspace, mention.token, clause, year, year_basis, quoted,
                )))
            continue
        for mention in mentions:
            references.append(tag(_name_reference(
                workspace, mention.token, clause, year, year_basis, quoted,
            )))
    return references


def _range_reference(
    workspace: Workspace,
    clause: str,
    year: int,
    year_basis: str,
    quoted: bool,
    mentions: list[Mention],
    paragraph_branch: str | None,
) -> Reference:
    names = [item for item in mentions if item.kind == "名称"]
    first, last = names[0], names[-1]
    constraint, branch_set = _branch_constraint(workspace, paragraph_branch)
    start_candidates = resolve_name(workspace, first.token, year, constraint, branch_set)
    end_candidates = resolve_name(workspace, last.token, year, constraint, branch_set)
    cautions: list[str] = []
    interpretations: list[Interpretation] = []
    if paragraph_branch and len(start_candidates) > 1:
        cautions.append(f"同段承接语点出{paragraph_branch}，已据此收窄“{first.token}”的同名候选")
    for head in start_candidates:
        for tail in end_candidates:
            branch = branch_of_unit(workspace, head.units[0])
            units = downstream_expand(workspace, set(head.units), set(tail.units))
            basis = (
                f"“{first.token}”起（{head.record_id}）沿河至“{last.token}”（{tail.record_id}），"
                f"按相邻关系展开为{''.join(units)}"
            )
            interpretations.append(
                Interpretation(f"{first.token}→{last.token}", units, basis, [head, tail])
            )
    for candidate in (*start_candidates, *end_candidates):
        if candidate.relation == "无统一边界主体":
            cautions.append(f"{candidate.name}为习惯合称、无统一边界主体，范围需人工确认")
    branch = branch_of_unit(workspace, start_candidates[0].units[0]) if start_candidates else paragraph_branch
    return Reference(clause, "范围指代", year, year_basis, quoted, branch, interpretations, cautions)


def _name_reference(
    workspace: Workspace,
    token: str,
    clause: str,
    year: int,
    year_basis: str,
    quoted: bool,
) -> Reference:
    candidates = resolve_name(workspace, token, year)
    missing = not candidates
    # 该年没有在效记录时才回退到现行记录；“采用旧界”再沿沿革回到旧范围。
    # 回退只用于解释，候选自身的年代保持其在效年份，不冒充指代年代。
    if missing:
        fallback_year = max(workspace.observation_years(_edition_of(workspace, clause)))
        candidates = resolve_name(workspace, token, fallback_year)
    interpretations = [
        Interpretation(
            f"{token}按{candidate.record_id}解释",
            candidate.units,
            candidate.basis,
            [candidate],
        )
        for candidate in candidates
    ]
    cautions = [
        f"{candidate.name}无统一边界主体，不能按单一地物落图"
        for candidate in candidates
        if candidate.relation == "无统一边界主体"
    ]
    if mentions_old_boundary(clause):
        for candidate in list(candidates):
            record = next(row for row in workspace.names if row.id == candidate.record_id)
            reading = old_boundary_reading(workspace, record)
            if reading is not None:
                interpretations.insert(
                    0,
                    Interpretation(
                        f"{token}采用旧界（{reading.record_id}）",
                        reading.units,
                        reading.basis,
                        [reading],
                    ),
                )
    if missing:
        cautions.append(f"{year}年地名表中没有“{token}”的在效记录，按沿革解释")
    return Reference(clause, "专名", year, year_basis, quoted, None, interpretations, cautions)


def _edition_of(workspace: Workspace, clause: str) -> str:
    for paragraph in workspace.paragraphs:
        if clause in paragraph.text:
            return paragraph.edition
    return workspace.editions[0]


def _anaphora_reference(
    workspace: Workspace,
    clause: str,
    year: int,
    year_basis: str,
    quoted: bool,
    branch: str,
    prior_ranges: list[tuple[str, tuple[str, ...]]],
) -> Reference:
    source, whole = branch_units(list(workspace.reaches), branch)
    constraint, branch_set = _branch_constraint(workspace, branch)
    cautions: list[str] = []
    interpretations: list[Interpretation] = []

    inherited = [
        (label, units)
        for label, units in prior_ranges
        if branch_set is not None and set(units).issubset(branch_set)
    ]
    if inherited and _ANAPHORA_RE.search(clause):
        label, units = inherited[-1]
        candidates = _candidates_in_units(workspace, units, year, branch)
        interpretations.append(
            Interpretation(
                f"{branch}承接上文范围（{label}）",
                units,
                f"承接语回到上文“{label}”所定范围{''.join(units)}，并以{branch}收窄地名候选",
                candidates,
            )
        )
    else:
        interpretations.append(
            Interpretation(
                f"{branch}源段",
                tuple(sorted(source)),
                f"按reaches登记，{branch}源段单元为{''.join(sorted(source))}，汇入点不计入源段",
            )
        )
        if whole != source:
            interpretations.append(
                Interpretation(
                    f"{branch}全支系（含汇入点）",
                    tuple(sorted(whole)),
                    f"按reaches登记，{branch}全支系单元为{''.join(sorted(whole))}，供内部复核",
                )
            )
        cautions.append(f"“{clause}”为承接语，孤立句无法定位，已列出{branch}的多种解释")
    return Reference(clause, "承接语", year, year_basis, quoted, constraint, interpretations, cautions)


def _candidates_in_units(
    workspace: Workspace, units: tuple[str, ...], year: int, branch: str
) -> list[NameCandidate]:
    _, branch_set = _branch_constraint(workspace, branch)
    out: list[NameCandidate] = []
    for record in workspace.names:
        if not any(unit in (branch_set or set()) for unit in record.units):
            continue
        if year < record.start or (record.end is not None and year > record.end):
            continue
        out.extend(resolve_name(workspace, record.name, year, branch, branch_set))
    dedup: dict[str, NameCandidate] = {item.record_id: item for item in out}
    return list(dedup.values())


def analyze_edition(workspace: Workspace, edition: str) -> dict[str, list[Reference]]:
    """按版面顺序解析整版，返回段落 id -> 指代列表（承接语可见上文范围）。"""
    result: dict[str, list[Reference]] = {}
    prior_ranges: list[tuple[str, tuple[str, ...]]] = []
    for paragraph in workspace.paragraphs_of(edition):
        references = analyze_paragraph(workspace, paragraph, prior_ranges)
        for reference in references:
            if reference.kind == "范围指代":
                for interpretation in reference.interpretations:
                    prior_ranges.append((reference.text, interpretation.units))
        result[paragraph.id] = references
    return result

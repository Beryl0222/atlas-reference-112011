"""编校命令行：指代分析、图幅组、确认台账、复核、渠道闸门、反查与阻塞清单。

输出统一为 UTF-8 JSON，键名沿用资料原词。所有命令只读取 data/ 下的交接资料，
确认台账写到独立的 work/ 目录，不改动任何原始 JSON。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from atlas.model import load_workspace
from atlas.scan import analyze_paragraph
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
    web_export,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DEFAULT_LEDGER = ROOT / "work" / "ledger.json"
BLOCKERS = {"地名解释": BLOCKER_NAME, "图幅条件": BLOCKER_SHEET, "印前定位": BLOCKER_PRESS}


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _workspace(args) -> object:
    return load_workspace(args.data)


def cmd_analyze(args) -> None:
    workspace = load_workspace(args.data)
    if args.paragraph:
        paragraph = workspace.paragraph(args.paragraph, args.edition)
        references = analyze_paragraph(workspace, paragraph)
        payload = {
            "段落": paragraph.id,
            "版次": paragraph.edition,
            "位置": paragraph.position,
            "正文": paragraph.text,
            "指代": [item.to_dict() for item in references],
        }
    else:
        payload = {
            "版次": args.edition,
            "章节指代": [
                {
                    "段落": paragraph.id,
                    "位置": paragraph.position,
                    "正文": paragraph.text,
                    "指代": [item.to_dict() for item in references],
                }
                for paragraph in workspace.paragraphs_of(args.edition)
                for references in [analyze_paragraph(workspace, paragraph)]
            ],
        }
    _print(payload)


def cmd_links(args) -> None:
    workspace = load_workspace(args.data)
    ledger = Ledger(args.ledger)
    links = build_links(workspace, args.edition)
    if args.paragraph:
        links = [link for link in links if link.paragraph_id == args.paragraph]
    for link in links:
        matches = ledger.matching(link)
        if matches:
            link.decision_id = matches[-1].decision_id
    _print([link.to_dict() for link in links])


def cmd_groups(args) -> None:
    workspace = load_workspace(args.data)
    units = tuple(token.strip() for token in args.units.split(",") if token.strip())
    groups = cover_groups(workspace, args.edition, args.year, units)
    _print({
        "版次": args.edition,
        "年代": args.year,
        "要求覆盖": list(units),
        "极小覆盖组": [group.to_dict() for group in groups],
    })


def cmd_decide(args) -> None:
    workspace = load_workspace(args.data)
    ledger = Ledger(args.ledger)
    links = [
        link for link in build_links(workspace, args.edition)
        if link.paragraph_id == args.paragraph
        and link.interpretation == args.interpretation
    ]
    if not links:
        raise SystemExit("找不到对应段落与解释，先运行 links 核对")
    link = links[0]
    if not link.groups:
        raise SystemExit("该解释没有可覆盖的图幅组，不能确认出版链接")
    group = next((item for item in link.groups if tuple(item.sheet_ids) == tuple(args.sheets.split(","))), None)
    if group is None:
        raise SystemExit("指定图幅组不在候选内，不允许自组图号")
    try:
        decision = record_decision(
            workspace, ledger, link, group, args.by, args.date, args.channel, args.basis,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    _print(decision.to_dict())


def cmd_recheck(args) -> None:
    workspace = load_workspace(args.data)
    ledger = Ledger(args.ledger)
    _print([item.to_dict() for item in recheck(workspace, ledger, args.edition)])


def cmd_web(args) -> None:
    workspace = load_workspace(args.data)
    ledger = Ledger(args.ledger)
    _print(web_export(workspace, args.edition, ledger))


def cmd_press(args) -> None:
    workspace = load_workspace(args.data)
    ledger = Ledger(args.ledger)
    _print(press_gate(workspace, args.edition, ledger))


def cmd_reverse(args) -> None:
    workspace = load_workspace(args.data)
    index = reverse_index(workspace, args.edition)
    if args.sheet:
        _print(index.get(args.sheet, {"图幅": args.sheet, "引用段落": []}))
    else:
        _print(list(index.values()))


def cmd_blocked(args) -> None:
    workspace = load_workspace(args.data)
    blocker = BLOCKERS.get(args.kind) if args.kind else None
    _print(blocked_links(workspace, args.edition, blocker))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="图集编校链接工具")
    parser.add_argument("--data", default=DATA, help="交接资料目录")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER, help="确认台账路径")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="解析段落/整版的指代、年代与解释")
    analyze.add_argument("edition")
    analyze.add_argument("--paragraph")
    analyze.set_defaults(func=cmd_analyze)

    links = sub.add_parser("links", help="列出每个解释对应的图幅组与阻塞")
    links.add_argument("edition")
    links.add_argument("--paragraph")
    links.set_defaults(func=cmd_links)

    groups = sub.add_parser("groups", help="按年代和单元直接求极小图幅组")
    groups.add_argument("edition")
    groups.add_argument("year", type=int)
    groups.add_argument("units", help="逗号分隔的单元，如 A,B,C")
    groups.set_defaults(func=cmd_groups)

    decide = sub.add_parser("decide", help="登记一条编辑确认（纸刊要求印前已定）")
    decide.add_argument("edition")
    decide.add_argument("paragraph")
    decide.add_argument("interpretation", help="links 输出中的“采用解释”字样")
    decide.add_argument("sheets", help="候选组图幅 id，逗号分隔，如 F1,F2")
    decide.add_argument("--by", required=True)
    decide.add_argument("--date", required=True)
    decide.add_argument("--channel", choices=["网页", "纸刊"], required=True)
    decide.add_argument("--basis", default="", help="确认依据，便于回溯")
    decide.set_defaults(func=cmd_decide)

    recheck_cmd = sub.add_parser("recheck", help="按现行正文与图幅事实复核台账")
    recheck_cmd.add_argument("edition")
    recheck_cmd.set_defaults(func=cmd_recheck)

    web = sub.add_parser("web", help="线上发布稿：未决处保留文字并标图幅待考")
    web.add_argument("edition")
    web.set_defaults(func=cmd_web)

    press = sub.add_parser("press", help="纸刊闸门：图号、页码、版次未定即不放行")
    press.add_argument("edition")
    press.set_defaults(func=cmd_press)

    reverse = sub.add_parser("reverse", help="从图幅反查引用它的段落")
    reverse.add_argument("edition")
    reverse.add_argument("--sheet")
    reverse.set_defaults(func=cmd_reverse)

    blocked = sub.add_parser("blocked", help="按阻塞类别列出卡住的链接")
    blocked.add_argument("edition")
    blocked.add_argument("--kind", choices=list(BLOCKERS))
    blocked.set_defaults(func=cmd_blocked)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .atomic_mover import load_move_specs, move_citation_fields
from .change_surface import check_docx_change_surface
from .experiment_text import extract_visible_introduction
from .officecli_guard import load_officecli_guard_plan, run_officecli_guarded
from .ooxml import DocxError
from .patcher import load_patch_specs, patch_docx
from .rewrite_view import apply_rewrite_view, export_rewrite_view, load_rewrite_view
from .scanner import scan_docx
from .verifier import verify_docx


def _write_json(payload: dict[str, Any], path: str | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    print(text)


def _paragraph_list(value: str) -> list[int]:
    try:
        paragraphs = [int(item) for item in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Use comma-separated paragraph numbers."
        ) from exc
    if not paragraphs or any(item < 1 for item in paragraphs):
        raise argparse.ArgumentTypeError("Paragraph numbers must be positive.")
    if len(set(paragraphs)) != len(paragraphs):
        raise argparse.ArgumentTypeError("Paragraph numbers must be unique.")
    return paragraphs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="endnote-safe-word",
        description="Conservative guardrails for DOCX files containing EndNote fields.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser(
        "scan", help="Inventory EndNote fields and formatting markers."
    )
    scan.add_argument("input")
    scan.add_argument("--json", dest="json_path")

    patch = sub.add_parser(
        "patch", help="Apply field-excluding single-node text patches."
    )
    patch.add_argument("input")
    patch.add_argument("--patches", required=True, help="JSON patch specification.")
    patch.add_argument("--output", required=True)
    patch.add_argument("--report")
    patch.add_argument("--overwrite-output", action="store_true")
    patch.add_argument("--keep-failed-output", action="store_true")
    patch.add_argument(
        "--allow-format-count-change",
        action="store_true",
        help="Warn rather than fail if direct superscript/subscript counts change.",
    )

    move = sub.add_parser(
        "move-citations",
        help="Move complete citation fields to declared ordinary-text placeholders.",
    )
    move.add_argument("input")
    move.add_argument("--moves", required=True, help="JSON citation-move plan.")
    move.add_argument("--output", required=True)
    move.add_argument("--report")
    move.add_argument("--overwrite-output", action="store_true")
    move.add_argument("--keep-failed-output", action="store_true")

    guarded = sub.add_parser(
        "officecli-guard",
        help="Execute a version-pinned allowlist of safe OfficeCLI DOCX writes.",
    )
    guarded.add_argument("input")
    guarded.add_argument("--plan", required=True, help="JSON OfficeCLI guard plan.")
    guarded.add_argument("--output", required=True)
    guarded.add_argument("--report")
    guarded.add_argument("--overwrite-output", action="store_true")
    guarded.add_argument("--keep-failed-output", action="store_true")
    guarded.add_argument(
        "--allow-planned-format-change",
        action="store_true",
        help="Warn rather than fail for a deliberately conditioned format marker.",
    )

    surface = sub.add_parser(
        "check-surface",
        help="Classify and enforce DOCX package and node change boundaries.",
    )
    surface.add_argument("before")
    surface.add_argument("after")
    surface.add_argument(
        "--editable-paragraphs",
        required=True,
        type=_paragraph_list,
        help="Comma-separated one-based body paragraph numbers.",
    )
    surface.add_argument("--officecli-version")
    surface.add_argument("--allow-new-run-properties", action="store_true")
    surface.add_argument("--json", dest="json_path")

    intro = sub.add_parser(
        "intro-text",
        help="Extract visible introduction prose and its frozen experiment word count.",
    )
    intro.add_argument("input")
    intro.add_argument(
        "--paragraphs",
        type=_paragraph_list,
        default=[1, 2, 3, 4],
        help="Comma-separated one-based body paragraphs (default: 1,2,3,4).",
    )
    intro.add_argument("--json", dest="json_path")

    rewrite_export = sub.add_parser(
        "rewrite-export",
        help="Export native OOXML prose with opaque citation and format tokens.",
    )
    rewrite_export.add_argument("input")
    rewrite_export.add_argument(
        "--paragraphs",
        required=True,
        type=_paragraph_list,
        help="Contiguous one-based body paragraphs to expose for rewriting.",
    )
    rewrite_export.add_argument("--json", dest="json_path", required=True)

    rewrite_apply = sub.add_parser(
        "rewrite-apply",
        help="Map an edited rewrite view back to native OOXML atoms.",
    )
    rewrite_apply.add_argument("input")
    rewrite_apply.add_argument(
        "--view", required=True, help="Edited rewrite-view JSON."
    )
    rewrite_apply.add_argument("--output", required=True)
    rewrite_apply.add_argument("--report")
    rewrite_apply.add_argument("--overwrite-output", action="store_true")
    rewrite_apply.add_argument("--keep-failed-output", action="store_true")

    verify = sub.add_parser("verify", help="Compare EndNote/field invariants.")
    verify.add_argument("before")
    verify.add_argument("after")
    verify.add_argument("--json", dest="json_path")
    verify.add_argument("--allow-format-count-change", action="store_true")
    verify.add_argument(
        "--allow-field-reordering",
        action="store_true",
        help="Accept reordered citation fields instead of requiring an identical "
        "atomic field order.",
    )
    verify.add_argument(
        "--expected-citation-order",
        metavar="SHA256,...",
        help="Declared citation field order (e.g. the expected_citation_order from "
        "a rewrite-apply report) as a comma-separated field SHA-256 list. "
        "Requires --allow-field-reordering.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            payload = scan_docx(args.input).to_dict()
            _write_json(payload, args.json_path)
            return 0 if not payload["errors"] else 2
        if args.command == "patch":
            payload = patch_docx(
                args.input,
                args.output,
                load_patch_specs(args.patches),
                overwrite_output=args.overwrite_output,
                keep_failed_output=args.keep_failed_output,
                strict_format_counts=not args.allow_format_count_change,
            )
            _write_json(payload, args.report)
            return 0 if payload["status"] == "pass" else 2
        if args.command == "move-citations":
            payload = move_citation_fields(
                args.input,
                args.output,
                load_move_specs(args.moves),
                overwrite_output=args.overwrite_output,
                keep_failed_output=args.keep_failed_output,
            )
            _write_json(payload, args.report)
            return 0 if payload["status"] == "pass" else 2
        if args.command == "officecli-guard":
            payload = run_officecli_guarded(
                args.input,
                args.output,
                load_officecli_guard_plan(args.plan),
                overwrite_output=args.overwrite_output,
                keep_failed_output=args.keep_failed_output,
                strict_format_counts=not args.allow_planned_format_change,
            )
            _write_json(payload, args.report)
            return 0 if payload["status"] == "pass" else 2
        if args.command == "check-surface":
            payload = check_docx_change_surface(
                args.before,
                args.after,
                editable_paragraphs=args.editable_paragraphs,
                officecli_version=args.officecli_version,
                allow_new_run_properties=args.allow_new_run_properties,
            )
            _write_json(payload, args.json_path)
            return 0 if payload["status"] == "pass" else 2
        if args.command == "intro-text":
            payload = extract_visible_introduction(
                args.input,
                paragraph_indices=args.paragraphs,
            )
            _write_json(payload, args.json_path)
            return 0
        if args.command == "rewrite-export":
            payload = export_rewrite_view(
                args.input,
                paragraph_indices=args.paragraphs,
            )
            _write_json(payload, args.json_path)
            return 0
        if args.command == "rewrite-apply":
            payload = apply_rewrite_view(
                args.input,
                args.output,
                load_rewrite_view(args.view),
                overwrite_output=args.overwrite_output,
                keep_failed_output=args.keep_failed_output,
            )
            _write_json(payload, args.report)
            return 0 if payload["status"] == "pass" else 2
        if args.command == "verify":
            if args.expected_citation_order is not None:
                expected_order = args.expected_citation_order.split(",")
                if not args.allow_field_reordering:
                    raise DocxError(
                        "--expected-citation-order requires --allow-field-reordering."
                    )
            else:
                expected_order = None
            payload = verify_docx(
                args.before,
                args.after,
                strict_format_counts=not args.allow_format_count_change,
                allow_field_reordering=args.allow_field_reordering,
                expected_citation_order=expected_order,
            )
            _write_json(payload, args.json_path)
            return 0 if payload["status"] == "pass" else 2
    except (DocxError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

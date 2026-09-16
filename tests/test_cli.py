from __future__ import annotations

import json
from pathlib import Path

from fixture_factory import make_nested_endnote_docx

from endnote_safe_word.cli import main
from endnote_safe_word.rewrite_view import export_rewrite_view


def test_cli_verify_rejects_reordered_fields_without_flag(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    view_path = tmp_path / "view.json"
    report_path = tmp_path / "report.json"
    make_nested_endnote_docx(source)

    view = export_rewrite_view(source, paragraph_indices=[1, 2])
    view["paragraphs"][0]["text"] = (
        "Reframed combined evidence [[CIT:C2]] with [[FMT:F1]]."
    )
    view["paragraphs"][1]["text"] = (
        "Then preserve [[CIT:C1]] together with [[FMT:F2]] and [[FMT:F3]]."
    )
    view_path.write_text(json.dumps(view), encoding="utf-8")

    status = main(
        [
            "rewrite-apply",
            str(source),
            "--view",
            str(view_path),
            "--output",
            str(output),
            "--report",
            str(report_path),
        ]
    )
    assert status == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "pass"

    # The rewrite moved C2 before C1; a plain verify must fail.
    assert main(["verify", str(source), str(output)]) == 2

    # The declared citation order from the apply report unlocks the reordering.
    order = ",".join(report["expected_citation_order"])
    assert (
        main(
            [
                "verify",
                str(source),
                str(output),
                "--allow-field-reordering",
                "--expected-citation-order",
                order,
            ]
        )
        == 0
    )

    # A wrong declared order must still fail.
    assert (
        main(
            [
                "verify",
                str(source),
                str(output),
                "--allow-field-reordering",
                "--expected-citation-order",
                ",".join(reversed(report["expected_citation_order"])),
            ]
        )
        == 2
    )

    # Declaring an order without allowing reordering is rejected up front.
    assert (
        main(
            [
                "verify",
                str(source),
                str(output),
                "--expected-citation-order",
                order,
            ]
        )
        == 2
    )


def test_cli_verify_identical_order_passes_without_flags(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    view_path = tmp_path / "view.json"
    report_path = tmp_path / "report.json"
    make_nested_endnote_docx(source)

    view = export_rewrite_view(source, paragraph_indices=[1, 2])
    view_path.write_text(json.dumps(view), encoding="utf-8")
    assert (
        main(
            [
                "rewrite-apply",
                str(source),
                "--view",
                str(view_path),
                "--output",
                str(output),
                "--report",
                str(report_path),
            ]
        )
        == 0
    )
    assert main(["verify", str(source), str(output)]) == 0
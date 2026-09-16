from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from endnote_safe_word.officecli_guard import (
    OFFICECLI_VERSION,
    OfficeCliGuardPlan,
    run_officecli_guarded,
    validate_officecli_operations,
)
from endnote_safe_word.ooxml import DocxError, sha256_file
from fixture_factory import make_docx, make_nested_endnote_docx


def _plan(source: Path, operations: list[dict], allowed: list[int] | None = None):
    return OfficeCliGuardPlan.from_dict(
        {
            "expected_source_sha256": sha256_file(source),
            "officecli_version": OFFICECLI_VERSION,
            "allowed_paragraphs": allowed or [1],
            "operations": operations,
        }
    )


def test_guard_accepts_pinned_ordinary_run_text(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    plan = _plan(
        source,
        [
            {
                "op": "set_run_text",
                "path": "/body/p[1]/r[1]",
                "expected_text": "Treatment works.",
                "text": "Treatment was effective.",
            }
        ],
    )
    validated = validate_officecli_operations(
        source, plan, operation_type="set_run_text"
    )
    assert validated[0]["paragraph_index"] == 1


@pytest.mark.parametrize("op", ["raw-set", "add", "remove", "move", "swap", "set"])
def test_guard_rejects_unlisted_commands(tmp_path: Path, op: str) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    with pytest.raises(DocxError, match="Unsupported OfficeCLI operation"):
        _plan(source, [{"op": op, "path": "/body/p[1]/r[1]"}])


def test_guard_rejects_paragraph_or_field_text_targets(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    paragraph_plan = _plan(
        source,
        [
            {
                "op": "set_run_text",
                "path": "/body/p[1]",
                "expected_text": "Treatment works. 1",
                "text": "Unsafe",
            }
        ],
    )
    with pytest.raises(DocxError, match="explicit direct run path"):
        validate_officecli_operations(
            source, paragraph_plan, operation_type="set_run_text"
        )

    field_plan = _plan(
        source,
        [
            {
                "op": "set_run_text",
                "path": "/body/p[1]/r[6]",
                "expected_text": "1",
                "text": "2",
            }
        ],
    )
    with pytest.raises(DocxError, match="inside a complex Word field"):
        validate_officecli_operations(
            source, field_plan, operation_type="set_run_text"
        )


def test_guard_rejects_formatted_and_wrapped_targets(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)
    formatted = _plan(
        source,
        [
            {
                "op": "set_run_text",
                "path": "/body/p[1]/r[1]",
                "expected_text": "MTHFR",
                "text": "Changed",
            }
        ],
    )
    with pytest.raises(DocxError, match="protected direct formatting"):
        validate_officecli_operations(
            source, formatted, operation_type="set_run_text"
        )

    for wrapper in ("ins", "moveFrom", "sdt"):
        wrapped = _plan(
            source,
            [
                {
                    "op": "set_run_text",
                    "path": f"/body/p[1]/{wrapper}[1]/r[1]",
                    "expected_text": "MTHFR",
                    "text": "Changed",
                }
            ],
        )
        with pytest.raises(DocxError, match="explicit direct run path"):
            validate_officecli_operations(
                source, wrapped, operation_type="set_run_text"
            )


def test_guard_validates_range_and_rejects_field_overlap(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    safe = _plan(
        source,
        [
            {
                "op": "format_range",
                "path": "/body/p[1]",
                "start": 0,
                "end": 9,
                "expected_text": "Treatment",
                "properties": {"italic": True},
            }
        ],
    )
    validated = validate_officecli_operations(
        source, safe, operation_type="format_range"
    )
    assert validated[0]["expected_text"] == "Treatment"

    field = _plan(
        source,
        [
            {
                "op": "format_range",
                "path": "/body/p[1]",
                "start": 17,
                "end": 18,
                "expected_text": "1",
                "properties": {"superscript": True},
            }
        ],
    )
    with pytest.raises(DocxError, match="inside a complex Word field"):
        validate_officecli_operations(source, field, operation_type="format_range")


def test_guard_rejects_body_range_and_unsafe_format_properties(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    body = _plan(
        source,
        [
            {
                "op": "format_range",
                "path": "/body",
                "start": 0,
                "end": 9,
                "expected_text": "Treatment",
                "properties": {"italic": True},
            }
        ],
    )
    with pytest.raises(DocxError, match="explicit /body/p"):
        validate_officecli_operations(source, body, operation_type="format_range")

    with pytest.raises(DocxError, match="exactly one true property"):
        _plan(
            source,
            [
                {
                    "op": "format_range",
                    "path": "/body/p[1]",
                    "start": 0,
                    "end": 9,
                    "expected_text": "Treatment",
                    "properties": {"bold": True},
                }
            ],
        )


@pytest.mark.skipif(shutil.which("officecli") is None, reason="officecli not installed")
def test_guarded_officecli_integration_preserves_field_and_superscript(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_docx(source)
    plan = _plan(
        source,
        [
            {
                "op": "set_run_text",
                "path": "/body/p[1]/r[1]",
                "expected_text": "Treatment works.",
                "text": "Treatment was effective.",
            }
        ],
    )
    result = run_officecli_guarded(source, output, plan)
    assert result["status"] == "pass"
    assert result["source_unchanged"]
    assert result["verification"]["checks"]["endnote_field_multiset_identical"]
    assert result["verification"]["checks"]["superscript_count_identical"]
    assert result["change_surface"]["status"] == "pass"

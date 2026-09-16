from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fixture_factory import make_docx, make_nested_endnote_docx

from word_document_safe_editing.ooxml import DocxError
from word_document_safe_editing.rewrite_view import (
    apply_rewrite_view,
    export_rewrite_view,
)
from word_document_safe_editing.scanner import scan_docx


def _rewrite_document_xml(path: Path, transform) -> None:
    temporary = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(
        temporary, "w"
    ) as output:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "word/document.xml":
                data = transform(data)
            output.writestr(info, data)
    temporary.replace(path)


def test_export_rewrite_view_exposes_opaque_nested_citations_and_formats(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)

    view = export_rewrite_view(source, paragraph_indices=[1, 2])

    assert [item["token"] for item in view["citations"]] == [
        "[[CIT:C1]]",
        "[[CIT:C2]]",
    ]
    assert view["citations"][1]["record_ids"] == [
        "test-database-id:102",
        "test-database-id:103",
    ]
    assert "ADDIN EN.CITE" not in view["paragraphs"][0]["text"]
    assert [item["text"] for item in view["format_atoms"]] == [
        "MTHFR",
        "12",
        "2",
    ]


def test_noop_rewrite_roundtrip_preserves_fields_and_format_atoms(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    before = scan_docx(source)
    view = export_rewrite_view(source, paragraph_indices=[1, 2])

    result = apply_rewrite_view(source, output, view)

    assert result["status"] == "pass"
    assert result["checks"] == {
        "field_and_format_verification_passed": True,
        "package_change_surface_passed": True,
        "semantic_paragraph_views_match": True,
    }
    assert result["word_count"] > 0
    assert "visible_prose" not in result
    after = scan_docx(output)
    assert [item.field_sha256 for item in after.endnote_fields] == [
        item.field_sha256 for item in before.endnote_fields
    ]
    assert after.superscript_count == before.superscript_count == 1
    assert after.subscript_count == before.subscript_count == 1


def test_rewrite_moves_complete_nested_fields_across_paragraphs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    before = scan_docx(source)
    view = export_rewrite_view(source, paragraph_indices=[1, 2])
    view["paragraphs"][0]["text"] = (
        "Reframed combined evidence [[CIT:C2]] with [[FMT:F1]]."
    )
    view["paragraphs"][1]["text"] = (
        "Then preserve [[CIT:C1]] together with [[FMT:F2]] and [[FMT:F3]]."
    )

    result = apply_rewrite_view(source, output, view)

    assert result["status"] == "pass"
    after = scan_docx(output)
    assert [item.field_sha256 for item in after.citation_fields] == [
        before.citation_fields[1].field_sha256,
        before.citation_fields[0].field_sha256,
    ]
    assert len(after.endnote_fields) == 3
    assert after.record_identities == [
        "test-database-id:102",
        "test-database-id:103",
        "test-database-id:101",
    ]
    assert after.superscript_count == before.superscript_count == 1
    assert after.subscript_count == before.subscript_count == 1
    assert result["verification"]["checks"]["format_spans_identical"]


def test_rewrite_rejects_missing_citation_token(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    view = export_rewrite_view(source, paragraph_indices=[1, 2])
    view["paragraphs"][0]["text"] = view["paragraphs"][0]["text"].replace(
        "[[CIT:C1]]", ""
    )

    with pytest.raises(DocxError, match="must occur exactly once"):
        apply_rewrite_view(source, output, view)
    assert not output.exists()


def test_rewrite_rejects_tampered_citation_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    view = export_rewrite_view(source, paragraph_indices=[1, 2])
    view["citations"][0]["field_sha256"] = "0" * 64

    with pytest.raises(DocxError, match="citation metadata changed"):
        apply_rewrite_view(source, output, view)
    assert not output.exists()


def test_rewrite_rejects_tracked_change_in_selected_paragraph(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)

    def add_tracked_change(data: bytes) -> bytes:
        return data.replace(
            b"<w:r><w:rPr><w:i/></w:rPr><w:t>MTHFR</w:t></w:r>",
            b'<w:ins w:id="1"><w:r><w:rPr><w:i/></w:rPr>'
            b"<w:t>MTHFR</w:t></w:r></w:ins>",
        )

    _rewrite_document_xml(source, add_tracked_change)
    with pytest.raises(DocxError, match="protected or unsupported ins"):
        export_rewrite_view(source, paragraph_indices=[1, 2])


def test_rewrite_rejects_reserved_token_text_in_source(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)

    def add_reserved_text(data: bytes) -> bytes:
        return data.replace(b"Combined evidence ", b"Combined [[CIT:C9]] evidence ")

    _rewrite_document_xml(source, add_reserved_text)
    with pytest.raises(DocxError, match="reserved rewrite token syntax"):
        export_rewrite_view(source, paragraph_indices=[1, 2])


def test_rewrite_rejects_simple_endnote_field(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)

    def use_simple_field(data: bytes) -> bytes:
        start = data.index(b'      <w:r><w:fldChar w:fldCharType="begin"/></w:r>')
        end_marker = b'      <w:r><w:fldChar w:fldCharType="end"/></w:r>'
        end = data.index(end_marker, start) + len(end_marker)
        replacement = (
            b'      <w:fldSimple w:instr=" ADDIN EN.CITE simple ">'
            b"<w:r><w:t>1</w:t></w:r></w:fldSimple>"
        )
        return data[:start] + replacement + data[end:]

    _rewrite_document_xml(source, use_simple_field)
    with pytest.raises(DocxError, match="Simple EndNote fields are unsupported"):
        export_rewrite_view(source, paragraph_indices=[1])


def test_rewrite_rejects_selected_reference_list_field(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)

    def add_reference_list(data: bytes) -> bytes:
        reference = b"""
    <w:p>
      <w:r><w:t>References </w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.REFLIST </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>Reference result</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
    </w:p>
"""
        return data.replace(b"    <w:sectPr/>", reference + b"    <w:sectPr/>")

    _rewrite_document_xml(source, add_reference_list)
    with pytest.raises(DocxError, match="unsupported ADDIN EN.REFLIST field"):
        export_rewrite_view(source, paragraph_indices=[1, 2, 3])


def test_rewrite_rejects_noncontiguous_paragraph_range(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_nested_endnote_docx(source)

    with pytest.raises(DocxError, match="one contiguous body range"):
        export_rewrite_view(source, paragraph_indices=[1, 3])


def test_rewrite_requires_selected_endnote_citation(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)

    def remove_field(data: bytes) -> bytes:
        start = data.index(b'      <w:r><w:fldChar w:fldCharType="begin"/></w:r>')
        end_marker = b'      <w:r><w:fldChar w:fldCharType="end"/></w:r>'
        end = data.index(end_marker, start) + len(end_marker)
        return data[:start] + b"      <w:r><w:t>plain</w:t></w:r>" + data[end:]

    _rewrite_document_xml(source, remove_field)
    with pytest.raises(DocxError, match="at least one EndNote citation"):
        export_rewrite_view(source, paragraph_indices=[1])

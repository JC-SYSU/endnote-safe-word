from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fixture_factory import make_docx, make_nested_endnote_docx
from lxml import etree

from endnote_safe_word.atomic_mover import CitationMoveSpec, move_citation_fields
from endnote_safe_word.experiment_text import extract_visible_introduction
from endnote_safe_word.ooxml import DocxError
from endnote_safe_word.patcher import PatchSpec, patch_docx
from endnote_safe_word.scanner import scan_docx
from endnote_safe_word.verifier import verify_docx


def test_scan_detects_endnote_field(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    make_docx(source)
    report = scan_docx(source)
    assert len(report.endnote_fields) == 1
    assert report.endnote_fields[0].kind == "ADDIN EN.CITE"
    assert report.superscript_count == 1
    assert report.all_fields_balanced


def test_safe_patch_preserves_field(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_docx(source)
    result = patch_docx(
        source,
        output,
        [PatchSpec(find="Treatment works.", replace="Treatment was effective.")],
    )
    assert result["status"] == "pass"
    assert output.exists()
    verification = verify_docx(source, output)
    assert verification["status"] == "pass"
    with zipfile.ZipFile(output) as zf:
        assert b"Treatment was effective." in zf.read("word/document.xml")


def test_patch_inside_field_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_docx(source)
    with pytest.raises(DocxError, match="inside a complex Word field"):
        patch_docx(source, output, [PatchSpec(find="1", replace="2")])
    assert not output.exists()


def test_verify_detects_field_change(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    changed = tmp_path / "changed.docx"
    make_docx(source, field_text="1")
    make_docx(changed, field_text="2")
    result = verify_docx(source, changed)
    assert result["status"] == "fail"
    assert any("EndNote field #1 changed" in item for item in result["failures"])


def _rewrite_document_xml(path: Path, transform) -> None:
    tmp = path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(path, "r") as src, zipfile.ZipFile(tmp, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "word/document.xml":
                data = transform(data)
            dst.writestr(info, data)
    tmp.replace(path)


def test_patch_inside_tracked_change_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "tracked.docx"
    output = tmp_path / "output.docx"
    make_docx(source, outside="Tracked wording.")

    def wrap(data: bytes) -> bytes:
        return data.replace(
            b"<w:r><w:t>Tracked wording.</w:t></w:r>",
            b'<w:ins w:id="1"><w:r><w:t>Tracked wording.</w:t></w:r></w:ins>',
        )

    _rewrite_document_xml(source, wrap)
    with pytest.raises(DocxError, match="inside tracked changes"):
        patch_docx(source, output, [PatchSpec(find="Tracked wording.", replace="New.")])


def test_verify_detects_superscript_loss(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    changed = tmp_path / "changed.docx"
    make_docx(source)
    make_docx(changed)

    def remove_superscript(data: bytes) -> bytes:
        return data.replace(b'<w:vertAlign w:val="superscript"/>', b"")

    _rewrite_document_xml(changed, remove_superscript)
    result = verify_docx(source, changed)
    assert result["status"] == "fail"
    assert any("superscript count changed" in item for item in result["failures"])


def test_semantic_field_hash_ignores_serializer_whitespace(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    reformatted = tmp_path / "reformatted.docx"
    make_nested_endnote_docx(source)
    reformatted.write_bytes(source.read_bytes())

    def pretty_print(data: bytes) -> bytes:
        root = etree.fromstring(data)
        etree.indent(root, space="    ")
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    _rewrite_document_xml(reformatted, pretty_print)
    result = verify_docx(source, reformatted)
    assert result["status"] == "pass"
    assert result["checks"]["endnote_field_multiset_identical"]


def test_scan_classifies_atomic_nested_fields_and_records(tmp_path: Path) -> None:
    source = tmp_path / "nested.docx"
    make_nested_endnote_docx(source)
    report = scan_docx(source)

    assert len(report.endnote_fields) == 3
    assert len(report.atomic_endnote_fields) == 2
    assert len(report.citation_fields) == 2
    assert [field.kind for field in report.endnote_fields] == [
        "ADDIN EN.CITE",
        "ADDIN EN.CITE",
        "ADDIN EN.CITE.DATA",
    ]
    assert report.endnote_fields[2].parent_field_index == 2
    assert not report.endnote_fields[2].top_level
    assert report.record_identities == [
        "test-database-id:101",
        "test-database-id:102",
        "test-database-id:103",
    ]


def test_verify_detects_direct_format_span_loss(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    changed = tmp_path / "changed.docx"
    make_nested_endnote_docx(source)
    changed.write_bytes(source.read_bytes())

    def remove_italic(data: bytes) -> bytes:
        return data.replace(b"<w:i/>", b"")

    _rewrite_document_xml(changed, remove_italic)
    result = verify_docx(source, changed)
    assert result["status"] == "fail"
    assert any("formatted text spans changed" in item for item in result["failures"])


def test_verify_allows_only_declared_atomic_reordering(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    reordered = tmp_path / "reordered.docx"
    make_nested_endnote_docx(source)
    reordered.write_bytes(source.read_bytes())

    def reverse_paragraphs(data: bytes) -> bytes:
        root = etree.fromstring(data)
        body = root.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}body")
        assert body is not None
        paragraphs = body.findall("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p")
        body.remove(paragraphs[0])
        body.insert(1, paragraphs[0])
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    _rewrite_document_xml(reordered, reverse_paragraphs)
    strict = verify_docx(source, reordered)
    assert strict["status"] == "fail"
    assert "Atomic EndNote field order changed." in strict["failures"]

    expected = [
        field.field_sha256 for field in scan_docx(reordered).citation_fields
    ]
    allowed = verify_docx(
        source,
        reordered,
        allow_field_reordering=True,
        expected_citation_order=expected,
    )
    assert allowed["status"] == "pass"

    wrong_order = verify_docx(
        source,
        reordered,
        allow_field_reordering=True,
        expected_citation_order=list(reversed(expected)),
    )
    assert wrong_order["status"] == "fail"
    assert any("declared transaction order" in item for item in wrong_order["failures"])


def _citation_moves(path: Path) -> list[CitationMoveSpec]:
    fields = scan_docx(path).citation_fields
    return [
        CitationMoveSpec(
            citation_index=index,
            placeholder=f"[[CIT:{index}]]",
            expected_field_sha256=field.field_sha256,
        )
        for index, field in enumerate(fields, start=1)
    ]


def test_atomic_mover_reorders_whole_nested_fields(tmp_path: Path) -> None:
    source = tmp_path / "prepared.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_placeholders(data: bytes) -> bytes:
        return data.replace(
            b'<w:t xml:space="preserve"> evidence </w:t>',
            b'<w:t xml:space="preserve"> evidence [[CIT:2]] then [[CIT:1]] </w:t>',
        )

    _rewrite_document_xml(source, add_placeholders)
    before = scan_docx(source)
    result = move_citation_fields(source, output, _citation_moves(source))

    assert result["status"] == "pass"
    assert output.exists()
    after = scan_docx(output)
    assert [field.field_sha256 for field in after.citation_fields] == [
        before.citation_fields[1].field_sha256,
        before.citation_fields[0].field_sha256,
    ]
    assert len(after.endnote_fields) == 3
    assert len(after.atomic_endnote_fields) == 2
    assert after.record_identities == [
        "test-database-id:102",
        "test-database-id:103",
        "test-database-id:101",
    ]
    assert after.superscript_count == before.superscript_count
    assert after.subscript_count == before.subscript_count
    assert result["verification"]["checks"]["format_spans_identical"]


def test_atomic_mover_requires_complete_frozen_plan(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    first = _citation_moves(source)[0]

    with pytest.raises(DocxError, match="every citation field"):
        move_citation_fields(source, output, [first])
    assert not output.exists()


def test_atomic_mover_rejects_stale_field_hash(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    moves = _citation_moves(source)
    moves[0].expected_field_sha256 = "0" * 64

    with pytest.raises(DocxError, match="does not match the frozen plan"):
        move_citation_fields(source, output, moves)
    assert not output.exists()


def test_atomic_mover_rejects_placeholder_inside_field(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_unsafe_placeholders(data: bytes) -> bytes:
        return data.replace(b"<w:t>[1]</w:t>", b"<w:t>[[CIT:1]]</w:t>").replace(
            b"Combined evidence ", b"Combined evidence [[CIT:2]] "
        )

    _rewrite_document_xml(source, add_unsafe_placeholders)
    with pytest.raises(DocxError, match="inside a complex Word field"):
        move_citation_fields(source, output, _citation_moves(source))
    assert not output.exists()


def test_atomic_mover_rejects_placeholder_in_tracked_change(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_tracked_placeholders(data: bytes) -> bytes:
        return data.replace(
            b'<w:r><w:t xml:space="preserve"> evidence </w:t></w:r>',
            b'<w:ins w:id="1"><w:r><w:t xml:space="preserve"> evidence [[CIT:1]] </w:t></w:r></w:ins>',
        ).replace(
            b"Combined evidence ", b"Combined evidence [[CIT:2]] "
        )

    _rewrite_document_xml(source, add_tracked_placeholders)
    with pytest.raises(DocxError, match="inside tracked changes"):
        move_citation_fields(source, output, _citation_moves(source))
    assert not output.exists()


def test_atomic_mover_rejects_multi_paragraph_citation(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_docx(source)

    def span_paragraphs(data: bytes) -> bytes:
        root = etree.fromstring(data)
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        body = root.find(f"{ns}body")
        assert body is not None
        first = body.find(f"{ns}p")
        assert first is not None
        end = next(
            run
            for run in first.findall(f"{ns}r")
            if run.find(f'{ns}fldChar[@{ns}fldCharType="end"]') is not None
        )
        first.remove(end)
        second = etree.Element(f"{ns}p")
        second.append(end)
        run = etree.SubElement(second, f"{ns}r")
        text = etree.SubElement(run, f"{ns}t")
        text.text = "[[CIT:1]]"
        body.insert(1, second)
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    _rewrite_document_xml(source, span_paragraphs)
    with pytest.raises(DocxError, match="Multi-paragraph citation fields"):
        move_citation_fields(source, output, _citation_moves(source))
    assert not output.exists()


def test_repeated_record_multiplicity_survives_atomic_movement(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source, repeat_first_record=True)

    def add_placeholders(data: bytes) -> bytes:
        return data.replace(
            b'<w:t xml:space="preserve"> evidence </w:t>',
            b'<w:t xml:space="preserve"> evidence [[CIT:2]] [[CIT:3]] [[CIT:1]] </w:t>',
        )

    _rewrite_document_xml(source, add_placeholders)
    before = scan_docx(source)
    assert len(before.citation_fields) == 3
    assert before.record_identities.count("test-database-id:101") == 2
    assert before.citation_fields[0].field_sha256 == before.citation_fields[2].field_sha256

    result = move_citation_fields(source, output, _citation_moves(source))
    after = scan_docx(output)
    assert result["status"] == "pass"
    assert after.record_identities == [
        "test-database-id:102",
        "test-database-id:103",
        "test-database-id:101",
        "test-database-id:101",
    ]
    assert after.record_identities.count("test-database-id:101") == 2
    assert result["verification"]["checks"]["record_identities_identical"]


def test_verifier_detects_loss_of_one_repeated_record_instance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    changed = tmp_path / "changed.docx"
    make_nested_endnote_docx(source, repeat_first_record=True)
    make_nested_endnote_docx(changed)

    result = verify_docx(source, changed)
    assert result["status"] == "fail"
    assert any("record identities changed" in item for item in result["failures"])


def test_experiment_text_excludes_fields_hidden_text_and_deletions(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.docx"
    make_docx(source, outside="Visible words.")

    def add_excluded_text(data: bytes) -> bytes:
        return data.replace(
            b"<w:sectPr/>",
            b"""
    <w:p>
      <w:r><w:rPr><w:vanish/></w:rPr><w:t>Hidden words.</w:t></w:r>
      <w:del w:id="1"><w:r><w:delText>Deleted words.</w:delText></w:r></w:del>
    </w:p>
    <w:sectPr/>""",
        )

    _rewrite_document_xml(source, add_excluded_text)
    report = extract_visible_introduction(source, paragraph_indices=[1, 2])
    assert report["text"] == "Visible words. \n"
    assert report["word_count"] == 2

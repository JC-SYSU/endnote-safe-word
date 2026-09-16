from __future__ import annotations

import zipfile
from pathlib import Path

from fixture_factory import make_nested_endnote_docx
from lxml import etree

from endnote_safe_word.change_surface import check_docx_change_surface


def _rewrite_package(
    source: Path,
    output: Path,
    *,
    transforms: dict[str, object] | None = None,
    additions: dict[str, bytes] | None = None,
) -> None:
    transforms = transforms or {}
    additions = additions or {}
    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(output, "w") as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            transform = transforms.get(info.filename)
            if callable(transform):
                data = transform(data)
            dst.writestr(info, data)
        for name, data in additions.items():
            dst.writestr(name, data)


def test_change_surface_allows_text_only_in_declared_paragraph(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def edit(data: bytes) -> bytes:
        return data.replace(
            b'<w:t xml:space="preserve"> evidence </w:t>',
            b'<w:t xml:space="preserve"> updated evidence </w:t>',
        )

    _rewrite_package(source, output, transforms={"word/document.xml": edit})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "pass"
    assert result["changes"] == [
        {
            "part": "word/document.xml",
            "change": "modified",
            "class": "intended_content_change",
        }
    ]


def test_change_surface_rejects_text_outside_declared_paragraph(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def edit(data: bytes) -> bytes:
        return data.replace(b"Combined evidence ", b"Changed evidence ")

    _rewrite_package(source, output, transforms={"word/document.xml": edit})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert any("outside the declared" in item for item in result["failures"])


def test_change_surface_rejects_paragraph_property_change(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_properties(data: bytes) -> bytes:
        root = etree.fromstring(data)
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraph = next(root.iter(f"{namespace}p"))
        properties = etree.Element(f"{namespace}pPr")
        etree.SubElement(properties, f"{namespace}keepNext")
        paragraph.insert(0, properties)
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    _rewrite_package(
        source, output, transforms={"word/document.xml": add_properties}
    )
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert "Paragraph properties changed in editable paragraph 1." in result["failures"]


def test_change_surface_rejects_new_tracked_change(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def track(data: bytes) -> bytes:
        return data.replace(
            b'<w:r><w:t xml:space="preserve"> evidence </w:t></w:r>',
            b'<w:ins w:id="7"><w:r><w:t xml:space="preserve"> evidence </w:t></w:r></w:ins>',
        )

    _rewrite_package(source, output, transforms={"word/document.xml": track})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert any("Tracked changes" in item for item in result["failures"])


def test_change_surface_rejects_undeclared_run_properties(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_bold(data: bytes) -> bytes:
        return data.replace(
            b'<w:r><w:t xml:space="preserve"> evidence </w:t></w:r>',
            b'<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve"> evidence </w:t></w:r>',
        )

    _rewrite_package(source, output, transforms={"word/document.xml": add_bold})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert any("run property set" in item for item in result["failures"])


def test_change_surface_rejects_unexpected_part(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    _rewrite_package(source, output, additions={"word/unexpected.xml": b"<unexpected/>"})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert any("Unexpected package parts" in item for item in result["failures"])


def test_change_surface_rejects_dangling_relationship(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    def add_dangling(data: bytes) -> bytes:
        root = etree.fromstring(data)
        namespace = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        relationship = etree.SubElement(root, f"{namespace}Relationship")
        relationship.set("Id", "rIdMissing")
        relationship.set(
            "Type",
            "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image",
        )
        relationship.set("Target", "word/missing.png")
        return etree.tostring(root, encoding="UTF-8", xml_declaration=True)

    _rewrite_package(source, output, transforms={"_rels/.rels": add_dangling})
    result = check_docx_change_surface(
        source, output, editable_paragraphs=[1]
    )
    assert result["status"] == "fail"
    assert any("targets missing part" in item for item in result["failures"])


def test_change_surface_rejects_added_unknown_package_part(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)
    custom_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Properties '
        b'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
        b'custom-properties">'
        b"<property name=\"OfficeCLI.Version\" fmtid=\""
        b"{D5CDD505-2E9C-101B-9397-08002B2CF9AE}\" pid=\"2\">"
        b"<vt:lpwstr xmlns:vt=\"http://schemas.openxmlformats.org/officeDocument/"
        b"2006/docPropsVTypes\">1.0.136</vt:lpwstr></property></Properties>"
    )
    _rewrite_package(
        source, output, additions={"docProps/custom.xml": custom_xml}
    )
    result = check_docx_change_surface(source, output, editable_paragraphs=[1])
    assert result["status"] == "fail"
    assert any("parts were added" in item for item in result["failures"])
    assert "officecli_version" not in result


def test_change_surface_reports_no_officecli_machinery(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    make_nested_endnote_docx(source)

    _rewrite_package(
        source,
        output,
        transforms={"word/document.xml": lambda data: data},
    )
    result = check_docx_change_surface(source, output, editable_paragraphs=[1])
    assert "officecli_version" not in result
    assert all(item["class"] != "known_tool_metadata" for item in result["changes"])

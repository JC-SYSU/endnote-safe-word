from __future__ import annotations

import hashlib
import posixpath
import zipfile
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from lxml import etree

from .constants import W
from .ooxml import (
    DocxError,
    element_token,
    ensure_docx,
    parse_xml,
    sha256_file,
    token_hash,
)

_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_PROTECTED_TAGS = {
    f"{W}ins",
    f"{W}del",
    f"{W}moveFrom",
    f"{W}moveTo",
    f"{W}sdt",
    f"{W}hyperlink",
    f"{W}bookmarkStart",
    f"{W}bookmarkEnd",
    f"{W}commentRangeStart",
    f"{W}commentRangeEnd",
    f"{W}commentReference",
    f"{W}footnoteReference",
    f"{W}endnoteReference",
    f"{W}drawing",
    f"{W}object",
    f"{W}txbxContent",
}
_ORDINARY_RUN_CHILDREN = {
    f"{W}rPr",
    f"{W}t",
    f"{W}lastRenderedPageBreak",
}


def _part_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("word/document.xml has no body.")
    return [child for child in body if child.tag == f"{W}p"]


def _protected_multiset(root: etree._Element) -> Counter[str]:
    return Counter(
        token_hash([element_token(item) for item in element.iter()])
        for element in root.iter()
        if element.tag in _PROTECTED_TAGS
    )


def _run_property_hash(run: etree._Element) -> str:
    rpr = run.find(f"{W}rPr")
    if rpr is None or (len(rpr) == 0 and not rpr.attrib and not (rpr.text or "").strip()):
        return "none"
    return token_hash([element_token(element) for element in rpr.iter()])


def _ordinary_run_property_hashes(paragraphs: list[etree._Element]) -> set[str]:
    hashes: set[str] = set()
    depth = 0
    for paragraph in paragraphs:
        for element in paragraph.iter():
            if element.tag == f"{W}fldChar":
                field_type = element.get(f"{W}fldCharType", "")
                if field_type == "begin":
                    depth += 1
                elif field_type == "end":
                    depth = max(0, depth - 1)
            elif element.tag == f"{W}r" and depth == 0:
                if any(child.tag == f"{W}t" for child in element):
                    hashes.add(_run_property_hash(element))
    return hashes


def _validate_editable_runs(
    paragraphs: list[tuple[int, etree._Element]],
    allowed_property_hashes: set[str],
    *,
    allow_new_run_properties: bool,
) -> list[str]:
    failures: list[str] = []
    depth = 0
    for paragraph_index, paragraph in paragraphs:
        for element in paragraph.iter():
            if element.tag == f"{W}fldChar":
                field_type = element.get(f"{W}fldCharType", "")
                if field_type == "begin":
                    depth += 1
                elif field_type == "end":
                    depth = max(0, depth - 1)
            elif element.tag == f"{W}r" and depth == 0:
                if not any(child.tag == f"{W}t" for child in element):
                    continue
                unexpected = [
                    etree.QName(child).localname
                    for child in element
                    if child.tag not in _ORDINARY_RUN_CHILDREN
                ]
                if unexpected:
                    failures.append(
                        f"Editable paragraph {paragraph_index} ordinary run contains "
                        f"unsupported children: {unexpected}."
                    )
                property_hash = _run_property_hash(element)
                if (
                    not allow_new_run_properties
                    and property_hash not in allowed_property_hashes
                ):
                    failures.append(
                        f"Editable paragraph {paragraph_index} introduced an "
                        "undeclared ordinary-run property set."
                    )
    return failures


def _normalized_document_tokens(
    root: etree._Element,
    editable_paragraphs: set[int],
) -> list[tuple]:
    normalized = deepcopy(root)
    for index, paragraph in enumerate(_paragraphs(normalized), start=1):
        if index not in editable_paragraphs:
            continue
        ppr = paragraph.find(f"{W}pPr")
        for child in list(paragraph):
            if child is not ppr:
                paragraph.remove(child)
        sentinel_run = etree.SubElement(paragraph, f"{W}r")
        sentinel_text = etree.SubElement(sentinel_run, f"{W}t")
        sentinel_text.text = f"EDITABLE-PARAGRAPH-{index}"
    return [element_token(element) for element in normalized.iter()]


def _validate_document_change(
    before_data: bytes,
    after_data: bytes,
    editable_paragraphs: list[int],
    *,
    allow_new_run_properties: bool,
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    before = parse_xml(before_data, "word/document.xml")
    after = parse_xml(after_data, "word/document.xml")
    before_paragraphs = _paragraphs(before)
    after_paragraphs = _paragraphs(after)
    editable = set(editable_paragraphs)
    if len(before_paragraphs) != len(after_paragraphs):
        failures.append(
            "Body paragraph count changed; this first-round policy permits content "
            "changes only within existing editable paragraphs."
        )
    for index in editable:
        if index > len(before_paragraphs) or index > len(after_paragraphs):
            failures.append(f"Editable paragraph {index} does not exist in both files.")
            continue
        before_ppr = before_paragraphs[index - 1].find(f"{W}pPr")
        after_ppr = after_paragraphs[index - 1].find(f"{W}pPr")
        before_tokens = (
            []
            if before_ppr is None
            else [element_token(item) for item in before_ppr.iter()]
        )
        after_tokens = (
            []
            if after_ppr is None
            else [element_token(item) for item in after_ppr.iter()]
        )
        if before_tokens != after_tokens:
            failures.append(f"Paragraph properties changed in editable paragraph {index}.")

    if len(before_paragraphs) == len(after_paragraphs):
        before_tokens = _normalized_document_tokens(before, editable)
        after_tokens = _normalized_document_tokens(after, editable)
        if before_tokens != after_tokens:
            failures.append(
                "Document nodes outside the declared editable paragraph contents changed."
            )

    if _protected_multiset(before) != _protected_multiset(after):
        failures.append(
            "Tracked changes, moves, content controls, links, bookmarks, comments, "
            "notes, drawings, objects, or text boxes changed."
        )

    source_property_hashes = _ordinary_run_property_hashes(
        [
            paragraph
            for index, paragraph in enumerate(before_paragraphs, start=1)
            if index in editable
        ]
    )
    failures.extend(
        _validate_editable_runs(
            [
                (index, paragraph)
                for index, paragraph in enumerate(after_paragraphs, start=1)
                if index in editable
            ],
            source_property_hashes,
            allow_new_run_properties=allow_new_run_properties,
        )
    )
    return failures, {
        "before_paragraph_count": len(before_paragraphs),
        "after_paragraph_count": len(after_paragraphs),
        "editable_paragraphs": sorted(editable),
        "source_ordinary_run_property_hashes": sorted(source_property_hashes),
        "allow_new_run_properties": allow_new_run_properties,
        "protected_nodes_identical": _protected_multiset(before)
        == _protected_multiset(after),
    }


def _relationship_source_part(rels_part: str) -> str:
    if rels_part == "_rels/.rels":
        return ""
    directory, filename = posixpath.split(rels_part)
    if not directory.endswith("/_rels") or not filename.endswith(".rels"):
        return ""
    base_directory = directory[: -len("/_rels")]
    source_name = filename[: -len(".rels")]
    return posixpath.join(base_directory, source_name)


def _dangling_relationships(zf: zipfile.ZipFile) -> list[str]:
    names = set(zf.namelist())
    failures: list[str] = []
    for part in sorted(name for name in names if name.endswith(".rels")):
        root = parse_xml(zf.read(part), part)
        source_part = _relationship_source_part(part)
        base = posixpath.dirname(source_part)
        for relationship in root:
            if relationship.tag != f"{_REL_NS}Relationship":
                continue
            if relationship.get("TargetMode") == "External":
                continue
            target = relationship.get("Target", "").split("#", 1)[0]
            if not target:
                failures.append(f"{part} contains an empty internal relationship target.")
                continue
            if target.startswith("/"):
                resolved = posixpath.normpath(target.lstrip("/"))
            else:
                resolved = posixpath.normpath(posixpath.join(base, target))
            if resolved not in names:
                failures.append(
                    f"{part} relationship {relationship.get('Id', '')} targets "
                    f"missing part {resolved}."
                )
    return failures


def check_docx_change_surface(
    before_path: str | Path,
    after_path: str | Path,
    *,
    editable_paragraphs: list[int],
    allow_new_run_properties: bool = False,
) -> dict[str, Any]:
    before_file = ensure_docx(before_path)
    after_file = ensure_docx(after_path)
    if not editable_paragraphs or any(
        not isinstance(index, int) or index < 1 for index in editable_paragraphs
    ):
        raise DocxError("editable_paragraphs must contain positive integers.")
    if len(set(editable_paragraphs)) != len(editable_paragraphs):
        raise DocxError("editable_paragraphs must not contain duplicates.")

    failures: list[str] = []
    changes: list[dict[str, Any]] = []
    with zipfile.ZipFile(before_file, "r") as before_zip, zipfile.ZipFile(
        after_file, "r"
    ) as after_zip:
        before_names = before_zip.namelist()
        after_names = after_zip.namelist()
        if len(before_names) != len(set(before_names)):
            failures.append("Source package contains duplicate ZIP part names.")
        if len(after_names) != len(set(after_names)):
            failures.append("Output package contains duplicate ZIP part names.")
        before_set = set(before_names)
        after_set = set(after_names)
        deleted = sorted(before_set - after_set)
        added = sorted(after_set - before_set)
        if deleted:
            failures.append(f"Package parts were deleted: {deleted}.")
        if added:
            failures.append(f"Unexpected package parts were added: {added}.")
        for part in added:
            changes.append({"part": part, "change": "added", "class": "unexpected_change"})

        common = sorted(before_set & after_set)
        changed_parts = [
            part
            for part in common
            if _part_hash(before_zip.read(part)) != _part_hash(after_zip.read(part))
        ]
        document_details: dict[str, Any] = {}
        for part in changed_parts:
            before_data = before_zip.read(part)
            after_data = after_zip.read(part)
            classification = "unexpected_change"
            if part == "word/document.xml":
                document_failures, document_details = _validate_document_change(
                    before_data,
                    after_data,
                    editable_paragraphs,
                    allow_new_run_properties=allow_new_run_properties,
                )
                failures.extend(document_failures)
                classification = (
                    "intended_content_change"
                    if not document_failures
                    else "unexpected_change"
                )
            else:
                failures.append(f"Unexpected package part changed: {part}.")
            changes.append({"part": part, "change": "modified", "class": classification})

        failures.extend(_dangling_relationships(after_zip))

    failures = list(dict.fromkeys(failures))
    return {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "before": str(before_file),
        "before_sha256": sha256_file(before_file),
        "after": str(after_file),
        "after_sha256": sha256_file(after_file),
        "editable_paragraphs": editable_paragraphs,
        "allow_new_run_properties": allow_new_run_properties,
        "changes": changes,
        "document": document_details,
        "failures": failures,
    }

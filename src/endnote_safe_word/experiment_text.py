from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from .constants import W
from .ooxml import DocxError, ancestor_has_tag, ensure_docx, parse_xml, sha256_file


WORD_PATTERN = r"[A-Za-z0-9\u0370-\u03FF]+(?:[-\u2013][A-Za-z0-9\u0370-\u03FF]+)*"
_WORD_RE = re.compile(WORD_PATTERN)


def _is_on_off_enabled(element: etree._Element | None) -> bool:
    if element is None:
        return False
    value = element.get(f"{W}val")
    return value is None or value.lower() not in {"0", "false", "off", "none"}


def _run_is_directly_hidden(node: etree._Element) -> bool:
    parent = node.getparent()
    while parent is not None and parent.tag != f"{W}r":
        parent = parent.getparent()
    if parent is None:
        return False
    rpr = parent.find(f"{W}rPr")
    return rpr is not None and _is_on_off_enabled(rpr.find(f"{W}vanish"))


def extract_visible_introduction(
    path: str | Path,
    *,
    paragraph_indices: list[int] | None = None,
) -> dict[str, Any]:
    document = ensure_docx(path)
    selected = paragraph_indices or [1, 2, 3, 4]
    if (
        not selected
        or any(not isinstance(index, int) or index < 1 for index in selected)
        or len(set(selected)) != len(selected)
    ):
        raise DocxError("paragraph_indices must contain unique positive integers.")

    with zipfile.ZipFile(document, "r") as zf:
        root = parse_xml(zf.read("word/document.xml"), "word/document.xml")
    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("word/document.xml has no body.")
    paragraphs = [child for child in body if child.tag == f"{W}p"]
    if max(selected) > len(paragraphs):
        raise DocxError("Selected introduction paragraph is out of range.")

    selected_set = set(selected)
    depth = 0
    visible_paragraphs: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        chunks: list[str] = []
        for element in paragraph.iter():
            if element.tag == f"{W}fldChar":
                field_type = element.get(f"{W}fldCharType", "")
                if field_type == "begin":
                    depth += 1
                elif field_type == "end":
                    if depth < 1:
                        raise DocxError(
                            f"Unmatched complex-field end before paragraph {paragraph_index}."
                        )
                    depth -= 1
            elif element.tag == f"{W}t" and paragraph_index in selected_set:
                if depth or ancestor_has_tag(element, f"{W}fldSimple"):
                    continue
                if ancestor_has_tag(element, f"{W}del") or _run_is_directly_hidden(
                    element
                ):
                    continue
                chunks.append(element.text or "")
        if paragraph_index in selected_set:
            text = "".join(chunks)
            visible_paragraphs.append(
                {
                    "paragraph_index": paragraph_index,
                    "text": text,
                    "word_count": len(_WORD_RE.findall(text)),
                }
            )
        if paragraph_index >= max(selected):
            break

    if depth:
        raise DocxError(
            "A complex field crossing the selected introduction boundary is unsupported."
        )
    combined = "\n".join(item["text"] for item in visible_paragraphs)
    words = _WORD_RE.findall(combined)
    return {
        "schema_version": 1,
        "path": str(document),
        "file_sha256": sha256_file(document),
        "paragraph_indices": selected,
        "word_pattern": WORD_PATTERN,
        "exclusions": [
            "complex_field_contents",
            "simple_field_contents",
            "direct_hidden_runs",
            "deleted_revision_text",
            "paragraphs_outside_selection",
        ],
        "paragraphs": visible_paragraphs,
        "text": combined,
        "words": words,
        "word_count": len(words),
    }

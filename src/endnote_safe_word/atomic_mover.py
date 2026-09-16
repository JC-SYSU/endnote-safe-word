from __future__ import annotations

import json
import re
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from .constants import W, XML_SPACE
from .ooxml import (
    DocxError,
    ancestor_has_tag,
    ensure_docx,
    nearest_paragraph,
    parse_xml,
    serialize_xml,
    write_docx_with_replacements,
)
from .scanner import classify_endnote, scan_docx, scan_part
from .verifier import compare_reports


@dataclass(slots=True)
class CitationMoveSpec:
    citation_index: int
    placeholder: str
    expected_field_sha256: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CitationMoveSpec":
        index = data.get("citation_index")
        placeholder = data.get("placeholder")
        expected = data.get("expected_field_sha256")
        if not isinstance(index, int) or index < 1:
            raise DocxError("'citation_index' must be a positive integer.")
        if not isinstance(placeholder, str) or not placeholder:
            raise DocxError("'placeholder' must be a non-empty string.")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise DocxError("'expected_field_sha256' must be a lowercase SHA-256 hash.")
        return cls(
            citation_index=index,
            placeholder=placeholder,
            expected_field_sha256=expected,
        )


@dataclass(slots=True)
class _AtomicRange:
    kind: str
    field_sha256: str
    start: etree._Element
    end: etree._Element


@dataclass(slots=True)
class _PlaceholderTarget:
    spec: CitationMoveSpec
    text_node: etree._Element
    run: etree._Element
    paragraph: etree._Element
    character_offset: int


def load_move_specs(path: str | Path) -> list[CitationMoveSpec]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        payload = payload.get("moves")
    if not isinstance(payload, list) or not payload:
        raise DocxError("Move JSON must be a non-empty list or {'moves': [...]}.")
    if not all(isinstance(item, dict) for item in payload):
        raise DocxError("Every citation move must be a JSON object.")
    return [CitationMoveSpec.from_dict(item) for item in payload]


def _atomic_endnote_ranges(
    root: etree._Element,
    document_xml: bytes,
) -> list[_AtomicRange]:
    depth = 0
    start: etree._Element | None = None
    instruction_chunks: list[str] = []
    raw_ranges: list[tuple[str, etree._Element, etree._Element]] = []

    for element in root.iter():
        if element.tag == f"{W}fldChar":
            field_type = element.get(f"{W}fldCharType", "")
            if field_type == "begin":
                if depth == 0:
                    start = element
                    instruction_chunks = []
                depth += 1
            elif field_type == "end":
                if depth < 1:
                    raise DocxError("Unmatched complex-field end marker in document.xml.")
                if depth == 1:
                    if start is None:
                        raise DocxError("Complex-field range has no start marker.")
                    kind = classify_endnote("".join(instruction_chunks))
                    if kind:
                        raw_ranges.append((kind, start, element))
                    start = None
                    instruction_chunks = []
                depth -= 1
        elif element.tag == f"{W}instrText" and depth == 1 and element.text:
            instruction_chunks.append(element.text)

    if depth != 0:
        raise DocxError("Unclosed complex field in document.xml.")

    summary = scan_part("word/document.xml", document_xml)
    complex_atomic = [
        field
        for field in summary.endnote_fields
        if field.top_level
        and field.kind != "ADDIN EN.CITE.DATA"
        and not field.simple_field
    ]
    if any(
        field.simple_field
        for field in summary.endnote_fields
        if field.top_level and field.kind != "ADDIN EN.CITE.DATA"
    ):
        raise DocxError("Simple EndNote fields are unsupported by the atomic mover.")
    if len(raw_ranges) != len(complex_atomic):
        raise DocxError(
            "Could not correlate top-level EndNote ranges with semantic field records."
        )

    ranges: list[_AtomicRange] = []
    for raw, field in zip(raw_ranges, complex_atomic):
        kind, begin, end = raw
        if kind != field.kind:
            raise DocxError(
                "Top-level EndNote field classification changed during range mapping."
            )
        ranges.append(
            _AtomicRange(
                kind=kind,
                field_sha256=field.field_sha256,
                start=begin,
                end=end,
            )
        )
    return ranges


def _field_depth_by_text(root: etree._Element) -> dict[etree._Element, int]:
    depth = 0
    result: dict[etree._Element, int] = {}
    for element in root.iter():
        if element.tag == f"{W}fldChar":
            field_type = element.get(f"{W}fldCharType", "")
            if field_type == "begin":
                depth += 1
            elif field_type == "end":
                depth = max(0, depth - 1)
        elif element.tag == f"{W}t":
            result[element] = depth
    return result


def _direct_run_child(
    marker: etree._Element,
    paragraph: etree._Element,
) -> etree._Element:
    node = marker
    while node.getparent() is not None and node.getparent() is not paragraph:
        node = node.getparent()
    if node.getparent() is not paragraph or node.tag != f"{W}r":
        raise DocxError(
            "Citation field boundary must be in a direct paragraph run; "
            "tracked, hyperlink, and content-control wrappers are refused."
        )
    return node


def _detach_field_elements(field: _AtomicRange) -> list[etree._Element]:
    start_paragraph = nearest_paragraph(field.start)
    end_paragraph = nearest_paragraph(field.end)
    if start_paragraph is None or end_paragraph is None:
        raise DocxError("Citation field boundary is not inside a paragraph.")
    if start_paragraph is not end_paragraph:
        raise DocxError("Multi-paragraph citation fields are unsupported.")

    start_run = _direct_run_child(field.start, start_paragraph)
    end_run = _direct_run_child(field.end, start_paragraph)
    if start_run.xpath(".//w:t | .//w:delText", namespaces={"w": W[1:-1]}):
        raise DocxError("Citation begin run contains text and cannot be moved atomically.")
    if end_run.xpath(".//w:t | .//w:delText", namespaces={"w": W[1:-1]}):
        raise DocxError("Citation end run contains text and cannot be moved atomically.")

    children = list(start_paragraph)
    start_index = children.index(start_run)
    end_index = children.index(end_run)
    if end_index < start_index:
        raise DocxError("Citation field boundaries are reversed.")
    elements = children[start_index : end_index + 1]
    for element in elements:
        start_paragraph.remove(element)
    return elements


def _unsafe_placeholder_reason(
    node: etree._Element,
    field_depth: int,
) -> str | None:
    if field_depth:
        return "placeholder is inside a complex Word field"
    if ancestor_has_tag(node, f"{W}fldSimple"):
        return "placeholder is inside a simple Word field"
    if ancestor_has_tag(node, f"{W}ins") or ancestor_has_tag(node, f"{W}del"):
        return "placeholder is inside tracked changes"
    if ancestor_has_tag(node, f"{W}moveFrom") or ancestor_has_tag(node, f"{W}moveTo"):
        return "placeholder is inside a tracked move"
    if ancestor_has_tag(node, f"{W}sdt"):
        return "placeholder is inside a content control"
    return None


def _placeholder_targets(
    root: etree._Element,
    specs: list[CitationMoveSpec],
) -> list[_PlaceholderTarget]:
    depths = _field_depth_by_text(root)
    targets: list[_PlaceholderTarget] = []
    for spec in specs:
        matches: list[etree._Element] = []
        unsafe: list[str] = []
        for node in root.iter(f"{W}t"):
            count = (node.text or "").count(spec.placeholder)
            if not count:
                continue
            reason = _unsafe_placeholder_reason(node, depths.get(node, 0))
            if reason:
                unsafe.extend([reason] * count)
            else:
                matches.extend([node] * count)
        if unsafe:
            raise DocxError(
                f"Refusing placeholder {spec.placeholder!r}: "
                f"{'; '.join(sorted(set(unsafe)))}."
            )
        if len(matches) != 1:
            raise DocxError(
                f"Placeholder {spec.placeholder!r} must occur exactly once in one "
                f"ordinary text node; found {len(matches)} occurrence(s)."
            )

        node = matches[0]
        run = node.getparent()
        paragraph = nearest_paragraph(node)
        if run is None or run.tag != f"{W}r" or paragraph is None:
            raise DocxError("Placeholder must be in a direct paragraph run.")
        if run.getparent() is not paragraph:
            raise DocxError("Placeholder run must be a direct paragraph child.")
        allowed_children = {f"{W}rPr", f"{W}t"}
        if len(run.findall(f"{W}t")) != 1 or any(
            child.tag not in allowed_children for child in run
        ):
            raise DocxError(
                "Placeholder run contains unsupported non-text content and cannot be split."
            )
        targets.append(
            _PlaceholderTarget(
                spec=spec,
                text_node=node,
                run=run,
                paragraph=paragraph,
                character_offset=(node.text or "").find(spec.placeholder),
            )
        )

    document_order = {node: index for index, node in enumerate(root.iter())}
    return sorted(
        targets,
        key=lambda target: (
            document_order[target.text_node],
            target.character_offset,
        ),
    )


def _text_run(template: etree._Element, text: str) -> etree._Element:
    run = deepcopy(template)
    node = run.find(f"{W}t")
    if node is None:
        raise DocxError("Placeholder run lost its text node during cloning.")
    node.text = text
    if text.startswith(" ") or text.endswith(" "):
        node.set(XML_SPACE, "preserve")
    else:
        node.attrib.pop(XML_SPACE, None)
    return run


def _replace_placeholder_runs(
    targets: list[_PlaceholderTarget],
    field_elements: dict[int, list[etree._Element]],
) -> None:
    by_run: dict[etree._Element, list[_PlaceholderTarget]] = {}
    for target in targets:
        by_run.setdefault(target.run, []).append(target)

    for run, run_targets in by_run.items():
        paragraph = run.getparent()
        if paragraph is None or paragraph.tag != f"{W}p":
            raise DocxError("Placeholder paragraph changed during atomic transaction.")
        original = run.find(f"{W}t")
        if original is None:
            raise DocxError("Placeholder text node disappeared during atomic transaction.")
        text = original.text or ""
        pattern = re.compile(
            "(" + "|".join(
                re.escape(target.spec.placeholder)
                for target in sorted(
                    run_targets,
                    key=lambda item: len(item.spec.placeholder),
                    reverse=True,
                )
            ) + ")"
        )
        target_by_placeholder = {
            target.spec.placeholder: target for target in run_targets
        }
        pieces = pattern.split(text)
        insertion_index = list(paragraph).index(run)
        replacements: list[etree._Element] = []
        for piece in pieces:
            target = target_by_placeholder.get(piece)
            if target is not None:
                replacements.extend(field_elements[target.spec.citation_index])
            elif piece:
                replacements.append(_text_run(run, piece))
        for offset, element in enumerate(replacements):
            paragraph.insert(insertion_index + offset, element)
        paragraph.remove(run)


def move_citation_fields(
    source_path: str | Path,
    output_path: str | Path,
    move_specs: list[CitationMoveSpec],
    *,
    overwrite_output: bool = False,
    keep_failed_output: bool = False,
) -> dict[str, Any]:
    source = ensure_docx(source_path)
    output = Path(output_path).expanduser().resolve()
    before = scan_docx(source)
    if before.errors or not before.all_fields_balanced:
        raise DocxError(
            "Source document failed preflight: XML scan errors or unbalanced fields."
        )
    if not move_specs:
        raise DocxError("At least one citation move is required.")

    citation_count = len(before.citation_fields)
    indexes = [spec.citation_index for spec in move_specs]
    placeholders = [spec.placeholder for spec in move_specs]
    if len(set(indexes)) != len(indexes):
        raise DocxError("Each citation_index may appear only once.")
    if len(set(placeholders)) != len(placeholders):
        raise DocxError("Each placeholder must be unique.")
    if any(
        left != right and left in right
        for left in placeholders
        for right in placeholders
    ):
        raise DocxError("Placeholders must not contain or overlap one another.")
    if sorted(indexes) != list(range(1, citation_count + 1)):
        raise DocxError(
            "Atomic movement requires exactly one move for every citation field; "
            f"expected indexes 1..{citation_count}, received {sorted(indexes)}."
        )
    for spec in move_specs:
        actual = before.citation_fields[spec.citation_index - 1].field_sha256
        if spec.expected_field_sha256 != actual:
            raise DocxError(
                f"Citation {spec.citation_index} field hash does not match the frozen plan."
            )

    with zipfile.ZipFile(source, "r") as zf:
        document_xml = zf.read("word/document.xml")
    root = parse_xml(document_xml, "word/document.xml")
    all_ranges = _atomic_endnote_ranges(root, document_xml)
    citation_ranges = [field for field in all_ranges if field.kind == "ADDIN EN.CITE"]
    if len(citation_ranges) != citation_count:
        raise DocxError("Citation range count does not match semantic preflight.")

    targets = _placeholder_targets(root, move_specs)
    ranges_by_index = {
        index: field for index, field in enumerate(citation_ranges, start=1)
    }
    field_elements: dict[int, list[etree._Element]] = {}
    for spec in move_specs:
        field = ranges_by_index[spec.citation_index]
        if field.field_sha256 != spec.expected_field_sha256:
            raise DocxError("Citation range identity changed before detachment.")
        field_elements[spec.citation_index] = _detach_field_elements(field)

    expected_order = [target.spec.expected_field_sha256 for target in targets]
    _replace_placeholder_runs(targets, field_elements)
    replacement = serialize_xml(root, document_xml)
    write_docx_with_replacements(
        source,
        output,
        {"word/document.xml": replacement},
        overwrite_output=overwrite_output,
    )

    after = scan_docx(output)
    verification = compare_reports(
        before,
        after,
        allow_field_reordering=True,
        expected_citation_order=expected_order,
    )
    result = {
        "schema_version": 1,
        "status": verification["status"],
        "source": str(source),
        "output": str(output),
        "expected_citation_order": expected_order,
        "moves": [
            {
                "citation_index": target.spec.citation_index,
                "placeholder": target.spec.placeholder,
                "field_sha256": target.spec.expected_field_sha256,
            }
            for target in targets
        ],
        "verification": verification,
    }
    if verification["status"] != "pass" and not keep_failed_output:
        output.unlink(missing_ok=True)
        result["output_deleted"] = True
    else:
        result["output_deleted"] = False
    return result

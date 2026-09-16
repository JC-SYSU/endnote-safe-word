from __future__ import annotations

import base64
import binascii
import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from .constants import ENDNOTE_MARKERS, W
from .models import FieldRecord, FormatSpanRecord, PartSummary, ScanReport
from .ooxml import (
    element_token,
    ensure_docx,
    nearest_paragraph,
    paragraph_text,
    parse_xml,
    sha256_file,
    token_hash,
    word_xml_parts,
)


_SIGNIFICANT_RUN_PROPERTIES = {
    "b",
    "bCs",
    "i",
    "iCs",
    "u",
    "strike",
    "dstrike",
    "caps",
    "smallCaps",
    "vertAlign",
}


@dataclass
class _OpenField:
    field_index: int
    paragraph_index: int | None
    nesting_level: int
    parent_field_index: int | None
    tokens: list[tuple] = field(default_factory=list)
    instruction_chunks: list[str] = field(default_factory=list)
    field_data_chunks: list[str] = field(default_factory=list)


def classify_endnote(instruction: str) -> str | None:
    upper = " ".join(instruction.upper().split())
    for marker in ENDNOTE_MARKERS:
        if marker in upper:
            return marker
    return None


def _paragraph_index_map(root: etree._Element) -> dict[etree._Element, int]:
    return {p: i for i, p in enumerate(root.iter(f"{W}p"), start=1)}


def _paragraph_index(
    element: etree._Element, mapping: dict[etree._Element, int]
) -> int | None:
    p = nearest_paragraph(element)
    return mapping.get(p) if p is not None else None


def _begin_run_token(element: etree._Element) -> tuple | None:
    parent = element.getparent()
    while parent is not None and parent.tag != f"{W}r":
        parent = parent.getparent()
    return element_token(parent) if parent is not None else None


def _decode_field_data(text: str) -> bytes | None:
    compact = "".join(text.split())
    if not compact:
        return None
    try:
        return base64.b64decode(compact, validate=True).rstrip(b"\x00")
    except (binascii.Error, ValueError):
        return None


def _extract_endnote_xml(instruction: str) -> bytes | None:
    start = instruction.find("<EndNote>")
    end_marker = "</EndNote>"
    end = instruction.rfind(end_marker)
    if start < 0 or end < start:
        return None
    return instruction[start : end + len(end_marker)].encode("utf-8")


def _record_metadata(
    instruction: str, field_data_chunks: list[str]
) -> tuple[list[str], list[str], list[str], str]:
    payloads: list[bytes] = []
    inline = _extract_endnote_xml(instruction)
    if inline:
        payloads.append(inline)
    for chunk in field_data_chunks:
        decoded = _decode_field_data(chunk)
        if decoded:
            payloads.append(decoded)

    for payload in payloads:
        try:
            root = etree.fromstring(
                payload,
                parser=etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    recover=False,
                    huge_tree=True,
                ),
            )
        except etree.XMLSyntaxError:
            continue

        record_ids: list[str] = []
        record_numbers: list[str] = []
        database_ids: list[str] = []
        cites = root.xpath("./Cite")
        for cite in cites:
            number = "".join(cite.xpath("./RecNum/text()")[:1]).strip()
            if not number:
                number = "".join(
                    cite.xpath("./record/rec-number/text()")[:1]
                ).strip()
            key_nodes = cite.xpath("./record/foreign-keys/key")
            database_id = ""
            key_number = ""
            if key_nodes:
                database_id = key_nodes[0].get("db-id", "").strip()
                key_number = (key_nodes[0].text or "").strip()
            stable_number = key_number or number
            if stable_number:
                record_numbers.append(stable_number)
                database_ids.append(database_id)
                record_ids.append(
                    f"{database_id}:{stable_number}"
                    if database_id
                    else stable_number
                )

        display = "".join(root.xpath("./Cite/DisplayText/text()")[:1])
        if record_ids or display:
            return record_ids, record_numbers, database_ids, display

    return [], [], [], ""


def _format_span(
    part: str,
    run: etree._Element,
    paragraph_map: dict[etree._Element, int],
) -> FormatSpanRecord | None:
    if run.find(f"{W}fldChar") is not None or run.find(f"{W}instrText") is not None:
        return None
    text = "".join(node.text or "" for node in run.iter(f"{W}t"))
    if not text:
        return None
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return None

    properties: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    property_tokens: list[tuple] = []
    for child in rpr:
        local = etree.QName(child).localname
        if local not in _SIGNIFICANT_RUN_PROPERTIES:
            continue
        attrs = tuple(sorted((str(k), str(v)) for k, v in child.attrib.items()))
        properties.append((local, attrs))
        property_tokens.append(element_token(child))
    if not properties:
        return None
    return FormatSpanRecord(
        part=part,
        paragraph_index=_paragraph_index(run, paragraph_map),
        text=text,
        properties=properties,
        properties_sha256=token_hash(property_tokens),
    )


def scan_part(part: str, data: bytes) -> PartSummary:
    root = parse_xml(data, part)
    summary = PartSummary(part=part)
    paragraph_map = _paragraph_index_map(root)
    open_fields: list[_OpenField] = []
    field_counter = 0

    for element in root.iter():
        token = element_token(element)
        for opened in open_fields:
            opened.tokens.append(token)

        if element.tag == f"{W}r" and not open_fields:
            span = _format_span(part, element, paragraph_map)
            if span:
                summary.format_spans.append(span)

        if element.tag == f"{W}fldChar":
            field_type = element.get(f"{W}fldCharType", "")
            if field_type == "begin":
                summary.begin_count += 1
                field_counter += 1
                run_token = _begin_run_token(element)
                opened = _OpenField(
                    field_index=field_counter,
                    paragraph_index=_paragraph_index(element, paragraph_map),
                    nesting_level=len(open_fields) + 1,
                    parent_field_index=(
                        open_fields[-1].field_index if open_fields else None
                    ),
                    tokens=([run_token] if run_token is not None else []) + [token],
                )
                open_fields.append(opened)
            elif field_type == "separate":
                summary.separate_count += 1
            elif field_type == "end":
                summary.end_count += 1
                if not open_fields:
                    summary.unmatched_end_fields += 1
                    continue
                closed = open_fields.pop()
                instruction = "".join(closed.instruction_chunks)
                kind = classify_endnote(instruction)
                if kind:
                    record_ids, record_numbers, database_ids, display = (
                        _record_metadata(instruction, closed.field_data_chunks)
                    )
                    start_paragraph = next(
                        (
                            paragraph
                            for paragraph, index in paragraph_map.items()
                            if index == closed.paragraph_index
                        ),
                        None,
                    )
                    summary.endnote_fields.append(
                        FieldRecord(
                            part=part,
                            field_index=closed.field_index,
                            paragraph_index=closed.paragraph_index,
                            end_paragraph_index=_paragraph_index(
                                element, paragraph_map
                            ),
                            nesting_level=closed.nesting_level,
                            parent_field_index=closed.parent_field_index,
                            top_level=closed.nesting_level == 1,
                            kind=kind,
                            instruction=instruction,
                            instruction_sha256=hashlib.sha256(
                                instruction.encode("utf-8")
                            ).hexdigest(),
                            field_sha256=token_hash(closed.tokens),
                            text_preview=paragraph_text(start_paragraph),
                            record_ids=record_ids,
                            record_numbers=record_numbers,
                            database_ids=database_ids,
                            display_text=display,
                        )
                    )

        if element.tag == f"{W}instrText" and element.text and open_fields:
            open_fields[-1].instruction_chunks.append(element.text)

        if element.tag == f"{W}fldData" and element.text and open_fields:
            open_fields[-1].field_data_chunks.append(element.text)

        if element.tag == f"{W}fldSimple":
            instruction = element.get(f"{W}instr", "")
            kind = classify_endnote(instruction)
            if kind:
                field_counter += 1
                tokens = [element_token(x) for x in element.iter()]
                record_ids, record_numbers, database_ids, display = _record_metadata(
                    instruction, []
                )
                summary.endnote_fields.append(
                    FieldRecord(
                        part=part,
                        field_index=field_counter,
                        paragraph_index=_paragraph_index(element, paragraph_map),
                        end_paragraph_index=_paragraph_index(element, paragraph_map),
                        nesting_level=len(open_fields) + 1,
                        parent_field_index=(
                            open_fields[-1].field_index if open_fields else None
                        ),
                        top_level=not open_fields,
                        kind=kind,
                        instruction=instruction,
                        instruction_sha256=hashlib.sha256(
                            instruction.encode("utf-8")
                        ).hexdigest(),
                        field_sha256=token_hash(tokens),
                        text_preview=paragraph_text(nearest_paragraph(element)),
                        simple_field=True,
                        record_ids=record_ids,
                        record_numbers=record_numbers,
                        database_ids=database_ids,
                        display_text=display,
                    )
                )

        if element.tag == f"{W}vertAlign":
            value = element.get(f"{W}val", "")
            if value == "superscript":
                summary.superscript_count += 1
            elif value == "subscript":
                summary.subscript_count += 1

    summary.unclosed_fields = len(open_fields)
    summary.endnote_fields.sort(key=lambda item: item.field_index)
    return summary


def scan_docx(path: str | Path) -> ScanReport:
    source = ensure_docx(path)
    report = ScanReport(path=str(source), file_sha256=sha256_file(source), parts=[])
    with zipfile.ZipFile(source, "r") as zf:
        for part in sorted(word_xml_parts(zf)):
            try:
                report.parts.append(scan_part(part, zf.read(part)))
            except Exception as exc:  # retain partial report for diagnosis
                report.errors.append(f"{part}: {exc}")
    return report

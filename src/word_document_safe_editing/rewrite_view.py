from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from .atomic_mover import _atomic_endnote_ranges, _direct_run_child
from .change_surface import check_docx_change_surface
from .constants import XML_SPACE, W
from .experiment_text import extract_visible_introduction
from .models import FieldRecord, ScanReport
from .ooxml import (
    DocxError,
    element_token,
    ensure_docx,
    parse_xml,
    serialize_xml,
    sha256_file,
    token_hash,
    write_docx_with_replacements,
)
from .scanner import scan_docx, scan_part
from .verifier import compare_reports

VIEW_SCHEMA_VERSION = 1
VIEW_MODE = "native_ooxml_rewrite"
_CITATION_TOKEN = re.compile(r"\[\[CIT:C[1-9][0-9]*\]\]")
_ANY_RESERVED_TOKEN = re.compile(r"\[\[(?:CIT|FMT):[^\]\r\n]*\]\]")
_RUN_CHILDREN = {f"{W}rPr", f"{W}t", f"{W}lastRenderedPageBreak"}
_IGNORABLE_PARAGRAPH_CHILDREN = {f"{W}proofErr"}
_HIDDEN_PROPERTIES = {f"{W}vanish", f"{W}webHidden"}


@dataclass(slots=True)
class _CitationAtom:
    token: str
    citation_index: int
    paragraph_index: int
    field: FieldRecord
    elements: list[etree._Element]


@dataclass(slots=True)
class _FormatAtom:
    token: str
    paragraph_index: int
    text: str
    run_sha256: str
    run: etree._Element


@dataclass(slots=True)
class _ParagraphModel:
    paragraph_index: int
    paragraph: etree._Element
    source_text: str
    base_run_attributes: dict[str, str]
    base_run_properties: etree._Element | None


@dataclass(slots=True)
class _SourceModel:
    source: Path
    source_sha256: str
    document_xml: bytes
    root: etree._Element
    scan: ScanReport
    paragraph_indices: list[int]
    paragraphs: list[_ParagraphModel]
    citations: dict[str, _CitationAtom]
    formats: dict[str, _FormatAtom]


def _validate_paragraph_indices(paragraph_indices: list[int]) -> list[int]:
    if (
        not paragraph_indices
        or any(not isinstance(index, int) or index < 1 for index in paragraph_indices)
        or len(set(paragraph_indices)) != len(paragraph_indices)
    ):
        raise DocxError("paragraph_indices must contain unique positive integers.")
    ordered = sorted(paragraph_indices)
    if ordered != list(range(ordered[0], ordered[-1] + 1)):
        raise DocxError(
            "Native rewrite paragraphs must form one contiguous body range."
        )
    return ordered


def _body_paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("word/document.xml has no body.")
    paragraphs = [child for child in body if child.tag == f"{W}p"]
    if len(paragraphs) != len(list(root.iter(f"{W}p"))):
        raise DocxError(
            "Native rewrite currently refuses documents with paragraphs inside "
            "tables, text boxes, or other nested body structures."
        )
    return paragraphs


def _run_property_hash(run: etree._Element) -> str:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return "none"
    return token_hash([element_token(element) for element in rpr.iter()])


def _run_hash(run: etree._Element) -> str:
    return token_hash([element_token(element) for element in run.iter()])


def _run_is_hidden(run: etree._Element) -> bool:
    rpr = run.find(f"{W}rPr")
    if rpr is None:
        return False
    for tag in _HIDDEN_PROPERTIES:
        item = rpr.find(tag)
        if item is None:
            continue
        value = item.get(f"{W}val")
        if value is None or value.lower() not in {"0", "false", "off", "none"}:
            return True
    return False


def _ordinary_run_text(run: etree._Element, paragraph_index: int) -> str:
    unexpected = [
        etree.QName(child).localname for child in run if child.tag not in _RUN_CHILDREN
    ]
    if unexpected:
        raise DocxError(
            f"Paragraph {paragraph_index} contains an unsupported ordinary run: "
            f"{unexpected}."
        )
    text_nodes = run.findall(f"{W}t")
    if len(text_nodes) != 1:
        raise DocxError(
            f"Paragraph {paragraph_index} ordinary runs must contain exactly one "
            "direct w:t node."
        )
    if _run_is_hidden(run):
        raise DocxError(f"Paragraph {paragraph_index} contains directly hidden text.")
    text = text_nodes[0].text or ""
    if "[[CIT:" in text or "[[FMT:" in text:
        raise DocxError(
            f"Paragraph {paragraph_index} prose contains reserved rewrite token syntax."
        )
    return text


def _range_elements(
    start: etree._Element,
    end: etree._Element,
    paragraph: etree._Element,
) -> tuple[int, int, list[etree._Element]]:
    start_run = _direct_run_child(start, paragraph)
    end_run = _direct_run_child(end, paragraph)
    children = list(paragraph)
    start_index = children.index(start_run)
    end_index = children.index(end_run)
    if end_index < start_index:
        raise DocxError("Citation field boundaries are reversed.")
    elements = children[start_index : end_index + 1]
    if any(element.tag != f"{W}r" for element in elements):
        raise DocxError(
            "Citation fields containing non-run paragraph children are unsupported."
        )
    return start_index, end_index, elements


def _citation_metadata(atom: _CitationAtom, source_text: str) -> dict[str, Any]:
    position = source_text.index(atom.token)
    return {
        "id": f"C{atom.citation_index}",
        "token": atom.token,
        "source_citation_index": atom.citation_index,
        "source_paragraph_index": atom.paragraph_index,
        "field_sha256": atom.field.field_sha256,
        "instruction_sha256": atom.field.instruction_sha256,
        "display_text": atom.field.display_text,
        "record_ids": atom.field.record_ids,
        "left_context": source_text[max(0, position - 120) : position],
        "right_context": source_text[
            position + len(atom.token) : position + len(atom.token) + 120
        ],
    }


def _format_metadata(atom: _FormatAtom) -> dict[str, Any]:
    return {
        "id": atom.token[6:-2],
        "token": atom.token,
        "source_paragraph_index": atom.paragraph_index,
        "text": atom.text,
        "run_sha256": atom.run_sha256,
    }


def _build_source_model(
    source_path: str | Path,
    paragraph_indices: list[int],
) -> _SourceModel:
    source = ensure_docx(source_path)
    selected = _validate_paragraph_indices(paragraph_indices)
    before = scan_docx(source)
    if before.errors or not before.all_fields_balanced:
        raise DocxError(
            "Source document failed preflight: XML scan errors or unbalanced fields."
        )
    if any(field.part != "word/document.xml" for field in before.citation_fields):
        raise DocxError(
            "Native rewrite currently refuses citation fields outside document.xml."
        )

    with zipfile.ZipFile(source, "r") as archive:
        document_xml = archive.read("word/document.xml")
    root = parse_xml(document_xml, "word/document.xml")
    paragraphs = _body_paragraphs(root)
    if selected[-1] > len(paragraphs):
        raise DocxError("Selected rewrite paragraph is out of range.")
    paragraph_map = {
        paragraph: index for index, paragraph in enumerate(paragraphs, start=1)
    }

    atomic_ranges = _atomic_endnote_ranges(root, document_xml)
    summary = scan_part("word/document.xml", document_xml)
    atomic_fields = [
        field
        for field in summary.endnote_fields
        if field.top_level
        and field.kind != "ADDIN EN.CITE.DATA"
        and not field.simple_field
    ]
    if len(atomic_ranges) != len(atomic_fields):
        raise DocxError("EndNote range and semantic field counts do not match.")

    selected_set = set(selected)
    citation_counter = 0
    citation_by_start: dict[tuple[etree._Element, int], tuple[int, _CitationAtom]] = {}
    citations: dict[str, _CitationAtom] = {}
    for field_range, field in zip(atomic_ranges, atomic_fields):
        if field.kind == "ADDIN EN.CITE":
            citation_counter += 1
        start_paragraph = field_range.start
        while start_paragraph is not None and start_paragraph.tag != f"{W}p":
            start_paragraph = start_paragraph.getparent()
        end_paragraph = field_range.end
        while end_paragraph is not None and end_paragraph.tag != f"{W}p":
            end_paragraph = end_paragraph.getparent()
        start_index = paragraph_map.get(start_paragraph)
        end_index = paragraph_map.get(end_paragraph)
        intersects = start_index in selected_set or end_index in selected_set
        if not intersects:
            continue
        if field.kind != "ADDIN EN.CITE":
            raise DocxError(
                f"Selected paragraphs intersect unsupported {field.kind} field."
            )
        if start_paragraph is None or start_paragraph is not end_paragraph:
            raise DocxError("Selected citation fields must stay within one paragraph.")
        if start_index is None or start_index not in selected_set:
            raise DocxError("A citation field crosses the selected paragraph boundary.")
        range_start, range_end, elements = _range_elements(
            field_range.start,
            field_range.end,
            start_paragraph,
        )
        token = f"[[CIT:C{citation_counter}]]"
        atom = _CitationAtom(
            token=token,
            citation_index=citation_counter,
            paragraph_index=start_index,
            field=field,
            elements=elements,
        )
        citation_by_start[(start_paragraph, range_start)] = (range_end, atom)
        citations[token] = atom

    paragraph_models: list[_ParagraphModel] = []
    formats: dict[str, _FormatAtom] = {}
    format_counter = 0
    for paragraph_index in selected:
        paragraph = paragraphs[paragraph_index - 1]
        items: list[tuple[str, Any]] = []
        ordinary_runs: list[tuple[etree._Element, str, str]] = []
        children = list(paragraph)
        child_index = 0
        while child_index < len(children):
            child = children[child_index]
            if child.tag == f"{W}pPr" or child.tag in _IGNORABLE_PARAGRAPH_CHILDREN:
                child_index += 1
                continue
            citation_match = citation_by_start.get((paragraph, child_index))
            if citation_match is not None:
                range_end, atom = citation_match
                items.append(("citation", atom))
                child_index = range_end + 1
                continue
            if child.tag != f"{W}r":
                raise DocxError(
                    f"Paragraph {paragraph_index} contains protected or unsupported "
                    f"{etree.QName(child).localname} content."
                )
            if child.xpath(
                ".//w:fldChar | .//w:instrText | .//w:fldData",
                namespaces={"w": W[1:-1]},
            ):
                raise DocxError(
                    f"Paragraph {paragraph_index} contains an unsupported Word field."
                )
            text = _ordinary_run_text(child, paragraph_index)
            property_hash = _run_property_hash(child)
            ordinary_runs.append((child, text, property_hash))
            items.append(("run", (child, text, property_hash)))
            child_index += 1

        if not ordinary_runs:
            raise DocxError(
                f"Paragraph {paragraph_index} has no ordinary prose run to use as "
                "the rewrite formatting baseline."
            )
        property_weights: Counter[str] = Counter()
        for _, text, property_hash in ordinary_runs:
            property_weights[property_hash] += max(1, len(text))
        base_hash = max(
            property_weights,
            key=lambda item: (property_weights[item], item),
        )
        base_run = next(run for run, _, item in ordinary_runs if item == base_hash)
        base_rpr = base_run.find(f"{W}rPr")

        chunks: list[str] = []
        for kind, item in items:
            if kind == "citation":
                chunks.append(item.token)
                continue
            run, text, property_hash = item
            if property_hash == base_hash:
                chunks.append(text)
                continue
            format_counter += 1
            token = f"[[FMT:F{format_counter}]]"
            atom = _FormatAtom(
                token=token,
                paragraph_index=paragraph_index,
                text=text,
                run_sha256=_run_hash(run),
                run=run,
            )
            formats[token] = atom
            chunks.append(token)

        source_text = "".join(chunks)
        paragraph_models.append(
            _ParagraphModel(
                paragraph_index=paragraph_index,
                paragraph=paragraph,
                source_text=source_text,
                base_run_attributes=dict(base_run.attrib),
                base_run_properties=deepcopy(base_rpr)
                if base_rpr is not None
                else None,
            )
        )

    found_tokens = {
        token
        for paragraph in paragraph_models
        for token in _CITATION_TOKEN.findall(paragraph.source_text)
    }
    if found_tokens != set(citations):
        raise DocxError("Not every selected citation field was mapped to one token.")
    if not citations:
        raise DocxError(
            "Native rewrite requires at least one EndNote citation in the selected "
            "paragraph range."
        )

    return _SourceModel(
        source=source,
        source_sha256=sha256_file(source),
        document_xml=document_xml,
        root=root,
        scan=before,
        paragraph_indices=selected,
        paragraphs=paragraph_models,
        citations=citations,
        formats=formats,
    )


def _view_from_model(model: _SourceModel) -> dict[str, Any]:
    source_text_by_index = {
        paragraph.paragraph_index: paragraph.source_text
        for paragraph in model.paragraphs
    }
    citations = sorted(model.citations.values(), key=lambda atom: atom.citation_index)
    formats = sorted(model.formats.values(), key=lambda atom: int(atom.token[7:-2]))
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "mode": VIEW_MODE,
        "source": str(model.source),
        "source_sha256": model.source_sha256,
        "paragraph_indices": model.paragraph_indices,
        "rules": [
            "Edit only paragraphs[].text.",
            "Keep every CIT and FMT token exactly once.",
            "CIT tokens are complete opaque EndNote fields.",
            "FMT tokens are complete opaque directly formatted runs.",
            "Do not add, delete, rename, split, or nest tokens.",
        ],
        "paragraphs": [
            {
                "paragraph_index": paragraph.paragraph_index,
                "source_text": paragraph.source_text,
                "text": paragraph.source_text,
            }
            for paragraph in model.paragraphs
        ],
        "citations": [
            _citation_metadata(
                atom,
                source_text_by_index[atom.paragraph_index],
            )
            for atom in citations
        ],
        "format_atoms": [_format_metadata(atom) for atom in formats],
    }


def export_rewrite_view(
    source_path: str | Path,
    *,
    paragraph_indices: list[int],
) -> dict[str, Any]:
    return _view_from_model(_build_source_model(source_path, paragraph_indices))


def load_rewrite_view(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise DocxError("Rewrite view must be a JSON object.")
    return payload


def _validate_edited_view(
    model: _SourceModel,
    view: dict[str, Any],
) -> list[tuple[int, str]]:
    frozen = _view_from_model(model)
    if (
        view.get("schema_version") != VIEW_SCHEMA_VERSION
        or view.get("mode") != VIEW_MODE
    ):
        raise DocxError("Rewrite view schema or mode is unsupported.")
    if view.get("source_sha256") != model.source_sha256:
        raise DocxError("Rewrite view source SHA-256 does not match the input DOCX.")
    if view.get("paragraph_indices") != model.paragraph_indices:
        raise DocxError("Rewrite view paragraph range changed after export.")
    if view.get("citations") != frozen["citations"]:
        raise DocxError("Rewrite view citation metadata changed after export.")
    if view.get("format_atoms") != frozen["format_atoms"]:
        raise DocxError("Rewrite view format metadata changed after export.")

    paragraphs = view.get("paragraphs")
    if not isinstance(paragraphs, list) or len(paragraphs) != len(model.paragraphs):
        raise DocxError("Rewrite view paragraph list changed after export.")
    edited: list[tuple[int, str]] = []
    for expected, item in zip(frozen["paragraphs"], paragraphs):
        if not isinstance(item, dict):
            raise DocxError("Every rewrite paragraph must be an object.")
        if item.get("paragraph_index") != expected["paragraph_index"]:
            raise DocxError("Rewrite paragraph identity changed after export.")
        if item.get("source_text") != expected["source_text"]:
            raise DocxError("Rewrite paragraph source_text changed after export.")
        text = item.get("text")
        if not isinstance(text, str):
            raise DocxError("Every rewrite paragraph requires string text.")
        if "\n" in text or "\r" in text:
            raise DocxError("Rewrite paragraph text must not contain line breaks.")
        edited.append((expected["paragraph_index"], text))

    combined = "\n".join(text for _, text in edited)
    known_tokens = set(model.citations) | set(model.formats)
    unknown = sorted(set(_ANY_RESERVED_TOKEN.findall(combined)) - known_tokens)
    if unknown:
        raise DocxError(f"Rewrite view contains unknown reserved tokens: {unknown}.")
    if "[[CIT:" in _ANY_RESERVED_TOKEN.sub(
        "", combined
    ) or "[[FMT:" in _ANY_RESERVED_TOKEN.sub("", combined):
        raise DocxError("Rewrite view contains malformed reserved token syntax.")
    for token in sorted(known_tokens):
        count = combined.count(token)
        if count != 1:
            raise DocxError(
                f"Rewrite token {token} must occur exactly once; found {count}."
            )
    return edited


def _plain_run(paragraph: _ParagraphModel, text: str) -> etree._Element:
    run = etree.Element(f"{W}r", attrib=paragraph.base_run_attributes)
    if paragraph.base_run_properties is not None:
        run.append(deepcopy(paragraph.base_run_properties))
    node = etree.SubElement(run, f"{W}t")
    try:
        node.text = text
    except ValueError as exc:
        raise DocxError(
            "Rewrite text contains characters that XML cannot store."
        ) from exc
    if text.startswith(" ") or text.endswith(" "):
        node.set(XML_SPACE, "preserve")
    return run


def _token_pattern(tokens: set[str]) -> re.Pattern[str]:
    return re.compile(
        "(" + "|".join(re.escape(token) for token in sorted(tokens)) + ")"
    )


def _identity_view(
    text: str,
    citations: dict[str, _CitationAtom],
    formats: dict[str, _FormatAtom],
) -> str:
    replacements = {
        token: f"<CITE sha256={atom.field.field_sha256}>"
        for token, atom in citations.items()
    }
    replacements.update(
        {
            token: (f"<FORMAT sha256={atom.run_sha256}>" f"{atom.text}</FORMAT>")
            for token, atom in formats.items()
        }
    )
    for token in sorted(replacements, key=len, reverse=True):
        text = text.replace(token, replacements[token])
    return text


def _expected_citation_order(
    model: _SourceModel,
    edited: list[tuple[int, str]],
) -> list[str]:
    selected = set(model.paragraph_indices)
    before = [
        field.field_sha256
        for field in model.scan.citation_fields
        if field.paragraph_index is not None
        and field.paragraph_index < model.paragraph_indices[0]
    ]
    after = [
        field.field_sha256
        for field in model.scan.citation_fields
        if field.paragraph_index is not None
        and field.paragraph_index > model.paragraph_indices[-1]
    ]
    covered = sum(
        1 for field in model.scan.citation_fields if field.paragraph_index in selected
    )
    if covered != len(model.citations):
        raise DocxError("Selected citation accounting changed before rewrite.")
    token_order: list[str] = []
    for _, text in edited:
        token_order.extend(_CITATION_TOKEN.findall(text))
    moved = [model.citations[token].field.field_sha256 for token in token_order]
    return before + moved + after


def apply_rewrite_view(
    source_path: str | Path,
    output_path: str | Path,
    view: dict[str, Any],
    *,
    overwrite_output: bool = False,
    keep_failed_output: bool = False,
) -> dict[str, Any]:
    paragraph_indices = view.get("paragraph_indices")
    if not isinstance(paragraph_indices, list):
        raise DocxError("Rewrite view requires paragraph_indices.")
    model = _build_source_model(source_path, paragraph_indices)
    edited = _validate_edited_view(model, view)
    output = Path(output_path).expanduser().resolve()
    tokens = set(model.citations) | set(model.formats)
    pattern = _token_pattern(tokens)
    paragraph_by_index = {
        paragraph.paragraph_index: paragraph for paragraph in model.paragraphs
    }

    for paragraph_index, text in edited:
        paragraph_model = paragraph_by_index[paragraph_index]
        paragraph = paragraph_model.paragraph
        ppr = paragraph.find(f"{W}pPr")
        for child in list(paragraph):
            if child is not ppr:
                paragraph.remove(child)
        for piece in pattern.split(text):
            if not piece:
                continue
            citation = model.citations.get(piece)
            if citation is not None:
                paragraph.extend(deepcopy(citation.elements))
                continue
            formatted = model.formats.get(piece)
            if formatted is not None:
                paragraph.append(deepcopy(formatted.run))
                continue
            paragraph.append(_plain_run(paragraph_model, piece))

    replacement = serialize_xml(model.root, model.document_xml)
    write_docx_with_replacements(
        model.source,
        output,
        {"word/document.xml": replacement},
        overwrite_output=overwrite_output,
    )

    expected_order = _expected_citation_order(model, edited)
    after = scan_docx(output)
    verification = compare_reports(
        model.scan,
        after,
        allow_field_reordering=True,
        expected_citation_order=expected_order,
    )
    surface = check_docx_change_surface(
        model.source,
        output,
        editable_paragraphs=model.paragraph_indices,
    )
    expected_identity = [
        {
            "paragraph_index": paragraph_index,
            "text": _identity_view(text, model.citations, model.formats),
        }
        for paragraph_index, text in edited
    ]
    output_model = _build_source_model(output, model.paragraph_indices)
    actual_identity = [
        {
            "paragraph_index": paragraph.paragraph_index,
            "text": _identity_view(
                paragraph.source_text,
                output_model.citations,
                output_model.formats,
            ),
        }
        for paragraph in output_model.paragraphs
    ]
    identity_matches = expected_identity == actual_identity

    visible = extract_visible_introduction(
        output,
        paragraph_indices=model.paragraph_indices,
    )

    failures: list[str] = []
    if verification["status"] != "pass":
        failures.append("EndNote and formatting invariants failed.")
    if surface["status"] != "pass":
        failures.append("DOCX package change surface failed.")
    if not identity_matches:
        failures.append(
            "Reparsed paragraph atom positions do not match the rewrite view."
        )

    result = {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "source": str(model.source),
        "source_sha256": model.source_sha256,
        "output": str(output),
        "output_sha256": sha256_file(output),
        "paragraph_indices": model.paragraph_indices,
        "word_count": visible["word_count"],
        "expected_citation_order": expected_order,
        "expected_identity_view": expected_identity,
        "actual_identity_view": actual_identity,
        "checks": {
            "field_and_format_verification_passed": verification["status"] == "pass",
            "package_change_surface_passed": surface["status"] == "pass",
            "semantic_paragraph_views_match": identity_matches,
        },
        "verification": verification,
        "change_surface": surface,
        "failures": failures,
    }
    if failures and not keep_failed_output:
        output.unlink(missing_ok=True)
        result["output_deleted"] = True
    else:
        result["output_deleted"] = False
    return result

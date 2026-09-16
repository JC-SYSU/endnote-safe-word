from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from .constants import PATCHABLE_PREFIXES, XML_SPACE, W
from .ooxml import (
    DocxError,
    ancestor_has_tag,
    ensure_docx,
    parse_xml,
    serialize_xml,
    write_docx_with_replacements,
)
from .scanner import scan_docx
from .verifier import compare_reports


@dataclass(slots=True)
class PatchSpec:
    find: str
    replace: str
    part: str = "word/document.xml"
    occurrence: int | None = None
    expected_count: int | None = 1
    context_contains: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatchSpec:
        if not isinstance(data.get("find"), str) or not data["find"]:
            raise DocxError("Each patch requires a non-empty string 'find'.")
        if not isinstance(data.get("replace"), str):
            raise DocxError("Each patch requires a string 'replace'.")
        occurrence = data.get("occurrence")
        expected_count = data.get("expected_count", 1)
        if occurrence is not None and (not isinstance(occurrence, int) or occurrence < 1):
            raise DocxError("'occurrence' must be a positive integer.")
        if expected_count is not None and (
            not isinstance(expected_count, int) or expected_count < 0
        ):
            raise DocxError("'expected_count' must be a non-negative integer or null.")
        return cls(
            find=data["find"],
            replace=data["replace"],
            part=data.get("part", "word/document.xml"),
            occurrence=occurrence,
            expected_count=expected_count,
            context_contains=data.get("context_contains"),
        )


def load_patch_specs(path: str | Path) -> list[PatchSpec]:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if isinstance(payload, dict):
        payload = payload.get("patches")
    if not isinstance(payload, list) or not payload:
        raise DocxError("Patch JSON must be a non-empty list or {'patches': [...]}.")
    return [PatchSpec.from_dict(item) for item in payload]


def _is_patchable_part(part: str) -> bool:
    return any(part == prefix or part.startswith(prefix) for prefix in PATCHABLE_PREFIXES)


def _paragraph_text(node: etree._Element) -> str:
    parent = node
    while parent is not None and parent.tag != f"{W}p":
        parent = parent.getparent()
    if parent is None:
        return ""
    return "".join(t.text or "" for t in parent.iter(f"{W}t"))


def _field_depth_by_text_node(
    root: etree._Element,
) -> dict[etree._Element, int]:
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


def _unsafe_reason(node: etree._Element, field_depth: int) -> str | None:
    if field_depth > 0:
        return "text is inside a complex Word field"
    if ancestor_has_tag(node, f"{W}fldSimple"):
        return "text is inside a simple Word field"
    if ancestor_has_tag(node, f"{W}ins") or ancestor_has_tag(node, f"{W}del"):
        return "text is inside tracked changes"
    if ancestor_has_tag(node, f"{W}moveFrom") or ancestor_has_tag(node, f"{W}moveTo"):
        return "text is inside a tracked move"
    if ancestor_has_tag(node, f"{W}sdt"):
        return "text is inside a content control"
    return None


def _apply_one(root: etree._Element, patch: PatchSpec) -> dict[str, Any]:
    depths = _field_depth_by_text_node(root)
    candidates: list[etree._Element] = []
    unsafe_matches: list[str] = []

    for node in root.iter(f"{W}t"):
        text = node.text or ""
        if patch.find not in text:
            continue
        if patch.context_contains and patch.context_contains not in _paragraph_text(node):
            continue
        reason = _unsafe_reason(node, depths.get(node, 0))
        if reason:
            unsafe_matches.append(reason)
            continue
        candidates.append(node)

    if unsafe_matches:
        raise DocxError(
            f"Refusing patch {patch.find!r}: at least one match is unsafe "
            f"({'; '.join(sorted(set(unsafe_matches)))})."
        )

    # Count literal occurrences, not just matching nodes.
    literal_count = sum((node.text or "").count(patch.find) for node in candidates)
    if patch.expected_count is not None and literal_count != patch.expected_count:
        raise DocxError(
            f"Patch {patch.find!r} expected {patch.expected_count} safe occurrence(s), "
            f"found {literal_count}. This alpha does not patch across runs."
        )

    targets: list[tuple[etree._Element, int]] = []
    running = 0
    for node in candidates:
        text = node.text or ""
        start = 0
        while True:
            position = text.find(patch.find, start)
            if position < 0:
                break
            running += 1
            if patch.occurrence is None or running == patch.occurrence:
                targets.append((node, position))
            start = position + len(patch.find)

    if patch.occurrence is not None and not targets:
        raise DocxError(
            f"Patch {patch.find!r} requested occurrence {patch.occurrence}, "
            f"but only {literal_count} safe occurrence(s) exist."
        )

    # Multiple replacements in a single node are applied using str.replace only when
    # occurrence is unspecified. For a selected occurrence, replace exactly one.
    changed_nodes = 0
    if patch.occurrence is None:
        for node in candidates:
            original = node.text or ""
            updated = original.replace(patch.find, patch.replace)
            if updated != original:
                node.text = updated
                changed_nodes += 1
                if updated.startswith(" ") or updated.endswith(" "):
                    node.set(XML_SPACE, "preserve")
    else:
        node, position = targets[0]
        original = node.text or ""
        node.text = (
            original[:position]
            + patch.replace
            + original[position + len(patch.find) :]
        )
        changed_nodes = 1
        if (node.text or "").startswith(" ") or (node.text or "").endswith(" "):
            node.set(XML_SPACE, "preserve")

    return {
        "find": patch.find,
        "replace": patch.replace,
        "part": patch.part,
        "safe_occurrences": literal_count,
        "changed_nodes": changed_nodes,
        "occurrence": patch.occurrence,
    }


def patch_docx(
    source_path: str | Path,
    output_path: str | Path,
    patch_specs: list[PatchSpec],
    *,
    overwrite_output: bool = False,
    keep_failed_output: bool = False,
    strict_format_counts: bool = True,
) -> dict[str, Any]:
    source = ensure_docx(source_path)
    output = Path(output_path).expanduser().resolve()
    before = scan_docx(source)
    if before.errors or not before.all_fields_balanced:
        raise DocxError(
            "Source document failed preflight: XML scan errors or unbalanced fields."
        )

    grouped: dict[str, list[PatchSpec]] = {}
    for spec in patch_specs:
        if not _is_patchable_part(spec.part):
            raise DocxError(f"Part is not patchable in alpha: {spec.part}")
        grouped.setdefault(spec.part, []).append(spec)

    replacements: dict[str, bytes] = {}
    operations: list[dict[str, Any]] = []
    with zipfile.ZipFile(source, "r") as zf:
        names = set(zf.namelist())
        for part, specs in grouped.items():
            if part not in names:
                raise DocxError(f"DOCX part not found: {part}")
            original = zf.read(part)
            root = parse_xml(original, part)
            for spec in specs:
                operations.append(_apply_one(root, spec))
            replacements[part] = serialize_xml(root, original)

    write_docx_with_replacements(
        source,
        output,
        replacements,
        overwrite_output=overwrite_output,
    )

    after = scan_docx(output)
    verification = compare_reports(
        before, after, strict_format_counts=strict_format_counts
    )
    result = {
        "schema_version": 1,
        "status": verification["status"],
        "source": str(source),
        "output": str(output),
        "operations": operations,
        "verification": verification,
    }
    if verification["status"] != "pass" and not keep_failed_output:
        output.unlink(missing_ok=True)
        result["output_deleted"] = True
    else:
        result["output_deleted"] = False
    return result

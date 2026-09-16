from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lxml import etree

from .change_surface import check_docx_change_surface
from .constants import W
from .ooxml import (
    DocxError,
    ancestor_has_tag,
    ensure_docx,
    parse_xml,
    sha256_file,
)
from .scanner import scan_docx
from .verifier import compare_reports


OFFICECLI_VERSION = "1.0.136"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"

_RUN_PATH = re.compile(
    r"^/body/p(?:\[(?P<index>[1-9][0-9]*)\]|"
    r"\[@paraId=(?P<para_id>[0-9A-Fa-f]{8})\])/r\[(?P<run>[1-9][0-9]*)\]$"
)
_PARAGRAPH_PATH = re.compile(
    r"^/body/p(?:\[(?P<index>[1-9][0-9]*)\]|"
    r"\[@paraId=(?P<para_id>[0-9A-Fa-f]{8})\])$"
)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORMAT_PROPERTIES = {"italic", "subscript", "superscript"}
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
_VISIBLE_SPECIALS = {
    f"{W}tab",
    f"{W}br",
    f"{W}cr",
    f"{W}noBreakHyphen",
    f"{W}softHyphen",
}


@dataclass(slots=True)
class SetRunTextOperation:
    path: str
    expected_text: str
    text: str
    op: str = "set_run_text"


@dataclass(slots=True)
class FormatRangeOperation:
    path: str
    start: int
    end: int
    expected_text: str
    properties: dict[str, bool]
    op: str = "format_range"


OfficeCliOperation = SetRunTextOperation | FormatRangeOperation


@dataclass(slots=True)
class OfficeCliGuardPlan:
    expected_source_sha256: str
    officecli_version: str
    allowed_paragraphs: list[int]
    operations: list[OfficeCliOperation]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OfficeCliGuardPlan":
        if not isinstance(data, dict):
            raise DocxError("OfficeCLI guard plan must be a JSON object.")
        expected = data.get("expected_source_sha256")
        version = data.get("officecli_version")
        paragraphs = data.get("allowed_paragraphs")
        raw_operations = data.get("operations")
        if not isinstance(expected, str) or not _HASH.fullmatch(expected):
            raise DocxError("'expected_source_sha256' must be a lowercase SHA-256 hash.")
        if version != OFFICECLI_VERSION:
            raise DocxError(
                f"Guard plan must pin OfficeCLI {OFFICECLI_VERSION}; received {version!r}."
            )
        if (
            not isinstance(paragraphs, list)
            or not paragraphs
            or not all(isinstance(item, int) and item > 0 for item in paragraphs)
            or len(set(paragraphs)) != len(paragraphs)
        ):
            raise DocxError("'allowed_paragraphs' must contain unique positive integers.")
        if not isinstance(raw_operations, list) or not raw_operations:
            raise DocxError("'operations' must be a non-empty list.")

        operations: list[OfficeCliOperation] = []
        seen_format = False
        seen_run_paths: set[str] = set()
        ranges: dict[str, list[tuple[int, int]]] = {}
        for raw in raw_operations:
            if not isinstance(raw, dict):
                raise DocxError("Each OfficeCLI operation must be a JSON object.")
            op = raw.get("op")
            if op == "set_run_text":
                if seen_format:
                    raise DocxError("All set_run_text operations must precede format_range.")
                if set(raw) != {"op", "path", "expected_text", "text"}:
                    raise DocxError("set_run_text contains missing or unsupported keys.")
                if not all(
                    isinstance(raw.get(key), str)
                    for key in ("path", "expected_text", "text")
                ):
                    raise DocxError("set_run_text path and text values must be strings.")
                if raw["path"] in seen_run_paths:
                    raise DocxError("A run path may be edited only once per guard plan.")
                if any(character in raw["text"] for character in "\r\n\t"):
                    raise DocxError("set_run_text cannot insert paragraph or tab controls.")
                seen_run_paths.add(raw["path"])
                operations.append(
                    SetRunTextOperation(
                        path=raw["path"],
                        expected_text=raw["expected_text"],
                        text=raw["text"],
                    )
                )
            elif op == "format_range":
                seen_format = True
                if set(raw) != {
                    "op",
                    "path",
                    "start",
                    "end",
                    "expected_text",
                    "properties",
                }:
                    raise DocxError("format_range contains missing or unsupported keys.")
                if not isinstance(raw.get("path"), str) or not isinstance(
                    raw.get("expected_text"), str
                ):
                    raise DocxError("format_range path and expected_text must be strings.")
                start = raw.get("start")
                end = raw.get("end")
                if not isinstance(start, int) or not isinstance(end, int) or start < 0:
                    raise DocxError("format_range offsets must be non-negative integers.")
                if end <= start:
                    raise DocxError("format_range end must be greater than start.")
                properties = raw.get("properties")
                if (
                    not isinstance(properties, dict)
                    or len(properties) != 1
                    or not set(properties).issubset(_FORMAT_PROPERTIES)
                    or next(iter(properties.values()), None) is not True
                ):
                    raise DocxError(
                        "format_range permits exactly one true property: "
                        "italic, subscript, or superscript."
                    )
                for left, right in ranges.setdefault(raw["path"], []):
                    if start < right and left < end:
                        raise DocxError("format_range operations must not overlap.")
                ranges[raw["path"]].append((start, end))
                operations.append(
                    FormatRangeOperation(
                        path=raw["path"],
                        start=start,
                        end=end,
                        expected_text=raw["expected_text"],
                        properties=dict(properties),
                    )
                )
            else:
                raise DocxError(
                    f"Unsupported OfficeCLI operation {op!r}; only set_run_text and "
                    "format_range are allowed."
                )

        return cls(
            expected_source_sha256=expected,
            officecli_version=version,
            allowed_paragraphs=list(paragraphs),
            operations=operations,
        )


def load_officecli_guard_plan(path: str | Path) -> OfficeCliGuardPlan:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return OfficeCliGuardPlan.from_dict(payload)


def _document_root(path: Path) -> etree._Element:
    with zipfile.ZipFile(path, "r") as zf:
        return parse_xml(zf.read("word/document.xml"), "word/document.xml")


def _body_paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise DocxError("word/document.xml has no body.")
    return [child for child in body if child.tag == f"{W}p"]


def _resolve_paragraph(
    root: etree._Element,
    path: str,
) -> tuple[etree._Element, int]:
    match = _PARAGRAPH_PATH.fullmatch(path)
    if match is None:
        raise DocxError(
            "Only one explicit /body/p[N] or /body/p[@paraId=XXXXXXXX] "
            "paragraph path is allowed."
        )
    paragraphs = _body_paragraphs(root)
    if match.group("index"):
        index = int(match.group("index"))
        if index > len(paragraphs):
            raise DocxError(f"Paragraph path is out of range: {path}")
        return paragraphs[index - 1], index

    para_id = match.group("para_id").upper()
    matches = [
        (paragraph, index)
        for index, paragraph in enumerate(paragraphs, start=1)
        if paragraph.get(f"{W14}paraId", "").upper() == para_id
    ]
    if len(matches) != 1:
        raise DocxError(f"Paragraph ID path must resolve exactly once: {path}")
    return matches[0]


def _resolve_run(
    root: etree._Element,
    path: str,
) -> tuple[etree._Element, etree._Element, int]:
    match = _RUN_PATH.fullmatch(path)
    if match is None:
        raise DocxError(
            "Only an explicit direct run path /body/p[...]/r[N] is allowed for text."
        )
    paragraph_path = path[: path.rfind("/r[")]
    paragraph, paragraph_index = _resolve_paragraph(root, paragraph_path)
    runs = [child for child in paragraph if child.tag == f"{W}r"]
    run_index = int(match.group("run"))
    if run_index > len(runs):
        raise DocxError(f"Run path is out of range: {path}")
    return runs[run_index - 1], paragraph, paragraph_index


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


def _unsafe_text_reason(node: etree._Element, field_depth: int) -> str | None:
    if field_depth:
        return "target run is inside a complex Word field"
    if ancestor_has_tag(node, f"{W}fldSimple"):
        return "target run is inside a simple Word field"
    if ancestor_has_tag(node, f"{W}ins") or ancestor_has_tag(node, f"{W}del"):
        return "target run is inside tracked changes"
    if ancestor_has_tag(node, f"{W}moveFrom") or ancestor_has_tag(node, f"{W}moveTo"):
        return "target run is inside a tracked move"
    if ancestor_has_tag(node, f"{W}sdt"):
        return "target run is inside a content control"
    return None


def _validate_set_run_text(
    root: etree._Element,
    operation: SetRunTextOperation,
    allowed_paragraphs: set[int],
) -> dict[str, Any]:
    run, _, paragraph_index = _resolve_run(root, operation.path)
    if paragraph_index not in allowed_paragraphs:
        raise DocxError(
            f"Run target paragraph {paragraph_index} is outside allowed_paragraphs."
        )
    allowed_children = {f"{W}rPr", f"{W}t"}
    nodes = run.findall(f"{W}t")
    if len(nodes) != 1 or any(child.tag not in allowed_children for child in run):
        raise DocxError("Target run must contain exactly one ordinary w:t text node.")
    node = nodes[0]
    reason = _unsafe_text_reason(node, _field_depth_by_text(root).get(node, 0))
    if reason:
        raise DocxError(reason)
    rpr = run.find(f"{W}rPr")
    if rpr is not None and any(
        etree.QName(child).localname in _SIGNIFICANT_RUN_PROPERTIES for child in rpr
    ):
        raise DocxError("Target run carries protected direct formatting.")
    actual = node.text or ""
    if actual != operation.expected_text:
        raise DocxError(
            f"Run text does not match frozen expectation at {operation.path}: "
            f"expected={operation.expected_text!r}; actual={actual!r}."
        )
    return {
        "op": operation.op,
        "path": operation.path,
        "paragraph_index": paragraph_index,
        "expected_text": operation.expected_text,
        "text": operation.text,
    }


def _visible_segments(
    paragraph: etree._Element,
) -> tuple[str, list[tuple[int, int, etree._Element, int]]]:
    stack: list[bool] = []
    text_chunks: list[str] = []
    segments: list[tuple[int, int, etree._Element, int]] = []
    cursor = 0
    for element in paragraph.iter():
        if element.tag == f"{W}fldChar":
            field_type = element.get(f"{W}fldCharType", "")
            if field_type == "begin":
                stack.append(False)
            elif field_type == "separate" and stack:
                stack[-1] = True
            elif field_type == "end" and stack:
                stack.pop()
        elif element.tag in _VISIBLE_SPECIALS:
            if not stack or all(stack):
                raise DocxError(
                    "Range formatting is refused in paragraphs containing visible "
                    "tabs, breaks, or special text tokens."
                )
        elif element.tag == f"{W}t" and (not stack or all(stack)):
            text = element.text or ""
            if text:
                start = cursor
                cursor += len(text)
                text_chunks.append(text)
                segments.append((start, cursor, element, len(stack)))
    return "".join(text_chunks), segments


def _validate_format_range(
    root: etree._Element,
    operation: FormatRangeOperation,
    allowed_paragraphs: set[int],
) -> dict[str, Any]:
    paragraph, paragraph_index = _resolve_paragraph(root, operation.path)
    if paragraph_index not in allowed_paragraphs:
        raise DocxError(
            f"Range target paragraph {paragraph_index} is outside allowed_paragraphs."
        )
    visible_text, segments = _visible_segments(paragraph)
    if operation.end > len(visible_text):
        raise DocxError("format_range extends beyond visible paragraph text.")
    actual = visible_text[operation.start : operation.end]
    if actual != operation.expected_text:
        raise DocxError(
            f"Range text does not match frozen expectation at {operation.path}: "
            f"expected={operation.expected_text!r}; actual={actual!r}."
        )
    covered = [
        segment
        for segment in segments
        if operation.start < segment[1] and segment[0] < operation.end
    ]
    if not covered:
        raise DocxError("format_range does not cover an ordinary text node.")
    for _, _, node, field_depth in covered:
        reason = _unsafe_text_reason(node, field_depth)
        if reason:
            raise DocxError(reason.replace("target run", "format_range"))
    return {
        "op": operation.op,
        "path": operation.path,
        "paragraph_index": paragraph_index,
        "start": operation.start,
        "end": operation.end,
        "expected_text": operation.expected_text,
        "properties": operation.properties,
    }


def validate_officecli_operations(
    path: str | Path,
    plan: OfficeCliGuardPlan,
    *,
    operation_type: str,
) -> list[dict[str, Any]]:
    document = ensure_docx(path)
    root = _document_root(document)
    allowed = set(plan.allowed_paragraphs)
    validated: list[dict[str, Any]] = []
    for operation in plan.operations:
        if operation.op != operation_type:
            continue
        if isinstance(operation, SetRunTextOperation):
            validated.append(_validate_set_run_text(root, operation, allowed))
        elif isinstance(operation, FormatRangeOperation):
            validated.append(_validate_format_range(root, operation, allowed))
    return validated


def _run_officecli(
    executable: str,
    args: list[str],
    *,
    check: bool = True,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["OFFICECLI_RESIDENT_FLUSH"] = "each"
    completed = subprocess.run(
        [executable, *args, "--json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    payload: Any = None
    if completed.stdout.strip():
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = None
    success = (
        completed.returncode == 0
        and isinstance(payload, dict)
        and payload.get("success") is True
    )
    result = {
        "args": args,
        "returncode": completed.returncode,
        "success": success,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }
    if check and not success:
        raise DocxError(
            f"OfficeCLI command failed: {args!r}; returncode={completed.returncode}; "
            f"stderr={completed.stderr.strip()!r}; stdout={completed.stdout.strip()!r}"
        )
    return result


def _execute_phase(
    executable: str,
    document: Path,
    operations: list[OfficeCliOperation],
) -> list[dict[str, Any]]:
    if not operations:
        return []
    logs: list[dict[str, Any]] = []
    opened = False
    try:
        logs.append(_run_officecli(executable, ["open", str(document)]))
        opened = True
        for operation in operations:
            if isinstance(operation, SetRunTextOperation):
                args = [
                    "set",
                    str(document),
                    operation.path,
                    "--prop",
                    f"text={operation.text}",
                ]
            else:
                property_name, property_value = next(
                    iter(operation.properties.items())
                )
                args = [
                    "set",
                    str(document),
                    operation.path,
                    "--prop",
                    f"range={operation.start}:{operation.end}",
                    "--prop",
                    f"{property_name}={str(property_value).lower()}",
                ]
            logs.append(_run_officecli(executable, args))
    finally:
        if opened:
            logs.append(
                _run_officecli(
                    executable,
                    ["close", str(document)],
                    check=False,
                )
            )
    return logs


def run_officecli_guarded(
    source_path: str | Path,
    output_path: str | Path,
    plan: OfficeCliGuardPlan,
    *,
    executable: str = "officecli",
    overwrite_output: bool = False,
    keep_failed_output: bool = False,
    strict_format_counts: bool = True,
) -> dict[str, Any]:
    source = ensure_docx(source_path)
    output = Path(output_path).expanduser().resolve()
    if source == output:
        raise DocxError("Refusing to overwrite the OfficeCLI guard input.")
    if output.exists() and not overwrite_output:
        raise DocxError(f"Output already exists: {output}")
    source_sha256 = sha256_file(source)
    if source_sha256 != plan.expected_source_sha256:
        raise DocxError("Source hash does not match the frozen OfficeCLI guard plan.")

    before = scan_docx(source)
    if before.errors or not before.all_fields_balanced:
        raise DocxError(
            "Source document failed preflight: XML scan errors or unbalanced fields."
        )
    version = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    actual_version = version.stdout.strip()
    if version.returncode != 0 or actual_version != plan.officecli_version:
        raise DocxError(
            f"OfficeCLI version mismatch: expected {plan.officecli_version}, "
            f"received {actual_version or version.stderr.strip()!r}."
        )

    text_operations = [
        operation
        for operation in plan.operations
        if isinstance(operation, SetRunTextOperation)
    ]
    format_operations = [
        operation
        for operation in plan.operations
        if isinstance(operation, FormatRangeOperation)
    ]
    validate_officecli_operations(source, plan, operation_type="set_run_text")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)
    logs: list[dict[str, Any]] = []
    try:
        logs.extend(_execute_phase(executable, output, text_operations))
        validate_officecli_operations(output, plan, operation_type="format_range")
        logs.extend(_execute_phase(executable, output, format_operations))
        logs.append(_run_officecli(executable, ["validate", str(output)]))
    except Exception:
        if not keep_failed_output:
            output.unlink(missing_ok=True)
        raise

    after = scan_docx(output)
    verification = compare_reports(
        before,
        after,
        strict_format_counts=strict_format_counts,
    )
    change_surface = check_docx_change_surface(
        source,
        output,
        editable_paragraphs=plan.allowed_paragraphs,
        officecli_version=actual_version,
        allow_new_run_properties=not strict_format_counts,
    )
    if change_surface["status"] != "pass":
        verification["failures"].extend(
            f"Package change surface: {failure}"
            for failure in change_surface["failures"]
        )
        verification["status"] = "fail"
    source_unchanged = sha256_file(source) == source_sha256
    if not source_unchanged:
        verification["failures"].append("Source file changed during OfficeCLI run.")
        verification["status"] = "fail"
    result = {
        "schema_version": 1,
        "status": verification["status"],
        "source": str(source),
        "source_sha256": source_sha256,
        "source_unchanged": source_unchanged,
        "output": str(output),
        "output_sha256": sha256_file(output),
        "officecli_version": actual_version,
        "resident_flush": "each",
        "allowed_paragraphs": plan.allowed_paragraphs,
        "operations": [asdict(operation) for operation in plan.operations],
        "officecli_log": logs,
        "verification": verification,
        "change_surface": change_surface,
    }
    if verification["status"] != "pass" and not keep_failed_output:
        output.unlink(missing_ok=True)
        result["output_deleted"] = True
    else:
        result["output_deleted"] = False
    return result

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zipfile
from collections import Counter
from copy import deepcopy
from importlib.resources import files
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


_CONTENT_TYPES_NS = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CUSTOM_NS = (
    "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
)
_VT_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}"
_CUSTOM_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_CUSTOM_REL_ID = re.compile(r"^R[0-9a-fA-F]{16}$")
_MC_IGNORABLE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"
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


def _load_policy(version: str) -> dict[str, Any]:
    resource = files("endnote_safe_word").joinpath(
        "policies", f"officecli-{version}.json"
    )
    if not resource.is_file():
        raise DocxError(f"No package change-surface policy for OfficeCLI {version}.")
    policy = json.loads(resource.read_text(encoding="utf-8"))
    if policy.get("version") != version or policy.get("tool") != "officecli":
        raise DocxError("OfficeCLI package policy identity is invalid.")
    return policy


def _part_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _semantic_xml_hash(data: bytes, part: str) -> str:
    root = parse_xml(data, part)
    return token_hash([element_token(element) for element in root.iter()])


def _entry_set(root: etree._Element) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    return {
        (
            etree.QName(child).localname,
            tuple(sorted((str(key), str(value)) for key, value in child.attrib.items())),
        )
        for child in root
    }


def _validate_content_types(
    before_data: bytes,
    after_data: bytes,
    policy: dict[str, Any],
) -> tuple[bool, str]:
    before = parse_xml(before_data, "[Content_Types].xml")
    after = parse_xml(after_data, "[Content_Types].xml")
    before_entries = _entry_set(before)
    after_entries = _entry_set(after)
    if len(before) != len(before_entries) or len(after) != len(after_entries):
        return False, "content types contain duplicate entries"
    expected = (
        "Override",
        tuple(
            sorted(
                {
                    "PartName": "/docProps/custom.xml",
                    "ContentType": policy["custom_properties_content_type"],
                }.items()
            )
        ),
    )
    if expected in before_entries:
        return (
            before_entries == after_entries,
            "content types must remain semantically identical once custom.xml exists",
        )
    return (
        after_entries == before_entries | {expected},
        "content types may add only the exact custom.xml override",
    )


def _relationship_records(data: bytes) -> list[dict[str, str]]:
    root = parse_xml(data, "_rels/.rels")
    return [
        {
            "Id": child.get("Id", ""),
            "Type": child.get("Type", ""),
            "Target": child.get("Target", ""),
            "TargetMode": child.get("TargetMode", ""),
        }
        for child in root
        if child.tag == f"{_REL_NS}Relationship"
    ]


def _validate_root_relationships(
    before_data: bytes,
    after_data: bytes,
    policy: dict[str, Any],
) -> tuple[bool, str]:
    before = _relationship_records(before_data)
    after = _relationship_records(after_data)
    custom_type = policy["custom_properties_relationship_type"]

    def without_custom(records: list[dict[str, str]]) -> Counter[tuple[str, ...]]:
        return Counter(
            (item["Id"], item["Type"], item["Target"], item["TargetMode"])
            for item in records
            if item["Type"] != custom_type
        )

    if without_custom(before) != without_custom(after):
        return False, "non-custom root relationships changed"
    before_custom = [item for item in before if item["Type"] == custom_type]
    after_custom = [item for item in after if item["Type"] == custom_type]
    if len(after_custom) != 1:
        return False, "output must contain exactly one OfficeCLI custom relationship"
    item = after_custom[0]
    if (
        item["Target"] != policy["custom_properties_target"]
        or item["TargetMode"]
        or not _CUSTOM_REL_ID.fullmatch(item["Id"])
    ):
        return False, "OfficeCLI custom relationship shape is not allowlisted"
    if before_custom:
        if len(before_custom) != 1:
            return False, "source custom relationship multiplicity is invalid"
        source_item = before_custom[0]
        if source_item["Type"] != item["Type"] or source_item["Target"] != item["Target"]:
            return False, "existing custom relationship changed type or target"
    return True, "only the versioned OfficeCLI custom relationship is present"


def _validate_custom_properties(data: bytes, version: str) -> tuple[bool, str]:
    root = parse_xml(data, "docProps/custom.xml")
    if root.tag != f"{_CUSTOM_NS}Properties":
        return False, "custom.xml has an unexpected root"
    if len(root) != 2:
        return False, "custom.xml must contain exactly two properties"
    properties: dict[str, str] = {}
    for child in root:
        if child.tag != f"{_CUSTOM_NS}property":
            return False, "custom.xml contains an unexpected element"
        name = child.get("name", "")
        if (
            child.get("fmtid") != _CUSTOM_FMTID
            or child.get("pid") not in {"2", "3"}
            or len(child) != 1
            or child[0].tag != f"{_VT_NS}lpwstr"
        ):
            return False, f"custom property {name!r} has an unexpected shape"
        properties[name] = child[0].text or ""
    if len(properties) != len(root):
        return False, "custom.xml contains duplicate property names"
    if set(properties) != {"OfficeCLI.Version", "OfficeCLI.LastModified"}:
        return False, "custom.xml property names are not allowlisted"
    if properties["OfficeCLI.Version"] != version:
        return False, "custom.xml OfficeCLI version does not match policy"
    if not _TIMESTAMP.fullmatch(properties["OfficeCLI.LastModified"]):
        return False, "custom.xml LastModified is not a UTC second timestamp"
    return True, "OfficeCLI custom properties match the versioned schema"


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
    *,
    officecli_serialization: bool,
) -> list[tuple]:
    normalized = deepcopy(root)
    if officecli_serialization:
        ignorable = normalized.get(_MC_IGNORABLE, "").split()
        remaining = [value for value in ignorable if value != "w14"]
        if remaining:
            normalized.set(_MC_IGNORABLE, " ".join(remaining))
        else:
            normalized.attrib.pop(_MC_IGNORABLE, None)
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
    officecli_serialization: bool,
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
        before_tokens = _normalized_document_tokens(
            before,
            editable,
            officecli_serialization=officecli_serialization,
        )
        after_tokens = _normalized_document_tokens(
            after,
            editable,
            officecli_serialization=officecli_serialization,
        )
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
    officecli_version: str | None = None,
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
    policy = _load_policy(officecli_version) if officecli_version else None

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

        allowed_added = (
            {policy["custom_properties_part"]}
            if policy and policy["custom_properties_part"] not in before_set
            else set()
        )
        unexpected_added = sorted(set(added) - allowed_added)
        if unexpected_added:
            failures.append(f"Unexpected package parts were added: {unexpected_added}.")
        for part in added:
            classification = (
                "known_tool_metadata" if part in allowed_added else "unexpected_change"
            )
            changes.append({"part": part, "change": "added", "class": classification})

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
                    officecli_serialization=policy is not None,
                    allow_new_run_properties=allow_new_run_properties,
                )
                failures.extend(document_failures)
                classification = (
                    "intended_content_change"
                    if not document_failures
                    else "unexpected_change"
                )
            elif policy and part in policy["semantic_serializer_parts"]:
                if _semantic_xml_hash(before_data, part) == _semantic_xml_hash(
                    after_data, part
                ):
                    classification = "known_serializer_change"
                else:
                    failures.append(f"Semantic content changed in serializer part {part}.")
            elif policy and part == "[Content_Types].xml":
                ok, reason = _validate_content_types(before_data, after_data, policy)
                classification = "known_tool_metadata" if ok else "unexpected_change"
                if not ok:
                    failures.append(f"[Content_Types].xml rejected: {reason}.")
            elif policy and part == "_rels/.rels":
                ok, reason = _validate_root_relationships(before_data, after_data, policy)
                classification = "known_tool_metadata" if ok else "unexpected_change"
                if not ok:
                    failures.append(f"_rels/.rels rejected: {reason}.")
            elif policy and part == policy["custom_properties_part"]:
                ok, reason = _validate_custom_properties(after_data, officecli_version)
                classification = "known_tool_metadata" if ok else "unexpected_change"
                if not ok:
                    failures.append(f"docProps/custom.xml rejected: {reason}.")
            else:
                failures.append(f"Unexpected package part changed: {part}.")
            changes.append({"part": part, "change": "modified", "class": classification})

        if policy:
            custom_part = policy["custom_properties_part"]
            if custom_part not in after_set:
                failures.append("OfficeCLI output is missing docProps/custom.xml.")
            else:
                ok, reason = _validate_custom_properties(
                    after_zip.read(custom_part), officecli_version
                )
                if not ok:
                    failures.append(f"docProps/custom.xml rejected: {reason}.")
            if "[Content_Types].xml" in before_set & after_set:
                ok, reason = _validate_content_types(
                    before_zip.read("[Content_Types].xml"),
                    after_zip.read("[Content_Types].xml"),
                    policy,
                )
                if not ok:
                    failures.append(f"[Content_Types].xml rejected: {reason}.")
            if "_rels/.rels" in before_set & after_set:
                ok, reason = _validate_root_relationships(
                    before_zip.read("_rels/.rels"),
                    after_zip.read("_rels/.rels"),
                    policy,
                )
                if not ok:
                    failures.append(f"_rels/.rels rejected: {reason}.")

        failures.extend(_dangling_relationships(after_zip))

    failures = list(dict.fromkeys(failures))
    return {
        "schema_version": 1,
        "status": "pass" if not failures else "fail",
        "before": str(before_file),
        "before_sha256": sha256_file(before_file),
        "after": str(after_file),
        "after_sha256": sha256_file(after_file),
        "officecli_version": officecli_version,
        "editable_paragraphs": editable_paragraphs,
        "allow_new_run_properties": allow_new_run_properties,
        "changes": changes,
        "document": document_details,
        "failures": failures,
    }

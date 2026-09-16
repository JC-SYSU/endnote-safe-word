from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .models import ScanReport
from .scanner import scan_docx


def _field_signature(report: ScanReport, *, atomic_only: bool = False) -> list[dict[str, Any]]:
    fields = report.atomic_endnote_fields if atomic_only else report.endnote_fields
    return [
        {
            "part": f.part,
            "kind": f.kind,
            "instruction_sha256": f.instruction_sha256,
            "field_sha256": f.field_sha256,
            "simple_field": f.simple_field,
        }
        for f in fields
    ]


def _hashable_signature(signature: dict[str, Any]) -> tuple[Any, ...]:
    return (
        signature["part"],
        signature["kind"],
        signature["instruction_sha256"],
        signature["field_sha256"],
        signature["simple_field"],
    )


def _format_signature(report: ScanReport) -> list[tuple[str, str, str]]:
    return [
        (span.part, span.text, span.properties_sha256)
        for span in report.format_spans
    ]


def compare_reports(
    before: ScanReport,
    after: ScanReport,
    *,
    strict_format_counts: bool = True,
    allow_field_reordering: bool = False,
    expected_citation_order: list[str] | None = None,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    before_fields = _field_signature(before)
    after_fields = _field_signature(after)
    before_atomic = _field_signature(before, atomic_only=True)
    after_atomic = _field_signature(after, atomic_only=True)
    before_field_multiset = Counter(map(_hashable_signature, before_fields))
    after_field_multiset = Counter(map(_hashable_signature, after_fields))
    before_atomic_multiset = Counter(map(_hashable_signature, before_atomic))
    after_atomic_multiset = Counter(map(_hashable_signature, after_atomic))
    before_record_multiset = Counter(before.record_identities)
    after_record_multiset = Counter(after.record_identities)
    before_format_multiset = Counter(_format_signature(before))
    after_format_multiset = Counter(_format_signature(after))
    after_citation_order = [f.field_sha256 for f in after.citation_fields]

    if before.errors:
        failures.append(f"Source scan contains {len(before.errors)} error(s).")
    if after.errors:
        failures.append(f"Output scan contains {len(after.errors)} error(s).")
    if not before.all_fields_balanced:
        failures.append("Source document contains unbalanced complex fields.")
    if not after.all_fields_balanced:
        failures.append("Output document contains unbalanced complex fields.")

    if len(before_fields) != len(after_fields):
        failures.append(
            f"EndNote field count changed: {len(before_fields)} -> {len(after_fields)}."
        )
    elif before_field_multiset != after_field_multiset:
        for index, (left, right) in enumerate(zip(before_fields, after_fields), start=1):
            if left != right:
                failures.append(
                    f"EndNote field #{index} changed. "
                    f"Before={left}; after={right}"
                )
                break
        else:
            failures.append("EndNote field signatures changed.")

    if len(before_atomic) != len(after_atomic):
        failures.append(
            "Atomic EndNote field count changed: "
            f"{len(before_atomic)} -> {len(after_atomic)}."
        )
    elif before_atomic_multiset != after_atomic_multiset:
        failures.append("Atomic EndNote field signatures changed.")

    if not allow_field_reordering and before_atomic != after_atomic:
        failures.append("Atomic EndNote field order changed.")
    elif (
        allow_field_reordering
        and expected_citation_order is not None
        and after_citation_order != expected_citation_order
    ):
        failures.append(
            "Citation field order does not match the declared transaction order: "
            f"expected={expected_citation_order}; after={after_citation_order}."
        )

    if before_record_multiset != after_record_multiset:
        failures.append(
            "EndNote record identities changed: "
            f"before={dict(before_record_multiset)}; "
            f"after={dict(after_record_multiset)}."
        )

    before_parts = {p.part: p for p in before.parts}
    after_parts = {p.part: p for p in after.parts}
    for part in sorted(set(before_parts) | set(after_parts)):
        left = before_parts.get(part)
        right = after_parts.get(part)
        if left is None or right is None:
            failures.append(f"Word XML part set changed around {part}.")
            continue
        left_counts = (left.begin_count, left.separate_count, left.end_count)
        right_counts = (right.begin_count, right.separate_count, right.end_count)
        if left_counts != right_counts:
            failures.append(
                f"Field marker counts changed in {part}: {left_counts} -> {right_counts}."
            )

    if before.superscript_count != after.superscript_count:
        message = (
            f"Direct superscript count changed: {before.superscript_count} -> "
            f"{after.superscript_count}."
        )
        (failures if strict_format_counts else warnings).append(message)
    if before.subscript_count != after.subscript_count:
        message = (
            f"Direct subscript count changed: {before.subscript_count} -> "
            f"{after.subscript_count}."
        )
        (failures if strict_format_counts else warnings).append(message)

    if before_format_multiset != after_format_multiset:
        message = (
            "Direct formatted text spans changed: "
            f"before={dict(before_format_multiset)}; "
            f"after={dict(after_format_multiset)}."
        )
        (failures if strict_format_counts else warnings).append(message)

    return {
        "schema_version": 2,
        "status": "pass" if not failures else "fail",
        "before": before.to_dict(),
        "after": after.to_dict(),
        "checks": {
            "strict_format_counts": strict_format_counts,
            "allow_field_reordering": allow_field_reordering,
            "expected_citation_order": expected_citation_order,
            "endnote_field_multiset_identical": (
                before_field_multiset == after_field_multiset
            ),
            "atomic_endnote_field_multiset_identical": (
                before_atomic_multiset == after_atomic_multiset
            ),
            "atomic_endnote_field_order_identical": before_atomic == after_atomic,
            "citation_order_after": after_citation_order,
            "record_identities_identical": (
                before_record_multiset == after_record_multiset
            ),
            "field_balance_ok": before.all_fields_balanced and after.all_fields_balanced,
            "superscript_count_identical": (
                before.superscript_count == after.superscript_count
            ),
            "subscript_count_identical": (
                before.subscript_count == after.subscript_count
            ),
            "format_spans_identical": (
                before_format_multiset == after_format_multiset
            ),
        },
        "failures": failures,
        "warnings": warnings,
    }


def verify_docx(
    before_path: str | Path,
    after_path: str | Path,
    *,
    strict_format_counts: bool = True,
    allow_field_reordering: bool = False,
    expected_citation_order: list[str] | None = None,
) -> dict[str, Any]:
    return compare_reports(
        scan_docx(before_path),
        scan_docx(after_path),
        strict_format_counts=strict_format_counts,
        allow_field_reordering=allow_field_reordering,
        expected_citation_order=expected_citation_order,
    )

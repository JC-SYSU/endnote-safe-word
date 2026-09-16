from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class FieldRecord:
    part: str
    field_index: int
    paragraph_index: int | None
    kind: str
    instruction: str
    instruction_sha256: str
    field_sha256: str
    text_preview: str
    simple_field: bool = False
    end_paragraph_index: int | None = None
    nesting_level: int = 1
    parent_field_index: int | None = None
    top_level: bool = True
    record_ids: list[str] = field(default_factory=list)
    record_numbers: list[str] = field(default_factory=list)
    database_ids: list[str] = field(default_factory=list)
    display_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FormatSpanRecord:
    part: str
    paragraph_index: int | None
    text: str
    properties: list[tuple[str, tuple[tuple[str, str], ...]]]
    properties_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PartSummary:
    part: str
    begin_count: int = 0
    separate_count: int = 0
    end_count: int = 0
    unclosed_fields: int = 0
    unmatched_end_fields: int = 0
    superscript_count: int = 0
    subscript_count: int = 0
    endnote_fields: list[FieldRecord] = field(default_factory=list)
    format_spans: list[FormatSpanRecord] = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return (
            self.begin_count == self.end_count
            and self.unclosed_fields == 0
            and self.unmatched_end_fields == 0
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["balanced"] = self.balanced
        return data


@dataclass(slots=True)
class ScanReport:
    path: str
    file_sha256: str
    parts: list[PartSummary]
    errors: list[str] = field(default_factory=list)

    @property
    def endnote_fields(self) -> list[FieldRecord]:
        return [f for p in self.parts for f in p.endnote_fields]

    @property
    def atomic_endnote_fields(self) -> list[FieldRecord]:
        return [
            f
            for f in self.endnote_fields
            if f.top_level and f.kind != "ADDIN EN.CITE.DATA"
        ]

    @property
    def citation_fields(self) -> list[FieldRecord]:
        return [f for f in self.atomic_endnote_fields if f.kind == "ADDIN EN.CITE"]

    @property
    def reference_fields(self) -> list[FieldRecord]:
        return [f for f in self.atomic_endnote_fields if f.kind == "ADDIN EN.REFLIST"]

    @property
    def record_identities(self) -> list[str]:
        return [record for f in self.citation_fields for record in f.record_ids]

    @property
    def format_spans(self) -> list[FormatSpanRecord]:
        return [span for p in self.parts for span in p.format_spans]

    @property
    def superscript_count(self) -> int:
        return sum(p.superscript_count for p in self.parts)

    @property
    def subscript_count(self) -> int:
        return sum(p.subscript_count for p in self.parts)

    @property
    def all_fields_balanced(self) -> bool:
        return all(p.balanced for p in self.parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "path": self.path,
            "file_sha256": self.file_sha256,
            "summary": {
                "endnote_field_count": len(self.endnote_fields),
                "atomic_endnote_field_count": len(self.atomic_endnote_fields),
                "citation_field_count": len(self.citation_fields),
                "reference_record_count": len(self.record_identities),
                "superscript_count": self.superscript_count,
                "subscript_count": self.subscript_count,
                "format_span_count": len(self.format_spans),
                "all_fields_balanced": self.all_fields_balanced,
                "error_count": len(self.errors),
            },
            "parts": [p.to_dict() for p in self.parts],
            "ordered_endnote_fields": [f.to_dict() for f in self.endnote_fields],
            "ordered_atomic_endnote_fields": [
                f.to_dict() for f in self.atomic_endnote_fields
            ],
            "record_identities": self.record_identities,
            "format_spans": [span.to_dict() for span in self.format_spans],
            "errors": self.errors,
        }

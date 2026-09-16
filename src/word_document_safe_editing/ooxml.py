from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from .constants import W

W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
_VOLATILE_SEMANTIC_ATTRIBUTES = {
    f"{W14}paraId",
    f"{W14}textId",
}
_MEANINGFUL_TEXT_TAGS = {
    f"{W}t",
    f"{W}instrText",
    f"{W}delText",
}


class DocxError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_docx(path: str | os.PathLike[str]) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise DocxError(f"DOCX not found: {p}")
    if not zipfile.is_zipfile(p):
        raise DocxError(f"Not a valid ZIP/DOCX package: {p}")
    with zipfile.ZipFile(p) as zf:
        names = set(zf.namelist())
        if "[Content_Types].xml" not in names or "word/document.xml" not in names:
            raise DocxError(f"Missing required DOCX parts: {p}")
    return p


def parse_xml(data: bytes, part: str) -> etree._Element:
    parser = etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        remove_blank_text=False,
        strip_cdata=False,
        recover=False,
        huge_tree=True,
    )
    try:
        return etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise DocxError(f"Invalid XML in {part}: {exc}") from exc


def serialize_xml(root: etree._Element, original: bytes) -> bytes:
    # Preserve whether the original part had an XML declaration. OOXML does not
    # require a particular whitespace layout; semantic verification uses tokens.
    has_decl = original.lstrip().startswith(b"<?xml")
    return etree.tostring(
        root,
        encoding="UTF-8",
        xml_declaration=has_decl,
        standalone=None,
    )


def word_xml_parts(zf: zipfile.ZipFile) -> Iterable[str]:
    for name in zf.namelist():
        if name.startswith("word/") and name.endswith(".xml"):
            yield name


def element_token(element: etree._Element) -> tuple:
    attrs = tuple(
        sorted(
            (str(k), str(v))
            for k, v in element.attrib.items()
            if str(k) not in _VOLATILE_SEMANTIC_ATTRIBUTES
        )
    )
    text = element.text or ""
    if element.tag == f"{W}fldData":
        compact = "".join(text.split())
        try:
            decoded = base64.b64decode(compact, validate=True).rstrip(b"\x00")
        except (binascii.Error, ValueError):
            text = compact
        else:
            text = f"base64-sha256:{sha256_bytes(decoded)}"
    elif element.tag not in _MEANINGFUL_TEXT_TAGS and not text.strip():
        text = ""

    tail = element.tail or ""
    if not tail.strip():
        tail = ""
    return (
        str(element.tag),
        attrs,
        text,
        tail,
    )


def token_hash(tokens: list[tuple]) -> str:
    payload = json.dumps(tokens, ensure_ascii=False, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def ancestor_has_tag(element: etree._Element, tag: str) -> bool:
    parent = element.getparent()
    while parent is not None:
        if parent.tag == tag:
            return True
        parent = parent.getparent()
    return False


def nearest_paragraph(element: etree._Element) -> etree._Element | None:
    parent: etree._Element | None = element
    while parent is not None:
        if parent.tag == f"{W}p":
            return parent
        parent = parent.getparent()
    return None


def paragraph_text(paragraph: etree._Element | None, limit: int = 160) -> str:
    if paragraph is None:
        return ""
    chunks: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{W}t" and node.text:
            chunks.append(node.text)
    text = "".join(chunks).replace("\n", " ")
    return text[:limit]


def write_docx_with_replacements(
    source: Path,
    output: Path,
    replacements: dict[str, bytes],
    *,
    overwrite_output: bool = False,
) -> None:
    if source == output:
        raise DocxError("Refusing to overwrite the input DOCX. Choose a new output path.")
    if output.exists() and not overwrite_output:
        raise DocxError(f"Output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
            for info in src.infolist():
                data = replacements.get(info.filename, src.read(info.filename))
                # Reuse ZipInfo metadata where possible.
                dst.writestr(info, data)
        os.replace(tmp_path, output)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

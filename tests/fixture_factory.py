from __future__ import annotations

import base64
import html
import zipfile
from pathlib import Path

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def make_docx(path: Path, *, field_text: str = "1", outside: str = "Treatment works.") -> None:
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:t>{outside}</w:t></w:r>
      <w:r><w:t xml:space="preserve"> </w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE {{"author":"Smith"}} </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>{field_text}</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", document)


def make_nested_endnote_docx(path: Path, *, repeat_first_record: bool = False) -> None:
    database_id = "test-database-id"
    first_xml = f"""<EndNote><Cite><RecNum>101</RecNum><record><foreign-keys><key db-id="{database_id}">101</key></foreign-keys></record><DisplayText>[1]</DisplayText></Cite></EndNote>"""
    combined_xml = f"""<EndNote><Cite><RecNum>102</RecNum><record><foreign-keys><key db-id="{database_id}">102</key></foreign-keys></record><DisplayText>[2, 3]</DisplayText></Cite><Cite><RecNum>103</RecNum><record><foreign-keys><key db-id="{database_id}">103</key></foreign-keys></record></Cite></EndNote>"""
    field_data = base64.b64encode(combined_xml.encode("utf-8") + b"\x00").decode("ascii")
    repeated_field = ""
    if repeat_first_record:
        repeated_field = f"""
      <w:r><w:t xml:space="preserve"> repeated evidence </w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE {html.escape(first_xml)} </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>[1]</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>"""
    document = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r><w:rPr><w:i/></w:rPr><w:t>MTHFR</w:t></w:r>
      <w:r><w:t xml:space="preserve"> evidence </w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE {html.escape(first_xml)} </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>[1]</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
    </w:p>
    <w:p>
      <w:r><w:t>Combined evidence </w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"><w:fldData>{field_data}</w:fldData></w:fldChar></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText xml:space="preserve"> ADDIN EN.CITE.DATA helper </w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>helper</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
      <w:r><w:fldChar w:fldCharType="separate"/></w:r>
      <w:r><w:t>[2, 3]</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
      <w:r><w:rPr><w:vertAlign w:val="subscript"/></w:rPr><w:t>12</w:t></w:r>
      <w:r><w:t xml:space="preserve"> and x</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>2</w:t></w:r>
      {repeated_field}
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", document)

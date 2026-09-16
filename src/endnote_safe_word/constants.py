from __future__ import annotations

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"w": W_NS}

W = f"{{{W_NS}}}"
XML_SPACE = f"{{{XML_NS}}}space"

ENDNOTE_MARKERS = (
    "ADDIN EN.CITE.DATA",
    "ADDIN EN.REFLIST",
    "ADDIN EN.CITE",
)

# XML parts that may contain visible Word text or fields. The scanner checks every
# word/*.xml part, but the patcher is deliberately limited to these text-bearing parts.
PATCHABLE_PREFIXES = (
    "word/document.xml",
    "word/header",
    "word/footer",
    "word/footnotes.xml",
    "word/endnotes.xml",
    "word/comments.xml",
)

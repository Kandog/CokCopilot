"""Local text extraction from attached / knowledge-base documents."""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

SUPPORTED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
SUPPORTED_DOC_EXT = {"pdf", "docx", "txt", "md", "csv", "json", "xml", "html"}

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# A small .docx can unpack to gigabytes (a "zip bomb"); real documents stay far below this.
MAX_DOCX_XML_BYTES = 64 * 1024 * 1024


def extension(name: str | Path) -> str:
    return Path(name).suffix.lower().lstrip(".")


def is_image(name: str | Path) -> bool:
    return extension(name) in SUPPORTED_IMAGE_EXT


def is_document(name: str | Path) -> bool:
    return extension(name) in SUPPORTED_DOC_EXT


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = [(p.extract_text() or "").rstrip() for p in reader.pages]
    return "\n".join(p for p in pages if p.strip())


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        if z.getinfo("word/document.xml").file_size > MAX_DOCX_XML_BYTES:
            raise ValueError("this Word document is too large to read")
        xml = z.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for p in root.iter(f"{_W}p"):
        parts = []
        for el in p.iter():
            if el.tag == f"{_W}t":
                parts.append(el.text or "")
            elif el.tag == f"{_W}tab":
                parts.append("\t")
            elif el.tag in (f"{_W}br", f"{_W}cr"):
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs)


def extract_document_text(path: Path) -> str:
    """Raises OSError (incl. PermissionError) or ValueError-like errors on failure."""
    ext = extension(path)
    if ext == "pdf":
        return _pdf_text(path)
    if ext == "docx":
        return _docx_text(path)
    return path.read_bytes().decode("utf-8", errors="replace")

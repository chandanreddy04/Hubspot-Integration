"""Turn a downloaded agreement (bytes) into text for extraction.

Same logic as rally-ar-agent's extraction/text.py, copied as a standalone
module (no cross-repo import) since it has no rally_ar-specific dependency:

- Text-bearing PDFs / .txt: decoded directly.
- PDFs with a real text layer: pdfplumber, if the optional dependency is
  installed (``pip install pdfplumber``).
- Scanned / image-only PDFs: flagged ``needs_ocr`` — nothing here can read
  a scanned page; that's a human/OCR-backend job, not this pipeline's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("coworker.pdf_text")


@dataclass
class ExtractedText:
    text: str
    method: str            # "utf8" | "pdfplumber" | "empty"
    needs_ocr: bool = False


def _looks_like_pdf(content: bytes) -> bool:
    return content[:5] == b"%PDF-"


def _try_pdfplumber(content: bytes) -> str | None:
    try:
        import io

        import pdfplumber  # type: ignore
    except ImportError:
        return None
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
    except Exception as e:  # pragma: no cover
        log.warning("pdfplumber failed: %s", e)
        return None


def extract_text(content: bytes) -> ExtractedText:
    if _looks_like_pdf(content):
        via_pdf = _try_pdfplumber(content)
        if via_pdf:
            return ExtractedText(via_pdf, "pdfplumber")
        # fall through: maybe it still has a plain text tail, else needs OCR
    try:
        decoded = content.decode("utf-8").strip()
    except UnicodeDecodeError:
        decoded = ""
    if decoded and any(c.isalpha() for c in decoded):
        return ExtractedText(decoded, "utf8")
    return ExtractedText("", "empty", needs_ocr=True)

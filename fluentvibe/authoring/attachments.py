"""Attachment extraction helpers for authoring turns."""

from __future__ import annotations

import base64
import importlib.util
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
MAX_EXTRACTED_CHARS_PER_TURN = 60_000

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".yaml",
    ".yml",
    ".py",
}


@dataclass(frozen=True)
class ExtractedAttachment:
    name: str
    mime_type: str | None
    size: int
    stored_path: Path
    extracted_text_path: Path
    extraction_method: str
    page_count: int | None
    extracted_chars: int
    text: str
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mime_type": self.mime_type,
            "size": self.size,
            "stored_path": str(self.stored_path),
            "extracted_text_path": str(self.extracted_text_path),
            "extraction_method": self.extraction_method,
            "page_count": self.page_count,
            "extracted_chars": self.extracted_chars,
            "warnings": list(self.warnings),
        }


def extract_uploaded_attachments(
    attachments: list[dict[str, Any]] | None,
    *,
    output_dir: Path,
    turn_index: int,
) -> list[ExtractedAttachment]:
    """Decode, persist, and extract user-uploaded authoring attachments."""

    if not attachments:
        return []
    if not isinstance(attachments, list):
        raise ValueError("attachments must be a list")

    turn_dir = output_dir / "attachments" / f"turn-{turn_index:03d}"
    turn_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[ExtractedAttachment] = []
    used_chars = 0
    for index, item in enumerate(attachments, start=1):
        if not isinstance(item, dict):
            raise ValueError("each attachment must be an object")
        name = _safe_name(str(item.get("name") or f"attachment-{index}"))
        mime_type = str(item.get("mime_type") or item.get("type") or "").strip() or None
        raw = _decode_attachment(item)
        if len(raw) > MAX_ATTACHMENT_BYTES:
            raise ValueError(
                f"Attachment {name!r} is too large "
                f"({len(raw)} bytes; max {MAX_ATTACHMENT_BYTES} bytes)"
            )
        stored_path = _unique_path(turn_dir / name)
        stored_path.write_bytes(raw)
        text, extraction_method, page_count, warnings = _extract_text(stored_path, raw)
        if not text.strip():
            raise ValueError(f"Attachment {name!r} did not contain extractable text")

        remaining = MAX_EXTRACTED_CHARS_PER_TURN - used_chars
        if remaining <= 0:
            text = ""
            warnings = (*warnings, "Extracted text omitted because the per-turn limit was reached.")
        elif len(text) > remaining:
            text = text[:remaining]
            warnings = (
                *warnings,
                f"Extracted text truncated to the per-turn limit of "
                f"{MAX_EXTRACTED_CHARS_PER_TURN} characters.",
            )
        used_chars += len(text)

        extracted_text_path = stored_path.with_suffix(stored_path.suffix + ".txt")
        extracted_text_path.write_text(text, encoding="utf-8")
        extracted.append(
            ExtractedAttachment(
                name=name,
                mime_type=mime_type,
                size=len(raw),
                stored_path=stored_path,
                extracted_text_path=extracted_text_path,
                extraction_method=extraction_method,
                page_count=page_count,
                extracted_chars=len(text),
                text=text,
                warnings=warnings,
            )
        )
    return extracted


def build_attachment_context(user_text: str, attachments: list[ExtractedAttachment]) -> str:
    """Build the model-visible turn text with provenance-marked attachments."""

    text = user_text.strip()
    if not attachments:
        return text
    if not text:
        text = "Please author a fluentvibe protocol from the attached file context."

    blocks = [text, "\n\nAttached file context:"]
    for item in attachments:
        blocks.append(
            "\n".join(
                [
                    "",
                    f"--- Attached file: {item.name} ---",
                    f"Stored path: {item.stored_path}",
                    f"Extracted text path: {item.extracted_text_path}",
                    f"Extraction method: {item.extraction_method}",
                    *([f"Page count: {item.page_count}"] if item.page_count is not None else []),
                    f"Extracted characters: {item.extracted_chars}",
                    *[f"Extraction warning: {warning}" for warning in item.warnings],
                    "Extracted text follows:",
                    item.text,
                    f"--- End attached file: {item.name} ---",
                ]
            )
        )
    return "\n".join(blocks)


def _decode_attachment(item: dict[str, Any]) -> bytes:
    encoded = item.get("content_base64") or item.get("data_base64")
    if not isinstance(encoded, str) or not encoded.strip():
        raise ValueError("attachment content_base64 is required")
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("attachment content_base64 is not valid base64") from exc


def _extract_text(path: Path, raw: bytes) -> tuple[str, str, int | None, tuple[str, ...]]:
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return raw.decode("utf-8", errors="replace"), "text", None, ()
    if suffix == ".pdf":
        return _extract_pdf_text(path)
    raise ValueError(
        f"Unsupported attachment type {suffix or '<none>'!r}; "
        "supported types are PDF and text-like files"
    )


def _extract_pdf_text(path: Path) -> tuple[str, str, int | None, tuple[str, ...]]:
    text, page_count, warnings = _extract_pdf_text_direct(path)
    if text.strip():
        return text, "pdf-text", page_count, warnings
    ocr_text, ocr_page_count, ocr_warnings = _extract_pdf_text_ocr(path)
    return (
        ocr_text,
        "pdf-ocr",
        ocr_page_count or page_count,
        (*warnings, *ocr_warnings),
    )


def _extract_pdf_text_direct(path: Path) -> tuple[str, int, tuple[str, ...]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "PDF attachments require the optional dependency pypdf. "
            "Install with `python -m pip install 'fluentvibe[pdf]'` or "
            "`python -m pip install pypdf`."
        ) from exc

    reader = PdfReader(str(path))
    warnings: list[str] = []
    pages: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - parser-specific failures
            warnings.append(f"Page {index} text extraction failed: {exc}")
            text = ""
        if text.strip():
            pages.append(f"[page {index}]\n{text.strip()}")
    if not pages:
        warnings.append("PDF embedded text was empty; OCR fallback required.")
        return "", len(reader.pages), tuple(warnings)
    warnings.append(f"PDF pages read: {len(reader.pages)}")
    return "\n\n".join(pages), len(reader.pages), tuple(warnings)


def _extract_pdf_text_ocr(path: Path) -> tuple[str, int | None, tuple[str, ...]]:
    caps = attachment_capabilities()["attachments"]
    if not caps["ocr_available"]:
        error = caps.get("ocr_error") or "OCR support is unavailable."
        raise ValueError(
            f"PDF attachment {path.name!r} has no extractable embedded text, "
            f"and OCR is not available: {error}"
        )

    import fitz  # type: ignore[import-not-found]
    import pytesseract  # type: ignore[import-not-found]
    from PIL import Image

    doc = fitz.open(str(path))
    page_count = doc.page_count
    pages: list[str] = []
    warnings: list[str] = [f"OCR pages read: {page_count}"]
    try:
        for index, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            text = pytesseract.image_to_string(image) or ""
            if text.strip():
                pages.append(f"[page {index}]\n{text.strip()}")
    finally:
        doc.close()
    if not pages:
        raise ValueError(
            f"PDF attachment {path.name!r} had no extractable text after OCR."
        )
    return "\n\n".join(pages), page_count, tuple(warnings)


def attachment_capabilities() -> dict[str, Any]:
    """Return runtime capability information for UI preflight checks."""

    pdf_text_available = importlib.util.find_spec("pypdf") is not None
    ocr_error = None
    missing = [
        name
        for name in ("fitz", "pytesseract", "PIL")
        if importlib.util.find_spec(name) is None
    ]
    if missing:
        ocr_error = "Missing Python package(s): " + ", ".join(missing)
    elif shutil.which("tesseract") is None:
        ocr_error = "Tesseract executable not found on PATH"
    return {
        "ok": True,
        "attachments": {
            "enabled": True,
            "supported_extensions": sorted(TEXT_EXTENSIONS | {".pdf"}),
            "max_bytes": MAX_ATTACHMENT_BYTES,
            "max_extracted_chars_per_turn": MAX_EXTRACTED_CHARS_PER_TURN,
            "pdf_text_available": pdf_text_available,
            "ocr_available": ocr_error is None,
            "ocr_error": ocr_error,
        },
    }


def looks_like_pdf_path(text: str) -> bool:
    """Return True when text appears to include a local PDF path."""

    return bool(re.search(r"(?i)(?:[A-Z]:\\|\\\\|/)[^\r\n]+\.pdf\b", text or ""))


def _safe_name(name: str) -> str:
    base = Path(name).name.strip() or "attachment"
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .")
    return safe or "attachment"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not allocate a unique filename for {path.name!r}")

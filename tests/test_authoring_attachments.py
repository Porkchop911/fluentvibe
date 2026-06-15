from __future__ import annotations

import base64
import builtins
from pathlib import Path

import pytest

from fluentvibe.authoring.attachments import (
    MAX_ATTACHMENT_BYTES,
    attachment_capabilities,
    build_attachment_context,
    extract_uploaded_attachments,
    looks_like_pdf_path,
)


def _attachment(name: str, data: bytes, mime_type: str = "text/plain") -> dict:
    return {
        "name": name,
        "mime_type": mime_type,
        "size": len(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


def test_text_attachment_is_stored_and_extracted(tmp_path: Path) -> None:
    extracted = extract_uploaded_attachments(
        [_attachment("protocol.md", b"# Protocol\nTransfer 20 uL.")],
        output_dir=tmp_path,
        turn_index=1,
    )

    assert len(extracted) == 1
    item = extracted[0]
    assert item.name == "protocol.md"
    assert item.stored_path.exists()
    assert item.extracted_text_path.exists()
    assert item.extraction_method == "text"
    assert item.page_count is None
    assert "Transfer 20 uL" in item.text
    assert item.extracted_chars == len(item.text)

    context = build_attachment_context("Author this.", extracted)
    assert "Author this." in context
    assert "Attached file: protocol.md" in context
    assert "Extracted text follows:" in context
    assert "Extraction method: text" in context
    assert "Transfer 20 uL" in context


def test_unsupported_attachment_type_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported attachment type"):
        extract_uploaded_attachments(
            [_attachment("image.png", b"\x89PNG")],
            output_dir=tmp_path,
            turn_index=1,
        )


def test_oversized_attachment_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="too large"):
        extract_uploaded_attachments(
            [_attachment("big.txt", b"x" * (MAX_ATTACHMENT_BYTES + 1))],
            output_dir=tmp_path,
            turn_index=1,
        )


def test_pdf_without_pypdf_reports_install_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ValueError, match="PDF attachments require"):
        extract_uploaded_attachments(
            [_attachment("protocol.pdf", b"%PDF-1.4\n", "application/pdf")],
            output_dir=tmp_path,
            turn_index=1,
        )


def test_pdf_without_embedded_text_falls_back_to_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fluentvibe.authoring.attachments as mod

    monkeypatch.setattr(
        mod,
        "_extract_pdf_text_direct",
        lambda path: ("", 2, ("PDF embedded text was empty; OCR fallback required.",)),
    )
    monkeypatch.setattr(
        mod,
        "_extract_pdf_text_ocr",
        lambda path: ("[page 1]\nOCR protocol text", 2, ("OCR pages read: 2",)),
    )

    extracted = extract_uploaded_attachments(
        [_attachment("scan.pdf", b"%PDF-1.4\n", "application/pdf")],
        output_dir=tmp_path,
        turn_index=1,
    )

    assert extracted[0].extraction_method == "pdf-ocr"
    assert extracted[0].page_count == 2
    assert "OCR protocol text" in extracted[0].text
    assert any("embedded text was empty" in warning for warning in extracted[0].warnings)


def test_scanned_pdf_reports_missing_ocr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fluentvibe.authoring.attachments as mod

    monkeypatch.setattr(mod, "_extract_pdf_text_direct", lambda path: ("", 1, ()))
    monkeypatch.setattr(
        mod,
        "attachment_capabilities",
        lambda: {
            "ok": True,
            "attachments": {
                "ocr_available": False,
                "ocr_error": "Tesseract executable not found on PATH",
            },
        },
    )

    with pytest.raises(ValueError, match="OCR is not available"):
        extract_uploaded_attachments(
            [_attachment("scan.pdf", b"%PDF-1.4\n", "application/pdf")],
            output_dir=tmp_path,
            turn_index=1,
        )


def test_capabilities_report_attachment_contract() -> None:
    caps = attachment_capabilities()["attachments"]
    assert caps["enabled"] is True
    assert ".pdf" in caps["supported_extensions"]
    assert caps["max_bytes"] == MAX_ATTACHMENT_BYTES
    assert "pdf_text_available" in caps
    assert "ocr_available" in caps


def test_looks_like_pdf_path_detects_local_paths() -> None:
    assert looks_like_pdf_path(r"C:\Users\Niko\Downloads\protocol.pdf")
    assert looks_like_pdf_path(r"\\server\share\protocol.pdf")
    assert looks_like_pdf_path("/tmp/protocol.pdf")
    assert not looks_like_pdf_path("Please use the uploaded PDF.")

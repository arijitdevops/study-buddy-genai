"""Upload validation, filename sanitisation and chunking. No disk, no DB."""

from __future__ import annotations

import pytest

from app.services.file_service import (
    ALLOWED_TYPES,
    FileValidationError,
    validate_upload,
    verify_magic,
)
from app.services.storage import build_stored_name, sanitise_filename
from app.services.text_extract import chunk_text


# ----------------------------------------------------------------------
# Filename handling
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("notes.pdf", "notes.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\kid\\report.docx", "report.docx"),
        ("my notes (final).md", "my notes _final_.md"),
        ("....", "upload"),
        ("", "upload"),
    ],
)
def test_sanitise_filename(raw: str, expected: str) -> None:
    assert sanitise_filename(raw) == expected


def test_stored_name_is_a_uuid_with_the_extension() -> None:
    stored = build_stored_name("chapter 4.pdf")
    assert stored.endswith(".pdf")
    assert len(stored) == 32 + len(".pdf")
    assert build_stored_name("chapter 4.pdf") != stored


# ----------------------------------------------------------------------
# Upload validation
# ----------------------------------------------------------------------
def test_accepts_a_normal_pdf() -> None:
    name, extension = validate_upload("notes.pdf", "application/pdf", 1024)
    assert name == "notes.pdf"
    assert extension == ".pdf"


def test_accepts_every_allowlisted_extension() -> None:
    for extension, mimes in ALLOWED_TYPES.items():
        name, resolved = validate_upload(f"file{extension}", mimes[0], 512)
        assert resolved == extension


def test_rejects_executable() -> None:
    with pytest.raises(FileValidationError, match="supported"):
        validate_upload("virus.exe", "application/octet-stream", 100)


def test_rejects_mime_extension_mismatch() -> None:
    with pytest.raises(FileValidationError, match="extension"):
        validate_upload("notes.pdf", "text/plain", 100)


def test_accepts_missing_content_type() -> None:
    _, extension = validate_upload("notes.md", "", 100)
    assert extension == ".md"


def test_rejects_empty_file() -> None:
    with pytest.raises(FileValidationError, match="empty"):
        validate_upload("notes.pdf", "application/pdf", 0)


def test_rejects_oversized_file() -> None:
    with pytest.raises(FileValidationError, match="limit"):
        validate_upload("big.pdf", "application/pdf", 500 * 1024 * 1024)


def test_rejects_path_traversal_via_filename() -> None:
    name, _ = validate_upload("../../secret.txt", "text/plain", 50)
    assert "/" not in name and ".." not in name


# ----------------------------------------------------------------------
# Magic-number check
# ----------------------------------------------------------------------
def test_magic_accepts_a_real_pdf_header() -> None:
    verify_magic(".pdf", b"%PDF-1.7\n...")


def test_magic_rejects_a_renamed_file() -> None:
    with pytest.raises(FileValidationError, match="doesn't look like"):
        verify_magic(".pdf", b"MZ\x90\x00this is an exe")


def test_magic_is_skipped_for_plain_text() -> None:
    verify_magic(".txt", b"just some words")


# ----------------------------------------------------------------------
# Chunking
# ----------------------------------------------------------------------
def test_chunking_splits_long_text() -> None:
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(12))
    chunks = chunk_text(text, chunk_chars=500, overlap=50)
    assert len(chunks) > 1
    assert all(chunk.content for chunk in chunks)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_chunking_tags_pages() -> None:
    chunks = chunk_text("Some content on a page.", page=7)
    assert chunks and all(chunk.page == 7 for chunk in chunks)


def test_chunking_handles_empty_input() -> None:
    assert chunk_text("   \n  ") == []


def test_a_single_huge_paragraph_is_split() -> None:
    chunks = chunk_text("x" * 5000, chunk_chars=1000, overlap=100)
    assert len(chunks) > 1

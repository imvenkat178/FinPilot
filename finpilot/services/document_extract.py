"""Isolated PDF text extraction worker; called with bounded bytes over stdin."""
from __future__ import annotations
import io
import json
import logging
import sys

MAX_BYTES = 2_000_000
MAX_PAGES = 100
MAX_TEXT_CHARS = 500_000


def extract_pdf(content: bytes) -> list[tuple[int, str]]:
    import pypdf.filters as filters
    from pypdf import PdfReader
    # Bound decompressed streams before extraction; images are never decoded.
    for name in ("ZLIB_MAX_OUTPUT_LENGTH", "LZW_MAX_OUTPUT_LENGTH", "RUN_LENGTH_MAX_OUTPUT_LENGTH",
                 "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH", "MAX_DECLARED_STREAM_LENGTH", "FLATE_MAX_BUFFER_SIZE"):
        if hasattr(filters, name):
            setattr(filters, name, 10_000_000)
    reader = PdfReader(io.BytesIO(content), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported. Export an unlocked PDF first.")
    if len(reader.pages) > MAX_PAGES:
        raise ValueError("PDFs must contain at most 100 pages.")
    pages, total = [], 0
    for number, page in enumerate(reader.pages, 1):
        value = page.extract_text() or ""
        total += len(value)
        if total > MAX_TEXT_CHARS:
            raise ValueError("The document contains too much text. Split it into smaller files.")
        pages.append((number, value))
    if not any(value.strip() for _, value in pages):
        raise ValueError("No readable text was found. Scanned PDFs need OCR before uploading.")
    return pages


def main():
    logging.disable(logging.CRITICAL)
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
    try:
        content = sys.stdin.buffer.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("Upload files smaller than 2 MB.")
        result = {"pages": extract_pdf(content)}
    except ValueError as exc:
        result = {"error": str(exc)}
    except Exception:
        result = {"error": "This PDF could not be read. Export a text PDF and try again."}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True).encode("utf-8"))


if __name__ == "__main__":
    main()

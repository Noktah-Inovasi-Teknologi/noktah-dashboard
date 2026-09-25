"""
Intake sources → what the model reads and what is kept as evidence (research R3, G-31).

  - text     pasted text, normalised (line endings, trailing spaces, blank runs)
  - image    one screenshot, PNG/JPEG/WebP, up to 5 MB, checked by its magic bytes
  - gdoc     a Google Doc link, read with the Docs API (ported from knowledge-base)
  - pdf      pypdf reads the text layer; a PDF whose text layer is too thin is a
             SCANNED PDF and is rendered to images with pypdfium2 — at most 10
             pages. A longer scanned PDF is refused ("unreadable"), never cut short
             silently: the Hub asks for text instead of guessing (spec edge cases).

`content_hash` is sha256 over the kind and the content the model will see, so an
identical re-submission hits the Intake cache (constitution XI) and a Google Doc
that changed since is read again.
"""
import asyncio
import base64
import binascii
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PDF_BYTES = 7 * 1024 * 1024
MAX_SCANNED_PAGES = 10
# A page with fewer characters than this in its text layer has no real text layer.
MIN_CHARS_PER_PAGE = 40
MAX_TEXT_CHARS = 60_000

IMAGE_MAGIC = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/webp": (b"RIFF",),
}

_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


class SourceError(Exception):
    """The input can't be read. `message` is shown to the Manager as is (Bahasa)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass
class Source:
    kind: str                                   # intakes.kind
    text: Optional[str] = None                  # what the model reads, for text-like kinds
    images: List[Tuple[str, bytes]] = field(default_factory=list)  # (mime, bytes) sent to the model
    raw_blob: Optional[bytes] = None            # evidence kept 12 months (screenshot or original PDF)
    raw_mime: Optional[str] = None
    source_ref: Optional[str] = None            # Doc URL or file name
    content_hash: str = ""

    @property
    def is_image(self) -> bool:
        return self.kind in ("image", "pdf_scanned")


def normalize_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _hash(kind: str, payload: bytes) -> str:
    return hashlib.sha256(kind.encode() + b"\0" + payload).hexdigest()


def _decode(b64: Optional[str], what: str, limit: int) -> bytes:
    if not b64:
        raise SourceError(f"{what} kosong.")
    if "," in b64[:100] and b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise SourceError(f"{what} rusak atau bukan base64.")
    if len(data) > limit:
        raise SourceError(f"{what} terlalu besar (maks {limit // (1024 * 1024)} MB).")
    return data


def _text_source(kind: str, text: str, source_ref: Optional[str] = None) -> Source:
    text = normalize_text(text)
    if not text:
        raise SourceError("Tidak ada teks yang bisa dibaca.")
    if len(text) > MAX_TEXT_CHARS:
        raise SourceError(f"Teks terlalu panjang (maks {MAX_TEXT_CHARS:,} karakter). Bagi menjadi beberapa Intake.")
    return Source(kind=kind, text=text, source_ref=source_ref, content_hash=_hash(kind, text.encode()))


def read_text(text: Optional[str]) -> Source:
    return _text_source("text", text or "")


def read_image(b64: Optional[str], mime: Optional[str], filename: Optional[str] = None) -> Source:
    mime = (mime or "").lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in IMAGE_MAGIC:
        raise SourceError("Gambar harus PNG, JPEG, atau WebP.")
    data = _decode(b64, "Gambar", MAX_IMAGE_BYTES)
    if not data.startswith(IMAGE_MAGIC[mime]) or (mime == "image/webp" and data[8:12] != b"WEBP"):
        raise SourceError("Berkas bukan gambar yang valid.")
    return Source(kind="image", images=[(mime, data)], raw_blob=data, raw_mime=mime, source_ref=filename,
                  content_hash=_hash("image", data))


# ── Google Docs ───────────────────────────────────────────────────────────────

def doc_id(url: str) -> Optional[str]:
    match = _DOC_ID_RE.search(url or "")
    return match.group(1) if match else None


def doc_text(document: dict) -> str:
    """Flatten a Docs API document body into plain text (paragraphs and table cells)."""
    parts: List[str] = []

    def walk(content: list) -> None:
        for element in content or []:
            paragraph = element.get("paragraph")
            if paragraph:
                for run in paragraph.get("elements", []):
                    text_run = run.get("textRun")
                    if text_run and text_run.get("content"):
                        parts.append(text_run["content"])
            table = element.get("table")
            if table:
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        walk(cell.get("content", []))
                    parts.append("\n")

    walk(document.get("body", {}).get("content", []))
    return "".join(parts)


def _fetch_doc(document_id: str) -> dict:
    from .. import google

    try:
        docs = google.service("docs", "v1", [google.DOCS_READONLY_SCOPE])
    except google.GoogleNotConfigured as e:
        raise SourceError(str(e))
    return docs.documents().get(documentId=document_id).execute()


async def read_gdoc(url: Optional[str]) -> Source:
    document_id = doc_id(url or "")
    if not document_id:
        raise SourceError("Tautan harus berupa Google Doc (docs.google.com/document/d/…).")
    try:
        document = await asyncio.to_thread(_fetch_doc, document_id)
    except SourceError:
        raise
    except Exception as e:  # HttpError, refresh failure: the Manager needs to know it wasn't read
        status = getattr(getattr(e, "resp", None), "status", None)
        if status in (403, 404):
            raise SourceError("Dokumen tidak bisa dibuka. Pastikan dokumen dibagikan ke akun Noktah.")
        raise SourceError(f"Gagal membaca Google Doc ({type(e).__name__}).")
    return _text_source("gdoc", doc_text(document), source_ref=url)


# ── PDF ───────────────────────────────────────────────────────────────────────

def _pdf_text_pages(data: bytes) -> List[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise SourceError("PDF dikunci kata sandi.")
        return [(page.extract_text() or "") for page in reader.pages]
    except PdfReadError:
        raise SourceError("PDF rusak atau tidak bisa dibaca.")


def _render_pages(data: bytes, count: int) -> List[bytes]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    images = []
    try:
        for i in range(count):
            # scale 1.5 ≈ 108 dpi: legible text, small enough for the model call.
            pil = pdf[i].render(scale=1.5).to_pil()
            buf = io.BytesIO()
            pil.convert("RGB").save(buf, format="JPEG", quality=80)
            images.append(buf.getvalue())
    finally:
        pdf.close()
    return images


def is_scanned(pages: List[str]) -> bool:
    """A text layer averaging under MIN_CHARS_PER_PAGE per page is a scan, not a document."""
    if not pages:
        return True
    total = sum(len(p.strip()) for p in pages)
    return total < MIN_CHARS_PER_PAGE * len(pages)


def read_pdf(b64: Optional[str], filename: Optional[str] = None) -> Source:
    data = _decode(b64, "PDF", MAX_PDF_BYTES)
    if not data.startswith(b"%PDF"):
        raise SourceError("Berkas bukan PDF.")
    pages = _pdf_text_pages(data)
    if not is_scanned(pages):
        src = _text_source("pdf_text", "\n\n".join(pages), source_ref=filename)
        src.raw_blob, src.raw_mime = data, "application/pdf"
        return src
    if len(pages) > MAX_SCANNED_PAGES:
        raise SourceError(
            f"PDF hasil scan ini {len(pages)} halaman; maksimal {MAX_SCANNED_PAGES}. "
            "Kirim teksnya, atau bagi PDF-nya.")
    try:
        rendered = _render_pages(data, len(pages))
    except Exception as e:
        raise SourceError(f"Halaman PDF tidak bisa dibaca sebagai gambar ({type(e).__name__}).")
    return Source(kind="pdf_scanned", images=[("image/jpeg", img) for img in rendered], raw_blob=data,
                  raw_mime="application/pdf", source_ref=filename, content_hash=_hash("pdf_scanned", data))


async def read(kind: str, *, text: Optional[str] = None, image_base64: Optional[str] = None,
               mime: Optional[str] = None, url: Optional[str] = None, pdf_base64: Optional[str] = None,
               filename: Optional[str] = None) -> Source:
    if kind == "text":
        return read_text(text)
    if kind == "image":
        return read_image(image_base64, mime, filename)
    if kind == "gdoc":
        return await read_gdoc(url)
    if kind == "pdf":
        return await asyncio.to_thread(read_pdf, pdf_base64, filename)
    raise SourceError("Jenis masukan harus text, image, gdoc, atau pdf.")

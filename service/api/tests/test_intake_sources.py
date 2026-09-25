"""Intake source readers (research R3). No network: Google Docs is only parsed here."""
import base64
import io

import pytest

from app.intake import sources
from app.intake.sources import SourceError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _pdf(pages_text):
    """A small PDF with one text line per page (pypdf can write, but not draw text; use a raw file)."""
    objects = []
    kids = []
    for i, text in enumerate(pages_text):
        content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
        page_no = 3 + i * 2
        kids.append(f"{page_no} 0 R")
        objects.append((page_no, f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {page_no + 1} 0 R "
                                  f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> >>".encode()))
        objects.append((page_no + 1, b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"))
    objects.insert(0, (2, f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()))
    objects.insert(0, (1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num, body in objects:
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    count = max(offsets) + 1
    out.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode())
    for n in range(1, count):
        out.write(f"{offsets[n]:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def test_text_is_normalised_and_hashed():
    a = sources.read_text("Halo\r\n\r\n\r\n\r\nharga   \nbaru  ")
    assert a.text == "Halo\n\nharga\nbaru"
    assert a.kind == "text" and len(a.content_hash) == 64
    assert sources.read_text("Halo\n\n\n\nharga\nbaru").content_hash == a.content_hash


def test_empty_text_refused():
    with pytest.raises(SourceError):
        sources.read_text("   \n  ")


def test_image_checks_type_and_magic():
    src = sources.read_image(b64(PNG), "image/png", "chat.png")
    assert src.is_image and src.raw_blob == PNG and src.images == [("image/png", PNG)]
    assert sources.read_image("data:image/png;base64," + b64(PNG), "image/png").raw_blob == PNG
    with pytest.raises(SourceError, match="PNG, JPEG"):
        sources.read_image(b64(PNG), "image/gif")
    with pytest.raises(SourceError, match="bukan gambar"):
        sources.read_image(b64(b"not an image at all"), "image/png")
    with pytest.raises(SourceError, match="base64"):
        sources.read_image("%%%", "image/png")


def test_image_size_limit():
    big = PNG + b"\x00" * sources.MAX_IMAGE_BYTES
    with pytest.raises(SourceError, match="terlalu besar"):
        sources.read_image(b64(big), "image/png")


def test_doc_id_and_text():
    assert sources.doc_id("https://docs.google.com/document/d/1AbC-_x/edit?tab=t.0") == "1AbC-_x"
    assert sources.doc_id("https://docs.google.com/spreadsheets/d/1AbC/edit") is None
    doc = {"body": {"content": [
        {"paragraph": {"elements": [{"textRun": {"content": "Tone: hangat\n"}}]}},
        {"table": {"tableRows": [{"tableCells": [
            {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Jangan"}}]}}]},
            {"content": [{"paragraph": {"elements": [{"textRun": {"content": "murah"}}]}}]}]}]}},
    ]}}
    assert sources.doc_text(doc) == "Tone: hangat\nJanganmurah\n"


async def test_gdoc_needs_a_doc_link():
    with pytest.raises(SourceError, match="Google Doc"):
        await sources.read_gdoc("https://example.com/x")


def test_text_pdf_is_read_as_text():
    data = _pdf(["Tone of voice: hangat, ramah, tidak menggurui pasien.", "Jangan pakai kata murah di konten."])
    src = sources.read_pdf(b64(data), "guideline.pdf")
    assert src.kind == "pdf_text"
    assert "hangat, ramah" in src.text and "kata murah" in src.text
    assert src.raw_blob == data and src.raw_mime == "application/pdf"


def test_thin_pdf_counts_as_scanned():
    assert sources.is_scanned(["", "  ", "x"])
    assert not sources.is_scanned(["a" * 200, ""])  # one cover page doesn't make a document a scan


def test_scanned_pdf_over_ten_pages_is_refused_not_cut():
    data = _pdf([""] * 11)
    with pytest.raises(SourceError, match="11 halaman"):
        sources.read_pdf(b64(data))


def test_scanned_pdf_is_rendered_to_images():
    data = _pdf(["", ""])
    src = sources.read_pdf(b64(data), "scan.pdf")
    assert src.kind == "pdf_scanned" and src.is_image
    assert len(src.images) == 2 and all(m == "image/jpeg" for m, _ in src.images)


def test_not_a_pdf():
    with pytest.raises(SourceError, match="bukan PDF"):
        sources.read_pdf(b64(b"hello"))

"""
SP letters (spec 009 G-44, G-48, G-49; template specs/009-hub-otomasi-laporan/sp-letter-template.md).

Rendered here as HTML from config/hub/sp_letter.html; the hub-sanctions flow uploads each as a
Google Doc into Company > HR > Surat Peringatan. The Hub never sends a letter to anyone. A
teguran lisan gets no letter, and termination is never written by the Hub.
"""
import html
from datetime import date
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Optional

import asyncpg

from .ladder import LABELS

ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")
MONTHS_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
             "Oktober", "November", "Desember")
TITLES = {"sp1": "SURAT PERINGATAN PERTAMA", "sp2": "SURAT PERINGATAN KEDUA", "sp3": "SURAT PERINGATAN KETIGA",
          "peringatan_terakhir": "PERINGATAN PERTAMA DAN TERAKHIR"}
KINDS = {"sp1": "Surat Peringatan Pertama (SP1)", "sp2": "Surat Peringatan Kedua (SP2)",
         "sp3": "Surat Peringatan Ketiga (SP3)", "peringatan_terakhir": "Peringatan Pertama dan Terakhir"}
ROLE_LABELS = {"content_planner": "Content Planner", "field_associate": "Field Associate",
               "content_editor": "Content Editor", "quality_assurance": "Quality Assurance",
               "account_executive": "Account Executive", "production_manager": "Production Manager",
               "brand_manager": "Brand Manager"}


def tanggal(d: date) -> str:
    return f"{d.day} {MONTHS_ID[d.month - 1]} {d.year}"


async def next_number(conn: asyncpg.Connection, issued_on: date) -> str:
    """`{urut}/SP/ESK/{bulan romawi}/{tahun}`, counting from 001 each year (G-48)."""
    n = await conn.fetchval(
        """INSERT INTO sanction_letter_counters (year, last) VALUES ($1, 1)
           ON CONFLICT (year) DO UPDATE SET last = sanction_letter_counters.last + 1 RETURNING last""",
        issued_on.year)
    return f"{n:03d}/SP/ESK/{ROMAN[issued_on.month - 1]}/{issued_on.year}"


def _template() -> Template:
    from ..settings import get_settings
    return Template((Path(get_settings().card_definition_dir) / "sp_letter.html").read_text(encoding="utf-8"))


def render(*, level: str, number: str, name: str, role: str, issued_on: date, valid_until: date,
           source: str, events: List[Dict[str, Any]], points: Optional[int], period: Optional[date],
           rule: str, direct_item: Optional[str], with_pip: bool, ceo: str, brand_manager: str,
           recipient_is_brand_manager: bool, template: Optional[Template] = None) -> str:
    e = html.escape
    if source == "direct":
        dasar = f"Bagian 4.3, {e(direct_item or rule)}"
        ev = events[0] if events else {}
        alasan = (f"<p>Berdasarkan Event {e(ev.get('key', ''))} tertanggal {e(ev.get('date', ''))} yang telah dinilai, "
                  f"Saudara/i dinyatakan melakukan pelanggaran: <b>{e(ev.get('summary') or rule)}</b>. Atas pelanggaran "
                  f"tersebut, sanksi dijatuhkan secara langsung tanpa menunggu rekap poin bulanan.</p>")
    else:
        dasar = "Bagian 3.2, Jenjang Sanksi dan Tingkat Surat Peringatan"
        rows = "".join(f"<tr><td>{e(v['key'])}</td><td>{e(v['date'])}</td><td>{e(v.get('category') or '')}</td>"
                       f"<td>{e(v.get('summary') or '')}</td><td>{v['points']:+d}</td></tr>" for v in events)
        bulan = f"{MONTHS_ID[period.month - 1]} {period.year}" if period else ""
        alasan = (f"<p>Berdasarkan rekap bulan {e(bulan)}, Saudara/i tercatat memperoleh <b>{points} poin "
                  f"Violation</b> dari Event yang telah dinilai berikut:</p>"
                  f"<table><tr><th>Event</th><th>Tanggal</th><th>Kategori</th><th>Uraian</th><th>Poin</th></tr>"
                  f"{rows}</table><p>Ketentuan yang diterapkan: {e(rule)}.</p>")
    akhir = (" Pelanggaran yang dilakukan kembali selama masa berlaku surat ini akan diproses sesuai ketentuan "
             "pemutusan hubungan kerja." if level in ("sp3", "peringatan_terakhir") else "")
    pip = ("<h3>Performance Improvement Plan (PIP)</h3><p>Surat ini disertai PIP selama <b>30 hari</b>. Sasaran dan "
           "cara penilaiannya ditetapkan oleh atasan langsung dan dilampirkan pada surat ini. PIP yang sasarannya "
           "tidak tercapai akan diproses sesuai Bagian 3.2.</p>" if with_pip else "")
    blank = "______________________"
    if recipient_is_brand_manager:
        ttd = (f"<table><tr><td>Diterbitkan oleh</td><td>Diterima oleh</td></tr><tr><td><br><br>{blank}</td>"
               f"<td><br><br>{blank}</td></tr><tr><td>{e(ceo)}<br>CEO</td><td>{e(name)}<br>Tanggal diterima: "
               f"____________</td></tr></table>")
    else:
        ttd = (f"<table><tr><td colspan=\"2\">Diterbitkan oleh</td><td>Diterima oleh</td></tr>"
               f"<tr><td><br><br>{blank}</td><td><br><br>{blank}</td><td><br><br>{blank}</td></tr>"
               f"<tr><td>{e(ceo)}<br>CEO</td><td>{e(brand_manager)}<br>Brand Manager Eskala</td>"
               f"<td>{e(name)}<br>Tanggal diterima: ____________</td></tr></table>")
    return (template or _template()).safe_substitute(
        judul=TITLES[level], nomor=e(number), jenis_surat=KINDS[level], nama=e(name), peran=e(role),
        dasar_pasal=dasar, alasan=alasan, tanggal_terbit=tanggal(issued_on), tanggal_berakhir=tanggal(valid_until),
        kalimat_tingkat_akhir=akhir, blok_pip=pip, tanda_tangan=ttd)


def file_name(number: str, name: str, level: str) -> str:
    return f"{number.replace('/', '-')} - {name} - {LABELS[level]}"

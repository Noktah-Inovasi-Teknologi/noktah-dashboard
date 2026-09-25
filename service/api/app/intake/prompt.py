"""
The Intake prompt and response schema, version `intake_v1` (contracts/intake-ai.md).

The model reads one source (text, or up to 10 images) for ONE chosen Client and
returns candidate items. It never writes to the card: every item becomes a
Proposal that a Manager accepts, edits or rejects (FR-035).

Values are always the COMPLETE new field value, so a Proposal can be shown as a
before/after and accepting it never wipes what the source didn't mention:
  - object fields: only the sub-fields that change (the pipeline merges them into
    the current value);
  - list / lines fields: the whole new list, i.e. the current list with the change
    applied.
"""
import base64
import json
from typing import Any, Dict, List, Optional

from ..card.definition import Definition
from .sources import Source

PROMPT_VERSION = "intake_v1"
MAX_TOKENS = 6000

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "no_card_home", "nothing_found"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["target", "field_key", "value", "excerpt", "speaker", "spoke_at", "valid_until",
                             "is_update", "is_patient_data"],
                "properties": {
                    "target": {"type": "string", "enum": ["profil", "guideline", "request"]},
                    "field_key": {"type": ["string", "null"]},
                    # text, list, object or lines, by the field's shape; a request is text.
                    "value": {},
                    "excerpt": {"type": "string"},
                    "speaker": {"type": ["string", "null"]},
                    "spoke_at": {"type": ["string", "null"]},
                    "valid_until": {"type": ["string", "null"]},
                    "is_update": {"type": "boolean"},
                    "is_patient_data": {"type": "boolean"},
                },
            },
        },
        "no_card_home": {"type": "array", "items": {"type": "string"}},
        "nothing_found": {"type": "boolean"},
    },
}

SYSTEM = """Anda membantu tim agensi konten mencatat fakta dan permintaan klien ke Kartu Klien.
Anda membaca SATU sumber (chat, dokumen, atau gambar) untuk SATU klien, lalu mengusulkan item.
Usulan Anda tidak langsung disimpan: seorang manajer memeriksa setiap item.

Aturan:
1. Ambil HANYA yang tertulis di sumber. JANGAN menerjemahkan. Salin nilai KATA PER KATA:
   angka, satuan, nama, gelar, tagline, ejaan, huruf besar, dan bahasa aslinya.
2. Setiap item punya target:
   - "profil" atau "guideline": fakta untuk satu kolom kartu; isi field_key dengan kunci kolomnya.
   - "request": sesuatu yang klien minta agar Noktah kerjakan; field_key = null, value = kalimat
     permintaannya kata per kata.
   Satu kalimat bisa menghasilkan DUA item: permintaan dan fakta (mis. "harga jadi X, tolong buat konten").
3. "excerpt" disalin PERSIS dari sumber: potongan terpendek yang mendukung item. Jangan merapikan,
   meringkas, atau menggabungkan kalimat yang berjauhan.
4. Isi "speaker" dan "spoke_at" (YYYY-MM-DD) bila sumber menunjukkannya (baris WhatsApp
   "[dd/mm/yy jam] Nama: ..." menunjukkannya). Bila tidak ada, null.
5. "valid_until" (YYYY-MM-DD) hanya bila sumber menyebut batas berlaku. Jangan menebak.
6. "field_key" SELALU kunci kolom utama (mis. "tentang_usaha"), BUKAN "kolom.sub_kolom".
   Bentuk "value" mengikuti kolomnya:
   - text: satu teks.
   - list: daftar LENGKAP yang baru (daftar saat ini + perubahan).
   - object: HANYA sub-kolom yang berubah, dengan kunci sub-kolom yang tersedia.
   - lines: daftar baris LENGKAP yang baru (baris saat ini + perubahan). Baris harga WAJIB punya
     "satuan" dan "syarat"; bila sumber tidak menyebutnya, biarkan "" (kosong). Jangan mengarang.
   - rating: angka 1-5. choice: salah satu kunci pilihan yang tersedia.
7. "is_update": true bila sumber menyatakan perubahan dari nilai saat ini ("mulai", "sekarang jadi",
   "ganti"); false bila hanya menyebut nilai.
8. Tandai "is_patient_data": true untuk apa pun tentang pasien atau pelanggan perorangan (nama,
   diagnosis, wajah, nomor pribadi). Item ini tidak akan pernah disimpan.
9. Salam, basa-basi, penjadwalan rapat, dan ucapan terima kasih BUKAN item.
10. Isi yang tidak cocok dengan kolom mana pun (angka performa bulanan, logistik rapat) tulis
    singkat di "no_card_home". Bila sama sekali tidak ada fakta atau permintaan, "nothing_found": true.
11. Jangan menambah fakta dari pengetahuan Anda sendiri, dan jangan memakai klaim yang dilarang
    sebagai nilai kecuali klien menulisnya persis (tetap salin apa adanya).

Jawab HANYA dengan JSON sesuai skema."""


def _field_lines(d: Definition) -> List[str]:
    lines: List[str] = []
    for part in ("profil", "guideline"):
        lines.append(f"## {part}")
        for f in d.fields(part):
            head = f"- {f['key']} ({f['shape']}): {f['label']}"
            if f.get("help"):
                head += f". {f['help']}"
            lines.append(head)
            for s in f.get("subfields", []):
                extra = ""
                if s.get("choices"):
                    extra = " pilihan: " + ", ".join(d.choice_keys(s["choices"]))
                lines.append(f"    - {s['key']} ({s['shape']}): {s['label']}{extra}")
    return lines


def build_messages(d: Definition, *, client_name: str, current: Dict[str, Dict[str, Any]], pic_name: Optional[str],
                   source: Source) -> List[Dict[str, Any]]:
    context = "\n".join([
        f"# Klien: {client_name}",
        f"PIC (satu-satunya yang ucapannya bisa mengubah fakta): {pic_name or '(belum diisi)'}",
        "",
        "# Kolom kartu (definisi " + d.version + ")",
        *_field_lines(d),
        "",
        "# Klaim yang selalu dilarang",
        ", ".join(d.always_banned),
        "",
        "# Isi kartu saat ini (yang sudah dikonfirmasi)",
        json.dumps(current, ensure_ascii=False, default=str),
    ])
    if source.is_image:
        note = ("Sumber berupa gambar (tangkapan layar)." if source.kind == "image"
                else "Sumber berupa halaman PDF hasil scan.")
        content: List[Dict[str, Any]] = [{"type": "text", "text": context + "\n\n# Sumber\n" + note}]
        for mime, data in source.images:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}})
    else:
        label = {"text": "teks yang ditempel", "gdoc": "Google Doc", "pdf_text": "teks dari PDF",
                 "old_note": "catatan lama"}.get(source.kind, "teks")
        content = context + f"\n\n# Sumber ({label})\n<<<\n{source.text}\n>>>"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]


def validate_shape(parsed: Any) -> None:
    """Envelope-level validation: a failure here is retried once with this message quoted.

    Per-item problems (unknown field, value in the wrong shape) are NOT retried: that
    item is dropped and counted, the rest of the answer is still useful.
    """
    if not isinstance(parsed, dict):
        raise ValueError("Akar jawaban harus objek JSON.")
    for key in ("items", "no_card_home", "nothing_found"):
        if key not in parsed:
            raise ValueError(f"Kunci wajib '{key}' tidak ada.")
    if not isinstance(parsed["items"], list):
        raise ValueError("'items' harus daftar.")
    if not isinstance(parsed["no_card_home"], list) or not all(isinstance(x, str) for x in parsed["no_card_home"]):
        raise ValueError("'no_card_home' harus daftar teks.")
    if not isinstance(parsed["nothing_found"], bool):
        raise ValueError("'nothing_found' harus true atau false.")
    for i, item in enumerate(parsed["items"]):
        if not isinstance(item, dict):
            raise ValueError(f"items[{i}] harus objek.")
        if item.get("target") not in ("profil", "guideline", "request"):
            raise ValueError(f"items[{i}].target harus profil, guideline, atau request.")
        if not isinstance(item.get("excerpt"), str) or not item["excerpt"].strip():
            raise ValueError(f"items[{i}].excerpt wajib berisi kutipan dari sumber.")
        if not isinstance(item.get("is_patient_data"), bool):
            raise ValueError(f"items[{i}].is_patient_data harus true atau false.")

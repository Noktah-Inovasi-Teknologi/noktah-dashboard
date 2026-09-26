"""Songbird's client facts come from the Hub's Client Card (current values and open
requests), found by exact key or alias; the PIC stays out of the prompt."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import songbird_tasks as st  # noqa: E402

DEFINITION = {"version": "v1", "parts": {
    "profil": {"label": "Profil", "fields": [
        {"key": "nama_penulisan", "label": "Nama & penulisan", "shape": "object",
         "subfields": [{"key": "nama_resmi", "label": "Nama resmi"}, {"key": "jangan_ditulis", "label": "Jangan ditulis"}]},
        {"key": "pic", "label": "PIC", "shape": "object", "subfields": [{"key": "nama", "label": "Nama"}]}]},
    "guideline": {"label": "Guideline", "fields": [
        {"key": "suara", "label": "Suara", "shape": "object", "subfields": [{"key": "sapaan", "label": "Sapaan"}]}]}}}


def test_card_values_read_as_labelled_lines():
    labels = {"nama_resmi": "Nama resmi", "jangan_ditulis": "Jangan ditulis"}
    assert st._card_text({"nama_resmi": "Klinik Mata Sampang", "jangan_ditulis": ["KMS", "Klinik Sampang"], "grup": ""},
                         labels) == "Nama resmi: Klinik Mata Sampang; Jangan ditulis: KMS | Klinik Sampang"
    assert st._card_text([{"item": "LASIK", "harga": "Rp 9.500.000"}, {"item": "", "harga": None}], {}) == \
        "item: LASIK; harga: Rp 9.500.000"
    assert st._card_text(None, {}) == ""


@pytest.mark.schema
@pytest.mark.asyncio
async def test_the_client_card_and_open_requests_ground_songbird(spine_db):
    conn = spine_db
    cid = await conn.fetchval(
        "INSERT INTO clients (client_key, display_name) VALUES ('klinik mata sampang', 'Klinik Mata Sampang') RETURNING id")
    await conn.execute("INSERT INTO client_aliases (client_id, alias_key, alias_text, source) "
                       "VALUES ($1, 'kms', 'KMS', 'manual')", cid)
    pid = await conn.fetchval("INSERT INTO people (display_name) VALUES ('Defila') RETURNING id")
    await conn.execute("INSERT INTO card_definitions (version, body) VALUES ('v1', $1::jsonb)", json.dumps(DEFINITION))
    for part, key, value, state in [
        ("profil", "nama_penulisan", {"nama_resmi": "Klinik Mata Sampang"}, "current"),
        ("profil", "pic", {"nama": "Bu Rina", "nomor": "0812"}, "current"),
        ("guideline", "suara", {"sapaan": "Anda"}, "current"),
        ("guideline", "suara", {"sapaan": "Kamu"}, "pending"),
    ]:
        await conn.execute(
            """INSERT INTO card_values (client_id, part, field_key, definition_version, value, state, set_by)
               VALUES ($1, $2, $3, 'v1', $4::jsonb, $5, $6)""", cid, part, key, json.dumps(value), state, pid)
    await conn.execute(
        """INSERT INTO client_requests (client_id, requested_on, text, requested_by, channel, status, created_by)
           VALUES ($1, '2026-09-20', 'Video testimoni pasien katarak', 'Bu Rina', 'whatsapp_group', 'baru', $2),
                  ($1, '2026-09-01', 'Sudah selesai', 'Bu Rina', 'whatsapp_group', 'selesai', $2)""", cid, pid)

    out = await st._client_card(conn, "KMS")  # found through the alias
    assert out["client_name"] == "Klinik Mata Sampang"
    assert out["records"] == [
        {"subject": "Profil · Nama & penulisan", "information": "Nama resmi: Klinik Mata Sampang"},
        {"subject": "Guideline · Suara", "information": "Sapaan: Anda"},
        {"subject": "Permintaan klien terbuka (2026-09-20)", "information": "Video testimoni pasien katarak"},
    ], "only current values and open requests; the PIC and pending values stay out"

    assert (await st._client_card(conn, "Klinik Mata Sampang Baru"))["records"] == [], "no similar-name match"

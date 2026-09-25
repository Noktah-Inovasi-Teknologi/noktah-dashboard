"""The v1 card definition (G-24, G-28, G-29, G-30) and the pure rules built on it."""
from pathlib import Path

import pytest

from app.card.definition import completeness, is_empty, load_file, satisfies_required, validate_value

DEF = load_file(str(Path(__file__).resolve().parents[3] / "config" / "hub"))


def test_v1_has_the_agreed_fields():
    assert [f["key"] for f in DEF.fields("profil")] == [
        "nama_penulisan", "tentang_usaha", "lokasi_kontak_jam", "akun_hashtag", "produk_layanan",
        "harga_promo", "orang_yang_tampil", "target_audiens", "pic", "aturan_produksi_privasi"]
    assert [f["key"] for f in DEF.fields("guideline")] == [
        "inti_brand", "positioning", "identitas_prism", "kepribadian_arketipe", "suara", "visual",
        "pesan_pilar", "wajib_ada", "batasan", "referensi_pembeda"]
    assert {f["key"] for f in DEF.fields("profil") if f.get("required")} == {
        "nama_penulisan", "lokasi_kontak_jam", "akun_hashtag", "produk_layanan", "pic"}
    assert {f["key"] for f in DEF.fields("guideline") if f.get("required")} == {
        "positioning", "suara", "visual", "batasan"}
    assert len(DEF.choice_keys("arketipe")) == 12


def test_shapes_are_enforced():
    validate_value(DEF, "profil", "harga_promo", [
        {"item": "LASIK", "harga": "9.500.000", "satuan": "per mata", "syarat": "", "berlaku_sampai": ""}])
    validate_value(DEF, "guideline", "kepribadian_arketipe", {"sincerity": 5, "arketipe_utama": "caregiver"})
    with pytest.raises(ValueError):
        validate_value(DEF, "guideline", "kepribadian_arketipe", {"sincerity": 6})
    with pytest.raises(ValueError):
        validate_value(DEF, "guideline", "kepribadian_arketipe", {"arketipe_utama": "wizard"})
    with pytest.raises(ValueError):
        validate_value(DEF, "profil", "pic", {"hobi": "x"})
    with pytest.raises(ValueError):
        validate_value(DEF, "profil", "tidak_ada", "x")


def test_emptiness_and_required_subfields():
    assert is_empty({"a": "", "b": []}) and is_empty("  ") and not is_empty({"a": "x"})
    positioning = DEF.field("guideline", "positioning")
    assert not satisfies_required(positioning, {"tagline": "Lihat lebih jelas"})
    assert satisfies_required(positioning, {"pernyataan": "Klinik mata untuk keluarga di Sampang."})


def test_completeness_counts():
    current = {
        "profil": {"nama_penulisan": {"nama_resmi": "Klinik Mata Sampang"}, "pic": {"nama": "Bu Rina"}},
        "guideline": {"suara": {"sapaan": "Anda"}, "positioning": {"tagline": "x"}},
    }
    c = completeness(DEF, current)
    assert c["profil"] == {"filled": 2, "required": 5,
                           "missing": ["lokasi_kontak_jam", "akun_hashtag", "produk_layanan"]}
    assert c["guideline"]["filled"] == 2 and c["guideline"]["total"] == 10
    assert c["guideline"]["missing_required"] == ["positioning", "visual", "batasan"]

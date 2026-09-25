"""Deterministic Intake checks (research R3). No database, no network."""
import pytest

from app.intake import checks
from app.intake.checks import Context, check_decision, flags_for

CHAT = """[25/09/26 10.15] Rina Wati: Selamat pagi kak
[25/09/26 10.16] Rina Wati: Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap.
[25/09/26 10.16] Rina Wati: Tolong minggu depan bikin konten promo ini.
[25/09/26 10.20] Andi (staff): Jangan lupa tulis “Klinik Mata Bireuen” ya, bukan KMB
25/09/26 10.21 - Rina Wati: Oke makasih"""

NAMES = ["Klinik Mata Bireuen", "Klinik Mata Sampang", "Klinik Mata Smec Bitung", "Klinik Utama Gresik",
         "Ecky Dental Center"]


def ctx(**over):
    base = dict(client_name="Klinik Mata Bireuen", source_text=CHAT, is_image=False, pic_name="Ibu Rina Wati",
                other_client_names=[n for n in NAMES if n != "Klinik Mata Bireuen"],
                generic=checks.generic_tokens(NAMES), never_write=["KMB"], current={})
    base.update(over)
    return Context(**base)


def item(**over):
    base = dict(target="profil", field_key="harga_promo",
                value=[{"item": "LASIK", "harga": "9,5 jt", "satuan": "per mata", "syarat": "promo pelajar tetap"}],
                excerpt="Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya", speaker="Rina Wati",
                spoke_at="2026-09-25", valid_until=None, is_update=True, is_patient_data=False)
    base.update(over)
    return base


# ── quote in text ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("excerpt", [
    "Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya",
    "mulai 1 oktober   harga lasik jadi 9,5 JT per mata ya",            # case + whitespace
    "Tolong minggu depan bikin konten promo ini.",
    'tulis "Klinik Mata Bireuen" ya',                                    # curly vs straight quotes
    "promo pelajar tetap.\nTolong minggu depan",                          # spans two WhatsApp lines
    "Oke makasih",                                                       # Android export prefix
])
def test_real_quotes_are_found(excerpt):
    assert checks.quote_found(excerpt, CHAT)


# SC-004: quotes the model could plausibly invent. Every one must come back unverified.
FABRICATED = [
    "Mulai 1 Oktober harga LASIK jadi 9 jt per mata ya",                 # number changed
    "harga LASIK jadi 9,5 jt per paket",                                 # unit changed
    "Mulai 1 November harga LASIK jadi 9,5 jt per mata",                 # date changed
    "promo pelajar dihentikan",                                          # meaning reversed
    "Tolong besok bikin konten promo ini.",                              # time changed
    "Harga LASIK 9,5 jt per mata berlaku sampai Desember",               # invented condition
    "Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar dihapus.",
    "[25/09/26 10.16] Rina Wati: harga Femto LASIK 12 jt",               # invented line, real prefix
    "ya",                                                                # too short to prove anything
    "",
]


@pytest.mark.parametrize("excerpt", FABRICATED)
def test_fabricated_quotes_are_not_found(excerpt):
    assert not checks.quote_found(excerpt, CHAT)


def test_fabricated_set_is_all_flagged_unverified():
    flagged = [e for e in FABRICATED if "unverified" in flags_for(item(excerpt=e), ctx())]
    assert len(flagged) == len(FABRICATED)


def test_whatsapp_prefix_variants_are_stripped():
    assert checks.strip_wa_prefix("[25/09/26, 10.15.33] Rina: halo") == "halo"
    assert checks.strip_wa_prefix("25/09/2026 10:15 - Rina Wati: halo") == "halo"
    assert checks.strip_wa_prefix("[1/9/26 9.05 PM] Rina: halo") == "halo"
    assert checks.strip_wa_prefix("Harga: 9,5 jt") == "Harga: 9,5 jt"  # not a chat line


# ── PIC ───────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("speaker,pic,expected", [
    ("Rina Wati", "Ibu Rina Wati", True),
    ("Bu Rina", "Rina Wati", True),
    ("dr. Rina Wati, Sp.M", "Rina Wati", True),    # title and degree around the PIC name
    ("Andi (staff)", "Rina Wati", False),
    ("+62 812-3456-7890", "Rina Wati", False),
    (None, "Rina Wati", False),
    ("Rina Wati", None, False),
    ("Rina Wati", "", False),
])
def test_speaker_is_pic(speaker, pic, expected):
    assert checks.speaker_is_pic(speaker, pic) is expected


def test_not_pic_flags_facts_only():
    assert "not_pic" in flags_for(item(speaker="Andi (staff)"), ctx())
    assert "not_pic" in flags_for(item(speaker=None), ctx())
    assert "not_pic" in flags_for(item(), ctx(pic_name=None))
    request = item(target="request", field_key=None, value="Tolong minggu depan bikin konten promo ini.",
                   excerpt="Tolong minggu depan bikin konten promo ini.", speaker="Andi (staff)")
    assert "not_pic" not in flags_for(request, ctx())


# ── price, other client, contradiction, language, images ─────────────────────

def test_price_without_unit_or_conditions():
    assert checks.price_incomplete("harga_promo", [{"item": "LASIK", "harga": "9,5 jt", "satuan": "", "syarat": "x"}])
    assert checks.price_incomplete("harga_promo", [{"item": "LASIK", "harga": "9,5 jt", "satuan": "per mata"}])
    assert not checks.price_incomplete("harga_promo", [{"item": "LASIK", "harga": "9,5 jt", "satuan": "per mata",
                                                        "syarat": "promo pelajar tetap"}])
    assert not checks.price_incomplete("harga_promo", [{"item": "Konsultasi gratis", "harga": ""}])
    assert not checks.price_incomplete("tentang_usaha", {"ringkasan": "x"})


def test_sibling_branch_by_place_name():
    generic = checks.generic_tokens(NAMES)
    others = [n for n in NAMES if n != "Klinik Mata Bireuen"]
    assert checks.names_other_client("Promo ini juga berlaku di Sampang", "Klinik Mata Bireuen", others, generic)
    assert checks.names_other_client("sama seperti smec bitung", "Klinik Mata Bireuen", others, generic)
    assert not checks.names_other_client("Klinik mata terbaik di Bireuen", "Klinik Mata Bireuen", others, generic)
    assert not checks.names_other_client("pemeriksaan mata gratis", "Klinik Mata Bireuen", others, generic)


def test_never_write_names_are_other_client():
    flags = flags_for(item(excerpt="Jangan lupa tulis", value=[{"item": "KMB promo", "harga": ""}]), ctx())
    assert "other_client" in flags
    # a word merely containing the letters is not the name
    assert not checks.names_other_client("tkmbx", "Klinik Mata Bireuen", [], set(), ["KMB"])


def test_contradiction_only_when_not_an_update():
    current = {"profil": {"harga_promo": [{"item": "LASIK", "harga": "9 jt", "satuan": "per mata", "syarat": "-"}]}}
    assert "contradicts" in flags_for(item(is_update=False), ctx(current=current))
    assert "contradicts" not in flags_for(item(is_update=True), ctx(current=current))
    assert "contradicts" not in flags_for(item(is_update=False), ctx(current={}))


def test_plainly_english_is_flagged_but_code_mixing_is_not():
    assert checks.plainly_english("We should not use the word cheap for our services in this campaign")
    assert not checks.plainly_english("Tolong pakai tagline See the World Clearly di setiap konten ya")
    assert not checks.plainly_english("Grand opening")


def test_image_sources_skip_the_quote_check_and_are_marked():
    flags = flags_for(item(excerpt="anything the model read off the picture"), ctx(source_text=None, is_image=True))
    assert "from_image" in flags and "unverified" not in flags


def test_flags_come_in_a_stable_order():
    flags = flags_for(item(excerpt="invented text here", speaker="Andi", is_update=False,
                           value=[{"item": "LASIK", "harga": "9 jt"}]),
                      ctx(current={"profil": {"harga_promo": [{"item": "x"}]}}))
    assert flags == ["unverified", "not_pic", "price_incomplete", "contradicts"]


# ── acceptance rules ──────────────────────────────────────────────────────────

def test_ticks_required_by_flags():
    with pytest.raises(ValueError, match="Sudah dicek"):
        check_decision(["from_image"], "accept", {}, "v", None)
    with pytest.raises(ValueError, match="PIC sudah konfirmasi"):
        check_decision(["not_pic"], "accept", {"pic_confirmed": "yes"}, "v", None)  # must be literally true
    check_decision(["from_image", "not_pic"], "accept", {"image_checked": True, "pic_confirmed": True}, "v", None)


def test_unverified_must_be_edited_or_rejected():
    with pytest.raises(ValueError, match="Ketik ulang"):
        check_decision(["unverified"], "accept", {}, "v", None)
    with pytest.raises(ValueError, match="Isi nilai"):
        check_decision(["unverified"], "edit", {}, "v", None)
    check_decision(["unverified"], "edit", {}, "v", "v retyped")
    check_decision(["unverified", "from_image"], "reject", {}, "v", None)  # rejecting needs no ticks


def test_unknown_outcome_refused():
    with pytest.raises(ValueError):
        check_decision([], "approve", {}, "v", None)

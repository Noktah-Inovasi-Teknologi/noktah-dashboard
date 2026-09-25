"""Pure parts of the Intake pipeline: turning model items into Proposals. No database."""
from app.card.definition import load_file
from app.intake import checks, pipeline
from tests.conftest import ROOT

D = load_file(str(ROOT / "config" / "hub"))
TEXT = "Vision & mission\n\nVision: To become a trusted dental clinic in Madura."


def ctx(current=None):
    return checks.Context(client_name="Ecky Dental Center", source_text=TEXT, is_image=False, pic_name=None,
                          current=current or {"profil": {}, "guideline": {}})


def item(**over):
    base = {"target": "profil", "field_key": "tentang_usaha", "value": {"visi_misi": "x"},
            "excerpt": "Vision: To become a trusted dental clinic in Madura.", "speaker": None, "spoke_at": None,
            "valid_until": None, "is_update": False, "is_patient_data": False}
    return base | over


def run(items, current=None):
    out = pipeline.IntakeOutcome("t")
    return pipeline._proposals(D, items, ctx(current), out), out.dropped


def test_dotted_subfield_key_becomes_the_field_with_that_subfield():
    kept, dropped = run([item(field_key="tentang_usaha.visi_misi", value="Vision: To become a trusted dental clinic")])
    assert dropped == {"patient_data": 0, "invalid": 0, "unchanged": 0}
    assert kept[0]["field_key"] == "tentang_usaha"
    assert kept[0]["value"] == {"visi_misi": "Vision: To become a trusted dental clinic"}


def test_object_proposal_keeps_only_the_changed_subfields():
    current = {"profil": {"tentang_usaha": {"ringkasan": "Klinik gigi", "industri": "Kesehatan"}}, "guideline": {}}
    kept, _ = run([item(field_key="tentang_usaha.visi_misi", value="Vision: X")], current)
    assert kept[0]["value"] == {"visi_misi": "Vision: X"}
    assert pipeline.on_current(kept[0]["value"], current["profil"]["tentang_usaha"]) == {
        "ringkasan": "Klinik gigi", "industri": "Kesehatan", "visi_misi": "Vision: X"}


def test_parts_of_one_field_become_one_proposal():
    kept, _ = run([item(field_key="tentang_usaha.visi_misi", value="Vision: X",
                        excerpt="Vision: To become a trusted dental clinic in Madura."),
                   item(field_key="tentang_usaha.industri", value="Dental", excerpt="Vision & mission"),
                   item(target="profil", field_key="target_audiens", value="Keluarga",
                        excerpt="Vision & mission")])
    assert [(p["field_key"]) for p in kept] == ["tentang_usaha", "target_audiens"]
    assert kept[0]["value"] == {"visi_misi": "Vision: X", "industri": "Dental"}
    assert "…" in kept[0]["excerpt"]


def test_unchanged_subfield_is_dropped():
    current = {"profil": {"tentang_usaha": {"visi_misi": "Vision: X"}}, "guideline": {}}
    _, dropped = run([item(field_key="tentang_usaha.visi_misi", value="Vision: X")], current)
    assert dropped["unchanged"] == 1


def test_unknown_subfield_or_field_is_still_dropped():
    _, dropped = run([item(field_key="tentang_usaha.tidak_ada", value="x"),
                      item(field_key="tidak_ada.visi_misi", value="x"),
                      item(field_key="target_audiens.x", value="x")])  # text field has no sub-fields
    assert dropped["invalid"] == 3


def test_patient_data_is_dropped_before_anything_else():
    _, dropped = run([item(is_patient_data=True)])
    assert dropped["patient_data"] == 1

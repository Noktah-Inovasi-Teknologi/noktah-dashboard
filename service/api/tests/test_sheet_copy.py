"""Sheet copy planning (research R5, FR-017, G-20). Pure: rows in, cell writes out."""
from app.registry import sheet_copy
from app.registry.sheet_copy import Plan, plan_all

HEADER = ["No.", "Name", "Folder ID", "Content Plan Folder ID", "Status", "Post", "Story", "Short Video",
          "Total Minutes Equivalent", "Instagram", "TikTok"]
CLIENTS = [
    HEADER,
    ["1", "Ecky Dental Center", "F1", "CP1", "active", "4", "4", "4", "720", "https://www.instagram.com/eckydental/", "-",
     "Done"],
    ["2", "Nirwana Coffee Shop Sumenep", "F2", "CP2", "active", "4", "8", "6", "1040", "https://www.instagram.com/nirwana/",
     "-"],
]
# Hashmaps from row 3: A..T (No., WORKERS B/C, _, No., COMPONENTS F/G, _, No., CE J/K, _, No., FA N/O, _, No., SOCIAL R/S/T)
HASHMAPS = [
    ["1", "Nadya", "jira-nadya", "", "1", "Ecky Dental Center", "10000", "", "1", "Ecky Dental Center", "Putri", "",
     "1", "Ecky Dental Center", "Nadya", "", "1", "Ecky Dental Center", "Klinik Gigi X",
     "https://www.instagram.com/gigix/"],
    ["2", "Putri", "jira-putri", "", "2", "Nirwana Coffee Space Sumenep", "10001"],
    ["", "", "", "", "3", "Eskala", "99999"],
]

ECKY, NIRWANA, ESKALA = "c-ecky", "c-nirwana", "c-eskala"
PEOPLE = [{"id": "p-nadya", "display_name": "Nadya", "jira_account_id": "jira-nadya", "status": "active"},
          {"id": "p-putri", "display_name": "Putri", "jira_account_id": "jira-putri", "status": "active"}]
ALIASES = {"ecky dental center": ECKY, "nirwana coffee shop sumenep": NIRWANA, "nirwana coffee space sumenep": NIRWANA,
           "eskala": ESKALA}


def registry(**over):
    ecky = {"id": ECKY, "name": "Ecky Dental Center", "status": "active", "is_internal": False, "quota_post": 4,
            "quota_story": 4, "quota_short_video": 4, "drive_folder_id": "F1", "content_plan_folder_id": "CP1",
            "jira_component_id": "10000", "sheet_row_name": "Ecky Dental Center", "own": {"instagram": "eckydental"},
            "competitors": ["gigix"], "team": {"content_editor": "Putri", "field_associate": "Nadya"}}
    nirwana = {"id": NIRWANA, "name": "Nirwana Coffee Shop Sumenep", "status": "active", "is_internal": False,
               "quota_post": 4, "quota_story": 8, "quota_short_video": 6, "drive_folder_id": "F2",
               "content_plan_folder_id": "CP2", "jira_component_id": "10001",
               "sheet_row_name": "Nirwana Coffee Shop Sumenep", "own": {"instagram": "nirwana"}, "competitors": [],
               "team": {}}
    eskala = {"id": ESKALA, "name": "Eskala", "status": "active", "is_internal": True, "quota_post": 10,
              "quota_story": None, "quota_short_video": None, "drive_folder_id": None, "content_plan_folder_id": None,
              "jira_component_id": "99999", "sheet_row_name": None, "own": {}, "competitors": [], "team": {}}
    rows = {ECKY: ecky, NIRWANA: nirwana, ESKALA: eskala}
    for cid, changes in over.items():
        rows[cid] = rows[cid] | changes
    return list(rows.values())


def plan(reg=None, people=None, clients=None, hashmaps=None) -> Plan:
    return plan_all(clients or CLIENTS, hashmaps or HASHMAPS, reg or registry(), people or PEOPLE, ALIASES)


def test_registry_that_matches_the_sheet_writes_nothing():
    p = plan()
    assert p.cells == {}, p.overwritten


def test_only_managed_columns_are_written():
    p = plan(registry(**{ECKY: {"quota_post": 5, "status": "inactive"}}))
    assert p.cells == {"Clients!F2": "5", "Clients!E2": "inactive"}
    # never No. (A), Total Minutes Equivalent (I), or the extra column past the header (L)
    assert not any(a1.startswith(("Clients!A", "Clients!I", "Clients!L")) for a1 in p.cells)


def test_internal_clients_stay_out_of_the_clients_tab_but_keep_their_hashmaps():
    p = plan()
    assert p.cells == {}, "Eskala's COMPONENTS row is kept, and no Clients row is written for it"
    p = plan(registry(**{ESKALA: {"jira_component_id": "99998"}}))
    assert p.cells == {"Hashmaps!G5": "99998"}


def test_rename_rewrites_the_same_row_via_sheet_row_name():
    p = plan(registry(**{ECKY: {"name": "Ecky Dental Centre"}}))
    assert p.cells["Clients!B2"] == "Ecky Dental Centre"
    assert p.renamed_rows == {ECKY: "Ecky Dental Centre"}
    assert not any(a1.startswith("Clients!") and a1.endswith("4") for a1 in p.cells), "no second row appended"


def test_new_client_is_appended_below_the_last_row():
    extra = {"id": "c-new", "name": "Klinik Baru", "status": "pending", "is_internal": False, "quota_post": 2,
             "quota_story": None, "quota_short_video": None, "drive_folder_id": None, "content_plan_folder_id": None,
             "jira_component_id": None, "sheet_row_name": None, "own": {}, "competitors": [], "team": {}}
    p = plan(registry() + [extra])
    assert p.cells["Clients!B4"] == "Klinik Baru" and p.cells["Clients!E4"] == "pending"
    assert p.cells["Clients!F4"] == "2"
    assert "Clients!A4" not in p.cells


def test_url_formatting_differences_are_not_changes():
    clients = [HEADER, CLIENTS[1][:9] + ["instagram.com/EckyDental", "-"], CLIENTS[2]]
    assert plan(clients=clients).cells == {}


def test_hashmaps_keys_keep_the_live_spelling():
    # COMPONENTS says "…Space…" while the Registry name says "…Shop…": the key stays as the automations know it.
    p = plan(registry(**{NIRWANA: {"jira_component_id": "10002"}}))
    assert p.cells == {"Hashmaps!G4": "10002"}


def test_team_change_rewrites_the_block_value_only():
    p = plan(registry(**{ECKY: {"team": {"content_editor": "Nadya", "field_associate": "Nadya"}}}))
    assert p.cells == {"Hashmaps!K3": "Nadya"}


def test_removed_competitor_is_blanked_and_new_one_appended():
    p = plan(registry(**{ECKY: {"competitors": ["kmneyecare"]}}))
    assert p.cells["Hashmaps!S3"] == "kmneyecare"
    assert p.cells["Hashmaps!T3"] == "https://www.instagram.com/kmneyecare/"
    assert "Hashmaps!R3" not in p.cells  # same client key, kept
    p = plan(registry(**{ECKY: {"competitors": []}}))
    assert p.cells == {"Hashmaps!R3": "", "Hashmaps!S3": "", "Hashmaps!T3": ""}


def test_person_who_left_leaves_workers():
    people = [PEOPLE[0], PEOPLE[1] | {"status": "left"}]
    p = plan(registry(**{ECKY: {"team": {"field_associate": "Nadya"}}}), people=people)
    assert p.cells["Hashmaps!B4"] == "" and p.cells["Hashmaps!C4"] == ""


def test_check_mode_lists_what_it_overwrote():
    p = plan(registry(**{ECKY: {"quota_post": 5}}))
    assert p.overwritten == [{"cell": "Clients!F2", "what": "Ecky Dental Center · Post", "sheet": "4", "registry": "5"}]


def test_same_treats_blanks_and_numbers_alike():
    assert sheet_copy.same("", "-") and sheet_copy.same("4", "4.0") and not sheet_copy.same("4", "5")
    assert sheet_copy.same("https://www.instagram.com/a/", "@a", "Instagram")

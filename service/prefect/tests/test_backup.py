"""Nightly database backup: which copies are removed, and when removal must stop. Pure, no I/O."""
from datetime import datetime

from tasks.backup_tasks import backup_file_name, files_to_delete, shrink_problem


def f(name, id_=None):
    return {"name": name, "id": id_ or name}


def test_file_names_sort_in_time_order():
    a = backup_file_name("noktah_dashboard", datetime(2026, 9, 9, 2, 0))
    b = backup_file_name("noktah_dashboard", datetime(2026, 9, 25, 2, 0))
    assert a == "noktah_dashboard_2026-09-09_0200.dump" and a < b


def test_keeps_the_newest_and_deletes_the_rest():
    files = [f(f"noktah_dashboard_2026-09-{d:02d}_0200.dump") for d in range(1, 21)]
    stale = files_to_delete(files, "noktah_dashboard", keep=14)
    assert [x["name"][17:27] for x in stale] == [f"2026-09-{d:02d}" for d in range(6, 0, -1)]


def test_never_touches_other_files_in_the_folder():
    files = [f("README.txt"), f("noktah_dashboard_dev_2026-09-01_0200.dump"), f("prefect_2026-09-01_0200.dump"),
             f("noktah_dashboard_2026-09-01_0200.dump")]
    assert files_to_delete(files, "noktah_dashboard", keep=1) == []
    # the _dev database's copies look similar but belong to another rotation
    assert files_to_delete(files, "noktah_dashboard_dev", keep=0)[0]["name"].startswith("noktah_dashboard_dev_")


def test_fewer_backups_than_keep_deletes_nothing():
    assert files_to_delete([f("noktah_dashboard_2026-09-01_0200.dump")], "noktah_dashboard", keep=14) == []


def test_a_normal_night_is_not_a_shrink():
    assert shrink_problem(24_000_000, 25_000_000) is None
    assert shrink_problem(24_000_000, None) is None          # first backup ever


def test_a_backup_under_half_the_previous_size_raises_the_alarm():
    msg = shrink_problem(5_000_000, 24_000_000)
    assert msg and "tidak dihapus" in msg

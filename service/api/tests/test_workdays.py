"""Working days for the monthly incentive steps (spec 009 R10)."""
from datetime import date, datetime

from app import workdays

OFF = [date(2026, 12, 25), date(2027, 1, 1)]


def test_working_days_skip_weekends_and_holidays():
    # November 2026 starts on a Sunday
    assert workdays.working_day(2026, 11, 1, OFF) == date(2026, 11, 2)
    assert workdays.working_day(2026, 11, 2, OFF) == date(2026, 11, 3)
    # January 2027: the 1st is a holiday (Friday), then a weekend
    assert workdays.working_day(2027, 1, 1, OFF) == date(2027, 1, 4)
    assert workdays.working_day(2027, 1, 3, OFF) == date(2027, 1, 6)


def test_next_working_day_and_end_of_day_in_wib():
    assert workdays.next_working_day(date(2026, 12, 24), OFF) == date(2026, 12, 28)
    end = workdays.end_of(date(2026, 11, 4))
    assert end == datetime(2026, 11, 5, 0, 0, tzinfo=workdays.WIB)


def test_previous_month():
    assert workdays.previous_month(date(2027, 1, 15)) == date(2026, 12, 1)


def test_the_holiday_file_is_read(tmp_path, monkeypatch):
    (tmp_path / "holidays.yaml").write_text("holidays:\n  - 2026-12-25\n", encoding="utf-8")
    monkeypatch.setenv("HUB_API_DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("HUB_API_ACCESS_API_AUD", "x")
    from app.settings import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("HUB_API_CARD_DEFINITION_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        assert date(2026, 12, 25) in workdays.holidays()
    finally:
        get_settings.cache_clear()

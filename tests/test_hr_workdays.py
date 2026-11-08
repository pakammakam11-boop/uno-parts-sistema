from datetime import date

from core.hr import workdays


def test_easter_2026_and_2027():
    assert workdays.easter_sunday(2026) == date(2026, 4, 5)
    assert workdays.easter_sunday(2027) == date(2027, 3, 28)


def test_weekends_are_not_workdays():
    assert not workdays.is_workday(date(2027, 2, 6))  # šeštadienis
    assert not workdays.is_workday(date(2027, 2, 7))  # sekmadienis
    assert workdays.is_workday(date(2027, 2, 8))


def test_fixed_and_easter_holidays_2026_2027():
    for day in (date(2026, 1, 1), date(2026, 2, 16), date(2026, 3, 11), date(2026, 4, 6), date(2026, 5, 1),
                date(2026, 6, 24), date(2026, 7, 6), date(2026, 8, 15), date(2026, 11, 1), date(2026, 11, 2),
                date(2026, 12, 24), date(2026, 12, 25), date(2026, 12, 26),
                date(2027, 3, 29), date(2027, 1, 1), date(2027, 12, 24)):
        assert workdays.holiday_name(day), day


def test_work_days_skip_holidays():
    assert workdays.work_days(date(2026, 12, 21), date(2026, 12, 31)) == 7   # 24-26 šventės, 27 sekmadienis
    assert workdays.work_days(date(2027, 2, 15), date(2027, 2, 19)) == 4     # 16 d. šventė
    assert workdays.work_days(date(2027, 2, 6), date(2027, 2, 7)) == 0


def test_single_day_range():
    assert workdays.work_days(date(2027, 2, 8), date(2027, 2, 8)) == 1


def test_holidays_between_lists_weekend_holidays_too():
    names = workdays.holidays_between(date(2026, 12, 24), date(2026, 12, 27))
    assert [h["date"] for h in names] == ["2026-12-24", "2026-12-25", "2026-12-26"]

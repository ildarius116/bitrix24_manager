"""Юнит-тесты src.dates: разбор CLI-дат, извлечение даты из title, окно 4 дней.

Без сети. Окно редактирования (политика N дней, CLAUDE.md §5):
today − N <= дата <= today (включительно). Граница: today − N допустима, today − (N+1) — нет.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.dates import (
    DateParseError,
    extract_date,
    parse_cli_date,
    within_edit_window,
)

DATE_FIELD = "ufCrm46_1742342657"


# --- parse_cli_date: оба формата + ошибочный ввод ---


@pytest.mark.parametrize(
    "value,expected",
    [
        ("25.06.2026", date(2026, 6, 25)),
        ("01.01.2026", date(2026, 1, 1)),
        ("2026-06-25", date(2026, 6, 25)),
        ("2026-01-01", date(2026, 1, 1)),
        ("  25.06.2026  ", date(2026, 6, 25)),  # пробелы по краям
    ],
)
def test_parse_cli_date_valid(value, expected):
    assert parse_cli_date(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "не дата",
        "2026/06/25",      # неподдерживаемый разделитель
        "06-25-2026",      # mm-dd-yyyy не поддержан
        "32.13.2026",      # несуществующая дата
        "2026-13-01",      # несуществующий месяц
    ],
)
def test_parse_cli_date_invalid(value):
    with pytest.raises(DateParseError):
        parse_cli_date(value)


def test_parse_cli_date_none():
    with pytest.raises(DateParseError):
        parse_cli_date(None)  # type: ignore[arg-type]


# --- extract_date из title ---


def test_extract_date_ru_from_title():
    assert extract_date("Иванов Иван | 25.06.2026") == date(2026, 6, 25)


def test_extract_date_iso_fallback():
    assert extract_date("Петров П. 2026-06-25") == date(2026, 6, 25)


def test_extract_date_none_and_empty():
    assert extract_date(None) is None
    assert extract_date("") is None
    assert extract_date("без даты в строке") is None


def test_extract_date_invalid_in_title():
    # Совпадает по шаблону дд.мм.гггг, но дата невалидна → None.
    assert extract_date("Кто-то | 32.01.2026") is None


# --- within_edit_window: границы ---


def _entry(d: date) -> dict:
    return {DATE_FIELD: d.isoformat()}


def test_window_today_is_inside():
    today = date(2026, 6, 25)
    assert within_edit_window(_entry(today), 4, today, date_field=DATE_FIELD) is True


def test_window_today_minus_4_is_inside():
    today = date(2026, 6, 25)
    entry = _entry(today - timedelta(days=4))
    assert within_edit_window(entry, 4, today, date_field=DATE_FIELD) is True


def test_window_today_minus_5_is_outside():
    today = date(2026, 6, 25)
    entry = _entry(today - timedelta(days=5))
    assert within_edit_window(entry, 4, today, date_field=DATE_FIELD) is False


def test_window_future_date_is_outside():
    today = date(2026, 6, 25)
    entry = _entry(today + timedelta(days=1))
    assert within_edit_window(entry, 4, today, date_field=DATE_FIELD) is False


def test_window_missing_date_is_false():
    today = date(2026, 6, 25)
    assert within_edit_window({}, 4, today, date_field=DATE_FIELD) is False
    assert within_edit_window({DATE_FIELD: None}, 4, today, date_field=DATE_FIELD) is False
    assert within_edit_window({DATE_FIELD: ""}, 4, today, date_field=DATE_FIELD) is False


def test_window_falls_back_to_title_date():
    today = date(2026, 6, 25)
    # Поля даты нет, но дата есть в title и попадает в окно.
    entry = {"title": "Сидоров С. | 24.06.2026"}
    assert within_edit_window(entry, 4, today, date_field=DATE_FIELD) is True


def test_window_iso_with_time_suffix():
    today = date(2026, 6, 25)
    entry = {DATE_FIELD: "2026-06-23T00:00:00+03:00"}
    assert within_edit_window(entry, 4, today, date_field=DATE_FIELD) is True

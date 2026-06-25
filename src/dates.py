"""Работа с датами: разбор CLI-дат, извлечение даты из title и окно редактирования.

Политика 4 дней (CLAUDE.md §5, FR-2.1.3): редактировать можно день не старше
`edit_window_days` календарных дней — то есть дата записи >= сегодня − N (включительно),
по полю `ufCrm46_1742342657`, таймзона Europe/Moscow.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Europe/Moscow — фиксированный UTC+3 (без перехода на летнее время с 2014 г.).
# Не зависим от наличия tzdata/zoneinfo на хосте.
MOSCOW_TZ = timezone(timedelta(hours=3), name="Europe/Moscow")

# дд.мм.гггг
_RU_DATE_RE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")
# ГГГГ-ММ-ДД (возможно с временем — берём только дату)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# Префикс ISO-8601 в начале строки: 'ГГГГ-ММ-ДД' с опциональным временем ('T'/пробел...).
# Bitrix отдаёт даты как '2026-06-23T00:00:00+03:00' — обычный \b перед 'T' не срабатывает.
_ISO_DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class DateParseError(ValueError):
    """Понятная ошибка разбора даты."""


def today_moscow() -> date:
    """Сегодняшняя дата в таймзоне Europe/Moscow."""
    return datetime.now(MOSCOW_TZ).date()


def parse_cli_date(value: str) -> date:
    """Разобрать дату из CLI: поддерживаются 'дд.мм.гггг' и 'ГГГГ-ММ-ДД'.

    Бросает DateParseError с понятным сообщением при неверном формате.
    """
    if value is None:
        raise DateParseError("Пустое значение даты.")
    raw = value.strip()
    if not raw:
        raise DateParseError("Пустое значение даты.")

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise DateParseError(
        f"Не удалось разобрать дату {raw!r}. Ожидаемые форматы: дд.мм.гггг или ГГГГ-ММ-ДД."
    )


def extract_date(title: Optional[str]) -> Optional[date]:
    """Извлечь дату рабочего дня из title '<ФИО> | дд.мм.гггг' (PRD §2.6).

    Возвращает None, если title пуст или дата не найдена. Резервно понимает ГГГГ-ММ-ДД.
    Поле `ufCrm46_1742342657` остаётся надёжным источником; это вспомогательный разбор.
    """
    if not title:
        return None

    m = _RU_DATE_RE.search(title)
    if m:
        day, month, year = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    m = _ISO_DATE_RE.search(title)
    if m:
        year, month, day = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def _coerce_date(value: Any) -> Optional[date]:
    """Привести произвольное значение поля даты к date (или None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        # Bitrix отдаёт ISO-8601, иногда с временем/смещением: берём дату из начала строки.
        m = _ISO_DATE_PREFIX_RE.match(raw)
        if m:
            year, month, day = (int(g) for g in m.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
        # Резерв: дд.мм.гггг.
        m2 = _RU_DATE_RE.search(raw)
        if m2:
            day, month, year = (int(g) for g in m2.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    return None


def entry_date(entry: Dict[str, Any], *, date_field: str = "ufCrm46_1742342657") -> Optional[date]:
    """Достать дату записи «Рабочий день»: сначала поле даты, затем резерв из title."""
    if entry is None:
        return None
    value = entry.get(date_field)
    parsed = _coerce_date(value)
    if parsed is not None:
        return parsed
    return extract_date(entry.get("title") or entry.get("TITLE"))


def within_edit_window(
    entry: Dict[str, Any],
    days: int,
    today: Optional[date] = None,
    *,
    date_field: str = "ufCrm46_1742342657",
) -> bool:
    """True, если дата записи попадает в окно редактирования (политика N дней).

    Условие: today − days <= entry_date <= today (включительно с обеих границ).
    Дата вне диапазона, пустая или будущая (после today) → False.
    Граница: сегодня − N допустима, сегодня − (N+1) уже нет.
    """
    ref = today or today_moscow()
    d = entry_date(entry, date_field=date_field)
    if d is None:
        return False
    earliest = ref - timedelta(days=days)
    return earliest <= d <= ref

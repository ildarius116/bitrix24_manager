"""Оффлайн-тесты агрегатов выгрузки: data_time_range и группировка (без сети).

Синтетические WorkdayDay/WorkLog; проверяем, что:
- рамки дат вычисляются по фактическим датам (без None);
- группировка по «Описанию задачи» считает кол-во/сумму часов/даты корректно;
- сумма часов по группам сходится с суммой по основным строкам.
"""

from __future__ import annotations

from datetime import date

from src.export_excel import NO_DESCRIPTION, _group_rows
from src.workday import WorkdayDay, WorkLog, data_time_range


def _log(desc: str, hours, *, log_id: int = 0) -> WorkLog:
    return WorkLog(
        id=log_id,
        parent_day_id=None,
        title="t",
        description=desc,
        hours=hours,
        contract="T512_2",
        result=hours,
        raw={},
    )


def _day(d, logs, *, day_id: int = 1) -> WorkdayDay:
    return WorkdayDay(
        id=day_id,
        date=d,
        title="Сотрудник | x",
        employee="1244",
        works_ids=[l.id for l in logs],
        raw={},
        logs=logs,
    )


# --- data_time_range ---


def test_data_time_range_basic():
    days = [
        _day(date(2026, 6, 1), []),
        _day(date(2026, 6, 25), []),
        _day(date(2026, 6, 10), []),
    ]
    mn, mx, cnt = data_time_range(days)
    assert mn == date(2026, 6, 1)
    assert mx == date(2026, 6, 25)
    assert cnt == 3


def test_data_time_range_ignores_none_dates():
    days = [
        _day(None, []),
        _day(date(2026, 6, 5), []),
        _day(None, []),
    ]
    mn, mx, cnt = data_time_range(days)
    assert mn == mx == date(2026, 6, 5)
    assert cnt == 1  # дни без даты не считаются


def test_data_time_range_empty():
    assert data_time_range([]) == (None, None, 0)
    assert data_time_range([_day(None, [])]) == (None, None, 0)


# --- группировка ---


def test_group_rows_aggregates_and_sorts():
    days = [
        _day(date(2026, 6, 1), [_log("Beta", 8), _log("Alpha", 4)]),
        _day(date(2026, 6, 2), [_log("Alpha", 2)]),
        _day(date(2026, 6, 3), [_log("", None)]),  # пустое описание + None часов
    ]
    rows = _group_rows(days)
    # Сортировка по описанию (регистронезависимо): (без описания), Alpha, Beta.
    keys = [r[0] for r in rows]
    assert keys == [NO_DESCRIPTION, "Alpha", "Beta"]

    by_key = {r[0]: r for r in rows}
    # Alpha: 2 записи, 4+2=6 часов, 2 даты.
    assert by_key["Alpha"][1] == 2
    assert by_key["Alpha"][2] == 6
    assert by_key["Alpha"][3] == [date(2026, 6, 1), date(2026, 6, 2)]
    # Beta: 1 запись, 8 часов.
    assert by_key["Beta"][1] == 1
    assert by_key["Beta"][2] == 8
    # (без описания): None часов трактуется как 0.
    assert by_key[NO_DESCRIPTION][1] == 1
    assert by_key[NO_DESCRIPTION][2] == 0


def test_group_total_matches_main_sum():
    days = [
        _day(date(2026, 6, 1), [_log("A", 8), _log("B", 7)]),
        _day(date(2026, 6, 2), [_log("A", 8)]),
        _day(date(2026, 6, 3), []),  # день без учётов — не влияет на часы
    ]
    main_sum = sum((wl.hours or 0.0) for d in days for wl in d.logs)
    group_sum = sum(r[2] for r in _group_rows(days))
    assert main_sum == group_sum == 23


def test_group_dates_deduplicated_per_group():
    # Два учёта одного описания в один день → дата в списке группы один раз.
    days = [_day(date(2026, 6, 1), [_log("A", 4), _log("A", 4)])]
    rows = _group_rows(days)
    assert rows[0][3] == [date(2026, 6, 1)]
    assert rows[0][1] == 2  # но записей всё равно две

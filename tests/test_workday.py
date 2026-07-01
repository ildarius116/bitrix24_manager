"""Оффлайн-тесты агрегатов выгрузки: data_time_range, группировка и select_candidates (без сети).

Синтетические WorkdayDay/WorkLog; проверяем, что:
- рамки дат вычисляются по фактическим датам (без None);
- группировка по «Описанию задачи» считает кол-во/сумму часов/даты корректно;
- сумма часов по группам сходится с суммой по основным строкам;
- select_candidates корректно применяет все фильтры (лимит, дата, окно, заполненность).
"""

from __future__ import annotations

import types
from datetime import date

from src.export_excel import NO_DESCRIPTION, _group_rows
from src.workday import WorkdayDay, WorkLog, data_time_range, select_candidates, select_repair_days

# Фиксированная «сегодня» для детерминированных тестов select_candidates.
TODAY = date(2026, 6, 25)

# Стаб Config: select_candidates/select_repair_days используют edit_window_days и
# day_type_work_ids (whitelist «Типа дня», ЭТАП B1).
_cfg = types.SimpleNamespace(edit_window_days=4, day_type_work_ids=[351])


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


def _day(d, logs, *, day_id: int = 1, day_type_id: int = 351) -> WorkdayDay:
    return WorkdayDay(
        id=day_id,
        date=d,
        title="Сотрудник | x",
        employee="1244",
        works_ids=[l.id for l in logs],
        day_type_id=day_type_id,
        raw={},
        logs=logs,
    )


def _bare_day(d, *, day_id: int = 1, works_ids=None, day_type_id: int = 351) -> WorkdayDay:
    """Создать WorkdayDay без учётов, с явным контролем works_ids и «Типа дня».

    Используется в тестах select_candidates: нужны дни только с датой и
    works_ids, без реальных WorkLog-объектов. day_type_id по умолчанию 351
    («Рабочий день»), чтобы день проходил фильтр «Типа дня» (ЭТАП B1).
    """
    return WorkdayDay(
        id=day_id,
        date=d,
        title="Сотрудник | x",
        employee="1244",
        works_ids=works_ids if works_ids is not None else [],
        day_type_id=day_type_id,
        raw={},
        logs=[],
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


# ---------------------------------------------------------------------------
# select_candidates (Фаза 3, FR-2.1.1–2.1.5)
# today=2026-06-25, edit_window_days=4 → earliest=2026-06-21
# ---------------------------------------------------------------------------


def test_select_candidates_happy_path():
    """Корректный день (в окне, пустой works_ids) проходит и возвращается."""
    day = _bare_day(TODAY, day_id=10)
    result = select_candidates([day], _cfg, TODAY)
    assert result == [day]


def test_select_candidates_boundary_in_window():
    """Граница окна: today − edit_window_days (2026-06-21) ПРОХОДИТ."""
    earliest = TODAY - __import__("datetime").timedelta(days=_cfg.edit_window_days)  # 2026-06-21
    day = _bare_day(earliest, day_id=20)
    result = select_candidates([day], _cfg, TODAY)
    assert len(result) == 1
    assert result[0].id == 20


def test_select_candidates_boundary_outside_window():
    """Граница: today − (edit_window_days + 1) (2026-06-20) НЕ ПРОХОДИТ."""
    from datetime import timedelta
    too_old = TODAY - timedelta(days=_cfg.edit_window_days + 1)  # 2026-06-20
    day = _bare_day(too_old, day_id=30)
    result = select_candidates([day], _cfg, TODAY)
    assert result == []


def test_select_candidates_future_date_excluded():
    """Будущая дата (> today) отсекается (FR-2.1.3 — верхняя граница = today)."""
    from datetime import timedelta
    future = TODAY + timedelta(days=1)
    day = _bare_day(future, day_id=40)
    result = select_candidates([day], _cfg, TODAY)
    assert result == []


def test_select_candidates_no_date_excluded():
    """День с date=None пропускается (FR-2.1.2)."""
    day = _bare_day(None, day_id=50)
    result = select_candidates([day], _cfg, TODAY)
    assert result == []


def test_select_candidates_filled_excluded():
    """День с непустым works_ids отсекается как уже заполненный (FR-2.1.4/5)."""
    day = _bare_day(TODAY, day_id=60, works_ids=[101, 102])
    result = select_candidates([day], _cfg, TODAY)
    assert result == []


def test_select_candidates_empty_works_ids_passes():
    """День с пустым works_ids (явно []) проходит фильтр заполненности."""
    day = _bare_day(TODAY, day_id=70, works_ids=[])
    result = select_candidates([day], _cfg, TODAY)
    assert len(result) == 1


def test_select_candidates_default_limit_5():
    """Дефолтный limit=5: из 7 подходящих дней возвращаются только первые 5 (FR-2.1.1)."""
    # Список уже отсортирован id desc (как read_days гарантирует).
    days = [_bare_day(TODAY, day_id=i) for i in range(100, 107)]  # 7 дней
    result = select_candidates(days, _cfg, TODAY)
    assert len(result) == 5
    # Возвращаются первые 5 (id 100..104), а не 105/106.
    returned_ids = [d.id for d in result]
    assert returned_ids == [100, 101, 102, 103, 104]


def test_select_candidates_explicit_limit():
    """Явный limit=2: из 5 подходящих дней возвращается не более 2."""
    days = [_bare_day(TODAY, day_id=i) for i in range(200, 205)]  # 5 дней
    result = select_candidates(days, _cfg, TODAY, limit=2)
    assert len(result) == 2
    assert [d.id for d in result] == [200, 201]


def test_select_candidates_limit_excludes_beyond_window():
    """Дни за пределами limit не рассматриваются, даже если они подходят по дате."""
    from datetime import timedelta
    # Первые 5 — слишком старые, 6-й — в окне. limit=5 → 6-й даже не проверяется.
    too_old = TODAY - timedelta(days=_cfg.edit_window_days + 1)  # 2026-06-20
    days = [_bare_day(too_old, day_id=i) for i in range(300, 305)]  # 5 за пределами окна
    days.append(_bare_day(TODAY, day_id=305))  # 6-й — в окне, но за limit
    result = select_candidates(days, _cfg, TODAY, limit=5)
    assert result == []


def test_select_candidates_mixed_reasons():
    """Комбинированный сценарий: несколько причин пропуска + один кандидат."""
    from datetime import timedelta
    # Список приходит отсортированным id desc (убывание).
    days = [
        _bare_day(TODAY + timedelta(days=1), day_id=1),        # будущая — пропуск
        _bare_day(TODAY, day_id=2, works_ids=[99]),             # заполнено — пропуск
        _bare_day(None, day_id=3),                              # нет даты — пропуск
        _bare_day(TODAY - timedelta(days=_cfg.edit_window_days + 1), day_id=4),  # старее окна
        _bare_day(TODAY, day_id=5),                             # кандидат
    ]
    result = select_candidates(days, _cfg, TODAY)
    assert len(result) == 1
    assert result[0].id == 5


def test_select_candidates_empty_input():
    """Пустой список дней → пустой результат (без исключений)."""
    result = select_candidates([], _cfg, TODAY)
    assert result == []


def test_select_candidates_all_dates_in_window_all_pass():
    """Все дни в окне, все с пустым works_ids, все в пределах limit — все проходят."""
    from datetime import timedelta
    days = [
        _bare_day(TODAY - timedelta(days=i), day_id=i)
        for i in range(_cfg.edit_window_days + 1)  # 0..4 → даты 25..21
    ]
    result = select_candidates(days, _cfg, TODAY)
    assert len(result) == len(days)  # все 5 проходят


# ---------------------------------------------------------------------------
# select_candidates — фильтр «Типа дня» (ЭТАП B1, whitelist=[351])
# ---------------------------------------------------------------------------


def test_select_candidates_work_day_type_passes():
    """Рабочий день (day_type_id=351) в окне с пустым works — проходит."""
    day = _bare_day(TODAY, day_id=10, day_type_id=351)
    assert select_candidates([day], _cfg, TODAY) == [day]


def test_select_candidates_vacation_excluded():
    """Отпуск (352) — пропускается, даже если день в окне и пустой."""
    day = _bare_day(TODAY, day_id=11, day_type_id=352)
    assert select_candidates([day], _cfg, TODAY) == []


def test_select_candidates_non_work_types_excluded():
    """355 (отгул), 356 (работа в выходной), 357 (удалёнка) — все пропускаются."""
    for dt in (355, 356, 357):
        day = _bare_day(TODAY, day_id=100 + dt, day_type_id=dt)
        assert select_candidates([day], _cfg, TODAY) == [], f"тип {dt} не должен проходить"


def test_select_candidates_unknown_type_excluded():
    """Неизвестный тип (напр. 999, не в whitelist) — пропускается."""
    day = _bare_day(TODAY, day_id=12, day_type_id=999)
    assert select_candidates([day], _cfg, TODAY) == []


def test_select_candidates_empty_type_excluded():
    """Пустой/неизвестный тип (day_type_id=None) — пропускается."""
    day = _bare_day(TODAY, day_id=13, day_type_id=None)
    assert select_candidates([day], _cfg, TODAY) == []


def test_select_candidates_daytype_filter_mixed():
    """Смешанный список: только рабочий день (351) с пустым works попадает в кандидаты."""
    days = [
        _bare_day(TODAY, day_id=1, day_type_id=352),   # отпуск → пропуск
        _bare_day(TODAY, day_id=2, day_type_id=357),   # удалёнка → пропуск
        _bare_day(TODAY, day_id=3, day_type_id=351),   # рабочий → кандидат
        _bare_day(TODAY, day_id=4, day_type_id=None),  # нет типа → пропуск
    ]
    result = select_candidates(days, _cfg, TODAY)
    assert [d.id for d in result] == [3]


# ---------------------------------------------------------------------------
# select_repair_days (FR-2.1.7): зеркало select_candidates, но берёт заполненные дни
# today=2026-06-25, edit_window_days=4 → earliest=2026-06-21
# ---------------------------------------------------------------------------


def test_select_repair_days_returns_filled_day_in_window():
    """Заполненный день в окне (works непуст) → попадает в ремонт."""
    day = _bare_day(TODAY, day_id=1, works_ids=[100])
    result = select_repair_days([day], _cfg, TODAY)
    assert result == [day]


def test_select_repair_days_empty_works_excluded():
    """Пустые works_ids → не ремонт (эти дни берёт select_candidates)."""
    day = _bare_day(TODAY, day_id=2, works_ids=[])
    result = select_repair_days([day], _cfg, TODAY)
    assert result == []


def test_select_repair_days_boundary_today_minus_4_included():
    """Граница today−4 (2026-06-21) ПРОХОДИТ в ремонт (включительно)."""
    from datetime import timedelta
    boundary = TODAY - timedelta(days=_cfg.edit_window_days)  # 2026-06-21
    day = _bare_day(boundary, day_id=3, works_ids=[99])
    result = select_repair_days([day], _cfg, TODAY)
    assert len(result) == 1
    assert result[0].id == 3


def test_select_repair_days_today_minus_5_excluded():
    """today−5 (2026-06-20) НЕ ПРОХОДИТ: вне 4-дневного окна."""
    from datetime import timedelta
    too_old = TODAY - timedelta(days=_cfg.edit_window_days + 1)  # 2026-06-20
    day = _bare_day(too_old, day_id=4, works_ids=[99])
    result = select_repair_days([day], _cfg, TODAY)
    assert result == []


def test_select_repair_days_future_date_excluded():
    """Будущая дата (> today) → исключается (верхняя граница = today)."""
    from datetime import timedelta
    future = TODAY + timedelta(days=1)
    day = _bare_day(future, day_id=5, works_ids=[99])
    result = select_repair_days([day], _cfg, TODAY)
    assert result == []


def test_select_repair_days_no_date_excluded():
    """День без даты (date=None) → исключается."""
    day = _bare_day(None, day_id=6, works_ids=[99])
    result = select_repair_days([day], _cfg, TODAY)
    assert result == []


def test_select_repair_days_limit_respected():
    """limit соблюдается: из 7 подходящих дней берутся только первые limit."""
    days = [_bare_day(TODAY, day_id=200 + i, works_ids=[200 + i]) for i in range(7)]
    result = select_repair_days(days, _cfg, TODAY, limit=3)
    assert len(result) == 3
    assert [d.id for d in result] == [200, 201, 202]


def test_select_repair_days_empty_input():
    """Пустой список дней → пустой результат без исключений."""
    result = select_repair_days([], _cfg, TODAY)
    assert result == []


def test_select_repair_days_mixed_days():
    """Комбинированный сценарий: только заполненные + в окне попадают в ремонт."""
    from datetime import timedelta
    days = [
        _bare_day(TODAY, day_id=1, works_ids=[]),           # пустой → нет
        _bare_day(TODAY, day_id=2, works_ids=[10]),          # заполнен, в окне → ДА
        _bare_day(None, day_id=3, works_ids=[11]),           # нет даты → нет
        _bare_day(TODAY - timedelta(days=_cfg.edit_window_days + 1), day_id=4, works_ids=[12]),  # вне окна → нет
        _bare_day(TODAY, day_id=5, works_ids=[13]),          # заполнен, в окне → ДА
    ]
    result = select_repair_days(days, _cfg, TODAY)
    assert [d.id for d in result] == [2, 5]


def test_select_repair_days_vacation_type_excluded():
    """Ремонт консистентен с whitelist: отпуск (352) с учётом не попадает в ремонт."""
    day = _bare_day(TODAY, day_id=7, works_ids=[70], day_type_id=352)
    assert select_repair_days([day], _cfg, TODAY) == []


# ---------------------------------------------------------------------------
# read_days — серверный фильтр по «Типу дня» (ЭТАП B1) и разбор day_type_id
# ---------------------------------------------------------------------------


class _FakeB24List:
    """Мок B24: фиксирует параметры item_list_all и возвращает заранее заданные items."""

    def __init__(self, items):
        self._items = items
        self.calls = []

    def item_list_all(self, entity_type_id, *, filter=None, select=None, order=None):
        self.calls.append(
            {"entity_type_id": entity_type_id, "filter": filter, "select": select, "order": order}
        )
        return self._items


_DAY_TYPE_FIELD = "ufCrm46_1742341877"

# Стаб Config для read_days: только используемые геттеры/поля.
_read_cfg = types.SimpleNamespace(
    workday_type_id=1208,
    field_workday_date="ufCrm46_1742342657",
    field_workday_works="ufCrm46_1742997115",
    field_workday_employee="ufCrm46_1742341577",
    field_workday_day_type=_DAY_TYPE_FIELD,
    day_type_work_ids=[351],
)


def test_read_days_adds_server_daytype_filter():
    """day_type_ids передан → серверный фильтр {"@<код>": [...]} присутствует в запросе."""
    from src.workday import read_days
    b24 = _FakeB24List(items=[])
    read_days(b24, _read_cfg, date(2026, 6, 21), date(2026, 6, 25), day_type_ids=[351])
    flt = b24.calls[0]["filter"]
    assert flt[f"@{_DAY_TYPE_FIELD}"] == [351]
    # Поле «Типа дня» также попадает в select (для клиентской проверки/аудита).
    assert _DAY_TYPE_FIELD in b24.calls[0]["select"]


def test_read_days_no_daytype_filter_for_export():
    """Без day_type_ids (export) серверного фильтра по типу дня НЕТ, но поле в select есть."""
    from src.workday import read_days
    b24 = _FakeB24List(items=[])
    read_days(b24, _read_cfg, date(2026, 6, 1), date(2026, 6, 25))
    flt = b24.calls[0]["filter"]
    assert f"@{_DAY_TYPE_FIELD}" not in flt
    assert _DAY_TYPE_FIELD in b24.calls[0]["select"]


def test_read_days_parses_day_type_id():
    """day_type_id разбирается из ответа портала (число/строка) в int, пусто → None."""
    from src.workday import read_days
    items = [
        {"id": 1, _DAY_TYPE_FIELD: 351},
        {"id": 2, _DAY_TYPE_FIELD: "352"},
        {"id": 3, _DAY_TYPE_FIELD: ""},
    ]
    b24 = _FakeB24List(items=items)
    days = read_days(b24, _read_cfg, date(2026, 6, 21), date(2026, 6, 25))
    by_id = {d.id: d.day_type_id for d in days}
    assert by_id == {1: 351, 2: 352, 3: None}

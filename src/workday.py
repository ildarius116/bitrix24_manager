"""Чтение «Рабочего дня» (1208) и связанных «Учётов рабочего времени» (1218).

Фаза 2 (Парсинг, FR-1). ТОЛЬКО ЧТЕНИЕ: используются исключительно read-only вызовы
(`crm.item.list`). Никаких add/update/delete — записей в прод нет.

Метамодель и коды полей берутся из `Config` (config.yaml), а не хардкодятся (CLAUDE.md):
- 1208: дата `field_workday_date`, работы `field_workday_works`, сотрудник `field_workday_employee`.
- 1218: описание `field_log_description`, часы `field_log_hours`, договор `field_log_contract`,
  итог `field_log_result`, родитель `field_log_parent`.

Структуры данных:
- `WorkdayDay` — один рабочий день (1208) + список связанных учётов (`logs`).
- `WorkLog` — один учёт рабочего времени (1218).

Поле «Информация о проделанной работе» (UI) — см. константу WORK_INFO_FIELD_KEY ниже и
комментарий о выводе по реальным данным (phase_2_02).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .b24 import B24
from .config import Config
from .dates import entry_date, extract_date

log = logging.getLogger("workday")

# --- Вывод по открытому вопросу phase_2_02 («Информация о проделанной работе») -------------
# Разбор реальной записи 1218 (read-only, портал bitrix.incomsystem.ru, период
# 2026-06-01..2026-06-25, напр. учёт id=156084) дал однозначную картину полей:
#   * title                = «Общие задачи подразделения | 8:00»  ← название задачи + часы
#   * ufCrm48_1744239302   = «AI.12.08, DO.20.16»   ← «Описание задачи» (содержательный ТЕКСТ)
#   * ufCrm48_1742996959   = 8                       ← «Количество часов» (double)
#   * ufCrm48_1743029170   = 8                       ← «Итог» — ЧИСЛО, равно часам (НЕ текст!)
#   * ufCrm48_1742996936   = «T512_2»                ← «Договор» (код договора)
#
# ВЫВОД: «Итог» (ufCrm48_1743029170) — это НЕ «Информация о проделанной работе», а числовой
# дубликат часов. Единственное содержательное текстовое поле о работе — «Описание задачи»
# (ufCrm48_1744239302). Человекочитаемое имя задачи («Общие задачи подразделения») лежит в
# `title` учёта. Поэтому колонку «Информация о проделанной работе» наполняем из «Описания
# задачи»; имя задачи из title учёта выносим отдельно (log_title).
#
# ОТКРЫТЫЙ ВОПРОС К ЗАКАЗЧИКУ: в UI «Информация о проделанной работе» = «Описание задачи»
# (ufCrm48_1744239302) или это имя задачи из title учёта? На данных это два разных значения
# («AI.12.08, DO.20.16» vs «Общие задачи подразделения»). До подтверждения берём «Описание
# задачи»; обе величины доступны (log.description и log.title) — финальный состав колонок
# согласовать.
WORK_INFO_SOURCE = "log_description"  # ufCrm48_1744239302 — «Описание задачи»


def _as_str(value: Any) -> str:
    """Безопасно привести значение поля к строке (None/пусто → '')."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _as_float(value: Any) -> Optional[float]:
    """Привести значение часов к float; None/пусто/мусор → None."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):  # bool — подкласс int, отсекаем явно
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().replace(",", ".")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _id_list(value: Any) -> List[int]:
    """Нормализовать значение crm[]-поля (работы) к списку int ID.

    Bitrix может отдавать массив строк/чисел, одиночное значение или None.
    """
    if value is None or value == "":
        return []
    raw_items = value if isinstance(value, (list, tuple)) else [value]
    result: List[int] = []
    for item in raw_items:
        if item is None or item == "":
            continue
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


@dataclass
class WorkLog:
    """Учёт рабочего времени (1218) — дочерний к рабочему дню.

    Поля извлекаются по кодам из Config; raw хранит исходный словарь записи.
    """

    id: int
    parent_day_id: Optional[int]
    title: str                # title учёта («Общие задачи подразделения | 8:00»)
    description: str          # ufCrm48_1744239302 (Описание задачи) — текст о работе
    hours: Optional[float]    # ufCrm48_1742996959 (Количество часов)
    contract: str             # ufCrm48_1742996936 (Договор)
    result: Optional[float]   # ufCrm48_1743029170 (Итог) — число, дубликат часов (не текст)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def work_info(self) -> str:
        """Текст для колонки «Информация о проделанной работе».

        Источник — «Описание задачи» (ufCrm48_1744239302): единственное содержательное
        текстовое поле о работе. «Итог» — числовой дубликат часов, для текста непригоден
        (см. WORK_INFO_SOURCE и разбор выше). Финальный состав согласовать с заказчиком.
        """
        return self.description

    @classmethod
    def from_item(cls, item: Dict[str, Any], config: Config) -> "WorkLog":
        try:
            log_id = int(item.get("id") or item.get("ID") or 0)
        except (TypeError, ValueError):
            log_id = 0
        parent_raw = item.get(config.field_log_parent)
        parent_id: Optional[int]
        try:
            parent_id = int(parent_raw) if parent_raw not in (None, "") else None
        except (TypeError, ValueError):
            parent_id = None
        return cls(
            id=log_id,
            parent_day_id=parent_id,
            title=_as_str(item.get("title") or item.get("TITLE")),
            description=_as_str(item.get(config.field_log_description)),
            hours=_as_float(item.get(config.field_log_hours)),
            contract=_as_str(item.get(config.field_log_contract)),
            result=_as_float(item.get(config.field_log_result)),
            raw=item,
        )


@dataclass
class WorkdayDay:
    """Рабочий день (1208) + связанные учёты (1218)."""

    id: int
    date: Optional[date]
    title: str
    employee: str
    works_ids: List[int]
    raw: Dict[str, Any] = field(default_factory=dict)
    logs: List[WorkLog] = field(default_factory=list)

    @classmethod
    def from_item(cls, item: Dict[str, Any], config: Config) -> "WorkdayDay":
        try:
            day_id = int(item.get("id") or item.get("ID") or 0)
        except (TypeError, ValueError):
            day_id = 0
        title = _as_str(item.get("title") or item.get("TITLE"))
        # Дата дня: надёжный источник — поле даты; запасной — разбор из title.
        d = entry_date(item, date_field=config.field_workday_date)
        if d is None:
            d = extract_date(title)
        return cls(
            id=day_id,
            date=d,
            title=title,
            employee=_as_str(item.get(config.field_workday_employee)),
            works_ids=_id_list(item.get(config.field_workday_works)),
            raw=item,
        )


def read_days(b24: B24, config: Config, date_from: date, date_to: date) -> List[WorkdayDay]:
    """Прочитать все дни «Рабочий день» (1208) за период [date_from; date_to].

    Фильтр по полю даты (>=/<=), сортировка id desc, ПОЛНАЯ пагинация (item_list_all).
    Коды полей — из Config. Возвращает список WorkdayDay (без учётов; их грузит read_logs).
    """
    date_field = config.field_workday_date
    flt = {
        f">={date_field}": date_from.isoformat(),
        f"<={date_field}": date_to.isoformat(),
    }
    select = [
        "id",
        "title",
        date_field,
        config.field_workday_works,
        config.field_workday_employee,
    ]
    items = b24.item_list_all(
        config.workday_type_id,
        filter=flt,
        select=select,
        order={"id": "desc"},
    )
    days = [WorkdayDay.from_item(it, config) for it in items]
    log.info(
        "Прочитано дней «Рабочий день» (1208) за %s … %s: %d",
        date_from.isoformat(),
        date_to.isoformat(),
        len(days),
    )
    return days


def _chunks(seq: List[int], size: int) -> List[List[int]]:
    """Разбить список на чанки фиксированного размера (для filter {"@id":[...]})."""
    return [seq[i : i + size] for i in range(0, len(seq), size)]


def read_logs(b24: B24, config: Config, days: List[WorkdayDay]) -> List[WorkLog]:
    """Прочитать связанные учёты (1218) для выгруженных дней и привязать к дням.

    Собираем все ID из works_ids по дням, читаем 1218 через crm.item.list
    filter={"@id":[...]} чанками (защита от больших объёмов / лимита длины фильтра).
    Заполняем `day.logs`. Возвращает плоский список всех учётов.
    ТОЛЬКО ЧТЕНИЕ.
    """
    all_ids: List[int] = []
    seen = set()
    for day in days:
        for wid in day.works_ids:
            if wid not in seen:
                seen.add(wid)
                all_ids.append(wid)

    if not all_ids:
        log.info("Связанных учётов (1218) нет: ни у одного дня не заполнены «Работы за день».")
        for day in days:
            day.logs = []
        return []

    select = [
        "id",
        "title",
        config.field_log_parent,
        config.field_log_description,
        config.field_log_result,
        config.field_log_hours,
        config.field_log_contract,
    ]

    logs_by_id: Dict[int, WorkLog] = {}
    for chunk in _chunks(all_ids, 50):
        items = b24.item_list_all(
            config.timelog_type_id,
            filter={"@id": chunk},
            select=select,
        )
        for it in items:
            wl = WorkLog.from_item(it, config)
            if wl.id:
                logs_by_id[wl.id] = wl

    # Привязка учётов к дням по works_ids (порядок — как в массиве works_ids дня).
    all_logs: List[WorkLog] = []
    for day in days:
        day_logs = [logs_by_id[wid] for wid in day.works_ids if wid in logs_by_id]
        day.logs = day_logs
        all_logs.extend(day_logs)

    missing = len(all_ids) - len(logs_by_id)
    if missing > 0:
        log.warning(
            "Не удалось прочитать %d из %d связанных учётов (1218) — возможно, удалены "
            "или недоступны.",
            missing,
            len(all_ids),
        )
    log.info(
        "Прочитано учётов (1218): %d (по %d дням с заполненными работами).",
        len(logs_by_id),
        sum(1 for d in days if d.works_ids),
    )
    return all_logs


def data_time_range(days: List[WorkdayDay]) -> Tuple[Optional[date], Optional[date], int]:
    """Фактические временные рамки выгрузки.

    Возвращает (min_date, max_date, число_дней_с_датой). Дни без распознанной даты
    в рамки не входят. Если дат нет вовсе → (None, None, 0).
    """
    dated = [d.date for d in days if d.date is not None]
    if not dated:
        return None, None, 0
    return min(dated), max(dated), len(dated)


def select_candidates(
    days: List[WorkdayDay],
    cfg: Config,
    today: date,
    *,
    limit: int = 5,
) -> List[WorkdayDay]:
    """Отобрать дни «Рабочий день», которым нужно создать учёт (FR-2.1.1–2.1.5).

    Алгоритм (порядок фильтров):
    1. Рассматриваем только первые `limit` дней (топ по убыванию id, FR-2.1.1).
       Вызывающий обязан передать список, уже отсортированный id desc (read_days это гарантирует).
    2. Дни без распознанной даты (day.date is None) — пропуск «нет даты» (FR-2.1.2).
    3. 4-дневное окно (FR-2.1.3): оставить только дни, у которых
       today − cfg.edit_window_days <= day.date <= today (включительно с обеих сторон;
       будущие даты > today тоже отсекаются). Семантика совпадает с within_edit_window из dates.py.
    4. Пустота (FR-2.1.4/5): оставить только дни с пустым works_ids — значит учётов ещё нет.

    Каждый пропущенный день логируется через log.info с понятной причиной на русском.
    Каждый отобранный кандидат тоже логируется.

    Параметры:
        days    — список WorkdayDay, отсортированный id desc (обычно из read_days).
        cfg     — конфигурация; используется cfg.edit_window_days.
        today   — «сегодня» в таймзоне Europe/Moscow (передаётся вызывающим через today_moscow()).
        limit   — сколько верхних дней рассматривать (FR-2.1.1, дефолт = 5).

    Возвращает список отобранных WorkdayDay (может быть пустым).
    """
    earliest: date = today - timedelta(days=cfg.edit_window_days)
    candidates: List[WorkdayDay] = []

    for day in days[:limit]:
        # FR-2.1.2: дата не распознана.
        if day.date is None:
            log.info(
                "День id=%d пропущен: нет даты (title=%r).",
                day.id,
                day.title,
            )
            continue

        # FR-2.1.3: вне окна редактирования.
        if not (earliest <= day.date <= today):
            log.info(
                "День id=%d пропущен: вне окна редактирования (дата %s, окно с %s по %s).",
                day.id,
                day.date.isoformat(),
                earliest.isoformat(),
                today.isoformat(),
            )
            continue

        # FR-2.1.4/5: учёты уже заполнены.
        if day.works_ids:
            log.info(
                "День id=%d пропущен: уже заполнено (%d учётов), дата %s.",
                day.id,
                len(day.works_ids),
                day.date.isoformat(),
            )
            continue

        log.info(
            "День id=%d отобран как кандидат на заполнение (дата %s).",
            day.id,
            day.date.isoformat(),
        )
        candidates.append(day)

    log.info(
        "Отбор кандидатов завершён: рассмотрено %d из %d дней (limit=%d), "
        "отобрано кандидатов: %d.",
        min(limit, len(days)),
        len(days),
        limit,
        len(candidates),
    )
    return candidates


def select_repair_days(
    days: List[WorkdayDay],
    cfg: Config,
    today: date,
    *,
    limit: int = 5,
) -> List[WorkdayDay]:
    """Отобрать дни для «ремонта» закрытия дела (FR-2.1.7): учёт ЕСТЬ, но день мог не закрыться.

    Зеркало select_candidates по первым `limit` дням (топ id desc), с теми же фильтрами
    «есть дата» и «в окне 4 дней», НО с обратным условием по работам: берём дни, у которых
    works_ids НЕ пуст (именно их select_candidates отбрасывает шагом «уже заполнено»).

    Таким дням учёт 1218 создавать НЕ нужно — но открытое CRM-дело «Заполнить работы по
    договорам» на карточке могло остаться (как у наблюдённого дня 271557). «Ремонт»-проход
    в run_fill завершает такие дела (crm.activity.update). Сам отбор — без сети.

    Возвращает список WorkdayDay (может быть пустым).
    """
    earliest: date = today - timedelta(days=cfg.edit_window_days)
    repair: List[WorkdayDay] = []

    for day in days[:limit]:
        if day.date is None:
            continue
        if not (earliest <= day.date <= today):
            continue
        # Только уже заполненные дни (works_ids непуст) — те, кого создание учёта пропускает.
        if not day.works_ids:
            continue
        log.info(
            "День id=%d отобран для «ремонта» закрытия дела (дата %s, учётов %d).",
            day.id,
            day.date.isoformat(),
            len(day.works_ids),
        )
        repair.append(day)

    log.info(
        "Отбор дней для «ремонта» завершён: рассмотрено %d из %d дней (limit=%d), "
        "отобрано: %d.",
        min(limit, len(days)),
        len(days),
        limit,
        len(repair),
    )
    return repair

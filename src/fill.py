"""Write-пайплайн «Работы/задачи за день»: создание учётов рабочего времени (1218).

Фаза 4 (FR-2.1.6–2.1.11). Реализует подфазы:
- 4_02 `collect_values` — сбор значений (дефолт + интерактивное подтверждение);
- 4_03 `build_payload`/`create_log` — формирование payload и создание 1218 (plan→execute);
- 4_04 `verify_log` — верификация привязки/значений перечитыванием;
- оркестрация `run_fill` со сводной таблицей итогов.

БЕЗОПАСНОСТЬ (CLAUDE.md §5):
- По умолчанию dry-run. Реальная запись (`crm.item.add` без plan_only) выполняется только при
  `dry_run=False`, который main.py включает лишь при явном `--confirm-write`.
- Перед каждой записью — ГАРД: перечитать день, заново проверить 4-дневное окно и пустоту
  «Работ за день» (идемпотентность, защита от гонки и повторного запуска).
- Коды полей/entityTypeId/категории — из Config (config.yaml), не хардкодим.
- Код вебхука НЕ логируется (маскирование уже в транспорте); payload секрета не содержит.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Union

from .b24 import B24, B24Error
from .config import Config
from .dates import today_moscow, within_edit_window
from .journal import DEFAULT_JOURNAL_PATH, is_processed, load_journal, mark_processed
from .workday import WorkdayDay, _id_list

log = logging.getLogger("workday")


# --- Сигналы сбора значений (4_02) -----------------------------------------------------------

@dataclass(frozen=True)
class Values:
    """Принятые значения для нового учёта: описание задачи и количество часов."""

    description: str
    hours: float


class _Skip:
    """Сигнал «пропустить этот день» (skip)."""

    __slots__ = ()


class _Abort:
    """Сигнал «прервать весь пакет» (abort)."""

    __slots__ = ()


SKIP = _Skip()
ABORT = _Abort()

# Результат collect_values: значения, либо сигнал SKIP/ABORT.
CollectResult = Union[Values, _Skip, _Abort]


class AbortFill(Exception):
    """Прерывание всего пакета пользователем (abort)."""


def _parse_hours(raw: str) -> Optional[float]:
    """Разобрать строку часов в положительный float. None — если невалидно (<=0 или мусор)."""
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def collect_values(
    day: WorkdayDay,
    cfg: Config,
    *,
    interaction: bool,
    applied_to_all: Optional[Values] = None,
) -> CollectResult:
    """Собрать значения (описание, часы) для дня (FR-2.1.10.2/10.3, §6).

    Дефолты — из cfg.defaults (task_description, hours).

    Если ранее выбрано «применить ко всем» (`applied_to_all` задан) — вернуть эти значения
    без вопросов (даже в интерактиве).

    `interaction=False` (--no-interaction): вернуть дефолты без вопросов, с валидацией часов
    дефолта (если в config задан некорректный hours <= 0 — это ошибка конфигурации).

    `interaction=True`: по дню показать предложенные значения и принять ввод:
      - описание: Enter — принять предложенное; иной текст — переопределить;
        `skip` — пропустить день (SKIP); `abort` — прервать пакет (ABORT);
        `all` — применить дефолты ко всем оставшимся (вернуть Values, оркестратор запомнит).
      - часы: Enter — принять; число > 0 — переопределить; невалид — переспросить;
        `skip`/`abort` — соответствующий сигнал.

    Ввод читается через input() — тестируется monkeypatch на stdin.
    Возвращает Values | SKIP | ABORT.
    """
    if applied_to_all is not None:
        log.info("День id=%d: применяю ранее выбранные значения «ко всем».", day.id)
        return applied_to_all

    default_desc = str(cfg.defaults.get("task_description", ""))
    default_hours_raw = cfg.defaults.get("hours", 0)
    default_hours = _parse_hours(str(default_hours_raw))

    if not interaction:
        if default_hours is None:
            raise ValueError(
                f"Некорректное дефолтное значение часов в config.yaml: {default_hours_raw!r} "
                "(ожидается число > 0)."
            )
        return Values(description=default_desc, hours=default_hours)

    # --- Интерактивный режим ---
    date_str = day.date.strftime("%d.%m.%Y") if day.date else "(нет даты)"
    print()
    print(f"=== День id={day.id} | {date_str} | {day.title} ===")
    print(f"  Предлагаемое описание: {default_desc!r}")
    print(f"  Предлагаемые часы:     {default_hours}")
    print("  Ввод: Enter — принять, текст — переопределить, "
          "skip — пропустить, abort — прервать всё, all — применить дефолт ко всем.")

    # 1) Описание (+ перехват служебных команд).
    raw_desc = input("  Описание задачи [Enter=дефолт]: ").strip()
    low = raw_desc.lower()
    if low == "skip":
        return SKIP
    if low == "abort":
        return ABORT
    if low == "all":
        if default_hours is None:
            raise ValueError("Некорректные дефолтные часы в config.yaml для режима «ко всем».")
        return Values(description=default_desc, hours=default_hours)
    description = raw_desc if raw_desc else default_desc

    # 2) Часы (с переспросом при невалидном вводе).
    hours: Optional[float] = default_hours
    while True:
        raw_hours = input(f"  Количество часов [Enter={default_hours}]: ").strip()
        low_h = raw_hours.lower()
        if low_h == "skip":
            return SKIP
        if low_h == "abort":
            return ABORT
        if not raw_hours:
            if default_hours is None:
                print("  ! Дефолтные часы некорректны (config.yaml). Введите число > 0.")
                continue
            hours = default_hours
            break
        parsed = _parse_hours(raw_hours)
        if parsed is None:
            print("  ! Некорректные часы: нужно число > 0. Повторите.")
            continue
        hours = parsed
        break

    return Values(description=description, hours=float(hours))


# --- Формирование payload (4_03, чистая функция) ---------------------------------------------

def build_payload(day: WorkdayDay, description: str, hours: float, cfg: Config) -> Dict[str, Any]:
    """Собрать `fields` для crm.item.add 1218 (чистая функция, без сети).

    Состав (план phase_4_01, коды/категория/техкод — из Config):
      parentId1208           = ID дня (cfg.field_log_parent)
      categoryId             = воронка «Работы/задачи по договорам» (cfg.timelog_category_id)
      <log_contract>         = код договора «Общие задачи подразделения» (cfg.contract_general_tasks)
      <log_contract_tech>    = [тех] ID договора (cfg.contract_tech_id)
      <log_description>      = описание задачи
      <log_hours>            = количество часов

    НЕ задаём: ufCrm46_1742997115 у дня (создаётся авто), «Итог»/stageId (см. phase_4_01).
    """
    return {
        cfg.field_log_parent: day.id,
        "categoryId": cfg.timelog_category_id,
        cfg.field_log_contract: cfg.contract_general_tasks,
        cfg.field_log_contract_tech: cfg.contract_tech_id,
        cfg.field_log_description: description,
        cfg.field_log_hours: hours,
    }


# --- Гард идемпотентности перед записью (4_03 / 5_01) ----------------------------------------

def _reread_guard(b24: B24, day: WorkdayDay, cfg: Config, today: date) -> Optional[str]:
    """Перечитать день и проверить готовность к записи (окно + пустота).

    Возвращает None, если писать можно; иначе строку-причину пропуска.
    Любая ошибка чтения трактуется как «нельзя писать» (безопасный отказ).

    Окно перепроверяется по свежей дате Europe/Moscow на момент записи
    (защита от перехода через полночь). Параметр `today` сохранён в сигнатуре
    для совместимости с тестами (monkeypatch «src.fill.today_moscow»), но фактически
    для определения окна используется today_moscow() — вызов в теле функции.
    """
    select = ["id", cfg.field_workday_date, cfg.field_workday_works]
    try:
        fresh = b24.item_get(cfg.workday_type_id, day.id, select=select)
    except B24Error as exc:
        return f"не удалось перечитать день перед записью: {exc}"
    if not fresh:
        return "день не найден при перечитывании перед записью"

    # 4-дневное окно по свежим данным: берём актуальную дату МСК на момент записи.
    effective_today = today_moscow()
    if not within_edit_window(
        fresh, cfg.edit_window_days, today=effective_today, date_field=cfg.field_workday_date
    ):
        return "дата вне 4-дневного окна (повторная проверка перед записью)"

    # Пустота «Работ за день» (идемпотентность — день мог быть заполнен параллельно).
    works = _id_list(fresh.get(cfg.field_workday_works))
    if works:
        return f"день уже заполнен ({len(works)} учётов) — повторная запись не нужна"

    return None


# --- Гард окна для «ремонта» закрытия дела (FR-2.1.7) ----------------------------------------

def _window_guard(b24: B24, day: WorkdayDay, cfg: Config) -> Optional[str]:
    """Лёгкий гард 4-дневного окна перед завершением дела дня (без проверки пустоты works).

    Отличие от `_reread_guard`: НЕ требует пустоты «Работ за день» — завершать дело нужно и
    у уже заполненных дней («ремонт»). Перечитываем день (id + дата), пересчитываем актуальную
    дату МСК и проверяем окно. Любая ошибка/отсутствие дня → строка-причина (безопасный отказ).

    Возвращает None, если завершать дела можно; иначе строку-причину пропуска.
    """
    select = ["id", cfg.field_workday_date]
    try:
        fresh = b24.item_get(cfg.workday_type_id, day.id, select=select)
    except B24Error as exc:
        return f"не удалось перечитать день перед завершением дела: {exc}"
    if not fresh:
        return "день не найден при перечитывании перед завершением дела"

    effective_today = today_moscow()
    if not within_edit_window(
        fresh, cfg.edit_window_days, today=effective_today, date_field=cfg.field_workday_date
    ):
        return "дата вне 4-дневного окна (проверка перед завершением дела)"

    return None


# --- Завершение CRM-дел дня «Выполнено» (FR-2.1.7) -------------------------------------------

# Поля дела для select crm.activity.list (ВЕРХНИЙ регистр — так отдаёт API).
_ACTIVITY_SELECT = [
    "ID", "SUBJECT", "COMPLETED", "PROVIDER_ID",
    "RESPONSIBLE_ID", "OWNER_ID", "OWNER_TYPE_ID",
]


def _activity_id(item: Dict[str, Any]) -> Optional[int]:
    """Достать ID дела из словаря crm.activity.list (ключ "ID"/"id"). None — если не разобрать."""
    raw = item.get("ID") if item.get("ID") not in (None, "") else item.get("id")
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def complete_day_activities(
    b24: B24,
    day: WorkdayDay,
    cfg: Config,
    *,
    dry_run: bool,
) -> Dict[str, Any]:
    """Завершить открытые CRM-дела «Заполнить работы по договорам» на дне (кнопка «Выполнено»).

    Алгоритм (FR-2.1.7):
      1. Гард 4-дневного окна (_window_guard); если вне окна/нет дня → status="skipped".
      2. crm.activity.list открытых дел дня (PROVIDER_ID=cfg.activity_provider_id, COMPLETED=N).
         Любая B24Error → status="error".
      3. Нет открытых дел → status="no-activity" (день уже закрыт / дела нет).
      4. dry-run → status="dry-run" + список activity_ids (ничего не пишем).
      5. Боевой режим → crm.activity.update COMPLETED=Y по каждому делу. При любой ошибке —
         status="error" (с собранными причинами), иначе status="completed".

    Идемпотентность: фильтр COMPLETED=N не возвращает уже закрытые дела, поэтому повторный
    запуск безопасен. Завершаем ВСЕ найденные дела (на случай дублей).

    Возвращает словарь {status, ...}; в лог — id дня и количество дел (без значений/секретов).
    """
    reason = _window_guard(b24, day, cfg)
    if reason is not None:
        log.info("День id=%d: завершение дела пропущено — %s.", day.id, reason)
        return {"status": "skipped", "reason": reason}

    try:
        open_acts = b24.activity_list(
            cfg.workday_type_id,
            day.id,
            provider_id=cfg.activity_provider_id,
            only_open=True,
            select=_ACTIVITY_SELECT,
        )
    except B24Error as exc:
        log.error("День id=%d: ошибка чтения дел (crm.activity.list): %s", day.id, exc)
        return {"status": "error", "reason": f"не удалось прочитать дела дня: {exc}"}

    if not open_acts:
        log.info("День id=%d: открытых дел «%s» нет — закрывать нечего.",
                 day.id, cfg.activity_provider_id)
        return {"status": "no-activity"}

    activity_ids: List[int] = []
    for item in open_acts:
        aid = _activity_id(item)
        if aid is not None:
            activity_ids.append(aid)

    if not activity_ids:
        # Не логируем содержимое дел (значения полей) — только число и набор ключей.
        log.error(
            "День id=%d: найдено дел %d, но без корректного ID (ключи: %s).",
            day.id, len(open_acts), [sorted(it.keys()) for it in open_acts],
        )
        return {"status": "error", "reason": "дела найдены, но без корректного ID"}

    if dry_run:
        log.info(
            "DRY-RUN: план crm.activity.update COMPLETED=Y для дня id=%d, дел=%d (id %s) — "
            "запись НЕ выполнена.",
            day.id, len(activity_ids), activity_ids,
        )
        return {"status": "dry-run", "activity_ids": activity_ids}

    # Боевой режим: завершаем все найденные открытые дела.
    problems: List[str] = []
    for aid in activity_ids:
        try:
            b24.activity_complete(aid, plan_only=False)
        except B24Error as exc:
            log.error("День id=%d: ошибка завершения дела id=%d: %s", day.id, aid, exc)
            problems.append(f"дело id={aid}: {exc}")
    if problems:
        return {
            "status": "error",
            "reason": "; ".join(problems),
            "activity_ids": activity_ids,
        }

    log.info("День id=%d: завершено дел «Выполнено»: %d (id %s).",
             day.id, len(activity_ids), activity_ids)
    return {"status": "completed", "activity_ids": activity_ids}


def verify_activities_closed(b24: B24, day_id: int, cfg: Config) -> Dict[str, Any]:
    """Подтвердить, что у дня не осталось открытых дел «Заполнить работы по договорам».

    Перечитывает открытые дела (COMPLETED=N) и возвращает {ok, open_ids, day_id}.
    ok=True, если открытых дел не осталось. Ошибку чтения трактуем как «не подтверждено».
    """
    try:
        open_acts = b24.activity_list(
            cfg.workday_type_id,
            day_id,
            provider_id=cfg.activity_provider_id,
            only_open=True,
            select=_ACTIVITY_SELECT,
        )
    except B24Error as exc:
        log.error("День id=%d: не удалось проверить закрытие дел: %s", day_id, exc)
        return {"ok": False, "open_ids": [], "day_id": day_id, "reason": str(exc)}

    open_ids = [aid for aid in (_activity_id(it) for it in open_acts) if aid is not None]
    ok = not open_ids
    if not ok:
        log.error("День id=%d: остались открытые дела после завершения: %s", day_id, open_ids)
    return {"ok": ok, "open_ids": open_ids, "day_id": day_id}


# --- Создание учёта (4_03) -------------------------------------------------------------------

def create_log(
    b24: B24,
    day: WorkdayDay,
    description: str,
    hours: float,
    cfg: Config,
    *,
    dry_run: bool,
    today: date,
) -> Dict[str, Any]:
    """Создать учёт 1218 под днём (plan→execute) с гардом идемпотентности.

    Возвращает словарь результата:
      {status: "dry-run", payload: {...}}         — план показан, ничего не записано;
      {status: "skipped", reason: "..."}          — гард не пропустил запись;
      {status: "filled",  new_id: int, payload}   — учёт создан (боевой режим);
      {status: "error",   reason: "..."}          — ошибка API/ответа.
    """
    # ГАРД перед ЛЮБОЙ записью (в т.ч. перед dry-run plan, чтобы не показывать невалидный план).
    reason = _reread_guard(b24, day, cfg, today)
    if reason is not None:
        log.info("День id=%d: запись пропущена — %s.", day.id, reason)
        return {"status": "skipped", "reason": reason}

    payload = build_payload(day, description, hours, cfg)

    if dry_run:
        plan = b24.item_add(cfg.timelog_type_id, payload, plan_only=True)
        log.info(
            "DRY-RUN: план crm.item.add 1218 для дня id=%d: parent=%s, договор=%s, часы=%s, "
            "описание=%r (запись НЕ выполнена).",
            day.id,
            payload.get(cfg.field_log_parent),
            payload.get(cfg.field_log_contract),
            payload.get(cfg.field_log_hours),
            payload.get(cfg.field_log_description),
        )
        return {"status": "dry-run", "payload": plan.get("params", {}).get("fields", payload)}

    # Боевой режим.
    try:
        created = b24.item_add(cfg.timelog_type_id, payload, plan_only=False)
    except B24Error as exc:
        log.error("День id=%d: ошибка создания учёта 1218: %s", day.id, exc)
        return {"status": "error", "reason": str(exc)}

    new_id_raw = created.get("id") or created.get("ID")
    try:
        new_id = int(new_id_raw)
    except (TypeError, ValueError):
        log.error("День id=%d: ответ add без корректного id нового учёта: %r", day.id, created)
        return {"status": "error", "reason": "ответ crm.item.add без id нового учёта"}

    log.info("День id=%d: создан учёт 1218 id=%d.", day.id, new_id)
    return {"status": "filled", "new_id": new_id, "payload": payload}


# --- Верификация (4_04) ----------------------------------------------------------------------

def verify_log(
    b24: B24,
    day_id: int,
    new_log_id: int,
    description: str,
    hours: float,
    cfg: Config,
) -> Dict[str, Any]:
    """Подтвердить привязку и значения созданного учёта перечитыванием (FR-2.1.11).

    Проверки:
      1) день 1208: new_log_id ∈ ufCrm46_1742997115 (двусторонняя связь создана ядром);
      2) учёт 1218: parentId1208 == day_id, описание и часы совпадают с записанными.

    Возвращает {ok: bool, problems: [..], day_id, new_log_id}. Расхождения логируются как ошибки.
    """
    problems: List[str] = []

    # 1) День — связь.
    try:
        day_item = b24.item_get(
            cfg.workday_type_id, day_id, select=["id", cfg.field_workday_works]
        )
    except B24Error as exc:
        problems.append(f"не удалось перечитать день: {exc}")
        day_item = None
    if day_item is not None:
        works = _id_list(day_item.get(cfg.field_workday_works))
        if new_log_id not in works:
            problems.append(
                f"учёт id={new_log_id} не найден в «Работах за день» дня (works={works})"
            )

    # 2) Учёт — поля.
    try:
        log_item = b24.item_get(
            cfg.timelog_type_id,
            new_log_id,
            select=[
                "id",
                cfg.field_log_parent,
                cfg.field_log_description,
                cfg.field_log_hours,
            ],
        )
    except B24Error as exc:
        problems.append(f"не удалось перечитать учёт: {exc}")
        log_item = None
    if log_item is not None:
        parent_raw = log_item.get(cfg.field_log_parent)
        try:
            parent = int(parent_raw) if parent_raw not in (None, "") else None
        except (TypeError, ValueError):
            parent = None
        if parent != day_id:
            problems.append(f"parentId1208={parent!r} не совпадает с днём id={day_id}")

        got_desc = str(log_item.get(cfg.field_log_description) or "").strip()
        if got_desc != description.strip():
            problems.append(f"описание расходится: ожидалось {description!r}, получено {got_desc!r}")

        got_hours_raw = log_item.get(cfg.field_log_hours)
        try:
            got_hours = float(str(got_hours_raw).replace(",", "."))
        except (TypeError, ValueError):
            got_hours = None
        if got_hours is None or abs(got_hours - float(hours)) > 1e-6:
            problems.append(f"часы расходятся: ожидалось {hours}, получено {got_hours_raw!r}")

    ok = not problems
    if ok:
        log.info("Верификация дня id=%d / учёта id=%d: OK.", day_id, new_log_id)
    else:
        for p in problems:
            log.error("Верификация дня id=%d / учёта id=%d: %s", day_id, new_log_id, p)

    return {"ok": ok, "problems": problems, "day_id": day_id, "new_log_id": new_log_id}


# --- Оркестрация (4_02–4_04) -----------------------------------------------------------------

def _result_row(day: WorkdayDay, status: str, **extra: Any) -> Dict[str, Any]:
    """Собрать строку результата по дню для сводки."""
    row: Dict[str, Any] = {
        "day_id": day.id,
        "date": day.date.isoformat() if day.date else "",
        "status": status,
    }
    row.update(extra)
    return row


def _finalize_activity(
    b24: B24, day: WorkdayDay, cfg: Config, outcome: Dict[str, Any]
) -> Dict[str, Any]:
    """По результату complete_day_activities собрать поля строки результата (для сводки/кода).

    Возвращает {activity_status, activity_ids, activity_ok}. activity_ok:
      True  — закрывать было нечего (no-activity), показан план (dry-run), либо закрыто и
              подтверждено перечитыванием (completed → verify_activities_closed.ok);
      False — skipped/error, либо после завершения остались открытые дела (verify не прошёл).
    """
    a_status = outcome["status"]
    a_ids = outcome.get("activity_ids", [])
    if a_status == "completed":
        ver = verify_activities_closed(b24, day.id, cfg)
        return {"activity_status": a_status, "activity_ids": a_ids, "activity_ok": ver["ok"]}
    if a_status in ("no-activity", "dry-run"):
        return {"activity_status": a_status, "activity_ids": a_ids, "activity_ok": True}
    # skipped / error — завершение не выполнено, считаем проблемой для сводки и кода возврата.
    return {"activity_status": a_status, "activity_ids": a_ids, "activity_ok": False}


def _activity_detail(row: Dict[str, Any]) -> str:
    """Краткое описание состояния дела для строки сводки (или '' если дела не трогали)."""
    a_status = row.get("activity_status")
    if not a_status:
        return ""
    ids = row.get("activity_ids") or []
    human = {
        "completed": "дело закрыто",
        "no-activity": "дел нет",
        "dry-run": "план закрытия дела",
        "skipped": "закрытие пропущено",
        "error": "ОШИБКА закрытия дела",
    }.get(a_status, a_status)
    suffix = f" (id {ids})" if ids else ""
    text = f"{human}{suffix}"
    if row.get("activity_ok") is False:
        text += "; ДЕЛО НЕ ЗАКРЫТО"
    return text


# Статус строки результата «ремонта» по статусу complete_day_activities.
_REPAIR_STATUS = {
    "completed": "repaired",
    "no-activity": "already-closed",
    "dry-run": "dry-run",
    "skipped": "skipped",
    "error": "error",
}


def _print_summary(results: List[Dict[str, Any]], *, dry_run: bool) -> None:
    """Напечатать сводную таблицу итогов и счётчики (создание + отдельный блок «ремонта»)."""
    counts: Dict[str, int] = {}
    create_rows = [r for r in results if not r.get("repair")]
    repair_rows = [r for r in results if r.get("repair")]

    log.info("=== Сводка fill (%s) ===", "DRY-RUN" if dry_run else "БОЕВОЙ РЕЖИМ")
    for row in create_rows:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
        detail = ""
        if "new_id" in row:
            detail = f"новый учёт id={row['new_id']}"
        elif "reason" in row and row["reason"]:
            detail = str(row["reason"])
        if row.get("verify_ok") is False:
            detail = (detail + "; " if detail else "") + "ВЕРИФИКАЦИЯ НЕ ПРОЙДЕНА"
        act = _activity_detail(row)
        if act:
            detail = (detail + "; " if detail else "") + act
        log.info(
            "  день id=%s | %s | %-8s | %s",
            row["day_id"],
            row["date"] or "—",
            status,
            detail or "—",
        )

    if repair_rows:
        log.info("--- «Ремонт» закрытия дел (учёт есть, дело могло остаться открытым) ---")
        for row in repair_rows:
            status = row["status"]
            counts[status] = counts.get(status, 0) + 1
            detail = str(row.get("reason") or "")
            act = _activity_detail(row)
            if act:
                detail = (detail + "; " if detail else "") + act
            log.info(
                "  день id=%s | %s | %-13s | %s",
                row["day_id"],
                row["date"] or "—",
                status,
                detail or "—",
            )

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "нет кандидатов"
    log.info("Итого: %s.", summary)


def run_fill(
    b24: B24,
    cfg: Config,
    *,
    dry_run: bool,
    interaction: bool,
    today: date,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Оркестрация автозаполнения: отбор → сбор значений → создание → верификация → сводка.

    Читаем дни шире окна (today − (edit_window_days + 3) … today), чтобы select_candidates
    корректно взял топ-`limit` по id desc, затем отбираем кандидатов (окно + пустота).
    Для каждого кандидата: collect_values → create_log → verify_log → закрытие дела
    «Выполнено» (complete_day_activities, FR-2.1.7). Сигнал ABORT прерывает цикл;
    необработанные кандидаты помечаются «aborted».

    «Ремонт» (FR-2.1.7): после цикла создания — отдельный проход по дням, у которых учёт
    уже есть (works непуст), но дело «Заполнить работы по договорам» могло остаться открытым
    (день не ушёл из «Запланированные»). Таким дням учёт не создаём, только закрываем дело.
    Проход самоидемпотентен (фильтр COMPLETED=N); журнал для него не задействуем.
    Возвращает список результатов; в конце печатает сводную таблицу.

    Идемпотентность (5_01): в боевом режиме перед обработкой кандидата сверяемся с
    локальным журналом (.runtime/processed.json) — день, обработанный в пределах TTL,
    пропускаем без REST-перечитки. В dry-run журнал НЕ читаем и НЕ пишем, чтобы режим
    оставался чистым предпросмотром. `now_ts` берётся один раз и прокидывается в helpers.
    """
    from datetime import timedelta

    from .workday import read_days, select_candidates, select_repair_days

    now_ts = int(time.time())
    # Журнал читаем только в боевом режиме (в dry-run он не задействован).
    journal = load_journal() if not dry_run else {}

    date_from = today - timedelta(days=cfg.edit_window_days + 3)
    # Серверный фильтр по «Типу дня» (ЭТАП B1): отпуск/отгул/нерабочие типы не тянем с портала.
    # select_candidates/select_repair_days дублируют проверку на клиенте (защита belt-and-suspenders).
    days = read_days(b24, cfg, date_from, today, day_type_ids=cfg.day_type_work_ids)
    candidates = select_candidates(days, cfg, today, limit=limit)
    repair_days = select_repair_days(days, cfg, today, limit=limit)

    results: List[Dict[str, Any]] = []
    if not candidates:
        log.info("Кандидатов на создание учёта нет (после отбора в окне 4 дней).")

    applied_to_all: Optional[Values] = None
    aborted = False

    for idx, day in enumerate(candidates):
        if aborted:
            results.append(_result_row(day, "aborted", reason="прервано пользователем (abort)"))
            continue

        # Идемпотентность (5_01): в боевом режиме пропускаем дни, уже отмеченные в
        # журнале в пределах TTL (экономим лишнюю REST-перечитку; гард остаётся главным).
        if not dry_run and is_processed(
            journal, day.id, ttl_sec=cfg.journal_ttl_sec, now_ts=now_ts
        ):
            reason = "уже обработан ранее (журнал, в пределах TTL)"
            log.info("День id=%d: пропуск — %s.", day.id, reason)
            results.append(_result_row(day, "skipped", reason=reason))
            continue

        outcome = collect_values(
            day, cfg, interaction=interaction, applied_to_all=applied_to_all
        )

        if outcome is ABORT:
            log.warning("День id=%d: пользователь прервал пакет (abort).", day.id)
            results.append(_result_row(day, "aborted", reason="прервано пользователем (abort)"))
            aborted = True
            continue
        if outcome is SKIP:
            log.info("День id=%d: пропущен пользователем (skip).", day.id)
            results.append(_result_row(day, "skipped", reason="пропущено пользователем (skip)"))
            continue

        assert isinstance(outcome, Values)

        # Опция «применить ко всем»: после первого ручного ввода в интерактиве предложить
        # распространить значения на остальных кандидатов пакета.
        if interaction and applied_to_all is None and idx < len(candidates) - 1:
            ans = input("  Применить эти значения ко всем оставшимся дням? [y/N]: ").strip().lower()
            if ans in ("y", "yes", "д", "да"):
                applied_to_all = outcome
                log.info("Значения будут применены ко всем оставшимся кандидатам пакета.")

        created = create_log(
            b24, day, outcome.description, outcome.hours, cfg, dry_run=dry_run, today=today
        )
        status = created["status"]

        if status == "filled":
            # Боевая запись прошла — фиксируем день в журнале (идемпотентность, 5_01).
            # В dry-run сюда не попадаем (там статус "dry-run"), журнал не трогаем.
            try:
                mark_processed(
                    DEFAULT_JOURNAL_PATH,
                    day.id,
                    day.date.isoformat() if day.date else "",
                    created["new_id"],
                    now_ts=now_ts,
                )
            except Exception as _journal_exc:
                log.warning(
                    "День id=%d: не удалось записать журнал обработки (%s); "
                    "запись в Bitrix уже создана, гард защитит от дубля.",
                    day.id,
                    _journal_exc,
                )
            verification = verify_log(
                b24, day.id, created["new_id"], outcome.description, outcome.hours, cfg
            )
            if verification["ok"]:
                # FR-2.1.7: учёт создан и подтверждён — закрыть дело дня «Выполнено».
                act_outcome = complete_day_activities(b24, day, cfg, dry_run=dry_run)
                act_fields = _finalize_activity(b24, day, cfg, act_outcome)
            else:
                # Учёт создан, но верификация не прошла — дело НЕ закрываем: день остаётся
                # в «Запланированные» как видимый сигнал проблемы (не маскируем расхождение).
                log.warning(
                    "День id=%d: верификация учёта не пройдена — дело «Выполнено» НЕ закрываем.",
                    day.id,
                )
                act_fields = {
                    "activity_status": "skipped",
                    "activity_ids": [],
                    "activity_ok": False,
                }
            results.append(
                _result_row(
                    day,
                    status,
                    new_id=created["new_id"],
                    verify_ok=verification["ok"],
                    reason=("; ".join(verification["problems"]) if verification["problems"] else ""),
                    **act_fields,
                )
            )
        elif status == "dry-run":
            # Предпросмотр включает и закрытие дела «Выполнено» (read-only в dry-run).
            act_outcome = complete_day_activities(b24, day, cfg, dry_run=dry_run)
            act_fields = _finalize_activity(b24, day, cfg, act_outcome)
            results.append(
                _result_row(
                    day, status, reason="план показан, запись не выполнена", **act_fields
                )
            )
        elif status == "skipped":
            results.append(_result_row(day, status, reason=created.get("reason", "")))
        else:  # error
            results.append(_result_row(day, status, reason=created.get("reason", "")))

    # --- «Ремонт» закрытия дел (FR-2.1.7) ---------------------------------------------------
    # Дни с уже созданным учётом (works непуст), но возможно открытым делом «Заполнить работы
    # по договорам» — день не ушёл из «Запланированные». Учёт не создаём, только закрываем дело.
    # При aborted весь пакет прерван пользователем — «ремонт» тоже не выполняем.
    if not aborted:
        for day in repair_days:
            act_outcome = complete_day_activities(b24, day, cfg, dry_run=dry_run)
            act_fields = _finalize_activity(b24, day, cfg, act_outcome)
            row_status = _REPAIR_STATUS.get(act_outcome["status"], act_outcome["status"])
            results.append(
                _result_row(
                    day,
                    row_status,
                    repair=True,
                    reason=act_outcome.get("reason", ""),
                    **act_fields,
                )
            )

    _print_summary(results, dry_run=dry_run)
    return results

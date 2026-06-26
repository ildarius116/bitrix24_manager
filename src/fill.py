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


def _print_summary(results: List[Dict[str, Any]], *, dry_run: bool) -> None:
    """Напечатать сводную таблицу итогов и счётчики по кандидатам."""
    counts: Dict[str, int] = {}
    log.info("=== Сводка fill (%s) ===", "DRY-RUN" if dry_run else "БОЕВОЙ РЕЖИМ")
    for row in results:
        status = row["status"]
        counts[status] = counts.get(status, 0) + 1
        detail = ""
        if "new_id" in row:
            detail = f"новый учёт id={row['new_id']}"
        elif "reason" in row and row["reason"]:
            detail = str(row["reason"])
        if row.get("verify_ok") is False:
            detail = (detail + "; " if detail else "") + "ВЕРИФИКАЦИЯ НЕ ПРОЙДЕНА"
        log.info(
            "  день id=%s | %s | %-8s | %s",
            row["day_id"],
            row["date"] or "—",
            status,
            detail or "—",
        )
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "нет кандидатов"
    log.info("Итого по кандидатам: %s.", summary)


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
    Для каждого кандидата: collect_values → create_log → (filled и не dry_run) verify_log.
    Сигнал ABORT прерывает цикл; необработанные кандидаты помечаются «aborted».
    Возвращает список результатов; в конце печатает сводную таблицу.

    Идемпотентность (5_01): в боевом режиме перед обработкой кандидата сверяемся с
    локальным журналом (.runtime/processed.json) — день, обработанный в пределах TTL,
    пропускаем без REST-перечитки. В dry-run журнал НЕ читаем и НЕ пишем, чтобы режим
    оставался чистым предпросмотром. `now_ts` берётся один раз и прокидывается в helpers.
    """
    from datetime import timedelta

    from .workday import read_days, select_candidates

    now_ts = int(time.time())
    # Журнал читаем только в боевом режиме (в dry-run он не задействован).
    journal = load_journal() if not dry_run else {}

    date_from = today - timedelta(days=cfg.edit_window_days + 3)
    days = read_days(b24, cfg, date_from, today)
    candidates = select_candidates(days, cfg, today, limit=limit)

    results: List[Dict[str, Any]] = []
    if not candidates:
        log.info("Кандидатов на заполнение нет (после отбора в окне 4 дней).")
        _print_summary(results, dry_run=dry_run)
        return results

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
            results.append(
                _result_row(
                    day,
                    status,
                    new_id=created["new_id"],
                    verify_ok=verification["ok"],
                    reason=("; ".join(verification["problems"]) if verification["problems"] else ""),
                )
            )
        elif status == "dry-run":
            results.append(_result_row(day, status, reason="план показан, запись не выполнена"))
        elif status == "skipped":
            results.append(_result_row(day, status, reason=created.get("reason", "")))
        else:  # error
            results.append(_result_row(day, status, reason=created.get("reason", "")))

    _print_summary(results, dry_run=dry_run)
    return results

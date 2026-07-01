"""Фоновые QThread-воркеры для export/fill (фаза 6_03).

Назначение
----------
Выполнить тяжёлые REST-операции (`export` и `fill`) вне main thread, чтобы UI не
блокировался. Воркеры НЕ трогают виджеты напрямую — только эмитят сигналы; все
обновления интерфейса делает MainWindow в main thread (слоты, подключённые к
сигналам через очередь событий Qt).

Логирование
-----------
Воркеры пишут в ``logging.getLogger("workday")`` — лог-панель уже подключена к нему
(``LogPanel.attach``). Свой лог-сигнал не дублируем. Секрет вебхука в логах
маскируется ``SecretMaskingFilter`` (см. ``main._run_gui`` / ``src.logging_setup``).

Интерактивный fill («Индивидуально»)
------------------------------------
`src/fill.py` НЕ меняем (GUI_SPEC §10). Выбор дней и значения берутся из левой панели
(``ControlPanel``), а не из per-day поп-апа. Механизм — monkeypatch ``builtins.input``
(и ``sys.stdout`` — см. ниже) только при ``interaction=True``. Подмена ПРОЦЕССНАЯ
(глобальная): на время ``FillWorker.run()`` ``builtins.input``/``sys.stdout`` заменяются
для всего процесса и восстанавливаются в ``finally``. Это безопасно, потому что:
(1) ``MainWindow`` гарантирует только одну активную операцию (``_busy``-гард), так что
параллельных ``run()`` нет; (2) main thread в GUI ``input()`` не вызывает, а вывод
проксируется в оригинальный ``stdout`` без потери; (3) при закрытии окна во время
операции ``MainWindow.closeEvent`` дожидается завершения воркера, поэтому ``finally``
всегда отрабатывает и патч не утекает.

Фейковый ``input`` (``_fake_input``) СИНХРОНЕН — без диалогов и блокировки потока:
он распознаёт промпты `collect_values` по подстрокам «Описание» / «Количество часов»
и решает по левой панели (``selected_days`` / ``per_day_values``). Дата текущего дня
берётся из ``_last_day_date`` (его наполняет ``_StdoutTap`` из заголовка дня). День вне
``selected_days`` → возвращаем «skip» (``collect_values`` пропускает его); отмеченный →
подставляем описание/часы его инлайн-полей. Так пишутся ТОЛЬКО отмеченные дни своими
значениями. ``_reread_guard`` (src/fill.py) остаётся страховкой окна/пустоты.

Определение текущего дня (id/дата)
----------------------------------
``collect_values`` печатает перед каждым ``input()`` строку вида
``=== День id=12345 | 25.06.2026 | <title> ===`` через ``print()``. Чтобы ``_fake_input``
мог сопоставить текущий день с выбором левой панели (``selected_days`` по датам),
``sys.stdout`` на время ``interaction=True`` подменяется прокси ``_StdoutTap`` —
он пишет каждую строку в оригинальный stdout (вывод не глушится) и одновременно
сканирует её регулярками на ``День id=(\\d+)`` / ``(\\d{2}\\.\\d{2}\\.\\d{4})``,
запоминая последний распознанный id/дату в ``FillWorker``. Восстанавливается в
``finally`` вместе с ``builtins.input``. Если регэксп не сматчил — ``_last_day_date``
останется пустым, и ``_resolve_day_description`` вернёт «skip» (день не сопоставлен).

Замечание про QThread.finished
------------------------------
У ``QThread`` уже есть встроенный сигнал ``finished`` (без аргументов). Чтобы его не
затенять, код возврата операции эмитится отдельным сигналом ``finished_code(int)``.
"""

from __future__ import annotations

import builtins
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Signal

from src.b24 import B24, B24Error
from src.dates import today_moscow
from src.export_excel import build_workbook
from src.fill import run_fill
from src.workday import _day_type_allowed, read_days, read_logs

log = logging.getLogger("workday")

# Корень проекта = родитель каталога gui/ (рядом с src/, config.yaml, out/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Регулярки для распознавания заголовка дня, который collect_values печатает
# через print() перед input() (см. docstring модуля «Обогащение day_info»).
_DAY_ID_RE = re.compile(r"День id=(\d+)")
_DAY_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")


class _StdoutTap:
    """Прокси stdout: проксирует запись в оригинальный поток и сканирует строки.

    Используется только при ``interaction=True`` (см. ``FillWorker.run``), чтобы
    вытащить id/дату текущего дня из заголовка, который печатает
    ``src.fill.collect_values`` (сам ``src/fill.py`` не меняем — GUI_SPEC §10).
    """

    def __init__(self, original, worker: "FillWorker") -> None:
        self._original = original
        self._worker = worker

    def write(self, text: str) -> int:
        self._original.write(text)
        if "День id=" in text:
            id_match = _DAY_ID_RE.search(text)
            date_match = _DAY_DATE_RE.search(text)
            if id_match:
                self._worker._last_day_id = id_match.group(1)
            if date_match:
                self._worker._last_day_date = date_match.group(1)
        return len(text)

    def flush(self) -> None:
        self._original.flush()

    def __getattr__(self, name: str):
        # Прочие атрибуты (isatty, encoding, ...) — делегируем оригинальному потоку.
        return getattr(self._original, name)


def _smoke(b24: B24) -> bool:
    """Read-only smoke-проверка доступа (как ``_smoke_or_exit`` в main.py, без sys.exit).

    Возвращает ``True`` при успехе (и пишет «Доступ ОК…»), ``False`` при ошибке.
    Секрет не печатается.
    """
    try:
        info = b24.smoke()
    except B24Error as exc:
        log.error("Нет доступа к порталу при smoke-проверке: %s", exc)
        return False
    except Exception as exc:  # сетевые/непредвиденные — без трейсбека
        log.error("Непредвиденная ошибка при smoke-проверке доступа: %s", exc)
        return False

    log.info(
        "Доступ ОК: %s (id %s), TZ %s",
        info["full_name"],
        info["id"] or "?",
        info["time_zone"],
    )
    return True


class SmokeWorker(QThread):
    """Фоновая read-only smoke-проверка доступа к порталу (индикатор вебхука).

    Не пишет в прод и не трогает виджеты — только эмитит результат булевым сигналом.
    Используется для индикатора «● Вебхук подключён» в меню-баре (зелёный/красный).
    Секрет вебхука не логируется/не эмитится (см. ``_smoke``).

    Сигналы
    -------
    smoke_result(bool)
        ``True`` — доступ есть; ``False`` — ошибка доступа/сети/конфигурации.
    """

    smoke_result = Signal(bool)

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            b24 = B24(self._config)
        except Exception as exc:
            log.error("Не удалось инициализировать клиент B24 для проверки доступа: %s", exc)
            self.smoke_result.emit(False)
            return
        self.smoke_result.emit(_smoke(b24))


def classify_day_states(days, cfg, today: date) -> Dict[str, Dict[str, Any]]:
    """Классифицировать дни 4-дневного окна на «можно заполнить» / «недоступно» (Часть A).

    Чистая функция БЕЗ сети. Источник истины «fillable» — те же условия, что и
    ``src.workday.select_candidates`` (день есть, рабочий тип, в окне, works пуст), но
    вычисляемые по уже прочитанным ``WorkdayDay`` (REST-логика записи не дублируется).

    Параметры
    ---------
    days:
        список ``WorkdayDay`` за окно ``today − cfg.edit_window_days … today``
        (из ``read_days`` БЕЗ серверного фильтра по «Типу дня» — иначе нерабочие дни
        не попали бы в список и причина «не рабочий день» была бы недостижима).
    cfg:
        конфигурация (``edit_window_days``, ``day_type_work_ids``).
    today:
        «сегодня» (Europe/Moscow).

    Возвращает ``{iso_date: {"fillable": bool, "reason": str}}`` для КАЖДОЙ даты окна
    (те же 5 строк, что рисует левая панель). Причины недоступности:
    ``нет записи «Рабочий день»`` / ``не рабочий день`` / ``вне окна 4 дней`` /
    ``уже заполнен (N)``.
    """
    window_days = int(getattr(cfg, "edit_window_days", 4))
    earliest = today - timedelta(days=window_days)
    work_ids = cfg.day_type_work_ids

    # Первая запись на дату (read_days сортирует id desc — берём верхнюю).
    by_date: Dict[date, Any] = {}
    for day in days:
        if day.date is not None and day.date not in by_date:
            by_date[day.date] = day

    states: Dict[str, Dict[str, Any]] = {}
    for offset in range(window_days + 1):
        d = today - timedelta(days=offset)
        key = d.isoformat()
        day = by_date.get(d)
        if day is None:
            states[key] = {"fillable": False, "reason": "нет записи «Рабочий день»"}
            continue
        if not _day_type_allowed(day, work_ids, log_skip=False):
            states[key] = {"fillable": False, "reason": "не рабочий день"}
            continue
        if not (earliest <= d <= today):
            states[key] = {"fillable": False, "reason": "вне окна 4 дней"}
            continue
        if day.works_ids:
            states[key] = {
                "fillable": False,
                "reason": f"уже заполнен ({len(day.works_ids)})",
            }
            continue
        states[key] = {"fillable": True, "reason": ""}
    return states


class DayStatesWorker(QThread):
    """Фоновая read-only классификация дней окна для метода «Индивидуально» (Часть A).

    Читает дни окна (``read_days`` БЕЗ фильтра по «Типу дня» — чтобы нерабочие дни тоже
    попали в выборку) и вычисляет их доступность (``classify_day_states``). В прод НЕ
    пишет и виджеты не трогает — только
    эмитит результат. Секрет вебхука не логируется/не эмитится.

    Сигналы
    -------
    states_ready(object)
        ``dict[iso_date -> {"fillable": bool, "reason": str}]``.
    states_failed()
        Ошибка доступа/сети/конфигурации — панель откатит блокировку.
    """

    states_ready = Signal(object)
    states_failed = Signal()

    def __init__(self, config, today: "date | None" = None, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._today = today

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            b24 = B24(self._config)
        except Exception as exc:
            log.error("Не удалось инициализировать клиент B24 для статусов дней: %s", exc)
            self.states_failed.emit()
            return

        if not _smoke(b24):
            self.states_failed.emit()
            return

        try:
            today = self._today or today_moscow()
            earliest = today - timedelta(days=int(getattr(self._config, "edit_window_days", 4)))
            # ВАЖНО: читаем ВСЕ дни окна БЕЗ серверного фильтра по «Типу дня». Иначе
            # нерабочий день (отпуск/отгул) исчез бы из выборки и classify_day_states
            # пометил бы его «нет записи «Рабочий день»» вместо «не рабочий день».
            # Причину по типу дня расставляет сам классификатор через _day_type_allowed.
            days = read_days(b24, self._config, earliest, today)
            states = classify_day_states(days, self._config, today)
        except B24Error as exc:
            log.error("Ошибка чтения дней окна для статусов: %s", exc)
            self.states_failed.emit()
            return
        except Exception as exc:
            log.error("Непредвиденная ошибка при вычислении статусов дней: %s", exc)
            self.states_failed.emit()
            return

        self.states_ready.emit(states)


class ExportWorker(QThread):
    """Фоновая выгрузка «Рабочего дня» в Excel (повторяет пайплайн ``_cmd_export``).

    Сигналы
    -------
    result_ready(object)
        Кортеж ``(days, user_map)``: ``list[WorkdayDay]`` и ``{str(employee_id): "ФИО"}``.
    finished_code(int)
        Код возврата операции: 0 — успех, 2 — ошибка доступа/выгрузки.
    """

    result_ready = Signal(object)
    finished_code = Signal(int)

    def __init__(self, config, date_from, date_to, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._date_from = date_from
        self._date_to = date_to

    def run(self) -> None:  # noqa: D401 - QThread entry point
        if self._date_from > self._date_to:
            log.error(
                "Начало периода (%s) позже конца (%s).",
                self._date_from.isoformat(),
                self._date_to.isoformat(),
            )
            self.finished_code.emit(2)
            return

        log.info(
            "export: период %s … %s",
            self._date_from.isoformat(),
            self._date_to.isoformat(),
        )

        try:
            b24 = B24(self._config)
        except Exception as exc:
            log.error("Не удалось инициализировать клиент B24: %s", exc)
            self.finished_code.emit(2)
            return

        if not _smoke(b24):
            self.finished_code.emit(2)
            return

        try:
            days = read_days(b24, self._config, self._date_from, self._date_to)
            read_logs(b24, self._config, days)
            user_map = self._resolve_users(b24, days)
        except B24Error as exc:
            log.error("Ошибка чтения данных портала: %s", exc)
            self.finished_code.emit(2)
            return
        except Exception as exc:
            log.error("Непредвиденная ошибка при чтении данных: %s", exc)
            self.finished_code.emit(2)
            return

        # Показать таблицу даже если последующая запись файла не удастся.
        self.result_ready.emit((days, user_map))

        try:
            self._build_workbook(days, user_map)
        except Exception as exc:
            log.error("Ошибка формирования Excel-файла: %s", exc)
            self.finished_code.emit(2)
            return

        self.finished_code.emit(0)

    # ------------------------------------------------------------------
    def _resolve_users(self, b24: B24, days) -> Dict[str, str]:
        """Собрать уникальные employee id и резолвить ФИО (как в _cmd_export)."""
        unique_ids: List[int] = []
        seen: set = set()
        for day in days:
            raw = day.employee.strip() if day.employee else ""
            if not raw:
                continue
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if uid not in seen:
                seen.add(uid)
                unique_ids.append(uid)

        if not unique_ids:
            return {}

        resolved = b24.resolve_users(unique_ids)
        log.info(
            "Резолвинг сотрудников: %d уникальных id → %d найдено ФИО (кэш b24).",
            len(unique_ids),
            sum(1 for n in resolved.values() if not str(n).startswith("id ")),
        )
        return {str(uid): name for uid, name in resolved.items()}

    def _build_workbook(self, days, user_map: Dict[str, str]) -> None:
        """Сформировать Excel по export.filename_pattern (config.yaml)."""
        out_dir = Path(str(self._config.export.get("output_dir", "./out")))
        if not out_dir.is_absolute():
            out_dir = _PROJECT_ROOT / out_dir
        pattern = self._config.export.get(
            "filename_pattern", "workday_{date_from}_{date_to}.xlsx"
        )
        filename = pattern.format(
            date_from=self._date_from.isoformat(),
            date_to=self._date_to.isoformat(),
        )
        out_path = out_dir / filename

        summary = build_workbook(
            days, self._date_from, self._date_to, out_path, user_map=user_map
        )
        log.info(
            "Выгрузка готова: %s | строк=%s, групп=%s, часов(лист)=%s, часов(группы)=%s.",
            summary["path"],
            summary["main_rows"],
            summary["group_count"],
            summary["main_hours"],
            summary["group_hours"],
        )
        if abs(float(summary["main_hours"]) - float(summary["group_hours"])) > 1e-6:
            log.warning(
                "Сумма часов основного листа (%s) не совпала с группировкой (%s)!",
                summary["main_hours"],
                summary["group_hours"],
            )


class FillWorker(QThread):
    """Фоновое автозаполнение учётов (вызывает ``run_fill``).

    Сигналы
    -------
    result_ready(object)
        ``list[dict]`` результатов ``run_fill`` (ключи date/day_id/status/...).
    finished_code(int)
        Код возврата: 0 — без ошибок, 2 — есть ошибки/ошибка доступа.

    Метод «Индивидуально» (``interaction=True``) СИНХРОННО решает по левой панели
    (``selected_days`` / ``per_day_values``) — без per-day диалога (см. модульный
    docstring). Метод «По-умолчанию» (``interaction=False``) значения берёт из
    ``cfg.defaults`` (через ``_apply_defaults_override``).
    """

    result_ready = Signal(object)
    finished_code = Signal(int)

    def __init__(
        self,
        config,
        *,
        dry_run: bool,
        interaction: bool,
        today,
        description: str,
        hours: float,
        limit: int = 5,
        selected_days: "Optional[set]" = None,
        per_day_values: "Optional[Dict[Any, Any]]" = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._dry_run = dry_run
        self._interaction = interaction
        self._today = today
        self._default_description = description
        self._default_hours = hours
        self._limit = limit

        # Выбор/значения из левой панели (метод «Индивидуально»). None → выбор не задан
        # (метод «По-умолчанию» / export): интерактивный путь тогда применяет дефолты
        # ко всем кандидатам. Ключи — объекты ``datetime.date``.
        self._selected_days: "Optional[set]" = (
            set(selected_days) if selected_days is not None else None
        )
        self._per_day_values: Dict[Any, Any] = dict(per_day_values or {})

        # Значения текущего дня — для промпта «Количество часов» того же дня.
        self._current_description: str = description
        self._current_hours: float = hours

        # Последние id/дата дня, распознанные _StdoutTap из заголовка collect_values
        # (см. docstring модуля «Обогащение day_info»). Пустые строки — пока не распознано.
        self._last_day_id: str = ""
        self._last_day_date: str = ""

    # ------------------------------------------------------------------
    # No-op приёмник (совместимость с closeEvent).
    # ------------------------------------------------------------------
    def provide_confirmation(self, response: Dict[str, Any]) -> None:
        """Больше не используется: метод «Индивидуально» не запрашивает подтверждение
        по дню (значения берутся из левой панели). Оставлен как no-op, потому что
        ``MainWindow.closeEvent`` безусловно дёргает его для ``FillWorker``."""
        return None

    # ------------------------------------------------------------------
    # Фейковый input (только при interaction=True, метод «Индивидуально»).
    # ------------------------------------------------------------------
    def _fake_input(self, prompt: str = "") -> str:
        text = prompt or ""

        # Промпт часов текущего дня — возвращаем уже выбранное число строкой.
        if "Количество часов" in text:
            return str(self._current_hours)

        # Промпт описания — старт нового дня: решаем по левой панели.
        if "Описание" in text:
            return self._resolve_day_description()

        # Промпт run_fill «Применить эти значения ко всем оставшимся? [y/N]»:
        # решение принимается по каждому дню отдельно (левая панель), поэтому «N».
        if "ко всем" in text:
            return "N"

        return ""

    def _parse_last_day_date(self) -> "Optional[date]":
        """Дата текущего дня из заголовка collect_values (``dd.mm.yyyy``) → ``date``."""
        raw = (self._last_day_date or "").strip()
        if not raw:
            return None
        try:
            return datetime.strptime(raw, "%d.%m.%Y").date()
        except ValueError:
            return None

    def _resolve_day_description(self) -> str:
        """Решить судьбу текущего дня по выбору левой панели (метод «Индивидуально»).

        - выбор не задан (``selected_days is None``) → дефолтное описание (как auto);
        - день НЕ отмечен → «skip» (``collect_values`` пропустит его);
        - день отмечен → подставить его инлайн-часы в ``_current_hours`` и вернуть
          его инлайн-описание.
        """
        if self._selected_days is None:
            return self._current_description

        day_date = self._parse_last_day_date()
        if day_date is None or day_date not in self._selected_days:
            return "skip"

        desc, hours = self._per_day_values.get(
            day_date, (self._default_description, self._default_hours)
        )
        self._current_description = str(desc)
        try:
            self._current_hours = float(hours)
        except (TypeError, ValueError):
            self._current_hours = float(self._default_hours)
        return self._current_description

    # ------------------------------------------------------------------
    def _precheck_fill_config(self) -> List[str]:
        """Ранний пре-чек конфигурации для записи 1218 (копия списка из main.py:215-240).

        Возвращает список отсутствующих/некорректных полей (метки для лога). Приватное
        из main.py не импортируем — дублируем проверки по публичным геттерам Config.
        """
        cfg = self._config

        def _nonempty(value):
            if not value:
                raise KeyError(value)
            return value

        checks = [
            ("entity.timelog_category_id", lambda: cfg.timelog_category_id),
            ("entity.timelog_type_id",     lambda: cfg.timelog_type_id),
            ("fields.log_parent",          lambda: cfg.field_log_parent),
            ("fields.log_contract",        lambda: cfg.field_log_contract),
            ("fields.log_contract_tech",   lambda: cfg.field_log_contract_tech),
            ("fields.log_description",     lambda: cfg.field_log_description),
            ("fields.log_hours",           lambda: cfg.field_log_hours),
            ("contract_general_tasks",     lambda: _nonempty(cfg.contract_general_tasks)),
            ("defaults.contract_tech_id",  lambda: _nonempty(cfg.contract_tech_id)),
            ("defaults.task_description",  lambda: cfg.defaults["task_description"]),
            ("defaults.hours",             lambda: cfg.defaults["hours"]),
        ]
        missing: List[str] = []
        for label, getter in checks:
            try:
                getter()
            except (KeyError, TypeError, ValueError):
                missing.append(label)
        return missing

    def _apply_defaults_override(self) -> "Optional[Dict[str, Any]]":
        """Временно подставить в cfg.defaults описание/часы, заданные в колонке «Редактирование».

        ``run_fill`` (src/fill.py, не меняем) берёт описание/часы из ``cfg.defaults``, а не
        из аргументов. Чтобы значения инлайн-полей применялись и в методе «По-умолчанию»
        (interaction=False), временно переопределяем содержимое
        словаря ``cfg.defaults`` (мутируем сам dict — cfg может быть frozen dataclass, но
        сам словарь изменяемый) и восстанавливаем в ``run().finally`` через
        ``_restore_defaults``. Правится только in-memory config текущего процесса; исходные
        значения возвращаются, чтобы не влиять на export/повторные запуски.

        Возвращает снимок исходного словаря (для восстановления) или None, если defaults
        недоступен/не dict.
        """
        defaults = getattr(self._config, "defaults", None)
        if not isinstance(defaults, dict):
            return None
        saved = dict(defaults)
        defaults["task_description"] = self._default_description
        defaults["hours"] = self._default_hours
        return saved

    def _restore_defaults(self, saved: "Optional[Dict[str, Any]]") -> None:
        """Восстановить исходный cfg.defaults после переопределения (см. _apply_defaults_override)."""
        if saved is None:
            return
        defaults = getattr(self._config, "defaults", None)
        if isinstance(defaults, dict):
            defaults.clear()
            defaults.update(saved)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        original_input = builtins.input
        original_stdout = sys.stdout
        if self._interaction:
            builtins.input = self._fake_input  # type: ignore[assignment]
            sys.stdout = _StdoutTap(original_stdout, self)  # type: ignore[assignment]
        # Подставить значения диалога «Значения по-умолчанию» в cfg.defaults на время прогона
        # (применяется и в методе «По-умолчанию»); восстановление — в finally ниже.
        saved_defaults = self._apply_defaults_override()
        try:
            # Ранний пре-чек конфигурации записи 1218 (как main.py:215-240): при нехватке —
            # понятная ошибка и аккуратный выход, без падения «Непредвиденная ошибка».
            missing = self._precheck_fill_config()
            if missing:
                log.error(
                    "Для команды fill в config.yaml не хватает полей записи 1218: %s. "
                    "Заполните entity.timelog_category_id / fields.log_* / defaults.",
                    ", ".join(missing),
                )
                self.finished_code.emit(2)
                return

            try:
                b24 = B24(self._config)
            except Exception as exc:
                log.error("Не удалось инициализировать клиент B24: %s", exc)
                self.finished_code.emit(2)
                return

            if not _smoke(b24):
                self.finished_code.emit(2)
                return

            # Сообщение о режиме (как main.py:242-252): dry-run info / боевой warning.
            if self._dry_run:
                log.info(
                    "fill: режим DRY-RUN (%s). Запись в прод НЕ выполняется — только показ плана.",
                    "Индивидуально" if self._interaction else "По-умолчанию",
                )
            else:
                log.warning(
                    "БОЕВОЙ РЕЖИМ: будет выполнена запись в прод (crm.item.add 1218 + закрытие "
                    "дела «Выполнено» crm.activity.update, FR-2.1.7). Окно 4 дней и пустота "
                    "перепроверяются перед каждой записью; «ремонт» закрывает дела и у уже "
                    "заполненных дней."
                )

            try:
                results = run_fill(
                    b24,
                    self._config,
                    dry_run=self._dry_run,
                    interaction=self._interaction,
                    today=self._today,
                    limit=self._limit,
                )
            except ValueError as exc:
                log.error("Ошибка значений/конфигурации при заполнении: %s", exc)
                self.finished_code.emit(2)
                return
            except B24Error as exc:
                log.error("Ошибка работы с порталом при заполнении: %s", exc)
                self.finished_code.emit(2)
                return
            except Exception as exc:
                log.error("Непредвиденная ошибка при заполнении: %s", exc)
                self.finished_code.emit(2)
                return

            self.result_ready.emit(results)

            has_errors = any(
                r.get("status") == "error"
                or r.get("verify_ok") is False
                or r.get("activity_ok") is False
                for r in results
            )
            self.finished_code.emit(2 if has_errors else 0)
        finally:
            self._restore_defaults(saved_defaults)
            if self._interaction:
                builtins.input = original_input  # type: ignore[assignment]
                sys.stdout = original_stdout  # type: ignore[assignment]

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
маскируется ``SecretMaskingFilter`` (см. ``gui_main`` / ``src.logging_setup``).

Интерактивный fill
------------------
`src/fill.py` НЕ меняем (GUI_SPEC §10). Подтверждение по дню перехватывается
monkeypatch-ем ``builtins.input`` (и ``sys.stdout`` — см. ниже) только при
``interaction=True``. Важно: подмена ПРОЦЕССНАЯ (глобальная), а не потоколокальная —
на время выполнения ``FillWorker.run()`` ``builtins.input``/``sys.stdout`` заменяются
для всего процесса и восстанавливаются в ``finally``. Это безопасно, потому что:
(1) ``MainWindow`` гарантирует только одну активную операцию (``_busy``-гард), так что
параллельных ``run()`` нет; (2) main thread в GUI ``input()`` не вызывает, а вывод
проксируется в оригинальный ``stdout`` без потери; (3) при закрытии окна во время
операции ``MainWindow.closeEvent`` разблокирует воркер и дожидается его завершения,
поэтому ``finally`` всегда отрабатывает и патч не утекает. Фейковый ``input``
распознаёт промпты `collect_values` по подстрокам «Описание» / «Количество часов»,
запрашивает решение у main thread через сигнал ``confirm_requested`` и блокирует
поток (``QWaitCondition``) до ответа ``provide_confirmation`` (см. PLAN_GUI §GUI-3).

Обогащение day_info (id/дата дня)
----------------------------------
``collect_values`` печатает перед каждым ``input()`` строку вида
``=== День id=12345 | 25.06.2026 | <title> ===`` через ``print()``. Чтобы диалог
подтверждения мог показать конкретный день (GUI_SPEC §5: «День: дд.мм.гггг (id N)»),
``sys.stdout`` на время ``interaction=True`` подменяется прокси ``_StdoutTap`` —
он пишет каждую строку в оригинальный stdout (вывод не глушится) и одновременно
сканирует её регулярками на ``День id=(\\d+)`` / ``(\\d{2}\\.\\d{2}\\.\\d{4})``,
запоминая последний распознанный id/дату в ``FillWorker``. Восстанавливается в
``finally`` вместе с ``builtins.input``. Если регэксп не сматчил — поля day_id/
day_date останутся пустыми, и диалог покажет «День: —».

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
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QMutex, QThread, QWaitCondition, Signal

from src.b24 import B24, B24Error
from src.export_excel import build_workbook
from src.fill import run_fill
from src.workday import read_days, read_logs

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
    confirm_requested(object)
        Запрос подтверждения по дню (dict ``day_info``) — обрабатывается в main thread,
        ответ возвращается через ``provide_confirmation``.

    Интерактив реализован monkeypatch-ем ``builtins.input`` (см. модульный docstring).
    """

    result_ready = Signal(object)
    finished_code = Signal(int)
    confirm_requested = Signal(object)

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

        # Блокировка потока на время ожидания ответа из main thread.
        self._mutex = QMutex()
        self._wait = QWaitCondition()
        self._response: Optional[Dict[str, Any]] = None
        # Флаг «воркер ждёт подтверждения» — делает provide_confirmation идемпотентным:
        # вызов вне ожидания (напр. из closeEvent, когда воркер занят REST) — no-op.
        self._waiting = False

        # Кэш «применить ко всем» (worker-level): {"description","hours"} либо None.
        self._applied_all: Optional[Dict[str, Any]] = None
        # Значения текущего дня — для промпта «Количество часов» того же дня.
        self._current_description: str = description
        self._current_hours: float = hours

        # Последние id/дата дня, распознанные _StdoutTap из заголовка collect_values
        # (см. docstring модуля «Обогащение day_info»). Пустые строки — пока не распознано.
        self._last_day_id: str = ""
        self._last_day_date: str = ""

    # ------------------------------------------------------------------
    # Приём ответа из main thread (вызывается слотом MainWindow).
    # ------------------------------------------------------------------
    def provide_confirmation(self, response: Dict[str, Any]) -> None:
        """Разблокировать воркер ответом диалога подтверждения.

        ``response`` — dict с ключом ``choice`` ∈ {"ok","all","skip","abort"} и
        опциональными ``description`` / ``hours`` (для ok/all).

        Идемпотентно: если воркер сейчас НЕ ждёт подтверждения (``_waiting`` False) —
        вызов игнорируется (no-op). Это позволяет ``closeEvent`` безопасно слать
        ``{"choice": "abort"}`` независимо от текущей фазы воркера, не оставляя
        «протухший» ответ, который мог бы быть съеден следующим запросом.
        """
        self._mutex.lock()
        try:
            if not self._waiting:
                return
            self._response = dict(response or {"choice": "ok"})
            self._wait.wakeAll()
        finally:
            self._mutex.unlock()

    # ------------------------------------------------------------------
    # Фейковый input (только при interaction=True).
    # ------------------------------------------------------------------
    def _fake_input(self, prompt: str = "") -> str:
        text = prompt or ""

        # Промпт часов текущего дня — возвращаем уже выбранное число строкой.
        if "Количество часов" in text:
            return str(self._current_hours)

        # Промпт описания — старт нового дня.
        if "Описание" in text:
            if self._applied_all is not None:
                self._current_description = str(self._applied_all["description"])
                self._current_hours = float(self._applied_all["hours"])
                return self._current_description
            return self._request_confirmation()

        # Промпт run_fill «Применить эти значения ко всем оставшимся? [y/N]»:
        # «ко всем» мы реализуем на уровне воркера (кэш _applied_all), поэтому здесь
        # всегда отвечаем «N» — иначе run_fill зафиксировал бы свой applied_to_all и
        # перестал звать collect_values (наш короткозамыкатель повторов).
        if "ко всем" in text:
            return "N"

        return ""

    def _request_confirmation(self) -> str:
        """Запросить решение у main thread и заблокироваться до ответа."""
        day_info = {
            "default_description": self._default_description,
            "default_hours": self._default_hours,
            "day_id": self._last_day_id,
            "day_date": self._last_day_date,
        }
        self._mutex.lock()
        try:
            self._response = None
            self._waiting = True
            self.confirm_requested.emit(day_info)
            while self._response is None:
                self._wait.wait(self._mutex)
            resp = self._response
            self._response = None
            self._waiting = False
        finally:
            self._mutex.unlock()

        choice = str(resp.get("choice", "ok"))
        if choice == "skip":
            return "skip"
        if choice == "abort":
            return "abort"

        desc = str(resp.get("description", self._default_description))
        hours = resp.get("hours", self._default_hours)
        try:
            hours = float(hours)
        except (TypeError, ValueError):
            hours = float(self._default_hours)

        self._current_description = desc
        self._current_hours = hours
        if choice == "all":
            self._applied_all = {"description": desc, "hours": hours}
        return desc

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
        """Временно подставить в cfg.defaults значения из диалога «Значения по-умолчанию».

        ``run_fill`` (src/fill.py, не меняем) берёт описание/часы из ``cfg.defaults``, а не
        из аргументов. Чтобы значения SettingsDialog применялись и в методе «По-умолчанию»
        (interaction=False, без per-day ConfirmDialog), временно переопределяем содержимое
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
                    "с подтверждением" if self._interaction else "без подтверждений",
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

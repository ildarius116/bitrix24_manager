# Фаза 6 — GUI: десктопная обёртка PySide6 над export/fill

**Статус:** TODO
**Зависит от:** Фазы 1–5 (DONE) — GUI только вызывает готовые модули `src/`.
**Связано:** `GUI_SPEC.md`, `PLAN_GUI.md`, макет `design/bitrix24-console-standalone.html`.

## Цель фазы
Дать неконсольный потокобезопасный интерфейс к командам `export`/`fill`: ribbon-кнопки, форма
параметров, таблица результатов с цветовой индикацией статусов, панель деталей, живой лог,
статусбар и интерактивный диалог подтверждения записи. Бизнес-логика остаётся в `src/`,
CLI (`main.py`) работает параллельно. Design system переиспользуется из проекта
`United_Stand_Platform` (USP) — копируется (вендорится) в проект.

## Результат фазы (DoD)
`venv/Scripts/python.exe gui_main.py` открывает окно; export строит `out/*.xlsx` и заполняет
таблицу; fill в dry-run показывает план без записи; fill в интерактиве показывает диалог по дню;
статусы окрашены по палитре; переключение тем работает; `src/` не изменён; `pytest` зелёный.

## Решения (подтверждены заказчиком)
1. **Theme** — вендорить `theme_service.py` + `main.qss` из USP в `bitrix24`; без живой зависимости.
2. **Интерактивный fill** — `src/` НЕ менять (спека §10); подтверждение через
   **monkeypatch `builtins.input`** в воркере + `QDialog` (сигнал/блокировка).
3. **Объём** — строго по `GUI_SPEC.md`; «Расписание/Настройки/Фильтр» из макета вне задания.

## Подзадачи
1. `phase_6_01_skeleton_theme.md` — каркас окна + вендоренная тема (ThemeManager + QSS).
2. `phase_6_02_log_params_statusbar.md` — лог-панель (QLoggingHandler), параметры, статусбар.
3. `phase_6_03_ribbon_workers_table.md` — ribbon, QThread-воркеры, таблица/детали.
4. `phase_6_04_confirm_dialog_menu.md` — диалог подтверждения fill + меню/темы.
5. `phase_6_05_deps_tests_docs.md` — зависимости, тесты, ревью, документация.

## Делегирование (CLAUDE.md §2)
6_01/6_03 — `coder-expert`; 6_02/6_04 — `coder-simple`; тесты — `tester`; ревью — `reviewer`;
доки/отчёты — `docs-keeper`.

## Важно (безопасность, CLAUDE.md §4/§5)
- Dry-run включён по умолчанию; боевая запись только при выключенном Dry-run.
- Политика 4 дней НЕ дублируется в GUI — она внутри `run_fill`/`_reread_guard`.
- Код вебхука не попадает в лог-панель/детали (`raw` не выводим); лог уже маскируется.
- `src/` неизменен — если монки-патч не покрывает кейс, остановиться и согласовать с заказчиком.

## Переиспользуемая поверхность `src/` (проверенные сигнатуры)
- `config.load_config() -> Config`; `config.export`, `config.defaults`, `config.env`.
- `b24.B24(config)`; `.smoke() -> {id, full_name, time_zone, raw}`; `.resolve_users(ids) -> {id: ФИО}`.
- `workday.read_days(b24, config, date_from, date_to) -> List[WorkdayDay]`; `read_logs(b24, config, days)`.
  - `WorkdayDay(id, date, title, employee, works_ids, raw, logs)`;
    `WorkLog(id, parent_day_id, title, description, hours, contract, result, raw)`.
- `fill.run_fill(b24, cfg, *, dry_run, interaction, today, limit=5) -> List[dict]`.
  - `collect_values()` читает `input()` (строки 135/150): «Описание» — Enter/текст/`skip`/`abort`/`all`;
    «Количество часов» — Enter/число/`skip`/`abort`.
  - result-dict: `day_id, date, status` (+ `new_id, verify_ok, reason, activity_status, activity_ids,
    activity_ok` при успехе; `reason` при skip/error).
  - `status`: `filled, repaired, dry-run, skipped, already-closed, error, aborted`.
- `logging.getLogger("workday")` — секрет маскируется `SecretMaskingFilter`; GUI добавляет свой handler.
- `main.py` — эталон порядка: load_config → setup_logging → smoke → команда.

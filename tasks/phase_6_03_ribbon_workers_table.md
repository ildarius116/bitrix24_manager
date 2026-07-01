# phase_6_03 — Ribbon, QThread-воркеры, таблица результатов и панель деталей

**Статус:** DONE — отчёт: `../tasks_done/phase_6_done.md` (2026-06-30)
**Фаза:** 6 (GUI)
**Зависит от:** phase_6_01, phase_6_02
**Связано:** `GUI_SPEC.md` §3.2, §3.4, §3.5, §4; `PLAN_GUI.md`.
**Агент:** `coder-expert`.

## Цель
Связать UI с бизнес-логикой `src/`: запуск export/fill в фоновом потоке, отображение результатов
в таблице с цветовой индикацией статусов и деталей выбранной строки. UI не блокируется.

## Объём работ
- `gui/ribbon.py`:
  - кнопки «Выгрузить» (export.svg) и «Заполнить» (fill.svg);
  - toggles: Dry-run (по умолчанию ВКЛ) и Авто-режим (`no_interaction`);
  - сигналы запуска команд; кнопки блокируются на время операции.
- `gui/worker.py`:
  - `ExportWorker(QThread)` — выполняет пайплайн как `_cmd_export` (read_days → read_logs →
    resolve_users → build_workbook). Сигналы `log_message`, `result_ready(list[WorkdayDay])`,
    `finished(int)`.
  - `FillWorker(QThread)` — вызывает `run_fill(b24, cfg, dry_run, interaction, today, limit)`.
    Сигнал `result_ready(list[dict])`, `finished(int)`.
  - **Интерактив (monkeypatch `builtins.input`)** при `interaction=True`, обёртка в `run()` с
    `try/finally` восстановлением. Фейковый `input(prompt)`:
    - распознаёт промпт по подстроке «Описание» (старт нового дня) / «Количество часов»;
    - на «Описание»: если в воркере закэшировано «применить ко всем» — вернуть сохранённое
      описание (без диалога); иначе `emit confirm_requested(day_info)`, блокировать поток
      (`QSemaphore`/`QWaitCondition`), дождаться выбора из диалога:
      OK→описание (этот день); Применить-ко-всем→закэшировать (desc,hours)+вернуть описание;
      Пропустить→`"skip"`; Отмена→`"abort"`;
    - на «Количество часов»: вернуть сохранённое для текущего дня число строкой.
    > «Применить ко всем» уважает отредактированные значения (не форсит дефолты как токен `all`).
    > `run_fill` вызывает `collect_values` каждый день с `applied_to_all=None` — фейковый input
    > короткозамыкает повтор сам.
  - База воркера: получить `config`/`B24` из MainWindow, smoke перед операцией (как `_smoke_or_exit`).
- `gui/result_table.py`:
  - режим Export — колонки §3.4 (Дата, Сотрудник через `user_map`, Заполнено, Часы, Учёты);
  - режим Fill — колонки §3.4 (Дата, ID дня, Статус, Новый учёт, Дело, Причина);
  - цвет ячейки «Статус» по `status` → токен палитры: filled/repaired→`status_ok`,
    dry-run→`status_running`, skipped/already-closed→`text_muted`, error→`status_fail`,
    aborted→`status_warning`. Маппинг вынести чистой функцией (тестируется).
- `gui/detail_panel.py`:
  - Export — детали дня: дата/ID/сотрудник + список учётов (title/description/hours);
  - Fill — детали результата: status/reason/new_id/activity_status/activity_ids/verify_ok.

## Критерии приёмки (DoD)
- Export заполняет таблицу, строит `out/*.xlsx`; UI не зависает; кнопки блокируются на время.
- Fill (dry-run) показывает строки `dry-run` без записи; статусбар обновляет счётчики.
- Все обновления UI — в main thread через сигналы; воркер не трогает виджеты напрямую.
- Клик по строке наполняет панель деталей; маппинг статус→цвет покрыт юнит-тестом (см. gui_05).

## Артефакты
`gui/ribbon.py`, `gui/worker.py`, `gui/result_table.py`, `gui/detail_panel.py`,
правки `gui/main_window.py` (проводка сигналов).

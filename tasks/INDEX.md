# Индекс задач

Разбивка `PLAN.md` на фазы/подфазы. Правила именования и процесса — см. память проекта.
Завершённые фазы документируются в `../tasks_done/` (исходные файлы здесь НЕ удаляются).

## Фаза 1 — Каркас + REST-обёртка [DONE]
- `phase_1_00_overview.md`
- `phase_1_01_skeleton.md` — структура, config, CLI, логирование
- `phase_1_02_b24_wrapper.md` — обёртка над bitrix24_client.py
- `phase_1_03_smoke_access.md` — smoke-доступ + обработка ошибок
- Отчёт: `../tasks_done/phase_1_done.md` (2026-06-25)

## Фаза 2 — Парсинг (FR-1) [DONE]
- `phase_2_00_overview.md`
- `phase_2_01_read_days.md` — чтение дней 1208 за период
- `phase_2_02_read_logs.md` — связанные учёты 1218 (batch)
- `phase_2_03_excel_main_sheet.md` — основной лист Excel
- `phase_2_04_grouping_sheet.md` — лист группировки (FR-1.2)
- `phase_2_05_time_range_meta.md` — рамки данных + лист метаданных (FR-1.1.6)
- Отчёт: `../tasks_done/phase_2_done.md` (2026-06-25)

## Фаза 3 — Отбор кандидатов (FR-2.1.1–2.1.5) [DONE]
- `phase_3_01_select_candidates.md`
- Отчёт: `../tasks_done/phase_3_done.md` (2026-06-25)

## Фаза 4 — Создание учёта (FR-2.1.6–2.1.11) [DONE]
- `phase_4_00_overview.md`
- `phase_4_01_done_button_investigation.md` — наблюдение «Выполнено» (read-only) [DONE]
- `phase_4_02_values_prompt.md` — дефолт + подтверждение [DONE]
- `phase_4_03_create_log.md` — crm.item.add 1218 (plan→execute) [DONE]
- `phase_4_04_verify.md` — верификация перечитыванием [DONE]
- Отчёт: `../tasks_done/phase_4_done.md` (2026-06-26). Боевой тест на одном дне выполнен с явного
  разрешения заказчика: учёт 1218 id=163736 под днём id=271557, верификация OK.

## Фаза 5 — Надёжность и эксплуатация [DONE]
- `phase_5_00_overview.md`
- `phase_5_01_idempotency.md`
- `phase_5_02_dry_run.md`
- `phase_5_03_logging_audit.md`
- `phase_5_04_tests.md`
- Отчёт: `../tasks_done/phase_5_done.md` (2026-06-26). 223 passed (+99 новых тестов).
  Журнал идемпотентности `.runtime/processed.json`, аудит REST `.runtime/bitrix24_audit.jsonl`.
  Коммиты: `9b86384` (реализация) + `39a1834` (верификационные тесты + nit). Re-review: approve.

## Отложенные
- `phase_delayed_1_01_task_elapseditem.md` — альтернативный механизм через task.elapseditem
- `phase_delayed_1_02_browser_fallback.md` — браузерный fallback (если REST не покрывает «Выполнено»)
- `phase_delayed_1_03_scheduler.md` — автозапуск по расписанию

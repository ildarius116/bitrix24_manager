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
- `phase_4_01_done_button_investigation.md` — наблюдение «Выполнено» (read-only) [DONE, вывод исправлен 2026-06-26]
- `phase_4_02_values_prompt.md` — дефолт + подтверждение [DONE]
- `phase_4_03_create_log.md` — crm.item.add 1218 (plan→execute) [DONE]
- `phase_4_04_verify.md` — верификация перечитыванием [DONE]
- `phase_4_05_done_button_fix.md` — FR-2.1.7 завершение CRM-дела «Выполнено» [DONE]
- Отчёт: `../tasks_done/phase_4_done.md` (2026-06-26). Боевой тест на одном дне выполнен с явного
  разрешения заказчика: учёт 1218 id=163736 под днём id=271557, верификация OK.
- Отчёт (доработка): `../tasks_done/phase_4_05_done_button_fix.md` (2026-06-26). Закрытие CRM-дела
  «Выполнено» (crm.activity.update COMPLETED=Y); боевой прогон: дело id=169987 на дне id=271557 —
  status repaired, exit 0. 261 passed.

## Фаза 5 — Надёжность и эксплуатация [DONE]
- `phase_5_00_overview.md`
- `phase_5_01_idempotency.md`
- `phase_5_02_dry_run.md`
- `phase_5_03_logging_audit.md`
- `phase_5_04_tests.md`
- Отчёт: `../tasks_done/phase_5_done.md` (2026-06-26). 223 passed (+99 новых тестов).
  Журнал идемпотентности `.runtime/processed.json`, аудит REST `.runtime/bitrix24_audit.jsonl`.
  Коммиты: `9b86384` (реализация) + `39a1834` (верификационные тесты + nit). Re-review: approve.

## Фаза 6 — GUI: десктопная обёртка PySide6 (GUI_SPEC.md / PLAN_GUI.md) [DONE]
- `phase_6_00_overview.md` [DONE]
- `phase_6_01_skeleton_theme.md` — каркас окна + вендоренная тема (ThemeManager + QSS) [coder-expert] [DONE]
- `phase_6_02_log_params_statusbar.md` — лог (QLoggingHandler), параметры, статусбар [coder-simple] [DONE]
- `phase_6_03_ribbon_workers_table.md` — ribbon, QThread-воркеры, таблица/детали [coder-expert] [DONE]
- `phase_6_04_confirm_dialog_menu.md` — диалог подтверждения fill + меню/темы [coder-simple] [DONE]
- `phase_6_05_deps_tests_docs.md` — PySide6, тесты, ревью, документация [tester/reviewer/docs-keeper] [DONE]
- Решения: тема вендорится из USP; интерактив fill — monkeypatch `builtins.input` (src/ не меняем);
  объём строго по GUI_SPEC.md.
- Отчёт: `../tasks_done/phase_6_done.md` (2026-06-30). 346 passed; GUI-обёртка PySide6 (`gui/`,
  `gui_main.py`) над export/fill; `src/` и `main.py` не изменены. Ревью без блокеров (4 замечания
  устранены: closeEvent, паритет пре-чека/предупреждения с CLI, доп. подтверждение боевого
  авто-режима, SecretMaskingFilter на QLoggingHandler). Известное расхождение (намеренное):
  `export.output_dir` в GUI резолвится от корня проекта, в CLI — от CWD. Боевой end-to-end прогон
  `fill` через GUI отложен (заказчик в отпуске, рабочих дней без учёта в окне 4 дней нет) —
  открытый пункт ручной верификации перед эксплуатацией.

## Фаза 7 — GUI под макет + фильтр «Тип дня» (`PLAN_GUI_FIX.md`) [DONE]
Спецификация — `../PLAN_GUI_FIX.md` (вне `tasks/`, по образцу `GUI_SPEC.md`/`PLAN_GUI.md` Фазы 6;
подфазовых файлов `tasks/phase_7_*.md` не заводили — план уже содержал разбивку §5 на этапы A/B1/B2).
- Этап A — переверстка GUI под макет `design/bitrix24-console-standalone.html`: токены/QSS,
  титульная полоса-баннер (`gui/title_bar.py`), левая колонка (`gui/control_panel.py`: тумблер
  Парсинг/Редактирование, период, пилюли режима, CTA «Получить выписку»), правая тёмная консоль
  (`gui/console_panel.py`: ПРОГРЕСС + ЖУРНАЛ с фильтрами [Все][REST][OK][Ошибки]), меню
  «Настройки», индикатор вебхука, статусбар.
- Этап B1 — фильтр «Тип дня» (`ufCrm46_1742341877`, whitelist `[351]` в `config.yaml`) для `fill`
  (`src/config.py`, `src/workday.py`): серверный `crm.item.list`-фильтр + клиентская защитная
  проверка в `select_candidates`/`select_repair_days`.
- Этап B2 — датапикеры в режиме «Редактирование» дизейблятся (вариант A из плана; вариант B —
  fill уважает диапазон — не реализован, follow-up).
- Решения заказчика: баннер под системным заголовком (не безрамочное окно); whitelist «Типа дня»
  строго `[351]`; датапикеры — дизейбл (вариант A).
- Отчёт: `../tasks_done/phase_7_done.md` (2026-07-01). 375 passed; PNG-рендер offscreen-окна
  сверен с PNG макета глазами (light/dark). Открытый пункт: живая проверка серверного day-type
  фильтра против портала не проводилась (заказчик в отпуске) — рекомендуется перед следующей
  боевой записью `fill`.
- **Доработка (2026-07-01, тот же отчёт, отдельная секция):** по требованию заказчика dry-run
  убран из GUI полностью; левая колонка стала режим-зависимой (Парсинг: даты/период;
  Редактирование: `QComboBox` метода «По-умолчанию»/«Индивидуально», поля «Описание/Часов
  по-умолчанию», чекбоксы дней 4-дневного окна с датами, «Запланировать»); боевая запись гейтится
  обязательным диалогом подтверждения (`QMessageBox` для обоих методов безусловно, per-day
  `ConfirmDialog` дополнительно для «Индивидуально»); диалог `gui/settings_dialog.py` (меню
  «Настройки → Значения по-умолчанию…») редактирует те же дефолты, что и инлайн-поля колонки
  (зеркало, не единственное место). CLI dry-run не затронут.
- **Ретроспектива (2026-07-01, отдельная секция того же отчёта):** доки приведены в соответствие
  с фактическим кодом GUI после нескольких итераций, где состояние выдавалось за «готово»/«сверено
  с макетом» без реальной визуальной проверки. Честно зафиксировано, что чекбоксы дней и инлайн-
  поля индивидуальных значений («Индивидуально») — пока только UI и не управляют реальной записью;
  «Запланировать» — визуально, без реализации; серверный фильтр «Тип дня» не проверен вживую;
  offscreen-рендеры не показывают кириллицу. Подробности — `../README.md` (раздел «Графический
  интерфейс (GUI)» → «Известные ограничения GUI») и `../tasks_done/phase_7_done.md`.

## Отложенные
- `phase_delayed_1_01_task_elapseditem.md` — альтернативный механизм через task.elapseditem
- `phase_delayed_1_02_browser_fallback.md` — браузерный fallback (REST полностью покрывает «Выполнено» через crm.activity.update; fallback не нужен)
- `phase_delayed_1_03_scheduler.md` — автозапуск по расписанию

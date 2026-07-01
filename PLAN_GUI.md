# План: GUI для Bitrix24 «Рабочий день» (PySide6)

## Контекст

Проект `bitrix24` — это CLI (`main.py`) над модулями `src/` для выгрузки «Рабочего дня» в
Excel (`export`) и автозаполнения «Учёта рабочего времени» (`fill`). Задание `GUI_SPEC.md`
(+ визуальный макет `design/bitrix24-console-standalone.html`) требует **десктопную GUI-обёртку
на PySide6**, переиспользующую design system соседнего проекта `United_Stand_Platform` (USP).

GUI — **только обёртка**: вся бизнес-логика остаётся в `src/`, CLI продолжает работать
параллельно. Цель — дать неконсольный, потокобезопасный интерфейс к `export`/`fill` с живым
логом, таблицей результатов, цветовой индикацией статусов и интерактивным подтверждением записи.

### Решения (подтверждены пользователем)
1. **Theme:** копируем (вендорим) `theme_service.py` и `main.qss` из USP в `bitrix24`, адаптируем
   токены/QSS под проект. Без живой зависимости от пути USP.
2. **Интерактивный fill:** `src/` НЕ меняем (спека §10). Подтверждение перехватываем
   **monkeypatch `builtins.input`** в воркере, маршрутизируя в `QDialog` через сигнал + блокировку.
3. **Объём:** строго по `GUI_SPEC.md`. «Расписание/Настройки/Фильтр» из макета — вне задания.

---

## Что переиспользуем из существующего кода (НЕ переписывать)

Точные сигнатуры (проверены):

- **`src/config.py`** — `load_config() -> Config`. `config.export` (dict: `output_dir`,
  `filename_pattern`), `config.defaults` (dict: `task_description`, `hours`, `contract_tech_id`),
  `config.env`. Передаётся в `MainWindow`.
- **`src/b24.py`** — `B24(config)`, `B24.smoke() -> {id, full_name, time_zone, raw}`,
  `B24.resolve_users(ids: List[int]) -> {id: "ФИО"}` (кэшируется).
- **`src/workday.py`** — `read_days(b24, config, date_from, date_to) -> List[WorkdayDay]`,
  `read_logs(b24, config, days) -> List[WorkLog]` (мутирует `day.logs`).
  - `WorkdayDay`: `id, date, title, employee, works_ids, raw, logs`.
  - `WorkLog`: `id, parent_day_id, title, description, hours, contract, result, raw`.
- **`src/fill.py`** — `run_fill(b24, cfg, *, dry_run, interaction, today, limit=5) -> List[dict]`.
  - Интерактив реализован через `collect_values()` с прямыми `input()` (строки 135, 150).
    Протокол ответов: промпт «Описание» принимает `Enter`/текст/`skip`/`abort`/`all`;
    промпт «Количество часов» принимает `Enter`/число/`skip`/`abort` (переспрос при невалиде).
  - Ключи result-dict: `day_id, date, status` (+ при успехе: `new_id, verify_ok, reason,
    activity_status, activity_ids, activity_ok`; при skip/error: `reason`).
  - Значения `status`: `filled, repaired, dry-run, skipped, already-closed, error, aborted`.
- **`src/logging_setup.py`** — логгер `logging.getLogger("workday")`; секреты уже маскируются
  `SecretMaskingFilter`. GUI **добавляет свой handler** к этому логгеру (не трогая root).
- **`main.py`** — эталон порядка вызовов (`_cmd_export` строки 111–184, `_cmd_fill` 187–276):
  load_config → setup_logging → smoke → команда. GUI повторяет ту же последовательность.

Из USP копируем как образцы/шаблоны (адаптировать, не импортировать вживую):
`unified_stand/core/services/theme_service.py` (ThemeManager + PALETTES, `@token@`-подстановка),
`resources/styles/main.qss` (592 стр., template с `@token@`),
виджеты-паттерны `core/views/{ribbon_widget,log_panel,progress_panel}.py`,
иконки `resources/icons/*.svg`.

---

## Целевая структура (новые файлы, `src/` не трогаем)

```
bitrix24/
├── gui/
│   ├── __init__.py
│   ├── theme.py            # вендоренный ThemeManager + PALETTES (из USP), адаптирован
│   ├── main_window.py      # QMainWindow: меню, layout, статусбар, оркестрация воркеров
│   ├── ribbon.py           # кнопки Выгрузить/Заполнить + toggles Dry-run/Авто-режим
│   ├── params_panel.py     # QDateEdit ×2, описание/часы (активны при !dry_run), кнопка папки
│   ├── result_table.py     # QTableWidget: режимы export/fill, цвета статусов
│   ├── detail_panel.py     # правая панель деталей выбранной строки
│   ├── log_panel.py        # тёмный QTextEdit + QLoggingHandler (сигнал в main thread)
│   ├── confirm_dialog.py   # QDialog подтверждения (день, описание, часы, 4 кнопки)
│   │                       # ИСТОРИЧЕСКОЕ: удалён в Фазе 8.01 (per-day диалог заменён
│   │                       #   перехватом input() по левой панели) — см. tasks_done/phase_8_done.md
│   └── worker.py           # QThread воркеры export/fill + monkeypatch input()
├── resources/styles/main.qss   # копия из USP + селекторы bitrix24
└── gui_main.py             # точка входа GUI
```

---

## Этапы реализации (делегирование по CLAUDE.md §2)

Спец-файлы фаз — в `tasks/` (`phase_gui_NN_*.md`); отчёты по завершении — в `tasks_done/`.

### Фаза GUI-1 — Каркас + тема (coder-expert)
- `gui/theme.py`: вендорить ThemeManager (singleton `theme`, `apply(app, mode)`,
  `theme_changed` Signal, `current_palette()`); PALETTES со всеми токенами из спеки §3.4/§8
  (`accent #0078d4`, `status_ok/fail/running/warning`, `text_muted`, `log_bg #1e1e1e`,
  `log_text #d4d4d4` — лог всегда тёмный). Frozen-aware пути допустимо упростить (без PyInstaller).
- `resources/styles/main.qss`: копия из USP, оставить `@token@`-механизм; добавить селекторы
  для ribbon/таблицы/лога bitrix24.
- `gui_main.py` + `gui/main_window.py`: каркас окна (меню Файл/Вид/Справка, центральный layout,
  статусбар, `theme.apply(app, "system")`), без логики операций. `load_config()` при старте,
  ошибку конфигурации показать в диалоге.

### Фаза GUI-2 — Лог, параметры, статусбар (coder-simple)
- `gui/log_panel.py`: read-only `QTextEdit` (Cascadia Code 10px), `QLoggingHandler(logging.Handler)`
  c сигналом `record_emitted(level, msg)` → append в main thread; цвета INFO/WARNING/ERROR
  по спеке §3.6. Handler вешается на `logging.getLogger("workday")`.
- `gui/params_panel.py`: `QDateEdit` ×2 (дд.мм.гггг), поле «Описание» (QLineEdit, дефолт
  `config.defaults["task_description"]`), «Часы» (QDoubleSpinBox, дефолт `config.defaults["hours"]`);
  описание/часы `setEnabled(not dry_run)`; кнопка «Открыть папку» → `out_dir` в проводнике.
- Статусбар: счётчики OK/Предупр./Ошибки + текст состояния + indeterminate `QProgressBar`.

### Фаза GUI-3 — Ribbon + воркеры + таблица/детали (coder-expert)
- `gui/ribbon.py`: кнопки «Выгрузить»/«Заполнить» (иконки export.svg/fill.svg), toggles
  Dry-run (по умолчанию ВКЛ) и Авто-режим (`no_interaction`).
- `gui/worker.py`:
  - `ExportWorker(QThread)`: повторяет `_cmd_export` (read_days→read_logs→resolve_users→
    build_workbook — **либо** просто вызвать пайплайн), сигналы `log_message`,
    `result_ready(list[WorkdayDay])`, `finished(int)`.
  - `FillWorker(QThread)`: вызывает `run_fill(...)`; сигнал `result_ready(list[dict])`.
    **Интерактив:** при `interaction=True` в `run()` обернуть `builtins.input` (try/finally
    восстановить). Логика фейкового input:
    - распознаёт промпт по подстроке: «Описание» (старт дня) / «Количество часов».
    - на промпте «Описание»: если в воркере закэшировано «применить ко всем» — вернуть
      сохранённое описание (без диалога); иначе emit `confirm_requested(day_info)`,
      блокировать поток (`QSemaphore`/`QWaitCondition`), дождаться выбора из `confirm_dialog`:
      OK→вернуть текст описания (этот день); Применить-ко-всем→закэшировать (desc,hours) и
      вернуть описание; Пропустить→вернуть `"skip"`; Отмена→вернуть `"abort"`.
    - на промпте «Количество часов»: вернуть сохранённое для текущего дня число строкой.
    > Так «применить ко всем» уважает отредактированные значения (не форсит дефолты, как
    > токен `all` в `collect_values`). `run_fill` вызывает `collect_values` каждый день с
    > `applied_to_all=None` — фейковый input короткозамыкает повтор сам.
  - Общая база воркера: load_config (или из MainWindow), `B24(config)`, smoke перед операцией
    (как `_smoke_or_exit`), блокировка кнопок ribbon на время работы.
- `gui/result_table.py`: два режима колонок (export/fill по спеке §3.4); цвет ячейки «Статус»
  по `status` → токен палитры (filled/repaired→status_ok, dry-run→status_running,
  skipped/already-closed→text_muted, error→status_fail, aborted→status_warning).
- `gui/detail_panel.py`: по клику строки — детали дня (учёты: title/description/hours) или
  результата fill (status/reason/new_id/activity_status/activity_ids/verify_ok).

### Фаза GUI-4 — Диалог подтверждения + меню (coder-simple)
> Историческая секция: per-day диалог, описанный ниже, реализован в Фазе 6, затем УДАЛЁН в
> Фазе 8.01 (метод «Индивидуально» теперь пишет по выбору левой панели без диалога) — см.
> `tasks_done/phase_8_done.md`. Текст ниже сохранён как есть, не переписан задним числом.
- `gui/confirm_dialog.py`: `QDialog` (день+id, «Описание задачи» QLineEdit, «Количество часов»
  QDoubleSpinBox, кнопки [Пропустить][Применить ко всем][Отмена][OK]); возвращает структуру
  выбора в main_window, которая разблокирует воркер.
- Меню: Файл→Открыть выгрузку (Excel) / Выход; Вид→Тема (Светлая/Тёмная/Системная — `theme.apply`)
  / Масштаб; Справка→О программе.

### Фаза GUI-5 — Зависимости, тесты, документация
- `requirements.txt`: добавить `PySide6>=6.6.0`.
- Тесты (tester, без сети, без реального Qt-цикла где возможно):
  - чистая логика фейкового `input()` / маппинг кнопок диалога → токены (`skip`/`abort`/текст);
  - кэш «применить ко всем»;
  - маппинг `status` → токен цвета;
  - построение строк таблицы из `WorkdayDay`/result-dict.
  GUI-виджеты — smoke-инстанцирование с `QApplication` (offscreen) при возможности.
- reviewer: ревью гардов (нет утечки `webhook_code` в лог-панель/детали; Dry-run по умолчанию;
  политика 4 дней не обходится — она внутри `run_fill`/`_reread_guard`, GUI её не дублирует).
- docs-keeper: отчёты в `tasks_done/`, обновить README (раздел про GUI + запуск `gui_main.py`),
  индекс `tasks/INDEX.md`.

---

## Ключевые риски / заметки
- **Потокобезопасность Qt:** все обновления UI — только в main thread через сигналы. Воркер
  не трогает виджеты напрямую. Блокировка input-воркера — `QSemaphore`/`QWaitCondition`.
- **monkeypatch `builtins.input`** глобален: патчить строго в `FillWorker.run()` с `try/finally`;
  GUI больше нигде `input()` не вызывает. Распознавание промпта — по стабильным подстрокам
  «Описание»/«Количество часов» (их менять в `src/fill.py` нельзя).
- **Безопасность (CLAUDE.md §4/§5):** Dry-run toggle включён по умолчанию; боевая запись только
  при выключенном Dry-run (GUI передаёт это как эквивалент `--confirm-write`). Лог уже маскирует
  секрет; детали/таблица не выводят `raw`-поля с секретами.
- **`src/` неизменен** — если выяснится, что монки-патч не покрывает кейс, остановиться и
  согласовать с пользователем (а не править `src/`).

---

## Верификация (end-to-end)
1. `venv/Scripts/python.exe -m pip install -r requirements.txt` (ставит PySide6).
2. `venv/Scripts/python.exe -m pytest -q` — все тесты зелёные (логика GUI + существующие).
3. `venv/Scripts/python.exe gui_main.py` — окно открывается, тема применяется, лог показывает
   «Доступ ОК…» после smoke.
4. **Export:** выбрать период → «Выгрузить» → таблица заполняется днями, правая панель — детали,
   создаётся `out/workday_*.xlsx`, статусбар обновляет счётчики.
5. **Fill dry-run** (toggle ВКЛ): «Заполнить» → строки со статусом `dry-run` (жёлтый),
   в прод ничего не пишется.
6. **Fill интерактив** (Dry-run ВЫКЛ, Авто-режим ВЫКЛ): по дню всплывает диалог; проверить
   OK / Пропустить / Применить-ко-всем / Отмена; статусы окрашиваются (`filled`/`skipped`/`error`).
7. Переключение тем (Светлая/Тёмная/Системная) перекрашивает UI; лог-панель остаётся тёмной.

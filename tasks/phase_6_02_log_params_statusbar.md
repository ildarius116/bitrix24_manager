# phase_6_02 — Лог-панель, панель параметров, статусбар

**Статус:** TODO
**Фаза:** 6 (GUI)
**Зависит от:** phase_6_01
**Связано:** `GUI_SPEC.md` §3.3, §3.6, §3.7; `PLAN_GUI.md`.
**Агент:** `coder-simple`.

## Цель
Реализовать пассивные виджеты окна: живой лог (перехват `logging`), форму параметров команд
и статусбар со счётчиками.

## Объём работ
- `gui/log_panel.py`:
  - read-only `QTextEdit`, шрифт Cascadia Code 10px (fallback Consolas), тёмный фон (`log_bg`).
  - `QLoggingHandler(logging.Handler)` с Signal `record_emitted(str level, str msg)` → append
    выполняется в main thread (handler сам в любом потоке — emit сигнала потокобезопасен).
  - цвета уровней по §3.6: INFO `#d4d4d4`, WARNING `#dcdcaa`, ERROR `#f44747`.
  - handler вешается на `logging.getLogger("workday")` (root не трогаем — секрет уже маскируется).
- `gui/params_panel.py`:
  - Export: `QDateEdit` «Дата с» и «Дата по» (формат дд.мм.гггг, календарь), кнопка «Открыть папку»
    (`out_dir` из `config.export["output_dir"]` в проводнике через `QDesktopServices`).
  - Fill: «Описание задачи» (`QLineEdit`, дефолт `config.defaults["task_description"]`),
    «Количество часов» (`QDoubleSpinBox`, дефолт `config.defaults["hours"]`).
  - Поля описания/часов активны только при выключенном Dry-run (`setEnabled(not dry_run)`).
  - Публичные геттеры значений для воркеров (date_from/date_to/description/hours).
- Статусбар (в `main_window.py`): счётчики OK / Предупреждения / Ошибки; текст состояния
  («Готов»/«Выполняется...»/«Ошибка»); indeterminate `QProgressBar` (виден во время операции).
  Метод обновления счётчиков по списку результатов.

## Критерии приёмки (DoD)
- Лог-панель показывает записи логгера `workday` с корректными цветами уровней; секрет не виден.
- Даты вводятся/читаются в дд.мм.гггг; кнопка «Открыть папку» открывает `out`.
- Описание/часы дизейблятся при Dry-run; дефолты берутся из config.
- Статусбар обновляет счётчики и текст; прогрессбар indeterminate переключается.

## Артефакты
`gui/log_panel.py`, `gui/params_panel.py`, правки `gui/main_window.py` (статусбар, сборка панелей).

# phase_6_05 — Зависимости, тесты, ревью, документация

**Статус:** DONE — отчёт: `../tasks_done/phase_6_done.md` (2026-06-30)
**Фаза:** 6 (GUI)
**Зависит от:** phase_6_01–04
**Связано:** `GUI_SPEC.md` §9; `PLAN_GUI.md`; CLAUDE.md §3, §4.
**Агенты:** `tester` (тесты), `reviewer` (ревью), `docs-keeper` (доки/отчёты).

## Цель
Закрыть фазу GUI: зафиксировать зависимость, покрыть логику тестами, провести ревью безопасности
и обновить документацию/трекинг.

## Объём работ
- Зависимости: добавить `PySide6>=6.6.0` в `requirements.txt`.
- Тесты (`tester`, без сети; Qt-логика — без полноценного event loop где возможно,
  виджеты — offscreen `QT_QPA_PLATFORM=offscreen`):
  - чистая логика фейкового `input()`: маппинг кнопок диалога → ответы (`skip`/`abort`/текст/число);
  - кэш «применить ко всем» (последующие дни не вызывают диалог);
  - распознавание промпта по подстроке «Описание»/«Количество часов»;
  - маппинг `status` → токен цвета (filled/repaired/dry-run/skipped/already-closed/error/aborted);
  - построение строк таблицы из `WorkdayDay` и result-dict;
  - smoke-инстанцирование ключевых виджетов с `QApplication`.
- Ревью (`reviewer`, read-only):
  - нет утечки `webhook_code` в лог-панель/детали/таблицу (`raw` не выводится);
  - Dry-run по умолчанию ВКЛ; боевая запись только при выключенном Dry-run;
  - политика 4 дней не дублируется/не обходится в GUI (живёт в `run_fill`/`_reread_guard`);
  - `src/` и `main.py` не изменены; потокобезопасность Qt (UI только в main thread);
  - monkeypatch `builtins.input` восстанавливается в `finally`.
- Документация (`docs-keeper`):
  - README — раздел про GUI и запуск `venv/Scripts/python.exe gui_main.py`;
  - отчёт в `tasks_done/phase_6_done.md` (что/как сделано, DoD, результаты прогонов);
  - обновить `tasks/INDEX.md` (статусы фазы GUI).

## Критерии приёмки (DoD)
- `venv/Scripts/python.exe -m pip install -r requirements.txt` ставит PySide6.
- `venv/Scripts/python.exe -m pytest -q` — зелёный (существующие + новые тесты).
- Ревью без блокеров (или замечания устранены и переревью пройдено — см. память «close-review-loop»).
- README/INDEX обновлены; отчёт в `tasks_done/`.

## Артефакты
`requirements.txt`, `tests/test_gui_*.py`, `README.md`, `tasks_done/phase_6_done.md`,
`tasks/INDEX.md`.

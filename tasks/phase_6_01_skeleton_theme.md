# phase_6_01 — Каркас окна + вендоренная тема (ThemeManager + QSS)

**Статус:** TODO
**Фаза:** 6 (GUI)
**Зависит от:** —
**Связано:** `GUI_SPEC.md` §2, §3.1, §6, §7, §8; `PLAN_GUI.md`.
**Агент:** `coder-expert`.

## Цель
Поднять каркас приложения PySide6 и перенести design system из USP так, чтобы окно открывалось,
тема применялась и переключалась, а структура файлов соответствовала спеке.

## Объём работ
- `gui/theme.py` — вендорить `ThemeManager` из
  `United_Stand_Platform/unified_stand/core/services/theme_service.py`:
  singleton `theme`, `apply(app, mode)` (`light`/`dark`/`system`), Signal `theme_changed(dict)`,
  `current_palette()`, `current_mode()`. Механизм подстановки `@token@` в QSS сохранить.
  Frozen-aware пути допустимо упростить (PyInstaller не требуется).
- PALETTES — перенести все токены из спеки §3.4/§8: `accent #0078d4`, `status_ok/fail/running/warning`,
  `text_muted`, `log_bg #1e1e1e` / `log_text #d4d4d4` (лог всегда тёмный в обеих темах),
  плюс базовые `bg_window/bg_surface/border/text` (light/dark пары из USP).
- `resources/styles/main.qss` — копия из USP (template с `@token@`); удалить специфичные для USP
  селекторы, оставить/добавить нужные bitrix24 (QMainWindow, QMenuBar/QMenu, ribbon, таблица, лог,
  статусбар, QProgressBar, инпуты, скроллбары).
- `gui_main.py` — точка входа: `QApplication`, `load_config()` (ошибку конфигурации — в QMessageBox),
  создать `MainWindow(config)`, `theme.apply(app, "system")`, `window.show()`, `app.exec()`.
- `gui/__init__.py`, `gui/main_window.py` — `QMainWindow`: меню (Файл/Вид/Справка — пункты-заглушки),
  центральный layout-плейсхолдер (ribbon/параметры/таблица|детали/лог), статусбар. Без логики операций.

## Критерии приёмки (DoD)
- `venv/Scripts/python.exe gui_main.py` открывает окно без ошибок; тема применена.
- Структура файлов из `PLAN_GUI.md` создана; `src/` и `main.py` не изменены.
- Переключение `theme.apply(app, "light"/"dark"/"system")` перекрашивает окно (проверка вручную).
- Лог-область (плейсхолдер) визуально тёмная независимо от темы.

## Артефакты
`gui/theme.py`, `gui/main_window.py`, `gui/__init__.py`, `gui_main.py`,
`resources/styles/main.qss`.

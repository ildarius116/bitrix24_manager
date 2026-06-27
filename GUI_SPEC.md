# GUI Specification — Bitrix24 «Рабочий день»

## 1. Контекст

Проект `bitrix24` — Python/PySide6 десктопное приложение для автоматизации работы с
порталом Bitrix24. Функционал реализован в `src/` (CLI через `main.py`). GUI — обёртка
над существующим кодом.

**Стек GUI:** PySide6 (Qt6), аналогично проекту `United_Stand_Platform`.

**Design System:** `United_Stand_Platform` — использовать `ThemeManager` и `main.qss` из
`C:\Users\Ildar.Sabirov\Documents\projects\United_Stand_Platform\unified_stand\resources\styles\main.qss`
и `core/services/theme_service.py`. Цветовые токены (light/dark) и шрифты — идентичны USP.

---

## 2. Структура окна

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Меню: Файл / Вид / Справка]                    Bitrix24 Рабочий день │
├─────────────────────────────────────────────────────────────────────┤
│  RIBBON (панель инструментов — одна строка кнопок)                  │
│  [▶ Выгрузить]  [✏ Заполнить]  [──]  [○ Dry-run]  [○ Авто-режим]  │
├─────────────────────────────────────────────────────────────────────┤
│  ПАРАМЕТРЫ (под ribbon — форма в одну строку)                       │
│  Дата с: [дд.мм.гггг ▼]   по: [дд.мм.гггг ▼]   [Путь к файлу 📁] │
├──────────────────────────────────────────┬──────────────────────────┤
│  ТАБЛИЦА РЕЗУЛЬТАТОВ (основная область)  │  ПАНЕЛЬ ДЕТАЛЕЙ          │
│                                          │  (правая, фикс. ширина)  │
│  Дата | Сотрудник | Статус | Учёты | Ч  │  Детали выбранной строки │
│  ─────|───────────|────────|───────|─── │  или результата операции │
│  ...  │           │        │       │    │                          │
│                                          │                          │
├──────────────────────────────────────────┴──────────────────────────┤
│  ЛОГ (тёмная панель, высота ~150px, шрифт Cascadia Code 10px)      │
│  [13:04:01] Доступ ОК: Иванов И.И. (id 1), TZ Europe/Moscow        │
├─────────────────────────────────────────────────────────────────────┤
│  СТАТУСБАР: OK 0  Предупреждения 0  Ошибки 0       Готов           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Компоненты

### 3.1. Меню

- **Файл** → Открыть выгрузку (Excel) | Выход
- **Вид** → Тема (Светлая / Тёмная / Системная) | Масштаб
- **Справка** → О программе

### 3.2. Ribbon

Одна группа «Действия»:

| Кнопка | Иконка | Действие |
|--------|--------|----------|
| Выгрузить | export.svg | Запустить `_cmd_export` |
| Заполнить | fill.svg | Запустить `_cmd_fill` |

Toggles (CheckBox в ribbon):
- **Dry-run** — передаёт `dry_run=True` в `_cmd_fill` (по умолчанию включён)
- **Авто-режим** — `no_interaction=True` для fill (без интерактивного подтверждения)

### 3.3. Панель параметров

Для команды **Export**:
- `QDateEdit` «Дата с» (формат дд.мм.гггг)
- `QDateEdit` «Дата по» (формат дд.мм.гггг)
- Кнопка «Открыть папку» (показать папку `./out` в проводнике)

Для команды **Fill**:
- Поля «Описание задачи» (QLineEdit, дефолт из config.yaml)
- Поле «Количество часов» (QDoubleSpinBox, дефолт из config.yaml)
- Эти поля активны только если Dry-run выключен

### 3.4. Таблица результатов

**Export** — отображает `WorkdayDay`:

| Колонка | Источник |
|---------|---------|
| Дата | `day.date` |
| Сотрудник | `day.employee` (резолвится через `user_map`) |
| Заполнено | `len(day.works_ids)` учётов |
| Часы | сумма `log.hours` по `day.logs` |
| Учёты | список `log.title` через запятую |

**Fill** — отображает результат `run_fill`:

| Колонка | Источник |
|---------|---------|
| Дата | `row["date"]` |
| ID дня | `row["day_id"]` |
| Статус | `row["status"]` (цветовая индикация) |
| Новый учёт | `row.get("new_id", "—")` |
| Дело | `row.get("activity_status", "—")` |
| Причина | `row.get("reason", "")` |

**Цвета статусов** (из Design System):
- `filled` / `repaired` → `status_ok` (#107c10 light / #4ec9b0 dark)
- `dry-run` → `status_running` (жёлтый)
- `skipped` / `already-closed` → `text_muted` (серый)
- `error` → `status_fail` (#d83b01 light / #f44747 dark)
- `aborted` → `status_warning`

### 3.5. Панель деталей (правая)

При клике на строку таблицы показывает:

**Export** — детали дня:
- Дата, ID, Сотрудник
- Список учётов: для каждого `WorkLog` → название + описание + часы

**Fill** — детали результата:
- Статус, причина, ID нового учёта
- Статус дела (activity_status, activity_ids)
- Результат верификации (verify_ok)

### 3.6. Панель лога

- Тёмный фон (`log_bg: #1e1e1e`), светлый текст (`log_text: #d4d4d4`)
- Шрифт: Cascadia Code 10px (fallback: Consolas)
- `QTextEdit` в режиме read-only
- Перехватывает `logging` через кастомный `QLoggingHandler`
- Цвета уровней:
  - INFO → `#d4d4d4`
  - WARNING → `#dcdcaa`
  - ERROR → `#f44747`

### 3.7. Статусбар

- Счётчики: OK | Предупреждения | Ошибки (обновляются после каждой операции)
- Справа: текущий статус («Готов» / «Выполняется...» / «Ошибка»)

---

## 4. Асинхронность

**Критично:** все операции (`_cmd_export`, `run_fill`) выполняются в `QThread` (Worker),
чтобы не блокировать UI.

```python
class Worker(QThread):
    log_message = Signal(str, str)   # (уровень, текст)
    result_ready = Signal(object)    # список результатов
    finished = Signal(int)           # код возврата

    def run(self):
        # вызов _cmd_export или run_fill
        pass
```

Во время выполнения:
- Кнопки Ribbon заблокированы
- В статусбаре «Выполняется...» + анимация (QProgressBar в indeterminate режиме)
- Лог обновляется в реальном времени через сигналы

---

## 5. Интерактивный режим Fill

Когда Dry-run **выключен** и Авто-режим **выключен**, `fill` ожидает подтверждения
по каждому дню (FR-2.1.10.2/3).

В GUI это реализуется через `QDialog`:

```
┌─ Подтверждение записи ────────────────────────────────┐
│  День: 25.06.2026 (id 270608)                         │
│                                                        │
│  Описание задачи:  [Общие задачи подразделения      ] │
│  Количество часов: [8.0                             ] │
│                                                        │
│  [Пропустить]  [Применить ко всем]  [Отмена]  [OK]   │
└────────────────────────────────────────────────────────┘
```

Worker передаёт запрос подтверждения через сигнал в main thread, ждёт ответа через `QSemaphore`.

---

## 6. Структура файлов GUI

```
bitrix24/
├── gui/
│   ├── __init__.py
│   ├── main_window.py      # QMainWindow — главное окно
│   ├── ribbon.py           # Ribbon-панель (кнопки + toggles)
│   ├── params_panel.py     # Панель параметров (даты, описание, часы)
│   ├── result_table.py     # QTableWidget/QTableView для результатов
│   ├── detail_panel.py     # Панель деталей (правая)
│   ├── log_panel.py        # Панель лога + QLoggingHandler
│   ├── confirm_dialog.py   # Диалог подтверждения для fill
│   ├── worker.py           # QThread Workers для export и fill
│   └── theme.py            # Подключение ThemeManager из USP
├── resources/
│   └── styles/
│       └── main.qss        # Скопировать из USP + добавить специфику bitrix24
└── gui_main.py             # Точка входа GUI (альтернатива main.py для CLI)
```

---

## 7. Точка входа

`gui_main.py` — запускает GUI вместо CLI:

```python
import sys
from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow
from src.config import load_config

def main():
    app = QApplication(sys.argv)
    config = load_config()
    window = MainWindow(config)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
```

---

## 8. Design System — ссылки

Все стили берутся из USP:

```
C:\Users\Ildar.Sabirov\Documents\projects\United_Stand_Platform\
  unified_stand\resources\styles\main.qss        ← QSS шаблон
  unified_stand\core\services\theme_service.py   ← PALETTES + ThemeManager
  unified_stand\resources\icons\                 ← иконки (Feather SVG)
```

**Ключевые токены:**
- Акцент: `#0078d4` (Windows Blue)
- Шрифт UI: Segoe UI, 12px
- Шрифт лога: Cascadia Code, 10px
- Лог фон: `#1e1e1e` (всегда тёмный, в обеих темах)

---

## 9. Зависимости

Добавить в `requirements.txt`:
```
PySide6>=6.6.0
```

Остальные зависимости уже в `requirements.txt` проекта.

---

## 10. Что НЕ нужно менять

- `src/` — весь существующий код остаётся без изменений
- `main.py` — CLI продолжает работать параллельно с GUI
- `config.yaml` / `.env` — конфигурация та же

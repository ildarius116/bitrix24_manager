# phase_1_01 — Каркас проекта, конфигурация, CLI, логирование

**Статус:** TODO
**Фаза:** 1 (Каркас + REST-обёртка)
**Зависит от:** —

## Цель
Создать функционально законченный скелет: структура `src/`, загрузка конфигурации,
точка входа CLI и логирование с маскированием секрета.

## Объём работ
- `src/config.py`:
  - читает `.env` (`B24_DOMAIN`, `B24_AUTH_MODE`, `B24_WEBHOOK_USER_ID`, `B24_WEBHOOK_CODE`, опц. `B24_AUDIT_FILE`);
  - читает `config.yaml` (`entity.*`, `fields.*`, `contract_general_tasks`, `edit_window_days`, `defaults.*`, `export.*`, `runtime.*`);
  - валидация: понятная ошибка, если не задан `B24_WEBHOOK_CODE`.
- `src/logging_setup.py`: консоль (UTF-8) + файл `out/run.log`, фильтр-маскировщик секрета вебхука.
- `main.py`: `argparse` с подкомандами `export` (`--date-from`, `--date-to`) и `fill` (`--dry-run`, `--no-interaction`); общий разбор + вызов соответствующих модулей (заглушки на этой фазе).
- `src/dates.py`: `parse_cli_date` (дд.мм.гггг / ГГГГ-ММ-ДД), `extract_date` (из title), `within_edit_window(entry, days, today)`.

## Критерии приёмки (DoD)
- `python main.py --help`, `... export --help`, `... fill --help` работают.
- Запуск без `.env` даёт понятную ошибку про переменные окружения.
- Юнит-проверка `dates.py` (парсинг, окно 4 дня) проходит на синтетике.
- В логах код вебхука не виден (маскируется).

## Артефакты
`src/config.py`, `src/logging_setup.py`, `src/dates.py`, `main.py`, обновлённый `requirements.txt`.

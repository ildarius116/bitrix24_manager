#!/usr/bin/env python3
"""CLI «Рабочий день» Bitrix24: подкоманды export / fill.

Фаза 1 (Каркас + REST-обёртка): тела export/fill — заглушки. Общий старт делает:
1. загрузку конфигурации (src.config.load_config) — секрет берётся из .env, не печатается;
2. инициализацию логирования (src.logging_setup) с маскированием кода вебхука;
3. read-only smoke-проверку доступа к порталу (user.current) перед работой.

Безопасность (CLAUDE.md §4, §5): код вебхука — СЕКРЕТ, не печатается и маскируется
в логах. Записей в прод нет: вызывается только read-only user.current. crm.item.add
на этой фазе не выполняется.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from typing import List, Optional

from pathlib import Path

from src.b24 import B24, B24Error
from src.config import Config, ConfigError, load_config
from src.dates import DateParseError, parse_cli_date, today_moscow
from src.export_excel import build_workbook
from src.fill import run_fill
from src.logging_setup import setup_logging
from src.workday import read_days, read_logs

log = logging.getLogger("workday")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Bitrix24 «Рабочий день»: выгрузка (export) и автозаполнение (fill).",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="{export,fill}")

    export_p = subparsers.add_parser(
        "export",
        help="Выгрузить таблицу «Рабочий день» в Excel за период.",
        description="Выгрузка «Рабочего дня» за период [--date-from; --date-to].",
    )
    export_p.add_argument(
        "--date-from",
        required=True,
        metavar="ДАТА",
        help="Начало периода (дд.мм.гггг или ГГГГ-ММ-ДД).",
    )
    export_p.add_argument(
        "--date-to",
        required=True,
        metavar="ДАТА",
        help="Конец периода (дд.мм.гггг или ГГГГ-ММ-ДД).",
    )

    fill_p = subparsers.add_parser(
        "fill",
        help="Автозаполнение «Работы/задачи за день» (создание учётов).",
        description="Автозаполнение учётов рабочего времени. По умолчанию — dry-run.",
    )
    fill_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать план, ничего не писать в прод (рекомендуется).",
    )
    fill_p.add_argument(
        "--no-interaction",
        action="store_true",
        help="Не спрашивать подтверждение (для автозапуска).",
    )
    fill_p.add_argument(
        "--confirm-write",
        action="store_true",
        help=(
            "ЯВНЫЙ гейт боевой записи в прод. Без него (или вместе с --dry-run) — только dry-run. "
            "Боевой режим (crm.item.add 1218 + закрытие дела «Выполнено» crm.activity.update, "
            "FR-2.1.7) — лишь при --confirm-write БЕЗ --dry-run."
        ),
    )

    return parser


def _smoke_or_exit(config: Config) -> None:
    """Read-only smoke-проверка доступа. При ошибке — понятное сообщение и выход.

    Печатает «Доступ ОК: <ФИО> (id N), TZ <таймзона>». Секрет не печатается.
    """
    try:
        client = B24(config)
        info = client.smoke()
    except B24Error as exc:
        log.error("Нет доступа к порталу при smoke-проверке: %s", exc)
        sys.exit(2)
    except Exception as exc:  # сетевые/непредвиденные — без трейсбека для пользователя
        log.error("Непредвиденная ошибка при smoke-проверке доступа: %s", exc)
        sys.exit(2)

    log.info(
        "Доступ ОК: %s (id %s), TZ %s",
        info["full_name"],
        info["id"] or "?",
        info["time_zone"],
    )


def _cmd_export(config: Config, args: argparse.Namespace) -> int:
    """Выгрузка «Рабочего дня» за период в Excel (FR-1). ТОЛЬКО ЧТЕНИЕ."""
    date_from: date = parse_cli_date(args.date_from)
    date_to: date = parse_cli_date(args.date_to)
    if date_from > date_to:
        log.error(
            "Начало периода (%s) позже конца (%s). Поменяйте --date-from/--date-to.",
            date_from.isoformat(),
            date_to.isoformat(),
        )
        return 2

    log.info("export: период %s … %s", date_from.isoformat(), date_to.isoformat())

    b24 = B24(config)
    # 1) Дни (1208) за период — полная пагинация.
    days = read_days(b24, config, date_from, date_to)
    # 2) Связанные учёты (1218) по «Работам за день».
    read_logs(b24, config, days)

    # 3) Резолвинг сотрудников: собираем уникальные employee id, запрашиваем одной пачкой.
    unique_employee_ids: List[int] = []
    seen_emp: set = set()
    for day in days:
        raw = day.employee.strip() if day.employee else ""
        if not raw:
            continue
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            continue
        if uid not in seen_emp:
            seen_emp.add(uid)
            unique_employee_ids.append(uid)

    user_map: dict = {}
    if unique_employee_ids:
        resolved = b24.resolve_users(unique_employee_ids)
        # Ключи в user_map — строки (employee хранится как str).
        user_map = {str(uid): name for uid, name in resolved.items()}
        log.info(
            "Резолвинг сотрудников: %d уникальных id → %d найдено ФИО (кэш b24).",
            len(unique_employee_ids),
            sum(1 for n in resolved.values() if not n.startswith("id ")),
        )

    # 4) Путь файла из export.filename_pattern (config.yaml).
    out_dir = Path(config.export.get("output_dir", "./out"))
    if not out_dir.is_absolute():
        out_dir = Path(__file__).resolve().parent / out_dir
    pattern = config.export.get("filename_pattern", "workday_{date_from}_{date_to}.xlsx")
    filename = pattern.format(date_from=date_from.isoformat(), date_to=date_to.isoformat())
    out_path = out_dir / filename

    summary = build_workbook(days, date_from, date_to, out_path, user_map=user_map)

    log.info(
        "Выгрузка готова: %s | строк=%s, групп=%s, часов(лист)=%s, часов(группы)=%s, "
        "рамки=%s … %s",
        summary["path"],
        summary["main_rows"],
        summary["group_count"],
        summary["main_hours"],
        summary["group_hours"],
        summary["min_date"].strftime("%d.%m.%Y") if summary["min_date"] else "—",
        summary["max_date"].strftime("%d.%m.%Y") if summary["max_date"] else "—",
    )
    if abs(float(summary["main_hours"]) - float(summary["group_hours"])) > 1e-6:
        log.warning(
            "Сумма часов основного листа (%s) не совпала с группировкой (%s)!",
            summary["main_hours"],
            summary["group_hours"],
        )
    return 0


def _cmd_fill(config: Config, args: argparse.Namespace) -> int:
    """Автозаполнение «Работы/задачи за день» — создание учётов 1218 (Фаза 4).

    Гейт боевой записи (безопасность по умолчанию): реальная запись происходит ТОЛЬКО при
    --confirm-write И БЕЗ --dry-run. По умолчанию (или при --dry-run) — dry_run=True.
    Если заданы оба (--confirm-write и --dry-run) — побеждает dry-run (с предупреждением).
    """
    # Определение режима записи.
    if args.confirm_write and args.dry_run:
        log.warning(
            "Заданы и --confirm-write, и --dry-run: побеждает dry-run, запись НЕ выполняется."
        )
        dry_run = True
    elif args.confirm_write:
        dry_run = False
    else:
        dry_run = True

    interaction = not args.no_interaction
    today = today_moscow()

    # Ранняя проверка наличия полей, необходимых для записи 1218.
    # Отдельные геттеры бросают KeyError при отсутствии ключа в config.yaml.
    def _nonempty(value: str) -> str:
        if not value:
            raise KeyError(value)
        return value

    _fill_config_missing: list = []
    _fill_checks = [
        ("entity.timelog_category_id", lambda: config.timelog_category_id),
        ("entity.timelog_type_id",     lambda: config.timelog_type_id),
        ("fields.log_parent",          lambda: config.field_log_parent),
        ("fields.log_contract",        lambda: config.field_log_contract),
        ("fields.log_contract_tech",   lambda: config.field_log_contract_tech),
        ("fields.log_description",     lambda: config.field_log_description),
        ("fields.log_hours",           lambda: config.field_log_hours),
        ("contract_general_tasks",     lambda: _nonempty(config.contract_general_tasks)),
        ("defaults.contract_tech_id",  lambda: _nonempty(config.contract_tech_id)),
        ("defaults.task_description",  lambda: config.defaults["task_description"]),
        ("defaults.hours",             lambda: config.defaults["hours"]),
    ]
    for _label, _getter in _fill_checks:
        try:
            _getter()
        except (KeyError, TypeError, ValueError):
            _fill_config_missing.append(_label)
    if _fill_config_missing:
        log.error(
            "Для команды fill в config.yaml не хватает полей записи 1218: %s. "
            "Заполните entity.timelog_category_id / fields.log_* / defaults.",
            ", ".join(_fill_config_missing),
        )
        return 2

    if dry_run:
        log.info(
            "fill: режим DRY-RUN (%s). Запись в прод НЕ выполняется — только показ плана.",
            "с подтверждением" if interaction else "без подтверждений",
        )
    else:
        log.warning(
            "БОЕВОЙ РЕЖИМ: будет выполнена запись в прод (crm.item.add 1218 + закрытие дела "
            "«Выполнено» crm.activity.update, FR-2.1.7). Окно 4 дней и пустота перепроверяются "
            "перед каждой записью; «ремонт» закрывает дела и у уже заполненных дней."
        )

    b24 = B24(config)
    try:
        results = run_fill(
            b24,
            config,
            dry_run=dry_run,
            interaction=interaction,
            today=today,
        )
    except ValueError as exc:
        log.error("Ошибка значений/конфигурации при заполнении: %s", exc)
        return 2
    except (KeyboardInterrupt, EOFError):
        log.warning("fill прерван пользователем (Ctrl+C / EOF). Запись не выполнена/прервана.")
        return 2

    has_errors = any(
        r["status"] == "error"
        or r.get("verify_ok") is False
        or r.get("activity_ok") is False
        for r in results
    )
    return 2 if has_errors else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # 1) Конфигурация (секрет из .env). Понятная ошибка без трейсбека.
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    # 2) Логирование с маскированием кода вебхука (секрет в литералах маскировщика).
    setup_logging(secret_literals=(config.env.webhook_code,))
    log.debug("Конфигурация загружена: %s", config.env.masked_summary())

    # 3) Read-only smoke-проверка доступа перед любой работой.
    _smoke_or_exit(config)

    try:
        if args.command == "export":
            return _cmd_export(config, args)
        if args.command == "fill":
            return _cmd_fill(config, args)
    except DateParseError as exc:
        print(f"Ошибка разбора даты: {exc}", file=sys.stderr)
        return 2
    except B24Error as exc:
        log.error("Ошибка работы с порталом: %s", exc)
        return 2

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

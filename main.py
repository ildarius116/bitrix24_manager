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
from src.dates import DateParseError, parse_cli_date
from src.export_excel import build_workbook
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
    """Заглушка fill (фаза 1): подтвердить режим, ничего не писать."""
    mode = "dry-run" if args.dry_run else "обычный (требует явного разрешения на запись)"
    interaction = "без подтверждений" if args.no_interaction else "с подтверждением"
    log.info(
        "fill: режим=%s, %s (заглушка фазы 1 — запись в прод НЕ выполняется).",
        mode,
        interaction,
    )
    return 0


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

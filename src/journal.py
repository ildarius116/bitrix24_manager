"""Локальный журнал обработанных дней с TTL (идемпотентность, phase_5_01).

Дополнительный слой к гарду `_reread_guard` в fill.py: журнал помнит дни, по которым
уже был успешно создан учёт 1218, и при повторном прогоне позволяет пропустить их
без лишней REST-перечитки (пока запись не протухла по TTL). Главной защитой остаётся
сам гард (перечитывание дня перед записью); журнал — лишь оптимизация.

Безопасность: журнал НЕсекретный — хранит только id дня, дату дня, время обработки и
id созданного учёта. Кода вебхука и значений полей в журнале нет. Файл —
`.runtime/processed.json` (каталог в .gitignore); запись атомарна (временный файл +
`os.replace`).

Чистая логика: текущее время (`now_ts`) инъектируется параметром, поэтому модуль
тестируется без сети и без зависимости от системных часов.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger("workday")

# Корень проекта = родитель каталога src/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_JOURNAL_PATH = PROJECT_ROOT / ".runtime" / "processed.json"


def load_journal(path: Path = DEFAULT_JOURNAL_PATH) -> Dict[str, Any]:
    """Прочитать журнал обработанных дней.

    При отсутствии файла или повреждённом/неожиданном содержимом возвращает пустой
    словарь (безопасно): журнал — лишь оптимизация поверх главного гарда, его потеря
    не критична. Повреждение фиксируется через log.warning.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Журнал %s повреждён или нечитаем (%s) — считаю пустым.", p, exc)
        return {}
    if not isinstance(data, dict):
        log.warning("Журнал %s имеет неожиданный формат — считаю пустым.", p)
        return {}
    return data


def is_processed(
    journal: Dict[str, Any],
    day_id: int,
    *,
    ttl_sec: int,
    now_ts: int,
) -> bool:
    """Признак «день уже обработан и запись не протухла по TTL».

    Возвращает True только если в журнале есть запись по `day_id` и
    `now_ts - ts <= ttl_sec`. Протухшие записи трактуются как необработанные (False),
    чтобы день можно было обработать заново.
    """
    entry = journal.get(str(day_id))
    if not isinstance(entry, dict):
        return False
    ts_raw = entry.get("ts")
    try:
        ts = int(ts_raw)
    except (TypeError, ValueError):
        return False
    return (now_ts - ts) <= ttl_sec


def mark_processed(
    path: Path,
    day_id: int,
    day_date: str,
    log_id: int,
    *,
    now_ts: int,
) -> None:
    """Атомарно добавить/обновить запись о дне в журнале.

    Запись НЕсекретная: id дня, дата дня (isoformat), время обработки (ISO), id
    созданного учёта 1218 и unix-метка `ts` (для TTL). Файл пишется через временный
    файл + `os.replace`, чтобы исключить частично записанный JSON при сбое.
    """
    p = Path(path)
    journal = load_journal(p)
    journal[str(day_id)] = {
        "day_id": int(day_id),
        "date": str(day_date),
        "processed_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
        "log_id": int(log_id),
        "ts": int(now_ts),
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (p.name + ".tmp")
    try:
        tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise

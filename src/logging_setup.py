"""Настройка логирования: консоль (UTF-8) + файл out/run.log.

Ключевая гарантия безопасности (CLAUDE.md §4): КОД ВЕБХУКА никогда не попадает в логи.
Для этого на оба хендлера навешивается фильтр-маскировщик `SecretMaskingFilter`, который
заменяет конкретное значение кода вебхука (и общие токены вида auth=/webhook_code=...) на `***`
ещё до записи. Сам секрет в коде/логах не печатается.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional

# Общие шаблоны секретов в URL/JSON (на случай, если в сообщение просочится строка вызова).
_GENERIC_SECRET_PATTERNS = [
    re.compile(r"(auth|webhook_code|access_token|refresh_token)=([^&\s\"']+)", re.IGNORECASE),
    re.compile(
        r"\"(auth|webhook_code|access_token|refresh_token)\"\s*:\s*\"[^\"]*\"",
        re.IGNORECASE,
    ),
]


class SecretMaskingFilter(logging.Filter):
    """Маскирует точные секретные значения и типовые токены в тексте лог-записи.

    `literals` — конкретные секреты (например, код вебхука): любое их вхождение → `***`.
    Маскирование применяется к уже отформатированному сообщению (record.getMessage()),
    после чего args обнуляются, чтобы хендлер не переформатировал исходные данные обратно.
    """

    def __init__(self, literals: Iterable[str] = ()) -> None:
        super().__init__()
        # Маскируем только непустые и достаточно длинные значения (защита от ложного
        # схлопывания коротких строк). Сортируем по длине убыв., чтобы длинные не «съедались».
        self._literals: List[str] = sorted(
            {s for s in literals if s and len(s) >= 4},
            key=len,
            reverse=True,
        )

    def _mask(self, text: str) -> str:
        result = text
        for secret in self._literals:
            if secret in result:
                result = result.replace(secret, "***")
        for pattern in _GENERIC_SECRET_PATTERNS:
            if "=" in pattern.pattern:
                result = pattern.sub(lambda m: f"{m.group(1)}=***", result)
            else:
                result = pattern.sub(lambda m: f'"{m.group(1)}":"***"', result)
        return result

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        masked = self._mask(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def setup_logging(
    *,
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    secret_literals: Iterable[str] = (),
) -> logging.Logger:
    """Сконфигурировать корневой логгер: консоль (UTF-8 stdout) + out/run.log.

    secret_literals — конкретные секреты для маскирования (минимум: код вебхука).
    Идемпотентно: повторный вызов не плодит хендлеры.
    """
    root = logging.getLogger()
    root.setLevel(level)

    if log_file is None:
        log_file = Path(__file__).resolve().parent.parent / "out" / "run.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    mask_filter = SecretMaskingFilter(secret_literals)
    fmt = logging.Formatter(
        fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Снять прежние хендлеры (идемпотентность при повторном вызове в тестах/командах).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # Консоль — принудительно UTF-8, чтобы кириллица/ФИО не падали на Windows.
    stream = sys.stdout
    try:
        stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(stream)
    console.setLevel(level)
    console.setFormatter(fmt)
    console.addFilter(mask_filter)
    root.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(mask_filter)
    root.addHandler(file_handler)

    return root

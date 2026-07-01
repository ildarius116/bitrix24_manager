"""Точка входа GUI для bitrix24 «Рабочий день» (PySide6).

Запуск::

    venv/Scripts/python.exe gui_main.py

Альтернатива CLI (`main.py`): то же ядро `src/`, но с десктопным интерфейсом.
Бизнес-логика (export/fill) остаётся в `src/`; GUI — только обёртка.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from src.config import ConfigError, load_config
from src.logging_setup import SecretMaskingFilter, setup_logging


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Bitrix24 — Рабочий день")

    # Конфигурацию грузим после создания QApplication, чтобы ошибку можно было
    # показать в диалоге (а не падать в консоль).
    try:
        config = load_config()
    except ConfigError as exc:
        QMessageBox.critical(
            None,
            "Ошибка конфигурации",
            f"Не удалось загрузить конфигурацию:\n\n{exc}",
        )
        return 2
    except Exception as exc:  # неожиданная ошибка — тоже показать пользователю
        QMessageBox.critical(
            None,
            "Ошибка запуска",
            f"Непредвиденная ошибка при инициализации:\n\n{exc}",
        )
        return 1

    # Логирование с маскированием кода вебхука (как в main.py): консоль + out/run.log.
    setup_logging(secret_literals=(config.env.webhook_code,))
    # Доп. защита: фильтр на самом логгере «workday» гарантирует, что лог-панель GUI
    # (handler на этом логгере) тоже получает уже замаскированные записи — фильтр
    # логгера отрабатывает до вызова любых handler-ов.
    logging.getLogger("workday").addFilter(
        SecretMaskingFilter((config.env.webhook_code,))
    )

    # Импортируем тему и окно после успешной конфигурации.
    from gui.main_window import MainWindow
    from gui.theme import theme

    window = MainWindow(config)
    theme.apply(app, "system")
    window.show()

    # Индикатор вебхука: фоновая read-only smoke-проверка ПОСЛЕ показа окна
    # (не в конструкторе — чтобы тесты/стаб-конфиг не делали сети).
    window.check_webhook()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

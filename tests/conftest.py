"""Общие фикстуры pytest (без сети).

Offscreen QApplication для GUI-тестов (``tests/test_gui_*.py``): один экземпляр
на всю сессию (``QApplication.instance()`` запрещает создавать второй). Платформа
``offscreen`` выставляется ДО импорта PySide6, иначе на Windows может быть
предпринята попытка открыть реальный дисплей/окно.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(scope="session")
def qapp():
    """Единственный QApplication на сессию тестов (offscreen, без сети)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication(["bitrix24-tests"])
    yield app

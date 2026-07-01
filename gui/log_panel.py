"""Панель живого лога (фаза 6_02).

Read-only ``QTextEdit`` (тёмный фон, Cascadia Code 10px) + ``QLoggingHandler`` — мост
``logging`` → Qt-сигнал. Handler можно создавать/эмитить из любого потока: ``Signal.emit``
у PySide6 потокобезопасен (через очередь событий), а сам append в виджет выполняется
в слоте, подключённом в main thread — см. ``LogPanel.attach``.

Перехватывается логгер ``logging.getLogger("workday")`` (см. ``src/logging_setup.py``) —
секрет вебхука уже маскируется ``SecretMaskingFilter`` на уровне ``logging.LogRecord``,
поэтому здесь дополнительная маскировка не требуется.
"""

from __future__ import annotations

import html
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

# Цвета уровней логов (GUI_SPEC §3.6). Лог-панель всегда тёмная — цвета не зависят от темы.
_LEVEL_COLORS = {
    "DEBUG": "#9a9a9a",
    "INFO": "#d4d4d4",
    "WARNING": "#dcdcaa",
    "ERROR": "#f44747",
    "CRITICAL": "#f44747",
}
_DEFAULT_COLOR = "#d4d4d4"

# Цвет категории REST в журнале (отдельная подсветка, ConsolePanel).
_REST_COLOR = "#4fc1ff"

# Подстроки-маркеры REST-вызовов. Все src-модули логируют в один логгер «workday»,
# поэтому категорию REST определяем эвристикой по имени логгера ИЛИ по подстроке
# в сообщении. Единственная точка правды — используется и цветом, и фильтром-пилюлей.
_REST_MSG_MARKERS = (
    "crm.",
    "batch",
    "user.current",
    "user.get",
    "rest",
    "REST",
    "портал",
    "Доступ ОК",
    "smoke",
    "@ufcrm",
    "@ufCrm",
)


def classify_record(logger_name: str, level: str, message: str) -> str:
    """Отнести запись лога к категории журнала (единая эвристика).

    Категории (совпадают с пилюлями-фильтрами ConsolePanel):

    - ``"rest"``  — запись REST-вызова портала (по имени логгера ``*rest*``/``*b24*``
      либо по подстроке-маркеру в сообщении, см. ``_REST_MSG_MARKERS``);
    - ``"error"`` — уровень WARNING/ERROR/CRITICAL;
    - ``"ok"``    — успешные INFO/DEBUG-записи (всё остальное).

    Приоритет: REST важнее уровня (REST-ошибка тоже относится к REST), поэтому
    сначала проверяем REST, затем уровень. Фильтр «Все» показывает всё независимо
    от категории.
    """
    name = (logger_name or "").lower()
    if "rest" in name or "b24" in name:
        return "rest"
    msg = message or ""
    if any(marker in msg for marker in _REST_MSG_MARKERS):
        return "rest"
    if level in ("WARNING", "ERROR", "CRITICAL"):
        return "error"
    return "ok"


class QLoggingHandler(logging.Handler, QObject):
    """Мост ``logging.Handler`` → Qt-сигнал ``record_emitted(level, name, msg)``.

    ``emit()`` вызывается логгером в потоке, где выполнялся вызов ``log.info(...)`` и т.п.
    (может быть не main thread — например, из воркера ``QThread``). Сам ``emit`` сигнала
    PySide6 потокобезопасен между потоками одного приложения; слушатель, подключённый
    через обычный ``connect`` (без ``Qt.DirectConnection``), выполнится в потоке владельца
    сигнала (main thread), поэтому обновление виджета остаётся безопасным.

    Сигнал несёт также имя логгера (``record.name``) — оно нужно ``classify_record``
    для вкладки-фильтра [REST] в ConsolePanel.
    """

    record_emitted = Signal(str, str, str)

    def __init__(self, level: int = logging.INFO) -> None:
        logging.Handler.__init__(self, level)
        QObject.__init__(self)
        self.setFormatter(logging.Formatter(fmt="[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - защитный путь, не должен срабатывать
            msg = record.getMessage()
        self.record_emitted.emit(record.levelname, record.name, msg)


class LogPanel(QWidget):
    """Виджет лог-панели: тёмный read-only ``QTextEdit`` + подключаемый handler."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._text_edit = QTextEdit(self)
        self._text_edit.setObjectName("LogTextEdit")
        self._text_edit.setReadOnly(True)
        self._text_edit.setPlaceholderText("Лог операций…")

        font = QFont()
        font.setFamilies(["Cascadia Code", "Consolas"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self._text_edit.setFont(font)

        layout.addWidget(self._text_edit)

        self._handler: "QLoggingHandler | None" = None
        self._logger_name: "str | None" = None

    # ------------------------------------------------------------------
    # Подключение к логгеру
    # ------------------------------------------------------------------

    def attach(
        self,
        logger_name: str = "workday",
        level: int = logging.INFO,
        secret_filter: "logging.Filter | None" = None,
    ) -> None:
        """Подключить handler к ``logging.getLogger(logger_name)`` (root не трогаем).

        Идемпотентно: повторный вызов сначала отключает прежний handler.

        ``secret_filter`` (defense-in-depth, CLAUDE.md §4): навешивается напрямую на
        экземпляр ``QLoggingHandler``. Фильтр на самом логгере не отрабатывает для
        записей дочерних логгеров ``logger_name.*``, всплывающих к этому handler-у, —
        фильтр на handler-е маскирует и их. Сам секрет сюда передаётся как объект
        фильтра (его значение не логируется/не печатается).
        """
        self.detach()
        handler = QLoggingHandler(level)
        if secret_filter is not None:
            handler.addFilter(secret_filter)
        handler.record_emitted.connect(self._append)
        logging.getLogger(logger_name).addHandler(handler)
        self._handler = handler
        self._logger_name = logger_name

    def detach(self) -> None:
        """Снять ранее подключённый handler (если был)."""
        if self._handler is not None and self._logger_name is not None:
            logging.getLogger(self._logger_name).removeHandler(self._handler)
        self._handler = None
        self._logger_name = None

    # ------------------------------------------------------------------
    # Слот добавления строки (выполняется в main thread)
    # ------------------------------------------------------------------

    def _append(self, level: str, name: str, msg: str) -> None:
        if classify_record(name, level, msg) == "rest":
            color = _REST_COLOR
        else:
            color = _LEVEL_COLORS.get(level, _DEFAULT_COLOR)
        escaped = html.escape(msg)
        self._text_edit.append(f'<span style="color:{color};">{escaped}</span>')

    def clear(self) -> None:
        """Очистить содержимое лог-панели."""
        self._text_edit.clear()

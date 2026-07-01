"""Правая тёмная консоль (фаза 7): ПРОГРЕСС + ЖУРНАЛ.

Заменяет прежнюю узкую ленту ``log_panel.LogPanel`` полноценной консольной колонкой
по макету ``design/bitrix24-console-standalone.html``. Всегда тёмная (log_bg #1e1e1e)
независимо от темы приложения.

Состав:

- **ПРОГРЕСС**: заголовок + «N%» + ``QProgressBar`` (indeterminate во время операции)
  + строка состояния («Ожидание»/«Выполняется…»).
- **ЖУРНАЛ**: заголовок + значок-фильтр + пилюли-фильтры [Все][REST][OK][Ошибки]
  (активная — accent), счётчик «N стр.», экспорт журнала (⤓) и очистка (🗑).
  Сам лог — read-only ``QTextEdit`` (mono, цвета уровней/категории REST).

Переиспользуется мост ``QLoggingHandler`` из ``gui.log_panel`` (+ маскировка секрета
на хендлере — CLAUDE.md §4) и единая эвристика категорий ``classify_record``. Виджет
хранит все пришедшие записи и перерисовывает лог при смене фильтра.
"""

from __future__ import annotations

import html
import logging
from typing import List, Tuple

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.log_panel import QLoggingHandler, classify_record

# Цвета уровней (консоль всегда тёмная — не зависят от темы).
_LEVEL_COLORS = {
    "DEBUG": "#9a9a9a",
    "INFO": "#d4d4d4",
    "WARNING": "#dcdcaa",
    "ERROR": "#f44747",
    "CRITICAL": "#f44747",
}
_REST_COLOR = "#4fc1ff"
_DEFAULT_COLOR = "#d4d4d4"

# Пилюли-фильтры: подпись → множество категорий classify_record (None = все).
_FILTERS: List[Tuple[str, object]] = [
    ("Все", None),
    ("REST", {"rest"}),
    ("OK", {"ok"}),
    ("Ошибки", {"error"}),
]


class ConsolePanel(QWidget):
    """Тёмная консоль: прогресс + журнал с пилюлями-фильтрами."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("ConsolePanel")

        # (level, logger_name, message, category) — все записи для перефильтрации.
        self._records: List[Tuple[str, str, str, str]] = []
        self._active_filter: object = None  # None = «Все»

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        root.addWidget(self._build_progress())
        root.addWidget(self._build_journal_header())

        self._log = QTextEdit(self)
        self._log.setObjectName("ConsoleLog")
        self._log.setReadOnly(True)
        self._log.setPlaceholderText("// журнал пуст — запустите операцию")
        font = QFont()
        font.setFamilies(["Cascadia Code", "Consolas"])
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self._log.setFont(font)
        root.addWidget(self._log, stretch=1)

        self._handler: "QLoggingHandler | None" = None
        self._logger_name: "str | None" = None
        self._update_counter()

    # ------------------------------------------------------------------
    # ПРОГРЕСС
    # ------------------------------------------------------------------
    def _build_progress(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("ProgressBlock")
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        head = QHBoxLayout()
        title = QLabel("ПРОГРЕСС", wrap)
        title.setObjectName("ConsoleHeader")
        self._percent = QLabel("0%", wrap)
        self._percent.setObjectName("ProgressPercent")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._percent)
        box.addLayout(head)

        self._progress = QProgressBar(wrap)
        self._progress.setObjectName("ConsoleProgress")
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        box.addWidget(self._progress)

        self._status = QLabel("Ожидание", wrap)
        self._status.setObjectName("ConsoleStatus")
        box.addWidget(self._status)
        return wrap

    # ------------------------------------------------------------------
    # ЖУРНАЛ header
    # ------------------------------------------------------------------
    def _build_journal_header(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("JournalHeader")
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        top = QHBoxLayout()
        title = QLabel("ЖУРНАЛ", wrap)
        title.setObjectName("ConsoleHeader")
        filt = QLabel("▾", wrap)
        filt.setObjectName("JournalFilterIcon")
        top.addWidget(title)
        top.addWidget(filt)
        top.addStretch(1)

        self._counter = QLabel("0 стр.", wrap)
        self._counter.setObjectName("JournalCounter")
        top.addWidget(self._counter)

        self._btn_export = QPushButton("⤓", wrap)
        self._btn_export.setObjectName("JournalIconBtn")
        self._btn_export.setToolTip("Экспортировать журнал в файл")
        self._btn_export.clicked.connect(self._export_log)
        top.addWidget(self._btn_export)

        self._btn_clear = QPushButton("🗑", wrap)
        self._btn_clear.setObjectName("JournalIconBtn")
        self._btn_clear.setToolTip("Очистить журнал")
        self._btn_clear.clicked.connect(self.clear)
        top.addWidget(self._btn_clear)
        box.addLayout(top)

        pills = QHBoxLayout()
        pills.setSpacing(6)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for i, (label, categories) in enumerate(_FILTERS):
            pill = QPushButton(label, wrap)
            pill.setObjectName("JournalPill")
            pill.setCheckable(True)
            if i == 0:
                pill.setChecked(True)
            pill.clicked.connect(lambda _=False, c=categories: self._set_filter(c))
            self._filter_group.addButton(pill)
            pills.addWidget(pill)
        pills.addStretch(1)
        box.addLayout(pills)
        return wrap

    # ------------------------------------------------------------------
    # Подключение к логгеру (переиспользует QLoggingHandler + маск-фильтр)
    # ------------------------------------------------------------------
    def attach(
        self,
        logger_name: str = "workday",
        level: int = logging.INFO,
        secret_filter: "logging.Filter | None" = None,
    ) -> None:
        """Подключить ``QLoggingHandler`` к ``logging.getLogger(logger_name)``.

        Идемпотентно (повторный вызов сначала отключает прежний handler).
        ``secret_filter`` (CLAUDE.md §4) навешивается прямо на хендлер — маскирует
        секрет и в записях дочерних логгеров ``logger_name.*``.
        """
        self.detach()
        handler = QLoggingHandler(level)
        if secret_filter is not None:
            handler.addFilter(secret_filter)
        handler.record_emitted.connect(self._on_record)
        logging.getLogger(logger_name).addHandler(handler)
        self._handler = handler
        self._logger_name = logger_name

    def detach(self) -> None:
        if self._handler is not None and self._logger_name is not None:
            logging.getLogger(self._logger_name).removeHandler(self._handler)
        self._handler = None
        self._logger_name = None

    # ------------------------------------------------------------------
    # Приём и рендер записей (main thread)
    # ------------------------------------------------------------------
    def _on_record(self, level: str, name: str, msg: str) -> None:
        category = classify_record(name, level, msg)
        self._records.append((level, name, msg, category))
        if self._passes_filter(category):
            self._append_line(level, category, msg)
        self._update_counter()

    def _append_line(self, level: str, category: str, msg: str) -> None:
        color = _REST_COLOR if category == "rest" else _LEVEL_COLORS.get(level, _DEFAULT_COLOR)
        self._log.append(f'<span style="color:{color};">{html.escape(msg)}</span>')

    def _passes_filter(self, category: str) -> bool:
        return self._active_filter is None or category in self._active_filter

    def _set_filter(self, categories: object) -> None:
        self._active_filter = categories
        self._rerender()

    def _rerender(self) -> None:
        self._log.clear()
        for level, _name, msg, category in self._records:
            if self._passes_filter(category):
                self._append_line(level, category, msg)
        self._update_counter()

    def _visible_count(self) -> int:
        return sum(1 for _l, _n, _m, c in self._records if self._passes_filter(c))

    def _update_counter(self) -> None:
        self._counter.setText(f"{self._visible_count()} стр.")

    # ------------------------------------------------------------------
    # Экспорт/очистка
    # ------------------------------------------------------------------
    def _export_log(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить журнал", "journal.log", "Журнал (*.log *.txt)"
        )
        if not path:
            return
        lines = [
            msg for _l, _n, msg, c in self._records if self._passes_filter(c)
        ]
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        except OSError as exc:
            # Не роняем UI, но и не глотаем ошибку молча — показываем пользователю.
            QMessageBox.warning(
                self,
                "Экспорт журнала",
                f"Не удалось сохранить журнал в файл:\n{path}\n\n{exc}",
            )

    def clear(self) -> None:
        """Очистить журнал (записи и отображение)."""
        self._records.clear()
        self._log.clear()
        self._update_counter()

    # ------------------------------------------------------------------
    # Прогресс/состояние (вызывается окном)
    # ------------------------------------------------------------------
    def set_busy(self, busy: bool) -> None:
        """Indeterminate-прогресс во время операции; сброс в 0% в покое."""
        if busy:
            self._progress.setRange(0, 0)  # indeterminate
            self._percent.setText("…")
        else:
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._percent.setText("0%")

    def set_status(self, text: str) -> None:
        self._status.setText(text)

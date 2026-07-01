"""Таблица результатов export/fill (фаза 6_03).

Два режима колонок (GUI_SPEC §3.4):
- export — по ``WorkdayDay`` (Дата, Сотрудник, Заполнено, Часы, Учёты);
- fill — по result-dict ``run_fill`` (Дата, ID дня, Статус, Новый учёт, Дело, Причина),
  с цветовой индикацией ячейки «Статус».

Цвет статуса берётся из активной палитры темы (``theme.current_palette()``) по токену,
который выдаёт чистая функция ``status_color_token`` (вынесена для юнит-теста, gui_05).

Исходные объекты строк (``WorkdayDay`` / result-dict) хранятся параллельно строкам и
отдаются наружу сигналом ``row_selected`` для панели деталей.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidget, QTableWidgetItem

from gui.theme import theme

# Колонки режимов.
_EXPORT_HEADERS = ["Дата", "Сотрудник", "Заполнено", "Часы", "Учёты"]
_FILL_HEADERS = ["Дата", "ID дня", "Статус", "Новый учёт", "Дело", "Причина"]

# Индекс колонки «Статус» в режиме fill (для окраски).
_FILL_STATUS_COL = 2


def status_color_token(status: str) -> str:
    """Сопоставить статус результата fill токену цвета палитры (GUI_SPEC §3.4).

    filled/repaired → ``status_ok``; ``dry-run`` → ``status_running``;
    skipped/already-closed → ``text_muted``; ``error`` → ``status_fail``;
    ``aborted`` → ``status_warning``. Неизвестный статус → ``text_muted`` (нейтральный).
    """
    mapping = {
        "filled": "status_ok",
        "repaired": "status_ok",
        "dry-run": "status_running",
        "skipped": "text_muted",
        "already-closed": "text_muted",
        "error": "status_fail",
        "aborted": "status_warning",
    }
    return mapping.get(status, "text_muted")


class ResultTable(QTableWidget):
    """Таблица результатов с двумя режимами и сигналом выбора строки.

    Сигнал ``contents_changed`` эмитится после каждого наполнения/сброса таблицы
    (``show_export``/``show_fill``/``_reset``) — по нему ``ControlPanel`` скрывает
    пустую таблицу и показывает её только при наличии строк (см. GUI-фикс: пустой
    белый прямоугольник в левой колонке не должен отображаться).
    """

    row_selected = Signal(object)
    contents_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ResultTable")
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)

        # Объекты строк (WorkdayDay либо result-dict), параллельно строкам таблицы.
        self._objects: List[Any] = []
        self._mode: Optional[str] = None

        self.itemSelectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------
    # Режим export
    # ------------------------------------------------------------------
    def show_export(self, days, user_map: Optional[Dict[str, str]] = None) -> None:
        user_map = user_map or {}
        self._reset(_EXPORT_HEADERS, "export")
        self.setRowCount(len(days))
        for r, day in enumerate(days):
            self._objects.append(day)
            date_str = day.date.strftime("%d.%m.%Y") if day.date else ""
            employee = user_map.get(day.employee, day.employee or "")
            filled = len(day.works_ids or [])
            hours = sum((lg.hours or 0) for lg in (day.logs or []))
            titles = ", ".join(lg.title for lg in (day.logs or []) if lg.title)

            self.setItem(r, 0, self._item(date_str))
            self.setItem(r, 1, self._item(employee))
            self.setItem(r, 2, self._item(str(filled)))
            self.setItem(r, 3, self._item(self._fmt_hours(hours)))
            self.setItem(r, 4, self._item(titles))
        self.resizeColumnsToContents()
        self.contents_changed.emit()

    # ------------------------------------------------------------------
    # Режим fill
    # ------------------------------------------------------------------
    def show_fill(self, rows: List[Dict[str, Any]]) -> None:
        rows = rows or []
        self._reset(_FILL_HEADERS, "fill")
        self.setRowCount(len(rows))
        palette = theme.current_palette()
        for r, row in enumerate(rows):
            self._objects.append(row)
            status = str(row.get("status", ""))
            new_id = row.get("new_id", "—")
            activity = row.get("activity_status", "—")
            reason = row.get("reason", "") or ""

            self.setItem(r, 0, self._item(str(row.get("date", ""))))
            self.setItem(r, 1, self._item(str(row.get("day_id", ""))))

            status_item = self._item(status)
            color = palette.get(status_color_token(status))
            if color:
                status_item.setForeground(QBrush(QColor(color)))
            self.setItem(r, _FILL_STATUS_COL, status_item)

            self.setItem(r, 3, self._item(str(new_id)))
            self.setItem(r, 4, self._item(str(activity)))
            self.setItem(r, 5, self._item(str(reason)))
        self.resizeColumnsToContents()
        self.contents_changed.emit()

    # ------------------------------------------------------------------
    # Внутреннее
    # ------------------------------------------------------------------
    def _reset(self, headers: List[str], mode: str) -> None:
        self._mode = mode
        self._objects = []
        self.clearContents()
        self.setRowCount(0)
        self.setColumnCount(len(headers))
        self.setHorizontalHeaderLabels(headers)

    @staticmethod
    def _item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _fmt_hours(hours: float) -> str:
        try:
            value = float(hours)
        except (TypeError, ValueError):
            return "0"
        return str(int(value)) if value == int(value) else f"{value:g}"

    def _on_selection_changed(self) -> None:
        rows = self.selectionModel().selectedRows() if self.selectionModel() else []
        if not rows:
            return
        idx = rows[0].row()
        if 0 <= idx < len(self._objects):
            self.row_selected.emit(self._objects[idx])

"""Диалог подтверждения записи учёта рабочего времени по дню (фаза 6_04).

Реализует метод заполнения «Индивидуально» (interaction=True, FR-2.1.10.2/10.3):
перед созданием учёта за каждый день показывается этот модальный диалог с
предложенными описанием/часами. (Общий боевой прогон дополнительно подтверждается
один раз диалогом «Боевой режим — подтверждение записи» в главном окне.)

Вызывается из main thread слотом ``MainWindow._on_confirm_requested`` по сигналу
``FillWorker.confirm_requested`` — сам диалог про воркер ничего не знает (никаких
QThread/QWaitCondition здесь нет, это забота ``gui/worker.py``).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Действия диалога → используются MainWindow для маппинга в choice воркера
# (см. _ACTION_TO_CHOICE в main_window.py): ok/apply_all/skip/abort.
_ACTIONS = ("ok", "apply_all", "skip", "abort")


class ConfirmDialog(QDialog):
    """Модальный диалог «Подтверждение записи» (GUI_SPEC §5).

    Параметры
    ---------
    day_info:
        Словарь с данными дня и дефолтами, переданный воркером в сигнале
        ``confirm_requested``: ``default_description``, ``default_hours`` и,
        опционально (фаза 6_04), ``day_id`` / ``day_date`` (строки). Если
        day_id/day_date отсутствуют — метка дня показывает «День: —».

    Кнопки [Пропустить] [Применить ко всем] [Отмена] [OK]. Закрытие крестиком
    (или Esc) трактуется как «Отмена» — действие по умолчанию ``abort``.
    """

    def __init__(self, day_info: Optional[Dict[str, Any]] = None, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Подтверждение записи")
        self.setModal(True)

        info = day_info if isinstance(day_info, dict) else {}
        # Действие по умолчанию, если диалог закрыт крестиком/Esc без клика по кнопке.
        self._action: str = "abort"

        layout = QVBoxLayout(self)

        day_label = QLabel(self._format_day_label(info), self)
        day_label.setObjectName("ConfirmDayLabel")
        layout.addWidget(day_label)

        form = QFormLayout()

        self._description = QLineEdit(self)
        self._description.setText(str(info.get("default_description", "")))
        form.addRow("Описание задачи:", self._description)

        self._hours = QDoubleSpinBox(self)
        self._hours.setRange(0.0, 24.0)
        self._hours.setDecimals(2)
        self._hours.setSingleStep(0.5)
        self._hours.setValue(self._coerce_hours(info.get("default_hours")))
        form.addRow("Количество часов:", self._hours)

        layout.addLayout(form)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch(1)

        self._btn_skip = QPushButton("Пропустить", self)
        self._btn_apply_all = QPushButton("Применить ко всем", self)
        self._btn_cancel = QPushButton("Отмена", self)
        self._btn_ok = QPushButton("OK", self)
        self._btn_ok.setDefault(True)

        for btn in (self._btn_skip, self._btn_apply_all, self._btn_cancel, self._btn_ok):
            buttons_row.addWidget(btn)

        self._btn_skip.clicked.connect(lambda: self._finish("skip"))
        self._btn_apply_all.clicked.connect(lambda: self._finish("apply_all"))
        self._btn_cancel.clicked.connect(lambda: self._finish("abort"))
        self._btn_ok.clicked.connect(lambda: self._finish("ok"))

        layout.addLayout(buttons_row)

    # ------------------------------------------------------------------
    # Вспомогательное
    # ------------------------------------------------------------------

    @staticmethod
    def _format_day_label(info: Dict[str, Any]) -> str:
        day_date = str(info.get("day_date") or "").strip()
        day_id = str(info.get("day_id") or "").strip()
        if not day_date:
            return "День: —"
        suffix = f" (id {day_id})" if day_id else ""
        return f"День: {day_date}{suffix}"

    @staticmethod
    def _coerce_hours(raw: Any) -> float:
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    def _finish(self, action: str) -> None:
        self._action = action
        if action in ("ok", "apply_all"):
            self.accept()
        else:
            self.reject()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def result_payload(self) -> Dict[str, Any]:
        """Собрать итог выбора пользователя.

        Возвращает ``{action, description, hours}``, где ``action`` ∈
        {ok, apply_all, skip, abort}. Если диалог закрыт без клика по одной из
        кнопок (крестик/Esc), ``action`` остаётся дефолтным ``"abort"``.
        """
        action = self._action if self._action in _ACTIONS else "abort"
        return {
            "action": action,
            "description": self._description.text(),
            "hours": self._hours.value(),
        }

    @staticmethod
    def request_confirmation(
        day_info: Optional[Dict[str, Any]], parent: "QWidget | None" = None
    ) -> Dict[str, Any]:
        """Показать диалог модально и вернуть выбор пользователя (helper для вызывающего кода)."""
        dialog = ConfirmDialog(day_info, parent)
        dialog.exec()
        return dialog.result_payload()

"""Диалог «Настройки → Значения по-умолчанию» (фаза 7 — исправление под макет).

Редактирует те же дефолты «Описание задачи» и «Часы», что и одноимённые инлайн-поля
в левой колонке «Редактирование» (``gui/control_panel.py``, «Описание задачи по-
умолчанию» / «Часов по-умолчанию») — это зеркало, а не единственное место
редактирования. Значения применяются главным окном при заполнении: как пре-филл
``ConfirmDialog`` в методе «Индивидуально» и как значения, передаваемые в
``FillWorker`` для метода «По-умолчанию».

Начальные значения — из ``config.defaults`` (``task_description`` / ``hours``);
дальше окно хранит их на себе и передаёт сюда текущие значения при открытии.
Диалог REST/бизнес-логики не содержит — только собирает два поля.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)


class SettingsDialog(QDialog):
    """Модальный диалог значений по-умолчанию: «Описание задачи» + «Часы».

    Параметры
    ---------
    description:
        Текущее дефолтное описание задачи (пре-филл ``QLineEdit``).
    hours:
        Текущие дефолтные часы (пре-филл ``QDoubleSpinBox`` 0..24).
    """

    def __init__(
        self,
        description: str = "",
        hours: float = 0.0,
        parent: "QWidget | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("Значения по-умолчанию")
        self.setModal(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._description = QLineEdit(self)
        self._description.setText(str(description or ""))
        form.addRow("Описание задачи:", self._description)

        self._hours = QDoubleSpinBox(self)
        self._hours.setRange(0.0, 24.0)
        self._hours.setDecimals(2)
        self._hours.setSingleStep(0.5)
        self._hours.setValue(self._coerce_hours(hours))
        form.addRow("Часы:", self._hours)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_hours(raw: Any) -> float:
        try:
            return float(raw or 0)
        except (TypeError, ValueError):
            return 0.0

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------
    def description(self) -> str:
        return self._description.text()

    def hours(self) -> float:
        return self._hours.value()

    def values(self) -> Tuple[str, float]:
        """Вернуть кортеж ``(description, hours)`` текущих значений диалога."""
        return self.description(), self.hours()

    @staticmethod
    def edit_defaults(
        description: str,
        hours: float,
        parent: "QWidget | None" = None,
    ) -> Optional[Tuple[str, float]]:
        """Показать диалог модально; вернуть новые ``(description, hours)`` или
        ``None``, если пользователь нажал «Отмена»."""
        dialog = SettingsDialog(description, hours, parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.values()
        return None

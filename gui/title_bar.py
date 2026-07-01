"""Синий баннер-заголовок под системной рамкой окна (фаза 7).

Решение пользователя: НЕ безрамочное окно. Оставляем стандартную рамку Windows и
рисуем синюю полосу-баннер (accent) сразу под меню-баром, с названием приложения —
как в макете ``design/bitrix24-console-standalone.html``.

Виджет чисто презентационный: ни бизнес-логики, ни REST — только оформление.
Цвет задаётся через QSS (objectName ``TitleBar``/``TitleBarLabel``), не хардкодится
здесь, чтобы менялся вместе с темой (accent — единственный источник в палитре).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

BANNER_TEXT = "Bitrix24 · Рабочий день"


class TitleBar(QWidget):
    """Синяя титульная полоса-баннер (accent) с названием приложения."""

    def __init__(self, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        # QWidget-подкласс НЕ красит фон из QSS без этого атрибута — иначе `background`
        # у #TitleBar игнорируется и синий баннер остаётся прозрачным (текст на фоне окна).
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        self._label = QLabel(BANNER_TEXT, self)
        self._label.setObjectName("TitleBarLabel")
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._label)
        layout.addStretch(1)

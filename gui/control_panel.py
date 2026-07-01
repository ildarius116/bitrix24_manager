"""Левая колонка управления (фаза 7, режим-зависимая — по макету).

Набор контролов левой колонки ЗАВИСИТ ОТ РЕЖИМА (источник истины — макет
``design/bitrix24-console-standalone.html``):

- Верхний переключатель режима (mutually exclusive): «⬇ Парсинг» (export) /
  «✎ Редактирование» (fill). Это ВЫБОР режима — запуск делает единая синяя CTA внизу.
- Секция «Период» (QDateEdit «Начальная/Конечная дата» + подпись «Период: …») —
  видима ТОЛЬКО в режиме «Парсинг» (выгрузка read-only по диапазону дат).
- Секция «Редактирование» — видима ТОЛЬКО в режиме fill (как в макете):
    * «Метод заполнения задач» — выпадающий список «По-умолчанию» (auto) /
      «Индивидуально» (interactive);
    * «Описание задачи по-умолчанию» / «Часов по-умолчанию» — поля по умолчанию;
    * «ДНИ ДЛЯ ЗАПОЛНЕНИЯ» — чекбоксы дней 4-дневного окна (Сегодня … 4 дня назад)
      с датами; у отмеченных показывается «<описание> · <часы> ч»;
    * «Запланировать» — чекбокс + время (визуально; запуск «сразу»).

Dry-run в GUI нет: боевая запись гейтится подтверждением в главном окне (QMessageBox
для «По-умолчанию», per-day ConfirmDialog для «Индивидуально»).

Виджет не содержит REST/бизнес-логики: только собирает параметры и эмитит сигналы.
"""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, QTime, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from src.dates import today_moscow

from gui.result_table import ResultTable

_DATE_FORMAT = "dd.MM.yyyy"

MODE_EXPORT = "export"
MODE_FILL = "fill"

# Метод заполнения (fill): auto — значения по умолчанию без вопросов;
# interactive — подтверждение по каждому дню (безопаснее, дефолт).
METHOD_AUTO = "auto"
METHOD_INTERACTIVE = "interactive"

# Дни 4-дневного окна: сегодня и до 4 дней назад (политика 4 дней).
_DAY_LABELS = ["Сегодня", "1 день назад", "2 дня назад", "3 дня назад", "4 дня назад"]

_CTA_TEXT = {
    MODE_EXPORT: "⬇  Получить выписку",
    MODE_FILL: "Выполнить",
}


class ControlPanel(QWidget):
    """Левая колонка: режим, период (export) / параметры заполнения (fill), CTA.

    Сигналы
    -------
    run_requested()
        Нажата CTA — запустить выбранный режим (export/fill).
    mode_changed(str)
        Сменён режим («export»/«fill»).
    row_selected(object)
        Проброс выбора строки из ``ResultTable`` (для панели деталей окна).
    """

    run_requested = Signal()
    mode_changed = Signal(str)
    row_selected = Signal(object)

    def __init__(self, config, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("ControlPanel")
        self._config = config
        self._mode = MODE_EXPORT
        self._day_rows: list[tuple[QCheckBox, date, QLabel]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_mode_row())
        root.addWidget(self._build_period_section())
        root.addWidget(self._build_edit_section())

        # Таблица результатов растягивается по вертикали, но ПОКАЗЫВАЕТСЯ только когда
        # в ней есть строки. Пока данных нет — вместо неё растягивающийся спейсер, чтобы
        # синяя CTA была прижата к низу колонки.
        self._result_table = ResultTable(self)
        self._result_table.row_selected.connect(self.row_selected.emit)
        self._result_table.contents_changed.connect(self._refresh_result_visibility)
        self._result_table.setVisible(False)
        root.addWidget(self._result_table, stretch=1)

        self._result_spacer = QWidget(self)
        self._result_spacer.setObjectName("ResultSpacer")
        self._result_spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        root.addWidget(self._result_spacer, stretch=1)

        root.addWidget(self._build_cta())

        self._apply_mode(MODE_EXPORT, emit=False)
        self._update_period_label()
        self._refresh_day_rows()
        self._refresh_result_visibility()

    # ------------------------------------------------------------------
    # Секция «переключатель режима»
    # ------------------------------------------------------------------
    def _build_mode_row(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("ModeRow")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        self._btn_export = QPushButton("⬇  Парсинг", wrap)
        self._btn_export.setObjectName("ModeButtonExport")
        self._btn_export.setCheckable(True)
        self._btn_export.setChecked(True)

        self._btn_fill = QPushButton("✎  Редактирование", wrap)
        self._btn_fill.setObjectName("ModeButtonFill")
        self._btn_fill.setCheckable(True)

        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._btn_export)
        self._mode_group.addButton(self._btn_fill)

        self._btn_export.clicked.connect(lambda: self._apply_mode(MODE_EXPORT))
        self._btn_fill.clicked.connect(lambda: self._apply_mode(MODE_FILL))

        row.addWidget(self._btn_export, stretch=1)
        row.addWidget(self._btn_fill, stretch=1)
        return wrap

    # ------------------------------------------------------------------
    # Секция «Период» (только режим «Парсинг»)
    # ------------------------------------------------------------------
    def _build_period_section(self) -> QWidget:
        wrap = QWidget(self)
        wrap.setObjectName("PeriodSection")
        box = QVBoxLayout(wrap)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(6)

        row_from = QHBoxLayout()
        lbl_from = QLabel("Начальная дата", wrap)
        lbl_from.setObjectName("FieldLabel")
        lbl_from.setMinimumWidth(120)
        self._date_from = self._make_date_edit()
        row_from.addWidget(lbl_from)
        row_from.addWidget(self._date_from, stretch=1)
        box.addLayout(row_from)

        row_to = QHBoxLayout()
        lbl_to = QLabel("Конечная дата", wrap)
        lbl_to.setObjectName("FieldLabel")
        lbl_to.setMinimumWidth(120)
        self._date_to = self._make_date_edit()
        row_to.addWidget(lbl_to)
        row_to.addWidget(self._date_to, stretch=1)
        box.addLayout(row_to)

        self._period = QLabel("Период: …", wrap)
        self._period.setObjectName("PeriodLabel")
        box.addWidget(self._period)

        self._date_from.dateChanged.connect(self._update_period_label)
        self._date_to.dateChanged.connect(self._update_period_label)
        self._period_section = wrap
        return wrap

    def _update_period_label(self) -> None:
        d1 = self._date_from.date().toString(_DATE_FORMAT)
        d2 = self._date_to.date().toString(_DATE_FORMAT)
        self._period.setText(f"Период: {d1} … {d2}")

    # ------------------------------------------------------------------
    # Секция «Редактирование» (только режим fill) — по макету
    # ------------------------------------------------------------------
    def _build_edit_section(self) -> QWidget:
        frame = QFrame(self)
        frame.setObjectName("EditSection")
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(10)

        # --- Ряд: Метод заполнения | Описание задачи по-умолчанию ---
        top = QHBoxLayout()
        top.setSpacing(12)

        method_col = QVBoxLayout()
        method_col.setSpacing(4)
        method_col.addWidget(self._field_caption("Метод заполнения задач"))
        self._method_combo = QComboBox(frame)
        self._method_combo.setObjectName("MethodCombo")
        self._method_combo.addItem("По-умолчанию", METHOD_AUTO)
        self._method_combo.addItem("Индивидуально", METHOD_INTERACTIVE)
        method_col.addWidget(self._method_combo)
        top.addLayout(method_col)

        desc_col = QVBoxLayout()
        desc_col.setSpacing(4)
        desc_col.addWidget(self._field_caption("Описание задачи по-умолчанию"))
        self._description = QLineEdit(frame)
        self._description.setObjectName("DescInput")
        self._description.setText(str(self._config.defaults.get("task_description", "")))
        self._description.textChanged.connect(self._refresh_day_rows)
        desc_col.addWidget(self._description)
        top.addLayout(desc_col, stretch=1)
        box.addLayout(top)

        # --- Часов по-умолчанию ---
        hours_col = QVBoxLayout()
        hours_col.setSpacing(4)
        hours_col.addWidget(self._field_caption("Часов по-умолчанию"))
        hrow = QHBoxLayout()
        hrow.setSpacing(6)
        self._hours = QDoubleSpinBox(frame)
        self._hours.setObjectName("HoursInput")
        self._hours.setRange(0.0, 24.0)
        self._hours.setDecimals(2)
        self._hours.setSingleStep(0.5)
        self._hours.setValue(float(self._config.defaults.get("hours", 8) or 0))
        self._hours.setMaximumWidth(90)
        self._hours.valueChanged.connect(self._refresh_day_rows)
        hrow.addWidget(self._hours)
        hrow.addWidget(QLabel("ч", frame))
        hrow.addStretch(1)
        hours_col.addLayout(hrow)
        box.addLayout(hours_col)

        # --- ДНИ ДЛЯ ЗАПОЛНЕНИЯ (4-дневное окно) ---
        days_caption = QLabel("ДНИ ДЛЯ ЗАПОЛНЕНИЯ", frame)
        days_caption.setObjectName("SectionCaption")
        box.addWidget(days_caption)

        today = today_moscow()
        for offset, label in enumerate(_DAY_LABELS):
            d = today - timedelta(days=offset)
            # (row_frame, checkbox, date, info, edit_wrap, desc_edit, hours_spin)
            row_frame, *entry = self._make_day_row(frame, label, d)
            box.addWidget(row_frame)
            entry[0].toggled.connect(self._refresh_day_rows)  # checkbox
            self._day_rows.append(tuple(entry))
        if self._day_rows:
            self._day_rows[0][0].setChecked(True)  # «Сегодня» отмечен по умолчанию

        # Метод меняет вид строк: «Индивидуально» → поля ввода, «По-умолчанию» → текст.
        self._method_combo.currentIndexChanged.connect(self._refresh_day_rows)

        # --- Запланировать (визуально; запуск «сразу») ---
        sched = QHBoxLayout()
        sched.setSpacing(8)
        self._schedule_check = QCheckBox("Запланировать", frame)
        self._schedule_check.setObjectName("ScheduleCheck")
        self._schedule_time = QTimeEdit(frame)
        self._schedule_time.setObjectName("ScheduleTime")
        self._schedule_time.setDisplayFormat("HH:mm")
        self._schedule_time.setTime(QTime(9, 0))
        self._schedule_time.setEnabled(False)
        self._schedule_check.toggled.connect(self._schedule_time.setEnabled)
        sched.addWidget(self._schedule_check)
        sched.addWidget(self._schedule_time)
        sched.addWidget(QLabel("24ч", frame))
        sched.addStretch(1)
        box.addLayout(sched)

        self._edit_section = frame
        return frame

    def _field_caption(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setObjectName("FieldLabel")
        return lbl

    def _make_day_row(
        self, parent: QWidget, label: str, d: date
    ) -> tuple[QFrame, QCheckBox, date, QLabel, QWidget, QLineEdit, QDoubleSpinBox]:
        row = QFrame(parent)
        row.setObjectName("DayRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        checkbox = QCheckBox(label, row)
        checkbox.setObjectName("DayCheck")
        lay.addWidget(checkbox)

        date_lbl = QLabel(d.strftime("%d.%m.%Y"), row)
        date_lbl.setObjectName("DayDate")
        lay.addWidget(date_lbl)

        lay.addStretch(1)

        # «По-умолчанию»: статический текст «<описание> · <часы> ч».
        info = QLabel("", row)
        info.setObjectName("DayInfo")
        lay.addWidget(info)

        # «Индивидуально»: поля ввода описания/часов для этого дня.
        edit_wrap = QWidget(row)
        ew = QHBoxLayout(edit_wrap)
        ew.setContentsMargins(0, 0, 0, 0)
        ew.setSpacing(6)
        desc_edit = QLineEdit(edit_wrap)
        desc_edit.setObjectName("DayDescInput")
        desc_edit.setMinimumWidth(220)
        desc_edit.setText(str(self._config.defaults.get("task_description", "")))
        hours_spin = QDoubleSpinBox(edit_wrap)
        hours_spin.setObjectName("DayHoursInput")
        hours_spin.setRange(0.0, 24.0)
        hours_spin.setDecimals(2)
        hours_spin.setSingleStep(0.5)
        hours_spin.setValue(float(self._config.defaults.get("hours", 8) or 0))
        hours_spin.setMaximumWidth(90)
        ew.addWidget(desc_edit)
        ew.addWidget(hours_spin)
        ew.addWidget(QLabel("ч", edit_wrap))
        edit_wrap.setVisible(False)
        lay.addWidget(edit_wrap)

        return row, checkbox, d, info, edit_wrap, desc_edit, hours_spin

    def _refresh_day_rows(self) -> None:
        """Вид строк дней зависит от метода и отметки:

        - день НЕ отмечен → ни текста, ни полей;
        - «По-умолчанию» (auto) + отмечен → статический текст «<описание> · <часы> ч»;
        - «Индивидуально» (interactive) + отмечен → поля ввода описания/часов для дня.
        """
        interactive = self._method_combo.currentData() == METHOD_INTERACTIVE
        desc = self._description.text().strip()
        hours = self._hours.value()
        for checkbox, _d, info, edit_wrap, _desc_edit, _hours_spin in self._day_rows:
            checked = checkbox.isChecked()
            if not checked:
                info.setVisible(False)
                edit_wrap.setVisible(False)
            elif interactive:
                info.setVisible(False)
                edit_wrap.setVisible(True)
            else:
                info.setText(f"{desc} · {hours:g} ч")
                info.setVisible(True)
                edit_wrap.setVisible(False)

    # ------------------------------------------------------------------
    # CTA
    # ------------------------------------------------------------------
    def _build_cta(self) -> QPushButton:
        self._cta = QPushButton(_CTA_TEXT[MODE_EXPORT], self)
        self._cta.setObjectName("CtaButton")
        self._cta.clicked.connect(self.run_requested.emit)
        return self._cta

    # ------------------------------------------------------------------
    # Режим
    # ------------------------------------------------------------------
    def _apply_mode(self, mode: str, *, emit: bool = True) -> None:
        self._mode = mode
        is_export = mode == MODE_EXPORT
        self._btn_export.setChecked(is_export)
        self._btn_fill.setChecked(not is_export)

        self._period_section.setVisible(is_export)
        self._edit_section.setVisible(not is_export)
        self._cta.setText(_CTA_TEXT[mode])

        if emit:
            self.mode_changed.emit(mode)

    # ------------------------------------------------------------------
    # Вспомогательные конструкторы
    # ------------------------------------------------------------------
    def _make_date_edit(self) -> QDateEdit:
        edit = QDateEdit(self)
        edit.setObjectName("DateEdit")
        edit.setDisplayFormat(_DATE_FORMAT)
        edit.setCalendarPopup(True)
        edit.setDate(QDate.currentDate())
        return edit

    # ------------------------------------------------------------------
    # Публичный API (используется MainWindow)
    # ------------------------------------------------------------------
    def mode(self) -> str:
        return self._mode

    def method(self) -> str:
        """Метод заполнения (fill): ``"auto"`` / ``"interactive"``."""
        return self._method_combo.currentData()

    def description(self) -> str:
        """Описание задачи по-умолчанию (для заполнения)."""
        return self._description.text()

    def hours(self) -> float:
        """Часы по-умолчанию (для заполнения)."""
        return self._hours.value()

    def set_description(self, text: str) -> None:
        self._description.setText(text)

    def set_hours(self, value: float) -> None:
        self._hours.setValue(float(value))

    def selected_days(self) -> list[date]:
        """Отмеченные дни 4-дневного окна (Сегодня … 4 дня назад)."""
        return [d for checkbox, d, *_ in self._day_rows if checkbox.isChecked()]

    def per_day_values(self) -> "dict[date, tuple[str, float]]":
        """Значения (описание, часы) по каждому отмеченному дню.

        В «Индивидуально» берутся из полей строки, в «По-умолчанию» — из общих
        значений по-умолчанию. Пока используется для отображения; проводка в fill
        (чтобы значения строк реально применялись) — отдельный шаг.
        """
        interactive = self._method_combo.currentData() == METHOD_INTERACTIVE
        out: "dict[date, tuple[str, float]]" = {}
        for checkbox, d, _info, _wrap, desc_edit, hours_spin in self._day_rows:
            if not checkbox.isChecked():
                continue
            if interactive:
                out[d] = (desc_edit.text(), hours_spin.value())
            else:
                out[d] = (self._description.text(), self._hours.value())
        return out

    def date_from(self) -> date:
        return self._date_from.date().toPython()

    def date_to(self) -> date:
        return self._date_to.date().toPython()

    def result_table(self) -> ResultTable:
        return self._result_table

    def _refresh_result_visibility(self) -> None:
        """Показать таблицу результатов только при наличии строк."""
        has_rows = self._result_table.rowCount() > 0
        self._result_table.setVisible(has_rows)
        self._result_spacer.setVisible(not has_rows)

    def set_enabled(self, enabled: bool) -> None:
        """Блокировать/разблокировать управление на время операции."""
        widgets = [
            self._btn_export,
            self._btn_fill,
            self._date_from,
            self._date_to,
            self._method_combo,
            self._description,
            self._hours,
            self._schedule_check,
            self._cta,
        ]
        widgets.extend(checkbox for checkbox, *_ in self._day_rows)
        for w in widgets:
            w.setEnabled(enabled)
        self._schedule_time.setEnabled(enabled and self._schedule_check.isChecked())
        if enabled:
            self._apply_mode(self._mode, emit=False)

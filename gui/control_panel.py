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

Dry-run в GUI нет: боевая запись гейтится одним подтверждением в главном окне
(QMessageBox). В методе «Индивидуально» пишутся ТОЛЬКО отмеченные дни своими инлайн-
значениями (без per-day поп-апа), а недоступные дни окна (уже заполнены / не рабочий
тип / вне окна / нет записи) блокируются по статусам с портала (см. ``apply_day_states``).

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

# Метод заполнения (fill): auto («По-умолчанию», дефолт) — общие значения для всех
# кандидатов; interactive («Индивидуально») — пишутся только отмеченные дни своими
# инлайн-значениями, недоступные дни окна блокируются по статусам с портала.
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
    day_states_requested = Signal()

    def __init__(self, config, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self.setObjectName("ControlPanel")
        self._config = config
        self._mode = MODE_EXPORT
        # Строки дней: (checkbox, date, info_label, edit_wrap, desc_edit, hours_spin, reason_label).
        self._day_rows: list[tuple] = []
        # Последняя применённая карта статусов дней (iso→{fillable,reason}) или None.
        self._day_states: "dict | None" = None

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
        # Шапка секции: заголовок + кнопка «↻ Обновить» (видна только в «Индивидуально»,
        # перезапрашивает статусы дней с портала для блокировки недоступных чекбоксов).
        days_header = QHBoxLayout()
        days_header.setSpacing(8)
        self._days_caption = QLabel("ДНИ ДЛЯ ЗАПОЛНЕНИЯ", frame)
        self._days_caption.setObjectName("SectionCaption")
        days_header.addWidget(self._days_caption)
        days_header.addStretch(1)
        self._refresh_days_btn = QPushButton("↻ Обновить", frame)
        self._refresh_days_btn.setObjectName("RefreshDaysButton")
        self._refresh_days_btn.setToolTip("Перезапросить доступность дней с портала")
        self._refresh_days_btn.clicked.connect(self._maybe_request_day_states)
        self._refresh_days_btn.setVisible(False)
        days_header.addWidget(self._refresh_days_btn)
        box.addLayout(days_header)

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
        # …и управляет блокировкой недоступных дней (запрос статусов / снятие блокировки).
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)

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
        self._schedule_hint = QLabel("24ч", frame)
        sched.addWidget(self._schedule_check)
        sched.addWidget(self._schedule_time)
        sched.addWidget(self._schedule_hint)
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
    ) -> tuple[QFrame, QCheckBox, date, QLabel, QWidget, QLineEdit, QDoubleSpinBox, QLabel]:
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

        # Причина недоступности («Индивидуально»): показывается у заблокированных дней.
        reason = QLabel("", row)
        reason.setObjectName("DayReason")
        reason.setVisible(False)
        lay.addWidget(reason)

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

        return row, checkbox, d, info, edit_wrap, desc_edit, hours_spin, reason

    def _refresh_day_rows(self) -> None:
        """Вид строк дней зависит от метода и отметки:

        - день НЕ отмечен → ни текста, ни полей;
        - «По-умолчанию» (auto) + отмечен → статический текст «<описание> · <часы> ч»;
        - «Индивидуально» (interactive) + отмечен → поля ввода описания/часов для дня.
        """
        interactive = self._method_combo.currentData() == METHOD_INTERACTIVE
        desc = self._description.text().strip()
        hours = self._hours.value()
        for checkbox, _d, info, edit_wrap, _desc_edit, _hours_spin, _reason in self._day_rows:
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
        self._sync_refresh_btn_visibility()
        self._sync_schedule_enabled()

        if emit:
            # Реальное переключение режима пользователем: в «Индивидуально» (fill)
            # запросить статусы дней, иначе снять блокировку. При emit=False (восстановление
            # после busy) сеть не дёргаем — только пере-применяем уже известную блокировку.
            if self._is_individual_fill():
                self._maybe_request_day_states()
            else:
                self._unblock_all_days()
            self.mode_changed.emit(mode)
        else:
            self._reapply_block_state()

    # ------------------------------------------------------------------
    # Блокировка недоступных дней (Часть A, только метод «Индивидуально»)
    # ------------------------------------------------------------------
    def _is_individual_fill(self) -> bool:
        return (
            self._mode == MODE_FILL
            and self._method_combo.currentData() == METHOD_INTERACTIVE
        )

    def _sync_refresh_btn_visibility(self) -> None:
        self._refresh_days_btn.setVisible(self._is_individual_fill())

    def _on_method_changed(self) -> None:
        """Смена метода заполнения: показать/скрыть «Обновить», запросить/снять блокировку."""
        self._sync_refresh_btn_visibility()
        self._sync_schedule_enabled()
        if self._is_individual_fill():
            self._maybe_request_day_states()
        else:
            self._unblock_all_days()

    def _sync_schedule_enabled(self) -> None:
        """Строка «Запланировать» неактивна в методе «Индивидуально» (только «По-умолчанию»)."""
        active = not self._is_individual_fill()
        self._schedule_check.setEnabled(active)
        self._schedule_hint.setEnabled(active)
        # Время доступно только когда строка активна И чекбокс отмечен.
        self._schedule_time.setEnabled(active and self._schedule_check.isChecked())

    def _maybe_request_day_states(self) -> None:
        """Запросить статусы дней с портала (только в «Индивидуально»/fill)."""
        if not self._is_individual_fill():
            return
        self.set_day_states_loading()
        self.day_states_requested.emit()

    def set_day_states_loading(self) -> None:
        """Показать «загрузка…» и временно заблокировать чекбоксы дней (ожидание статусов)."""
        self._days_caption.setText("ДНИ ДЛЯ ЗАПОЛНЕНИЯ · загрузка…")
        for checkbox, _d, _info, _wrap, _desc, _hours, reason in self._day_rows:
            checkbox.setEnabled(False)
            reason.setVisible(False)

    def apply_day_states(self, mapping: "dict | None") -> None:
        """Применить карту статусов дней: недоступные → disabled + снять галку + причина.

        Применяется ТОЛЬКО в методе «Индивидуально». ``mapping`` — ``{iso_date:
        {"fillable": bool, "reason": str}}`` (из ``DayStatesWorker``). Отсутствующая в
        карте дата трактуется как недоступная («нет данных»).
        """
        self._day_states = dict(mapping or {})
        self._days_caption.setText("ДНИ ДЛЯ ЗАПОЛНЕНИЯ")
        if not self._is_individual_fill():
            # Метод переключили, пока грузились статусы — не блокируем.
            self._unblock_all_days()
            return
        self._reapply_block_state()
        self._refresh_day_rows()

    def _reapply_block_state(self) -> None:
        """Пере-применить последнюю карту статусов к чекбоксам (после busy/восстановления)."""
        if self._day_states is None or not self._is_individual_fill():
            return
        for checkbox, d, _info, _wrap, _desc, _hours, reason in self._day_rows:
            st = self._day_states.get(d.isoformat())
            fillable = bool(st and st.get("fillable"))
            if fillable:
                checkbox.setEnabled(True)
                checkbox.setToolTip("")
                reason.setText("")
                reason.setVisible(False)
            else:
                checkbox.setChecked(False)
                checkbox.setEnabled(False)
                text = (st or {}).get("reason") or "нет данных"
                checkbox.setToolTip(text)
                reason.setText(text)
                reason.setVisible(True)

    def apply_day_states_error(self) -> None:
        """Фолбэк при ошибке загрузки статусов: вернуть чекбоксы в активное состояние."""
        self._day_states = None
        self._days_caption.setText("ДНИ ДЛЯ ЗАПОЛНЕНИЯ · не удалось получить статусы")
        for checkbox, _d, _info, _wrap, _desc, _hours, reason in self._day_rows:
            checkbox.setEnabled(True)
            checkbox.setToolTip("")
            reason.setText("")
            reason.setVisible(False)

    def _unblock_all_days(self) -> None:
        """Снять любую блокировку дней (переход в «По-умолчанию»/export)."""
        self._day_states = None
        self._days_caption.setText("ДНИ ДЛЯ ЗАПОЛНЕНИЯ")
        for checkbox, _d, _info, _wrap, _desc, _hours, reason in self._day_rows:
            checkbox.setEnabled(True)
            checkbox.setToolTip("")
            reason.setText("")
            reason.setVisible(False)

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
        for checkbox, d, _info, _wrap, desc_edit, hours_spin, _reason in self._day_rows:
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
            self._refresh_days_btn,
            self._cta,
        ]
        widgets.extend(checkbox for checkbox, *_ in self._day_rows)
        for w in widgets:
            w.setEnabled(enabled)
        self._schedule_time.setEnabled(enabled and self._schedule_check.isChecked())
        if enabled:
            self._apply_mode(self._mode, emit=False)

"""Главное окно GUI bitrix24 «Рабочий день» (фаза 7 — пересборка под макет).

Компоновка приведена к ``design/bitrix24-console-standalone.html`` (источник истины):

    ┌ система: рамка Windows ────────────────────────────────────────┐
    │ СИНИЙ БАННЕР (accent): «Bitrix24 · Рабочий день»                │
    ├────────────────────────────────────────────────────────────────┤
    │ Меню: Файл · Настройки · Вид · Справка   [🌙]  [● Вебхук …]     │
    ├───────────────────────────────┬────────────────────────────────┤
    │ ControlPanel (управление)     │ ConsolePanel (тёмная консоль)   │
    ├───────────────────────────────┴────────────────────────────────┤
    │ Готов · Режим: Парсинг/Редактирование  <портал> · REST · 1208/1218│
    └────────────────────────────────────────────────────────────────┘

Решения пользователя: НЕ безрамочное окно (синий баннер под системным заголовком, НАД меню);
левая колонка режим-зависимая (в «Редактировании» — метод заполнения (комбобокс), описание/часы
по умолчанию, чекбоксы дней 4-дневного окна с инлайн-полями для метода «Индивидуально»,
«Запланировать»); dry-run из GUI убран — боевая запись гейтится подтверждением.

Бизнес-логика (export/fill, интерактив, идемпотентность) — в ``src/`` и ``gui/worker.py``
и НЕ меняется. Окно лишь собирает UI, проводит сигналы и запускает воркеры в QThread.
Все обновления виджетов — в main thread (через сигналы), воркеры виджеты не трогают.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.dates import today_moscow
from src.logging_setup import SecretMaskingFilter

from gui.confirm_dialog import ConfirmDialog
from gui.console_panel import ConsolePanel
from gui.control_panel import MODE_EXPORT, METHOD_INTERACTIVE, ControlPanel
from gui.theme import resolve_effective, theme
from gui.title_bar import TitleBar
from gui.worker import ExportWorker, FillWorker, SmokeWorker

APP_TITLE = "Bitrix24 — Рабочий день"

# Корень проекта = родитель каталога gui/ (рядом с src/, config.yaml, out/).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ICONS_DIR = _PROJECT_ROOT / "resources" / "icons"

# Маппинг action диалога подтверждения (ConfirmDialog) → choice, который ждёт
# FillWorker.provide_confirmation (см. gui/worker.py).
_ACTION_TO_CHOICE = {"ok": "ok", "apply_all": "all", "skip": "skip", "abort": "abort"}


class MainWindow(QMainWindow):
    """Каркас главного окна под макет. Принимает загруженный ``Config`` из ``src.config``."""

    def __init__(self, config, parent: "QWidget | None" = None) -> None:
        super().__init__(parent)
        self._config = config

        # Ссылки на активные воркеры (чтобы их не собрал GC до завершения).
        self._export_worker: "ExportWorker | None" = None
        self._fill_worker: "FillWorker | None" = None
        self._smoke_worker: "SmokeWorker | None" = None
        self._busy = False

        self.setWindowTitle(APP_TITLE)
        self.resize(1280, 860)

        self._build_menu()
        self._build_central()
        self._build_statusbar()
        self._wire_signals()
        self._sync_theme_icon()
        self._set_webhook_status(None)

        # Консоль слушает логгер «workday». Маск-фильтр навешиваем прямо на handler
        # (defense-in-depth, CLAUDE.md §4): он маскирует и записи дочерних логгеров
        # «workday.*», всплывающие к handler-у мимо фильтра самого логгера.
        self._console.attach(
            "workday",
            secret_filter=SecretMaskingFilter((self._config.env.webhook_code,)),
        )

    # ------------------------------------------------------------------
    # Меню + правый угол (тема + индикатор вебхука)
    # ------------------------------------------------------------------
    def _build_menu(self) -> None:
        # НЕ используем нативный self.menuBar() — иначе меню всегда сверху, НАД баннером.
        # Строим меню на отдельном QMenuBar и вставляем его в центральный layout ПОД
        # синим баннером (порядок макета: системный заголовок → синий баннер → меню).
        menubar = QMenuBar(self)
        self._menubar = menubar

        # --- Файл ---
        file_menu = menubar.addMenu("Файл")
        self._act_open_export = QAction("Открыть выгрузку (Excel)…", self)
        self._act_open_export.triggered.connect(self._open_export_file)
        file_menu.addAction(self._act_open_export)
        file_menu.addSeparator()
        act_exit = QAction("Выход", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        # --- Настройки ---
        # Дефолты «Описание/Часы» редактируются инлайн в колонке «Редактирование»
        # (см. gui/control_panel.py); отдельного диалога «Значения по-умолчанию» нет —
        # он дублировал те же поля.
        settings_menu = menubar.addMenu("Настройки")
        act_portal = QAction("Портал и вебхук…", self)
        act_portal.triggered.connect(self._show_portal_info)
        settings_menu.addAction(act_portal)
        settings_menu.addSeparator()
        act_open_folder = QAction("Открыть папку выгрузок", self)
        act_open_folder.triggered.connect(self._open_output_folder)
        settings_menu.addAction(act_open_folder)
        act_recheck = QAction("Проверить доступ к порталу", self)
        act_recheck.triggered.connect(self.check_webhook)
        settings_menu.addAction(act_recheck)

        # --- Вид ---
        view_menu = menubar.addMenu("Вид")
        theme_menu = view_menu.addMenu("Тема")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for label, mode in (
            ("Светлая", "light"),
            ("Тёмная", "dark"),
            ("Системная", "system"),
        ):
            act = QAction(label, self, checkable=True)
            act.setData(mode)
            act.triggered.connect(lambda _checked=False, m=mode: self._set_theme(m))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)
        self._sync_theme_menu()

        # --- Справка ---
        help_menu = menubar.addMenu("Справка")
        act_about = QAction("О программе", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

        # --- Правый угол: переключатель темы + индикатор вебхука ---
        corner = QWidget(self)
        corner.setObjectName("MenuCorner")
        crow = QHBoxLayout(corner)
        crow.setContentsMargins(0, 0, 10, 0)
        crow.setSpacing(10)

        self._theme_toggle = QToolButton(corner)
        self._theme_toggle.setObjectName("ThemeToggle")
        self._theme_toggle.setToolTip("Переключить тему (светлая/тёмная)")
        self._theme_toggle.setAutoRaise(True)
        self._theme_toggle.clicked.connect(self._toggle_theme)
        crow.addWidget(self._theme_toggle)

        self._webhook_indicator = QLabel("● Проверка…", corner)
        self._webhook_indicator.setObjectName("WebhookIndicator")
        crow.addWidget(self._webhook_indicator)

        menubar.setCornerWidget(corner, Qt.Corner.TopRightCorner)

    def _set_theme(self, mode: str) -> None:
        app = QApplication.instance()
        if app is not None:
            theme.apply(app, mode)
        self._sync_theme_menu()
        self._sync_theme_icon()

    def _toggle_theme(self) -> None:
        effective = resolve_effective(theme.current_mode())
        self._set_theme("light" if effective == "dark" else "dark")

    def _sync_theme_menu(self) -> None:
        current = theme.current_mode()
        for act in self._theme_group.actions():
            act.setChecked(act.data() == current)

    def _sync_theme_icon(self) -> None:
        """Иконка тумблера: луна (перейти в тёмную) когда сейчас светлая, и наоборот."""
        effective = resolve_effective(theme.current_mode())
        icon_name = "sun.svg" if effective == "dark" else "moon.svg"
        path = _ICONS_DIR / icon_name
        if path.exists():
            self._theme_toggle.setIcon(QIcon(str(path)))
        else:
            self._theme_toggle.setText("☾" if effective != "dark" else "☀")

    def _show_portal_info(self) -> None:
        """Read-only инфо о портале (домен и entityTypeId) БЕЗ кода вебхука."""
        domain = getattr(getattr(self._config, "env", None), "domain", None) or "—"
        QMessageBox.information(
            self,
            "Портал и вебхук",
            f"<b>Портал:</b> {domain}<br>"
            "<b>Доступ:</b> REST-вебхук (код хранится в .env, не отображается)<br>"
            "<b>Сущности:</b> 1208 «Рабочий день» / 1218 «Учёт рабочего времени»",
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "О программе",
            f"<b>{APP_TITLE}</b><br><br>"
            "GUI-обёртка над REST-автоматизацией Bitrix24.<br>"
            "Выгрузка «Рабочего дня» в Excel и автозаполнение «Учёта рабочего времени».",
        )

    # ------------------------------------------------------------------
    # Индикатор вебхука
    # ------------------------------------------------------------------
    def _set_webhook_status(self, ok: "bool | None") -> None:
        """Обновить индикатор «● Вебхук …» по результату smoke-проверки.

        ``None`` — идёт/не запускалась проверка (нейтральный), ``True`` — подключён
        (зелёный), ``False`` — недоступен (красный). Цвет задаётся QSS по dynamic
        property ``status`` (unknown/ok/fail). Секрет вебхука здесь не участвует.
        """
        if ok is None:
            text, status = "● Проверка…", "unknown"
        elif ok:
            text, status = "● Вебхук подключён", "ok"
        else:
            text, status = "● Вебхук недоступен", "fail"
        self._webhook_indicator.setText(text)
        self._webhook_indicator.setProperty("status", status)
        self._repolish(self._webhook_indicator)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    def check_webhook(self) -> None:
        """Запустить фоновую smoke-проверку доступа (обновит индикатор вебхука).

        Вызывается из ``gui_main`` после показа окна (не из ``__init__``), чтобы
        GUI-тесты, конструирующие ``MainWindow`` со стаб-конфигом, не делали сети.
        """
        if self._smoke_worker is not None and self._smoke_worker.isRunning():
            return
        self._set_webhook_status(None)
        worker = SmokeWorker(self._config)
        worker.smoke_result.connect(self._set_webhook_status)
        self._smoke_worker = worker
        worker.start()

    # ------------------------------------------------------------------
    # Центральная область: баннер + две колонки
    # ------------------------------------------------------------------
    def _build_central(self) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Порядок как в макете: синий баннер сверху, ПОД ним — меню, затем контент.
        self._title_bar = TitleBar(central)
        root.addWidget(self._title_bar)
        root.addWidget(self._menubar)

        split = QSplitter(Qt.Orientation.Horizontal, central)
        split.setObjectName("MainSplitter")
        split.setChildrenCollapsible(False)

        self._control = ControlPanel(self._config, split)
        split.addWidget(self._control)

        self._console = ConsolePanel(split)
        split.addWidget(self._console)

        # Консоль шире левой колонки (как в макете: ~44% / ~56%).
        split.setStretchFactor(0, 11)  # ControlPanel
        split.setStretchFactor(1, 14)  # ConsolePanel — больше веса
        split.setSizes([560, 720])
        root.addWidget(split, stretch=1)

        self.setCentralWidget(central)

    # ------------------------------------------------------------------
    # Статусбар
    # ------------------------------------------------------------------
    def _build_statusbar(self) -> None:
        status = QStatusBar(self)
        status.setObjectName("MainStatusBar")
        status.setSizeGripEnabled(False)

        self._status_left = QLabel("", self)
        self._status_left.setObjectName("StatusLabel")
        status.addWidget(self._status_left)

        self._status_right = QLabel(self._portal_summary(), self)
        self._status_right.setObjectName("StatusLabel")
        status.addPermanentWidget(self._status_right)

        self.setStatusBar(status)
        self._state_text = "Готов"
        self._update_status_left()

    def _portal_summary(self) -> str:
        """Правая часть статусбара: «<портал> · REST · 1208/1218» (без секрета).

        Портал берём из ``config.env.domain`` (host, не путь с кодом вебхука). Если
        поля нет (стаб-конфиг в тестах) — нейтральная заглушка.
        """
        domain = getattr(getattr(self._config, "env", None), "domain", None) or "портал"
        return f"{domain} · REST · 1208/1218"

    def _update_status_left(self) -> None:
        mode_ru = "Парсинг" if self._control.mode() == MODE_EXPORT else "Редактирование"
        self._status_left.setText(f"{self._state_text} · Режим: {mode_ru}")

    def set_state(self, text: str) -> None:
        self._state_text = text
        self._update_status_left()

    # ------------------------------------------------------------------
    # Файловые операции меню
    # ------------------------------------------------------------------
    def _resolve_output_dir(self) -> Path:
        out_dir = Path(str(self._config.export.get("output_dir", "./out")))
        if not out_dir.is_absolute():
            out_dir = _PROJECT_ROOT / out_dir
        return out_dir

    def _open_export_file(self) -> None:
        out_dir = self._resolve_output_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть выгрузку", str(out_dir), "Excel (*.xlsx)"
        )
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_output_folder(self) -> None:
        out_dir = self._resolve_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(out_dir.resolve())))

    # ------------------------------------------------------------------
    # Проводка сигналов
    # ------------------------------------------------------------------
    def _wire_signals(self) -> None:
        self._control.run_requested.connect(self._start_selected)
        self._control.mode_changed.connect(lambda _m: self._update_status_left())

    def _start_selected(self) -> None:
        """CTA «Получить выписку»: запустить выбранный режим (export/fill)."""
        if self._control.mode() == MODE_EXPORT:
            self._start_export()
        else:
            self._start_fill()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _start_export(self) -> None:
        if self._busy:
            return
        date_from = self._control.date_from()
        date_to = self._control.date_to()

        self._set_busy_ui(True)
        self.set_state("Выгрузка…")
        self._console.set_status("Выполняется выгрузка…")

        worker = ExportWorker(self._config, date_from, date_to)
        worker.result_ready.connect(self._on_export_result)
        worker.finished_code.connect(self._on_op_finished)
        self._export_worker = worker
        worker.start()

    def _on_export_result(self, payload: object) -> None:
        days, user_map = payload  # type: ignore[misc]
        self._control.result_table().show_export(days, user_map)

    # ------------------------------------------------------------------
    # Fill
    # ------------------------------------------------------------------
    def _start_fill(self) -> None:
        if self._busy:
            return
        interaction = self._control.method() == METHOD_INTERACTIVE
        today: date = today_moscow()

        # Гейт §5: БЕЗУСЛОВНОЕ подтверждение боевого прогона — до старта воркера для
        # ОБОИХ методов. Причина: запись идёт не только при создании учётов, но и в
        # «ремонте» (complete_day_activities → crm.activity.update, src/fill.py), который
        # НЕ проходит через per-day ConfirmDialog. Раньше это прикрывал дефолтный dry-run;
        # после его удаления единственная защита — этот явный диалог. Метод «Индивидуально»
        # ДОПОЛНИТЕЛЬНО подтверждает создание учётов по дням (per-day ConfirmDialog).
        if not self._confirm_live_write():
            return

        self._set_busy_ui(True)
        self.set_state("Заполнение…")
        self._console.set_status("Выполняется заполнение…")

        worker = FillWorker(
            self._config,
            dry_run=False,
            interaction=interaction,
            today=today,
            description=self._control.description(),
            hours=self._control.hours(),
        )
        worker.result_ready.connect(self._on_fill_result)
        worker.finished_code.connect(self._on_op_finished)
        worker.confirm_requested.connect(self._on_confirm_requested)
        self._fill_worker = worker
        worker.start()

    def _confirm_live_write(self) -> bool:
        """Диалог подтверждения боевой записи (метод «По-умолчанию», текст макета).

        Возвращает True, если пользователь нажал «Подтвердить запись».
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Боевой режим — подтверждение записи")
        box.setText(
            "Будет выполнена реальная запись в прод. "
            "Проверьте параметры перед подтверждением."
        )
        confirm_btn = box.addButton(
            "Подтвердить запись", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(box.buttons()[-1])  # дефолт — «Отмена» (безопаснее)
        box.exec()
        return box.clickedButton() is confirm_btn

    def _on_fill_result(self, rows: object) -> None:
        self._control.result_table().show_fill(rows)  # type: ignore[arg-type]

    def _on_confirm_requested(self, day_info: object) -> None:
        """Показать ``ConfirmDialog`` по дню и передать выбор пользователя воркеру.

        Вызывается слотом сигнала ``confirm_requested`` (уже в main thread), поэтому
        модальный ``exec()`` здесь безопасен. Маппинг action→choice — ``_ACTION_TO_CHOICE``.
        """
        info = day_info if isinstance(day_info, dict) else {}
        result = ConfirmDialog.request_confirmation(info, parent=self)
        choice = _ACTION_TO_CHOICE.get(result["action"], "abort")
        if self._fill_worker is not None:
            self._fill_worker.provide_confirmation(
                {
                    "choice": choice,
                    "description": result["description"],
                    "hours": result["hours"],
                }
            )

    # ------------------------------------------------------------------
    # Общие слоты операций
    # ------------------------------------------------------------------
    def _on_op_finished(self, code: int) -> None:
        self._set_busy_ui(False)
        text = "Готов" if code == 0 else "Завершено с ошибками"
        self.set_state(text)
        self._console.set_status("Ожидание" if code == 0 else "Завершено с ошибками")

    def _set_busy_ui(self, busy: bool) -> None:
        self._busy = busy
        self._console.set_busy(busy)
        self._control.set_enabled(not busy)

    # ------------------------------------------------------------------
    # Корректное закрытие при активной операции
    # ------------------------------------------------------------------
    _CLOSE_WAIT_MS = 5000

    def _active_worker(self):
        """Вернуть запущенный воркер (fill/export/smoke) или None."""
        for worker in (self._fill_worker, self._export_worker, self._smoke_worker):
            if worker is not None and worker.isRunning():
                return worker
        return None

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Не дать уничтожить QThread на ходу и не оставить утёкший monkeypatch.

        Если воркер активен: для ``FillWorker`` сначала разблокируем возможное ожидание
        подтверждения (``provide_confirmation({"choice":"abort"})`` — no-op, если воркер
        не ждёт), затем ``quit()`` + ``wait(timeout)``. Если за таймаут воркер не
        завершился — предупреждаем и оставляем окно открытым (``event.ignore()``).
        """
        worker = self._active_worker()
        if worker is not None:
            if isinstance(worker, FillWorker):
                worker.provide_confirmation({"choice": "abort"})
            worker.quit()
            if not worker.wait(self._CLOSE_WAIT_MS):
                QMessageBox.warning(
                    self,
                    "Операция выполняется",
                    "Операция ещё выполняется. Дождитесь её завершения перед закрытием окна.",
                )
                event.ignore()
                return
        event.accept()

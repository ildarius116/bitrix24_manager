"""Smoke-инстанцирование ключевых GUI-виджетов (фаза 6_05).

Без сети: ``MainWindow`` конструируется с лёгким стаб-конфигом (SimpleNamespace),
без ``load_config()``/``.env`` — конструкторы виджетов не делают REST-вызовов
(воркеры стартуют только по клику кнопки в ribbon), так что сетевого обращения
не происходит независимо от источника конфига. Используем стаб, чтобы тест
не зависел от наличия .env на машине CI.
"""

from __future__ import annotations

import types

import pytest

from gui.console_panel import ConsolePanel
from gui.control_panel import (
    METHOD_AUTO,
    METHOD_INTERACTIVE,
    MODE_EXPORT,
    MODE_FILL,
    ControlPanel,
)
from gui.log_panel import LogPanel
from gui.main_window import MainWindow
from gui.result_table import ResultTable
from gui.title_bar import TitleBar


def _make_stub_config() -> types.SimpleNamespace:
    """Лёгкий стаб Config, достаточный для конструкторов ControlPanel/MainWindow.

    Конструкторы виджетов читают только ``defaults`` (dict-like с .get) и
    ``export`` (dict-like с .get) — никаких сетевых обращений при __init__.
    """
    return types.SimpleNamespace(
        defaults={"task_description": "Общие задачи подразделения", "hours": 8},
        export={"output_dir": "./out", "filename_pattern": "workday_{date_from}_{date_to}.xlsx"},
        # MainWindow навешивает SecretMaskingFilter на лог-хэндлер (защита от утечки
        # секрета в дочерних логгерах «workday.*») — читает env.webhook_code при __init__.
        env=types.SimpleNamespace(webhook_code="dummy-webhook-not-a-secret"),
    )


class TestWidgetSmokeInstantiation:
    def test_title_bar_constructs_without_exception(self, qapp):
        widget = TitleBar()
        assert widget is not None

    def test_console_panel_constructs_without_exception(self, qapp):
        widget = ConsolePanel()
        assert widget is not None
        widget.attach("workday_console_smoke_logger")
        widget.detach()
        widget.clear()

    def test_control_panel_constructs_without_exception(self, qapp):
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        assert widget is not None
        # Стартовый режим — export; метод fill по умолчанию «По-умолчанию» (как в макете).
        assert widget.mode() == MODE_EXPORT
        assert widget.method() == METHOD_AUTO
        # dry-run в колонке нет; Описание/Часы по-умолчанию — инлайн (по макету).
        assert not hasattr(widget, "dry_run")
        assert not hasattr(widget, "auto_mode")
        assert widget.description() == "Общие задачи подразделения"
        assert widget.hours() == 8.0
        # Дни 4-дневного окна: 5 строк, «Сегодня» отмечен.
        assert len(widget._day_rows) == 5
        assert widget._day_rows[0][0].isChecked() is True

    def test_control_panel_export_mode_hides_edit_section(self, qapp):
        """В «Парсинге» видна секция «Период», секция «Редактирование» скрыта.

        Полей «Описание задачи»/«Часы» в колонке нет вовсе (перенесены в Настройки).
        """
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        widget.show()  # чтобы isVisible() отражал состояние секций
        try:
            assert widget.mode() == MODE_EXPORT
            assert widget._period_section.isVisible() is True
            assert widget._edit_section.isVisible() is False
            # Датапикеры на месте (для выгрузки по периоду).
            assert widget._date_from.isEnabled() is True
        finally:
            widget.hide()

    def test_control_panel_fill_mode_shows_edit_section(self, qapp):
        """В «Редактировании» видна секция «Редактирование» (Метод/Записей/Расписание),
        секция «Период» скрыта, CTA='Выполнить'."""
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        widget.show()
        try:
            widget._apply_mode(MODE_FILL)
            assert widget.mode() == MODE_FILL
            assert widget._edit_section.isVisible() is True
            assert widget._period_section.isVisible() is False
            assert widget._cta.text() == "Выполнить"
            # Контролы по макету присутствуют.
            assert widget._method_combo.count() == 2
            assert widget._description.text() == "Общие задачи подразделения"
            assert len(widget._day_rows) == 5
            assert widget._schedule_check is not None
        finally:
            widget.hide()

    def test_control_panel_result_table_hidden_when_empty(self, qapp):
        """Пустая таблица результатов скрыта; вместо неё спейсер прижимает CTA к низу."""
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        widget.show()
        try:
            assert widget._result_table.rowCount() == 0
            assert widget._result_table.isVisible() is False
            assert widget._result_spacer.isVisible() is True
        finally:
            widget.hide()

    def test_control_panel_result_table_shown_after_fill_data(self, qapp):
        """После show_fill с данными таблица становится видимой, спейсер скрывается."""
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        widget.show()
        try:
            widget.result_table().show_fill(
                [{"date": "01.07.2026", "day_id": 1, "status": "filled", "new_id": 10}]
            )
            assert widget._result_table.rowCount() == 1
            assert widget._result_table.isVisible() is True
            assert widget._result_spacer.isVisible() is False
        finally:
            widget.hide()

    def test_control_panel_result_table_shown_after_export_data(self, qapp):
        """После show_export с данными таблица видима (спейсер скрыт)."""
        import types as _types
        from datetime import date as _date

        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        widget.show()
        try:
            day = _types.SimpleNamespace(
                date=_date(2026, 7, 1), employee="1", works_ids=[], logs=[]
            )
            widget.result_table().show_export([day], {})
            assert widget._result_table.isVisible() is True
            assert widget._result_spacer.isVisible() is False
        finally:
            widget.hide()

    def test_control_panel_method_toggle(self, qapp):
        """method() по умолчанию auto («По-умолчанию») и переключается на interactive."""
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        assert widget.method() == METHOD_AUTO
        widget._method_combo.setCurrentIndex(widget._method_combo.findData(METHOD_INTERACTIVE))
        assert widget.method() == METHOD_INTERACTIVE
        widget._method_combo.setCurrentIndex(widget._method_combo.findData(METHOD_AUTO))
        assert widget.method() == METHOD_AUTO

    def test_control_panel_cta_text_switches_by_mode(self, qapp):
        """CTA-лейбл зависит от режима: Парсинг → «Получить выписку», fill → «Выполнить»."""
        cfg = _make_stub_config()
        widget = ControlPanel(cfg)
        assert "Получить выписку" in widget._cta.text()
        widget._apply_mode(MODE_FILL)
        assert widget._cta.text() == "Выполнить"
        widget._apply_mode(MODE_EXPORT)
        assert "Получить выписку" in widget._cta.text()

    def test_log_panel_constructs_without_exception(self, qapp):
        widget = LogPanel()
        assert widget is not None
        widget.attach("workday_smoke_test_logger")
        widget.detach()

    def test_result_table_constructs_without_exception(self, qapp):
        widget = ResultTable()
        assert widget is not None
        assert widget.rowCount() == 0

    def test_main_window_constructs_without_exception(self, qapp):
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        assert window is not None
        assert window.windowTitle() == "Bitrix24 — Рабочий день"

    def test_main_window_no_busy_workers_on_construction(self, qapp):
        """MainWindow не создаёт воркеров/не стартует фоновые операции при __init__."""
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        assert window._export_worker is None
        assert window._fill_worker is None
        assert window._busy is False

    def test_main_window_no_dry_run_in_gui(self, qapp):
        """Dry-run убран из GUI: у ControlPanel нет метода dry_run, статусбар без «пробный»."""
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        assert not hasattr(window._control, "dry_run")
        assert "пробный" not in window._status_left.text().lower()
        assert "dry-run" not in window._status_left.text().lower()

    def test_main_window_defaults_seed_control_panel(self, qapp):
        """Дефолты «Описание/Часы» из config.defaults попадают в инлайн-поля колонки
        «Редактирование» (отдельного диалога «Значения по-умолчанию» больше нет)."""
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        assert window._control.description() == "Общие задачи подразделения"
        assert window._control.hours() == 8.0

    def test_main_window_no_smoke_worker_on_construction(self, qapp):
        """__init__ НЕ запускает smoke-проверку (сеть только по check_webhook())."""
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        assert window._smoke_worker is None


class TestMainWindowRealConfigIfAvailable:
    """Дополнительная (опциональная) проверка с реальным load_config(), если .env есть.

    Сетевых вызовов всё равно не делает — load_config() только читает .env/config.yaml.
    Если .env отсутствует на машине — тест пропускается (ConfigError ожидаем, не падаем).
    """

    def test_main_window_with_real_config(self, qapp):
        from src.config import ConfigError, load_config

        try:
            cfg = load_config()
        except ConfigError:
            pytest.skip(".env отсутствует/не заполнен — пропуск (нет секретов в CI)")

        window = MainWindow(cfg)
        assert window is not None

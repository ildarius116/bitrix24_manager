"""Тесты правой консоли и эвристики категорий журнала (фаза 7).

Без сети. ``classify_record`` — чистая функция (тестируется без QApplication).
Фильтрация ``ConsolePanel`` и счётчик строк — с offscreen QApplication (фикстура ``qapp``).
"""

from __future__ import annotations

import pytest

from gui.console_panel import ConsolePanel
from gui.log_panel import classify_record


# ---------------------------------------------------------------------------
# classify_record — единая эвристика категорий (чистая функция).
# ---------------------------------------------------------------------------


class TestClassifyRecord:
    def test_rest_by_logger_name(self):
        assert classify_record("workday.b24", "INFO", "что-то") == "rest"
        assert classify_record("some.rest.client", "INFO", "x") == "rest"

    @pytest.mark.parametrize(
        "msg",
        [
            "crm.item.list вызван",
            "batch: 3 запроса",
            "user.current проверка",
            "Доступ ОК: Иванов",
        ],
    )
    def test_rest_by_message_marker(self, msg):
        assert classify_record("workday", "INFO", msg) == "rest"

    def test_error_level_without_rest_marker(self):
        assert classify_record("workday", "ERROR", "обычная ошибка") == "error"
        assert classify_record("workday", "WARNING", "предупреждение") == "error"

    def test_ok_info_without_markers(self):
        assert classify_record("workday", "INFO", "день заполнен") == "ok"

    def test_rest_priority_over_error_level(self):
        # REST-ошибка всё равно относится к категории REST (маркер важнее уровня).
        assert classify_record("workday", "ERROR", "crm.item.add отклонён") == "rest"


# ---------------------------------------------------------------------------
# ConsolePanel — накопление записей, счётчик, фильтрация.
# ---------------------------------------------------------------------------


class TestConsolePanelFiltering:
    def _feed(self, panel: ConsolePanel) -> None:
        # 1 REST, 1 OK, 1 error.
        panel._on_record("INFO", "workday", "Доступ ОК: тест")            # rest
        panel._on_record("INFO", "workday", "день 25.06 заполнен")        # ok
        panel._on_record("ERROR", "workday", "не удалось записать день")  # error

    def test_counter_counts_all_when_filter_all(self, qapp):
        panel = ConsolePanel()
        self._feed(panel)
        assert panel._visible_count() == 3

    def test_filter_rest_only(self, qapp):
        panel = ConsolePanel()
        self._feed(panel)
        panel._set_filter({"rest"})
        assert panel._visible_count() == 1

    def test_filter_errors_only(self, qapp):
        panel = ConsolePanel()
        self._feed(panel)
        panel._set_filter({"error"})
        assert panel._visible_count() == 1

    def test_filter_ok_only(self, qapp):
        panel = ConsolePanel()
        self._feed(panel)
        panel._set_filter({"ok"})
        assert panel._visible_count() == 1

    def test_clear_resets_records_and_counter(self, qapp):
        panel = ConsolePanel()
        self._feed(panel)
        panel.clear()
        assert panel._visible_count() == 0
        assert panel._records == []

    def test_set_busy_toggles_indeterminate_range(self, qapp):
        panel = ConsolePanel()
        panel.set_busy(True)
        assert panel._progress.minimum() == 0 and panel._progress.maximum() == 0
        panel.set_busy(False)
        assert panel._progress.maximum() == 100

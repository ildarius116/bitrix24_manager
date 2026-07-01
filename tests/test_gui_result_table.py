"""Тесты gui/result_table.py (фаза 6_05).

Без сети. ``status_color_token`` — чистая функция (тестируется без QApplication).
``ResultTable.show_export`` / ``show_fill`` строят строки таблицы из синтетических
``WorkdayDay`` / result-dict — требуют offscreen QApplication (фикстура ``qapp``).
"""

from __future__ import annotations

from datetime import date

import pytest

from src.workday import WorkdayDay, WorkLog

from gui.result_table import ResultTable, status_color_token

# ---------------------------------------------------------------------------
# status_color_token — чистая функция, все ветки.
# ---------------------------------------------------------------------------


class TestStatusColorToken:
    @pytest.mark.parametrize("status", ["filled", "repaired"])
    def test_ok_statuses(self, status):
        assert status_color_token(status) == "status_ok"

    def test_dry_run(self):
        assert status_color_token("dry-run") == "status_running"

    @pytest.mark.parametrize("status", ["skipped", "already-closed"])
    def test_muted_statuses(self, status):
        assert status_color_token(status) == "text_muted"

    def test_error(self):
        assert status_color_token("error") == "status_fail"

    def test_aborted(self):
        assert status_color_token("aborted") == "status_warning"

    def test_unknown_status_defaults_to_muted(self):
        assert status_color_token("совершенно-неизвестный-статус") == "text_muted"

    def test_empty_string_defaults_to_muted(self):
        assert status_color_token("") == "text_muted"


# ---------------------------------------------------------------------------
# Синтетические данные.
# ---------------------------------------------------------------------------


def _make_log(
    log_id: int = 1,
    parent_day_id: int = 100,
    title: str = "Общие задачи подразделения | 8:00",
    description: str = "Описание работы",
    hours: float = 8.0,
) -> WorkLog:
    return WorkLog(
        id=log_id,
        parent_day_id=parent_day_id,
        title=title,
        description=description,
        hours=hours,
        contract="T512_2",
        result=hours,
        raw={"SECRET_FIELD": "should-not-leak"},
    )


def _make_day(
    day_id: int = 100,
    d: date = date(2026, 6, 25),
    employee: str = "1244",
    works_ids=None,
    logs=None,
    title: str = "Иванов И.И. | 25.06.2026",
) -> WorkdayDay:
    return WorkdayDay(
        id=day_id,
        date=d,
        title=title,
        employee=employee,
        works_ids=works_ids if works_ids is not None else [],
        raw={},
        logs=logs if logs is not None else [],
    )


# ---------------------------------------------------------------------------
# show_export
# ---------------------------------------------------------------------------


class TestShowExport:
    def test_row_count_matches_days(self, qapp):
        table = ResultTable()
        days = [_make_day(day_id=1), _make_day(day_id=2)]
        table.show_export(days)
        assert table.rowCount() == 2

    def test_column_count_is_export_headers(self, qapp):
        table = ResultTable()
        table.show_export([_make_day()])
        assert table.columnCount() == 5

    def test_date_cell_formatted(self, qapp):
        table = ResultTable()
        table.show_export([_make_day(d=date(2026, 6, 25))])
        assert table.item(0, 0).text() == "25.06.2026"

    def test_employee_resolved_via_user_map(self, qapp):
        table = ResultTable()
        days = [_make_day(employee="1244")]
        table.show_export(days, user_map={"1244": "Иванов И.И."})
        assert table.item(0, 1).text() == "Иванов И.И."

    def test_employee_falls_back_to_raw_id_when_not_in_map(self, qapp):
        table = ResultTable()
        days = [_make_day(employee="9999")]
        table.show_export(days, user_map={})
        assert table.item(0, 1).text() == "9999"

    def test_filled_count_from_works_ids(self, qapp):
        table = ResultTable()
        days = [_make_day(works_ids=[10, 20, 30])]
        table.show_export(days)
        assert table.item(0, 2).text() == "3"

    def test_hours_sum_from_logs(self, qapp):
        table = ResultTable()
        logs = [_make_log(log_id=1, hours=8.0), _make_log(log_id=2, hours=2.5)]
        days = [_make_day(logs=logs)]
        table.show_export(days)
        assert table.item(0, 3).text() == "10.5"

    def test_hours_sum_integer_formatted_without_decimal(self, qapp):
        table = ResultTable()
        logs = [_make_log(log_id=1, hours=4.0), _make_log(log_id=2, hours=4.0)]
        days = [_make_day(logs=logs)]
        table.show_export(days)
        assert table.item(0, 3).text() == "8"

    def test_no_logs_zero_hours(self, qapp):
        table = ResultTable()
        days = [_make_day(logs=[])]
        table.show_export(days)
        assert table.item(0, 3).text() == "0"

    def test_titles_joined(self, qapp):
        table = ResultTable()
        logs = [_make_log(log_id=1, title="Работа A"), _make_log(log_id=2, title="Работа B")]
        days = [_make_day(logs=logs)]
        table.show_export(days)
        assert table.item(0, 4).text() == "Работа A, Работа B"

    def test_empty_days_zero_rows(self, qapp):
        table = ResultTable()
        table.show_export([])
        assert table.rowCount() == 0

    def test_row_selected_emits_day_object(self, qapp):
        table = ResultTable()
        day = _make_day(day_id=42)
        table.show_export([day])
        received = []
        table.row_selected.connect(lambda obj: received.append(obj))
        table.selectRow(0)
        assert received == [day]

    def test_reraw_clears_previous_objects(self, qapp):
        """Повторный вызов show_export сбрасывает предыдущие строки/объекты."""
        table = ResultTable()
        table.show_export([_make_day(day_id=1), _make_day(day_id=2)])
        table.show_export([_make_day(day_id=99)])
        assert table.rowCount() == 1
        assert table._objects[0].id == 99


# ---------------------------------------------------------------------------
# show_fill
# ---------------------------------------------------------------------------


def _make_fill_row(
    day_id: int = 100,
    d: str = "2026-06-25",
    status: str = "filled",
    new_id=None,
    verify_ok=None,
    reason: str = "",
    activity_status: str = "completed",
    activity_ids=None,
    activity_ok: bool = True,
):
    return {
        "day_id": day_id,
        "date": d,
        "status": status,
        "new_id": new_id,
        "verify_ok": verify_ok,
        "reason": reason,
        "activity_status": activity_status,
        "activity_ids": activity_ids if activity_ids is not None else [],
        "activity_ok": activity_ok,
    }


class TestShowFill:
    def test_row_count_matches_rows(self, qapp):
        table = ResultTable()
        rows = [_make_fill_row(day_id=1), _make_fill_row(day_id=2), _make_fill_row(day_id=3)]
        table.show_fill(rows)
        assert table.rowCount() == 3

    def test_column_count_is_fill_headers(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row()])
        assert table.columnCount() == 6

    def test_date_and_day_id_cells(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row(day_id=777, d="2026-06-25")])
        assert table.item(0, 0).text() == "2026-06-25"
        assert table.item(0, 1).text() == "777"

    def test_status_cell_text(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row(status="filled")])
        assert table.item(0, 2).text() == "filled"

    def test_new_id_cell(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row(new_id=999)])
        assert table.item(0, 3).text() == "999"

    def test_new_id_missing_shows_dash(self, qapp):
        table = ResultTable()
        rows = [
            {
                "day_id": 1,
                "date": "2026-06-25",
                "status": "skipped",
                "reason": "вне окна",
            }
        ]
        table.show_fill(rows)
        assert table.item(0, 3).text() == "—"

    def test_activity_status_cell(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row(activity_status="completed")])
        assert table.item(0, 4).text() == "completed"

    def test_reason_cell(self, qapp):
        table = ResultTable()
        table.show_fill([_make_fill_row(reason="дата вне окна")])
        assert table.item(0, 5).text() == "дата вне окна"

    def test_none_reason_becomes_empty_string(self, qapp):
        table = ResultTable()
        rows = [_make_fill_row(reason=None)]
        # reason=None через make_fill_row дефолт str ""; принудительно None в dict
        rows[0]["reason"] = None
        table.show_fill(rows)
        assert table.item(0, 5).text() == ""

    def test_empty_rows_zero_rowcount(self, qapp):
        table = ResultTable()
        table.show_fill([])
        assert table.rowCount() == 0

    def test_none_rows_treated_as_empty(self, qapp):
        table = ResultTable()
        table.show_fill(None)  # type: ignore[arg-type]
        assert table.rowCount() == 0

    def test_row_selected_emits_dict(self, qapp):
        table = ResultTable()
        row = _make_fill_row(day_id=55)
        table.show_fill([row])
        received = []
        table.row_selected.connect(lambda obj: received.append(obj))
        table.selectRow(0)
        assert received == [row]

    @pytest.mark.parametrize(
        "status,token",
        [
            ("filled", "status_ok"),
            ("repaired", "status_ok"),
            ("dry-run", "status_running"),
            ("skipped", "text_muted"),
            ("already-closed", "text_muted"),
            ("error", "status_fail"),
            ("aborted", "status_warning"),
        ],
    )
    def test_status_cell_color_matches_token(self, qapp, status, token):
        """Цвет ячейки «Статус» соответствует токену палитры status_color_token(status)."""
        from gui.theme import theme

        table = ResultTable()
        table.show_fill([_make_fill_row(status=status)])
        expected_hex = theme.current_palette().get(token)
        item = table.item(0, 2)
        if expected_hex:
            assert item.foreground().color().name() == expected_hex.lower()

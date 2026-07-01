"""Тесты gui/confirm_dialog.py::ConfirmDialog (фаза 6_05).

Без сети, без exec(): кнопки дёргаются программно (clicked.emit() / прямой вызов
слота через _finish), затем читаем result_payload(). Требует offscreen QApplication.
"""

from __future__ import annotations

from gui.confirm_dialog import ConfirmDialog


def _make_dialog(qapp, **info):
    day_info = {
        "default_description": "Общие задачи подразделения",
        "default_hours": 8.0,
        "day_id": "12345",
        "day_date": "25.06.2026",
    }
    day_info.update(info)
    return ConfirmDialog(day_info)


class TestButtonActionMapping:
    def test_ok_button_maps_to_action_ok(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_ok.click()
        payload = dlg.result_payload()
        assert payload["action"] == "ok"

    def test_apply_all_button_maps_to_action_apply_all(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_apply_all.click()
        payload = dlg.result_payload()
        assert payload["action"] == "apply_all"

    def test_skip_button_maps_to_action_skip(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_skip.click()
        payload = dlg.result_payload()
        assert payload["action"] == "skip"

    def test_cancel_button_maps_to_action_abort(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_cancel.click()
        payload = dlg.result_payload()
        assert payload["action"] == "abort"

    def test_close_without_click_defaults_to_abort(self, qapp):
        """Диалог, закрытый без клика по кнопке (reject()/Esc/крестик), даёт action='abort'."""
        dlg = _make_dialog(qapp)
        dlg.reject()
        payload = dlg.result_payload()
        assert payload["action"] == "abort"

    def test_ok_button_accepts_dialog(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_ok.click()
        assert dlg.result() == ConfirmDialog.DialogCode.Accepted

    def test_apply_all_button_accepts_dialog(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_apply_all.click()
        assert dlg.result() == ConfirmDialog.DialogCode.Accepted

    def test_skip_button_rejects_dialog(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_skip.click()
        assert dlg.result() == ConfirmDialog.DialogCode.Rejected

    def test_cancel_button_rejects_dialog(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._btn_cancel.click()
        assert dlg.result() == ConfirmDialog.DialogCode.Rejected


class TestPayloadDescriptionAndHours:
    def test_default_description_prefilled(self, qapp):
        dlg = _make_dialog(qapp, default_description="Дефолтное описание")
        payload = dlg.result_payload()
        assert payload["description"] == "Дефолтное описание"

    def test_default_hours_prefilled(self, qapp):
        dlg = _make_dialog(qapp, default_hours=6.5)
        payload = dlg.result_payload()
        assert payload["hours"] == 6.5

    def test_edited_description_reflected_in_payload(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._description.setText("Новое описание")
        dlg._btn_ok.click()
        payload = dlg.result_payload()
        assert payload["description"] == "Новое описание"

    def test_edited_hours_reflected_in_payload(self, qapp):
        dlg = _make_dialog(qapp)
        dlg._hours.setValue(3.5)
        dlg._btn_ok.click()
        payload = dlg.result_payload()
        assert payload["hours"] == 3.5

    def test_invalid_default_hours_coerced_to_zero(self, qapp):
        dlg = _make_dialog(qapp, default_hours="не число")
        payload = dlg.result_payload()
        assert payload["hours"] == 0.0

    def test_none_default_hours_coerced_to_zero(self, qapp):
        dlg = _make_dialog(qapp, default_hours=None)
        payload = dlg.result_payload()
        assert payload["hours"] == 0.0


class TestDayLabel:
    def test_day_label_shows_date_and_id(self, qapp):
        from PySide6.QtWidgets import QLabel

        dlg = _make_dialog(qapp, day_id="999", day_date="25.06.2026")
        label = dlg.findChild(QLabel, "ConfirmDayLabel")
        assert label is not None
        assert "25.06.2026" in label.text()
        assert "999" in label.text()

    def test_day_label_dash_when_no_date(self, qapp):
        from PySide6.QtWidgets import QLabel

        dlg = _make_dialog(qapp, day_id="", day_date="")
        label = dlg.findChild(QLabel, "ConfirmDayLabel")
        assert label.text() == "День: —"

    def test_day_label_without_id_no_suffix(self, qapp):
        from PySide6.QtWidgets import QLabel

        dlg = _make_dialog(qapp, day_id="", day_date="25.06.2026")
        label = dlg.findChild(QLabel, "ConfirmDayLabel")
        assert label.text() == "День: 25.06.2026"

    def test_none_day_info_defaults_to_dash(self, qapp):
        from PySide6.QtWidgets import QLabel

        dlg = ConfirmDialog(None)
        label = dlg.findChild(QLabel, "ConfirmDayLabel")
        assert label.text() == "День: —"

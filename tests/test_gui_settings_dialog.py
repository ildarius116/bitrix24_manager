"""Тесты gui/settings_dialog.py::SettingsDialog (фаза 7 — исправление под макет).

Без сети. Проверяем, что диалог пре-филлит переданные значения и отдаёт их обратно
через ``values()`` / ``description()`` / ``hours()``. Требует offscreen QApplication
(фикстура ``qapp``).
"""

from __future__ import annotations

from gui.settings_dialog import SettingsDialog


class TestSettingsDialog:
    def test_prefills_and_returns_values(self, qapp):
        dialog = SettingsDialog("Моё описание", 6.5)
        assert dialog.description() == "Моё описание"
        assert dialog.hours() == 6.5
        assert dialog.values() == ("Моё описание", 6.5)

    def test_edited_values_are_returned(self, qapp):
        dialog = SettingsDialog("Старое", 8.0)
        dialog._description.setText("Новое")
        dialog._hours.setValue(4.0)
        assert dialog.values() == ("Новое", 4.0)

    def test_invalid_hours_coerced_to_zero(self, qapp):
        dialog = SettingsDialog("x", "не число")  # type: ignore[arg-type]
        assert dialog.hours() == 0.0

    def test_hours_clamped_to_range(self, qapp):
        dialog = SettingsDialog("x", 999)
        assert dialog.hours() == 24.0

"""Тест единственного safety-гейта боевой записи fill (фаза 7 — после удаления dry-run).

Проверяет, что ``MainWindow._start_fill`` НЕ стартует ``FillWorker`` без явного
подтверждения — для ОБОИХ методов («По-умолчанию»/auto и «Индивидуально»/interactive).
Это критично: после удаления дефолтного dry-run единственная защита от боевой записи
(создание учётов И «ремонт» закрытия дел crm.activity.update) — диалог
«Боевой режим — подтверждение записи».

Без сети и без реального QThread: ``FillWorker`` подменяется стабом (start() только
ставит флаг, run() не вызывается), а ``QMessageBox.exec``/``clickedButton`` —
monkeypatch-ем (диалог не блокирует, «нажатую» кнопку выбираем в тесте).
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtWidgets import QMessageBox

import gui.main_window as mw
from gui.control_panel import METHOD_AUTO, METHOD_INTERACTIVE, MODE_FILL
from gui.main_window import MainWindow


def _make_stub_config() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        defaults={"task_description": "Общие задачи подразделения", "hours": 8},
        export={"output_dir": "./out", "filename_pattern": "workday_{date_from}_{date_to}.xlsx"},
        env=types.SimpleNamespace(webhook_code="dummy", domain="bitrix.incomsystem.ru"),
    )


class _FakeSignal:
    def connect(self, *_a, **_k):
        return None


class _FakeFillWorker:
    """Стаб FillWorker: фиксирует kwargs и факт start() без запуска потока."""

    instances: list = []

    def __init__(self, config, *, dry_run, interaction, today, description, hours, **_k):
        self.dry_run = dry_run
        self.interaction = interaction
        self.description = description
        self.hours = hours
        self.started = False
        self.result_ready = _FakeSignal()
        self.finished_code = _FakeSignal()
        self.confirm_requested = _FakeSignal()
        _FakeFillWorker.instances.append(self)

    def start(self):
        self.started = True

    def isRunning(self):
        return False


def _patch_messagebox(monkeypatch, *, confirm: bool) -> None:
    """Подменить QMessageBox.exec/clickedButton: exec не блокирует; clickedButton
    возвращает accept-кнопку (confirm=True) либо reject/None (confirm=False)."""

    def fake_exec(self):
        return 0

    def fake_clicked(self):
        accept = None
        reject = None
        for b in self.buttons():
            role = self.buttonRole(b)
            if role == QMessageBox.ButtonRole.AcceptRole:
                accept = b
            elif role == QMessageBox.ButtonRole.RejectRole:
                reject = b
        return accept if confirm else reject

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "clickedButton", fake_clicked)


@pytest.fixture(autouse=True)
def _reset_instances():
    _FakeFillWorker.instances = []
    yield
    _FakeFillWorker.instances = []


class TestFillSafetyGate:
    def _window(self, monkeypatch, method: str) -> MainWindow:
        cfg = _make_stub_config()
        window = MainWindow(cfg)
        window._control._apply_mode(MODE_FILL)
        combo = window._control._method_combo
        combo.setCurrentIndex(combo.findData(method))
        monkeypatch.setattr(mw, "FillWorker", _FakeFillWorker)
        return window

    @pytest.mark.parametrize("method", [METHOD_AUTO, METHOD_INTERACTIVE])
    def test_cancel_does_not_start_worker(self, qapp, monkeypatch, method):
        """«Отмена» на гейте ⇒ FillWorker НЕ создаётся/не стартует (оба метода)."""
        window = self._window(monkeypatch, method)
        _patch_messagebox(monkeypatch, confirm=False)

        window._start_fill()

        assert _FakeFillWorker.instances == []
        assert window._fill_worker is None
        assert window._busy is False

    @pytest.mark.parametrize("method", [METHOD_AUTO, METHOD_INTERACTIVE])
    def test_confirm_starts_worker_live(self, qapp, monkeypatch, method):
        """«Подтвердить запись» ⇒ стартует FillWorker с dry_run=False (оба метода)."""
        window = self._window(monkeypatch, method)
        _patch_messagebox(monkeypatch, confirm=True)

        window._start_fill()

        assert len(_FakeFillWorker.instances) == 1
        worker = _FakeFillWorker.instances[0]
        assert worker.started is True
        assert worker.dry_run is False
        assert worker.interaction is (method == METHOD_INTERACTIVE)
        # Значения приходят из «Значений по-умолчанию» (self._default_*).
        assert worker.description == "Общие задачи подразделения"
        assert worker.hours == 8.0

    def test_interactive_also_passes_the_gate(self, qapp, monkeypatch):
        """Регрессия BLOCKER: метод «Индивидуально» ТОЖЕ проходит гейт до старта.

        При «Отмена» воркер не стартует даже в интерактиве (раньше interaction=True
        стартовал сразу, а боевой «ремонт» закрытия дел шёл без подтверждения)."""
        window = self._window(monkeypatch, METHOD_INTERACTIVE)
        _patch_messagebox(monkeypatch, confirm=False)

        window._start_fill()

        assert _FakeFillWorker.instances == []

"""Тесты gui/worker.py::FillWorker — фейковый input, диалог «ко всем» (фаза 6_05).

Без сети, без реального QThread.start()/run(): тестируем _fake_input/
_request_confirmation напрямую, подменяя блокирующий механизм ожидания ответа
(``provide_confirmation``) синхронным вызовом из того же потока — _request_confirmation
блокируется на QWaitCondition.wait() до ``self._response`` не станет не-None, поэтому
в тесте мы заранее кладём ответ в FillWorker._response через provide_confirmation()
ДО вызова _fake_input (тест синхронный, без реальной параллельности).

Требует offscreen QApplication, т.к. FillWorker — QThread (QObject иерархия).
"""

from __future__ import annotations

import threading
import time

import pytest

from gui.worker import FillWorker


def _make_worker(qapp, *, interaction=True, dry_run=False, today=None):
    from datetime import date

    import types

    cfg = types.SimpleNamespace()  # FillWorker.__init__ не обращается к содержимому cfg
    worker = FillWorker(
        cfg,
        dry_run=dry_run,
        interaction=interaction,
        today=today or date(2026, 6, 25),
        description="Общие задачи подразделения",
        hours=8.0,
    )
    return worker


# ---------------------------------------------------------------------------
# Распознавание промпта по подстроке.
# ---------------------------------------------------------------------------


class TestFakeInputPromptRecognition:
    def test_hours_prompt_returns_current_hours_without_dialog(self, qapp):
        """Промпт «Количество часов» возвращает текущие часы строкой, без сигнала."""
        worker = _make_worker(qapp)
        worker._current_hours = 6.5
        received = []
        worker.confirm_requested.connect(lambda info: received.append(info))

        result = worker._fake_input("  Количество часов [Enter=8]: ")

        assert result == "6.5"
        assert received == []  # диалог НЕ вызывается на промпте часов

    def test_unknown_prompt_returns_empty_string(self, qapp):
        """Промпт без «Описание»/«Количество часов»/«ко всем» — пустая строка (Enter)."""
        worker = _make_worker(qapp)
        assert worker._fake_input("какой-то другой текст") == ""

    def test_apply_to_all_prompt_from_run_fill_returns_n(self, qapp):
        """run_fill спрашивает «Применить ко всем? [y/N]» — воркер всегда отвечает 'N'

        (логика «ко всем» реализована на уровне воркера через _applied_all, поэтому
        run_fill не должен сам зафиксировать свой собственный applied_to_all).
        """
        worker = _make_worker(qapp)
        result = worker._fake_input("  Применить эти значения ко всем оставшимся дням? [y/N]: ")
        assert result == "N"

    def test_description_prompt_triggers_confirmation_when_no_cache(self, qapp):
        """Промпт «Описание» без кэша «ко всем» запрашивает подтверждение у main thread."""
        worker = _make_worker(qapp)

        # Подложить ответ заранее (синхронный тест, без реального параллелизма):
        # _request_confirmation блокируется на QWaitCondition, ждущей self._response.
        # Запустим ответ в отдельном потоке с небольшой задержкой, чтобы имитировать
        # асинхронный main thread, отвечающий через provide_confirmation().
        def _respond_later():
            time.sleep(0.05)
            worker.provide_confirmation(
                {"choice": "ok", "description": "Моя задача", "hours": 5.0}
            )

        received = []
        worker.confirm_requested.connect(lambda info: received.append(info))
        t = threading.Thread(target=_respond_later, daemon=True)
        t.start()

        result = worker._fake_input("  Описание задачи [Enter=дефолт]: ")
        t.join(timeout=2)

        assert result == "Моя задача"
        assert len(received) == 1
        assert received[0]["default_description"] == "Общие задачи подразделения"
        assert received[0]["default_hours"] == 8.0


# ---------------------------------------------------------------------------
# Маппинг ответов диалога choice → возвращаемое значение _request_confirmation.
# ---------------------------------------------------------------------------


class TestRequestConfirmationChoiceMapping:
    def _ask(self, worker, response: dict):
        """Запустить _request_confirmation и сразу ответить из другого потока."""
        def _respond():
            time.sleep(0.02)
            worker.provide_confirmation(response)

        t = threading.Thread(target=_respond, daemon=True)
        t.start()
        result = worker._request_confirmation()
        t.join(timeout=2)
        return result

    def test_choice_ok_returns_description_and_updates_current_values(self, qapp):
        worker = _make_worker(qapp)
        result = self._ask(worker, {"choice": "ok", "description": "Задача X", "hours": 3.0})
        assert result == "Задача X"
        assert worker._current_description == "Задача X"
        assert worker._current_hours == 3.0
        assert worker._applied_all is None

    def test_choice_skip_returns_skip_literal(self, qapp):
        worker = _make_worker(qapp)
        result = self._ask(worker, {"choice": "skip"})
        assert result == "skip"

    def test_choice_abort_returns_abort_literal(self, qapp):
        worker = _make_worker(qapp)
        result = self._ask(worker, {"choice": "abort"})
        assert result == "abort"

    def test_choice_all_returns_description_and_sets_cache(self, qapp):
        worker = _make_worker(qapp)
        result = self._ask(worker, {"choice": "all", "description": "Массовая", "hours": 7.5})
        assert result == "Массовая"
        assert worker._applied_all == {"description": "Массовая", "hours": 7.5}

    def test_missing_description_falls_back_to_default(self, qapp):
        worker = _make_worker(qapp)
        result = self._ask(worker, {"choice": "ok"})
        assert result == worker._default_description

    def test_invalid_hours_falls_back_to_default_hours(self, qapp):
        worker = _make_worker(qapp)
        self._ask(worker, {"choice": "ok", "description": "X", "hours": "не число"})
        assert worker._current_hours == worker._default_hours


# ---------------------------------------------------------------------------
# Кэш «применить ко всем»: после choice="all" последующие дни НЕ зовут диалог.
# ---------------------------------------------------------------------------


class TestAppliedToAllCache:
    def test_after_apply_all_subsequent_description_prompt_skips_dialog(self, qapp):
        """После choice='all' второй вызов _fake_input('Описание...') не эмитит confirm_requested."""
        worker = _make_worker(qapp)
        worker._applied_all = {"description": "Кэш-задача", "hours": 4.0}

        received = []
        worker.confirm_requested.connect(lambda info: received.append(info))

        result = worker._fake_input("  Описание задачи [Enter=дефолт]: ")

        assert result == "Кэш-задача"
        assert received == []  # диалог НЕ вызывается — значения взяты из кэша

    def test_applied_all_also_updates_current_hours_for_next_prompt(self, qapp):
        """После применения кэша «ко всем» промпт часов того же дня отдаёт закэшированное."""
        worker = _make_worker(qapp)
        worker._applied_all = {"description": "Кэш-задача", "hours": 4.0}

        worker._fake_input("  Описание задачи [Enter=дефолт]: ")
        hours_result = worker._fake_input("  Количество часов [Enter=8]: ")

        assert hours_result == "4.0"

    def test_cache_persists_across_multiple_days(self, qapp):
        """Кэш «ко всем» не очищается между несколькими последующими днями."""
        worker = _make_worker(qapp)
        worker._applied_all = {"description": "Для всех", "hours": 6.0}

        received = []
        worker.confirm_requested.connect(lambda info: received.append(info))

        for _ in range(3):
            desc = worker._fake_input("  Описание задачи [Enter=дефолт]: ")
            hrs = worker._fake_input("  Количество часов [Enter=8]: ")
            assert desc == "Для всех"
            assert hrs == "6.0"

        assert received == []


# ---------------------------------------------------------------------------
# provide_confirmation — формат ответа.
# ---------------------------------------------------------------------------


class TestDefaultsOverride:
    """SHOULD-FIX #2: значения SettingsDialog применяются к cfg.defaults и в auto.

    run_fill (src/fill.py, не меняем) читает описание/часы из cfg.defaults, а не из
    аргументов. FillWorker временно переопределяет cfg.defaults на время прогона и
    восстанавливает исходные значения в finally.
    """

    def _worker_with_defaults(self, qapp, defaults):
        import types
        from datetime import date

        cfg = types.SimpleNamespace(defaults=defaults)
        return FillWorker(
            cfg,
            dry_run=False,
            interaction=False,
            today=date(2026, 6, 25),
            description="Из диалога",
            hours=3.5,
        )

    def test_apply_override_substitutes_and_snapshot_restores(self, qapp):
        defaults = {"task_description": "Из config", "hours": 8, "contract_tech_id": "2"}
        worker = self._worker_with_defaults(qapp, defaults)

        saved = worker._apply_defaults_override()
        # Во время прогона run_fill увидит значения диалога.
        assert defaults["task_description"] == "Из диалога"
        assert defaults["hours"] == 3.5
        # Прочие ключи defaults не тронуты.
        assert defaults["contract_tech_id"] == "2"

        worker._restore_defaults(saved)
        # После восстановления — исходные значения config.
        assert defaults["task_description"] == "Из config"
        assert defaults["hours"] == 8
        assert defaults["contract_tech_id"] == "2"

    def test_override_noop_when_defaults_not_dict(self, qapp):
        worker = self._worker_with_defaults(qapp, None)
        assert worker._apply_defaults_override() is None
        # restore не падает на None-снимке.
        worker._restore_defaults(None)


class TestProvideConfirmation:
    def test_default_choice_ok_when_response_falsy(self, qapp):
        """provide_confirmation(None/{}) трактуется как choice='ok' (см. docstring)."""
        worker = _make_worker(qapp)
        worker._waiting = True  # эмулируем фазу ожидания подтверждения (иначе вызов — no-op)
        worker.provide_confirmation(None)
        assert worker._response == {"choice": "ok"}

    def test_response_stored_as_dict_copy(self, qapp):
        worker = _make_worker(qapp)
        worker._waiting = True  # эмулируем фазу ожидания подтверждения (иначе вызов — no-op)
        original = {"choice": "skip"}
        worker.provide_confirmation(original)
        assert worker._response == {"choice": "skip"}
        assert worker._response is not original

    def test_provide_confirmation_noop_when_not_waiting(self, qapp):
        """Идемпотентность: вне фазы ожидания provide_confirmation ничего не пишет."""
        worker = _make_worker(qapp)
        assert worker._waiting is False
        worker.provide_confirmation({"choice": "abort"})
        assert worker._response is None

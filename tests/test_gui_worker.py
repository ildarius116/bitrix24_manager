"""Тесты gui/worker.py: фейковый input метода «Индивидуально» + classify_day_states.

Без сети, без реального QThread.start()/run(): тестируем ``_fake_input`` синхронно.
Метод «Индивидуально» (фаза 8_01) решает судьбу дня по левой панели
(``selected_days`` / ``per_day_values``) и дате из ``_last_day_date`` — без per-day
диалога/блокировки потока.

Требует offscreen QApplication, т.к. FillWorker — QThread (QObject иерархия).
"""

from __future__ import annotations

import types
from datetime import date

from gui.worker import FillWorker, classify_day_states


def _make_worker(
    qapp,
    *,
    interaction=True,
    dry_run=False,
    today=None,
    selected_days=None,
    per_day_values=None,
):
    cfg = types.SimpleNamespace()  # FillWorker.__init__ не обращается к содержимому cfg
    return FillWorker(
        cfg,
        dry_run=dry_run,
        interaction=interaction,
        today=today or date(2026, 6, 25),
        description="Общие задачи подразделения",
        hours=8.0,
        selected_days=selected_days,
        per_day_values=per_day_values,
    )


# ---------------------------------------------------------------------------
# Распознавание промпта по подстроке.
# ---------------------------------------------------------------------------


class TestFakeInputPromptRecognition:
    def test_hours_prompt_returns_current_hours(self, qapp):
        worker = _make_worker(qapp)
        worker._current_hours = 6.5
        assert worker._fake_input("  Количество часов [Enter=8]: ") == "6.5"

    def test_unknown_prompt_returns_empty_string(self, qapp):
        worker = _make_worker(qapp)
        assert worker._fake_input("какой-то другой текст") == ""

    def test_apply_to_all_prompt_returns_n(self, qapp):
        """run_fill спрашивает «Применить ко всем? [y/N]» — воркер всегда отвечает 'N'."""
        worker = _make_worker(qapp)
        result = worker._fake_input("  Применить эти значения ко всем оставшимся дням? [y/N]: ")
        assert result == "N"


# ---------------------------------------------------------------------------
# Метод «Индивидуально»: описание решается по selected_days / per_day_values.
# ---------------------------------------------------------------------------


class TestIndividualDescriptionResolution:
    def test_no_selection_falls_back_to_default_description(self, qapp):
        """selected_days=None (метод «По-умолчанию») → дефолтное описание для любого дня."""
        worker = _make_worker(qapp, selected_days=None)
        worker._last_day_date = "25.06.2026"
        assert worker._fake_input("  Описание задачи [Enter=дефолт]: ") == (
            "Общие задачи подразделения"
        )

    def test_unselected_day_returns_skip(self, qapp):
        """День НЕ в selected_days → «skip» (collect_values пропустит его)."""
        worker = _make_worker(
            qapp,
            selected_days={date(2026, 6, 24)},
            per_day_values={date(2026, 6, 24): ("desc", 5.0)},
        )
        worker._last_day_date = "25.06.2026"  # другой день
        assert worker._fake_input("  Описание задачи [Enter=дефолт]: ") == "skip"

    def test_selected_day_returns_inline_desc_and_sets_hours(self, qapp):
        """Отмеченный день → его инлайн-описание и инлайн-часы (для промпта часов)."""
        d = date(2026, 6, 25)
        worker = _make_worker(
            qapp,
            selected_days={d},
            per_day_values={d: ("Моя задача", 3.5)},
        )
        worker._last_day_date = "25.06.2026"

        desc = worker._fake_input("  Описание задачи [Enter=дефолт]: ")
        hours = worker._fake_input("  Количество часов [Enter=8]: ")

        assert desc == "Моя задача"
        assert hours == "3.5"

    def test_selected_but_missing_values_uses_defaults(self, qapp):
        """Отмечен, но нет записи в per_day_values → дефолтные описание/часы."""
        d = date(2026, 6, 25)
        worker = _make_worker(qapp, selected_days={d}, per_day_values={})
        worker._last_day_date = "25.06.2026"
        assert worker._fake_input("  Описание задачи [Enter=дефолт]: ") == (
            "Общие задачи подразделения"
        )
        assert worker._fake_input("  Количество часов [Enter=8]: ") == "8.0"

    def test_unparsable_day_date_returns_skip(self, qapp):
        """Если дата дня не распознана (_last_day_date пуст/битый) → «skip» (безопасно)."""
        worker = _make_worker(qapp, selected_days={date(2026, 6, 25)})
        worker._last_day_date = ""
        assert worker._fake_input("  Описание задачи [Enter=дефолт]: ") == "skip"

    def test_empty_selection_skips_every_day(self, qapp):
        """selected_days=set() (все сняты) → каждый день «skip»."""
        worker = _make_worker(qapp, selected_days=set())
        worker._last_day_date = "25.06.2026"
        assert worker._fake_input("  Описание задачи [Enter=дефолт]: ") == "skip"


class TestParseLastDayDate:
    def test_valid(self, qapp):
        worker = _make_worker(qapp)
        worker._last_day_date = "01.07.2026"
        assert worker._parse_last_day_date() == date(2026, 7, 1)

    def test_empty_returns_none(self, qapp):
        worker = _make_worker(qapp)
        worker._last_day_date = ""
        assert worker._parse_last_day_date() is None

    def test_garbage_returns_none(self, qapp):
        worker = _make_worker(qapp)
        worker._last_day_date = "не дата"
        assert worker._parse_last_day_date() is None


# ---------------------------------------------------------------------------
# classify_day_states — чистая функция (Часть A), без сети.
# ---------------------------------------------------------------------------


def _cfg(edit_window_days=4, work_ids=(351,)):
    return types.SimpleNamespace(
        edit_window_days=edit_window_days,
        day_type_work_ids=list(work_ids),
    )


def _day(d, *, day_type_id=351, works_ids=()):
    return types.SimpleNamespace(
        id=1,
        title="день",
        date=d,
        day_type_id=day_type_id,
        works_ids=list(works_ids),
    )


class TestClassifyDayStates:
    def test_window_covers_edit_window_plus_today(self, qapp):
        today = date(2026, 6, 25)
        states = classify_day_states([], _cfg(edit_window_days=4), today)
        assert len(states) == 5  # today .. today-4
        assert set(states) == {
            date(2026, 6, d).isoformat() for d in (21, 22, 23, 24, 25)
        }

    def test_missing_day_is_not_fillable(self, qapp):
        today = date(2026, 6, 25)
        states = classify_day_states([], _cfg(), today)
        st = states[today.isoformat()]
        assert st["fillable"] is False
        assert "нет записи" in st["reason"]

    def test_non_work_type_not_fillable(self, qapp):
        today = date(2026, 6, 25)
        days = [_day(today, day_type_id=352)]  # отпуск
        st = classify_day_states(days, _cfg(), today)[today.isoformat()]
        assert st["fillable"] is False
        assert st["reason"] == "не рабочий день"

    def test_already_filled_not_fillable(self, qapp):
        today = date(2026, 6, 25)
        days = [_day(today, works_ids=[10, 11])]
        st = classify_day_states(days, _cfg(), today)[today.isoformat()]
        assert st["fillable"] is False
        assert st["reason"] == "уже заполнен (2)"

    def test_empty_work_day_is_fillable(self, qapp):
        today = date(2026, 6, 25)
        days = [_day(today, works_ids=[])]
        st = classify_day_states(days, _cfg(), today)[today.isoformat()]
        assert st["fillable"] is True
        assert st["reason"] == ""


# ---------------------------------------------------------------------------
# SHOULD-FIX: значения инлайн-полей «Редактирование» применяются к cfg.defaults и в auto.
# ---------------------------------------------------------------------------


class TestDefaultsOverride:
    """run_fill (src/fill.py, не меняем) читает описание/часы из cfg.defaults, а не из
    аргументов. FillWorker временно переопределяет cfg.defaults на время прогона и
    восстанавливает исходные значения в finally.
    """

    def _worker_with_defaults(self, qapp, defaults):
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
        assert defaults["task_description"] == "Из диалога"
        assert defaults["hours"] == 3.5
        assert defaults["contract_tech_id"] == "2"

        worker._restore_defaults(saved)
        assert defaults["task_description"] == "Из config"
        assert defaults["hours"] == 8
        assert defaults["contract_tech_id"] == "2"

    def test_override_noop_when_defaults_not_dict(self, qapp):
        worker = self._worker_with_defaults(qapp, None)
        assert worker._apply_defaults_override() is None
        worker._restore_defaults(None)


class TestProvideConfirmationNoop:
    def test_provide_confirmation_is_noop(self, qapp):
        """provide_confirmation оставлен как no-op (совместимость с closeEvent)."""
        worker = _make_worker(qapp)
        # Не должно бросать и ничего не менять.
        assert worker.provide_confirmation({"choice": "abort"}) is None

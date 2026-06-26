"""Юнит-тесты write-пайплайна src/fill.py (Фаза 4).

Все тесты БЕЗ сети: B24 и input полностью замоканы.
Фиксированная «сегодня» = 2026-06-25.

Покрываемые единицы:
- _parse_hours               — разбор строки часов в float
- build_payload              — формирование fields для crm.item.add 1218 (чистая функция)
- collect_values             — сбор значений (no-interaction / interactive / applied_to_all)
- _reread_guard              — идемпотентность (окно + пустота, мок B24.item_get)
- create_log (dry_run=True)  — plan_only вызов, боевой add НЕ зовётся
- create_log (dry_run=False) — боевой add, статусы filled / error
- verify_log                 — верификация привязки и полей по двум item_get
"""

from __future__ import annotations

import types
from collections import deque
from datetime import date
from typing import Any, Dict, List, Optional

import pytest

from src.b24 import B24Error
from src.fill import (
    ABORT,
    SKIP,
    AbortFill,
    Values,
    _Abort,
    _Skip,
    _parse_hours,
    _reread_guard,
    build_payload,
    collect_values,
    create_log,
    verify_log,
)
from src.workday import WorkdayDay

# ---------------------------------------------------------------------------
# Константы / фикстуры
# ---------------------------------------------------------------------------

TODAY = date(2026, 6, 25)
# today − 4 дня = 2026-06-21 (последняя граница окна — проходит)
DAY_IN_WINDOW = TODAY  # самый свежий
DAY_BOUNDARY = date(2026, 6, 21)  # today − 4 (граница — ДОЛЖЕН проходить)
DAY_TOO_OLD = date(2026, 6, 20)   # today − 5 (вне окна — НЕ должен проходить)


def _make_cfg() -> types.SimpleNamespace:
    """Лёгкий стаб Config — берём реальные коды из config.yaml."""
    return types.SimpleNamespace(
        # entity ids
        workday_type_id=1208,
        timelog_type_id=1218,
        timelog_category_id=63,
        # коды полей дня 1208
        field_workday_date="ufCrm46_1742342657",
        field_workday_works="ufCrm46_1742997115",
        field_workday_employee="ufCrm46_1742341577",
        # коды полей учёта 1218
        field_log_parent="parentId1208",
        field_log_contract="ufCrm48_1742996936",
        field_log_contract_tech="ufCrm48_1754894889",
        field_log_description="ufCrm48_1744239302",
        field_log_hours="ufCrm48_1742996959",
        field_log_result="ufCrm48_1743029170",
        # значения
        contract_general_tasks="T512_2",
        contract_tech_id="2",
        edit_window_days=4,
        # дефолты
        defaults={"task_description": "Общие задачи подразделения", "hours": 8},
    )


def _make_day(
    day_id: int = 100,
    d: Optional[date] = None,
    works_ids: Optional[List[int]] = None,
    title: str = "Сотрудник | 25.06.2026",
) -> WorkdayDay:
    """Вспомогательный конструктор WorkdayDay для тестов fill."""
    return WorkdayDay(
        id=day_id,
        date=d if d is not None else TODAY,
        title=title,
        employee="1244",
        works_ids=works_ids if works_ids is not None else [],
        raw={},
        logs=[],
    )


# ---------------------------------------------------------------------------
# Фейковый B24 с записью вызовов
# ---------------------------------------------------------------------------

class FakeB24:
    """Мок B24 с управляемыми ответами item_get / item_add.

    Использование:
        b24 = FakeB24(
            item_get_responses=[dict(...), dict(...)],   # очередь ответов
            item_add_response={"id": 999},               # ответ add (или B24Error)
        )
    """

    def __init__(
        self,
        item_get_responses: Optional[List[Any]] = None,
        item_add_response: Any = None,
    ) -> None:
        # очередь ответов item_get; каждый элемент — dict или B24Error (исключение) или None
        self._get_queue: deque = deque(item_get_responses or [])
        self._add_response = item_add_response

        # журнал вызовов
        self.item_get_calls: List[Dict[str, Any]] = []
        self.item_add_calls: List[Dict[str, Any]] = []

    def item_get(self, entity_type_id: int, item_id: int, *, select=None) -> Optional[Dict]:
        self.item_get_calls.append(
            {"entity_type_id": entity_type_id, "item_id": item_id, "select": select}
        )
        if not self._get_queue:
            return None
        resp = self._get_queue.popleft()
        if isinstance(resp, Exception):
            raise resp
        return resp

    def item_add(self, entity_type_id: int, fields: Dict[str, Any], *, plan_only: bool = False) -> Dict:
        self.item_add_calls.append(
            {"entity_type_id": entity_type_id, "fields": fields, "plan_only": plan_only}
        )
        resp = self._add_response
        if isinstance(resp, Exception):
            raise resp
        if plan_only:
            # Воспроизводим поведение реального B24.item_add при plan_only=True
            return {
                "plan_only": True,
                "method": "crm.item.add",
                "params": {"entityTypeId": entity_type_id, "fields": fields},
                "note": "Запись НЕ выполнена (plan_only).",
            }
        if resp is None:
            return {}
        return resp


# ---------------------------------------------------------------------------
# Тесты _parse_hours
# ---------------------------------------------------------------------------

class TestParseHours:
    """FR: разбор строки часов в положительный float."""

    def test_integer_string_returns_float(self):
        """'8' → 8.0 (обычный случай)."""
        assert _parse_hours("8") == 8.0

    def test_comma_decimal(self):
        """'8,5' (русский формат) → 8.5."""
        assert _parse_hours("8,5") == 8.5

    def test_dot_decimal(self):
        """'7.5' (точка) → 7.5."""
        assert _parse_hours("7.5") == 7.5

    def test_zero_returns_none(self):
        """'0' → None (нулевые часы недопустимы)."""
        assert _parse_hours("0") is None

    def test_negative_returns_none(self):
        """'-1' → None (отрицательные недопустимы)."""
        assert _parse_hours("-1") is None

    def test_empty_string_returns_none(self):
        """'' → None."""
        assert _parse_hours("") is None

    def test_none_input_returns_none(self):
        """None → None (через (raw or '') защита)."""
        assert _parse_hours(None) is None  # type: ignore[arg-type]

    def test_garbage_returns_none(self):
        """'abc' → None."""
        assert _parse_hours("abc") is None

    def test_whitespace_returns_none(self):
        """'   ' → None (после strip — пусто)."""
        assert _parse_hours("   ") is None

    def test_very_small_positive(self):
        """'0.5' → 0.5 (положительное, меньше 1)."""
        assert _parse_hours("0.5") == 0.5

    def test_large_hours(self):
        """'24' → 24.0 (технически валидно)."""
        assert _parse_hours("24") == 24.0


# ---------------------------------------------------------------------------
# Тесты build_payload (чистая функция, FR требование)
# ---------------------------------------------------------------------------

class TestBuildPayload:
    """Payload содержит правильные ключи из cfg, без хардкода."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day(day_id=42)

    def test_parent_id_uses_day_id(self):
        """parentId1208 = day.id (по ключу cfg.field_log_parent)."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert p[self.cfg.field_log_parent] == 42

    def test_category_id_from_cfg(self):
        """categoryId = cfg.timelog_category_id = 63."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert p["categoryId"] == self.cfg.timelog_category_id

    def test_contract_from_cfg(self):
        """Поле договора = cfg.contract_general_tasks = 'T512_2'."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert p[self.cfg.field_log_contract] == self.cfg.contract_general_tasks

    def test_contract_tech_from_cfg(self):
        """Поле тех-кода договора = cfg.contract_tech_id = '2'."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert p[self.cfg.field_log_contract_tech] == self.cfg.contract_tech_id

    def test_description_stored(self):
        """Описание задачи попадает в поле cfg.field_log_description."""
        p = build_payload(self.day, "Моя задача", 8.0, self.cfg)
        assert p[self.cfg.field_log_description] == "Моя задача"

    def test_hours_stored(self):
        """Часы попадают в поле cfg.field_log_hours."""
        p = build_payload(self.day, "Работа", 6.5, self.cfg)
        assert p[self.cfg.field_log_hours] == 6.5

    def test_result_field_absent(self):
        """Поле «Итог» (cfg.field_log_result) НЕ должно быть в payload."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert self.cfg.field_log_result not in p

    def test_workday_works_field_absent(self):
        """Поле «Работы за день» дня (cfg.field_workday_works) НЕ в payload — ставится ядром."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        assert self.cfg.field_workday_works not in p

    def test_keys_use_cfg_not_hardcode(self):
        """При изменении кода поля в cfg payload отражает новый код."""
        cfg2 = _make_cfg()
        cfg2.field_log_description = "ufCrm48_CUSTOM_DESC"
        p = build_payload(self.day, "Текст", 8.0, cfg2)
        assert "ufCrm48_CUSTOM_DESC" in p
        # старый код отсутствует
        assert "ufCrm48_1744239302" not in p

    def test_payload_exact_key_count(self):
        """Payload содержит ровно 6 ключей (без лишних)."""
        p = build_payload(self.day, "Работа", 8.0, self.cfg)
        # parentId1208, categoryId, contract, contract_tech, description, hours
        assert len(p) == 6


# ---------------------------------------------------------------------------
# Тесты collect_values (no-interaction)
# ---------------------------------------------------------------------------

class TestCollectValuesNoInteraction:
    """interaction=False → дефолты из cfg, без вызова input."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day()

    def test_returns_values_with_defaults(self):
        """Возвращает Values с дефолтными описанием и часами из cfg."""
        result = collect_values(self.day, self.cfg, interaction=False)
        assert isinstance(result, Values)
        assert result.description == "Общие задачи подразделения"
        assert result.hours == 8.0

    def test_raises_if_default_hours_zero(self):
        """Если дефолтные часы <= 0 — ValueError (ошибка конфигурации)."""
        self.cfg.defaults = {"task_description": "Работа", "hours": 0}
        with pytest.raises(ValueError, match="config"):
            collect_values(self.day, self.cfg, interaction=False)

    def test_raises_if_default_hours_negative(self):
        """Отрицательные дефолтные часы тоже вызывают ValueError."""
        self.cfg.defaults = {"task_description": "Работа", "hours": -5}
        with pytest.raises(ValueError):
            collect_values(self.day, self.cfg, interaction=False)

    def test_raises_if_default_hours_garbage(self):
        """Строковый мусор в дефолте часов → ValueError."""
        self.cfg.defaults = {"task_description": "Работа", "hours": "abc"}
        with pytest.raises(ValueError):
            collect_values(self.day, self.cfg, interaction=False)

    def test_input_not_called(self, monkeypatch):
        """В no-interaction режиме input НЕ вызывается."""
        def _no_input(prompt=""):
            raise AssertionError("input() не должна вызываться в no-interaction режиме")
        monkeypatch.setattr("builtins.input", _no_input)
        result = collect_values(self.day, self.cfg, interaction=False)
        assert isinstance(result, Values)


# ---------------------------------------------------------------------------
# Тесты collect_values (applied_to_all задан)
# ---------------------------------------------------------------------------

class TestCollectValuesAppliedToAll:
    """applied_to_all задан → возвращает его без вызова input."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day()
        self.preset = Values(description="Массовая задача", hours=7.5)

    def test_returns_applied_to_all_unchanged(self):
        """Если applied_to_all задан — возвращается он без изменений."""
        result = collect_values(
            self.day, self.cfg, interaction=True, applied_to_all=self.preset
        )
        assert result is self.preset

    def test_input_not_called_when_applied_to_all(self, monkeypatch):
        """input НЕ вызывается, если applied_to_all задан."""
        def _no_input(prompt=""):
            raise AssertionError("input() не должна вызываться при applied_to_all")
        monkeypatch.setattr("builtins.input", _no_input)
        result = collect_values(
            self.day, self.cfg, interaction=True, applied_to_all=self.preset
        )
        assert isinstance(result, Values)


# ---------------------------------------------------------------------------
# Тесты collect_values (interaction=True) — monkeypatch input
# ---------------------------------------------------------------------------

def _make_input_queue(*answers: str):
    """Вернуть функцию, отдающую ответы из очереди; AssertionError если очередь пуста."""
    q = deque(answers)
    def _input(prompt=""):
        if not q:
            raise AssertionError(f"Очередь ответов input исчерпана (prompt={prompt!r})")
        return q.popleft()
    return _input


class TestCollectValuesInteractive:
    """interaction=True: тесты интерактивного сбора значений."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day()

    def test_enter_enter_returns_defaults(self, monkeypatch):
        """Enter+Enter → Values с дефолтными описанием и часами."""
        monkeypatch.setattr("builtins.input", _make_input_queue("", ""))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.description == "Общие задачи подразделения"
        assert result.hours == 8.0

    def test_custom_description_and_hours(self, monkeypatch):
        """Ввод описания + часов → Values с введёнными значениями."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Моя задача", "10"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.description == "Моя задача"
        assert result.hours == 10.0

    def test_enter_then_custom_hours(self, monkeypatch):
        """Enter (описание=дефолт) + '6,5' → часы переопределены."""
        monkeypatch.setattr("builtins.input", _make_input_queue("", "6,5"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.description == "Общие задачи подразделения"
        assert result.hours == 6.5

    def test_skip_at_description(self, monkeypatch):
        """'skip' на шаге описания → SKIP."""
        monkeypatch.setattr("builtins.input", _make_input_queue("skip"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert result is SKIP

    def test_skip_case_insensitive(self, monkeypatch):
        """'SKIP' (верхний регистр) → тоже SKIP."""
        monkeypatch.setattr("builtins.input", _make_input_queue("SKIP"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert result is SKIP

    def test_abort_at_description(self, monkeypatch):
        """'abort' на шаге описания → ABORT."""
        monkeypatch.setattr("builtins.input", _make_input_queue("abort"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert result is ABORT

    def test_abort_at_hours(self, monkeypatch):
        """'abort' на шаге часов → ABORT."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Работа", "abort"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert result is ABORT

    def test_skip_at_hours(self, monkeypatch):
        """'skip' на шаге часов → SKIP."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Работа", "skip"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert result is SKIP

    def test_all_returns_defaults_as_values(self, monkeypatch):
        """'all' → Values с дефолтами (чтобы оркестратор запомнил applied_to_all)."""
        monkeypatch.setattr("builtins.input", _make_input_queue("all"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.description == "Общие задачи подразделения"
        assert result.hours == 8.0

    def test_invalid_hours_then_valid(self, monkeypatch):
        """Невалидные часы → переспрос; затем валидные принимаются."""
        # Порядок: описание, первые часы (мусор), вторые часы (валидные)
        monkeypatch.setattr("builtins.input", _make_input_queue("Задача", "abc", "4"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.hours == 4.0

    def test_zero_hours_then_valid(self, monkeypatch):
        """Нулевые часы невалидны → переспрос; затем валидные."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Задача", "0", "3"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.hours == 3.0

    def test_negative_hours_then_valid(self, monkeypatch):
        """-1 невалидно → переспрос."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Задача", "-1", "5"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert result.hours == 5.0

    def test_returns_values_type(self, monkeypatch):
        """Результат при нормальном вводе — всегда тип Values, не SKIP/ABORT."""
        monkeypatch.setattr("builtins.input", _make_input_queue("Работа", "8"))
        result = collect_values(self.day, self.cfg, interaction=True)
        assert isinstance(result, Values)
        assert not isinstance(result, (_Skip, _Abort))


# ---------------------------------------------------------------------------
# Тесты _reread_guard
# ---------------------------------------------------------------------------

class TestRereadeGuard:
    """Гард идемпотентности: окно + пустота works_ids."""

    @pytest.fixture(autouse=True)
    def _freeze_today(self, monkeypatch):
        """Зафиксировать «сегодня» для src.fill.today_moscow = 2026-06-25."""
        monkeypatch.setattr("src.fill.today_moscow", lambda: date(2026, 6, 25))

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day(day_id=10)

    def _fresh_item(self, d: Optional[date], works_ids: List[int] = None) -> Dict[str, Any]:
        """Сформировать словарь «свежего» дня как вернул бы item_get."""
        item: Dict[str, Any] = {"id": 10}
        if d is not None:
            item[self.cfg.field_workday_date] = d.isoformat()
        if works_ids is not None:
            item[self.cfg.field_workday_works] = [str(w) for w in works_ids]
        return item

    def test_in_window_empty_works_returns_none(self):
        """День в окне, works пуст → None (можно писать)."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(TODAY, [])])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert result is None

    def test_boundary_day_returns_none(self):
        """Граница окна (today−4 = 2026-06-21) → None (граница разрешена)."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(DAY_BOUNDARY, [])])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert result is None

    def test_outside_window_returns_reason(self):
        """День вне окна (today−5 = 2026-06-20) → строка-причина."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(DAY_TOO_OLD, [])])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_works_not_empty_returns_reason(self):
        """Если works непуст → строка-причина (день уже заполнен)."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(TODAY, [111, 222])])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert isinstance(result, str)
        assert "заполнен" in result or "уже" in result or len(result) > 0

    def test_item_get_raises_b24error_returns_reason(self):
        """item_get бросает B24Error → строка-причина (безопасный отказ)."""
        b24 = FakeB24(item_get_responses=[B24Error("Ошибка сети", code="NETWORK")])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_item_get_returns_none_reason(self):
        """item_get возвращает None (день не найден) → строка-причина."""
        b24 = FakeB24(item_get_responses=[None])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert isinstance(result, str)
        assert "не найден" in result or len(result) > 0

    def test_item_get_called_with_correct_entity_type(self):
        """item_get вызывается с workday_type_id и day.id."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(TODAY, [])])
        _reread_guard(b24, self.day, self.cfg, TODAY)
        assert len(b24.item_get_calls) == 1
        call = b24.item_get_calls[0]
        assert call["entity_type_id"] == self.cfg.workday_type_id
        assert call["item_id"] == self.day.id

    def test_single_works_id_returns_reason(self):
        """Один учёт в works → тоже причина (не пуст)."""
        b24 = FakeB24(item_get_responses=[self._fresh_item(TODAY, [555])])
        result = _reread_guard(b24, self.day, self.cfg, TODAY)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Тесты create_log (dry_run=True)
# ---------------------------------------------------------------------------

class TestCreateLogDryRun:
    """dry_run=True: plan_only=True вызов, боевой add НЕ вызывается."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day(day_id=20)

    def _guard_passes_response(self) -> Dict[str, Any]:
        return {
            "id": 20,
            self.cfg.field_workday_date: TODAY.isoformat(),
            self.cfg.field_workday_works: [],
        }

    def test_dry_run_returns_dry_run_status(self):
        """Гард проходит → статус 'dry-run'."""
        b24 = FakeB24(item_get_responses=[self._guard_passes_response()])
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert result["status"] == "dry-run"

    def test_dry_run_payload_present(self):
        """Ответ содержит ключ 'payload'."""
        b24 = FakeB24(item_get_responses=[self._guard_passes_response()])
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert "payload" in result

    def test_dry_run_item_add_called_with_plan_only_true(self):
        """item_add вызывается с plan_only=True."""
        b24 = FakeB24(item_get_responses=[self._guard_passes_response()])
        create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert len(b24.item_add_calls) == 1
        assert b24.item_add_calls[0]["plan_only"] is True

    def test_dry_run_no_live_add_call(self):
        """Боевой add (plan_only=False) НЕ вызывается."""
        b24 = FakeB24(item_get_responses=[self._guard_passes_response()])
        create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        live_calls = [c for c in b24.item_add_calls if not c["plan_only"]]
        assert len(live_calls) == 0

    def test_dry_run_item_add_entity_type_id(self):
        """item_add вызывается с timelog_type_id = 1218."""
        b24 = FakeB24(item_get_responses=[self._guard_passes_response()])
        create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert b24.item_add_calls[0]["entity_type_id"] == self.cfg.timelog_type_id

    def test_dry_run_guard_blocks_returns_skipped(self):
        """Если гард не пускает → статус 'skipped', item_add не зовётся."""
        # item_get вернёт день вне окна
        b24 = FakeB24(item_get_responses=[{
            "id": 20,
            self.cfg.field_workday_date: DAY_TOO_OLD.isoformat(),
            self.cfg.field_workday_works: [],
        }])
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert result["status"] == "skipped"
        assert len(b24.item_add_calls) == 0

    def test_dry_run_guard_blocks_returns_reason(self):
        """При 'skipped' присутствует ключ 'reason'."""
        b24 = FakeB24(item_get_responses=[{
            "id": 20,
            self.cfg.field_workday_date: DAY_TOO_OLD.isoformat(),
            self.cfg.field_workday_works: [],
        }])
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=True, today=TODAY)
        assert "reason" in result


# ---------------------------------------------------------------------------
# Тесты create_log (dry_run=False)
# ---------------------------------------------------------------------------

class TestCreateLogLive:
    """dry_run=False: боевой add, статусы filled / error."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day = _make_day(day_id=30)

    def _guard_passes_response(self) -> Dict[str, Any]:
        return {
            "id": 30,
            self.cfg.field_workday_date: TODAY.isoformat(),
            self.cfg.field_workday_works: [],
        }

    def test_live_filled_status(self):
        """Гард ок, item_add вернул {'id': 999} → статус 'filled'."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"id": 999},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "filled"

    def test_live_filled_new_id(self):
        """Статус 'filled' → new_id == 999."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"id": 999},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["new_id"] == 999

    def test_live_item_add_called_with_plan_only_false(self):
        """В боевом режиме item_add вызывается с plan_only=False."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"id": 999},
        )
        create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert len(b24.item_add_calls) == 1
        assert b24.item_add_calls[0]["plan_only"] is False

    def test_live_b24error_returns_error_status(self):
        """item_add бросает B24Error → статус 'error'."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response=B24Error("Ошибка записи"),
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "error"
        assert "reason" in result

    def test_live_response_without_id_returns_error(self):
        """Ответ add без id → статус 'error'."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"some_field": "value"},  # нет id
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "error"

    def test_live_payload_attached_on_filled(self):
        """На статусе 'filled' присутствует ключ 'payload'."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"id": 999},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert "payload" in result

    def test_live_guard_error_skipped(self):
        """Гард: item_get кидает B24Error → статус 'skipped', item_add не зовётся."""
        b24 = FakeB24(
            item_get_responses=[B24Error("Ошибка чтения")],
            item_add_response={"id": 999},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "skipped"
        assert len(b24.item_add_calls) == 0

    def test_live_works_not_empty_skipped(self):
        """Гард: works непуст → статус 'skipped'."""
        b24 = FakeB24(
            item_get_responses=[{
                "id": 30,
                self.cfg.field_workday_date: TODAY.isoformat(),
                self.cfg.field_workday_works: ["777"],
            }],
            item_add_response={"id": 999},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "skipped"
        assert len(b24.item_add_calls) == 0

    def test_id_field_uppercase_accepted(self):
        """Ответ с ключом 'ID' (верхний регистр) тоже принимается."""
        b24 = FakeB24(
            item_get_responses=[self._guard_passes_response()],
            item_add_response={"ID": 888},
        )
        result = create_log(b24, self.day, "Работа", 8.0, self.cfg, dry_run=False, today=TODAY)
        assert result["status"] == "filled"
        assert result["new_id"] == 888


# ---------------------------------------------------------------------------
# Тесты verify_log
# ---------------------------------------------------------------------------

class TestVerifyLog:
    """Верификация привязки и полей созданного учёта (FR-2.1.11)."""

    def setup_method(self):
        self.cfg = _make_cfg()
        self.day_id = 50
        self.log_id = 999
        self.description = "Общие задачи подразделения"
        self.hours = 8.0

    def _day_item(self, works_ids: List[int]) -> Dict[str, Any]:
        """Словарь дня с works_ids."""
        return {
            "id": self.day_id,
            self.cfg.field_workday_works: [str(w) for w in works_ids],
        }

    def _log_item(
        self,
        parent_id: int = None,
        description: str = None,
        hours: float = None,
    ) -> Dict[str, Any]:
        """Словарь учёта с заданными полями."""
        parent_id = parent_id if parent_id is not None else self.day_id
        description = description if description is not None else self.description
        hours = hours if hours is not None else self.hours
        return {
            "id": self.log_id,
            self.cfg.field_log_parent: str(parent_id),
            self.cfg.field_log_description: description,
            self.cfg.field_log_hours: str(hours),
        }

    def test_ok_when_all_match(self):
        """Все поля совпадают → ok=True, problems пуст."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is True
        assert result["problems"] == []

    def test_log_id_not_in_works_fails(self):
        """new_log_id нет в works дня → ok=False."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([111, 222]),  # log_id=999 отсутствует
            self._log_item(),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert any("999" in p or "works" in p for p in result["problems"])

    def test_wrong_parent_id_fails(self):
        """parentId1208 не совпадает с day_id → ok=False."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(parent_id=9999),  # неверный родитель
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert any("parent" in p.lower() or str(9999) in p for p in result["problems"])

    def test_wrong_description_fails(self):
        """Описание расходится → ok=False."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(description="Другая задача"),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert any("описани" in p.lower() or "расход" in p.lower() for p in result["problems"])

    def test_wrong_hours_fails(self):
        """Часы расходятся → ok=False."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(hours=4.0),  # ожидалось 8.0
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert any("час" in p.lower() or "расход" in p.lower() for p in result["problems"])

    def test_day_item_get_raises_b24error(self):
        """item_get для дня бросает B24Error → ok=False, проблема задокументирована."""
        b24 = FakeB24(item_get_responses=[
            B24Error("Ошибка чтения дня"),
            self._log_item(),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert len(result["problems"]) >= 1

    def test_log_item_get_raises_b24error(self):
        """item_get для учёта бросает B24Error → ok=False."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            B24Error("Ошибка чтения учёта"),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        assert len(result["problems"]) >= 1

    def test_result_contains_day_id_and_log_id(self):
        """Результат содержит day_id и new_log_id."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["day_id"] == self.day_id
        assert result["new_log_id"] == self.log_id

    def test_multiple_problems_accumulated(self):
        """Если расходятся несколько полей — все проблемы накапливаются."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([111]),  # log_id не в списке
            self._log_item(parent_id=9999, description="Другое", hours=1.0),
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is False
        # Ожидаем минимум 3 проблемы: works, parent, description, hours
        assert len(result["problems"]) >= 3

    def test_hours_comma_format_accepted(self):
        """Часы в ответе API с запятой ('8,0') разбираются корректно — совпадение ok."""
        log_item = self._log_item()
        log_item[self.cfg.field_log_hours] = "8,0"  # формат с запятой
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            log_item,
        ])
        result = verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert result["ok"] is True

    def test_two_item_get_calls_made(self):
        """verify_log делает ровно 2 вызова item_get: день + учёт."""
        b24 = FakeB24(item_get_responses=[
            self._day_item([self.log_id]),
            self._log_item(),
        ])
        verify_log(b24, self.day_id, self.log_id, self.description, self.hours, self.cfg)
        assert len(b24.item_get_calls) == 2
        # первый вызов — день (workday_type_id)
        assert b24.item_get_calls[0]["entity_type_id"] == self.cfg.workday_type_id
        # второй вызов — учёт (timelog_type_id)
        assert b24.item_get_calls[1]["entity_type_id"] == self.cfg.timelog_type_id


# ---------------------------------------------------------------------------
# Типы/константы
# ---------------------------------------------------------------------------

class TestSentinels:
    """Проверка сигнальных объектов SKIP, ABORT, Values."""

    def test_skip_is_singleton(self):
        """SKIP — единственный экземпляр _Skip."""
        assert isinstance(SKIP, _Skip)

    def test_abort_is_singleton(self):
        """ABORT — единственный экземпляр _Abort."""
        assert isinstance(ABORT, _Abort)

    def test_values_frozen(self):
        """Values — frozen dataclass, атрибуты неизменяемы."""
        v = Values(description="Задача", hours=8.0)
        with pytest.raises((AttributeError, TypeError)):
            v.hours = 10.0  # type: ignore[misc]

    def test_values_equality(self):
        """Два Values с одинаковыми полями равны (dataclass __eq__)."""
        v1 = Values(description="А", hours=4.0)
        v2 = Values(description="А", hours=4.0)
        assert v1 == v2

    def test_skip_and_abort_are_distinct(self):
        """SKIP и ABORT — разные объекты."""
        assert SKIP is not ABORT

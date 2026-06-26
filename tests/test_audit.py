"""Юнит-тесты аудита REST-вызовов в src/b24.py (Фаза 5, phase_5_03/5_04).

Без сети — Bitrix24Client полностью заменён стабом. Аудит-файл направляется в
tmp_path pytest (никаких .runtime/).

Покрываемые единицы:
- B24._audited_call   → успешный вызов пишет строку status="ok" в JSONL
- B24._audited_call   → ошибка (BitrixAPIError) пишет status="error" + error_code
- Безопасность       → param_keys содержит только КЛЮЧИ; значений в файле нет
- item_list_all      → каждая страница пагинации пишет строку аудита
- batch              → пишет строку аудита на вызов
- audit_file=None    → вызовы работают, файл не создаётся
"""

from __future__ import annotations

import json
import types
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.b24 import B24, B24Error

# Импортируем BitrixAPIError из скилла (нужен для тестирования ошибочной ветки).
import sys
_skill_scripts = Path(__file__).resolve().parent.parent / ".claude" / "skills" / "bitrix24-agent" / "scripts"
if str(_skill_scripts) not in sys.path:
    sys.path.insert(0, str(_skill_scripts))
from bitrix24_client import BitrixAPIError  # type: ignore[import-not-found]


# ---------------------------------------------------------------------------
# Стаб Bitrix24Client
# ---------------------------------------------------------------------------

class FakeClient:
    """Мок Bitrix24Client: возвращает заданные ответы или бросает ошибку."""

    def __init__(
        self,
        call_responses: Optional[List[Any]] = None,
        call_error: Optional[BitrixAPIError] = None,
        batch_response: Any = None,
        batch_error: Optional[BitrixAPIError] = None,
    ) -> None:
        self._call_queue: deque = deque(call_responses or [])
        self._call_error = call_error
        self._batch_response = batch_response
        self._batch_error = batch_error
        self.call_calls: List[Dict[str, Any]] = []
        self.batch_calls: List[Any] = []

    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.call_calls.append({"method": method, "params": params})
        if self._call_error is not None:
            raise self._call_error
        if self._call_queue:
            resp = self._call_queue.popleft()
            if isinstance(resp, Exception):
                raise resp
            return resp
        return {}

    def batch(self, commands: Dict[str, str], halt: bool = False) -> Dict[str, Any]:
        self.batch_calls.append(commands)
        if self._batch_error is not None:
            raise self._batch_error
        return self._batch_response or {}


# ---------------------------------------------------------------------------
# Фабрика B24 для тестов
# ---------------------------------------------------------------------------

def _make_b24(client: FakeClient, audit_path: Optional[Path] = None) -> B24:
    """Создать B24 с фиктивным клиентом. audit_path → _audit_file (или None)."""
    fake_cfg = types.SimpleNamespace(
        env=types.SimpleNamespace(
            domain="test.example.ru",
            audit_file="",   # пустая строка → get_audit_file_path вернёт None
        )
    )
    b24 = B24(fake_cfg, client=client)   # type: ignore[arg-type]
    # Перекрываем _audit_file напрямую — это безопаснее, чем полагаться на env-переменные.
    b24._audit_file = audit_path
    return b24


def _read_audit_lines(path: Path) -> List[Dict[str, Any]]:
    """Прочитать JSONL-файл аудита и вернуть список словарей."""
    if not path.exists():
        return []
    lines = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if raw_line:
            lines.append(json.loads(raw_line))
    return lines


# ---------------------------------------------------------------------------
# TestAuditSuccessfulCall
# ---------------------------------------------------------------------------

class TestAuditSuccessfulCall:
    """Успешный вызов B24.call → строка status='ok' в JSONL."""

    def test_audit_file_created_on_success(self, tmp_path):
        """После успешного вызова аудит-файл создаётся."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{"result": []}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("user.current")
        assert audit_file.exists()

    def test_audit_row_status_ok(self, tmp_path):
        """Успешный вызов → status='ok'."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{"result": {"ID": "1"}}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("user.current")
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"

    def test_audit_row_contains_method(self, tmp_path):
        """Строка аудита содержит правильный method."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("crm.item.list", {"entityTypeId": 1208})
        rows = _read_audit_lines(audit_file)
        assert rows[0]["method"] == "crm.item.list"

    def test_audit_row_ts_is_int(self, tmp_path):
        """Поле ts — целое число (unix-метка)."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("user.current")
        rows = _read_audit_lines(audit_file)
        assert isinstance(rows[0]["ts"], int)

    def test_audit_row_duration_ms_present(self, tmp_path):
        """Поле duration_ms присутствует и является числом."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("user.current")
        rows = _read_audit_lines(audit_file)
        assert "duration_ms" in rows[0]
        assert isinstance(rows[0]["duration_ms"], int)

    def test_audit_row_param_keys_sorted(self, tmp_path):
        """param_keys содержит только ключи параметров, отсортированные."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        params = {"entityTypeId": 1208, "filter": {}, "start": 0}
        b24.call("crm.item.list", params)
        rows = _read_audit_lines(audit_file)
        expected_keys = sorted(params.keys())
        assert rows[0]["param_keys"] == expected_keys


# ---------------------------------------------------------------------------
# TestAuditParamValueSecurity
# ---------------------------------------------------------------------------

class TestAuditParamValueSecurity:
    """КРИТИЧНО: аудит содержит только ключи, не значения параметров."""

    def test_param_values_not_in_audit_file(self, tmp_path):
        """Значения параметров (в т.ч. потенциально секретные) НЕ попадают в JSONL."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        # Имитируем вызов с «секретным» значением
        sensitive_value = "SUPER_SECRET_VALUE_99999"
        b24.call("crm.item.add", {"fields": sensitive_value, "entityTypeId": 1218})
        raw_content = audit_file.read_text(encoding="utf-8")
        assert sensitive_value not in raw_content, (
            "Значение параметра не должно попасть в аудит-файл"
        )

    def test_param_keys_only_keys_in_row(self, tmp_path):
        """param_keys в JSONL — список строк (имён параметров), не значения."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("crm.item.list", {"entityTypeId": 1208, "select": ["id"]})
        rows = _read_audit_lines(audit_file)
        param_keys = rows[0]["param_keys"]
        # param_keys содержит строки-имена (не числа, не списки)
        assert isinstance(param_keys, list)
        for k in param_keys:
            assert isinstance(k, str)
        # Значение 1208 не должно быть в списке ключей
        assert 1208 not in param_keys
        assert "entityTypeId" in param_keys

    def test_no_webhook_code_in_audit(self, tmp_path):
        """Код вебхука, переданный в params, никогда не попадает в аудит."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        # Передаём «секрет» как значение одного из параметров.
        fake_secret = "FAKE_WEBHOOK_SECRET_XYZ999"
        b24.call("user.current", {"webhook_code": fake_secret, "user_id": "42"})
        raw_content = audit_file.read_text(encoding="utf-8")
        # Секретное значение не должно присутствовать в файле аудита.
        assert fake_secret not in raw_content, (
            "Значение параметра (потенциальный секрет) не должно попасть в аудит-файл"
        )
        # param_keys должен содержать только имена ключей, не значения.
        rows = _read_audit_lines(audit_file)
        param_keys = rows[0]["param_keys"]
        assert isinstance(param_keys, list)
        for k in param_keys:
            assert isinstance(k, str)
        assert "webhook_code" in param_keys
        assert "user_id" in param_keys
        assert fake_secret not in param_keys

    def test_tenant_field_contains_domain_not_code(self, tmp_path):
        """Поле 'tenant' в аудите — домен портала, не код вебхука."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.call("user.current")
        rows = _read_audit_lines(audit_file)
        assert rows[0]["tenant"] == "test.example.ru"


# ---------------------------------------------------------------------------
# TestAuditErrorCall
# ---------------------------------------------------------------------------

class TestAuditErrorCall:
    """Ошибочный вызов → строка status='error' в JSONL, B24Error пробрасывается."""

    def test_audit_row_status_error_on_bitrix_error(self, tmp_path):
        """BitrixAPIError от клиента → status='error' в аудите."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("Ошибка доступа", code="ACCESS_DENIED", status=403)
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.call("crm.item.list")
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "error"

    def test_audit_row_error_code_present(self, tmp_path):
        """На ошибке: поле error_code содержит код ошибки."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("Нет прав", code="insufficient_scope", status=403)
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.call("crm.item.list")
        rows = _read_audit_lines(audit_file)
        assert rows[0].get("error_code") == "insufficient_scope"

    def test_audit_row_error_message_present(self, tmp_path):
        """На ошибке: поле error_message присутствует."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("Сообщение об ошибке", code="ERR", status=400)
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.call("crm.item.list")
        rows = _read_audit_lines(audit_file)
        assert "error_message" in rows[0]
        assert isinstance(rows[0]["error_message"], str)

    def test_audit_row_method_correct_on_error(self, tmp_path):
        """На ошибке: поле method заполнено правильно."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("fail", code="X")
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.call("crm.item.get", {"entityTypeId": 1208, "id": 1})
        rows = _read_audit_lines(audit_file)
        assert rows[0]["method"] == "crm.item.get"

    def test_b24error_raised_after_audit_write(self, tmp_path):
        """B24Error бросается наружу даже после успешной записи аудита."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("Ошибка", code="ERR")
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.call("crm.item.list")
        # Аудит тоже записан (два условия выполняются независимо).
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# TestAuditItemListAll
# ---------------------------------------------------------------------------

class TestAuditItemListAll:
    """item_list_all: каждая страница пагинации пишет строку аудита."""

    def test_single_page_writes_one_row(self, tmp_path):
        """Одна страница — одна строка аудита."""
        audit_file = tmp_path / "audit.jsonl"
        # Ответ без next → одна страница
        client = FakeClient(call_responses=[
            {"result": {"items": [{"id": 1}, {"id": 2}]}}
        ])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.item_list_all(1208, filter={"categoryId": 61})
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"

    def test_two_pages_writes_two_rows(self, tmp_path):
        """Две страницы → две строки аудита."""
        audit_file = tmp_path / "audit.jsonl"
        page1 = {"result": {"items": [{"id": i} for i in range(50)]}, "next": 50}
        page2 = {"result": {"items": [{"id": 51}]}}
        client = FakeClient(call_responses=[page1, page2])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.item_list_all(1208)
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 2
        assert all(r["status"] == "ok" for r in rows)

    def test_item_list_all_audit_method_name(self, tmp_path):
        """Метод в аудите — 'crm.item.list'."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(call_responses=[{"result": {"items": []}}])
        b24 = _make_b24(client, audit_path=audit_file)
        b24.item_list_all(1218)
        rows = _read_audit_lines(audit_file)
        assert rows[0]["method"] == "crm.item.list"


# ---------------------------------------------------------------------------
# TestAuditBatch
# ---------------------------------------------------------------------------

class TestAuditBatch:
    """batch: пишет строку аудита на вызов."""

    def test_batch_success_writes_one_row(self, tmp_path):
        """Успешный batch → одна строка status='ok'."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(batch_response={"result": {}})
        b24 = _make_b24(client, audit_path=audit_file)
        commands = {"cmd1": "user.current?{}"}
        b24.batch(commands)
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "ok"

    def test_batch_audit_method_name(self, tmp_path):
        """Метод в аудите batch — 'batch'."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(batch_response={})
        b24 = _make_b24(client, audit_path=audit_file)
        b24.batch({"a": "user.current?{}"})
        rows = _read_audit_lines(audit_file)
        assert rows[0]["method"] == "batch"

    def test_batch_error_writes_error_row(self, tmp_path):
        """Ошибочный batch → строка status='error'."""
        audit_file = tmp_path / "audit.jsonl"
        error = BitrixAPIError("batch fail", code="BATCH_ERR")
        client = FakeClient(batch_error=error)
        b24 = _make_b24(client, audit_path=audit_file)
        with pytest.raises(B24Error):
            b24.batch({"cmd": "user.current?{}"})
        rows = _read_audit_lines(audit_file)
        assert len(rows) == 1
        assert rows[0]["status"] == "error"

    def test_batch_param_keys_are_command_names(self, tmp_path):
        """param_keys для batch — имена команд (ключи словаря commands)."""
        audit_file = tmp_path / "audit.jsonl"
        client = FakeClient(batch_response={})
        b24 = _make_b24(client, audit_path=audit_file)
        commands = {"get_user": "user.current?{}", "list_days": "crm.item.list?entityTypeId=1208"}
        b24.batch(commands)
        rows = _read_audit_lines(audit_file)
        assert set(rows[0]["param_keys"]) == set(commands.keys())


# ---------------------------------------------------------------------------
# TestAuditDisabled
# ---------------------------------------------------------------------------

class TestAuditDisabled:
    """audit_file=None → вызовы работают, файл не создаётся."""

    def test_call_works_without_audit(self):
        """Вызов без аудита не падает и возвращает ответ клиента."""
        client = FakeClient(call_responses=[{"result": {"ID": "1"}}])
        b24 = _make_b24(client, audit_path=None)
        response = b24.call("user.current")
        assert response == {"result": {"ID": "1"}}

    def test_no_audit_file_created(self, tmp_path):
        """При _audit_file=None никакого файла не создаётся."""
        before = list(tmp_path.iterdir())
        client = FakeClient(call_responses=[{}])
        b24 = _make_b24(client, audit_path=None)
        b24.call("user.current")
        after = list(tmp_path.iterdir())
        assert before == after  # tmp_path не тронут

    def test_item_list_all_works_without_audit(self):
        """item_list_all без аудита работает корректно."""
        client = FakeClient(call_responses=[{"result": {"items": [{"id": 1}]}}])
        b24 = _make_b24(client, audit_path=None)
        result = b24.item_list_all(1208)
        assert result == [{"id": 1}]

    def test_batch_works_without_audit(self):
        """batch без аудита работает корректно."""
        client = FakeClient(batch_response={"result": {"cmd1": {"id": 5}}})
        b24 = _make_b24(client, audit_path=None)
        result = b24.batch({"cmd1": "user.current?{}"})
        assert "result" in result

    def test_error_propagates_without_audit(self):
        """Ошибка клиента пробрасывается как B24Error даже без аудит-файла."""
        error = BitrixAPIError("fail", code="ERR")
        client = FakeClient(call_error=error)
        b24 = _make_b24(client, audit_path=None)
        with pytest.raises(B24Error):
            b24.call("crm.item.list")

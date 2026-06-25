"""Тонкая обёртка над REST-клиентом скилла bitrix24-agent.

Скрывает детали транспорта и даёт удобные методы для работы со смарт-процессами
(`crm.item.*`) и `batch`. Транспорт (ретраи, rate-limit, маскирование) НЕ дублируем —
переиспользуем `Bitrix24Client` из скилла, путь к которому добавляем в sys.path.

Безопасность: код вебхука берётся только из Config.env и НИКОГДА не печатается/не логируется.
Унифицированная обработка ошибок: insufficient_scope / INVALID_CREDENTIALS /
QUERY_LIMIT_EXCEEDED → понятные сообщения через B24Error (без трейсбека клиента).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config

# --- Импорт клиента скилла (НЕ копируем транспорт) ---
_SKILL_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / ".claude"
    / "skills"
    / "bitrix24-agent"
    / "scripts"
)
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

try:
    from bitrix24_client import (  # type: ignore[import-not-found]
        Bitrix24Client,
        BitrixAPIError,
        TenantConfig,
        build_rate_limiter_from_env,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        f"Не удалось импортировать REST-клиент скилла из {_SKILL_SCRIPTS}. "
        "Проверьте наличие .claude/skills/bitrix24-agent/scripts/bitrix24_client.py."
    ) from exc


# Подсказки по типичным ошибкам доступа (PRD §2.6, phase_1_03).
_REQUIRED_SCOPES = "crm,task,timeman,user"
_ERROR_HINTS = {
    "insufficient_scope": (
        "Недостаточно прав (scope) у вебхука. Нужны scope: "
        f"{_REQUIRED_SCOPES}. Перегенерируйте вебхук на портале (/devops/) с этими правами."
    ),
    "INVALID_CREDENTIALS": (
        "Неверные учётные данные вебхука или у пользователя-владельца нет прав на "
        "«Рабочий день»/«Учёт рабочего времени». Вебхук должен быть создан под учёткой "
        "Владельца записей. Проверьте B24_WEBHOOK_USER_ID и B24_WEBHOOK_CODE в .env."
    ),
    "QUERY_LIMIT_EXCEEDED": (
        "Превышен лимит частоты запросов к порталу. Повторите позже или используйте batch "
        "для массовых чтений (ретраи уже выполняются клиентом)."
    ),
    "NO_AUTH_FOUND": (
        "Авторизация не найдена: код вебхука пуст или некорректен. Заполните B24_WEBHOOK_CODE "
        "в .env (это секрет)."
    ),
    "ACCESS_DENIED": (
        "Доступ запрещён для этого метода/сущности. Проверьте права пользователя-владельца "
        "вебхука и набор scope."
    ),
}


class B24Error(RuntimeError):
    """Читаемая ошибка работы с порталом (без трейсбека клиента).

    Несёт code/status исходной ошибки Bitrix для диагностики верхнего уровня.
    """

    def __init__(self, message: str, *, code: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _wrap_error(exc: BitrixAPIError) -> B24Error:
    """Преобразовать BitrixAPIError в понятную B24Error с подсказкой."""
    hint = _ERROR_HINTS.get(exc.code)
    base = f"Ошибка Bitrix24 (code={exc.code or 'unknown'}, status={exc.status}): {exc}"
    message = f"{base}\n  → {hint}" if hint else base
    return B24Error(message, code=exc.code, status=exc.status)


def _result_items(response: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Извлечь строки из ответа crm.item.* (result.items) или плоского result(list)."""
    result = response.get("result")
    if isinstance(result, dict):
        items = result.get("items")
        if isinstance(items, list):
            return items
    if isinstance(result, list):
        return result
    return []


class B24:
    """Удобная обёртка над Bitrix24Client для смарт-процессов «Рабочего дня»."""

    def __init__(self, config: Config, *, client: Optional[Bitrix24Client] = None) -> None:
        self.config = config
        if client is not None:
            self.client = client
            return
        env = config.env
        tenant = TenantConfig(
            domain=env.domain,
            auth_mode="webhook",
            webhook_user_id=env.webhook_user_id,
            webhook_code=env.webhook_code,  # секрет — не логируется
        )
        self.client = Bitrix24Client(
            tenant,
            rate_limiter=build_rate_limiter_from_env(),
        )

    # --- Базовый вызов ---
    def call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Вызвать произвольный REST-метод, перехватив ошибки в B24Error."""
        try:
            return self.client.call(method, params=params or {})
        except BitrixAPIError as exc:
            raise _wrap_error(exc) from None

    def user_current(self) -> Dict[str, Any]:
        """user.current — для smoke-проверки доступа (read-only)."""
        response = self.call("user.current")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def smoke(self) -> Dict[str, Any]:
        """Read-only smoke-проверка доступа к порталу через user.current.

        Возвращает нормализованный словарь: {id, full_name, time_zone, raw}.
        Бросает B24Error с понятной подсказкой при ошибках доступа
        (insufficient_scope / INVALID_CREDENTIALS / NO_AUTH_FOUND и т.п.).
        Никаких записей: используется ТОЛЬКО read-only метод user.current.
        """
        user = self.user_current()

        def pick(*keys: str) -> str:
            for key in keys:
                value = user.get(key)
                if value not in (None, ""):
                    return str(value)
            return ""

        user_id = pick("ID", "id")
        first = pick("NAME", "name")
        last = pick("LAST_NAME", "lastName", "last_name")
        second = pick("SECOND_NAME", "secondName", "second_name")
        full_name = " ".join(part for part in (last, first, second) if part).strip()
        if not full_name:
            full_name = pick("EMAIL", "email") or "<без имени>"
        time_zone = pick("TIME_ZONE", "timeZone", "time_zone") or "<не указана>"

        return {
            "id": user_id,
            "full_name": full_name,
            "time_zone": time_zone,
            "raw": user,
        }

    # --- Смарт-процессы ---
    def item_list(
        self,
        entity_type_id: int,
        *,
        filter: Optional[Dict[str, Any]] = None,
        select: Optional[List[str]] = None,
        order: Optional[Dict[str, str]] = None,
        start: int = 0,
    ) -> List[Dict[str, Any]]:
        """Одна страница crm.item.list (до 50 элементов). Возвращает список словарей."""
        params: Dict[str, Any] = {"entityTypeId": entity_type_id, "start": start}
        if filter:
            params["filter"] = filter
        if select:
            params["select"] = select
        if order:
            params["order"] = order
        response = self.call("crm.item.list", params)
        return _result_items(response)

    def item_list_all(
        self,
        entity_type_id: int,
        *,
        filter: Optional[Dict[str, Any]] = None,
        select: Optional[List[str]] = None,
        order: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Полная выборка с пагинацией по start/next (собирает >50 записей).

        Идёт по курсору `next` из ответа, пока он присутствует. Защита от зацикливания:
        прерываемся, если страница пуста или курсор не двигается вперёд.
        """
        params: Dict[str, Any] = {"entityTypeId": entity_type_id}
        if filter:
            params["filter"] = filter
        if select:
            params["select"] = select
        if order:
            params["order"] = order

        collected: List[Dict[str, Any]] = []
        start = 0
        while True:
            page_params = {**params, "start": start}
            try:
                response = self.client.call("crm.item.list", params=page_params)
            except BitrixAPIError as exc:
                raise _wrap_error(exc) from None

            items = _result_items(response)
            collected.extend(items)

            next_start = response.get("next")
            if next_start is None or not items:
                break
            try:
                next_start_int = int(next_start)
            except (TypeError, ValueError):
                break
            if next_start_int <= start:
                break
            start = next_start_int

        return collected

    def item_get(
        self,
        entity_type_id: int,
        item_id: int,
        *,
        select: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """crm.item.get одного элемента. Возвращает словарь или None."""
        params: Dict[str, Any] = {"entityTypeId": entity_type_id, "id": item_id}
        if select:
            params["select"] = select
        response = self.call("crm.item.get", params)
        result = response.get("result")
        if isinstance(result, dict):
            item = result.get("item")
            if isinstance(item, dict):
                return item
            return result
        return None

    def item_add(
        self,
        entity_type_id: int,
        fields: Dict[str, Any],
        *,
        plan_only: bool = False,
    ) -> Dict[str, Any]:
        """Создать элемент crm.item.add.

        ВНИМАНИЕ (CLAUDE.md §5): реальная запись в прод — только по явному разрешению.
        Поддержка двухфазной записи plan→execute: при plan_only=True ничего не пишет, а
        возвращает план-описание операции для последующего исполнения. Реальный вызов
        crm.item.add на этой фазе НЕ выполняется (smoke использует только user.current).
        """
        params = {"entityTypeId": entity_type_id, "fields": fields}
        if plan_only:
            return {
                "plan_only": True,
                "method": "crm.item.add",
                "params": params,
                "note": (
                    "Запись НЕ выполнена (plan_only). Для исполнения требуется явное "
                    "разрешение пользователя (политика записи в прод)."
                ),
            }
        response = self.call("crm.item.add", params)
        result = response.get("result")
        if isinstance(result, dict):
            return result.get("item") if isinstance(result.get("item"), dict) else result
        return {"result": result}

    def batch(self, commands: Dict[str, str], *, halt: int = 0) -> Dict[str, Any]:
        """Пакетный вызов до 50 команд за раз (для массовых чтений).

        commands — {name: 'method?param=value'}; halt: 0 (продолжать) / 1 (стоп на ошибке).
        """
        try:
            return self.client.batch(commands, halt=bool(halt))
        except BitrixAPIError as exc:
            raise _wrap_error(exc) from None
        except ValueError as exc:
            # Например, >50 команд — отдаём читаемо, без трейсбека.
            raise B24Error(f"Некорректный batch-запрос: {exc}") from None

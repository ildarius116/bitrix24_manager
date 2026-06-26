"""Загрузка конфигурации проекта: секреты из .env + метамодель/дефолты из config.yaml.

Разделение ответственности (правило проекта):
- `.env` (вне git) — ТОЛЬКО секреты доступа (домен, режим, user id, КОД вебхука).
- `config.yaml` — несекретная метамодель (entityTypeId, коды полей, дефолты, окно дней).

Логика приложения берёт коды полей из `Config.fields`, а не хардкодит их (CLAUDE.md).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("workday")

import yaml

try:
    from dotenv import load_dotenv
except ImportError as exc:  # pragma: no cover - зависимость объявлена в requirements.txt
    raise ImportError(
        "Не установлен python-dotenv. Выполните: "
        "venv/Scripts/python.exe -m pip install -r requirements.txt"
    ) from exc


# Корень проекта = родитель каталога src/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """Понятная ошибка конфигурации (без трейсбека для пользователя)."""


@dataclass(frozen=True)
class EnvConfig:
    """Секретная часть конфигурации (из .env). Код вебхука НЕ печатать/не логировать."""

    domain: str
    auth_mode: str
    webhook_user_id: str
    webhook_code: str  # СЕКРЕТ
    audit_file: Optional[str] = None

    def masked_summary(self) -> str:
        """Безопасное представление для логов/диагностики — без секрета."""
        return (
            f"domain={self.domain} auth_mode={self.auth_mode} "
            f"webhook_user_id={self.webhook_user_id} webhook_code=***"
        )


@dataclass(frozen=True)
class Config:
    """Полная конфигурация: секрет (env) + несекретная метамодель/дефолты (yaml)."""

    env: EnvConfig
    entity: Dict[str, Any] = field(default_factory=dict)
    fields: Dict[str, Any] = field(default_factory=dict)
    defaults: Dict[str, Any] = field(default_factory=dict)
    export: Dict[str, Any] = field(default_factory=dict)
    runtime: Dict[str, Any] = field(default_factory=dict)
    contract_general_tasks: str = ""
    edit_window_days: int = 4

    # --- Удобные геттеры метамодели (читаемость в остальном коде) ---
    @property
    def workday_type_id(self) -> int:
        return int(self.entity["workday_type_id"])

    @property
    def timelog_type_id(self) -> int:
        return int(self.entity["timelog_type_id"])

    @property
    def timelog_category_id(self) -> int:
        """ID воронки учёта «Работы/задачи по договорам» (63). Не хардкодить — из config.yaml."""
        return int(self.entity["timelog_category_id"])

    @property
    def field_workday_date(self) -> str:
        return str(self.fields["workday_date"])

    @property
    def field_workday_works(self) -> str:
        return str(self.fields["workday_works"])

    @property
    def field_workday_employee(self) -> str:
        return str(self.fields["workday_employee"])

    @property
    def field_log_parent(self) -> str:
        return str(self.fields["log_parent"])

    @property
    def field_log_contract(self) -> str:
        return str(self.fields["log_contract"])

    @property
    def field_log_contract_tech(self) -> str:
        """Код поля [тех] ID договора (ufCrm48_1754894889), парного коду договора."""
        return str(self.fields["log_contract_tech"])

    @property
    def field_log_description(self) -> str:
        return str(self.fields["log_description"])

    @property
    def field_log_hours(self) -> str:
        return str(self.fields["log_hours"])

    @property
    def field_log_result(self) -> str:
        return str(self.fields["log_result"])

    @property
    def contract_tech_id(self) -> str:
        """[тех] строковый ID договора (defaults.contract_tech_id, напр. "2")."""
        return str(self.defaults.get("contract_tech_id", ""))

    @property
    def journal_ttl_sec(self) -> int:
        """TTL локального журнала обработанных дней в секундах.

        Читается из runtime.journal_ttl_days (дни, дефолт 7) и переводится в секунды.
        """
        try:
            return int(self.runtime.get("journal_ttl_days", 7)) * 86400
        except (ValueError, TypeError) as exc:
            log.warning(
                "Некорректное значение runtime.journal_ttl_days (%s) — откат к 7 дням.",
                exc,
            )
            return 7 * 86400


def _require_env(name: str, *, secret: bool = False) -> str:
    """Достать обязательную переменную окружения, иначе — понятная ошибка.

    Для секрета (`secret=True`) значение НЕ включается в текст ошибки.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        hint = ""
        if name == "B24_WEBHOOK_CODE":
            hint = (
                " Это СЕКРЕТНЫЙ код вебхука. Скопируйте .env.example в .env и заполните "
                "B24_WEBHOOK_CODE значением из конструктора вебхуков портала "
                "(/devops/). В git .env не попадает."
            )
        raise ConfigError(
            f"Не задана переменная окружения {name}. "
            f"Проверьте файл .env в корне проекта.{hint}"
        )
    _ = secret  # значение секрета не используется в сообщениях
    return raw


def load_env(env_path: Optional[Path] = None) -> EnvConfig:
    """Прочитать .env и собрать секретную конфигурацию с валидацией.

    Понятно падает, если нет .env или не заполнен B24_WEBHOOK_CODE.
    """
    path = env_path or DEFAULT_ENV_PATH
    if path.exists():
        load_dotenv(dotenv_path=path, override=False)
    # Если файла нет — _require_env всё равно даст понятную ошибку про переменные.

    domain = _require_env("B24_DOMAIN")
    auth_mode = os.getenv("B24_AUTH_MODE", "webhook").strip().lower() or "webhook"
    if auth_mode != "webhook":
        raise ConfigError(
            f"B24_AUTH_MODE={auth_mode!r} не поддерживается: проект работает только в режиме "
            "'webhook' (REST-only)."
        )
    webhook_user_id = _require_env("B24_WEBHOOK_USER_ID")
    webhook_code = _require_env("B24_WEBHOOK_CODE", secret=True)
    audit_file = os.getenv("B24_AUDIT_FILE", "").strip() or None

    return EnvConfig(
        domain=domain,
        auth_mode=auth_mode,
        webhook_user_id=webhook_user_id,
        webhook_code=webhook_code,
        audit_file=audit_file,
    )


def load_yaml(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Прочитать несекретный config.yaml."""
    path = config_path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Не найден файл конфигурации {path}.")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Ошибка разбора {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"Ожидался YAML-объект в {path}, получено: {type(data).__name__}.")
    return data


def load_config(
    *,
    env_path: Optional[Path] = None,
    config_path: Optional[Path] = None,
) -> Config:
    """Собрать полную конфигурацию (env + yaml) с валидацией.

    Бросает ConfigError с понятным сообщением при любой проблеме конфигурации.
    """
    env = load_env(env_path)
    data = load_yaml(config_path)

    entity = data.get("entity") or {}
    fields = data.get("fields") or {}
    if not isinstance(entity, dict) or not isinstance(fields, dict):
        raise ConfigError("Секции 'entity' и 'fields' в config.yaml должны быть объектами.")

    for key in ("workday_type_id", "timelog_type_id"):
        if key not in entity:
            raise ConfigError(f"В config.yaml отсутствует entity.{key}.")
    # timelog_category_id и поля для записи 1218 нужны только для fill; при их отсутствии
    # геттеры дадут KeyError на этапе записи. Здесь не делаем их обязательными, чтобы
    # export продолжал работать на минимальной конфигурации.
    for key in ("workday_date", "workday_works"):
        if key not in fields:
            raise ConfigError(f"В config.yaml отсутствует fields.{key}.")

    return Config(
        env=env,
        entity=entity,
        fields=fields,
        defaults=data.get("defaults") or {},
        export=data.get("export") or {},
        runtime=data.get("runtime") or {},
        contract_general_tasks=str(data.get("contract_general_tasks", "")),
        edit_window_days=int(data.get("edit_window_days", 4)),
    )

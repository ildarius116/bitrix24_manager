"""Юнит-тесты src/journal.py и Config.journal_ttl_sec (Фаза 5, phase_5_01/5_04).

Без сети, без .runtime/ — все пути в tmp_path pytest.
Фиксированная «сегодня» не требуется (now_ts инъектируется параметром).

Покрываемые единицы:
- load_journal   — отсутствующий/битый/валидный файл
- is_processed   — TTL-граница (<=ttl: True; >ttl: False); отсутствующий day_id
- mark_processed — roundtrip, обновление существующей записи, атомарность
- Нет секретов   — структура ключей записи журнала
- Config.journal_ttl_sec — 7 дней по умолчанию; кастомное значение
"""

from __future__ import annotations

import json
import logging
import os
import types
from pathlib import Path

import pytest

from src.journal import is_processed, load_journal, mark_processed
from src.config import Config, EnvConfig


# ---------------------------------------------------------------------------
# Вспомогательная фикстура: минимальный EnvConfig для Config
# ---------------------------------------------------------------------------

def _make_env() -> EnvConfig:
    """Минимальный EnvConfig без реальных секретов."""
    return EnvConfig(
        domain="test.example.ru",
        auth_mode="webhook",
        webhook_user_id="1",
        webhook_code="TEST_SECRET_NOT_REAL",
    )


# ---------------------------------------------------------------------------
# TestLoadJournal
# ---------------------------------------------------------------------------

class TestLoadJournal:
    """load_journal: отсутствие/битый/валидный файл."""

    def test_missing_file_returns_empty_dict(self, tmp_path):
        """Если файл отсутствует — возвращает {} без исключений."""
        p = tmp_path / "nonexistent.json"
        result = load_journal(p)
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path):
        """Пустой файл → JSON ошибка → {} без краша."""
        p = tmp_path / "journal.json"
        p.write_text("", encoding="utf-8")
        result = load_journal(p)
        assert result == {}

    def test_broken_json_returns_empty_dict(self, tmp_path):
        """Битый JSON → {} (без исключений)."""
        p = tmp_path / "journal.json"
        p.write_text("{invalid json}", encoding="utf-8")
        result = load_journal(p)
        assert result == {}

    def test_truncated_json_returns_empty_dict(self, tmp_path):
        """Оборванный JSON → {} (без исключений)."""
        p = tmp_path / "journal.json"
        p.write_text('{"100": {"day_id": 100, "ts": 17000', encoding="utf-8")
        result = load_journal(p)
        assert result == {}

    def test_valid_file_returns_correct_dict(self, tmp_path):
        """Валидный JSON возвращается как словарь."""
        p = tmp_path / "journal.json"
        data = {"42": {"day_id": 42, "date": "2026-06-25", "ts": 1000, "log_id": 999}}
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_journal(p)
        assert result == data

    def test_json_array_instead_of_dict_returns_empty(self, tmp_path):
        """JSON-массив вместо объекта → {} (неожиданный формат)."""
        p = tmp_path / "journal.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = load_journal(p)
        assert result == {}

    def test_json_null_returns_empty(self, tmp_path):
        """JSON null вместо объекта → {}."""
        p = tmp_path / "journal.json"
        p.write_text("null", encoding="utf-8")
        result = load_journal(p)
        assert result == {}

    def test_multiple_entries_all_returned(self, tmp_path):
        """Несколько записей — все возвращаются."""
        p = tmp_path / "journal.json"
        data = {
            "1": {"day_id": 1, "ts": 100, "log_id": 10},
            "2": {"day_id": 2, "ts": 200, "log_id": 20},
            "3": {"day_id": 3, "ts": 300, "log_id": 30},
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        result = load_journal(p)
        assert len(result) == 3
        assert "1" in result and "2" in result and "3" in result


# ---------------------------------------------------------------------------
# TestIsProcessed
# ---------------------------------------------------------------------------

class TestIsProcessed:
    """is_processed: TTL-граница и отсутствующие записи."""

    def _entry(self, ts: int) -> dict:
        return {"day_id": 100, "date": "2026-06-25", "ts": ts, "log_id": 999}

    def test_fresh_record_returns_true(self):
        """Запись свежее TTL → True."""
        journal = {"100": self._entry(ts=1000)}
        # now=1100, ts=1000, delta=100, ttl=604800 → 100 <= 604800 → True
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=1100) is True

    def test_exactly_at_ttl_boundary_returns_true(self):
        """Ровно на границе TTL (delta == ttl_sec) → True (граница включена)."""
        ttl = 604800  # 7 дней
        ts = 1_000_000
        now = ts + ttl  # now - ts == ttl → 604800 <= 604800 → True
        journal = {"100": self._entry(ts=ts)}
        assert is_processed(journal, 100, ttl_sec=ttl, now_ts=now) is True

    def test_one_second_past_ttl_returns_false(self):
        """На 1 секунду позже TTL (delta == ttl_sec + 1) → False."""
        ttl = 604800
        ts = 1_000_000
        now = ts + ttl + 1  # delta = ttl + 1 > ttl → False
        journal = {"100": self._entry(ts=ts)}
        assert is_processed(journal, 100, ttl_sec=ttl, now_ts=now) is False

    def test_expired_record_returns_false(self):
        """Протухшая запись → False."""
        journal = {"100": self._entry(ts=1000)}
        # now=1_000_000, delta=999_000, ttl=7200 → 999000 > 7200 → False
        assert is_processed(journal, 100, ttl_sec=7200, now_ts=1_000_000) is False

    def test_missing_day_id_returns_false(self):
        """Отсутствующий day_id → False."""
        journal = {"200": self._entry(ts=1000)}
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=1100) is False

    def test_empty_journal_returns_false(self):
        """Пустой журнал → False."""
        assert is_processed({}, 100, ttl_sec=604800, now_ts=1000) is False

    def test_entry_not_a_dict_returns_false(self):
        """Запись в журнале — не словарь → False (защитная ветка)."""
        journal = {"100": "not a dict"}
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=999999) is False

    def test_entry_without_ts_returns_false(self):
        """Запись без поля 'ts' → False."""
        journal = {"100": {"day_id": 100, "log_id": 9}}
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=1000) is False

    def test_entry_with_none_ts_returns_false(self):
        """Поле 'ts' = None → False."""
        journal = {"100": {"ts": None}}
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=1000) is False

    def test_entry_with_string_ts_garbage_returns_false(self):
        """Строковый мусор в 'ts' → False."""
        journal = {"100": {"ts": "garbage"}}
        assert is_processed(journal, 100, ttl_sec=604800, now_ts=1000) is False

    def test_day_id_int_matches_string_key(self):
        """day_id как int: ключ словаря ищется как str(day_id)."""
        journal = {"42": self._entry(ts=1000)}
        assert is_processed(journal, 42, ttl_sec=604800, now_ts=1100) is True

    def test_zero_ttl_only_same_second_is_valid(self):
        """TTL=0: только запись с точно тем же ts считается валидной."""
        ts = 5000
        journal = {"1": self._entry(ts=ts)}
        assert is_processed(journal, 1, ttl_sec=0, now_ts=ts) is True
        assert is_processed(journal, 1, ttl_sec=0, now_ts=ts + 1) is False


# ---------------------------------------------------------------------------
# TestMarkProcessed
# ---------------------------------------------------------------------------

class TestMarkProcessed:
    """mark_processed: запись, обновление, атомарность, roundtrip."""

    def test_creates_file_if_not_exists(self, tmp_path):
        """Если файла нет — создаёт его и пишет запись."""
        p = tmp_path / "processed.json"
        assert not p.exists()
        mark_processed(p, 100, "2026-06-25", 999, now_ts=1000)
        assert p.exists()

    def test_roundtrip_day_id_in_journal(self, tmp_path):
        """После mark_processed день обнаруживается через load_journal."""
        p = tmp_path / "processed.json"
        mark_processed(p, 42, "2026-06-25", 888, now_ts=5000)
        journal = load_journal(p)
        assert "42" in journal

    def test_roundtrip_fields_correct(self, tmp_path):
        """Запись содержит корректные day_id, date, log_id, ts."""
        p = tmp_path / "processed.json"
        mark_processed(p, 42, "2026-06-21", 123, now_ts=9999)
        journal = load_journal(p)
        entry = journal["42"]
        assert entry["day_id"] == 42
        assert entry["date"] == "2026-06-21"
        assert entry["log_id"] == 123
        assert entry["ts"] == 9999

    def test_processed_at_is_iso_string(self, tmp_path):
        """Поле processed_at — ISO-строка (UTC)."""
        p = tmp_path / "processed.json"
        mark_processed(p, 1, "2026-06-25", 1, now_ts=1750000000)
        journal = load_journal(p)
        entry = journal["1"]
        # Must be parseable as datetime string
        processed_at = entry["processed_at"]
        assert isinstance(processed_at, str)
        assert "T" in processed_at  # ISO format contains 'T'

    def test_repeat_mark_updates_existing(self, tmp_path):
        """Повторный mark_processed обновляет существующую запись."""
        p = tmp_path / "processed.json"
        mark_processed(p, 100, "2026-06-25", 999, now_ts=1000)
        # Обновляем: другой log_id и новый ts
        mark_processed(p, 100, "2026-06-25", 555, now_ts=2000)
        journal = load_journal(p)
        entry = journal["100"]
        assert entry["log_id"] == 555
        assert entry["ts"] == 2000

    def test_multiple_days_in_journal(self, tmp_path):
        """Несколько дней в журнале — все присутствуют после mark_processed."""
        p = tmp_path / "processed.json"
        mark_processed(p, 1, "2026-06-23", 10, now_ts=1000)
        mark_processed(p, 2, "2026-06-24", 20, now_ts=2000)
        mark_processed(p, 3, "2026-06-25", 30, now_ts=3000)
        journal = load_journal(p)
        assert "1" in journal and "2" in journal and "3" in journal

    def test_atomicity_file_valid_json_after_write(self, tmp_path):
        """После записи файл содержит валидный JSON (атомарность через tmp+replace)."""
        p = tmp_path / "processed.json"
        mark_processed(p, 77, "2026-06-25", 1, now_ts=1000)
        raw = p.read_text(encoding="utf-8")
        parsed = json.loads(raw)  # не должен бросать
        assert isinstance(parsed, dict)

    def test_creates_parent_dir(self, tmp_path):
        """Создаёт вложенные каталоги при необходимости."""
        p = tmp_path / "subdir" / "deep" / "processed.json"
        assert not p.parent.exists()
        mark_processed(p, 5, "2026-06-25", 1, now_ts=1000)
        assert p.exists()

    def test_tmp_file_cleaned_up(self, tmp_path):
        """Временный файл .tmp убирается после успешной записи."""
        p = tmp_path / "processed.json"
        mark_processed(p, 10, "2026-06-25", 1, now_ts=1000)
        tmp = p.parent / (p.name + ".tmp")
        assert not tmp.exists()

    def test_tmp_cleaned_on_os_replace_failure(self, tmp_path, monkeypatch):
        """os.replace сбоит после создания tmp → исключение пробрасывается, .tmp-файл удалён.

        Проверяем две гарантии реализации:
        1) исключение НЕ проглатывается mark_processed (пробрасывается наружу);
        2) осиротевший .tmp-файл удалён в except-блоке (не остаётся на диске).
        """
        p = tmp_path / "processed.json"
        tmp_file = tmp_path / "processed.json.tmp"

        def fail_replace(src, dst):
            raise OSError("replace failed: disk full")

        # Патчим os.replace в самом модуле os — src.journal использует тот же объект модуля.
        monkeypatch.setattr(os, "replace", fail_replace)

        # Исключение должно пробрасываться наружу (mark_processed не глушит его).
        with pytest.raises(OSError, match="replace failed"):
            mark_processed(p, 42, "2026-06-25", 999, now_ts=10000)

        # .tmp-файл создаётся через write_text до вызова os.replace.
        # После сбоя except-блок должен его удалить.
        assert not tmp_file.exists(), (
            ".tmp-файл должен быть удалён при сбое os.replace"
        )
        # Основной файл не создан (os.replace не выполнился).
        assert not p.exists(), (
            "processed.json не должен существовать при сбое os.replace"
        )


# ---------------------------------------------------------------------------
# TestJournalNoSecrets
# ---------------------------------------------------------------------------

class TestJournalNoSecrets:
    """Журнал не содержит секретных данных."""

    ALLOWED_KEYS = {"day_id", "date", "processed_at", "log_id", "ts"}

    def test_journal_entry_has_only_non_secret_keys(self, tmp_path):
        """Запись содержит только разрешённые несекретные ключи."""
        p = tmp_path / "processed.json"
        mark_processed(p, 42, "2026-06-25", 999, now_ts=10000)
        journal = load_journal(p)
        entry = journal["42"]
        for key in entry:
            assert key in self.ALLOWED_KEYS, f"Неожиданный ключ в журнале: {key!r}"

    def test_journal_entry_no_webhook_code(self, tmp_path):
        """В записи журнала нет «webhook», «code», «secret» и подобных слов."""
        p = tmp_path / "processed.json"
        mark_processed(p, 100, "2026-06-24", 1, now_ts=9999)
        raw = p.read_text(encoding="utf-8").lower()
        for forbidden in ("webhook", "secret", "password", "token", "code"):
            assert forbidden not in raw, f"Потенциально секретное слово {forbidden!r} в журнале"

    def test_journal_values_are_safe_types(self, tmp_path):
        """Значения в записи — только int или str (нет вложенных объектов с секретами)."""
        p = tmp_path / "processed.json"
        mark_processed(p, 7, "2026-06-25", 88, now_ts=50000)
        journal = load_journal(p)
        entry = journal["7"]
        for key, value in entry.items():
            assert isinstance(value, (int, str)), (
                f"Значение поля {key!r} имеет неожиданный тип {type(value).__name__}"
            )


# ---------------------------------------------------------------------------
# TestJournalTtlSec
# ---------------------------------------------------------------------------

class TestJournalTtlSec:
    """Config.journal_ttl_sec: конвертация journal_ttl_days → секунды."""

    def _make_config(self, runtime: dict) -> Config:
        """Создать реальный Config с заданным runtime."""
        return Config(
            env=_make_env(),
            entity={"workday_type_id": 1208, "timelog_type_id": 1218},
            fields={"workday_date": "uf1", "workday_works": "uf2"},
            runtime=runtime,
        )

    def test_default_7_days_returns_604800(self):
        """runtime.journal_ttl_days=7 → 7*86400 = 604800 секунд."""
        cfg = self._make_config({"journal_ttl_days": 7})
        assert cfg.journal_ttl_sec == 604800

    def test_empty_runtime_uses_default_7_days(self):
        """Пустой runtime → дефолт 7 дней = 604800 секунд."""
        cfg = self._make_config({})
        assert cfg.journal_ttl_sec == 604800

    def test_custom_3_days_returns_259200(self):
        """runtime.journal_ttl_days=3 → 3*86400 = 259200 секунд."""
        cfg = self._make_config({"journal_ttl_days": 3})
        assert cfg.journal_ttl_sec == 259200

    def test_custom_1_day_returns_86400(self):
        """runtime.journal_ttl_days=1 → 86400 секунд."""
        cfg = self._make_config({"journal_ttl_days": 1})
        assert cfg.journal_ttl_sec == 86400

    def test_custom_14_days(self):
        """runtime.journal_ttl_days=14 → 14*86400 = 1209600 секунд."""
        cfg = self._make_config({"journal_ttl_days": 14})
        assert cfg.journal_ttl_sec == 14 * 86400

    def test_ttl_sec_is_int(self):
        """journal_ttl_sec возвращает int, а не float."""
        cfg = self._make_config({"journal_ttl_days": 7})
        assert isinstance(cfg.journal_ttl_sec, int)

    # --- Ветки фолбэка на некорректных значениях (error-ветки ревью) ---

    def test_string_garbage_ttl_days_returns_default_with_warning(self, caplog):
        """journal_ttl_days='abc' (нечисловая строка) → fallback 604800 + log.warning."""
        cfg = self._make_config({"journal_ttl_days": "abc"})
        with caplog.at_level(logging.WARNING, logger="workday"):
            result = cfg.journal_ttl_sec
        assert result == 7 * 86400, (
            f"Ожидался fallback 604800 при нечисловом journal_ttl_days, получено {result}"
        )
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Ожидалось log.warning при journal_ttl_days='abc'"
        )

    def test_none_ttl_days_returns_default_with_warning(self, caplog):
        """journal_ttl_days=None → fallback 604800 + log.warning."""
        cfg = self._make_config({"journal_ttl_days": None})
        with caplog.at_level(logging.WARNING, logger="workday"):
            result = cfg.journal_ttl_sec
        assert result == 7 * 86400, (
            f"Ожидался fallback 604800 при journal_ttl_days=None, получено {result}"
        )
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Ожидалось log.warning при journal_ttl_days=None"
        )

    def test_list_ttl_days_returns_default_with_warning(self, caplog):
        """journal_ttl_days=[] (список) → fallback 604800 + log.warning."""
        cfg = self._make_config({"journal_ttl_days": []})
        with caplog.at_level(logging.WARNING, logger="workday"):
            result = cfg.journal_ttl_sec
        assert result == 7 * 86400, (
            f"Ожидался fallback 604800 при journal_ttl_days=[], получено {result}"
        )
        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "Ожидалось log.warning при journal_ttl_days=[]"
        )

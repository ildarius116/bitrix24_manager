# phase_1_02 — Обёртка над REST-клиентом скилла

**Статус:** TODO
**Фаза:** 1 (Каркас + REST-обёртка)
**Зависит от:** phase_1_01

## Цель
Единая тонкая обёртка `src/b24.py` над `.claude/skills/bitrix24-agent/scripts/bitrix24_client.py`,
скрывающая детали транспорта и дающая удобные методы для работы со смарт-процессами.

## Объём работ
- Импорт `Bitrix24Client`, `TenantConfig`, `BitrixAPIError` из клиента скилла (путь добавлять в `sys.path`).
- Конструктор из `Config` (env): создание клиента в режиме `webhook`.
- Методы:
  - `item_list(entity_type_id, *, filter=None, select=None, order=None, start=0)` — постранично или одной страницей;
  - `item_list_all(...)` — полная выборка с пагинацией по `start`/`next`;
  - `item_get(entity_type_id, id, select=None)`;
  - `item_add(entity_type_id, fields, *, plan_only=False)` — поддержка двухфазной записи (plan→execute);
  - `batch(commands, halt=0)`.
- Унифицированная обработка ошибок: `insufficient_scope`/`INVALID_CREDENTIALS`/`QUERY_LIMIT_EXCEEDED` → понятные сообщения; ретраи берём из клиента.

## Критерии приёмки (DoD)
- `b24.item_list(1208, order={"id":"desc"})` возвращает список словарей без ошибок.
- Пагинация `item_list_all` корректно собирает >50 записей.
- Ошибки доступа дают читаемое сообщение, а не трейсбек клиента.

## Артефакты
`src/b24.py`.

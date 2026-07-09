# ADR-042 · Канонизация домена бэка к форме `https://<host>/`

- Статус: accepted
- Дата: 2026-07-09
- Затрагивает: [modules/backends](../modules/backends/README.md)
- Амендмент: [ADR-020](ADR-020-backends-healthcheck-monitor.md) (нормализация домена бэка и построение health-URL)

## Контекст

По [ADR-020](ADR-020-backends-healthcheck-monitor.md) домен бэка нормализуется на входе `POST`/`PATCH` к **«голому» host[:port]** (без схемы, без пути; CHECK `domain ~ '^[^\s/]+$'`), а монитор строит health-URL склейкой `GET https://{domain}/health`.

Пользователь вводит домен по-разному: `https://lumorixsite.shop/`, `https://lumorixsite.shop`, `lumorixsite.shop`. Требование — приводить **любую** форму к **одному канону `https://<host>/`** (схема `https://` + host + завершающий `/`), т.е. все три → `https://lumorixsite.shop/`, и хранить/показывать именно эту форму.

**Критический риск.** Если сменить канон на `https://<host>/`, прежняя наивная склейка `https://{domain}/health` даст **битый URL** `https://https://lumorixsite.shop//health`. Построение health-URL обязано измениться синхронно с каноном.

## Решение

### 1. Новый канон домена — `https://<host>/` (амендмент [ADR-020](ADR-020-backends-healthcheck-monitor.md))

Чистая функция нормализации (на входе `POST`/`PATCH`, тестируется без сети):

1. Trim пробелов.
2. Снять схему `http://` / `https://`, если присутствует (регистронезависимо).
3. Снять всё, начиная с первого `/` (путь/query/fragment) — оставить authority `host[:port]`.
4. Привести host к нижнему регистру.
5. **Валидация host** (как прежде): непустой `host` из валидных DNS-меток (буквы/цифры/дефис, точки-разделители) + опциональный `:port` (`1..65535`); без пробелов/`/`. Невалидный → `422 unprocessable` (`details[].field="domain"`).
6. **Собрать канон:** `"https://" + host[:port] + "/"`. Это значение сохраняется в `backends.domain`.

Примеры: `https://lumorixsite.shop/` → `https://lumorixsite.shop/`; `https://lumorixsite.shop` → `https://lumorixsite.shop/`; `lumorixsite.shop` → `https://lumorixsite.shop/`; `HTTP://API.Example.com:8443/path?x=1` → `https://api.example.com:8443/`.

### 2. Построение health-URL из канона (нормативно — анти-двойная-схема)

Монитор бэков **больше НЕ склеивает** `https://{domain}/health`. Так как канон уже содержит схему и завершающий `/`, health-URL строится **дописыванием `health`**:

```
health_url = domain + "health"     # domain = "https://<host>/"  →  "https://<host>/health"
```

`https://lumorixsite.shop/` → `https://lumorixsite.shop/health`. Путь `/health` и схема `https://` остаются фиксированными. Правка — в `app/infra/backend_check.py` (сборка URL) и/или `BackendMonitorService`; функция сборки URL — отдельная чистая функция, тестируется на побайтовое совпадение (в т.ч. анти-регресс `https://https://…`).

### 3. DB-инвариант (CHECK) и миграция

- CHECK-констрейнт `ck_backends_domain` меняется: было `char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^[^\s/]+$'` (голый host) → **`char_length(domain) BETWEEN 1 AND 255 AND domain ~ '^https://[^\s/]+/$'`** (схема `https://` + host без пробелов/`/` + завершающий `/`). Это «свободный» DB-инвариант; полная валидация host — на Pydantic (`422`).
- Миграция **`0020_backends_domain_canon`** (`down_revision = "0019_backends_relations_secrets"`; рабочий `downgrade()`):
  - `upgrade()`: снять старый CHECK; **backfill** существующих строк `UPDATE backends SET domain = 'https://' || lower(domain) || '/'` (для уже-голых доменов); добавить новый CHECK.
  - `downgrade()`: снять новый CHECK; обратный backfill `UPDATE backends SET domain = regexp_replace(regexp_replace(domain, '^https://', ''), '/$', '')`; вернуть старый CHECK.
- **Влияние на данные:** в проде **0 бэков** → backfill фактически no-op, но миграция всё равно переставляет CHECK (иначе новый канон не пройдёт запись). Правило применяется ко всем новым/редактируемым бэкам.

### 4. Отображение

Карточка/detail бэка показывает `domain` как есть — теперь **`https://lumorixsite.shop/`** (моношрифт). Прежняя оговорка «путь в UI не показывается» снимается (путь и не хранится; в UI виден полный канон со схемой и `/`).

## Последствия

- `docs/03-data-model.md`: правило нормализации `backends.domain` → канон `https://<host>/`; CHECK-инвариант; миграция `0020_backends_domain_canon`; описание колонки `domain`.
- `docs/04-api.md`: `BackendCreateRequest`/`UpdateRequest` — `domain` нормализуется к `https://<host>/`; примеры (`api.example.com` → `https://api.example.com/`); `BackendListItem.domain` — канон.
- `docs/modules/backends/README.md`: алгоритм нормализации (канон `https://<host>/`), **построение health-URL `domain + "health"`** (анти-двойная-схема), DoD, примеры.
- `docs/08-design-system.md`: карточка «Бэки» — `domain` показывается как `https://host/`.
- Backend: нормализация домена (сборка канона) + сборка health-URL из канона; миграция `0020`.

## Альтернативы (отклонены)

- **Оставить голый host + чинить только UI.** Отклонено: пользователь требует хранить/канонизировать именно `https://<host>/`.
- **Хранить голый host, показывать `https://host/` в UI.** Отклонено: канон должен быть единым источником (хранение = отображение), иначе рассинхрон ввод↔хранение↔показ.
- **Оставить склейку `https://{domain}/health`.** Отклонено: с новым каноном даёт битый `https://https://…//health`; health-URL строится дописыванием `health` к канону.

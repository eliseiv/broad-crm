# ADR-040 · Расширение сущности «Бэк» связями (`server_id`/`ai_key_id`) и секретами (`api_key`/`admin_api_key`) + reverse-lookup списков бэков в detail сервера/ИИ-ключа

- Статус: accepted
- Дата: 2026-07-09
- Затрагивает: [modules/backends](../modules/backends/README.md), [modules/servers](../modules/servers/README.md), [modules/ai-keys](../modules/ai-keys/README.md), [modules/ui](../modules/ui/README.md)
- Амендмент: [ADR-020](ADR-020-backends-healthcheck-monitor.md) (бэк был «без секрета/связей»), [ADR-035](ADR-035-detail-view-secret-reveal.md) (reveal секретов), [ADR-007](ADR-007-shifrovanie-fernet.md) (Fernet)

## Контекст

Сущность «Бэк» ([ADR-020](ADR-020-backends-healthcheck-monitor.md)) была намеренно минимальной: три публичных поля (`code`/`name`/`domain`), без секрета и без связей с другими сущностями CRM. Новое требование пользователя — обогатить бэк дополнительной **необязательной** информацией и связями:

1. **Фикс 6.** При добавлении/редактировании бэка — доп. сворачиваемая секция «Информация» (все поля необязательны): **Сервер** (на каком сервере CRM лежит бэк), **ИИ-ключ** (какой ИИ-ключ CRM использует бэк), **API KEY**, **ADMIN API KEY** (секретные ключи доступа к самому бэку). Detail-карточка бэка показывает всю доп. информацию.
2. **Фикс 7.** В detail-карточке **сервера** — сворачиваемый список бэков, лежащих на этом сервере (свёрнут по умолчанию, показывает количество; при раскрытии — Код/Название/Домен).
3. **Фикс 8.** В detail-карточке **ИИ-ключа** — сворачиваемый список бэков, использующих этот ключ (аналогично).

Это разворачивает решение [ADR-020](ADR-020-backends-healthcheck-monitor.md) «у бэка нет секрета/связей»: у бэка появляются два опциональных FK и два опциональных секрета. `api_key`/`admin_api_key` — **секреты класса Fernet** (как SSH-пароль/пароль прокси/AI-ключ): дают доступ к API целевого бэка, поэтому шифруются at-rest и не отдаются в обычных ответах (только on-demand reveal по образцу [ADR-035](ADR-035-detail-view-secret-reveal.md)).

## Решение

### 1. Модель `backends` += две связи и два секрета (амендмент [ADR-020](ADR-020-backends-healthcheck-monitor.md))

Миграция **`0019_backends_relations_secrets`** (`down_revision = "0018_teams_mail_group_id"` — текущая голова цепочки; рабочий `downgrade()`), добавляет колонки:

| Колонка | Тип | Ограничения | Смысл |
|---------|-----|-------------|-------|
| `server_id` | `uuid` | `NULL`, FK → `servers(id)` **`ON DELETE SET NULL`** | Сервер CRM, на котором лежит бэк. `NULL` — не задан. Удаление сервера обнуляет связь (бэк не удаляется). |
| `ai_key_id` | `uuid` | `NULL`, FK → `ai_keys(id)` **`ON DELETE SET NULL`** | ИИ-ключ CRM, используемый бэком. `NULL` — не задан. Удаление ключа обнуляет связь. |
| `api_key_encrypted` | `bytea` | `NULL` | Fernet-ciphertext **API KEY** бэка (секрет). `NULL` — не задан. Plaintext не хранится/не логируется. |
| `admin_api_key_encrypted` | `bytea` | `NULL` | Fernet-ciphertext **ADMIN API KEY** бэка (секрет). `NULL` — не задан. |
| `git` | `text` | `NULL` | Ссылка на репозиторий (URL). **НЕ секрет** — plaintext, отдаётся в обычных ответах. `NULL` — не задан. |
| `note` | `text` | `NULL` | Свободные примечания к бэку. **НЕ секрет** — plaintext, отдаётся в обычных ответах. `NULL` — не задан. |

Индексы: `ix_backends_server_id`, `ix_backends_ai_key_id` (под reverse-lookup «бэки сервера»/«бэки ключа» и `ON DELETE SET NULL`). FK корректны: `servers`/`ai_keys` создаются миграциями `0001`/`0002` — раньше `backends` (`0007`). Поля `git`/`note` — обычные `text NULL`, без индексов (не секрет, не связь).

**`ON DELETE SET NULL`** (а не `RESTRICT`/`CASCADE`): бэк — самостоятельная сущность мониторинга; удаление сервера/ключа не должно ни блокироваться связью, ни удалять бэк. Связь просто обнуляется.

### 2. Секреты `api_key`/`admin_api_key` — Fernet at-rest + on-demand reveal (амендмент [ADR-035](ADR-035-detail-view-secret-reveal.md)/[ADR-007](ADR-007-shifrovanie-fernet.md))

- Шифрование — **Fernet** тем же `FERNET_KEY` (`app/infra/crypto.encrypt_secret`), что SSH-пароли/пароли прокси/AI-ключи. Шифрование при `POST`/`PATCH` (только если ключ задан); в БД — только `*_encrypted bytea`.
- В обычных ответах (`BackendListItem`) секреты **не отдаются** — только производные флаги `has_api_key` / `has_admin_api_key` (`= *_encrypted IS NOT NULL`), по образцу `has_password` прокси.
- **Reveal по требованию** (по образцу [ADR-035](ADR-035-detail-view-secret-reveal.md)) — два per-resource GET-эндпоинта:
  - `GET /api/backends/{id}/api-key` → `SecretRevealResponse {value}`.
  - `GET /api/backends/{id}/admin-api-key` → `SecretRevealResponse {value}`.
  - Гейт **`require("backends","edit")`** (супер-админ/`admin` — всегда). Обоснование гейта симметрично [ADR-035](ADR-035-detail-view-secret-reveal.md): держатель `backends:edit` может **перезаписать** ключ через `PATCH`, поэтому раскрытие ему симметрично. Новое право в каталоге **не вводится**.
  - `Cache-Control: no-store`; расшифровка `decrypt_secret` in-memory; аудит-лог `secret_revealed` (`resource_type="backend"`, `resource_id`, `actor`, `at`; значение не логируется).
  - Ошибки: `401`, `403`, `404 backend_not_found`; секрет не задан (`has_*=false`) → **`404 secret_not_set`** (как у прокси без пароля).

### 3. API-контракт `backends` (амендмент [04-api.md](../04-api.md#backends))

- **`BackendCreateRequest` / `BackendUpdateRequest`** += `server_id` (uuid?), `ai_key_id` (uuid?), `api_key` (str?), `admin_api_key` (str?), `git` (str?), `note` (str?) — все **опциональны**. `git`/`note` — не секреты (presence-семантика PATCH та же: отсутствует → не менять; `null`/`""` → очистить; непустая строка → установить).
  - **PATCH — presence-семантика** (`__pydantic_fields_set__`): поле **отсутствует** → не менять; поле присутствует.
    - Для FK (`server_id`/`ai_key_id`): значение `null` → **обнулить** связь; валидный `uuid` → проверить существование (несуществующий → `422 unprocessable`, `details[].field`) и установить.
    - Для секретов (`api_key`/`admin_api_key`): непустая строка → зашифровать и установить; `null`/`""` → **очистить** (в `NULL`).
  - Валидация существования `server_id`/`ai_key_id` (несуществующий → `422 unprocessable`) выполняется и на `POST`, и на `PATCH`.
- **`BackendListItem`** (используется и как detail-объект — отдельного `GET /api/backends/{id}` нет) += `server_id`, `server_name` (для отображения; join `servers.name`; `null` если связи нет), `ai_key_id`, `ai_key_name` (join `ai_keys.name`), `has_api_key`, `has_admin_api_key`, **`git`** (str?), **`note`** (str?). Секреты и шифртексты **не** отдаются; `git`/`note` — не секреты, отдаются как есть.

### 4. Reverse-lookup: списки бэков в detail сервера и ИИ-ключа (фиксы 7/8)

Реализовано по образцу **CRM-команд** ([ADR-030](ADR-030-sms-module-full-merge.md)/[ADR-038](ADR-038-mail-headless-integration.md): `member_count`/`number_count` в списке + ленивый `GET /api/teams/{id}/numbers`/`/mailboxes`):

- **Счётчик в свёрнутом виде — в list-схеме.** `ServerListItem` += `backend_count: int`; `AiKeyListItem` += `backend_count: int` (COUNT бэков с данным `server_id`/`ai_key_id`). Свёрнутая секция detail-карточки показывает «Бэков: N» без дополнительного запроса.
- **Раскрытый список — ленивый per-resource эндпоинт:**
  - `GET /api/servers/{id}/backends` → `{ "backends": [BackendRef] }`, гейт `require("servers","view")`.
  - `GET /api/ai-keys/{id}/backends` → `{ "backends": [BackendRef] }`, гейт `require("ai-keys","view")`.
  - `BackendRef = { code, name, domain }` (только идентификация — секреты/связи не нужны для списка). Сортировка `position ASC, created_at DESC, id`. Пагинации нет (NFR-1).
  - Ошибки: `401`, `403`, `404 server_not_found` / `404 ai_key_not_found`.

**Почему отдельный эндпоинт, а не список в detail-ответе.** Секция свёрнута по умолчанию и грузится лениво при раскрытии — полный список бэков не нужно преднагружать в каждый элемент `GET /api/servers`/`/api/ai-keys`. В list-схему выносится только дешёвый `backend_count` (для «Бэков: N» без раскрытия) — тот же паттерн, что счётчики команд. Эндпоинт гейтится `<page>:view` (просмотр сущности достаточен для просмотра связанных бэков; их поля `code`/`name`/`domain` публичны).

### 5. Frontend (фиксы 6/7/8)

- **Форма бэка (`AddBackendModal` add+edit)** — доп. **сворачиваемая секция «Информация»** (свёрнута по умолчанию, все поля опциональны). **Порядок полей:** **Сервер** (`Select` из `GET /api/servers`, первая опция «Не выбрано»), **ИИ-ключ** (`Select` из `GET /api/ai-keys`, «Не выбрано»), **API KEY** (`Input`, маска + глаз-toggle вводимого значения — как поле ключа `AddAiKeyModal`), **ADMIN API KEY** (то же), **Git** (`Input`, URL), **Примечания** (`Textarea`, **последнее поле**). В edit-режиме FK/`git`/`note` префилятся; поля секретов пустые с подсказкой «Оставьте пустым, чтобы не менять» (очистка секрета через пустое поле в UI **не** выполняется — см. [TD-035](../100-known-tech-debt.md); FK очищается выбором «Не выбрано»; `git`/`note` очищаются очисткой поля).
- **`BackendDetailModal`** — Код/Название/Домен + **Сервер** (`server_name`/«—») + **ИИ-ключ** (`ai_key_name`/«—») + **API KEY** / **ADMIN API KEY** (`••••` + глаз-reveal под `backends:edit`, если `has_*`; иначе «—») + **Git** (`git`/«—», ссылка) + **Примечания** (`note`/«—»).
- **`ServerDetailModal` / `AiKeyDetailModal`** — снизу сворачиваемая секция «Бэки» (свёрнута = «Бэков: N» по `backend_count`; раскрытие → ленивый reverse-lookup эндпоинт → список Код/Название/Домен; состояния loading/empty «Бэков нет»/error).

## Последствия

- `docs/03-data-model.md`: `backends` += `server_id`/`ai_key_id`/`api_key_encrypted`/`admin_api_key_encrypted`, ER-диаграмма (FK `servers`/`ai_keys` → `backends`), обновление прозы «независимые таблицы», миграция `0019`, шифрование двух секретов.
- `docs/04-api.md`: `BackendCreateRequest`/`UpdateRequest`/`BackendListItem` расширены (`server_id`/`ai_key_id`/`api_key`/`admin_api_key`/`git`/`note`); два reveal-эндпоинта бэка; reverse-lookup `GET /api/servers/{id}/backends` и `GET /api/ai-keys/{id}/backends` + схема `BackendRef`; `ServerListItem`/`AiKeyListItem` += `backend_count`; таблица reveal-секретов += две строки бэка. `git`/`note` — не секреты, в обычных ответах.
- `docs/05-security.md`: раздел «Защита API-ключей бэка» (Fernet, reveal-гейт `backends:edit`, аудит); reveal-секция += бэк; таблица управления секретами + модель угроз.
- `docs/08-design-system.md`: секция «Информация» в форме бэка; detail бэка (связи + reveal ключей); сворачиваемые «Бэки»-секции в detail сервера/ИИ-ключа.
- Module READMEs: backends/servers/ai-keys/ui.
- Новый долг [TD-035](../100-known-tech-debt.md): очистка секретов бэка через UI (поле «пусто = не менять», явной кнопки «очистить» нет).

## Альтернативы (отклонены)

- **`api_key`/`admin_api_key` как публичные поля** (не секреты). Отклонено: ключи дают доступ к API бэка — класс секрета, симметрично AI-ключам/паролям прокси; Fernet + reveal под `edit` обязательны.
- **Полный список бэков в detail-ответе сервера/ключа** (вместо `backend_count` + ленивый эндпоинт). Отклонено: секция свёрнута по умолчанию — преднагрузка списка в каждый элемент `GET /api/servers`/`/api/ai-keys` избыточна; счётчик дёшев, список ленив (паттерн команд [ADR-030](ADR-030-sms-module-full-merge.md)).
- **`ON DELETE RESTRICT`/`CASCADE` на FK.** Отклонено: удаление сервера/ключа не должно блокироваться связью с бэком (`RESTRICT`) и не должно удалять бэк (`CASCADE`); `SET NULL` обнуляет связь, бэк живёт.
- **Отдельный reveal-эндпоинт под `delete`/admin-only.** Отклонено ради единообразия с [ADR-035](ADR-035-detail-view-secret-reveal.md) (гейт `edit`).

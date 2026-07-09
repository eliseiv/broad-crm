# ADR-035 · Read-only detail-view с карандашом + on-demand reveal секретов (Серверы / Прокси / ИИ-ключи / Бэки)

- Статус: accepted
- Дата: 2026-07-09
- Затрагивает: [modules/servers](../modules/servers/README.md), [modules/proxies](../modules/proxies/README.md), [modules/ai-keys](../modules/ai-keys/README.md), [modules/backends](../modules/backends/README.md)
- Связан с / амендмент: [ADR-007](ADR-007-shifrovanie-fernet.md), [ADR-010](ADR-010-ai-key-monitor-vnutri-backend.md), [ADR-019](ADR-019-proxies-availability-monitor.md), [ADR-011](ADR-011-poryadok-blokov-server-side-dnd-kit.md) (клик по карточке)

## Контекст

На карточных страницах «Серверы», «Прокси», «ИИ-ключи», «Бэки» **клик по карточке открывал СРАЗУ edit-модалку** (`Add<Entity>Modal mode='edit'`, [ADR-011](ADR-011-poryadok-blokov-server-side-dnd-kit.md)). Требование пользователя: клик должен открывать **read-only окно с данными сущности**, а переход в редактирование — по **карандашу** вверху справа. Такой паттерн уже есть на `/teams` (detail-панель → карандаш → `AddTeamModal`, [ADR-030](ADR-030-sms-module-full-merge.md)).

Дополнительно секреты сущностей были **write-only** и не отдавались вообще: сервер `ssh_password` и прокси `password` — только флаг/шифртекст; ИИ-ключ — только `key_masked`. Требование: в detail-view секрет показан `****` с кнопкой-глазом «показать», раскрывающей значение **по требованию**. Это осознанный **разворот** принципа «секрет не возвращается ни в одном ответе API» — контролируемое раскрытие по явному действию под правом.

Задача security-критична (раскрытие секретов at-rest), поэтому механизм спроектирован консервативно.

## Решение

### 1. Единый паттерн detail-view для карточных страниц

- **Клик по карточке** (короткий клик, разведение жестов drag/click из [ADR-011](ADR-011-poryadok-blokov-server-side-dnd-kit.md) сохраняется) открывает **read-only** `<Entity>DetailModal` (Radix Dialog) — **вместо** прежней edit-модалки. Доступно держателю `<page>:view`.
- **Карандаш `Pencil`** вверху справа (гейт `<page>:edit`, `stopPropagation`) → закрывает detail и открывает **существующую** edit-модалку (`Add<Entity>Modal mode='edit'`, форма не меняется). Без `edit` карандаш не рендерится (detail-view остаётся чисто просмотровым).
- Кнопка **«Удалить»** на карточке — по-прежнему `stopPropagation` (detail не открывает).
- Это **UI-разворот идиомы `/teams`** на карточные страницы: там list-аккордеон, здесь modal-вариант того же паттерна (клик → read-only detail → карандаш → edit).

**Поля detail-view (read-only) по сущности** (из текущих response-схем):

| Сущность | Поля | Секрет + reveal |
|----------|------|-----------------|
| Сервер | Название (`name`), IP (`ip`), Пользователь (`ssh_user`) | **Пароль** — `****` + глаз → `GET /api/servers/{id}/ssh-password` |
| Прокси | Название (`name`), Тип (`proxy_type`), Хост (`host`), Порт (`port`), Логин (`username`) | **Пароль** (если `has_password`) — `****` + глаз → `GET /api/proxies/{id}/password`; иначе «—» (глаза нет) |
| ИИ-ключ | Название (`name`), Провайдер (`provider`), Ключ (`key_masked`) | **Ключ** — `key_masked` + глаз → `GET /api/ai-keys/{id}/key` (полное значение) |
| Бэк | Код (`code`), Название (`name`), Домен (`domain`) | **секрета нет** — reveal не применяется |

Для сервера `ssh_user` добавляется в read-контракт (`ServerListItem += ssh_user`); колонка уже есть в БД, миграции нет. `ssh_user` — **не секрет** (по аналогии с `username` прокси, [ADR-019](ADR-019-proxies-availability-monitor.md)).

### 2. On-demand reveal-эндпоинты (по требованию, секрет не преднагружается)

Секрет **никогда** не отдаётся в общих list/detail-ответах — только выделенным per-resource эндпоинтом по явному действию:

- `GET /api/servers/{id}/ssh-password` — расшифровка `ssh_password_encrypted`.
- `GET /api/proxies/{id}/password` — расшифровка `password_encrypted`.
- `GET /api/ai-keys/{id}/key` — расшифровка `key_encrypted` (полный ключ).

**Контракт (единый):**
- **Response 200** — `SecretRevealResponse`: `{ "value": "<plaintext>" }`.
- **Заголовок ответа `Cache-Control: no-store`** (обязательно) — секрет не кэшируется прокси/браузером.
- Расшифровка — `app/infra/crypto.decrypt_secret` в памяти обработчика непосредственно перед формированием ответа; plaintext не логируется.
- **Ошибки:** `401 unauthorized`; `403 forbidden` (нет права); `404 <resource>_not_found`; для прокси без пароля — `404 secret_not_set`.
- HTTP-метод **GET** допустим: секрет — в теле ответа, **не** в URL (в URL только `id`), поэтому в access-логах секрета нет; `no-store` исключает кэш.

### 3. Гейтинг: право `<page>:edit`

Reveal каждого секрета гейтится **`require("<page>", "edit")`** соответствующей страницы: `servers:edit` / `proxies:edit` / `ai-keys:edit`. Супер-админ и роль `admin` (полный каталог) — всегда.

- **Обоснование:** `edit` — право управления сущностью. Для прокси/ИИ-ключей держатель `edit` уже может **перезаписать** секрет (re-encrypt через `PATCH`), то есть управляет секретом — раскрытие ему симметрично. Для серверов `edit` (по [modules/servers](../modules/servers/README.md) — только `name`) секрет не перезаписывает, но `servers:edit` означает доверенное управление серверами, и оператору сервера легитимно нужен введённый им SSH-пароль.
- **Новое право/действие в каталоге НЕ вводится** (NFR-1): переиспользуется существующее `edit`. Каталог прав (`app/domain/permissions.py::CATALOG`) не меняется.
- Строже (`delete`/admin-only) — отклонено ради простоты и единообразия; вынесено на подтверждение пользователя ([Q-SEC-5](../99-open-questions.md)).

### 4. Аудит reveal (без утечки секрета)

Каждый **успешный** reveal обязан порождать структурированную запись лога `secret_revealed` (structlog) с полями: `actor` (`username`/`user_id` принципала), `resource_type` (`server`/`proxy`/`ai_key`), `resource_id`, `at` (timestamp). **Само значение секрета в лог НЕ пишется** (structlog-фильтр секретов). Это лёгкий аудит через логи; персистентная аудит-таблица действий остаётся [TD-001](../100-known-tech-debt.md).

### 5. Frontend-поведение

- Detail-view показывает секрет как `****`. Кнопка-глаз рядом → **on-demand** запрос к reveal-эндпоинту → показ значения (+опц. copy-to-clipboard).
- Раскрытое значение хранится **только в локальном стейте компонента** detail-модалки; **не** кладётся в TanStack Query-кэш / Zustand; скрывается по повторному клику и **сбрасывается при закрытии** модалки.
- Кнопка-глаз рендерится только при `<page>:edit` (для прокси — дополнительно только при `has_password`). Без права — секрет остаётся `****` без возможности раскрытия.

### 6. Editable-scope (что правит карандаш) — зафиксировано

- **Сервер — карандаш правит ТОЛЬКО `name`** (текущий контракт `PATCH /api/servers/{id}`). `ip`/`ssh_user`/`ssh_password` **неизменяемы** через API: их смена требовала бы репровижининга агента (вне scope Этапа 1, [modules/servers](../modules/servers/README.md#out-of-scope)). Detail-view показывает `ip`/`ssh_user`/пароль (просмотр + reveal), но edit-модалка — только имя. Полноценное редактирование server-кредов — на подтверждение ([Q-UI-4](../99-open-questions.md)).
- **Прокси — карандаш правит `name`/`proxy_type`/`host`/`port`/`username`/`password`** (существующий полный контракт `PATCH /api/proxies/{id}`). Секрет пере-вводится (пустой ⇒ не менять). Без изменений.
- **ИИ-ключ — карандаш правит `name`/`provider`/`key`** (существующий контракт). Без изменений.
- **Бэк — карандаш правит `code`/`name`/`domain`** (существующий контракт). Без изменений.

## Последствия

- `docs/04-api.md`: `ServerListItem += ssh_user`; три reveal-эндпоинта; схема `SecretRevealResponse`; код ошибки `404 secret_not_set`.
- `docs/05-security.md`: раздел «Reveal секретов по требованию» (гейт `edit`, `no-store`, аудит, decrypt in-memory, frontend memory); в разделах SSH/AI/proxy-секретов — исключение «кроме выделенного reveal-эндпоинта под `<page>:edit`»; модель угроз.
- `docs/08-design-system.md`: единый паттерн detail-view карточных страниц; правки страниц Серверы/Прокси/ИИ-ключи/Бэки (клик → detail, глаз-reveal); словарь строк.
- Module READMEs: servers/proxies/ai-keys/backends — reveal-эндпоинт, detail-view, editable-scope.
- Backend: три reveal-handler'а под `require(page,"edit")`, `decrypt_secret`, `no-store`, лог `secret_revealed`; `ServerListItem += ssh_user`. Frontend: `<Entity>DetailModal` ×4, глаз-reveal ×3.

## Альтернативы (отклонены)

- **Отдавать секрет прямо в detail-ответе** (list/detail) — отклонено: секрет преднагружался бы и попадал в кэш/логи; принцип «по требованию» нарушен.
- **Метод POST для reveal** — не требуется: секрет в теле ответа (не в URL), `Cache-Control: no-store` исключает кэш; GET проще и RESTful для «прочитать секрет».
- **Общий эндпоинт `GET /api/secrets/{type}/{id}`** — отклонено: per-resource эндпоинты явнее гейтятся и совпадают с существующей нарезкой роутеров.
- **Новое право `<page>:reveal`** — отклонено (NFR-1): переиспользуется `edit`, каталог прав не растёт.

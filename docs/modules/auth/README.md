# Модуль `auth` — Аутентификация

Статус: `spec-ready` · Исполнители: backend, frontend

## Scope
Двухшаговый вход администратора и защита API через JWT. Учётка — только из `.env` ([ADR-008](../../adr/ADR-008-admin-iz-env.md)). Поток и решение — [ADR-002](../../adr/ADR-002-dvuhshagovyy-auth.md).

## Out of scope
Многопользовательский режим, RBAC, refresh-токены, OAuth/SSO, UI смены пароля.

## Backend — ТЗ

### Endpoints (контракт — [04-api.md](../../04-api.md#auth))
- `POST /api/auth/login {username,password}` → `200 {access_token,token_type,expires_in}` | `401 invalid_credentials` | `400 validation_error` | `429 rate_limited`.
- `GET /api/auth/me` (JWT) → `200 {username}` | `401 unauthorized`.

### Требования
1. Креды из настроек (`ADMIN_USER`, `ADMIN_PASSWORD`) через pydantic-settings.
2. Сравнение логина и пароля — `secrets.compare_digest` (constant-time), оба сравнения выполняются всегда (без раннего возврата), чтобы не было timing-разницы.
3. JWT: HS256, `JWT_SECRET`, claims `sub`, `iat`, `exp`, `type:"access"`, TTL `JWT_EXPIRES_MIN` (1440 мин / 24 ч, [05-security.md](../../05-security.md#jwt)).
4. FastAPI-dependency `get_current_user`, защищающая все роутеры, кроме `/api/auth/login` и `/api/health`. Невалидный/просроченный токен → `401 unauthorized`.
5. Rate-limit на `/api/auth/login`: по IP, 10 попыток / 5 мин (in-memory на Этапе 1), превышение → `429 rate_limited`.
6. Единое сообщение об ошибке для неверного логина и/или пароля.
7. Логи аутентификации — без паролей/токенов (structlog маскирование).

## Frontend — ТЗ
1. Роуты: `/login` (двухшаговый), `/servers` (защищён).
2. Шаг 1 — поле «Логин» + «Далее» (клиентский переход, без запроса). Шаг 2 — показ логина + «назад», поле «Пароль» + «Войти» → `POST /api/auth/login`.
3. Хранение access-токена — в памяти (Zustand); допустимо `sessionStorage` для переживания перезагрузки. НЕ `localStorage` ([05-security.md](../../05-security.md)).
4. Все запросы к `/api/*` шлют `Authorization: Bearer`. На `401` — сброс сессии и редирект на `/login`.
5. Ошибка входа → единое сообщение «Неверный логин или пароль», без раскрытия деталей; shake-анимация (учитывать `prefers-reduced-motion`).
6. UI экрана входа — [08-design-system.md](../../08-design-system.md#экран-входа-двухшаговый).

## DoD
- [ ] Endpoints соответствуют [04-api.md](../../04-api.md).
- [ ] Тесты auth (unit+интеграция) из [06-testing-strategy.md](../../06-testing-strategy.md) зелёные, coverage ≥90 %.
- [ ] Нет секретов в логах.
- [ ] Двухшаговый UI работает, защита роутов и обработка 401.

## Changelog
- 2026-06-28: спецификация создана (architect, bootstrap).
- 2026-07-07: TTL JWT увеличен с 60 до **1440 мин (24 ч)** по запросу пользователя (`JWT_EXPIRES_MIN`); обоснование и trade-off — [05-security.md](../../05-security.md#jwt).

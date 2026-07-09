import type { Zone } from '@/lib/zones';

/** Статус провижининга сервера (04-api.md). */
export type ProvisionStatus = 'pending' | 'installing' | 'online' | 'error';

/** Деталь метрики. value/total могут быть null (например, CPU unit:"cores"). */
export interface MetricDetail {
  value: number | null;
  total: number | null;
  unit: string;
}

export interface Metric {
  /** null при offline/недоступной метрике (04-api.md graceful degradation). */
  usage_percent: number | null;
  /** null когда usage_percent недоступен. */
  zone: Zone | null;
  detail: MetricDetail;
}

export interface ServerMetrics {
  cpu: Metric;
  ram: Metric;
  ssd: Metric;
}

export interface Server {
  id: string;
  name: string;
  ip: string;
  exporter_port: number;
  provision_status: ProvisionStatus;
  /** Порядок карточки (drag-and-drop). Меньше = выше. 04-api.md. */
  position: number;
  online: boolean;
  uptime_seconds: number | null;
  last_updated: string | null;
  metrics: ServerMetrics | null;
}

export interface ServersListResponse {
  items: Server[];
}

export interface LoginRequest {
  username: string;
  password: string;
}

/**
 * Ответ POST /api/auth/login — дискриминированный по `password_setup_required`
 * (04-api.md схема `LoginResponse`, ADR-025). Успех (`false`) несёт обычный
 * access-токен; «требуется установка» (`true`) — limited-scope setup-токен,
 * принимаемый ТОЛЬКО POST /api/auth/set-password (модель «открытого первого входа»).
 */
export interface LoginSuccessResponse {
  password_setup_required: false;
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginSetupRequiredResponse {
  password_setup_required: true;
  setup_token: string;
  token_type: string;
  expires_in: number;
}

export type LoginResponse = LoginSuccessResponse | LoginSetupRequiredResponse;

/**
 * Тело POST /api/auth/set-password (04-api.md схема `SetPasswordRequest`, ADR-025).
 * `password` 8–128. Auth — Bearer setup-token из ответа login. Ответ — `LoginSuccessResponse`
 * (пользователь сразу залогинен обычным access-токеном).
 */
export interface SetPasswordRequest {
  password: string;
}

/**
 * Ответ GET /api/auth/me (04-api.md, схема `MeResponse`). Профиль + права
 * текущего принципала для UI-гейтинга. `role` для супер-админа — "admin".
 * `permissions` — `{ "<page>": ["<action>", ...] }` (для супер-админа — полный каталог).
 * Безопасность обеспечивает сервер (403); гейтинг — только UX.
 */
export interface MeResponse {
  username: string;
  role: string;
  is_superadmin: boolean;
  permissions: PermissionsMap;
}

export interface CreateServerRequest {
  name: string;
  ip: string;
  ssh_user: string;
  ssh_password: string;
}

export interface CreateServerResponse {
  id: string;
  name: string;
  ip: string;
  exporter_port: number;
  provision_status: ProvisionStatus;
  position: number;
}

/** Тело PATCH /api/servers/{id} — на Этапе 1 меняется только name (04-api.md). */
export interface UpdateServerRequest {
  name: string;
}

/** Ответ PATCH /api/servers/{id} — summary-объект сервера без метрик (04-api.md). */
export interface UpdateServerResponse {
  id: string;
  name: string;
  ip: string;
  exporter_port: number;
  provision_status: ProvisionStatus;
  position: number;
  created_at: string;
  updated_at: string;
}

/** Тело PATCH /api/servers/order — полная перестановка (04-api.md). */
export interface ReorderServersRequest {
  ids: string[];
}

export interface StatusResponse {
  id: string;
  provision_status: ProvisionStatus;
  error_message: string | null;
  updated_at: string;
}

// --- AI Keys (04-api.md «AI Keys», modules/ai-keys) ---

/** Провайдер AI-ключа (04-api.md). */
export type AiProvider = 'openai' | 'anthropic';

/** Статус проверки валидности AI-ключа (04-api.md). */
export type AiKeyStatus = 'pending' | 'working' | 'error';

/** Элемент списка AI-ключей. Полный ключ не возвращается — только маска. */
export interface AiKey {
  id: string;
  name: string;
  provider: AiProvider;
  /** Маска вида «sk-p…bA3T» (04-api.md, key_masked). */
  key_masked: string;
  check_status: AiKeyStatus;
  /** Рус. причина при check_status='error', иначе null. */
  error_message: string | null;
  /** Порядок карточки внутри провайдер-группы (drag-and-drop). Меньше = выше. 04-api.md. */
  position: number;
  last_checked_at: string | null;
  created_at: string;
}

export interface AiKeysListResponse {
  items: AiKey[];
}

/**
 * Тело POST /api/ai-keys. Поле ключа на проводе — `key` (04-api.md, source of truth),
 * NOT `api_key`. См. prompt_issues в отчёте frontend.
 */
export interface CreateAiKeyRequest {
  name: string;
  provider: AiProvider;
  key: string;
}

export interface CreateAiKeyResponse {
  id: string;
  name: string;
  provider: AiProvider;
  check_status: AiKeyStatus;
  position: number;
}

/**
 * Тело PATCH /api/ai-keys/{id} (04-api.md). Все поля опциональны — передаются
 * только изменяемые. Пустой/отсутствующий `key` = «не менять ключ» (секрет не префилится).
 */
export interface UpdateAiKeyRequest {
  name?: string;
  provider?: AiProvider;
  key?: string;
}

/** Тело PATCH /api/ai-keys/order — перестановка внутри провайдер-группы (04-api.md). */
export interface ReorderAiKeysRequest {
  provider: AiProvider;
  ids: string[];
}

/** Лёгкий статус проверки ключа (04-api.md, GET /api/ai-keys/{id}/status). */
export interface AiKeyStatusResponse {
  id: string;
  check_status: AiKeyStatus;
  error_message: string | null;
  last_checked_at: string | null;
}

// --- Mail (04-api.md «Mail», modules/mail) ---

/** Почтовый аккаунт-получатель (04-api.md, MailAccount). */
export interface MailAccount {
  id: number;
  email: string;
  display_name: string | null;
}

/** Тег письма (04-api.md, MailTag). `color` — HEX для бейджа. */
export interface MailTag {
  id: number;
  name: string;
  color: string;
}

/**
 * Письмо ленты «Почты» (04-api.md, MailMessage). Read-through-прокси: поля
 * приходят из внешнего сервиса как есть; `body_html` рендерится ТОЛЬКО в
 * sandbox-iframe (modules/mail «Изоляция HTML-тела»).
 */
export interface MailMessage {
  id: number;
  subject: string | null;
  internal_date: string;
  from_addr: string;
  from_name: string | null;
  to_addrs: string;
  cc_addrs: string | null;
  mail_account: MailAccount;
  body_text: string;
  body_html: string | null;
  body_present: boolean;
  body_truncated: boolean;
  tags: MailTag[];
}

/** Режим пагинации ленты писем (04-api.md, GET /api/mail/messages `order`). */
export type MailOrder = 'asc' | 'desc';

/**
 * Ответ GET /api/mail/messages (04-api.md, MailListResponse). Единая схема для обоих
 * режимов; заполнен курсор запрошенного режима, второй — `null`.
 * - `next_since_id` — asc-режим: максимальный `id` батча (курсор `since_id` вперёд);
 *   `null` для пустого батча. В desc-режиме всегда `null`.
 * - `next_before_id` — desc-режим (основной для страницы): минимальный `id` батча
 *   (курсор `before_id` — догрузка более старых); `null`, если старее нет или батч пуст.
 *   В asc-режиме всегда `null`.
 * - `has_more` — есть ли ещё письма в запрошенном направлении.
 */
export interface MailListResponse {
  messages: MailMessage[];
  next_since_id: number | null;
  next_before_id: number | null;
  has_more: boolean;
}

/**
 * Тело POST /api/mail/messages/{id}/reply (04-api.md, MailReplyRequest).
 * `body` обязательный непустой; `to`/`cc`/`subject` опциональны.
 */
export interface MailReplyRequest {
  to?: string[];
  cc?: string[] | null;
  subject?: string;
  body: string;
}

/** Ответ POST /api/mail/messages/{id}/reply (04-api.md, MailReplyResponse). */
export interface MailReplyResponse {
  sent_id: number;
  smtp_message_id: string;
}

/**
 * Команда внешнего почтового сервиса (04-api.md, MailTeam; external ADR-0037).
 * Это `groups` внешнего сервиса. Команда ≠ тег (MailTag).
 */
export interface MailTeam {
  id: number;
  name: string;
}

/** Ответ GET /api/mail/teams (04-api.md, MailTeamsResponse). */
export interface MailTeamsResponse {
  teams: MailTeam[];
}

/**
 * Почтовый ящик внешнего сервиса (04-api.md, MailMailbox; external ADR-0037).
 * `id` используется как `mail_account_id` в серверном фильтре ленты;
 * привязка к команде — через `group_id`; `is_active` — статус ящика (для дашборда).
 */
export interface MailMailbox {
  id: number;
  email: string;
  display_name: string | null;
  group_id: number | null;
  is_active: boolean;
}

/** Ответ GET /api/mail/mailboxes (04-api.md, MailMailboxesResponse). */
export interface MailMailboxesResponse {
  mailboxes: MailMailbox[];
}

// --- Proxies (04-api.md «Proxies», modules/proxies) ---

/** Тип прокси (04-api.md, proxy_type). */
export type ProxyType = 'http' | 'https' | 'socks5';

/** Статус проверки доступности прокси (04-api.md, check_status). */
export type ProxyCheckStatus = 'pending' | 'working' | 'error';

/**
 * Элемент списка прокси (04-api.md, схема `ProxyListItem`). Пароль не возвращается
 * никогда — вместо него флаг `has_password`. `username` (логин) — не секрет.
 */
export interface Proxy {
  id: string;
  name: string;
  proxy_type: ProxyType;
  host: string;
  port: number;
  /** Логин прокси (не секрет); null — без авторизации. */
  username: string | null;
  /** Производное `password_encrypted IS NOT NULL`. Сам пароль не возвращается. */
  has_password: boolean;
  check_status: ProxyCheckStatus;
  /** Рус. причина при check_status='error', иначе null. */
  error_message: string | null;
  /** Порядок карточки в едином списке (drag-and-drop). Меньше = выше. 04-api.md. */
  position: number;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/proxies (04-api.md, схема `ProxyListResponse`). */
export interface ProxyListResponse {
  items: Proxy[];
}

/**
 * Тело POST /api/proxies (04-api.md, `ProxyCreateRequest`). `username`/`password`
 * опциональны; отсутствие/пустое → без логина/пароля. Пароль шифруется, в ответе не возвращается.
 */
export interface CreateProxyRequest {
  name: string;
  proxy_type: ProxyType;
  host: string;
  port: number;
  username?: string;
  password?: string;
}

/**
 * Тело PATCH /api/proxies/{id} (04-api.md, `ProxyUpdateRequest`). Все поля опциональны —
 * передаются только изменяемые. Семантика пароля (нормативно): поле не передано → не менять;
 * `null`/`""` → очистить (убрать пароль); непустая строка → заменить (re-encrypt).
 * Для `username`: не передано → не менять; `null`/`""` → убрать логин; значение → установить.
 */
export interface UpdateProxyRequest {
  name?: string;
  proxy_type?: ProxyType;
  host?: string;
  port?: number;
  username?: string | null;
  password?: string | null;
}

/** Тело PATCH /api/proxies/order — полная перестановка единого списка (04-api.md). */
export interface ReorderProxiesRequest {
  ids: string[];
}

/** Лёгкий статус проверки прокси (04-api.md, `ProxyStatusResponse`). */
export interface ProxyStatusResponse {
  id: string;
  check_status: ProxyCheckStatus;
  error_message: string | null;
  last_checked_at: string | null;
}

/** Статус проверки доступности бэка (04-api.md, check_status). */
export type BackendCheckStatus = 'pending' | 'working' | 'error';

/**
 * Элемент списка бэков (04-api.md, схема `BackendListItem`). Секрета у сущности нет —
 * все поля (`code`/`name`/`domain`) публичны и возвращаются как есть. `code` уникален.
 */
export interface Backend {
  id: string;
  /** Бизнес-код сервиса (1–64), уникален по реестру. */
  code: string;
  name: string;
  /** Нормализованный домен (`host[:port]`, без схемы/пути). Проверка — `https://{domain}/health`. */
  domain: string;
  check_status: BackendCheckStatus;
  /** Рус. причина при check_status='error', иначе null. */
  error_message: string | null;
  /** Порядок карточки в едином списке (drag-and-drop). Меньше = выше. 04-api.md. */
  position: number;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/backends (04-api.md, схема `BackendListResponse`). */
export interface BackendListResponse {
  items: Backend[];
}

/**
 * Тело POST /api/backends (04-api.md, `BackendCreateRequest`). `code` уникален —
 * дубликат → 409 backend_code_taken. `domain` принимается с/без схемы, нормализуется на backend.
 */
export interface CreateBackendRequest {
  code: string;
  name: string;
  domain: string;
}

/**
 * Тело PATCH /api/backends/{id} (04-api.md, `BackendUpdateRequest`). Все поля опциональны —
 * передаются только изменяемые (семантика exclude_unset). Смена `code` на занятый другим
 * бэком → 409 backend_code_taken. Смена `domain` → повторная проверка (check_status='pending').
 */
export interface UpdateBackendRequest {
  code?: string;
  name?: string;
  domain?: string;
}

/** Тело PATCH /api/backends/order — полная перестановка единого списка (04-api.md). */
export interface ReorderBackendsRequest {
  ids: string[];
}

/** Лёгкий статус проверки бэка (04-api.md, `BackendStatusResponse`). */
export interface BackendStatusResponse {
  id: string;
  check_status: BackendCheckStatus;
  error_message: string | null;
  last_checked_at: string | null;
}

// --- RBAC: Permissions / Users / Roles (04-api.md «Permissions»/«Users»/«Roles», ADR-021) ---

/** Матрица прав `{ "<page>": ["<action>", ...] }` (04-api.md). Ключи/действия — из каталога. */
export type PermissionsMap = Record<string, string[]>;

/** Страница каталога прав (04-api.md, `PermissionCatalogPage`). */
export interface PermissionCatalogPage {
  page: string;
  actions: string[];
}

/**
 * Ответ GET /api/permissions/catalog (04-api.md, `PermissionsCatalogResponse`).
 * `pages` упорядочен — порядок = порядок строк матрицы прав в UI. Страница `users`
 * в каталог не входит (гейтится require_admin, не матрицей).
 */
export interface PermissionsCatalogResponse {
  pages: PermissionCatalogPage[];
}

/**
 * Ссылка на CRM-команду пользователя (04-api.md, `TeamRef`). Денормализовано
 * для группировки списка «Пользователи» по командам. Это CRM-команды ([Teams]),
 * НЕ mail-«команды» (MailTeam).
 */
export interface TeamRef {
  id: string;
  name: string;
}

/**
 * Элемент списка пользователей (04-api.md, схема `UserListItem`). Пароль
 * (`password`/`password_hash`) в ответах отсутствует всегда — только на вход.
 */
export interface UserListItem {
  id: string;
  username: string;
  /**
   * Опциональный телеграм-ник (ADR-025; заменяет прежний `email`); `null` — не
   * задан. Нормализован (без `@`, lower-case). Второй идентификатор входа.
   */
  telegram: string | null;
  /**
   * Производное `password_hash IS NOT NULL` (ADR-025). `false` — беспарольный
   * пользователь (ещё не прошёл «открытый первый вход»). Сам пароль не возвращается.
   */
  has_password: boolean;
  role_id: string;
  /** Имя роли (денормализовано для UI-списка). */
  role_name: string;
  is_active: boolean;
  /**
   * Производный тристатус (ADR-028): `"inactive"` (`is_active==false`);
   * `"pending"` (активен, но ещё ни разу не входил, `first_login_at IS NULL`);
   * `"active"` (активен И входил хотя бы раз). Приоритет `is_active`. UI-лейблы:
   * «Неактивен» / «Ожидает входа» / «Активен». Метка `first_login_at` наружу не отдаётся.
   */
  status: 'pending' | 'active' | 'inactive';
  /** CRM-команды пользователя (может быть пустым) — для группировки списка. */
  teams: TeamRef[];
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/users (04-api.md, схема `UserListResponse`). */
export interface UserListResponse {
  items: UserListItem[];
}

/**
 * Тело POST /api/users (04-api.md, `UserCreateRequest`; ADR-025). `username` 1–64
 * (кириллица допускается), `role_id` — существующая роль. `password` **опционален**
 * (8–128 при наличии; отсутствие → беспарольный пользователь «открытого первого входа»).
 * `telegram` опционален (формат телеграм-ника, нормализуется/уникален на сервере);
 * `team_ids` — опц. набор CRM-команд.
 */
export interface UserCreateRequest {
  username: string;
  telegram?: string;
  password?: string;
  role_id: string;
  team_ids?: string[];
}

/**
 * Тело PATCH /api/users/{id} (04-api.md, `UserUpdateRequest`; ADR-025). `username`
 * не редактируется. Все поля опциональны — передаются только изменяемые
 * (exclude_unset). `password`: не передан → не менять; непустой (8–128) → сброс/установка.
 * `telegram`: не передан → не менять; `null`/`""` → убрать телеграм. `team_ids`
 * (если передан) полностью заменяет набор CRM-команд пользователя.
 */
export interface UserUpdateRequest {
  telegram?: string | null;
  role_id?: string;
  is_active?: boolean;
  password?: string;
  team_ids?: string[];
}

/**
 * Элемент списка ролей (04-api.md, схема `RoleListItem`). `admin` —
 * зарезервированное имя (доступ к «Пользователям»).
 */
export interface RoleListItem {
  id: string;
  name: string;
  permissions: PermissionsMap;
  /** Число пользователей с этой ролью (ADR-022). `≥1` → удаление запрещено. */
  user_count: number;
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/roles (04-api.md, схема `RoleListResponse`). */
export interface RoleListResponse {
  items: RoleListItem[];
}

/**
 * Тело POST /api/roles (04-api.md, `RoleCreateRequest`). `name` 1–64
 * (уникально → 409 role_name_taken). `permissions` валидируется против каталога.
 */
export interface RoleCreateRequest {
  name: string;
  permissions: PermissionsMap;
}

/**
 * Тело PATCH /api/roles/{id} (04-api.md, `RoleUpdateRequest`). Все поля
 * опциональны. `permissions` (если передан) полностью заменяет матрицу прав.
 */
export interface RoleUpdateRequest {
  name?: string;
  permissions?: PermissionsMap;
}

// --- Teams (04-api.md «Teams», modules/teams, ADR-022) ---

/**
 * Участник CRM-команды (04-api.md, `TeamMember`). Отдаётся в списке команд
 * для prefill формы редактирования — отдельного GET /api/teams/{id} нет.
 */
export interface TeamMember {
  id: string;
  username: string;
}

/**
 * Элемент списка CRM-команд (04-api.md, схема `TeamListItem`). CRM-команды —
 * отдельная сущность (uuid, БД CRM, лидер+участники), НЕ mail-«команды» (MailTeam).
 */
export interface TeamListItem {
  id: string;
  /** Название (уникально). Дубликат → 409 team_name_taken. */
  name: string;
  /** ID лидера; `null` — команда без лидера (ADR-026). */
  leader_id: string | null;
  /** Логин лидера (денормализовано, JOIN users); `null` — без лидера (ADR-026). */
  leader_username: string | null;
  /** Число участников (= members.length; включает лидера, если он есть). Может быть 0. */
  member_count: number;
  /**
   * Число SMS-номеров команды (04-api.md, COUNT sms_phone_numbers; ADR-030). Может
   * быть 0. Денормализованный агрегат для чипа «N номеров» на карточке команды;
   * список номеров — GET /api/teams/{id}/numbers.
   */
  number_count: number;
  /** Участники команды (включая лидера, если задан; может быть пустым). */
  members: TeamMember[];
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/teams (04-api.md, схема `TeamListResponse`). */
export interface TeamListResponse {
  items: TeamListItem[];
}

/**
 * Тело POST /api/teams (04-api.md, `TeamCreateRequest`; ADR-026). Лидер и участники —
 * **опциональны** (можно создать пустую команду без лидера). Если `leader_id` задан —
 * он добавляется в участники автоматически; если не задан, а `member_ids` непуст —
 * лидером становится первый участник. `member_ids` опц. (default `[]`); дубль
 * `leader_id` в `member_ids` — не ошибка. Уникальность `name` → 409 team_name_taken.
 */
export interface TeamCreateRequest {
  name: string;
  leader_id?: string;
  member_ids?: string[];
}

/**
 * Тело PATCH /api/teams/{id} (04-api.md, `TeamUpdateRequest`; ADR-026). Все поля
 * опциональны (exclude_unset). `leader_id`: задан → сменить лидера; `null` → снять
 * лидера (команда без лидера). `member_ids` (если передан) полностью заменяет состав;
 * при исключении текущего лидера лидерство авто-передаётся (или команда без лидера).
 */
export interface TeamUpdateRequest {
  name?: string;
  leader_id?: string | null;
  member_ids?: string[];
}

// --- SMS (04-api.md «SMS», modules/sms, ADR-030) ---

/**
 * Ссылка на CRM-команду номера/сообщения (04-api.md, схема `SmsTeamRef`).
 * Текущее состояние команды; `null` в родителе — номер unassigned.
 */
export interface SmsTeamRef {
  id: string;
  name: string;
}

/**
 * Ссылка на ТЕКУЩИЙ номер сообщения (04-api.md, схема `SmsNumberRef`; по `to_number`).
 * Источник бейджа команды и пилюль `Логин/Приложение/Примечание` на карточке SMS.
 * `null` в сообщении — номер удалён.
 */
export interface SmsNumberRef {
  id: number;
  phone_number: string;
  /** Текущая команда номера; `null` — unassigned. */
  team: SmsTeamRef | null;
  login: string | null;
  app_name: string | null;
  note: string | null;
}

/**
 * Элемент списка номеров (04-api.md, схема `SmsNumberItem`). `label` — системный
 * никнейм (Twilio `friendly_name`), редактированию через API не подлежит; редактируются
 * только `login`/`app_name`/`note` (PATCH). Номера создаются автоматически (нет `create`).
 */
export interface SmsNumber {
  id: number;
  phone_number: string;
  /** Системный никнейм (Twilio friendly_name); не редактируется через API. */
  label: string | null;
  /** Текущая команда; `null` — unassigned. */
  team: SmsTeamRef | null;
  login: string | null;
  app_name: string | null;
  note: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/sms/numbers (04-api.md, схема `SmsNumbersResponse`). */
export interface SmsNumbersResponse {
  numbers: SmsNumber[];
}

/**
 * Элемент списка номеров команды (04-api.md, схема `TeamNumberItem`) — МИНИМАЛЬНАЯ
 * схема detail-панели /teams: только `id`/`phone_number`/`team`. БЕЗ
 * `login`/`app_name`/`note`/`label`/`is_active`/меток (не переиспользуем полный `SmsNumber`).
 */
export interface TeamNumberItem {
  id: number;
  phone_number: string;
  /** Команда номера (= запрошенная команда `{id}`). */
  team: SmsTeamRef;
}

/** Ответ GET /api/teams/{id}/numbers (04-api.md, схема `TeamNumbersResponse`). */
export interface TeamNumbersResponse {
  numbers: TeamNumberItem[];
}

/**
 * Входящее SMS ленты (04-api.md, схема `SmsMessageItem`, newest-first, keyset-курсор).
 * Бейдж команды и пилюли берутся из `number` (текущий номер по `to_number`).
 */
export interface SmsMessage {
  id: number;
  from_number: string;
  to_number: string;
  body: string;
  received_at: string;
  /** Текущий номер (по `to_number`); `null` — номер удалён. */
  number: SmsNumberRef | null;
}

/**
 * Ответ GET /api/sms/messages (04-api.md, схема `SmsMessagesResponse`).
 * `next_cursor` — opaque keyset-курсор следующей (более старой) страницы; `null` — старее нет.
 */
export interface SmsMessagesResponse {
  messages: SmsMessage[];
  next_cursor: string | null;
}

/**
 * Тело PATCH /api/sms/numbers/{id} (04-api.md, схема `SmsNumberUpdateRequest`).
 * Presence-семантика затирания: ключ присутствует + непустое значение → установить;
 * ключ присутствует + пусто/`null` → затереть (NULL); ключ отсутствует → не менять.
 * `label` не редактируется.
 */
export interface SmsNumberUpdateRequest {
  login?: string | null;
  app_name?: string | null;
  note?: string | null;
}

/**
 * Тело POST /api/sms/numbers/{id}/transfer (04-api.md, схема `SmsNumberTransferRequest`).
 * `null` → снять команду (unassigned); иначе привязать к существующей команде.
 */
export interface SmsNumberTransferRequest {
  team_id: string | null;
}

/** Ответ POST /api/sms/numbers/sync (04-api.md, схема `SmsSyncResult`). */
export interface SmsSyncResult {
  synced_total: number;
  added: number;
  skipped_existing: number;
}

/** Единый формат ошибки API (04-api.md). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Array<{ field: string; message: string }> | null;
  };
}

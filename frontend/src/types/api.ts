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
  /** SSH-логин целевого сервера (не секрет). Показывается в detail-view (ADR-035). */
  ssh_user: string;
  exporter_port: number;
  provision_status: ProvisionStatus;
  /** Порядок карточки (drag-and-drop). Меньше = выше. 04-api.md. */
  position: number;
  /**
   * Число бэков, связанных с сервером (COUNT по `backends.server_id`, 04-api.md, ADR-040).
   * Для свёрнутой секции «Бэки» detail-view сервера («Бэков: N») без вызова reverse-lookup.
   */
  backend_count: number;
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
  /**
   * Производный admin-уровень видимости SMS (04-api.md, ADR-032/ADR-036):
   * `is_superadmin OR полный каталог прав`. `true` ⇔ актор видит все SMS-команды.
   * Frontend по нему решает, показывать ли фильтр «Все команды» на /sms
   * (backend — единственный источник, фронт не дублирует predicate).
   */
  sees_all_sms_teams: boolean;
  /**
   * Производный admin-уровень видимости почты (04-api.md, ADR-038 §3): тот же предикат
   * `is_superadmin OR полный каталог прав`, что у SMS. `true` ⇔ актор видит все
   * почтовые команды. Frontend по нему решает, показывать ли фильтр «Все команды» на
   * /mail (backend — единственный источник, фронт не дублирует predicate).
   */
  sees_all_mail_teams: boolean;
  /**
   * ЭФФЕКТИВНЫЙ scope команд канала «Почты» (04-api.md `MeResponse`, ADR-055 §5.1):
   * у не-админа — `user_teams ∪ доп-команды` (объединение, НЕ только добавка); у
   * admin-уровня (`sees_all_mail_teams === true`) — ВСЕ команды системы (`[]` не отдаётся).
   * ЕДИНСТВЕННЫЙ источник опций команд канала на клиенте (ADR-055 §6.3): фильтр «Команда»
   * (5 экранов), селектор формы ящика, резолв имени команды, дропдаун переноса.
   * `GET /api/teams` для этого использовать ЗАПРЕЩЕНО (гейт `teams:view`, у mail-оператора
   * его нет ⇒ пустой список; в Mini App эндпоинт не берётся вовсе).
   */
  mail_teams: TeamRef[];
  /** То же для канала «СМС» (ADR-055 §5.1). */
  sms_teams: TeamRef[];
  /**
   * Видит ли актор объекты канала БЕЗ команды (`team_id IS NULL`) — ящики/письма
   * (ADR-055 §3). При `sees_all_mail_teams === true` backend отдаёт `true`.
   */
  mail_includes_unassigned: boolean;
  /** То же для канала «СМС» (номера/сообщения без команды, ADR-055 §3). */
  sms_includes_unassigned: boolean;
  permissions: PermissionsMap;
}

/**
 * Ответ reveal-эндпоинтов секрета по требованию (04-api.md, схема `SecretRevealResponse`,
 * ADR-035): `GET /api/servers/{id}/ssh-password` · `GET /api/proxies/{id}/password` ·
 * `GET /api/ai-keys/{id}/key`. `value` — расшифрованный секрет (plaintext). НЕ кэшируется
 * (backend отдаёт `Cache-Control: no-store`); фронт держит значение только в локальном
 * стейте модалки и чистит при закрытии.
 */
export interface SecretRevealResponse {
  value: string;
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
  /**
   * Число бэков, использующих ключ (COUNT по `backends.ai_key_id`, 04-api.md, ADR-040).
   * Для свёрнутой секции «Бэки» detail-view ИИ-ключа («Бэков: N») без вызова reverse-lookup.
   */
  backend_count: number;
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

// --- Mail (ADR-044; CRM — система-запись писем/тегов/каталога ящиков) ---

/**
 * Команда-владелец почтового ящика (04-api.md, схема `MailTeamRef`; ADR-056 §1) —
 * зеркало `SmsTeamRef`. `null` в `MailAccount.team` — ящик без команды.
 */
export interface MailTeamRef {
  id: string;
  name: string;
}

/**
 * Ящик-владелец письма (ADR-044 §2, `MailAccountRef`; расширен ADR-056 §1 аддитивно).
 * `number` («Номер»), `app_name` («Приложение») и `team` нужны Mini App почты: имя команды
 * на клиенте иначе недостижимо (`GET /api/teams` гейтится `teams:view`). `display_name` —
 * производная склейка `number`+`app_name`, используется десктопом; в Mini App НЕ рендерится.
 */
export interface MailAccount {
  id: number;
  email: string;
  display_name: string | null;
  number: string | null;
  app_name: string | null;
  team: MailTeamRef | null;
}

/** Тег письма (ADR-044 §5, MailTag). `id` — UUID; `color` — HEX для бейджа. */
export interface MailTag {
  id: string;
  name: string;
  color: string;
}

/**
 * Письмо ленты «Почты» (ADR-044 §2, MailMessage). Хранится в БД CRM; `body_html`
 * рендерится ТОЛЬКО в sandbox-iframe (modules/mail «Изоляция HTML-тела»). Порядок
 * ленты — `internal_date DESC, id DESC` (истинная дата письма, а не порядок push'а).
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
  /**
   * ЛИЧНОЕ производное (ADR-050, 04-api.md «Схема MailMessage»): `true` ⇔ для текущего
   * принципала нет строки `mail_message_reads(user_id, message_id)`. Не nullable. Один и тот
   * же `id` письма у разных пользователей даёт разные значения. Для супер-админа из `.env` —
   * всегда `false`. Меняется вызовами `POST`/`DELETE /api/mail/messages/{id}/read`.
   */
  is_unread: boolean;
}

/**
 * Ответ GET /api/mail/messages (ADR-044 §2, MailListResponse). Компаундный keyset
 * по паре `(internal_date, id)`. `next_cursor` — opaque-токен последнего элемента
 * страницы для догрузки более старых (передаётся обратно как query `before`);
 * `null` — старее нет.
 */
export interface MailListResponse {
  messages: MailMessage[];
  next_cursor: string | null;
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
 * Почтовый ящик из каталога CRM `mail_accounts` (ADR-044 §2/§4, MailMailbox).
 * `id` = id ящика в агрегаторе (используется как `mail_account_id` в серверном
 * фильтре ленты); привязка к команде — напрямую через `team_id` (UUID CRM-команды;
 * `null` — ящик без команды, unassigned). Поля статуса синка
 * (`last_synced_at`/`last_sync_error`/`consecutive_failures`) зеркалятся из агрегатора
 * status-каналом — для кружка статуса и диагностики на вкладке «Почты».
 */
export interface MailMailbox {
  id: number;
  email: string;
  /** «Номер» ящика (04-api.md, ADR-047 §3); `null` — не задан. */
  number: string | null;
  /** «Приложение» ящика (04-api.md, ADR-047 §3); `null` — не задано. */
  app_name: string | null;
  /**
   * ПРОИЗВОДНОЕ (read-only для клиента, ADR-047 §3.3): `"<number> <app_name>"` (пустые
   * части опускаются; обе пусты → `null`). Считает сервер; в запросах НЕ принимается.
   */
  display_name: string | null;
  team_id: string | null;
  is_active: boolean;
  /** Время последней успешной синхронизации; `null` — ещё не синхронизировался. */
  last_synced_at: string | null;
  /** Текст последней ошибки синка; `null` — ошибок нет. */
  last_sync_error: string | null;
  /** Число подряд идущих неудачных синков (0 — здоров). */
  consecutive_failures: number;
}

/** Ответ GET /api/mail/mailboxes (04-api.md, MailMailboxesResponse). */
export interface MailMailboxesResponse {
  mailboxes: MailMailbox[];
}

/**
 * Тело POST /api/mail/mailboxes/test (04-api.md, MailMailboxTestRequest). Пароли —
 * транзитом в агрегатор (не логируются, не возвращаются, ADR-038 §5). `smtp_username`/
 * `smtp_password` опц.: `null` → внешний сервис берёт `email`/`password`. `smtp_ssl` и
 * `smtp_starttls` взаимоисключающи (оба обязательны).
 */
export interface MailMailboxTestRequest {
  email: string;
  imap_host: string;
  imap_port: number;
  imap_ssl: boolean;
  smtp_host: string;
  smtp_port: number;
  smtp_ssl: boolean;
  smtp_starttls: boolean;
  smtp_username?: string | null;
  password: string;
  smtp_password?: string | null;
}

/** Ответ 200 POST /api/mail/mailboxes/test (04-api.md, MailMailboxTestResponse). */
export interface MailMailboxTestResponse {
  imap_ok: boolean;
  smtp_ok: boolean;
}

/**
 * Тело POST /api/mail/mailboxes (04-api.md, MailMailboxCreateRequest) = поля `test`
 * + `number`/`app_name` (ADR-047 §3; оба опц.) + `team_id` (UUID CRM-команды-владельца;
 * `null` — без команды, unassigned — только admin-уровень). Для не-admin `team_id` обязан
 * ∈ его командам. **`display_name` в запросе НЕ принимается** — сервер вычисляет его сам.
 */
export interface MailMailboxCreateRequest extends MailMailboxTestRequest {
  number?: string | null;
  app_name?: string | null;
  team_id?: string | null;
}

/**
 * Тело PATCH /api/mail/mailboxes/{id} (04-api.md, MailMailboxUpdateRequest). Все поля
 * опциональны — присутствие поля = «изменить». Пароль не передан → не менять (секрет не
 * префилится). `number`/`app_name` (ADR-047 §3): значение — установить, `null` — очистить.
 * **`display_name` НЕ принимается** — сервер пересчитывает его из `number`/`app_name`.
 * `team_id`: UUID — сменить команду (перенос между командами — только admin-уровень);
 * `null` — снять привязку. `is_active` — активация/деактивация ящика.
 */
export interface MailMailboxUpdateRequest {
  email?: string;
  number?: string | null;
  app_name?: string | null;
  imap_host?: string;
  imap_port?: number;
  imap_ssl?: boolean;
  smtp_host?: string;
  smtp_port?: number;
  smtp_ssl?: boolean;
  smtp_starttls?: boolean;
  smtp_username?: string | null;
  password?: string;
  smtp_password?: string | null;
  is_active?: boolean;
  team_id?: string | null;
}

/** Ответ 202 POST /api/mail/mailboxes/{id}/sync (04-api.md, MailMailboxSyncResponse). */
export interface MailMailboxSyncResponse {
  queued: boolean;
}

/**
 * Тело POST /api/mail/mailboxes/oauth/authorize (ADR-045 §3, MailOauthAuthorizeRequest).
 * `team_id` — UUID CRM-команды-владельца будущего Outlook-ящика; `null` («без команды») —
 * только admin-уровень. Не-admin обязан указать команду ∈ своим (иначе 403 forbidden).
 */
export interface MailOauthAuthorizeRequest {
  team_id: string | null;
}

/**
 * Ответ 200 POST /api/mail/mailboxes/oauth/authorize (ADR-045 §3, MailOauthAuthorizeResponse).
 * `authorize_url` — Microsoft OAuth-ссылка; CRM показывает её для открытия в нужном профиле
 * OctoBrowser (не auto-redirect). Ошибки: 401/403 forbidden/404 team_not_found/502
 * mail_unavailable/503 mail_not_configured (Outlook-OAuth выключен — кнопка скрывается).
 */
export interface MailOauthAuthorizeResponse {
  authorize_url: string;
}

/** Тип правила тега (04-api.md, MailTagRule). Человекочитаемые подписи — 08-design-system.md. */
export type MailTagRuleType =
  | 'subject_contains'
  | 'body_contains'
  | 'sender_contains'
  | 'sender_exact';

/** Режим совпадения правил тега (04-api.md): `any` — любое правило, `all` — все. */
export type MailTagMatchMode = 'any' | 'all';

/** Правило тега (ADR-044 §5, MailTagRule). `id` — UUID. */
export interface MailTagRule {
  id: string;
  type: MailTagRuleType;
  pattern: string;
  created_at: string;
}

/**
 * Полный тег с правилами для вкладки «Теги» (04-api.md, MailTagFull). Глобальный
 * админский каталог; `id` — UUID. `color` — HEX из палитры 8 цветов (08-design-system.md).
 * **Поля `is_builtin` НЕТ** (ADR-047 §1): признак «встроенный тег» упразднён, колонка
 * дропнута миграцией `0023`. Удалить можно ЛЮБОЙ тег.
 */
export interface MailTagFull {
  id: string;
  name: string;
  color: string;
  match_mode: MailTagMatchMode;
  rules: MailTagRule[];
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/mail/tags (04-api.md, MailTagsResponse). */
export interface MailTagsResponse {
  tags: MailTagFull[];
}

/** Тело POST /api/mail/tags (04-api.md, MailTagCreateRequest). `match_mode` опц. (default `any`). */
export interface MailTagCreateRequest {
  name: string;
  color: string;
  match_mode?: MailTagMatchMode;
}

/** Тело PATCH /api/mail/tags/{id} (04-api.md, MailTagUpdateRequest). Все поля опц. */
export interface MailTagUpdateRequest {
  name?: string;
  color?: string;
  match_mode?: MailTagMatchMode;
}

/** Тело POST /api/mail/tags/{id}/rules (04-api.md, MailTagRuleCreateRequest). */
export interface MailTagRuleCreateRequest {
  type: MailTagRuleType;
  pattern: string;
}

/** Ответ 200 POST /api/mail/tags/{id}/apply-to-existing (04-api.md, MailTagApplyResponse). */
export interface MailTagApplyResponse {
  applied_count: number;
}

/**
 * Ящик команды для detail-панели /teams (04-api.md, TeamMailboxItem; ADR-038,
 * расширена ADR-048 §2). Минимальная схема без кредов/статуса синка (гейт
 * `teams:view`, не `mail:view`).
 */
export interface TeamMailboxItem {
  id: number;
  email: string;
  /** «Номер» ящика (mail_accounts.number, ADR-047 §3); `null` — не задан. */
  number: string | null;
  /** «Приложение» ящика (mail_accounts.app_name, ADR-047 §3); `null` — не задано. */
  app_name: string | null;
  /**
   * Производное имя ящика («<number> <app_name>», TD-052). В строке detail-панели
   * /teams НЕ рендерится (ADR-048 §2/§3) — составляющие показаны явно.
   */
  display_name: string | null;
  is_active: boolean;
}

/** Ответ GET /api/teams/{id}/mailboxes (04-api.md, TeamMailboxesResponse). */
export interface TeamMailboxesResponse {
  mailboxes: TeamMailboxItem[];
}

/**
 * Состояние opt-out Telegram-уведомлений почты (ADR-044 §2, MailUserSettingsResponse).
 * Ответ GET/PATCH /api/mail/me/settings. Дефолт (нет строки) = уведомления включены.
 */
export interface MailUserSettings {
  tg_notifications_enabled: boolean;
}

/** Тело PATCH /api/mail/me/settings (ADR-044 §2, MailUserSettingsUpdateRequest). */
export interface MailUserSettingsUpdateRequest {
  tg_notifications_enabled: boolean;
}

/**
 * Тело POST /api/mail/telegram/auth (ADR-044 §7, MailTelegramAuthRequest) —
 * беспарольный Telegram-SSO Mini App `/tg/mail`. `init_data` — raw Telegram WebApp
 * initData (HMAC-подпись бота — граница безопасности). Публичный эндпоинт.
 */
export interface MailTelegramAuthRequest {
  init_data: string;
}

/**
 * Ответ 200 POST /api/mail/telegram/auth (ADR-044 §7, MailTelegramAuthResponse).
 * Успешный SSO: выдан CRM access-JWT + auto-upsert линка. Ошибки — 401
 * `invalid_init_data`/`init_data_expired`, 403 `mail_operator_not_provisioned`,
 * 400 `validation_error`.
 */
export interface MailTelegramAuthResponse {
  /** Обычный CRM access-JWT (как у POST /api/auth/login). Держится Mini App в памяти. */
  access_token: string;
  /** Всегда `"bearer"`. */
  token_type: string;
  /** TTL access-токена в секундах. */
  expires_in: number;
  /** Из проверенного `init_data`. */
  telegram_user_id: number;
  /** Всегда `true` при успехе (линк upserted). */
  linked: boolean;
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
 * Элемент списка бэков (04-api.md, схема `BackendListItem`). `code`/`name`/`domain`/`git`/`note`
 * публичны; секреты (`api_key`/`admin_api_key`) НЕ отдаются — только флаги `has_*` + on-demand
 * reveal (ADR-040). Связи `server_id`/`ai_key_id` (+ денормализованные имена для отображения).
 * `code` уникален; `name` — нет (дубли группируются, ADR-039).
 */
export interface Backend {
  id: string;
  /** Бизнес-код сервиса (1–64), уникален по реестру. */
  code: string;
  name: string;
  /** Каноничный домен (`https://<host>/`, ADR-042). Проверка — `{domain}health`. */
  domain: string;
  /** Сервер CRM, на котором лежит бэк (ADR-040); `null` — не задан. */
  server_id: string | null;
  /** Имя связанного сервера для отображения (join `servers.name`); `null` при `server_id=null`. */
  server_name: string | null;
  /** ИИ-ключ CRM, используемый бэком (ADR-040); `null` — не задан. */
  ai_key_id: string | null;
  /** Имя связанного ИИ-ключа (join `ai_keys.name`); `null` при `ai_key_id=null`. */
  ai_key_name: string | null;
  /** Задан ли API KEY (`api_key_encrypted IS NOT NULL`, ADR-040). Сам секрет не отдаётся. */
  has_api_key: boolean;
  /** Задан ли ADMIN API KEY (`admin_api_key_encrypted IS NOT NULL`, ADR-040). */
  has_admin_api_key: boolean;
  /** Ссылка на репозиторий (URL, не секрет, ADR-040); `null` — не задан. */
  git: string | null;
  /** Свободные примечания (не секрет, ADR-040); `null` — не заданы. */
  note: string | null;
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
 * дубликат → 409 backend_code_taken. `domain` принимается с/без схемы, канонизируется на backend.
 * Секция «Информация» (ADR-040) — все поля опциональны: FK `server_id`/`ai_key_id`
 * (несуществующий → 422), секреты `api_key`/`admin_api_key` (шифруются), `git`/`note` (не секреты).
 */
export interface CreateBackendRequest {
  code: string;
  name: string;
  domain: string;
  server_id?: string | null;
  ai_key_id?: string | null;
  api_key?: string | null;
  admin_api_key?: string | null;
  git?: string | null;
  note?: string | null;
}

/**
 * Тело PATCH /api/backends/{id} (04-api.md, `BackendUpdateRequest`). Все поля опциональны —
 * передаются только изменяемые (семантика exclude_unset). Смена `code` на занятый другим
 * бэком → 409 backend_code_taken. Смена `domain` → повторная проверка (check_status='pending').
 * FK: `null` → обнулить связь; uuid → установить (несуществующий → 422). Секреты: непустая
 * строка → зашифровать; `null`/`""` → очистить. `git`/`note`: значение → установить; `null`/`""` → очистить.
 */
export interface UpdateBackendRequest {
  code?: string;
  name?: string;
  domain?: string;
  server_id?: string | null;
  ai_key_id?: string | null;
  api_key?: string | null;
  admin_api_key?: string | null;
  git?: string | null;
  note?: string | null;
}

/**
 * Компактная ссылка на бэк (04-api.md, схема `BackendRef`, ADR-040) для reverse-lookup
 * списков «бэки сервера»/«бэки ключа». Только идентификация — секреты/связи не отдаются.
 */
export interface BackendRef {
  code: string;
  name: string;
  domain: string;
}

/**
 * Ответ GET /api/servers/{id}/backends и GET /api/ai-keys/{id}/backends
 * (04-api.md, схема `BackendRefListResponse`, ADR-040). Сортировка position ASC.
 */
export interface BackendRefListResponse {
  backends: BackendRef[];
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
 * для группировки списка «Пользователи» по командам.
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
  /**
   * ТОЛЬКО ДОБАВКА канала «Почты» (строки `user_channel_teams`, ADR-055 §5.2) — БЕЗ базовых
   * `teams`: то, что реально хранится. Эффективный scope канала = `teams ∪ mail_extra_teams`
   * (его в готовом виде отдаёт `GET /api/auth/me` — имена полей разведены намеренно).
   */
  mail_extra_teams: TeamRef[];
  /** Флаг «Без команды» канала «Почты» (доступ к ящикам с `team_id = null`). */
  mail_extra_includes_unassigned: boolean;
  /** ТОЛЬКО ДОБАВКА канала «СМС» (ADR-055 §5.2). */
  sms_extra_teams: TeamRef[];
  /** Флаг «Без команды» канала «СМС» (доступ к номерам с `team_id = null`). */
  sms_extra_includes_unassigned: boolean;
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
  /**
   * ДОПОЛНИТЕЛЬНЫЕ команды канала сверх базового членства (ADR-055 §5.2; default `[]`).
   * Базовые (`team_ids`) сюда НЕ включаются — пересечение сервис вычитает (это не ошибка).
   */
  mail_extra_team_ids?: string[];
  /** «Без команды» канала «Почты» (default `false`). */
  mail_extra_includes_unassigned?: boolean;
  /** ДОПОЛНИТЕЛЬНЫЕ команды канала «СМС» (ADR-055 §5.2; default `[]`). */
  sms_extra_team_ids?: string[];
  /** «Без команды» канала «СМС» (default `false`). */
  sms_extra_includes_unassigned?: boolean;
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
  /**
   * Добавка канала «Почты» (ADR-055 §5.2): не передано → не менять; передано → ПОЛНОСТЬЮ
   * заменяет набор добавок (`[]` → снять все). Пересечение с базовым набором вычитает сервис.
   */
  mail_extra_team_ids?: string[];
  mail_extra_includes_unassigned?: boolean;
  /** Добавка канала «СМС» (ADR-055 §5.2; те же правила). */
  sms_extra_team_ids?: string[];
  sms_extra_includes_unassigned?: boolean;
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
 * отдельная сущность (uuid, БД CRM, лидер+участники). Ящик почты крепится к команде
 * напрямую через `mail_accounts.team_id` (ADR-044; групп агрегатора больше нет).
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
  /**
   * Число почтовых ящиков команды (04-api.md, COUNT mail_accounts WHERE team_id;
   * ADR-048 §1). Может быть 0. Агрегат для чипа «N почт» на карточке команды;
   * список почт — GET /api/teams/{id}/mailboxes.
   */
  mailbox_count: number;
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
 * Элемент списка номеров команды (04-api.md, схема `TeamNumberItem`; ADR-034):
 * `id`/`phone_number`/`team` + слабо-чувствительный идентифицирующий контекст
 * `login`/`app_name`. БЕЗ `note`/`label`/`is_active` (доступны только под `sms:*`;
 * не переиспользуем полный `SmsNumber`).
 */
export interface TeamNumberItem {
  id: number;
  phone_number: string;
  /** Команда номера (= запрошенная команда `{id}`). */
  team: SmsTeamRef;
  /** Логин учётной записи номера (ADR-034); `null` — не задан. */
  login: string | null;
  /** Приложение номера (ADR-034); `null` — не задано. */
  app_name: string | null;
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

/**
 * Тело POST /api/sms/telegram/auth (04-api.md, схема `TelegramAuthRequest`) —
 * беспарольный Telegram-SSO операторской Mini App. `init_data` — raw Telegram
 * WebApp initData (аутентификатор; HMAC-SHA256 + TTL). Публичный эндпоинт.
 */
export interface TelegramAuthRequest {
  init_data: string;
}

/**
 * Ответ 200 POST /api/sms/telegram/auth (04-api.md, схема `TelegramAuthResponse`,
 * ADR-031). Успешный SSO: выдан CRM access-JWT + авто-upsert/revive линка.
 * Ошибки — 401 `invalid_init_data`/`init_data_expired`, 403
 * `sms_operator_not_provisioned`, 400 `validation_error`.
 */
export interface TelegramAuthResponse {
  /** Обычный CRM access-JWT (как у POST /api/auth/login). Хранится Mini App в памяти. */
  access_token: string;
  /** Всегда `"bearer"`. */
  token_type: string;
  /** TTL access-токена в секундах. */
  expires_in: number;
  /** Из проверенного `init_data`. */
  telegram_user_id: number;
  /** Всегда `true` при успехе (линк upserted/revived). */
  linked: boolean;
}

/** Единый формат ошибки API (04-api.md). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Array<{ field: string; message: string }> | null;
  };
}

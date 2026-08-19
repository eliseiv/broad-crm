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

/**
 * Способ входа на целевой сервер (04-api.md, ADR-067): пароль ЛИБО приватный SSH-ключ.
 * **Не секрет** — это способ входа, а не материал. Флагов `has_password`/`has_key` не
 * вводится: CHECK `ck_servers_auth_material` делает наличие материала однозначной
 * функцией `auth_method`.
 */
export type ServerAuthMethod = 'password' | 'key';

export interface Server {
  id: string;
  name: string;
  ip: string;
  /** SSH-логин целевого сервера (не секрет). Показывается в detail-view (ADR-035). */
  ssh_user: string;
  /**
   * Способ входа (04-api.md, ADR-067). UI по нему рисует строку «Способ входа» и решает,
   * рендерить ли кнопку-глаз reveal: `key` → приватный ключ и парольная фраза write-only,
   * маска БЕЗ глаза (ADR-067 §4/§6).
   */
  auth_method: ServerAuthMethod;
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
 * текущего принципала для UI-гейтинга. `roles` для супер-админа — ["admin"].
 * `is_admin_level` — производный admin-уровень (ADR-078); гейт `/users`.
 * `permissions` — `{ "<page>": ["<action>", ...] }` (для супер-админа — полный каталог).
 * Безопасность обеспечивает сервер (403); гейтинг — только UX.
 */
export interface MeResponse {
  username: string;
  /**
   * Имена ВСЕХ ролей актора (04-api.md `MeResponse`, ADR-079 §3), порядок —
   * `user_roles.created_at ASC, role_id ASC`; для супер-админа — `["admin"]`.
   * Заменяет прежнее поле `role: string` (роли стали M2M). Пустой массив
   * (роли сняты прямым SQL) читается как «прав нет», а не как ошибка.
   */
  roles: string[];
  is_superadmin: boolean;
  /**
   * Производный admin-уровень (04-api.md, ADR-078 в редакции ADR-079 §2): тот же
   * предикат, что `require_admin` — `is_superadmin OR "admin" ∈ roles OR полный
   * каталог по union прав всех ролей`.
   * `true` ⇔ актор видит страницу «Пользователи». Backend — единственный
   * источник; фронт не пересчитывает покрытие каталога.
   */
  is_admin_level: boolean;
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

/** Общая часть тела POST /api/servers (04-api.md). */
interface CreateServerBase {
  name: string;
  ip: string;
  ssh_user: string;
}

/**
 * Ветка «пароль» (04-api.md §POST /api/servers, ADR-067). `auth_method` опционален с
 * дефолтом `password` ⇒ прежнее тело `{name,ip,ssh_user,ssh_password}` остаётся валидным.
 * Поля key-ветки в этом теле присутствовать НЕ должны — даже пустыми: правило «ровно один
 * способ» даёт `422` на любое поле чужого режима.
 */
export interface CreateServerPasswordRequest extends CreateServerBase {
  auth_method?: 'password';
  ssh_password: string;
}

/**
 * Ветка «приватный SSH-ключ» (04-api.md, ADR-067). `ssh_private_key` — 1–`SSH_KEY_MAX_BYTES`
 * байт (16384 по умолчанию), `ssh_key_passphrase` — опциональна и допустима ТОЛЬКО здесь.
 */
export interface CreateServerKeyRequest extends CreateServerBase {
  auth_method: 'key';
  ssh_private_key: string;
  ssh_key_passphrase?: string;
}

/** Тело POST /api/servers — дискриминировано по `auth_method` (04-api.md, ADR-067). */
export type CreateServerRequest = CreateServerPasswordRequest | CreateServerKeyRequest;

export interface CreateServerResponse {
  id: string;
  name: string;
  ip: string;
  ssh_user: string;
  auth_method: ServerAuthMethod;
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
  ssh_user: string;
  auth_method: ServerAuthMethod;
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

/** Исход синхронизации оценочного остатка (ADR-070). */
export type BalanceSyncStatus = 'ok' | 'error' | 'unknown';

/** Уровень алерта по остатку (ADR-070). */
export type BalanceAlertLevel = 'normal' | 'low' | 'depleted';

/** Бинарный credit-probe (ADR-075): есть кредиты / нет. */
export type CreditStatus = 'ok' | 'depleted';

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
  updated_at: string;
  balance_monitoring_enabled: boolean;
  balance_initial_usd: string | null;
  balance_remaining_usd: string | null;
  balance_low_threshold_usd: string | null;
  balance_anchor_at: string | null;
  balance_last_sync_at: string | null;
  balance_sync_status: BalanceSyncStatus | null;
  balance_sync_error: string | null;
  balance_alert_level: BalanceAlertLevel | null;
  credit_status: CreditStatus | null;
  credit_last_probed_at: string | null;
  credit_probe_error: string | null;
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
  balance_monitoring_enabled?: boolean;
  balance_initial_usd?: string;
  balance_low_threshold_usd?: string;
  billing_admin_key?: string;
}

/**
 * Ответ 202 POST /api/ai-keys — созданный `AiKeyListItem` целиком, с `check_status:"pending"`
 * (04-api.md §POST /api/ai-keys, «Response 202 Accepted»). Состав полей — тот же, что у
 * элемента списка (04-api.md §«Схема `AiKeyListItem`»), включая контур остатка (ADR-070):
 * при `balance_monitoring_enabled=true` ответ уже несёт якорь и вычисленный остаток.
 */
export type CreateAiKeyResponse = AiKey;

/**
 * Тело PATCH /api/ai-keys/{id} (04-api.md). Все поля опциональны — передаются
 * только изменяемые. Пустой/отсутствующий `key` = «не менять ключ» (секрет не префилится).
 */
export interface UpdateAiKeyRequest {
  name?: string;
  provider?: AiProvider;
  key?: string;
  balance_monitoring_enabled?: boolean;
  balance_initial_usd?: string;
  balance_low_threshold_usd?: string;
  billing_admin_key?: string;
}

/** Тело POST /api/ai-keys/{id}/balance/reset (ADR-070). */
export interface ResetAiKeyBalanceRequest {
  balance_initial_usd: string;
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

/** Отправленное письмо (ADR-071). */
export interface MailSentMessage {
  id: string;
  subject: string | null;
  sent_at: string;
  to_addrs: string;
  cc_addrs: string | null;
  body_text: string;
  mail_account: MailAccount;
  smtp_message_id: string | null;
}

export interface MailSentListResponse {
  messages: MailSentMessage[];
  next_cursor: string | null;
}

export interface MailUnreadCountResponse {
  count: number;
}

export interface MailMessageBatchRequest {
  message_ids: number[];
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

/**
 * Тело POST /api/mail/mailboxes/{id}/compose — новое письмо (не reply).
 * `to` обязательный непустой список; `body` обязательный непустой.
 */
export interface MailComposeRequest {
  to: string[];
  cc?: string[] | null;
  subject?: string;
  body: string;
}

/**
 * Ответ POST /api/mail/messages/{id}/reply (04-api.md, MailReplyResponse).
 *
 * `sent_id` — uuid строки `mail_sent_messages` самой CRM (ADR-057 §1); прежний `number`
 * приходил от агрегатора, который идентификатор отправки больше не выдаёт.
 *
 * `smtp_message_id` — `string | null` (ADR-057 §5.3): `null` = письмо ОТПРАВЛЕНО и
 * записано в историю, но Message-ID агрегатор не прислал. Это по-прежнему `200` —
 * успешная отправка, а не ошибка; значение поля SPA не читает.
 */
export interface MailReplyResponse {
  sent_id: string;
  smtp_message_id: string | null;
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
 * Компактная ссылка на роль (04-api.md, схема `RoleRef`; по образцу `TeamRef`,
 * ADR-079 §1). Используется в `UserListItem.roles`.
 */
export interface RoleRef {
  id: string;
  name: string;
}

/**
 * Элемент списка пользователей (04-api.md, схема `UserListItem`). Пароль
 * (`password`/`password_hash`) в ответах отсутствует всегда — только на вход.
 */
export interface UserListItem {
  id: string;
  /**
   * СКРЫТЫЙ технический логин (ADR-079 §9): в запросах его больше нет (сервис
   * выводит `username := normalize_telegram(telegram)`), в UI — только фолбэк-
   * отображение при пустом ФИО и диагностическое значение. Остаётся идентификатором входа.
   */
  username: string;
  /**
   * ФИО (ADR-079 §7). `null` — часть не заполнена (у исторических строк заполнено
   * только `first_name` — туда миграция 0039 перенесла прежний логин). Отображаемое
   * имя строит клиент — `fullName()` (`features/users/fullName.ts`).
   */
  last_name: string | null;
  first_name: string | null;
  middle_name: string | null;
  /**
   * Телеграм-ник (ADR-025). Нормализован (без `@`, lower-case), второй идентификатор
   * входа. `null` — ТОЛЬКО у исторических строк: с ADR-079 §8 поле обязательно
   * при создании и не очищается.
   */
  telegram: string | null;
  /**
   * Производное `password_hash IS NOT NULL` (ADR-025). `false` — беспарольный
   * пользователь (ещё не прошёл «открытый первый вход»). Сам пароль не возвращается.
   */
  has_password: boolean;
  /**
   * ВСЕ роли пользователя (ADR-079 §1), порядок — `user_roles.created_at ASC,
   * role_id ASC`. Заменяет удалённые `role_id`/`role_name`.
   */
  roles: RoleRef[];
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
  /**
   * Запуск ИИ-бота базы знаний (04-api.md, ADR-076): `true` ⇔ есть хотя бы одна
   * активная строка `knowledge_bot_links`. Числовой chat_id и `started_at` наружу не отдаются.
   */
  bot_started: boolean;
  created_at: string;
  updated_at: string;
}

/** Ответ GET /api/users (04-api.md, схема `UserListResponse`). */
export interface UserListResponse {
  items: UserListItem[];
}

/**
 * Тело POST /api/users (04-api.md, `UserCreateRequest`; ADR-025, ADR-079).
 * `last_name`/`first_name` — обязательны (1–64 после trim, кириллица допускается),
 * `middle_name` — опц. `telegram` **обязателен** (§8): из него же сервис выводит
 * `username`, поэтому поля `username` в запросе НЕТ (§9). `password` **опционален**
 * (8–128 при наличии; отсутствие → беспарольный пользователь «открытого первого
 * входа»). `role_ids` — непустой набор существующих ролей (§1). `team_ids` — опц.
 */
export interface UserCreateRequest {
  last_name: string;
  first_name: string;
  middle_name?: string;
  telegram: string;
  password?: string;
  role_ids: string[];
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
 * Тело PATCH /api/users/{id} (04-api.md, `UserUpdateRequest`; ADR-025, ADR-079).
 * `username` не редактируется и не пересчитывается при смене телеграма (§9). Все поля
 * опциональны — передаются только изменяемые (exclude_unset). `password`: не передан →
 * не менять; непустой (8–128) → сброс/установка. `telegram`: не передан → не менять;
 * ⛔ `null`/`""` → `422` — **очистка запрещена** (§8), форма обязана её блокировать.
 * `role_ids` (если передан) полностью заменяет набор ролей (`[]` → `422`). `team_ids`
 * (если передан) полностью заменяет набор CRM-команд пользователя.
 */
export interface UserUpdateRequest {
  /** Обязательные части ФИО: передано → установить; очистка (`""`/`null`) → `422`. */
  last_name?: string;
  first_name?: string;
  /** Единственная снимаемая часть ФИО: `null` → очистить. */
  middle_name?: string | null;
  /** Смена телеграма; очистка запрещена — `null`/`""` даёт `422` (ADR-079 §8). */
  telegram?: string;
  /** Полная замена набора ролей (ADR-079 §1); пустой набор → `422`. */
  role_ids?: string[];
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

// --- Рассылка (модуль `broadcast`, ADR-076; 04-api.md §Broadcast) ---

/** Роль в аудитории рассылки (GET /api/broadcasts/audience). */
export interface BroadcastAudienceRole {
  id: string;
  name: string;
  started_count: number;
  not_started_count: number;
}

/** Ответ GET /api/broadcasts/audience. */
export interface BroadcastAudienceResponse {
  roles: BroadcastAudienceRole[];
  all_started_count: number;
  all_not_started_count: number;
}

/** Тело POST /api/broadcasts. Ровно одно: `all=true` или непустой `role_ids`. */
export interface BroadcastCreateRequest {
  text: string;
  all: boolean;
  role_ids: string[];
}

/** Ответ POST /api/broadcasts (частичный успех — тоже 200). */
export interface BroadcastCreateResponse {
  sent: number;
  failed: number;
  skipped_not_started: number;
}

// --- Документы (модуль `documents`, ADR-059/061/062; 04-api.md §Documents) ---

/** Тип узла дерева документов. */
export type DocumentNodeType = 'folder' | 'document';

/** Режим видимости узла: наследуется от предка/публичен ↔ ограничен ролями. */
export type DocumentVisibilityMode = 'inherit' | 'restricted';

/**
 * Узел дерева документов (04-api.md §Documents «Форма узла в ответах»). `content_md`
 * приходит ТОЛЬКО у документа и ТОЛЬКО в `GET /nodes/{id}` (в дереве/списках — `null`).
 */
export interface DocumentNode {
  id: string;
  node_type: DocumentNodeType;
  parent_id: string | null;
  name: string;
  content_md: string | null;
  owner_id: string;
  visibility_mode: DocumentVisibilityMode;
  content_version: number;
  position: number;
  created_at: string;
  updated_at: string;
}

/** POST /api/documents/folders — тело. */
export interface DocumentFolderCreateRequest {
  parent_id: string | null;
  name: string;
}

/** POST /api/documents/documents — тело (`content_md` опц., default ""). */
export interface DocumentCreateRequest {
  parent_id: string | null;
  name: string;
  content_md?: string;
}

/**
 * PATCH /api/documents/nodes/{id} — любое подмножество (presence-семантика).
 * `expected_version` опц. (TD-064): при передаче ≠ текущему → 409 document_node_conflict.
 */
export interface DocumentNodeUpdateRequest {
  name?: string;
  content_md?: string;
  expected_version?: number;
}

/** POST /api/documents/nodes/{id}/copy — тело (default target = тот же parent). */
export interface DocumentCopyRequest {
  target_parent_id: string | null;
}

/**
 * GET/PATCH /api/documents/nodes/{id}/visibility — симметричная форма (read↔write).
 * `role_ids` — СОБСТВЕННЫЕ роли узла (при `restricted`); `inherit` → `[]`.
 */
export interface DocumentVisibility {
  visibility_mode: DocumentVisibilityMode;
  role_ids: string[];
  /** «Не включать в RAG» — собственный флаг узла; наследуется вниз по дереву (backend). */
  rag_exclude?: boolean;
}

/** PATCH /api/documents/order — полная перестановка уровня одного `parent_id` → 204. */
export interface DocumentOrderRequest {
  parent_id: string | null;
  ids: string[];
}

/** Элемент GET /api/documents/role-refs (лёгкий список ролей для модалки видимости). */
export interface DocumentRoleRef {
  id: string;
  name: string;
}

/** MIME-типы вложений-изображений — ровно четыре (04-api.md, ADR-068 §2.3; SVG исключён). */
export type DocumentAttachmentMime = 'image/png' | 'image/jpeg' | 'image/webp' | 'image/gif';

/**
 * Вложение-изображение документа (04-api.md §Вложения, схема `DocumentAttachment`, ADR-068).
 * Ответ `POST /api/documents/nodes/{id}/attachments`.
 *
 * `url` — **канонический адрес, который формирует сервер**; клиент его НЕ конструирует
 * (иначе форма ссылки стала бы неявным контрактом, размазанным по фронту). Именно это
 * значение уходит в `src` узла изображения и в markdown `![alt](url)`.
 */
export interface DocumentAttachment {
  id: string;
  document_node_id: string;
  /** Исходное имя файла (1–255) — идёт в `alt` вставляемой картинки. */
  filename: string;
  mime: DocumentAttachmentMime;
  size_bytes: number;
  /** sha256 hex (64 симв.); он же `ETag` отдачи. */
  checksum: string;
  url: string;
  created_at: string;
}

// --- Пользователи бэков (04-api.md#backend-users, CRM Admin API contract v1) ---

/**
 * Строка объединённого списка пользователей бэков (GET /api/backend-users).
 * Данные — транзит из CRM Admin API бэка; `backend_*` добавляет CRM при агрегации.
 */
export interface BackendUserItem {
  backend_id: string;
  backend_code: string;
  backend_name: string;
  /** Стабильный id пользователя в бэке (User ID таблицы). */
  id: string;
  external_id: string | null;
  is_paid: boolean;
  payments_count: number;
  renewals_count: number;
  tokens: number;
  subscription_active: boolean;
  subscription_expires_at: string | null;
  plan_id: string | null;
  registered_at: string;
}

/** Сводка шапки списка. `cr_percent` считает CRM (paid/total, 1 знак). */
export interface BackendUsersStats {
  users_total: number;
  paid_users: number;
  payments_sum_usd: number;
  cr_percent: number;
}

/** Бэк, не ответивший при агрегации «Все приложения» (partial-data warning). */
export interface BackendUsersSourceError {
  backend_id: string;
  backend_code: string;
  backend_name: string;
  message: string;
}

/**
 * Расходы на API-провайдеров по бэкам (04-api.md, схема `BackendUsersApiCosts`;
 * ADR-080 §5/§6). Показатель **накопительный за всё время (lifetime)** — фильтр
 * периода страницы на него НЕ действует; UI обязан называть это подписью.
 * Ключи провайдеров нормализует сервер (openai/anthropic/fal, прочее → other).
 */
export interface BackendUsersApiCosts {
  openai_usd: number;
  anthropic_usd: number;
  fal_usd: number;
  other_usd: number;
  total_usd: number;
  /**
   * `true` — сумма неполная: backfill карточек не завершён ЛИБО источник не отдаёт
   * блок `revenue` (постоянное занижение). UI обязан это показать, иначе неполная
   * сумма читается как полная.
   */
  partial: boolean;
}

/** Ответ GET /api/backend-users. */
export interface BackendUsersListResponse {
  total: number;
  items: BackendUserItem[];
  stats: BackendUsersStats;
  errors: BackendUsersSourceError[];
  /**
   * Момент последнего полного цикла воркера снимка — `MIN(refreshed_at)` по
   * участвующим источникам (ADR-080 §3). `null` — хотя бы один источник ни разу не
   * обновлялся («Снимок формируется…»).
   */
  snapshot_at: string | null;
  /** `null` — снимок ещё не сформирован (тот же случай, что `snapshot_at: null`). */
  api_costs: BackendUsersApiCosts | null;
}

export interface BackendUserBalance {
  tokens: number;
  credited_total: number | null;
  spent_total: number | null;
}

export interface BackendUserSubscriptionInfo {
  plan_id: string | null;
  plan_name: string | null;
  /** Строка вида «$9.99/мес» (формирует бэк). */
  price: string | null;
  active: boolean;
  expires_at: string | null;
  last_payment_at: string | null;
  /** Например «Карта •••• 4242». */
  last_payment_method: string | null;
}

/** Экономика пользователя; `null` — бэк её не считает (секция скрывается). */
export interface BackendUserRevenue {
  income_usd: number;
  api_cost_usd: number;
  /** Расход по провайдерам: `{ "Claude": 28.4, ... }`. */
  providers: Record<string, number>;
}

export interface BackendUserMediaCounters {
  total: number;
  success: number;
  failed: number;
}

export interface BackendUserAvgGeneration {
  photo: number | null;
  video: number | null;
  overall: number | null;
}

/** Статистика генераций; `null` — не применимо к бэку (секция скрывается). */
export interface BackendUserMediaStats {
  photos: BackendUserMediaCounters;
  videos: BackendUserMediaCounters;
  avg_generation_sec: BackendUserAvgGeneration;
}

/** Ответ GET /api/backend-users/{backend_id}/users/{user_id}. */
export interface BackendUserDetail {
  backend_id: string;
  backend_code: string;
  backend_name: string;
  id: string;
  external_id: string | null;
  registered_at: string;
  balance: BackendUserBalance;
  subscription: BackendUserSubscriptionInfo;
  revenue: BackendUserRevenue | null;
  media_stats: BackendUserMediaStats | null;
}

export interface BackendUserPayment {
  title: string;
  description: string | null;
  amount: number;
  currency: string;
  status: 'success' | 'failed';
  occurred_at: string;
}

export interface BackendUserPaymentsResponse {
  total: number;
  items: BackendUserPayment[];
}

export interface BackendUserRequestItem {
  endpoint: string;
  prompt_preview: string | null;
  status_code: number;
  status: 'ok' | 'slow' | 'error';
  duration_sec: number | null;
  sent_at: string;
  /**
   * Contract v1.1 «экономика» (ADR-072 §5, 04-api.md#backend-users). Списано токенов;
   * `null` = «НЕ измерено», а НЕ ноль — UI рендерит «—». Бэк уровня v1 поле не отдаёт
   * (не ошибка), поэтому значение может прийти и как `undefined`.
   */
  tokens_spent?: number | null;
  /**
   * Себестоимость генерации у провайдера, USD. **`null` = «не измерено», НЕ ноль**:
   * рендерить `$0.00` вместо «—» ЗАПРЕЩЕНО, `value || 0` / `?? 0` / `coalesce` на уровне
   * строки запрещены (ADR-072 §5). Измеренный `0` → `$0.00`. Формат — до 4 знаков.
   */
  provider_cost_usd?: number | null;
  /**
   * `true` — себестоимость выведена из тарифной пачки (оценка сверху, UI ставит «≈»);
   * `false` — точное значение; **`null` — поле не отдано** (бэк уровня v1) или себестоимости
   * нет вовсе. ⚠️ **`null` ≠ `false`**.
   */
  provider_cost_estimated?: boolean | null;
  /**
   * `true` — списание возвращено (`tokens_spent` при этом ОСТАЁТСЯ заполненным: возврат
   * не обнуляет стоимость); `false` — возврата не было; **`null` — поле не отдано** (бэк
   * уровня v1; CRM нормализует отсутствующее поле в `null`). ⚠️ **`null` ≠ `false`** —
   * «не отдано» не значит «не возвращено»; пометка возврата рендерится ТОЛЬКО при `true`
   * (04-api.md#backend-users, 08-design-system.md §История запросов).
   */
  refunded?: boolean | null;
}

export interface BackendUserRequestsResponse {
  total: number;
  items: BackendUserRequestItem[];
}

/** Тариф бэка (GET /api/backend-users/{backend_id}/products) для формы «Установить план». */
export interface BackendProduct {
  product_id: string;
  name: string;
  price: string | null;
  period: string | null;
  /**
   * Contract v1.2 (ADR-073 §5): продукт снят с ВИТРИНЫ. Форма «Установить план»
   * архивные **НЕ фильтрует** (выдать архивный план — законная операция; `archived` и
   * `grantable` ортогональны, `scope=grantable` МОЖЕТ вернуть архивные), но **помечает**
   * подпись опции суффиксом из словаря. `null`/отсутствует = у бэка нет понятия архива
   * ⇒ помечать нечего (modules/backend-users/README.md §Архивные продукты).
   */
  archived?: boolean | null;
}

export interface BackendProductsResponse {
  items: BackendProduct[];
}

/** Тело POST .../tokens. Отрицательное значение — списание; 0 отвергает форма. */
export interface AddBackendUserTokensRequest {
  amount: number;
}

/**
 * Тело POST .../subscription. `grant_id` — ключ идемпотентности (contract v1 §3.2),
 * генерируется при ОТКРЫТИИ модалки (crypto.randomUUID) — повторный сабмит той же
 * формы не продлит подписку дважды.
 */
export interface GrantBackendUserSubscriptionRequest {
  product_id: string;
  expires_in_days: number;
  grant_id: string;
}

export interface BackendUserTokensResponse {
  id: string;
  tokens: number;
}

/** `applied=false` — бэк распознал повтор grant_id и не продлил повторно.
 *  `tokens: null` — бэк уровня v1 не отдал поле (null ≠ 0, ADR-072). */
export interface BackendUserGrantResponse {
  id: string;
  tokens: number | null;
  subscription_active: boolean;
  subscription_expires_at: string | null;
  applied: boolean;
}

/* ── Backend Economics: «Продукты и тарифы» (ADR-072, 04-api.md#backend-economics) ───── */

/** Элемент селектора приложения (GET /api/backend-economics/backends). */
export interface BackendEconomicsBackend {
  id: string;
  code: string;
  name: string;
}

export interface BackendEconomicsBackendsResponse {
  items: BackendEconomicsBackend[];
}

/**
 * Границы клиентской валидации формы (ADR-072 §7.2). **Заморожены только имена ключей и
 * типы** — сами числа являются runtime-данными КАЖДОГО бэка и в коде НЕ хардкодятся.
 * Отсутствующий ключ ⇒ клиентская проверка по нему НЕ выполняется (полагаемся на `400`
 * бэка), форма при этом остаётся работоспособной. Незнакомый ключ игнорируется.
 *
 * ⚠️ Тип каждого ключа — `number | null`, а НЕ просто `number | undefined`: CRM
 * сериализует отсутствующую границу ЯВНЫМ `null` (`backend/app/schemas/
 * backend_economics.py:56-59` — поля объявлены `… | None = None`), поэтому проверять
 * границу обязательно через `!= null`; сравнение с `undefined` молча снимет проверку.
 */
export interface BackendEconomicsLimits {
  product_tokens_max?: number | null;
  product_avatar_tokens_max?: number | null;
  tariff_tokens_max?: number | null;
  tariff_decimal_places?: number | null;
}

/**
 * Конверт `capabilities` списков (ADR-072 §7). Право записи выводится ТОЛЬКО из
 * `features` (`products.write_tokens` / `pricing.write_tokens`), а НЕ из наличия поля
 * `tokens`. `capabilities: null` = «фич НЕ подтверждено» (любой неуспех подзапроса:
 * 404, таймаут, 5xx, 401/403, битый JSON) ⇒ модуль read-only при любом праве (fail-closed).
 *
 * ⚠️ **Обязательно ТОЛЬКО `features`** (04-api.md#backend-economics, ADR-072 §7.2 —
 * критерий обязательности: строгость оправдана лишь там, где значение используется).
 * `contract_version` и `cache_effective_after_seconds` CRM не показывает вовсе, `limits`
 * имеет определённое поведение при отсутствии — поэтому все три nullable, и CRM
 * сериализует их отсутствие явным `null` (`backend/app/schemas/backend_economics.py:82-85`
 * — `int | None = None`). Требовать их значило бы превращать безобидное умолчание
 * конформного бэка в `schema_mismatch` ⇒ `capabilities: null` ⇒ молча read-only страницу.
 */
export interface BackendEconomicsCapabilities {
  contract_version?: number | null;
  features: string[];
  limits?: BackendEconomicsLimits | null;
  cache_effective_after_seconds?: number | null;
}

/**
 * Продукт каталога (GET /api/backend-economics/{backend_id}/products, `scope=all`).
 * Все поля v1.1 — **nullable**: на бэке уровня v1 путь отвечает `200` без них, CRM
 * нормализует отсутствующее поле в `null` (ADR-072 §1.1). `tokens: null` ⇒ ячейка «—» и
 * read-only строка; `grantable: null` ⇒ «—», а НЕ «Нет».
 */
export interface BackendEconomicsProduct {
  product_id: string;
  name: string;
  price: string | null;
  period: string | null;
  tokens: number | null;
  avatar_tokens: number | null;
  grantable: boolean | null;
  /**
   * Contract v1.2 (ADR-073 §1): продукт скрыт с ВИТРИНЫ — на начисление и выдачу это НЕ
   * влияет. **Опционально для читателя**: `null`/отсутствует = у бэка нет самого понятия
   * архива ⇒ все продукты считаются активными, переключатель и контрол архива не
   * рендерятся (штатное состояние, НЕ ошибка). ⚠️ Сознательное отличие от правила
   * «`null` ≠ `false`» у `refunded`: там неизвестность — видимый оператору факт о
   * деньгах, здесь же единственное осмысленное поведение витрины — показать всё.
   */
  archived?: boolean | null;
  updated_at: string | null;
}

export interface BackendEconomicsProductsResponse {
  items: BackendEconomicsProduct[];
  capabilities: BackendEconomicsCapabilities | null;
}

export type BackendEconomicsTariffKind = 'chat' | 'photo' | 'video' | 'other';

/**
 * Тариф списания (GET /api/backend-economics/{backend_id}/pricing). `tariff_id` —
 * **opaque**: ключ строки и путь `PATCH`, разбирать его на части UI не вправе.
 * Асимметрия с `products` намеренная: путь существует только в v1.1, его поля обязательны.
 */
export interface BackendEconomicsTariff {
  tariff_id: string;
  kind: BackendEconomicsTariffKind;
  name: string | null;
  tokens: number;
  updated_at: string | null;
}

export interface BackendEconomicsPricingResponse {
  items: BackendEconomicsTariff[];
  capabilities: BackendEconomicsCapabilities | null;
}

/**
 * Тело PATCH …/products/{product_id}: хотя бы одно из ТРЁХ значимых полей —
 * `tokens` / `avatar_tokens` / `archived` (contract v1.2, ADR-073 §1).
 * `if_updated_at` значимым не считается — это значение `updated_at`, которое видел
 * оператор (защита от «двух операторов»); при `updated_at === null` ключ НЕ отправляется.
 * ⚠️ `archived: false` («вернуть из архива») — значимое значение: отбор идёт по наличию
 * ключа, а не по истинности, поэтому `false` обязано доходить до бэка.
 */
export interface UpdateBackendEconomicsProductRequest {
  tokens?: number;
  avatar_tokens?: number;
  archived?: boolean;
  if_updated_at?: string;
}

/** Тело PATCH …/pricing/{tariff_id}. */
export interface UpdateBackendEconomicsTariffRequest {
  tokens: number;
  if_updated_at?: string;
}

/**
 * Хвост ответа PATCH: дельта для тоста + задержка применения у бэка. **ОДИН тип на ОБА
 * эндпоинта модуля** — правка продукта и правка тарифа (ADR-073 §8 п.4: правило
 * симметрично, два правила разбора для двух почти одинаковых ответов — ловушка
 * сопровождения).
 *
 * ⚠️ **Все три поля ОПЦИОНАЛЬНЫ** (04-api.md, обе секции `PATCH`): ответ разбирается
 * ПОСЛЕ необратимого side-effect (значение у бэка уже изменено — и токены продукта, и
 * тариф списания), поэтому строгость означала бы «действие выполнено, оператору красная
 * ошибка, аудит молчит» (прецедент — ADR-057 §5). Обязанность бэка слать все три НЕ
 * снята — это толерантность читателя, а не разрешение опускать.
 *
 * Поведение при отсутствии (одинаковое для обоих эндпоинтов): `previous_tokens` → тост
 * БЕЗ дельты; `changed` → трактуется как «изменилось» (тост УСПЕХА, а не нейтральный —
 * иначе оператор решит, что правка не применилась, и повторит её);
 * `effective_after_seconds` → предложение о задержке опускается. Ни один из случаев НЕ
 * переводит страницу в ошибочное состояние.
 */
interface BackendEconomicsUpdateMeta {
  previous_tokens?: number | null;
  changed?: boolean | null;
  effective_after_seconds?: number | null;
}

export interface BackendEconomicsProductUpdateResponse
  extends BackendEconomicsProduct,
    BackendEconomicsUpdateMeta {}

export interface BackendEconomicsTariffUpdateResponse
  extends BackendEconomicsTariff,
    BackendEconomicsUpdateMeta {}

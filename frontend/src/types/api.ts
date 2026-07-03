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

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface MeResponse {
  username: string;
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

/** Единый формат ошибки API (04-api.md). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Array<{ field: string; message: string }> | null;
  };
}

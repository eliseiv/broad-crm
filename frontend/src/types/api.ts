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
}

/** Лёгкий статус проверки ключа (04-api.md, GET /api/ai-keys/{id}/status). */
export interface AiKeyStatusResponse {
  id: string;
  check_status: AiKeyStatus;
  error_message: string | null;
  last_checked_at: string | null;
}

/** Единый формат ошибки API (04-api.md). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Array<{ field: string; message: string }> | null;
  };
}

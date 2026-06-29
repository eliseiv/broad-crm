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

/** Единый формат ошибки API (04-api.md). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    details: Array<{ field: string; message: string }> | null;
  };
}

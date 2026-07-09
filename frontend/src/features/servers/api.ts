import { apiRequest } from '@/lib/api';
import type {
  BackendRefListResponse,
  CreateServerRequest,
  CreateServerResponse,
  ReorderServersRequest,
  SecretRevealResponse,
  ServersListResponse,
  StatusResponse,
  UpdateServerRequest,
  UpdateServerResponse,
} from '@/types/api';

export function listServers(signal?: AbortSignal): Promise<ServersListResponse> {
  return apiRequest<ServersListResponse>('/servers', { signal });
}

export function createServer(payload: CreateServerRequest): Promise<CreateServerResponse> {
  return apiRequest<CreateServerResponse>('/servers', { method: 'POST', body: payload });
}

export function updateServer(
  id: string,
  payload: UpdateServerRequest,
): Promise<UpdateServerResponse> {
  return apiRequest<UpdateServerResponse>(`/servers/${id}`, { method: 'PATCH', body: payload });
}

export function reorderServers(payload: ReorderServersRequest): Promise<void> {
  return apiRequest<void>('/servers/order', { method: 'PATCH', body: payload });
}

export function getServerStatus(id: string, signal?: AbortSignal): Promise<StatusResponse> {
  return apiRequest<StatusResponse>(`/servers/${id}/status`, { signal });
}

export function deleteServer(id: string): Promise<void> {
  return apiRequest<void>(`/servers/${id}`, { method: 'DELETE' });
}

/**
 * Reveal SSH-пароля по требованию (04-api.md, ADR-035). Гейт `servers:edit`.
 * Секрет НЕ кэшируется (вызывается напрямую из detail-модалки, не через react-query;
 * backend отдаёт `Cache-Control: no-store`) — значение живёт только в локальном стейте.
 */
export function revealServerPassword(
  id: string,
  signal?: AbortSignal,
): Promise<SecretRevealResponse> {
  return apiRequest<SecretRevealResponse>(`/servers/${id}/ssh-password`, { signal });
}

/**
 * Reverse-lookup: бэки, связанные с сервером (04-api.md, ADR-040). Гейт `servers:view`.
 * Ленивая загрузка при раскрытии секции «Бэки» detail-view сервера (свёрнутый счётчик —
 * `Server.backend_count`, без этого запроса).
 */
export function listServerBackends(
  id: string,
  signal?: AbortSignal,
): Promise<BackendRefListResponse> {
  return apiRequest<BackendRefListResponse>(`/servers/${id}/backends`, { signal });
}

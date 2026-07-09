import { apiRequest } from '@/lib/api';
import type {
  Backend,
  BackendListResponse,
  BackendStatusResponse,
  CreateBackendRequest,
  ReorderBackendsRequest,
  SecretRevealResponse,
  UpdateBackendRequest,
} from '@/types/api';

export function listBackends(signal?: AbortSignal): Promise<BackendListResponse> {
  return apiRequest<BackendListResponse>('/backends', { signal });
}

/** POST /api/backends → 202 с полным `BackendListItem` (04-api.md). */
export function createBackend(payload: CreateBackendRequest): Promise<Backend> {
  return apiRequest<Backend>('/backends', { method: 'POST', body: payload });
}

/** PATCH /api/backends/{id} → 200 с обновлённым `BackendListItem` (04-api.md). */
export function updateBackend(id: string, payload: UpdateBackendRequest): Promise<Backend> {
  return apiRequest<Backend>(`/backends/${id}`, { method: 'PATCH', body: payload });
}

export function reorderBackends(payload: ReorderBackendsRequest): Promise<void> {
  return apiRequest<void>('/backends/order', { method: 'PATCH', body: payload });
}

export function getBackendStatus(id: string, signal?: AbortSignal): Promise<BackendStatusResponse> {
  return apiRequest<BackendStatusResponse>(`/backends/${id}/status`, { signal });
}

export function deleteBackend(id: string): Promise<void> {
  return apiRequest<void>(`/backends/${id}`, { method: 'DELETE' });
}

/**
 * Reveal API KEY бэка по требованию (04-api.md, ADR-040/ADR-035). Гейт `backends:edit`.
 * Секрет НЕ кэшируется (вызывается напрямую из detail-модалки, не через react-query;
 * backend отдаёт `Cache-Control: no-store`) — значение живёт только в локальном стейте.
 */
export function revealBackendApiKey(
  id: string,
  signal?: AbortSignal,
): Promise<SecretRevealResponse> {
  return apiRequest<SecretRevealResponse>(`/backends/${id}/api-key`, { signal });
}

/** Reveal ADMIN API KEY бэка по требованию (04-api.md, ADR-040). Гейт `backends:edit`. */
export function revealBackendAdminApiKey(
  id: string,
  signal?: AbortSignal,
): Promise<SecretRevealResponse> {
  return apiRequest<SecretRevealResponse>(`/backends/${id}/admin-api-key`, { signal });
}

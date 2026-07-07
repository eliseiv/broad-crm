import { apiRequest } from '@/lib/api';
import type {
  Backend,
  BackendListResponse,
  BackendStatusResponse,
  CreateBackendRequest,
  ReorderBackendsRequest,
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

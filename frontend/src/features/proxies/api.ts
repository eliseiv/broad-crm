import { apiRequest } from '@/lib/api';
import type {
  CreateProxyRequest,
  Proxy,
  ProxyListResponse,
  ProxyStatusResponse,
  ReorderProxiesRequest,
  UpdateProxyRequest,
} from '@/types/api';

export function listProxies(signal?: AbortSignal): Promise<ProxyListResponse> {
  return apiRequest<ProxyListResponse>('/proxies', { signal });
}

/** POST /api/proxies → 202 с полным `ProxyListItem` (04-api.md). */
export function createProxy(payload: CreateProxyRequest): Promise<Proxy> {
  return apiRequest<Proxy>('/proxies', { method: 'POST', body: payload });
}

/** PATCH /api/proxies/{id} → 200 с обновлённым `ProxyListItem` (04-api.md). */
export function updateProxy(id: string, payload: UpdateProxyRequest): Promise<Proxy> {
  return apiRequest<Proxy>(`/proxies/${id}`, { method: 'PATCH', body: payload });
}

export function reorderProxies(payload: ReorderProxiesRequest): Promise<void> {
  return apiRequest<void>('/proxies/order', { method: 'PATCH', body: payload });
}

export function getProxyStatus(id: string, signal?: AbortSignal): Promise<ProxyStatusResponse> {
  return apiRequest<ProxyStatusResponse>(`/proxies/${id}/status`, { signal });
}

export function deleteProxy(id: string): Promise<void> {
  return apiRequest<void>(`/proxies/${id}`, { method: 'DELETE' });
}

import { apiRequest } from '@/lib/api';
import type {
  CreateServerRequest,
  CreateServerResponse,
  ReorderServersRequest,
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

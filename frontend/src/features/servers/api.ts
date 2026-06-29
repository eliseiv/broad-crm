import { apiRequest } from '@/lib/api';
import type {
  CreateServerRequest,
  CreateServerResponse,
  ServersListResponse,
  StatusResponse,
} from '@/types/api';

export function listServers(signal?: AbortSignal): Promise<ServersListResponse> {
  return apiRequest<ServersListResponse>('/servers', { signal });
}

export function createServer(payload: CreateServerRequest): Promise<CreateServerResponse> {
  return apiRequest<CreateServerResponse>('/servers', { method: 'POST', body: payload });
}

export function getServerStatus(id: string, signal?: AbortSignal): Promise<StatusResponse> {
  return apiRequest<StatusResponse>(`/servers/${id}/status`, { signal });
}

export function deleteServer(id: string): Promise<void> {
  return apiRequest<void>(`/servers/${id}`, { method: 'DELETE' });
}

import { apiRequest } from '@/lib/api';
import type {
  AiKeyStatusResponse,
  AiKeysListResponse,
  CreateAiKeyRequest,
  CreateAiKeyResponse,
} from '@/types/api';

export function listAiKeys(signal?: AbortSignal): Promise<AiKeysListResponse> {
  return apiRequest<AiKeysListResponse>('/ai-keys', { signal });
}

export function createAiKey(payload: CreateAiKeyRequest): Promise<CreateAiKeyResponse> {
  return apiRequest<CreateAiKeyResponse>('/ai-keys', { method: 'POST', body: payload });
}

export function getAiKeyStatus(id: string, signal?: AbortSignal): Promise<AiKeyStatusResponse> {
  return apiRequest<AiKeyStatusResponse>(`/ai-keys/${id}/status`, { signal });
}

export function deleteAiKey(id: string): Promise<void> {
  return apiRequest<void>(`/ai-keys/${id}`, { method: 'DELETE' });
}

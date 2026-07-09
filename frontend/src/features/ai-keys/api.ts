import { apiRequest } from '@/lib/api';
import type {
  AiKey,
  AiKeyStatusResponse,
  AiKeysListResponse,
  BackendRefListResponse,
  CreateAiKeyRequest,
  CreateAiKeyResponse,
  ReorderAiKeysRequest,
  SecretRevealResponse,
  UpdateAiKeyRequest,
} from '@/types/api';

export function listAiKeys(signal?: AbortSignal): Promise<AiKeysListResponse> {
  return apiRequest<AiKeysListResponse>('/ai-keys', { signal });
}

export function createAiKey(payload: CreateAiKeyRequest): Promise<CreateAiKeyResponse> {
  return apiRequest<CreateAiKeyResponse>('/ai-keys', { method: 'POST', body: payload });
}

export function updateAiKey(id: string, payload: UpdateAiKeyRequest): Promise<AiKey> {
  return apiRequest<AiKey>(`/ai-keys/${id}`, { method: 'PATCH', body: payload });
}

export function reorderAiKeys(payload: ReorderAiKeysRequest): Promise<void> {
  return apiRequest<void>('/ai-keys/order', { method: 'PATCH', body: payload });
}

export function getAiKeyStatus(id: string, signal?: AbortSignal): Promise<AiKeyStatusResponse> {
  return apiRequest<AiKeyStatusResponse>(`/ai-keys/${id}/status`, { signal });
}

export function deleteAiKey(id: string): Promise<void> {
  return apiRequest<void>(`/ai-keys/${id}`, { method: 'DELETE' });
}

/**
 * Reveal полного ключа по требованию (04-api.md, ADR-035). Гейт `ai-keys:edit`.
 * Секрет НЕ кэшируется — вызывается напрямую из detail-модалки, живёт только в
 * локальном стейте (в обычных ответах — только `key_masked`).
 */
export function revealAiKeyValue(id: string, signal?: AbortSignal): Promise<SecretRevealResponse> {
  return apiRequest<SecretRevealResponse>(`/ai-keys/${id}/key`, { signal });
}

/**
 * Reverse-lookup: бэки, использующие ИИ-ключ (04-api.md, ADR-040). Гейт `ai-keys:view`.
 * Ленивая загрузка при раскрытии секции «Бэки» detail-view ключа (свёрнутый счётчик —
 * `AiKey.backend_count`, без этого запроса).
 */
export function listAiKeyBackends(
  id: string,
  signal?: AbortSignal,
): Promise<BackendRefListResponse> {
  return apiRequest<BackendRefListResponse>(`/ai-keys/${id}/backends`, { signal });
}

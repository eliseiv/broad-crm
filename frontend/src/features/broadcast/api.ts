import { apiRequest } from '@/lib/api';
import type {
  BroadcastAudienceResponse,
  BroadcastCreateRequest,
  BroadcastCreateResponse,
} from '@/types/api';

/** GET /api/broadcasts/audience — роли и счётчики (гейт broadcast:view). */
export function getBroadcastAudience(signal?: AbortSignal): Promise<BroadcastAudienceResponse> {
  return apiRequest<BroadcastAudienceResponse>('/broadcasts/audience', { signal });
}

/** POST /api/broadcasts — fan-out (гейт broadcast:send). */
export function createBroadcast(payload: BroadcastCreateRequest): Promise<BroadcastCreateResponse> {
  return apiRequest<BroadcastCreateResponse>('/broadcasts', { method: 'POST', body: payload });
}

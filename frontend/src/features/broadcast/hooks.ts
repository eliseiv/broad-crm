import { useMutation, useQuery } from '@tanstack/react-query';
import { createBroadcast, getBroadcastAudience } from '@/features/broadcast/api';
import type { BroadcastCreateRequest } from '@/types/api';

export const broadcastAudienceKey = ['broadcasts', 'audience'] as const;

/** Аудитория чекбоксов (GET /api/broadcasts/audience). Не admin-gated /api/roles. */
export function useBroadcastAudience() {
  return useQuery({
    queryKey: broadcastAudienceKey,
    queryFn: ({ signal }) => getBroadcastAudience(signal),
  });
}

/** Отправка рассылки (POST /api/broadcasts). */
export function useCreateBroadcast() {
  return useMutation({
    mutationFn: (payload: BroadcastCreateRequest) => createBroadcast(payload),
  });
}

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createAiKey, deleteAiKey, getAiKeyStatus, listAiKeys } from '@/features/ai-keys/api';
import { env } from '@/lib/env';
import type { AiKeyStatus, AiKeyStatusResponse, CreateAiKeyRequest } from '@/types/api';

export const aiKeysKey = ['ai-keys'] as const;
export const aiKeyStatusKey = (id: string) => ['ai-key-status', id] as const;

/**
 * Routine-опрос списка ключей: единственный запрос GET /api/ai-keys с refetchInterval
 * (по образцу useServers, modules/ai-keys — зеркало servers).
 */
export function useAiKeys() {
  return useQuery({
    queryKey: aiKeysKey,
    queryFn: ({ signal }) => listAiKeys(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
  });
}

function isPending(status: AiKeyStatus | undefined): boolean {
  return status === 'pending';
}

/**
 * Status-polling ТОЛЬКО пока проверка ключа в состоянии pending.
 * Останавливается при working/error (по образцу useServerStatus).
 */
export function useAiKeyStatus(id: string, initialStatus: AiKeyStatus) {
  return useQuery<AiKeyStatusResponse>({
    queryKey: aiKeyStatusKey(id),
    queryFn: ({ signal }) => getAiKeyStatus(id, signal),
    enabled: isPending(initialStatus),
    refetchInterval: (query) => {
      const status = query.state.data?.check_status ?? initialStatus;
      return isPending(status) ? env.statusPollIntervalMs : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useCreateAiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateAiKeyRequest) => createAiKey(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: aiKeysKey });
    },
  });
}

export function useDeleteAiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteAiKey(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: aiKeysKey });
    },
  });
}

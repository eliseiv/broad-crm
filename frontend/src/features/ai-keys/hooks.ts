import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  createAiKey,
  deleteAiKey,
  getAiKeyStatus,
  listAiKeyBackends,
  listAiKeys,
  reorderAiKeys,
  updateAiKey,
} from '@/features/ai-keys/api';
import { env } from '@/lib/env';
import type {
  AiKey,
  AiKeysListResponse,
  AiKeyStatus,
  AiKeyStatusResponse,
  AiProvider,
  CreateAiKeyRequest,
  ReorderAiKeysRequest,
  UpdateAiKeyRequest,
} from '@/types/api';

export const aiKeysKey = ['ai-keys'] as const;
export const aiKeyStatusKey = (id: string) => ['ai-key-status', id] as const;
export const aiKeyBackendsKey = (id: string) => ['ai-key-backends', id] as const;

/**
 * Ленивый reverse-lookup «Бэки ключа» (ADR-040): запрос уходит только при раскрытии
 * секции (`enabled`). Своё состояние loading/empty/error внутри секции detail-модалки.
 */
export function useAiKeyBackends(id: string, enabled: boolean) {
  return useQuery({
    queryKey: aiKeyBackendsKey(id),
    queryFn: ({ signal }) => listAiKeyBackends(id, signal),
    enabled,
    retry: false,
  });
}

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

export function useUpdateAiKey(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateAiKeyRequest) => updateAiKey(id, payload),
    onSuccess: () => {
      // Инвалидация вернёт свежий check_status; при provider/key → 'pending',
      // карточка возобновит polling через useAiKeyStatus (08-design-system.md).
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

/**
 * Перестановка AI-ключей ВНУТРИ одной провайдер-группы с оптимистичным обновлением.
 * onMutate: перезаписываем position ключей этого провайдера; onError: откат + toast;
 * onSettled: invalidate GET /api/ai-keys (08-design-system.md, 04-api.md).
 */
export function useReorderAiKeys() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, ReorderAiKeysRequest, { previous?: AiKeysListResponse }>({
    mutationFn: (payload) => reorderAiKeys(payload),
    onMutate: async ({ provider, ids }: { provider: AiProvider; ids: string[] }) => {
      await queryClient.cancelQueries({ queryKey: aiKeysKey });
      const previous = queryClient.getQueryData<AiKeysListResponse>(aiKeysKey);
      if (previous) {
        const items: AiKey[] = previous.items.map((k) =>
          k.provider === provider ? { ...k, position: ids.indexOf(k.id) } : k,
        );
        queryClient.setQueryData<AiKeysListResponse>(aiKeysKey, { ...previous, items });
      }
      return { previous };
    },
    onError: (_err, _payload, context) => {
      if (context?.previous) {
        queryClient.setQueryData(aiKeysKey, context.previous);
      }
      toast.error('Не удалось сохранить порядок');
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: aiKeysKey });
    },
  });
}

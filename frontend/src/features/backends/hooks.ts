import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  createBackend,
  deleteBackend,
  getBackendStatus,
  listBackends,
  reorderBackends,
  updateBackend,
} from '@/features/backends/api';
import { env } from '@/lib/env';
import type {
  Backend,
  BackendCheckStatus,
  BackendListResponse,
  BackendStatusResponse,
  CreateBackendRequest,
  UpdateBackendRequest,
} from '@/types/api';

export const backendsKey = ['backends'] as const;
export const backendStatusKey = (id: string) => ['backend-status', id] as const;

/**
 * Routine-опрос списка бэков: единственный запрос GET /api/backends с refetchInterval
 * (по образцу useProxies/useServers).
 */
export function useBackends() {
  return useQuery({
    queryKey: backendsKey,
    queryFn: ({ signal }) => listBackends(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
  });
}

function isPending(status: BackendCheckStatus | undefined): boolean {
  return status === 'pending';
}

/**
 * Status-polling ТОЛЬКО пока проверка бэка в состоянии pending.
 * Останавливается при working/error (по образцу useProxyStatus).
 */
export function useBackendStatus(id: string, initialStatus: BackendCheckStatus) {
  return useQuery<BackendStatusResponse>({
    queryKey: backendStatusKey(id),
    queryFn: ({ signal }) => getBackendStatus(id, signal),
    enabled: isPending(initialStatus),
    refetchInterval: (query) => {
      const status = query.state.data?.check_status ?? initialStatus;
      return isPending(status) ? env.statusPollIntervalMs : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useCreateBackend() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateBackendRequest) => createBackend(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: backendsKey });
    },
  });
}

export function useUpdateBackend(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateBackendRequest) => updateBackend(id, payload),
    onSuccess: () => {
      // Инвалидация вернёт свежий check_status; при смене domain → 'pending',
      // карточка возобновит polling через useBackendStatus (08-design-system.md).
      void queryClient.invalidateQueries({ queryKey: backendsKey });
    },
  });
}

export function useDeleteBackend() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteBackend(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: backendsKey });
    },
  });
}

/**
 * Перестановка бэков drag-and-drop (единый список) с оптимистичным обновлением кэша.
 * onMutate: перезаписываем порядок (и position) в кэше GET /api/backends;
 * onError: откат + toast «Не удалось сохранить порядок»; onSettled: invalidate
 * (канонический порядок от backend). 08-design-system.md, 04-api.md.
 */
export function useReorderBackends() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, string[], { previous?: BackendListResponse }>({
    mutationFn: (ids: string[]) => reorderBackends({ ids }),
    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: backendsKey });
      const previous = queryClient.getQueryData<BackendListResponse>(backendsKey);
      if (previous) {
        const byId = new Map(previous.items.map((b) => [b.id, b]));
        const items = ids
          .map((id, index) => {
            const backend = byId.get(id);
            return backend ? { ...backend, position: index } : undefined;
          })
          .filter((b): b is Backend => b !== undefined);
        queryClient.setQueryData<BackendListResponse>(backendsKey, { ...previous, items });
      }
      return { previous };
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) {
        queryClient.setQueryData(backendsKey, context.previous);
      }
      toast.error('Не удалось сохранить порядок');
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: backendsKey });
    },
  });
}

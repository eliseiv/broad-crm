import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  createProxy,
  deleteProxy,
  getProxyStatus,
  listProxies,
  reorderProxies,
  updateProxy,
} from '@/features/proxies/api';
import { env } from '@/lib/env';
import type {
  CreateProxyRequest,
  Proxy,
  ProxyCheckStatus,
  ProxyListResponse,
  ProxyStatusResponse,
  UpdateProxyRequest,
} from '@/types/api';

export const proxiesKey = ['proxies'] as const;
export const proxyStatusKey = (id: string) => ['proxy-status', id] as const;

/**
 * Routine-опрос списка прокси: единственный запрос GET /api/proxies с refetchInterval
 * (по образцу useServers/useAiKeys).
 */
export function useProxies() {
  return useQuery({
    queryKey: proxiesKey,
    queryFn: ({ signal }) => listProxies(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
  });
}

function isPending(status: ProxyCheckStatus | undefined): boolean {
  return status === 'pending';
}

/**
 * Status-polling ТОЛЬКО пока проверка прокси в состоянии pending.
 * Останавливается при working/error (по образцу useAiKeyStatus).
 */
export function useProxyStatus(id: string, initialStatus: ProxyCheckStatus) {
  return useQuery<ProxyStatusResponse>({
    queryKey: proxyStatusKey(id),
    queryFn: ({ signal }) => getProxyStatus(id, signal),
    enabled: isPending(initialStatus),
    refetchInterval: (query) => {
      const status = query.state.data?.check_status ?? initialStatus;
      return isPending(status) ? env.statusPollIntervalMs : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useCreateProxy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateProxyRequest) => createProxy(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: proxiesKey });
    },
  });
}

export function useUpdateProxy(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateProxyRequest) => updateProxy(id, payload),
    onSuccess: () => {
      // Инвалидация вернёт свежий check_status; при смене связанного с подключением
      // поля → 'pending', карточка возобновит polling через useProxyStatus (08-design-system.md).
      void queryClient.invalidateQueries({ queryKey: proxiesKey });
    },
  });
}

export function useDeleteProxy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProxy(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: proxiesKey });
    },
  });
}

/**
 * Перестановка прокси drag-and-drop (единый список) с оптимистичным обновлением кэша.
 * onMutate: перезаписываем порядок (и position) в кэше GET /api/proxies;
 * onError: откат + toast «Не удалось сохранить порядок»; onSettled: invalidate
 * (канонический порядок от backend). 08-design-system.md, 04-api.md.
 */
export function useReorderProxies() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, string[], { previous?: ProxyListResponse }>({
    mutationFn: (ids: string[]) => reorderProxies({ ids }),
    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: proxiesKey });
      const previous = queryClient.getQueryData<ProxyListResponse>(proxiesKey);
      if (previous) {
        const byId = new Map(previous.items.map((p) => [p.id, p]));
        const items = ids
          .map((id, index) => {
            const proxy = byId.get(id);
            return proxy ? { ...proxy, position: index } : undefined;
          })
          .filter((p): p is Proxy => p !== undefined);
        queryClient.setQueryData<ProxyListResponse>(proxiesKey, { ...previous, items });
      }
      return { previous };
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) {
        queryClient.setQueryData(proxiesKey, context.previous);
      }
      toast.error('Не удалось сохранить порядок');
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: proxiesKey });
    },
  });
}

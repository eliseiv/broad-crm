import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  createServer,
  deleteServer,
  getServerStatus,
  listServers,
  reorderServers,
  updateServer,
} from '@/features/servers/api';
import { env } from '@/lib/env';
import type {
  CreateServerRequest,
  ProvisionStatus,
  Server,
  ServersListResponse,
  StatusResponse,
  UpdateServerRequest,
} from '@/types/api';

export const serversKey = ['servers'] as const;
export const serverStatusKey = (id: string) => ['server-status', id] as const;

/**
 * Routine-метрики: единственный запрос GET /api/servers с refetchInterval.
 * Per-card /metrics в цикле НЕ используется (modules/ui/README.md §4).
 */
export function useServers() {
  return useQuery({
    queryKey: serversKey,
    queryFn: ({ signal }) => listServers(signal),
    refetchInterval: env.pollIntervalMs,
    refetchIntervalInBackground: false,
    staleTime: env.pollIntervalMs,
  });
}

const PROVISIONING: ProvisionStatus[] = ['pending', 'installing'];

function isProvisioning(status: ProvisionStatus | undefined): boolean {
  return status !== undefined && PROVISIONING.includes(status);
}

/**
 * Status-polling ТОЛЬКО во время провижининга (pending/installing).
 * Останавливается при online/error. Запускается через `enabled`.
 */
export function useServerStatus(id: string, initialStatus: ProvisionStatus) {
  return useQuery<StatusResponse>({
    queryKey: serverStatusKey(id),
    queryFn: ({ signal }) => getServerStatus(id, signal),
    enabled: isProvisioning(initialStatus),
    refetchInterval: (query) => {
      const status = query.state.data?.provision_status ?? initialStatus;
      return isProvisioning(status) ? env.statusPollIntervalMs : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useCreateServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateServerRequest) => createServer(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    },
  });
}

export function useUpdateServer(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: UpdateServerRequest) => updateServer(id, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    },
  });
}

export function useDeleteServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteServer(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    },
  });
}

/**
 * Перестановка серверов drag-and-drop с оптимистичным обновлением кэша.
 * onMutate: перезаписываем порядок (и position) в кэше GET /api/servers;
 * onError: откат + toast «Не удалось сохранить порядок»; onSettled: invalidate
 * (канонический порядок + свежие метрики от backend). 08-design-system.md.
 */
export function useReorderServers() {
  const queryClient = useQueryClient();
  return useMutation<void, unknown, string[], { previous?: ServersListResponse }>({
    mutationFn: (ids: string[]) => reorderServers({ ids }),
    onMutate: async (ids) => {
      await queryClient.cancelQueries({ queryKey: serversKey });
      const previous = queryClient.getQueryData<ServersListResponse>(serversKey);
      if (previous) {
        const byId = new Map(previous.items.map((s) => [s.id, s]));
        const items = ids
          .map((id, index) => {
            const server = byId.get(id);
            return server ? { ...server, position: index } : undefined;
          })
          .filter((s): s is Server => s !== undefined);
        queryClient.setQueryData<ServersListResponse>(serversKey, { ...previous, items });
      }
      return { previous };
    },
    onError: (_err, _ids, context) => {
      if (context?.previous) {
        queryClient.setQueryData(serversKey, context.previous);
      }
      toast.error('Не удалось сохранить порядок');
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    },
  });
}

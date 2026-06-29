import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createServer, deleteServer, getServerStatus, listServers } from '@/features/servers/api';
import { env } from '@/lib/env';
import type { CreateServerRequest, ProvisionStatus, StatusResponse } from '@/types/api';

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

export function useDeleteServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteServer(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    },
  });
}

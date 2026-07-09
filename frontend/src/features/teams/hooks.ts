import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createTeam, deleteTeam, listTeams, updateTeam } from '@/features/teams/api';
import { usersKey } from '@/features/users/hooks';
import type { TeamCreateRequest, TeamUpdateRequest } from '@/types/api';

export const teamsKey = ['teams'] as const;

/**
 * Список CRM-команд. `enabled` (default `true`) позволяет вызывающему отключить
 * запрос, когда команды не нужны/недоступны (напр. фильтр «Все команды» на /sms
 * рендерится только admin-уровню, ADR-036 — прочим ролям /api/teams вернул бы 403).
 */
export function useTeams(enabled = true) {
  return useQuery({
    queryKey: teamsKey,
    queryFn: ({ signal }) => listTeams(signal),
    enabled,
  });
}

export function useCreateTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TeamCreateRequest) => createTeam(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: teamsKey });
      // Членство влияет на группировку списка «Пользователи» по командам.
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

export function useUpdateTeam(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TeamUpdateRequest) => updateTeam(id, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: teamsKey });
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

export function useDeleteTeam() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteTeam(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: teamsKey });
      void queryClient.invalidateQueries({ queryKey: usersKey });
    },
  });
}

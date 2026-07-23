import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  addBackendUserTokens,
  getBackendUser,
  grantBackendUserSubscription,
  listBackendProducts,
  listBackendUserPayments,
  listBackendUserRequests,
  listBackendUsers,
  type BackendUsersListParams,
} from '@/features/backend-users/api';
import type { AddBackendUserTokensRequest, GrantBackendUserSubscriptionRequest } from '@/types/api';

export const backendUsersKey = (params: BackendUsersListParams) =>
  ['backend-users', params] as const;
export const backendUserKey = (backendId: string, userId: string) =>
  ['backend-user', backendId, userId] as const;

/**
 * Список пользователей бэков. `placeholderData: keepPreviousData` — при смене
 * страницы/фильтра таблица не мигает пустотой (показывает прежние данные).
 */
export function useBackendUsers(params: BackendUsersListParams) {
  return useQuery({
    queryKey: backendUsersKey(params),
    queryFn: ({ signal }) => listBackendUsers(params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useBackendUser(backendId: string, userId: string) {
  return useQuery({
    queryKey: backendUserKey(backendId, userId),
    queryFn: ({ signal }) => getBackendUser(backendId, userId, signal),
  });
}

export function useBackendUserPayments(backendId: string, userId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...backendUserKey(backendId, userId), 'payments'] as const,
    queryFn: ({ signal }) => listBackendUserPayments(backendId, userId, signal),
    enabled,
  });
}

export function useBackendUserRequests(backendId: string, userId: string, enabled: boolean) {
  return useQuery({
    queryKey: [...backendUserKey(backendId, userId), 'requests'] as const,
    queryFn: ({ signal }) => listBackendUserRequests(backendId, userId, signal),
    enabled,
  });
}

/** Тарифы бэка — только когда открыта модалка «Установить план» (`enabled`). */
export function useBackendProducts(backendId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['backend-products', backendId] as const,
    queryFn: ({ signal }) => listBackendProducts(backendId, signal),
    enabled,
  });
}

/** Инвалидация карточки пользователя + всех страниц списка после admin-операции. */
function useInvalidateBackendUser(backendId: string, userId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: backendUserKey(backendId, userId) });
    void queryClient.invalidateQueries({ queryKey: ['backend-users'] });
  };
}

export function useAddBackendUserTokens(backendId: string, userId: string) {
  const invalidate = useInvalidateBackendUser(backendId, userId);
  return useMutation({
    mutationFn: (payload: AddBackendUserTokensRequest) =>
      addBackendUserTokens(backendId, userId, payload),
    onSuccess: invalidate,
  });
}

export function useGrantBackendUserSubscription(backendId: string, userId: string) {
  const invalidate = useInvalidateBackendUser(backendId, userId);
  return useMutation({
    mutationFn: (payload: GrantBackendUserSubscriptionRequest) =>
      grantBackendUserSubscription(backendId, userId, payload),
    onSuccess: invalidate,
  });
}

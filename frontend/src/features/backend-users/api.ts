import { apiRequest } from '@/lib/api';
import type {
  AddBackendUserTokensRequest,
  BackendProductsResponse,
  BackendUserDetail,
  BackendUserGrantResponse,
  BackendUserPaymentsResponse,
  BackendUserRequestsResponse,
  BackendUsersListResponse,
  BackendUserTokensResponse,
  GrantBackendUserSubscriptionRequest,
} from '@/types/api';

/** Параметры GET /api/backend-users (04-api.md#backend-users). */
export interface BackendUsersListParams {
  backendId: string | null;
  search: string;
  dateFrom: string | null;
  dateTo: string | null;
  isPaid: boolean | null;
  limit: number;
  offset: number;
}

export function listBackendUsers(
  params: BackendUsersListParams,
  signal?: AbortSignal,
): Promise<BackendUsersListResponse> {
  const query = new URLSearchParams();
  if (params.backendId) query.set('backend_id', params.backendId);
  if (params.search) query.set('search', params.search);
  if (params.dateFrom) query.set('date_from', params.dateFrom);
  if (params.dateTo) query.set('date_to', params.dateTo);
  if (params.isPaid !== null) query.set('is_paid', String(params.isPaid));
  query.set('limit', String(params.limit));
  query.set('offset', String(params.offset));
  return apiRequest<BackendUsersListResponse>(`/backend-users?${query.toString()}`, { signal });
}

export function getBackendUser(
  backendId: string,
  userId: string,
  signal?: AbortSignal,
): Promise<BackendUserDetail> {
  return apiRequest<BackendUserDetail>(
    `/backend-users/${backendId}/users/${encodeURIComponent(userId)}`,
    { signal },
  );
}

export function listBackendUserPayments(
  backendId: string,
  userId: string,
  signal?: AbortSignal,
): Promise<BackendUserPaymentsResponse> {
  return apiRequest<BackendUserPaymentsResponse>(
    `/backend-users/${backendId}/users/${encodeURIComponent(userId)}/payments`,
    { signal },
  );
}

export function listBackendUserRequests(
  backendId: string,
  userId: string,
  signal?: AbortSignal,
): Promise<BackendUserRequestsResponse> {
  return apiRequest<BackendUserRequestsResponse>(
    `/backend-users/${backendId}/users/${encodeURIComponent(userId)}/requests`,
    { signal },
  );
}

export function listBackendProducts(
  backendId: string,
  signal?: AbortSignal,
): Promise<BackendProductsResponse> {
  return apiRequest<BackendProductsResponse>(`/backend-users/${backendId}/products`, { signal });
}

/** POST токенов — НЕ идемпотентен (contract v1 §3.1): вызывать строго один раз на сабмит. */
export function addBackendUserTokens(
  backendId: string,
  userId: string,
  payload: AddBackendUserTokensRequest,
): Promise<BackendUserTokensResponse> {
  return apiRequest<BackendUserTokensResponse>(
    `/backend-users/${backendId}/users/${encodeURIComponent(userId)}/tokens`,
    { method: 'POST', body: payload },
  );
}

/** POST подписки — идемпотентен по `grant_id` (contract v1 §3.2). */
export function grantBackendUserSubscription(
  backendId: string,
  userId: string,
  payload: GrantBackendUserSubscriptionRequest,
): Promise<BackendUserGrantResponse> {
  return apiRequest<BackendUserGrantResponse>(
    `/backend-users/${backendId}/users/${encodeURIComponent(userId)}/subscription`,
    { method: 'POST', body: payload },
  );
}

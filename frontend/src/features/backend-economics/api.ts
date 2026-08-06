import { apiRequest } from '@/lib/api';
import type {
  BackendEconomicsBackendsResponse,
  BackendEconomicsPricingResponse,
  BackendEconomicsProductsResponse,
  BackendEconomicsProductUpdateResponse,
  BackendEconomicsTariffUpdateResponse,
  UpdateBackendEconomicsProductRequest,
  UpdateBackendEconomicsTariffRequest,
} from '@/types/api';

/**
 * Клиент модуля «Продукты и тарифы» (04-api.md#backend-economics, ADR-072).
 * CRM — прокси без собственного хранилища: списки читаются на лету, правка уходит
 * прямо в бэк. Все пути — под гейтом `backend-economics:view` / `:edit` (сервер).
 */

/**
 * Список бэков с заданным Admin API Key — опции селектора приложения. Гейт эндпоинта —
 * `backend-economics:view` (НЕ `backends:view`): селектор страницы не зависит от чужого
 * права, режима «Все приложения» здесь нет.
 */
export function listBackendEconomicsBackends(
  signal?: AbortSignal,
): Promise<BackendEconomicsBackendsResponse> {
  return apiRequest<BackendEconomicsBackendsResponse>('/backend-economics/backends', { signal });
}

/** Полный каталог продуктов бэка (`scope=all` шлёт CRM) + конверт `capabilities`. */
export function listBackendEconomicsProducts(
  backendId: string,
  signal?: AbortSignal,
): Promise<BackendEconomicsProductsResponse> {
  return apiRequest<BackendEconomicsProductsResponse>(`/backend-economics/${backendId}/products`, {
    signal,
  });
}

/** Тарифы списания за генерацию + тот же конверт `capabilities`. */
export function listBackendEconomicsPricing(
  backendId: string,
  signal?: AbortSignal,
): Promise<BackendEconomicsPricingResponse> {
  return apiRequest<BackendEconomicsPricingResponse>(`/backend-economics/${backendId}/pricing`, {
    signal,
  });
}

/**
 * Правка токенов продукта. **Идемпотентен** (устанавливает значение, а не дельту) —
 * ключ идемпотентности не нужен; повтор того же значения → `changed: false`.
 */
export function updateBackendEconomicsProduct(
  backendId: string,
  productId: string,
  payload: UpdateBackendEconomicsProductRequest,
): Promise<BackendEconomicsProductUpdateResponse> {
  return apiRequest<BackendEconomicsProductUpdateResponse>(
    `/backend-economics/${backendId}/products/${encodeURIComponent(productId)}`,
    { method: 'PATCH', body: payload },
  );
}

/** Правка тарифа списания. `tariff_id` — opaque-ключ пути (интерпретации не подлежит). */
export function updateBackendEconomicsTariff(
  backendId: string,
  tariffId: string,
  payload: UpdateBackendEconomicsTariffRequest,
): Promise<BackendEconomicsTariffUpdateResponse> {
  return apiRequest<BackendEconomicsTariffUpdateResponse>(
    `/backend-economics/${backendId}/pricing/${encodeURIComponent(tariffId)}`,
    { method: 'PATCH', body: payload },
  );
}

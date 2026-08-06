import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listBackendEconomicsBackends,
  listBackendEconomicsPricing,
  listBackendEconomicsProducts,
  updateBackendEconomicsProduct,
  updateBackendEconomicsTariff,
} from '@/features/backend-economics/api';
import type {
  UpdateBackendEconomicsProductRequest,
  UpdateBackendEconomicsTariffRequest,
} from '@/types/api';

export const backendEconomicsBackendsKey = ['backend-economics', 'backends'] as const;
export const backendEconomicsProductsKey = (backendId: string) =>
  ['backend-economics', 'products', backendId] as const;
export const backendEconomicsPricingKey = (backendId: string) =>
  ['backend-economics', 'pricing', backendId] as const;

/** Опции селектора приложения. Без выбранного бэка обе таблицы не запрашиваются. */
export function useBackendEconomicsBackends() {
  return useQuery({
    queryKey: backendEconomicsBackendsKey,
    queryFn: ({ signal }) => listBackendEconomicsBackends(signal),
  });
}

/**
 * Каталог продуктов выбранного бэка. Состояния таблиц НЕЗАВИСИМЫ (08-design-system.md):
 * отказ `pricing` не гасит `products`, поэтому это два отдельных запроса, а не один.
 * `retry: false` — коды деградации (`…extension_not_supported`, `…key_not_set`) —
 * устойчивые состояния, повтор их не изменит.
 */
export function useBackendEconomicsProducts(backendId: string) {
  return useQuery({
    queryKey: backendEconomicsProductsKey(backendId),
    queryFn: ({ signal }) => listBackendEconomicsProducts(backendId, signal),
    enabled: Boolean(backendId),
    retry: false,
  });
}

/** Тарифы списания выбранного бэка (независимо от таблицы продуктов). */
export function useBackendEconomicsPricing(backendId: string) {
  return useQuery({
    queryKey: backendEconomicsPricingKey(backendId),
    queryFn: ({ signal }) => listBackendEconomicsPricing(backendId, signal),
    enabled: Boolean(backendId),
    retry: false,
  });
}

/**
 * Правка токенов продукта. Инвалидация задевает и кэш продуктов страницы «Юзеры бэков»
 * (`['backend-products', backendId]`, форма «Установить план» показывает тот же продукт).
 */
export function useUpdateBackendEconomicsProduct(backendId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { productId: string; payload: UpdateBackendEconomicsProductRequest }) =>
      updateBackendEconomicsProduct(backendId, vars.productId, vars.payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: backendEconomicsProductsKey(backendId) });
      void queryClient.invalidateQueries({ queryKey: ['backend-products', backendId] });
    },
  });
}

/** Правка тарифа списания. */
export function useUpdateBackendEconomicsTariff(backendId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (vars: { tariffId: string; payload: UpdateBackendEconomicsTariffRequest }) =>
      updateBackendEconomicsTariff(backendId, vars.tariffId, vars.payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: backendEconomicsPricingKey(backendId) });
    },
  });
}

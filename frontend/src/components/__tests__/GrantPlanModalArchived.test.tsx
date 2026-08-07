import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GrantPlanModal } from '@/components/BackendUserActionModals';
import type { BackendProduct } from '@/types/api';

/**
 * Форма «Установить план» и архивные продукты (ADR-073 §5, 08-design-system.md §Локализация).
 *
 * Ключевая норма — форма архивные **НЕ фильтрует**: `archived` (товарный вид витрины) и
 * `grantable` (право выдачи) ортогональны, выдать архивный план законно. Но опция
 * **помечается** суффиксом: без пометки снятую с витрины позицию не отличить от активной.
 */

const state = vi.hoisted(() => ({
  products: [] as BackendProduct[],
}));

vi.mock('@/features/backend-users/hooks', () => ({
  useBackendProducts: () => ({
    data: { items: state.products },
    isLoading: false,
    isError: false,
  }),
  useGrantBackendUserSubscription: () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() }),
  useAddBackendUserTokens: () => ({ mutate: vi.fn(), isPending: false, reset: vi.fn() }),
}));

vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function product(overrides: Partial<BackendProduct> = {}): BackendProduct {
  return {
    product_id: 'p-1',
    name: 'Pro',
    price: '990',
    period: 'месяц',
    ...overrides,
  };
}

/** Нормативный суффикс подписи архивной опции (08-design-system.md). */
const ARCHIVED_SUFFIX = ' (в архиве)';

function renderModal() {
  render(<GrantPlanModal open onOpenChange={vi.fn()} backendId="b-1" userId="u-1" />, { wrapper });
}

describe('GrantPlanModal — архивные продукты (ADR-073 §5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.products = [];
  });

  it('архивная опция ОСТАЁТСЯ в списке (негативный ассерт на фильтрацию) и несёт суффикс', () => {
    state.products = [
      product({ product_id: 'p-active', name: 'Pro', archived: false }),
      product({ product_id: 'p-archived', name: 'Legacy', archived: true }),
    ];
    renderModal();

    const options = screen.getAllByRole('option').map((o) => o.textContent);
    // Скрыть опцию значило бы молча лишить оператора законной операции.
    expect(options).toContain(`Legacy · 990 · месяц${ARCHIVED_SUFFIX}`);
    expect(options).toContain('Pro · 990 · месяц');
    // Активная подпись суффикса не несёт.
    expect(options.filter((o) => o?.includes(ARCHIVED_SUFFIX))).toHaveLength(1);
    // Опция реально выбираема — она не задизейблена и не отфильтрована.
    expect(
      screen.getByRole('option', { name: `Legacy · 990 · месяц${ARCHIVED_SUFFIX}` }),
    ).toBeEnabled();
  });

  it('`archived: null` (бэк без понятия архива) → суффикса нет', () => {
    state.products = [product({ product_id: 'p-1', name: 'Pro', archived: null })];
    renderModal();

    const options = screen.getAllByRole('option').map((o) => o.textContent);
    expect(options).toContain('Pro · 990 · месяц');
    expect(options.some((o) => o?.includes(ARCHIVED_SUFFIX))).toBe(false);
  });

  it('поля без `archived` вовсе (контракт v1.1) → суффикса нет, форма работает как прежде', () => {
    state.products = [product({ product_id: 'p-1', name: 'Pro' })];
    renderModal();

    const options = screen.getAllByRole('option').map((o) => o.textContent);
    expect(options).toContain('Pro · 990 · месяц');
    expect(options.some((o) => o?.includes(ARCHIVED_SUFFIX))).toBe(false);
  });

  it('`archived: false` суффикса не даёт (пометка строго при `true`)', () => {
    state.products = [product({ product_id: 'p-1', name: 'Pro', archived: false })];
    renderModal();

    expect(screen.getByRole('option', { name: 'Pro · 990 · месяц' })).toBeInTheDocument();
    expect(screen.queryByText(/в архиве/)).not.toBeInTheDocument();
  });
});

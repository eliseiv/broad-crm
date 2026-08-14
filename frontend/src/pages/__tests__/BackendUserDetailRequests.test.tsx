import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BackendUserDetailPage } from '@/pages/BackendUserDetailPage';
import type { BackendUserDetail, BackendUserRequestItem } from '@/types/api';

/**
 * История запросов карточки пользователя бэка — колонки contract v1.1 (ADR-072 §5,
 * 08-design-system.md §История запросов). Нормативные сценарии — 06-testing-strategy.md.
 *
 * Регресс-гейт волны: **`null` ≠ ноль**. Подстановка `$0.00`/`0` вместо «—» (в т.ч.
 * через `value || 0` / `?? 0`) превращает «не измерено» в утверждение «стоило ноль».
 */

const state = vi.hoisted(() => ({
  canView: true,
  canEdit: false,
  requests: [] as BackendUserRequestItem[],
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: () => state.canEdit,
}));

vi.mock('@/features/backend-users/hooks', () => ({
  useBackendUser: () => ({
    data: DETAIL,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useBackendUserPayments: () => ({
    data: { total: 0, items: [] },
    isLoading: false,
    isError: false,
  }),
  useBackendUserRequests: () => ({
    data: { total: state.requests.length, items: state.requests },
    isLoading: false,
    isError: false,
  }),
}));

const DETAIL: BackendUserDetail = {
  backend_id: 'b-1',
  backend_code: 'alpha',
  backend_name: 'Alpha API',
  id: 'u-1',
  external_id: null,
  registered_at: '2026-07-01T10:00:00Z',
  balance: { tokens: 500, credited_total: null, spent_total: null },
  subscription: {
    plan_id: null,
    plan_name: null,
    price: null,
    active: false,
    expires_at: null,
    last_payment_at: null,
    last_payment_method: null,
  },
  revenue: null,
  media_stats: null,
};

function request(overrides: Partial<BackendUserRequestItem> = {}): BackendUserRequestItem {
  return {
    endpoint: '/v1/chat',
    prompt_preview: null,
    status_code: 200,
    status: 'ok',
    duration_sec: 1.2,
    sent_at: '2026-08-01T10:00:00Z',
    ...overrides,
  };
}

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={['/backend-users/b-1/u-1']}>
        <Routes>
          <Route path="/backend-users/:backendId/:userId" element={children} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** Открывает вкладку «Запросы» — по умолчанию активна вкладка «Оплаты». */
async function renderRequestsTab() {
  const user = userEvent.setup();
  render(<BackendUserDetailPage />, { wrapper });
  await user.click(screen.getByRole('button', { name: 'Запросы' }));
  return user;
}

/** Ячейки строки истории (порядок колонок — 08-design-system.md §История запросов). */
function cells(index = 0) {
  const row = screen.getAllByRole('row').at(index + 1)!;
  return within(row).getAllByRole('cell');
}

describe('История запросов — «не измерено» ≠ ноль (ADR-072 §5)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.canView = true;
    state.canEdit = false;
    state.requests = [];
  });

  it('`provider_cost_usd: null` → «—» и НИКОГДА не «$0.00»', async () => {
    state.requests = [request({ provider_cost_usd: null, tokens_spent: null })];
    await renderRequestsTab();

    const [, , spent, cost] = cells();
    expect(cost).toHaveTextContent('—');
    expect(cost).not.toHaveTextContent('$0.00');
    expect(cost).not.toHaveTextContent('$');
    // Та же норма для «Списано токенов»: `null` — прочерк, а не ноль.
    expect(spent).toHaveTextContent('—');
    expect(spent).not.toHaveTextContent('0');
  });

  it('измеренный `0` → «$0.00» (ноль — это значение, а не отсутствие)', async () => {
    state.requests = [request({ provider_cost_usd: 0, tokens_spent: 0 })];
    await renderRequestsTab();

    const [, , spent, cost] = cells();
    expect(cost).toHaveTextContent('$0.00');
    expect(cost).not.toHaveTextContent('—');
    expect(spent).toHaveTextContent('0');
  });

  it('суб-центовая себестоимость не схлопывается в «$0.00» (до 4 знаков)', async () => {
    state.requests = [request({ provider_cost_usd: 0.0004 })];
    await renderRequestsTab();

    expect(cells()[3]).toHaveTextContent('$0.0004');
  });

  it('`provider_cost_estimated: true` → «≈$…» и легенда оценки', async () => {
    state.requests = [request({ provider_cost_usd: 1.6, provider_cost_estimated: true })];
    await renderRequestsTab();

    expect(cells()[3]).toHaveTextContent('≈$1.60');
    expect(
      screen.getByText('≈ — оценка сверху (точную цену провайдера восстановить нельзя)'),
    ).toBeInTheDocument();
  });

  it('легенда «— — себестоимость не измерена» есть при хотя бы одном прочерке', async () => {
    state.requests = [
      request({ provider_cost_usd: 0.5, tokens_spent: 10 }),
      request({ endpoint: '/v1/photo', provider_cost_usd: null, tokens_spent: 10 }),
    ];
    await renderRequestsTab();

    expect(screen.getByText('— — себестоимость не измерена')).toBeInTheDocument();
  });

  it('легенды нет, когда измерены все строки', async () => {
    state.requests = [request({ provider_cost_usd: 0, tokens_spent: 0 })];
    await renderRequestsTab();

    expect(screen.queryByText('— — себестоимость не измерена')).not.toBeInTheDocument();
  });

  it('`refunded: true` → пометка «Возврат» ТЕКСТОМ, списание остаётся показанным', async () => {
    state.requests = [request({ refunded: true, tokens_spent: 120, provider_cost_usd: 0.5 })];
    await renderRequestsTab();

    const spent = cells()[2];
    expect(spent).toHaveTextContent('Возврат');
    // Возврат НЕ обнуляет списание (ADR-072 §1, инвариант 2).
    expect(spent).toHaveTextContent('120');
  });

  it.each([
    ['false', false],
    ['null (поле не отдано)', null],
  ])('`refunded: %s` → пометки «Возврат» НЕТ', async (_name, refunded) => {
    state.requests = [request({ refunded, tokens_spent: 120, provider_cost_usd: 0.5 })];
    await renderRequestsTab();

    // `null` ≠ `false`, но пометка рендерится СТРОГО при `true` — отдельного
    // индикатора «неизвестно» нет.
    expect(screen.queryByText('Возврат')).not.toBeInTheDocument();
  });

  it('бэк уровня v1 (полей нет вовсе) не ломает таблицу: обе новые колонки — «—»', async () => {
    state.requests = [request()];
    await renderRequestsTab();

    const [, , spent, cost] = cells();
    expect(spent).toHaveTextContent('—');
    expect(cost).toHaveTextContent('—');
    expect(screen.getByText('— — себестоимость не измерена')).toBeInTheDocument();
  });
});

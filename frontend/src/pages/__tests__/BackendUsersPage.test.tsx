import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BackendUsersPage } from '@/pages/BackendUsersPage';
import type {
  Backend,
  BackendUsersApiCosts,
  BackendUsersListResponse,
  BackendUsersSourceError,
} from '@/types/api';

/**
 * Блок «Расходы API» страницы «Пользователи бэков» (ADR-080 §5/§6; нормативные
 * сценарии — 06-testing-strategy.md §Волна ADR-080, Frontend). До этой волны у
 * страницы не было ни одного собственного теста, поэтому кейсы — регресс-гейты на
 * конкретные способы сломаться:
 *  - «Прочее» как постоянная четвёртая ячейка («$0.00» читается как «прочих ждут»);
 *  - «Снимок формируется…» вместо «—» на этапе загрузки (утверждение о снимке,
 *    которого ещё не видели);
 *  - молчание о `partial` (неполная сумма читается как полная);
 *  - keyless-бэк в `errors[]` → жёлтая плашка «данные неполные» на штатной конфигурации.
 */

const state = vi.hoisted(() => ({
  data: undefined as BackendUsersListResponse | undefined,
  isLoading: false,
  backends: [] as unknown[],
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => true,
}));

vi.mock('@/features/backend-users/hooks', () => ({
  useBackendUsers: () => ({
    data: state.data,
    isLoading: state.isLoading,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/features/backends/hooks', () => ({
  useBackends: () => ({
    data: { items: state.backends },
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

function backend(over: Partial<Backend> & Pick<Backend, 'id' | 'code' | 'name'>): Backend {
  return {
    domain: 'https://example.com/',
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: true,
    has_admin_api_key: true,
    git: null,
    note: null,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: null,
    created_at: '2026-08-01T09:00:00Z',
    updated_at: '2026-08-01T09:00:00Z',
    ...over,
  };
}

function costs(over: Partial<BackendUsersApiCosts> = {}): BackendUsersApiCosts {
  return {
    openai_usd: 12.5,
    anthropic_usd: 3.25,
    fal_usd: 0.4,
    other_usd: 0,
    total_usd: 16.15,
    partial: false,
    ...over,
  };
}

function response(over: Partial<BackendUsersListResponse> = {}): BackendUsersListResponse {
  return {
    total: 0,
    items: [],
    stats: { users_total: 0, paid_users: 0, payments_sum_usd: 0, cr_percent: 0 },
    errors: [],
    snapshot_at: '2026-08-19T05:00:00Z',
    api_costs: costs(),
    ...over,
  };
}

const SOURCE_ERROR: BackendUsersSourceError = {
  backend_id: 'b-2',
  backend_code: 'legacy',
  backend_name: 'Легаси',
  message: 'Бэк не ответил',
};

/** Нормативная строка жёлтой плашки partial-data (BackendUsersPage.tsx). */
const PARTIAL_BANNER = 'Часть бэков не попала в выборку — данные неполные:';

/** Значение сводной ячейки `SummaryCell` по её подписи (подпись → следующий `<p>`). */
function cellValue(container: HTMLElement, label: string): string | undefined {
  return (
    [...container.querySelectorAll('p')].find((p) => p.textContent === label)?.nextElementSibling
      ?.textContent ?? undefined
  );
}

/** Подписи всех отрисованных сводных ячеек в порядке DOM. */
function cellLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll('p.text-\\[12px\\]')]
    .filter((p) => p.nextElementSibling?.classList.contains('font-bold'))
    .map((p) => p.textContent ?? '');
}

describe('BackendUsersPage — блок «Расходы API» (ADR-080 §5/§6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.isLoading = false;
    state.backends = [];
    state.data = response();
  });

  it('рендерит ТРИ ячейки расходов и НЕ рендерит «Прочее» при other_usd = 0', () => {
    state.data = response({ api_costs: costs({ other_usd: 0 }) });

    const { container } = render(<BackendUsersPage />, { wrapper });

    expect(cellValue(container, 'Расход OpenAI')).toBe('$12,50');
    expect(cellValue(container, 'Расход Anthropic')).toBe('$3,25');
    // Центы обязательны (ADR-080 §6): округление до целых показало бы «$0».
    expect(cellValue(container, 'Расход Fal')).toBe('$0,40');
    expect(screen.queryByText('Прочее')).not.toBeInTheDocument();
    expect(cellLabels(container).filter((l) => l.startsWith('Расход'))).toHaveLength(3);
  });

  it('добавляет ячейку «Прочее» при other_usd > 0', () => {
    state.data = response({ api_costs: costs({ other_usd: 1.05 }) });

    const { container } = render(<BackendUsersPage />, { wrapper });

    expect(cellValue(container, 'Прочее')).toBe('$1,05');
  });

  it('подпись блока называет накопительный период (фильтр периода не действует)', () => {
    render(<BackendUsersPage />, { wrapper });

    expect(
      screen.getByText('Расходы API — накопительно за всё время, фильтр периода не действует'),
    ).toBeInTheDocument();
  });

  it('рендерит метку «Данные на HH:MM» из snapshot_at', () => {
    // Локальное время без смещения — час/минута детерминированы в любой TZ раннера.
    state.data = response({ snapshot_at: '2026-08-19T14:35:00' });

    render(<BackendUsersPage />, { wrapper });

    expect(screen.getByText('Данные на 14:35')).toBeInTheDocument();
    expect(screen.queryByText('Снимок формируется…')).not.toBeInTheDocument();
  });

  it('snapshot_at === null → «Снимок формируется…», ячейки расходов — «—»', () => {
    // api_costs приходит `null` тем же случаем (04-api.md, ADR-080 §6).
    state.data = response({ snapshot_at: null, api_costs: null });

    const { container } = render(<BackendUsersPage />, { wrapper });

    expect(screen.getByText('Снимок формируется…')).toBeInTheDocument();
    expect(cellValue(container, 'Расход OpenAI')).toBe('—');
    expect(cellValue(container, 'Расход Anthropic')).toBe('—');
    expect(cellValue(container, 'Расход Fal')).toBe('—');
    // Нулевого «Прочее» при отсутствующем снимке тоже нет.
    expect(screen.queryByText('Прочее')).not.toBeInTheDocument();
  });

  it('partial: true → пометка «расходы ещё собираются»', () => {
    state.data = response({ api_costs: costs({ partial: true }) });

    render(<BackendUsersPage />, { wrapper });

    expect(screen.getByText('расходы ещё собираются')).toBeInTheDocument();
  });

  it('partial: false → пометки «расходы ещё собираются» НЕТ', () => {
    state.data = response({ api_costs: costs({ partial: false }) });

    render(<BackendUsersPage />, { wrapper });

    expect(screen.queryByText('расходы ещё собираются')).not.toBeInTheDocument();
  });

  it('isLoading → «—» вместо утверждения о снимке и в ячейках расходов', () => {
    state.isLoading = true;
    state.data = undefined;

    const { container } = render(<BackendUsersPage />, { wrapper });

    // Пока ответа нет, «Снимок формируется…» недоказуемо — показывается «—».
    expect(screen.queryByText('Снимок формируется…')).not.toBeInTheDocument();
    expect(screen.queryByText(/^Данные на /)).not.toBeInTheDocument();
    expect(cellValue(container, 'Расход OpenAI')).toBe('—');
    expect(cellValue(container, 'Расход Anthropic')).toBe('—');
    expect(cellValue(container, 'Расход Fal')).toBe('—');
    expect(cellValue(container, 'Всего пользователей')).toBe('—');
  });
});

describe('BackendUsersPage — keyless-бэки скрыты (ADR-080 §4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.isLoading = false;
    state.backends = [];
    state.data = response();
  });

  it('бэк без Admin API Key не даёт жёлтую плашку — его нет в errors[]', () => {
    state.backends = [
      backend({ id: 'b-1', code: 'main', name: 'Основной' }),
      backend({ id: 'b-9', code: 'keyless', name: 'Безключевой', has_admin_api_key: false }),
    ];
    state.data = response({ errors: [] });

    render(<BackendUsersPage />, { wrapper });

    expect(screen.queryByText(PARTIAL_BANNER)).not.toBeInTheDocument();
    expect(screen.queryByText(/Безключевой/)).not.toBeInTheDocument();
  });

  it('позитивный контроль: непустой errors[] жёлтую плашку ДАЁТ', () => {
    state.data = response({ errors: [SOURCE_ERROR] });

    render(<BackendUsersPage />, { wrapper });

    expect(screen.getByText(PARTIAL_BANNER)).toBeInTheDocument();
    expect(screen.getByText('Легаси — legacy — Бэк не ответил')).toBeInTheDocument();
  });

  it('keyless-бэк не попадает в опции фильтра «Приложение»', () => {
    state.backends = [
      backend({ id: 'b-1', code: 'main', name: 'Основной' }),
      backend({ id: 'b-9', code: 'keyless', name: 'Безключевой', has_admin_api_key: false }),
    ];

    render(<BackendUsersPage />, { wrapper });

    const select = screen.getByLabelText('Приложение');
    const options = [...select.querySelectorAll('option')].map((o) => o.textContent);
    expect(options).toEqual(['Все приложения', 'Основной — main']);
  });
});

import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SmsPage } from '@/pages/SmsPage';
import { INSUFFICIENT_PERMISSIONS_TITLE } from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import type { SmsFeedResult } from '@/features/sms/hooks';
import type { SmsMessage, SmsNumber, TeamListItem } from '@/types/api';

// Гейтинг: page-level view-guard `sms:view` + действия sms:edit/transfer/delete/sync.
const auth = vi.hoisted(() => ({
  canView: true,
  can: {} as Record<string, boolean>,
}));

// Лента SMS: spy на аргумент фильтра (комбинируемость number+team, ADR-030).
const useSmsMessagesSpy = vi.hoisted(() => vi.fn());
const feed = vi.hoisted(() => ({ value: null as unknown }));
const numbersQuery = vi.hoisted(() => ({ value: null as unknown }));
const teamsQuery = vi.hoisted(() => ({ value: null as unknown }));
const mutations = vi.hoisted(() => ({
  sync: vi.fn(),
  update: vi.fn(),
  transfer: vi.fn(),
  del: vi.fn(),
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => auth.canView,
  useCan: (_page: string, action: string) => auth.can[action] ?? false,
}));

vi.mock('@/features/sms/hooks', () => ({
  useSmsMessages: (filter: unknown) => {
    useSmsMessagesSpy(filter);
    return feed.value;
  },
  useSmsNumbers: () => numbersQuery.value,
  useSyncSmsNumbers: () => ({ mutate: mutations.sync, isPending: false }),
  useUpdateSmsNumber: () => ({ mutate: mutations.update, isPending: false }),
  useTransferSmsNumber: () => ({ mutate: mutations.transfer, isPending: false }),
  useDeleteSmsNumber: () => ({ mutate: mutations.del, isPending: false }),
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => teamsQuery.value,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// Управляемый IntersectionObserver (образец — MailPage.test): захватываем колбэк
// sentinel-эффекта, чтобы детерминированно эмулировать пересечение (догрузку).
let ioCallback: IntersectionObserverCallback | null = null;
const ioObserve = vi.fn();
const ioDisconnect = vi.fn();

class MockIntersectionObserver {
  constructor(cb: IntersectionObserverCallback) {
    ioCallback = cb;
  }
  observe = ioObserve;
  disconnect = ioDisconnect;
  unobserve = vi.fn();
  takeRecords = vi.fn();
  root = null;
  rootMargin = '';
  thresholds = [];
}

function triggerIntersection(): void {
  act(() => {
    ioCallback?.(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

function makeNumber(id: number, over: Partial<SmsNumber> = {}): SmsNumber {
  return {
    id,
    phone_number: `+1555000${id}`,
    label: null,
    team: null,
    login: null,
    app_name: null,
    note: null,
    is_active: true,
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
    ...over,
  };
}

function makeMessage(id: number, over: Partial<SmsMessage> = {}): SmsMessage {
  return {
    id,
    from_number: '+15550000001',
    to_number: '+15550000002',
    body: `SMS ${id}`,
    received_at: '2026-07-02T09:15:00Z',
    number: null,
    ...over,
  };
}

function makeTeam(id: string, name: string): TeamListItem {
  return {
    id,
    name,
    leader_id: null,
    leader_username: null,
    member_count: 0,
    number_count: 0,
    members: [],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  };
}

function baseFeed(over: Partial<SmsFeedResult> = {}): SmsFeedResult {
  return {
    messages: [],
    phase: 'ready',
    error: null,
    hasMore: false,
    isFetchingMore: false,
    isReloading: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
    ...over,
  };
}

function numbersData(numbers: SmsNumber[]) {
  return {
    data: { numbers },
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  };
}

describe('SmsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    auth.canView = true;
    auth.can = { edit: true, transfer: true, delete: true, sync: true };
    feed.value = baseFeed();
    numbersQuery.value = numbersData([]);
    teamsQuery.value = { data: { items: [] } };
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('вкладка «Сообщения» активна по умолчанию; клик по «Номера» переключает aria-selected', async () => {
    const user = userEvent.setup();
    render(<SmsPage />);

    const msgTab = screen.getByRole('tab', { name: 'Сообщения' });
    const numTab = screen.getByRole('tab', { name: 'Номера' });
    expect(msgTab).toHaveAttribute('aria-selected', 'true');
    expect(numTab).toHaveAttribute('aria-selected', 'false');

    await user.click(numTab);
    expect(numTab).toHaveAttribute('aria-selected', 'true');
    expect(msgTab).toHaveAttribute('aria-selected', 'false');
  });

  it('фильтры number+team комбинируемы (AND): выбор одного не сбрасывает другой', async () => {
    const user = userEvent.setup();
    numbersQuery.value = numbersData([makeNumber(5, { phone_number: '+15551234567' })]);
    teamsQuery.value = { data: { items: [makeTeam('t1', 'Продажи')] } };
    feed.value = baseFeed({ messages: [makeMessage(1)] });
    render(<SmsPage />);

    const numberSelect = screen.getByLabelText('Фильтр по номеру') as HTMLSelectElement;
    const teamSelect = screen.getByLabelText('Фильтр по команде') as HTMLSelectElement;

    await user.selectOptions(numberSelect, '5');
    await user.selectOptions(teamSelect, 't1');

    // Оба контрола сохранили значение — второй выбор не сбросил первый.
    expect(numberSelect.value).toBe('5');
    expect(teamSelect.value).toBe('t1');
    // Хук ленты получил оба фильтра одновременно (комбинируемость AND).
    expect(useSmsMessagesSpy).toHaveBeenLastCalledWith({ numberId: 5, teamId: 't1' });
  });

  it('догрузка по курсору: пересечение sentinel вызывает loadMore', () => {
    const loadMore = vi.fn();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true, loadMore });
    render(<SmsPage />);

    expect(ioObserve).toHaveBeenCalled();
    triggerIntersection();
    expect(loadMore).toHaveBeenCalledTimes(1);
  });

  it('состояние loading: показывает skeleton', () => {
    feed.value = baseFeed({ phase: 'loading' });
    const { container } = render(<SmsPage />);
    expect(container.querySelector('.animate-pulse')).not.toBeNull();
  });

  it('состояние empty: «Сообщений пока нет»', () => {
    feed.value = baseFeed({ phase: 'ready', messages: [] });
    render(<SmsPage />);
    expect(screen.getByText('Сообщений пока нет')).toBeInTheDocument();
  });

  it('состояние error: заглушка + «Повторить» вызывает reload', async () => {
    const user = userEvent.setup();
    const reload = vi.fn();
    feed.value = baseFeed({ phase: 'error', error: new ApiError(500, 'x', 'y'), reload });
    render(<SmsPage />);

    expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Повторить/ }));
    expect(reload).toHaveBeenCalled();
  });

  it('page-guard: useCanViewPage(sms)=false → InsufficientPermissions, лента не запрашивается', () => {
    auth.canView = false;
    feed.value = baseFeed({ messages: [makeMessage(1)] });
    render(<SmsPage />);

    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    // За guard'ом контент не монтируется: лента SMS не запрашивается, табов нет.
    expect(useSmsMessagesSpy).not.toHaveBeenCalled();
    expect(screen.queryByRole('tab')).not.toBeInTheDocument();
  });

  it('вкладка «Номера»: рендерит строку номера из списка', async () => {
    const user = userEvent.setup();
    numbersQuery.value = numbersData([makeNumber(5, { phone_number: '+15551234567' })]);
    render(<SmsPage />);

    await user.click(screen.getByRole('tab', { name: 'Номера' }));
    expect(screen.getByText('+15551234567')).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ProxyCard } from '@/components/ProxyCard';
import type { Proxy } from '@/types/api';

const hooks = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
  updateMutate: vi.fn(),
  createMutate: vi.fn(),
}));

vi.mock('@/features/proxies/hooks', () => ({
  proxiesKey: ['proxies'],
  useProxyStatus: () => ({ data: undefined }),
  useDeleteProxy: () => ({ mutate: hooks.deleteMutate, isPending: false }),
  useUpdateProxy: () => ({ mutate: hooks.updateMutate, isPending: false }),
  useCreateProxy: () => ({ mutate: hooks.createMutate, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeProxy(overrides: Partial<Proxy> = {}): Proxy {
  return {
    id: 'proxy-1',
    name: 'DE Residential',
    proxy_type: 'socks5',
    host: 'proxy.example.com',
    port: 1080,
    username: 'user01',
    has_password: true,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('ProxyCard — status badges', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders "Работает" badge and host:port for working status', () => {
    render(<ProxyCard proxy={makeProxy({ check_status: 'working' })} />, { wrapper });

    expect(screen.getByText('Работает')).toBeInTheDocument();
    expect(screen.getByText('proxy.example.com:1080')).toBeInTheDocument();
    expect(screen.getByText('SOCKS5')).toBeInTheDocument();
  });

  it('renders "Не работает" badge with reason for error status', () => {
    render(
      <ProxyCard
        proxy={makeProxy({ check_status: 'error', error_message: 'Прокси недоступен' })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не работает')).toBeInTheDocument();
    expect(screen.getByText('Прокси недоступен')).toBeInTheDocument();
  });

  it('renders "Проверка…" for pending status', () => {
    render(<ProxyCard proxy={makeProxy({ check_status: 'pending' })} />, { wrapper });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
    expect(screen.queryByText('Работает')).not.toBeInTheDocument();
  });

  it('shows login and "Пароль задан" when username/has_password present', () => {
    render(<ProxyCard proxy={makeProxy({ username: 'user01', has_password: true })} />, {
      wrapper,
    });

    expect(screen.getByText('user01')).toBeInTheDocument();
    expect(screen.getByText('Пароль задан')).toBeInTheDocument();
  });
});

describe('ProxyCard — interactions', () => {
  beforeEach(() => vi.clearAllMocks());

  it('opens edit modal prefilled on card click', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Изменить прокси DE Residential' }));

    expect(screen.getByText('Изменить прокси')).toBeInTheDocument();
    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Название') as HTMLInputElement).value).toBe('DE Residential');
  });

  it('delete button opens confirm dialog without opening edit (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Удалить прокси DE Residential' }));

    expect(screen.getByText('Удалить прокси?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить прокси')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});

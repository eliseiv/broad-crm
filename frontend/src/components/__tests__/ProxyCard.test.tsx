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

  it('renders "Работает" badge and ТОЛЬКО host (без порта, ADR-023) for working status', () => {
    render(<ProxyCard proxy={makeProxy({ check_status: 'working' })} />, { wrapper });

    expect(screen.getByText('Работает')).toBeInTheDocument();
    // ADR-023: карточка показывает только host (логин/пароль/порт — в форме edit).
    expect(screen.getByText('proxy.example.com')).toBeInTheDocument();
    expect(screen.queryByText('proxy.example.com:1080')).not.toBeInTheDocument();
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

  it('в error-состоянии — ровно одна кнопка «Удалить» (в шапке; второй нет, ADR-023)', () => {
    render(
      <ProxyCard
        proxy={makeProxy({ check_status: 'error', error_message: 'Прокси недоступен' })}
      />,
      { wrapper },
    );

    // Единственная кнопка удаления — в шапке карточки (нет второй в error-футере).
    expect(screen.getAllByRole('button', { name: 'Удалить прокси DE Residential' })).toHaveLength(
      1,
    );
  });

  it('не показывает логин/«Пароль задан» на карточке (ADR-023: только host)', () => {
    render(<ProxyCard proxy={makeProxy({ username: 'user01', has_password: true })} />, {
      wrapper,
    });

    expect(screen.queryByText('user01')).not.toBeInTheDocument();
    expect(screen.queryByText('Пароль задан')).not.toBeInTheDocument();
  });
});

describe('ProxyCard — detail → edit (ADR-035)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('клик по карточке открывает detail-модалку (Просмотр), НЕ edit', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр прокси DE Residential' }));

    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByText('Просмотр')).toBeInTheDocument();
    // Видимая зона — идентификаторы (Название / Хост / Порт); Тип и Логин — внутри
    // свёрнутой по умолчанию «Информации» (ADR-046 §2в).
    expect(dialog.getByText('Хост')).toBeInTheDocument();
    expect(dialog.queryByText('user01')).not.toBeInTheDocument();

    await user.click(dialog.getByRole('button', { name: 'Информация' }));
    expect(dialog.getByText('user01')).toBeInTheDocument();
    expect(screen.queryByText('Изменить прокси')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });

  it('карандаш в detail-модалке открывает edit prefilled', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр прокси DE Residential' }));
    await user.click(await screen.findByRole('button', { name: 'Редактировать' }));

    expect(await screen.findByText('Изменить прокси')).toBeInTheDocument();
    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Название') as HTMLInputElement).value).toBe('DE Residential');
  });

  it('без права proxies:edit (canEdit=false) карандаш в detail-модалке скрыт', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} canEdit={false} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Просмотр прокси DE Residential' }));
    await screen.findByText('Просмотр');

    expect(screen.queryByRole('button', { name: 'Редактировать' })).not.toBeInTheDocument();
  });

  it('delete button opens confirm dialog without opening detail/edit (stopPropagation)', async () => {
    const user = userEvent.setup();
    render(<ProxyCard proxy={makeProxy()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Удалить прокси DE Residential' }));

    expect(screen.getByText('Удалить прокси?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить прокси')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});

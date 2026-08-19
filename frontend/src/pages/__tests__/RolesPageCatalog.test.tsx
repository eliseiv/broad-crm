import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RolesPage } from '@/pages/RolesPage';
import { loginAs, logout } from '@/test/authTestUtils';

const CATALOG = {
  pages: [
    { page: 'documents', actions: ['view', 'create', 'edit', 'delete', 'share'] },
    { page: 'broadcast', actions: ['view', 'send'] },
    { page: 'mail', actions: ['view', 'create', 'edit', 'delete', 'sync', 'tags'] },
  ],
};

const ROLES = {
  items: [
    {
      id: 'r1',
      name: 'Оператор',
      permissions: { documents: ['view'] },
      user_count: 1,
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:05:00Z',
    },
  ],
};

function wrapper({ children }: PropsWithChildren) {
  return (
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      {children}
    </QueryClientProvider>
  );
}

describe('RolesPage — матрица по-прежнему из GET /permissions/catalog (ADR-078)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    logout();
    vi.unstubAllGlobals();
  });

  it('запрашивает catalog и рисует extra-столбцы share/send/sync/tags', async () => {
    const fetchMock = vi.fn((url: string) => {
      const path = String(url);
      if (path.includes('/permissions/catalog')) {
        return Promise.resolve(new Response(JSON.stringify(CATALOG)));
      }
      if (path.includes('/roles')) {
        return Promise.resolve(new Response(JSON.stringify(ROLES)));
      }
      return Promise.resolve(
        new Response(JSON.stringify({ error: { code: 'not_found' } }), { status: 404 }),
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    loginAs({
      isSuperadmin: false,
      roles: ['Оператор'],
      isAdminLevel: false,
      permissions: { roles: ['view', 'create', 'edit'] },
    });
    render(<RolesPage />, { wrapper });

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes('/permissions/catalog')),
      ).toBe(true);
    });

    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: 'Добавить роль' }));
    expect(await screen.findByRole('dialog', { name: 'Добавить роль' })).toBeInTheDocument();
    expect(screen.getByText('Видимость')).toBeInTheDocument();
    expect(screen.getByText('Отправка')).toBeInTheDocument();
    expect(screen.getByText('Синк')).toBeInTheDocument();
    expect(screen.getByText('Теги')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Документы — Видимость' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Рассылка — Отправка' })).toBeInTheDocument();
  });
});

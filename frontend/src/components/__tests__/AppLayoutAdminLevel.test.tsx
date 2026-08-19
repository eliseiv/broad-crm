import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppLayout } from '@/components/AppLayout';
import { loginAs, logout } from '@/test/authTestUtils';
import { useAuthStore } from '@/store/auth';
import type { MeResponse } from '@/types/api';

const IS_ADMIN_LEVEL_KEY = 'crm.auth.isAdminLevel';

function meBody(overrides: Partial<MeResponse> = {}): MeResponse {
  return {
    username: 'ivan',
    roles: ['Админ'],
    is_superadmin: false,
    is_admin_level: true,
    sees_all_sms_teams: true,
    sees_all_mail_teams: true,
    mail_teams: [],
    sms_teams: [],
    mail_includes_unassigned: true,
    sms_includes_unassigned: true,
    permissions: { servers: ['view'], documents: ['view', 'share'] },
    ...overrides,
  };
}

function renderLayout(initial = '/servers') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/servers" element={<div>Контент серверов</div>} />
        <Route path="/users" element={<div>Контент пользователей</div>} />
        <Route path="/roles" element={<div>Контент ролей</div>} />
        <Route path="*" element={<div>Прочий контент</div>} />
      </Route>
    </Routes>,
    { wrapper },
  );
}

function catalogCalls(fetchMock: ReturnType<typeof vi.fn>): string[] {
  return fetchMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.includes('/permissions/catalog'));
}

describe('AppLayout — isAdminLevel из /me, без каталога (ADR-078)', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    logout();
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('роль «Админ»: Users виден при is_admin_level=true; catalog НЕ вызывается', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).includes('/auth/me')) {
        return Promise.resolve(new Response(JSON.stringify(meBody())));
      }
      return Promise.resolve(new Response(JSON.stringify({ pages: [] })));
    });
    vi.stubGlobal('fetch', fetchMock);

    loginAs({
      username: 'ivan',
      isSuperadmin: false,
      roles: ['Админ'],
      isAdminLevel: true,
      permissions: { servers: ['view'], documents: ['view', 'share'] },
    });
    renderLayout('/servers');

    expect(await screen.findByRole('link', { name: 'Пользователи' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Загрузка')).not.toBeInTheDocument();
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(catalogCalls(fetchMock)).toEqual([]);
  });

  it('isAdminLevel=false скрывает «Пользователи»; catalog всё равно не вызывается', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).includes('/auth/me')) {
        return Promise.resolve(
          new Response(
            JSON.stringify(
              meBody({
                is_admin_level: false,
                permissions: { servers: ['view'] },
              }),
            ),
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ pages: [] })));
    });
    vi.stubGlobal('fetch', fetchMock);

    loginAs({
      isSuperadmin: false,
      roles: ['Админ'],
      isAdminLevel: false,
      permissions: { servers: ['view'] },
    });
    renderLayout('/servers');

    expect(screen.getByRole('link', { name: 'Серверы' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Пользователи' })).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(catalogCalls(fetchMock)).toEqual([]);
  });

  it('ошибка /me ≠ 401 оставляет персист isAdminLevel и пункт «Пользователи»', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).includes('/auth/me')) {
        return Promise.resolve(
          new Response(JSON.stringify({ error: { code: 'internal_error', message: 'fail' } }), {
            status: 500,
          }),
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ pages: [] })));
    });
    vi.stubGlobal('fetch', fetchMock);

    loginAs({
      username: 'ivan',
      isSuperadmin: false,
      roles: ['Админ'],
      isAdminLevel: true,
      permissions: { servers: ['view'] },
    });
    expect(localStorage.getItem(IS_ADMIN_LEVEL_KEY)).toBe('1');

    renderLayout('/servers');

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/auth/me'))).toBe(true);
    });
    expect(screen.getByRole('link', { name: 'Пользователи' })).toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
    expect(useAuthStore.getState().isAdminLevel).toBe(true);
    expect(localStorage.getItem(IS_ADMIN_LEVEL_KEY)).toBe('1');
    expect(catalogCalls(fetchMock)).toEqual([]);
  });

  it('401 на /me сбрасывает сессию и crm.auth.isAdminLevel', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (String(url).includes('/auth/me')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({ error: { code: 'unauthorized', message: 'Требуется авторизация' } }),
            { status: 401 },
          ),
        );
      }
      return Promise.resolve(new Response(JSON.stringify({ pages: [] })));
    });
    vi.stubGlobal('fetch', fetchMock);

    loginAs({
      username: 'ivan',
      isSuperadmin: false,
      roles: ['Админ'],
      isAdminLevel: true,
      permissions: { servers: ['view'] },
    });
    expect(localStorage.getItem(IS_ADMIN_LEVEL_KEY)).toBe('1');

    renderLayout('/servers');

    await waitFor(() => {
      expect(useAuthStore.getState().isAuthenticated).toBe(false);
    });
    expect(useAuthStore.getState().isAdminLevel).toBe(false);
    expect(localStorage.getItem(IS_ADMIN_LEVEL_KEY)).toBeNull();
    expect(screen.queryByRole('link', { name: 'Пользователи' })).not.toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '@/App';
import { loginAs, loginSuperadmin, logout } from '@/test/authTestUtils';
import { useAuthStore } from '@/store/auth';

// Страницы мокаем маркерами — тест про роутинг (permission-aware дефолт/fallback → первая
// доступная вкладка, БЕЗ /dashboard, ADR-022), а не про их внутренности.
vi.mock('@/pages/DashboardPage', () => ({ DashboardPage: () => <div>DASHBOARD</div> }));
vi.mock('@/pages/MailPage', () => ({ MailPage: () => <div>MAIL</div> }));
vi.mock('@/pages/ServersPage', () => ({ ServersPage: () => <div>SERVERS</div> }));
vi.mock('@/pages/AiKeysPage', () => ({ AiKeysPage: () => <div>AIKEYS</div> }));
vi.mock('@/pages/ProxiesPage', () => ({ ProxiesPage: () => <div>PROXIES</div> }));
vi.mock('@/pages/BackendsPage', () => ({ BackendsPage: () => <div>BACKENDS</div> }));
vi.mock('@/pages/RolesPage', () => ({ RolesPage: () => <div>ROLES</div> }));
vi.mock('@/pages/TeamsPage', () => ({ TeamsPage: () => <div>TEAMS</div> }));
vi.mock('@/pages/UsersPage', () => ({ UsersPage: () => <div>USERS</div> }));
vi.mock('@/pages/LoginPage', () => ({ LoginPage: () => <div>LOGIN</div> }));

// AppLayout вызывает useMe() (GET /api/auth/me). В тесте роутинга сеть не нужна —
// мокаем useMe как no-op; гейтинг читает принципала из стора (loginAs/loginSuperadmin).
vi.mock('@/features/auth/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/auth/hooks')>();
  return { ...actual, useMe: () => ({ data: undefined }) };
});

function renderAt(path: string) {
  function wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[path]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(<App />, { wrapper });
}

describe('App routing (permission-aware default без /dashboard, ADR-022)', () => {
  afterEach(() => logout());

  describe('superadmin', () => {
    beforeEach(() => loginSuperadmin());

    it('redirects index "/" to the first nav leaf /mail', () => {
      renderAt('/');
      expect(screen.getByText('MAIL')).toBeInTheDocument();
    });

    it('redirects an unknown path (fallback *) to the first nav leaf /mail', () => {
      renderAt('/does-not-exist');
      expect(screen.getByText('MAIL')).toBeInTheDocument();
    });

    it('renders /servers on its route', () => {
      renderAt('/servers');
      expect(screen.getByText('SERVERS')).toBeInTheDocument();
    });

    it('keeps /dashboard reachable by direct URL (out of the menu)', () => {
      renderAt('/dashboard');
      expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
    });
  });

  it('resolves the default to the first tab the user can reach (no mail → /servers)', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
    renderAt('/');
    expect(screen.getByText('SERVERS')).toBeInTheDocument();
  });

  it('redirects to /login when unauthenticated', () => {
    useAuthStore.getState().clearSession();
    renderAt('/servers');
    expect(screen.getByText('LOGIN')).toBeInTheDocument();
  });
});

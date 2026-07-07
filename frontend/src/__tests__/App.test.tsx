import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '@/App';
import { loginSuperadmin } from '@/test/authTestUtils';
import { useAuthStore } from '@/store/auth';

// Страницы мокаем маркерами — тест про роутинг (дефолт/fallback → /dashboard, ADR-017),
// а не про их внутренности (иначе подтянулись бы реальные data-хуки/запросы).
vi.mock('@/pages/DashboardPage', () => ({ DashboardPage: () => <div>DASHBOARD</div> }));
vi.mock('@/pages/MailPage', () => ({ MailPage: () => <div>MAIL</div> }));
vi.mock('@/pages/ServersPage', () => ({ ServersPage: () => <div>SERVERS</div> }));
vi.mock('@/pages/AiKeysPage', () => ({ AiKeysPage: () => <div>AIKEYS</div> }));
vi.mock('@/pages/LoginPage', () => ({ LoginPage: () => <div>LOGIN</div> }));

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

describe('App routing (ADR-017 default route /dashboard)', () => {
  beforeEach(() => {
    // ADR-021: DefaultRoute резолвит /dashboard по dashboard:view/superadmin — задаём принципала.
    loginSuperadmin();
  });

  it('redirects index "/" to /dashboard', () => {
    renderAt('/');
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
  });

  it('redirects an unknown path (fallback *) to /dashboard', () => {
    renderAt('/does-not-exist');
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
  });

  it('renders /mail on its route', () => {
    renderAt('/mail');
    expect(screen.getByText('MAIL')).toBeInTheDocument();
  });

  it('redirects to /login when unauthenticated', () => {
    useAuthStore.getState().clearSession();
    renderAt('/dashboard');
    expect(screen.getByText('LOGIN')).toBeInTheDocument();
  });
});

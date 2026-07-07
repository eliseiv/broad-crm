import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';
import { DefaultRoute } from '@/routes/DefaultRoute';
import { loginAs, logout } from '@/test/authTestUtils';

function renderDefault() {
  function wrapper({ children }: PropsWithChildren) {
    return <MemoryRouter initialEntries={['/']}>{children}</MemoryRouter>;
  }
  return render(
    <Routes>
      <Route path="/" element={<DefaultRoute />} />
      <Route path="/dashboard" element={<div>DASHBOARD</div>} />
      <Route path="/mail" element={<div>MAIL</div>} />
      <Route path="/servers" element={<div>SERVERS</div>} />
    </Routes>,
    { wrapper },
  );
}

describe('DefaultRoute (permission-aware default, ADR-021)', () => {
  afterEach(() => logout());

  it('redirects to /dashboard when the user has dashboard:view', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { dashboard: ['view'] } });
    renderDefault();
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
  });

  it('superadmin lands on /dashboard', () => {
    loginAs({ isSuperadmin: true });
    renderDefault();
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
  });

  it('redirects to the first available tab when there is no dashboard access', () => {
    // Нет dashboard:view, но есть mail:view — по порядку навигации первая доступная — /mail.
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'], servers: ['view'] },
    });
    renderDefault();
    expect(screen.getByText('MAIL')).toBeInTheDocument();
    expect(screen.queryByText('SERVERS')).not.toBeInTheDocument();
  });

  it('shows the «Недостаточно прав» stub when the user has no view anywhere', () => {
    loginAs({ isSuperadmin: false, role: 'Пусто', permissions: {} });
    renderDefault();
    expect(screen.getByText('Недостаточно прав')).toBeInTheDocument();
    // Не редиректит и не разлогинивает — только заглушка.
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
  });
});

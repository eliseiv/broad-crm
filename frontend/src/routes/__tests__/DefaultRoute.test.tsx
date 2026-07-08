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
      <Route path="/mail" element={<div>MAIL</div>} />
      <Route path="/servers" element={<div>SERVERS</div>} />
      <Route path="/users" element={<div>USERS</div>} />
      <Route path="/roles" element={<div>ROLES</div>} />
    </Routes>,
    { wrapper },
  );
}

describe('DefaultRoute (permission-aware default без /dashboard, ADR-022)', () => {
  afterEach(() => logout());

  it('superadmin lands on the first nav leaf /mail (dashboard больше не дефолт)', () => {
    loginAs({ isSuperadmin: true });
    renderDefault();
    expect(screen.getByText('MAIL')).toBeInTheDocument();
  });

  it('redirects to /mail when the user has mail:view', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'], servers: ['view'] },
    });
    renderDefault();
    expect(screen.getByText('MAIL')).toBeInTheDocument();
    expect(screen.queryByText('SERVERS')).not.toBeInTheDocument();
  });

  it('falls through to the first available leaf when earlier leaves are inaccessible', () => {
    // Нет mail, но есть servers:view — первая доступная в порядке навигации /servers.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
    renderDefault();
    expect(screen.getByText('SERVERS')).toBeInTheDocument();
  });

  it('resolves /roles when only roles:view is granted', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { roles: ['view'] } });
    renderDefault();
    expect(screen.getByText('ROLES')).toBeInTheDocument();
  });

  it('non-superadmin role=admin (no explicit perms) reaches /users via admin flag', () => {
    // `users` гейтится admin-признаком, не матрицей (04-api.md, ADR-021/022).
    loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });
    renderDefault();
    expect(screen.getByText('USERS')).toBeInTheDocument();
  });

  it('shows the «Недостаточно прав» stub when the user has no view anywhere', () => {
    loginAs({ isSuperadmin: false, role: 'Пусто', permissions: {} });
    renderDefault();
    expect(screen.getByText('Недостаточно прав')).toBeInTheDocument();
    // Не редиректит и не разлогинивает — только заглушка.
    expect(screen.queryByText('MAIL')).not.toBeInTheDocument();
  });
});

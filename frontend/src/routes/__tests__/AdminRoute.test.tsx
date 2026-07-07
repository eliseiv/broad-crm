import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';
import { AdminRoute } from '@/routes/AdminRoute';
import { loginAs, logout } from '@/test/authTestUtils';

function renderAdmin() {
  function wrapper({ children }: PropsWithChildren) {
    return <MemoryRouter initialEntries={['/users']}>{children}</MemoryRouter>;
  }
  return render(
    <Routes>
      <Route element={<AdminRoute />}>
        <Route path="/users" element={<div>USERS PAGE</div>} />
      </Route>
      <Route path="/dashboard" element={<div>DASHBOARD</div>} />
    </Routes>,
    { wrapper },
  );
}

describe('AdminRoute (admin-only guard, ADR-021)', () => {
  afterEach(() => logout());

  it('renders the guarded page for a superadmin', () => {
    loginAs({ isSuperadmin: true });
    renderAdmin();
    expect(screen.getByText('USERS PAGE')).toBeInTheDocument();
  });

  it('renders the guarded page for a DB user with role admin', () => {
    loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });
    renderAdmin();
    expect(screen.getByText('USERS PAGE')).toBeInTheDocument();
  });

  it('redirects a non-admin to /dashboard (session not cleared)', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
    renderAdmin();
    expect(screen.getByText('DASHBOARD')).toBeInTheDocument();
    expect(screen.queryByText('USERS PAGE')).not.toBeInTheDocument();
  });
});

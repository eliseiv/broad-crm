import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it } from 'vitest';
import { AdminRoute } from '@/routes/AdminRoute';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { useAuthStore } from '@/store/auth';
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

  it('shows the page-scoped «Недостаточно прав» stub for a non-admin (no redirect, session kept)', () => {
    // ADR-021 §6: AdminRoute для /users показывает page-scoped заглушку, а НЕ
    // редиректит на /dashboard и НЕ сбрасывает сессию.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
    renderAdmin();

    // Page-scoped заглушка «нет доступа к разделу» (доступ к другим разделам может быть).
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Контент /users скрыт; редиректа на /dashboard нет.
    expect(screen.queryByText('USERS PAGE')).not.toBeInTheDocument();
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
    // Сессия НЕ сброшена (403 ≠ 401).
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
});

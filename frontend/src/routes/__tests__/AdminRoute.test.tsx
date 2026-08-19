import { render, screen } from '@testing-library/react';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AdminRoute } from '@/routes/AdminRoute';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { useAuthStore } from '@/store/auth';
import { loginAs, logout } from '@/test/authTestUtils';

const getPermissionsCatalog = vi.fn();
vi.mock('@/features/users/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/users/api')>();
  return {
    ...actual,
    getPermissionsCatalog: (...args: unknown[]) => getPermissionsCatalog(...args),
  };
});

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

describe('AdminRoute (admin-only guard, ADR-078)', () => {
  afterEach(() => {
    logout();
    getPermissionsCatalog.mockClear();
  });

  it('renders the guarded page when isAdminLevel is true (superadmin)', () => {
    loginAs({ isSuperadmin: true, isAdminLevel: true });
    renderAdmin();
    expect(screen.getByText('USERS PAGE')).toBeInTheDocument();
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
  });

  it('renders the guarded page when isAdminLevel is true (role admin)', () => {
    loginAs({ isSuperadmin: false, roles: ['admin'], isAdminLevel: true, permissions: {} });
    renderAdmin();
    expect(screen.getByText('USERS PAGE')).toBeInTheDocument();
  });

  it('роль «Админ» с isAdminLevel открывает /users без Spinner каталога', () => {
    loginAs({ isSuperadmin: false, roles: ['Админ'], isAdminLevel: true, permissions: {} });
    renderAdmin();
    expect(screen.getByText('USERS PAGE')).toBeInTheDocument();
    expect(screen.queryByText(INSUFFICIENT_PERMISSIONS_TITLE)).not.toBeInTheDocument();
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
    expect(getPermissionsCatalog).not.toHaveBeenCalled();
  });

  it('shows the page-scoped «Недостаточно прав» stub when isAdminLevel is false (no redirect, session kept)', () => {
    loginAs({
      isSuperadmin: false,
      roles: ['Оператор'],
      isAdminLevel: false,
      permissions: { servers: ['view'] },
    });
    renderAdmin();

    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    expect(screen.queryByText('USERS PAGE')).not.toBeInTheDocument();
    expect(screen.queryByText('DASHBOARD')).not.toBeInTheDocument();
    expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
});

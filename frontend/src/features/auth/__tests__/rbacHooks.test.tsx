import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useCan, useCanViewPage, useIsAdmin } from '@/features/auth/hooks';
import { loginAs, logout } from '@/test/authTestUtils';

describe('useCan / useIsAdmin (UI-гейтинг RBAC, ADR-021)', () => {
  afterEach(() => logout());

  it('superadmin can do everything and is admin', () => {
    loginAs({ isSuperadmin: true });

    expect(renderHook(() => useCan('servers', 'delete')).result.current).toBe(true);
    expect(renderHook(() => useCan('ai-keys', 'create')).result.current).toBe(true);
    expect(renderHook(() => useIsAdmin()).result.current).toBe(true);
  });

  it('operator is gated by its permissions map', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { servers: ['view'], mail: ['view'] },
    });

    expect(renderHook(() => useCan('servers', 'view')).result.current).toBe(true);
    expect(renderHook(() => useCan('servers', 'edit')).result.current).toBe(false);
    expect(renderHook(() => useCan('ai-keys', 'view')).result.current).toBe(false);
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
  });

  it('a DB user whose role is named admin passes useIsAdmin', () => {
    loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });

    expect(renderHook(() => useIsAdmin()).result.current).toBe(true);
  });

  it('a user without loaded permissions can do nothing', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: {} });

    expect(renderHook(() => useCan('servers', 'view')).result.current).toBe(false);
    expect(renderHook(() => useIsAdmin()).result.current).toBe(false);
  });
});

// Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
// доступ к странице ⇔ admin/superadmin ИЛИ `view` ∈ permissions[page].
describe('useCanViewPage (page-level view-guard, ADR-021 §6)', () => {
  afterEach(() => logout());

  it('superadmin can view any page', () => {
    loginAs({ isSuperadmin: true });

    expect(renderHook(() => useCanViewPage('dashboard')).result.current).toBe(true);
    expect(renderHook(() => useCanViewPage('mail')).result.current).toBe(true);
    expect(renderHook(() => useCanViewPage('servers')).result.current).toBe(true);
  });

  it('a DB user with role admin can view any page (даже с пустыми permissions)', () => {
    loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });

    expect(renderHook(() => useCanViewPage('dashboard')).result.current).toBe(true);
    expect(renderHook(() => useCanViewPage('mail')).result.current).toBe(true);
    expect(renderHook(() => useCanViewPage('ai-keys')).result.current).toBe(true);
  });

  it('a regular user can view a page iff view ∈ permissions[page]', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'], servers: ['view', 'edit'] },
    });

    expect(renderHook(() => useCanViewPage('mail')).result.current).toBe(true);
    expect(renderHook(() => useCanViewPage('servers')).result.current).toBe(true);
    // Нет ключа страницы → нет доступа.
    expect(renderHook(() => useCanViewPage('dashboard')).result.current).toBe(false);
    expect(renderHook(() => useCanViewPage('ai-keys')).result.current).toBe(false);
  });

  it('a page present without the view action is not viewable', () => {
    // Ключ страницы есть, но без действия `view` (напр. только create) → нет доступа.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['create'] } });

    expect(renderHook(() => useCanViewPage('servers')).result.current).toBe(false);
  });
});

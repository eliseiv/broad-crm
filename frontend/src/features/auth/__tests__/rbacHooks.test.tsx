import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useCan, useIsAdmin } from '@/features/auth/hooks';
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

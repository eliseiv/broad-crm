import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MeResponse } from '@/types/api';

/**
 * Auth-стор: токен и права принципала персистятся в localStorage (ADR-041, амендмент
 * 05-security.md). Ключевое отличие от прежнего sessionStorage — переживание reload и
 * ШАРИНГ между вкладками: новая вкладка синхронно регидрирует стор из общего localStorage
 * (guard видит сессию на первом рендере). Регидрация происходит в `create()`, поэтому
 * мульти-вкладочность проверяется свежим импортом модуля (`vi.resetModules`).
 */

const TOKEN_KEY = 'crm.auth.token';
const USER_KEY = 'crm.auth.username';
const ROLE_KEY = 'crm.auth.role';
const SUPERADMIN_KEY = 'crm.auth.superadmin';
const PERMISSIONS_KEY = 'crm.auth.permissions';

const me: MeResponse = {
  username: 'ivan',
  role: 'Оператор',
  is_superadmin: false,
  sees_all_sms_teams: false,
  sees_all_mail_teams: false,
  permissions: { servers: ['view', 'edit'] },
};

/** Свежая копия модуля стора — регидрация из localStorage выполняется в create(). */
async function freshStore() {
  vi.resetModules();
  return import('@/store/auth');
}

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  localStorage.clear();
  sessionStorage.clear();
});

describe('auth store — сессия в localStorage (ADR-041)', () => {
  it('setSession пишет токен в localStorage (переживает reload), НЕ в sessionStorage', async () => {
    const { useAuthStore, getToken } = await freshStore();
    useAuthStore.getState().setSession('jwt-abc', 'ivan');

    expect(localStorage.getItem(TOKEN_KEY)).toBe('jwt-abc');
    expect(localStorage.getItem(USER_KEY)).toBe('ivan');
    // Прежнее хранилище (sessionStorage, изолированное по вкладке) больше не используется.
    expect(sessionStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(getToken()).toBe('jwt-abc');
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });

  it('мульти-вкладочность: токен из localStorage регидрирует стор при свежем импорте (новая вкладка)', async () => {
    // Вкладка A уже залогинилась — токен и права лежат в общем localStorage.
    localStorage.setItem(TOKEN_KEY, 'jwt-shared');
    localStorage.setItem(USER_KEY, 'ivan');
    localStorage.setItem(ROLE_KEY, 'Оператор');
    localStorage.setItem(SUPERADMIN_KEY, '0');
    localStorage.setItem(PERMISSIONS_KEY, JSON.stringify({ servers: ['view'] }));

    // Вкладка B монтирует приложение заново → стор наполнен ДО первого рендера.
    const { useAuthStore, getToken } = await freshStore();
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(true);
    expect(getToken()).toBe('jwt-shared');
    expect(state.username).toBe('ivan');
    expect(state.role).toBe('Оператор');
    expect(state.isSuperadmin).toBe(false);
    expect(state.permissions).toEqual({ servers: ['view'] });
  });

  it('без токена в localStorage стор стартует разлогиненным', async () => {
    const { useAuthStore } = await freshStore();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(useAuthStore.getState().token).toBeNull();
  });

  it('setPrincipal персистит роль/права/superadmin в localStorage', async () => {
    const { useAuthStore } = await freshStore();
    useAuthStore.getState().setPrincipal(me);

    expect(localStorage.getItem(ROLE_KEY)).toBe('Оператор');
    expect(localStorage.getItem(SUPERADMIN_KEY)).toBe('0');
    expect(JSON.parse(localStorage.getItem(PERMISSIONS_KEY) as string)).toEqual({
      servers: ['view', 'edit'],
    });
  });

  it('clearSession стирает все crm.auth.* и сбрасывает состояние (logout во всех вкладках)', async () => {
    const { useAuthStore, clearSession } = await freshStore();
    useAuthStore.getState().setSession('jwt-abc', 'ivan');
    useAuthStore.getState().setPrincipal(me);

    clearSession();

    for (const key of [TOKEN_KEY, USER_KEY, ROLE_KEY, SUPERADMIN_KEY, PERMISSIONS_KEY]) {
      expect(localStorage.getItem(key)).toBeNull();
    }
    const state = useAuthStore.getState();
    expect(state.isAuthenticated).toBe(false);
    expect(state.token).toBeNull();
    expect(state.permissions).toBeNull();
  });
});

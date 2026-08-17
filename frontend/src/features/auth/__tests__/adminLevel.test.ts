import { describe, expect, it } from 'vitest';
import {
  coversFullCatalog,
  needsPermissionsCatalog,
  resolveAdminLevel,
} from '@/features/auth/adminLevel';
import type { PermissionCatalogPage, PermissionsMap } from '@/types/api';

const CATALOG: PermissionCatalogPage[] = [
  { page: 'dashboard', actions: ['view'] },
  { page: 'documents', actions: ['view', 'create', 'edit', 'delete', 'share'] },
  { page: 'broadcast', actions: ['view', 'send'] },
];

const FULL: PermissionsMap = {
  dashboard: ['view'],
  documents: ['view', 'create', 'edit', 'delete', 'share'],
  broadcast: ['view', 'send'],
};

describe('adminLevel (ADR-076, без мигания заглушки)', () => {
  it('needsPermissionsCatalog: superadmin и role=admin — каталог не нужен', () => {
    expect(needsPermissionsCatalog(true, 'x', FULL)).toBe(false);
    expect(needsPermissionsCatalog(false, 'admin', {})).toBe(false);
  });

  it('needsPermissionsCatalog: «Админ» с roles:view — каталог нужен', () => {
    expect(needsPermissionsCatalog(false, 'Админ', { roles: ['view'], ...FULL })).toBe(true);
  });

  it('coversFullCatalog: полный набор → true; без share или broadcast → false', () => {
    expect(coversFullCatalog(FULL, CATALOG)).toBe(true);
    expect(
      coversFullCatalog({ ...FULL, documents: ['view', 'create', 'edit', 'delete'] }, CATALOG),
    ).toBe(false);
    const noBroadcast = { ...FULL };
    delete noBroadcast.broadcast;
    expect(coversFullCatalog(noBroadcast, CATALOG)).toBe(false);
    expect(coversFullCatalog(FULL, undefined)).toBe(false);
  });

  it('resolveAdminLevel: пока каталог грузится — catalogPending, не «не admin»', () => {
    const pending = resolveAdminLevel({
      isSuperadmin: false,
      role: 'Админ',
      permissions: { roles: ['view'], ...FULL },
      catalog: undefined,
      needsCatalog: true,
      catalogReady: false,
    });
    expect(pending).toEqual({ isAdmin: false, catalogPending: true });
  });

  it('resolveAdminLevel: каталог готов и покрыт — isAdmin без pending', () => {
    const ready = resolveAdminLevel({
      isSuperadmin: false,
      role: 'Админ',
      permissions: { roles: ['view'], ...FULL },
      catalog: CATALOG,
      needsCatalog: true,
      catalogReady: true,
    });
    expect(ready).toEqual({ isAdmin: true, catalogPending: false });
  });

  it('resolveAdminLevel: superadmin / role=admin — сразу admin, без pending', () => {
    expect(
      resolveAdminLevel({
        isSuperadmin: true,
        role: 'x',
        permissions: {},
        catalog: undefined,
        needsCatalog: false,
        catalogReady: false,
      }),
    ).toEqual({ isAdmin: true, catalogPending: false });
    expect(
      resolveAdminLevel({
        isSuperadmin: false,
        role: 'admin',
        permissions: {},
        catalog: undefined,
        needsCatalog: false,
        catalogReady: false,
      }),
    ).toEqual({ isAdmin: true, catalogPending: false });
  });
});

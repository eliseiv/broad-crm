import { createContext, useContext } from 'react';
import { useAuthStore } from '@/store/auth';
import type { PermissionCatalogPage, PermissionsMap } from '@/types/api';

export interface AdminLevelState {
  isAdmin: boolean;
  /** `true` — ждём GET /api/permissions/catalog; ещё нельзя считать «не admin». */
  catalogPending: boolean;
}

export const AdminLevelContext = createContext<AdminLevelState | null>(null);

/** Нужен серверный каталог, чтобы проверить полное покрытие (не superadmin / не role admin). */
export function needsPermissionsCatalog(
  isSuperadmin: boolean,
  role: string | null,
  permissions: PermissionsMap | null,
): boolean {
  return !isSuperadmin && role !== 'admin' && Boolean(permissions?.roles?.includes('view'));
}

/**
 * Полное покрытие серверного каталога правами принципала (ADR-076 §4).
 * Без каталога — false: не угадываем состав «на глаз».
 */
export function coversFullCatalog(
  permissions: PermissionsMap | null | undefined,
  catalog: PermissionCatalogPage[] | undefined,
): boolean {
  if (!permissions || !catalog?.length) return false;
  return catalog.every(
    ({ page, actions }) =>
      actions.length > 0 && actions.every((action) => Boolean(permissions[page]?.includes(action))),
  );
}

/**
 * `is_admin_level` + фаза загрузки каталога. Пока `needsCatalog && !catalogReady`
 * не считаем «не admin» (`catalogPending`).
 */
export function resolveAdminLevel(input: {
  isSuperadmin: boolean;
  role: string | null;
  permissions: PermissionsMap | null;
  catalog: PermissionCatalogPage[] | undefined;
  needsCatalog: boolean;
  catalogReady: boolean;
}): AdminLevelState {
  if (input.isSuperadmin || input.role === 'admin') {
    return { isAdmin: true, catalogPending: false };
  }
  if (input.needsCatalog && !input.catalogReady) {
    return { isAdmin: false, catalogPending: true };
  }
  return {
    isAdmin: coversFullCatalog(input.permissions, input.catalog),
    catalogPending: false,
  };
}

/**
 * Admin-уровень из провайдера (AppLayout читает catalog из query.data в том же рендере).
 * Без провайдера (тесты) — только `is_superadmin || role==="admin"`.
 */
export function useAdminLevel(): AdminLevelState {
  const ctx = useContext(AdminLevelContext);
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const role = useAuthStore((s) => s.role);
  if (ctx) return ctx;
  return { isAdmin: isSuperadmin || role === 'admin', catalogPending: false };
}

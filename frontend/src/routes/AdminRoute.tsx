import { Outlet } from 'react-router-dom';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { useIsAdmin } from '@/features/auth/hooks';

/**
 * Гард admin-only маршрутов (страница «Пользователи»). Доступ — только
 * `is_superadmin || role=="admin"`; иначе — заглушка «Недостаточно прав»
 * (page-scoped), БЕЗ редиректа и БЕЗ сброса сессии (ADR-021 §6,
 * 08-design-system.md «Page-level view-guard»). Ставится внутри
 * ProtectedRoute + AppLayout.
 */
export function AdminRoute() {
  const isAdmin = useIsAdmin();
  if (!isAdmin) {
    return <InsufficientPermissions />;
  }
  return <Outlet />;
}

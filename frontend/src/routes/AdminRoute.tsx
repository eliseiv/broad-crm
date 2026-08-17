import { Outlet } from 'react-router-dom';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Spinner } from '@/components/ui/Spinner';
import { useAdminLevel } from '@/features/auth/hooks';

/**
 * Гард admin-only маршрутов (страница «Пользователи»). Доступ — `is_admin_level`
 * (`is_superadmin || role=="admin"` или полное покрытие серверного каталога);
 * пока каталог грузится — Spinner (не заглушка); иначе — заглушка «Недостаточно прав»
 * (page-scoped), БЕЗ редиректа и БЕЗ сброса сессии (ADR-021 §6, ADR-076,
 * 08-design-system.md «Page-level view-guard»). Ставится внутри
 * ProtectedRoute + AppLayout.
 */
export function AdminRoute() {
  const { isAdmin, catalogPending } = useAdminLevel();
  if (catalogPending) {
    return (
      <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
        <Spinner className="text-text-secondary" />
        Загрузка…
      </div>
    );
  }
  if (!isAdmin) {
    return <InsufficientPermissions />;
  }
  return <Outlet />;
}

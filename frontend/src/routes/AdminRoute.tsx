import { Navigate, Outlet } from 'react-router-dom';
import { useIsAdmin } from '@/features/auth/hooks';

/**
 * Гард admin-only маршрутов (страница «Пользователи»). Доступ — только
 * `is_superadmin || role=="admin"`; иначе редирект на /dashboard (без сброса
 * сессии). Ставится внутри ProtectedRoute + AppLayout. ADR-021,
 * 08-design-system.md «Гейтинг и 403».
 */
export function AdminRoute() {
  const isAdmin = useIsAdmin();
  if (!isAdmin) {
    return <Navigate to="/dashboard" replace />;
  }
  return <Outlet />;
}

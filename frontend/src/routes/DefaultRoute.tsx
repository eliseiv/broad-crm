import { Navigate } from 'react-router-dom';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { useIsAdmin } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

/**
 * Плоский порядок листьев навигации (08-design-system.md «Навигация», ADR-022) —
 * БЕЗ `dashboard`: первая доступная по правам вкладка определяется этим порядком
 * сверху вниз. `users` гейтится admin-признаком (не матрицей). «Дашборд» больше не
 * дефолт — доступен только по прямому URL `/dashboard`.
 */
const NAV_ORDER: { path: string; page: string }[] = [
  { path: '/mail', page: 'mail' },
  { path: '/sms', page: 'sms' },
  { path: '/servers', page: 'servers' },
  { path: '/ai-keys', page: 'ai-keys' },
  { path: '/proxies', page: 'proxies' },
  { path: '/backends', page: 'backends' },
  { path: '/users', page: 'users' },
  { path: '/roles', page: 'roles' },
  { path: '/teams', page: 'teams' },
  // «Документы» — в конце плоского порядка (ADR-061 §1), приоритеты не меняются.
  { path: '/documents', page: 'documents' },
];

/**
 * Permission-aware дефолтный маршрут (index `/` и fallback `*`), 08-design-system.md
 * «Дефолтный маршрут после логина (permission-aware)», ADR-022:
 *  - редирект на ПЕРВУЮ доступную вкладку в порядке навигации (без `dashboard`);
 *    `users` — по admin-признаку, ресурсные/roles/teams — по `<page>:view`;
 *  - если нет ни одного доступного листа (и не admin/superadmin) — заглушка
 *    «Недостаточно прав» (global-scope), БЕЗ сброса сессии и редиректа на /login.
 * Рендерится внутри AppLayout (шапка с «Выйти» доступна; useMe обновляет права).
 */
export function DefaultRoute() {
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const permissions = useAuthStore((s) => s.permissions);
  const isAdmin = useIsAdmin();

  const canReach = (page: string) => {
    if (page === 'users') return isAdmin;
    return isSuperadmin || Boolean(permissions?.[page]?.includes('view'));
  };

  const firstTab = NAV_ORDER.find((tab) => canReach(tab.page));
  if (firstTab) {
    return <Navigate to={firstTab.path} replace />;
  }

  // Нет ни одного доступного листа (и не admin/superadmin) — заглушка «нет ни
  // одного раздела» (global-scope), БЕЗ сброса сессии / редиректа (08-design-system.md).
  return <InsufficientPermissions scope="global" />;
}

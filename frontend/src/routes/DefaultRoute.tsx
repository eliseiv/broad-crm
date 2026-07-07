import { ShieldAlert } from 'lucide-react';
import { Navigate } from 'react-router-dom';
import { useIsAdmin } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

/**
 * Порядок навигации (08-design-system.md «Навигация»): первая доступная по `view`
 * вкладка определяется этим порядком сверху вниз. «Пользователи» гейтится admin —
 * покрывается коротким замыканием isAdmin ниже, поэтому в списке ресурсных вкладок нет.
 */
const NAV_ORDER: { path: string; page: string }[] = [
  { path: '/dashboard', page: 'dashboard' },
  { path: '/mail', page: 'mail' },
  { path: '/servers', page: 'servers' },
  { path: '/ai-keys', page: 'ai-keys' },
  { path: '/proxies', page: 'proxies' },
  { path: '/backends', page: 'backends' },
];

/**
 * Permission-aware дефолтный маршрут (index `/` и fallback `*`), 08-design-system.md
 * «Дефолтный маршрут после логина (permission-aware)»:
 *  - `/dashboard`, если есть `dashboard:view` (или пользователь admin/superadmin);
 *  - иначе — редирект на ПЕРВУЮ доступную по `view` вкладку в порядке навигации;
 *  - если нет ни одного `view` (и не admin/superadmin) — страница-заглушка
 *    «Недостаточно прав» БЕЗ сброса сессии и БЕЗ редиректа на /login.
 * Рендерится внутри AppLayout (шапка с «Выйти» доступна; useMe обновляет права).
 */
export function DefaultRoute() {
  const isSuperadmin = useAuthStore((s) => s.isSuperadmin);
  const permissions = useAuthStore((s) => s.permissions);
  const isAdmin = useIsAdmin();

  const canView = (page: string) => isSuperadmin || Boolean(permissions?.[page]?.includes('view'));

  if (isSuperadmin || isAdmin || canView('dashboard')) {
    return <Navigate to="/dashboard" replace />;
  }

  const firstTab = NAV_ORDER.find((tab) => canView(tab.page));
  if (firstTab) {
    return <Navigate to={firstTab.path} replace />;
  }

  return <NoAccessStub />;
}

/** Заглушка «Недостаточно прав» — тексты дословно из словаря (08-design-system.md). */
function NoAccessStub() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
      <ShieldAlert className="h-10 w-10 text-text-tertiary" aria-hidden="true" />
      <div className="max-w-md">
        <p className="text-lg font-semibold text-text-primary">Недостаточно прав</p>
        <p className="mt-1 text-[13px] text-text-secondary">
          У вашей учётной записи нет доступа ни к одному разделу. Обратитесь к администратору.
        </p>
      </div>
    </div>
  );
}

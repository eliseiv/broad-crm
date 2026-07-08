import { LogOut, ServerCog } from 'lucide-react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { NavMenu } from '@/components/ui/NavMenu';
import type { NavMenuItem } from '@/components/ui/NavMenu';
import { cn } from '@/lib/cn';
import { useCanViewPage, useIsAdmin, useMe } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

/**
 * Категоризированная навигация (08-design-system.md «Навигация (категории-
 * дропдауны)», ADR-022). Каждый пункт хранит `page` — ключ гейтинга видимости.
 * «Дашборд» в меню отсутствует (маршрут доступен только по прямому URL).
 */
interface NavLeaf extends NavMenuItem {
  page: string;
}
interface NavCategory {
  label: string;
  leaves: NavLeaf[];
}

const CATEGORIES: NavCategory[] = [
  {
    label: 'Агрегатор',
    leaves: [{ to: '/mail', label: 'Почты', page: 'mail' }],
  },
  {
    label: 'Мониторинг',
    leaves: [
      { to: '/servers', label: 'Серверы', page: 'servers' },
      { to: '/ai-keys', label: 'ИИ - ключи', page: 'ai-keys' },
      { to: '/proxies', label: 'Прокси', page: 'proxies' },
      { to: '/backends', label: 'Бэки', page: 'backends' },
    ],
  },
  {
    label: 'Пользователи',
    leaves: [
      { to: '/users', label: 'Пользователи', page: 'users' },
      { to: '/roles', label: 'Роли', page: 'roles' },
      { to: '/teams', label: 'Команды', page: 'teams' },
    ],
  },
];

/**
 * Общий layout с верхней категоризированной навигацией (08-design-system.md
 * «Навигация», ADR-022). Все страницы — под auth-guard. Гейтинг видимости — только
 * UX; безопасность обеспечивает сервер (403).
 */
export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const username = useAuthStore((s) => s.username);
  const clearSession = useAuthStore((s) => s.clearSession);

  // Обновляем права принципала при входе на защищённые страницы (ADR-021:
  // права могут меняться без пере-логина). Наполняет стор → гейтинг реактивен.
  useMe();

  // Доступ по страницам (общий useCanViewPage, без инлайн-canView). Пункт `users`
  // гейтится admin-признаком (вне матрицы, ADR-021); остальные — <page>:view.
  const isAdmin = useIsAdmin();
  const access: Record<string, boolean> = {
    mail: useCanViewPage('mail'),
    servers: useCanViewPage('servers'),
    'ai-keys': useCanViewPage('ai-keys'),
    proxies: useCanViewPage('proxies'),
    backends: useCanViewPage('backends'),
    roles: useCanViewPage('roles'),
    teams: useCanViewPage('teams'),
    users: isAdmin,
  };
  const canSee = (page: string) => Boolean(access[page]);

  // Два режима shell по маршруту (08-design-system.md «Full-bleed layout»):
  //  • /mail (full-bleed) — фиксированная высота; скролл внутри панелей master-detail.
  //  • не-mail — обычный поток документа (min-h-screen), ширину держит внутренний div.
  const isFullBleed = location.pathname.startsWith('/mail');

  const handleLogout = () => {
    clearSession();
    queryClient.clear();
    navigate('/login', { replace: true });
  };

  return (
    <div
      className={cn(
        'flex flex-col bg-bg-base',
        isFullBleed ? 'h-screen overflow-hidden' : 'min-h-screen',
      )}
    >
      <header
        className={cn(
          'border-b border-border-subtle bg-bg-base/80 backdrop-blur-md',
          isFullBleed ? 'shrink-0' : 'sticky top-0 z-30',
        )}
      >
        <div className="mx-auto flex max-w-[1400px] items-center justify-between px-6 py-3">
          <div className="flex items-center gap-6">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/15 text-accent">
              <ServerCog className="h-[18px] w-[18px]" aria-hidden="true" />
            </span>
            <nav className="flex items-center gap-1" aria-label="Основная навигация">
              {CATEGORIES.map((category) => {
                // Пункты категории, доступные пользователю (08-design-system.md:
                // пункт виден ⇔ есть доступ). Категория видна ⇔ ≥1 доступного пункта.
                const visibleLeaves = category.leaves.filter((leaf) => canSee(leaf.page));
                if (visibleLeaves.length === 0) return null;
                const active = visibleLeaves.some((leaf) => location.pathname.startsWith(leaf.to));
                return (
                  <NavMenu
                    key={category.label}
                    label={category.label}
                    active={active}
                    items={visibleLeaves.map(({ to, label }) => ({ to, label }))}
                  />
                );
              })}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            {username && (
              <span className="hidden text-[13px] text-text-secondary sm:inline">
                <span className="font-mono text-text-primary">{username}</span>
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={handleLogout}>
              <LogOut className="h-4 w-4" />
              Выйти
            </Button>
          </div>
        </div>
      </header>

      {isFullBleed ? (
        <main className="w-full min-h-0 flex-1 overflow-hidden">
          <Outlet />
        </main>
      ) : (
        <main>
          <div className="mx-auto max-w-[1400px] px-6 py-8">
            <Outlet />
          </div>
        </main>
      )}
    </div>
  );
}

import { LogOut, Moon, ServerCog, Sun } from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { useTheme } from '@/lib/theme';
import { useCanViewPage, useIsAdmin, useMe } from '@/features/auth/hooks';
import { useAuthStore } from '@/store/auth';

/**
 * Плоская навигация (08-design-system.md «Навигация (плоская, AppLayout)»,
 * ADR-033). Каждый пункт — `NavLink` прямо в ряду хэдера, без категорий-дропдаунов.
 * `page` — ключ гейтинга видимости. «Дашборд» в меню отсутствует (только по URL).
 */
interface NavItem {
  to: string;
  label: string;
  page: string;
}

// Нормативный порядок (08-design-system.md §Навигация): агрегатор → мониторинг →
// пользователи, но плоским рядом без визуальных заголовков-категорий.
const NAV_ITEMS: NavItem[] = [
  { to: '/mail', label: 'Почты', page: 'mail' },
  { to: '/sms', label: 'СМС', page: 'sms' },
  { to: '/servers', label: 'Серверы', page: 'servers' },
  { to: '/ai-keys', label: 'ИИ - ключи', page: 'ai-keys' },
  { to: '/proxies', label: 'Прокси', page: 'proxies' },
  { to: '/backends', label: 'Бэки', page: 'backends' },
  { to: '/backend-users', label: 'Юзеры бэков', page: 'backend-users' },
  { to: '/users', label: 'Пользователи', page: 'users' },
  { to: '/roles', label: 'Роли', page: 'roles' },
  { to: '/teams', label: 'Команды', page: 'teams' },
  // «Документы» — пункт 10 в конце ряда (ADR-061 §1), гейт documents:view.
  { to: '/documents', label: 'Документы', page: 'documents' },
];

/**
 * Общий layout с верхней плоской навигацией (08-design-system.md «Навигация»,
 * ADR-033). Все страницы — под auth-guard. Гейтинг видимости — только UX;
 * безопасность обеспечивает сервер (403). В правой части — переключатель темы.
 */
export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const username = useAuthStore((s) => s.username);
  const clearSession = useAuthStore((s) => s.clearSession);
  const { theme, toggle } = useTheme();

  // Обновляем права принципала при входе на защищённые страницы (ADR-021:
  // права могут меняться без пере-логина). Наполняет стор → гейтинг реактивен.
  useMe();

  // Доступ по страницам (общий useCanViewPage, без инлайн-canView). Пункт `users`
  // гейтится admin-признаком (вне матрицы, ADR-021); остальные — <page>:view.
  const isAdmin = useIsAdmin();
  const access: Record<string, boolean> = {
    mail: useCanViewPage('mail'),
    sms: useCanViewPage('sms'),
    servers: useCanViewPage('servers'),
    'ai-keys': useCanViewPage('ai-keys'),
    proxies: useCanViewPage('proxies'),
    backends: useCanViewPage('backends'),
    'backend-users': useCanViewPage('backend-users'),
    roles: useCanViewPage('roles'),
    teams: useCanViewPage('teams'),
    documents: useCanViewPage('documents'),
    users: isAdmin,
  };

  // Только видимые по правам пункты (скрытый по правам пункт не рендерится).
  const visibleItems = NAV_ITEMS.filter((item) => Boolean(access[item.page]));

  // Два режима shell по маршруту (08-design-system.md «Full-bleed layout», ADR-061):
  //  • /mail и /documents (full-bleed) — фиксированная высота; скролл внутри панелей.
  //  • остальные — обычный поток документа (min-h-screen), ширину держит внутренний div.
  const isFullBleed =
    location.pathname.startsWith('/mail') || location.pathname.startsWith('/documents');

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
        <div className="flex w-full items-center gap-4 px-6 py-3">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <ServerCog className="h-[18px] w-[18px]" aria-hidden="true" />
          </span>
          {/* Плоский ряд пунктов; на узких вьюпортах — горизонтальный скролл ряда
              (scrollbar-none), высота хэдера фиксирована (flex-nowrap), sticky/
              full-bleed не ломаются (ADR-033 §Деградация хэдера). */}
          <nav
            aria-label="Основная навигация"
            className="scrollbar-none flex min-w-0 flex-1 flex-nowrap items-center gap-1 overflow-x-auto"
          >
            {visibleItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cn(
                    'shrink-0 whitespace-nowrap rounded-md px-3 py-2 text-[14px] font-medium transition-colors',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    isActive ? 'text-accent' : 'text-text-secondary hover:text-text-primary',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex shrink-0 items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={toggle}
              aria-label={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
            >
              {theme === 'dark' ? (
                <Sun className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Moon className="h-4 w-4" aria-hidden="true" />
              )}
            </Button>
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
          {/* Контент — на всю ширину вьюпорта (решение владельца, 2026-07-23): прежний
              контейнер max-w-[1400px] упразднён, отступы по краям сохранены. */}
          <div className="w-full px-6 py-8">
            <Outlet />
          </div>
        </main>
      )}
    </div>
  );
}

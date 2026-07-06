import { LogOut, ServerCog } from 'lucide-react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/cn';
import { useAuthStore } from '@/store/auth';

const TABS: { to: string; label: string }[] = [
  { to: '/mail', label: 'Почты' },
  { to: '/servers', label: 'Серверы' },
  { to: '/ai-keys', label: 'ИИ - ключи' },
];

/**
 * Общий layout с верхними вкладками-навигацией (08-design-system.md «Навигация»).
 * Заголовок, ранее зашитый в ServersPage, вынесен сюда. Обе страницы — под auth-guard.
 */
export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const username = useAuthStore((s) => s.username);
  const clearSession = useAuthStore((s) => s.clearSession);

  // Два режима shell по маршруту (08-design-system.md «Full-bleed layout»):
  //  • /mail (full-bleed) — модель фиксированной высоты: shell `h-screen overflow-hidden`,
  //    хэдер `shrink-0`, `<main>` `flex-1 min-h-0 w-full overflow-hidden`, `<Outlet/>` напрямую.
  //    Страница сама не скроллится, скролл — внутри панелей master-detail.
  //  • не-mail (/servers, /ai-keys) — ОБЫЧНЫЙ поток документа: shell `min-h-screen`
  //    (без h-screen/overflow-hidden), хэдер `sticky top-0`, `<main>` — простой контейнер
  //    (НЕ скролл-контейнер), ширину 1400px держит внутренний `<div>`-обёртка. Скроллит `body`
  //    нативно (скроллбар у края окна); влезает — скроллбара нет. Это устраняет контейнерный/
  //    фантомный скролл `<main>` (overflow-y-auto), который давал «панель скролла».
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
              {TABS.map((tab) => (
                <NavLink
                  key={tab.to}
                  to={tab.to}
                  className={({ isActive }) =>
                    cn(
                      'relative rounded-md px-3 py-2 text-[14px] font-medium transition-colors',
                      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                      'after:absolute after:inset-x-3 after:-bottom-[13px] after:h-0.5 after:rounded-full after:transition-colors',
                      isActive
                        ? 'text-accent after:bg-accent'
                        : 'text-text-secondary after:bg-transparent hover:text-text-primary',
                    )
                  }
                >
                  {tab.label}
                </NavLink>
              ))}
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

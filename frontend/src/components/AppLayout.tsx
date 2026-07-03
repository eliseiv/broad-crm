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

  // Full-bleed layout для /mail: список примыкает вплотную к sticky-хэдеру, без внешнего
  // max-w-контейнера и верхних паддингов (08-design-system.md «Full-bleed layout», ADR-013
  // поправка). Прочие маршруты («Серверы»/«ИИ-ключи») — прежний ограниченный контейнер.
  const isFullBleed = location.pathname.startsWith('/mail');

  const handleLogout = () => {
    clearSession();
    queryClient.clear();
    navigate('/login', { replace: true });
  };

  return (
    <div className="min-h-screen bg-bg-base">
      <header className="sticky top-0 z-30 border-b border-border-subtle bg-bg-base/80 backdrop-blur-md">
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

      <main className={isFullBleed ? 'w-full' : 'mx-auto max-w-[1400px] px-6 py-8'}>
        <Outlet />
      </main>
    </div>
  );
}

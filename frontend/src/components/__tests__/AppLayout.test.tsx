import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AppLayout } from '@/components/AppLayout';
import { useAuthStore } from '@/store/auth';

function renderAt(initial: string) {
  function wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(
    <Routes>
      <Route element={<AppLayout />}>
        <Route path="/mail" element={<div>Контент почт</div>} />
        <Route path="/servers" element={<div>Контент серверов</div>} />
        <Route path="/ai-keys" element={<div>Контент ключей</div>} />
      </Route>
    </Routes>,
    { wrapper },
  );
}

describe('AppLayout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().setSession('jwt-token', 'admin');
  });

  it('renders both navigation tabs', () => {
    renderAt('/servers');
    expect(screen.getByRole('link', { name: 'Серверы' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ИИ - ключи' })).toBeInTheDocument();
  });

  it('marks the current route tab active', () => {
    renderAt('/servers');
    const serversTab = screen.getByRole('link', { name: 'Серверы' });
    const keysTab = screen.getByRole('link', { name: 'ИИ - ключи' });
    // Активная вкладка подсвечивается акцентом; неактивная — вторичным цветом.
    expect(serversTab.className).toContain('text-accent');
    expect(keysTab.className).toContain('text-text-secondary');
  });

  it('marks the ai-keys tab active on that route', () => {
    renderAt('/ai-keys');
    expect(screen.getByRole('link', { name: 'ИИ - ключи' }).className).toContain('text-accent');
    expect(screen.getByRole('link', { name: 'Серверы' }).className).toContain(
      'text-text-secondary',
    );
  });

  it('renders the child page through the Outlet', () => {
    renderAt('/servers');
    expect(screen.getByText('Контент серверов')).toBeInTheDocument();
    expect(screen.queryByText('Контент ключей')).not.toBeInTheDocument();
  });

  it('navigates to ai-keys when the tab is clicked', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    await user.click(screen.getByRole('link', { name: 'ИИ - ключи' }));

    expect(screen.getByText('Контент ключей')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ИИ - ключи' }).className).toContain('text-accent');
  });

  it('renders /mail full-bleed: <main> is w-full/overflow-hidden and Outlet renders directly', () => {
    renderAt('/mail');
    const main = document.querySelector('main');
    expect(main).not.toBeNull();
    // Full-bleed (08-design-system.md «Full-bleed layout», ADR-013 поправка):
    // <main> — полноширинный overflow-hidden без mx-auto/max-w/паддингов контейнера.
    expect(main?.className).toContain('w-full');
    expect(main?.className).toContain('overflow-hidden');
    expect(main?.className).not.toContain('max-w-[1400px]');
    expect(main?.className).not.toContain('mx-auto');
    expect(main?.className).not.toContain('px-6');
    expect(main?.className).not.toContain('py-8');
    // Outlet рендерится НАПРЯМУЮ в <main> (нет внутреннего max-w-div-обёртки).
    const content = screen.getByText('Контент почт');
    expect(content.parentElement?.tagName).toBe('MAIN');
  });

  it('/servers and /ai-keys: <main> is a full-width scroll container without max-width', () => {
    // Скролл-контейнер <main> — полноширинный (скроллбар у края окна), БЕЗ ограничения ширины
    // (08-design-system.md «Разделение скролл-контейнера и контейнера ширины», баг «панель скролла»).
    const { unmount } = renderAt('/servers');
    const serversMain = document.querySelector('main');
    expect(serversMain?.className).toContain('overflow-y-auto');
    expect(serversMain?.className).toContain('w-full');
    expect(serversMain?.className).toContain('flex-1');
    expect(serversMain?.className).toContain('min-h-0');
    expect(serversMain?.className).not.toContain('max-w-[1400px]');
    expect(serversMain?.className).not.toContain('mx-auto');
    unmount();

    renderAt('/ai-keys');
    const keysMain = document.querySelector('main');
    expect(keysMain?.className).toContain('overflow-y-auto');
    expect(keysMain?.className).toContain('w-full');
    expect(keysMain?.className).not.toContain('max-w-[1400px]');
  });

  it('/servers and /ai-keys: width is constrained by the inner max-width wrapper, not <main>', () => {
    // Ширину 1400px держит ВНУТРЕННИЙ <div>-обёртка вокруг <Outlet/>, а не сам <main>.
    const { unmount } = renderAt('/servers');
    const serversWrapper = screen.getByText('Контент серверов').parentElement;
    expect(serversWrapper?.tagName).toBe('DIV');
    expect(serversWrapper?.className).toContain('mx-auto');
    expect(serversWrapper?.className).toContain('max-w-[1400px]');
    expect(serversWrapper?.className).toContain('px-6');
    expect(serversWrapper?.className).toContain('py-8');
    // Обёртка лежит непосредственно внутри <main>.
    expect(serversWrapper?.parentElement?.tagName).toBe('MAIN');
    unmount();

    renderAt('/ai-keys');
    const keysWrapper = screen.getByText('Контент ключей').parentElement;
    expect(keysWrapper?.className).toContain('mx-auto');
    expect(keysWrapper?.className).toContain('max-w-[1400px]');
    expect(keysWrapper?.className).toContain('px-6');
    expect(keysWrapper?.className).toContain('py-8');
  });

  it('regression: /servers and /ai-keys content width is scroll-independent and identical', () => {
    // Оба бага не возвращаются: скролл-контейнер полноширинный без max-w, а ширину контента
    // на /servers и /ai-keys задаёт один и тот же внутренний max-w-div → ширина одинакова
    // независимо от наличия скролла (08-design-system.md §418).
    const { unmount } = renderAt('/servers');
    const serversMain = document.querySelector('main');
    const serversWrapperCls = screen.getByText('Контент серверов').parentElement?.className;
    expect(serversMain?.className).not.toContain('max-w-[1400px]');
    unmount();

    renderAt('/ai-keys');
    const keysMain = document.querySelector('main');
    const keysWrapperCls = screen.getByText('Контент ключей').parentElement?.className;
    expect(keysMain?.className).not.toContain('max-w-[1400px]');
    // Классы контейнера ширины идентичны на обоих маршрутах.
    expect(keysWrapperCls).toBe(serversWrapperCls);
  });

  it('shows the username and clears session on logout', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    expect(screen.getByText('admin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /выйти/i }));

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

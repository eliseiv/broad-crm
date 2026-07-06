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
        <Route path="/dashboard" element={<div>Контент дашборда</div>} />
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

  it('renders all navigation tabs including Дашборд', () => {
    renderAt('/servers');
    expect(screen.getByRole('link', { name: 'Дашборд' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Почты' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Серверы' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ИИ - ключи' })).toBeInTheDocument();
  });

  it('orders tabs with Дашборд first (ADR-017)', () => {
    renderAt('/servers');
    const tabNames = screen
      .getAllByRole('link')
      .map((el) => el.textContent?.trim())
      .filter((name): name is string => Boolean(name));
    // Порядок: Дашборд → Почты → Серверы → ИИ - ключи (08-design-system.md «Навигация»).
    expect(tabNames).toEqual(['Дашборд', 'Почты', 'Серверы', 'ИИ - ключи']);
  });

  it('marks the Дашборд tab active on /dashboard', () => {
    renderAt('/dashboard');
    expect(screen.getByRole('link', { name: 'Дашборд' }).className).toContain('text-accent');
    expect(screen.getByRole('link', { name: 'Почты' }).className).toContain('text-text-secondary');
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

  // Хелпер: shell — это <div>-обёртка, непосредственно содержащая <header> и <main>.
  function getShell() {
    return document.querySelector('header')?.parentElement ?? null;
  }

  it('renders /mail full-bleed: <main> w-full/overflow-hidden, Outlet direct, shell h-screen, header shrink-0', () => {
    renderAt('/mail');
    const main = document.querySelector('main');
    expect(main).not.toBeNull();
    // Full-bleed (08-design-system.md «Full-bleed layout», ADR-013): режим фиксированной высоты.
    // <main> — полноширинный overflow-hidden без scroll-контейнера и без mx-auto/max-w/паддингов.
    // classList.contains — точное сравнение по токенам (min-h-screen НЕ считается как h-screen).
    expect(main?.classList.contains('w-full')).toBe(true);
    expect(main?.classList.contains('overflow-hidden')).toBe(true);
    expect(main?.classList.contains('overflow-y-auto')).toBe(false);
    expect(main?.classList.contains('max-w-[1400px]')).toBe(false);
    expect(main?.classList.contains('mx-auto')).toBe(false);
    expect(main?.classList.contains('px-6')).toBe(false);
    expect(main?.classList.contains('py-8')).toBe(false);
    // Outlet рендерится НАПРЯМУЮ в <main> (нет внутренней max-w-обёртки).
    const content = screen.getByText('Контент почт');
    expect(content.parentElement?.tagName).toBe('MAIN');
    // Shell фиксированной высоты; хэдер не сжимается (лежит вне скролл-области).
    expect(getShell()?.classList.contains('h-screen')).toBe(true);
    expect(document.querySelector('header')?.classList.contains('shrink-0')).toBe(true);
  });

  it('/servers and /ai-keys: <main> is a plain container — not a scroll/flex/width container', () => {
    // Обычный поток документа (08-design-system.md §424-429): <main> НЕ скролл-контейнер и не
    // держит ширину. Скроллит body, ширину держит внутренний <div>. Прежние scroll-контейнерные
    // классы у <main> устарели после утверждённого layout-изменения.
    const { unmount } = renderAt('/servers');
    const serversMain = document.querySelector('main');
    expect(serversMain).not.toBeNull();
    for (const token of [
      'overflow-y-auto',
      'flex-1',
      'min-h-0',
      'w-full',
      'max-w-[1400px]',
      'mx-auto',
      'px-6',
      'py-8',
    ]) {
      expect(serversMain?.classList.contains(token)).toBe(false);
    }
    unmount();

    renderAt('/ai-keys');
    const keysMain = document.querySelector('main');
    for (const token of [
      'overflow-y-auto',
      'flex-1',
      'min-h-0',
      'w-full',
      'max-w-[1400px]',
      'mx-auto',
      'px-6',
      'py-8',
    ]) {
      expect(keysMain?.classList.contains(token)).toBe(false);
    }
  });

  it('/servers and /ai-keys: width held by inner <div> wrapper (content.parentElement is DIV), identical class', () => {
    // Ширину 1400px держит ВНУТРЕННИЙ <div>-обёртка вокруг <Outlet/>, а не <main>.
    const { unmount } = renderAt('/servers');
    const serversWrapper = screen.getByText('Контент серверов').parentElement;
    expect(serversWrapper?.tagName).toBe('DIV');
    expect(serversWrapper?.classList.contains('mx-auto')).toBe(true);
    expect(serversWrapper?.classList.contains('max-w-[1400px]')).toBe(true);
    expect(serversWrapper?.classList.contains('px-6')).toBe(true);
    expect(serversWrapper?.classList.contains('py-8')).toBe(true);
    // Обёртка лежит непосредственно внутри <main>.
    expect(serversWrapper?.parentElement?.tagName).toBe('MAIN');
    const serversWrapperCls = serversWrapper?.className;
    unmount();

    renderAt('/ai-keys');
    const keysWrapper = screen.getByText('Контент ключей').parentElement;
    expect(keysWrapper?.tagName).toBe('DIV');
    expect(keysWrapper?.classList.contains('mx-auto')).toBe(true);
    expect(keysWrapper?.classList.contains('max-w-[1400px]')).toBe(true);
    expect(keysWrapper?.classList.contains('px-6')).toBe(true);
    expect(keysWrapper?.classList.contains('py-8')).toBe(true);
    // Класс обёртки идентичен на /servers и /ai-keys.
    expect(keysWrapper?.className).toBe(serversWrapperCls);
  });

  it('/servers and /ai-keys: shell is min-h-screen (not h-screen/overflow-hidden), header is sticky top-0', () => {
    const { unmount } = renderAt('/servers');
    const serversShell = getShell();
    expect(serversShell?.classList.contains('min-h-screen')).toBe(true);
    expect(serversShell?.classList.contains('h-screen')).toBe(false);
    expect(serversShell?.classList.contains('overflow-hidden')).toBe(false);
    const serversHeader = document.querySelector('header');
    expect(serversHeader?.classList.contains('sticky')).toBe(true);
    expect(serversHeader?.classList.contains('top-0')).toBe(true);
    unmount();

    renderAt('/ai-keys');
    const keysShell = getShell();
    expect(keysShell?.classList.contains('min-h-screen')).toBe(true);
    expect(keysShell?.classList.contains('h-screen')).toBe(false);
    expect(keysShell?.classList.contains('overflow-hidden')).toBe(false);
    const keysHeader = document.querySelector('header');
    expect(keysHeader?.classList.contains('sticky')).toBe(true);
    expect(keysHeader?.classList.contains('top-0')).toBe(true);
  });

  it('/dashboard goes through the non-full-bleed branch like /servers (ADR-017)', () => {
    // Дашборд — не-full-bleed: обычный поток документа (min-h-screen shell, sticky header),
    // <main> — простой контейнер, ширину держит внутренний <div> max-w-[1400px]. НЕ как /mail.
    renderAt('/dashboard');
    const shell = getShell();
    expect(shell?.classList.contains('min-h-screen')).toBe(true);
    expect(shell?.classList.contains('h-screen')).toBe(false);
    expect(shell?.classList.contains('overflow-hidden')).toBe(false);

    const header = document.querySelector('header');
    expect(header?.classList.contains('sticky')).toBe(true);
    expect(header?.classList.contains('top-0')).toBe(true);
    expect(header?.classList.contains('shrink-0')).toBe(false);

    // <main> — не full-bleed скролл-контейнер; контент обёрнут в div.max-w-[1400px].
    const main = document.querySelector('main');
    expect(main?.classList.contains('w-full')).toBe(false);
    expect(main?.classList.contains('overflow-hidden')).toBe(false);
    const wrapper = screen.getByText('Контент дашборда').parentElement;
    expect(wrapper?.tagName).toBe('DIV');
    expect(wrapper?.classList.contains('max-w-[1400px]')).toBe(true);
    expect(wrapper?.parentElement?.tagName).toBe('MAIN');
  });

  it('regression: non-mail <main> is not a scroll container and /mail <main> stays overflow-hidden', () => {
    // Анти-регресс обоих режимов: не-mail <main> не порождает контейнерный скролл;
    // /mail <main> остаётся overflow-hidden (страница сама не скроллится).
    const { unmount } = renderAt('/servers');
    expect(document.querySelector('main')?.classList.contains('overflow-y-auto')).toBe(false);
    unmount();

    renderAt('/mail');
    expect(document.querySelector('main')?.classList.contains('overflow-hidden')).toBe(true);
  });

  it('shows the username and clears session on logout', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    expect(screen.getByText('admin')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /выйти/i }));

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppLayout } from '@/components/AppLayout';
import { loginAs, loginSuperadmin, logout } from '@/test/authTestUtils';
import { useAuthStore } from '@/store/auth';

// AppLayout вызывает useMe() (GET /api/auth/me). Сеть в тесте не нужна — мокаем no-op;
// гейтинг читает принципала из стора (loginAs/loginSuperadmin).
vi.mock('@/features/auth/hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/auth/hooks')>();
  return { ...actual, useMe: () => ({ data: undefined }) };
});

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
        {/* Catch-all: AppLayout (шапка/навигация) монтируется на любом маршруте,
            даже если контент-заглушка не задана (напр. /roles в gating-тестах). */}
        <Route path="*" element={<div>Прочий контент</div>} />
      </Route>
    </Routes>,
    { wrapper },
  );
}

describe('AppLayout — навигация категориями-дропдаунами (ADR-022)', () => {
  afterEach(() => logout());

  describe('супер-админ видит все категории', () => {
    beforeEach(() => loginSuperadmin());

    it('рендерит три категории-триггера: Агрегатор, Мониторинг, Пользователи', () => {
      renderAt('/servers');
      expect(screen.getByRole('button', { name: /Агрегатор/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Мониторинг/ })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Пользователи/ })).toBeInTheDocument();
    });

    it('«Дашборд» отсутствует в меню (нет ни категории, ни пункта)', () => {
      renderAt('/servers');
      expect(screen.queryByText('Дашборд')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Дашборд/ })).not.toBeInTheDocument();
    });

    it('категория «Мониторинг» раскрывает Серверы/ИИ - ключи/Прокси/Бэки', async () => {
      const user = userEvent.setup();
      renderAt('/servers');

      await user.click(screen.getByRole('button', { name: /Мониторинг/ }));

      for (const label of ['Серверы', 'ИИ - ключи', 'Прокси', 'Бэки']) {
        expect(await screen.findByRole('menuitem', { name: label })).toBeInTheDocument();
      }
      // Дашборда нет и внутри раскрытой панели.
      expect(screen.queryByRole('menuitem', { name: 'Дашборд' })).not.toBeInTheDocument();
    });

    it('категория «Агрегатор» раскрывает пункт «Почты»', async () => {
      const user = userEvent.setup();
      renderAt('/servers');

      await user.click(screen.getByRole('button', { name: /Агрегатор/ }));

      expect(await screen.findByRole('menuitem', { name: 'Почты' })).toBeInTheDocument();
    });

    it('категория «Пользователи» раскрывает Пользователи/Роли/Команды', async () => {
      const user = userEvent.setup();
      renderAt('/servers');

      await user.click(screen.getByRole('button', { name: /Пользователи/ }));

      for (const label of ['Пользователи', 'Роли', 'Команды']) {
        expect(await screen.findByRole('menuitem', { name: label })).toBeInTheDocument();
      }
    });

    it('навигация по клику на пункт меню (Серверы → ИИ - ключи)', async () => {
      const user = userEvent.setup();
      renderAt('/servers');

      await user.click(screen.getByRole('button', { name: /Мониторинг/ }));
      await user.click(await screen.findByRole('menuitem', { name: 'ИИ - ключи' }));

      expect(screen.getByText('Контент ключей')).toBeInTheDocument();
    });

    it('активна категория, содержащая текущий маршрут (/servers → «Мониторинг»)', () => {
      renderAt('/servers');
      expect(screen.getByRole('button', { name: /Мониторинг/ }).className).toContain('text-accent');
      expect(screen.getByRole('button', { name: /Агрегатор/ }).className).toContain(
        'text-text-secondary',
      );
    });
  });

  describe('видимость категорий/пунктов по правам (категория видна ⇔ ≥1 доступный пункт)', () => {
    it('только servers:view → видна лишь «Мониторинг» с единственным пунктом «Серверы»', async () => {
      const user = userEvent.setup();
      loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
      renderAt('/servers');

      expect(screen.getByRole('button', { name: /Мониторинг/ })).toBeInTheDocument();
      // Категории без доступных пунктов не рендерятся.
      expect(screen.queryByRole('button', { name: /Агрегатор/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Пользователи/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /Мониторинг/ }));
      expect(await screen.findByRole('menuitem', { name: 'Серверы' })).toBeInTheDocument();
      // Недоступные пункты той же категории скрыты.
      expect(screen.queryByRole('menuitem', { name: 'ИИ - ключи' })).not.toBeInTheDocument();
      expect(screen.queryByRole('menuitem', { name: 'Прокси' })).not.toBeInTheDocument();
      expect(screen.queryByRole('menuitem', { name: 'Бэки' })).not.toBeInTheDocument();
    });

    it('только roles:view → «Пользователи» с единственным пунктом «Роли» (не Пользователи/Команды)', async () => {
      const user = userEvent.setup();
      loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { roles: ['view'] } });
      renderAt('/roles');

      expect(screen.getByRole('button', { name: /Пользователи/ })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Мониторинг/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /Пользователи/ }));
      expect(await screen.findByRole('menuitem', { name: 'Роли' })).toBeInTheDocument();
      // Пункт «Пользователи» — admin-only; «Команды» — teams:view (нет).
      expect(screen.queryByRole('menuitem', { name: 'Пользователи' })).not.toBeInTheDocument();
      expect(screen.queryByRole('menuitem', { name: 'Команды' })).not.toBeInTheDocument();
    });

    it('пункт «Пользователи» виден admin-признаком (role=admin), не матрицей', async () => {
      const user = userEvent.setup();
      loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });
      renderAt('/servers');

      await user.click(screen.getByRole('button', { name: /Пользователи/ }));
      expect(await screen.findByRole('menuitem', { name: 'Пользователи' })).toBeInTheDocument();
    });
  });

  it('показывает имя пользователя и очищает сессию при выходе', async () => {
    const user = userEvent.setup();
    loginSuperadmin();
    renderAt('/servers');

    expect(screen.getByText('admin')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /выйти/i }));

    expect(useAuthStore.getState().isAuthenticated).toBe(false);
  });
});

// Хелпер: shell — <div>-обёртка, непосредственно содержащая <header> и <main>.
function getShell() {
  return document.querySelector('header')?.parentElement ?? null;
}

describe('AppLayout — режимы shell по маршруту (08-design-system.md «Full-bleed layout»)', () => {
  beforeEach(() => loginSuperadmin());
  afterEach(() => logout());

  it('renders /mail full-bleed: <main> w-full/overflow-hidden, Outlet direct, shell h-screen, header shrink-0', () => {
    renderAt('/mail');
    const main = document.querySelector('main');
    expect(main).not.toBeNull();
    expect(main?.classList.contains('w-full')).toBe(true);
    expect(main?.classList.contains('overflow-hidden')).toBe(true);
    expect(main?.classList.contains('overflow-y-auto')).toBe(false);
    expect(main?.classList.contains('max-w-[1400px]')).toBe(false);
    expect(main?.classList.contains('mx-auto')).toBe(false);
    expect(main?.classList.contains('px-6')).toBe(false);
    expect(main?.classList.contains('py-8')).toBe(false);
    const content = screen.getByText('Контент почт');
    expect(content.parentElement?.tagName).toBe('MAIN');
    expect(getShell()?.classList.contains('h-screen')).toBe(true);
    expect(document.querySelector('header')?.classList.contains('shrink-0')).toBe(true);
  });

  it('/servers and /ai-keys: <main> is a plain container — not a scroll/flex/width container', () => {
    const tokens = [
      'overflow-y-auto',
      'flex-1',
      'min-h-0',
      'w-full',
      'max-w-[1400px]',
      'mx-auto',
      'px-6',
      'py-8',
    ];
    const { unmount } = renderAt('/servers');
    const serversMain = document.querySelector('main');
    expect(serversMain).not.toBeNull();
    for (const token of tokens) {
      expect(serversMain?.classList.contains(token)).toBe(false);
    }
    unmount();

    renderAt('/ai-keys');
    const keysMain = document.querySelector('main');
    for (const token of tokens) {
      expect(keysMain?.classList.contains(token)).toBe(false);
    }
  });

  it('/servers and /ai-keys: width held by inner <div> wrapper (content.parentElement is DIV), identical class', () => {
    const { unmount } = renderAt('/servers');
    const serversWrapper = screen.getByText('Контент серверов').parentElement;
    expect(serversWrapper?.tagName).toBe('DIV');
    expect(serversWrapper?.classList.contains('mx-auto')).toBe(true);
    expect(serversWrapper?.classList.contains('max-w-[1400px]')).toBe(true);
    expect(serversWrapper?.classList.contains('px-6')).toBe(true);
    expect(serversWrapper?.classList.contains('py-8')).toBe(true);
    expect(serversWrapper?.parentElement?.tagName).toBe('MAIN');
    const serversWrapperCls = serversWrapper?.className;
    unmount();

    renderAt('/ai-keys');
    const keysWrapper = screen.getByText('Контент ключей').parentElement;
    expect(keysWrapper?.tagName).toBe('DIV');
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
  });

  it('/dashboard (по прямому URL) идёт по не-full-bleed ветке как /servers (ADR-022)', () => {
    renderAt('/dashboard');
    const shell = getShell();
    expect(shell?.classList.contains('min-h-screen')).toBe(true);
    expect(shell?.classList.contains('h-screen')).toBe(false);

    const header = document.querySelector('header');
    expect(header?.classList.contains('sticky')).toBe(true);
    expect(header?.classList.contains('shrink-0')).toBe(false);

    const main = document.querySelector('main');
    expect(main?.classList.contains('w-full')).toBe(false);
    expect(main?.classList.contains('overflow-hidden')).toBe(false);
    const wrapper = screen.getByText('Контент дашборда').parentElement;
    expect(wrapper?.tagName).toBe('DIV');
    expect(wrapper?.classList.contains('max-w-[1400px]')).toBe(true);
    expect(wrapper?.parentElement?.tagName).toBe('MAIN');
  });

  it('regression: non-mail <main> is not a scroll container and /mail <main> stays overflow-hidden', () => {
    const { unmount } = renderAt('/servers');
    expect(document.querySelector('main')?.classList.contains('overflow-y-auto')).toBe(false);
    unmount();

    renderAt('/mail');
    expect(document.querySelector('main')?.classList.contains('overflow-hidden')).toBe(true);
  });
});

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

// Полный нормативный порядок пунктов плоской навигации (ADR-033).
const ALL_LABELS = [
  'Почты',
  'СМС',
  'Серверы',
  'ИИ - ключи',
  'Прокси',
  'Бэки',
  'Пользователи',
  'Роли',
  'Команды',
];

describe('AppLayout — плоская навигация (ADR-033)', () => {
  afterEach(() => {
    logout();
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  describe('супер-админ видит все пункты плоским рядом', () => {
    beforeEach(() => loginSuperadmin());

    it('рендерит все пункты навигации как ссылки (плоский ряд)', () => {
      renderAt('/servers');
      for (const label of ALL_LABELS) {
        expect(screen.getByRole('link', { name: label })).toBeInTheDocument();
      }
    });

    it('нет категорий-дропдаунов / разделителя «|» / пунктов-меню (плоская навигация)', () => {
      renderAt('/servers');
      // Категорий-триггеров прежней навигации (ADR-022) больше нет.
      for (const cat of ['Агрегатор', 'Мониторинг']) {
        expect(screen.queryByRole('button', { name: new RegExp(cat) })).not.toBeInTheDocument();
      }
      // Нет role=menuitem (NavMenu удалён) и нет визуального разделителя «|».
      expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
      expect(screen.queryByText('|')).not.toBeInTheDocument();
    });

    it('«Дашборд» отсутствует в навигации (доступен только по URL)', () => {
      renderAt('/servers');
      expect(screen.queryByRole('link', { name: /Дашборд/ })).not.toBeInTheDocument();
      expect(screen.queryByText('Дашборд')).not.toBeInTheDocument();
    });

    it('активен пункт текущего маршрута (/servers → «Серверы» text-accent)', () => {
      renderAt('/servers');
      expect(screen.getByRole('link', { name: 'Серверы' }).className).toContain('text-accent');
      expect(screen.getByRole('link', { name: 'Почты' }).className).toContain(
        'text-text-secondary',
      );
    });

    it('навигация по клику на пункт (Серверы → ИИ - ключи)', async () => {
      const user = userEvent.setup();
      renderAt('/servers');

      await user.click(screen.getByRole('link', { name: 'ИИ - ключи' }));

      expect(screen.getByText('Контент ключей')).toBeInTheDocument();
    });

    it('в хэдере присутствует переключатель темы (Moon при светлой теме)', () => {
      // data-theme не задан, ОС светлая (matchMedia matches:false) → тема light →
      // иконка Moon, aria-label «Тёмная тема».
      renderAt('/servers');
      expect(screen.getByRole('button', { name: 'Тёмная тема' })).toBeInTheDocument();
    });
  });

  describe('видимость пунктов по правам (гейтинг: скрытый пункт не рендерится)', () => {
    it('только servers:view → виден лишь «Серверы» (users скрыт — не admin)', () => {
      loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
      renderAt('/servers');

      expect(screen.getByRole('link', { name: 'Серверы' })).toBeInTheDocument();
      for (const label of ALL_LABELS.filter((l) => l !== 'Серверы')) {
        expect(screen.queryByRole('link', { name: label })).not.toBeInTheDocument();
      }
    });

    it('только roles:view → виден «Роли»; «Пользователи»(admin) и «Команды»(teams:view) скрыты', () => {
      loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { roles: ['view'] } });
      renderAt('/roles');

      expect(screen.getByRole('link', { name: 'Роли' })).toBeInTheDocument();
      // «Пользователи» — admin-only; «Команды» — teams:view (нет права).
      expect(screen.queryByRole('link', { name: 'Пользователи' })).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'Команды' })).not.toBeInTheDocument();
    });

    it('«Пользователи» виден admin-признаком (role=admin), не матрицей прав', () => {
      loginAs({ isSuperadmin: false, role: 'admin', permissions: {} });
      renderAt('/servers');

      expect(screen.getByRole('link', { name: 'Пользователи' })).toBeInTheDocument();
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

describe('AppLayout — переключатель темы в хэдере (ADR-033)', () => {
  beforeEach(() => {
    loginSuperadmin();
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });
  afterEach(() => {
    logout();
    localStorage.clear();
    document.documentElement.removeAttribute('data-theme');
  });

  it('клик по переключателю меняет data-theme и залипает (повторный клик возвращает)', async () => {
    const user = userEvent.setup();
    renderAt('/servers');

    // Старт: нет сохранённого выбора, ОС светлая → тема light → кнопка «Тёмная тема».
    await user.click(screen.getByRole('button', { name: 'Тёмная тема' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(localStorage.getItem('crm-theme')).toBe('dark');
    // Иконка/лейбл переключились на «Светлая тема».
    expect(screen.getByRole('button', { name: 'Светлая тема' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Светлая тема' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(localStorage.getItem('crm-theme')).toBe('light');
    expect(screen.getByRole('button', { name: 'Тёмная тема' })).toBeInTheDocument();
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

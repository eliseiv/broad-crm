import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailPage } from '@/pages/MailPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { loginAs, loginSuperadmin, logout } from '@/test/authTestUtils';
import type { MailFeedResult } from '@/features/mail/hooks';
import type { MailMessage } from '@/types/api';

const feed = vi.hoisted(() => ({ value: null as unknown }));
// Spy для проверки, что лента НЕ запрашивается за page-level view-guard (ADR-021 §6).
const useMailFeedSpy = vi.hoisted(() => vi.fn());
// Справочники дропдаунов «Почта»/«Команда» (серверные фильтры, ADR-017) — управляемы
// из тестов; по умолчанию отдают по одной опции, чтобы тулбар был полнофункционален.
const mailboxes = vi.hoisted(() => ({
  value: {
    data: {
      mailboxes: [
        {
          id: 7,
          email: 'inbox@postapp.store',
          display_name: 'Входящие',
          group_id: 3,
          is_active: true,
        },
      ],
    },
  } as unknown,
}));
const teams = vi.hoisted(() => ({
  value: { data: { teams: [{ id: 3, name: 'Продажи' }] } } as unknown,
}));

vi.mock('@/features/mail/hooks', () => ({
  useMailFeed: (args: unknown) => {
    useMailFeedSpy(args);
    return feed.value;
  },
  // MailDetail → MailReplyForm использует useReplyMail — мокаем как no-op мутацию.
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
  // Дропдауны серверных фильтров тянут справочники ящиков/команд (ADR-017).
  useMailMailboxes: () => mailboxes.value,
  useMailTeams: () => teams.value,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

// Управляемый IntersectionObserver: захватываем колбэк sentinel-эффекта, чтобы
// детерминированно эмулировать пересечение (догрузку) без реального скролла.
let ioCallback: IntersectionObserverCallback | null = null;
const ioObserve = vi.fn();
const ioDisconnect = vi.fn();

class MockIntersectionObserver {
  constructor(cb: IntersectionObserverCallback) {
    ioCallback = cb;
  }
  observe = ioObserve;
  disconnect = ioDisconnect;
  unobserve = vi.fn();
  takeRecords = vi.fn();
  root = null;
  rootMargin = '';
  thresholds = [];
}

function triggerIntersection(): void {
  act(() => {
    ioCallback?.(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

function makeMessage(id: number, tags: MailMessage['tags'] = []): MailMessage {
  return {
    id,
    subject: `Письмо ${id}`,
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: { id: 3, email: 'inbox@postapp.store', display_name: 'Входящие' },
    body_text: 'тело',
    body_html: null,
    body_present: true,
    body_truncated: false,
    tags,
  };
}

const tag: MailMessage['tags'][number] = { id: 5, name: 'важное', color: '#EF4444' };

function baseFeed(overrides: Partial<MailFeedResult> = {}): MailFeedResult {
  return {
    messages: [],
    phase: 'ready',
    error: null,
    hasMore: false,
    isFetchingMore: false,
    isReloading: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
    ...overrides,
  };
}

describe('MailPage master-detail', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    // Контент почты доступен только с `mail:view` (page-level view-guard, ADR-021 §6).
    // Существующие кейсы контента прогоняем как супер-админ.
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('shows "Сервис почт не настроен" on 503 without toast spam', () => {
    feed.value = baseFeed({
      phase: 'not_configured',
      error: new ApiError(503, 'mail_not_configured', 'not configured'),
    });
    render(<MailPage />);

    expect(screen.getByText('Сервис почт не настроен')).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('shows unavailable message + retry on 502', () => {
    feed.value = baseFeed({
      phase: 'error',
      error: new ApiError(502, 'mail_unavailable', 'unavailable'),
    });
    render(<MailPage />);

    expect(screen.getByText('Почтовый сервис временно недоступен')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Повторить/ })).toBeInTheDocument();
  });

  it('shows empty state when the feed is ready and has no messages', () => {
    feed.value = baseFeed({ phase: 'ready', messages: [] });
    render(<MailPage />);

    // Пустая лента: подпись и в списке (левая панель), и в заглушке детали (правая).
    expect(screen.getAllByText('Писем пока нет')).toHaveLength(2);
  });

  it('auto-selects the newest message (first in desc feed) into the detail panel', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    // Деталь показывает самое свежее письмо (id=2) заголовком темы.
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
    // Inline-reply отрисован под телом (форма ответа доступна).
    expect(screen.getByLabelText('Сообщение')).toBeInTheDocument();
  });

  it('switches the detail when another list item is clicked', async () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    // Клик по элементу списка письма 1 (кликаем по его теме внутри кнопки).
    await userEvent.setup().click(screen.getByText('Письмо 1'));

    expect(screen.getByRole('heading', { name: 'Письмо 1' })).toBeInTheDocument();
  });

  it('does not render a "Загрузить ещё" button (infinite scroll only)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true });
    render(<MailPage />);

    expect(screen.queryByRole('button', { name: 'Загрузить ещё' })).not.toBeInTheDocument();
    expect(screen.queryByText('Загрузить ещё')).not.toBeInTheDocument();
  });

  it('loads older messages when the sentinel intersects the viewport', () => {
    const loadMore = vi.fn();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true, loadMore });
    render(<MailPage />);

    // Эффект подписал IntersectionObserver на sentinel.
    expect(ioObserve).toHaveBeenCalled();
    triggerIntersection();
    expect(loadMore).toHaveBeenCalledTimes(1);
  });

  it('renders the adaptive "Назад" button in the detail panel', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    expect(screen.getByRole('button', { name: 'Назад' })).toBeInTheDocument();
  });
});

describe('MailPage "С тегами" filter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    // Контент почты доступен только с `mail:view` (page-level view-guard, ADR-021 §6).
    // Существующие кейсы контента прогоняем как супер-админ.
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('toggles aria-pressed on the filter button', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const toggle = screen.getByRole('button', { name: /С тегами/ });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');

    await user.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
  });

  it('client-side filters the list to messages with non-empty tags', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [tag]), makeMessage(1)] });
    render(<MailPage />);

    // До фильтра оба письма в списке.
    expect(screen.getByText('Письмо 1')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    // Письмо без тегов (id=1) скрыто; тегированное (id=2) остаётся видимым в детали.
    expect(screen.queryByText('Письмо 1')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
  });

  it('shows the empty-filter notice when no loaded message has tags and nothing more to load', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: false });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    expect(screen.getByText('Нет писем с тегами среди загруженных')).toBeInTheDocument();
  });

  it('keeps the selected message in the detail panel even when the filter hides it from the list', async () => {
    const user = userEvent.setup();
    // id=2 — самое свежее (авто-выбор), без тегов; id=1 — тегированное.
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1, [tag])] });
    render(<MailPage />);

    // Авто-выбор — самое свежее письмо (id=2).
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    // Правая панель сохранила выбор id=2, хотя список скрыл его; в списке теперь id=1.
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
    expect(screen.getByText('Письмо 1')).toBeInTheDocument();
  });

  it('keeps loading older messages while the filter is active and hasMore is true', async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true, loadMore });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    // Финальная заглушка НЕ показывается, пока есть ещё старые письма.
    expect(screen.queryByText('Нет писем с тегами среди загруженных')).not.toBeInTheDocument();
    // Sentinel продолжает догрузку старых батчей даже при активном фильтре.
    triggerIntersection();
    expect(loadMore).toHaveBeenCalled();
  });
});

// Серверные фильтры «Почта»/«Команда» (дропдауны, ADR-017, 08-design-system.md
// «Фильтры ленты»). Тумблер «С тегами» — клиентский; дропдауны — серверные, взаимоисключающи.
describe('MailPage server filters (Почта/Команда dropdowns)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    // Контент почты доступен только с `mail:view` (page-level view-guard, ADR-021 §6).
    // Существующие кейсы контента прогоняем как супер-админ.
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  function getMailboxSelect(): HTMLSelectElement {
    return screen.getByLabelText('Почта') as HTMLSelectElement;
  }
  function getTeamSelect(): HTMLSelectElement {
    return screen.getByLabelText('Команда') as HTMLSelectElement;
  }

  it('renders both server-filter dropdowns with reset-first option', () => {
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    // Первая опция каждого дропдауна — сброс фильтра (08-design-system.md).
    expect(screen.getByRole('option', { name: 'Все почты' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Все команды' })).toBeInTheDocument();
    // Опции справочников: ящик (display_name + email) и команда (name).
    expect(
      screen.getByRole('option', { name: 'Входящие inbox@postapp.store' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Продажи' })).toBeInTheDocument();
  });

  it('shows the filter toolbar even when the server-filtered feed is empty', () => {
    // Пустой результат серверного фильтра — тулбар остаётся, чтобы фильтр можно сбросить.
    feed.value = baseFeed({ phase: 'ready', messages: [] });
    render(<MailPage />);

    expect(screen.getAllByText('Писем пока нет').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /С тегами/ })).toBeInTheDocument();
    expect(getMailboxSelect()).toBeInTheDocument();
    expect(getTeamSelect()).toBeInTheDocument();
  });

  it('selecting a mailbox sets it and resets the team dropdown (mutual exclusion)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    // Сначала выбираем команду.
    await user.selectOptions(getTeamSelect(), '3');
    expect(getTeamSelect().value).toBe('3');

    // Затем выбираем почтовый ящик — команда сбрасывается в «Все команды».
    await user.selectOptions(getMailboxSelect(), '7');
    expect(getMailboxSelect().value).toBe('7');
    expect(getTeamSelect().value).toBe('');
  });

  it('selecting a team sets it and resets the mailbox dropdown (mutual exclusion)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.selectOptions(getMailboxSelect(), '7');
    expect(getMailboxSelect().value).toBe('7');

    await user.selectOptions(getTeamSelect(), '3');
    expect(getTeamSelect().value).toBe('3');
    expect(getMailboxSelect().value).toBe('');
  });

  it('resetting a dropdown to "Все …" clears the server filter', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.selectOptions(getMailboxSelect(), '7');
    expect(getMailboxSelect().value).toBe('7');

    await user.selectOptions(getMailboxSelect(), '');
    expect(getMailboxSelect().value).toBe('');
    expect(getTeamSelect().value).toBe('');
  });
});

// Скрытие полосы прокрутки (08-design-system.md «Скрытие полосы прокрутки», раздел «Где
// применяется» → MAIL — список писем). jsdom НЕ вычисляет computed scrollbar-width — проверяем
// НАЛИЧИЕ класса scrollbar-none и СОХРАНЕНИЕ overflow-класса (прокрутка не отменяется).
describe('MailPage scrollbar hiding (scrollbar-none on the list scroll container)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    // Контент почты доступен только с `mail:view` (page-level view-guard, ADR-021 §6).
    // Существующие кейсы контента прогоняем как супер-админ.
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  // Скролл-контейнер списка — единственный div с overflow-y-auto (у <pre> тела — overflow-auto,
  // у карточки-обёртки — overflow-hidden). Так он однозначно отделяется от прочих scrollbar-none.
  function getListScrollContainer(): HTMLElement | null {
    return document.querySelector<HTMLElement>('.overflow-y-auto');
  }

  it('applies scrollbar-none to the list scroll container', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const list = getListScrollContainer();
    expect(list).not.toBeNull();
    expect(list?.classList.contains('scrollbar-none')).toBe(true);
  });

  it('keeps overflow-y-auto on the list container (scroll preserved, not overflow-hidden)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const list = getListScrollContainer();
    expect(list).not.toBeNull();
    // Прокрутка сохранена: контейнер остаётся overflow-y-auto и НЕ становится overflow-hidden.
    expect(list?.classList.contains('overflow-y-auto')).toBe(true);
    expect(list?.classList.contains('overflow-hidden')).toBe(false);
  });
});

// Page-level view-guard (ADR-021 §6, 08-design-system.md «Page-level view-guard»):
// прямой URL/навигация без `mail:view` → page-scoped заглушка «Недостаточно прав»,
// лента не запрашивается.
describe('MailPage view-guard (mail:view)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('renders the page-scoped stub and does not request the feed without mail:view', () => {
    // Обычный пользователь с доступом к другому разделу, но без `mail:view`.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { servers: ['view'] } });
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    // Page-scoped заглушка (не «нет ни одного раздела»), ADR-021 §6.
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Лента не запрашивается — useMailFeed не вызывается за guard'ом.
    expect(useMailFeedSpy).not.toHaveBeenCalled();
    // Тулбар фильтров и master-detail скрыты (контента нет).
    expect(screen.queryByRole('button', { name: /С тегами/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Почта')).not.toBeInTheDocument();
  });

  it('renders the mail content for a user holding mail:view', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    // Guard пропускает — лента запрашивается, контент виден.
    expect(useMailFeedSpy).toHaveBeenCalled();
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
    expect(screen.queryByText(INSUFFICIENT_PERMISSIONS_TITLE)).not.toBeInTheDocument();
  });
});

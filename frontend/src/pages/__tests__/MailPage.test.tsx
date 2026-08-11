import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailPage } from '@/pages/MailPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { loginAs, logout } from '@/test/authTestUtils';

// Команды канала «Почты» из `/api/auth/me` (ADR-055 §5.1) — ЕДИНСТВЕННЫЙ источник опций
// фильтра. У admin-уровня `includes_unassigned = true` ⇒ 1 команда + «Без команды» = 2
// варианта ⇒ порог §6.2 выполнен и фильтр рендерится.
const MAIL_TEAMS = [{ id: 'team-3', name: 'Продажи' }];

/** Супер-админ с непустым scope канала (фильтр «Команда» рендерится). */
function loginSuperadmin(): void {
  loginAs({ isSuperadmin: true, mailTeams: MAIL_TEAMS });
}
import type { MailFeedResult } from '@/features/mail/hooks';
import type { MailMessage } from '@/types/api';

const feed = vi.hoisted(() => ({ value: null as unknown }));
// Spy для проверки, что лента НЕ запрашивается за page-level view-guard (ADR-021 §6).
const mailFeedSpy = vi.hoisted(() => vi.fn());
// Справочники фильтров «Почта» (`ui/Combobox`, ADR-052 §2) / «Команда» (`ui/Select`) —
// управляемы из тестов. Два ящика: у второго заполнены `number`/`app_name` — на нём
// проверяется фильтрация ВЫПАДАЮЩЕГО СПИСКА вводом (лента при этом не трогается).
const mailboxes = vi.hoisted(() => ({
  value: {
    data: {
      mailboxes: [
        {
          id: 7,
          email: 'inbox@postapp.store',
          number: null,
          app_name: null,
          display_name: 'Входящие',
          team_id: 'team-3',
          is_active: true,
          last_synced_at: null,
          last_sync_error: null,
          consecutive_failures: 0,
        },
        {
          id: 9,
          email: 'beta@postapp.store',
          number: '7011',
          app_name: 'Nova Ledger',
          display_name: '7011 Nova Ledger',
          team_id: null,
          is_active: true,
          last_synced_at: null,
          last_sync_error: null,
          consecutive_failures: 0,
        },
      ],
    },
    isLoading: false,
  } as unknown,
}));
// `GET /api/teams` БОЛЬШЕ НЕ ИСТОЧНИК опций фильтра «Команда» (ADR-055 §6.2/§6.3): команды
// канала приходят из `GET /api/auth/me` (`me.mail_teams`), а `GET /api/teams` гейтится
// `teams:view` — у mail-оператора его нет. Спай обязан остаться НЕВЫЗВАННЫМ.
const teamsSpy = vi.hoisted(() => vi.fn(() => ({ data: { items: [] } })));

// Мутации личной прочитанности (ADR-050 §2.6/§2.7): спаим ФАКТ и АРГУМЕНТ вызова —
// `POST …/read` обязан уходить РОВНО ОДИН раз на СМЕНУ письма (не на каждый рендер),
// `DELETE …/read` — по кнопке «Отметить непрочитанным».
const markReadSpy = vi.hoisted(() => vi.fn());
const unmarkReadSpy = vi.hoisted(() => vi.fn());
const batchReadSpy = vi.hoisted(() => vi.fn());
const sentFeed = vi.hoisted(() => ({ value: null as unknown }));
const mailSentFeedSpy = vi.hoisted(() => vi.fn());

vi.mock('@/features/mail/hooks', () => ({
  useMailFeed: (args: unknown, enabled?: boolean) => {
    if (enabled !== false) mailFeedSpy(args);
    return feed.value;
  },
  useMailSentFeed: (args: unknown, enabled?: boolean) => {
    if (enabled !== false) mailSentFeedSpy(args);
    return sentFeed.value ?? feed.value;
  },
  useMailTags: () => ({
    data: { tags: [{ id: 'tag-1', name: 'важное', color: '#EF4444', rules: [] }] },
    isLoading: false,
  }),
  useMailUnreadCount: () => ({ data: { count: 5 } }),
  useBatchMarkMailRead: () => ({ mutate: batchReadSpy, isPending: false }),
  useBatchArchiveMail: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchDeleteMail: () => ({ mutate: vi.fn(), isPending: false }),
  useBatchRestoreMail: () => ({ mutate: vi.fn(), isPending: false }),
  // MailDetail → MailReplyForm использует useReplyMail — мокаем как no-op мутацию.
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
  useComposeMail: () => ({ mutate: vi.fn(), isPending: false }),
  // Дропдаун «Почта» тянет справочник ящиков.
  useMailMailboxes: () => mailboxes.value,
  // Шапка вкладок рендерит MailNotificationsToggle → useMailSettings/useUpdateMailSettings.
  useMailSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  useUpdateMailSettings: () => ({ mutate: vi.fn(), isPending: false }),
  useMarkMailRead: () => ({ mutate: markReadSpy, isPending: false }),
  useUnmarkMailRead: () => ({ mutate: unmarkReadSpy, isPending: false }),
}));

vi.mock('@/features/teams/hooks', () => ({ useTeams: teamsSpy }));

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

function makeMessage(id: number, tags: MailMessage['tags'] = [], isUnread = false): MailMessage {
  return {
    id,
    subject: `Письмо ${id}`,
    internal_date: '2026-07-02T09:15:00Z',
    from_addr: 'sender@example.com',
    from_name: 'Иван',
    to_addrs: 'inbox@postapp.store',
    cc_addrs: null,
    mail_account: {
      id: 3,
      email: 'inbox@postapp.store',
      display_name: 'Входящие',
      number: '5108',
      app_name: 'Klyro Forge',
      team: { id: 'team-1', name: 'Команда Ивана' },
    },
    body_text: 'тело',
    body_html: null,
    body_present: true,
    body_truncated: false,
    // Персональный признак непрочитанности (ADR-050 §2.2) — обязательное поле контракта.
    is_unread: isUnread,
    tags,
  };
}

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

    expect(screen.getByText('Писем пока нет')).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('does not open detail until a list item is clicked', () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    expect(screen.queryByRole('heading', { name: 'Письмо 2' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('opens detail when a list item is clicked', async () => {
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    await userEvent.setup().click(screen.getByText('Письмо 2'));

    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
    expect(screen.getByLabelText('Сообщение')).toBeInTheDocument();
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

  it('renders the "Назад" button after opening a message', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    await user.click(screen.getByText('Письмо 2'));

    expect(screen.getByRole('button', { name: 'Назад' })).toBeInTheDocument();
  });
});

describe('MailPage "С тегами" navigation (ADR-071)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('клик «С тегами» в сайдбаре шлёт серверный has_tags=true', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    expect(mailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ hasTags: true, folder: 'inbox' }),
    );
  });

  it('повторный клик «С тегами» сбрасывает фильтр has_tags', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const taggedBtn = screen.getByRole('button', { name: /С тегами/ });
    await user.click(taggedBtn);
    await user.click(taggedBtn);

    expect(mailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ hasTags: undefined, folder: 'inbox' }),
    );
  });

  it('выбор ящика в «Почта» шлёт mailAccountId в ленту (ADR-052 §2)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const combobox = screen.getByRole('combobox', { name: 'Почта' });
    await user.click(combobox);
    await user.click(screen.getByRole('option', { name: /7011 Nova Ledger beta@postapp.store/ }));

    expect(mailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: 9, folder: 'inbox' }),
    );
  });

  it('bulk «Прочитано» вызывает batch read для выбранных писем', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    render(<MailPage />);

    await user.click(screen.getByRole('checkbox', { name: /Выбрать письмо 2/ }));
    await user.click(screen.getByRole('button', { name: /Прочитано/ }));

    expect(batchReadSpy).toHaveBeenCalledWith([2], expect.anything());
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
    return document.querySelector<HTMLElement>('.scrollbar-none.overflow-y-auto');
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
    expect(mailFeedSpy).not.toHaveBeenCalled();
    // Тулбар фильтров и master-detail скрыты (контента нет).
    expect(screen.queryByRole('button', { name: /С тегами/ })).not.toBeInTheDocument();
  });

  it('renders the mail content for a user holding mail:view', () => {
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    expect(mailFeedSpy).toHaveBeenCalled();
    expect(screen.queryByRole('heading', { name: 'Письмо 2' })).not.toBeInTheDocument();
    expect(screen.queryByText(INSUFFICIENT_PERMISSIONS_TITLE)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Входящие/ })).toBeInTheDocument();
    expect(screen.queryByText('Команды')).not.toBeInTheDocument();
  });
});

// --- Личная прочитанность писем (ADR-050 §2) ---------------------------------
//
// Прогоняем под ОБЫЧНЫМ пользователем (`mail:view`, НЕ супер-админ из `.env`): личное
// состояние прочитанности есть только у БД-пользователя (§2.5).
describe('MailPage — пометка «прочитано» при открытии (ADR-050 §2.6)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('открытие письма кликом шлёт РОВНО ОДИН POST …/read', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({
      messages: [makeMessage(2, [], true), makeMessage(1, [], true)],
    });
    render(<MailPage />);

    expect(markReadSpy).not.toHaveBeenCalled();

    await user.click(screen.getByText('Письмо 2'));

    expect(markReadSpy).toHaveBeenCalledTimes(1);
    expect(markReadSpy).toHaveBeenCalledWith(2);
  });

  it('повторные рендеры при неизменном выбранном письме POST повторно НЕ шлют (триггер = смена письма)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    const { rerender } = render(<MailPage />);
    await user.click(screen.getByText('Письмо 2'));
    expect(markReadSpy).toHaveBeenCalledTimes(1);

    rerender(<MailPage />);
    rerender(<MailPage />);

    expect(markReadSpy).toHaveBeenCalledTimes(1);
  });

  it('смена выбранного письма кликом шлёт ровно один POST на НОВОЕ письмо', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    render(<MailPage />);
    await user.click(screen.getByText('Письмо 2'));
    expect(markReadSpy).toHaveBeenCalledTimes(1);

    await user.click(screen.getByText('Письмо 1'));

    expect(markReadSpy).toHaveBeenCalledTimes(2);
    expect(markReadSpy).toHaveBeenLastCalledWith(1);
  });

  it('непрочитанное письмо в списке несёт sr-only «Непрочитано» (не только цвет/вес, a11y)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], false)] });
    render(<MailPage />);

    // Ровно одно непрочитанное письмо в списке.
    expect(screen.getAllByText('Непрочитано')).toHaveLength(1);
  });
});

describe('MailPage — откат «Отметить непрочитанным» (ADR-050 §2.7)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('кнопка рендерится ТОЛЬКО когда письмо уже прочитано (is_unread === false)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    const { rerender } = render(<MailPage />);

    await user.click(screen.getByText('Письмо 2'));

    expect(
      screen.queryByRole('button', { name: /Отметить непрочитанным/ }),
    ).not.toBeInTheDocument();

    feed.value = baseFeed({ messages: [makeMessage(2, [], false)] });
    rerender(<MailPage />);

    expect(screen.getByRole('button', { name: /Отметить непрочитанным/ })).toBeInTheDocument();
  });

  it('клик шлёт DELETE …/read, НЕ закрывает деталь и НЕ ретриггерит авто-пометку', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], false), makeMessage(1, [], false)] });
    const { rerender } = render(<MailPage />);

    await user.click(screen.getByText('Письмо 2'));
    expect(markReadSpy).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: /Отметить непрочитанным/ }));

    expect(unmarkReadSpy).toHaveBeenCalledTimes(1);
    expect(unmarkReadSpy).toHaveBeenCalledWith(2);
    // Деталь осталась открытой (письмо не «схлопнулось»).
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    // Кэш ленты обновился (is_unread=true), письмо ОСТАЛОСЬ выбранным: авто-пометка повторно
    // не срабатывает — её триггер — СМЕНА письма, а не рендер (иначе откат затирался бы).
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], false)] });
    rerender(<MailPage />);
    expect(markReadSpy).toHaveBeenCalledTimes(1);
  });
});


// ADR-051 §2/§3 ОТМЕНИЛ норму ADR-050 §2.5 («у супер-админа личного состояния нет»).
// Теперь супер-админ из `.env` — полноценный субъект личной прочитанности: его идентичность
// это системная строка-якорь в `users` (ADR-051 §1.1), поэтому контролы прочитанности
// рендерятся ему БЕЗУСЛОВНО, наравне с любым `mail:view` (гейта по `me.is_superadmin` в
// прочитанности больше нет). Что НЕ изменилось — контрол «Уведомления» (см. последний кейс).
describe('MailPage — супер-админ имеет ПОЛНОЕ личное состояние прочитанности (ADR-051 §3)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    loginSuperadmin();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    logout();
  });

  it('контролы прочитанности рендерятся: индикатор и кнопка отката', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], false), makeMessage(1, [], true)] });
    render(<MailPage />);

    expect(screen.getByText('Непрочитано')).toBeInTheDocument();

    await user.click(screen.getByText('Письмо 2'));

    expect(screen.getByRole('button', { name: /Отметить непрочитанным/ })).toBeInTheDocument();
  });

  it('пометка при открытии письма ВЫЗЫВАЕТСЯ (была запрещена §2.5)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    render(<MailPage />);

    expect(markReadSpy).not.toHaveBeenCalled();

    await user.click(screen.getByText('Письмо 2'));
    expect(markReadSpy).toHaveBeenLastCalledWith(2);

    await user.click(screen.getByText('Письмо 1'));

    expect(markReadSpy).toHaveBeenLastCalledWith(1);
  });

  it('откат в «непрочитано» доступен: клик по кнопке шлёт DELETE …/read', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(1, [], false)] });
    render(<MailPage />);

    await user.click(screen.getByText('Письмо 1'));
    await user.click(screen.getByRole('button', { name: /Отметить непрочитанным/ }));

    expect(unmarkReadSpy).toHaveBeenLastCalledWith(1);
  });

  it('РЕГРЕСС: контрол «Уведомления» супер-админу по-прежнему НЕ рендерится (ADR-051 §1.6)', () => {
    feed.value = baseFeed({ messages: [makeMessage(1, [], true)] });
    render(<MailPage />);

    expect(screen.queryByRole('button', { name: /Уведомления/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Прочитано/ })).toBeInTheDocument();
  });
});

// Экран 1 из пяти: `/mail` — блок «Команды» в сайдбаре, порог 2 (ADR-055 §6.2)
describe('MailPage — блок «Команды» в сайдбаре: порог 2 (ADR-055 §6.2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    feed.value = baseFeed();
  });

  afterEach(() => logout());

  const SUPPORT = { id: 'team-9', name: 'Поддержка' };

  it('0 вариантов канала → блока «Команды» НЕТ', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'] },
      mailTeams: [],
      mailIncludesUnassigned: false,
    });
    render(<MailPage />);

    expect(screen.queryByText('Команды')).not.toBeInTheDocument();
  });

  it('1 вариант (одна команда, без «Без команды») → блока НЕТ', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'] },
      mailTeams: MAIL_TEAMS,
      mailIncludesUnassigned: false,
    });
    render(<MailPage />);

    expect(screen.queryByText('Команды')).not.toBeInTheDocument();
  });

  it('2 команды у НЕ-АДМИНА → блок ЕСТЬ', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'] },
      mailTeams: [...MAIL_TEAMS, SUPPORT],
      mailIncludesUnassigned: false,
    });
    render(<MailPage />);

    expect(screen.getByText('Команды')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Все команды' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продажи' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Поддержка' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Без команды' })).not.toBeInTheDocument();
  });

  it('1 команда + «Без команды» = 2 варианта → блок ЕСТЬ', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'] },
      mailTeams: MAIL_TEAMS,
      mailIncludesUnassigned: true,
    });
    render(<MailPage />);

    expect(screen.getByText('Команды')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Все команды' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Продажи' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Без команды' })).toBeInTheDocument();
  });

  it('admin-уровень с 1 вариантом → блока НЕТ', () => {
    loginAs({ isSuperadmin: true, mailTeams: [], mailIncludesUnassigned: true });
    render(<MailPage />);

    expect(screen.queryByText('Команды')).not.toBeInTheDocument();
  });

  it('опции — из `/api/auth/me`; `GET /api/teams` не вызывается (§6.3)', () => {
    loginAs({ isSuperadmin: true, mailTeams: [...MAIL_TEAMS, SUPPORT] });
    render(<MailPage />);

    expect(screen.getByText('Команды')).toBeInTheDocument();
    expect(teamsSpy).not.toHaveBeenCalled();
  });

  it('выбор «Без команды» → серверный `no_team=true` (§5.3)', async () => {
    const user = userEvent.setup();
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      permissions: { mail: ['view'] },
      mailTeams: MAIL_TEAMS,
      mailIncludesUnassigned: true,
    });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: 'Без команды' }));

    const args = mailFeedSpy.mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(args.noTeam).toBe(true);
    expect(args.teamId).toBeUndefined();
  });

  it('кнопка «Написать» открывает модалку нового письма', async () => {
    const user = userEvent.setup();
    loginSuperadmin();
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: 'Написать новое письмо' }));

    expect(screen.getByRole('dialog', { name: 'Новое письмо' })).toBeInTheDocument();
    expect(screen.getByLabelText('Кому')).toBeInTheDocument();
  });
});

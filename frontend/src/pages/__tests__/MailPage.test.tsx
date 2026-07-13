import { render, screen, act, within } from '@testing-library/react';
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
// Справочник CRM-команд (GET /api/teams) — источник дропдауна «Команда» (ADR-044 §7):
// групп агрегатора больше нет, фильтр по команде идёт по UUID CRM-команды.
const teams = vi.hoisted(() => ({
  value: { data: { items: [{ id: 'team-3', name: 'Продажи' }] } } as unknown,
}));

// Мутации личной прочитанности (ADR-050 §2.6/§2.7): спаим ФАКТ и АРГУМЕНТ вызова —
// `POST …/read` обязан уходить РОВНО ОДИН раз на СМЕНУ письма (не на каждый рендер),
// `DELETE …/read` — по кнопке «Отметить непрочитанным».
const markReadSpy = vi.hoisted(() => vi.fn());
const unmarkReadSpy = vi.hoisted(() => vi.fn());

vi.mock('@/features/mail/hooks', () => ({
  useMailFeed: (args: unknown) => {
    useMailFeedSpy(args);
    return feed.value;
  },
  // MailDetail → MailReplyForm использует useReplyMail — мокаем как no-op мутацию.
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
  // Дропдаун «Почта» тянет справочник ящиков.
  useMailMailboxes: () => mailboxes.value,
  // Шапка вкладок рендерит MailNotificationsToggle → useMailSettings/useUpdateMailSettings.
  useMailSettings: () => ({ data: undefined, isLoading: false, isError: false }),
  useUpdateMailSettings: () => ({ mutate: vi.fn(), isPending: false }),
  useMarkMailRead: () => ({ mutate: markReadSpy, isPending: false }),
  useUnmarkMailRead: () => ({ mutate: unmarkReadSpy, isPending: false }),
}));

// Дропдаун «Команда» тянет CRM-команды через feature teams (ADR-044 §7).
vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => teams.value,
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

function makeMessage(id: number, tags: MailMessage['tags'] = [], isUnread = false): MailMessage {
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
    // Персональный признак непрочитанности (ADR-050 §2.2) — обязательное поле контракта.
    is_unread: isUnread,
    tags,
  };
}

const tag: MailMessage['tags'][number] = {
  id: '5a1f0c2e-0000-4000-8000-000000000005',
  name: 'важное',
  color: '#EF4444',
};

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

  it('re-selects the first VISIBLE message when the filter hides the current selection (ADR-044 §7)', async () => {
    const user = userEvent.setup();
    // id=2 — самое свежее (авто-выбор), без тегов; id=1 — тегированное.
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1, [tag])] });
    render(<MailPage />);

    // Авто-выбор — самое свежее письмо (id=2).
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /С тегами/ }));

    // Фильтр скрыл id=2 из видимого списка → в detail НЕ должно остаться письмо без тегов,
    // отсутствующее в ленте: авто-выбор переезжает на первый ВИДИМЫЙ (id=1).
    expect(screen.queryByRole('heading', { name: 'Письмо 2' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Письмо 1' })).toBeInTheDocument();
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

// Серверные фильтры ленты: «Почта» — `ui/Combobox` `mode='select'` (ADR-052 §2; норма
// «`ui/Select`» из ADR-044 §7 / 08 §Дропдауны ОТМЕНЕНА), «Команда» — по-прежнему `ui/Select`.
// Семантика фильтра НЕ менялась: выбор → серверный `mail_account_id` в запросе ленты;
// фильтры комбинируемы (AND, ADR-038): выбор одного не сбрасывает другой. ВВОД текста ленту
// НЕ фильтрует — только выпадающий список (§2).
describe('MailPage server filters («Почта» combobox + «Команда» select)', () => {
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

  function getMailboxCombobox(): HTMLInputElement {
    return screen.getByRole('combobox', { name: 'Почта' }) as HTMLInputElement;
  }
  function getTeamSelect(): HTMLSelectElement {
    return screen.getByLabelText('Команда') as HTMLSelectElement;
  }
  function mailboxOptionLabels(): string[] {
    return within(screen.getByRole('listbox'))
      .getAllByRole('option')
      .map((o) => o.textContent ?? '');
  }
  /** Выбор почты: открыть панель → клик по опции (клик по опции ≡ Enter на ней). */
  async function pickMailbox(user: ReturnType<typeof userEvent.setup>, label: string) {
    await user.click(getMailboxCombobox());
    await user.click(within(screen.getByRole('listbox')).getByRole('option', { name: label }));
  }

  it('при монтировании «нет фильтра» = ОДНО состояние: текст «Все почты», `X` не отрисован (§1.1а)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    // Плейсхолдера на этой вкладке нет — поле никогда не пусто (в нём лейбл опции сброса).
    expect(getMailboxCombobox().value).toBe('Все почты');
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: undefined }),
    );
  });

  it('открытие показывает ВСЕ опции: `pinned` «Все почты» первой + ящики справочника', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.click(getMailboxCombobox());

    expect(mailboxOptionLabels()).toEqual([
      'Все почты',
      'Входящие inbox@postapp.store',
      '7011 Nova Ledger beta@postapp.store',
    ]);
    // Фильтр «Команда» остался нативным `ui/Select` со своим сбросом первой опцией.
    expect(Array.from(getTeamSelect().options).map((o) => o.textContent)).toEqual([
      'Все команды',
      'Продажи',
    ]);
  });

  it('ВВОД фильтрует ТОЛЬКО выпадающий список — серверный запрос ленты не меняется (§2)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    const before = useMailFeedSpy.mock.calls.length;
    // Поле не пусто (в нём лейбл опции сброса) — пользователь стирает его и печатает запрос.
    await user.clear(getMailboxCombobox());
    await user.type(getMailboxCombobox(), 'nova'); // ищет по `app_name` (единый предикат §3.3)

    // Список сузился (плюс всегда видимая `pinned`-опция сброса).
    expect(mailboxOptionLabels()).toEqual(['Все почты', '7011 Nova Ledger beta@postapp.store']);
    // Лента НЕ перезапрашивалась с новым фильтром: `mail_account_id` не менялся.
    expect(useMailFeedSpy.mock.calls.length).toBeGreaterThanOrEqual(before);
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: undefined }),
    );
  });

  it('ВЫБОР опции меняет серверный `mail_account_id` (семантика фильтра не изменилась)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await pickMailbox(user, 'Входящие inbox@postapp.store');

    expect(useMailFeedSpy).toHaveBeenLastCalledWith(expect.objectContaining({ mailAccountId: 7 }));
    expect(getMailboxCombobox().value).toBe('Входящие inbox@postapp.store');
    // Выбрана не-`pinned` опция ⇒ есть что сбрасывать ⇒ `X` отрисован (§1.1а).
    expect(screen.getByRole('button', { name: 'Очистить' })).toBeInTheDocument();
  });

  it('нет совпадений: в панели видны И «Все почты», И строка «Ничего не найдено» (§2)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.clear(getMailboxCombobox());
    await user.type(getMailboxCombobox(), 'zzz-nomatch');

    // `pinned`-опция сброса в предикате не участвует ⇒ видна ВМЕСТЕ со строкой «Ничего не найдено».
    expect(mailboxOptionLabels()).toEqual(['Все почты']);
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
  });

  it('`dirty` сбрасывается на закрытии: напечатал → закрыл без выбора → текст «Все почты», `X` нет (§1.1)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.clear(getMailboxCombobox());
    await user.type(getMailboxCombobox(), 'nova');
    // Поле `dirty` ⇒ есть что сбрасывать ⇒ `X` отрисован (пока панель открыта).
    expect(screen.getByRole('button', { name: 'Очистить' })).toBeInTheDocument();

    await user.keyboard('{Escape}'); // закрытие панели без выбора

    expect(getMailboxCombobox().value).toBe('Все почты');
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: undefined }),
    );
  });

  it('shows the filter toolbar even when the server-filtered feed is empty', () => {
    // Пустой результат серверного фильтра — тулбар остаётся, чтобы фильтр можно сбросить.
    feed.value = baseFeed({ phase: 'ready', messages: [] });
    render(<MailPage />);

    expect(screen.getAllByText('Писем пока нет').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /С тегами/ })).toBeInTheDocument();
    expect(getMailboxCombobox()).toBeInTheDocument();
    expect(getTeamSelect()).toBeInTheDocument();
  });

  it('selecting a mailbox after a team keeps both (AND-combinable, ADR-038)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    // Сначала выбираем команду.
    await user.selectOptions(getTeamSelect(), 'team-3');
    expect(getTeamSelect().value).toBe('team-3');

    // Затем выбираем ящик — команда НЕ сбрасывается (фильтры комбинируемы, AND).
    await pickMailbox(user, 'Входящие inbox@postapp.store');
    expect(getTeamSelect().value).toBe('team-3');
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: 7, teamId: 'team-3' }),
    );
  });

  it('selecting a team after a mailbox keeps both (AND-combinable, ADR-038)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await pickMailbox(user, 'Входящие inbox@postapp.store');
    expect(getMailboxCombobox().value).toBe('Входящие inbox@postapp.store');

    // Команда добавляется к уже выбранному ящику — ящик остаётся (и в поле, и в запросе).
    await user.selectOptions(getTeamSelect(), 'team-3');
    expect(getMailboxCombobox().value).toBe('Входящие inbox@postapp.store');
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: 7, teamId: 'team-3' }),
    );
  });

  it('выбор «Все почты» сбрасывает серверный фильтр; фильтр «Команда» при этом НЕ сбрасывается', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    render(<MailPage />);

    await user.selectOptions(getTeamSelect(), 'team-3');
    await pickMailbox(user, 'Входящие inbox@postapp.store');
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(expect.objectContaining({ mailAccountId: 7 }));

    await pickMailbox(user, 'Все почты');

    expect(getMailboxCombobox().value).toBe('Все почты');
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ mailAccountId: undefined, teamId: 'team-3' }),
    );
    expect(getTeamSelect().value).toBe('team-3');
  });

  it('`X` / Escape ≡ выбор «Все почты»: идентичное состояние поля и ленты (§1.1а)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2)] });
    const { unmount } = render(<MailPage />);

    // Ветка 1 — очистка кнопкой `X`.
    await pickMailbox(user, 'Входящие inbox@postapp.store');
    await user.click(screen.getByRole('button', { name: 'Очистить' }));
    const afterClear = {
      text: getMailboxCombobox().value,
      feedArgs: useMailFeedSpy.mock.lastCall?.[0],
      hasClear: screen.queryByRole('button', { name: 'Очистить' }) !== null,
    };
    unmount();

    // Ветка 2 — явный выбор опции сброса.
    render(<MailPage />);
    await pickMailbox(user, 'Входящие inbox@postapp.store');
    await pickMailbox(user, 'Все почты');

    expect(afterClear.text).toBe('Все почты');
    expect(afterClear.hasClear).toBe(false);
    expect(getMailboxCombobox().value).toBe(afterClear.text);
    expect(useMailFeedSpy.mock.lastCall?.[0]).toEqual(afterClear.feedArgs);
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
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
    // Дропдаун «Почта» доступен под mail:view; «Команда» — только admin-уровню
    // (`sees_all_mail_teams`, ADR-038 §3): у роли mail:view он скрыт (анти-энумерация).
    expect(screen.getByLabelText('Почта')).toBeInTheDocument();
    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
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

  it('авто-выбор самого свежего письма шлёт РОВНО ОДИН POST …/read (тело отрендерено ⇒ открыто)', () => {
    feed.value = baseFeed({
      messages: [makeMessage(2, [], true), makeMessage(1, [], true)],
    });
    render(<MailPage />);

    // Авто-выбранное свежее письмо тоже помечается прочитанным (нормативно, §2.6).
    expect(markReadSpy).toHaveBeenCalledTimes(1);
    expect(markReadSpy).toHaveBeenCalledWith(2);
  });

  it('повторные рендеры при неизменном выбранном письме POST повторно НЕ шлют (триггер = смена письма)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    const { rerender } = render(<MailPage />);
    expect(markReadSpy).toHaveBeenCalledTimes(1);

    // Ре-рендер (напр. ре-фетч ленты) при том же selectedId — нового запроса нет.
    rerender(<MailPage />);
    rerender(<MailPage />);

    expect(markReadSpy).toHaveBeenCalledTimes(1);
  });

  it('смена выбранного письма кликом шлёт ровно один POST на НОВОЕ письмо', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    render(<MailPage />);
    expect(markReadSpy).toHaveBeenCalledTimes(1); // авто-выбор id=2

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

  it('кнопка рендерится ТОЛЬКО когда письмо уже прочитано (is_unread === false)', () => {
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    const { rerender } = render(<MailPage />);

    // Открытое письмо ещё числится непрочитанным → кнопки отката нет.
    expect(
      screen.queryByRole('button', { name: /Отметить непрочитанным/ }),
    ).not.toBeInTheDocument();

    // После успешного 204 кэш ленты правится точечно: is_unread=false → кнопка появляется.
    feed.value = baseFeed({ messages: [makeMessage(2, [], false)] });
    rerender(<MailPage />);

    expect(screen.getByRole('button', { name: /Отметить непрочитанным/ })).toBeInTheDocument();
  });

  it('клик шлёт DELETE …/read, НЕ закрывает деталь и НЕ ретриггерит авто-пометку', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], false), makeMessage(1, [], false)] });
    render(<MailPage />);
    expect(markReadSpy).toHaveBeenCalledTimes(1); // авто-пометка при открытии

    await user.click(screen.getByRole('button', { name: /Отметить непрочитанным/ }));

    expect(unmarkReadSpy).toHaveBeenCalledTimes(1);
    expect(unmarkReadSpy).toHaveBeenCalledWith(2);
    // Деталь осталась открытой (письмо не «схлопнулось»).
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    // Кэш ленты обновился (is_unread=true), письмо ОСТАЛОСЬ выбранным: авто-пометка повторно
    // не срабатывает — её триггер — СМЕНА письма, а не рендер (иначе откат затирался бы).
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], false)] });
    render(<MailPage />);
    expect(markReadSpy).toHaveBeenCalledTimes(2); // ровно +1 за новый монтаж, не больше
  });
});

describe('MailPage — серверный фильтр «Непрочитанные» (ADR-050 §2.8)', () => {
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

  it('тумблер уходит в ЗАПРОС ленты (unread=true) — фильтрация серверная, не клиентская', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    render(<MailPage />);

    const toggle = screen.getByRole('button', { name: /Непрочитанные/ });
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(expect.objectContaining({ unread: false }));

    await user.click(toggle);

    expect(screen.getByRole('button', { name: /Непрочитанные/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(expect.objectContaining({ unread: true }));
  });

  it('AND-комбинируется с дропдаунами «Почта»/«Команда» (ни один не сбрасывает другой)', async () => {
    const user = userEvent.setup();
    loginAs({
      isSuperadmin: false,
      role: 'Менеджер',
      seesAllMailTeams: true,
      permissions: { mail: ['view'], teams: ['view'] },
    });
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Непрочитанные/ }));
    // «Почта» — `ui/Combobox` (ADR-052 §2): выбор = открыть панель + клик по опции.
    const mailbox = screen.getByRole('combobox', { name: 'Почта' });
    await user.click(mailbox);
    await user.click(
      within(screen.getByRole('listbox')).getByRole('option', {
        name: 'Входящие inbox@postapp.store',
      }),
    );
    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');

    // Все три фильтра уходят в ОДИН запрос ленты — ни один не сбросил другие.
    expect(useMailFeedSpy).toHaveBeenLastCalledWith({
      mailAccountId: 7,
      teamId: 'team-3',
      unread: true,
    });
  });

  it('открытое письмо при активном фильтре ОСТАЁТСЯ в списке (ленту не инвалидируем)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    const { rerender } = render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Непрочитанные/ }));
    await user.click(screen.getByText('Письмо 1'));
    expect(markReadSpy).toHaveBeenLastCalledWith(1);

    // Точечная правка кэша ленты (is_unread=false) вместо инвалидэйта — набор писем тот же.
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], false)] });
    rerender(<MailPage />);

    // Строка НЕ исчезает из-под курсора (в списке — элемент, в детали — заголовок), а
    // индикатор непрочитанного гаснет только у неё (§2.8).
    const listItem = screen
      .getAllByRole('button')
      .find((el) => el.getAttribute('aria-current') === 'true');
    expect(listItem?.textContent).toContain('Письмо 1');
    expect(screen.getByRole('heading', { name: 'Письмо 1' })).toBeInTheDocument();
    expect(screen.getAllByText('Непрочитано')).toHaveLength(1);
  });

  it('пустой результат фильтра → нормативная строка «Непрочитанных писем нет»', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    const { rerender } = render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Непрочитанные/ }));
    feed.value = baseFeed({ messages: [] });
    rerender(<MailPage />);

    // И в списке, и в заглушке детали — своя строка (не «Писем пока нет»).
    expect(screen.getAllByText('Непрочитанных писем нет')).toHaveLength(2);
    expect(screen.queryByText('Писем пока нет')).not.toBeInTheDocument();
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

  it('контролы прочитанности рендерятся: фильтр, индикатор, кнопка отката', () => {
    // Авто-выбирается ПЕРВОЕ письмо (id=2) — делаем его прочитанным, чтобы в шапке детали
    // была кнопка отката; второе (id=1) оставляем непрочитанным — для индикатора в списке.
    feed.value = baseFeed({ messages: [makeMessage(2, [], false), makeMessage(1, [], true)] });
    render(<MailPage />);

    // Разворот §2.5: фильтр-тумблер и индикатор непрочитанного супер-админу ВИДНЫ.
    expect(screen.getByRole('button', { name: /Непрочитанные/ })).toBeInTheDocument();
    expect(screen.getByText('Непрочитано')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Отметить непрочитанным/ })).toBeInTheDocument();
  });

  it('пометка при открытии письма ВЫЗЫВАЕТСЯ (была запрещена §2.5)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true), makeMessage(1, [], true)] });
    render(<MailPage />);

    // Авто-выбор первого письма помечает его прочитанным — ровно как у БД-пользователя.
    expect(markReadSpy).toHaveBeenLastCalledWith(2);

    await user.click(screen.getByText('Письмо 1'));

    expect(markReadSpy).toHaveBeenLastCalledWith(1);
  });

  it('откат в «непрочитано» доступен: клик по кнопке шлёт DELETE …/read', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(1, [], false)] });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Отметить непрочитанным/ }));

    expect(unmarkReadSpy).toHaveBeenLastCalledWith(1);
  });

  it('фильтр «Непрочитанные» уходит в запрос ленты (не пустая страница, §2)', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2, [], true)] });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Непрочитанные/ }));

    expect(screen.getByRole('button', { name: /Непрочитанные/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(useMailFeedSpy).toHaveBeenLastCalledWith(expect.objectContaining({ unread: true }));
  });

  it('РЕГРЕСС: контрол «Уведомления» супер-админу по-прежнему НЕ рендерится (ADR-051 §1.6)', () => {
    feed.value = baseFeed({ messages: [makeMessage(1, [], true)] });
    render(<MailPage />);

    // Прочитанность разблокирована, но Telegram-привязка супер-админу запрещена (403
    // на `/api/mail/me/settings` СОХРАНЁН по security-основанию) ⇒ тумблер скрыт.
    expect(screen.queryByRole('button', { name: /Уведомления/ })).not.toBeInTheDocument();
    // Контроль: сами контролы прочитанности при этом на месте — скрыт именно тумблер.
    expect(screen.getByRole('button', { name: /Непрочитанные/ })).toBeInTheDocument();
  });
});

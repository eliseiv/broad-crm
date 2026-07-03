import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailPage } from '@/pages/MailPage';
import { ApiError } from '@/lib/api';
import type { MailFeedResult } from '@/features/mail/hooks';
import type { MailMessage } from '@/types/api';

const feed = vi.hoisted(() => ({ value: null as unknown }));

vi.mock('@/features/mail/hooks', () => ({
  useMailFeed: () => feed.value,
  // MailDetail → MailReplyForm использует useReplyMail — мокаем как no-op мутацию.
  useReplyMail: () => ({ mutate: vi.fn(), isPending: false }),
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
  });

  afterEach(() => {
    vi.unstubAllGlobals();
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

describe('MailPage "Только с тегами" filter', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    ioCallback = null;
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('toggles aria-pressed on the filter button', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)] });
    render(<MailPage />);

    const toggle = screen.getByRole('button', { name: /Только с тегами/ });
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

    await user.click(screen.getByRole('button', { name: /Только с тегами/ }));

    // Письмо без тегов (id=1) скрыто; тегированное (id=2) остаётся видимым в детали.
    expect(screen.queryByText('Письмо 1')).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
  });

  it('shows the empty-filter notice when no loaded message has tags and nothing more to load', async () => {
    const user = userEvent.setup();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: false });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Только с тегами/ }));

    expect(screen.getByText('Нет писем с тегами среди загруженных')).toBeInTheDocument();
  });

  it('keeps the selected message in the detail panel even when the filter hides it from the list', async () => {
    const user = userEvent.setup();
    // id=2 — самое свежее (авто-выбор), без тегов; id=1 — тегированное.
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1, [tag])] });
    render(<MailPage />);

    // Авто-выбор — самое свежее письмо (id=2).
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /Только с тегами/ }));

    // Правая панель сохранила выбор id=2, хотя список скрыл его; в списке теперь id=1.
    expect(screen.getByRole('heading', { name: 'Письмо 2' })).toBeInTheDocument();
    expect(screen.getByText('Письмо 1')).toBeInTheDocument();
  });

  it('keeps loading older messages while the filter is active and hasMore is true', async () => {
    const user = userEvent.setup();
    const loadMore = vi.fn();
    feed.value = baseFeed({ messages: [makeMessage(2), makeMessage(1)], hasMore: true, loadMore });
    render(<MailPage />);

    await user.click(screen.getByRole('button', { name: /Только с тегами/ }));

    // Финальная заглушка НЕ показывается, пока есть ещё старые письма.
    expect(screen.queryByText('Нет писем с тегами среди загруженных')).not.toBeInTheDocument();
    // Sentinel продолжает догрузку старых батчей даже при активном фильтре.
    triggerIntersection();
    expect(loadMore).toHaveBeenCalled();
  });
});

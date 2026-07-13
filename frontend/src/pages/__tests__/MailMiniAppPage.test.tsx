import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailMiniAppPage } from '@/pages/MailMiniAppPage';
import { ApiError } from '@/lib/api';
import { useMailMiniAppAuthStore } from '@/features/mail/miniAppAuth';
import type { MailMessage } from '@/types/api';

// Telegram SDK и SSO-эндпоинт управляются из теста — эмулируем вход/ошибки без сети/Telegram.
const tg = vi.hoisted(() => ({ loadTelegramSdk: vi.fn(), applyTelegramTheme: vi.fn() }));
vi.mock('@/features/sms/telegramSdk', () => tg);

const mailApi = vi.hoisted(() => ({ mailTelegramAuth: vi.fn() }));
vi.mock('@/features/mail/api', () => mailApi);

// Лента Mini App после успешного SSO — управляемый результат (по умолчанию пустая, ready).
const feed = vi.hoisted(() => ({
  value: {
    messages: [],
    phase: 'ready',
    error: null,
    hasMore: false,
    isFetchingMore: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
  } as unknown,
}));
// Пометка «прочитано ПРИ ОТКРЫТИИ» в Mini App (ADR-050 §2.6) — тот же `POST …/read`, что и в
// вебе (Mini App несёт обычный CRM-JWT с `uid`, спец-эндпоинта нет). Спаим факт и аргумент.
const markReadSpy = vi.hoisted(() => vi.fn());
vi.mock('@/features/mail/miniAppHooks', () => ({
  useMailMiniAppFeed: () => feed.value,
  useMarkMailMiniAppRead: () => ({ mutate: markReadSpy, isPending: false }),
}));

/** Минимальный Telegram WebApp с непустым initData (успешный контекст запуска из бота). */
function webApp(initData = 'tg-init-data') {
  return {
    initData,
    ready: vi.fn(),
    expand: vi.fn(),
    onEvent: vi.fn(),
    offEvent: vi.fn(),
  };
}

function authResponse() {
  return {
    access_token: 'sso-jwt',
    token_type: 'bearer',
    expires_in: 3600,
    telegram_user_id: 42,
    linked: true,
  };
}

/** Полный `MailMessage` (лента возвращает всё для detail — ADR-044 §2). Переопределяемый. */
function mailMessage(overrides: Partial<MailMessage> = {}): MailMessage {
  return {
    id: 1,
    subject: 'Квартальный отчёт',
    internal_date: '2026-07-01T09:30:00Z',
    from_addr: 'alice@example.com',
    from_name: 'Alice Sender',
    to_addrs: 'ops@team.com',
    cc_addrs: null,
    mail_account: { id: 7, email: 'ops@team.com', display_name: 'Операторы' },
    body_text: 'Тело письма в виде простого текста.',
    body_html: '<p>Тело письма в <b>HTML</b>.</p>',
    body_present: true,
    body_truncated: false,
    // Персональная непрочитанность (ADR-050 §2.2) — обязательное поле контракта ленты.
    is_unread: false,
    tags: [],
    ...overrides,
  };
}

/** Успешный SSO-контекст + управляемая лента писем. */
function setSuccessWithMessages(messages: MailMessage[]) {
  tg.loadTelegramSdk.mockResolvedValue(webApp());
  mailApi.mailTelegramAuth.mockResolvedValue(authResponse());
  feed.value = {
    messages,
    phase: 'ready',
    error: null,
    hasMore: false,
    isFetchingMore: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
  };
}

describe('MailMiniAppPage SSO (/tg/mail, ADR-044 §7)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMailMiniAppAuthStore.getState().clear();
    feed.value = {
      messages: [],
      phase: 'ready',
      error: null,
      hasMore: false,
      isFetchingMore: false,
      loadMore: vi.fn(),
      reload: vi.fn(),
    };
  });

  it('successful SSO renders the feed directly (no h1, no «Сообщения» tab) and stores the SSO token', async () => {
    tg.loadTelegramSdk.mockResolvedValue(webApp());
    mailApi.mailTelegramAuth.mockResolvedValue(authResponse());

    render(<MailMiniAppPage />);

    // Пустая лента ready → «Писем пока нет» рендерится напрямую.
    await waitFor(() => expect(screen.getByText('Писем пока нет')).toBeInTheDocument());
    expect(mailApi.mailTelegramAuth).toHaveBeenCalledWith('tg-init-data');
    // Экран без прежнего h1-заголовка и без декоративной таб-пилюли «Сообщения» (ADR-044 поправка).
    expect(screen.queryByText('Сообщения')).not.toBeInTheDocument();
    expect(screen.queryByText('Почта — уведомления')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    // SSO-токен положен в изолированный стор.
    expect(useMailMiniAppAuthStore.getState().token).toBe('sso-jwt');
    expect(useMailMiniAppAuthStore.getState().telegramUserId).toBe(42);
  });

  it('successful SSO with messages renders clickable cards (role="button") without a tab label', async () => {
    setSuccessWithMessages([mailMessage()]);

    render(<MailMiniAppPage />);

    const card = await screen.findByRole('button', { name: /Alice Sender/ });
    expect(card).toBeInTheDocument();
    expect(screen.queryByText('Сообщения')).not.toBeInTheDocument();
    expect(screen.queryByText('Писем пока нет')).not.toBeInTheDocument();
  });

  it('403 mail_operator_not_provisioned shows the «Доступ не настроен» screen', async () => {
    tg.loadTelegramSdk.mockResolvedValue(webApp());
    mailApi.mailTelegramAuth.mockRejectedValue(
      new ApiError(403, 'mail_operator_not_provisioned', 'not provisioned'),
    );

    render(<MailMiniAppPage />);

    await waitFor(() => expect(screen.getByText('Доступ не настроен')).toBeInTheDocument());
    expect(useMailMiniAppAuthStore.getState().token).toBeNull();
  });

  it('opening outside Telegram (no WebApp initData) shows the «open via bot» hint and skips SSO', async () => {
    // Вне Telegram initData пуст → SSO не стартует (public-эндпоинт всё равно не зовём).
    tg.loadTelegramSdk.mockResolvedValue(webApp(''));

    render(<MailMiniAppPage />);

    await waitFor(() =>
      expect(
        screen.getByText('Откройте это приложение по кнопке бота в Telegram'),
      ).toBeInTheDocument(),
    );
    expect(mailApi.mailTelegramAuth).not.toHaveBeenCalled();
  });

  it('missing window.Telegram.WebApp (undefined SDK) also shows the «open via bot» hint', async () => {
    tg.loadTelegramSdk.mockResolvedValue(undefined);

    render(<MailMiniAppPage />);

    await waitFor(() =>
      expect(
        screen.getByText('Откройте это приложение по кнопке бота в Telegram'),
      ).toBeInTheDocument(),
    );
    expect(mailApi.mailTelegramAuth).not.toHaveBeenCalled();
  });

  it('network/5xx SSO error shows «Не удалось загрузить» with a retry button', async () => {
    tg.loadTelegramSdk.mockResolvedValue(webApp());
    mailApi.mailTelegramAuth.mockRejectedValue(new ApiError(500, 'internal_error', 'boom'));

    render(<MailMiniAppPage />);

    await waitFor(() => expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('401 init_data_expired shows the stale-session hint', async () => {
    tg.loadTelegramSdk.mockResolvedValue(webApp());
    mailApi.mailTelegramAuth.mockRejectedValue(new ApiError(401, 'init_data_expired', 'expired'));

    render(<MailMiniAppPage />);

    await waitFor(() =>
      expect(
        screen.getByText('Сессия Telegram устарела — откройте приложение заново через бота'),
      ).toBeInTheDocument(),
    );
  });
});

describe('MailMiniAppPage detail (read-only, ADR-044 поправка 2026-07-10)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMailMiniAppAuthStore.getState().clear();
    feed.value = {
      messages: [],
      phase: 'ready',
      error: null,
      hasMore: false,
      isFetchingMore: false,
      loadMore: vi.fn(),
      reload: vi.fn(),
    };
  });

  /** Рендерит страницу с одним письмом и дожидается карточки в ленте. */
  async function renderWithMessage(overrides: Partial<MailMessage> = {}) {
    const message = mailMessage(overrides);
    setSuccessWithMessages([message]);
    render(<MailMiniAppPage />);
    const card = await screen.findByRole('button', {
      name: new RegExp(message.from_name ?? message.from_addr),
    });
    return { message, card };
  }

  it('click on a card opens the full-width read-only detail (dialog) with header fields', async () => {
    const { card, message } = await renderWithMessage();

    fireEvent.click(card);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Назад' })).toBeInTheDocument();
    // Шапка: отправитель, тема, «Получено на: {display_name} <{email}>», дата ru-RU.
    expect(dialog).toHaveTextContent('Alice Sender');
    expect(dialog).toHaveTextContent('alice@example.com');
    expect(dialog).toHaveTextContent('Квартальный отчёт');
    expect(dialog).toHaveTextContent('Получено на:');
    expect(dialog).toHaveTextContent('Операторы');
    expect(dialog).toHaveTextContent('<ops@team.com>');
    // Дата рендерится в <time> с точным dateTime = internal_date письма.
    const time = within(dialog).getByText((_t, el) => el?.tagName === 'TIME');
    expect(time).toHaveAttribute('datetime', message.internal_date);
    expect(time?.textContent?.trim()).not.toBe('');
  });

  it('Enter key on a focused card opens the detail (keyboard accessible)', async () => {
    const { card } = await renderWithMessage();

    fireEvent.keyDown(card, { key: 'Enter' });

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('Space key on a focused card opens the detail (keyboard accessible)', async () => {
    const { card } = await renderWithMessage();

    fireEvent.keyDown(card, { key: ' ' });

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('«Назад» button closes the detail and returns to the feed', async () => {
    const { card } = await renderWithMessage();

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Назад' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // Лента снова доступна — карточка на месте.
    expect(screen.getByRole('button', { name: /Alice Sender/ })).toBeInTheDocument();
  });

  it('body_html renders inside a locked-down sandbox iframe (XSS invariant, no allow-scripts/allow-same-origin)', async () => {
    const { card, message } = await renderWithMessage({
      body_html: '<p>Секрет от <b>внешнего</b> отправителя</p>',
    });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    const iframe = within(dialog).getByTitle('Тело письма') as HTMLIFrameElement;
    // Безопасность: sandbox ПУСТОЙ — без allow-scripts / allow-same-origin (ADR-012, XSS-инвариант).
    const sandbox = iframe.getAttribute('sandbox');
    expect(sandbox).toBe('');
    expect(sandbox).not.toContain('allow-scripts');
    expect(sandbox).not.toContain('allow-same-origin');
    expect(iframe.getAttribute('referrerpolicy')).toBe('no-referrer');
    // Недоверенный HTML уходит в srcDoc, а не в основной DOM.
    expect(iframe.getAttribute('srcdoc')).toContain('Секрет от');
    expect(message.body_html).toBeTruthy();
  });

  it('empty body_html falls back to body_text (no iframe rendered)', async () => {
    const { card } = await renderWithMessage({
      body_html: null,
      body_text: 'Только текстовое тело письма.',
    });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('Только текстовое тело письма.')).toBeInTheDocument();
    expect(within(dialog).queryByTitle('Тело письма')).not.toBeInTheDocument();
  });

  it('body_truncated=true shows the «Письмо показано не полностью» note (no lazy-load)', async () => {
    const { card } = await renderWithMessage({ body_truncated: true });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('Письмо показано не полностью')).toBeInTheDocument();
  });

  it('body_truncated=false does NOT show the truncation note', async () => {
    const { card } = await renderWithMessage({ body_truncated: false });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).queryByText('Письмо показано не полностью')).not.toBeInTheDocument();
  });

  it('body_present=false shows «Тело письма недоступно» and no iframe/text body', async () => {
    const { card } = await renderWithMessage({
      body_present: false,
      body_html: '<p>ignored</p>',
      body_text: 'ignored',
    });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByText('Тело письма недоступно')).toBeInTheDocument();
    expect(within(dialog).queryByTitle('Тело письма')).not.toBeInTheDocument();
  });

  it('null subject shows «(без темы)» in the detail header', async () => {
    const { card } = await renderWithMessage({ subject: null });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(within(dialog).getByRole('heading', { name: '(без темы)' })).toBeInTheDocument();
  });

  it('empty display_name renders «Получено на:» with the bare email (no angle brackets)', async () => {
    const { card } = await renderWithMessage({
      mail_account: { id: 9, email: 'raw@team.com', display_name: null },
    });

    fireEvent.click(card);
    const dialog = await screen.findByRole('dialog');

    expect(dialog).toHaveTextContent('Получено на:');
    expect(dialog).toHaveTextContent('raw@team.com');
    expect(dialog).not.toHaveTextContent('<raw@team.com>');
  });
});

// Личная прочитанность в Mini App (ADR-050 §2.6/§2.8): пометка ПРИ ОТКРЫТИИ тем же эндпоинтом,
// индикатор непрочитанного в карточке. Фильтра «Непрочитанные» и кнопки отката в Mini App НЕТ.
describe('MailMiniAppPage — прочитанность (ADR-050 §2.6/§2.8)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useMailMiniAppAuthStore.getState().clear();
    feed.value = {
      messages: [],
      phase: 'ready',
      error: null,
      hasMore: false,
      isFetchingMore: false,
      loadMore: vi.fn(),
      reload: vi.fn(),
    };
  });

  it('клик по карточке открывает деталь и шлёт РОВНО ОДИН POST …/read с id письма', async () => {
    setSuccessWithMessages([mailMessage({ id: 42, is_unread: true })]);
    render(<MailMiniAppPage />);
    const card = await screen.findByRole('button', { name: /Alice Sender/ });

    expect(markReadSpy).not.toHaveBeenCalled();

    fireEvent.click(card);

    await screen.findByRole('dialog');
    expect(markReadSpy).toHaveBeenCalledTimes(1);
    expect(markReadSpy).toHaveBeenCalledWith(42);
  });

  it('открытие с клавиатуры (Enter) тоже помечает письмо прочитанным', async () => {
    setSuccessWithMessages([mailMessage({ id: 7, is_unread: true })]);
    render(<MailMiniAppPage />);
    const card = await screen.findByRole('button', { name: /Alice Sender/ });

    fireEvent.keyDown(card, { key: 'Enter' });

    await screen.findByRole('dialog');
    expect(markReadSpy).toHaveBeenCalledTimes(1);
    expect(markReadSpy).toHaveBeenCalledWith(7);
  });

  it('непрочитанная карточка несёт sr-only «Непрочитано»; прочитанная — нет', async () => {
    setSuccessWithMessages([
      mailMessage({ id: 1, is_unread: true }),
      mailMessage({ id: 2, is_unread: false, from_name: 'Bob Reader' }),
    ]);
    render(<MailMiniAppPage />);
    await screen.findByRole('button', { name: /Alice Sender/ });

    // Индикатор только у непрочитанного письма (не полагаемся на цвет/вес — a11y).
    expect(screen.getAllByText('Непрочитано')).toHaveLength(1);
  });

  it('в Mini App НЕТ фильтра «Непрочитанные» и кнопки отката «Отметить непрочитанным»', async () => {
    setSuccessWithMessages([mailMessage({ id: 1, is_unread: true })]);
    render(<MailMiniAppPage />);
    const card = await screen.findByRole('button', { name: /Alice Sender/ });

    expect(screen.queryByRole('button', { name: /Непрочитанные/ })).not.toBeInTheDocument();

    fireEvent.click(card);
    await screen.findByRole('dialog');

    expect(
      screen.queryByRole('button', { name: /Отметить непрочитанным/ }),
    ).not.toBeInTheDocument();
  });
});

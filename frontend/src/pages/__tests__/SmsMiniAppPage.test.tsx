import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SmsMiniAppPage } from '@/pages/SmsMiniAppPage';
import { ApiError } from '@/lib/api';
import { useMiniAppAuthStore } from '@/features/sms/miniAppAuth';
import { useAuthStore } from '@/store/auth';
import type { TelegramAuthResponse } from '@/types/api';

/**
 * Интеграционные тесты операторской Mini App (`/tg/sms`, ADR-031, беспарольный
 * Telegram-SSO). Мокаем self-hosted SDK (`telegramSdk`), SSO-запрос (`telegramAuth`)
 * и view-хуки Mini App (`miniAppHooks`) — сеть НЕ трогаем. Контракт SSO —
 * 04-api.md#post-apismstelegramauth. Auth-store Mini App (`miniAppAuth`) — РЕАЛЬНЫЙ
 * (проверяем изоляцию токена от админского `crm.auth.*`).
 */

// --- Мок self-hosted Telegram WebApp SDK (window.Telegram.WebApp) ------------
const tg = vi.hoisted(() => ({
  webApp: undefined as unknown,
  loadShouldReject: false,
}));

vi.mock('@/features/sms/telegramSdk', () => ({
  loadTelegramSdk: vi.fn(() =>
    tg.loadShouldReject
      ? Promise.reject(new Error('telegram_sdk_load_failed'))
      : Promise.resolve(tg.webApp),
  ),
  applyTelegramTheme: vi.fn(),
}));

// --- Мок SSO-запроса (POST /api/sms/telegram/auth) ---------------------------
const authMock = vi.hoisted(() => ({ fn: vi.fn() }));

vi.mock('@/features/sms/api', async () => {
  const actual = await vi.importActual<typeof import('@/features/sms/api')>('@/features/sms/api');
  return { ...actual, telegramAuth: (...args: unknown[]) => authMock.fn(...args) };
});

// --- Мок view-хуков Mini App (numbers/messages под sms:view) ------------------
const hooks = vi.hoisted(() => ({
  numbers: null as unknown,
  messages: null as unknown,
}));

vi.mock('@/features/sms/miniAppHooks', () => ({
  useMiniAppSmsNumbers: () => hooks.numbers,
  useMiniAppSmsMessages: () => hooks.messages,
}));

// Управляемый IntersectionObserver (sentinel-эффект AuthorizedView).
class MockIntersectionObserver {
  observe = vi.fn();
  disconnect = vi.fn();
  unobserve = vi.fn();
  takeRecords = vi.fn();
  root = null;
  rootMargin = '';
  thresholds = [];
}

interface FakeWebApp {
  initData: string;
  initDataUnsafe: Record<string, unknown>;
  themeParams: Record<string, string>;
  ready: ReturnType<typeof vi.fn>;
  expand: ReturnType<typeof vi.fn>;
  onEvent: ReturnType<typeof vi.fn>;
  offEvent: ReturnType<typeof vi.fn>;
}

function makeWebApp(over: Partial<FakeWebApp> = {}): FakeWebApp {
  return {
    initData: 'query_id=AAA&user=%7B%22id%22%3A1%7D&auth_date=1700000000&hash=deadbeef',
    initDataUnsafe: {},
    themeParams: {},
    ready: vi.fn(),
    expand: vi.fn(),
    onEvent: vi.fn(),
    offEvent: vi.fn(),
    ...over,
  };
}

/** Полная схема TelegramAuthResponse 200 (04-api.md#post-apismstelegramauth). */
function makeAuthResponse(over: Partial<TelegramAuthResponse> = {}): TelegramAuthResponse {
  return {
    access_token: 'sso-access-jwt',
    token_type: 'bearer',
    expires_in: 86400,
    telegram_user_id: 123456789,
    linked: true,
    ...over,
  };
}

function readyNumbers(numbers: unknown[] = []) {
  return {
    data: { numbers },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  };
}

function forbiddenNumbers() {
  return {
    data: undefined,
    isLoading: false,
    isError: true,
    error: new ApiError(403, 'forbidden', 'Недостаточно прав'),
    refetch: vi.fn(),
  };
}

function readyMessages(messages: unknown[] = []) {
  return {
    messages,
    phase: 'ready' as const,
    error: null,
    hasMore: false,
    isFetchingMore: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
  };
}

function forbiddenMessages() {
  return {
    messages: [],
    phase: 'error' as const,
    error: new ApiError(403, 'forbidden', 'Недостаточно прав'),
    hasMore: false,
    isFetchingMore: false,
    loadMore: vi.fn(),
    reload: vi.fn(),
  };
}

describe('SmsMiniAppPage (ADR-031 беспарольный Telegram-SSO)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMock.fn.mockReset();
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    tg.webApp = makeWebApp();
    tg.loadShouldReject = false;
    hooks.numbers = readyNumbers([]);
    hooks.messages = readyMessages([]);
    // Чистое состояние обоих сторов + sessionStorage перед каждым тестом.
    useMiniAppAuthStore.getState().clear();
    useAuthStore.getState().clearSession();
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // === 1. Ветки SSO-auth ====================================================

  it('200: токен сохранён в miniAppAuth, рендерится AuthorizedView («Привязан»)', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    render(<SmsMiniAppPage />);

    expect(await screen.findByText('Привязан')).toBeInTheDocument();
    expect(
      screen.getByText('Telegram привязан — новые SMS вашей команды приходят сюда.'),
    ).toBeInTheDocument();
    // SSO вызван с raw initData из WebApp.
    expect(authMock.fn).toHaveBeenCalledTimes(1);
    expect(authMock.fn).toHaveBeenCalledWith((tg.webApp as FakeWebApp).initData);
    // Токен и telegram_user_id записаны в изолированный стор Mini App.
    expect(useMiniAppAuthStore.getState().token).toBe('sso-access-jwt');
    expect(useMiniAppAuthStore.getState().telegramUserId).toBe(123456789);
  });

  it('401 invalid_init_data → экран «сессия устарела», токен не сохранён', async () => {
    authMock.fn.mockRejectedValue(new ApiError(401, 'invalid_init_data', 'bad hmac'));
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Сессия Telegram устарела — откройте приложение заново через бота'),
    ).toBeInTheDocument();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
    expect(screen.queryByText('Привязан')).not.toBeInTheDocument();
  });

  it('401 init_data_expired → экран «сессия устарела»', async () => {
    authMock.fn.mockRejectedValue(new ApiError(401, 'init_data_expired', 'expired'));
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Сессия Telegram устарела — откройте приложение заново через бота'),
    ).toBeInTheDocument();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
  });

  it('403 sms_operator_not_provisioned → экран «Доступ не настроен»', async () => {
    authMock.fn.mockRejectedValue(
      new ApiError(403, 'sms_operator_not_provisioned', 'not provisioned'),
    );
    render(<SmsMiniAppPage />);

    expect(await screen.findByText('Доступ не настроен')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Ваш Telegram не сопоставлен с оператором CRM. Обратитесь к администратору.',
      ),
    ).toBeInTheDocument();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
    expect(screen.queryByText('Привязан')).not.toBeInTheDocument();
  });

  it('400 validation_error → экран «сессия устарела» (пустой/битый init_data)', async () => {
    authMock.fn.mockRejectedValue(new ApiError(400, 'validation_error', 'invalid'));
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Сессия Telegram устарела — откройте приложение заново через бота'),
    ).toBeInTheDocument();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
  });

  it('5xx ApiError → экран сети «Не удалось загрузить» с кнопкой «Повторить»', async () => {
    authMock.fn.mockRejectedValue(new ApiError(500, 'internal_error', 'boom'));
    render(<SmsMiniAppPage />);

    expect(await screen.findByText('Не удалось загрузить')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('сетевая ошибка (не ApiError) → экран сети; «Повторить» повторяет SSO и грузит контент', async () => {
    const user = userEvent.setup();
    authMock.fn.mockRejectedValueOnce(new Error('Failed to fetch'));
    render(<SmsMiniAppPage />);

    expect(await screen.findByText('Не удалось загрузить')).toBeInTheDocument();

    // Повтор: тот же initData → успех → AuthorizedView.
    authMock.fn.mockResolvedValueOnce(makeAuthResponse());
    await user.click(screen.getByRole('button', { name: 'Повторить' }));

    expect(await screen.findByText('Привязан')).toBeInTheDocument();
    expect(authMock.fn).toHaveBeenCalledTimes(2);
    expect(authMock.fn).toHaveBeenLastCalledWith((tg.webApp as FakeWebApp).initData);
  });

  it('сбой загрузки SDK (reject) → экран сети, SSO не вызывается', async () => {
    tg.loadShouldReject = true;
    render(<SmsMiniAppPage />);

    expect(await screen.findByText('Не удалось загрузить')).toBeInTheDocument();
    expect(authMock.fn).not.toHaveBeenCalled();
  });

  // === 2. Изоляция SSO-токена от админского стора ============================

  it('изоляция: после 200 токен НЕ попадает в crm.auth.* и не аутентифицирует админ-стор', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    render(<SmsMiniAppPage />);
    await screen.findByText('Привязан');

    // miniAppAuth содержит SSO-токен…
    expect(useMiniAppAuthStore.getState().token).toBe('sso-access-jwt');
    // …а админский стор (crm.auth.*) НЕ тронут — нет редиректа на /login основной админки.
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(sessionStorage.getItem('crm.auth.token')).toBeNull();
    expect(sessionStorage.getItem('crm.auth.username')).toBeNull();
  });

  // === 3. Гейт sms:view: 403 на секциях + пустые данные ======================

  it('sms:view 403 на numbers → секция «Мои номера» скрыта; статус привязки остаётся', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = forbiddenNumbers();
    hooks.messages = readyMessages([]);
    render(<SmsMiniAppPage />);
    await screen.findByText('Привязан');

    expect(screen.queryByText('Мои номера')).not.toBeInTheDocument();
    // messages-секция под доступом остаётся видимой (пустая).
    expect(screen.getByText('Мои сообщения')).toBeInTheDocument();
    expect(screen.getByText('Сообщений пока нет')).toBeInTheDocument();
  });

  it('sms:view 403 на messages → секция «Мои сообщения» скрыта', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = readyNumbers([]);
    hooks.messages = forbiddenMessages();
    render(<SmsMiniAppPage />);
    await screen.findByText('Привязан');

    expect(screen.queryByText('Мои сообщения')).not.toBeInTheDocument();
    expect(screen.getByText('Мои номера')).toBeInTheDocument();
    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
  });

  it('пустые данные обеих секций → empty-заглушки «Номеров нет» / «Сообщений пока нет»', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = readyNumbers([]);
    hooks.messages = readyMessages([]);
    render(<SmsMiniAppPage />);
    await screen.findByText('Привязан');

    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
    expect(screen.getByText('Сообщений пока нет')).toBeInTheDocument();
  });

  // === 4. «Вне Telegram» =====================================================

  it('вне Telegram (WebApp отсутствует) → заглушка «откройте в Telegram», SSO не вызывается', async () => {
    tg.webApp = undefined;
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Откройте это приложение по кнопке бота в Telegram'),
    ).toBeInTheDocument();
    expect(authMock.fn).not.toHaveBeenCalled();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
  });

  it('пустой initData (initData === "") → заглушка «откройте в Telegram», SSO не вызывается', async () => {
    tg.webApp = makeWebApp({ initData: '' });
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Откройте это приложение по кнопке бота в Telegram'),
    ).toBeInTheDocument();
    expect(authMock.fn).not.toHaveBeenCalled();
  });
});

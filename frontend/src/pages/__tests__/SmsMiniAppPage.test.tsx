import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SmsMiniAppPage } from '@/pages/SmsMiniAppPage';
import { ApiError } from '@/lib/api';
import { useMiniAppAuthStore } from '@/features/sms/miniAppAuth';
import { useAuthStore } from '@/store/auth';
import type { TelegramAuthResponse, MeResponse } from '@/types/api';

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

/**
 * Ответ `/api/auth/me` под SSO-токеном Mini App — ЕДИНСТВЕННЫЙ источник опций фильтра
 * «Команда» (ADR-055 §5.1/§6.2: `GET /api/teams` из Mini App ЗАПРЕЩЁН). Управляемый:
 * тест диктует состав `mail_teams`/`includes_unassigned` и проверяет порог рендера (≥ 2).
 */
const me = vi.hoisted(() => ({
  value: { data: undefined as unknown },
}));

vi.mock('@/features/sms/miniAppHooks', () => ({
  useMiniAppSmsNumbers: () => hooks.numbers,
  useMiniAppSmsMessages: () => hooks.messages,
  useSmsMiniAppMe: () => me.value,
}));

// `GET /api/teams` из Mini App ЗАПРЕЩЁН (ADR-055 §6.2) — спай обязан остаться нулевым.
const teamsSpy = vi.hoisted(() => vi.fn(() => ({ data: { items: [] } })));
vi.mock('@/features/teams/hooks', () => ({ useTeams: teamsSpy }));

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

  it('200: токен сохранён в miniAppAuth, рендерится AuthorizedView (две вкладки, без бейджа «Привязан»)', async () => {
    authMock.fn.mockResolvedValue(makeAuthResponse());
    render(<SmsMiniAppPage />);

    // ADR-037: успех = две вкладки «Сообщения»/«Номера»; бейдж «Привязан»/hint убран.
    expect(await screen.findByRole('tab', { name: 'Сообщения' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Номера' })).toBeInTheDocument();
    expect(screen.queryByText('Привязан')).not.toBeInTheDocument();
    expect(
      screen.queryByText('Telegram привязан — новые SMS вашей команды приходят сюда.'),
    ).not.toBeInTheDocument();
    // SSO вызван с raw initData из WebApp.
    expect(authMock.fn).toHaveBeenCalledTimes(1);
    expect(authMock.fn).toHaveBeenCalledWith((tg.webApp as FakeWebApp).initData);
    // Токен и telegram_user_id записаны в изолированный стор Mini App.
    expect(useMiniAppAuthStore.getState().token).toBe('sso-access-jwt');
    expect(useMiniAppAuthStore.getState().telegramUserId).toBe(123456789);
  });

  it('ADR-037: вкладка «Сообщения» активна по умолчанию; клик по «Номера» переключает aria-selected', async () => {
    const user = userEvent.setup();
    authMock.fn.mockResolvedValue(makeAuthResponse());
    render(<SmsMiniAppPage />);

    const msgTab = await screen.findByRole('tab', { name: 'Сообщения' });
    const numTab = screen.getByRole('tab', { name: 'Номера' });
    expect(msgTab).toHaveAttribute('aria-selected', 'true');
    expect(numTab).toHaveAttribute('aria-selected', 'false');

    await user.click(numTab);
    expect(numTab).toHaveAttribute('aria-selected', 'true');
    expect(msgTab).toHaveAttribute('aria-selected', 'false');
  });

  it('401 invalid_init_data → экран «сессия устарела», токен не сохранён', async () => {
    authMock.fn.mockRejectedValue(new ApiError(401, 'invalid_init_data', 'bad hmac'));
    render(<SmsMiniAppPage />);

    expect(
      await screen.findByText('Сессия Telegram устарела — откройте приложение заново через бота'),
    ).toBeInTheDocument();
    expect(useMiniAppAuthStore.getState().token).toBeNull();
    expect(screen.queryByRole('tab', { name: 'Сообщения' })).not.toBeInTheDocument();
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
    expect(screen.queryByRole('tab', { name: 'Сообщения' })).not.toBeInTheDocument();
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

    expect(await screen.findByRole('tab', { name: 'Сообщения' })).toBeInTheDocument();
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
    await screen.findByRole('tab', { name: 'Сообщения' });

    // miniAppAuth содержит SSO-токен…
    expect(useMiniAppAuthStore.getState().token).toBe('sso-access-jwt');
    // …а админский стор (crm.auth.*) НЕ тронут — нет редиректа на /login основной админки.
    expect(useAuthStore.getState().token).toBeNull();
    expect(useAuthStore.getState().isAuthenticated).toBe(false);
    expect(sessionStorage.getItem('crm.auth.token')).toBeNull();
    expect(sessionStorage.getItem('crm.auth.username')).toBeNull();
  });

  // === 3. Гейт sms:view: 403 на вкладках + пустые данные =====================

  it('sms:view 403 на numbers → панель вкладки «Номера» пуста (без empty-заглушки)', async () => {
    const user = userEvent.setup();
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = forbiddenNumbers();
    hooks.messages = readyMessages([]);
    render(<SmsMiniAppPage />);
    await screen.findByRole('tab', { name: 'Сообщения' });

    // Вкладка «Сообщения» под доступом — пустая заглушка видна.
    expect(screen.getByText('Сообщений пока нет')).toBeInTheDocument();

    // Переключение на «Номера»: 403 → панель пуста, empty-заглушки «Номеров нет» нет.
    await user.click(screen.getByRole('tab', { name: 'Номера' }));
    expect(screen.queryByText('Номеров нет')).not.toBeInTheDocument();
  });

  it('sms:view 403 на messages → панель вкладки «Сообщения» пуста', async () => {
    const user = userEvent.setup();
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = readyNumbers([]);
    hooks.messages = forbiddenMessages();
    render(<SmsMiniAppPage />);
    await screen.findByRole('tab', { name: 'Сообщения' });

    // Вкладка «Сообщения» под 403 — панель пуста (нет empty-заглушки).
    expect(screen.queryByText('Сообщений пока нет')).not.toBeInTheDocument();

    // Вкладка «Номера» под доступом — пустая заглушка видна.
    await user.click(screen.getByRole('tab', { name: 'Номера' }));
    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
  });

  it('пустые данные обеих вкладок → empty-заглушки «Сообщений пока нет» / «Номеров нет»', async () => {
    const user = userEvent.setup();
    authMock.fn.mockResolvedValue(makeAuthResponse());
    hooks.numbers = readyNumbers([]);
    hooks.messages = readyMessages([]);
    render(<SmsMiniAppPage />);
    await screen.findByRole('tab', { name: 'Сообщения' });

    // Дефолтная вкладка «Сообщения».
    expect(screen.getByText('Сообщений пока нет')).toBeInTheDocument();
    // Вкладка «Номера».
    await user.click(screen.getByRole('tab', { name: 'Номера' }));
    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
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

// ============================================================================
// Mini App `/tg/sms`: фильтр «Команда» — порог 2 (ADR-055 §6.2, экран 5 из пяти)
// ============================================================================

/** Ответ `/me` под SSO-токеном Mini App — ЕДИНСТВЕННЫЙ источник опций (ADR-055 §5.1/§6.2). */
function meResponse(over: Partial<MeResponse> = {}): MeResponse {
  return {
    username: 'ivan',
    role: 'Оператор',
    is_superadmin: false,
    sees_all_sms_teams: false,
    sees_all_mail_teams: false,
    mail_teams: [],
    sms_teams: [],
    mail_includes_unassigned: false,
    sms_includes_unassigned: false,
    permissions: { sms: ['view'] },
    ...over,
  };
}

const T_SALES = { id: 't1', name: 'Продажи' };
const T_SUPPORT = { id: 't2', name: 'Поддержка' };

describe('SmsMiniAppPage — фильтр «Команда»: порог 2 (ADR-055 §6.2, экран 5 из пяти)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMock.fn.mockReset();
    vi.stubGlobal('IntersectionObserver', MockIntersectionObserver);
    tg.webApp = makeWebApp();
    tg.loadShouldReject = false;
    hooks.numbers = readyNumbers([]);
    hooks.messages = readyMessages([]);
    me.value = { data: undefined };
    useMiniAppAuthStore.getState().clear();
    useAuthStore.getState().clearSession();
    sessionStorage.clear();
    authMock.fn.mockResolvedValue(makeAuthResponse());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    me.value = { data: undefined };
  });

  async function renderApp() {
    render(<SmsMiniAppPage />);
    await screen.findByRole('tab', { name: 'Сообщения' });
  }

  it('0 вариантов канала → контрола «Команда» НЕТ вовсе', async () => {
    me.value = { data: meResponse() };
    await renderApp();

    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
  });

  it('1 вариант (одна команда) → контрола НЕТ (фильтровать нечего)', async () => {
    me.value = { data: meResponse({ sms_teams: [T_SALES] }) };
    await renderApp();

    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
  });

  it('2 команды → контрол ЕСТЬ и содержит их', async () => {
    me.value = { data: meResponse({ sms_teams: [T_SALES, T_SUPPORT] }) };
    await renderApp();

    const select = screen.getByLabelText('Команда');
    expect(within(select).getByRole('option', { name: 'Продажи' })).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'Поддержка' })).toBeInTheDocument();
    expect(within(select).queryByRole('option', { name: 'Без команды' })).not.toBeInTheDocument();
  });

  it('1 команда + «Без команды» = 2 варианта → контрол ЕСТЬ', async () => {
    me.value = { data: meResponse({ sms_teams: [T_SALES], sms_includes_unassigned: true }) };
    await renderApp();

    expect(
      within(screen.getByLabelText('Команда')).getByRole('option', { name: 'Без команды' }),
    ).toBeInTheDocument();
  });

  it('ОБЯЗАТЕЛЬНЫЙ КЕЙС: актор admin-уровня → фильтр рендерится И СОДЕРЖИТ команды', async () => {
    // Регрессия дефекта редакции 1 ADR-055 (§5.1): при `sees_all → []` фильтр в Mini App был бы
    // ПУСТЫМ (`GET /api/teams` оттуда запрещён). Пустой контрол (0 команд) = ДЕФЕКТ.
    me.value = {
      data: meResponse({
        sees_all_sms_teams: true,
        sms_teams: [T_SALES, T_SUPPORT],
        sms_includes_unassigned: true,
      }),
    };
    await renderApp();

    const select = screen.getByLabelText('Команда');
    expect(within(select).getByRole('option', { name: 'Продажи' })).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'Поддержка' })).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'Без команды' })).toBeInTheDocument();
    // `GET /api/teams` из Mini App ЗАПРЕЩЁН — и под admin-уровнем тоже (§6.2/§6.3).
    expect(teamsSpy).not.toHaveBeenCalled();
  });

  it('НЕТ ветки «админу показывать всегда»: admin-уровень с 1 вариантом → контрола НЕТ', async () => {
    me.value = {
      data: meResponse({
        sees_all_sms_teams: true,
        sms_teams: [],
        sms_includes_unassigned: true,
      }),
    };
    await renderApp();

    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
  });
});

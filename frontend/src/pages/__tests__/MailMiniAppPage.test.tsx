import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailMiniAppPage } from '@/pages/MailMiniAppPage';
import { ApiError } from '@/lib/api';
import { useMailMiniAppAuthStore } from '@/features/mail/miniAppAuth';

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
vi.mock('@/features/mail/miniAppHooks', () => ({
  useMailMiniAppFeed: () => feed.value,
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

  it('successful SSO renders the authorized «Сообщения» view and stores the SSO token', async () => {
    tg.loadTelegramSdk.mockResolvedValue(webApp());
    mailApi.mailTelegramAuth.mockResolvedValue(authResponse());

    render(<MailMiniAppPage />);

    await waitFor(() => expect(screen.getByText('Сообщения')).toBeInTheDocument());
    expect(mailApi.mailTelegramAuth).toHaveBeenCalledWith('tg-init-data');
    // Пустая лента ready → «Писем пока нет»; SSO-токен положен в изолированный стор.
    expect(screen.getByText('Писем пока нет')).toBeInTheDocument();
    expect(useMailMiniAppAuthStore.getState().token).toBe('sso-jwt');
    expect(useMailMiniAppAuthStore.getState().telegramUserId).toBe(42);
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

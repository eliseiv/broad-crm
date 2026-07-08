import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LoginPage } from '@/pages/LoginPage';
import { ApiError } from '@/lib/api';
import { useAuthStore } from '@/store/auth';

const authHooks = vi.hoisted(() => ({
  mutate: vi.fn(),
  setPasswordMutate: vi.fn(),
}));

vi.mock('@/features/auth/hooks', () => ({
  useLogin: () => ({
    mutate: authHooks.mutate,
    isPending: false,
  }),
  useSetPassword: () => ({
    mutate: authHooks.setPasswordMutate,
    isPending: false,
  }),
}));

// Permission-aware дефолт (ADR-021): LoginPage редиректит на index `/`, а DefaultRoute
// резолвит целевой раздел по правам. Проверяем, что цель редиректа — `/`, а не `/dashboard`.
const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

function wrapper({ children }: PropsWithChildren) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

const IDENTIFIER_LABEL = 'Логин или Телеграм';
const SETUP_TITLE = 'Придумайте пароль';

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().clearSession();
  });

  it('шаг-1 использует лейбл «Логин или Телеграм» (ADR-025)', () => {
    render(<LoginPage />, { wrapper });
    expect(screen.getByLabelText(IDENTIFIER_LABEL)).toBeInTheDocument();
  });

  // --- Probe-flow «Далее» (ADR-029): пробный login({username, password:''}) ---

  it('«Далее» шлёт пробный login с пустым паролем (probe, ADR-029)', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation(() => {});

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(authHooks.mutate).toHaveBeenCalledWith(
      { username: 'admin', password: '' },
      expect.any(Object),
    );
  });

  it('«Далее»: password_setup_required → окно «Придумайте пароль» (без шага «Пароль»)', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: true, setup_token: 'setup-xyz' }),
    );

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'nikita');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(await screen.findByRole('dialog', { name: SETUP_TITLE })).toBeInTheDocument();
    // Поле «Пароль» шага 2 беспарольному не показывается.
    expect(screen.queryByLabelText('Пароль')).not.toBeInTheDocument();
  });

  it('«Далее»: пустой пароль отклонён (401) → переход на шаг «Пароль»', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(401, 'invalid_credentials', 'Неверные креды')),
    );

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    // Пользователь с паролем: сервер отклонил пустой пароль → показываем ввод пароля.
    expect(screen.getByLabelText('Пароль')).toHaveFocus();
  });

  it('«Далее»: 429 rate-limit → ошибка на шаге логина, шаг «Пароль» не открывается', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(429, 'rate_limited', 'Слишком много попыток')),
    );

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(screen.getByText('Слишком много попыток входа. Попробуйте позже.')).toBeInTheDocument();
    expect(screen.queryByLabelText('Пароль')).not.toBeInTheDocument();
  });

  it('«Далее»: успех с паролем → редирект на / (permission-aware default)', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: false, access_token: 't' }),
    );

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  // --- Шаг «Пароль» ---

  async function reachPasswordStep(user: ReturnType<typeof userEvent.setup>, username = 'admin') {
    authHooks.mutate.mockImplementationOnce((_payload, options) =>
      options.onError(new ApiError(401, 'invalid_credentials', 'Неверные креды')),
    );
    render(<LoginPage />, { wrapper });
    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), username);
    await user.click(screen.getByRole('button', { name: /далее/i }));
  }

  it('шаг «Пароль»: «Войти» шлёт login с введённым паролем', async () => {
    const user = userEvent.setup();
    await reachPasswordStep(user);
    authHooks.mutate.mockImplementation(() => {});

    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(authHooks.mutate).toHaveBeenLastCalledWith(
      { username: 'admin', password: 'secret' },
      expect.any(Object),
    );
  });

  it('шаг «Пароль»: неверный пароль → общая ошибка', async () => {
    const user = userEvent.setup();
    await reachPasswordStep(user);
    authHooks.mutate.mockImplementation((_payload, options) => options.onError(new Error('boom')));

    await user.type(screen.getByLabelText('Пароль'), 'bad-password');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(screen.getByRole('alert')).toHaveTextContent('Неверный логин или пароль');
  });

  it('шаг «Пароль»: успех → редирект на /', async () => {
    const user = userEvent.setup();
    await reachPasswordStep(user);
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: false, access_token: 't' }),
    );

    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it('redirects an already-authenticated user to / on mount', () => {
    useAuthStore.getState().setSession('jwt-token', 'admin');

    render(<LoginPage />, { wrapper });

    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  // --- «Открытый первый вход»: password_setup_required → «Придумайте пароль» (ADR-029) ---

  async function reachSetup(user: ReturnType<typeof userEvent.setup>) {
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: true, setup_token: 'setup-xyz' }),
    );
    render(<LoginPage />, { wrapper });
    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'nikita');
    await user.click(screen.getByRole('button', { name: /далее/i }));
  }

  it('окно установки использует заголовок «Придумайте пароль» (ADR-029)', async () => {
    const user = userEvent.setup();
    await reachSetup(user);

    expect(await screen.findByRole('dialog', { name: SETUP_TITLE })).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalledWith('/', { replace: true });
  });

  it('set-password: успех → редирект на /', async () => {
    const user = userEvent.setup();
    authHooks.setPasswordMutate.mockImplementation((_vars, opts) => opts.onSuccess());
    await reachSetup(user);

    await user.type(screen.getByLabelText('Новый пароль'), 'brand-new-pass');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(authHooks.setPasswordMutate).toHaveBeenCalledWith(
      { payload: { password: 'brand-new-pass' }, setupToken: 'setup-xyz', username: 'nikita' },
      expect.any(Object),
    );
    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it('set-password: короткий пароль (<8) не уходит на сервер (клиентская проверка)', async () => {
    const user = userEvent.setup();
    await reachSetup(user);

    await user.type(screen.getByLabelText('Новый пароль'), 'short');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(authHooks.setPasswordMutate).not.toHaveBeenCalled();
    expect(screen.getByText('Не менее 8 символов')).toBeInTheDocument();
  });

  it('set-password: 422 от сервера → ошибка «Не менее 8 символов»', async () => {
    const user = userEvent.setup();
    authHooks.setPasswordMutate.mockImplementation((_vars, opts) =>
      opts.onError(new ApiError(422, 'unprocessable', 'Недопустимая длина пароля')),
    );
    await reachSetup(user);

    await user.type(screen.getByLabelText('Новый пароль'), 'brand-new-pass');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(screen.getByText('Не менее 8 символов')).toBeInTheDocument();
  });

  it('set-password: 409 password_already_set → баннер «войдите с паролем»', async () => {
    const user = userEvent.setup();
    authHooks.setPasswordMutate.mockImplementation((_vars, opts) =>
      opts.onError(new ApiError(409, 'password_already_set', 'Пароль уже установлен')),
    );
    await reachSetup(user);

    await user.type(screen.getByLabelText('Новый пароль'), 'brand-new-pass');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Пароль уже задан, войдите с паролем');
    // Окно установки закрыто.
    expect(screen.queryByRole('dialog', { name: SETUP_TITLE })).not.toBeInTheDocument();
  });

  it('set-password: 401 (setup-токен просрочен) → возврат к шагу логина с баннером', async () => {
    const user = userEvent.setup();
    authHooks.setPasswordMutate.mockImplementation((_vars, opts) =>
      opts.onError(new ApiError(401, 'unauthorized', 'Требуется авторизация')),
    );
    await reachSetup(user);

    await user.type(screen.getByLabelText('Новый пароль'), 'brand-new-pass');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(screen.getByRole('alert')).toHaveTextContent('Сессия установки истекла, начните заново');
    // Вернулись на шаг ввода идентификатора.
    expect(screen.getByLabelText(IDENTIFIER_LABEL)).toBeInTheDocument();
  });
});

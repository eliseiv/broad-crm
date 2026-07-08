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

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().clearSession();
  });

  it('шаг-1 использует лейбл «Логин или Телеграм» (ADR-025)', () => {
    render(<LoginPage />, { wrapper });
    expect(screen.getByLabelText(IDENTIFIER_LABEL)).toBeInTheDocument();
  });

  it('moves from username step to password step without API request', async () => {
    const user = userEvent.setup();
    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(screen.getByLabelText('Пароль')).toHaveFocus();
    expect(authHooks.mutate).not.toHaveBeenCalled();
  });

  it('пустой пароль на шаге-2 НЕ блокируется — уходит запрос на login (ADR-025)', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation(() => {});

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'nikita');
    await user.click(screen.getByRole('button', { name: /далее/i }));
    // Пароль оставляем пустым (беспарольный пользователь).
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(authHooks.mutate).toHaveBeenCalledWith(
      { username: 'nikita', password: '' },
      expect.any(Object),
    );
  });

  it('submits login on step two and displays generic error', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) => options.onError(new Error('boom')));

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));
    await user.type(screen.getByLabelText('Пароль'), 'bad-password');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(authHooks.mutate).toHaveBeenCalledWith(
      { username: 'admin', password: 'bad-password' },
      expect.any(Object),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Неверный логин или пароль');
  });

  it('redirects to / (permission-aware default) after a successful login', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: false, access_token: 't' }),
    );

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));
    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  it('redirects an already-authenticated user to / on mount', () => {
    useAuthStore.getState().setSession('jwt-token', 'admin');

    render(<LoginPage />, { wrapper });

    expect(navigate).toHaveBeenCalledWith('/', { replace: true });
  });

  // --- «Открытый первый вход»: password_setup_required → «Задайте пароль» (ADR-025) ---

  async function reachSetup(user: ReturnType<typeof userEvent.setup>) {
    authHooks.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ password_setup_required: true, setup_token: 'setup-xyz' }),
    );
    render(<LoginPage />, { wrapper });
    await user.type(screen.getByLabelText(IDENTIFIER_LABEL), 'nikita');
    await user.click(screen.getByRole('button', { name: /далее/i }));
    await user.click(screen.getByRole('button', { name: /войти/i }));
  }

  it('password_setup_required → открывает окно «Задайте пароль»', async () => {
    const user = userEvent.setup();
    await reachSetup(user);

    expect(await screen.findByRole('dialog', { name: 'Задайте пароль' })).toBeInTheDocument();
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
    expect(screen.queryByRole('dialog', { name: 'Задайте пароль' })).not.toBeInTheDocument();
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

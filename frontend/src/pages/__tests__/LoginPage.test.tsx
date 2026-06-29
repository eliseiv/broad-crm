import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LoginPage } from '@/pages/LoginPage';
import { useAuthStore } from '@/store/auth';

const authHooks = vi.hoisted(() => ({
  mutate: vi.fn(),
}));

vi.mock('@/features/auth/hooks', () => ({
  useLogin: () => ({
    mutate: authHooks.mutate,
    isPending: false,
  }),
}));

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

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAuthStore.getState().clearSession();
  });

  it('moves from username step to password step without API request', async () => {
    const user = userEvent.setup();
    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText('Логин'), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));

    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getByLabelText('Пароль')).toHaveFocus();
    expect(authHooks.mutate).not.toHaveBeenCalled();
  });

  it('submits login on step two and displays generic error', async () => {
    const user = userEvent.setup();
    authHooks.mutate.mockImplementation((_payload, options) => options.onError(new Error('boom')));

    render(<LoginPage />, { wrapper });

    await user.type(screen.getByLabelText('Логин'), 'admin');
    await user.click(screen.getByRole('button', { name: /далее/i }));
    await user.type(screen.getByLabelText('Пароль'), 'bad-password');
    await user.click(screen.getByRole('button', { name: /войти/i }));

    expect(authHooks.mutate).toHaveBeenCalledWith(
      { username: 'admin', password: 'bad-password' },
      expect.any(Object),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Неверный логин или пароль');
  });
});

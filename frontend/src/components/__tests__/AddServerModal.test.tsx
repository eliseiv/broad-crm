import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddServerModal } from '@/components/AddServerModal';
import { ApiError } from '@/lib/api';

const mutation = vi.hoisted(() => ({
  mutate: vi.fn(),
  isPending: false,
}));

vi.mock('@/features/servers/hooks', () => ({
  useCreateServer: () => mutation,
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe('AddServerModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutation.isPending = false;
  });

  it('validates required fields and invalid IP', async () => {
    const user = userEvent.setup();
    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите название')).toBeInTheDocument();
    expect(screen.getByText('Укажите IP-адрес')).toBeInTheDocument();
    expect(screen.getByText('Укажите пользователя')).toBeInTheDocument();
    expect(screen.getByText('Укажите пароль')).toBeInTheDocument();

    await user.type(screen.getByLabelText('IP-адрес'), 'not-an-ip');
    expect(screen.getByText('Некорректный IPv4/IPv6-адрес')).toBeInTheDocument();
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it('submits trimmed payload and shows provisioning state on success', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onSuccess({ name: 'Server 01' }),
    );

    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), ' Server 01 ');
    await user.type(screen.getByLabelText('IP-адрес'), '10.0.0.10');
    await user.type(screen.getByLabelText('Пользователь'), ' root ');
    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutation.mutate).toHaveBeenCalledWith(
      { name: 'Server 01', ip: '10.0.0.10', ssh_user: 'root', ssh_password: 'secret' },
      expect.any(Object),
    );
    expect(screen.getByText('Сервер добавлен')).toBeInTheDocument();
    expect(screen.getByText('Установка агента…')).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('Сервер добавлен');
  });

  it('maps 409 and 422 API errors to IP field', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(409, 'server_conflict', 'conflict')),
    );

    render(<AddServerModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'Server 01');
    await user.type(screen.getByLabelText('IP-адрес'), '10.0.0.10');
    await user.type(screen.getByLabelText('Пользователь'), 'root');
    await user.type(screen.getByLabelText('Пароль'), 'secret');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Сервер с таким IP уже добавлен')).toBeInTheDocument();
  });
});

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddProxyModal } from '@/components/AddProxyModal';
import { ApiError } from '@/lib/api';
import type { Proxy } from '@/types/api';

const createMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const updateMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/proxies/hooks', () => ({
  useCreateProxy: () => createMutation,
  useUpdateProxy: () => updateMutation,
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function makeProxy(overrides: Partial<Proxy> = {}): Proxy {
  return {
    id: 'proxy-1',
    name: 'DE Residential',
    proxy_type: 'socks5',
    host: 'proxy.example.com',
    port: 1080,
    username: 'user01',
    has_password: true,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('AddProxyModal — add', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMutation.isPending = false;
  });

  it('validates required fields', async () => {
    const user = userEvent.setup();
    render(<AddProxyModal open onOpenChange={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите название')).toBeInTheDocument();
    expect(screen.getByText('Укажите хост')).toBeInTheDocument();
    expect(screen.getByText('Укажите порт')).toBeInTheDocument();
    expect(createMutation.mutate).not.toHaveBeenCalled();
  });

  it('submits payload without optional username/password when empty', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<AddProxyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), ' DE Residential ');
    await user.type(screen.getByLabelText('Хост'), ' proxy.example.com ');
    await user.type(screen.getByLabelText('Порт'), '1080');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(createMutation.mutate).toHaveBeenCalledTimes(1);
    const payload = createMutation.mutate.mock.calls[0][0];
    // username/password не отправляются, когда поля пустые (04-api.md).
    expect(payload).toEqual({
      name: 'DE Residential',
      proxy_type: 'http',
      host: 'proxy.example.com',
      port: 1080,
    });
    expect(payload).not.toHaveProperty('username');
    expect(payload).not.toHaveProperty('password');
    expect(toast.success).toHaveBeenCalledWith('Прокси добавлен');
  });

  it('includes username/password only when provided', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<AddProxyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'Auth proxy');
    await user.type(screen.getByLabelText('Хост'), 'host');
    await user.type(screen.getByLabelText('Порт'), '3128');
    await user.type(screen.getByLabelText('Логин'), 'user01');
    await user.type(screen.getByLabelText('Пароль'), 's3cr3t');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = createMutation.mutate.mock.calls[0][0];
    expect(payload.username).toBe('user01');
    expect(payload.password).toBe('s3cr3t');
  });

  it('maps 422 API error to port field', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'unprocessable', 'Недопустимый порт')),
    );
    render(<AddProxyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'P');
    await user.type(screen.getByLabelText('Хост'), 'host');
    await user.type(screen.getByLabelText('Порт'), '8080');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Недопустимый тип или порт')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Недопустимый тип или порт прокси');
  });

  it('maps 400 details to per-field errors', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(
        new ApiError(400, 'validation_error', 'Невалидные данные запроса', [
          { field: 'host', message: 'Не более 255 символов' },
        ]),
      ),
    );
    render(<AddProxyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'P');
    await user.type(screen.getByLabelText('Хост'), 'host');
    await user.type(screen.getByLabelText('Порт'), '8080');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Не более 255 символов')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Проверьте корректность полей');
  });
});

describe('AddProxyModal — edit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateMutation.isPending = false;
  });

  it('prefills fields, keeps password empty, and sends nothing changed → closes without request', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<AddProxyModal mode="edit" proxy={makeProxy()} open onOpenChange={onOpenChange} />);

    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Название') as HTMLInputElement).value).toBe('DE Residential');
    expect((dialog.getByLabelText('Тип') as HTMLSelectElement).value).toBe('socks5');
    expect((dialog.getByLabelText('Хост') as HTMLInputElement).value).toBe('proxy.example.com');
    expect((dialog.getByLabelText('Порт') as HTMLInputElement).value).toBe('1080');
    expect((dialog.getByLabelText('Логин') as HTMLInputElement).value).toBe('user01');
    // Пароль ПУСТОЙ (секрет не префилится).
    expect((dialog.getByLabelText('Пароль') as HTMLInputElement).value).toBe('');

    // Ничего не изменено → закрытие без запроса.
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(updateMutation.mutate).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('does not send password when left empty on edit', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddProxyModal mode="edit" proxy={makeProxy()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Название'));
    await user.type(dialog.getByLabelText('Название'), 'Renamed');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    expect(payload).toEqual({ name: 'Renamed' });
    expect(payload).not.toHaveProperty('password');
  });

  it('sends username as null when cleared on edit', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddProxyModal mode="edit" proxy={makeProxy()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Логин'));
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    // "" → убрать логин (null), не пустая строка (04-api.md семантика username).
    expect(payload.username).toBeNull();
  });

  it('sends changed host/port/type (connection-related → re-check)', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddProxyModal mode="edit" proxy={makeProxy()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Хост'));
    await user.type(dialog.getByLabelText('Хост'), 'new.example.com');
    await user.clear(dialog.getByLabelText('Порт'));
    await user.type(dialog.getByLabelText('Порт'), '3128');
    await user.selectOptions(dialog.getByLabelText('Тип'), 'http');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    expect(payload).toEqual({ host: 'new.example.com', port: 3128, proxy_type: 'http' });
  });

  it('sends new password when entered on edit', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddProxyModal mode="edit" proxy={makeProxy()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.type(dialog.getByLabelText('Пароль'), 'n3w-pass');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    expect(payload.password).toBe('n3w-pass');
  });
});

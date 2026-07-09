import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddBackendModal } from '@/components/AddBackendModal';
import { ApiError } from '@/lib/api';
import type { Backend } from '@/types/api';

const createMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
const updateMutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/backends/hooks', () => ({
  useCreateBackend: () => createMutation,
  useUpdateBackend: () => updateMutation,
}));

// Секция «Информация» (ADR-040) подтягивает опции серверов/ключей через useServers/useAiKeys —
// мокаем их пустыми списками, чтобы модалка рендерилась без QueryClientProvider.
vi.mock('@/features/servers/hooks', () => ({
  useServers: () => ({ data: { items: [] } }),
}));
vi.mock('@/features/ai-keys/hooks', () => ({
  useAiKeys: () => ({ data: { items: [] } }),
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

function makeBackend(overrides: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'api.example.com',
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: false,
    has_admin_api_key: false,
    git: null,
    note: null,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('AddBackendModal — add', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createMutation.isPending = false;
  });

  it('validates required fields', async () => {
    const user = userEvent.setup();
    render(<AddBackendModal open onOpenChange={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите код')).toBeInTheDocument();
    expect(screen.getByText('Укажите название')).toBeInTheDocument();
    expect(screen.getByText('Укажите домен')).toBeInTheDocument();
    expect(createMutation.mutate).not.toHaveBeenCalled();
  });

  it('submits trimmed code/name/domain', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<AddBackendModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Код'), ' api-eu ');
    await user.type(screen.getByLabelText('Название'), ' API EU ');
    await user.type(screen.getByLabelText('Домен'), ' api.example.com ');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(createMutation.mutate).toHaveBeenCalledTimes(1);
    const payload = createMutation.mutate.mock.calls[0][0];
    expect(payload).toEqual({ code: 'api-eu', name: 'API EU', domain: 'api.example.com' });
    expect(toast.success).toHaveBeenCalledWith('Бэк добавлен');
  });

  it('maps 409 backend_code_taken to the code field', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(409, 'backend_code_taken', 'Бэк с таким кодом уже существует')),
    );
    render(<AddBackendModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Код'), 'api-eu');
    await user.type(screen.getByLabelText('Название'), 'API EU');
    await user.type(screen.getByLabelText('Домен'), 'api.example.com');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // 409 → пофилдово под «Код».
    expect(screen.getByText('Код занят')).toBeInTheDocument();
  });

  it('maps 422 unprocessable to the domain field', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'unprocessable', 'Невалидный формат домена')),
    );
    render(<AddBackendModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Код'), 'api-eu');
    await user.type(screen.getByLabelText('Название'), 'API EU');
    await user.type(screen.getByLabelText('Домен'), 'bad domain');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    // 422 → пофилдово под «Домен» + toast.
    expect(screen.getByText('Некорректный домен')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Некорректный домен');
  });

  it('maps 400 details to per-field errors', async () => {
    const user = userEvent.setup();
    createMutation.mutate.mockImplementation((_payload, options) =>
      options.onError(
        new ApiError(400, 'validation_error', 'Невалидные данные запроса', [
          { field: 'domain', message: 'Не более 255 символов' },
        ]),
      ),
    );
    render(<AddBackendModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Код'), 'api-eu');
    await user.type(screen.getByLabelText('Название'), 'API EU');
    await user.type(screen.getByLabelText('Домен'), 'api.example.com');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Не более 255 символов')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Проверьте корректность полей');
  });
});

describe('AddBackendModal — edit', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateMutation.isPending = false;
  });

  it('prefills fields and closes without request when nothing changed', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <AddBackendModal mode="edit" backend={makeBackend()} open onOpenChange={onOpenChange} />,
    );

    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Код') as HTMLInputElement).value).toBe('api-eu');
    expect((dialog.getByLabelText('Название') as HTMLInputElement).value).toBe('API EU');
    expect((dialog.getByLabelText('Домен') as HTMLInputElement).value).toBe('api.example.com');

    // Ничего не изменено → закрытие без запроса.
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));
    expect(updateMutation.mutate).not.toHaveBeenCalled();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('sends only the changed name field', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddBackendModal mode="edit" backend={makeBackend()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Название'));
    await user.type(dialog.getByLabelText('Название'), 'Renamed');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    // Отправляются ТОЛЬКО изменённые поля (04-api.md exclude_unset).
    expect(payload).toEqual({ name: 'Renamed' });
    expect(payload).not.toHaveProperty('code');
    expect(payload).not.toHaveProperty('domain');
    expect(toast.success).toHaveBeenCalledWith('Бэк обновлён');
  });

  it('sends only the changed domain field (triggers re-check)', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) => options.onSuccess?.());
    render(<AddBackendModal mode="edit" backend={makeBackend()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Домен'));
    await user.type(dialog.getByLabelText('Домен'), 'new.example.com');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    const payload = updateMutation.mutate.mock.calls[0][0];
    expect(payload).toEqual({ domain: 'new.example.com' });
  });

  it('maps 409 backend_code_taken to the code field on edit', async () => {
    const user = userEvent.setup();
    updateMutation.mutate.mockImplementation((_payload, options) =>
      options.onError?.(new ApiError(409, 'backend_code_taken', 'занят')),
    );
    render(<AddBackendModal mode="edit" backend={makeBackend()} open onOpenChange={vi.fn()} />);

    const dialog = within(screen.getByRole('dialog'));
    await user.clear(dialog.getByLabelText('Код'));
    await user.type(dialog.getByLabelText('Код'), 'api-us');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(screen.getByText('Код занят')).toBeInTheDocument();
  });
});

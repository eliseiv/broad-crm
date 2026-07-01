import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { toast } from 'sonner';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddAiKeyModal } from '@/components/AddAiKeyModal';
import { ApiError } from '@/lib/api';

const mutation = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));

vi.mock('@/features/ai-keys/hooks', () => ({
  useCreateAiKey: () => mutation,
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe('AddAiKeyModal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mutation.isPending = false;
  });

  it('validates required name and key fields', async () => {
    const user = userEvent.setup();
    render(<AddAiKeyModal open onOpenChange={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите название')).toBeInTheDocument();
    expect(screen.getByText('Укажите ключ')).toBeInTheDocument();
    expect(mutation.mutate).not.toHaveBeenCalled();
  });

  it('renders a provider Select and submits the chosen provider', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<AddAiKeyModal open onOpenChange={vi.fn()} />);

    const select = screen.getByLabelText('Провайдер');
    expect(select.tagName).toBe('SELECT');
    await user.selectOptions(select, 'anthropic');

    await user.type(screen.getByLabelText('Название'), 'Claude Prod');
    await user.type(screen.getByLabelText('Ключ'), 'sk-ant-secret-key-xyz');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutation.mutate).toHaveBeenCalledWith(
      { name: 'Claude Prod', provider: 'anthropic', key: 'sk-ant-secret-key-xyz' },
      expect.any(Object),
    );
  });

  it('submits the payload with a `key` field (not api_key) and trims values', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) => options.onSuccess());
    render(<AddAiKeyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), '  OpenAI Prod  ');
    await user.type(screen.getByLabelText('Ключ'), '  sk-proj-abc123  ');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = mutation.mutate.mock.calls[0][0];
    expect(payload).toHaveProperty('key', 'sk-proj-abc123');
    expect(payload).not.toHaveProperty('api_key');
    expect(payload.name).toBe('OpenAI Prod');
    expect(payload.provider).toBe('openai');
    // Успех показывает состояние проверки ключа.
    expect(toast.success).toHaveBeenCalledWith('Ключ добавлен');
    expect(screen.getByText('Проверка ключа…')).toBeInTheDocument();
  });

  it('toggles key visibility between password and text', async () => {
    const user = userEvent.setup();
    render(<AddAiKeyModal open onOpenChange={vi.fn()} />);

    const keyInput = screen.getByLabelText('Ключ') as HTMLInputElement;
    expect(keyInput.type).toBe('password');

    await user.click(screen.getByRole('button', { name: 'Показать ключ' }));
    expect(keyInput.type).toBe('text');

    await user.click(screen.getByRole('button', { name: 'Скрыть ключ' }));
    expect(keyInput.type).toBe('password');
  });

  it('maps a 422 API error to the provider field', async () => {
    const user = userEvent.setup();
    mutation.mutate.mockImplementation((_payload, options) =>
      options.onError(new ApiError(422, 'unprocessable', 'Недопустимый провайдер')),
    );
    render(<AddAiKeyModal open onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Название'), 'Bad');
    await user.type(screen.getByLabelText('Ключ'), 'some-key-123');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Недопустимый провайдер')).toBeInTheDocument();
    expect(toast.error).toHaveBeenCalledWith('Недопустимый провайдер');
  });
});

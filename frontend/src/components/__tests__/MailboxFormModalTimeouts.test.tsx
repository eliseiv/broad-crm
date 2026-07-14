import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { ApiError } from '@/lib/api';
import { MAIL_CONNECTION_PROGRESS_HINT } from '@/features/mail/errorMessages';
import type { MailMailbox } from '@/types/api';

/**
 * ADR-053 §4 (TD-059) — форма ящика на ДОЛГИХ вызовах (`test`/`create`/`patch`, до 105 с):
 *  - прогресс-состояние (спиннер + подпись + disabled) на КАЖДОМ из трёх;
 *  - истинная причина отказа (422-семейство, 504) — В ФОРМЕ, а не тостом «сервис недоступен»;
 *  - «сервис недоступен» — ТОЛЬКО за `502 mail_unavailable` (различение по `error.code`);
 *  - закрытие во время проверки → abort запроса, тоста нет; во время сохранения — заблокировано;
 *  - `connectionError` гаснет при правке полей подключения.
 */

// Управляемое состояние мутаций: тест диктует pending-флаги и колбэк ошибки.
const state = vi.hoisted(() => ({
  testPending: false,
  submitPending: false,
  testError: null as unknown,
  createError: null as unknown,
  updateError: null as unknown,
  lastTestSignal: undefined as AbortSignal | undefined,
}));

const spies = vi.hoisted(() => ({ test: vi.fn(), create: vi.fn(), update: vi.fn() }));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) => page === 'mail' && action === 'create',
  useSeesAllMailTeams: () => true,
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({ data: { items: [{ id: 'team-3', name: 'Продажи' }] } }),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: undefined }) }));

vi.mock('@/features/mail/hooks', () => ({
  mailMailboxesKey: ['mail', 'mailboxes'],
  useTestMailbox: () => ({
    isPending: state.testPending,
    mutate: (
      vars: { payload: unknown; signal?: AbortSignal },
      opts?: { onSuccess?: (r: unknown) => void; onError?: (e: unknown) => void },
    ) => {
      spies.test(vars);
      state.lastTestSignal = vars.signal;
      if (state.testError !== null) opts?.onError?.(state.testError);
      else opts?.onSuccess?.({ imap_ok: true, smtp_ok: true });
    },
  }),
  useCreateMailbox: () => ({
    isPending: state.submitPending,
    mutate: (
      payload: unknown,
      opts?: { onSuccess?: () => void; onError?: (e: unknown) => void },
    ) => {
      spies.create(payload);
      if (state.createError !== null) opts?.onError?.(state.createError);
      else opts?.onSuccess?.();
    },
  }),
  useUpdateMailbox: () => ({
    isPending: state.submitPending,
    mutate: (vars: unknown, opts?: { onSuccess?: () => void; onError?: (e: unknown) => void }) => {
      spies.update(vars);
      if (state.updateError !== null) opts?.onError?.(state.updateError);
      else opts?.onSuccess?.();
    },
  }),
  useMailboxOAuthAuthorize: () => ({ isPending: false, mutate: vi.fn() }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

beforeEach(() => {
  state.testPending = false;
  state.submitPending = false;
  state.testError = null;
  state.createError = null;
  state.updateError = null;
  state.lastTestSignal = undefined;
});

afterEach(() => {
  vi.clearAllMocks();
});

function mailbox(): MailMailbox {
  return {
    id: 42,
    email: 'box@example.com',
    number: '5108',
    app_name: 'Klyro',
    display_name: '5108 Klyro',
    team_id: null,
    is_active: true,
    last_synced_at: null,
    last_sync_error: null,
    consecutive_failures: 0,
  };
}

/** Заполняет минимум, при котором «Проверить соединение» активна (порты префилены). */
async function fillConnection(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Адрес почты'), 'box@example.com');
  await user.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
  await user.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
  await user.type(screen.getByLabelText('Пароль (IMAP)'), 'app-password');
}

describe('MailboxFormModal — прогресс-состояние долгого вызова (ADR-053 §4)', () => {
  it('во время ПРОВЕРКИ: спиннер + подпись + кнопки disabled', () => {
    state.testPending = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    const progress = screen.getByRole('status');
    expect(progress).toHaveTextContent(MAIL_CONNECTION_PROGRESS_HINT);
    expect(screen.getByRole('button', { name: /Добавить/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Проверить соединение/ })).toBeDisabled();
  });

  it('во время СОЗДАНИЯ (create): спиннер + подпись + disabled, закрытие заблокировано', () => {
    state.submitPending = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.getByRole('status')).toHaveTextContent(MAIL_CONNECTION_PROGRESS_HINT);
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Проверить соединение/ })).toBeDisabled();
  });

  it('во время ПРАВКИ (patch): спиннер + подпись + disabled', () => {
    state.submitPending = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={mailbox()} />);

    expect(screen.getByRole('status')).toHaveTextContent(MAIL_CONNECTION_PROGRESS_HINT);
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
  });

  it('в покое прогресс-строки нет', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('закрытие ЗАБЛОКИРОВАНО во время сохранения (запрос уже пишет состояние)', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    state.submitPending = true;
    render(<MailboxFormModal open onOpenChange={onOpenChange} mode="add" />);

    await user.keyboard('{Escape}');

    expect(onOpenChange).not.toHaveBeenCalled();
  });
});

describe('MailboxFormModal — причина отказа в форме, а не тостом (ADR-053 §2)', () => {
  it.each([
    ['mail_imap_failed', 'Не удалось подключиться к IMAP. Проверьте хост, порт, SSL и пароль.'],
    [
      'mail_smtp_failed',
      'Не удалось подключиться к SMTP. Проверьте хост, порт, SSL/STARTTLS и пароль.',
    ],
    ['mail_invalid_host', 'Недопустимый адрес сервера: приватные и локальные хосты запрещены.'],
  ])('422 %s на проверке → сообщение В ФОРМЕ, тоста нет', async (code, expected) => {
    const user = userEvent.setup();
    state.testError = new ApiError(422, code, 'aggregator message');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(expected);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('504 mail_timeout на проверке → текст действия «test» в форме', async () => {
    const user = userEvent.setup();
    state.testError = new ApiError(504, 'mail_timeout', 'x');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Проверка не завершилась за отведённое время: почтовый сервер не ответил. Проверьте хост и порт.',
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('504 mail_timeout на создании → текст действия «save» в форме', async () => {
    const user = userEvent.setup();
    state.createError = new ApiError(504, 'mail_timeout', 'x');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Операция не завершилась вовремя. Состояние ящика могло измениться — обновите список.',
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('422 mail_imap_failed на правке (patch) → сообщение в форме', async () => {
    const user = userEvent.setup();
    state.updateError = new ApiError(422, 'mail_imap_failed', 'x');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={mailbox()} />);

    await user.type(screen.getByLabelText('Пароль (IMAP)'), 'new-app-password');
    await user.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Не удалось подключиться к IMAP. Проверьте хост, порт, SSL и пароль.',
    );
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('«сервис недоступен» — ТОЛЬКО за 502 mail_unavailable (различение по error.code)', async () => {
    const user = userEvent.setup();
    state.testError = new ApiError(502, 'mail_unavailable', 'x');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Почтовый сервис временно недоступен'),
    );
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('прочий 502 (другой code) НЕ выдаётся за «сервис недоступен»', async () => {
    const user = userEvent.setup();
    state.testError = new ApiError(502, 'some_future_code', 'Сообщение backend’а');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Сообщение backend’а'));
    expect(toast.error).not.toHaveBeenCalledWith('Почтовый сервис временно недоступен');
  });

  it('сообщение об отказе гаснет при правке полей подключения', async () => {
    const user = userEvent.setup();
    state.testError = new ApiError(422, 'mail_imap_failed', 'x');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));
    expect(await screen.findByRole('alert')).toBeInTheDocument();

    // Правка ЛЮБОГО параметра подключения — сообщение относилось к ПРЕЖНИМ значениям.
    await user.type(screen.getByLabelText('IMAP-хост'), 'x');

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });
});

describe('MailboxFormModal — abort проверки, без клиентского таймаута (ADR-053 §4)', () => {
  it('закрытие формы во время проверки обрывает запрос по signal', async () => {
    const user = userEvent.setup();
    state.testPending = true; // запрос «висит»
    const { rerender } = render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    // Кнопка disabled на pending — стартуем проверку из непендингового состояния.
    state.testPending = false;
    rerender(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    const signal = state.lastTestSignal;
    expect(signal).toBeInstanceOf(AbortSignal);
    expect(signal?.aborted).toBe(false);

    // Закрытие формы размонтирует диалог (ремоунт по ключу с `open`) → abort.
    rerender(<MailboxFormModal open={false} onOpenChange={vi.fn()} mode="add" />);

    await waitFor(() => expect(signal?.aborted).toBe(true));
  });

  it('AbortError пользователя — не ошибка: ни тоста, ни сообщения в форме', async () => {
    const user = userEvent.setup();
    state.testError = new DOMException('aborted', 'AbortError');
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await fillConnection(user);
    await user.click(screen.getByRole('button', { name: /Проверить соединение/ }));

    expect(toast.error).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

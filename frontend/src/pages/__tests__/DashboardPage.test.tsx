import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { DashboardPage } from '@/pages/DashboardPage';
import { ApiError } from '@/lib/api';
import type { AiKey, MailMailbox, Server } from '@/types/api';

// Навигация по клику — spy на useNavigate (клик по карточке = переход в раздел, ADR-017).
const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigate };
});

// Каждая карточка сама тянет свой list-эндпоинт (клиентская агрегация, без backend-агрегатора).
const mail = vi.hoisted(() => ({ value: null as unknown }));
const servers = vi.hoisted(() => ({ value: null as unknown }));
const aiKeys = vi.hoisted(() => ({ value: null as unknown }));

vi.mock('@/features/mail/hooks', () => ({ useMailMailboxes: () => mail.value }));
vi.mock('@/features/servers/hooks', () => ({ useServers: () => servers.value }));
vi.mock('@/features/ai-keys/hooks', () => ({ useAiKeys: () => aiKeys.value }));

interface QueryLike {
  data: unknown;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: ReturnType<typeof vi.fn>;
  isFetching: boolean;
}

function queryResult(overrides: Partial<QueryLike> = {}): QueryLike {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    isFetching: false,
    ...overrides,
  };
}

function mailbox(id: number, isActive: boolean): MailMailbox {
  return {
    id,
    email: `box${id}@postapp.store`,
    display_name: null,
    group_id: null,
    is_active: isActive,
  };
}

function server(id: string, online: boolean): Server {
  return {
    id,
    name: `Server ${id}`,
    ip: '10.0.0.1',
    exporter_port: 9100,
    provision_status: 'online',
    position: 0,
    online,
    uptime_seconds: null,
    last_updated: null,
    metrics: null,
  };
}

function aiKey(id: string, status: AiKey['check_status']): AiKey {
  return {
    id,
    name: `Key ${id}`,
    provider: 'openai',
    key_masked: 'sk-p…bA3T',
    check_status: status,
    error_message: null,
    position: 0,
    last_checked_at: null,
    created_at: '2026-07-02T09:15:00Z',
  };
}

/** Успешные ответы по умолчанию, чтобы карточки не мешали друг другу в изоляции. */
function setAllSuccess(): void {
  mail.value = queryResult({ data: { mailboxes: [] } });
  servers.value = queryResult({ data: { items: [] } });
  aiKeys.value = queryResult({ data: { items: [] } });
}

function card(title: string): HTMLElement {
  return screen.getByRole('button', { name: new RegExp(`${title} — открыть раздел`) });
}

describe('DashboardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAllSuccess();
  });

  it('renders three section cards', () => {
    render(<DashboardPage />);
    expect(card('Почты')).toBeInTheDocument();
    expect(card('Серверы')).toBeInTheDocument();
    expect(card('ИИ - ключи')).toBeInTheDocument();
  });

  it('client-side counts active/inactive mailboxes by is_active', () => {
    mail.value = queryResult({
      data: { mailboxes: [mailbox(1, true), mailbox(2, false), mailbox(3, true)] },
    });
    render(<DashboardPage />);

    const c = within(card('Почты'));
    expect(c.getByText('Активные')).toBeInTheDocument();
    expect(c.getByText('2')).toBeInTheDocument(); // is_active=true
    expect(c.getByText('Неактивные')).toBeInTheDocument();
    expect(c.getByText('1')).toBeInTheDocument(); // is_active=false
  });

  it('client-side counts online/offline servers by online flag', () => {
    servers.value = queryResult({
      data: { items: [server('a', true), server('b', true), server('c', false)] },
    });
    render(<DashboardPage />);

    const c = within(card('Серверы'));
    expect(c.getByText('В сети')).toBeInTheDocument();
    expect(c.getByText('2')).toBeInTheDocument();
    expect(c.getByText('Не в сети')).toBeInTheDocument();
    expect(c.getByText('1')).toBeInTheDocument();
  });

  it('client-side counts ai-keys by check_status incl. optional Проверяется', () => {
    aiKeys.value = queryResult({
      data: {
        items: [
          aiKey('1', 'working'),
          aiKey('2', 'working'),
          aiKey('3', 'error'),
          aiKey('4', 'pending'),
          aiKey('5', 'pending'),
          aiKey('6', 'pending'),
        ],
      },
    });
    render(<DashboardPage />);

    // Значения различны (2/1/3), чтобы каждую группу проверить точечно.
    const c = within(card('ИИ - ключи'));
    expect(c.getByText('Работает')).toBeInTheDocument();
    expect(c.getByText('2')).toBeInTheDocument(); // working
    expect(c.getByText('Не работает')).toBeInTheDocument();
    expect(c.getByText('1')).toBeInTheDocument(); // error
    // Проверяется — опциональный нейтральный счётчик, показывается только при pending>0.
    expect(c.getByText('Проверяется')).toBeInTheDocument();
    expect(c.getByText('3')).toBeInTheDocument(); // pending
  });

  it('does not show Проверяется when no key is pending', () => {
    aiKeys.value = queryResult({ data: { items: [aiKey('1', 'working')] } });
    render(<DashboardPage />);

    expect(within(card('ИИ - ключи')).queryByText('Проверяется')).not.toBeInTheDocument();
  });

  it('empty source renders zero counters, card stays clickable', async () => {
    render(<DashboardPage />); // setAllSuccess → пустые списки
    const c = within(card('Серверы'));
    // 0 / 0 — обе группы показывают ноль.
    expect(c.getAllByText('0')).toHaveLength(2);

    await userEvent.setup().click(card('Серверы'));
    expect(navigate).toHaveBeenCalledWith('/servers');
  });

  it('shows loading skeleton (no counters) while a source is loading', () => {
    servers.value = queryResult({ isLoading: true });
    render(<DashboardPage />);

    const c = within(card('Серверы'));
    expect(c.queryByText('В сети')).not.toBeInTheDocument();
    // Скелетон-плейсхолдеры вместо счётчиков.
    expect(card('Серверы').querySelector('.animate-pulse')).not.toBeNull();
  });

  it('shows error state with retry when a source fails', () => {
    servers.value = queryResult({ isError: true, error: new Error('boom') });
    render(<DashboardPage />);

    const c = within(card('Серверы'));
    expect(c.getByText('Не удалось загрузить')).toBeInTheDocument();
    expect(c.getByRole('button', { name: /Повторить/ })).toBeInTheDocument();
  });

  it('mail card shows "Сервис почт не настроен" on 503 instead of counters', () => {
    mail.value = queryResult({
      isError: true,
      error: new ApiError(503, 'mail_not_configured', 'x'),
    });
    render(<DashboardPage />);

    const c = within(card('Почты'));
    expect(c.getByText('Сервис почт не настроен')).toBeInTheDocument();
    expect(c.queryByText('Не удалось загрузить')).not.toBeInTheDocument();
    expect(c.queryByRole('button', { name: /Повторить/ })).not.toBeInTheDocument();
  });

  it('navigates to the section on card click', async () => {
    render(<DashboardPage />);
    const user = userEvent.setup();

    await user.click(card('Почты'));
    expect(navigate).toHaveBeenCalledWith('/mail');

    await user.click(card('ИИ - ключи'));
    expect(navigate).toHaveBeenCalledWith('/ai-keys');
  });

  it('navigates on Enter and Space keydown (a11y)', () => {
    render(<DashboardPage />);

    fireEvent.keyDown(card('Серверы'), { key: 'Enter' });
    expect(navigate).toHaveBeenCalledWith('/servers');

    navigate.mockClear();
    fireEvent.keyDown(card('Серверы'), { key: ' ' });
    expect(navigate).toHaveBeenCalledWith('/servers');
  });

  it('retry button uses stopPropagation and does not navigate', async () => {
    const refetch = vi.fn();
    servers.value = queryResult({ isError: true, error: new Error('boom'), refetch });
    render(<DashboardPage />);

    await userEvent
      .setup()
      .click(within(card('Серверы')).getByRole('button', { name: /Повторить/ }));

    expect(refetch).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });
});

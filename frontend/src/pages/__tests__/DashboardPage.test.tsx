import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DashboardPage } from '@/pages/DashboardPage';
import {
  INSUFFICIENT_PERMISSIONS_TITLE,
  NO_SECTION_ACCESS_HINT,
} from '@/components/InsufficientPermissions';
import { ApiError } from '@/lib/api';
import { loginAs, loginSuperadmin, logout } from '@/test/authTestUtils';
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
    ssh_user: 'root',
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
    // Контент дашборда доступен только с `dashboard:view` (page-level view-guard,
    // ADR-021 §6). Существующие кейсы контента прогоняем как супер-админ.
    loginSuperadmin();
    setAllSuccess();
  });

  afterEach(() => logout());

  it('renders three section cards', () => {
    render(<DashboardPage />);
    expect(card('Почты')).toBeInTheDocument();
    expect(card('Серверы')).toBeInTheDocument();
    expect(card('ИИ - ключи')).toBeInTheDocument();
  });

  it('renders the page-scoped «Недостаточно прав» stub when the user lacks dashboard:view', () => {
    // Обычный пользователь с доступом к другому разделу, но без `dashboard:view`.
    loginAs({ isSuperadmin: false, role: 'Оператор', permissions: { mail: ['view'] } });
    render(<DashboardPage />);

    // Page-scoped заглушка (не «нет ни одного раздела») вместо контента, ADR-021 §6.
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.getByText(NO_SECTION_ACCESS_HINT)).toBeInTheDocument();
    // Карточки/данные разделов скрыты: guard короткозамыкает до их рендера.
    expect(
      screen.queryByRole('button', { name: /Почты — открыть раздел/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Серверы — открыть раздел/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /ИИ - ключи — открыть раздел/ }),
    ).not.toBeInTheDocument();
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

  it('empty source renders only the active zero counter, card stays clickable', async () => {
    render(<DashboardPage />); // setAllSuccess → пустые списки
    const c = within(card('Серверы'));
    // Активный счётчик виден всегда и показывает 0; неактивный при 0 скрыт (нормативно).
    expect(c.getByText('В сети')).toBeInTheDocument();
    expect(c.queryByText('Не в сети')).not.toBeInTheDocument();
    // Единственный отображаемый ноль — активного счётчика.
    expect(c.getAllByText('0')).toHaveLength(1);

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

  // — Скрытие нулевых вторичных счётчиков + центрирование (08-design-system.md
  //   «Блоки (Этап 1)» / «Статус-строка карточки», уточнение 2026-07-06).

  it('hides zero secondary counter — servers 4/0 shows only В сети', () => {
    servers.value = queryResult({
      data: {
        items: [server('a', true), server('b', true), server('c', true), server('d', true)],
      },
    });
    render(<DashboardPage />);

    const c = within(card('Серверы'));
    expect(c.getByText('В сети')).toBeInTheDocument();
    expect(c.getByText('4')).toBeInTheDocument();
    // Не в сети = 0 → вторичный счётчик не рендерится.
    expect(c.queryByText('Не в сети')).not.toBeInTheDocument();
  });

  it('hides zero secondary counters — ai-keys 1/0/0 shows only Работает', () => {
    aiKeys.value = queryResult({ data: { items: [aiKey('1', 'working')] } });
    render(<DashboardPage />);

    const c = within(card('ИИ - ключи'));
    expect(c.getByText('Работает')).toBeInTheDocument();
    expect(c.getByText('1')).toBeInTheDocument();
    // Не работает = 0 и Проверяется = 0 → оба вторичных счётчика скрыты.
    expect(c.queryByText('Не работает')).not.toBeInTheDocument();
    expect(c.queryByText('Проверяется')).not.toBeInTheDocument();
  });

  it('keeps both counters when active and inactive are > 0 — mail 124/9', () => {
    const active = Array.from({ length: 124 }, (_, i) => mailbox(i + 1, true));
    const inactive = Array.from({ length: 9 }, (_, i) => mailbox(1000 + i, false));
    mail.value = queryResult({ data: { mailboxes: [...active, ...inactive] } });
    render(<DashboardPage />);

    const c = within(card('Почты'));
    expect(c.getByText('Активные')).toBeInTheDocument();
    expect(c.getByText('124')).toBeInTheDocument();
    expect(c.getByText('Неактивные')).toBeInTheDocument();
    expect(c.getByText('9')).toBeInTheDocument();
  });

  it('active green counter renders always, including 0 (ai-keys empty)', () => {
    render(<DashboardPage />); // aiKeys → пустой список
    const c = within(card('ИИ - ключи'));
    expect(c.getByText('Работает')).toBeInTheDocument();
    expect(c.getByText('0')).toBeInTheDocument();
    expect(c.queryByText('Не работает')).not.toBeInTheDocument();
    expect(c.queryByText('Проверяется')).not.toBeInTheDocument();
  });

  it('renders secondary counters with correct tones when > 0', () => {
    mail.value = queryResult({ data: { mailboxes: [mailbox(1, true), mailbox(2, false)] } });
    servers.value = queryResult({ data: { items: [server('a', true), server('b', false)] } });
    aiKeys.value = queryResult({
      data: { items: [aiKey('1', 'working'), aiKey('2', 'error'), aiKey('3', 'pending')] },
    });
    render(<DashboardPage />);

    const mailC = within(card('Почты'));
    expect(mailC.getByText('Активные').closest('.text-status-green')).not.toBeNull();
    expect(mailC.getByText('Неактивные').closest('.text-status-red')).not.toBeNull();

    const srvC = within(card('Серверы'));
    expect(srvC.getByText('В сети').closest('.text-status-green')).not.toBeNull();
    expect(srvC.getByText('Не в сети').closest('.text-status-red')).not.toBeNull();

    const aiC = within(card('ИИ - ключи'));
    expect(aiC.getByText('Работает').closest('.text-status-green')).not.toBeNull();
    expect(aiC.getByText('Не работает').closest('.text-status-red')).not.toBeNull();
    // «Проверяется» — семантически нейтральный тон.
    expect(aiC.getByText('Проверяется').closest('.text-text-secondary')).not.toBeNull();
  });

  it('status row is horizontally centered (justify-center) in both scenarios', () => {
    // Сценарий «только активный» (всё зелёное): серверы 2/0.
    servers.value = queryResult({
      data: { items: [server('a', true), server('b', true)] },
    });
    // Сценарий «активный + неактивный»: почты 2/1.
    mail.value = queryResult({
      data: { mailboxes: [mailbox(1, true), mailbox(2, true), mailbox(3, false)] },
    });
    render(<DashboardPage />);

    // Только активный счётчик — строка центрирована.
    expect(within(card('Серверы')).getByText('В сети').closest('.justify-center')).not.toBeNull();

    // Группа активный + неактивный — центрирована как группа.
    const mailC = within(card('Почты'));
    expect(mailC.getByText('Активные').closest('.justify-center')).not.toBeNull();
    expect(mailC.getByText('Неактивные').closest('.justify-center')).not.toBeNull();
  });
});

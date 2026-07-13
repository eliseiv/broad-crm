import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxesTab } from '@/components/MailboxesTab';
import { loginAs, logout } from '@/test/authTestUtils';
import type { MailMailbox } from '@/types/api';

/**
 * Вкладка «Почты» — КЛИЕНТСКИЕ поиск и фильтр по команде (ADR-050 §1). Каталог ящиков
 * приходит ЦЕЛИКОМ (`GET /api/mail/mailboxes` без пагинации), поэтому серверных параметров
 * `q`/`team_id` нет — backend по этому фиксу не менялся.
 *
 * Нормы: поиск по ТРЁМ полям (`number`, `app_name`, `email`), подстрока регистронезависимо;
 * `display_name` в поиск НЕ входит; фильтр «Команда» рендерится ТОЛЬКО при
 * `sees_all_mail_teams === true` (ADR-036/ADR-050 §1.2); фильтры комбинируются (AND, §1.3);
 * пустой результат активного поиска/фильтра → «Ничего не найдено» (≠ «Почт пока нет»).
 */

const mailboxesQuery = vi.hoisted(() => ({
  value: {
    data: undefined as { mailboxes: MailMailbox[] } | undefined,
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null as unknown,
    refetch: vi.fn(),
  },
}));
const teamsQuery = vi.hoisted(() => ({
  value: { data: { items: [{ id: 'team-3', name: 'Продажи' }] } } as unknown,
}));

vi.mock('@/features/mail/hooks', () => ({
  useMailboxesManage: () => mailboxesQuery.value,
  // Мутации строки ящика — no-op (проверяем тулбар и выборку строк, не запись).
  useUpdateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteMailbox: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/features/teams/hooks', () => ({ useTeams: () => teamsQuery.value }));
vi.mock('@/components/MailboxFormModal', () => ({ MailboxFormModal: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function mailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 1,
    email: 'inbox@postapp.store',
    number: '5108',
    app_name: 'Klyro Forge',
    display_name: '5108 Klyro Forge',
    team_id: null,
    is_active: true,
    last_synced_at: '2026-07-02T09:15:00Z',
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

/** Три ящика: по номеру / по приложению / по адресу — каждый находится своим полем. */
function catalog(): MailMailbox[] {
  return [
    mailbox({ id: 1, number: '5108', app_name: 'Klyro Forge', email: 'alpha@postapp.store' }),
    mailbox({
      id: 2,
      number: '7011',
      app_name: 'Nova Ledger',
      email: 'beta@postapp.store',
      team_id: 'team-3',
    }),
    mailbox({
      id: 3,
      number: null,
      app_name: null,
      display_name: null,
      email: 'gamma@other.store',
      team_id: 'team-9',
    }),
  ];
}

/** Адреса ящиков, фактически отрендеренных в таблице. */
function visibleEmails(): string[] {
  return catalog()
    .map((mb) => mb.email)
    .filter((email) => screen.queryByText(email) !== null);
}

describe('MailboxesTab — клиентский поиск (ADR-050 §1.1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loginSuperadminWithCatalog();
  });

  afterEach(() => logout());

  function loginSuperadminWithCatalog() {
    loginAs({ isSuperadmin: true });
    mailboxesQuery.value = { ...mailboxesQuery.value, data: { mailboxes: catalog() } };
  }

  it('поле поиска рядом с сегментом активности: нормативные плейсхолдер и aria-label', () => {
    render(<MailboxesTab />);

    const input = screen.getByLabelText('Поиск по почтам');
    expect(input).toHaveAttribute('placeholder', 'Поиск по почтам…');
    // Сегмент «Все / Активные / Неактивные» остался на месте.
    expect(screen.getByRole('group', { name: 'Фильтр активности' })).toBeInTheDocument();
  });

  it('ищет по номеру (регистронезависимая подстрока)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(screen.getByLabelText('Поиск по почтам'), '701');

    expect(visibleEmails()).toEqual(['beta@postapp.store']);
  });

  it('ищет по приложению (регистронезависимо)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(screen.getByLabelText('Поиск по почтам'), 'klyro');

    expect(visibleEmails()).toEqual(['alpha@postapp.store']);
  });

  it('ищет по самому адресу почты', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(screen.getByLabelText('Поиск по почтам'), 'GAMMA@');

    expect(visibleEmails()).toEqual(['gamma@other.store']);
  });

  it('пустой запрос (пробелы) фильтр не применяет — видны все ящики', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(screen.getByLabelText('Поиск по почтам'), '   ');

    expect(visibleEmails()).toHaveLength(3);
  });

  it('поиск без совпадений → «Ничего не найдено» (НЕ «Почт пока нет»)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(screen.getByLabelText('Поиск по почтам'), 'zzz-nomatch');

    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    expect(screen.queryByText('Почт пока нет')).not.toBeInTheDocument();
  });

  it('пустой каталог без активных фильтров → «Почт пока нет»', () => {
    mailboxesQuery.value = { ...mailboxesQuery.value, data: { mailboxes: [] } };
    render(<MailboxesTab />);

    expect(screen.getByText('Почт пока нет')).toBeInTheDocument();
    expect(screen.queryByText('Ничего не найдено')).not.toBeInTheDocument();
  });
});

describe('MailboxesTab — клиентский фильтр по команде (ADR-050 §1.2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mailboxesQuery.value = { ...mailboxesQuery.value, data: { mailboxes: catalog() } };
  });

  afterEach(() => logout());

  it('селектор «Команда» рендерится admin-уровню; опции — «Все команды» → команды → «Без команды»', () => {
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    const select = screen.getByLabelText('Команда') as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'Все команды',
      'Продажи',
      'Без команды',
    ]);
  });

  it('без `sees_all_mail_teams` селектор НЕ рендерится вовсе (не пустой, не disabled)', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      seesAllMailTeams: false,
      permissions: { mail: ['view'] },
    });
    render(<MailboxesTab />);

    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
    // Поиск при этом доступен всем ролям.
    expect(screen.getByLabelText('Поиск по почтам')).toBeInTheDocument();
  });

  it('выбор команды фильтрует по `team_id`; «Без команды» — ящики с team_id = null', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    expect(visibleEmails()).toEqual(['beta@postapp.store']);

    // Опция «Без команды» тулбара (у строк ящиков есть свой дропдаун переноса с таким же
    // лейблом) — выбираем по её значению, чтобы попасть именно в фильтр вкладки.
    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, '__no_team__');
    expect(visibleEmails()).toEqual(['alpha@postapp.store']);
  });

  it('поиск и фильтр по команде комбинируются (AND) — ни один не сбрасывает другой (§1.3)', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    await user.type(screen.getByLabelText('Поиск по почтам'), 'klyro');

    // Ящик команды «Продажи» (id=2) не совпадает с запросом → пусто, но оба фильтра активны.
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    expect((screen.getByLabelText('Команда') as HTMLSelectElement).value).toBe('team-3');
    expect((screen.getByLabelText('Поиск по почтам') as HTMLInputElement).value).toBe('klyro');

    // Совпадающий запрос по той же команде — строка находится.
    await user.clear(screen.getByLabelText('Поиск по почтам'));
    await user.type(screen.getByLabelText('Поиск по почтам'), 'nova');
    expect(visibleEmails()).toEqual(['beta@postapp.store']);
  });
});

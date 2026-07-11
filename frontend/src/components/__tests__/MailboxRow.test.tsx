import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { MailboxRow } from '@/components/MailboxRow';
import type { MailMailbox, TeamListItem } from '@/types/api';

// MailboxRow дергает мутации ящика — no-op моки (тест проверяет рендер строки, не запись).
vi.mock('@/features/mail/hooks', () => ({
  useUpdateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteMailbox: () => ({ mutate: vi.fn(), isPending: false }),
}));

// Форма редактирования — отдельный тест; здесь не разворачиваем её дерево хуков.
vi.mock('@/components/MailboxFormModal', () => ({ MailboxFormModal: () => null }));

function makeMailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 1,
    email: 'inbox@postapp.store',
    number: '5108',
    app_name: 'Klyro Forge (Codex)',
    display_name: '5108 Klyro Forge (Codex)',
    team_id: null,
    is_active: true,
    last_synced_at: '2026-07-02T09:15:00Z',
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

const TEAMS: TeamListItem[] = [
  {
    id: 'team-3',
    name: 'Продажи',
    leader_id: null,
    leader_username: null,
    member_count: 0,
    number_count: 0,
    members: [],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  },
];

function renderRow(mailbox: MailMailbox, over: Partial<Parameters<typeof MailboxRow>[0]> = {}) {
  return render(
    <table>
      <tbody>
        <MailboxRow
          mailbox={mailbox}
          teams={TEAMS}
          canTransfer={false}
          canEdit={false}
          canSync={false}
          canDelete={false}
          {...over}
        />
      </tbody>
    </table>,
  );
}

/**
 * Статус-кружок строки (ADR-047 §5): отдельной колонки статуса больше НЕТ — кружок переехал
 * в первый ряд идентификационной ячейки, а текст статуса доступен только скринридеру
 * (`sr-only` внутри `ui/Badge`). Цветовой токен несёт ОБЁРТКА-`Badge`, поэтому className
 * проверяется на родителе sr-only-узла, а не на нём самом.
 */
function statusBadge(text: string): HTMLElement {
  const srOnly = screen.getByText(text);
  expect(srOnly.className).toContain('sr-only');
  const badge = srOnly.parentElement;
  expect(badge).not.toBeNull();
  return badge as HTMLElement;
}

describe('MailboxRow status circle (08-design-system.md §«Вкладка Почты», ADR-047 §5)', () => {
  it('green «Активна» when active with no failures and no sync error', () => {
    renderRow(makeMailbox({ is_active: true, consecutive_failures: 0, last_sync_error: null }));
    const badge = statusBadge('Активна');
    expect(badge.className).toContain('text-status-green');
    expect(badge.className).not.toContain('text-status-red');
  });

  // Регрессия (реальный баг): активный ящик с живым last_sync_error при нулевом счётчике
  // подряд-ошибок ДОЛЖЕН быть красным «Ошибка синхронизации», а не зелёным «Активна».
  it('red «Ошибка синхронизации» when active, consecutive_failures===0 but last_sync_error != null', () => {
    renderRow(
      makeMailbox({
        is_active: true,
        consecutive_failures: 0,
        last_sync_error: 'IMAP login failed',
      }),
    );
    const badge = statusBadge('Ошибка синхронизации');
    expect(badge.className).toContain('text-status-red');
    expect(badge.className).not.toContain('text-status-green');
    expect(screen.queryByText('Активна')).not.toBeInTheDocument();
  });

  it('red «Ошибка синхронизации» when active with consecutive_failures > 0', () => {
    renderRow(makeMailbox({ is_active: true, consecutive_failures: 3, last_sync_error: null }));
    expect(statusBadge('Ошибка синхронизации').className).toContain('text-status-red');
  });

  it('red «Неактивна» when the mailbox is inactive', () => {
    renderRow(makeMailbox({ is_active: false, consecutive_failures: 0, last_sync_error: null }));
    expect(statusBadge('Неактивна').className).toContain('text-status-red');
  });
});

// --- Новый рендер идентификационной ячейки (ADR-047 §5, референс screen/1.jpg) ---------
describe('MailboxRow identity cell — «Номер» / «Приложение» / e-mail (ADR-047 §5)', () => {
  it('renders «Номер» + значение и «Приложение» пилюлей + адрес почты второй строкой', () => {
    renderRow(makeMailbox({ number: '5108', app_name: 'Klyro Forge (Codex)' }));

    expect(screen.getByText('Номер')).toBeInTheDocument();
    expect(screen.getByText('5108')).toBeInTheDocument();
    expect(screen.getByText('Приложение')).toBeInTheDocument();
    // «Приложение» — существующий примитив ui/Pill (tone="accent"): заливка + скругление chip.
    const pill = screen.getByText('Klyro Forge (Codex)');
    expect(pill.className).toContain('rounded-chip');
    // Значение не усекается (значимый контент виден полностью — CLAUDE.md).
    expect(pill.className).not.toContain('truncate');
    expect(pill.className).not.toContain('overflow-hidden');
    // Ряд 2 — адрес почты.
    expect(screen.getByText('inbox@postapp.store')).toBeInTheDocument();
  });

  it('пустой «Номер» → пара «лейбл + значение» НЕ рендерится (лейбла без значения нет)', () => {
    renderRow(makeMailbox({ number: null, app_name: 'WIU' }));

    expect(screen.queryByText('Номер')).not.toBeInTheDocument();
    expect(screen.getByText('Приложение')).toBeInTheDocument();
    expect(screen.getByText('WIU')).toBeInTheDocument();
  });

  it('пустое «Приложение» → пара «лейбл + значение» НЕ рендерится', () => {
    renderRow(makeMailbox({ number: '173, 57, 104', app_name: null }));

    expect(screen.getByText('Номер')).toBeInTheDocument();
    expect(screen.getByText('173, 57, 104')).toBeInTheDocument();
    expect(screen.queryByText('Приложение')).not.toBeInTheDocument();
  });

  it('обе части пусты → ряд 1 содержит только индикатор статуса', () => {
    renderRow(makeMailbox({ number: null, app_name: null, display_name: null }));

    expect(screen.queryByText('Номер')).not.toBeInTheDocument();
    expect(screen.queryByText('Приложение')).not.toBeInTheDocument();
    // Кружок статуса на месте (sr-only текст статуса).
    expect(statusBadge('Активна')).toBeInTheDocument();
    expect(screen.getByText('inbox@postapp.store')).toBeInTheDocument();
  });

  it('пробельные значения считаются пустыми (лейблы не рендерятся)', () => {
    renderRow(makeMailbox({ number: '   ', app_name: '  ' }));

    expect(screen.queryByText('Номер')).not.toBeInTheDocument();
    expect(screen.queryByText('Приложение')).not.toBeInTheDocument();
  });
});

describe('MailboxRow team selector editability (перенос — только admin-уровень, ADR-044 §4)', () => {
  it('renders an editable team <select> only when canTransfer (sees_all_mail_teams)', () => {
    renderRow(makeMailbox({ team_id: 'team-3' }), { canTransfer: true });
    const select = screen.getByLabelText('Команда почты inbox@postapp.store');
    expect(select.tagName).toBe('SELECT');
    // Опции: «Без команды» + команды.
    expect(within(select).getByRole('option', { name: 'Без команды' })).toBeInTheDocument();
    expect(within(select).getByRole('option', { name: 'Продажи' })).toBeInTheDocument();
  });

  it('renders the team name as read-only text (no <select>) when NOT canTransfer', () => {
    renderRow(makeMailbox({ team_id: 'team-3' }), { canTransfer: false });
    expect(screen.queryByLabelText('Команда почты inbox@postapp.store')).not.toBeInTheDocument();
    expect(screen.getByText('Продажи')).toBeInTheDocument();
  });

  it('shows «Без команды» read-only text for an unassigned mailbox when NOT canTransfer', () => {
    renderRow(makeMailbox({ team_id: null }), { canTransfer: false });
    expect(screen.queryByLabelText('Команда почты inbox@postapp.store')).not.toBeInTheDocument();
    expect(screen.getByText('Без команды')).toBeInTheDocument();
  });

  // ADR-047 §4: значение команды читается ПОЛНОСТЬЮ — клиппирование запрещено.
  it('значение команды не обрезается (нет truncate/overflow-hidden), контрол растёт с колонкой', () => {
    renderRow(makeMailbox({ team_id: 'team-3' }), { canTransfer: false });
    const value = screen.getByText('Продажи');
    expect(value.className).not.toContain('truncate');
    expect(value.className).not.toContain('overflow-hidden');
    expect(value.className).toContain('break-words');
  });
});

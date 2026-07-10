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
    display_name: 'Входящие',
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

/** Статус-бейдж строки (единственный span со статусным текстом). */
function statusBadge(text: string): HTMLElement {
  return screen.getByText(text);
}

describe('MailboxRow status circle (08-design-system.md §«Вкладка Почты»)', () => {
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
    const badge = statusBadge('Ошибка синхронизации');
    expect(badge.className).toContain('text-status-red');
  });

  it('red «Неактивна» when the mailbox is inactive', () => {
    renderRow(makeMailbox({ is_active: false, consecutive_failures: 0, last_sync_error: null }));
    const badge = statusBadge('Неактивна');
    expect(badge.className).toContain('text-status-red');
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
});

import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxRow } from '@/components/MailboxRow';
import { MAIL_CONNECTION_PROGRESS_HINT } from '@/features/mail/errorMessages';
import type { MailMailbox, TeamListItem } from '@/types/api';

/**
 * ADR-053 §4 (TD-059): смена команды в строке — это `PATCH /mailboxes/{id}`, т.е. ЛЮБОЙ
 * сетевой PATCH относится к mail-server-категории (§1.1) и легально идёт до 85 с. Значит
 * прогресс-состояние (спиннер + подпись + disabled) обязано быть и ЗДЕСЬ, а не только в форме.
 */

const update = vi.hoisted(() => ({ isPending: false, mutate: vi.fn() }));

vi.mock('@/features/mail/hooks', () => ({
  useUpdateMailbox: () => update,
  useSyncMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteMailbox: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/components/MailboxFormModal', () => ({ MailboxFormModal: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const TEAMS: TeamListItem[] = [
  {
    id: 'team-3',
    name: 'Продажи',
    leader_id: null,
    leader_username: null,
    member_count: 0,
    number_count: 0,
    mailbox_count: 0,
    members: [],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  },
];

function makeMailbox(): MailMailbox {
  return {
    id: 7,
    email: 'inbox@postapp.store',
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

function renderRow() {
  return render(
    <table>
      <tbody>
        <MailboxRow
          mailbox={makeMailbox()}
          teams={TEAMS}
          canTransfer
          canEdit={false}
          canSync={false}
          canDelete={false}
        />
      </tbody>
    </table>,
  );
}

beforeEach(() => {
  update.isPending = false;
});

afterEach(() => {
  vi.clearAllMocks();
});

describe('MailboxRow — прогресс долгого PATCH (смена команды, ADR-053 §4)', () => {
  it('во время переноса: спиннер + подпись + селект disabled', () => {
    update.isPending = true;
    renderRow();

    const progress = screen.getByRole('status');
    expect(progress).toHaveTextContent(MAIL_CONNECTION_PROGRESS_HINT);
    expect(screen.getByLabelText('Команда почты inbox@postapp.store')).toBeDisabled();
  });

  it('в покое прогресс-строки нет, селект активен', () => {
    renderRow();

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Команда почты inbox@postapp.store')).toBeEnabled();
  });
});

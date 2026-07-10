import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';

// Право mail:create управляется из теста — «Проверить соединение» бьёт POST
// /mail/mailboxes/test, закрытый гейтом mail:create (backend/app/api/mail.py: test_mailbox
// «Гейт mail:create»). Кнопку рендерим только под этим правом (UX-гейт; сервер — граница).
const perms = vi.hoisted(() => ({ canCreate: false }));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) =>
    page === 'mail' && action === 'create' ? perms.canCreate : false,
  useSeesAllMailTeams: () => true,
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({ data: { items: [] } }),
}));

vi.mock('@/features/mail/hooks', () => ({
  useCreateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useTestMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
}));

afterEach(() => {
  perms.canCreate = false;
});

describe('MailboxFormModal «Проверить соединение» gating (mail:create, ADR-044 §4)', () => {
  it('renders the «Проверить соединение» button when the actor holds mail:create', () => {
    perms.canCreate = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    expect(screen.getByRole('button', { name: /Проверить соединение/ })).toBeInTheDocument();
  });

  it('hides the «Проверить соединение» button without mail:create', () => {
    perms.canCreate = false;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    expect(screen.queryByRole('button', { name: /Проверить соединение/ })).not.toBeInTheDocument();
  });
});

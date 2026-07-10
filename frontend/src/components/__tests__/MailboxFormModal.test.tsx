import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { ApiError } from '@/lib/api';
import type { MailMailbox } from '@/types/api';

// --- Управляемое окружение хуков (vi.hoisted — доступно внутри vi.mock фабрик) ---
const perms = vi.hoisted(() => ({ canCreate: false, seesAll: true }));
// Управляемая мутация authorize: mutate(teamId, { onSuccess, onError }) —
// тест диктует, какой колбэк дёрнуть (успех 200 → panel; 503/404/502 → onError).
const authorize = vi.hoisted(() => ({
  lastTeamId: undefined as string | null | undefined,
  mode: 'success' as 'success' | 'unavailable' | 'idle',
  authorizeUrl: 'https://login.microsoftonline.com/consumers/authorize?x=1',
}));
// Управляемое значение watchQuery (пуллинг списка ящиков при открытой панели).
const watch = vi.hoisted(() => ({ value: { data: undefined } as unknown }));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) =>
    page === 'mail' && action === 'create' ? perms.canCreate : false,
  useSeesAllMailTeams: () => perms.seesAll,
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({ data: { items: [{ id: 'team-3', name: 'Продажи' }] } }),
}));

// watchQuery опирается на useQuery напрямую из @tanstack/react-query — мокаем его
// контролируемым значением (даёт детерминированный пуллинг без реального QueryClient).
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => watch.value,
}));

vi.mock('@/features/mail/hooks', () => ({
  mailMailboxesKey: ['mail', 'mailboxes'],
  useCreateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useTestMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useMailboxOAuthAuthorize: () => ({
    isPending: false,
    mutate: (
      teamId: string | null,
      opts?: {
        onSuccess?: (r: { authorize_url: string }) => void;
        onError?: (e: unknown) => void;
      },
    ) => {
      authorize.lastTeamId = teamId;
      if (authorize.mode === 'success')
        opts?.onSuccess?.({ authorize_url: authorize.authorizeUrl });
      else if (authorize.mode === 'unavailable')
        opts?.onError?.(new ApiError(503, 'mail_not_configured', 'not configured'));
    },
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

beforeEach(() => {
  perms.canCreate = false;
  perms.seesAll = true;
  authorize.lastTeamId = undefined;
  authorize.mode = 'success';
  watch.value = { data: undefined };
});

afterEach(() => {
  vi.clearAllMocks();
});

function makeMailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 42,
    email: 'box@outlook.com',
    display_name: 'Box',
    team_id: null,
    is_active: true,
    last_synced_at: null,
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

const OAUTH_BTN = /Подключить Outlook \(OAuth\)/;

// ---------------------------------------------------------------------------
// «Проверить соединение» gating (сохранённое поведение — mail:create).
// ---------------------------------------------------------------------------
describe('MailboxFormModal «Проверить соединение» gating (mail:create, ADR-044 §4)', () => {
  it('renders «Проверить соединение» when the actor holds mail:create', () => {
    perms.canCreate = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    expect(screen.getByRole('button', { name: /Проверить соединение/ })).toBeInTheDocument();
  });

  it('hides «Проверить соединение» without mail:create', () => {
    perms.canCreate = false;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    expect(screen.queryByRole('button', { name: /Проверить соединение/ })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Outlook OAuth — Вариант B (UI зеркалит backend: admin может без команды).
// ---------------------------------------------------------------------------
describe('MailboxFormModal Outlook OAuth (Вариант B, ADR-045 §5)', () => {
  it('renders an enabled «Подключить Outlook (OAuth)» button in add mode', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    const btn = screen.getByRole('button', { name: OAUTH_BTN });
    expect(btn).toBeInTheDocument();
    expect(btn).toBeEnabled();
  });

  it('calls authorize with team_id=null for admin at «Без команды» (no «select team first» hint)', async () => {
    // Вариант A удалён: подсказки «сначала выберите команду» больше нет.
    perms.seesAll = true; // admin
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    expect(screen.queryByText(/сначала выберите команду/i)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: OAUTH_BTN }));
    // team_id по умолчанию — «Без команды» (NO_TEAM = '') → authorize с null.
    expect(authorize.lastTeamId).toBeNull();
  });

  it('200 {authorize_url} → shows the OctoBrowser panel (readonly input, «Скопировать», «Открыть»)', async () => {
    authorize.mode = 'success';
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    await userEvent.click(screen.getByRole('button', { name: OAUTH_BTN }));

    // Кнопка-инициатор исчезла, показана панель-ссылка.
    expect(screen.queryByRole('button', { name: OAUTH_BTN })).not.toBeInTheDocument();
    const input = screen.getByLabelText('Ссылка для авторизации Outlook') as HTMLInputElement;
    expect(input).toHaveAttribute('readonly');
    expect(input.value).toBe(authorize.authorizeUrl);
    expect(screen.getByRole('button', { name: /Скопировать/ })).toBeInTheDocument();
    expect(screen.getAllByText(/OctoBrowser/).length).toBeGreaterThan(0);
    const openLink = screen.getByRole('link', { name: /Открыть/ });
    expect(openLink).toHaveAttribute('href', authorize.authorizeUrl);
  });

  it('503 mail_not_configured → hides the button and shows the unavailable message', async () => {
    authorize.mode = 'unavailable';
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    await userEvent.click(screen.getByRole('button', { name: OAUTH_BTN }));

    expect(screen.queryByRole('button', { name: OAUTH_BTN })).not.toBeInTheDocument();
    expect(screen.getByText(/Подключение Outlook временно недоступно/)).toBeInTheDocument();
  });

  it('closes on a new mailbox appearing during panel polling (success)', async () => {
    authorize.mode = 'success';
    const onOpenChange = vi.fn();
    // Стартовый снимок пуллинга — один ящик (id 1) на момент открытия панели.
    watch.value = { data: { mailboxes: [{ id: 1 }] } };
    const { rerender } = render(<MailboxFormModal open onOpenChange={onOpenChange} mode="add" />);
    await userEvent.click(screen.getByRole('button', { name: OAUTH_BTN }));

    // Появился новый ящик (id 2) → пуллинг детектит → успех + закрытие.
    watch.value = { data: { mailboxes: [{ id: 1 }, { id: 2 }] } };
    rerender(<MailboxFormModal open onOpenChange={onOpenChange} mode="add" />);

    expect(toast.success).toHaveBeenCalledWith('Ящик Outlook подключён');
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

// ---------------------------------------------------------------------------
// Режим edit: Outlook-секция / аккордеон / разделитель не рендерятся.
// ---------------------------------------------------------------------------
describe('MailboxFormModal edit mode omits the add-only Outlook UI', () => {
  it('does not render the Outlook section, the help accordion, or the divider', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={makeMailbox()} />);
    expect(screen.queryByRole('button', { name: OAUTH_BTN })).not.toBeInTheDocument();
    expect(screen.queryByText('Как добавить почту?')).not.toBeInTheDocument();
    expect(screen.queryByText(/или добавьте ящик вручную/)).not.toBeInTheDocument();
    // Заголовок секции «Outlook» отсутствует в edit.
    expect(screen.queryByRole('heading', { name: 'Outlook' })).not.toBeInTheDocument();
  });
});

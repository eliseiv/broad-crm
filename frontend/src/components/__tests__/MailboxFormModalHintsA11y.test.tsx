import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import type { MailMailbox } from '@/types/api';

/**
 * Доступность подсказок формы ящика (TD-061, 08-design-system.md §«Подсказка под полем формы
 * связывается с контролом»):
 *
 *   • подсказка «Код приложения» (ADR-054 — она несёт ВЕСЬ смысл переименования поля) обязана
 *     быть частью ДОСТУПНОГО ОПИСАНИЯ поля и НЕ исчезать при инлайн-ошибке;
 *   • подсказка `Select` «Команда» связана с контролом в ОБОИХ состояниях (перенос только
 *     админу / нет доступных команд);
 *   • ГРУППОВЫЕ подсказки (параметры подключения) связаны с ОБОИМИ `fieldset`'ами в режиме
 *     `edit`; в режиме `add` текста нет ⇒ атрибута нет (висячий IDREF запрещён).
 */

const perms = vi.hoisted(() => ({ canCreate: true, seesAll: true }));
const mailScope = vi.hoisted(() => ({
  value: {
    teams: [{ id: 'team-3', name: 'Продажи' }] as { id: string; name: string }[],
    includesUnassigned: false,
  },
}));
const mutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) =>
    page === 'mail' && action === 'create' && perms.canCreate,
  useSeesAllMailTeams: () => perms.seesAll,
  useChannelTeamScope: () => mailScope.value,
}));

vi.mock('@/features/teams/hooks', () => ({ useTeams: () => ({ data: { items: [] } }) }));

vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: undefined }) }));

vi.mock('@/features/mail/hooks', () => ({
  mailMailboxesKey: ['mail', 'mailboxes'],
  useCreateMailbox: () => ({ mutate: mutations.create, isPending: false }),
  useTestMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMailbox: () => ({ mutate: mutations.update, isPending: false }),
  useMailboxOAuthAuthorize: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const APP_PASSWORD_HINT =
  'Не пароль от почты, а пароль приложения (app password) из настроек безопасности почтового сервиса. Как его получить — в блоке «Как добавить почту?» выше.';
const APP_PASSWORD_ERROR = 'Укажите код приложения';
const EDIT_PASSWORD_HINT = 'Оставьте пустым, чтобы не менять код приложения.';
const CONNECTION_HINT =
  'Параметры подключения не отображаются из соображений безопасности. Заполните только то, что нужно изменить.';
const TEAM_HINT_ADMIN_ONLY = 'Перенос между командами доступен только администратору.';
const TEAM_HINT_NO_TEAMS = 'Нет доступных команд — обратитесь к администратору.';

beforeEach(() => {
  perms.canCreate = true;
  perms.seesAll = true;
  mailScope.value = { teams: [{ id: 'team-3', name: 'Продажи' }], includesUnassigned: false };
  vi.clearAllMocks();
});

function makeMailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 42,
    email: 'inbox@postapp.store',
    number: '5108',
    app_name: 'Klyro',
    display_name: '5108 Klyro',
    team_id: 'team-3',
    is_active: true,
    last_synced_at: null,
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

/** Тексты узлов, на которые ссылается `aria-describedby`. Висячий IDREF → падение теста. */
function describedTexts(el: HTMLElement): string[] {
  const attr = el.getAttribute('aria-describedby');
  if (attr === null) return [];
  return attr
    .split(' ')
    .filter(Boolean)
    .map((id) => {
      const node = document.getElementById(id);
      expect(node, `висячий IDREF: узла с id="${id}" нет в DOM`).not.toBeNull();
      return node?.textContent ?? '';
    });
}

describe('MailboxFormModal — подсказка «Код приложения» (ADR-054, TD-061)', () => {
  it('add: подсказка про app password — часть доступного описания поля', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    const field = screen.getByLabelText('Код приложения');
    expect(field).toHaveAccessibleDescription(APP_PASSWORD_HINT);
    expect(describedTexts(field)).toEqual([APP_PASSWORD_HINT]);
  });

  it('add: инлайн-ошибка НЕ вытесняет подсказку — описание = «подсказка + ошибка»', async () => {
    const user = userEvent.setup();
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await user.type(screen.getByLabelText('Адрес почты'), 'new@example.com');
    await user.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
    await user.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText(APP_PASSWORD_ERROR)).toBeVisible();
    // Подсказка осталась и видимой, и в описании — ошибка её не вытеснила (норма TD-061).
    expect(screen.getByText(APP_PASSWORD_HINT)).toBeVisible();

    const field = screen.getByLabelText('Код приложения');
    expect(describedTexts(field)).toEqual([APP_PASSWORD_HINT, APP_PASSWORD_ERROR]);
    expect(field).toHaveAccessibleDescription(`${APP_PASSWORD_HINT} ${APP_PASSWORD_ERROR}`);
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(mutations.create).not.toHaveBeenCalled();
  });

  it('edit: подсказка «Оставьте пустым…» связана с полем', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={makeMailbox()} />);

    const field = screen.getByLabelText('Код приложения');
    expect(field).toHaveAccessibleDescription(EDIT_PASSWORD_HINT);
    expect(describedTexts(field)).toEqual([EDIT_PASSWORD_HINT]);
  });
});

describe('MailboxFormModal — подсказка селектора «Команда» (TD-061)', () => {
  it('edit под не-админом: «Перенос … только администратору» связан с селектором', () => {
    perms.seesAll = false;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={makeMailbox()} />);

    const select = screen.getByLabelText('Команда');
    expect(select).toBeDisabled();
    expect(select).toHaveAccessibleDescription(TEAM_HINT_ADMIN_ONLY);
    expect(describedTexts(select)).toEqual([TEAM_HINT_ADMIN_ONLY]);
  });

  it('add без доступных команд: «Нет доступных команд …» связан с селектором', () => {
    perms.seesAll = false;
    mailScope.value = { teams: [], includesUnassigned: false };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    const select = screen.getByLabelText('Команда');
    expect(select).toBeDisabled();
    expect(select).toHaveAccessibleDescription(TEAM_HINT_NO_TEAMS);
    expect(describedTexts(select)).toEqual([TEAM_HINT_NO_TEAMS]);
  });

  it('add под админом с доступными командами: подсказки нет ⇒ атрибута нет', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    const select = screen.getByLabelText('Команда');
    expect(select).not.toHaveAttribute('aria-describedby');
    expect(select).toHaveAccessibleDescription('');
  });
});

describe('MailboxFormModal — групповая подсказка параметров подключения (TD-061)', () => {
  it('edit: подсказка связана с ОБОИМИ fieldset (IMAP и SMTP), id разрешается в DOM', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={makeMailbox()} />);

    const imap = screen.getByRole('group', { name: 'IMAP' });
    const smtp = screen.getByRole('group', { name: 'SMTP' });

    expect(describedTexts(imap)).toEqual([CONNECTION_HINT]);
    expect(describedTexts(smtp)).toEqual([CONNECTION_HINT]);
    // Один и тот же узел подсказки описывает обе группы.
    expect(imap.getAttribute('aria-describedby')).toBe(smtp.getAttribute('aria-describedby'));
    expect(screen.getByText(CONNECTION_HINT)).toBeVisible();
  });

  it('add: подсказки нет ⇒ у fieldset нет aria-describedby (висячий IDREF запрещён)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.queryByText(CONNECTION_HINT)).not.toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'IMAP' })).not.toHaveAttribute('aria-describedby');
    expect(screen.getByRole('group', { name: 'SMTP' })).not.toHaveAttribute('aria-describedby');
  });
});

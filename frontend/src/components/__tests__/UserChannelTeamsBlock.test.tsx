import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AddUserModal } from '@/components/AddUserModal';
import type { RoleListItem, TeamListItem, UserListItem } from '@/types/api';

/**
 * Блоки «СМС» и «Почты» в форме пользователя (ADR-055 §6.1, 08-design-system.md).
 *
 * Нормативно: оба блока ВНИЗУ формы, в порядке «СМС» → «Почты», оба СВЁРНУТЫ по умолчанию
 * (и в `add`, и в `edit`); базовая команда (из блока «Команды») внутри блока канала —
 * **checked + disabled** с подписью «из блока «Команды»»; снятие команды в блоке «Команды»
 * реактивно делает её чекбокс обычным НЕОТМЕЧЕННЫМ; в запрос уходит ТОЛЬКО ДОБАВКА
 * (`*_extra_team_ids` без базовых) + `*_extra_includes_unassigned`; подсказка под чекбоксом
 * «Без команды» ОБЯЗАТЕЛЬНА и РАЗНАЯ по каналам (§3.1 — объём флага у каналов разный).
 */

const mutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), del: vi.fn() }));

vi.mock('@/features/users/hooks', () => ({
  useCreateUser: () => ({ mutate: mutations.create, isPending: false }),
  useUpdateUser: () => ({ mutate: mutations.update, isPending: false }),
  useDeleteUser: () => ({ mutate: mutations.del, isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/** Нормативные строки подсказок (08-design-system.md — сверяются ПОБУКВЕННО). */
const SMS_HINT =
  'Даст доступ ко ВСЕМ номерам без команды — это весь ещё не распределённый поток из синхронизации Twilio, включая правку, удаление и перенос номера.';
const MAIL_HINT =
  'Даст доступ к ящикам без команды (их заводит только администратор), включая правку, синк и удаление.';

const ROLES: RoleListItem[] = [
  {
    id: 'r1',
    name: 'Оператор',
    permissions: { mail: ['view'] },
    user_count: 1,
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  },
];

function team(id: string, name: string): TeamListItem {
  return {
    id,
    name,
    leader_id: null,
    leader_username: null,
    member_count: 0,
    number_count: 0,
    mailbox_count: 0,
    members: [],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
  };
}

const TEAMS = [team('t1', 'Продажи'), team('t2', 'Поддержка'), team('t3', 'Логистика')];

function existingUser(over: Partial<UserListItem> = {}): UserListItem {
  return {
    id: 'u1',
    username: 'Никита',
    telegram: null,
    has_password: true,
    role_id: 'r1',
    role_name: 'Оператор',
    is_active: true,
    status: 'active',
    teams: [{ id: 't1', name: 'Продажи' }],
    mail_extra_teams: [],
    mail_extra_includes_unassigned: false,
    sms_extra_teams: [],
    sms_extra_includes_unassigned: false,
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
    ...over,
  } as UserListItem;
}

/** Кнопка-триггер свёрнутого блока канала (её текст — сводка §6.1). */
function blockToggle(channelTitle: 'СМС' | 'Почты'): HTMLElement {
  return screen.getByRole('button', { name: new RegExp(`^${channelTitle} ·`) });
}

/** Раскрытая панель блока канала (`aria-controls` → её id). */
function blockPanel(channelTitle: 'СМС' | 'Почты'): HTMLElement {
  const id = blockToggle(channelTitle).getAttribute('aria-controls')!;
  return document.getElementById(id)!;
}

beforeEach(() => vi.clearAllMocks());

// --- Свёрнутость, порядок, сводка (§6.1) -------------------------------------

describe('Блоки «СМС»/«Почты» — свёрнуты, порядок, сводка (ADR-055 §6.1)', () => {
  it('оба блока СВЁРНУТЫ по умолчанию в режиме `add` (содержимое не смонтировано)', () => {
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    expect(blockToggle('СМС')).toHaveAttribute('aria-expanded', 'false');
    expect(blockToggle('Почты')).toHaveAttribute('aria-expanded', 'false');
    // Содержимое монтируется только при раскрытии ⇒ подсказок в DOM ещё нет.
    expect(screen.queryByText(SMS_HINT)).not.toBeInTheDocument();
    expect(screen.queryByText(MAIL_HINT)).not.toBeInTheDocument();
  });

  it('оба блока СВЁРНУТЫ по умолчанию и в режиме `edit`', () => {
    render(
      <AddUserModal
        open
        onOpenChange={vi.fn()}
        roles={ROLES}
        teams={TEAMS}
        mode="edit"
        user={existingUser({ mail_extra_teams: [{ id: 't2', name: 'Поддержка' }] })}
      />,
    );

    expect(blockToggle('СМС')).toHaveAttribute('aria-expanded', 'false');
    expect(blockToggle('Почты')).toHaveAttribute('aria-expanded', 'false');
  });

  it('порядок блоков: «СМС», затем «Почты» (формулировка владельца)', () => {
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    const position = blockToggle('СМС').compareDocumentPosition(blockToggle('Почты'));
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('сводка свёрнутого заголовка: «доп. команд нет» при нулевой добавке', () => {
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    expect(blockToggle('СМС')).toHaveTextContent('СМС · доп. команд нет');
    expect(blockToggle('Почты')).toHaveTextContent('Почты · доп. команд нет');
  });

  it('сводка: «доп. команд: N» + «Без команды» (не раскрывая блок)', () => {
    render(
      <AddUserModal
        open
        onOpenChange={vi.fn()}
        roles={ROLES}
        teams={TEAMS}
        mode="edit"
        user={existingUser({
          mail_extra_teams: [
            { id: 't2', name: 'Поддержка' },
            { id: 't3', name: 'Логистика' },
          ],
          mail_extra_includes_unassigned: true,
        })}
      />,
    );

    expect(blockToggle('Почты')).toHaveTextContent('Почты · доп. команд: 2 + Без команды');
  });
});

// --- Подсказки «Без команды»: обязательны и РАЗНЫЕ по каналам (§3.1/§6.1) -----

describe('Подсказка «Без команды» — обязательна и РАЗНАЯ по каналам (ADR-055 §3.1)', () => {
  it('блок «СМС»: подсказка про ВЕСЬ нераспределённый поток номеров Twilio (побуквенно)', async () => {
    const user = userEvent.setup();
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.click(blockToggle('СМС'));

    expect(within(blockPanel('СМС')).getByText(SMS_HINT)).toBeVisible();
  });

  it('блок «Почты»: подсказка про ящики без команды (побуквенно) — ДРУГАЯ строка', async () => {
    const user = userEvent.setup();
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.click(blockToggle('Почты'));

    const panel = blockPanel('Почты');
    expect(within(panel).getByText(MAIL_HINT)).toBeVisible();
    // Строки НЕ взаимозаменяемы: объём флага у каналов разный (§3.1 — асимметрия).
    expect(within(panel).queryByText(SMS_HINT)).not.toBeInTheDocument();
    expect(SMS_HINT).not.toBe(MAIL_HINT);
  });

  it('подсказка программно связана с чекбоксом «Без команды» (`aria-describedby`)', async () => {
    const user = userEvent.setup();
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);
    await user.click(blockToggle('СМС'));

    const panel = blockPanel('СМС');
    const checkbox = within(panel).getByLabelText('Без команды');
    const hintId = checkbox.getAttribute('aria-describedby')!;
    expect(document.getElementById(hintId)).toHaveTextContent(SMS_HINT);
  });
});

// --- Базовая команда: checked + disabled, реактивность (§6.1) -----------------

describe('Базовая команда внутри блока канала (ADR-055 §6.1)', () => {
  it('отмечена и DISABLED (снять нельзя) + подпись «из блока «Команды»»', async () => {
    const user = userEvent.setup();
    render(
      <AddUserModal
        open
        onOpenChange={vi.fn()}
        roles={ROLES}
        teams={TEAMS}
        mode="edit"
        user={existingUser()} // базовая команда — t1 «Продажи»
      />,
    );

    await user.click(blockToggle('Почты'));
    const panel = blockPanel('Почты');

    const base = within(panel).getByLabelText('Продажи');
    expect(base).toBeChecked();
    expect(base).toBeDisabled();
    expect(within(panel).getByText('из блока «Команды»')).toBeInTheDocument();

    // Прочие команды — обычные неотмеченные чекбоксы.
    expect(within(panel).getByLabelText('Поддержка')).not.toBeChecked();
    expect(within(panel).getByLabelText('Поддержка')).toBeEnabled();
  });

  it('РЕАКТИВНОСТЬ: снятие команды в блоке «Команды» делает её чекбокс обычным НЕОТМЕЧЕННЫМ', async () => {
    const user = userEvent.setup();
    render(
      <AddUserModal
        open
        onOpenChange={vi.fn()}
        roles={ROLES}
        teams={TEAMS}
        mode="edit"
        user={existingUser()}
      />,
    );

    await user.click(blockToggle('Почты'));
    expect(within(blockPanel('Почты')).getByLabelText('Продажи')).toBeDisabled();

    // Снимаем «Продажи» в основном блоке «Команды» (`MultiSelect` — группа чекбоксов).
    const teamsGroup = screen.getByRole('group', { name: 'Команды' });
    await user.click(within(teamsGroup).getByRole('checkbox', { name: 'Продажи' }));

    const base = within(blockPanel('Почты')).getByLabelText('Продажи');
    // Авто-простановки галочки НЕ происходит: потеря доступа — намеренное действие админа.
    expect(base).toBeEnabled();
    expect(base).not.toBeChecked();
  });
});

// --- Отправляется ТОЛЬКО добавка (§6.1/§5.2) ---------------------------------

describe('Отправляется ТОЛЬКО добавка (ADR-055 §6.1/§5.2)', () => {
  it('create: базовые команды в `*_extra_team_ids` НЕ включаются, флаг уходит отдельно', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_p: unknown, opts: { onSuccess: () => void }) =>
      opts.onSuccess(),
    );
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Никита');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');
    // Базовая команда — «Продажи» (блок «Команды», `MultiSelect` — группа чекбоксов).
    const teamsGroup = screen.getByRole('group', { name: 'Команды' });
    await user.click(within(teamsGroup).getByRole('checkbox', { name: 'Продажи' }));

    // В блоке «Почты» добавляем «Поддержка» + «Без команды».
    await user.click(blockToggle('Почты'));
    const panel = blockPanel('Почты');
    await user.click(within(panel).getByLabelText('Поддержка'));
    await user.click(within(panel).getByLabelText('Без команды'));

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = mutations.create.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.team_ids).toEqual(['t1']);
    // ТОЛЬКО добавка: базовая «Продажи» (t1) в добавку НЕ попала.
    expect(payload.mail_extra_team_ids).toEqual(['t2']);
    expect(payload.mail_extra_includes_unassigned).toBe(true);
    // Канал СМС не трогали ⇒ его полей в payload нет (наборы независимы).
    expect(payload).not.toHaveProperty('sms_extra_team_ids');
  });

  it('каналы независимы: добавка «СМС» не попадает в поля «Почты»', async () => {
    const user = userEvent.setup();
    mutations.create.mockImplementation((_p: unknown, opts: { onSuccess: () => void }) =>
      opts.onSuccess(),
    );
    render(<AddUserModal open onOpenChange={vi.fn()} roles={ROLES} teams={TEAMS} mode="add" />);

    await user.type(screen.getByLabelText('Логин'), 'Пётр');
    await user.type(screen.getByLabelText('Пароль'), 's3cret-pass');

    await user.click(blockToggle('СМС'));
    await user.click(within(blockPanel('СМС')).getByLabelText('Логистика'));

    await user.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = mutations.create.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.sms_extra_team_ids).toEqual(['t3']);
    expect(payload).not.toHaveProperty('mail_extra_team_ids');
  });
});

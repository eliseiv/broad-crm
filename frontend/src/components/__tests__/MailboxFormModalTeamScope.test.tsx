import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import type { MailMailbox } from '@/types/api';

/**
 * Селектор «Команда» формы ящика: источник опций, прод-баг 2026-07-14 и «зеркало текущего
 * состояния» (ADR-054 §3, ADR-055 §6.3/§6.3.1, TD-050).
 *
 * **Прод-баг, который здесь стережётся.** Роль «Пользователь» с ПОЛНЫМ `mail:*`, но без
 * `teams:view`, НЕ МОГЛА создать ящик: селектор наполнялся из `GET /api/teams` (гейт
 * `teams:view`) ⇒ список приходил ПУСТЫМ ⇒ единственным вариантом оставалась опция «Без
 * команды», а она admin-only (создание ящика с `team_id=null` — ADR-044 §4, НЕ разворачивается)
 * ⇒ ЛЮБОЙ выбор давал `403 forbidden` («Недостаточно прав»). Тупик.
 *
 * Норма (§6.3): опции — из `me.mail_teams` (`GET /api/auth/me`); «Без команды» показывается
 * ТОЛЬКО admin-уровню (гейт `sees_all_mail_teams`, а НЕ `mail_includes_unassigned`).
 * Принцип: **вариант, который пользователь не вправе выбрать, не ПРЕДЛАГАЕТСЯ к выбору.**
 *
 * Исключение §6.3.1 («зеркало текущего состояния») — ТОЛЬКО при ВСЕХ трёх условиях:
 * (1) опция = ТЕКУЩЕЕ значение редактируемого объекта; (2) выбор физически невозможен
 * (контрол `disabled` целиком); (3) без опции `<select>` показал бы ЧУЖУЮ команду.
 * Режим `add` под исключение НЕ подпадает НИКОГДА (текущего значения нет) — там и жил баг.
 */

const perms = vi.hoisted(() => ({ canCreate: true, seesAll: false }));
const mailScope = vi.hoisted(() => ({
  value: {
    teams: [] as { id: string; name: string }[],
    includesUnassigned: false,
  },
}));
const mutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));
/** Спай `GET /api/teams`: в форме ящика он НЕ должен вызываться ни под кем (§6.3). */
const teamsSpy = vi.hoisted(() => vi.fn(() => ({ data: { items: [] } })));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) =>
    page === 'mail' && action === 'create' && perms.canCreate,
  useSeesAllMailTeams: () => perms.seesAll,
  useChannelTeamScope: () => mailScope.value,
}));

vi.mock('@/features/teams/hooks', () => ({ useTeams: teamsSpy }));

vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: undefined }) }));

vi.mock('@/features/mail/hooks', () => ({
  mailMailboxesKey: ['mail', 'mailboxes'],
  useCreateMailbox: () => ({ mutate: mutations.create, isPending: false }),
  useTestMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMailbox: () => ({ mutate: mutations.update, isPending: false }),
  useMailboxOAuthAuthorize: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const SALES = { id: 'team-3', name: 'Продажи' };
const SUPPORT = { id: 'team-9', name: 'Поддержка' };

function mailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 1,
    email: 'inbox@postapp.store',
    number: '5108',
    app_name: 'Klyro Forge',
    display_name: '5108 Klyro Forge',
    team_id: SALES.id,
    is_active: true,
    last_synced_at: null,
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

function teamSelect(): HTMLSelectElement {
  return screen.getByLabelText('Команда') as HTMLSelectElement;
}

function optionLabels(): string[] {
  return Array.from(teamSelect().options).map((o) => o.textContent ?? '');
}

beforeEach(() => {
  vi.clearAllMocks();
  perms.canCreate = true;
  perms.seesAll = false;
  mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: false };
});

afterEach(() => {
  vi.clearAllMocks();
});

// --- Источник опций: /api/auth/me, НЕ GET /api/teams (§6.3) -------------------

describe('MailboxFormModal — источник команд канала (ADR-055 §6.3, закрывает TD-050)', () => {
  it('не-админ (без teams:view): опции = me.mail_teams; GET /api/teams НЕ вызывается', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).toEqual(['Продажи', 'Поддержка']);
    expect(teamsSpy).not.toHaveBeenCalled();
  });

  it('admin-уровень: тот же источник (`me.mail_teams`), GET /api/teams тоже НЕ вызывается', () => {
    // Ветвления «admin ↔ не-админ» по источнику в клиенте НЕТ (§6.3): источник один на всех.
    perms.seesAll = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).toContain('Продажи');
    expect(teamsSpy).not.toHaveBeenCalled();
  });

  it('режим edit — тот же источник и тоже без GET /api/teams', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={mailbox()} />);

    expect(optionLabels()).toEqual(['Продажи', 'Поддержка']);
    expect(teamsSpy).not.toHaveBeenCalled();
  });
});

// --- Прод-баг 2026-07-14: «Без команды» не-админу не предлагается ---------------

describe('MailboxFormModal — опция «Без команды» (прод-баг 2026-07-14, ADR-055 §6.3)', () => {
  it('не-админ, режим add: опции «Без команды» в DOM НЕТ (она admin-only → гарантированный 403)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).not.toContain('Без команды');
  });

  it('не-админ С `mail_includes_unassigned=true`: «Без команды» ВСЁ РАВНО не предлагается', () => {
    // ⚠️ Гейт опции — `sees_all_mail_teams`, а НЕ `mail_includes_unassigned` (§6.3):
    // флаг даёт работу с СУЩЕСТВУЮЩИМИ бесхозными ящиками, но НЕ право создавать новые.
    mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: true };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).not.toContain('Без команды');
  });

  it('admin-уровень, режим add: «Без команды» ЕСТЬ (он вправе её выбрать)', () => {
    perms.seesAll = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).toContain('Без команды');
  });

  it('не-админ: предвыбрана ПЕРВАЯ доступная команда (submit не уйдёт с team_id=null)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(teamSelect().value).toBe(SALES.id);
  });

  it('не-админ с НУЛЁМ команд канала: кнопка «Добавить» disabled + причина под селектором', () => {
    // Ровно то состояние прод-бага: выбрать нечего, а «Без команды» ему запрещена ⇒ UI не
    // производит заведомо запрещённый вариант (кнопка заблокирована), а не ведёт в 403.
    mailScope.value = { teams: [], includesUnassigned: false };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.getByRole('button', { name: 'Добавить' })).toBeDisabled();
    expect(screen.getByText('Нет доступных команд — обратитесь к администратору.')).toBeVisible();
  });

  it('РЕГРЕСС-ГАРД: admin-уровень с тем же НУЛЁМ команд — «Добавить» АКТИВНА', () => {
    // Ему доступна опция «Без команды» ⇒ создать ящик он вправе. Блокировка кнопки обязана
    // гейтиться правом актора, а не пустым списком команд как таковым.
    perms.seesAll = true;
    mailScope.value = { teams: [], includesUnassigned: false };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).toEqual(['Без команды']);
    expect(screen.getByRole('button', { name: 'Добавить' })).toBeEnabled();
  });
});

// --- Поздний резолв профиля: показанное = отправляемое ------------------------

describe('MailboxFormModal — поздний резолв `/me` (показанное = отправляемое)', () => {
  it('`me.mail_teams` доехал ПОСЛЕ открытия формы → значение поля синхронизировано', async () => {
    // `useMe` ещё в полёте на момент монтирования ⇒ у не-админа `teamId` остался бы `NO_TEAM`,
    // а нативный `<select>` показал бы ПЕРВУЮ появившуюся команду. Submit ушёл бы с
    // `team_id: null` ⇒ гарантированный 403 — тот же прод-баг в другой обёртке.
    mailScope.value = { teams: [], includesUnassigned: false };
    const { rerender } = render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    // Профиль доехал: опции появились.
    mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: false };
    rerender(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await waitFor(() => expect(teamSelect().value).toBe(SALES.id));
    // Показанное И отправляемое — одна и та же команда, а не `null`.
    expect(teamSelect().selectedOptions[0].textContent).toBe('Продажи');
    expect(screen.getByRole('button', { name: 'Добавить' })).toBeEnabled();
  });

  it('после позднего резолва создание уходит с team_id выбранной команды, не с null', async () => {
    mailScope.value = { teams: [], includesUnassigned: false };
    const { rerender } = render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    mailScope.value = { teams: [SALES], includesUnassigned: false };
    rerender(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);
    await waitFor(() => expect(teamSelect().value).toBe(SALES.id));

    await userEvent.type(screen.getByLabelText('Адрес почты'), 'new@example.com');
    await userEvent.type(screen.getByLabelText('Код приложения'), 'app-code');
    await userEvent.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
    await userEvent.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledTimes(1);
    const payload = mutations.create.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.team_id).toBe(SALES.id);
    expect(payload.team_id).not.toBeNull();
  });
});

// --- §6.3.1 «зеркало текущего состояния» -------------------------------------

describe('MailboxFormModal — исключение «зеркало текущего состояния» (ADR-055 §6.3.1)', () => {
  it('не-админ + флаг, edit БЕСХОЗНОГО ящика: селектор disabled и показывает «Без команды»', () => {
    // Все три условия §6.3.1 выполнены: (1) опция = текущее значение ящика (`team_id = null`);
    // (2) контрол `disabled` целиком (перенос ящика — admin-only) ⇒ пути в 403 нет;
    // (3) без опции `<select>` с `value ∉ options` показал бы ЧУЖУЮ команду («Продажи»).
    mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: true };
    render(
      <MailboxFormModal
        open
        onOpenChange={vi.fn()}
        mode="edit"
        mailbox={mailbox({ team_id: null })}
      />,
    );

    const select = teamSelect();
    expect(select).toBeDisabled();
    // Отображается ФАКТИЧЕСКОЕ состояние, а не первая чужая команда — иначе UI соврал бы.
    expect(select.selectedOptions[0].textContent).toBe('Без команды');
    expect(select.selectedOptions[0].textContent).not.toBe('Продажи');
  });

  it('АНТИ-ЛАЗЕЙКА: тот же актор в режиме `add` опции «Без команды» НЕ видит', () => {
    // Режим `add` под исключение НЕ подпадает НИКОГДА (условие 1 невыполнимо: текущего
    // значения объекта нет). Именно там жил прод-баг: опция ПРЕДЛАГАЛАСЬ к выбору в
    // ENABLED-контроле и гарантированно вела в 403.
    mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: true };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(optionLabels()).not.toContain('Без команды');
    expect(teamSelect()).toBeEnabled();
  });

  it('не-админ, edit ящика СО СВОЕЙ командой: «Без команды» не подмешивается', () => {
    // Условие 1 не выполнено (текущее значение — команда, не «нет команды») ⇒ зеркала нет.
    mailScope.value = { teams: [SALES, SUPPORT], includesUnassigned: true };
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={mailbox()} />);

    expect(optionLabels()).not.toContain('Без команды');
    expect(teamSelect().value).toBe(SALES.id);
    expect(teamSelect()).toBeDisabled(); // перенос — admin-only (§3)
  });

  it('admin-уровень, edit: селектор ENABLED (перенос ящика разрешён) и несёт «Без команды»', () => {
    perms.seesAll = true;
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="edit" mailbox={mailbox()} />);

    expect(teamSelect()).toBeEnabled();
    expect(optionLabels()).toContain('Без команды');
  });
});

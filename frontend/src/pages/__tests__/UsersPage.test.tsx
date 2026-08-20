import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UsersPage } from '@/pages/UsersPage';
import type {
  RoleListResponse,
  TeamListResponse,
  UserListItem,
  UserListResponse,
} from '@/types/api';

const state = vi.hoisted(() => ({
  users: undefined as UserListResponse | undefined,
  roles: undefined as RoleListResponse | undefined,
  teams: undefined as TeamListResponse | undefined,
}));

const resetPasswordMock = vi.fn();

vi.mock('@/features/users/hooks', () => ({
  useUsers: () => ({
    data: state.users,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useRoles: () => ({
    data: state.roles,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteUser: () => ({ mutate: vi.fn(), isPending: false }),
  useResetUserPassword: () => ({ mutate: resetPasswordMock, isPending: false }),
}));

vi.mock('@/features/teams/hooks', () => ({
  useTeams: () => ({
    data: state.teams,
    isLoading: false,
    isError: false,
    isFetching: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeUser(
  over: Partial<UserListItem> & Pick<UserListItem, 'id' | 'username'>,
): UserListItem {
  return {
    // ADR-079 §7: ФИО nullable — историческая строка отображается по `username`.
    last_name: null,
    first_name: null,
    middle_name: null,
    roles: [{ id: 'r2', name: 'Оператор' }],
    telegram: null,
    has_password: true,
    is_active: true,
    status: 'active',
    teams: [],
    // ADR-055 §5.2: `UserListItem` несёт ТОЛЬКО добавку канала (без базовых `teams`).
    mail_extra_teams: [],
    mail_extra_includes_unassigned: false,
    sms_extra_teams: [],
    sms_extra_includes_unassigned: false,
    bot_started: false,
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T09:00:00Z',
    ...over,
  };
}

const ROLES: RoleListResponse = {
  items: [
    {
      id: 'r2',
      name: 'Оператор',
      permissions: { servers: ['view'] },
      user_count: 2,
      created_at: '2026-07-07T09:00:00Z',
      updated_at: '2026-07-07T09:00:00Z',
    },
  ],
};

const TEAMS: TeamListResponse = {
  items: [
    {
      id: 't1',
      name: 'Продажи',
      leader_id: 'u1',
      leader_username: 'Никита',
      member_count: 1,
      number_count: 0,
      mailbox_count: 0,
      members: [{ id: 'u1', username: 'Никита' }],
      created_at: '2026-07-08T09:00:00Z',
      updated_at: '2026-07-08T09:00:00Z',
    },
  ],
};

/** Кнопки «Открыть» строк таблицы в порядке DOM (accessible name — с именем пользователя). */
function openButtons(): HTMLElement[] {
  return screen.getAllByRole('button', { name: /^Открыть / });
}

/** Строка таблицы, содержащая переданный текст. */
function rowWith(text: string): HTMLElement {
  return screen.getByText(text).closest('tr') as HTMLElement;
}

describe('UsersPage (таблица пользователей, ADR-079 §10)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.users = undefined;
    state.roles = ROLES;
    state.teams = TEAMS;
  });

  it('renders the empty state for users', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.getByText('Пока нет пользователей')).toBeInTheDocument();
  });

  it('не рендерит H1-заголовок страницы (убран, ADR-029)', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    // Внутристраничный H1 «Пользователи» + подпись убраны — раздел обозначен навигацией.
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  });

  it('does NOT render a roles section or «Добавить роль» (moved to «Роли», ADR-022)', () => {
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });

    expect(screen.queryByRole('button', { name: 'Добавить роль' })).not.toBeInTheDocument();
    expect(screen.queryByText('Пока нет ролей')).not.toBeInTheDocument();
  });

  it('рендерит таблицу с нормативными колонками и одной строкой на пользователя', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'nikita_01',
          last_name: 'Петров',
          first_name: 'Никита',
          telegram: 'nikita_01',
          teams: [{ id: 't1', name: 'Продажи' }],
        }),
        makeUser({ id: 'u2', username: 'Пётр', is_active: false, status: 'inactive' }),
      ],
    };

    render(<UsersPage />, { wrapper });

    for (const column of ['ФИО', 'Роли', 'Команды', 'Telegram', 'Статус', 'Бот', 'Действия']) {
      expect(screen.getByRole('columnheader', { name: column })).toBeInTheDocument();
    }
    // Группировка по командам упразднена (ADR-065; норма сохранена ADR-079 §10).
    expect(screen.queryByRole('heading', { name: 'Продажи' })).not.toBeInTheDocument();

    expect(openButtons()).toHaveLength(2);
    // ФИО схлопывает пустые части; фолбэк на username — у строки без ФИО.
    expect(screen.getByText('Петров Никита')).toBeInTheDocument();
    expect(screen.getByText('Пётр')).toBeInTheDocument();
    expect(screen.getByText('@nikita_01')).toBeInTheDocument();
  });

  it('пользователь в нескольких командах занимает РОВНО одну строку', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'multi',
          first_name: 'Мультикомандный',
          teams: [
            { id: 't1', name: 'Продажи' },
            { id: 't2', name: 'Маркетинг' },
          ],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    expect(openButtons()).toHaveLength(1);
    const row = rowWith('Мультикомандный');
    expect(within(row).getByText('Продажи')).toBeInTheDocument();
    expect(within(row).getByText('Маркетинг')).toBeInTheDocument();
  });

  it('сортирует пользователей по ФИО через localeCompare («ru»), а не по code-unit', () => {
    // Порядок API — намеренно неотсортированный. localeCompare('ru'): Анна < борис < Яков
    // (кириллица по алфавиту, регистр — третичный). Наивный code-unit-sort дал бы
    // Анна(0x410) < Яков(0x42F) < борис(0x431) — иной порядок, что и различает кейс.
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'yakov', first_name: 'Яков' }),
        makeUser({ id: 'u2', username: 'boris', first_name: 'борис' }),
        makeUser({ id: 'u3', username: 'anna', first_name: 'Анна' }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const order = openButtons().map((b) => b.getAttribute('aria-label'));
    expect(order).toEqual(['Открыть Анна', 'Открыть борис', 'Открыть Яков']);
  });

  it('рендерит роли и команды чипами (ui/Pill), фолбэк «Без команды» — подписью', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'nikita',
          first_name: 'Никита',
          roles: [{ id: 'r2', name: 'Оператор' }],
          teams: [{ id: 't1', name: 'Продажи' }],
        }),
        makeUser({ id: 'u2', username: 'odinochka', first_name: 'Одиночка', teams: [] }),
      ],
    };

    render(<UsersPage />, { wrapper });

    // Чип — примитив ui/Pill: span с сигнатурными классами rounded-chip + инлайн-tone.
    for (const label of ['Оператор', 'Продажи']) {
      const chip = within(rowWith('Никита')).getByText(label);
      expect(chip.tagName).toBe('SPAN');
      expect(chip).toHaveClass('rounded-chip');
      expect(chip.getAttribute('style') ?? '').not.toBe('');
    }

    const fallback = within(rowWith('Одиночка')).getByText('Без команды');
    expect(fallback).toHaveClass('text-text-secondary');
    expect(fallback).not.toHaveClass('rounded-chip');
  });

  it('колонка ФИО показывает ТОЛЬКО ФИО — технического username в строке нет', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'nikita_01', last_name: 'Петров', first_name: 'Никита' }),
        makeUser({ id: 'u2', username: 'Пётр' }),
      ],
    };

    render(<UsersPage />, { wrapper });

    // Прежде под ФИО второй строкой печатался username — убрано: он дублировал
    // значение у исторических учёток и шумел у остальных.
    expect(screen.queryByText('nikita_01')).not.toBeInTheDocument();
    // Строка без ФИО по-прежнему опознаётся: фолбэк-имя = username.
    expect(screen.getAllByText('Пётр')).toHaveLength(1);
  });

  it('сортировка по ФИО / Ролям / Командам переключается кликом по заголовку', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'b',
          last_name: 'Яковлев',
          first_name: 'Яков',
          roles: [{ id: 'r1', name: 'Админ' }],
        }),
        makeUser({
          id: 'u2',
          username: 'a',
          last_name: 'Абрамов',
          first_name: 'Артём',
          roles: [{ id: 'r2', name: 'Оператор' }],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const names = () =>
      screen
        .getAllByRole('row')
        .slice(1)
        .map((row) => row.querySelector('td')?.textContent);

    // По умолчанию — ФИО по возрастанию.
    expect(names()).toEqual(['Абрамов Артём', 'Яковлев Яков']);

    // Повторный клик по той же колонке разворачивает направление.
    await user.click(screen.getByRole('button', { name: /ФИО/ }));
    expect(names()).toEqual(['Яковлев Яков', 'Абрамов Артём']);

    // Смена колонки начинает с возрастания: «Админ» < «Оператор».
    await user.click(screen.getByRole('button', { name: /Роли/ }));
    expect(names()).toEqual(['Яковлев Яков', 'Абрамов Артём']);
    await user.click(screen.getByRole('button', { name: /Роли/ }));
    expect(names()).toEqual(['Абрамов Артём', 'Яковлев Яков']);
  });

  it('рендерит тристатус-бейдж (ADR-028): «Ожидает входа» / «Активен» / «Неактивен»', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'p', first_name: 'Ожидающий', status: 'pending' }),
        makeUser({ id: 'u2', username: 'a', first_name: 'Активный', status: 'active' }),
        makeUser({
          id: 'u3',
          username: 'i',
          first_name: 'Выключенный',
          is_active: false,
          status: 'inactive',
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    expect(within(rowWith('Ожидающий')).getByText('Ожидает входа')).toBeInTheDocument();
    expect(within(rowWith('Активный')).getByText('Активен')).toBeInTheDocument();
    expect(within(rowWith('Выключенный')).getByText('Неактивен')).toBeInTheDocument();
  });

  it('бейдж «Бот» / «Бот не запущен» из bot_started (ADR-076)', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 's', first_name: 'СБотом', bot_started: true }),
        makeUser({ id: 'u2', username: 'b', first_name: 'БезБота', bot_started: false }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const started = rowWith('СБотом');
    const notStarted = rowWith('БезБота');
    expect(within(started).getByText('Бот')).toHaveClass('text-status-green');
    expect(within(notStarted).getByText('Бот не запущен')).toHaveClass('text-status-red');
    expect(within(started).queryByText('Бот не запущен')).not.toBeInTheDocument();
  });

  it('показывает бейдж «Без пароля» для беспарольного и не показывает для парольного (ADR-025)', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'np', first_name: 'Беспарольный', has_password: false }),
        makeUser({ id: 'u2', username: 'wp', first_name: 'Парольный', has_password: true }),
      ],
    };

    render(<UsersPage />, { wrapper });

    expect(screen.getAllByText('Без пароля')).toHaveLength(1);
    expect(within(rowWith('Беспарольный')).getByText('Без пароля')).toBeInTheDocument();
    expect(within(rowWith('Парольный')).queryByText('Без пароля')).not.toBeInTheDocument();
  });

  it('считает сводные плашки по производным полям (ADR-079 §10)', () => {
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'a',
          first_name: 'Один',
          status: 'active',
          bot_started: true,
        }),
        makeUser({ id: 'u2', username: 'b', first_name: 'Два', status: 'pending' }),
        makeUser({
          id: 'u3',
          username: 'c',
          first_name: 'Три',
          status: 'inactive',
          is_active: false,
        }),
      ],
    };

    const { container } = render(<UsersPage />, { wrapper });

    const cellValue = (label: string) =>
      [...container.querySelectorAll('p')].find((p) => p.textContent === label)?.nextElementSibling
        ?.textContent;
    expect(cellValue('Всего')).toBe('3');
    expect(cellValue('Активны')).toBe('1');
    expect(cellValue('Ожидают входа')).toBe('1');
    expect(cellValue('Активны в боте')).toBe('1');
  });

  it('клиентский поиск фильтрует по ФИО / username / telegram', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'petrov', last_name: 'Петров', first_name: 'Никита' }),
        makeUser({ id: 'u2', username: 'sidorov', last_name: 'Сидоров', first_name: 'Иван' }),
      ],
    };

    render(<UsersPage />, { wrapper });
    await user.type(screen.getByLabelText('Поиск по ФИО или Телеграму'), 'петров');

    // Ввод под debounce — ждём применения фильтра (регистр снят: «петров» ↔ «Петров»).
    await waitFor(() => expect(screen.queryByText('Сидоров Иван')).not.toBeInTheDocument());
    expect(screen.getByText('Петров Никита')).toBeInTheDocument();
  });

  it('клиентский поиск матчит username, а не только видимое ФИО', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        // ФИО заполнено — `nikita_01` в колонке ФИО НЕ видно, но искаться обязано:
        // это фолбэк того, по чему оператор помнит исторические учётки.
        makeUser({ id: 'u1', username: 'nikita_01', last_name: 'Петров', first_name: 'Никита' }),
        makeUser({ id: 'u2', username: 'sidorov', last_name: 'Сидоров', first_name: 'Иван' }),
      ],
    };

    render(<UsersPage />, { wrapper });
    await user.type(screen.getByLabelText('Поиск по ФИО или Телеграму'), 'nikita_01');

    await waitFor(() => expect(screen.queryByText('Сидоров Иван')).not.toBeInTheDocument());
    expect(screen.getByText('Петров Никита')).toBeInTheDocument();
  });

  it('клиентский поиск матчит telegram (регистр снят)', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        makeUser({
          id: 'u1',
          username: 'petrov',
          last_name: 'Петров',
          first_name: 'Никита',
          telegram: 'Nikita_TG',
        }),
        makeUser({ id: 'u2', username: 'sidorov', last_name: 'Сидоров', first_name: 'Иван' }),
      ],
    };

    render(<UsersPage />, { wrapper });
    await user.type(screen.getByLabelText('Поиск по ФИО или Телеграму'), 'nikita_tg');

    await waitFor(() => expect(screen.queryByText('Сидоров Иван')).not.toBeInTheDocument());
    expect(screen.getByText('Петров Никита')).toBeInTheDocument();
  });

  it('поиск без совпадений даёт «Ничего не найдено», а НЕ «Пока нет пользователей»', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'petrov', last_name: 'Петров', first_name: 'Никита' }),
      ],
    };

    render(<UsersPage />, { wrapper });
    await user.type(screen.getByLabelText('Поиск по ФИО или Телеграму'), 'зззз');

    await waitFor(() => expect(screen.getByText('Ничего не найдено')).toBeInTheDocument());
    // Пустой результат фильтра ≠ пустой реестр — состояния разведены.
    expect(screen.queryByText('Пока нет пользователей')).not.toBeInTheDocument();
  });

  it('сводные плашки считаются по ПОЛНОМУ списку и не меняются от строки поиска', async () => {
    const user = userEvent.setup();
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'petrov', last_name: 'Петров', first_name: 'Никита' }),
        makeUser({ id: 'u2', username: 'sidorov', last_name: 'Сидоров', first_name: 'Иван' }),
      ],
    };

    const { container } = render(<UsersPage />, { wrapper });
    const total = () =>
      [...container.querySelectorAll('p')].find((p) => p.textContent === 'Всего')
        ?.nextElementSibling?.textContent;
    expect(total()).toBe('2');

    await user.type(screen.getByLabelText('Поиск по ФИО или Телеграму'), 'петров');

    await waitFor(() => expect(screen.queryByText('Сидоров Иван')).not.toBeInTheDocument());
    expect(total()).toBe('2');
  });

  it('фолбэк «Без роли» — подписью, а не чипом (ADR-079 §10)', () => {
    state.users = {
      items: [
        makeUser({ id: 'u1', username: 'norole', first_name: 'Безролевой', roles: [] }),
        makeUser({
          id: 'u2',
          username: 'withrole',
          first_name: 'Ролевой',
          roles: [{ id: 'r2', name: 'Оператор' }],
        }),
      ],
    };

    render(<UsersPage />, { wrapper });

    const fallback = within(rowWith('Безролевой')).getByText('Без роли');
    expect(fallback).toHaveClass('text-text-secondary');
    expect(fallback).not.toHaveClass('rounded-chip');
    expect(within(rowWith('Ролевой')).queryByText('Без роли')).not.toBeInTheDocument();
  });

  it('opens the add-user modal from the toolbar (поля «Логин» в форме нет)', async () => {
    const user = userEvent.setup();
    state.users = { items: [] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Добавить пользователя' }));

    expect(screen.getByLabelText('Фамилия')).toBeInTheDocument();
    // Поле «Логин» удалено из формы (ADR-079 §9).
    expect(screen.queryByLabelText('Логин')).not.toBeInTheDocument();
  });

  it('opens the edit-user modal from the row action', async () => {
    const user = userEvent.setup();
    state.users = { items: [makeUser({ id: 'u1', username: 'nikita', first_name: 'Никита' })] };

    render(<UsersPage />, { wrapper });
    await user.click(screen.getByRole('button', { name: 'Открыть Никита' }));

    expect(screen.getByRole('heading', { name: 'Изменить пользователя' })).toBeInTheDocument();
  });

  it('кнопка «Сброс» открывает подтверждение и сбрасывает пароль (ADR-025)', async () => {
    const user = userEvent.setup();
    state.users = { items: [makeUser({ id: 'u1', username: 'nikita', first_name: 'Никита' })] };

    render(<UsersPage />, { wrapper });

    // Кнопка стоит рядом с «Открыть» в колонке действий каждой строки.
    const resetButtons = screen.getAllByRole('button', { name: /^Сбросить пароль — / });
    expect(resetButtons.length).toBe(1);

    await user.click(resetButtons[0]);

    // Подтверждение обязательно: сброс необратим для пользователя.
    expect(await screen.findByText('Сбросить пароль?')).toBeInTheDocument();
    expect(resetPasswordMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Сбросить' }));
    expect(resetPasswordMock).toHaveBeenCalledTimes(1);
    expect(resetPasswordMock.mock.calls[0][0]).toBe('u1');
  });
});

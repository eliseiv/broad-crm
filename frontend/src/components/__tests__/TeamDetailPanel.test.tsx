import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TeamDetailPanel } from '@/components/TeamDetailPanel';
import type { TeamListItem, TeamMailboxItem, TeamNumberItem } from '@/types/api';

// Ленивые GET /teams/{id}/numbers и /teams/{id}/mailboxes через хуки: обе секции
// свёрнуты по умолчанию, запрос `enabled` привязан к раскрытию (ADR-038). Моки
// фиксируют аргумент `enabled`, чтобы проверить ленивость.
const teamNumbers = vi.hoisted(() => ({
  value: null as unknown,
  calls: [] as { id: string; enabled: boolean }[],
}));
const teamMailboxes = vi.hoisted(() => ({
  value: null as unknown,
  calls: [] as { id: string; enabled: boolean }[],
}));

vi.mock('@/features/sms/hooks', () => ({
  useTeamNumbers: (id: string, enabled: boolean) => {
    teamNumbers.calls.push({ id, enabled });
    return teamNumbers.value;
  },
}));

vi.mock('@/features/mail/hooks', () => ({
  useTeamMailboxes: (id: string, enabled: boolean) => {
    teamMailboxes.calls.push({ id, enabled });
    return teamMailboxes.value;
  },
}));

function makeNumber(id: number, over: Partial<TeamNumberItem> = {}): TeamNumberItem {
  return {
    id,
    phone_number: '+15557778888',
    team: { id: 't1', name: 'Продажи' },
    login: null,
    app_name: null,
    ...over,
  };
}

function makeMailbox(id: number, over: Partial<TeamMailboxItem> = {}): TeamMailboxItem {
  return {
    id,
    email: `box${id}@postapp.store`,
    number: null,
    app_name: null,
    display_name: null,
    is_active: true,
    ...over,
  };
}

function makeTeam(over: Partial<TeamListItem> = {}): TeamListItem {
  return {
    id: 't1',
    name: 'Продажи',
    leader_id: 'u1',
    leader_username: 'Никита',
    member_count: 2,
    number_count: 1,
    mailbox_count: 1,
    members: [
      { id: 'u1', username: 'Никита' },
      { id: 'u2', username: 'Мария' },
    ],
    created_at: '2026-07-08T09:00:00Z',
    updated_at: '2026-07-08T09:00:00Z',
    ...over,
  };
}

const TEAM = makeTeam();

interface QueryOverrides {
  data?: unknown;
  isLoading?: boolean;
  isError?: boolean;
  isFetching?: boolean;
  refetch?: () => void;
}

function query(over: QueryOverrides = {}) {
  return {
    data: over.data,
    isLoading: over.isLoading ?? false,
    isError: over.isError ?? false,
    isFetching: over.isFetching ?? false,
    refetch: over.refetch ?? vi.fn(),
  };
}

function numbersToggle(): HTMLElement {
  return screen.getByRole('button', { name: /Номера команды/ });
}

function mailboxesToggle(): HTMLElement {
  return screen.getByRole('button', { name: /Почты команды/ });
}

describe('TeamDetailPanel (свёрнутые секции + ленивая загрузка, ADR-038)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    teamNumbers.value = query({ data: { numbers: [] } });
    teamNumbers.calls = [];
    teamMailboxes.value = query({ data: { mailboxes: [] } });
    teamMailboxes.calls = [];
  });

  // --- шапка + свёрнутость по умолчанию ---
  it('шапка команды (имя/лидер/участники) видна всегда, вне зависимости от секций', () => {
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    expect(screen.getByText('Продажи')).toBeInTheDocument();
    expect(screen.getAllByText('Никита').length).toBeGreaterThan(0);
    expect(screen.getByText('Мария')).toBeInTheDocument();
  });

  it('обе секции свёрнуты по умолчанию: контент не отрендерен, запросы enabled=false', () => {
    teamNumbers.value = query({ data: { numbers: [makeNumber(7)] } });
    teamMailboxes.value = query({ data: { mailboxes: [makeMailbox(1)] } });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);

    // Контент секций не в DOM (свёрнуто).
    expect(screen.queryByText('+15557778888')).not.toBeInTheDocument();
    expect(screen.queryByText('box1@postapp.store')).not.toBeInTheDocument();
    expect(numbersToggle()).toHaveAttribute('aria-expanded', 'false');
    expect(mailboxesToggle()).toHaveAttribute('aria-expanded', 'false');
    // Ленивость: до раскрытия ни один запрос не enabled.
    expect(teamNumbers.calls.every((c) => c.enabled === false)).toBe(true);
    expect(teamMailboxes.calls.every((c) => c.enabled === false)).toBe(true);
  });

  // --- секция «Номера команды» ---
  it('раскрытие «Номера команды» → ленивая загрузка (enabled=true) и рендер номеров', async () => {
    const user = userEvent.setup();
    teamNumbers.value = query({ data: { numbers: [makeNumber(7)] } });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);

    await user.click(numbersToggle());

    expect(screen.getByText('+15557778888')).toBeInTheDocument();
    expect(numbersToggle()).toHaveAttribute('aria-expanded', 'true');
    // После раскрытия запрос стал enabled (ленивая загрузка).
    expect(teamNumbers.calls.some((c) => c.enabled === true && c.id === 't1')).toBe(true);
  });

  it('номера: loading → индикатор загрузки после раскрытия', async () => {
    const user = userEvent.setup();
    teamNumbers.value = query({ isLoading: true });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(numbersToggle());
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
  });

  it('номера: empty → «Номеров нет» после раскрытия', async () => {
    const user = userEvent.setup();
    teamNumbers.value = query({ data: { numbers: [] } });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(numbersToggle());
    expect(screen.getByText('Номеров нет')).toBeInTheDocument();
  });

  it('номера: error → заглушка + «Повторить» вызывает refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    teamNumbers.value = query({ isError: true, refetch });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(numbersToggle());

    expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Повторить/ }));
    expect(refetch).toHaveBeenCalled();
  });

  it('номера: пилюли Логин/Приложение; пустые → «-» (ADR-034)', async () => {
    const user = userEvent.setup();
    teamNumbers.value = query({
      data: { numbers: [makeNumber(7, { login: 'sales01', app_name: null })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(numbersToggle());

    expect(screen.getByText('Логин: sales01')).toBeInTheDocument();
    expect(screen.getByText('Приложение: -')).toBeInTheDocument();
    expect(screen.queryByText(/Примечание/)).not.toBeInTheDocument();
  });

  // --- секция «Почты команды» (ADR-038) ---
  it('раскрытие «Почты команды» → ленивая загрузка (enabled=true) и рендер ящиков', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: { mailboxes: [makeMailbox(1, { email: 'inbox@postapp.store', is_active: true })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);

    await user.click(mailboxesToggle());

    expect(screen.getByText('inbox@postapp.store')).toBeInTheDocument();
    expect(screen.getByText('Активна')).toBeInTheDocument();
    expect(teamMailboxes.calls.some((c) => c.enabled === true && c.id === 't1')).toBe(true);
  });

  // Статус — ВИДИМЫЙ текст «Активна»/«Неактивна» (не sr-only), критерий — только
  // `is_active` (08-design-system.md §Страница «Команды», ADR-048 §3).
  it('почты: неактивный ящик → статус «Неактивна» видимым текстом', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: { mailboxes: [makeMailbox(1, { is_active: false })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    const status = screen.getByText('Неактивна');
    expect(status).toBeVisible();
    expect(status).not.toHaveClass('sr-only');
    expect(screen.queryByText('Активна')).not.toBeInTheDocument();
  });

  it('почты: строка ящика рендерит «Номер» и «Приложение» (ADR-048 §3)', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: {
        mailboxes: [
          makeMailbox(1, {
            email: 'inbox@postapp.store',
            number: '5108',
            app_name: 'Klyro Forge (Codex)',
          }),
        ],
      },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    expect(screen.getByText('inbox@postapp.store')).toBeInTheDocument();
    expect(screen.getByText('Номер')).toBeInTheDocument();
    expect(screen.getByText('5108')).toBeInTheDocument();
    expect(screen.getByText('Приложение')).toBeInTheDocument();
    expect(screen.getByText('Klyro Forge (Codex)')).toBeInTheDocument();
  });

  // Лейбл без значения не показывается никогда (ADR-047 §5, унаследовано ADR-048 §3):
  // при `null` не рендерится ВСЯ пара «лейбл + значение».
  it('почты: number/app_name = null → пары «лейбл + значение» не рендерятся', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: { mailboxes: [makeMailbox(1, { number: null, app_name: null })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    // Секция «Номера команды» свёрнута → лейблы могут быть только из строки почты.
    expect(screen.queryByText('Номер')).not.toBeInTheDocument();
    expect(screen.queryByText('Приложение')).not.toBeInTheDocument();
    // Строка при обоих null = статус + email.
    expect(screen.getByText('box1@postapp.store')).toBeInTheDocument();
    expect(screen.getByText('Активна')).toBeInTheDocument();
  });

  it('почты: только number задан → «Приложение» не рендерится, «Номер» рендерится', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: { mailboxes: [makeMailbox(1, { number: '5108', app_name: null })] },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    expect(screen.getByText('Номер')).toBeInTheDocument();
    expect(screen.getByText('5108')).toBeInTheDocument();
    expect(screen.queryByText('Приложение')).not.toBeInTheDocument();
  });

  // display_name — производное («<number> <app_name>», ADR-047 §3.3): поле остаётся в
  // схеме, но в строке detail-панели НЕ рендерится (ADR-048 §2/§3), иначе имя дублируется.
  it('почты: display_name в строке НЕ рендерится (ADR-048 §2)', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({
      data: {
        mailboxes: [
          makeMailbox(1, {
            number: '5108',
            app_name: 'Klyro Forge (Codex)',
            display_name: '5108 Klyro Forge (Codex)',
          }),
        ],
      },
    });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    expect(screen.queryByText('5108 Klyro Forge (Codex)')).not.toBeInTheDocument();
    // Составляющие показаны явно — по отдельности.
    expect(screen.getByText('5108')).toBeInTheDocument();
    expect(screen.getByText('Klyro Forge (Codex)')).toBeInTheDocument();
  });

  it('почты: ящиков нет → «Почт нет» (групп агрегатора больше нет, ADR-044)', async () => {
    const user = userEvent.setup();
    teamMailboxes.value = query({ data: { mailboxes: [] } });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());
    expect(screen.getByText('Почт нет')).toBeInTheDocument();
  });

  it('почты: error → заглушка + «Повторить» вызывает refetch', async () => {
    const user = userEvent.setup();
    const refetch = vi.fn();
    teamMailboxes.value = query({ isError: true, refetch });
    render(<TeamDetailPanel team={TEAM} id="panel-1" />);
    await user.click(mailboxesToggle());

    expect(screen.getByText('Не удалось загрузить')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Повторить/ }));
    expect(refetch).toHaveBeenCalled();
  });
});

import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxesTab } from '@/components/MailboxesTab';
import { loginAs, logout } from '@/test/authTestUtils';
import type { MailMailbox } from '@/types/api';

/**
 * Вкладка «Почты» — КЛИЕНТСКИЕ поиск/выбор почты и фильтр по команде (ADR-050 §1 в редакции
 * ADR-052 §3). Каталог ящиков приходит ЦЕЛИКОМ (`GET /api/mail/mailboxes` без пагинации),
 * поэтому серверных параметров `q`/`team_id` нет — backend не менялся.
 *
 * Контрол поиска — `ui/Combobox` `mode='search'` (ADR-052 §3; норма ADR-050 §1.1 «`ui/Input` +
 * `Search`» ОТМЕНЕНА). Семантика ГИБРИДНАЯ (§3.1): ВВОД фильтрует таблицу по ВСЕМ совпадениям
 * (таблица НЕ схлопывается) и список; ВЫБОР опции сужает таблицу до ОДНОЙ строки.
 *
 * Что ADR-052 НЕ отменял и что здесь сохранено: поиск по ТРЁМ полям (`number`, `app_name`,
 * `email`), подстрока регистронезависимо, `trim()`; `display_name` в поиск НЕ входит; фильтр
 * «Команда» — `ui/Select`, рендерится ТОЛЬКО при `sees_all_mail_teams` (ADR-036/ADR-050 §1.2);
 * фильтры комбинируются (AND, §3.2); пустой результат → «Ничего не найдено» (≠ «Почт пока нет»).
 */

const catalogState = vi.hoisted(() => ({
  mailboxes: [] as unknown[],
  isLoading: false,
}));
const teamsQuery = vi.hoisted(() => ({
  value: { data: { items: [{ id: 'team-3', name: 'Продажи' }] } } as unknown,
}));

vi.mock('@/features/mail/hooks', () => ({
  // Сегмент активности — СЕРВЕРНЫЙ фильтр: набор ящиков сужается самим запросом
  // (`useMailboxesManage(is_active)`), поэтому мок обязан на аргумент реагировать —
  // иначе §3.1а (выбранный ящик выпадает из набора) непроверяем.
  useMailboxesManage: (isActive?: boolean) => ({
    data: {
      mailboxes:
        isActive === undefined
          ? catalogState.mailboxes
          : (catalogState.mailboxes as MailMailbox[]).filter((mb) => mb.is_active === isActive),
    },
    isLoading: catalogState.isLoading,
    isError: false,
    isFetching: false,
    error: null as unknown,
    refetch: vi.fn(),
  }),
  // Мутации строки ящика — no-op (проверяем тулбар и выборку строк, не запись).
  useUpdateMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteMailbox: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/features/teams/hooks', () => ({ useTeams: () => teamsQuery.value }));
vi.mock('@/components/MailboxFormModal', () => ({ MailboxFormModal: () => null }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function mailbox(over: Partial<MailMailbox> = {}): MailMailbox {
  return {
    id: 1,
    email: 'inbox@postapp.store',
    number: '5108',
    app_name: 'Klyro Forge',
    display_name: '5108 Klyro Forge',
    team_id: null,
    is_active: true,
    last_synced_at: '2026-07-02T09:15:00Z',
    last_sync_error: null,
    consecutive_failures: 0,
    ...over,
  };
}

/**
 * Три ящика: по номеру / по приложению / по адресу — каждый находится своим полем.
 * `gamma` НЕАКТИВЕН — сегмент «Неактивные» даёт набор без выбираемых `alpha`/`beta` (§3.1а).
 */
function catalog(): MailMailbox[] {
  return [
    mailbox({ id: 1, number: '5108', app_name: 'Klyro Forge', email: 'alpha@postapp.store' }),
    mailbox({
      id: 2,
      number: '7011',
      app_name: 'Nova Ledger',
      display_name: '7011 Nova Ledger',
      email: 'beta@postapp.store',
      team_id: 'team-3',
    }),
    mailbox({
      id: 3,
      number: null,
      app_name: null,
      display_name: null,
      email: 'gamma@other.store',
      team_id: 'team-9',
      is_active: false,
    }),
  ];
}

/** Лейбл опции ящика в списке — `display_name ? "<display_name> <email>" : email` (ADR-052 §3). */
const LABEL_ALPHA = '5108 Klyro Forge alpha@postapp.store';
const LABEL_BETA = '7011 Nova Ledger beta@postapp.store';
const LABEL_GAMMA = 'gamma@other.store';

function searchBox(): HTMLInputElement {
  return screen.getByRole('combobox', { name: 'Поиск по почтам' }) as HTMLInputElement;
}

/**
 * Адреса ящиков, фактически отрендеренных В ТАБЛИЦЕ. Скоуп по таблице обязателен: лейблы
 * опций выпадающего списка содержат те же адреса (глобальный `queryByText` дал бы совпадения
 * из панели, а не из таблицы).
 */
function visibleEmails(): string[] {
  const table = screen.queryByRole('table');
  if (table === null) return [];
  return catalog()
    .map((mb) => mb.email)
    .filter((email) => within(table).queryByText(email) !== null);
}

/** Лейблы опций открытого выпадающего списка. */
function optionLabels(): string[] {
  return within(screen.getByRole('listbox'))
    .getAllByRole('option')
    .map((o) => o.textContent ?? '');
}

/** Выбор ящика из списка: открыть панель → кликнуть опцию (клик по опции ≡ Enter на ней). */
async function pickMailbox(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(searchBox());
  await user.click(within(screen.getByRole('listbox')).getByRole('option', { name: label }));
}

beforeEach(() => {
  vi.clearAllMocks();
  catalogState.mailboxes = catalog();
  catalogState.isLoading = false;
});

afterEach(() => logout());

describe('MailboxesTab — контрол поиска = ui/Combobox (ADR-052 §3)', () => {
  beforeEach(() => loginAs({ isSuperadmin: true }));

  it('поле рядом с сегментом активности: role=combobox, нормативные aria-label и плейсхолдер', () => {
    render(<MailboxesTab />);

    const box = searchBox();
    expect(box).toHaveAttribute('placeholder', 'Поиск по почтам…');
    expect(box).toHaveAttribute('aria-expanded', 'false');
    // Сегмент «Все / Активные / Неактивные» остался на месте.
    expect(screen.getByRole('group', { name: 'Фильтр активности' })).toBeInTheDocument();
  });

  it('клик по полю раскрывает список ВСЕХ почт сегмента (лейбл — display_name + email)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.click(searchBox());

    expect(optionLabels()).toEqual([LABEL_ALPHA, LABEL_BETA, LABEL_GAMMA]);
    expect(searchBox()).toHaveAttribute('aria-expanded', 'true');
  });

  it('опции сброса («Все почты») на этой вкладке НЕТ — сброс делают `X` / Escape', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.click(searchBox());

    expect(optionLabels()).not.toContain('Все почты');
  });
});

describe('MailboxesTab — ВВОД: фильтрует таблицу (ВСЕ совпадения) и список (ADR-052 §3.1)', () => {
  beforeEach(() => loginAs({ isSuperadmin: true }));

  it('таблица НЕ схлопывается до одной строки: показаны ВСЕ совпадения; список отфильтрован тем же запросом', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    // `postapp` совпадает по адресу у двух ящиков — регресс ADR-050 §1.1 недопустим.
    await user.type(searchBox(), 'postapp');

    expect(visibleEmails()).toEqual(['alpha@postapp.store', 'beta@postapp.store']);
    // Единый предикат: тот же запрос даёт тот же результат в списке (ADR-052 §3.3).
    expect(optionLabels()).toEqual([LABEL_ALPHA, LABEL_BETA]);
  });

  it('ищет по номеру (регистронезависимая подстрока)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), '701');

    expect(visibleEmails()).toEqual(['beta@postapp.store']);
  });

  it('ищет по приложению (регистронезависимо)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), 'klyro');

    expect(visibleEmails()).toEqual(['alpha@postapp.store']);
  });

  it('ищет по самому адресу почты', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), 'GAMMA@');

    expect(visibleEmails()).toEqual(['gamma@other.store']);
  });

  it('пустой запрос (пробелы) фильтр не применяет — видны все ящики', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), '   ');

    expect(visibleEmails()).toHaveLength(3);
  });

  it('поиск без совпадений → «Ничего не найдено» (НЕ «Почт пока нет»)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), 'zzz-nomatch');

    // Строка «Ничего не найдено» — И в выпадающем списке (нет опций), И в таблице (нет строк).
    expect(screen.getAllByText('Ничего не найдено')).toHaveLength(2);

    // Закрываем панель (в `mode='search'` текст сохраняется) — в таблице строка остаётся.
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    expect(screen.queryByText('Почт пока нет')).not.toBeInTheDocument();
  });

  it('пустой каталог без активных фильтров → «Почт пока нет»', () => {
    catalogState.mailboxes = [];
    render(<MailboxesTab />);

    expect(screen.getByText('Почт пока нет')).toBeInTheDocument();
    expect(screen.queryByText('Ничего не найдено')).not.toBeInTheDocument();
  });
});

describe('MailboxesTab — ВЫБОР из списка: ровно одна строка (ADR-052 §3.1)', () => {
  beforeEach(() => loginAs({ isSuperadmin: true }));

  it('выбор опции сужает таблицу до ОДНОЙ строки, даже если текст совпадал с несколькими', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), 'postapp');
    expect(visibleEmails()).toHaveLength(2);

    await user.click(within(screen.getByRole('listbox')).getByRole('option', { name: LABEL_BETA }));

    expect(visibleEmails()).toEqual(['beta@postapp.store']);
    expect(searchBox().value).toBe(LABEL_BETA);
    expect(searchBox()).toHaveAttribute('aria-expanded', 'false');
  });

  it('ввод после выбора СБРАСЫВАЕТ выбор — таблица снова показывает все совпадения текста', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_BETA);
    expect(visibleEmails()).toEqual(['beta@postapp.store']);

    // Текст и выбор взаимоисключающи (mode='search'): печать сбрасывает `selectedMailboxId`.
    await user.clear(searchBox());
    await user.type(searchBox(), 'postapp');

    expect(visibleEmails()).toEqual(['alpha@postapp.store', 'beta@postapp.store']);
  });

  it('`X` сбрасывает выбор И текст → полный набор; поле пусто, виден плейсхолдер', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_ALPHA);
    expect(visibleEmails()).toEqual(['alpha@postapp.store']);

    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    expect(visibleEmails()).toHaveLength(3);
    expect(searchBox().value).toBe('');
    expect(screen.queryByRole('button', { name: 'Очистить' })).not.toBeInTheDocument();
  });

  it('Escape при закрытом списке сбрасывает выбор → полный набор (pinned-опции нет)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_ALPHA);
    expect(visibleEmails()).toEqual(['alpha@postapp.store']);

    // Первый Escape закрывает панель (открылась по фокусу), второй — очищает.
    searchBox().focus();
    await user.keyboard('{Escape}{Escape}');

    expect(searchBox().value).toBe('');
    expect(visibleEmails()).toHaveLength(3);
  });
});

describe('MailboxesTab — смена сегмента активности НЕ сбрасывает выбор/текст (ADR-052 §3.1а)', () => {
  beforeEach(() => loginAs({ isSuperadmin: true }));

  it('выбранный ящик выпал из сегмента → таблица пуста, «Ничего не найдено», лейбл в поле, выбор ЖИВ', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_ALPHA); // alpha активен
    expect(visibleEmails()).toEqual(['alpha@postapp.store']);

    await user.click(screen.getByRole('button', { name: 'Неактивные' }));

    // Пустое пересечение — ШТАТНОЕ состояние: авто-сброса нет, состояние наблюдаемо.
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    expect(visibleEmails()).toHaveLength(0);
    expect(searchBox().value).toBe(LABEL_ALPHA);
    expect(screen.getByRole('button', { name: 'Очистить' })).toBeInTheDocument();
  });

  it('`X` в этом состоянии → полный набор НОВОГО сегмента', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_ALPHA);
    await user.click(screen.getByRole('button', { name: 'Неактивные' }));
    await user.click(screen.getByRole('button', { name: 'Очистить' }));

    expect(visibleEmails()).toEqual(['gamma@other.store']); // единственный неактивный
  });

  it('возврат сегмента «Все» → снова ОДНА строка выбранного ящика (выбор не терялся)', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await pickMailbox(user, LABEL_ALPHA);
    await user.click(screen.getByRole('button', { name: 'Неактивные' }));
    await user.click(screen.getByRole('button', { name: 'Все' }));

    expect(visibleEmails()).toEqual(['alpha@postapp.store']);
  });

  it('текст (без выбора) тоже переживает смену сегмента — фильтр применяется к новому набору', async () => {
    const user = userEvent.setup();
    render(<MailboxesTab />);

    await user.type(searchBox(), 'postapp');
    await user.click(screen.getByRole('button', { name: 'Активные' }));

    expect(searchBox().value).toBe('postapp');
    expect(visibleEmails()).toEqual(['alpha@postapp.store', 'beta@postapp.store']);
  });
});

describe('MailboxesTab — клиентский фильтр по команде (ADR-050 §1.2)', () => {
  it('селектор «Команда» рендерится admin-уровню; опции — «Все команды» → команды → «Без команды»', () => {
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    const select = screen.getByLabelText('Команда') as HTMLSelectElement;
    expect(select).toBeInTheDocument();
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'Все команды',
      'Продажи',
      'Без команды',
    ]);
  });

  it('без `sees_all_mail_teams` селектор НЕ рендерится вовсе (не пустой, не disabled)', () => {
    loginAs({
      isSuperadmin: false,
      role: 'Оператор',
      seesAllMailTeams: false,
      permissions: { mail: ['view'] },
    });
    render(<MailboxesTab />);

    expect(screen.queryByLabelText('Команда')).not.toBeInTheDocument();
    // Поиск при этом доступен всем ролям.
    expect(searchBox()).toBeInTheDocument();
  });

  it('выбор команды фильтрует по `team_id`; «Без команды» — ящики с team_id = null', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    expect(visibleEmails()).toEqual(['beta@postapp.store']);

    // Опция «Без команды» тулбара (у строк ящиков есть свой дропдаун переноса с таким же
    // лейблом) — выбираем по её значению, чтобы попасть именно в фильтр вкладки.
    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, '__no_team__');
    expect(visibleEmails()).toEqual(['alpha@postapp.store']);
  });

  it('ТЕКСТ и фильтр по команде комбинируются (AND) — ни один не сбрасывает другой (§3.2)', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    await user.type(searchBox(), 'klyro');

    // Ящик команды «Продажи» (id=2) не совпадает с запросом → пусто, но оба фильтра активны.
    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    expect((screen.getByLabelText('Команда') as HTMLSelectElement).value).toBe('team-3');
    expect(searchBox().value).toBe('klyro');

    // Совпадающий запрос по той же команде — строка находится.
    await user.clear(searchBox());
    await user.type(searchBox(), 'nova');
    expect(visibleEmails()).toEqual(['beta@postapp.store']);
  });

  it('ВЫБОР почты и фильтр по команде комбинируются (AND): пустое пересечение → «Ничего не найдено»', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    // alpha принадлежит другой команде (team_id = null) → пересечение пусто.
    await pickMailbox(user, LABEL_ALPHA);

    expect(screen.getByText('Ничего не найдено')).toBeInTheDocument();
    // Ни один фильтр не сброшен: выбор жив, команда на месте.
    expect(searchBox().value).toBe(LABEL_ALPHA);
    expect((screen.getByLabelText('Команда') as HTMLSelectElement).value).toBe('team-3');
  });

  it('ВЫБОР + сегмент + команда: три фильтра AND, совпадающая комбинация даёт строку', async () => {
    const user = userEvent.setup();
    loginAs({ isSuperadmin: true });
    render(<MailboxesTab />);

    await user.click(screen.getByRole('button', { name: 'Активные' }));
    await user.selectOptions(screen.getByLabelText('Команда') as HTMLSelectElement, 'team-3');
    await pickMailbox(user, LABEL_BETA); // beta: активен И team-3

    expect(visibleEmails()).toEqual(['beta@postapp.store']);
  });
});

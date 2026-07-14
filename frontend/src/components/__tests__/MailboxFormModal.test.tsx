import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { ApiError } from '@/lib/api';
import type { MailMailbox } from '@/types/api';

// --- Управляемое окружение хуков (vi.hoisted — доступно внутри vi.mock фабрик) ---
const perms = vi.hoisted(() => ({ canCreate: false, seesAll: true }));
// Scope команд канала «Почты» — ЕДИНСТВЕННЫЙ источник опций селектора «Команда»
// (ADR-055 §6.3: `me.mail_teams` из `/api/auth/me`, а НЕ `GET /api/teams`).
const mailScope = vi.hoisted(() => ({
  value: {
    teams: [{ id: 'team-3', name: 'Продажи' }] as { id: string; name: string }[],
    includesUnassigned: false,
  },
}));
// Управляемая мутация authorize: mutate(teamId, { onSuccess, onError }) —
// тест диктует, какой колбэк дёрнуть (успех 200 → panel; 503/404/502 → onError).
const authorize = vi.hoisted(() => ({
  lastTeamId: undefined as string | null | undefined,
  mode: 'success' as 'success' | 'unavailable' | 'idle',
  authorizeUrl: 'https://login.microsoftonline.com/consumers/authorize?x=1',
}));
// Управляемое значение watchQuery (пуллинг списка ящиков при открытой панели).
const watch = vi.hoisted(() => ({ value: { data: undefined } as unknown }));
// Спаи мутаций записи ящика — чтобы проверить ИСХОДЯЩИЙ payload формы (ADR-047 §3.2).
const mutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));
// Спай на `useTeams` — ассертим, что форма ящика за `GET /api/teams` НЕ ходит (§6.3).
const teamsSpy = vi.hoisted(() => vi.fn(() => ({ data: { items: [] } })));

vi.mock('@/features/auth/hooks', () => ({
  useCan: (page: string, action: string) =>
    page === 'mail' && action === 'create' ? perms.canCreate : false,
  useSeesAllMailTeams: () => perms.seesAll,
  useChannelTeamScope: () => mailScope.value,
}));

// `GET /api/teams` в форме ящика БОЛЬШЕ НЕ ИСПОЛЬЗУЕТСЯ (ADR-055 §6.3 закрывает TD-050 и
// прод-баг 2026-07-14: эндпоинт гейтится `teams:view`, у mail-оператора его нет ⇒ список
// приходил пустым и оставалась одна admin-only опция «Без команды» ⇒ ящик было не создать).
// Спай ловит любой вызов — он обязан остаться нулевым.
vi.mock('@/features/teams/hooks', () => ({ useTeams: teamsSpy }));

// watchQuery опирается на useQuery напрямую из @tanstack/react-query — мокаем его
// контролируемым значением (даёт детерминированный пуллинг без реального QueryClient).
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => watch.value,
}));

vi.mock('@/features/mail/hooks', () => ({
  mailMailboxesKey: ['mail', 'mailboxes'],
  useCreateMailbox: () => ({ mutate: mutations.create, isPending: false }),
  useTestMailbox: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMailbox: () => ({ mutate: mutations.update, isPending: false }),
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
    number: '5108',
    app_name: 'Klyro',
    display_name: '5108 Klyro',
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

// ---------------------------------------------------------------------------
// Поля «Номер» / «Приложение» вместо «Отображаемого имени» (ADR-047 §3.2/§3.6).
// display_name клиентом НЕ отправляется — сервер вычисляет его сам (производное поле).
// ---------------------------------------------------------------------------
describe('MailboxFormModal «Номер»/«Приложение» (ADR-047 §3.6)', () => {
  it('рендерит два поля имени и НЕ рендерит «Отображаемое имя»', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.getByLabelText('Номер')).toBeInTheDocument();
    expect(screen.getByLabelText('Приложение')).toBeInTheDocument();
    expect(screen.queryByLabelText('Отображаемое имя')).not.toBeInTheDocument();
  });

  it('edit: поля предзаполнены из number/app_name ящика', () => {
    render(
      <MailboxFormModal
        open
        onOpenChange={vi.fn()}
        mode="edit"
        mailbox={makeMailbox({ number: '173, 57, 104', app_name: 'WIU' })}
      />,
    );

    expect(screen.getByLabelText('Номер')).toHaveValue('173, 57, 104');
    expect(screen.getByLabelText('Приложение')).toHaveValue('WIU');
  });

  it('create: payload несёт number/app_name и НЕ несёт display_name', async () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await userEvent.type(screen.getByLabelText('Адрес почты'), 'new@example.com');
    await userEvent.type(screen.getByLabelText('Номер'), '5108');
    await userEvent.type(screen.getByLabelText('Приложение'), 'Klyro Forge (Codex)');
    await userEvent.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
    await userEvent.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
    await userEvent.type(screen.getByLabelText('Код приложения'), 's3cr3t');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(mutations.create).toHaveBeenCalledTimes(1);
    const payload = mutations.create.mock.calls[0][0] as Record<string, unknown>;
    expect(payload.number).toBe('5108');
    expect(payload.app_name).toBe('Klyro Forge (Codex)');
    expect(payload).not.toHaveProperty('display_name');
  });

  it('edit: правка «Номера» шлёт только number (display_name наружу не идёт)', async () => {
    render(
      <MailboxFormModal
        open
        onOpenChange={vi.fn()}
        mode="edit"
        mailbox={makeMailbox({ number: '5108', app_name: 'Klyro' })}
      />,
    );

    const numberInput = screen.getByLabelText('Номер');
    await userEvent.clear(numberInput);
    await userEvent.type(numberInput, '777');
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    expect(mutations.update).toHaveBeenCalledTimes(1);
    const { payload } = mutations.update.mock.calls[0][0] as {
      payload: Record<string, unknown>;
    };
    expect(payload.number).toBe('777');
    expect(payload).not.toHaveProperty('app_name'); // не менялось → presence-семантика
    expect(payload).not.toHaveProperty('display_name');
  });

  it('edit: очистка «Приложения» шлёт app_name = null', async () => {
    render(
      <MailboxFormModal
        open
        onOpenChange={vi.fn()}
        mode="edit"
        mailbox={makeMailbox({ number: '5108', app_name: 'Klyro' })}
      />,
    );

    await userEvent.clear(screen.getByLabelText('Приложение'));
    await userEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

    const { payload } = mutations.update.mock.calls[0][0] as {
      payload: Record<string, unknown>;
    };
    expect(payload.app_name).toBeNull();
    expect(payload).not.toHaveProperty('display_name');
  });
});

// --- ADR-054: лейбл «Код приложения» + порядок полей формы (нормативно) --------

describe('MailboxFormModal — поле секрета «Код приложения» (ADR-054)', () => {
  beforeEach(() => {
    perms.canCreate = true;
    perms.seesAll = true;
    mailScope.value = { teams: [{ id: 'team-3', name: 'Продажи' }], includesUnassigned: false };
  });

  it('поле доступно по лейблу «Код приложения»; старого «Пароль (IMAP)» в DOM НЕТ', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.getByLabelText('Код приложения')).toBeInTheDocument();
    // Прежний лейбл провоцировал ввести пароль ОТ ПОЧТЫ (частое поражение → 422
    // mail_imap_failed). ADR-054 §1: строку «Пароль (IMAP)» не использовать.
    expect(screen.queryByLabelText('Пароль (IMAP)')).not.toBeInTheDocument();
  });

  it('«Код приложения» — ВТОРОЕ поле формы, сразу после «Адрес почты» (ADR-054 §3)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    const form = document.getElementById('mailbox-form') as HTMLElement;
    const fields = Array.from(form.querySelectorAll('input, select')) as HTMLElement[];
    const labels = fields
      .map((el) => {
        const id = el.getAttribute('id');
        return id ? (form.querySelector(`label[for="${id}"]`)?.textContent ?? '') : '';
      })
      .filter(Boolean);

    // Порядок нормативен: «Адрес почты» → «Код приложения» → «Номер» → «Приложение» → «Команда».
    expect(labels.slice(0, 5)).toEqual([
      'Адрес почты',
      'Код приложения',
      'Номер',
      'Приложение',
      'Команда',
    ]);
  });

  it('подсказка режима `add` — про app password (ADR-054 §1)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(
      screen.getByText(
        'Не пароль от почты, а пароль приложения (app password) из настроек безопасности почтового сервиса. Как его получить — в блоке «Как добавить почту?» выше.',
      ),
    ).toBeVisible();
  });

  it('подсказка режима `edit` — «Оставьте пустым, чтобы не менять код приложения.»', () => {
    render(
      <MailboxFormModal
        open
        onOpenChange={vi.fn()}
        mode="edit"
        mailbox={{
          id: 1,
          email: 'inbox@postapp.store',
          number: '5108',
          app_name: 'Klyro Forge',
          display_name: '5108 Klyro Forge',
          team_id: 'team-3',
          is_active: true,
          last_synced_at: null,
          last_sync_error: null,
          consecutive_failures: 0,
        }}
      />,
    );

    expect(screen.getByText('Оставьте пустым, чтобы не менять код приложения.')).toBeVisible();
  });

  it('пустое поле при создании → инлайн-ошибка «Укажите код приложения» (ADR-054 §1)', async () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await userEvent.type(screen.getByLabelText('Адрес почты'), 'new@example.com');
    await userEvent.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
    await userEvent.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));

    expect(screen.getByText('Укажите код приложения')).toBeVisible();
    expect(mutations.create).not.toHaveBeenCalled();
  });

  it('«SMTP-пароль (опц.)» НА МЕСТЕ и не переименован (ADR-054 §2: другое понятие)', () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    expect(screen.getByLabelText('SMTP-пароль (опц.)')).toBeInTheDocument();
    // Склейка двух разных секретов в один лейбл запрещена (§2).
    expect(screen.queryByLabelText('Код приложения (SMTP)')).not.toBeInTheDocument();
    // Плейсхолдер приведён к новому словарю (§2).
    expect(screen.getByPlaceholderText('По умолчанию — Код приложения')).toBeInTheDocument();
  });

  it('в payload уходит поле `password` — КОНТРАКТ НЕ МЕНЯЛСЯ (ADR-054 §4)', async () => {
    render(<MailboxFormModal open onOpenChange={vi.fn()} mode="add" />);

    await userEvent.type(screen.getByLabelText('Адрес почты'), 'new@example.com');
    await userEvent.type(screen.getByLabelText('Код приложения'), 'app-code');
    await userEvent.type(screen.getByLabelText('IMAP-хост'), 'imap.example.com');
    await userEvent.type(screen.getByLabelText('SMTP-хост'), 'smtp.example.com');
    await userEvent.click(screen.getByRole('button', { name: 'Добавить' }));

    const payload = mutations.create.mock.calls[0][0] as Record<string, unknown>;
    // Переименование UI-лейбла НЕ переименовывает поле DTO (§4).
    expect(payload.password).toBe('app-code');
    expect(payload).not.toHaveProperty('app_password');
  });
});

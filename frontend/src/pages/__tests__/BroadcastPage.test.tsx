import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BroadcastPage } from '@/pages/BroadcastPage';
import { ApiError } from '@/lib/api';
import { INSUFFICIENT_PERMISSIONS_TITLE } from '@/components/InsufficientPermissions';
import type { BroadcastAudienceResponse, BroadcastCreateResponse } from '@/types/api';

const state = vi.hoisted(() => ({
  canView: true,
  canSend: true,
  audience: undefined as BroadcastAudienceResponse | undefined,
  audienceError: null as Error | null,
  isLoading: false,
  mutate: vi.fn(),
  isPending: false,
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: (_page: string, action: string) => (action === 'send' ? state.canSend : false),
}));

vi.mock('@/features/broadcast/hooks', () => ({
  useBroadcastAudience: () => ({
    data: state.audience,
    isLoading: state.isLoading,
    isError: Boolean(state.audienceError),
    error: state.audienceError,
    isFetching: false,
    refetch: vi.fn(),
  }),
  useCreateBroadcast: () => ({
    mutate: state.mutate,
    isPending: state.isPending,
  }),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { toast } from 'sonner';

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const AUDIENCE: BroadcastAudienceResponse = {
  roles: [
    { id: 'r1', name: 'Оператор', started_count: 2, not_started_count: 1 },
    { id: 'r2', name: 'Наблюдатель', started_count: 0, not_started_count: 3 },
  ],
  all_started_count: 2,
  all_not_started_count: 4,
};

const OPERATOR_NAME = 'Оператор (получат: 2, без бота: 1)';
const OBSERVER_NAME = 'Наблюдатель (получат: 0, без бота: 3)';

/** Тексты узлов из `aria-describedby`. Висячий IDREF → падение. */
function describedTexts(el: HTMLElement): string[] {
  const attr = el.getAttribute('aria-describedby');
  const ids = attr === null ? [] : attr.split(' ').filter(Boolean);
  return ids.map((id) => {
    const node = document.getElementById(id);
    expect(node, `висячий IDREF: узла с id="${id}" нет в DOM`).not.toBeNull();
    return node?.textContent ?? '';
  });
}

function liveRegion(): HTMLElement {
  const el = document.querySelector('[aria-live="polite"]');
  expect(el).toBeInstanceOf(HTMLElement);
  return el as HTMLElement;
}

/** Page-local обёртка ADR-077: items-center + justify-center + min-h-[calc(...)]. */
function isWorkspaceClass(className: string): boolean {
  return (
    /\bitems-center\b/.test(className) &&
    /\bjustify-center\b/.test(className) &&
    /\bmin-h-\[calc\(/.test(className)
  );
}

function findWorkspaceAncestor(el: HTMLElement): HTMLElement | null {
  let node: HTMLElement | null = el;
  while (node) {
    const cls = typeof node.className === 'string' ? node.className : '';
    if (isWorkspaceClass(cls)) return node;
    node = node.parentElement;
  }
  return null;
}

function expectWorkspace(el: HTMLElement): HTMLElement {
  const wrap = findWorkspaceAncestor(el);
  expect(wrap, 'ожидался предок с items-center + justify-center + min-h-[calc]').toBeTruthy();
  const tokens = wrap!.className.split(/\s+/).filter(Boolean);
  expect(tokens).toContain('flex');
  expect(tokens).toContain('w-full');
  expect(tokens).toContain('items-center');
  expect(tokens).toContain('justify-center');
  const minH = tokens.find((t) => t.startsWith('min-h-['));
  expect(minH, 'min-h-[calc(...)] на обёртке').toBeTruthy();
  expect(minH).toMatch(/^min-h-\[calc\(/);
  expect(minH).toMatch(/100dvh/);
  expect(minH).toMatch(/4rem/);
  expect(tokens).not.toContain('overflow-y-auto');
  expect(tokens.some((t) => /^h-/.test(t))).toBe(false);
  expect(tokens.some((t) => /^max-h-/.test(t))).toBe(false);
  return wrap!;
}

describe('BroadcastPage (ADR-076 / ADR-077)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.canView = true;
    state.canSend = true;
    state.audience = AUDIENCE;
    state.audienceError = null;
    state.isLoading = false;
    state.isPending = false;
    state.mutate.mockReset();
  });

  it('без broadcast:view показывает заглушку «Недостаточно прав»', () => {
    state.canView = false;
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('без broadcast:send кнопка «Отправить» скрыта, композер и сводка на месте', () => {
    state.canSend = false;
    render(<BroadcastPage />, { wrapper });
    expect(screen.queryByRole('button', { name: 'Отправить' })).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Всем' })).toBeInTheDocument();
    expect(screen.getByLabelText('Сообщение')).toBeInTheDocument();
    expect(liveRegion()).toHaveTextContent('Получат: 0 · Без бота: 0');
  });

  it('видимый legend «Аудитория» (не page-H1, не sr-only)', () => {
    render(<BroadcastPage />, { wrapper });
    const audience = screen.getByRole('group', { name: 'Аудитория' });
    const legend = audience.querySelector('legend');
    expect(legend).toBeTruthy();
    expect(legend).toBeVisible();
    expect(legend).toHaveTextContent('Аудитория');
    expect(legend).not.toHaveClass('sr-only');
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument();
  });

  it('hint textarea «n / 4096» связан через aria-describedby; n = length, не trim', async () => {
    const user = userEvent.setup();
    render(<BroadcastPage />, { wrapper });

    const textarea = screen.getByLabelText('Сообщение');
    expect(textarea).toHaveAttribute('maxLength', '4096');
    expect(textarea).toHaveAttribute('rows', '8');
    expect(describedTexts(textarea)).toEqual(['0 / 4096']);
    expect(textarea).toHaveAccessibleDescription('0 / 4096');

    await user.type(textarea, 'Привет');
    expect(describedTexts(textarea)).toEqual(['6 / 4096']);
    expect(textarea).toHaveAccessibleDescription('6 / 4096');

    await user.clear(textarea);
    await user.type(textarea, '  x  ');
    expect(describedTexts(textarea)).toEqual(['5 / 4096']);
  });

  it('композер — одна внешняя карточка; строки аудитории — под-карточки', async () => {
    const user = userEvent.setup();
    render(<BroadcastPage />, { wrapper });

    const textarea = screen.getByLabelText('Сообщение');
    const form = textarea.closest('form');
    expect(form).toBeTruthy();
    expect(form!.children).toHaveLength(1);
    const card = form!.children[0] as HTMLElement;
    const audience = screen.getByRole('group', { name: 'Аудитория' });
    expect(card).toContainElement(textarea);
    expect(card).toContainElement(audience);
    expect(card).toContainElement(screen.getByRole('button', { name: 'Отправить' }));

    const allBox = screen.getByRole('checkbox', { name: 'Всем' });
    const allRow = audience.querySelector(':scope > div');
    expect(allRow).toBeInstanceOf(HTMLElement);
    expect(allRow).toContainElement(allBox);
    expect(within(allRow as HTMLElement).getByText('Получат: 2')).toBeInTheDocument();
    expect(within(allRow as HTMLElement).getByText('Без бота: 4')).toBeInTheDocument();

    const items = within(audience).getAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(items[0]).not.toHaveAttribute('role', 'button');
    expect(items[1]).not.toHaveAttribute('role', 'button');

    await user.click(allBox);
    expect(allRow).toHaveClass('border-accent');

    const operatorItem = screen.getByRole('checkbox', { name: OPERATOR_NAME }).closest('li');
    expect(operatorItem).toBeTruthy();
    expect(operatorItem).not.toHaveClass('border-accent');
  });

  it('видимый label роли — только имя; accessible name — формула ADR-076; бейджи вне label', () => {
    render(<BroadcastPage />, { wrapper });

    const operator = screen.getByRole('checkbox', { name: OPERATOR_NAME });
    const observer = screen.getByRole('checkbox', { name: OBSERVER_NAME });
    expect(operator).toHaveAccessibleName(OPERATOR_NAME);
    expect(observer).toHaveAccessibleName(OBSERVER_NAME);

    const operatorLabel = operator.closest('label');
    expect(operatorLabel).toBeTruthy();
    expect(operatorLabel).toHaveTextContent(/^Оператор$/);
    expect(operatorLabel).not.toHaveTextContent(/получат/i);
    expect(operatorLabel).not.toHaveTextContent(/без бота/i);

    const operatorRow = operator.closest('li');
    expect(operatorRow).toBeTruthy();
    const got = within(operatorRow!).getByText('Получат: 2');
    const skip = within(operatorRow!).getByText('Без бота: 1');
    expect(operatorLabel!.contains(got)).toBe(false);
    expect(operatorLabel!.contains(skip)).toBe(false);

    const allBox = screen.getByRole('checkbox', { name: 'Всем' });
    expect(allBox).toHaveAccessibleName('Всем');
    const allLabel = allBox.closest('label');
    expect(allLabel).toHaveTextContent(/^Всем$/);
    const audience = screen.getByRole('group', { name: 'Аудитория' });
    const allRow = audience.querySelector(':scope > div') as HTMLElement;
    const allGot = within(allRow).getByText('Получат: 2');
    const allSkip = within(allRow).getByText('Без бота: 4');
    expect(allLabel!.contains(allGot)).toBe(false);
    expect(allLabel!.contains(allSkip)).toBe(false);
  });

  it('live-region — точный text content; видимые ячейки aria-hidden; aria-label на сводке нет', async () => {
    const user = userEvent.setup();
    render(<BroadcastPage />, { wrapper });

    const live = liveRegion();
    expect(live).toHaveTextContent('Получат: 0 · Без бота: 0');
    expect(live).not.toHaveAttribute('aria-label');
    expect(live).toHaveClass('sr-only');

    const strip = live.parentElement;
    expect(strip).toBeTruthy();
    const cells = strip!.querySelectorAll(':scope > div > [aria-hidden="true"]');
    expect(cells).toHaveLength(2);
    expect(cells[0]).toHaveAttribute('aria-hidden', 'true');
    expect(cells[0]).toHaveTextContent('Получат');
    expect(cells[0]).toHaveTextContent('0');
    expect(cells[1]).toHaveAttribute('aria-hidden', 'true');
    expect(cells[1]).toHaveTextContent('Без бота');
    expect(cells[1]).toHaveTextContent('0');

    await user.click(screen.getByRole('checkbox', { name: OPERATOR_NAME }));
    expect(liveRegion()).toHaveTextContent('Получат: 2 · Без бота: 1');

    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    expect(liveRegion()).toHaveTextContent('Получат: 2 · Без бота: 4');
  });

  it('footer: сводка перед CTA, без flex-col-reverse', () => {
    render(<BroadcastPage />, { wrapper });
    const submit = screen.getByRole('button', { name: 'Отправить' });
    const live = liveRegion();
    const footer = submit.parentElement;
    expect(footer).toBeTruthy();
    expect(footer).toContainElement(live);
    expect(footer!.className).not.toMatch(/flex-col-reverse/);
    expect(submit.compareDocumentPosition(live) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy();
  });

  it('«Всем» дизейблит чекбоксы ролей без opacity-60 на строке; submit шлёт all=true без role_ids', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onSuccess: (d: BroadcastCreateResponse) => void }) => {
        opts.onSuccess({ sent: 2, failed: 0, skipped_not_started: 4 });
      },
    );
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Привет команде');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));

    const roleBox = screen.getByRole('checkbox', { name: OPERATOR_NAME });
    expect(roleBox).toBeDisabled();
    const roleRow = roleBox.closest('li');
    expect(roleRow).toBeTruthy();
    expect(roleRow!.className.split(/\s+/)).not.toContain('opacity-60');

    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(state.mutate).toHaveBeenCalledWith(
      { text: 'Привет команде', all: true, role_ids: [] },
      expect.any(Object),
    );
  });

  it('без аудитории и на пробельном тексте submit disabled; выбранная роль шлёт role_ids', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onSuccess: (d: BroadcastCreateResponse) => void }) => {
        opts.onSuccess({ sent: 2, failed: 0, skipped_not_started: 1 });
      },
    );
    render(<BroadcastPage />, { wrapper });

    const submit = screen.getByRole('button', { name: 'Отправить' });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText('Сообщение'), '   ');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    expect(submit).toBeDisabled();

    await user.clear(screen.getByLabelText('Сообщение'));
    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('checkbox', { name: OPERATOR_NAME }));
    await user.click(submit);

    expect(state.mutate).toHaveBeenCalledWith(
      { text: 'Текст', all: false, role_ids: ['r1'] },
      expect.any(Object),
    );
  });

  it('тост успеха из sent/failed/skipped_not_started; инлайн-баннера нет', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation(
      (_payload: unknown, opts: { onSuccess: (d: BroadcastCreateResponse) => void }) => {
        opts.onSuccess({ sent: 3, failed: 1, skipped_not_started: 2 });
      },
    );
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: /Оператор/ }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(toast.success).toHaveBeenCalledWith('Отправлено: 3. Не доставлено: 1. Без бота: 2');
    expect(
      screen.queryByText('Отправлено: 3. Не доставлено: 1. Без бота: 2'),
    ).not.toBeInTheDocument();
    expect(state.mutate).toHaveBeenCalledWith(
      { text: 'Текст', all: false, role_ids: ['r1'] },
      expect.any(Object),
    );
  });

  it('503 knowledge_bot_not_configured на отправке → «ИИ-бот не настроен»', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен'));
    });
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(screen.getByText('ИИ-бот не настроен')).toBeInTheDocument();
    expect(
      screen.getByText('Обратитесь к администратору для настройки ИИ-бота базы знаний.'),
    ).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('503 на загрузке аудитории → «ИИ-бот не настроен»', () => {
    state.audience = undefined;
    state.audienceError = new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен');
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText('ИИ-бот не настроен')).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('loading аудитории — карточка «Загрузка…», композера нет', () => {
    state.isLoading = true;
    state.audience = undefined;
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText('Загрузка…')).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Аудитория' })).not.toBeInTheDocument();
  });

  it('ошибка аудитории — «Не удалось загрузить аудиторию» + «Повторить»', () => {
    state.audience = undefined;
    state.audienceError = new Error('network');
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText('Не удалось загрузить аудиторию')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Повторить/ })).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('403 на аудитории → заглушка «Недостаточно прав»', () => {
    state.audience = undefined;
    state.audienceError = new ApiError(403, 'forbidden', 'Forbidden');
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE)).toBeInTheDocument();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('пустой список ролей → «Ролей для выбора нет.»', () => {
    state.audience = { roles: [], all_started_count: 0, all_not_started_count: 0 };
    render(<BroadcastPage />, { wrapper });
    expect(screen.getByText('Ролей для выбора нет.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Всем' })).toBeInTheDocument();
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument();
  });

  it('422 на отправке → тост «Проверьте текст и аудиторию»', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new ApiError(422, 'validation_error', 'bad'));
    });
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(toast.error).toHaveBeenCalledWith('Проверьте текст и аудиторию');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('403 на отправке → тост «Недостаточно прав»', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new ApiError(403, 'forbidden', 'Forbidden'));
    });
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));

    expect(toast.error).toHaveBeenCalledWith('Недостаточно прав');
    expect(toast.success).not.toHaveBeenCalled();
  });

  it('прочая ошибка отправки → тост с message / фолбэк', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new ApiError(500, 'internal', 'Сервер недоступен'));
    });
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    expect(toast.error).toHaveBeenCalledWith('Сервер недоступен');

    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new Error('boom'));
    });
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    expect(toast.error).toHaveBeenCalledWith('Не удалось отправить рассылку');
  });

  it('isPending — кнопка «Отправить» disabled (loading)', async () => {
    const user = userEvent.setup();
    state.isPending = true;
    render(<BroadcastPage />, { wrapper });

    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    expect(screen.getByRole('button', { name: 'Отправить' })).toBeDisabled();
  });
});

describe('BroadcastPage — раскладка рабочей области (амендмент ADR-077)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.canView = true;
    state.canSend = true;
    state.audience = AUDIENCE;
    state.audienceError = null;
    state.isLoading = false;
    state.isPending = false;
    state.mutate.mockReset();
  });

  it('композер: предок form имеет items-center + justify-center + min-h-[calc]', () => {
    render(<BroadcastPage />, { wrapper });
    const form = screen.getByLabelText('Сообщение').closest('form');
    expect(form).toBeTruthy();
    const wrap = expectWorkspace(form as HTMLElement);
    expect(wrap).toContainElement(form);
    expect(form).toHaveClass('w-full', 'max-w-3xl');
  });

  it('loading: карточка «Загрузка…» внутри той же обёртки', () => {
    state.isLoading = true;
    state.audience = undefined;
    render(<BroadcastPage />, { wrapper });
    expectWorkspace(screen.getByText('Загрузка…'));
  });

  it('error audience: карточка «Не удалось загрузить…» внутри той же обёртки', () => {
    state.audience = undefined;
    state.audienceError = new Error('network');
    render(<BroadcastPage />, { wrapper });
    expectWorkspace(screen.getByText('Не удалось загрузить аудиторию'));
  });

  it('empty 503 на GET: «ИИ-бот не настроен» внутри той же обёртки', () => {
    state.audience = undefined;
    state.audienceError = new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен');
    render(<BroadcastPage />, { wrapper });
    expectWorkspace(screen.getByText('ИИ-бот не настроен'));
  });

  it('empty 503 на POST: empty-карточка внутри той же обёртки', async () => {
    const user = userEvent.setup();
    state.mutate.mockImplementation((_payload: unknown, opts: { onError: (e: Error) => void }) => {
      opts.onError(new ApiError(503, 'knowledge_bot_not_configured', 'ИИ-бот не настроен'));
    });
    render(<BroadcastPage />, { wrapper });
    await user.type(screen.getByLabelText('Сообщение'), 'Текст');
    await user.click(screen.getByRole('checkbox', { name: 'Всем' }));
    await user.click(screen.getByRole('button', { name: 'Отправить' }));
    expectWorkspace(screen.getByText('ИИ-бот не настроен'));
  });

  it('view-guard InsufficientPermissions не внутри центрирующей обёртки', () => {
    state.canView = false;
    render(<BroadcastPage />, { wrapper });
    const stub = screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE);
    expect(findWorkspaceAncestor(stub)).toBeNull();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });

  it('403 audience InsufficientPermissions не внутри центрирующей обёртки', () => {
    state.audience = undefined;
    state.audienceError = new ApiError(403, 'forbidden', 'Forbidden');
    render(<BroadcastPage />, { wrapper });
    const stub = screen.getByText(INSUFFICIENT_PERMISSIONS_TITLE);
    expect(findWorkspaceAncestor(stub)).toBeNull();
    expect(screen.queryByLabelText('Сообщение')).not.toBeInTheDocument();
  });
});

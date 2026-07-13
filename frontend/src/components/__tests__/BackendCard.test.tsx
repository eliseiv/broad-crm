import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { BackendCard } from '@/components/BackendCard';
import type { Backend } from '@/types/api';

const hooks = vi.hoisted(() => ({
  deleteMutate: vi.fn(),
  updateMutate: vi.fn(),
  createMutate: vi.fn(),
}));

// Reveal-функции секретов бэка — на уровне feature-api: раскрытие блока «Информация» НЕ
// должно к ним обращаться (ADR-049 §4), значение идёт только по клику на глаз.
const backendsApi = vi.hoisted(() => ({
  revealBackendApiKey: vi.fn(),
  revealBackendAdminApiKey: vi.fn(),
}));

vi.mock('@/features/backends/hooks', () => ({
  backendsKey: ['backends'],
  useBackendStatus: () => ({ data: undefined }),
  useDeleteBackend: () => ({ mutate: hooks.deleteMutate, isPending: false }),
  useUpdateBackend: () => ({ mutate: hooks.updateMutate, isPending: false }),
  useCreateBackend: () => ({ mutate: hooks.createMutate, isPending: false }),
}));
vi.mock('@/features/backends/api', () => backendsApi);

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function makeBackend(overrides: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    code: 'api-eu',
    name: 'API EU',
    domain: 'api.example.com',
    server_id: null,
    server_name: null,
    ai_key_id: null,
    ai_key_name: null,
    has_api_key: false,
    has_admin_api_key: false,
    git: null,
    note: null,
    check_status: 'working',
    error_message: null,
    position: 0,
    last_checked_at: '2026-07-07T10:15:00Z',
    created_at: '2026-07-07T09:00:00Z',
    updated_at: '2026-07-07T10:15:00Z',
    ...overrides,
  };
}

describe('BackendCard — status badges', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders "Работает" badge with code and domain for working status', () => {
    render(<BackendCard backend={makeBackend({ check_status: 'working' })} />, { wrapper });

    expect(screen.getByText('Работает')).toBeInTheDocument();
    expect(screen.getByText('api-eu')).toBeInTheDocument();
    expect(screen.getByText('api.example.com')).toBeInTheDocument();
    expect(screen.queryByText('Не работает')).not.toBeInTheDocument();
  });

  it('renders "Не работает" badge with reason for error status', () => {
    render(
      <BackendCard
        backend={makeBackend({ check_status: 'error', error_message: 'Бэк недоступен' })}
      />,
      { wrapper },
    );

    expect(screen.getByText('Не работает')).toBeInTheDocument();
    expect(screen.getByText('Бэк недоступен')).toBeInTheDocument();
  });

  it('renders "Проверка…" for pending status', () => {
    render(<BackendCard backend={makeBackend({ check_status: 'pending' })} />, { wrapper });

    expect(screen.getByText('Проверка…')).toBeInTheDocument();
    expect(screen.queryByText('Работает')).not.toBeInTheDocument();
  });

  it('в error-состоянии — ровно одна кнопка «Удалить» (нет второй, ADR-023)', () => {
    render(
      <BackendCard
        backend={makeBackend({ check_status: 'error', error_message: 'Бэк недоступен' })}
      />,
      { wrapper },
    );

    expect(screen.getAllByRole('button', { name: 'Удалить бэк API EU' })).toHaveLength(1);
  });
});

// ADR-049 §3: `/backends` — CARD-FIRST. `BackendDetailModal` упразднена, клик по телу карточки
// не открывает ничего, а с тела сняты все интерактивные семантики (role="button"/tabIndex/
// focus-ring): кликабельная по ARIA карточка без действия — a11y-дефект. Карандаш переехал в
// блок действий карточки; вся «Информация» — в свёрнутом блоке внизу карточки.
describe('BackendCard — card-first: «Информация» на карточке, detail-модалка упразднена (ADR-049 §3)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('тело карточки НЕ интерактивно: нет «Просмотр бэка …», клик по карточке ничего не открывает', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend({ note: 'важное' })} />, { wrapper });

    // Семантика паттерна ADR-035 снята с тела карточки.
    expect(screen.queryByRole('button', { name: 'Просмотр бэка API EU' })).not.toBeInTheDocument();
    expect(document.querySelector('[aria-label^="Просмотр бэка"]')).toBeNull();

    await user.click(screen.getByText('API EU'));

    // Ни detail-модалки, ни edit-формы — клик по телу карточки не открывает ничего.
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('Изменить бэк')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });

  it('блок «Информация» свёрнут по умолчанию; раскрытие показывает состав в нормативном порядке', async () => {
    const user = userEvent.setup();
    render(
      <BackendCard
        backend={makeBackend({
          server_name: 'Server 01',
          ai_key_name: 'OpenAI Prod',
          has_api_key: true,
          has_admin_api_key: true,
          git: 'https://github.com/acme/api-eu',
          note: 'важное',
        })}
      />,
      { wrapper },
    );

    const trigger = screen.getByRole('button', { name: 'Информация' });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    // Пока блок свёрнут — его содержимого в DOM нет.
    expect(screen.queryByText('Server 01')).not.toBeInTheDocument();

    await user.click(trigger);

    expect(screen.getByRole('button', { name: 'Информация' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    // Состав и порядок (ADR-049 §3): Сервер → ИИ-ключ → API KEY → ADMIN API KEY → Git → Примечания.
    const labels = Array.from(document.querySelectorAll('dt, span, p'))
      .map((el) => el.textContent?.trim())
      .filter((t): t is string =>
        ['Сервер', 'ИИ-ключ', 'API KEY', 'ADMIN API KEY', 'Git', 'Примечания'].includes(t ?? ''),
      );
    expect(labels).toEqual(['Сервер', 'ИИ-ключ', 'API KEY', 'ADMIN API KEY', 'Git', 'Примечания']);
    expect(screen.getByText('Server 01')).toBeInTheDocument();
    expect(screen.getByText('OpenAI Prod')).toBeInTheDocument();
    expect(screen.getByText('важное')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://github.com/acme/api-eu' })).toHaveAttribute(
      'href',
      'https://github.com/acme/api-eu',
    );
  });

  it('пустая «Информация» (нет связей/секретов/git/note) → блок не рендерится вовсе', () => {
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    expect(screen.queryByRole('button', { name: 'Информация' })).not.toBeInTheDocument();
    // Идентификаторы карточки при этом на месте.
    expect(screen.getByText('api-eu')).toBeInTheDocument();
    expect(screen.getByText('API EU')).toBeInTheDocument();
  });

  it('карандаш в блоке действий карточки открывает edit prefilled (гейт backends:edit)', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Редактировать бэк API EU' }));

    expect(await screen.findByText('Изменить бэк')).toBeInTheDocument();
    const dialog = within(screen.getByRole('dialog'));
    expect((dialog.getByLabelText('Код') as HTMLInputElement).value).toBe('api-eu');
    expect((dialog.getByLabelText('Домен') as HTMLInputElement).value).toBe('api.example.com');
  });

  it('без права backends:edit карандаша нет, а секрет — статичная маска без глаза', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend({ has_api_key: true })} canEdit={false} />, {
      wrapper,
    });

    expect(
      screen.queryByRole('button', { name: 'Редактировать бэк API EU' }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Информация' }));

    expect(screen.queryByRole('button', { name: 'Показать API KEY' })).not.toBeInTheDocument();
    expect(screen.getByText('••••••••')).toBeInTheDocument();
  });

  it('delete button opens confirm dialog without opening edit', async () => {
    const user = userEvent.setup();
    render(<BackendCard backend={makeBackend()} />, { wrapper });

    await user.click(screen.getByRole('button', { name: 'Удалить бэк API EU' }));

    expect(screen.getByText('Удалить бэк?')).toBeInTheDocument();
    expect(screen.queryByText('Изменить бэк')).not.toBeInTheDocument();
    expect(hooks.updateMutate).not.toHaveBeenCalled();
  });
});

// ADR-049 §4 (нормативно): раскрытие «Информации» НЕ приводит к преднагрузке секретов.
// «Раскрыть Информацию у N бэков подряд» обязано давать НОЛЬ обращений к reveal-эндпоинтам.
describe('BackendCard — секреты не преднагружаются при раскрытии «Информации» (ADR-049 §4)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('раскрытие «Информации» на 3 карточках подряд → НОЛЬ вызовов reveal-эндпоинтов', async () => {
    const user = userEvent.setup();
    const backends = [1, 2, 3].map((n) =>
      makeBackend({
        id: `backend-${n}`,
        code: `api-${n}`,
        name: `API ${n}`,
        has_api_key: true,
        has_admin_api_key: true,
      }),
    );
    render(
      <>
        {backends.map((b) => (
          <BackendCard key={b.id} backend={b} />
        ))}
      </>,
      { wrapper },
    );

    const triggers = screen.getAllByRole('button', { name: 'Информация' });
    expect(triggers).toHaveLength(3);
    for (const trigger of triggers) {
      await user.click(trigger);
    }

    // Все три блока раскрыты — но ни одного запроса за секретом (рендерится маска по has_*).
    expect(screen.getAllByText('••••••••')).toHaveLength(6);
    expect(backendsApi.revealBackendApiKey).not.toHaveBeenCalled();
    expect(backendsApi.revealBackendAdminApiKey).not.toHaveBeenCalled();
  });

  it('значение секрета приходит ТОЛЬКО по клику на глаз — один клик = один ресурс', async () => {
    const user = userEvent.setup();
    backendsApi.revealBackendApiKey.mockResolvedValue({ value: 'sk-backend-PLAIN' });
    backendsApi.revealBackendAdminApiKey.mockResolvedValue({ value: 'sk-admin-PLAIN' });
    render(<BackendCard backend={makeBackend({ has_api_key: true, has_admin_api_key: true })} />, {
      wrapper,
    });

    await user.click(screen.getByRole('button', { name: 'Информация' }));
    expect(backendsApi.revealBackendApiKey).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Показать API KEY' }));
    expect(backendsApi.revealBackendApiKey).toHaveBeenCalledTimes(1);
    expect(backendsApi.revealBackendApiKey.mock.calls[0][0]).toBe('backend-1');
    expect(await screen.findByText('sk-backend-PLAIN')).toBeInTheDocument();
    // Второй секрет по-прежнему не запрошен (по одному ресурсу за раз).
    expect(backendsApi.revealBackendAdminApiKey).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Показать ADMIN API KEY' }));
    expect(backendsApi.revealBackendAdminApiKey).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('sk-admin-PLAIN')).toBeInTheDocument();
  });
});

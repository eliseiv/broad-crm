import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { PropsWithChildren } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { BackendEconomicsPage } from '@/pages/BackendEconomicsPage';
import type {
  BackendEconomicsBackendsResponse,
  BackendEconomicsPricingResponse,
  BackendEconomicsProductsResponse,
  BackendEconomicsProductUpdateResponse,
  BackendEconomicsTariffUpdateResponse,
} from '@/types/api';

/**
 * Страница «Продукты и тарифы» (ADR-072, 08-design-system.md §Страница «Продукты и
 * тарифы»). Нормативные сценарии — 06-testing-strategy.md §Frontend. Модуль до этой
 * волны покрыт нулём тестов, поэтому кейсы — регресс-гейты на конкретные способы
 * сломаться, а не «общий happy path».
 *
 * ⛔ Кейс «поле осталось в режиме правки» намеренно НЕ ассертится: `InlineEditField.commit()`
 * закрывает режим безусловно (TD-079, `src/components/InlineEditField.tsx:45-48`), и такой
 * ассерт был бы красным на корректном коде.
 */

const state = vi.hoisted(() => ({
  backends: undefined as BackendEconomicsBackendsResponse | undefined,
  products: undefined as BackendEconomicsProductsResponse | undefined,
  pricing: undefined as BackendEconomicsPricingResponse | undefined,
  canView: true,
  canEdit: true,
  productPending: false,
  productResult: null as BackendEconomicsProductUpdateResponse | null,
  tariffResult: null as BackendEconomicsTariffUpdateResponse | null,
  productCalls: [] as unknown[],
  tariffCalls: [] as unknown[],
}));

vi.mock('@/features/auth/hooks', () => ({
  useCanViewPage: () => state.canView,
  useCan: (_page: string, action: string) => (action === 'edit' ? state.canEdit : state.canView),
}));

vi.mock('@/features/backend-economics/hooks', () => ({
  backendEconomicsProductsKey: (id: string) => ['backend-economics', 'products', id],
  backendEconomicsPricingKey: (id: string) => ['backend-economics', 'pricing', id],
  useBackendEconomicsBackends: () => ({
    data: state.backends,
    isLoading: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useBackendEconomicsProducts: () => ({
    data: state.products,
    isLoading: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useBackendEconomicsPricing: () => ({
    data: state.pricing,
    isLoading: false,
    isError: false,
    isSuccess: true,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateBackendEconomicsProduct: () => ({
    isPending: state.productPending,
    mutate: (vars: unknown, opts?: Record<string, (arg: unknown) => void>) => {
      state.productCalls.push(vars);
      opts?.onSuccess?.(state.productResult);
      opts?.onSettled?.(undefined);
    },
  }),
  useUpdateBackendEconomicsTariff: () => ({
    isPending: false,
    mutate: (vars: unknown, opts?: Record<string, (arg: unknown) => void>) => {
      state.tariffCalls.push(vars);
      opts?.onSuccess?.(state.tariffResult);
    },
  }),
}));

vi.mock('sonner', () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

function wrapper({ children }: PropsWithChildren) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

const BACKEND_ID = 'b-1';

const BACKENDS: BackendEconomicsBackendsResponse = {
  items: [{ id: BACKEND_ID, code: 'alpha', name: 'Alpha API' }],
};

// Границы — данные ФИКСТУРЫ, а не нормативные числа: конкретные значения `limits`
// в коде не хардкодятся (ADR-072 §7.2), поэтому ассерты берут их отсюда.
const LIMITS = {
  product_tokens_max: 500,
  product_avatar_tokens_max: 400,
  tariff_tokens_max: 100,
  tariff_decimal_places: 6,
};

function capabilities(features = ['products.write_tokens', 'pricing.write_tokens']) {
  return { contract_version: 11, features, limits: LIMITS, cache_effective_after_seconds: 30 };
}

const PRODUCT = {
  product_id: 'p-1',
  name: 'Базовый',
  price: '990',
  period: 'month',
  tokens: 100,
  avatar_tokens: 50,
  grantable: true,
  updated_at: '2026-08-01T10:00:00Z',
};

const TARIFF = {
  tariff_id: 't-1',
  kind: 'chat' as const,
  name: 'Чат',
  tokens: 1.5,
  updated_at: null,
};

/** Выбирает приложение в селекторе — до выбора таблицы не рендерятся вовсе. */
async function selectBackend(user: ReturnType<typeof userEvent.setup>) {
  await user.selectOptions(screen.getByLabelText('Приложение'), BACKEND_ID);
}

async function renderWithBackend() {
  const user = userEvent.setup();
  render(<BackendEconomicsPage />, { wrapper });
  await selectBackend(user);
  return user;
}

describe('BackendEconomicsPage — правка, гейты и деградация (ADR-072)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    state.backends = BACKENDS;
    state.products = { items: [{ ...PRODUCT }], capabilities: capabilities() };
    state.pricing = { items: [{ ...TARIFF }], capabilities: capabilities() };
    state.canView = true;
    state.canEdit = true;
    state.productPending = false;
    state.productCalls = [];
    state.tariffCalls = [];
    state.productResult = {
      ...PRODUCT,
      tokens: 100,
      avatar_tokens: 60,
      previous_tokens: 1000,
      changed: true,
      effective_after_seconds: 30,
    };
    state.tariffResult = {
      ...TARIFF,
      tokens: 0.123456,
      previous_tokens: 1.5,
      changed: true,
      effective_after_seconds: 30,
    };
  });

  /* ── Тост правки аватар-токенов: регресс-гейт на ложную дельту (§8, TD-080) ────── */

  it('тост правки аватар-токенов начинается с «Аватар-токены:» и НЕ содержит previous_tokens', async () => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Аватар-токены'));
    const input = screen.getByLabelText('Аватар-токены');
    await user.clear(input);
    await user.type(input, '60{Enter}');

    expect(state.productCalls).toEqual([
      { productId: 'p-1', payload: { avatar_tokens: 60, if_updated_at: PRODUCT.updated_at } },
    ]);
    const message = vi.mocked(toast.success).mock.calls[0][0] as string;
    // Строка словаря — «Аватар-токены: {previous} → {current}…» (08-design-system.md).
    expect(message.startsWith('Аватар-токены:')).toBe(true);
    // «Было» — снимок ОТРИСОВАННОЙ ячейки (50), а не `previous_tokens` ответа (1000):
    // подстановка `previous_tokens` дала бы ложное утверждение о ДРУГОЙ денежной величине
    // (прод-дефект «Токены продукта: 1000 → 60» при правке 50 → 60).
    expect(message).toContain('50 → 60');
    expect(message).not.toContain(String(state.productResult?.previous_tokens));
    expect(message).not.toContain('Токены продукта');
  });

  it('тост правки только `tokens` берёт дельту из previous_tokens ответа', async () => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    await user.type(input, '100{Enter}');

    const message = vi.mocked(toast.success).mock.calls[0][0] as string;
    expect(message).toContain('Токены продукта: 1000 → 100');
    expect(message).toContain('Применится в течение 30 с');
  });

  /* ── Карандаш: три независимых условия скрытия ───────────────────────────────── */

  it('карандаш не рендерится у строки с `tokens === null` (править нечего)', async () => {
    state.products = {
      items: [{ ...PRODUCT }, { ...PRODUCT, product_id: 'p-2', tokens: null, avatar_tokens: null }],
      capabilities: capabilities(),
    };
    await renderWithBackend();

    // Правима ровно одна строка из двух — у второй значения бэком не отданы.
    expect(screen.getAllByLabelText('Изменить: Токены')).toHaveLength(1);
    expect(screen.getAllByLabelText('Изменить: Аватар-токены')).toHaveLength(1);
  });

  it('карандашей нет при `capabilities: null` даже у держателя edit (fail-closed)', async () => {
    state.products = { items: [{ ...PRODUCT }], capabilities: null };
    state.pricing = { items: [{ ...TARIFF }], capabilities: null };
    await renderWithBackend();

    expect(screen.queryByLabelText(/^Изменить:/)).not.toBeInTheDocument();
  });

  it('карандаши убраны во время in-flight мутации (защита от второго PATCH)', async () => {
    state.productPending = true;
    await renderWithBackend();

    expect(screen.queryByLabelText('Изменить: Токены')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Изменить: Аватар-токены')).not.toBeInTheDocument();
    // Таблица тарифов от мутации продукта не зависит — её карандаш на месте.
    expect(screen.getByLabelText('Изменить: Токенов за генерацию')).toBeInTheDocument();
  });

  /* ── Подсказка при `capabilities === null` — адресована держателю `edit` ───────── */

  const HINT = 'Правка недоступна: бэк не подтвердил поддержку изменения значений';

  it('подсказка при `capabilities: null` рендерится держателю edit', async () => {
    state.products = { items: [{ ...PRODUCT }], capabilities: null };
    state.pricing = { items: [], capabilities: null };
    await renderWithBackend();

    expect(screen.getAllByText(HINT).length).toBeGreaterThan(0);
  });

  it('подсказка при `capabilities: null` НЕ рендерится держателю только view', async () => {
    state.canEdit = false;
    state.products = { items: [{ ...PRODUCT }], capabilities: null };
    state.pricing = { items: [], capabilities: null };
    await renderWithBackend();

    // Негативный ассерт обязателен: подсказка объясняет отсутствие карандашей тому,
    // у кого право есть; без права объяснять нечего.
    expect(screen.queryByText(HINT)).not.toBeInTheDocument();
  });

  /* ── Пара кейсов критерия обязательности полей `capabilities` (ADR-072 §7.2) ──── */

  it('`capabilities` только с `features` — карандаши на месте, PATCH уходит без границ', async () => {
    // Конформный бэк вправе не отдавать `contract_version`/`cache_effective_after_seconds`/
    // `limits`: у них нет потребителя в CRM либо есть штатное отсутствие. Обязательность
    // такого поля превратила бы умолчание в `capabilities: null` ⇒ молча read-only.
    state.products = {
      items: [{ ...PRODUCT }],
      capabilities: { features: ['products.write_tokens'] },
    };
    const user = await renderWithBackend();

    expect(screen.getByLabelText('Изменить: Токены')).toBeInTheDocument();
    // Подсказки «правка недоступна» нет — фичи подтверждены.
    expect(screen.queryByText(HINT)).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    await user.type(input, '10000000{Enter}');

    // Границы не пришли ⇒ клиентская проверка по ним не выполняется, запрос УХОДИТ
    // (авторитетна серверная проверка — бэк ответит 422 → `backend_admin_bad_request`).
    expect(state.productCalls).toHaveLength(1);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('обратный кейс: `capabilities: null` ⇒ страница read-only (fail-closed)', async () => {
    // Единственное поле, обязательность которого удерживает fail-closed, — `features`:
    // без него сервер отдаёт `capabilities: null`, и правка обязана исчезнуть.
    state.products = { items: [{ ...PRODUCT }], capabilities: null };
    state.pricing = { items: [{ ...TARIFF }], capabilities: null };
    await renderWithBackend();

    expect(screen.queryByLabelText(/^Изменить:/)).not.toBeInTheDocument();
    expect(screen.getAllByText(HINT).length).toBeGreaterThan(0);
  });

  /* ── Пустой каталог ≠ отсутствие расширения ──────────────────────────────────── */

  const YELLOW_BLOCK =
    'Бэк не отдаёт продукты и тарифы — требуется обновление до расширенного CRM Admin API';

  it('`items: []` даёт empty state «Продуктов нет», а НЕ жёлтый блок', async () => {
    state.products = { items: [], capabilities: capabilities() };
    await renderWithBackend();

    expect(screen.getByText('Продуктов нет')).toBeInTheDocument();
    expect(screen.queryByText(YELLOW_BLOCK)).not.toBeInTheDocument();
  });

  it('непустой список без единого `tokens` даёт жёлтый блок (бэк уровня v1)', async () => {
    state.products = {
      items: [{ ...PRODUCT, tokens: null, avatar_tokens: null }],
      capabilities: capabilities(),
    };
    await renderWithBackend();

    expect(screen.getByText(YELLOW_BLOCK)).toBeInTheDocument();
    expect(screen.queryByText('Продуктов нет')).not.toBeInTheDocument();
  });

  it('`grantable: null` рендерится «—», а НЕ «Нет»', async () => {
    state.products = {
      items: [{ ...PRODUCT, grantable: null }],
      capabilities: capabilities(),
    };
    await renderWithBackend();

    expect(screen.queryByText('Нет')).not.toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  /* ── Валидация в вызывающем коде: запрос не уходит ───────────────────────────── */

  it.each([
    ['пустое значение', '  '],
    ['нечисловое значение', 'abc'],
    ['отрицательное значение', '-5'],
    ['дробное значение в целочисленном поле', '1.5'],
  ])('невалидный ввод (%s) → toast.error и PATCH НЕ уходит', async (_name, raw) => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    if (raw.trim()) await user.type(input, raw);
    await user.keyboard('{Enter}');

    expect(toast.error).toHaveBeenCalledWith('Введите целое число ≥ 0');
    expect(state.productCalls).toEqual([]);
    // Значение ячейки не изменилось — правка не применялась.
    expect(screen.getByText(String(PRODUCT.tokens))).toBeInTheDocument();
  });

  it('значение выше `product_tokens_max` из фикстуры → toast.error, запрос не уходит', async () => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    await user.type(input, `${LIMITS.product_tokens_max + 1}{Enter}`);

    expect(toast.error).toHaveBeenCalledWith('Значение вне допустимого диапазона');
    expect(state.productCalls).toEqual([]);
  });

  it('без ключа `product_tokens_max` проверка границы снимается — запрос УХОДИТ', async () => {
    state.products = {
      items: [{ ...PRODUCT }],
      capabilities: {
        contract_version: 11,
        features: ['products.write_tokens'],
        limits: { tariff_decimal_places: 6 },
        cache_effective_after_seconds: 30,
      },
    };
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    await user.type(input, '999999{Enter}');

    // Отсутствие лимитов не блокирует правку (полагаемся на 400 бэка), но собственная
    // валидация «целое ≥ 0» продолжает работать.
    expect(state.productCalls).toHaveLength(1);
    expect(toast.error).not.toHaveBeenCalled();
  });

  /* ── `tariff_decimal_places`: хвостовые нули не считаются знаками ─────────────── */

  it('`tariff_decimal_places: 6` принимает «0.1234560» (хвостовой ноль не знак)', async () => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токенов за генерацию'));
    const input = screen.getByLabelText('Токенов за генерацию');
    await user.clear(input);
    await user.type(input, '0.1234560{Enter}');

    // В теле запроса уходит `0.123456` — шесть знаков, лимит не нарушен.
    expect(state.tariffCalls).toEqual([{ tariffId: 't-1', payload: { tokens: 0.123456 } }]);
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('`tariff_decimal_places: 6` отклоняет «0.1234567» без запроса', async () => {
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токенов за генерацию'));
    const input = screen.getByLabelText('Токенов за генерацию');
    await user.clear(input);
    await user.type(input, '0.1234567{Enter}');

    expect(toast.error).toHaveBeenCalledWith('Значение вне допустимого диапазона');
    expect(state.tariffCalls).toEqual([]);
  });

  /* ── Селектор приложения ─────────────────────────────────────────────────────── */

  it('подпись опции — «{name} — {code}»; пункта «Все приложения» в DOM нет', () => {
    render(<BackendEconomicsPage />, { wrapper });

    expect(screen.getByRole('option', { name: 'Alpha API — alpha' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Все приложения' })).not.toBeInTheDocument();
    // Пока бэк не выбран — вместо таблиц подсказка выбора.
    expect(
      screen.getByText('Выберите приложение, чтобы увидеть продукты и тарифы'),
    ).toBeInTheDocument();
  });

  it('пустой список бэков → строка-подсказка, селектора в DOM НЕТ', () => {
    state.backends = { items: [] };
    render(<BackendEconomicsPage />, { wrapper });

    expect(
      screen.getByText(
        'Нет приложений с Admin API Key — задайте ключ в карточке бэка на странице «Бэки»',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText('Приложение')).not.toBeInTheDocument();
  });

  it('page-level view-guard: без backend-economics:view — заглушка «Недостаточно прав»', () => {
    state.canView = false;
    render(<BackendEconomicsPage />, { wrapper });

    expect(screen.getByText('Недостаточно прав')).toBeInTheDocument();
  });

  /* ── `changed: false` — нейтральный тост, не ошибка ──────────────────────────── */

  it('`changed: false` → нейтральный тост «Значение не изменилось»', async () => {
    state.productResult = { ...state.productResult!, changed: false };
    const user = await renderWithBackend();

    await user.click(screen.getByLabelText('Изменить: Токены'));
    const input = screen.getByLabelText('Токены');
    await user.clear(input);
    await user.type(input, '100{Enter}');

    expect(toast).toHaveBeenCalledWith('Значение не изменилось');
    expect(toast.success).not.toHaveBeenCalled();
    expect(toast.error).not.toHaveBeenCalled();
  });
});

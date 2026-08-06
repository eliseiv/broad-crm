import { useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, KeyRound, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { InlineEditField } from '@/components/InlineEditField';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { formatBackendLabel, formatDateTimeRu } from '@/lib/format';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import {
  backendEconomicsPricingKey,
  backendEconomicsProductsKey,
  useBackendEconomicsBackends,
  useBackendEconomicsPricing,
  useBackendEconomicsProducts,
  useUpdateBackendEconomicsProduct,
  useUpdateBackendEconomicsTariff,
} from '@/features/backend-economics/hooks';
import type {
  BackendEconomicsCapabilities,
  BackendEconomicsLimits,
  BackendEconomicsProduct,
  BackendEconomicsTariff,
  BackendEconomicsTariffKind,
  UpdateBackendEconomicsProductRequest,
} from '@/types/api';

/**
 * Страница «Продукты и тарифы» (`/backend-economics`, ADR-072, 08-design-system.md
 * §Страница «Продукты и тарифы»). Экономика внешнего бэка: сколько токенов даёт продукт
 * и сколько списывается за генерацию, с правкой количества токенов ИНЛАЙН в ячейке
 * (PATCH идемпотентен — модалки нет, ADR-072 §8).
 *
 * Ключевые нормы, которые легко нарушить:
 *  • право записи выводится ТОЛЬКО из `capabilities.features`, а НЕ из наличия `tokens`;
 *    `capabilities: null` ⇒ вся страница read-only при любом праве (fail-closed, §7.1);
 *  • `tokens: null` (бэк уровня v1 поля не отдал) ⇒ «—» + строка read-only ни при каком праве;
 *  • `grantable: null` ⇒ «—», а НЕ «Нет» — «не отдано» ≠ «не выдаётся»;
 *  • границы валидации берутся ТОЛЬКО из `capabilities.limits` — числа в коде НЕ хардкодятся
 *    (§7.2): отсутствующий ключ снимает только проверку по нему, форма остаётся рабочей;
 *  • состояния двух таблиц НЕЗАВИСИМЫ — отказ одной не гасит другую.
 */

/* ── Нормативные строки (08-design-system.md §Локализация страницы) ──────────────── */

const HINT_NO_BACKEND = 'Выберите приложение, чтобы увидеть продукты и тарифы';
const HINT_NO_BACKENDS_AT_ALL =
  'Нет приложений с Admin API Key — задайте ключ в карточке бэка на странице «Бэки»';
const HINT_NO_CAPABILITIES = 'Правка недоступна: бэк не подтвердил поддержку изменения значений';
const BLOCK_EXTENSION_NOT_SUPPORTED =
  'Бэк не отдаёт продукты и тарифы — требуется обновление до расширенного CRM Admin API';
const BLOCK_KEY_NOT_SET = 'У бэка не задан Admin API Key — задайте его в карточке бэка';
const EMPTY_PRODUCTS = 'Продуктов нет';
const EMPTY_TARIFFS = 'Тарифов нет';
const ERROR_TITLE = 'Не удалось загрузить';
const ERROR_RETRY = 'Повторить';
const VALUE_EMPTY = '—';
const NEVER_UPDATED = 'не менялось';
const MSG_INTEGER = 'Введите целое число ≥ 0';
const MSG_NUMBER = 'Введите число ≥ 0';
const MSG_OUT_OF_RANGE = 'Значение вне допустимого диапазона';
const MSG_UNCHANGED = 'Значение не изменилось';
const MSG_CONFLICT = 'Значение изменил другой оператор — обновите страницу';
const MSG_SAVE_FAILED = 'Не удалось сохранить изменения';

const KIND_LABEL: Record<BackendEconomicsTariffKind, string> = {
  chat: 'Чат',
  photo: 'Фото',
  video: 'Видео',
  other: 'Другое',
};

/* ── Валидация ввода (в обработчике страницы, не в примитиве — TD-079) ───────────── */

type ParseResult = { ok: true; value: number } | { ok: false; message: string };

/**
 * Целое `≥ 0` (токены продукта и аватар-токены). Проверки «число / целое / ≥ 0» —
 * собственные, они от `limits` НЕ зависят и действуют всегда; верхняя граница
 * проверяется ТОЛЬКО если соответствующий ключ `limits` пришёл (иначе полагаемся на
 * `400` бэка — ADR-072 §7.2).
 */
function parseIntegerTokens(raw: string, max: number | null | undefined): ParseResult {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, message: MSG_INTEGER };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
    return { ok: false, message: MSG_INTEGER };
  }
  if (max != null && value > max) return { ok: false, message: MSG_OUT_OF_RANGE };
  return { ok: true, value };
}

/**
 * Количество ЗНАЧАЩИХ знаков после запятой — ровно столько, сколько уйдёт в JSON
 * запроса, чтобы клиентская проверка не была строже самого лимита бэка.
 *
 * Считается по СТРОКЕ ВВОДА (а не по `String(Number(raw))`: нормализация не видит
 * дробной части в экспоненциальной записи — `«1e-7»` точки не содержит), но **хвостовые
 * нули дробной части отбрасываются**: `«1.50»` → 1, `«0.1234560»` → 6 — в теле запроса
 * уйдут `1.5` и `0.123456`, и отклонять их при `tariff_decimal_places` 1 и 6
 * соответственно значило бы отвергать легитимное значение сообщением о диапазоне,
 * который не нарушен. Экспоненциальный ввод раскрывается через `toFixed(20)`:
 * `«1e-7»` → 7, `«1e3»` → 0. Проверка вспомогательная — авторитетна серверная (`400`).
 */
function fractionDigits(raw: string, value: number): number {
  const source = /[eE]/.test(raw) ? value.toFixed(20) : raw;
  const dot = source.indexOf('.');
  if (dot === -1) return 0;
  return source.slice(dot + 1).replace(/0+$/, '').length;
}

/** Тариф списания допускает дробное значение; границы — те же правила, что и выше. */
function parseDecimalTokens(raw: string, limits: BackendEconomicsLimits | null): ParseResult {
  const trimmed = raw.trim();
  if (!trimmed) return { ok: false, message: MSG_NUMBER };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) return { ok: false, message: MSG_NUMBER };
  const max = limits?.tariff_tokens_max;
  if (max != null && value > max) return { ok: false, message: MSG_OUT_OF_RANGE };
  const places = limits?.tariff_decimal_places;
  if (places != null && fractionDigits(trimmed, value) > places) {
    return { ok: false, message: MSG_OUT_OF_RANGE };
  }
  return { ok: true, value };
}

/* ── Общие блоки состояний ──────────────────────────────────────────────────────── */

function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2 px-5 py-4" aria-hidden="true">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="h-10 animate-pulse rounded-md bg-surface-3" />
      ))}
    </div>
  );
}

/**
 * Информационный ЖЁЛТЫЙ блок «расширение не поддерживается» — ОДНА строка на оба
 * триггера (код `backend_admin_extension_not_supported` у тарифов и вывод из данных
 * у продуктов). Не красная ошибка; кнопки повтора НЕТ — повтор ничего не изменит.
 */
function ExtensionNotSupportedBlock() {
  return (
    <div className="m-5 flex items-start gap-3 rounded-card border border-status-yellow/40 bg-status-yellow/10 px-4 py-3">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-status-yellow" aria-hidden="true" />
      <p className="text-[13px] text-text-primary">{BLOCK_EXTENSION_NOT_SUPPORTED}</p>
    </div>
  );
}

/** `backend_admin_key_not_set` — подсказка без кнопки повтора. */
function AdminKeyMissingBlock() {
  return (
    <div className="m-5 flex items-start gap-3 rounded-card border border-border-subtle bg-surface-2 px-4 py-3">
      <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-text-tertiary" aria-hidden="true" />
      <p className="text-[13px] text-text-primary">{BLOCK_KEY_NOT_SET}</p>
    </div>
  );
}

function LoadErrorBlock({
  message,
  onRetry,
  retrying,
}: {
  message: string | null;
  onRetry: () => void;
  retrying: boolean;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-5 py-10 text-center">
      <AlertTriangle className="h-8 w-8 text-status-red" aria-hidden="true" />
      <div>
        <p className="text-sm font-semibold text-text-primary">{ERROR_TITLE}</p>
        {message && <p className="mt-1 text-[13px] text-text-secondary">{message}</p>}
      </div>
      <Button variant="outline" size="sm" onClick={onRetry} loading={retrying}>
        <RefreshCw className="h-4 w-4" />
        {ERROR_RETRY}
      </Button>
    </div>
  );
}

function EmptyBlock({ text }: { text: string }) {
  return <p className="px-5 py-10 text-center text-[13px] text-text-secondary">{text}</p>;
}

/**
 * Нейтральная подсказка при `capabilities === null` — рендерится ТОЛЬКО держателю
 * `backend-economics:edit` (ADR-072 §7.1): без права объяснять нечего. Не ошибка,
 * без кнопки повтора, нейтральный тон.
 */
function CapabilitiesUnavailableHint() {
  return (
    <p className="border-b border-border-subtle px-5 py-3 text-[13px] text-text-secondary">
      {HINT_NO_CAPABILITIES}
    </p>
  );
}

function TableCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="overflow-hidden rounded-card border border-border-subtle bg-surface-1">
      <div className="border-b border-border-subtle px-5 py-3">
        <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
      </div>
      {children}
    </section>
  );
}

function TableHeadCell({ children }: { children: ReactNode }) {
  return <th className="px-4 py-3 font-medium">{children}</th>;
}

/** Ячейка «—» для не отданного бэком значения (нейтральный, не ошибочный тон). */
function EmptyValue() {
  return <span className="text-text-tertiary">{VALUE_EMPTY}</span>;
}

function formatUpdatedAt(iso: string | null): ReactNode {
  if (!iso) return <span className="text-text-tertiary">{NEVER_UPDATED}</span>;
  return formatDateTimeRu(iso);
}

/** Единый разбор ошибки списка → одно из нормативных состояний таблицы. */
function renderListError(error: unknown, onRetry: () => void, retrying: boolean): ReactNode {
  const code = error instanceof ApiError ? error.code : null;
  if (code === 'backend_admin_extension_not_supported') return <ExtensionNotSupportedBlock />;
  if (code === 'backend_admin_key_not_set') return <AdminKeyMissingBlock />;
  return (
    <LoadErrorBlock
      message={error instanceof ApiError ? error.message : null}
      onRetry={onRetry}
      retrying={retrying}
    />
  );
}

/** Тост ошибки правки: конфликт `if_updated_at` → своя строка + refetch списка. */
function handleSaveError(err: unknown, onConflict: () => void): void {
  if (err instanceof ApiError && err.code === 'backend_admin_conflict') {
    toast.error(MSG_CONFLICT);
    onConflict();
    return;
  }
  // Текст бэка (в т.ч. `backend_admin_bad_request`) показывается КАК ЕСТЬ — подменять
  // его на «расширение не поддерживается» запрещено (08-design-system.md).
  //
  // `backend_admin_extension_not_supported` на PATCH (единственный путь, где этот код
  // достижим у «Продуктов» — их GET всегда `200`, 04-api.md) остаётся ТОСТОМ с
  // сообщением бэка: подменять уже загруженную таблицу с непустыми `tokens` на блок
  // «бэк не отдаёт продукты» значило бы показать заведомо ложное утверждение — блок у
  // «Продуктов» нормативно выводится ИЗ ДАННЫХ (ADR-072 §1.1 п.4), а не из отказа PATCH.
  toast.error(err instanceof ApiError ? err.message : MSG_SAVE_FAILED);
}

/** «Применится в течение {N} с» — задержка применения у бэка (его кэш каталога). */
function effectiveSuffix(seconds: number): string {
  return `Применится в течение ${seconds} с`;
}

/**
 * Строка тоста успеха для продукта выбирается по тому, КАКИЕ поля были отправлены
 * (08-design-system.md §Локализация — три нормативные строки + фолбэк).
 *
 * ⛔ `previous_tokens` относится ИСКЛЮЧИТЕЛЬНО к `tokens`; поля `previous_avatar_tokens`
 * в замороженном контракте НЕТ, и подставлять `previous_tokens` в дельту аватар-токенов
 * ЗАПРЕЩЕНО (ложное утверждение о другой денежной величине: правка `50 → 60` показала бы
 * «Токены продукта: 1000 → 60», после чего оператор «исправит» глобальные токены).
 * Прежнее значение аватар-токенов — КЛИЕНТСКИЙ СНИМОК ячейки до правки (честный, но не
 * авторитетный источник — TD-080); снимка нет ⇒ вариант БЕЗ дельты, дельту не выдумываем.
 */
function buildProductSuccessToast(args: {
  sentTokens: boolean;
  sentAvatarTokens: boolean;
  /** Значение `avatar_tokens`, отрисованное в ячейке ДО правки (снимок), либо `null`. */
  previousAvatarTokens: number | null;
  currentTokens: number | null;
  currentAvatarTokens: number | null;
  previousTokens: number;
  effectiveAfterSeconds: number;
}): string {
  const tail = effectiveSuffix(args.effectiveAfterSeconds);
  const hasAvatarSnapshot = args.previousAvatarTokens != null;

  // Отправлены обе величины одним PATCH (тело это допускает) — обе дельты в одной строке.
  if (args.sentTokens && args.sentAvatarTokens) {
    const avatarPart = hasAvatarSnapshot
      ? `аватар-токены: ${args.previousAvatarTokens} → ${args.currentAvatarTokens}`
      : `аватар-токены: ${args.currentAvatarTokens}`;
    return `Токены продукта: ${args.previousTokens} → ${args.currentTokens}; ${avatarPart}. ${tail}`;
  }

  // Только `tokens` — единственный случай, где дельта берётся из `previous_tokens` ответа.
  if (args.sentTokens) {
    return `Токены продукта: ${args.previousTokens} → ${args.currentTokens}. ${tail}`;
  }

  // Только `avatar_tokens`: дельта из снимка, а без снимка — вариант без дельты.
  return hasAvatarSnapshot
    ? `Аватар-токены: ${args.previousAvatarTokens} → ${args.currentAvatarTokens}. ${tail}`
    : `Аватар-токены: ${args.currentAvatarTokens}. ${tail}`;
}

/* ── Страница ───────────────────────────────────────────────────────────────────── */

export function BackendEconomicsPage() {
  // Page-level view-guard (ADR-021 §6): без `backend-economics:view` — заглушка.
  const canView = useCanViewPage('backend-economics');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <BackendEconomicsView />;
}

function BackendEconomicsView() {
  const canEdit = useCan('backend-economics', 'edit');
  const [backendId, setBackendId] = useState('');
  const backendsQuery = useBackendEconomicsBackends();

  if (backendsQuery.error instanceof ApiError && backendsQuery.error.status === 403) {
    return <InsufficientPermissions />;
  }

  // Список бэков ПУСТ (`items: []` — ни у одного бэка не задан Admin API Key):
  // строка-подсказка ВМЕСТО таблиц И ВМЕСТО селектора — контрол с единственным
  // плейсхолдером вводит в заблуждение (выбирать нечего). Это НЕ ошибка: кнопки
  // повтора нет, состояние чинится в карточке бэка на странице «Бэки»
  // (08-design-system.md §Состояния, строка «Список бэков ПУСТ»).
  const backendsEmpty = backendsQuery.isSuccess && (backendsQuery.data?.items.length ?? 0) === 0;

  // Пункта «Все приложения» НЕТ: правка возможна только в конкретном бэке, а без
  // выбранного источника обе таблицы бессмысленны (08-design-system.md §Layout).
  // Подпись опции — общий `formatBackendLabel` («{name} — {code}»), тот же, что на
  // «Юзерах бэков»: формат нормативно ЕДИНЫЙ для обеих страниц и живёт в одном месте.
  const backendOptions: SelectOption[] = [
    { value: '', label: 'Выберите приложение' },
    ...(backendsQuery.data?.items ?? []).map((b) => ({
      value: b.id,
      label: formatBackendLabel(b.name, b.code),
    })),
  ];

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Продукты и тарифы</h1>
      </div>

      {/* Селектор не рендерится вовсе, когда выбирать нечего (`items: []`). */}
      {!backendsEmpty && (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-card border border-border-subtle bg-surface-1 p-4">
          <div className="w-72">
            <Select
              aria-label="Приложение"
              options={backendOptions}
              value={backendId}
              disabled={backendsQuery.isLoading}
              onChange={(e) => setBackendId(e.target.value)}
            />
          </div>
          {backendsQuery.isError && !(backendsQuery.error instanceof ApiError) && (
            <p className="text-[13px] text-status-red">{ERROR_TITLE}</p>
          )}
          {backendsQuery.isError && backendsQuery.error instanceof ApiError && (
            <div className="flex items-center gap-3">
              <p className="text-[13px] text-status-red">
                {ERROR_TITLE}: {backendsQuery.error.message}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => void backendsQuery.refetch()}
                loading={backendsQuery.isFetching}
              >
                <RefreshCw className="h-4 w-4" />
                {ERROR_RETRY}
              </Button>
            </div>
          )}
        </div>
      )}

      {backendsEmpty ? (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <p className="text-sm text-text-secondary">{HINT_NO_BACKENDS_AT_ALL}</p>
        </div>
      ) : !backendId ? (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <p className="text-sm text-text-secondary">{HINT_NO_BACKEND}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <ProductsTable backendId={backendId} canEdit={canEdit} />
          <PricingTable backendId={backendId} canEdit={canEdit} />
        </div>
      )}
    </>
  );
}

/* ── Таблица «Продукты» ─────────────────────────────────────────────────────────── */

function ProductsTable({ backendId, canEdit }: { backendId: string; canEdit: boolean }) {
  const query = useBackendEconomicsProducts(backendId);
  const capabilities: BackendEconomicsCapabilities | null = query.data?.capabilities ?? null;
  const items = query.data?.items ?? [];

  // Право записи — ТОЛЬКО из `features` (ADR-072 §7): наличие поля `tokens` признаком
  // записи НЕ является. `capabilities: null` ⇒ read-only при любом праве (fail-closed).
  const canWrite = canEdit && Boolean(capabilities?.features?.includes('products.write_tokens'));

  // Бэк уровня v1 (§1.1 п.4): `items` непуст И НИ ОДИН элемент не несёт `tokens` ⇒ тот же
  // информационный блок, что и у тарифов. `items: []` сюда НЕ относится (empty state).
  const extensionMissing = items.length > 0 && items.every((item) => item.tokens == null);

  let body: ReactNode;
  if (query.isLoading) {
    body = <TableSkeleton />;
  } else if (query.isError) {
    body = renderListError(query.error, () => void query.refetch(), query.isFetching);
  } else if (extensionMissing) {
    body = <ExtensionNotSupportedBlock />;
  } else if (items.length === 0) {
    body = <EmptyBlock text={EMPTY_PRODUCTS} />;
  } else {
    body = (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead>
            <tr className="border-b border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
              <TableHeadCell>Продукт</TableHeadCell>
              <TableHeadCell>Цена</TableHeadCell>
              <TableHeadCell>Период</TableHeadCell>
              <TableHeadCell>Токены</TableHeadCell>
              <TableHeadCell>Аватар-токены</TableHeadCell>
              <TableHeadCell>Выдаётся</TableHeadCell>
              <TableHeadCell>Обновлено</TableHeadCell>
            </tr>
          </thead>
          <tbody>
            {items.map((product) => (
              <ProductRow
                key={product.product_id}
                backendId={backendId}
                product={product}
                canWrite={canWrite}
                limits={capabilities?.limits ?? null}
              />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <TableCard title="Продукты">
      {query.isSuccess && capabilities === null && canEdit && <CapabilitiesUnavailableHint />}
      {body}
    </TableCard>
  );
}

function ProductRow({
  backendId,
  product,
  canWrite,
  limits,
}: {
  backendId: string;
  product: BackendEconomicsProduct;
  canWrite: boolean;
  limits: BackendEconomicsLimits | null;
}) {
  const queryClient = useQueryClient();
  const mutation = useUpdateBackendEconomicsProduct(backendId);
  const [savingField, setSavingField] = useState<'tokens' | 'avatar_tokens' | null>(null);

  const save = (field: 'tokens' | 'avatar_tokens', raw: string) => {
    const max = field === 'tokens' ? limits?.product_tokens_max : limits?.product_avatar_tokens_max;
    const parsed = parseIntegerTokens(raw, max);
    if (!parsed.ok) {
      // Запрос НЕ уходит — сообщение уходит тостом (у примитива нет пропа ошибки, TD-079).
      toast.error(parsed.message);
      return;
    }
    const payload: UpdateBackendEconomicsProductRequest =
      field === 'tokens' ? { tokens: parsed.value } : { avatar_tokens: parsed.value };
    // `updated_at === null` («ни разу не менялось» / поле не отдано) ⇒ `if_updated_at`
    // НЕ отправляется (08-design-system.md §Таблица «Продукты»).
    if (product.updated_at) payload.if_updated_at = product.updated_at;

    // Снимок ДО отправки: именно это значение аватар-токенов оператор видел в ячейке.
    // Авторитетного `previous_avatar_tokens` в контракте нет (TD-080), поэтому снимок —
    // единственный честный источник «было» для аватар-токенов.
    const previousAvatarSnapshot = product.avatar_tokens;

    setSavingField(field);
    mutation.mutate(
      { productId: product.product_id, payload },
      {
        onSuccess: (res) => {
          if (!res.changed) {
            toast(MSG_UNCHANGED);
            return;
          }
          toast.success(
            buildProductSuccessToast({
              sentTokens: payload.tokens !== undefined,
              sentAvatarTokens: payload.avatar_tokens !== undefined,
              previousAvatarTokens: previousAvatarSnapshot,
              currentTokens: res.tokens ?? payload.tokens ?? null,
              currentAvatarTokens: res.avatar_tokens ?? payload.avatar_tokens ?? null,
              previousTokens: res.previous_tokens,
              effectiveAfterSeconds: res.effective_after_seconds,
            }),
          );
        },
        onError: (err) =>
          handleSaveError(err, () =>
            queryClient.invalidateQueries({
              queryKey: backendEconomicsProductsKey(backendId),
            }),
          ),
        onSettled: () => setSavingField(null),
      },
    );
  };

  return (
    <tr className="border-b border-border-subtle align-top last:border-b-0">
      <td className="px-4 py-3">
        <p className="font-mono text-[13px] text-text-primary">{product.product_id}</p>
        <p className="mt-0.5 text-[12px] text-text-secondary">{product.name}</p>
      </td>
      <td className="px-4 py-3 text-text-secondary">{product.price ?? <EmptyValue />}</td>
      <td className="px-4 py-3 text-text-secondary">{product.period ?? <EmptyValue />}</td>
      <td className="px-4 py-3">
        {/* `tokens === null` ⇒ «—» и строка read-only: карандаш не рендерится НИ ПРИ КАКОМ
            праве и ни при каких `features` — текущее значение бэком не отдано (§1.1 п.2). */}
        {/* `!mutation.isPending` — блокировка двойного сабмита (08-design-system.md
            §Контракт инлайн-правки): примитив закрывает режим правки сразу (TD-079),
            поэтому проп `saving` управляет кнопками, которых уже нет на экране, — на
            время запроса убираем сам аффорданс (карандаш), иначе уходит второй PATCH
            и возможен ложный `409`. */}
        <InlineEditField
          label="Токены"
          value={product.tokens == null ? null : String(product.tokens)}
          canEdit={canWrite && product.tokens != null && !mutation.isPending}
          saving={mutation.isPending && savingField === 'tokens'}
          onSave={(next) => save('tokens', next)}
        />
      </td>
      <td className="px-4 py-3">
        <InlineEditField
          label="Аватар-токены"
          value={product.avatar_tokens == null ? null : String(product.avatar_tokens)}
          canEdit={canWrite && product.avatar_tokens != null && !mutation.isPending}
          saving={mutation.isPending && savingField === 'avatar_tokens'}
          onSave={(next) => save('avatar_tokens', next)}
        />
      </td>
      <td className="px-4 py-3 text-text-secondary">
        {/* `grantable: null` ⇒ «—», а НЕ «Нет»: «не отдано» ≠ «не выдаётся» (§1.1 п.3). */}
        {product.grantable == null ? <EmptyValue /> : product.grantable ? 'Да' : 'Нет'}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-text-secondary">
        {formatUpdatedAt(product.updated_at)}
      </td>
    </tr>
  );
}

/* ── Таблица «Тарифы списания» ──────────────────────────────────────────────────── */

function PricingTable({ backendId, canEdit }: { backendId: string; canEdit: boolean }) {
  const query = useBackendEconomicsPricing(backendId);
  const capabilities: BackendEconomicsCapabilities | null = query.data?.capabilities ?? null;
  const items = query.data?.items ?? [];
  const canWrite = canEdit && Boolean(capabilities?.features?.includes('pricing.write_tokens'));

  let body: ReactNode;
  if (query.isLoading) {
    body = <TableSkeleton />;
  } else if (query.isError) {
    // У «Тарифов» состояние «расширение не поддерживается» приходит уже на GET —
    // кодом `backend_admin_extension_not_supported` (путь есть только в v1.1).
    body = renderListError(query.error, () => void query.refetch(), query.isFetching);
  } else if (items.length === 0) {
    body = <EmptyBlock text={EMPTY_TARIFFS} />;
  } else {
    body = (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead>
            <tr className="border-b border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
              <TableHeadCell>Тип генерации</TableHeadCell>
              <TableHeadCell>Токенов за генерацию</TableHeadCell>
              <TableHeadCell>Обновлено</TableHeadCell>
            </tr>
          </thead>
          <tbody>
            {items.map((tariff) => (
              <TariffRow
                key={tariff.tariff_id}
                backendId={backendId}
                tariff={tariff}
                canWrite={canWrite}
                limits={capabilities?.limits ?? null}
              />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <TableCard title="Тарифы списания">
      {query.isSuccess && capabilities === null && canEdit && <CapabilitiesUnavailableHint />}
      {body}
    </TableCard>
  );
}

function TariffRow({
  backendId,
  tariff,
  canWrite,
  limits,
}: {
  backendId: string;
  tariff: BackendEconomicsTariff;
  canWrite: boolean;
  limits: BackendEconomicsLimits | null;
}) {
  const queryClient = useQueryClient();
  const mutation = useUpdateBackendEconomicsTariff(backendId);

  const save = (raw: string) => {
    const parsed = parseDecimalTokens(raw, limits);
    if (!parsed.ok) {
      toast.error(parsed.message);
      return;
    }
    const payload = {
      tokens: parsed.value,
      ...(tariff.updated_at ? { if_updated_at: tariff.updated_at } : {}),
    };
    mutation.mutate(
      { tariffId: tariff.tariff_id, payload },
      {
        onSuccess: (res) => {
          if (!res.changed) {
            toast(MSG_UNCHANGED);
            return;
          }
          toast.success(
            `Тариф: ${res.previous_tokens} → ${res.tokens}. ${effectiveSuffix(res.effective_after_seconds)}`,
          );
        },
        onError: (err) =>
          handleSaveError(err, () =>
            queryClient.invalidateQueries({ queryKey: backendEconomicsPricingKey(backendId) }),
          ),
      },
    );
  };

  return (
    <tr className="border-b border-border-subtle align-top last:border-b-0">
      <td className="px-4 py-3">
        {/* `kind="other"` → `name`, при пустом `name` — `tariff_id` (opaque, моно). */}
        {tariff.kind === 'other' ? (
          tariff.name ? (
            <span className="text-text-primary">{tariff.name}</span>
          ) : (
            <span className="font-mono text-[13px] text-text-primary">{tariff.tariff_id}</span>
          )
        ) : (
          <span className="text-text-primary">{KIND_LABEL[tariff.kind]}</span>
        )}
      </td>
      <td className="px-4 py-3">
        {/* `!mutation.isPending` — та же блокировка двойного сабмита, что и у продукта. */}
        <InlineEditField
          label="Токенов за генерацию"
          value={String(tariff.tokens)}
          canEdit={canWrite && !mutation.isPending}
          saving={mutation.isPending}
          onSave={save}
        />
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-text-secondary">
        {formatUpdatedAt(tariff.updated_at)}
      </td>
    </tr>
  );
}

import { useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, Archive, ArchiveRestore, KeyRound, RefreshCw } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { InlineEditField } from '@/components/InlineEditField';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Pill } from '@/components/ui/Pill';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
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
const TOGGLE_SHOW_ARCHIVED = 'Показать архивные';
const PILL_ARCHIVED = 'В архиве';
const ARIA_ARCHIVE = 'Архивировать';
const ARIA_UNARCHIVE = 'Вернуть из архива';
const TOAST_ARCHIVED = 'Продукт скрыт с витрины. Начисление по нему продолжает работать';
const TOAST_UNARCHIVED = 'Продукт возвращён на витрину';
const HINT_ALL_ARCHIVED = 'Все продукты в архиве. Включите «Показать архивные», чтобы увидеть их';
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

function TableCard({
  title,
  action,
  children,
}: {
  title: string;
  /** Контрол в шапке карточки (переключатель «Показать архивные»); `null` — нет контрола. */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-card border border-border-subtle bg-surface-1">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-subtle px-5 py-3">
        <h2 className="text-sm font-semibold text-text-primary">{title}</h2>
        {action}
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
 * Хвост «. Применится в течение {N} с» для ЛЮБОГО тоста успеха модуля — правки токенов,
 * тарифа и архива (единая сборка, чтобы окно применения не выпадало из части тостов).
 * Поля нет ⇒ хвост опускается (толерантность ADR-073 §8), точка предложения остаётся.
 *
 * Окно обязано быть ВИДНО оператору (ADR-072 §3: «невидимость окна, а не его
 * длительность, делает расхождение дорогим»): у архива умолчание особенно дорого —
 * оператор архивирует, обновляет страницу, видит продукт всё ещё активным (admin-чтение
 * контрагента может идти сквозь его кэш каталога) и повторяет уже состоявшуюся операцию.
 */
function effectiveTail(seconds?: number | null): string {
  return seconds != null ? ` ${effectiveSuffix(seconds)}` : '';
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
 *
 * Тост собирается из ДОСТУПНЫХ частей (ADR-073 §8): нет `previous_tokens` ⇒ без дельты,
 * нет `effective_after_seconds` ⇒ без предложения о задержке. Операция состоялась, и
 * меньшая детализация — не повод молчать о ней и не ошибочное состояние страницы.
 */
function buildProductSuccessToast(args: {
  sentTokens: boolean;
  sentAvatarTokens: boolean;
  /** Значение `avatar_tokens`, отрисованное в ячейке ДО правки (снимок), либо `null`. */
  previousAvatarTokens: number | null;
  currentTokens: number | null;
  currentAvatarTokens: number | null;
  previousTokens?: number | null;
  effectiveAfterSeconds?: number | null;
}): string {
  const tail = effectiveTail(args.effectiveAfterSeconds);
  const hasAvatarSnapshot = args.previousAvatarTokens != null;
  // Прежнего значения токенов нет ⇒ тот же фолбэк-паттерн «без дельты», что и у
  // аватар-токенов: показываем результат, а не выдуманное «было».
  const tokensPart =
    args.previousTokens != null
      ? `Токены продукта: ${args.previousTokens} → ${args.currentTokens}`
      : `Токены продукта: ${args.currentTokens}`;

  // Отправлены обе величины одним PATCH (тело это допускает) — обе дельты в одной строке.
  if (args.sentTokens && args.sentAvatarTokens) {
    const avatarPart = hasAvatarSnapshot
      ? `аватар-токены: ${args.previousAvatarTokens} → ${args.currentAvatarTokens}`
      : `аватар-токены: ${args.currentAvatarTokens}`;
    return `${tokensPart}; ${avatarPart}.${tail}`;
  }

  // Только `tokens` — единственный случай, где дельта берётся из `previous_tokens` ответа.
  if (args.sentTokens) {
    return `${tokensPart}.${tail}`;
  }

  // Только `avatar_tokens`: дельта из снимка, а без снимка — вариант без дельты.
  return hasAvatarSnapshot
    ? `Аватар-токены: ${args.previousAvatarTokens} → ${args.currentAvatarTokens}.${tail}`
    : `Аватар-токены: ${args.currentAvatarTokens}.${tail}`;
}

/**
 * Тост успеха правки тарифа — собирается из доступных частей ТАК ЖЕ, как у продукта
 * (ADR-073 §8 п.4: правило симметрично для обоих `PATCH`-эндпоинтов модуля). Нет
 * `previous_tokens` ⇒ без дельты; нет `effective_after_seconds` ⇒ без предложения о
 * задержке. Довод «у тарифа одна значимая величина, значит `previous_tokens` придёт
 * всегда» покрывает одно поле из трёх и не отменяет цены ошибки: правка уже применена
 * у бэка, и красная ошибка вместо тоста заставила бы оператора повторить её.
 */
function buildTariffSuccessToast(args: {
  previousTokens?: number | null;
  currentTokens: number;
  effectiveAfterSeconds?: number | null;
}): string {
  const tail = effectiveTail(args.effectiveAfterSeconds);
  return args.previousTokens != null
    ? `Тариф: ${args.previousTokens} → ${args.currentTokens}.${tail}`
    : `Тариф: ${args.currentTokens}.${tail}`;
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
  // Состояние переключателя — КЛИЕНТСКОЕ и НЕ персистентное (ADR-073 §3): при каждом
  // открытии страницы выключено. Персистенция потребовала бы хранилища настроек
  // оператора, а риск «смотрю с архивом и не помню об этом» выше пользы.
  const [showArchived, setShowArchived] = useState(false);

  // Право записи — ТОЛЬКО из `features` (ADR-072 §7): наличие поля `tokens` признаком
  // записи НЕ является. `capabilities: null` ⇒ read-only при любом праве (fail-closed).
  const canWrite = canEdit && Boolean(capabilities?.features?.includes('products.write_tokens'));
  // Архив — ОТДЕЛЬНАЯ фича (ADR-073 §4): бэк вправе реализовать архив без правки токенов
  // и наоборот; переиспользовать `products.write_tokens` значило бы обещать `PATCH`,
  // которого может не быть.
  const canWriteArchived =
    canEdit && Boolean(capabilities?.features?.includes('products.write_archived'));

  // Бэк уровня v1 (§1.1 п.4): `items` непуст И НИ ОДИН элемент не несёт `tokens` ⇒ тот же
  // информационный блок, что и у тарифов. `items: []` сюда НЕ относится (empty state).
  const extensionMissing = items.length > 0 && items.every((item) => item.tokens == null);

  // Понятие архива есть, только если бэк отдал поле хотя бы у одного элемента: иначе
  // переключатель и контрол не рендерятся (ADR-073 §1) — показывать и архивировать нечего.
  const hasArchiveSupport = items.some((item) => item.archived != null);
  // Фильтрация КЛИЕНТСКАЯ (§3): эндпоинт всегда отдаёт полный список с флагом.
  const visibleItems = showArchived ? items : items.filter((item) => item.archived !== true);
  // Третье состояние (§6): список непуст и это НЕ v1-бэк, но после фильтрации активных не
  // осталось. Не «Продуктов нет» и не жёлтый блок — иначе оператору названа ложная причина.
  const allArchived = !extensionMissing && items.length > 0 && visibleItems.length === 0;

  let body: ReactNode;
  if (query.isLoading) {
    body = <TableSkeleton />;
  } else if (query.isError) {
    body = renderListError(query.error, () => void query.refetch(), query.isFetching);
  } else if (extensionMissing) {
    body = <ExtensionNotSupportedBlock />;
  } else if (items.length === 0) {
    body = <EmptyBlock text={EMPTY_PRODUCTS} />;
  } else if (allArchived) {
    body = <EmptyBlock text={HINT_ALL_ARCHIVED} />;
  } else {
    body = (
      <div className="overflow-x-auto">
        {/* Ширина без колонки контрола меньше ровно на неё: на бэке без понятия архива
            раскладка совпадает с той, что была до ADR-073. */}
        <table
          className={cn(
            'w-full text-left text-sm',
            hasArchiveSupport ? 'min-w-[860px]' : 'min-w-[810px]',
          )}
        >
          <thead>
            <tr className="border-b border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
              {/* Колонок «Цена»/«Период» больше нет (ADR-073 §2) — это РЕШЕНИЕ, а не
                  дефект: у известных бэков они всегда `null`, а колонка из одних «—»
                  имитирует данные и отнимает ширину. Поля контракта при этом остаются. */}
              <TableHeadCell>Продукт</TableHeadCell>
              <TableHeadCell>Токены</TableHeadCell>
              <TableHeadCell>Аватар-токены</TableHeadCell>
              <TableHeadCell>Выдаётся</TableHeadCell>
              <TableHeadCell>Обновлено</TableHeadCell>
              {/* Контрол архива — последняя колонка БЕЗ видимого заголовка (идиома
                  таблицы номеров: подпись только для скринридера). Колонка рендерится
                  ТОЛЬКО когда у бэка есть понятие архива — тот же признак, по которому
                  скрывается переключатель: иначе она пуста во всех строках и лишь
                  отнимает ширину у значимых столбцов. */}
              {hasArchiveSupport && (
                <th className="px-4 py-3 font-medium">
                  <span className="sr-only">Архив</span>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {visibleItems.map((product) => (
              <ProductRow
                key={product.product_id}
                backendId={backendId}
                product={product}
                canWrite={canWrite}
                canWriteArchived={canWriteArchived}
                showArchiveColumn={hasArchiveSupport}
                limits={capabilities?.limits ?? null}
              />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <TableCard
      title="Продукты"
      /* Переключатель обязателен (ADR-073 §3): без него архивирование необратимо из UI —
         продукт исчезает вместе со строкой, на которой стоял бы контрол возврата. В
         состоянии «все архивные» он тоже виден, иначе состояние неисправимо. */
      action={
        query.isSuccess && hasArchiveSupport ? (
          <Checkbox
            label={TOGGLE_SHOW_ARCHIVED}
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
          />
        ) : null
      }
    >
      {query.isSuccess && capabilities === null && canEdit && <CapabilitiesUnavailableHint />}
      {body}
    </TableCard>
  );
}

function ProductRow({
  backendId,
  product,
  canWrite,
  canWriteArchived,
  showArchiveColumn,
  limits,
}: {
  backendId: string;
  product: BackendEconomicsProduct;
  canWrite: boolean;
  canWriteArchived: boolean;
  /** Есть ли у бэка понятие архива: без него колонки контрола нет ни в шапке, ни в строке. */
  showArchiveColumn: boolean;
  limits: BackendEconomicsLimits | null;
}) {
  const queryClient = useQueryClient();
  const mutation = useUpdateBackendEconomicsProduct(backendId);
  const [savingField, setSavingField] = useState<'tokens' | 'avatar_tokens' | 'archived' | null>(
    null,
  );
  const isArchived = product.archived === true;
  // Гейт контрола архива — ТРЁХСОСТАВНЫЙ (ADR-073 §4): право + ОТДЕЛЬНАЯ фича
  // `products.write_archived` + элемент несёт поле. ⚠️ Правилу «`tokens: null` ⇒ строка
  // read-only» контрол НЕ подчиняется: оно защищает от слепой установки НЕИЗВЕСТНОГО
  // значения, а текущее `archived` известно ⇒ строка без токенов архивируется штатно.
  // Правилу «`capabilities: null` ⇒ read-only» подчиняется (оно уже в `canWriteArchived`).
  const canToggleArchive = canWriteArchived && product.archived != null;

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
          // Нейтральный тост — ТОЛЬКО при явном `changed: false`. Отсутствующий `changed`
          // трактуется как «изменилось» (ADR-073 §8): сказать «значение не изменилось» о
          // состоявшейся правке значило бы толкнуть оператора повторить её.
          if (res.changed === false) {
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

  /**
   * Архивирование/возврат — инлайн, БЕЗ подтверждения: значение булево, операция
   * идемпотентна и обратима тем же контролом (ADR-073 §4). На начисление и выдачу не
   * влияет — это признак ВИТРИНЫ (§5), о чём и говорит текст тоста.
   */
  const toggleArchive = () => {
    const nextArchived = !isArchived;
    // `false` («вернуть из архива») — значимое значение и обязано уйти в теле.
    const payload: UpdateBackendEconomicsProductRequest = { archived: nextArchived };
    if (product.updated_at) payload.if_updated_at = product.updated_at;

    setSavingField('archived');
    mutation.mutate(
      { productId: product.product_id, payload },
      {
        onSuccess: (res) => {
          // Как и выше: только явный `changed: false` даёт нейтральный тост. Ровно здесь
          // риск и материализуется — контрагенту естественно опустить поля на archived-only
          // правке, а признак у бэка уже переключён (ADR-073 §8).
          if (res.changed === false) {
            toast(MSG_UNCHANGED);
            return;
          }
          // Окно применения показывается и у архивной правки: по контракту оно у неё
          // ЕСТЬ (ADR-073 §8), а умолчание о нём стоит дорого — оператор обновит
          // страницу, увидит прежнее состояние (кэш каталога у бэка) и повторит
          // состоявшуюся операцию. Хвост общий с тостами токенов и тарифа.
          toast.success(
            `${nextArchived ? TOAST_ARCHIVED : TOAST_UNARCHIVED}.${effectiveTail(res.effective_after_seconds)}`,
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
        <p className="mt-0.5 flex flex-wrap items-center gap-2 text-[12px] text-text-secondary">
          <span>{product.name}</span>
          {/* Пометка архива — текстом, а не только цветом (a11y). */}
          {isArchived && <Pill tone="neutral" label={PILL_ARCHIVED} />}
        </p>
      </td>
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
      {showArchiveColumn && (
        <td className="px-4 py-3">
          {canToggleArchive && (
            <button
              type="button"
              onClick={toggleArchive}
              disabled={mutation.isPending}
              aria-label={isArchived ? ARIA_UNARCHIVE : ARIA_ARCHIVE}
              title={isArchived ? ARIA_UNARCHIVE : ARIA_ARCHIVE}
              className="rounded-md p-1 text-text-tertiary transition-colors hover:bg-surface-3 hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60"
            >
              {mutation.isPending && savingField === 'archived' ? (
                <Spinner className="text-text-secondary" />
              ) : isArchived ? (
                <ArchiveRestore className="h-4 w-4" aria-hidden="true" />
              ) : (
                <Archive className="h-4 w-4" aria-hidden="true" />
              )}
            </button>
          )}
        </td>
      )}
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
          // Толерантность к неполному ответу — СИММЕТРИЧНА продукту (ADR-073 §8 п.4):
          // side-effect правки тарифа так же необратим (значение списания у бэка уже
          // изменено), поэтому только явный `changed: false` даёт нейтральный тост.
          if (res.changed === false) {
            toast(MSG_UNCHANGED);
            return;
          }
          toast.success(
            buildTariffSuccessToast({
              previousTokens: res.previous_tokens,
              currentTokens: res.tokens,
              effectiveAfterSeconds: res.effective_after_seconds,
            }),
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

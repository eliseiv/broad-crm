import { useState } from 'react';
import { AlertTriangle, ArrowLeft, Check, RefreshCw, X } from 'lucide-react';
import { Link, useParams } from 'react-router-dom';
import { AddTokensModal, GrantPlanModal } from '@/components/BackendUserActionModals';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatDateTimeRu } from '@/lib/format';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import {
  useBackendUser,
  useBackendUserPayments,
  useBackendUserRequests,
} from '@/features/backend-users/hooks';
import type {
  BackendUserMediaCounters,
  BackendUserPayment,
  BackendUserRequestItem,
} from '@/types/api';

type TabKey = 'payments' | 'requests';

function formatTokens(value: number): string {
  return value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
}

function formatUsd(value: number): string {
  return `$${value.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
}

/** Сумма оплаты со знаком и валютой: success → «+$9.99» (зелёный), failed — красный. */
function formatPaymentAmount(payment: BackendUserPayment): string {
  const symbol = payment.currency === 'RUB' ? '₽' : '$';
  const value = `${symbol}${Math.abs(payment.amount).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}`;
  return payment.status === 'success' ? `+${value}` : value;
}

function formatSeconds(value: number | null | undefined): string {
  if (value == null) return '—';
  return `${value.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}s`;
}

export function BackendUserDetailPage() {
  const canView = useCanViewPage('backend-users');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <BackendUserDetailView />;
}

function BackendUserDetailView() {
  const { backendId = '', userId = '' } = useParams();
  const { data, isLoading, isError, error, refetch, isFetching } = useBackendUser(
    backendId,
    userId,
  );
  const canEdit = useCan('backend-users', 'edit');
  const [tab, setTab] = useState<TabKey>('payments');
  const [tokensOpen, setTokensOpen] = useState(false);
  const [planOpen, setPlanOpen] = useState(false);

  const backLink = (
    <Link
      to="/backend-users"
      className="mb-4 inline-flex items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-3 py-1.5 text-[13px] font-medium text-text-primary transition-colors hover:bg-surface-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden="true" />К списку пользователей
    </Link>
  );

  if (isLoading) {
    return (
      <>
        {backLink}
        <div className="flex items-center justify-center rounded-card border border-border-subtle bg-surface-1 py-16">
          <Spinner className="text-text-secondary" />
        </div>
      </>
    );
  }

  if (isError || !data) {
    if (error instanceof ApiError && error.status === 403) {
      return <InsufficientPermissions />;
    }
    return (
      <>
        {backLink}
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить пользователя
            </p>
            <p className="mt-1 text-[13px] text-text-secondary">
              {error instanceof ApiError
                ? error.message
                : 'Проверьте соединение и попробуйте снова.'}
            </p>
          </div>
          <Button variant="outline" onClick={() => void refetch()} loading={isFetching}>
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      </>
    );
  }

  const { balance, subscription, revenue, media_stats: mediaStats } = data;

  return (
    <>
      {backLink}

      <div className="overflow-hidden rounded-card border border-border-subtle bg-surface-1">
        {/* Шапка: id + приложение/регистрация + действия (гейт backend-users:edit). */}
        <div className="flex flex-wrap items-start justify-between gap-4 px-5 py-4">
          <div>
            <h1 className="font-mono text-lg font-bold text-text-primary">{data.id}</h1>
            <p className="mt-1 text-[13px] text-text-secondary">
              Приложение: <span className="font-medium text-text-primary">{data.backend_name}</span>
              <span className="mx-2 text-border-strong">|</span>
              Регистрация: {formatDateTimeRu(data.registered_at)}
            </p>
          </div>
          {canEdit && (
            <div className="flex shrink-0 gap-2">
              <Button size="sm" onClick={() => setPlanOpen(true)}>
                Установить план
              </Button>
              <Button size="sm" variant="outline" onClick={() => setTokensOpen(true)}>
                Начислить токены
              </Button>
            </div>
          )}
        </div>

        <SectionHeader title="Подписка" />
        <div className="grid grid-cols-1 gap-px bg-border-subtle md:grid-cols-3">
          <MetricCell
            label="Баланс токенов"
            value={formatTokens(balance.tokens)}
            sub={[
              balance.credited_total != null &&
                `Начислено: ${formatTokens(balance.credited_total)}`,
              balance.spent_total != null && `Потрачено: ${formatTokens(balance.spent_total)}`,
            ]}
          />
          <MetricCell
            label="Текущий тариф"
            value={subscription.plan_name ?? subscription.plan_id ?? '—'}
            sub={[
              subscription.price,
              subscription.active && subscription.expires_at
                ? `до ${formatDateTimeRu(subscription.expires_at)}`
                : !subscription.active && 'не активна',
            ]}
          />
          <MetricCell
            label="Последняя покупка"
            value={
              subscription.last_payment_at ? formatDateTimeRu(subscription.last_payment_at) : '—'
            }
            sub={[subscription.last_payment_method]}
          />
        </div>

        {/* Экономика — опциональный блок контракта (§4.5): нет данных → секции нет. */}
        {revenue && (
          <>
            <SectionHeader title="Доход и провайдеры" />
            <div className="grid grid-cols-2 gap-px bg-border-subtle md:grid-cols-5">
              <MetricCell label="Доход" value={formatUsd(revenue.income_usd)} />
              <MetricCell label="Расход API" value={formatUsd(revenue.api_cost_usd)} />
              {Object.entries(revenue.providers).map(([name, cost]) => (
                <MetricCell key={name} label={name} value={formatUsd(cost)} />
              ))}
            </div>
          </>
        )}

        {/* Генерации — опциональный блок контракта (§4.5). */}
        {mediaStats && (
          <>
            <SectionHeader title="Генерация фото и видео" />
            <div className="grid grid-cols-1 gap-px bg-border-subtle md:grid-cols-3">
              <MediaCell label="Фото сгенерировано" counters={mediaStats.photos} />
              <MediaCell label="Видео сгенерировано" counters={mediaStats.videos} />
              <MetricCell
                label="Ср. время генерации"
                value={formatSeconds(mediaStats.avg_generation_sec.overall)}
                sub={[
                  mediaStats.avg_generation_sec.photo != null &&
                    `Фото: ${formatSeconds(mediaStats.avg_generation_sec.photo)}`,
                  mediaStats.avg_generation_sec.video != null &&
                    `Видео: ${formatSeconds(mediaStats.avg_generation_sec.video)}`,
                ]}
              />
            </div>
          </>
        )}

        {/* Вкладки «Оплаты» / «Запросы» (макет: подчёркнутый активный таб). */}
        <div className="grid grid-cols-2 border-t border-border-subtle">
          {(
            [
              ['payments', 'Оплаты'],
              ['requests', 'Запросы'],
            ] as Array<[TabKey, string]>
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={cn(
                'border-b-2 px-4 py-3 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent',
                tab === key
                  ? 'border-accent text-text-primary'
                  : 'border-transparent text-text-secondary hover:text-text-primary',
              )}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === 'payments' ? (
          <PaymentsTab backendId={backendId} userId={userId} />
        ) : (
          <RequestsTab backendId={backendId} userId={userId} />
        )}
      </div>

      {canEdit && (
        <>
          <AddTokensModal
            open={tokensOpen}
            onOpenChange={setTokensOpen}
            backendId={backendId}
            userId={userId}
          />
          <GrantPlanModal
            open={planOpen}
            onOpenChange={setPlanOpen}
            backendId={backendId}
            userId={userId}
          />
        </>
      )}
    </>
  );
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div className="border-y border-border-subtle bg-surface-2 px-5 py-2.5">
      <h2 className="text-[13px] font-semibold text-text-primary">{title}</h2>
    </div>
  );
}

function MetricCell({
  label,
  value,
  sub = [],
}: {
  label: string;
  value: string;
  sub?: Array<string | false | null | undefined>;
}) {
  const subParts = sub.filter((s): s is string => Boolean(s));
  return (
    <div className="bg-surface-1 px-5 py-4">
      <p className="text-[12px] text-text-tertiary">{label}</p>
      <p className="mt-1 text-xl font-bold text-text-primary">{value}</p>
      {subParts.length > 0 && (
        <p className="mt-1 text-[12px] text-text-secondary">{subParts.join(' · ')}</p>
      )}
    </div>
  );
}

function MediaCell({ label, counters }: { label: string; counters: BackendUserMediaCounters }) {
  return (
    <div className="bg-surface-1 px-5 py-4">
      <p className="text-[12px] text-text-tertiary">{label}</p>
      <p className="mt-1 text-xl font-bold text-text-primary">
        {counters.total.toLocaleString('ru-RU')}
      </p>
      <p className="mt-1 text-[12px] text-text-secondary">
        Успешных: <span className="text-status-green">{counters.success}</span> · Ошибок:{' '}
        <span className="text-status-red">{counters.failed}</span>
      </p>
    </div>
  );
}

function TabStates({
  isLoading,
  isError,
  isEmpty,
  emptyText,
}: {
  isLoading: boolean;
  isError: boolean;
  isEmpty: boolean;
  emptyText: string;
}) {
  if (isLoading) {
    return (
      <div className="flex justify-center border-t border-border-subtle py-10">
        <Spinner className="text-text-secondary" />
      </div>
    );
  }
  if (isError) {
    return (
      <p className="border-t border-border-subtle px-5 py-10 text-center text-[13px] text-text-secondary">
        Не удалось загрузить данные — попробуйте обновить страницу.
      </p>
    );
  }
  if (isEmpty) {
    return (
      <p className="border-t border-border-subtle px-5 py-10 text-center text-[13px] text-text-secondary">
        {emptyText}
      </p>
    );
  }
  return null;
}

function PaymentsTab({ backendId, userId }: { backendId: string; userId: string }) {
  const { data, isLoading, isError } = useBackendUserPayments(backendId, userId, true);
  const items = data?.items ?? [];
  const state = (
    <TabStates
      isLoading={isLoading}
      isError={isError}
      isEmpty={items.length === 0}
      emptyText="Оплат пока нет"
    />
  );
  if (isLoading || isError || items.length === 0) return state;

  return (
    <div className="border-t border-border-subtle">
      <div className="flex items-center gap-2 px-5 py-3">
        <h3 className="text-sm font-semibold text-text-primary">История оплат</h3>
        <span className="rounded-chip bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
          {data?.total ?? items.length}
        </span>
      </div>
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-y border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
            <th className="px-5 py-2.5 font-medium">Запрос</th>
            <th className="px-5 py-2.5 font-medium">Сумма</th>
            <th className="px-5 py-2.5 font-medium">Дата и время</th>
          </tr>
        </thead>
        <tbody>
          {items.map((payment, index) => (
            <tr key={index} className="border-b border-border-subtle last:border-b-0">
              <td className="px-5 py-3">
                <div className="flex items-center gap-3">
                  <span
                    className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-full',
                      payment.status === 'success'
                        ? 'bg-status-green/15 text-status-green'
                        : 'bg-surface-3 text-text-secondary',
                    )}
                    aria-hidden="true"
                  >
                    {payment.status === 'success' ? (
                      <Check className="h-4 w-4" />
                    ) : (
                      <X className="h-4 w-4" />
                    )}
                  </span>
                  <div>
                    <p className="font-medium text-text-primary">{payment.title}</p>
                    {payment.description && (
                      <p className="text-[12px] text-text-secondary">{payment.description}</p>
                    )}
                  </div>
                </div>
              </td>
              <td
                className={cn(
                  'px-5 py-3 font-semibold',
                  payment.status === 'success' ? 'text-status-green' : 'text-status-red',
                )}
              >
                {formatPaymentAmount(payment)}
              </td>
              <td className="px-5 py-3 text-text-secondary">
                {formatDateTimeRu(payment.occurred_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const REQUEST_STATUS_STYLE: Record<
  BackendUserRequestItem['status'],
  { dot: string; text: string }
> = {
  ok: { dot: 'bg-status-green', text: 'text-status-green' },
  slow: { dot: 'bg-status-yellow', text: 'text-status-yellow' },
  error: { dot: 'bg-status-red', text: 'text-status-red' },
};

/** «200 OK» / «200 Slow» / «500 Err» — код + словесный статус (макет). */
function requestStatusLabel(request: BackendUserRequestItem): string {
  const word = request.status === 'ok' ? 'OK' : request.status === 'slow' ? 'Slow' : 'Err';
  return `${request.status_code} ${word}`;
}

function RequestsTab({ backendId, userId }: { backendId: string; userId: string }) {
  const { data, isLoading, isError } = useBackendUserRequests(backendId, userId, true);
  const items = data?.items ?? [];
  const state = (
    <TabStates
      isLoading={isLoading}
      isError={isError}
      isEmpty={items.length === 0}
      emptyText="Бэк не передаёт историю запросов"
    />
  );
  if (isLoading || isError || items.length === 0) return state;

  return (
    <div className="border-t border-border-subtle">
      <div className="flex items-center gap-2 px-5 py-3">
        <h3 className="text-sm font-semibold text-text-primary">Последние запросы</h3>
        <span className="rounded-chip bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
          {data?.total ?? items.length}
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead>
            <tr className="border-y border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
              <th className="px-5 py-2.5 font-medium">Запрос</th>
              <th className="px-5 py-2.5 font-medium">Ответ от сервера</th>
              <th className="px-5 py-2.5 font-medium">Время обработки запроса</th>
              <th className="px-5 py-2.5 font-medium">Время отправки запроса</th>
            </tr>
          </thead>
          <tbody>
            {items.map((request, index) => {
              const style = REQUEST_STATUS_STYLE[request.status];
              return (
                <tr key={index} className="border-b border-border-subtle last:border-b-0">
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      <span
                        className={cn('h-2 w-2 shrink-0 rounded-full', style.dot)}
                        aria-hidden="true"
                      />
                      <code className="rounded-md bg-surface-3 px-2 py-0.5 font-mono text-[12px] text-text-primary">
                        {request.endpoint}
                      </code>
                      {request.prompt_preview && (
                        <span className="truncate text-[13px] text-text-secondary">
                          «{request.prompt_preview}»
                        </span>
                      )}
                    </div>
                  </td>
                  <td className={cn('px-5 py-3 font-medium', style.text)}>
                    {requestStatusLabel(request)}
                  </td>
                  <td className="px-5 py-3 text-text-secondary">
                    {formatSeconds(request.duration_sec)}
                  </td>
                  <td className="px-5 py-3 text-text-secondary">
                    {formatDateTimeRu(request.sent_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

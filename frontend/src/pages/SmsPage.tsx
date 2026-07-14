import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChangeEvent } from 'react';
import { AlertTriangle, Inbox, MessageSquare, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { SmsMessageCard } from '@/components/SmsMessageCard';
import { SmsNumberRow } from '@/components/SmsNumberRow';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import { useCan, useCanViewPage, useChannelTeamScope } from '@/features/auth/hooks';
import type { ChannelTeamScope } from '@/features/auth/channelTeams';
import {
  shouldRenderTeamFilter,
  teamFilterOptions,
  teamFilterParams,
} from '@/features/auth/channelTeams';
import { useSmsMessages, useSmsNumbers, useSyncSmsNumbers } from '@/features/sms/hooks';
import { ApiError } from '@/lib/api';
import type { SmsNumber } from '@/types/api';

type Tab = 'messages' | 'numbers';

/** Skeleton-карточки ленты / строк при начальной загрузке. */
function CardSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-24 animate-pulse rounded-card border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

/** Центрированная заглушка (пусто / ошибка / не найдено). */
function CenteredState({
  icon,
  title,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
      {icon}
      <p className="text-base font-semibold text-text-primary">{title}</p>
      {action}
    </div>
  );
}

export function SmsPage() {
  // Page-level view-guard `sms:view` (08-design-system.md, ADR-030): прямой URL без
  // права → заглушка «Недостаточно прав». Единственный хук до раннего возврата — гейт.
  const canView = useCanViewPage('sms');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <SmsContent />;
}

function SmsContent() {
  const [tab, setTab] = useState<Tab>('messages');
  // Команды канала «СМС» — ТОЛЬКО из `GET /api/auth/me` (`sms_teams` + `sms_includes_unassigned`,
  // ADR-055 §6.3), для ЛЮБОГО актора: источник и опций фильтра «Команда», и опций `Select`
  // переноса номера (гейт `sms:transfer` бывает у не-админа, а `GET /api/teams` под
  // `teams:view` возвращал ему пустой список — контрол молча деградировал).
  const smsScope = useChannelTeamScope('sms');
  const numbersQuery = useSmsNumbers();

  const numbers = numbersQuery.data?.numbers ?? [];

  const tabs: { key: Tab; label: string }[] = [
    { key: 'messages', label: 'Сообщения' },
    { key: 'numbers', label: 'Номера' },
  ];

  return (
    <div className="flex flex-col gap-4">
      <div role="tablist" aria-label="Разделы СМС" className="flex items-center gap-1">
        {tabs.map((t) => {
          const active = tab === t.key;
          return (
            <button
              key={t.key}
              type="button"
              role="tab"
              id={`sms-tab-${t.key}`}
              aria-selected={active}
              aria-controls={`sms-panel-${t.key}`}
              onClick={() => setTab(t.key)}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                active
                  ? 'bg-surface-2 text-text-primary'
                  : 'text-text-secondary hover:bg-surface-3 hover:text-text-primary',
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      {tab === 'messages' ? (
        <div role="tabpanel" id="sms-panel-messages" aria-labelledby="sms-tab-messages">
          <MessagesTab numbers={numbers} scope={smsScope} />
        </div>
      ) : (
        <div role="tabpanel" id="sms-panel-numbers" aria-labelledby="sms-tab-numbers">
          <NumbersTab
            numbers={numbers}
            scope={smsScope}
            isLoading={numbersQuery.isLoading}
            isError={numbersQuery.isError}
            isFetching={numbersQuery.isFetching}
            onRetry={() => void numbersQuery.refetch()}
          />
        </div>
      )}
    </div>
  );
}

function MessagesTab({ numbers, scope }: { numbers: SmsNumber[]; scope: ChannelTeamScope }) {
  const [numberId, setNumberId] = useState<number | undefined>(undefined);
  // '' (все) · UUID команды · '__no_team__' → серверный `no_team=true` (ADR-055 §5.3).
  const [teamFilter, setTeamFilter] = useState('');
  // Единое правило пяти экранов (ADR-055 §6.2): фильтр рендерится при ≥ 2 доступных вариантах
  // канала. Прежний гейт `sees_all_sms_teams` (ADR-036) — ОТМЕНЁН.
  const showTeamFilter = shouldRenderTeamFilter(scope);

  const { messages, phase, isFetchingMore, isReloading, hasMore, loadMore, reload } =
    useSmsMessages({ numberId, ...teamFilterParams(teamFilter) });

  const numberOptions: SelectOption[] = useMemo(
    () => [
      { value: '', label: 'Все номера' },
      ...numbers.map((n) => ({
        value: String(n.id),
        label: n.label ? `${n.phone_number} · ${n.label}` : n.phone_number,
      })),
    ],
    [numbers],
  );
  const teamOptions: SelectOption[] = useMemo(() => teamFilterOptions(scope), [scope]);

  // Фильтры комбинируемы (AND): выбор одного НЕ сбрасывает другой (в отличие от «Почты»).
  const handleNumberChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setNumberId(v ? Number(v) : undefined);
  };
  const handleTeamChange = (e: ChangeEvent<HTMLSelectElement>) => {
    setTeamFilter(e.target.value);
  };

  // IntersectionObserver-догрузка более старых (без кнопки, дедуп по id в хуке).
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node || !hasMore) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) loadMore();
      },
      { rootMargin: '200px' },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasMore, loadMore]);

  const toolbar = (
    <div className="flex flex-wrap items-center gap-2">
      <div className="w-56">
        <Select
          aria-label="Фильтр по номеру"
          options={numberOptions}
          value={numberId != null ? String(numberId) : ''}
          onChange={handleNumberChange}
        />
      </div>
      {showTeamFilter && (
        <div className="w-48">
          <Select
            aria-label="Фильтр по команде"
            options={teamOptions}
            value={teamFilter}
            onChange={handleTeamChange}
          />
        </div>
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      {toolbar}

      {phase === 'loading' && <CardSkeleton />}

      {phase === 'error' && (
        <CenteredState
          icon={<AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />}
          title="Не удалось загрузить"
          action={
            <Button variant="outline" onClick={reload} loading={isReloading}>
              <RefreshCw className="h-4 w-4" />
              Повторить
            </Button>
          }
        />
      )}

      {phase === 'ready' && messages.length === 0 && (
        <CenteredState
          icon={<Inbox className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
          title="Сообщений пока нет"
        />
      )}

      {phase === 'ready' && messages.length > 0 && (
        <div className="flex flex-col gap-3">
          {messages.map((m) => (
            <SmsMessageCard key={m.id} message={m} />
          ))}
          <div ref={sentinelRef} aria-hidden="true" className="h-px" />
          {isFetchingMore && (
            <div className="flex items-center justify-center gap-2 py-4 text-[12px] text-text-secondary">
              <Spinner className="text-text-secondary" />
              Загрузка…
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function NumbersTab({
  numbers,
  scope,
  isLoading,
  isError,
  isFetching,
  onRetry,
}: {
  numbers: SmsNumber[];
  /** Команды канала «СМС» из `/me` — опции переноса номера (ADR-055 §3.2/§6.3). */
  scope: ChannelTeamScope;
  isLoading: boolean;
  isError: boolean;
  isFetching: boolean;
  onRetry: () => void;
}) {
  const [search, setSearch] = useState('');
  const canEdit = useCan('sms', 'edit');
  const canTransfer = useCan('sms', 'transfer');
  const canDelete = useCan('sms', 'delete');
  const canSync = useCan('sms', 'sync');
  const syncMutation = useSyncSmsNumbers();

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return numbers;
    return numbers.filter((n) => n.phone_number.toLowerCase().includes(q));
  }, [numbers, search]);

  const handleSync = () => {
    syncMutation.mutate(undefined, {
      onSuccess: (res) => toast.success(`Синхронизировано: ${res.added} новых`),
      onError: (err) =>
        toast.error(err instanceof ApiError ? err.message : 'Не удалось синхронизировать'),
    });
  };

  const toolbar = (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="w-64">
        <Input
          aria-label="Поиск по номеру"
          placeholder="Поиск по номеру…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      {canSync && (
        <Button variant="outline" onClick={handleSync} loading={syncMutation.isPending}>
          <RefreshCw className="h-4 w-4" />
          Синхронизировать
        </Button>
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      {toolbar}

      {isLoading && <CardSkeleton />}

      {!isLoading && isError && (
        <CenteredState
          icon={<AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />}
          title="Не удалось загрузить"
          action={
            <Button variant="outline" onClick={onRetry} loading={isFetching}>
              <RefreshCw className="h-4 w-4" />
              Повторить
            </Button>
          }
        />
      )}

      {!isLoading && !isError && numbers.length === 0 && (
        <CenteredState
          icon={<MessageSquare className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
          title="Номеров нет"
        />
      )}

      {!isLoading && !isError && numbers.length > 0 && filtered.length === 0 && (
        <CenteredState
          icon={<MessageSquare className="h-9 w-9 text-text-tertiary" aria-hidden="true" />}
          title="Ничего не найдено"
        />
      )}

      {!isLoading && !isError && filtered.length > 0 && (
        <div className="scrollbar-none overflow-x-auto rounded-card border border-border-subtle bg-surface-1">
          <table className="w-full min-w-[720px] border-collapse text-left">
            <thead>
              <tr className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
                <th className="px-3 py-3 font-medium">Номер</th>
                <th className="px-3 py-3 font-medium">Логин</th>
                <th className="px-3 py-3 font-medium">Приложение</th>
                <th className="px-3 py-3 font-medium">Примечание</th>
                <th className="px-3 py-3 font-medium">Команда</th>
                {/* Удаление — компактная иконка Trash2 без текстового заголовка (ADR-033) */}
                <th className="px-3 py-3 font-medium">
                  <span className="sr-only">Удаление</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((n) => (
                <SmsNumberRow
                  key={n.id}
                  number={n}
                  teams={scope.teams}
                  // Опция «Без команды» (снятие, `team_id=null`) — только тому, кто вправе её
                  // выбрать: admin-уровню всегда, не-админу — только при
                  // `me.sms_includes_unassigned` (иначе снятие → 403, ADR-055 §3.2 п.2).
                  allowNoTeam={scope.includesUnassigned}
                  canEdit={canEdit}
                  canTransfer={canTransfer}
                  canDelete={canDelete}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

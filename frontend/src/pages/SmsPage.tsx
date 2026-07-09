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
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useSmsMessages, useSmsNumbers, useSyncSmsNumbers } from '@/features/sms/hooks';
import { useTeams } from '@/features/teams/hooks';
import { ApiError } from '@/lib/api';
import type { SmsNumber, TeamListItem } from '@/types/api';

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
  const numbersQuery = useSmsNumbers();
  const teamsQuery = useTeams();

  const numbers = numbersQuery.data?.numbers ?? [];
  const teams = teamsQuery.data?.items ?? [];

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
          <MessagesTab numbers={numbers} teams={teams} />
        </div>
      ) : (
        <div role="tabpanel" id="sms-panel-numbers" aria-labelledby="sms-tab-numbers">
          <NumbersTab
            numbers={numbers}
            teams={teams}
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

function MessagesTab({ numbers, teams }: { numbers: SmsNumber[]; teams: TeamListItem[] }) {
  const [numberId, setNumberId] = useState<number | undefined>(undefined);
  const [teamId, setTeamId] = useState<string | undefined>(undefined);

  const { messages, phase, isFetchingMore, isReloading, hasMore, loadMore, reload } =
    useSmsMessages({ numberId, teamId });

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
  const teamOptions: SelectOption[] = useMemo(
    () => [
      { value: '', label: 'Все команды' },
      ...teams.map((t) => ({ value: t.id, label: t.name })),
    ],
    [teams],
  );

  // Фильтры комбинируемы (AND): выбор одного НЕ сбрасывает другой (в отличие от «Почты»).
  const handleNumberChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setNumberId(v ? Number(v) : undefined);
  };
  const handleTeamChange = (e: ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    setTeamId(v || undefined);
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
      <div className="w-48">
        <Select
          aria-label="Фильтр по команде"
          options={teamOptions}
          value={teamId ?? ''}
          onChange={handleTeamChange}
        />
      </div>
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
  teams,
  isLoading,
  isError,
  isFetching,
  onRetry,
}: {
  numbers: SmsNumber[];
  teams: TeamListItem[];
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
                <th className="px-3 py-3 font-medium">Действия</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((n) => (
                <SmsNumberRow
                  key={n.id}
                  number={n}
                  teams={teams}
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

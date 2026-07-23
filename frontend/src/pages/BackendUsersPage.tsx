import { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  Search,
} from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Pill } from '@/components/ui/Pill';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatDateTimeRu } from '@/lib/format';
import { useCanViewPage } from '@/features/auth/hooks';
import { useBackendUsers } from '@/features/backend-users/hooks';
import { useBackends } from '@/features/backends/hooks';

const PAGE_SIZE = 50;

/** «305 577» / «$101 452» — целые с пробелами-разрядами (ru-RU). */
function formatInt(value: number): string {
  return Math.round(value).toLocaleString('ru-RU');
}

function formatUsd(value: number): string {
  return `$${formatInt(value)}`;
}

export function BackendUsersPage() {
  // Page-level view-guard (ADR-021 §6): без `backend-users:view` — заглушка.
  const canView = useCanViewPage('backend-users');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <BackendUsersList />;
}

function BackendUsersList() {
  const navigate = useNavigate();

  // Фильтры (макет: поиск по User ID, приложение, период по дате регистрации).
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [backendId, setBackendId] = useState<string>('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  // Фильтр «Платный»: null → все, true → «Да», false → «Нет» (цикл по клику в шапке).
  const [isPaid, setIsPaid] = useState<boolean | null>(null);
  const [page, setPage] = useState(0);

  // Debounce поиска: запрос уходит через 400мс после остановки ввода.
  useEffect(() => {
    const timer = setTimeout(() => setSearch(searchInput.trim()), 400);
    return () => clearTimeout(timer);
  }, [searchInput]);

  // Любая смена фильтра сбрасывает страницу.
  useEffect(() => {
    setPage(0);
  }, [search, backendId, dateFrom, dateTo, isPaid]);

  const params = useMemo(
    () => ({
      backendId: backendId || null,
      search,
      dateFrom: dateFrom || null,
      dateTo: dateTo || null,
      isPaid,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [backendId, search, dateFrom, dateTo, isPaid, page],
  );

  const { data, isLoading, isError, error, refetch, isFetching } = useBackendUsers(params);

  // Опции фильтра «приложение» — реестр бэков; при 403 (нет `backends:view`)
  // фильтр скрывается, страница остаётся рабочей в режиме «Все приложения».
  const backendsQuery = useBackends();
  const backendOptions = useMemo(() => {
    const items = backendsQuery.data?.items ?? [];
    return [
      { value: '', label: 'Все приложения' },
      ...items.filter((b) => b.has_admin_api_key).map((b) => ({ value: b.id, label: b.name })),
    ];
  }, [backendsQuery.data?.items]);
  const showBackendFilter = !(
    backendsQuery.error instanceof ApiError && backendsQuery.error.status === 403
  );

  const forbiddenMessage = error instanceof ApiError && error.status === 403 ? error.message : null;
  if (isError && forbiddenMessage) {
    return <InsufficientPermissions />;
  }

  const stats = data?.stats;
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const sourceErrors = data?.errors ?? [];
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const cyclePaidFilter = () => setIsPaid((prev) => (prev === null ? true : prev ? false : null));

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Пользователи бэков</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          {isLoading ? 'Загрузка…' : `${formatInt(total)} пользователей по подключённым бэкам`}
        </p>
      </div>

      {/* Тулбар фильтров (макет: поиск · приложение · период). */}
      <div className="mb-4 flex flex-wrap items-center gap-3 rounded-card border border-border-subtle bg-surface-1 p-4">
        <div className="w-64">
          <Input
            aria-label="Поиск по User ID"
            placeholder="Поиск по User ID"
            value={searchInput}
            trailing={<Search className="h-4 w-4 text-text-tertiary" aria-hidden="true" />}
            onChange={(e) => setSearchInput(e.target.value)}
          />
        </div>
        {showBackendFilter && (
          <div className="w-56">
            <Select
              aria-label="Приложение"
              options={backendOptions}
              value={backendId}
              onChange={(e) => setBackendId(e.target.value)}
            />
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-text-secondary">Период:</span>
          <Input
            aria-label="Дата от"
            type="date"
            className="w-40"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
          />
          <span className="text-text-tertiary">—</span>
          <Input
            aria-label="Дата до"
            type="date"
            className="w-40"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
          />
        </div>
        {isFetching && !isLoading && <Spinner className="ml-auto text-text-tertiary" />}
      </div>

      {/* Partial-data warning: часть бэков не ответила (агрегация продолжена без них). */}
      {sourceErrors.length > 0 && (
        <div className="mb-4 flex items-start gap-3 rounded-card border border-status-yellow/40 bg-status-yellow/10 px-4 py-3">
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-status-yellow"
            aria-hidden="true"
          />
          <div className="text-[13px] text-text-primary">
            <p className="font-medium">Часть бэков не ответила — данные неполные:</p>
            {sourceErrors.map((e) => (
              <p key={e.backend_id} className="text-text-secondary">
                {e.backend_name} — {e.message}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Сводка (макет: всего · платных · сумма оплат · CR%). */}
      <div className="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-card border border-border-subtle bg-border-subtle lg:grid-cols-4">
        <SummaryCell
          label="Всего пользователей"
          value={stats ? formatInt(stats.users_total) : '—'}
        />
        <SummaryCell
          label="Платных пользователей"
          value={stats ? formatInt(stats.paid_users) : '—'}
        />
        <SummaryCell label="Сумма оплат" value={stats ? formatUsd(stats.payments_sum_usd) : '—'} />
        <SummaryCell
          label="CR%"
          value={stats ? `${stats.cr_percent.toLocaleString('ru-RU')}%` : '—'}
        />
      </div>

      {isLoading && (
        <div className="flex items-center justify-center rounded-card border border-border-subtle bg-surface-1 py-16">
          <Spinner className="text-text-secondary" />
        </div>
      )}

      {isError && !forbiddenMessage && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить пользователей
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
      )}

      {!isLoading && !isError && items.length === 0 && (
        <div className="rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
          <p className="text-sm font-medium text-text-primary">
            {total === 0 && !search && isPaid === null && !dateFrom && !dateTo
              ? 'Нет данных: подключите бэк с Admin API Key (контракт CRM Admin API v1)'
              : 'Ничего не найдено'}
          </p>
        </div>
      )}

      {!isLoading && !isError && items.length > 0 && (
        <div className="overflow-x-auto rounded-card border border-border-subtle bg-surface-1">
          <table className="w-full min-w-[840px] text-left text-sm">
            <thead>
              <tr className="border-b border-border-subtle text-[12px] uppercase tracking-wide text-text-tertiary">
                <th className="px-4 py-3 font-medium">User ID</th>
                <th className="px-4 py-3 font-medium">
                  {/* Клик циклирует фильтр: Все → Да → Нет (макет: фильтр в шапке). */}
                  <button
                    type="button"
                    onClick={cyclePaidFilter}
                    className="inline-flex items-center gap-1 uppercase tracking-wide hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    Платный{isPaid !== null && `: ${isPaid ? 'да' : 'нет'}`}
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </th>
                <th className="px-4 py-3 font-medium">Оплаты</th>
                <th className="px-4 py-3 font-medium">Продлений</th>
                <th className="px-4 py-3 font-medium">Приложение</th>
                <th className="px-4 py-3 font-medium">Регистрация</th>
              </tr>
            </thead>
            <tbody>
              {items.map((user) => (
                <tr
                  key={`${user.backend_id}:${user.id}`}
                  onClick={() =>
                    navigate(`/backend-users/${user.backend_id}/${encodeURIComponent(user.id)}`)
                  }
                  className="cursor-pointer border-b border-border-subtle transition-colors last:border-b-0 hover:bg-surface-2"
                >
                  <td className="px-4 py-3 font-mono text-[13px] text-text-primary">{user.id}</td>
                  <td className="px-4 py-3">
                    <Pill
                      tone={user.is_paid ? 'green' : 'neutral'}
                      label={user.is_paid ? 'Да' : 'Нет'}
                    />
                  </td>
                  <td className="px-4 py-3 text-text-secondary">{user.payments_count}</td>
                  <td className="px-4 py-3 text-text-secondary">{user.renewals_count}</td>
                  <td className="px-4 py-3 font-medium text-text-primary">{user.backend_name}</td>
                  <td className="px-4 py-3 text-text-secondary">
                    {formatDateTimeRu(user.registered_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Пагинация: «N–M из T» + prev/next. */}
      {!isLoading && !isError && total > PAGE_SIZE && (
        <div className="mt-4 flex items-center justify-end gap-3 text-[13px] text-text-secondary">
          <span>
            {formatInt(page * PAGE_SIZE + 1)}–{formatInt(Math.min((page + 1) * PAGE_SIZE, total))}{' '}
            из {formatInt(total)}
          </span>
          <Button
            variant="outline"
            size="sm"
            aria-label="Предыдущая страница"
            disabled={page === 0 || isFetching}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Следующая страница"
            disabled={page >= pageCount - 1 || isFetching}
            onClick={() => setPage((p) => p + 1)}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </>
  );
}

function SummaryCell({ label, value }: { label: string; value: string }) {
  return (
    <div className={cn('bg-surface-1 px-5 py-4')}>
      <p className="text-[12px] text-text-tertiary">{label}</p>
      <p className="mt-1 text-xl font-bold text-text-primary">{value}</p>
    </div>
  );
}

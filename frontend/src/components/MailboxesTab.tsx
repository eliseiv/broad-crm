import { useMemo, useState } from 'react';
import type { ChangeEvent } from 'react';
import { AlertTriangle, Inbox, Mail, Plus, RefreshCw, Search } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { MailboxRow } from '@/components/MailboxRow';
import { cn } from '@/lib/cn';
import { ApiError } from '@/lib/api';
import { useCan, useSeesAllMailTeams } from '@/features/auth/hooks';
import { useMailboxesManage } from '@/features/mail/hooks';
import { useTeams } from '@/features/teams/hooks';

/** Значение опции «Без команды» в клиентском фильтре по команде (ящики с `team_id = null`). */
const NO_TEAM = '__no_team__';

/** Значение сегмента активности → query `is_active` (не задан / true / false). */
type ActivityFilter = 'all' | 'active' | 'inactive';

const SEGMENTS: { key: ActivityFilter; label: string }[] = [
  { key: 'all', label: 'Все' },
  { key: 'active', label: 'Активные' },
  { key: 'inactive', label: 'Неактивные' },
];

function toIsActive(filter: ActivityFilter): boolean | undefined {
  if (filter === 'active') return true;
  if (filter === 'inactive') return false;
  return undefined;
}

/** Skeleton-строки таблицы при начальной загрузке. */
function TableSkeleton() {
  return (
    <div className="flex flex-col gap-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-14 animate-pulse rounded-card border border-border-subtle bg-surface-1"
        />
      ))}
    </div>
  );
}

function CenteredState({
  icon,
  title,
  hint,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-16 text-center">
      {icon}
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        {hint && <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>}
      </div>
      {action}
    </div>
  );
}

/**
 * Вкладка «Почты» (08-design-system.md «Вкладка Почты», ADR-038): таблица ящиков с
 * цветным кружком статуса, сегмент активности (Все/Активные/Неактивные), CRUD и
 * форс-синк. Мутации гейтятся `mail:create/edit/delete/sync`; просмотр — под `mail:view`.
 */
export function MailboxesTab() {
  const [filter, setFilter] = useState<ActivityFilter>('all');
  const [addOpen, setAddOpen] = useState(false);
  // Поиск и фильтр по команде — КЛИЕНТСКИЕ (ADR-050 §1): каталог ящиков грузится ЦЕЛИКОМ
  // (пагинации в контракте `GET /api/mail/mailboxes` нет), поэтому клиентская фильтрация
  // полна и точна. Серверных параметров `q`/`team_id` у эндпоинта НЕТ — backend не меняется.
  const [search, setSearch] = useState('');
  const [teamFilter, setTeamFilter] = useState('');

  const canCreate = useCan('mail', 'create');
  const canEdit = useCan('mail', 'edit');
  const canSync = useCan('mail', 'sync');
  const canDelete = useCan('mail', 'delete');
  // Admin-уровень видимости почты (`me.sees_all_mail_teams`): гейт переноса ящика между
  // командами (ADR-044 §4) И гейт рендера фильтра по команде (ADR-050 §1.2 — та же норма,
  // что у фильтра «Команда» вкладки «Сообщения», ADR-036).
  const seesAllMailTeams = useSeesAllMailTeams();
  const canTransfer = seesAllMailTeams;

  const query = useMailboxesManage(toIsActive(filter));
  // CRM-команды (GET /api/teams) — источник дропдауна переноса и резолва имени команды.
  const teamsQuery = useTeams();
  const teams = useMemo(() => teamsQuery.data?.items ?? [], [teamsQuery.data]);
  const mailboxes = useMemo(() => query.data?.mailboxes ?? [], [query.data]);

  const isNotConfigured = query.error instanceof ApiError && query.error.status === 503;

  // Порядок применения фильтров (ADR-050 §1.3): серверный `is_active` → клиентский поиск →
  // клиентский фильтр по команде. Все три комбинируются (AND); ни один не сбрасывает другие.
  const searchQuery = search.trim().toLowerCase();
  const searchActive = searchQuery.length > 0;
  const teamFilterActive = teamFilter !== '';

  const visible = useMemo(() => {
    let rows = mailboxes;
    if (searchQuery) {
      // Поля поиска — ровно три (ADR-050 §1.1): `number`, `app_name`, `email`. Подстрока,
      // регистронезависимо. `display_name` не входит (производная склейка number+app_name).
      rows = rows.filter(
        (mb) =>
          (mb.number ?? '').toLowerCase().includes(searchQuery) ||
          (mb.app_name ?? '').toLowerCase().includes(searchQuery) ||
          mb.email.toLowerCase().includes(searchQuery),
      );
    }
    if (teamFilter) {
      rows = rows.filter((mb) =>
        teamFilter === NO_TEAM ? mb.team_id === null : mb.team_id === teamFilter,
      );
    }
    return rows;
  }, [mailboxes, searchQuery, teamFilter]);

  // Опции фильтра по команде: «Все команды» (сброс) → команды из GET /api/teams → «Без команды»
  // (ящики с `team_id = null` — иначе были бы нефильтруемы). ADR-050 §1.2.
  const teamFilterOptions = useMemo(
    () => [
      { value: '', label: 'Все команды' },
      ...teams.map((t) => ({ value: t.id, label: t.name })),
      { value: NO_TEAM, label: 'Без команды' },
    ],
    [teams],
  );

  const handleTeamFilterChange = (e: ChangeEvent<HTMLSelectElement>) => {
    setTeamFilter(e.target.value);
  };

  const segment = (
    <div
      role="group"
      aria-label="Фильтр активности"
      className="inline-flex items-center gap-1 rounded-[10px] border border-border-subtle bg-surface-1 p-1"
    >
      {SEGMENTS.map((s) => {
        const active = filter === s.key;
        return (
          <button
            key={s.key}
            type="button"
            aria-pressed={active}
            onClick={() => setFilter(s.key)}
            className={cn(
              'rounded-md px-3 py-1 text-[13px] font-medium transition-colors',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              active
                ? 'bg-surface-3 text-text-primary'
                : 'text-text-secondary hover:text-text-primary',
            )}
          >
            {s.label}
          </button>
        );
      })}
    </div>
  );

  // Тулбар вкладки (ADR-050 §1): поиск и фильтр по команде — В ОДНОЙ СТРОКЕ, рядом с
  // сегментом «Все/Активные/Неактивные». При нехватке ширины контролы переносятся
  // (`flex-wrap`), значимый текст не обрезается.
  const toolbar = (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap items-center gap-3">
        {segment}
        <div className="w-64">
          <Input
            aria-label="Поиск по почтам"
            placeholder="Поиск по почтам…"
            value={search}
            trailing={<Search className="h-4 w-4 text-text-tertiary" aria-hidden="true" />}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {/* Фильтр по команде — та же норма, что у фильтра «Команда» вкладки «Сообщения»
            (ADR-036): рендерится ТОЛЬКО при `sees_all_mail_teams === true`; для прочих
            ролей отсутствует (не пустой, не disabled) — опции берутся из `GET /api/teams`
            под чужим гейтом `teams:view`. */}
        {seesAllMailTeams && (
          <div className="w-48">
            <Select
              aria-label="Команда"
              options={teamFilterOptions}
              value={teamFilter}
              onChange={handleTeamFilterChange}
            />
          </div>
        )}
      </div>
      {canCreate && (
        <Button size="sm" onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" />
          Добавить почту
        </Button>
      )}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      {toolbar}

      {query.isLoading && <TableSkeleton />}

      {!query.isLoading && isNotConfigured && (
        <CenteredState
          icon={<Mail className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
          title="Сервис почт не настроен"
          hint="Обратитесь к администратору для настройки почтового сервиса."
        />
      )}

      {!query.isLoading && query.isError && !isNotConfigured && (
        <CenteredState
          icon={<AlertTriangle className="h-10 w-10 text-status-red" aria-hidden="true" />}
          title="Почтовый сервис временно недоступен"
          action={
            <Button
              variant="outline"
              onClick={() => void query.refetch()}
              loading={query.isFetching}
            >
              <RefreshCw className="h-4 w-4" />
              Повторить
            </Button>
          }
        />
      )}

      {/* Пустой каталог — «Почт пока нет»; активный фильтр/поиск без совпадений —
          «Ничего не найдено» (ADR-050 §1.1: эти состояния НЕ путать). */}
      {!query.isLoading && !query.isError && visible.length === 0 && (
        <CenteredState
          icon={<Inbox className="h-10 w-10 text-text-tertiary" aria-hidden="true" />}
          title={
            filter === 'all' && !searchActive && !teamFilterActive
              ? 'Почт пока нет'
              : 'Ничего не найдено'
          }
          hint={
            filter === 'all' && !searchActive && !teamFilterActive && canCreate
              ? 'Добавьте первый почтовый ящик, чтобы получать письма.'
              : undefined
          }
        />
      )}

      {!query.isLoading && !query.isError && visible.length > 0 && (
        <div className="scrollbar-none overflow-x-auto rounded-card border border-border-subtle bg-surface-1">
          <table className="w-full min-w-[760px] border-collapse text-left">
            <thead>
              {/* Отдельной колонки «Статус» НЕТ (ADR-047 §5): кружок переехал внутрь
                  идентификационной ячейки ящика. Колонка «Команда» получает долю ширины,
                  достаточную для чтения значения целиком (ADR-047 §4). */}
              <tr className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
                <th className="px-3 py-3 font-medium">Почта</th>
                <th className="w-[22rem] px-3 py-3 font-medium">Команда</th>
                <th className="px-3 py-3 font-medium">Синхронизация</th>
                {/* relative: даёт абсолютному `sr-only` позиционированного предка ВНУТРИ
                    overflow-x-auto обёртки, иначе его containing block — ICB, и он выпадает
                    из клипа обёртки, растягивая scrollWidth документа на узких вьюпортах. */}
                <th className="relative px-3 py-3 font-medium">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.map((mb) => (
                <MailboxRow
                  key={mb.id}
                  mailbox={mb}
                  teams={teams}
                  canTransfer={canTransfer}
                  canEdit={canEdit}
                  canSync={canSync}
                  canDelete={canDelete}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {query.isFetching && !query.isLoading && (
        <div className="flex items-center justify-center gap-2 py-1 text-[12px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Обновление…
        </div>
      )}

      <MailboxFormModal open={addOpen} onOpenChange={setAddOpen} mode="add" />
    </div>
  );
}

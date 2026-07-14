import { useMemo, useState } from 'react';
import type { ChangeEvent } from 'react';
import { AlertTriangle, Inbox, Mail, Plus, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Combobox } from '@/components/ui/Combobox';
import { Select } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { MailboxRow } from '@/components/MailboxRow';
import { cn } from '@/lib/cn';
import { ApiError } from '@/lib/api';
import { useCan, useChannelTeamScope, useSeesAllMailTeams } from '@/features/auth/hooks';
import {
  NO_TEAM_VALUE,
  shouldRenderTeamFilter,
  teamFilterOptions,
} from '@/features/auth/channelTeams';
import { useMailboxesManage } from '@/features/mail/hooks';
import { mailboxSearchKeywords, matchesMailboxQuery } from '@/features/mail/mailboxSearch';

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
  // Поиск/выбор почты и фильтр по команде — КЛИЕНТСКИЕ (ADR-050 §1, ADR-052 §3): каталог
  // ящиков грузится ЦЕЛИКОМ (пагинации в контракте `GET /api/mail/mailboxes` нет), поэтому
  // клиентская фильтрация полна и точна. Серверных `q`/`team_id` у эндпоинта НЕТ.
  // ГИБРИДНАЯ семантика `ui/Combobox` `mode='search'` (ADR-052 §3.1): текст и выбор
  // ВЗАИМОИСКЛЮЧАЮЩИ (ввод сбрасывает выбор — это делает сам примитив).
  const [search, setSearch] = useState('');
  const [selectedMailboxId, setSelectedMailboxId] = useState<number | null>(null);
  const [teamFilter, setTeamFilter] = useState('');

  const canCreate = useCan('mail', 'create');
  const canEdit = useCan('mail', 'edit');
  const canSync = useCan('mail', 'sync');
  const canDelete = useCan('mail', 'delete');
  // Перенос ящика между командами — ТОЛЬКО admin-уровень (ADR-044 §4, не разворачивается).
  // Рендер фильтра «Команда» по этому признаку БОЛЬШЕ НЕ гейтится (ADR-055 §6.2 отменил
  // норму ADR-050 §1.2/ADR-036) — см. `showTeamFilter` ниже.
  const seesAllMailTeams = useSeesAllMailTeams();
  const canTransfer = seesAllMailTeams;

  const query = useMailboxesManage(toIsActive(filter));
  // Команды канала — ТОЛЬКО из `GET /api/auth/me` (`mail_teams`, ADR-055 §6.3): источник опций
  // фильтра, дропдауна переноса и резолва имени команды в строке. `GET /api/teams` гейтится
  // `teams:view` (у mail-оператора его нет ⇒ пустой список — прод-баг TD-050) и здесь НЕ
  // используется. Все видимые ящики ∈ scope актора ⇒ имя команды резолвится.
  const mailScope = useChannelTeamScope('mail');
  const teams = mailScope.teams;
  const showTeamFilter = shouldRenderTeamFilter(mailScope);
  const mailboxes = useMemo(() => query.data?.mailboxes ?? [], [query.data]);

  const isNotConfigured = query.error instanceof ApiError && query.error.status === 503;

  // Порядок применения фильтров (ADR-050 §1.3 в редакции ADR-052 §3.2): серверный `is_active`
  // → клиентский combobox (ВЫБОР ИЛИ ТЕКСТ) → клиентский фильтр по команде. Все три
  // комбинируются (AND); ни один не сбрасывает другие — В ОБЕ СТОРОНЫ: смена сегмента/команды
  // НЕ сбрасывает выбор почты и текст (ADR-052 §3.1а). Выбранный ящик вне набора → таблица
  // пуста → «Ничего не найдено» (штатное пустое пересечение, авто-сброса НЕТ).
  const searchActive = selectedMailboxId !== null || search.trim() !== '';
  const teamFilterActive = teamFilter !== '';

  const visible = useMemo(() => {
    let rows = mailboxes;
    // Выбор из списка → ровно один ящик («быстрый переход к конкретной почте»); иначе текст →
    // ВСЕ совпадения по единому предикату (таблица НЕ схлопывается до одной строки при вводе).
    if (selectedMailboxId !== null) {
      rows = rows.filter((mb) => mb.id === selectedMailboxId);
    } else if (search.trim() !== '') {
      rows = rows.filter((mb) => matchesMailboxQuery(mb, search));
    }
    if (teamFilter) {
      rows = rows.filter((mb) =>
        teamFilter === NO_TEAM_VALUE ? mb.team_id === null : mb.team_id === teamFilter,
      );
    }
    return rows;
  }, [mailboxes, selectedMailboxId, search, teamFilter]);

  // Опции списка — из ТОГО ЖЕ набора, что рендерит таблица (серверный сегмент активности уже
  // применён). Опции сброса («Все почты») здесь НЕТ — сброс делает `X` / `Escape` (ADR-052 §3).
  // Лейбл — тот же, что на вкладке «Сообщения»; ключи поиска — единый предикат (§3.3).
  const mailboxOptions = useMemo(
    () =>
      mailboxes.map((mb) => ({
        value: String(mb.id),
        label: mb.display_name ? `${mb.display_name} ${mb.email}` : mb.email,
        keywords: mailboxSearchKeywords(mb),
      })),
    [mailboxes],
  );

  // Опции фильтра: «Все команды» (сброс) → команды канала из `me.mail_teams` → «Без команды»
  // (ТОЛЬКО при `me.mail_includes_unassigned` — иначе бесхозных ящиков актор не видит и
  // опция дала бы гарантированно пустой результат). ADR-055 §6.2/§6.3.
  const teamOptions = useMemo(() => teamFilterOptions(mailScope), [mailScope]);

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
        {/* Поиск/выбор почты — `ui/Combobox` `mode='search'` (ADR-052 §3): ввод фильтрует
            ТАБЛИЦУ (все совпадения) и список; выбор опции сужает таблицу до ОДНОЙ строки.
            Иконка — `ChevronDown` (не `Search`). */}
        <div className="w-64">
          <Combobox
            aria-label="Поиск по почтам"
            mode="search"
            placeholder="Поиск по почтам…"
            options={mailboxOptions}
            value={selectedMailboxId != null ? String(selectedMailboxId) : null}
            onChange={(v) => setSelectedMailboxId(v ? Number(v) : null)}
            query={search}
            onQueryChange={setSearch}
            loading={query.isLoading}
          />
        </div>
        {/* Фильтр по команде — КЛИЕНТСКИЙ (каталог ящиков загружен целиком), но правило
            рендера и источник опций — ЕДИНЫЕ на пяти экранах (ADR-055 §6.2): контрол
            рендерится при ≥ 2 доступных вариантах канала (команды + «Без команды»); при
            одном варианте отсутствует. Прежний гейт `sees_all_mail_teams` — ОТМЕНЁН. */}
        {showTeamFilter && (
          <div className="w-48">
            <Select
              aria-label="Команда"
              options={teamOptions}
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

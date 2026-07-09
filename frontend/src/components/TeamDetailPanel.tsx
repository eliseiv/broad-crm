import { useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, ChevronDown, RefreshCw } from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Pill } from '@/components/ui/Pill';
import { Spinner } from '@/components/ui/Spinner';
import { cn } from '@/lib/cn';
import { useTeamMailboxes } from '@/features/mail/hooks';
import { useTeamNumbers } from '@/features/sms/hooks';
import type { TeamMailboxItem, TeamNumberItem, TeamListItem } from '@/types/api';

/** `-` для пустых значений пилюль (строка не «прыгает»), 08-design-system.md. */
function orDash(value: string | null | undefined): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : '-';
}

/**
 * Строка номера команды (08-design-system.md §detail-панель, ADR-034): Номер + пилюли
 * Логин / Приложение. Номер не разрывается посреди цифр (`whitespace-nowrap`); пустые
 * значения пилюль → `-`. `note`/`label` не показываются (сужены под `sms:*`).
 */
function NumberRow({ number }: { number: TeamNumberItem }) {
  const login = orDash(number.login);
  const appName = orDash(number.app_name);
  return (
    <div className="flex flex-col gap-2 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
      <span className="whitespace-nowrap font-mono text-[13px] text-text-primary">
        {number.phone_number}
      </span>
      <div className="flex flex-wrap items-center gap-1.5">
        <Pill tone="accent" label={`Логин: ${login}`} title={login} wrap />
        <Pill tone="yellow" label={`Приложение: ${appName}`} title={appName} wrap />
      </div>
    </div>
  );
}

/**
 * Строка ящика команды (08-design-system.md §«Почты команды», ADR-038): цветной кружок
 * статуса (`Badge` dot: green — активна, red — неактивна) + адрес (+ display_name).
 * Схема `TeamMailboxItem` — без кредов/статуса синка. Адрес виден полностью (break-all).
 */
function MailboxRow({ mailbox }: { mailbox: TeamMailboxItem }) {
  return (
    <div className="flex flex-col gap-1 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
      <div className="flex items-center gap-2">
        <Badge tone={mailbox.is_active ? 'green' : 'red'} dot>
          {mailbox.is_active ? 'Активна' : 'Неактивна'}
        </Badge>
      </div>
      <span className="break-all font-mono text-[13px] text-text-primary">{mailbox.email}</span>
      {mailbox.display_name && (
        <span className="break-words text-[12px] text-text-secondary">{mailbox.display_name}</span>
      )}
    </div>
  );
}

interface CollapsibleSectionProps {
  title: string;
  id: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/** Раскрывающаяся секция detail-панели (свёрнута по умолчанию, ленивая загрузка контента). */
function CollapsibleSection({ title, id, open, onToggle, children }: CollapsibleSectionProps) {
  return (
    <div className="rounded-sub border border-border-subtle">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={onToggle}
        className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
      >
        <span className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
          {title}
        </span>
        <ChevronDown
          className={cn('h-4 w-4 text-text-tertiary transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div id={id} className="border-t border-border-subtle px-3 py-3">
          {children}
        </div>
      )}
    </div>
  );
}

/** Общий блок состояний загрузки/ошибки внутри секции (loading/error). */
function SectionStatus({
  loading,
  error,
  onRetry,
  fetching,
}: {
  loading: boolean;
  error: boolean;
  onRetry: () => void;
  fetching: boolean;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 py-2 text-[13px] text-text-secondary">
        <Spinner className="text-text-secondary" />
        Загрузка…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
        <AlertTriangle className="h-5 w-5 text-status-red" aria-hidden="true" />
        <span className="text-[13px] text-text-secondary">Не удалось загрузить</span>
        <Button variant="outline" size="sm" onClick={onRetry} loading={fetching}>
          <RefreshCw className="h-4 w-4" />
          Повторить
        </Button>
      </div>
    );
  }
  return null;
}

interface TeamDetailPanelProps {
  team: TeamListItem;
  /** id для aria-controls аккордеона (связь с кликабельной шапкой карточки). */
  id: string;
}

/**
 * Detail-панель команды (аккордеон на /teams, 08-design-system.md «Доработка /teams»,
 * ADR-030/038): Название / Лидер / Участники + две раскрывающиеся секции «Номера команды»
 * и «Почты команды» — обе свёрнуты по умолчанию, с ленивой загрузкой при первом раскрытии
 * (запросы `enabled` привязаны к состоянию раскрытия). Только просмотр — редактирование
 * состава через модалку (карандаш в шапке карточки).
 */
export function TeamDetailPanel({ team, id }: TeamDetailPanelProps) {
  const [numbersOpen, setNumbersOpen] = useState(false);
  const [mailboxesOpen, setMailboxesOpen] = useState(false);

  const numbersQuery = useTeamNumbers(team.id, numbersOpen);
  const mailboxesQuery = useTeamMailboxes(team.id, mailboxesOpen);

  const numbers = numbersQuery.data?.numbers ?? [];
  const mailboxes = mailboxesQuery.data?.mailboxes ?? [];
  // «Почты не привязаны» — команда без mail_group_id; «Почт нет» — привязка есть, ящиков нет.
  const mailboxesEmptyText = team.mail_group_id == null ? 'Почты не привязаны' : 'Почт нет';

  return (
    <div id={id} className="flex flex-col gap-4 border-t border-border-subtle px-4 py-4">
      <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="flex flex-col gap-0.5">
          <dt className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
            Название
          </dt>
          <dd className="break-words text-sm text-text-primary">{team.name}</dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
            Лидер
          </dt>
          <dd className="break-words text-sm text-text-primary">
            {team.leader_username ? (
              <span className="font-mono">{team.leader_username}</span>
            ) : (
              <span className="text-text-secondary">Без лидера</span>
            )}
          </dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
            Участники
          </dt>
          <dd className="text-sm text-text-primary">
            {team.members.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {team.members.map((m) => (
                  <span
                    key={m.id}
                    className="rounded-chip bg-surface-3 px-2 py-0.5 font-mono text-[12px] text-text-secondary"
                  >
                    {m.username}
                  </span>
                ))}
              </div>
            ) : (
              <span className="text-text-secondary">Участников нет</span>
            )}
          </dd>
        </div>
      </dl>

      <div className="flex flex-col gap-2">
        <CollapsibleSection
          title="Номера команды"
          id={`${id}-numbers`}
          open={numbersOpen}
          onToggle={() => setNumbersOpen((v) => !v)}
        >
          <SectionStatus
            loading={numbersQuery.isLoading}
            error={numbersQuery.isError}
            fetching={numbersQuery.isFetching}
            onRetry={() => void numbersQuery.refetch()}
          />
          {!numbersQuery.isLoading && !numbersQuery.isError && numbers.length === 0 && (
            <p className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5 text-[13px] text-text-secondary">
              Номеров нет
            </p>
          )}
          {!numbersQuery.isLoading && !numbersQuery.isError && numbers.length > 0 && (
            <div className="flex flex-col gap-2">
              {numbers.map((n) => (
                <NumberRow key={n.id} number={n} />
              ))}
            </div>
          )}
        </CollapsibleSection>

        <CollapsibleSection
          title="Почты команды"
          id={`${id}-mailboxes`}
          open={mailboxesOpen}
          onToggle={() => setMailboxesOpen((v) => !v)}
        >
          <SectionStatus
            loading={mailboxesQuery.isLoading}
            error={mailboxesQuery.isError}
            fetching={mailboxesQuery.isFetching}
            onRetry={() => void mailboxesQuery.refetch()}
          />
          {!mailboxesQuery.isLoading && !mailboxesQuery.isError && mailboxes.length === 0 && (
            <p className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5 text-[13px] text-text-secondary">
              {mailboxesEmptyText}
            </p>
          )}
          {!mailboxesQuery.isLoading && !mailboxesQuery.isError && mailboxes.length > 0 && (
            <div className="flex flex-col gap-2">
              {mailboxes.map((mb) => (
                <MailboxRow key={mb.id} mailbox={mb} />
              ))}
            </div>
          )}
        </CollapsibleSection>
      </div>
    </div>
  );
}

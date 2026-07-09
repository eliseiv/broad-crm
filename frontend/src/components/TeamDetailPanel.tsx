import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { Pill } from '@/components/ui/Pill';
import { Spinner } from '@/components/ui/Spinner';
import { useTeamNumbers } from '@/features/sms/hooks';
import type { TeamNumberItem, TeamListItem } from '@/types/api';

/** `-` для пустых значений пилюль (строка не «прыгает»), 08-design-system.md. */
function orDash(value: string | null | undefined): string {
  const trimmed = value?.trim();
  return trimmed ? trimmed : '-';
}

/**
 * Строка номера команды в detail-панели (08-design-system.md §detail-панель, ADR-034):
 * Номер + пилюли Логин / Приложение (схема `TeamNumberItem` += `login`/`app_name`; `note`/
 * `label` по-прежнему не показываются). Номер не разрывается посреди цифр (`whitespace-nowrap`);
 * пустые значения пилюль → `-`. Длинный список скроллит панель.
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

interface TeamDetailPanelProps {
  team: TeamListItem;
  /** id для aria-controls аккордеона (связь с кликабельной шапкой карточки). */
  id: string;
}

/**
 * Detail-панель команды (аккордеон на /teams, 08-design-system.md «Доработка /teams»,
 * ADR-030): Название / Лидер / Участники (из `team.members`) + ленивый список номеров
 * команды (GET /api/teams/{id}/numbers, свои loading/empty/error). Только просмотр —
 * редактирование состава через модалку (карандаш в шапке карточки).
 */
export function TeamDetailPanel({ team, id }: TeamDetailPanelProps) {
  const query = useTeamNumbers(team.id, true);
  const numbers = query.data?.numbers ?? [];

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
        <p className="text-[12px] font-medium uppercase tracking-wide text-text-tertiary">
          Номера команды
        </p>

        {query.isLoading && (
          <div className="flex items-center gap-2 py-2 text-[13px] text-text-secondary">
            <Spinner className="text-text-secondary" />
            Загрузка…
          </div>
        )}

        {!query.isLoading && query.isError && (
          <div className="flex flex-wrap items-center gap-3 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
            <AlertTriangle className="h-5 w-5 text-status-red" aria-hidden="true" />
            <span className="text-[13px] text-text-secondary">Не удалось загрузить</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void query.refetch()}
              loading={query.isFetching}
            >
              <RefreshCw className="h-4 w-4" />
              Повторить
            </Button>
          </div>
        )}

        {!query.isLoading && !query.isError && numbers.length === 0 && (
          <p className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5 text-[13px] text-text-secondary">
            Номеров нет
          </p>
        )}

        {!query.isLoading && !query.isError && numbers.length > 0 && (
          <div className="flex flex-col gap-2">
            {numbers.map((n) => (
              <NumberRow key={n.id} number={n} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

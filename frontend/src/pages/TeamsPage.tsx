import { useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, UsersRound } from 'lucide-react';
import { AddTeamModal } from '@/components/AddTeamModal';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { membersPlural } from '@/lib/plural';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { useTeams } from '@/features/teams/hooks';
import { useUsers } from '@/features/users/hooks';
import type { TeamListItem } from '@/types/api';

/**
 * Страница «Команды» (08-design-system.md «Страница Команды», ADR-022). CRM-команды
 * (лидер + участники). Page-level view-guard `teams:view`; кнопки create/edit/delete —
 * по `useCan('teams', action)`. CRM-команды ≠ mail-«команды».
 */
export function TeamsPage() {
  const canView = useCanViewPage('teams');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <TeamsList />;
}

function TeamsList() {
  const teamsQuery = useTeams();
  // Пользователи нужны форме (Лидер/Участники, 08-design-system.md «Страница Команды»).
  const usersQuery = useUsers();

  const canCreate = useCan('teams', 'create');
  const canEdit = useCan('teams', 'edit');
  const canDelete = useCan('teams', 'delete');

  const [modalOpen, setModalOpen] = useState(false);
  const [editTeam, setEditTeam] = useState<TeamListItem | undefined>(undefined);

  const teams = teamsQuery.data?.items ?? [];
  const users = usersQuery.data?.items ?? [];

  const openAdd = () => {
    setEditTeam(undefined);
    setModalOpen(true);
  };
  const openEdit = (team: TeamListItem) => {
    if (!canEdit) return;
    setEditTeam(team);
    setModalOpen(true);
  };

  const forbidden = teamsQuery.error instanceof ApiError && teamsQuery.error.status === 403;

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Команды</h1>
        <p className="mt-1 text-[13px] text-text-secondary">CRM-команды: лидер и участники.</p>
      </div>

      <div className="mb-4 flex items-center justify-end">
        {canCreate && (
          <Button size="sm" onClick={openAdd} disabled={usersQuery.isLoading}>
            <Plus className="h-4 w-4" />
            Добавить команду
          </Button>
        )}
      </div>

      {teamsQuery.isLoading && (
        <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Загрузка…
        </div>
      )}

      {!teamsQuery.isLoading && teamsQuery.isError && forbidden && <InsufficientPermissions />}

      {!teamsQuery.isLoading && teamsQuery.isError && !forbidden && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
          <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить команды
            </p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение с сервером и попробуйте снова.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => void teamsQuery.refetch()}
            loading={teamsQuery.isFetching}
          >
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {!teamsQuery.isLoading && !teamsQuery.isError && teams.length === 0 && canCreate && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <UsersRound className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-text-primary">Пока нет команд</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Создайте первую команду: назначьте лидера и участников.
            </p>
          </div>
        </div>
      )}

      {!teamsQuery.isLoading && !teamsQuery.isError && teams.length === 0 && !canCreate && (
        <div className="rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Список команд пуст</p>
        </div>
      )}

      {!teamsQuery.isLoading && !teamsQuery.isError && teams.length > 0 && (
        <ul className="flex flex-col gap-3">
          {teams.map((team) => {
            const interactiveProps = canEdit
              ? {
                  interactive: true,
                  role: 'button' as const,
                  tabIndex: 0,
                  'aria-label': `Изменить команду ${team.name}`,
                  onClick: () => openEdit(team),
                  onKeyDown: (e: React.KeyboardEvent) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      openEdit(team);
                    }
                  },
                }
              : {};
            return (
              <li key={team.id}>
                <Card
                  {...interactiveProps}
                  className={
                    canEdit
                      ? 'flex cursor-pointer flex-wrap items-center justify-between gap-3 p-4'
                      : 'flex flex-wrap items-center justify-between gap-3 p-4'
                  }
                >
                  <div className="flex min-w-0 items-center gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
                      <UsersRound className="h-5 w-5" aria-hidden="true" />
                    </span>
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="truncate text-sm font-medium text-text-primary">
                        {team.name}
                      </span>
                      <span className="truncate text-[13px] text-text-secondary">
                        {team.leader_username ? (
                          <>
                            Лидер: <span className="font-mono">{team.leader_username}</span>
                          </>
                        ) : (
                          'Без лидера'
                        )}
                      </span>
                    </div>
                  </div>
                  <span className="shrink-0 whitespace-nowrap font-mono text-[13px] text-text-secondary">
                    {membersPlural(team.member_count)}
                  </span>
                </Card>
              </li>
            );
          })}
        </ul>
      )}

      <AddTeamModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        users={users}
        mode={editTeam ? 'edit' : 'add'}
        team={editTeam}
        canDelete={canDelete}
      />
    </>
  );
}

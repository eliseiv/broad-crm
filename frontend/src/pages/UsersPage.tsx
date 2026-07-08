import { useMemo, useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, User as UserIcon } from 'lucide-react';
import { AddUserModal } from '@/components/AddUserModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { useTeams } from '@/features/teams/hooks';
import { useRoles, useUsers } from '@/features/users/hooks';
import type { UserListItem } from '@/types/api';

/** Секция списка: команда (или бакет «Без команды») + её пользователи. */
interface UserGroup {
  key: string;
  title: string;
  users: UserListItem[];
}

const NO_TEAM_KEY = '__no_team__';

/**
 * Группирует пользователей по CRM-командам (08-design-system.md «Список
 * пользователей»): пользователь в нескольких командах попадает в каждую группу;
 * пользователи без команды — в бакет «Без команды» в конце. Команды сортируются
 * по названию для стабильности.
 */
function groupByTeams(users: UserListItem[]): UserGroup[] {
  const teams = new Map<string, UserGroup>();
  const noTeam: UserListItem[] = [];

  for (const user of users) {
    if (user.teams.length === 0) {
      noTeam.push(user);
      continue;
    }
    for (const team of user.teams) {
      const group = teams.get(team.id);
      if (group) group.users.push(user);
      else teams.set(team.id, { key: team.id, title: team.name, users: [user] });
    }
  }

  const result = Array.from(teams.values()).sort((a, b) => a.title.localeCompare(b.title, 'ru'));
  if (noTeam.length > 0) {
    result.push({ key: NO_TEAM_KEY, title: 'Без команды', users: noTeam });
  }
  return result;
}

/**
 * Страница «Пользователи» (08-design-system.md «Страница Пользователи», ADR-022).
 * Admin-only (гейтинг — AdminRoute). Содержит ТОЛЬКО пользователей, сгруппированных
 * по CRM-командам. Роли — на странице «Роли», команды — на странице «Команды».
 */
export function UsersPage() {
  const usersQuery = useUsers();
  const rolesQuery = useRoles();
  const teamsQuery = useTeams();

  const [modalOpen, setModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserListItem | undefined>(undefined);

  const roles = rolesQuery.data?.items ?? [];
  const teams = teamsQuery.data?.items ?? [];
  const usersData = usersQuery.data?.items;
  const users = useMemo(() => usersData ?? [], [usersData]);
  const groups = useMemo(() => groupByTeams(users), [users]);

  const openAdd = () => {
    setEditUser(undefined);
    setModalOpen(true);
  };
  const openEdit = (user: UserListItem) => {
    setEditUser(user);
    setModalOpen(true);
  };

  return (
    <>
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-text-primary">Пользователи</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          Учётные записи, сгруппированные по командам.
        </p>
      </div>

      <div className="mb-4 flex items-center justify-end">
        <Button size="sm" onClick={openAdd} disabled={rolesQuery.isLoading}>
          <Plus className="h-4 w-4" />
          Добавить пользователя
        </Button>
      </div>

      {usersQuery.isLoading && (
        <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Загрузка…
        </div>
      )}

      {usersQuery.isError && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
          <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">
              Не удалось загрузить пользователей
            </p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение с сервером и попробуйте снова.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => void usersQuery.refetch()}
            loading={usersQuery.isFetching}
          >
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {!usersQuery.isLoading && !usersQuery.isError && users.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <UserIcon className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-text-primary">Пока нет пользователей</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Добавьте первого пользователя и назначьте ему роль.
            </p>
          </div>
        </div>
      )}

      {!usersQuery.isLoading && !usersQuery.isError && users.length > 0 && (
        <div className="flex flex-col gap-8">
          {groups.map((group) => (
            <section key={group.key}>
              <h2 className="mb-3 border-b border-border-subtle pb-2 text-base font-semibold text-text-secondary">
                {group.title}
              </h2>
              <ul className="flex flex-col gap-3">
                {group.users.map((user) => (
                  <li key={user.id}>
                    <Card
                      interactive
                      role="button"
                      tabIndex={0}
                      aria-label={`Изменить пользователя ${user.username}`}
                      onClick={() => openEdit(user)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          openEdit(user);
                        }
                      }}
                      className="flex cursor-pointer flex-wrap items-center justify-between gap-3 p-4"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
                          <UserIcon className="h-5 w-5" aria-hidden="true" />
                        </span>
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <span className="truncate font-mono text-sm font-medium text-text-primary">
                            {user.username}
                          </span>
                          {user.telegram && (
                            <span className="truncate font-mono text-[13px] text-text-secondary">
                              @{user.telegram}
                            </span>
                          )}
                          <span className="truncate text-[13px] text-text-secondary">
                            {user.role_name}
                          </span>
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        {/* Беспарольный пользователь ещё не завершил «открытый первый
                            вход» (ADR-025 §5) — единственный визуальный признак учётки. */}
                        {!user.has_password && <Badge tone="yellow">Без пароля</Badge>}
                        {user.is_active ? (
                          <Badge tone="green">Активен</Badge>
                        ) : (
                          <Badge tone="neutral">Неактивен</Badge>
                        )}
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      <AddUserModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        roles={roles}
        teams={teams}
        mode={editUser ? 'edit' : 'add'}
        user={editUser}
      />
    </>
  );
}

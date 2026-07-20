import { useMemo, useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, User as UserIcon } from 'lucide-react';
import { AddUserModal } from '@/components/AddUserModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Pill } from '@/components/ui/Pill';
import { Spinner } from '@/components/ui/Spinner';
import { useTeams } from '@/features/teams/hooks';
import { useRoles, useUsers } from '@/features/users/hooks';
import type { UserListItem } from '@/types/api';

/**
 * Бейдж производного тристатуса пользователя (ADR-028, 08-design-system.md
 * «Страница Пользователи»): «Активен» (green) — только после первого входа;
 * «Ожидает входа» (yellow) — заведён, но ещё не входил; «Неактивен» (neutral).
 */
function StatusBadge({ status }: { status: UserListItem['status'] }) {
  if (status === 'active') return <Badge tone="green">Активен</Badge>;
  if (status === 'pending') return <Badge tone="yellow">Ожидает входа</Badge>;
  return <Badge tone="neutral">Неактивен</Badge>;
}

/**
 * Страница «Пользователи» (08-design-system.md «Страница Пользователи», ADR-065).
 * Admin-only (гейтинг — AdminRoute). Плоский список пользователей БЕЗ группировки
 * по командам и без дублирования (ADR-065 отменяет прежнюю группировку ADR-021/022):
 * одна строка = один пользователь, сортировка по `username`; принадлежность к командам
 * показана чипами в строке. Роли — на странице «Роли», команды — на странице «Команды».
 */
export function UsersPage() {
  const usersQuery = useUsers();
  const rolesQuery = useRoles();
  // useTeams нужен модалке AddUserModal (мультивыбор команд), а не раскладке
  // списка — убирать нельзя (ADR-065 §5).
  const teamsQuery = useTeams();

  const [modalOpen, setModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserListItem | undefined>(undefined);

  const roles = rolesQuery.data?.items ?? [];
  const teams = teamsQuery.data?.items ?? [];
  const usersData = usersQuery.data?.items;
  // Плоский список, отсортированный по username (ADR-065 §3: localeCompare, локаль 'ru').
  const users = useMemo(
    () => [...(usersData ?? [])].sort((a, b) => a.username.localeCompare(b.username, 'ru')),
    [usersData],
  );

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
        <ul className="flex flex-col gap-3">
          {users.map((user) => (
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
                  <div className="flex min-w-0 flex-col gap-1">
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
                    {/* Принадлежность к командам — чипами (ADR-065 §2). Пустой массив →
                        единичная подпись «Без команды» вторичным цветом. */}
                    {user.teams.length > 0 ? (
                      <div className="flex flex-wrap gap-1">
                        {user.teams.map((team) => (
                          <Pill key={team.id} tone="neutral" label={team.name} title={team.name} />
                        ))}
                      </div>
                    ) : (
                      <span className="text-[13px] text-text-secondary">Без команды</span>
                    )}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  {/* Беспарольный пользователь ещё не завершил «открытый первый
                      вход» (ADR-025 §5) — единственный визуальный признак учётки. */}
                  {!user.has_password && <Badge tone="yellow">Без пароля</Badge>}
                  <StatusBadge status={user.status} />
                </div>
              </Card>
            </li>
          ))}
        </ul>
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

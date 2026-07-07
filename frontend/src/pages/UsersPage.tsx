import { useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, ShieldCheck, User as UserIcon } from 'lucide-react';
import { AddUserModal } from '@/components/AddUserModal';
import { RoleEditorModal } from '@/components/RoleEditorModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { pageLabel } from '@/features/users/labels';
import { usePermissionsCatalog, useRoles, useUsers } from '@/features/users/hooks';
import type { RoleListItem, UserListItem } from '@/types/api';

/** Краткая сводка прав роли для списка: перечень разделов (или «Нет прав»). */
function permissionsSummary(role: RoleListItem): string {
  const pages = Object.keys(role.permissions).filter(
    (page) => (role.permissions[page]?.length ?? 0) > 0,
  );
  if (pages.length === 0) return 'Нет прав';
  return pages.map(pageLabel).join(', ');
}

export function UsersPage() {
  const usersQuery = useUsers();
  const rolesQuery = useRoles();
  const catalogQuery = usePermissionsCatalog();

  const [userModalOpen, setUserModalOpen] = useState(false);
  const [editUser, setEditUser] = useState<UserListItem | undefined>(undefined);
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [editRole, setEditRole] = useState<RoleListItem | undefined>(undefined);

  const roles = rolesQuery.data?.items ?? [];
  const users = usersQuery.data?.items ?? [];
  const catalog = catalogQuery.data?.pages ?? [];

  const openAddUser = () => {
    setEditUser(undefined);
    setUserModalOpen(true);
  };
  const openEditUser = (user: UserListItem) => {
    setEditUser(user);
    setUserModalOpen(true);
  };
  const openAddRole = () => {
    setEditRole(undefined);
    setRoleModalOpen(true);
  };
  const openEditRole = (role: RoleListItem) => {
    setEditRole(role);
    setRoleModalOpen(true);
  };

  return (
    <>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-text-primary">Пользователи</h1>
        <p className="mt-1 text-[13px] text-text-secondary">
          Управление пользователями и ролями (доступ к разделам).
        </p>
      </div>

      {/* --- Секция «Пользователи» --- */}
      <section className="mb-10">
        <div className="mb-4 flex items-end justify-between gap-4 border-b border-border-subtle pb-2">
          <h2 className="text-base font-semibold text-text-secondary">Пользователи</h2>
          <Button size="sm" onClick={openAddUser} disabled={rolesQuery.isLoading}>
            <Plus className="h-4 w-4" />
            Добавить пользователя
          </Button>
        </div>

        {usersQuery.isLoading && <LoadingBlock />}

        {usersQuery.isError && (
          <ErrorBlock
            title="Не удалось загрузить пользователей"
            onRetry={() => void usersQuery.refetch()}
            loading={usersQuery.isFetching}
          />
        )}

        {!usersQuery.isLoading && !usersQuery.isError && users.length === 0 && (
          <EmptyBlock
            icon={<UserIcon className="h-8 w-8 text-text-tertiary" aria-hidden="true" />}
            title="Пока нет пользователей"
            hint="Добавьте первого пользователя и назначьте ему роль."
          />
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
                  onClick={() => openEditUser(user)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      openEditUser(user);
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
                      <span className="truncate text-[13px] text-text-secondary">
                        {user.role_name}
                      </span>
                    </div>
                  </div>
                  {user.is_active ? (
                    <Badge tone="green">Активен</Badge>
                  ) : (
                    <Badge tone="neutral">Неактивен</Badge>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* --- Секция «Роли» --- */}
      <section>
        <div className="mb-4 flex items-end justify-between gap-4 border-b border-border-subtle pb-2">
          <h2 className="text-base font-semibold text-text-secondary">Роли</h2>
          <Button size="sm" onClick={openAddRole} disabled={catalogQuery.isLoading}>
            <Plus className="h-4 w-4" />
            Добавить роль
          </Button>
        </div>

        {(rolesQuery.isLoading || catalogQuery.isLoading) && <LoadingBlock />}

        {(rolesQuery.isError || catalogQuery.isError) && (
          <ErrorBlock
            title="Не удалось загрузить роли"
            onRetry={() => {
              void rolesQuery.refetch();
              void catalogQuery.refetch();
            }}
            loading={rolesQuery.isFetching || catalogQuery.isFetching}
          />
        )}

        {!rolesQuery.isLoading &&
          !rolesQuery.isError &&
          !catalogQuery.isLoading &&
          !catalogQuery.isError &&
          roles.length === 0 && (
            <EmptyBlock
              icon={<ShieldCheck className="h-8 w-8 text-text-tertiary" aria-hidden="true" />}
              title="Пока нет ролей"
              hint="Создайте роль и настройте матрицу прав по разделам."
            />
          )}

        {!rolesQuery.isLoading &&
          !rolesQuery.isError &&
          !catalogQuery.isLoading &&
          !catalogQuery.isError &&
          roles.length > 0 && (
            <ul className="flex flex-col gap-3">
              {roles.map((role) => (
                <li key={role.id}>
                  <Card
                    interactive
                    role="button"
                    tabIndex={0}
                    aria-label={`Изменить роль ${role.name}`}
                    onClick={() => openEditRole(role)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        openEditRole(role);
                      }
                    }}
                    className="flex cursor-pointer flex-wrap items-center justify-between gap-3 p-4"
                  >
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
                        <ShieldCheck className="h-5 w-5" aria-hidden="true" />
                      </span>
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <span className="truncate text-sm font-medium text-text-primary">
                          {role.name}
                        </span>
                        <span className="truncate text-[13px] text-text-secondary">
                          {permissionsSummary(role)}
                        </span>
                      </div>
                    </div>
                  </Card>
                </li>
              ))}
            </ul>
          )}
      </section>

      <AddUserModal
        open={userModalOpen}
        onOpenChange={setUserModalOpen}
        roles={roles}
        mode={editUser ? 'edit' : 'add'}
        user={editUser}
      />
      <RoleEditorModal
        open={roleModalOpen}
        onOpenChange={setRoleModalOpen}
        catalog={catalog}
        mode={editRole ? 'edit' : 'add'}
        role={editRole}
      />
    </>
  );
}

function LoadingBlock() {
  return (
    <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
      <Spinner className="text-text-secondary" />
      Загрузка…
    </div>
  );
}

function ErrorBlock({
  title,
  onRetry,
  loading,
}: {
  title: string;
  onRetry: () => void;
  loading: boolean;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
      <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
      <div>
        <p className="text-base font-semibold text-text-primary">{title}</p>
        <p className="mt-1 text-[13px] text-text-secondary">
          Проверьте соединение с сервером и попробуйте снова.
        </p>
      </div>
      <Button variant="outline" onClick={onRetry} loading={loading}>
        <RefreshCw className="h-4 w-4" />
        Повторить
      </Button>
    </div>
  );
}

function EmptyBlock({ icon, title, hint }: { icon: React.ReactNode; title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
      {icon}
      <div>
        <p className="text-sm font-medium text-text-primary">{title}</p>
        <p className="mt-1 text-[13px] text-text-secondary">{hint}</p>
      </div>
    </div>
  );
}

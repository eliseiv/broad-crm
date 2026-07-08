import { useState } from 'react';
import { AlertTriangle, Plus, RefreshCw, ShieldCheck } from 'lucide-react';
import { InsufficientPermissions } from '@/components/InsufficientPermissions';
import { RoleEditorModal } from '@/components/RoleEditorModal';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { usersPlural } from '@/lib/plural';
import { useCan, useCanViewPage } from '@/features/auth/hooks';
import { pageLabel } from '@/features/users/labels';
import { usePermissionsCatalog, useRoles } from '@/features/users/hooks';
import type { RoleListItem } from '@/types/api';

/** Краткая сводка прав роли для списка: перечень разделов (или «Нет прав»). */
function permissionsSummary(role: RoleListItem): string {
  const pages = Object.keys(role.permissions).filter(
    (page) => (role.permissions[page]?.length ?? 0) > 0,
  );
  if (pages.length === 0) return 'Нет прав';
  return pages.map(pageLabel).join(', ');
}

/**
 * Страница «Роли» (08-design-system.md «Страница Роли», ADR-022). Список ролей с
 * числом носителей (`user_count`) + матрица прав в редакторе. Page-level view-guard
 * `roles:view`; кнопки create/edit/delete — по `useCan('roles', action)`.
 */
export function RolesPage() {
  const canView = useCanViewPage('roles');
  if (!canView) {
    return <InsufficientPermissions />;
  }
  return <RolesList />;
}

function RolesList() {
  const rolesQuery = useRoles();
  const catalogQuery = usePermissionsCatalog();

  const canCreate = useCan('roles', 'create');
  const canEdit = useCan('roles', 'edit');
  const canDelete = useCan('roles', 'delete');

  const [modalOpen, setModalOpen] = useState(false);
  const [editRole, setEditRole] = useState<RoleListItem | undefined>(undefined);

  const roles = rolesQuery.data?.items ?? [];
  const catalog = catalogQuery.data?.pages ?? [];

  const openAdd = () => {
    setEditRole(undefined);
    setModalOpen(true);
  };
  const openEdit = (role: RoleListItem) => {
    if (!canEdit) return;
    setEditRole(role);
    setModalOpen(true);
  };

  const isLoading = rolesQuery.isLoading || catalogQuery.isLoading;
  const isError = rolesQuery.isError || catalogQuery.isError;
  const forbidden =
    (rolesQuery.error instanceof ApiError && rolesQuery.error.status === 403) ||
    (catalogQuery.error instanceof ApiError && catalogQuery.error.status === 403);

  return (
    <>
      <div className="mb-4 flex items-center justify-end">
        {canCreate && (
          <Button size="sm" onClick={openAdd} disabled={catalogQuery.isLoading}>
            <Plus className="h-4 w-4" />
            Добавить роль
          </Button>
        )}
      </div>

      {isLoading && (
        <div className="flex items-center justify-center gap-3 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-[13px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Загрузка…
        </div>
      )}

      {!isLoading && isError && forbidden && <InsufficientPermissions />}

      {!isLoading && isError && !forbidden && (
        <div className="flex flex-col items-center justify-center gap-4 rounded-card border border-border-subtle bg-surface-1 px-6 py-12 text-center">
          <AlertTriangle className="h-9 w-9 text-status-red" aria-hidden="true" />
          <div>
            <p className="text-base font-semibold text-text-primary">Не удалось загрузить роли</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Проверьте соединение с сервером и попробуйте снова.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={() => {
              void rolesQuery.refetch();
              void catalogQuery.refetch();
            }}
            loading={rolesQuery.isFetching || catalogQuery.isFetching}
          >
            <RefreshCw className="h-4 w-4" />
            Повторить
          </Button>
        </div>
      )}

      {!isLoading && !isError && roles.length === 0 && canCreate && (
        <div className="flex flex-col items-center justify-center gap-3 rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <ShieldCheck className="h-8 w-8 text-text-tertiary" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-text-primary">Пока нет ролей</p>
            <p className="mt-1 text-[13px] text-text-secondary">
              Создайте роль и настройте матрицу прав по разделам.
            </p>
          </div>
        </div>
      )}

      {!isLoading && !isError && roles.length === 0 && !canCreate && (
        <div className="rounded-card border border-dashed border-border-strong bg-surface-1/40 px-6 py-12 text-center">
          <p className="text-sm font-medium text-text-primary">Список ролей пуст</p>
        </div>
      )}

      {!isLoading && !isError && roles.length > 0 && (
        <ul className="flex flex-col gap-3">
          {roles.map((role) => {
            const interactiveProps = canEdit
              ? {
                  interactive: true,
                  role: 'button' as const,
                  tabIndex: 0,
                  'aria-label': `Изменить роль ${role.name}`,
                  onClick: () => openEdit(role),
                  onKeyDown: (e: React.KeyboardEvent) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      openEdit(role);
                    }
                  },
                }
              : {};
            return (
              <li key={role.id}>
                <Card {...interactiveProps} className={cnCard(canEdit)}>
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
                  <span className="shrink-0 whitespace-nowrap font-mono text-[13px] text-text-secondary">
                    {usersPlural(role.user_count)}
                  </span>
                </Card>
              </li>
            );
          })}
        </ul>
      )}

      <RoleEditorModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        catalog={catalog}
        mode={editRole ? 'edit' : 'add'}
        role={editRole}
        canDelete={canDelete}
      />
    </>
  );
}

function cnCard(interactive: boolean): string {
  return interactive
    ? 'flex cursor-pointer flex-wrap items-center justify-between gap-3 p-4'
    : 'flex flex-wrap items-center justify-between gap-3 p-4';
}

import { useState } from 'react';
import { Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { ACTION_LABEL, ACTION_ORDER, pageLabel } from '@/features/users/labels';
import { useCreateRole, useDeleteRole, useUpdateRole } from '@/features/users/hooks';
import type {
  PermissionCatalogPage,
  PermissionsMap,
  RoleCreateRequest,
  RoleListItem,
  RoleUpdateRequest,
} from '@/types/api';

interface RoleEditorModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Каталог прав (GET /api/permissions/catalog) — строки/столбцы матрицы. */
  catalog: PermissionCatalogPage[];
  /** 'add' — создание (по умолчанию); 'edit' — редактирование роли. */
  mode?: 'add' | 'edit';
  /** Обязателен в режиме edit — источник префила и id для PATCH/DELETE. */
  role?: RoleListItem;
}

/** Проверяет, выбрано ли действие для страницы в текущем состоянии матрицы. */
function isChecked(perm: PermissionsMap, page: string, action: string): boolean {
  return Boolean(perm[page]?.includes(action));
}

/** Переключает ячейку матрицы (страница×действие), возвращая новую матрицу. */
function toggle(perm: PermissionsMap, page: string, action: string): PermissionsMap {
  const current = new Set(perm[page] ?? []);
  if (current.has(action)) current.delete(action);
  else current.add(action);
  return { ...perm, [page]: Array.from(current) };
}

/**
 * Собирает payload permissions: только действия из каталога, только страницы
 * с ≥1 выбранным действием (порядок действий — как в каталоге). 04-api.md:
 * ключи ∈ страниц каталога, действия ∈ CATALOG[page], без дублей.
 */
function buildPermissions(perm: PermissionsMap, catalog: PermissionCatalogPage[]): PermissionsMap {
  const result: PermissionsMap = {};
  for (const { page, actions } of catalog) {
    const selected = actions.filter((a) => perm[page]?.includes(a));
    if (selected.length > 0) result[page] = selected;
  }
  return result;
}

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

export function RoleEditorModal({
  open,
  onOpenChange,
  catalog,
  mode = 'add',
  role,
}: RoleEditorModalProps) {
  const key = `${mode}-${role?.id ?? 'new'}-${open ? 'open' : 'closed'}`;
  return (
    <RoleDialog
      key={key}
      open={open}
      onOpenChange={onOpenChange}
      catalog={catalog}
      mode={mode}
      role={role}
    />
  );
}

function RoleDialog({ open, onOpenChange, catalog, mode, role }: RoleEditorModalProps) {
  const isEdit = mode === 'edit' && role !== undefined;
  const [name, setName] = useState(role?.name ?? '');
  const [perm, setPerm] = useState<PermissionsMap>(role?.permissions ?? {});
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const createMutation = useCreateRole();
  const updateMutation = useUpdateRole(role?.id ?? '');
  const deleteMutation = useDeleteRole();

  const handleError = (err: unknown) => {
    if (err instanceof ApiError) {
      if (err.status === 409) {
        setNameError('Роль с таким названием уже существует');
        return;
      }
      if (err.status === 422 || err.status === 400) {
        const nameDetail = err.details?.find((d) => d.field === 'name');
        if (nameDetail) setNameError(nameDetail.message);
        else toast.error('Проверьте название и матрицу прав');
        return;
      }
      toast.error(err.message);
      return;
    }
    toast.error('Не удалось сохранить роль');
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const nErr = validateName(name);
    setNameError(nErr);
    if (nErr) return;

    const permissions = buildPermissions(perm, catalog);

    if (isEdit && role) {
      const payload: RoleUpdateRequest = { name: name.trim(), permissions };
      updateMutation.mutate(payload, {
        onSuccess: () => {
          toast.success('Роль обновлена');
          onOpenChange(false);
        },
        onError: handleError,
      });
    } else {
      const payload: RoleCreateRequest = { name: name.trim(), permissions };
      createMutation.mutate(payload, {
        onSuccess: () => {
          toast.success('Роль создана');
          onOpenChange(false);
        },
        onError: handleError,
      });
    }
  };

  const handleDelete = () => {
    if (!role) return;
    deleteMutation.mutate(role.id, {
      onSuccess: () => {
        toast.success('Роль удалена');
        setConfirmOpen(false);
        onOpenChange(false);
      },
      onError: (err) => {
        // 04-api.md: 409 role_in_use.
        if (err instanceof ApiError && err.status === 409) {
          toast.error('Роль назначена пользователям — удаление невозможно');
          setConfirmOpen(false);
          return;
        }
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить роль';
        toast.error(message);
      },
    });
  };

  const isSubmitting = createMutation.isPending || updateMutation.isPending;
  const formId = 'role-editor-form';

  return (
    <>
      <Modal
        open={open}
        onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
        title={isEdit ? 'Изменить роль' : 'Добавить роль'}
        dismissible={!isSubmitting}
        size="lg"
        footer={
          <div className="flex w-full items-center justify-between gap-2">
            {isEdit ? (
              <Button variant="danger" onClick={() => setConfirmOpen(true)} disabled={isSubmitting}>
                <Trash2 className="h-4 w-4" />
                Удалить
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                Отмена
              </Button>
              <Button type="submit" form={formId} loading={isSubmitting}>
                {isEdit ? 'Сохранить' : 'Добавить'}
              </Button>
            </div>
          </div>
        }
      >
        <form id={formId} onSubmit={handleSubmit} className="flex flex-col gap-5" noValidate>
          <Input
            label="Название"
            placeholder="Оператор"
            value={name}
            error={nameError}
            autoFocus
            maxLength={64}
            autoComplete="off"
            onChange={(e) => {
              setName(e.target.value);
              if (nameError) setNameError(undefined);
            }}
          />

          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-medium text-text-secondary">Права</span>
            <div className="overflow-x-auto rounded-sub border border-border-subtle">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-border-subtle bg-surface-2">
                    <th className="px-3 py-2 text-left font-medium text-text-secondary">
                      Страница
                    </th>
                    {ACTION_ORDER.map((action) => (
                      <th
                        key={action}
                        className="px-3 py-2 text-center font-medium text-text-secondary"
                      >
                        {ACTION_LABEL[action]}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {catalog.map(({ page, actions }) => (
                    <tr key={page} className="border-b border-border-subtle last:border-b-0">
                      <td className="whitespace-nowrap px-3 py-2 text-text-primary">
                        {pageLabel(page)}
                      </td>
                      {ACTION_ORDER.map((action) => (
                        <td key={action} className="px-3 py-2 text-center">
                          {actions.includes(action) ? (
                            <span className="inline-flex justify-center">
                              <Checkbox
                                aria-label={`${pageLabel(page)} — ${ACTION_LABEL[action]}`}
                                checked={isChecked(perm, page, action)}
                                onChange={() => setPerm((prev) => toggle(prev, page, action))}
                              />
                            </span>
                          ) : (
                            <span className="text-text-tertiary" aria-hidden="true">
                              —
                            </span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </form>
      </Modal>

      {isEdit && role && (
        <Modal
          open={confirmOpen}
          onOpenChange={(next) => !deleteMutation.isPending && setConfirmOpen(next)}
          title="Удалить роль?"
          description={`Роль «${role.name}» будет удалена. Действие необратимо.`}
          dismissible={!deleteMutation.isPending}
          footer={
            <>
              <Button
                variant="ghost"
                onClick={() => setConfirmOpen(false)}
                disabled={deleteMutation.isPending}
              >
                Отмена
              </Button>
              <Button variant="danger" loading={deleteMutation.isPending} onClick={handleDelete}>
                Удалить
              </Button>
            </>
          }
        >
          <p className="text-sm text-text-secondary">
            Удаление невозможно, если роль назначена хотя бы одному пользователю.
          </p>
        </Modal>
      )}
    </>
  );
}

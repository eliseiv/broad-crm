import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { MultiSelect } from '@/components/ui/MultiSelect';
import { Spinner } from '@/components/ui/Spinner';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { useNodeVisibility, useRoleRefs, useSetVisibility } from '@/features/documents/hooks';
import type { DocumentNode, DocumentVisibilityMode } from '@/types/api';

interface DocumentVisibilityModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Узел, чью видимость меняем (нужен `id` и `name`). */
  node: DocumentNode | null;
}

/**
 * Модалка «Сменить видимость» (08-design-system.md «Модалка видимости», ADR-059).
 * Предзаполнение — GET /nodes/{id}/visibility (собственные роли узла; `inherit` → []).
 * Опции ролей — GET /role-refs (НЕ admin-gated /api/roles). Сохранение — PATCH
 * /nodes/{id}/visibility (симметрично read-контракту). Оба GET и PATCH — гейт documents:share.
 */
export function DocumentVisibilityModal({
  open,
  onOpenChange,
  node,
}: DocumentVisibilityModalProps) {
  const nodeId = node?.id ?? null;
  const visibilityQuery = useNodeVisibility(nodeId, open);
  const roleRefsQuery = useRoleRefs(open);
  const setVisibilityMutation = useSetVisibility();

  const [mode, setMode] = useState<DocumentVisibilityMode>('inherit');
  const [roleIds, setRoleIds] = useState<string[]>([]);

  // Префилл при получении собственных настроек узла (read↔write симметрия).
  useEffect(() => {
    if (visibilityQuery.data) {
      setMode(visibilityQuery.data.visibility_mode);
      setRoleIds(visibilityQuery.data.role_ids);
    }
  }, [visibilityQuery.data]);

  if (!node) return null;

  const roleOptions = (roleRefsQuery.data ?? []).map((r) => ({ value: r.id, label: r.name }));
  const isLoading = visibilityQuery.isLoading || roleRefsQuery.isLoading;
  const loadError = visibilityQuery.error ?? roleRefsQuery.error;
  const isSaving = setVisibilityMutation.isPending;

  const handleSave = () => {
    // `inherit` очищает набор ролей; `restricted` шлёт собственный набор (пустой допустим).
    const payload = {
      visibility_mode: mode,
      role_ids: mode === 'restricted' ? roleIds : [],
    };
    setVisibilityMutation.mutate(
      { id: node.id, payload },
      {
        onSuccess: () => {
          toast.success('Видимость обновлена');
          onOpenChange(false);
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 403) {
            toast.error('Недостаточно прав для смены видимости');
            return;
          }
          if (err instanceof ApiError && err.status === 404) {
            toast.error('Документ не найден или недоступен');
            return;
          }
          toast.error(err instanceof ApiError ? err.message : 'Не удалось сменить видимость');
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSaving && onOpenChange(next)}
      title="Сменить видимость"
      description={`«${node.name}» — кто видит этот ${
        node.node_type === 'folder' ? 'раздел и вложенное' : 'документ'
      }.`}
      dismissible={!isSaving}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSaving}>
            Отмена
          </Button>
          <Button
            onClick={handleSave}
            loading={isSaving}
            disabled={isLoading || Boolean(loadError)}
          >
            Сохранить
          </Button>
        </>
      }
    >
      {isLoading ? (
        <div className="flex items-center justify-center gap-2 py-8 text-[13px] text-text-secondary">
          <Spinner className="text-text-secondary" />
          Загрузка настроек…
        </div>
      ) : loadError ? (
        <p className="rounded-sub border border-status-red/40 bg-status-red/5 px-3 py-3 text-[13px] text-text-secondary">
          Не удалось загрузить настройки видимости. Закройте окно и попробуйте снова.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-[13px] font-medium text-text-secondary">
              Режим видимости
            </legend>
            {(
              [
                {
                  value: 'inherit' as const,
                  title: 'Наследовать',
                  hint: 'Как у родителя; в корне — виден всем с доступом к разделу.',
                },
                {
                  value: 'restricted' as const,
                  title: 'Ограничить ролями',
                  hint: 'Виден только выбранным ролям (и ниже по дереву).',
                },
              ] satisfies { value: DocumentVisibilityMode; title: string; hint: string }[]
            ).map((opt) => {
              const active = mode === opt.value;
              return (
                <label
                  key={opt.value}
                  className={cn(
                    'flex cursor-pointer items-start gap-3 rounded-[10px] border px-3 py-2.5 transition-colors',
                    active
                      ? 'border-accent bg-accent/5'
                      : 'border-border-strong hover:border-border-strong hover:bg-surface-2',
                  )}
                >
                  <input
                    type="radio"
                    name="doc-visibility-mode"
                    value={opt.value}
                    checked={active}
                    onChange={() => setMode(opt.value)}
                    className="mt-0.5 h-4 w-4 accent-accent"
                  />
                  <span className="flex flex-col gap-0.5">
                    <span className="text-sm font-medium text-text-primary">{opt.title}</span>
                    <span className="text-[12px] leading-relaxed text-text-secondary">
                      {opt.hint}
                    </span>
                  </span>
                </label>
              );
            })}
          </fieldset>

          {mode === 'restricted' && (
            <MultiSelect
              label="Роли с доступом"
              value={roleIds}
              options={roleOptions}
              onChange={setRoleIds}
              emptyHint="Ролей нет"
              hint="Пустой список при «Ограничить ролями» — узел не виден никому, кроме администраторов."
            />
          )}
        </div>
      )}
    </Modal>
  );
}

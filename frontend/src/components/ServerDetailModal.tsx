import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { BackendsDetailSection } from '@/components/BackendsDetailSection';
import { DetailEditPencil, DetailRow, SecretRevealField } from '@/components/DetailFields';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { revealServerPassword } from '@/features/servers/api';
import { useServerBackends, useUpdateServer } from '@/features/servers/hooks';
import type { Server } from '@/types/api';

interface ServerDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: Server;
  /** Право `servers:edit` — гейт карандаша (inline-edit) и кнопки-глаза reveal. */
  canEdit: boolean;
}

function validateName(name: string): string | undefined {
  const trimmed = name.trim();
  if (!trimmed) return 'Укажите название';
  if (trimmed.length > 64) return 'Не более 64 символов';
  return undefined;
}

/**
 * Read-only detail-модалка сервера (08-design-system.md «Detail-view», ADR-035) с
 * инлайн-редактированием названия (ADR-039): короткий клик по карточке открывает её.
 * Поля: Название (карандаш → inline-edit прямо в detail-view, `PATCH name`, Сохранить/
 * Отмена) / IP / Пользователь + Пароль (reveal под `servers:edit`). Снизу — сворачиваемая
 * секция «Бэки» (`backend_count`, ленивый reverse-lookup, ADR-040). Отдельная edit-модалка
 * сервера больше не используется.
 */
export function ServerDetailModal({ open, onOpenChange, server, canEdit }: ServerDetailModalProps) {
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState(server.name);
  const [nameError, setNameError] = useState<string | null>(null);
  const [backendsOpen, setBackendsOpen] = useState(false);
  const updateMutation = useUpdateServer(server.id);
  const backendsQuery = useServerBackends(server.id, backendsOpen);

  // Сброс inline-edit при закрытии модалки (следующее открытие — read-only).
  useEffect(() => {
    if (!open) {
      setEditing(false);
      setNameError(null);
    }
  }, [open]);

  const startEdit = () => {
    setNameDraft(server.name);
    setNameError(null);
    setEditing(true);
  };

  const cancelEdit = () => {
    setEditing(false);
    setNameError(null);
  };

  const saveName = () => {
    const error = validateName(nameDraft);
    if (error) {
      setNameError(error);
      return;
    }
    const next = nameDraft.trim();
    if (next === server.name) {
      setEditing(false);
      return;
    }
    updateMutation.mutate(
      { name: next },
      {
        onSuccess: () => {
          toast.success('Сервер обновлён');
          setEditing(false);
        },
        onError: (err) => {
          if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
            setNameError('Некорректное название');
            return;
          }
          toast.error(err instanceof ApiError ? err.message : 'Не удалось обновить сервер');
        },
      },
    );
  };

  const isSaving = updateMutation.isPending;

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSaving && onOpenChange(next)}
      title="Просмотр"
      headerAction={canEdit && !editing ? <DetailEditPencil onClick={startEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        {editing ? (
          <div className="flex flex-col gap-2">
            <Input
              label="Название"
              value={nameDraft}
              error={nameError}
              autoFocus
              maxLength={64}
              disabled={isSaving}
              onChange={(e) => {
                setNameDraft(e.target.value);
                if (nameError) setNameError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  saveName();
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  cancelEdit();
                }
              }}
            />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={cancelEdit} disabled={isSaving}>
                Отмена
              </Button>
              <Button size="sm" onClick={saveName} loading={isSaving}>
                Сохранить
              </Button>
            </div>
          </div>
        ) : (
          <DetailRow label="Название" value={server.name} />
        )}

        <DetailRow label="IP" value={server.ip} mono />
        <DetailRow label="Пользователь" value={server.ssh_user} mono />
        <SecretRevealField
          label="Пароль"
          canReveal={canEdit}
          reveal={(signal) => revealServerPassword(server.id, signal)}
          showAria="Показать пароль"
          hideAria="Скрыть пароль"
        />

        <BackendsDetailSection
          count={server.backend_count}
          id={`server-${server.id}-backends`}
          open={backendsOpen}
          onToggle={() => setBackendsOpen((v) => !v)}
          query={backendsQuery}
        />
      </div>
    </Modal>
  );
}

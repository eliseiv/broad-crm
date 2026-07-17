import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { useUpdateNode } from '@/features/documents/hooks';
import type { DocumentNode } from '@/types/api';

interface DocumentRenameModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  node: DocumentNode | null;
}

/**
 * Переименование узла (PATCH /api/documents/nodes/{id}, поле `name`). `content_version`
 * инкрементируется сервером; expected_version для rename не передаём (last-write-wins,
 * TD-064). Ремоунт по ключу в родителе даёт свежий префилл.
 */
export function DocumentRenameModal({ open, onOpenChange, node }: DocumentRenameModalProps) {
  const [name, setName] = useState(node?.name ?? '');
  const [error, setError] = useState<string | null>(null);
  const updateMutation = useUpdateNode();

  if (!node) return null;
  const isSubmitting = updateMutation.isPending;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Укажите название');
      return;
    }
    if (trimmed.length > 255) {
      setError('Не более 255 символов');
      return;
    }
    if (trimmed === node.name) {
      onOpenChange(false);
      return;
    }
    updateMutation.mutate(
      { id: node.id, payload: { name: trimmed } },
      {
        onSuccess: () => {
          toast.success('Переименовано');
          onOpenChange(false);
        },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast.error('Документ изменён другим пользователем');
            return;
          }
          if (err instanceof ApiError && err.status === 403) {
            toast.error('Недостаточно прав');
            return;
          }
          if (err instanceof ApiError && (err.status === 400 || err.status === 422)) {
            const detail = err.details?.find((d) => d.field === 'name')?.message;
            setError(detail ?? 'Проверьте название');
            return;
          }
          toast.error(err instanceof ApiError ? err.message : 'Не удалось переименовать');
        },
      },
    );
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isSubmitting && onOpenChange(next)}
      title="Переименовать"
      dismissible={!isSubmitting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Отмена
          </Button>
          <Button type="submit" form="doc-rename-form" loading={isSubmitting}>
            Сохранить
          </Button>
        </>
      }
    >
      <form id="doc-rename-form" onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        <Input
          label="Название"
          value={name}
          error={error}
          autoFocus
          maxLength={255}
          autoComplete="off"
          onChange={(e) => {
            setName(e.target.value);
            if (error) setError(null);
          }}
        />
      </form>
    </Modal>
  );
}

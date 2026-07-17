import { toast } from 'sonner';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { useDeleteNode } from '@/features/documents/hooks';
import { descendantCount } from '@/features/documents/tree';
import { pluralRu } from '@/lib/plural';
import type { DocumentNode } from '@/types/api';

interface DocumentDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  node: DocumentNode | null;
  /** Все узлы — для подсчёта вложенных при удалении папки. */
  nodes: DocumentNode[];
  /** Вызывается после успешного удаления (сброс выбора в родителе). */
  onDeleted?: (node: DocumentNode) => void;
}

/**
 * Подтверждение удаления (soft-delete, DELETE /api/documents/nodes/{id}). Для папки —
 * каскад поддерева: показываем число вложенных элементов (считается клиентом по дереву).
 */
export function DocumentDeleteDialog({
  open,
  onOpenChange,
  node,
  nodes,
  onDeleted,
}: DocumentDeleteDialogProps) {
  const deleteMutation = useDeleteNode();

  if (!node) return null;
  const isFolder = node.node_type === 'folder';
  const nested = isFolder ? descendantCount(nodes, node.id) : 0;
  const isDeleting = deleteMutation.isPending;

  const handleDelete = () => {
    deleteMutation.mutate(node.id, {
      onSuccess: () => {
        toast.success(isFolder ? 'Папка удалена' : 'Документ удалён');
        onOpenChange(false);
        onDeleted?.(node);
      },
      onError: (err) => {
        if (err instanceof ApiError && err.status === 403) {
          toast.error('Недостаточно прав для удаления');
          return;
        }
        if (err instanceof ApiError && err.status === 404) {
          toast.error('Узел не найден или недоступен');
          onOpenChange(false);
          return;
        }
        toast.error(err instanceof ApiError ? err.message : 'Не удалось удалить');
      },
    });
  };

  return (
    <Modal
      open={open}
      onOpenChange={(next) => !isDeleting && onOpenChange(next)}
      title={isFolder ? 'Удалить папку?' : 'Удалить документ?'}
      dismissible={!isDeleting}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isDeleting}>
            Отмена
          </Button>
          <Button variant="danger" loading={isDeleting} onClick={handleDelete}>
            Удалить
          </Button>
        </>
      }
    >
      <p className="text-sm text-text-secondary">
        {isFolder ? (
          nested > 0 ? (
            <>
              Папка «{node.name}» и {nested}{' '}
              {pluralRu(nested, {
                one: 'вложенный элемент',
                few: 'вложенных элемента',
                many: 'вложенных элементов',
              })}{' '}
              будут удалены.
            </>
          ) : (
            <>Пустая папка «{node.name}» будет удалена.</>
          )
        ) : (
          <>Документ «{node.name}» будет удалён.</>
        )}
      </p>
    </Modal>
  );
}

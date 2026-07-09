import { DetailEditPencil, DetailRow } from '@/components/DetailFields';
import { Modal } from '@/components/ui/Modal';
import type { Backend } from '@/types/api';

interface BackendDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backend: Backend;
  /** Право `backends:edit` — гейт карандаша. */
  canEdit: boolean;
  /** Клик по карандашу: закрыть detail и открыть edit-модалку (ADR-035). */
  onEdit: () => void;
}

/**
 * Read-only detail-модалка бэка (08-design-system.md «Detail-view», ADR-035).
 * Поля: Код / Название / Домен. Секрета у сущности нет — reveal не показывается.
 * Карандаш вверху справа (под `backends:edit`) открывает существующую edit-модалку.
 */
export function BackendDetailModal({
  open,
  onOpenChange,
  backend,
  canEdit,
  onEdit,
}: BackendDetailModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Просмотр"
      headerAction={canEdit ? <DetailEditPencil onClick={onEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        <DetailRow label="Код" value={backend.code} mono />
        <DetailRow label="Название" value={backend.name} />
        <DetailRow label="Домен" value={backend.domain} mono />
      </div>
    </Modal>
  );
}

import { DetailEditPencil, DetailRow, SecretRevealField } from '@/components/DetailFields';
import { Modal } from '@/components/ui/Modal';
import { revealServerPassword } from '@/features/servers/api';
import type { Server } from '@/types/api';

interface ServerDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  server: Server;
  /** Право `servers:edit` — гейт карандаша и кнопки-глаза reveal. */
  canEdit: boolean;
  /** Клик по карандашу: закрыть detail и открыть edit-модалку (ADR-035). */
  onEdit: () => void;
}

/**
 * Read-only detail-модалка сервера (08-design-system.md «Detail-view», ADR-035):
 * короткий клик по карточке открывает её вместо edit. Поля: Название / IP /
 * Пользователь + Пароль (reveal по требованию под `servers:edit`). Карандаш вверху
 * справа (под `servers:edit`) открывает существующую edit-модалку.
 */
export function ServerDetailModal({
  open,
  onOpenChange,
  server,
  canEdit,
  onEdit,
}: ServerDetailModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Просмотр"
      headerAction={canEdit ? <DetailEditPencil onClick={onEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        <DetailRow label="Название" value={server.name} />
        <DetailRow label="IP" value={server.ip} mono />
        <DetailRow label="Пользователь" value={server.ssh_user} mono />
        <SecretRevealField
          label="Пароль"
          canReveal={canEdit}
          reveal={(signal) => revealServerPassword(server.id, signal)}
          showAria="Показать пароль"
          hideAria="Скрыть пароль"
        />
      </div>
    </Modal>
  );
}

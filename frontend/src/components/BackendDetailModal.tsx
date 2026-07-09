import { DetailEditPencil, DetailRow, SecretRevealField } from '@/components/DetailFields';
import { Modal } from '@/components/ui/Modal';
import { revealBackendAdminApiKey, revealBackendApiKey } from '@/features/backends/api';
import type { Backend } from '@/types/api';

interface BackendDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backend: Backend;
  /** Право `backends:edit` — гейт карандаша и кнопок-глаза reveal секретов. */
  canEdit: boolean;
  /** Клик по карандашу: закрыть detail и открыть edit-модалку (ADR-035). */
  onEdit: () => void;
}

const DASH = <span className="text-text-tertiary">—</span>;

/**
 * Read-only detail-модалка бэка (08-design-system.md «Detail-view», ADR-035/ADR-040).
 * Поля: Код / Название / Домен + Сервер (`server_name`/«—») / ИИ-ключ (`ai_key_name`/«—») /
 * API KEY / ADMIN API KEY (`••••` + глаз-reveal под `backends:edit`, если `has_*`; иначе «—») /
 * Git (ссылка/«—») / Примечания (`note`/«—»). Карандаш (под `backends:edit`) открывает
 * edit-модалку с секцией «Информация».
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
        <DetailRow label="Сервер" value={backend.server_name ?? DASH} />
        <DetailRow label="ИИ-ключ" value={backend.ai_key_name ?? DASH} />

        {backend.has_api_key ? (
          <SecretRevealField
            label="API KEY"
            canReveal={canEdit}
            reveal={(signal) => revealBackendApiKey(backend.id, signal)}
            showAria="Показать API KEY"
            hideAria="Скрыть API KEY"
          />
        ) : (
          <DetailRow label="API KEY" value={DASH} />
        )}

        {backend.has_admin_api_key ? (
          <SecretRevealField
            label="ADMIN API KEY"
            canReveal={canEdit}
            reveal={(signal) => revealBackendAdminApiKey(backend.id, signal)}
            showAria="Показать ADMIN API KEY"
            hideAria="Скрыть ADMIN API KEY"
          />
        ) : (
          <DetailRow label="ADMIN API KEY" value={DASH} />
        )}

        <DetailRow
          label="Git"
          value={
            backend.git ? (
              <a
                href={backend.git}
                target="_blank"
                rel="noopener noreferrer"
                className="break-all text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                {backend.git}
              </a>
            ) : (
              DASH
            )
          }
        />
        <DetailRow label="Примечания" value={backend.note ?? DASH} />
      </div>
    </Modal>
  );
}

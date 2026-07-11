import {
  DetailEditPencil,
  DetailInfoSection,
  DetailRow,
  SecretRevealField,
} from '@/components/DetailFields';
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

/**
 * Read-only detail-модалка бэка (08-design-system.md «Detail-view», ADR-035/ADR-040/ADR-046 §2в).
 * Сразу видны только **идентификаторы**: Код / Название / Домен. Всё остальное — Сервер,
 * ИИ-ключ, API KEY / ADMIN API KEY (`••••` + глаз-reveal под `backends:edit`, только при
 * `has_api_key`/`has_admin_api_key`), Git, Примечания — внутри свёрнутого блока «Информация».
 * Пустые поля не рендерятся (ADR-046 §3; прочерк «—» упразднён); если внутри «Информации» не
 * осталось ничего — блок не рендерится вовсе. Карандаш (под `backends:edit`) открывает edit.
 */
export function BackendDetailModal({
  open,
  onOpenChange,
  backend,
  canEdit,
  onEdit,
}: BackendDetailModalProps) {
  const hasInfo =
    Boolean(backend.server_name) ||
    Boolean(backend.ai_key_name) ||
    backend.has_api_key ||
    backend.has_admin_api_key ||
    Boolean(backend.git) ||
    Boolean(backend.note);

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

        {hasInfo && (
          <DetailInfoSection>
            <DetailRow label="Сервер" value={backend.server_name} />
            <DetailRow label="ИИ-ключ" value={backend.ai_key_name} />

            {backend.has_api_key && (
              <SecretRevealField
                label="API KEY"
                canReveal={canEdit}
                reveal={(signal) => revealBackendApiKey(backend.id, signal)}
                showAria="Показать API KEY"
                hideAria="Скрыть API KEY"
              />
            )}

            {backend.has_admin_api_key && (
              <SecretRevealField
                label="ADMIN API KEY"
                canReveal={canEdit}
                reveal={(signal) => revealBackendAdminApiKey(backend.id, signal)}
                showAria="Показать ADMIN API KEY"
                hideAria="Скрыть ADMIN API KEY"
              />
            )}

            {backend.git && (
              <DetailRow
                label="Git"
                value={
                  <a
                    href={backend.git}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    {backend.git}
                  </a>
                }
              />
            )}
            <DetailRow label="Примечания" value={backend.note} />
          </DetailInfoSection>
        )}
      </div>
    </Modal>
  );
}

import {
  DetailEditPencil,
  DetailInfoSection,
  DetailRow,
  SecretRevealField,
} from '@/components/DetailFields';
import { Modal } from '@/components/ui/Modal';
import { revealProxyPassword } from '@/features/proxies/api';
import type { Proxy, ProxyType } from '@/types/api';

/** Локализованное имя типа прокси (08-design-system.md, словарь). */
const TYPE_LABEL: Record<ProxyType, string> = {
  http: 'HTTP',
  https: 'HTTPS',
  socks5: 'SOCKS5',
};

interface ProxyDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  proxy: Proxy;
  /** Право `proxies:edit` — гейт карандаша и кнопки-глаза reveal. */
  canEdit: boolean;
  /** Клик по карандашу: закрыть detail и открыть edit-модалку (ADR-035). */
  onEdit: () => void;
}

/**
 * Read-only detail-модалка прокси (08-design-system.md «Detail-view», ADR-035/ADR-046 §2в).
 * Сразу видны только **идентификаторы**: Название / Хост / Порт. Тип, Логин и Пароль (reveal
 * под `proxies:edit`, только при `has_password`) — внутри свёрнутого блока «Информация».
 * Пустые поля не рендерятся (ADR-046 §3): без логина строка «Логин» отсутствует, без пароля —
 * строка «Пароль» отсутствует (прежнее «Пароль: —» упразднено). Карандаш под `proxies:edit`.
 */
export function ProxyDetailModal({
  open,
  onOpenChange,
  proxy,
  canEdit,
  onEdit,
}: ProxyDetailModalProps) {
  return (
    <Modal
      open={open}
      onOpenChange={onOpenChange}
      title="Просмотр"
      headerAction={canEdit ? <DetailEditPencil onClick={onEdit} /> : undefined}
    >
      <div className="flex flex-col gap-4">
        <DetailRow label="Название" value={proxy.name} />
        <DetailRow label="Хост" value={proxy.host} mono />
        <DetailRow label="Порт" value={proxy.port} mono />

        {/* «Тип» присутствует всегда ⇒ блок «Информация» рендерится всегда. */}
        <DetailInfoSection>
          <DetailRow label="Тип" value={TYPE_LABEL[proxy.proxy_type]} />
          <DetailRow
            label="Логин"
            value={proxy.username ? <span className="font-mono">{proxy.username}</span> : null}
          />
          {proxy.has_password && (
            <SecretRevealField
              label="Пароль"
              canReveal={canEdit}
              reveal={(signal) => revealProxyPassword(proxy.id, signal)}
              showAria="Показать пароль"
              hideAria="Скрыть пароль"
            />
          )}
        </DetailInfoSection>
      </div>
    </Modal>
  );
}

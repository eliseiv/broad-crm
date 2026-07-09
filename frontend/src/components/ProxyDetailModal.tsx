import { DetailEditPencil, DetailRow, SecretRevealField } from '@/components/DetailFields';
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
 * Read-only detail-модалка прокси (08-design-system.md «Detail-view», ADR-035).
 * Поля: Название / Тип / Хост / Порт / Логин + Пароль (reveal под `proxies:edit`
 * и только при `has_password`; без пароля — «Пароль: —»). Карандаш под `proxies:edit`.
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
        <DetailRow label="Тип" value={TYPE_LABEL[proxy.proxy_type]} />
        <DetailRow label="Хост" value={proxy.host} mono />
        <DetailRow label="Порт" value={proxy.port} mono />
        <DetailRow
          label="Логин"
          value={proxy.username ? <span className="font-mono">{proxy.username}</span> : '—'}
        />
        {proxy.has_password ? (
          <SecretRevealField
            label="Пароль"
            canReveal={canEdit}
            reveal={(signal) => revealProxyPassword(proxy.id, signal)}
            showAria="Показать пароль"
            hideAria="Скрыть пароль"
          />
        ) : (
          <DetailRow label="Пароль" value="—" />
        )}
      </div>
    </Modal>
  );
}

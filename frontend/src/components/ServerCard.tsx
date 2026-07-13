import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, Clock, Loader2, Server as ServerIcon, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { BackendsDetailSection } from '@/components/BackendsDetailSection';
import { ServerDetailModal } from '@/components/ServerDetailModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { MetricSubCard } from '@/components/MetricSubCard';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatRelativeTime, formatUptime } from '@/lib/format';
import {
  serversKey,
  useDeleteServer,
  useServerBackends,
  useServerStatus,
} from '@/features/servers/hooks';
import type { ProvisionStatus, Server } from '@/types/api';

interface ServerCardProps {
  server: Server;
  /** Право редактирования (клик по карточке → edit). RBAC-гейтинг. По умолчанию true. */
  canEdit?: boolean;
  /** Право удаления (кнопки «Удалить»). RBAC-гейтинг. По умолчанию true. */
  canDelete?: boolean;
}

// Подпись в теле карточки во время провижининга.
const PROVISIONING_LABEL: Record<'pending' | 'installing', string> = {
  pending: 'Ожидание установки…',
  installing: 'Установка агента…',
};

// Краткий статус для бейджа (словарь 08-design-system.md).
const PROVISIONING_BADGE: Record<'pending' | 'installing', string> = {
  pending: 'Ожидание',
  installing: 'Установка…',
};

export function ServerCard({ server, canEdit = true, canDelete = true }: ServerCardProps) {
  const queryClient = useQueryClient();
  const statusQuery = useServerStatus(server.id, server.provision_status);
  const deleteMutation = useDeleteServer();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  // Секция «Бэки» на карточке (ADR-049 §2). Список грузится ТОЛЬКО по раскрытию: `enabled`
  // ленивого хука = `backendsOpen`. Свёрнутая карточка не делает НИ ОДНОГО запроса —
  // преднагрузка списков для всех карточек сетки ЗАПРЕЩЕНА (это был бы N+1 на N карточек).
  const [backendsOpen, setBackendsOpen] = useState(false);
  const backendsQuery = useServerBackends(server.id, backendsOpen);

  const status: ProvisionStatus = statusQuery.data?.provision_status ?? server.provision_status;
  const errorMessage = statusQuery.data?.error_message ?? null;

  // При переходе провижининга в online/error — обновить общий список.
  useEffect(() => {
    if (status === 'online' || status === 'error') {
      void queryClient.invalidateQueries({ queryKey: serversKey });
    }
  }, [status, queryClient]);

  const handleDelete = () => {
    deleteMutation.mutate(server.id, {
      onSuccess: () => {
        toast.success('Сервер удалён');
        setConfirmOpen(false);
      },
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить сервер';
        toast.error(message);
      },
    });
  };

  const isProvisioning = status === 'pending' || status === 'installing';
  const isError = status === 'error';
  const isOnline = status === 'online' && server.online;
  const isOffline = status === 'online' && !server.online;

  // Короткий клик по карточке → read-only detail-модалка (ADR-035; drag активируется
  // зажатием — см. PointerSensor в ServersPage). Detail доступен держателю `servers:view`
  // (страница уже под view-guard). Кнопки «Удалить» гасят событие (stopPropagation).
  const onCardKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setDetailOpen(true);
    }
  };
  const stopForDelete = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <>
      <Card
        interactive
        role="button"
        tabIndex={0}
        aria-label={`Просмотр сервера ${server.name}`}
        onClick={() => setDetailOpen(true)}
        onKeyDown={onCardKeyDown}
        className={cn(
          'flex h-full cursor-pointer flex-col gap-4 p-4 sm:p-5',
          isError && 'border-status-red/70',
        )}
      >
        {/* Шапка */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
              <ServerIcon className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="truncate text-lg font-bold leading-tight text-text-primary">
                  {server.name}
                </h3>
                {isOnline && <Badge tone="green">В сети</Badge>}
                {isOffline && <Badge tone="red">Не в сети</Badge>}
                {isError && <Badge tone="red">Ошибка</Badge>}
                {isProvisioning && (
                  <Badge tone="accent">
                    {PROVISIONING_BADGE[status as 'pending' | 'installing']}
                  </Badge>
                )}
              </div>
              <p className="font-mono text-[12px] text-text-tertiary">{server.ip}</p>
            </div>
          </div>

          {canDelete && (
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onPointerDown={stopForDelete}
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmOpen(true);
                }}
                aria-label={`Удалить сервер ${server.name}`}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-status-red focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>

        {/* Мета-строка: Аптайм + Обновлено (online) */}
        {isOnline && (
          <div className="-mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-text-secondary">
            <span>
              Аптайм:{' '}
              <span className="font-mono text-text-primary">
                {formatUptime(server.uptime_seconds)}
              </span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              Обновлено: {formatRelativeTime(server.last_updated)}
            </span>
          </div>
        )}

        {/* Тело по состояниям */}
        {isProvisioning && (
          <div className="flex flex-col items-center gap-3 rounded-sub border border-border-subtle bg-surface-2 px-4 py-10 text-center">
            <Loader2 className="h-8 w-8 animate-spin text-accent" aria-hidden="true" />
            <p className="text-sm font-medium text-text-primary">
              {PROVISIONING_LABEL[status as 'pending' | 'installing']}
            </p>
            <p className="text-[13px] text-text-secondary">
              Это может занять несколько минут. Метрики появятся после установки.
            </p>
          </div>
        )}

        {isError && (
          <div className="flex flex-col items-center gap-3 rounded-sub border border-status-red/40 bg-status-red/5 px-4 py-8 text-center">
            <AlertTriangle className="h-8 w-8 text-status-red" aria-hidden="true" />
            <p className="text-sm font-medium text-text-primary">Ошибка установки агента</p>
            {errorMessage && <p className="text-[13px] text-text-secondary">{errorMessage}</p>}
            {canDelete && (
              <Button
                variant="danger"
                size="sm"
                loading={deleteMutation.isPending}
                onPointerDown={stopForDelete}
                onClick={(e) => {
                  e.stopPropagation();
                  setConfirmOpen(true);
                }}
              >
                <Trash2 className="h-3.5 w-3.5" />
                Удалить
              </Button>
            )}
          </div>
        )}

        {(isOnline || isOffline) && (
          <div className="grid grid-cols-3 gap-3">
            <MetricSubCard kind="cpu" metric={server.metrics?.cpu ?? null} />
            <MetricSubCard kind="ram" metric={server.metrics?.ram ?? null} />
            <MetricSubCard kind="ssd" metric={server.metrics?.ssd ?? null} />
          </div>
        )}

        {isOffline && (
          <p className="-mt-1 text-center text-[12px] text-text-tertiary">
            Не в сети. Обновлено: {formatRelativeTime(server.last_updated)}.
          </p>
        )}

        {/*
          Секция «Бэков: N» внизу карточки (ADR-049 §2). Разведение жестов (нормативно,
          обязательно): карточка ОДНОВРЕМЕННО кликабельна целиком (→ ServerDetailModal) и
          является drag-ручкой DnD (listeners @dnd-kit висят на обёртке SortableItem).
          Поэтому обёртка секции гасит всплытие pointer/click/keydown: раскрытие НЕ открывает
          detail-модалку и НЕ инициирует перетаскивание карточки. Сам триггер — собственный
          <button aria-expanded/aria-controls> внутри BackendsDetailSection.
          `mt-auto` прижимает секцию к низу карточки при разной высоте тела.
        */}
        <div
          className="mt-auto"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
          role="presentation"
        >
          <BackendsDetailSection
            count={server.backend_count}
            id={`server-${server.id}-backends`}
            open={backendsOpen}
            onToggle={() => setBackendsOpen((v) => !v)}
            query={backendsQuery}
            // backend_count = 0 → строка «Бэков: 0» рендерится, но секция НЕ раскрывается
            // (нет chevron, нет role="button", нет запроса) — информативный счётчик.
            collapsible={server.backend_count > 0}
          />
        </div>
      </Card>

      <Modal
        open={confirmOpen}
        onOpenChange={(open) => !deleteMutation.isPending && setConfirmOpen(open)}
        title="Удалить сервер?"
        description={`Сервер «${server.name}» (${server.ip}) будет снят с мониторинга. Действие необратимо.`}
        dismissible={!deleteMutation.isPending}
        footer={
          <>
            <Button
              variant="ghost"
              onClick={() => setConfirmOpen(false)}
              disabled={deleteMutation.isPending}
            >
              Отмена
            </Button>
            <Button variant="danger" loading={deleteMutation.isPending} onClick={handleDelete}>
              Удалить
            </Button>
          </>
        }
      >
        <p className="text-sm text-text-secondary">
          node_exporter на целевом сервере не удаляется автоматически.
        </p>
      </Modal>

      <ServerDetailModal
        open={detailOpen}
        onOpenChange={setDetailOpen}
        server={server}
        canEdit={canEdit}
      />
    </>
  );
}

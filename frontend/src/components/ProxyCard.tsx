import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Clock, Loader2, Network, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { AddProxyModal } from '@/components/AddProxyModal';
import { ProxyDetailModal } from '@/components/ProxyDetailModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import { proxiesKey, useDeleteProxy, useProxyStatus } from '@/features/proxies/hooks';
import type { Proxy, ProxyCheckStatus, ProxyType } from '@/types/api';

interface ProxyCardProps {
  proxy: Proxy;
  /** Право редактирования (клик по карточке → edit). RBAC-гейтинг. По умолчанию true. */
  canEdit?: boolean;
  /** Право удаления (кнопки «Удалить»). RBAC-гейтинг. По умолчанию true. */
  canDelete?: boolean;
}

/** Локализованное имя типа прокси (08-design-system.md, словарь). */
const TYPE_LABEL: Record<ProxyType, string> = {
  http: 'HTTP',
  https: 'HTTPS',
  socks5: 'SOCKS5',
};

export function ProxyCard({ proxy, canEdit = true, canDelete = true }: ProxyCardProps) {
  const queryClient = useQueryClient();
  const statusQuery = useProxyStatus(proxy.id, proxy.check_status);
  const deleteMutation = useDeleteProxy();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const status: ProxyCheckStatus = statusQuery.data?.check_status ?? proxy.check_status;
  const errorMessage = statusQuery.data?.error_message ?? proxy.error_message;
  const lastChecked = statusQuery.data?.last_checked_at ?? proxy.last_checked_at;

  // При выходе проверки из pending (working/error) — обновить общий список.
  useEffect(() => {
    if (status === 'working' || status === 'error') {
      void queryClient.invalidateQueries({ queryKey: proxiesKey });
    }
  }, [status, queryClient]);

  const handleDelete = () => {
    deleteMutation.mutate(proxy.id, {
      onSuccess: () => {
        toast.success('Прокси удалён');
        setConfirmOpen(false);
      },
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить прокси';
        toast.error(message);
      },
    });
  };

  const isError = status === 'error';
  const isPending = status === 'pending';

  // Короткий клик по карточке → read-only detail-модалка (ADR-035); кнопки «Удалить»
  // гасят событие (stopPropagation), чтобы не открывать detail и не стартовать drag.
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
        aria-label={`Просмотр прокси ${proxy.name}`}
        onClick={() => setDetailOpen(true)}
        onKeyDown={onCardKeyDown}
        className={cn(
          'flex h-full cursor-pointer flex-col gap-4 p-4 sm:p-5',
          isError && 'border-status-red/70',
        )}
      >
        {/* Шапка: иконка + имя + статус-бейдж */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
              <Network className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="truncate text-lg font-bold leading-tight text-text-primary">
                  {proxy.name}
                </h3>
                {status === 'working' && <Badge tone="green">Работает</Badge>}
                {isError && <Badge tone="red">Не работает</Badge>}
                {isPending && (
                  <span className="inline-flex items-center gap-1.5 text-[13px] font-medium text-text-secondary">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    Проверка…
                  </span>
                )}
              </div>
              <p className="text-[13px] text-text-secondary">{TYPE_LABEL[proxy.proxy_type]}</p>
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
                aria-label={`Удалить прокси ${proxy.name}`}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-status-red focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>

        {/* Адрес — только IP/хост (ADR-023: логин/пароль/порт не показываем; они в форме edit) */}
        <div className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
          <span
            className="font-mono text-sm tracking-tight text-text-secondary"
            aria-label="IP-адрес"
          >
            {proxy.host}
          </span>
        </div>

        {/* Причина ошибки при error */}
        {isError && errorMessage && <p className="text-[13px] text-status-red">{errorMessage}</p>}

        {/* Обновлено (единственная кнопка «Удалить» — в шапке карточки, ADR-023) */}
        <div className="flex items-center gap-2">
          {lastChecked ? (
            <span className="inline-flex items-center gap-1.5 text-[13px] text-text-secondary">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              Обновлено: {formatRelativeTime(lastChecked)}
            </span>
          ) : (
            <span className="text-[13px] text-text-tertiary">Ожидание первой проверки…</span>
          )}
        </div>
      </Card>

      <Modal
        open={confirmOpen}
        onOpenChange={(open) => !deleteMutation.isPending && setConfirmOpen(open)}
        title="Удалить прокси?"
        description={`Прокси «${proxy.name}» (${proxy.host}:${proxy.port}) будет удалён из реестра. Действие необратимо.`}
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
          Мониторинг доступности этого прокси будет прекращён.
        </p>
      </Modal>

      <ProxyDetailModal
        open={detailOpen}
        onOpenChange={setDetailOpen}
        proxy={proxy}
        canEdit={canEdit}
        onEdit={() => {
          setDetailOpen(false);
          setEditOpen(true);
        }}
      />

      <AddProxyModal mode="edit" proxy={proxy} open={editOpen} onOpenChange={setEditOpen} />
    </>
  );
}

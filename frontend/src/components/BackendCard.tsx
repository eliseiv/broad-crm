import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Boxes, Clock, Globe, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { AddBackendModal } from '@/components/AddBackendModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { formatRelativeTime } from '@/lib/format';
import { backendsKey, useBackendStatus, useDeleteBackend } from '@/features/backends/hooks';
import type { Backend, BackendCheckStatus } from '@/types/api';

interface BackendCardProps {
  backend: Backend;
}

export function BackendCard({ backend }: BackendCardProps) {
  const queryClient = useQueryClient();
  const statusQuery = useBackendStatus(backend.id, backend.check_status);
  const deleteMutation = useDeleteBackend();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const status: BackendCheckStatus = statusQuery.data?.check_status ?? backend.check_status;
  const errorMessage = statusQuery.data?.error_message ?? backend.error_message;
  const lastChecked = statusQuery.data?.last_checked_at ?? backend.last_checked_at;

  // При выходе проверки из pending (working/error) — обновить общий список.
  useEffect(() => {
    if (status === 'working' || status === 'error') {
      void queryClient.invalidateQueries({ queryKey: backendsKey });
    }
  }, [status, queryClient]);

  const handleDelete = () => {
    deleteMutation.mutate(backend.id, {
      onSuccess: () => {
        toast.success('Бэк удалён');
        setConfirmOpen(false);
      },
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить бэк';
        toast.error(message);
      },
    });
  };

  const isError = status === 'error';
  const isPending = status === 'pending';

  // Клик по карточке → edit; кнопки «Удалить» гасят событие (stopPropagation),
  // чтобы не открывать edit и не стартовать drag.
  const onCardKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setEditOpen(true);
    }
  };
  const stopForDelete = (e: React.SyntheticEvent) => e.stopPropagation();

  return (
    <>
      <Card
        interactive
        role="button"
        tabIndex={0}
        aria-label={`Изменить бэк ${backend.name}`}
        onClick={() => setEditOpen(true)}
        onKeyDown={onCardKeyDown}
        className={`flex h-full cursor-pointer flex-col gap-4 p-4 sm:p-5 ${isError ? 'border-status-red/70' : ''}`}
      >
        {/* Шапка: иконка + имя + статус-бейдж */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
              <Boxes className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="truncate text-lg font-bold leading-tight text-text-primary">
                  {backend.name}
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
              {/* Код бэка (моношрифт, вторичный цвет) */}
              <p className="truncate font-mono text-[13px] text-text-secondary" aria-label="Код">
                {backend.code}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onPointerDown={stopForDelete}
              onClick={(e) => {
                e.stopPropagation();
                setConfirmOpen(true);
              }}
              aria-label={`Удалить бэк ${backend.name}`}
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-status-red focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Домен (моношрифт) */}
        <div className="flex items-center gap-2 rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
          <Globe className="h-3.5 w-3.5 shrink-0 text-text-tertiary" aria-hidden="true" />
          <span
            className="truncate font-mono text-sm tracking-tight text-text-secondary"
            aria-label="Домен"
          >
            {backend.domain}
          </span>
        </div>

        {/* Причина ошибки при error */}
        {isError && errorMessage && <p className="text-[13px] text-status-red">{errorMessage}</p>}

        {/* Обновлено + действие */}
        <div className="flex items-center justify-between gap-2">
          {lastChecked ? (
            <span className="inline-flex items-center gap-1.5 text-[13px] text-text-secondary">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              Обновлено: {formatRelativeTime(lastChecked)}
            </span>
          ) : (
            <span className="text-[13px] text-text-tertiary">Ожидание первой проверки…</span>
          )}
          {isError && (
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
      </Card>

      <Modal
        open={confirmOpen}
        onOpenChange={(open) => !deleteMutation.isPending && setConfirmOpen(open)}
        title="Удалить бэк?"
        description={`Бэк «${backend.name}» (${backend.domain}) будет удалён из реестра. Действие необратимо.`}
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
          Мониторинг доступности этого бэка будет прекращён.
        </p>
      </Modal>

      <AddBackendModal mode="edit" backend={backend} open={editOpen} onOpenChange={setEditOpen} />
    </>
  );
}

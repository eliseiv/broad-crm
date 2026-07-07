import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Clock, KeyRound, Loader2, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { AddAiKeyModal } from '@/components/AddAiKeyModal';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import { aiKeysKey, useAiKeyStatus, useDeleteAiKey } from '@/features/ai-keys/hooks';
import type { AiKey, AiKeyStatus, AiProvider } from '@/types/api';

interface AiKeyCardProps {
  aiKey: AiKey;
  /** Право редактирования (клик по карточке → edit). RBAC-гейтинг. По умолчанию true. */
  canEdit?: boolean;
  /** Право удаления (кнопки «Удалить»). RBAC-гейтинг. По умолчанию true. */
  canDelete?: boolean;
}

/** Локализованное имя провайдера (08-design-system.md, словарь). */
const PROVIDER_LABEL: Record<AiProvider, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
};

export function AiKeyCard({ aiKey, canEdit = true, canDelete = true }: AiKeyCardProps) {
  const queryClient = useQueryClient();
  const statusQuery = useAiKeyStatus(aiKey.id, aiKey.check_status);
  const deleteMutation = useDeleteAiKey();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);

  const status: AiKeyStatus = statusQuery.data?.check_status ?? aiKey.check_status;
  const errorMessage = statusQuery.data?.error_message ?? aiKey.error_message;
  const lastChecked = statusQuery.data?.last_checked_at ?? aiKey.last_checked_at;

  // При выходе проверки из pending (working/error) — обновить общий список.
  useEffect(() => {
    if (status === 'working' || status === 'error') {
      void queryClient.invalidateQueries({ queryKey: aiKeysKey });
    }
  }, [status, queryClient]);

  const handleDelete = () => {
    deleteMutation.mutate(aiKey.id, {
      onSuccess: () => {
        toast.success('Ключ удалён');
        setConfirmOpen(false);
      },
      onError: (err) => {
        const message = err instanceof ApiError ? err.message : 'Не удалось удалить ключ';
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
        interactive={canEdit}
        role={canEdit ? 'button' : undefined}
        tabIndex={canEdit ? 0 : undefined}
        aria-label={canEdit ? `Изменить ключ ${aiKey.name}` : undefined}
        onClick={canEdit ? () => setEditOpen(true) : undefined}
        onKeyDown={canEdit ? onCardKeyDown : undefined}
        className={cn(
          'flex h-full flex-col gap-4 p-4 sm:p-5',
          canEdit && 'cursor-pointer',
          isError && 'border-status-red/70',
        )}
      >
        {/* Шапка: иконка + имя + статус-бейдж */}
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-chip bg-surface-3 text-text-secondary">
              <KeyRound className="h-5 w-5" aria-hidden="true" />
            </span>
            <div className="flex min-w-0 flex-col gap-0.5">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <h3 className="truncate text-lg font-bold leading-tight text-text-primary">
                  {aiKey.name}
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
              <p className="text-[13px] text-text-secondary">{PROVIDER_LABEL[aiKey.provider]}</p>
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
                aria-label={`Удалить ключ ${aiKey.name}`}
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors hover:bg-surface-3 hover:text-status-red focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <Trash2 className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          )}
        </div>

        {/* Маска ключа (моношрифт, полный ключ не показывается никогда) */}
        <div className="rounded-sub border border-border-subtle bg-surface-2 px-3 py-2.5">
          <span className="font-mono text-sm tracking-tight text-text-secondary" aria-label="Ключ">
            {aiKey.key_masked}
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
          {isError && canDelete && (
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
        title="Удалить ключ?"
        description={`Ключ «${aiKey.name}» (${PROVIDER_LABEL[aiKey.provider]}) будет удалён из реестра. Действие необратимо.`}
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
          Мониторинг валидности этого ключа будет прекращён.
        </p>
      </Modal>

      <AddAiKeyModal mode="edit" aiKey={aiKey} open={editOpen} onOpenChange={setEditOpen} />
    </>
  );
}

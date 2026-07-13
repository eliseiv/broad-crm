import { useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Boxes, Clock, Globe, Loader2, Pencil, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { AddBackendModal } from '@/components/AddBackendModal';
import { DetailInfoSection, DetailRow, SecretRevealField } from '@/components/DetailFields';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/cn';
import { formatRelativeTime } from '@/lib/format';
import { revealBackendAdminApiKey, revealBackendApiKey } from '@/features/backends/api';
import { backendsKey, useBackendStatus, useDeleteBackend } from '@/features/backends/hooks';
import type { Backend, BackendCheckStatus } from '@/types/api';

interface BackendCardProps {
  backend: Backend;
  /** Право `backends:edit` — гейт карандаша в блоке действий и глаз-reveal секретов. */
  canEdit?: boolean;
  /** Право удаления (кнопка «Удалить»). RBAC-гейтинг. По умолчанию true. */
  canDelete?: boolean;
}

/**
 * Карточка бэка — **CARD-FIRST** (08-design-system.md «Страница «Бэки»», ADR-049 §3):
 * вся информация живёт на карточке, `BackendDetailModal` **УПРАЗДНЕНА**, и **клик по телу
 * карточки не открывает ничего**. Поэтому у тела карточки НЕТ `role="button"`/`tabIndex`/
 * `onClick`/`cursor-pointer`/focus-ring — кликабельная по ARIA карточка без действия была бы
 * a11y-дефектом. Интерактивны только: триггер «Информация», карандаш, «Удалить», глаз-reveal
 * и ссылка Git (`stopPropagation` не нужен — всплывать некуда: DnD на `/backends` убран,
 * ADR-046 §2а).
 *
 * Блок **«Информация»** (свёрнут по умолчанию) внизу карточки: Сервер → ИИ-ключ → API KEY →
 * ADMIN API KEY → Git → Примечания. Раскрытие **не делает ни одного запроса** — все значения
 * уже пришли в `BackendListItem`. **Секреты НЕ преднагружаются** (ADR-049 §4): рендерится
 * маска по флагу `has_*`, значение запрашивается **только по клику на глаз**, по одному
 * ресурсу за раз. Пустые поля не рендерятся; если не осталось ни одной строки — блок
 * «Информация» не рендерится вовсе (ни триггера, ни chevron).
 */
export function BackendCard({ backend, canEdit = true, canDelete = true }: BackendCardProps) {
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

  // Пустые поля не рендерятся (ADR-046 §3, в силе для этого блока): если ни связей, ни
  // секретов, ни git/примечаний — блок «Информация» на карточке не рендерится вовсе
  // (ADR-049 §3). Строковые поля считаем непустыми ПО `trim()` — ровно той же семантикой,
  // что и `DetailRow` (он отбрасывает строки из одних пробелов). Иначе whitespace-only `note`
  // дал бы `hasInfo = true` и ПУСТОЙ блок: триггер + chevron, а внутри ни одной строки.
  const git = backend.git?.trim() ?? '';
  const hasInfo =
    Boolean(backend.server_name?.trim()) ||
    Boolean(backend.ai_key_name?.trim()) ||
    backend.has_api_key ||
    backend.has_admin_api_key ||
    git !== '' ||
    Boolean(backend.note?.trim());

  const iconBtn =
    'inline-flex h-8 w-8 items-center justify-center rounded-md text-text-tertiary transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent';

  return (
    <>
      <Card
        className={cn('flex h-full flex-col gap-4 p-4 sm:p-5', isError && 'border-status-red/70')}
      >
        {/* Шапка: иконка + имя + статус-бейдж; справа — блок действий (карандаш + удалить). */}
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

          {/* Блок действий карточки (ADR-049 §3): карандаш (backends:edit) → AddBackendModal
              mode='edit'; единственная кнопка «Удалить» (backends:delete). */}
          {(canEdit || canDelete) && (
            <div className="flex shrink-0 items-center gap-1">
              {canEdit && (
                <button
                  type="button"
                  onClick={() => setEditOpen(true)}
                  aria-label={`Редактировать бэк ${backend.name}`}
                  className={cn(iconBtn, 'hover:bg-surface-3 hover:text-text-primary')}
                >
                  <Pencil className="h-4 w-4" aria-hidden="true" />
                </button>
              )}
              {canDelete && (
                <button
                  type="button"
                  onClick={() => setConfirmOpen(true)}
                  aria-label={`Удалить бэк ${backend.name}`}
                  className={cn(iconBtn, 'hover:bg-surface-3 hover:text-status-red')}
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              )}
            </div>
          )}
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

        {/* Обновлено */}
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

        {/* Блок «Информация» — внизу карточки, свёрнут по умолчанию (ADR-049 §3). */}
        {hasInfo && (
          <div className="mt-auto">
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

              {/* Условие — по `trim()`: whitespace-only `git` иначе дал бы ссылку с пустым href. */}
              {git !== '' && (
                <DetailRow
                  label="Git"
                  value={
                    <a
                      href={git}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="break-all text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {git}
                    </a>
                  }
                />
              )}
              <DetailRow label="Примечания" value={backend.note} />
            </DetailInfoSection>
          </div>
        )}
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

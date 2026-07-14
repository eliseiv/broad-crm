import { useEffect, useState } from 'react';
import { Pencil, RefreshCw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Pill } from '@/components/ui/Pill';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { Spinner } from '@/components/ui/Spinner';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { ApiError } from '@/lib/api';
import { formatRelativeTime } from '@/lib/format';
import { MAIL_CONNECTION_PROGRESS_HINT } from '@/features/mail/errorMessages';
import { useDeleteMailbox, useSyncMailbox, useUpdateMailbox } from '@/features/mail/hooks';
import type { MailMailbox, TeamRef } from '@/types/api';

/** team_id = null (без команды). */
const NO_TEAM = '';

interface MailboxRowProps {
  mailbox: MailMailbox;
  /**
   * Команды КАНАЛА «Почты» — из `GET /api/auth/me` (`me.mail_teams`, ADR-055 §6.3), а НЕ из
   * `GET /api/teams` (гейт `teams:view`). Источник и опций дропдауна переноса (он рендерится
   * только admin-уровню, у которого `mail_teams` = все команды системы), и резолва имени
   * команды в статичном режиме (все видимые ящики ∈ scope ⇒ имя резолвится).
   */
  teams: TeamRef[];
  /** Может ли актор менять команду ящика (перенос — только admin-уровень, ADR-044 §4). */
  canTransfer: boolean;
  canEdit: boolean;
  canSync: boolean;
  canDelete: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Строка таблицы «Почты» (08-design-system.md «Рендер строки ящика», ADR-047 §5; референс
 * `screen/1.jpg`). Идентификационная ячейка — ДВА РЯДА, отдельной колонки статуса нет
 * (кружок переехал в первый ряд):
 *  - ряд 1: кружок статуса (`Badge` dot: green — активна и без ошибок синка; red — неактивна
 *    ИЛИ есть ошибки синка; статус продублирован текстом для скринридера) → лейбл «Номер»
 *    (вторичный) + `number` (крупно, полужирно) → лейбл «Приложение» (вторичный) + `app_name`
 *    пилюлей `ui/Pill tone="accent"`. Пустое значение → пара «лейбл + значение» не рендерится;
 *  - ряд 2: адрес почты (`email`).
 * Далее: команда (перенос `Select` — только admin-уровень; значение читается ПОЛНОСТЬЮ,
 * без truncate — ADR-047 §4), время последнего синка и ошибка, действия под правами.
 */
export function MailboxRow({
  mailbox,
  teams,
  canTransfer,
  canEdit,
  canSync,
  canDelete,
}: MailboxRowProps) {
  const currentTeam = mailbox.team_id ?? NO_TEAM;
  const [selectedTeam, setSelectedTeam] = useState(currentTeam);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const updateMutation = useUpdateMailbox();
  const syncMutation = useSyncMailbox();
  const deleteMutation = useDeleteMailbox();

  useEffect(() => {
    setSelectedTeam(currentTeam);
  }, [currentTeam]);

  // Кружок (08-design-system.md §«Вкладка Почты»): зелёный — активна И без ошибок синка
  // (consecutive_failures===0 И last_sync_error==null); красный — неактивна ИЛИ есть
  // ошибки синка (счётчик>0 ИЛИ живой last_sync_error). `null` last_sync_error — здоров.
  const healthy =
    mailbox.is_active && mailbox.consecutive_failures === 0 && mailbox.last_sync_error == null;
  const statusText = mailbox.is_active
    ? healthy
      ? 'Активна'
      : 'Ошибка синхронизации'
    : 'Неактивна';

  // Пустое значение → пара «лейбл + значение» не рендерится (лейбл без значения не
  // показывается никогда; ADR-047 §5).
  const number = mailbox.number?.trim() ? mailbox.number : null;
  const appName = mailbox.app_name?.trim() ? mailbox.app_name : null;

  const teamOptions: SelectOption[] = [
    { value: NO_TEAM, label: 'Без команды' },
    ...teams.map((t) => ({ value: t.id, label: t.name })),
  ];

  const currentTeamName = currentTeam
    ? (teams.find((t) => t.id === currentTeam)?.name ?? 'Команда')
    : 'Без команды';

  const handleTeamChange = (next: string) => {
    if (next === currentTeam) return;
    setSelectedTeam(next);
    updateMutation.mutate(
      { id: mailbox.id, payload: { team_id: next === NO_TEAM ? null : next } },
      {
        onSuccess: () => toast.success('Почта перенесена'),
        onError: (err) => {
          setSelectedTeam(currentTeam);
          toast.error(errorMessage(err, 'Не удалось перенести почту'));
        },
      },
    );
  };

  const handleSync = () => {
    syncMutation.mutate(mailbox.id, {
      onSuccess: () => toast.success('Синхронизация запущена'),
      onError: (err) => toast.error(errorMessage(err, 'Не удалось запустить синхронизацию')),
    });
  };

  const handleDelete = () => {
    deleteMutation.mutate(mailbox.id, {
      onSuccess: () => {
        toast.success('Почта удалена');
        setConfirmOpen(false);
      },
      onError: (err) => toast.error(errorMessage(err, 'Не удалось удалить почту')),
    });
  };

  return (
    <tr className="border-t border-border-subtle align-top">
      <td className="px-3 py-3">
        <div className="flex flex-col gap-1">
          {/* Ряд 1: статус-кружок + «Номер» + «Приложение» (ADR-047 §5, референс screen/1.jpg). */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Badge tone={healthy ? 'green' : 'red'} className="shrink-0">
              <span className="sr-only">{statusText}</span>
            </Badge>
            {number && (
              <span className="flex items-baseline gap-2">
                <span className="text-[13px] text-text-secondary">Номер</span>
                <span className="break-words text-lg font-bold leading-tight text-text-primary">
                  {number}
                </span>
              </span>
            )}
            {appName && (
              <span className="flex items-center gap-2">
                <span className="text-[13px] text-text-secondary">Приложение</span>
                <Pill label={appName} tone="accent" wrap title={appName} />
              </span>
            )}
          </div>
          {/* Ряд 2: адрес почты. */}
          <span className="break-all font-mono text-[13px] text-text-primary">{mailbox.email}</span>
        </div>
      </td>
      <td className="px-3 py-3">
        {/* Значение команды читается ПОЛНОСТЬЮ (ADR-047 §4): контрол не менее w-56,
            статичное значение переносится (break-words), truncate/overflow-hidden запрещены. */}
        {canTransfer ? (
          // Контрол не менее w-56 и растёт вместе с колонкой (`w-full`) — значение
          // читается целиком; `title` отдаёт полное имя команды при наведении.
          <div className="flex w-full min-w-[14rem] flex-col gap-1.5">
            <Select
              aria-label={`Команда почты ${mailbox.email}`}
              title={currentTeamName}
              options={teamOptions}
              value={selectedTeam}
              disabled={updateMutation.isPending}
              onChange={(e) => handleTeamChange(e.target.value)}
            />
            {/*
              Прогресс-состояние долгого ожидания (08-design-system.md «ожидание проверки
              соединения (long-running)»: PATCH /mailboxes/{id} — один из ТРЁХ нормированных
              эндпоинтов; ADR-053 §1.1 относит ЛЮБОЙ сетевой PATCH к mail-server-категории →
              запрос легально идёт до 85 с). Select уже disabled — добавляем спиннер + подпись,
              чтобы ожидание не читалось как зависание. Подпись ПЕРЕНОСИТСЯ (break-words),
              truncate/overflow-hidden на значимом тексте запрещены.
            */}
            {updateMutation.isPending && (
              <p
                className="flex items-start gap-2 text-[12px] leading-relaxed text-text-secondary"
                role="status"
              >
                <Spinner className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
                <span className="break-words">{MAIL_CONNECTION_PROGRESS_HINT}</span>
              </p>
            )}
          </div>
        ) : (
          <span className="block w-full min-w-[14rem] break-words text-[13px] text-text-secondary">
            {currentTeamName}
          </span>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="whitespace-nowrap text-[13px] text-text-secondary">
            {mailbox.last_synced_at ? formatRelativeTime(mailbox.last_synced_at) : 'ещё не было'}
          </span>
          {mailbox.last_sync_error && (
            <span className="break-words text-[12px] text-status-red">
              {mailbox.last_sync_error}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center justify-end gap-1">
          {canSync && (
            <Button
              variant="ghost"
              size="sm"
              onClick={handleSync}
              loading={syncMutation.isPending}
              aria-label={`Синхронизировать сейчас ${mailbox.email}`}
              className="text-text-tertiary hover:text-text-primary"
            >
              <RefreshCw className="h-4 w-4" />
            </Button>
          )}
          {canEdit && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditOpen(true)}
                aria-label={`Изменить почту ${mailbox.email}`}
                className="text-text-tertiary hover:text-text-primary"
              >
                <Pencil className="h-4 w-4" />
              </Button>
              <MailboxFormModal
                open={editOpen}
                onOpenChange={setEditOpen}
                mode="edit"
                mailbox={mailbox}
              />
            </>
          )}
          {canDelete && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmOpen(true)}
                aria-label={`Удалить почту ${mailbox.email}`}
                className="text-text-tertiary hover:text-status-red"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
              <Modal
                open={confirmOpen}
                onOpenChange={(next) => !deleteMutation.isPending && setConfirmOpen(next)}
                title="Удалить почту?"
                description={`Ящик ${mailbox.email} будет удалён из агрегатора.`}
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
                    <Button
                      variant="danger"
                      loading={deleteMutation.isPending}
                      onClick={handleDelete}
                    >
                      Удалить
                    </Button>
                  </>
                }
              >
                <p className="text-sm text-text-secondary">
                  Синхронизация писем этого ящика прекратится. Ранее полученные письма останутся в
                  истории агрегатора.
                </p>
              </Modal>
            </>
          )}
        </div>
      </td>
    </tr>
  );
}

import { useEffect, useState } from 'react';
import { Pencil, RefreshCw, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { MailboxFormModal } from '@/components/MailboxFormModal';
import { ApiError } from '@/lib/api';
import { formatRelativeTime } from '@/lib/format';
import { useDeleteMailbox, useSyncMailbox, useUpdateMailbox } from '@/features/mail/hooks';
import type { MailMailbox, MailTeam } from '@/types/api';

/** group_id = null (без команды). */
const NO_TEAM = '';

interface MailboxRowProps {
  mailbox: MailMailbox;
  /** Команды mail-агрегатора для привязки (GET /api/mail/teams). */
  teams: MailTeam[];
  canEdit: boolean;
  canSync: boolean;
  canDelete: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Строка таблицы «Почты» (08-design-system.md «Вкладка Почты», ADR-038): слева цветной
 * кружок статуса (`Badge` dot: green — активна и без ошибок синка; red — неактивна ИЛИ
 * есть ошибки синка), адрес + имя, привязка к команде (`Select`, как в SmsNumberRow),
 * время последнего синка и ошибка, действия (синк/редактировать/удалить) под правами.
 */
export function MailboxRow({ mailbox, teams, canEdit, canSync, canDelete }: MailboxRowProps) {
  const currentGroup = mailbox.group_id != null ? String(mailbox.group_id) : NO_TEAM;
  const [selectedGroup, setSelectedGroup] = useState(currentGroup);
  const [editOpen, setEditOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const updateMutation = useUpdateMailbox();
  const syncMutation = useSyncMailbox();
  const deleteMutation = useDeleteMailbox();

  useEffect(() => {
    setSelectedGroup(currentGroup);
  }, [currentGroup]);

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

  const teamOptions: SelectOption[] = [
    { value: NO_TEAM, label: 'Без команды' },
    ...teams.map((t) => ({ value: String(t.id), label: t.name })),
  ];

  const handleGroupChange = (next: string) => {
    if (next === currentGroup) return;
    setSelectedGroup(next);
    updateMutation.mutate(
      { id: mailbox.id, payload: { group_id: next === NO_TEAM ? null : Number(next) } },
      {
        onSuccess: () => toast.success('Почта перенесена'),
        onError: (err) => {
          setSelectedGroup(currentGroup);
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
        <Badge tone={healthy ? 'green' : 'red'}>{statusText}</Badge>
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="break-all font-mono text-[13px] text-text-primary">{mailbox.email}</span>
          {mailbox.display_name && (
            <span className="break-words text-[12px] text-text-secondary">
              {mailbox.display_name}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-3">
        {canEdit ? (
          <div className="w-40">
            <Select
              aria-label={`Команда почты ${mailbox.email}`}
              options={teamOptions}
              value={selectedGroup}
              disabled={updateMutation.isPending}
              onChange={(e) => handleGroupChange(e.target.value)}
            />
          </div>
        ) : (
          <span className="text-[13px] text-text-secondary">
            {teams.find((t) => String(t.id) === currentGroup)?.name ?? 'Без команды'}
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

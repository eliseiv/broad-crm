import { useState } from 'react';
import { ArrowRightLeft, Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { InlineEditField } from '@/components/InlineEditField';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useDeleteSmsNumber, useTransferSmsNumber, useUpdateSmsNumber } from '@/features/sms/hooks';
import type { SmsNumber, SmsNumberUpdateRequest, TeamListItem } from '@/types/api';

/** Значение опции «снять команду» (unassigned) в Select переноса. */
const NO_TEAM = '';

interface SmsNumberRowProps {
  number: SmsNumber;
  teams: TeamListItem[];
  canEdit: boolean;
  canTransfer: boolean;
  canDelete: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Строка таблицы «Номера» (08-design-system.md «Вкладка Номера»): номер + системный
 * `label`, инлайн-поля login/app_name/note, перенос в команду и удаление. Контролы
 * гейтятся правами (useCan sms:edit/transfer/delete) — без права не рендерятся.
 */
export function SmsNumberRow({
  number,
  teams,
  canEdit,
  canTransfer,
  canDelete,
}: SmsNumberRowProps) {
  const currentTeamId = number.team?.id ?? NO_TEAM;
  const [targetTeamId, setTargetTeamId] = useState(currentTeamId);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const updateMutation = useUpdateSmsNumber();
  const transferMutation = useTransferSmsNumber();
  const deleteMutation = useDeleteSmsNumber();

  const saveField = (field: keyof SmsNumberUpdateRequest, next: string) => {
    // Presence-семантика: ключ всегда присутствует; пустая строка → затирание (NULL).
    updateMutation.mutate(
      { id: number.id, payload: { [field]: next } },
      {
        onSuccess: () => toast.success('Изменения сохранены'),
        onError: (err) => toast.error(errorMessage(err, 'Не удалось сохранить изменения')),
      },
    );
  };

  const teamOptions: SelectOption[] = [
    { value: NO_TEAM, label: 'Без команды' },
    ...teams.map((t) => ({ value: t.id, label: t.name })),
  ];

  const handleTransfer = () => {
    transferMutation.mutate(
      { id: number.id, payload: { team_id: targetTeamId === NO_TEAM ? null : targetTeamId } },
      {
        onSuccess: () => toast.success('Номер перенесён'),
        onError: (err) => toast.error(errorMessage(err, 'Не удалось перенести номер')),
      },
    );
  };

  const handleDelete = () => {
    deleteMutation.mutate(number.id, {
      onSuccess: () => {
        toast.success('Номер удалён');
        setConfirmOpen(false);
      },
      onError: (err) => toast.error(errorMessage(err, 'Не удалось удалить номер')),
    });
  };

  const transferDisabled = targetTeamId === currentTeamId || transferMutation.isPending;

  return (
    <tr className="border-t border-border-subtle align-top">
      <td className="px-3 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="whitespace-nowrap font-mono text-[13px] text-text-primary">
            {number.phone_number}
          </span>
          {number.label && (
            <span className="break-words text-[12px] text-text-secondary">{number.label}</span>
          )}
        </div>
      </td>
      <td className="px-3 py-3">
        <InlineEditField
          value={number.login}
          label="Логин"
          canEdit={canEdit}
          saving={updateMutation.isPending}
          onSave={(v) => saveField('login', v)}
        />
      </td>
      <td className="px-3 py-3">
        <InlineEditField
          value={number.app_name}
          label="Приложение"
          canEdit={canEdit}
          saving={updateMutation.isPending}
          onSave={(v) => saveField('app_name', v)}
        />
      </td>
      <td className="px-3 py-3">
        <InlineEditField
          value={number.note}
          label="Примечание"
          canEdit={canEdit}
          saving={updateMutation.isPending}
          multiline
          onSave={(v) => saveField('note', v)}
        />
      </td>
      <td className="px-3 py-3">
        {canTransfer ? (
          <div className="w-40">
            <Select
              aria-label={`Команда номера ${number.phone_number}`}
              options={teamOptions}
              value={targetTeamId}
              onChange={(e) => setTargetTeamId(e.target.value)}
            />
          </div>
        ) : (
          <span className="text-[13px] text-text-secondary">
            {number.team?.name ?? 'Без команды'}
          </span>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-2">
          {canTransfer && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleTransfer}
              disabled={transferDisabled}
              loading={transferMutation.isPending}
            >
              <ArrowRightLeft className="h-4 w-4" />
              Перенести
            </Button>
          )}
          {canDelete && (
            <Button
              variant="danger"
              size="sm"
              onClick={() => setConfirmOpen(true)}
              aria-label={`Удалить номер ${number.phone_number}`}
            >
              <Trash2 className="h-4 w-4" />
              Удалить
            </Button>
          )}
        </div>

        {canDelete && (
          <Modal
            open={confirmOpen}
            onOpenChange={(next) => !deleteMutation.isPending && setConfirmOpen(next)}
            title="Удалить номер?"
            description={`Удалить номер ${number.phone_number}? История SMS сохранится.`}
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
              Номер будет удалён из списка. Ранее полученные SMS останутся в истории.
            </p>
          </Modal>
        )}
      </td>
    </tr>
  );
}

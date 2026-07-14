import { useEffect, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { toast } from 'sonner';
import { InlineEditField } from '@/components/InlineEditField';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import type { SelectOption } from '@/components/ui/Select';
import { ApiError } from '@/lib/api';
import { useDeleteSmsNumber, useTransferSmsNumber, useUpdateSmsNumber } from '@/features/sms/hooks';
import type { SmsNumber, SmsNumberUpdateRequest, TeamRef } from '@/types/api';

/** Значение опции «снять команду» (unassigned) в Select переноса. */
const NO_TEAM = '';

interface SmsNumberRowProps {
  number: SmsNumber;
  /**
   * Команды КАНАЛА «СМС» — из `GET /api/auth/me` (`me.sms_teams`, ADR-055 §6.3), а НЕ из
   * `GET /api/teams` (гейт `teams:view` — у sms-оператора его нет ⇒ список приходил пустым и
   * контрол молча деградировал). Это РОВНО те команды, в которые перенос разрешён: целевая
   * команда вне scope → `403 forbidden` (ADR-055 §3.2 п.3).
   */
  teams: TeamRef[];
  /**
   * Показывать ли опцию «Без команды» (снятие команды, `team_id=null`): admin-уровню — всегда,
   * не-админу — только при `me.sms_includes_unassigned` (иначе снятие даёт `403`, а номер ушёл
   * бы из его scope). ADR-055 §3.2 п.2 / §6.3.
   */
  allowNoTeam: boolean;
  canEdit: boolean;
  canTransfer: boolean;
  canDelete: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/**
 * Строка таблицы «Номера» (08-design-system.md «Вкладка Номера», ADR-033): номер +
 * системный `label`, инлайн-поля login/app_name/note, перенос в команду (Select в
 * колонке «Команда» коммитит перенос сразу при выборе) и удаление (компактная
 * иконка Trash2 без лейбл-колонки). Контролы гейтятся правами (useCan
 * sms:edit/transfer/delete) — без права не рендерятся.
 */
export function SmsNumberRow({
  number,
  teams,
  allowNoTeam,
  canEdit,
  canTransfer,
  canDelete,
}: SmsNumberRowProps) {
  const currentTeamId = number.team?.id ?? NO_TEAM;
  // Локальное значение Select для мгновенного фидбэка; синхронизируется с
  // серверным состоянием (props) после успешного переноса / инвалидации.
  const [selectedTeamId, setSelectedTeamId] = useState(currentTeamId);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const updateMutation = useUpdateSmsNumber();
  const transferMutation = useTransferSmsNumber();
  const deleteMutation = useDeleteSmsNumber();

  useEffect(() => {
    setSelectedTeamId(currentTeamId);
  }, [currentTeamId]);

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

  // Опции — только то, что актор ВПРАВЕ выбрать (ADR-055 §6.3): команды его scope канала +
  // «Без команды» под флагом. Текущая команда номера всегда ∈ scope (иначе номер не был бы
  // виден), но добавляем её оборонительно — иначе нативный `<select>` показал бы чужое
  // значение первой опции.
  const teamOptions: SelectOption[] = [
    ...(allowNoTeam ? [{ value: NO_TEAM, label: 'Без команды' }] : []),
    ...teams.map((t) => ({ value: t.id, label: t.name })),
  ];
  const ownTeam = number.team;
  if (ownTeam && !teamOptions.some((o) => o.value === ownTeam.id)) {
    teamOptions.push({ value: ownTeam.id, label: ownTeam.name });
  }

  // Перенос коммитится сразу при выборе значения в Select (без кнопки «Перенести»).
  const handleTransferChange = (nextTeamId: string) => {
    if (nextTeamId === currentTeamId) return;
    setSelectedTeamId(nextTeamId);
    transferMutation.mutate(
      { id: number.id, payload: { team_id: nextTeamId === NO_TEAM ? null : nextTeamId } },
      {
        onSuccess: () => toast.success('Номер перенесён'),
        onError: (err) => {
          setSelectedTeamId(currentTeamId); // откат к серверному состоянию
          toast.error(errorMessage(err, 'Не удалось перенести номер'));
        },
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
              value={selectedTeamId}
              disabled={transferMutation.isPending}
              onChange={(e) => handleTransferChange(e.target.value)}
            />
          </div>
        ) : (
          <span className="text-[13px] text-text-secondary">
            {number.team?.name ?? 'Без команды'}
          </span>
        )}
      </td>
      <td className="px-3 py-3">
        {canDelete && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setConfirmOpen(true)}
              className="text-text-tertiary hover:text-status-red"
              aria-label={`Удалить номер ${number.phone_number}`}
            >
              <Trash2 className="h-4 w-4" />
            </Button>

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
                Номер будет удалён из списка. Ранее полученные SMS останутся в истории.
              </p>
            </Modal>
          </>
        )}
      </td>
    </tr>
  );
}
